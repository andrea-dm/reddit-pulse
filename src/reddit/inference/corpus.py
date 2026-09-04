"""Shared corpus-labelling machinery.

Previously copy-pasted (with drift) across ``run_llms_serial.py``,
``run_bert_serial.py``, ``predict_llms.py`` and ``predict_bert.py``:
``update`` / ``process`` / ``predict``.  Behavioural knobs that differed
between the LLM and BERT variants are now explicit fields on
:class:`CorpusJob`, built by kind-specific factories in
``reddit.inference.llms`` and ``reddit.inference.bert``.

Notes:
    This module assigns the directional label (``down``/``neutral``/``up``,
    encoded ``-1``/``0``/``1`` — see :class:`reddit.core.config.LabelsConfig`)
    to each row of the **already-filtered** per-subreddit submission/comment
    CSVs (:data:`SUBREDDITS`). It is the inference half of the paper's
    pipeline; the upstream keyword-lexicon filter, geographic classification
    and comment-timeliness restriction that produce those CSVs are not part
    of this package (see
    `Advanced — Implementation Design: System Overview
    <advanced/implementation_design/system_overview.md#scope-boundary>`_).
    Aggregating the per-row directional labels into the daily/monthly
    inflation-signal indicators described in the paper is likewise out of
    scope here.
"""

# transformers models/tokenizers are untyped; Unknowns stay in this file, and
# the public signatures type them as explicit `Any` boundaries.
# pyright: reportUnknownVariableType=false, reportUnknownMemberType=false
# pyright: reportUnknownArgumentType=false

from __future__ import annotations

import gc
import logging
import os
import time
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from shutil import rmtree
from typing import TYPE_CHECKING, Any, cast

import torch
from pandas import DataFrame, concat, read_csv, read_json
from tqdm import tqdm

from reddit.core.errors import CorpusUnavailableError
from reddit.core.utils import dump_object, now

if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path

    from reddit.core.config import Config, LabelsConfig
    from reddit.core.protocols import LogFn

# Lowercase: these interpolate into the on-disk corpus filenames, which are
# lowercase; the previous "Economics" resolved only on case-insensitive mounts.
# Rationale: the three subreddits studied in the paper (Del Monaco, Longo,
# Marcucci & Tafani, "Reddit's 'pulse' on US inflation", Banca d'Italia QEF
# 1028, June 2026) — r/Economics, r/economy and r/wallstreetbets.
SUBREDDITS = ["economy", "economics", "wallstreetbets"]

# Rows per chunk when reloading the JSONL dump in `_load_dump`. Bounds the
# transient parsing overhead; the final frame is still built whole because the
# answers-file merge needs it whole.
DUMP_CHUNK_ROWS = 200_000

# The answers file is a shared read-modify-write target: two `reddit` processes
# labelling different models of the same family (the documented way to split
# work across GPUs) both read it, merge their own columns in and write it back,
# the second writer silently dropping the first one's columns. The atomic
# rename in `update_answers` protects against a torn file, not against that
# lost update, so the whole read-merge-write runs under `_answers_lock`.
# A merge of a multi-million-row CSV takes minutes; a lock older than the
# timeout belongs to a process that died without releasing it.
ANSWERS_LOCK_TIMEOUT = 30 * 60  # seconds
ANSWERS_LOCK_POLL = 1.0  # seconds between acquisition attempts


@dataclass(frozen=True, slots=True)
class CorpusJob:
    """One labelling pass over the corpus (submissions or comments).

    Replaces the eighteen-parameter signature :func:`predict_corpus` used to
    carry.  Every LLM/BERT behavioural difference is a named field here, set
    once by the factory that owns it — see
    :func:`reddit.inference.llms.build_jobs` and
    :func:`reddit.inference.bert.build_jobs`.

    Attributes:
        name: Short job name (``"submissions"`` or ``"comments"``), used in
            log messages only.
        desc: Progress-bar description passed to ``tqdm``.
        csv_format: Format string for the per-subreddit corpus filename,
            e.g. ``"{}_final_jae.csv"``; interpolated with each entry of
            :data:`SUBREDDITS`.
        output_filename: Filename of the standalone labelled CSV written
            under ``labels_dir``.
        answer_file: Filename of the consolidated answers CSV (under
            ``results_dir``) that new label/trend columns are merged into.
        dump_file: Path of the crash-safety JSONL dump accumulated during
            batched inference and removed once the labelled CSV is written
            (see :func:`predict_corpus`).
        label_col: Output column name for the decoded label string
            (e.g. ``"down"``/``"neutral"``/``"up"``).
        trend_col: Output column name for the decoded trend encoding
            (``-1``/``0``/``1``, from :attr:`reddit.core.config.LabelsConfig.encodings`).
        text_col: Input column holding the text to classify (submission
            title or comment body).
        cols: Join keys used both to select input columns and to merge the
            labelled rows back into the answers file via :func:`update_answers`.
        batch_size: Number of rows tokenized/classified per forward pass.
        max_length: Tokenizer truncation length.
        half: Cast the model to fp16 before inference (BERT path only; the
            LLM path is already quantized).
        keep_text: Keep ``text_col`` in the standalone labelled CSV. Always
            dropped before the answers-file merge regardless of this flag.
        family_suffix: When set, results are written to a per-family copy of
            the answers file (LLM behaviour); when ``None``, the shared
            answers file is updated in place (BERT behaviour).
    """

    name: str
    desc: str
    csv_format: str
    output_filename: str
    answer_file: str
    dump_file: Path
    label_col: str
    trend_col: str
    text_col: str
    cols: tuple[str, ...]
    batch_size: int
    max_length: int
    half: bool = False
    keep_text: bool = False
    family_suffix: str | None = None


@contextmanager
def _answers_lock(target: Path) -> Generator[None, None, None]:
    """Hold ``<target>.lock/`` for the duration of the block.

    A lock *directory*: ``mkdir`` is atomic on every filesystem this project
    runs on, network mounts included, where ``fcntl`` locks are not reliable.
    Waits :data:`ANSWERS_LOCK_POLL` seconds between attempts, breaks a lock
    older than :data:`ANSWERS_LOCK_TIMEOUT` (its owner has died), and gives
    up after the same timeout. Breaking a stale lock is not itself atomic:
    two waiters that judge the same lock stale at the same instant can, in
    a millisecond window, remove each other's fresh lock — an accepted
    residual risk given it needs a crashed process *and* two simultaneous
    waiters.

    Args:
        target: The answers file the lock guards.

    Raises:
        TimeoutError: the lock stayed held by a live process for longer than
            :data:`ANSWERS_LOCK_TIMEOUT`.

    Notes:
        Creates and removes ``<target>.lock/`` (I/O); writes an ``owner``
        file inside it naming the holder, for diagnosis only.
    """
    lock_dir = target.with_name(target.name + ".lock")
    deadline = time.monotonic() + ANSWERS_LOCK_TIMEOUT
    while True:
        try:
            lock_dir.mkdir()
            break
        except FileExistsError:
            try:
                age = time.time() - lock_dir.stat().st_mtime
            except FileNotFoundError:
                continue  # released between our mkdir and stat: retry at once
            if age > ANSWERS_LOCK_TIMEOUT:
                logging.warning("Breaking stale answers lock `%s` (%.0f s old).", lock_dir, age)
                rmtree(lock_dir, ignore_errors=True)
                continue
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"Could not acquire the answers-file lock `{lock_dir}` within {ANSWERS_LOCK_TIMEOUT:.0f} s; "
                    f"another labelling process is merging into `{target.name}`. This run's labelled CSV is "
                    f"already saved, so re-run the merge once the lock is released."
                ) from None
            time.sleep(ANSWERS_LOCK_POLL)
    try:
        with suppress(OSError):  # diagnostics only; the lock is the directory itself
            (lock_dir / "owner").write_text(f"pid={os.getpid()} since={now()}\n", encoding="utf-8")
        yield
    finally:
        rmtree(lock_dir, ignore_errors=True)


def update_answers(output_file: Path, df_labelled: DataFrame, job: CorpusJob) -> None:
    """Merge new label/trend columns into the consolidated answers CSV.

    With ``job.family_suffix`` set, results go to a per-family copy
    ``{stem}_{suffix}{ext}`` (seeded from the base file on first run) — the
    LLM behaviour.  Without it, the answers file is updated in place — the
    BERT behaviour.

    Args:
        output_file: Base answers-file path (under ``results_dir``); the
            actual write target may be a per-family copy, see above.
        df_labelled: Freshly labelled rows for this job (mutated: the text
            column is dropped from a local copy before merging, the input
            frame itself is untouched).
        job: Supplies the join keys (``job.cols``), the label/trend column
            names, the text column to exclude, and ``family_suffix``.

    Raises:
        CorpusUnavailableError: the answers file to merge into does not exist.
        TimeoutError: another process held the answers-file lock for longer
            than :data:`ANSWERS_LOCK_TIMEOUT` (see :func:`_answers_lock`).

    Notes:
        Reads and rewrites the target CSV (I/O) under ``<target>.lock/``, so
        concurrent ``reddit`` processes labelling different models of the
        same family serialise their merges instead of overwriting each
        other's columns. The write is atomic: the merged frame is written
        to a sibling ``.tmp`` file and moved into place with ``os.replace``,
        so a crash mid-write cannot corrupt the previously accumulated
        answers file.
    """
    if job.family_suffix:
        target = output_file.parent / f"{output_file.stem}_{job.family_suffix}{output_file.suffix}"
    else:
        target = output_file

    with _answers_lock(target):
        _merge_into_answers(output_file, target, df_labelled, job)


def _merge_into_answers(output_file: Path, target: Path, df_labelled: DataFrame, job: CorpusJob) -> None:
    """The read-merge-write of :func:`update_answers`; call it holding the lock.

    The source is resolved *inside* the lock: two first runs of a family
    that both saw the per-family copy missing would otherwise both seed it
    from the base file, and the second would overwrite the first.
    """
    source = target if target.exists() else output_file
    if not source.exists():
        raise CorpusUnavailableError(
            f"The consolidated answers file `{source}` does not exist. It is expected to "
            f"pre-exist; restore it (or create it from the corpus) before labelling."
        )

    df_answers = read_csv(source, encoding="utf-8", low_memory=False)
    for col in (job.label_col, job.trend_col):
        if col in df_answers.columns:
            df_answers = df_answers.drop(columns=col, errors="ignore")

    # The text column must never enter the merge. The answers file carries its
    # own copy, so a duplicate name makes pandas emit `_x`/`_y` variants that
    # corrupt the consolidated schema a little more on every run. The labelled
    # CSV written to `labels_dir` may still keep it (see `CorpusJob.keep_text`).
    df_new = df_labelled.drop(columns=[job.text_col], errors="ignore")

    join_cols = list(job.cols)
    # `many_to_one`: duplicate join keys in the freshly labelled frame would
    # silently multiply answers rows on every merge; failing loudly here is
    # caught by the caller, which has already persisted the labelled CSV.
    merged = df_answers.merge(df_new, on=join_cols, how="left", validate="many_to_one")

    # Write through a sibling temp file and rename: the BERT path rewrites the
    # answers file in place, so a crash mid-write would destroy the accumulated
    # corpus labels of every previously run model.
    tmp_target = target.with_name(target.name + ".tmp")
    merged.to_csv(tmp_target, encoding="utf-8", index=False)
    os.replace(tmp_target, target)


@torch.inference_mode()
def process_batch(
    texts: list[str],
    model: Any,
    tokenizer: Any,
    labels: LabelsConfig,
    device: str | torch.device,
    job: CorpusJob,
) -> list[dict[str, Any]]:
    """Run one batch of texts through the classifier and map ids to labels/trends.

    Args:
        texts: Strings to classify.
        model: The trained classification model (already on ``device``).
        tokenizer: Matching tokenizer.
        labels: The single label authority (``id2label`` + ``encodings``).
        device: Device the model lives on.
        job: Supplies the truncation length and the output column names.

    Returns:
        One ``{job.label_col: <str>, job.trend_col: <int>}`` dict per input
        text, in order, decoded via ``labels.id2label``/``labels.encodings``.
        An id absent from the mapping (a checkpoint with more heads than
        declared labels) decodes to label ``"unknown"`` and trend ``None``,
        so the trend column stays numeric rather than turning into a mixed
        int/str object column.

    Notes:
        Runs under ``torch.inference_mode()``; no gradients are tracked.
    """
    encoded = tokenizer(
        texts,
        return_tensors="pt",
        padding=True,
        truncation=True,
        max_length=job.max_length,
    )
    input_ids = encoded["input_ids"].to(device)
    attention_mask = encoded["attention_mask"].to(device)

    outputs = model(input_ids=input_ids, attention_mask=attention_mask)
    predicted_indices = torch.argmax(outputs.logits, dim=1).cpu().numpy()

    id2label = labels.id2label
    encodings = labels.encodings

    results: list[dict[str, Any]] = []
    for idx in predicted_indices:
        label = id2label.get(int(idx), "unknown")
        trend = encodings.get(label)
        results.append({job.label_col: label, job.trend_col: trend})
    return results


def _prepare_model_device(model: Any, job: CorpusJob) -> torch.device:
    """Place the model and return the device inputs must be moved to.

    Models loaded with ``device_map="auto"`` (the 4-bit LLM path) are already
    dispatched by accelerate — calling ``.to("cuda")`` on them is at best a
    no-op and wrong for a multi-GPU shard, and the previous hardcoded
    ``"cuda"`` also crashed outright on CPU-only hosts.

    Args:
        model: The model to place, and to switch into eval mode.
        job: ``job.half`` requests an fp16 cast for models not already
            accelerate-dispatched (the BERT path).

    Returns:
        The ``torch.device`` inputs must be moved to before the forward pass.
    """
    if getattr(model, "hf_device_map", None) is None:
        model.to("cuda" if torch.cuda.is_available() else "cpu")
        if job.half:
            model.half()
    model.eval()
    return cast(torch.device, next(model.parameters()).device)


def _assemble_records(df_batch: DataFrame, batch_results: list[dict[str, Any]], job: CorpusJob) -> DataFrame:
    """Attach one batch's label/trend columns to its input rows.

    Vectorized assembly: ``iterrows`` built one Series per corpus row, which
    is the slowest way pandas offers. Rows beyond ``len(batch_results)`` are
    dropped, so a short prediction list can never misalign the join keys.

    Args:
        df_batch: The input rows of this batch (join keys plus text).
        batch_results: One ``{label_col, trend_col}`` dict per row, in order.
        job: Supplies the column names and ``keep_text``.

    Returns:
        The labelled rows, with the text column dropped unless ``job.keep_text``.
    """
    n = min(len(df_batch), len(batch_results))
    records = df_batch.iloc[:n]
    if not job.keep_text:
        records = records.drop(columns=[job.text_col], errors="ignore")
    return records.assign(
        **{
            job.label_col: [r[job.label_col] for r in batch_results[:n]],
            job.trend_col: [r[job.trend_col] for r in batch_results[:n]],
        }
    )


def predict_corpus(model: Any, tokenizer: Any, config: Config, job: CorpusJob, *, log: LogFn) -> None:
    """Label every subreddit CSV, dumping each batch to JSONL for crash-safety.

    Subreddits are streamed one at a time — only one subreddit's frame is in
    memory during inference — and every batch is appended (and flushed) to
    the JSONL dump as it completes.  After all batches, the dump is reloaded
    once, the labelled table written to ``labels_dir``, then merged into the
    answers CSV via :func:`update_answers`.

    Args:
        model: Trained classification model (LLM or BERT checkpoint).
        tokenizer: Matching tokenizer.
        config: Project configuration; supplies ``paths.reddit_dir`` (the
            per-subreddit corpus CSVs) and ``labels`` (the label authority).
        job: Behavioural knobs for this pass — see :class:`CorpusJob`.
        log: Human-oriented progress logger.

    Notes:
        Per-subreddit and per-batch failures are caught, logged, and
        skipped rather than aborting the whole run: a missing or malformed
        ``r/{subreddit}`` CSV skips only that subreddit, and a batch
        exception skips only that batch. Reads one corpus CSV per
        subreddit and writes/reloads ``job.dump_file`` (JSONL); the dump is
        truncated at the start of the run, held open for its duration and
        deleted once the labelled CSV is on disk. Delegates final
        persistence to :func:`_persist_labels`.
    """
    device = _prepare_model_device(model, job)

    processed_count = 0
    # Opened once, in write mode: appending to a previous run's records would
    # duplicate join keys and multiply rows in the merge performed below, and
    # re-opening the file for every batch cost one open/close round-trip per
    # batch — tens of thousands per model on a network mount.
    with open(job.dump_file, "wb") as dump:
        for reddit in SUBREDDITS:
            logging.info("Loading r/%s...", reddit)
            # A missing or malformed subreddit CSV skips that subreddit only,
            # like every other per-item failure here — one absent file must
            # not abort the labelling of the remaining subreddits.
            try:
                df_reddit = read_csv(config.paths.reddit_dir / job.csv_format.format(reddit), low_memory=False)
                df_reddit = df_reddit.loc[df_reddit[job.text_col].notna(), [*job.cols, job.text_col]]
            except Exception as e:
                log(f"Could not load the r/{reddit} corpus: {e}", level="error")
                logging.exception("Could not load the r/%s corpus", reddit)
                continue
            log(f"r/{reddit}: {len(df_reddit):,d} texts to be labelled", level="info")

            for i in tqdm(range(0, len(df_reddit), job.batch_size), desc=f"{job.desc} [r/{reddit}]"):
                df_batch = df_reddit.iloc[i : i + job.batch_size]
                batch_texts = df_batch[job.text_col].tolist()

                try:
                    batch_results = process_batch(
                        texts=batch_texts,
                        model=model,
                        tokenizer=tokenizer,
                        labels=config.labels,
                        device=device,
                        job=job,
                    )
                    records = _assemble_records(df_batch, batch_results, job)
                    dump.writelines(
                        # Rationale: to_dict(orient="records") types keys as
                        # Hashable; these frames have string column names.
                        dump_object(cast("dict[str, object]", output_record))
                        for output_record in records.to_dict(orient="records")
                    )
                    # Crash-safety: hand the batch to the OS now rather than
                    # when the buffer happens to fill.
                    dump.flush()
                    processed_count += len(records)

                except Exception as e:
                    log(f"An error occurred in r/{reddit} batch starting at index {i}: {e}", level="critical")
                    logging.critical(
                        "An error occurred in r/%s batch starting at index %s: %s", reddit, i, e, exc_info=True
                    )

            del df_reddit
            gc.collect()

    torch.cuda.empty_cache()
    log(f"{processed_count:,d} texts successfully labelled", level="success")
    logging.debug("Dumped %s predictions to %s", f"{processed_count:,d}", job.dump_file)

    _persist_labels(config, job, log=log)


def _load_dump(path: Path) -> DataFrame:
    """Reload a JSONL dump into one frame, chunk by chunk.

    Replaces ``DataFrame([json.loads(line) for line in f])``, which held the
    whole corpus as a list of Python dicts — several times the size of the
    resulting frame — before pandas ever saw it. ``dtype=False`` and
    ``convert_dates=False`` keep every column exactly as the JSON parser
    typed it (integers as ``int64``, everything else as ``object``), which
    is what the per-line reload produced, so the join keys reach
    :func:`update_answers` unchanged.

    Args:
        path: The JSONL dump written by :func:`predict_corpus`.

    Returns:
        One frame with every record, or an empty frame for an empty dump.
    """
    chunks = list(read_json(path, lines=True, dtype=False, convert_dates=False, chunksize=DUMP_CHUNK_ROWS))
    return concat(chunks, ignore_index=True) if chunks else DataFrame()


def _persist_labels(config: Config, job: CorpusJob, *, log: LogFn) -> None:
    """Reload the JSONL dump, save the labelled table and update the answers.

    Reloads ``job.dump_file`` into a single frame (:func:`_load_dump`), then
    persists it in two independent, separately-guarded steps so a failure in
    the second (the answers-file merge) cannot discard the first (the
    standalone labelled CSV, the expensive artifact of the run): see
    :func:`update_answers`. The dump exists to survive a crash before the
    labelled CSV is written; once that CSV is on disk it is deleted — one
    full-corpus JSONL per model and method adds up on a shared mount. A
    failed CSV write keeps it.
    """
    df_labelled = DataFrame()
    try:
        df_labelled = _load_dump(job.dump_file)
        log(f"{len(df_labelled):,d} labels successfully loaded", level="success")
    except Exception as e:
        log(f"Could not read the dump file: {e}", level="error")
        logging.exception("Could not read the dump file")

    if not df_labelled.empty:
        # The labelled table is the expensive artifact of this run: persist it
        # first and in its own scope, so that a failure in the answers-file
        # bookkeeping below can no longer discard hours of inference.
        try:
            output_path = config.paths.labels_dir / job.output_filename
            df_labelled.to_csv(output_path, index=False, encoding="utf-8")
            log(f"Output successfully saved into `{output_path.name}`", level="success")
        except Exception as e:
            log(f"Could not save the labelled output: {e}", level="error")
            logging.exception("Could not save the labelled output")
        else:
            job.dump_file.unlink(missing_ok=True)

        try:
            update_answers(config.paths.results_dir / job.answer_file, df_labelled, job)
            log("Answers successfully updated", level="success")
        except Exception as e:
            log(f"Could not update the answers file: {e}", level="error")
            logging.exception("Could not update the answers file")

    del df_labelled
    gc.collect()
