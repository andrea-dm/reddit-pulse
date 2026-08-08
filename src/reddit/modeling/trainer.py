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
    """Trainer applying class weights in the cross-entropy loss."""

    def __init__(self, *args, class_weights=None, **kwargs):
        super().__init__(*args, **kwargs)
        if class_weights is not None:
            self.class_weights = torch.tensor(class_weights, dtype=torch.float).to(self.args.device)
        else:
            self.class_weights = None

    def compute_loss(self, model, inputs, return_outputs=False, num_items_in_batch=None, **kwargs) -> Any:
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
