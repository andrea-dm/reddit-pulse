"""Multi-seed QDoRA/xQDoRA fine-tuning of LLM classifiers.

Replaces ``legacy/scripts/run_llms_serial.py`` (full pipeline) and
``legacy/scripts/train_llms.py`` (no labeller injected).
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
from itertools import product
from shutil import rmtree
from time import monotonic_ns
from typing import Any, cast

import torch
from peft import get_peft_model, prepare_model_for_kbit_training
from peft.optimizers import create_loraplus_optimizer
from torch.optim import AdamW
from transformers import (
    AutoConfig,
    AutoModelForSequenceClassification,
    AutoTokenizer,
    DataCollatorWithPadding,
    TrainingArguments,
)

from reddit.core.config import Config, Models, ModelSpec
from reddit.core.errors import UnsupportedMethodError
from reddit.core.logging import print_log
from reddit.core.protocols import Labeller, LogFn
from reddit.core.utils import archive_model, clear_hf_cache, fmt_td
from reddit.data.preparation import DataBundle
from reddit.modeling.loading import LLM_MAX_LENGTH, llm_model_args
from reddit.modeling.peft import peft_config
from reddit.training.loop import SeedContext, run_seeds
from reddit.training.selection import select_median


class LlmSeedStrategy:
    """PEFT (QDoRA/xQDoRA) fine-tuning of a quantized decoder LLM classifier."""

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
            ctx.model.id, config=model_conf, cache_dir=ctx.hf_cache, **llm_model_args()
        )
        model = prepare_model_for_kbit_training(model)
        model = get_peft_model(model, peft_config[ctx.finetuning_method])
        # Rationale: PeftModel proxies `config` to the wrapped transformer;
        # its declared attribute type is too narrow for this assignment.
        cast("Any", model).config.pad_token_id = ctx.tokenizer.pad_token_id
        return model, model_conf

    def parameter_counts(self, model: Any) -> tuple[int | None, int | None]:
        try:
            return model.get_nb_trainable_parameters()
        except Exception:
            return None, None

    def tokenize(self, ctx: SeedContext, bundle: DataBundle) -> Any:
        tokenizer = ctx.tokenizer

        def tokenize_batch(examples):
            return tokenizer(examples["text"], truncation=True)

        tokenized = bundle.dataset.map(tokenize_batch, batched=True, desc="Tokenizing dataset")
        tokenized = tokenized.remove_columns(["text"])
        tokenized.set_format("torch")
        return tokenized

    def data_collator(self, ctx: SeedContext) -> Any:
        return DataCollatorWithPadding(tokenizer=ctx.tokenizer, pad_to_multiple_of=8, return_tensors="pt")

    def training_arguments(self, ctx: SeedContext) -> TrainingArguments:
        training = ctx.config.training
        return TrainingArguments(
            run_name=ctx.run_name,
            output_dir=str(ctx.cache_dir),
            learning_rate=training.learning_rate,
            lr_scheduler_type="cosine",
            num_train_epochs=training.num_train_epochs,
            gradient_accumulation_steps=training.gradient_accumulation_steps,
            gradient_checkpointing=True,
            gradient_checkpointing_kwargs={"use_reentrant": True},
            **training.arguments.model_dump(),
        )

    def optimizers(self, ctx: SeedContext, model: Any) -> tuple[Any, Any]:
        return (
            create_loraplus_optimizer(
                model=model,
                optimizer_cls=AdamW,
                lr=ctx.config.training.learning_rate,
                loraplus_lr_ratio=5,
                fused=True,
            ),
            None,
        )


def run_model(
    config: Config,
    models: Models,
    model: ModelSpec,
    finetuning_method: str,
    log: LogFn,
    *,
    seeds: tuple[int, ...],
    labeller: Labeller | None = None,
) -> bool:
    """Train one model with one method, select the median seed, optionally label the corpus.

    Returns:
        ``True`` when a median checkpoint was selected, ``False`` otherwise.
    """
    run_name = f"{model.name}_{finetuning_method}_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}"

    cache_dir = config.paths.cache_dir / run_name
    hf_cache = config.hf_home / model.name
    cache_dir.mkdir(parents=True, exist_ok=True)
    hf_cache.mkdir(parents=True, exist_ok=True)

    log(f"Preparing for `{finetuning_method}`...")
    t1 = monotonic_ns()

    tokenizer = AutoTokenizer.from_pretrained(
        model.id,
        truncation=True,
        max_length=LLM_MAX_LENGTH,
        padding="max_length",
        use_fast=True,
        cache_dir=hf_cache,
    )
    tokenizer.pad_token = tokenizer.pad_token or tokenizer.eos_token
    tokenizer.padding_side = "left"

    log(f"Preparing for `{finetuning_method}`... done: it took {fmt_td(monotonic_ns() - t1)}.")

    ctx = SeedContext(
        config=config,
        models=models,
        model=model,
        finetuning_method=finetuning_method,
        run_name=run_name,
        cache_dir=cache_dir,
        hf_cache=hf_cache,
        seeds=seeds,
        tokenizer=tokenizer,
        log=log,
    )

    log(f"Training via `{finetuning_method}`...")
    t1_run = monotonic_ns()
    results = run_seeds(ctx, LlmSeedStrategy())
    log(f"Training via `{finetuning_method}`... done: it took {fmt_td(monotonic_ns() - t1_run)}.")

    log(f"Selecting from `{finetuning_method}`...")
    t1 = monotonic_ns()
    median_model, median_conf = select_median(config, model.name, results, finetuning_method, log)
    log(f"Selecting from `{finetuning_method}`... done: it took {fmt_td(monotonic_ns() - t1)}.")

    if median_model is not None and labeller is not None:
        t1 = monotonic_ns()
        log(f"Inference via `{finetuning_method}`...")
        try:
            labeller(
                config=config,
                family=models.family,
                model_name=model.name,
                model_path=str(median_model),
                model_conf=median_conf,
                tokenizer=tokenizer,
                finetuning_method=finetuning_method,
                log=log,
            )
        except Exception as e:
            log(
                f"Something unexpected occurred in the inference process: {e}\n{traceback.format_exc()}",
                level="critical",
            )
            logging.critical(f"Something unexpected occurred in the inference process: {e}", exc_info=True)
        log(f"Inference via `{finetuning_method}`... done: it took {fmt_td(monotonic_ns() - t1)}.")

    log("Cleaning up...")
    t1 = monotonic_ns()
    if median_model is not None:
        archive_model(median_model, str(median_model))
    # Only the per-run training cache is dropped here. The downloaded weights
    # live under `hf_cache` and are shared by every fine-tuning method of this
    # model; `run_family` clears them once the last method has finished, which
    # is what `track_trained_models` was always meant to guarantee.
    rmtree(cache_dir, ignore_errors=True)
    log(f"Cleaning up... done: it took {fmt_td(monotonic_ns() - t1)}.")

    return median_model is not None


def run_family(
    config: Config,
    models: Models,
    *,
    labeller: Labeller | None = None,
    limit: int = 0,
) -> int:
    """Train every model x fine-tuning method combination in the family.

    Args:
        config: Project configuration.
        models: The resolved family selection.
        labeller: Injected corpus-labelling step; ``None`` skips labelling.
        limit: Use only the first ``limit`` seeds (``0`` = all).

    Returns:
        The number of (model, method) runs that produced a selected checkpoint.

    Raises:
        UnsupportedMethodError: a declared method has no PEFT configuration.
    """
    logging.info(f"Found {torch.cuda.device_count()} GPUs available for the pool.")
    logging.info(f"Found {len(models.models)} models in the config to train.")

    torch.backends.cuda.matmul.allow_tf32 = True
    torch.backends.cudnn.allow_tf32 = True

    methods = models.finetuning_methods
    unsupported = [m for m in methods if m not in peft_config]
    if unsupported:
        raise UnsupportedMethodError(
            f"No PEFT configuration registered for: {', '.join(unsupported)}. "
            f"Available: {', '.join(sorted(peft_config))}."
        )

    seeds = tuple(config.training.seeds[:limit] if limit > 0 else config.training.seeds)

    logging.info("Running started.")
    t1_run = monotonic_ns()

    n_methods = len(methods)
    logging.info(f"Found {n_methods:,d} fine-tuning methods to apply.")
    track_trained_models: dict[str, set[str]] = {m.name: set() for m in models.models}
    selected = 0

    for model, finetuning_method in product(models.models, methods):
        log_file = config.paths.logs_dir / f"{model.name}_{config.system.date}.log"
        log_file.touch(exist_ok=True)
        log = partial(print_log, dump=True, filename=log_file, pid=os.getpid())

        log(f"Running `{model.name}-{finetuning_method}` ...", level=None)
        t1 = monotonic_ns()

        if run_model(config, models, model, finetuning_method, log, seeds=seeds, labeller=labeller):
            selected += 1

        track_trained_models[model.name].add(finetuning_method)
        if len(track_trained_models[model.name]) == n_methods:
            logging.info(f"All the finetuning methods for `{model.name}` have been run. Cleaning from memory...")
            clear_hf_cache(
                model.id,
                extra_cache_dirs=[config.environment.hf_home] if config.environment.hf_home else None,
            )
            rmtree(config.hf_home / model.name, ignore_errors=True)
            track_trained_models.pop(model.name)
            gc.collect()
            logging.info(f"All the finetuning methods for `{model.name}` have been run. Cleaning from memory... done.")

        t2 = monotonic_ns()
        log(f"Running `{model.name}-{finetuning_method}`... done: it took {fmt_td(t2 - t1)}.\n", level=None)
        log(f"Elapsed time since the job was launched: {fmt_td(t2 - t1_run)}.\n", level=None)

    logging.info(f"Running ended. It took {fmt_td(monotonic_ns() - t1_run)}.")
    return selected
