"""Corpus labelling with fine-tuned decoder SLM classifiers (4-bit, device-tuned attention).

Runs the QDoRA+/xQDoRA+-fine-tuned small decoder LLMs (Gemma-2, Llama-3,
Qwen2.5; "SLM" in the paper, to distinguish them from the unfine-tuned
LLaMA-70B zero-shot baseline the paper also reports) over the corpus, using
the shared batching/crash-safety machinery in :mod:`reddit.inference.corpus`.

Notes:
    The LLaMA-70B zero-shot baseline is not implemented in this package.
"""

# transformers/peft models and tokenizers are untyped; Unknowns stay in this
# file, and public signatures type them as explicit `Any` boundaries.
# pyright: reportUnknownVariableType=false, reportUnknownMemberType=false
# pyright: reportUnknownArgumentType=false, reportUnknownParameterType=false
# pyright: reportMissingParameterType=false

from __future__ import annotations

import datetime
import gc
import logging
import os
import traceback
from functools import partial
from shutil import rmtree
from time import monotonic_ns
from typing import TYPE_CHECKING, Any

import torch
from transformers import AutoConfig, AutoModelForSequenceClassification, AutoTokenizer

from reddit.core.logging import print_log
from reddit.core.utils import fmt_td
from reddit.inference.corpus import CorpusJob, predict_corpus
from reddit.inference.discovery import iter_model_archives
from reddit.modeling.loading import LLM_MAX_LENGTH, llm_model_args

if TYPE_CHECKING:
    from pathlib import Path

    from reddit.core.config import Config, Models
    from reddit.core.protocols import LogFn


def build_jobs(config: Config, family: str, model_name: str, finetuning_method: str) -> list[CorpusJob]:
    """The submissions/comments labelling passes for an LLM checkpoint.

    Args:
        config: Project configuration (``inference.submissions``/``.comments``
            gate which jobs are built; ``inference.batch_size`` sizes them).
        family: Model family name; written into each job as
            ``CorpusJob.family_suffix`` so results land in a per-family
            answers-file copy.
        model_name: Short model name (e.g. ``"gemma2_9b"``), used to derive
            output/label column names.
        finetuning_method: ``"qdora"`` or ``"xqdora"``, appended to output
            column names and used as the run label.

    Returns:
        Zero, one, or two :class:`reddit.inference.corpus.CorpusJob`
        instances (submissions and/or comments), per the ``inference``
        config toggles.
    """
    label_col = f"{model_name}_{finetuning_method}_label"
    trend_col = f"{model_name}_{finetuning_method}_trend"
    desc = f"Predicting with {model_name} via {finetuning_method}"

    jobs: list[CorpusJob] = []
    if config.inference.submissions:
        jobs.append(
            CorpusJob(
                name="submissions",
                desc=desc,
                csv_format="{}_final_jae.csv",
                output_filename=f"{model_name}_submissions_{finetuning_method}_predicted_labels.csv",
                answer_file="all_final_jae.csv",
                dump_file=config.paths.output_dir / f"{model_name}_{finetuning_method}_labelled_submissions.jsonl",
                label_col=label_col,
                trend_col=trend_col,
                text_col="title_sub",
                cols=("created_utc", "id_sub"),
                batch_size=config.inference.batch_size,
                max_length=LLM_MAX_LENGTH,
                keep_text=False,
                family_suffix=family,
            )
        )
    if config.inference.comments:
        jobs.append(
            CorpusJob(
                name="comments",
                desc=desc,
                csv_format="{}_comments_final_jae.csv",
                output_filename=f"{model_name}_comments_{finetuning_method}_predicted_labels.csv",
                answer_file="all_comments_final_jae.csv",
                dump_file=config.paths.output_dir / f"{model_name}_{finetuning_method}_labelled_comments.jsonl",
                label_col=label_col,
                trend_col=trend_col,
                text_col="body_com",
                # Comments join on these three keys. The BERT pipeline uses a
                # four-key set; the divergence is deliberate and preserved.
                cols=("created_utc_com", "id_sub", "id_com"),
                batch_size=config.inference.batch_size,
                max_length=LLM_MAX_LENGTH,
                keep_text=False,
                family_suffix=family,
            )
        )
    return jobs


def label_corpus(  # noqa: PLR0913 — mirrors `reddit.core.protocols.Labeller`
    *,
    config: Config,
    family: str,
    model_name: str,
    model_path: str,
    model_conf: Any,
    tokenizer: Any,
    finetuning_method: str,
    log: LogFn,
) -> None:
    """Label submissions and comments with a trained LLM checkpoint.

    Implements :class:`reddit.core.protocols.Labeller` for the decoder-LLM
    path: reloads the checkpoint (config + weights) at ``model_path`` and
    runs :func:`reddit.inference.corpus.predict_corpus` for each job built
    by :func:`build_jobs`.

    Args:
        config: Project configuration.
        family: Model family name (see :func:`build_jobs`).
        model_name: Short model name.
        model_path: Filesystem path to the selected (median-seed) checkpoint.
        model_conf: The ``AutoConfig`` matching ``model_path``.
        tokenizer: Tokenizer matching ``model_path``.
        finetuning_method: ``"qdora"`` or ``"xqdora"``.
        log: Human-oriented progress logger.

    Notes:
        Loads the checkpoint onto the GPU (accelerate ``device_map="auto"``
        dispatch) and reads/writes the corpus and answers files transitively
        via :func:`reddit.inference.corpus.predict_corpus`.
    """
    logging.info("Labelling...")
    t1_task = monotonic_ns()

    # `use_cache` stays off: a classification forward pass never generates, so
    # a KV cache is pure allocation — gigabytes per batch on the 9B/27B models
    # at batch_size=64 x LLM_MAX_LENGTH, on a 16 GB card.
    loaded_model = AutoModelForSequenceClassification.from_pretrained(model_path, config=model_conf, **llm_model_args())
    loaded_model.config.pad_token_id = tokenizer.pad_token_id

    for job in build_jobs(config, family, model_name, finetuning_method):
        log(f"Labelling {job.name}...")
        t1 = monotonic_ns()
        predict_corpus(loaded_model, tokenizer, config, job, log=log)
        log(f"Labelling {job.name}... done: it took {fmt_td(monotonic_ns() - t1)}.", level="info")

    logging.info("Labelling... done: it took %s.", fmt_td(monotonic_ns() - t1_task))


def _predict_one(
    config: Config, models: Models, model_name: str, finetuning_method: str, model_path: str, log: LogFn
) -> None:
    """Load one checkpoint, label the corpus with it, then clean up caches.

    Builds the tokenizer/config from ``model_path``, delegates to
    :func:`label_corpus`, and finally removes the per-run cache directory
    and this model's Hugging Face Hub cache regardless of outcome.
    """
    run_name = f"{model_name}_{finetuning_method}_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}"
    log(f"Running `{run_name}`...", level="info")

    cache_dir = config.paths.cache_dir / run_name
    hf_cache = config.hf_home / model_name
    cache_dir.mkdir(parents=True, exist_ok=True)
    hf_cache.mkdir(parents=True, exist_ok=True)

    log(f"Preparing for `{finetuning_method}`...")
    t1 = monotonic_ns()

    tokenizer = AutoTokenizer.from_pretrained(model_path, use_fast=True, cache_dir=hf_cache)
    tokenizer.pad_token = tokenizer.pad_token or tokenizer.eos_token
    tokenizer.padding_side = "left"

    model_conf = AutoConfig.from_pretrained(
        model_path,
        num_labels=config.labels.num_labels,
        id2label=config.labels.id2label,
        label2id=config.labels.label2id,
        cache_dir=hf_cache,
        use_cache=False,
    )
    log(f"Preparing for `{finetuning_method}`... done: it took {fmt_td(monotonic_ns() - t1)}.")

    log(f"Inference via `{finetuning_method}`...")
    t1 = monotonic_ns()
    try:
        label_corpus(
            config=config,
            family=models.family,
            model_name=model_name,
            model_path=model_path,
            model_conf=model_conf,
            tokenizer=tokenizer,
            finetuning_method=finetuning_method,
            log=log,
        )
    except Exception as e:
        log(f"Something unexpected occurred in the inference process: {e}\n{traceback.format_exc()}", level="critical")
        logging.critical("Something unexpected occurred in the inference process: %s", e, exc_info=True)
    finally:
        torch.cuda.empty_cache()
        gc.collect()
    log(f"Inference via `{finetuning_method}`... done: it took {fmt_td(monotonic_ns() - t1)}.")

    logging.info("Cleaning up model `%s` artifacts...", model_name)
    rmtree(cache_dir, ignore_errors=True)
    rmtree(hf_cache, ignore_errors=True)
    logging.info("Cleaning up model `%s` artifacts... done.", model_name)


def predict_from_archives(config: Config, models: Models, directory: str | Path) -> int:
    """Label the corpus with every ``{model}_{method}_{seed}.zip`` archive found.

    Only archives whose model name appears in the selected family are used.

    Args:
        config: Project configuration.
        models: The resolved family selection (``models.finetuning_methods``
            filters which archives are considered).
        directory: Directory to scan for checkpoint archives (see
            :func:`reddit.inference.discovery.iter_model_archives`).

    Returns:
        The number of checkpoints that were labelled.
    """
    logging.info("Found %s GPUs available for the pool.", torch.cuda.device_count())
    logging.info("Found %s models in the config.", len(models.models))
    logging.info("Running started.")
    t1_run = monotonic_ns()

    list_of_models = [model.name for model in models.models]
    logging.info("Models to be used: %s", ", ".join(list_of_models))

    processed = 0
    for model_name, finetuning_method, model_path in iter_model_archives(directory, methods=models.finetuning_methods):
        if model_name not in list_of_models:
            logging.warning("Model `%s` not found in the config. Skipping...", model_name)
            continue

        logging.info("Inference of `%s` via %s from `%s`.", model_name, finetuning_method, model_path)

        log_file = config.paths.logs_dir / f"{model_name}_{config.system.date}.log"
        log_file.touch(exist_ok=True)
        log = partial(print_log, dump=True, filename=log_file, pid=os.getpid())

        _predict_one(config, models, model_name, finetuning_method, model_path, log)
        processed += 1

    logging.info("Running ended. It took %s.", fmt_td(monotonic_ns() - t1_run))
    return processed
