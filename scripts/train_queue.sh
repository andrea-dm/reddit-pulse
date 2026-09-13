#!/bin/bash
# Train a cohort of models as a memory-aware queue over the local GPUs, then
# publish every selected checkpoint to the Hugging Face Hub and add it to the
# configured collection.
#
#   cd <repo> && mkdir -p logs/launcher \
#     && nohup bash scripts/train_queue.sh > logs/launcher/queue.out 2>&1 & \
#     sleep 20; tail -f logs/launcher/queue.out logs/train_*.log
#
#   JOBS="qwen2.5_1.5b:33 qwen2.5_0.5b:17" ...   # "<model>:<footprint GB>" to queue
#   GPUS="0"       ...   # cards to schedule on (default: 0 1)
#   COMMAND=run    ...   # also label the corpus after each model's selection
#   UPLOAD=0       ...   # train only, publish later with `reddit upload`
#   DRY=1          ...   # print the queue and the first wave, launch nothing
#   REDDIT=<path>  ...   # the `reddit` executable, when it is not on PATH
#
# Scheduling. One `reddit train` process per model (the two PEFT methods of a
# model share a Hugging Face cache that the last one to finish deletes, so
# they cannot run side by side; different models can). The queue is sorted
# by measured GPU footprint, largest first (longest-processing-time first
# keeps the makespan close to the longest job), and every POLL seconds the
# next job goes to the card with the most budget left that can hold it --
# budget being BUDGET_GB minus the footprints of the jobs already running
# there, cross-checked against the memory the driver reports as used, so a
# foreign process on a card counts too. When no card fits the next job, the
# queue waits for a running job to exit. Once every job has exited the
# selected checkpoints are published in one `reddit upload`.
#
# The default JOBS are the sub-3B cohort with the peaks nvidia-smi reported
# on two A100 80GB cards on 2026-09-13 (batch 64 x 1024 tokens, bf16,
# gradient checkpointing off), plus a margin; the 2B Gemma is heavy for its
# size because of its 256k-token vocabulary. Measure again before queueing
# other models, settings or hardware.
set -uo pipefail
cd "$(dirname "$0")/.."

COMMAND="${COMMAND:-train}"
UPLOAD="${UPLOAD:-1}"
DRY="${DRY:-0}"
REDDIT="${REDDIT:-reddit}"
STAMP="$(date +%Y%m%d_%H%M%S)"
read -r -a GPUS <<< "${GPUS:-0 1}"
BUDGET_GB="${BUDGET_GB:-78}"   # per card, leaves headroom for allocator growth
POLL="${POLL:-30}"             # seconds between scheduling passes
read -r -a JOBS <<< "${JOBS:-llama3.2_3b:44 gemma2_2b:41 qwen2.5_1.5b:33 llama3.2_1b:22 qwen2.5_0.5b:17}"
# Fewer, larger segments: less reserved-but-unused memory per process.
export PYTORCH_CUDA_ALLOC_CONF="${PYTORCH_CUDA_ALLOC_CONF:-expandable_segments:True}"

for job in "${JOBS[@]}"; do
    [[ "$job" =~ ^[^:]+:[0-9]+$ ]] || { echo "Bad job \`$job\`: expected <model>:<footprint GB>." >&2; exit 2; }
done
command -v "$REDDIT" > /dev/null || {
    echo "\`$REDDIT\` not found: activate the project environment or set REDDIT=/path/to/reddit." >&2
    exit 2
}
# Largest footprint first, whatever order JOBS was given in.
mapfile -t JOBS < <(printf '%s\n' "${JOBS[@]}" | sort -t: -k2,2nr)
mkdir -p logs/launcher

log() { echo "$(date '+%F %T')  $*"; }

# gpu index -> GB the scheduler has committed there (running jobs' footprints)
declare -A COMMITTED=()
# pid -> "<gpu>:<model>:<footprint>"
declare -A RUNNING=()
for g in "${GPUS[@]}"; do COMMITTED[$g]=0; done

used_gb() {  # used_gb <gpu>: memory the driver reports in use, in GB (rounded up)
    local mib
    mib="$(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits -i "$1" 2>/dev/null | tr -d ' ')"
    echo $(( (${mib:-0} + 1023) / 1024 ))
}

pick_gpu() {  # pick_gpu <footprint>: the fitting card with the most budget left, or ""
    local need="$1" best="" best_left=-1 g left used
    for g in "${GPUS[@]}"; do
        left=$(( BUDGET_GB - COMMITTED[$g] ))
        used="$(used_gb "$g")"
        # A card just handed a job is still ramping up: trust the larger of
        # the committed figure and what the driver sees.
        (( used > COMMITTED[$g] )) && left=$(( BUDGET_GB - used ))
        if (( need <= left && left > best_left )); then best="$g"; best_left="$left"; fi
    done
    echo "$best"
}

launch() {  # launch <gpu> <model> <footprint>
    local gpu="$1" model="$2" need="$3" out="logs/launcher/${2}_${STAMP}.out"
    "$REDDIT" "$COMMAND" --model "$model" --gpu "$gpu" > "$out" 2>&1 &
    RUNNING[$!]="$gpu:$model:$need"
    COMMITTED[$gpu]=$(( COMMITTED[$gpu] + need ))
    log "launch  gpu $gpu  $model  (~${need} GB, card now ~${COMMITTED[$gpu]} GB committed)  pid $!  stdout $out"
}

reap() {  # forget finished jobs, release their budget; counts non-zero exits in FAILED
    local pid gpu model need status
    for pid in "${!RUNNING[@]}"; do
        if ! kill -0 "$pid" 2>/dev/null; then
            wait "$pid"; status=$?
            IFS=: read -r gpu model need <<< "${RUNNING[$pid]}"
            unset "RUNNING[$pid]"
            COMMITTED[$gpu]=$(( COMMITTED[$gpu] - need ))
            (( status != 0 )) && FAILED=$(( FAILED + 1 ))
            log "exit    gpu $gpu  $model  status $status  (card now ~${COMMITTED[$gpu]} GB committed)"
        fi
    done
}

FAILED=0
QUEUE=("${JOBS[@]}")
log "queue (largest first): ${QUEUE[*]}   budget ${BUDGET_GB} GB per card, poll ${POLL}s"
for g in "${GPUS[@]}"; do log "gpu $g currently reports $(used_gb "$g") GB in use"; done

while (( ${#QUEUE[@]} > 0 || ${#RUNNING[@]} > 0 )); do
    reap
    # Dispatch, in order, every queued job that fits somewhere (backfilling:
    # a job that must wait does not hold back the smaller ones behind it; the
    # largest jobs are dispatched first on empty cards, so none starves).
    PENDING=()
    for job in "${QUEUE[@]}"; do
        IFS=: read -r model need <<< "$job"
        gpu="$(pick_gpu "$need")"
        if [[ -z "$gpu" ]]; then
            if (( need > BUDGET_GB && ${#RUNNING[@]} == 0 )); then
                # Larger than any card's budget: it can only ever run alone.
                gpu="${GPUS[0]}"; for g in "${GPUS[@]}"; do (( COMMITTED[$g] < COMMITTED[$gpu] )) && gpu="$g"; done
                log "warning: $model (~${need} GB) exceeds the ${BUDGET_GB} GB budget; launching alone on gpu $gpu"
            else
                PENDING+=("$job"); continue  # wait for a job, or a foreign process, to free a card
            fi
        fi
        if [[ "$DRY" == "1" ]]; then
            log "dry run: would launch $model (~${need} GB) on gpu $gpu"
            COMMITTED[$gpu]=$(( COMMITTED[$gpu] + need ))
            RUNNING[dry-$model]="$gpu:$model:$need"
        else
            launch "$gpu" "$model" "$need"
        fi
    done
    QUEUE=("${PENDING[@]}")
    if [[ "$DRY" == "1" ]]; then
        log "dry run: ${#QUEUE[@]} job(s) would wait for a card: ${QUEUE[*]:-none}"
        exit 0
    fi
    (( ${#QUEUE[@]} > 0 || ${#RUNNING[@]} > 0 )) && sleep "$POLL"
done
log "all trainings finished ($FAILED process(es) exited non-zero)"

MODELS=()
for job in "${JOBS[@]}"; do MODELS+=("${job%%:*}"); done
if [[ "$UPLOAD" != "1" ]]; then
    log "UPLOAD=$UPLOAD: not publishing. Later: $REDDIT upload --model ${MODELS[*]}"
    exit "$FAILED"
fi
log "publishing the selected checkpoints of: ${MODELS[*]}"
"$REDDIT" upload --model "${MODELS[@]}" > "logs/launcher/upload_${STAMP}.out" 2>&1
status=$?
log "upload exited with $status (log: logs/launcher/upload_${STAMP}.out)"
exit $(( FAILED > 0 ? FAILED : status ))
