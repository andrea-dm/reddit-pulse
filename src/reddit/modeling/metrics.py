"""Evaluation metrics for single-label multi-class classification."""

# sklearn ships no type stubs; the resulting Unknowns are confined to this file.
# pyright: reportMissingTypeStubs=false, reportUnknownVariableType=false
# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false

from __future__ import annotations

from typing import Any

import torch
from numpy import argmax
from sklearn.metrics import (
    accuracy_score,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)


def compute_metrics(eval_pred: Any) -> dict[str, float]:
    """Compute accuracy, weighted/macro F1, macro recall/precision and ROC-AUC.

    Args:
        eval_pred: A ``transformers.EvalPrediction`` (or ``(predictions,
            label_ids)`` pair) — typed ``Any`` at this boundary because the
            transformers callback API is untyped.
    """
    predictions, labels = eval_pred
    preds = argmax(predictions, axis=1)

    # roc_auc_score needs probabilities
    probs = torch.nn.functional.softmax(torch.from_numpy(predictions), dim=-1).numpy()

    try:
        auc = float(roc_auc_score(labels, probs, multi_class="ovr", average="macro"))
    except ValueError:
        # a class may be absent from this evaluation batch
        auc = float("nan")

    # ``zero_division`` has no annotation in sklearn's own signature — only the
    # default value "warn" — so pyright infers `str` from that default and rejects
    # a numeric override. sklearn's docs and runtime validation
    # (``_check_zero_division``) explicitly accept 0, 1 or ``np.nan`` too; typing
    # this as ``Any`` documents that gap in one place instead of four.
    # ``0`` means "score an undefined metric (e.g. a class with no predicted or
    # true samples) as 0" rather than emitting an ``UndefinedMetricWarning``.
    zero_division: Any = 0

    return {
        "accuracy": float(accuracy_score(labels, preds)),
        "f1_weighted": float(f1_score(y_true=labels, y_pred=preds, average="weighted", zero_division=zero_division)),
        "f1_macro": float(f1_score(y_true=labels, y_pred=preds, average="macro", zero_division=zero_division)),
        "recall_macro": float(recall_score(y_true=labels, y_pred=preds, average="macro", zero_division=zero_division)),
        "precision_macro": float(
            precision_score(y_true=labels, y_pred=preds, average="macro", zero_division=zero_division)
        ),
        "roc_auc": auc,
    }
