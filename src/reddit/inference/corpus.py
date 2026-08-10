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
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import torch
from pandas import DataFrame, read_csv
from tqdm import tqdm

from reddit.core.config import Config, LabelsConfig
from reddit.core.errors import CorpusUnavailableError
from reddit.core.protocols import LogFn
from reddit.core.utils import dump_object

# Lowercase: these interpolate into the on-disk corpus filenames, which are
# lowercase; the previous "Economics" resolved only on case-insensitive mounts.
# Rationale: the three subreddits studied in the paper (Del Monaco, Longo,
# Marcucci & Tafani, "Reddit's 'pulse' on US inflation", Banca d'Italia QEF
# 1028, June 2026) — r/Economics, r/economy and r/wallstreetbets.
SUBREDDITS = ["economy", "economics", "wallstreetbets"]


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
            batched inference (see :func:`predict_corpus`).
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

    Notes:
        Reads and rewrites the target CSV (I/O). The write is atomic: the
        merged frame is written to a sibling ``.tmp`` file and moved into
        place with ``os.replace``, so a crash mid-write cannot corrupt the
        previously accumulated answers file.
    """
    if job.family_suffix:
        target = output_file.parent / f"{output_file.stem}_{job.family_suffix}{output_file.suffix}"
        source = target if target.exists() else output_file
    else:
        target = source = output_file

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
        text, in order, decoded via ``labels.id2label``/``labels.encodings``
        (``"unknown"`` for any id absent from either mapping).

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
        trend = encodings.get(label, "unknown")
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


def predict_corpus(model: Any, tokenizer: Any, config: Config, job: CorpusJob, *, log: LogFn) -> None:
    """Label every subreddit CSV, dumping each batch to JSONL for crash-safety.

    Subreddits are streamed one at a time — only one subreddit's frame is in
    memory during inference — and every batch is appended to the JSONL dump as
    it completes.  After all batches, the dump is reloaded once, the labelled
    table written to ``labels_dir``, then merged into the answers CSV via
    :func:`update_answers`.

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
        subreddit and writes/reloads ``job.dump_file`` (JSONL). Delegates
        final persistence to :func:`_persist_labels`.
    """
    # Start from an empty dump. Appending to a previous run's records would
    # duplicate join keys and multiply rows in the merge performed below.
    job.dump_file.write_bytes(b"")

    device = _prepare_model_device(model, job)

    processed_count = 0
    for reddit in SUBREDDITS:
        logging.info(f"Loading r/{reddit}...")
        # A missing or malformed subreddit CSV skips that subreddit only, like
        # every other per-item failure here — one absent file must not abort
        # the labelling of the remaining subreddits.
        try:
            df_reddit = read_csv(config.paths.reddit_dir / job.csv_format.format(reddit), low_memory=False)
            df_reddit = df_reddit.loc[df_reddit[job.text_col].notna(), [*job.cols, job.text_col]]
        except Exception as e:
            log(f"Could not load the r/{reddit} corpus: {e}", level="error")
            logging.error(f"Could not load the r/{reddit} corpus: {e}", exc_info=True)
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
                # Vectorized assembly: `iterrows` built one Series per corpus
                # row, which is the slowest way pandas offers to do this.
                n = min(len(df_batch), len(batch_results))
                records = df_batch.iloc[:n]
                if not job.keep_text:
                    records = records.drop(columns=[job.text_col], errors="ignore")
                records = records.assign(
                    **{
                        job.label_col: [r[job.label_col] for r in batch_results[:n]],
                        job.trend_col: [r[job.trend_col] for r in batch_results[:n]],
                    }
                )
                with open(job.dump_file, "ab") as f:
                    f.writelines(
                        # Rationale: to_dict(orient="records") types keys as
                        # Hashable; these frames have string column names.
                        dump_object(cast("dict[str, object]", output_record))
                        for output_record in records.to_dict(orient="records")
                    )
                processed_count += n

            except Exception as e:
                log(f"An error occurred in r/{reddit} batch starting at index {i}: {e}", level="critical")
                logging.critical(f"An error occurred in r/{reddit} batch starting at index {i}: {e}", exc_info=True)

        del df_reddit
        gc.collect()

    torch.cuda.empty_cache()
    log(f"{processed_count:,d} texts successfully labelled", level="success")
    logging.debug(f"Dumped {processed_count:,d} predictions to {job.dump_file}")

    _persist_labels(config, job, log=log)


def _persist_labels(config: Config, job: CorpusJob, *, log: LogFn) -> None:
    """Reload the JSONL dump, save the labelled table and update the answers.

    Reloads ``job.dump_file`` into a single frame, then persists it in two
    independent, separately-guarded steps so a failure in the second (the
    answers-file merge) cannot discard the first (the standalone labelled
    CSV, the expensive artifact of the run): see :func:`update_answers`.
    """
    df_labelled = DataFrame()
    try:
        with open(job.dump_file, "r", encoding="utf-8") as f:
            df_labelled = DataFrame([json.loads(line) for line in f])
        log(f"{len(df_labelled):,d} labels successfully loaded", level="success")
    except Exception as e:
        log(f"Could not read the dump file: {e}", level="error")
        logging.error(f"Could not read the dump file: {e}", exc_info=True)

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
            logging.error(f"Could not save the labelled output: {e}", exc_info=True)

        try:
            update_answers(config.paths.results_dir / job.answer_file, df_labelled, job)
            log("Answers successfully updated", level="success")
        except Exception as e:
            log(f"Could not update the answers file: {e}", level="error")
            logging.error(f"Could not update the answers file: {e}", exc_info=True)

    del df_labelled
    gc.collect()
