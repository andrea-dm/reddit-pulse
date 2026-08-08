"""Multi-seed full fine-tuning of BERT-family classifiers (no PEFT).

Replaces ``legacy/scripts/run_bert_serial.py``.
"""

# transformers/peft models and tokenizers are untyped; Unknowns stay in this
# file, and public signatures type them as explicit `Any` boundaries.
# pyright: reportUnknownVariableType=false, reportUnknownMemberType=false
# pyright: reportUnknownArgumentType=false, reportUnknownParameterType=false
# pyright: reportMissingParameterType=false

from __future__ import annotations

import datetime
import logging
import os
import traceback
from functools import partial
from shutil import rmtree
from time import monotonic_ns
from typing import Any

import torch
from transformers import (
    AutoConfig,
    AutoModelForSequenceClassification,
    AutoTokenizer,
    DataCollatorWithPadding,
    TrainingArguments,
)

from reddit.core.config import Config, Models, ModelSpec
from reddit.core.logging import print_log
from reddit.core.protocols import Labeller, LogFn
from reddit.core.utils import fmt_td
from reddit.data.preparation import DataBundle
from reddit.modeling.loading import BERT_MAX_LENGTH, bert_model_args
from reddit.training.loop import SeedContext, run_seeds
from reddit.training.selection import select_median


class BertSeedStrategy:
    """Full fine-tuning of a BERT-family encoder classifier."""

    def build_model(self, ctx: SeedContext, bundle: DataBundle) -> tuple[Any, Any]:
        model_conf = AutoConfig.from_pretrained(
            ctx.model.id,
            num_labels=bundle.num_labels,
            id2label=bundle.id2label,
            label2id=bundle.label2id,
            cache_dir=ctx.hf_cache,
            use_cache=False,
        )
        model = AutoModelForSequenceClassification.from_pretrained(
            ctx.model.id, config=model_conf, cache_dir=ctx.hf_cache, **bert_model_args()
        )
        return model, model_conf

    def parameter_counts(self, model: Any) -> tuple[int | None, int | None]:
        return (
            sum(p.numel() for p in model.parameters() if p.requires_grad),
            sum(p.numel() for p in model.parameters()),
        )

    def tokenize(self, ctx: SeedContext, bundle: DataBundle) -> Any:
        tokenizer = ctx.tokenizer

        def tokenize_batch(examples):
            return tokenizer(examples["text"], truncation=True, max_length=BERT_MAX_LENGTH)

        return bundle.dataset.map(tokenize_batch, batched=True, desc="Tokenizing dataset")

    def data_collator(self, ctx: SeedContext) -> Any:
        # Dynamic padding. Every example was previously padded to the full 512
        # tokens regardless of length, which on a corpus of short titles spent
        # most of the compute on padding.
        return DataCollatorWithPadding(tokenizer=ctx.tokenizer, pad_to_multiple_of=8, return_tensors="pt")

    def training_arguments(self, ctx: SeedContext) -> TrainingArguments:
        bert = ctx.config.training.bert
        return TrainingArguments(
            run_name=ctx.run_name,
            output_dir=str(ctx.cache_dir),
            learning_rate=bert.learning_rate,
            num_train_epochs=bert.num_train_epochs,
            **ctx.config.training.arguments.model_dump(),
        )

    def optimizers(self, ctx: SeedContext, model: Any) -> tuple[Any, Any]:
        return (None, None)


def train_model(
    config: Config,
    models: Models,
    model: ModelSpec,
    log: LogFn,
    *,
    seeds: tuple[int, ...],
    labeller: Labeller | None = None,
) -> bool:
    """Train one encoder over all seeds, select the median seed, optionally label the corpus.

    Returns:
        ``True`` when a median checkpoint was selected, ``False`` otherwise.
    """
    log("Preparing...")
    t1 = monotonic_ns()

    run_name = f"{model.name}_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}"

    cache_dir = config.paths.cache_dir / run_name
    hf_cache = config.hf_home / model.name
    cache_dir.mkdir(parents=True, exist_ok=True)
    hf_cache.mkdir(parents=True, exist_ok=True)

    tokenizer = AutoTokenizer.from_pretrained(model.id, use_fast=True, cache_dir=hf_cache)
    tokenizer.pad_token = tokenizer.pad_token or tokenizer.eos_token
    tokenizer.padding_side = "right"

    log(f"Preparing... done: it took {fmt_td(monotonic_ns() - t1)}.")

    ctx = SeedContext(
        config=config,
        models=models,
        model=model,
        finetuning_method="-",
        run_name=run_name,
        cache_dir=cache_dir,
        hf_cache=hf_cache,
        seeds=seeds,
        tokenizer=tokenizer,
        log=log,
    )

    log("Running...")
    t1_run = monotonic_ns()
    results = run_seeds(ctx, BertSeedStrategy())
    log(f"Running... done: it took {fmt_td(monotonic_ns() - t1_run)}.")

    log("Selecting...")
    t1 = monotonic_ns()
    median_model, median_conf = select_median(config, model.name, results, "-", log)
    log(f"Selecting... done: it took {fmt_td(monotonic_ns() - t1)}.")

    if median_model is not None and labeller is not None:
        log("Inference...")
        t1 = monotonic_ns()
        try:
            labeller(
                config=config,
                family=models.family,
                model_name=model.name,
                model_path=str(median_model),
                model_conf=median_conf,
                tokenizer=tokenizer,
                finetuning_method="-",
                log=log,
            )
        except Exception as e:
            log(
                f"Something unexpected occurred in the inference process: {e}\n{traceback.format_exc()}",
                level="critical",
            )
            logging.critical(f"Something unexpected occurred in the inference process: {e}", exc_info=True)
        log(f"Inference... done: it took {fmt_td(monotonic_ns() - t1)}.")

    log("Cleaning...")
    t1 = monotonic_ns()
    rmtree(cache_dir, ignore_errors=True)
    rmtree(hf_cache, ignore_errors=True)
    log(f"Cleaning... done: it took {fmt_td(monotonic_ns() - t1)}.")

    return median_model is not None


def run_family(
    config: Config,
    models: Models,
    *,
    labeller: Labeller | None = None,
    limit: int = 0,
) -> int:
    """Train every encoder in the family sequentially.

    Args:
        config: Project configuration.
        models: The resolved family selection.
        labeller: Injected corpus-labelling step; ``None`` skips labelling.
        limit: Use only the first ``limit`` seeds (``0`` = all).

    Returns:
        The number of models that produced a selected checkpoint.
    """
    t1_run = monotonic_ns()

    logging.info(f"Found {torch.cuda.device_count()} GPUs available for the pool.")
    logging.info(f"Found {len(models.models)} models in the config to train.")

    seeds = tuple(config.training.seeds[:limit] if limit > 0 else config.training.seeds)

    selected = 0
    for model in models.models:
        log_file = config.paths.logs_dir / f"{model.name}_{config.system.date}.log"
        log_file.touch(exist_ok=True)
        log = partial(print_log, dump=True, filename=log_file, pid=os.getpid())

        log(f"Running `{model.name}`...")
        t1 = monotonic_ns()

        if train_model(config, models, model, log, seeds=seeds, labeller=labeller):
            selected += 1

        t2 = monotonic_ns()
        log(f"Running `{model.name}`... done: it took {fmt_td(t2 - t1)}.")
        log(f"Elapsed time since the job was launched: {fmt_td(t2 - t1_run)}.", level="info")

    logging.info(f"Running... done: it took {fmt_td(monotonic_ns() - t1_run)}.")
    return selected
