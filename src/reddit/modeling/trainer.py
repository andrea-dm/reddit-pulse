"""Custom Trainer with class-weighted loss and JSONL metrics logging callback."""

# transformers' Trainer API is largely untyped; the Unknowns stay in this file.
# pyright: reportUnknownVariableType=false, reportUnknownMemberType=false
# pyright: reportUnknownArgumentType=false, reportUnknownParameterType=false
# pyright: reportMissingParameterType=false

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import torch
from transformers import (
    Trainer,
    TrainerCallback,
    TrainerControl,
    TrainerState,
    TrainingArguments,
)

from reddit.core.utils import dump_object, now


class WeightedLossTrainer(Trainer):
    """Trainer applying class weights in the cross-entropy loss.

    Used by :func:`reddit.training.loop.run_seeds` for both the decoder-LLM
    (PEFT) and BERT (full fine-tuning) pipelines, with class weights
    balanced against the gold dataset's label distribution (down/neutral/up
    are not necessarily equally represented).
    """

    def __init__(self, *args, class_weights=None, **kwargs):
        """Construct the trainer, moving ``class_weights`` onto the training device.

        Args:
            *args: Forwarded to ``transformers.Trainer``.
            class_weights: Per-class loss weights (e.g. from
                ``sklearn.utils.class_weight.compute_class_weight``), or
                ``None`` for unweighted cross-entropy.
            **kwargs: Forwarded to ``transformers.Trainer``.
        """
        super().__init__(*args, **kwargs)
        if class_weights is not None:
            self.class_weights = torch.tensor(class_weights, dtype=torch.float).to(self.args.device)
        else:
            self.class_weights = None

    def compute_loss(self, model, inputs, return_outputs=False, num_items_in_batch=None, **kwargs) -> Any:
        """Compute class-weighted cross-entropy loss, gradient-accumulation-safe.

        Args:
            model: The model being trained.
            inputs: Batch inputs, including ``"labels"`` (popped from the dict).
            return_outputs: Also return the model's forward-pass outputs.
            num_items_in_batch: Effective batch size across accumulated
                micro-batches, if gradient accumulation is active.
            **kwargs: Forwarded to the base ``compute_loss`` call.

        Returns:
            The scalar loss, or ``(loss, outputs)`` if ``return_outputs``.

        Notes:
            Under gradient accumulation, pre-multiplies the summed loss by
            the accumulation step count so the Trainer's subsequent
            division by ``num_items_in_batch`` yields the true full-batch
            average rather than an unweighted mean of per-micro-batch
            means (see inline rationale below).
        """
        # The base loss is discarded; only the forward pass output is needed.
        # Rationale: with return_outputs=True the base method returns a
        # (loss, outputs) tuple, but its declared type is the bare union.
        _, outputs = cast(
            "tuple[Any, Any]",
            super().compute_loss(model, inputs, return_outputs=True, num_items_in_batch=num_items_in_batch, **kwargs),
        )
        labels = inputs.pop("labels")
        logits = outputs.get("logits")

        flat_labels = labels.view(-1)
        loss_fct = torch.nn.CrossEntropyLoss(weight=self.class_weights, reduction="sum")
        loss = loss_fct(logits.view(-1, logits.size(-1)), flat_labels)

        if num_items_in_batch is not None:
            # Under gradient accumulation the Trainer divides the returned loss
            # by the accumulation step count (sequence-classification models do
            # not accept loss kwargs). Pre-multiplying by that count and
            # normalizing by the effective batch size makes the accumulated
            # gradient the true full-batch average; the previous per-micro-batch
            # mean over-weighted small trailing batches.
            gas = getattr(self, "current_gradient_accumulation_steps", self.args.gradient_accumulation_steps)
            loss = loss * gas / num_items_in_batch
        else:
            # Evaluation/prediction path: reproduce reduction="mean" exactly
            # (weighted mean normalizes by the summed class weights).
            if self.class_weights is not None:
                loss = loss / self.class_weights[flat_labels].sum()
            else:
                loss = loss / flat_labels.numel()

        return (loss, outputs) if return_outputs else loss


class LogMetricsCallback(TrainerCallback):
    """Append validation metrics to a JSONL file at each logging step."""

    def __init__(self, log_dir: str | Path, model_name: str, seed: int, date: str, finetuning_method: str = "-"):
        """Resolve the per-model JSONL log path and cache the run's metadata.

        Args:
            log_dir: Directory the JSONL log is written under.
            model_name: Short model name, embedded in the log filename.
            seed: Seed of the run this callback is attached to.
            date: Run-date stamp, recorded in every logged record.
            finetuning_method: Method label; ``"-"`` (BERT) omits the method
                suffix from the log filename.

        Notes:
            Creates (touches) the JSONL log file on disk.
        """
        super().__init__()
        ft = f"_{finetuning_method}" if finetuning_method != "-" else ""
        self.log_path = Path(log_dir) / f"{model_name}{ft}_training_logs.jsonl"
        self.log_path.touch(exist_ok=True)
        self.metadata: dict[str, object] = {
            "model": model_name,
            "seed": seed,
            "date": date,
            "finetuning_method": finetuning_method,
        }

    def on_log(  # pyright: ignore[reportIncompatibleMethodOverride] - base method is untyped
        self,
        args: TrainingArguments,
        state: TrainerState,
        control: TrainerControl,
        logs: dict[str, float] | None = None,
        **kwargs,
    ) -> TrainerControl:
        """Append one JSONL record for this logging step, if it is an eval log.

        Args:
            args: The active ``TrainingArguments``.
            state: Current trainer state (epoch, global step).
            control: Trainer control flags, returned unmodified.
            logs: The metrics dict for this logging event, or ``None``.
            **kwargs: Unused; accepted for base-class signature compatibility.

        Returns:
            ``control``, unmodified.

        Notes:
            Appends to ``self.log_path`` (I/O) only when ``logs`` contains
            ``"eval_loss"`` and this is the main process
            (``state.is_world_process_zero``); other logging events are
            ignored. Errors writing the file are caught and printed rather
            than raised, so a logging failure cannot abort training.
        """
        # only evaluation logs (they contain 'eval_loss'), only from the main process
        if logs is not None and "eval_loss" in logs and state.is_world_process_zero:
            metrics: dict[str, object] = {
                "timestamp": now(),
                **self.metadata,
                "epoch": state.epoch,
                "step": state.global_step,
                "split": "eval",
                **{k.replace("eval_", ""): v for k, v in logs.items()},
            }
            try:
                with open(self.log_path, "ab") as f:
                    f.write(dump_object(metrics))
            except Exception as e:
                print(f"Error writing to log file: {e}")
        return control
