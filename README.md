# reddit

Reddit sentiment labelling for inflation-trend research: fine-tunes decoder
LLMs (QDoRA / xQDoRA on 4-bit bases) and BERT-family encoders as sequence
classifiers over a hand-labelled gold dataset (`data/labelled.xlsx`), selects
the median-performing seed per model, and labels the full corpus of
submissions and comments from r/economy, r/Economics and r/wallstreetbets.

## Layout

```
config.yml            # unified configuration (paths, training, families)
src/reddit/
  cli.py              # `reddit` entry point (run / train / predict)
  core/               # config schema, logging, env bootstrap, shared utils
  data/               # gold-dataset preparation (split -> DatasetDict)
  modeling/           # quantization + PEFT configs, metrics, weighted trainer
  training/           # multi-seed pipelines (llms, bert) + median selection
  inference/          # corpus labelling, checkpoint discovery (zip/dirs)
  tasks/              # CLI task modules (setup_*/execute_* pairs)
data/                 # subreddit CSVs + labelled.xlsx gold set
models/               # trained checkpoints ({model}_{method}_{seed}[.zip])
outputs/ results/ labelled/ logs/   # run artefacts
legacy/               # pre-refactor scripts, flat src modules and yaml configs
```

## Setup

```bash
uv venv && uv pip install -e .
# optional, needs a CUDA toolchain:
uv pip install flash-attn --no-build-isolation
```

Secrets (`HUGGINGFACEHUB_API_TOKEN`) are read from the dotenv file configured
under `environment.dotenv` in `config.yml`.

Quality gates (also run in CI — see `.github/workflows/`):

```bash
ruff check src/ tests/
pyright              # strict; config + venv pinned in pyrightconfig.json
pytest tests/ -q
```

## Usage

```bash
reddit run     --family gemma     --gpu 0        # train + select + label corpus
reddit run     --family test --limit 1 --gpu 0   # smoke test (1 seed)
reddit train   --family gemma_27  --gpu 0        # train + select only
reddit predict --family gemma_27 --directory models   # label with saved checkpoints
reddit predict --family bert     --directory models
```

`--family` picks a group from `families:` in `config.yml`; `--gpu` sets
`CUDA_VISIBLE_DEVICES`. The `*_part1` / `*_part2` families split a cohort
across two GPUs — run one command per GPU.

### Old launcher → CLI mapping

| Old | New |
|---|---|
| `launch_gemma.sh` | `reddit run --family gemma --gpu 0` |
| `launch_gemma_27_gid0.sh` | `reddit run --family gemma_27 --gpu 0` |
| `launch_llama.sh` | `reddit run --family llama --gpu 0` |
| `launch_qwen.sh` | `reddit run --family qwen --gpu 1` |
| `launch_llms_small_p1_gid0.sh` / `p2_gid1.sh` | `reddit run --family small_part1 --gpu 0` / `small_part2 --gpu 1` |
| `launch_llms_med_p1_gid0.sh` / `p2_gid1.sh` | `reddit run --family medium_part1 --gpu 0` / `medium_part2 --gpu 1` |
| `launch_llms_tests_gid0.sh` | `reddit run --family test --limit 1 --gpu 0` |
| `launch_bert.sh` / `launch_bert_serial.sh` | `reddit run --family bert --gpu 0` |
| `launch_predict_llms.sh` | `reddit predict --family gemma_27 --directory models --gpu 0` |
| `launch_predict_bert.sh` | `reddit predict --family bert --directory models --gpu 0` |

The conda `LD_PRELOAD`/`LD_LIBRARY_PATH` workaround in the old launchers is
obsolete under the uv-managed environment.
