"""Corpus labelling with fully fine-tuned BERT-family classifiers."""

# transformers/peft models and tokenizers are untyped; Unknowns stay in this
# file, and public signatures type them as explicit `Any` boundaries.
# pyright: reportUnknownVariableType=false, reportUnknownMemberType=false
# pyright: reportUnknownArgumentType=false, reportUnknownParameterType=false
# pyright: reportMissingParameterType=false

from __future__ import annotations

import datetime
import logging
import os
from functools import partial
from pathlib import Path
from shutil import rmtree
from time import monotonic_ns
from typing import Any

import torch
from transformers import AutoConfig, AutoModelForSequenceClassification, AutoTokenizer

from reddit.core.config import Config, Models
from reddit.core.logging import print_log
from reddit.core.protocols import LogFn
from reddit.core.utils import fmt_td
from reddit.inference.corpus import CorpusJob, predict_corpus
from reddit.inference.discovery import iter_model_dirs
from reddit.modeling.loading import BERT_MAX_LENGTH, bert_model_args


def build_jobs(config: Config, model_name: str) -> list[CorpusJob]:
    """The submissions/comments labelling passes for a BERT checkpoint."""
    label_col = f"{model_name}_label"
    trend_col = f"{model_name}_trend"
    desc = f"Predicting with {model_name}"

    jobs: list[CorpusJob] = []
    if config.inference.submissions:
        jobs.append(
            CorpusJob(
                name="submissions",
                desc=desc,
                csv_format="{}_final_jae.csv",
                output_filename=f"{model_name}_submissions_predicted_labels.csv",
                answer_file="all_final_jae.csv",
                dump_file=config.paths.output_dir / f"{model_name}_labelled_submissions.jsonl",
                label_col=label_col,
                trend_col=trend_col,
                text_col="title_sub",
                cols=("created_utc", "id_sub"),
                batch_size=config.inference.batch_size,
                max_length=BERT_MAX_LENGTH,
                half=True,
                # The text column is kept in the labelled CSV, but dropped
                # before the answers merge — see `update_answers`.
                keep_text=True,
            )
        )
    if config.inference.comments:
        jobs.append(
            CorpusJob(
                name="comments",
                desc=desc,
                csv_format="{}_comments_final_jae.csv",
                output_filename=f"{model_name}_comments_predicted_labels.csv",
                answer_file="all_comments_final_jae.csv",
                dump_file=config.paths.output_dir / f"{model_name}_labelled_comments.jsonl",
                label_col=label_col,
                trend_col=trend_col,
                text_col="body_com",
                # Comments join on these four keys, one more than the LLM
                # pipeline. The divergence is deliberate and preserved.
                cols=("created_utc_sub", "created_utc_com", "id_sub", "id_com"),
                batch_size=config.inference.batch_size,
                max_length=BERT_MAX_LENGTH,
                half=True,
                keep_text=True,
            )
        )
    return jobs


def label_corpus(
    *,
    config: Config,
    family: str = "",
    model_name: str,
    model_path: str,
    model_conf: Any,
    tokenizer: Any,
    finetuning_method: str = "-",
    log: LogFn,
) -> None:
    """Label submissions and comments with a trained BERT checkpoint.

    ``family`` and ``finetuning_method`` are accepted for interface parity with
    the LLM labeller (see :class:`reddit.core.protocols.Labeller`); they do not
    influence the BERT jobs (answer files are updated in place and the output
    columns carry no method suffix), so they are only recorded in the log.
    """
    logging.info(f"Labelling... (family=`{family or '-'}`, method=`{finetuning_method}`)")
    t1_task = monotonic_ns()

    loaded_model = AutoModelForSequenceClassification.from_pretrained(
        model_path, config=model_conf, **bert_model_args()
    )

    for job in build_jobs(config, model_name):
        log(f"Labelling {job.name}...")
        t1 = monotonic_ns()
        predict_corpus(loaded_model, tokenizer, config, job, log=log)
        log(f"Labelling {job.name}... done: it took {fmt_td(monotonic_ns() - t1)}.", level="info")

    logging.info(f"Labelling... done: it took {fmt_td(monotonic_ns() - t1_task)}.")


def _predict_one(config: Config, models: Models, model_name: str, model_path: str, log: LogFn) -> None:
    run_name = f"predicting_{model_name}_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}"

    cache_dir = config.paths.cache_dir / run_name
    hf_cache = config.hf_home / model_name
    cache_dir.mkdir(parents=True, exist_ok=True)
    hf_cache.mkdir(parents=True, exist_ok=True)

    tokenizer = AutoTokenizer.from_pretrained(model_path, use_fast=True, cache_dir=hf_cache)
    tokenizer.pad_token = tokenizer.pad_token or tokenizer.eos_token
    tokenizer.padding_side = "right"

    model_conf = AutoConfig.from_pretrained(
        model_path,
        num_labels=config.labels.num_labels,
        id2label=config.labels.id2label,
        label2id=config.labels.label2id,
        cache_dir=hf_cache,
        use_cache=False,
    )

    log("Inference...")
    t1 = monotonic_ns()
    try:
        label_corpus(
            config=config,
            family=models.family,
            model_name=model_name,
            model_path=model_path,
            model_conf=model_conf,
            tokenizer=tokenizer,
            log=log,
        )
    except Exception as e:
        log(f"Something unexpected occurred in the inference process: {e}", level="critical")
        logging.critical(f"Something unexpected occurred in the inference process: {e}", exc_info=True)
    finally:
        log(f"Inference... done: it took {fmt_td(monotonic_ns() - t1)}.")

    logging.info(f"Cleaning up model `{model_name}` artifacts...")
    rmtree(cache_dir, ignore_errors=True)
    rmtree(hf_cache, ignore_errors=True)
    logging.info(f"Cleaning up model `{model_name}` artifacts... done.")


def predict_from_directories(config: Config, models: Models, directory: str | Path) -> int:
    """Label the corpus with every ``{model}_{seed}`` checkpoint directory found.

    Only directories whose model name appears in the selected family are used.

    Returns:
        The number of checkpoints that were labelled.
    """
    logging.info(f"Found {torch.cuda.device_count()} GPUs available for the pool.")
    logging.info(f"Found {len(models.models)} models in the config.")
    logging.info("Running started.")
    t1_run = monotonic_ns()

    list_of_models = [model.name for model in models.models]
    logging.info(f"Models to be used: {', '.join(list_of_models)}")

    processed = 0
    for model_name, model_path in iter_model_dirs(directory):
        if model_name not in list_of_models:
            logging.warning(f"Model `{model_name}` not found in the config. Skipping...")
            continue

        log_file = config.paths.logs_dir / f"{model_name}_{config.system.date}.log"
        log_file.touch(exist_ok=True)
        log = partial(print_log, dump=True, filename=log_file, pid=os.getpid())

        _predict_one(config, models, model_name, model_path, log)
        processed += 1

    logging.info(f"Running ended. It took {fmt_td(monotonic_ns() - t1_run)}.")
    return processed
