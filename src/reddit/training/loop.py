"""The shared multi-seed fine-tuning loop.

``training.llms`` and ``training.bert`` previously carried two near-identical
200-line loops that had already drifted apart (different metrics-dump
directories, different progress logging, one rebuilding the model config per
seed and the other sharing a single mutated instance).  Both now supply only a
:class:`SeedStrategy` describing what genuinely differs between a PEFT-adapted
decoder LLM and a fully fine-tuned encoder.
"""

# sklearn ships no type stubs; the resulting Unknowns are confined to this file.
# pyright: reportMissingTypeStubs=false, reportUnknownVariableType=false
# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false

from __future__ import annotations

import gc
import logging
import traceback
from dataclasses import dataclass
from pathlib import Path
from time import monotonic_ns
from typing import Any, Protocol

import numpy as np
import torch
from pandas import Timestamp
from sklearn.utils.class_weight import compute_class_weight
from transformers import EarlyStoppingCallback, TrainingArguments, set_seed
from transformers.trainer_callback import PrinterCallback

from reddit.core.config import Config, Models, ModelSpec
from reddit.core.protocols import LogFn
from reddit.core.utils import dump_object, fmt_td
from reddit.data.preparation import DataBundle, load_and_prepare_data
from reddit.modeling.metrics import compute_metrics
from reddit.modeling.trainer import LogMetricsCallback, WeightedLossTrainer


@dataclass(frozen=True, slots=True)
class SeedContext:
    """Everything one (model, method) run needs that does not vary per seed.

    Attributes:
        config: Project configuration.
        models: The resolved family selection this model belongs to.
        model: The specific model spec (name + hub id) being fine-tuned.
        finetuning_method: ``"qdora"``/``"xqdora"`` for LLMs, ``"-"`` for BERT.
        run_name: Unique run identifier, embedded in log/output paths.
        cache_dir: Per-run training cache directory (``TrainingArguments.output_dir``).
        hf_cache: Hugging Face Hub cache directory for this model.
        seeds: Every seed this (model, method) run will iterate over.
        tokenizer: Tokenizer shared across all seeds of this run.
        log: Human-oriented progress logger.
    """

    config: Config
    models: Models
    model: ModelSpec
    finetuning_method: str
    run_name: str
    cache_dir: Path
    hf_cache: Path
    seeds: tuple[int, ...]
    tokenizer: Any
    log: LogFn


@dataclass(frozen=True, slots=True)
class SeedResult:
    """Outcome of fine-tuning one seed.

    Attributes:
        seed: The random seed used for this run (split + ``set_seed``).
        performance: Test-split weighted F1 (``float("nan")`` if unavailable),
            the ranking key for :func:`reddit.training.selection.select_median`.
        method: Fine-tuning method label (mirrors :attr:`SeedContext.finetuning_method`).
        model: Path to the saved checkpoint for this seed.
        config: The ``AutoConfig`` matching ``model``, kept so the selected
            checkpoint can be reloaded without re-deriving it.
    """

    seed: int
    performance: float
    method: str
    model: Path
    config: Any | None = None


class SeedStrategy(Protocol):
    """The kind-specific parts of one fine-tuning run."""

    def build_model(self, ctx: SeedContext, bundle: DataBundle) -> tuple[Any, Any]:
        """Return ``(model, model_config)`` ready for training."""
        ...

    def parameter_counts(self, model: Any) -> tuple[int | None, int | None]:
        """Return ``(trainable, total)`` parameter counts."""
        ...

    def tokenize(self, ctx: SeedContext, bundle: DataBundle) -> Any:
        """Return the tokenized ``DatasetDict``."""
        ...

    def data_collator(self, ctx: SeedContext) -> Any | None:
        """Return the collator, or ``None`` for the Trainer default."""
        ...

    def training_arguments(self, ctx: SeedContext) -> TrainingArguments:
        """Return the fully populated ``TrainingArguments``."""
        ...

    def optimizers(self, ctx: SeedContext, model: Any, /) -> tuple[Any, Any]:
        """Return the ``(optimizer, scheduler)`` pair."""
        ...


def _announce(ctx: SeedContext, message: str) -> None:
    """Emit a progress line to both the per-run log file and the root logger.

    Notes:
        Writes to the per-run log file (via ``ctx.log``, I/O) and to the
        root logger (I/O).
    """
    ctx.log(message, level="info")
    logging.info(message)


def _metrics_record(
    ctx: SeedContext,
    seed: int,
    split: str,
    walltime: int,
    trainable: int | None,
    total: int | None,
    raw: dict[str, Any],
    prefix: str,
) -> dict[str, Any]:
    """Assemble one JSONL metrics record, stripping the split prefix from keys.

    Args:
        ctx: Run-scoped context (model/method/config identity).
        seed: The seed this record belongs to.
        split: ``"train"`` (validation metrics) or ``"test"``.
        walltime: Training wall-clock time in nanoseconds for this seed.
        trainable: Trainable parameter count, or ``None`` if unavailable.
        total: Total parameter count, or ``None`` if unavailable.
        raw: The raw metrics dict from the Trainer (``eval_``/``test_``-prefixed keys).
        prefix: The key prefix to strip (``"eval_"`` or ``"test_"``).

    Returns:
        A flat dict combining run identity, parameter counts and the
        de-prefixed metric values, ready for :func:`reddit.core.utils.dump_object`.
    """
    return {
        "inserted": Timestamp.now("Europe/Rome").strftime("%Y-%m-%d.%H:%M:%S"),
        "date": ctx.config.system.date,
        "seed": seed,
        "model": ctx.model.name,
        "finetuning_method": ctx.finetuning_method,
        "trainable_parameters": trainable,
        "all_parameters": total,
        "split": split,
        "walltime": walltime,
    } | {m.replace(prefix, ""): v for m, v in raw.items()}


def run_seeds(ctx: SeedContext, strategy: SeedStrategy) -> dict[int, SeedResult]:
    """Fine-tune one model with one method over every configured seed.

    For each seed in ``ctx.seeds``: reloads and re-splits the gold dataset
    (:func:`reddit.data.preparation.load_and_prepare_data`, seeded so every
    seed sees a different stratified train/validation/test partition),
    builds the model and optimizer via ``strategy``, fine-tunes with
    :class:`reddit.modeling.trainer.WeightedLossTrainer` (class-weighted
    cross-entropy, balanced by ``sklearn.utils.class_weight``) under
    early stopping, evaluates, tests, and saves the checkpoint. A failure on
    one seed is caught and logged; remaining seeds still run.

    Args:
        ctx: Everything the run needs that does not vary per seed.
        strategy: The kind-specific behaviour (LLM PEFT vs. BERT full
            fine-tuning) — see :class:`reddit.training.llms.LlmSeedStrategy`
            and :class:`reddit.training.bert.BertSeedStrategy`.

    Returns:
        ``{seed: SeedResult}`` for every seed that completed training,
        keyed for :func:`reddit.training.selection.select_median`. Seeds
        that raised are simply absent from the mapping.

    Notes:
        Appends one JSONL record per seed to both a train/validation
        metrics dump and a test metrics dump under
        ``output_dir/{family}_{date}/`` (I/O), and frees GPU memory
        (``torch.cuda.empty_cache``/``gc.collect``) after every seed,
        success or failure.
    """
    config = ctx.config
    models_dir = config.paths.models_dir
    output_dir = config.paths.output_dir / f"{ctx.models.family}_{config.system.date}"
    output_dir.mkdir(parents=True, exist_ok=True)

    train_metrics_dump = output_dir / f"dist_{ctx.model.name}_train_metrics.jsonl"
    train_metrics_dump.touch(exist_ok=True)
    test_metrics_dump = output_dir / f"dist_{ctx.model.name}_test_metrics.jsonl"
    test_metrics_dump.touch(exist_ok=True)

    # BERT checkpoints are `{model}_{seed}`; LLM checkpoints carry the method.
    methodful = ctx.finetuning_method != "-"
    suffix = f"_{ctx.finetuning_method}" if methodful else ""
    label = f"{ctx.model.name}-{ctx.finetuning_method}" if methodful else ctx.model.name

    results: dict[int, SeedResult] = {}
    n_seeds = len(ctx.seeds)

    for k, seed in enumerate(ctx.seeds, start=1):
        set_seed(seed)

        try:
            bundle = load_and_prepare_data(
                data_file_path=config.dataset.path,
                text_column=config.dataset.text_column,
                label_column=config.dataset.label_column,
                test_size=config.dataset.test_size,
                validation_size=config.dataset.validation_size,
                random_seed=seed,
                labels=config.labels,
            )

            model, model_conf = strategy.build_model(ctx, bundle)
            trainable_parameters, all_parameters = strategy.parameter_counts(model)
            tokenized_dataset = strategy.tokenize(ctx, bundle)
            args = strategy.training_arguments(ctx)

            class_weights = compute_class_weight(
                "balanced",
                classes=np.arange(bundle.num_labels),
                y=bundle.dataset["train"]["label"],
            )

            callbacks = [
                EarlyStoppingCallback(config.training.early_stopping_patience, 0.0),
                LogMetricsCallback(
                    log_dir=config.paths.dumps_dir,
                    model_name=ctx.model.name,
                    finetuning_method=ctx.finetuning_method,
                    seed=seed,
                    date=config.system.date,
                ),
            ]

            trainer = WeightedLossTrainer(
                model=model,
                args=args,
                train_dataset=tokenized_dataset["train"],
                eval_dataset=tokenized_dataset["validation"],
                processing_class=ctx.tokenizer,
                data_collator=strategy.data_collator(ctx),
                compute_metrics=compute_metrics,
                class_weights=class_weights,
                optimizers=strategy.optimizers(ctx, model),
                callbacks=callbacks,
            )
            trainer.remove_callback(PrinterCallback)

            checkpoint = models_dir / f"{ctx.model.name}{suffix}_{seed}"

            _announce(ctx, f"Training `{label}` on seed {seed} ({k}/{n_seeds})...")
            t1 = monotonic_ns()
            trainer.train()
            walltime = monotonic_ns() - t1
            trainer.save_model(str(checkpoint))
            _announce(ctx, f"Training `{label}` on seed {seed} ({k}/{n_seeds})... done: it took {fmt_td(walltime)}.")

            _announce(ctx, f"Evaluating `{label}` on seed {seed} ({k}/{n_seeds})...")
            t1 = monotonic_ns()
            eval_metrics = trainer.evaluate()
            eval_metrics.pop("epoch", None)
            with open(train_metrics_dump, "ab") as f:
                f.write(
                    dump_object(
                        _metrics_record(
                            ctx, seed, "train", walltime, trainable_parameters, all_parameters, eval_metrics, "eval_"
                        )
                    )
                )
            _announce(
                ctx,
                f"Evaluating `{label}` on seed {seed} ({k}/{n_seeds})... done: it took {fmt_td(monotonic_ns() - t1)}.",
            )

            _announce(ctx, f"Testing `{label}` on seed {seed} ({k}/{n_seeds})...")
            t1 = monotonic_ns()
            test_pred = trainer.predict(tokenized_dataset["test"]).metrics or {}
            with open(test_metrics_dump, "ab") as f:
                f.write(
                    dump_object(
                        _metrics_record(
                            ctx, seed, "test", walltime, trainable_parameters, all_parameters, test_pred, "test_"
                        )
                    )
                )
            _announce(
                ctx,
                f"Testing `{label}` on seed {seed} ({k}/{n_seeds})... done: it took {fmt_td(monotonic_ns() - t1)}.",
            )

            results[seed] = SeedResult(
                seed=seed,
                performance=float(test_pred.get("test_f1_weighted", float("nan"))),
                method=ctx.finetuning_method,
                model=checkpoint,
                config=model_conf,
            )

        except Exception as e:
            ctx.log(
                f"Something unexpected occurred while running `{ctx.run_name}`: {e}\n{traceback.format_exc()}",
                level="critical",
            )
            logging.critical(f"Something unexpected occurred while running `{ctx.run_name}`: {e}", exc_info=True)

        finally:
            torch.cuda.empty_cache()
            gc.collect()

    return results
