"""Contract tests for :mod:`reddit.modeling.metrics`.

``compute_metrics`` is the callback the Trainer hands its raw logits to; the
key set it returns is what ``config.training.arguments.metric_for_best_model``
selects from and what the JSONL metric dumps record.
"""

from __future__ import annotations

from math import isnan
from typing import Any

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from hypothesis.extra.numpy import arrays

from reddit.modeling.metrics import compute_metrics

METRIC_KEYS = {"accuracy", "f1_weighted", "f1_macro", "recall_macro", "precision_macro", "roc_auc"}

LOGITS = arrays(
    dtype=np.float32,
    shape=st.tuples(st.integers(min_value=3, max_value=12), st.just(3)),
    elements=st.floats(min_value=-8, max_value=8, width=32),
)

# Rows of pairwise-distinct integers: the argmax has a margin of at least 1.0,
# so it cannot flip under float32 rounding.
SEPARATED_LOGIT_ROWS = st.lists(
    st.lists(st.integers(min_value=-8, max_value=8), min_size=3, max_size=3, unique=True),
    min_size=3,
    max_size=12,
)


def one_hot(labels: list[int], classes: int = 3, confidence: float = 6.0) -> np.ndarray:
    """Logits whose argmax is exactly ``labels``."""
    logits = np.full((len(labels), classes), -confidence, dtype=np.float32)
    for row, label in enumerate(labels):
        logits[row, label] = confidence
    return logits


class TestMetricsModule:
    """Evaluation metrics for single-label multi-class classification."""

    @pytest.mark.unit
    class TestUnits:
        def test_every_documented_metric_is_reported(self) -> None:
            labels = [0, 1, 2, 0, 1, 2]

            metrics = compute_metrics((one_hot(labels), np.array(labels)))

            assert set(metrics) == METRIC_KEYS
            assert all(isinstance(value, float) for value in metrics.values())

        def test_perfect_predictions_score_one(self) -> None:
            labels = [0, 1, 2, 0, 1, 2]

            metrics = compute_metrics((one_hot(labels), np.array(labels)))

            assert metrics["accuracy"] == 1.0
            assert metrics["f1_weighted"] == 1.0
            assert metrics["f1_macro"] == 1.0
            assert metrics["recall_macro"] == 1.0
            assert metrics["precision_macro"] == 1.0
            assert metrics["roc_auc"] == 1.0

        def test_accuracy_counts_the_matching_rows(self) -> None:
            labels = np.array([0, 1, 2, 0])
            predictions = one_hot([0, 1, 2, 1])

            metrics = compute_metrics((predictions, labels))

            assert metrics["accuracy"] == 0.75

        def test_a_constant_classifier_scores_the_majority_rate(self) -> None:
            labels = np.array([0, 0, 0, 1])
            predictions = one_hot([0, 0, 0, 0])

            metrics = compute_metrics((predictions, labels))

            assert metrics["accuracy"] == 0.75
            assert metrics["f1_macro"] < metrics["f1_weighted"]

        def test_the_area_under_the_curve_is_nan_when_a_class_is_absent(self) -> None:
            """A class may be missing from a small evaluation batch."""
            labels = np.array([1, 1, 1, 1])

            metrics = compute_metrics((one_hot([1, 1, 1, 0]), labels))

            assert isnan(metrics["roc_auc"])
            assert not isnan(metrics["accuracy"])

        def test_an_eval_prediction_like_object_is_accepted(self) -> None:
            labels = [0, 1, 2]

            class EvalPredictionDouble:
                def __iter__(self) -> Any:
                    return iter((one_hot(labels), np.array(labels)))

            metrics = compute_metrics(EvalPredictionDouble())

            assert metrics["accuracy"] == 1.0

        def test_a_plain_list_pair_is_accepted(self) -> None:
            labels = [0, 1, 2]

            metrics = compute_metrics([one_hot(labels), np.array(labels)])

            assert metrics["accuracy"] == 1.0

        def test_confidence_does_not_change_the_hard_decisions(self) -> None:
            labels = np.array([0, 1, 2, 0, 1, 2])
            confident = compute_metrics((one_hot(list(labels), confidence=10.0), labels))
            timid = compute_metrics((one_hot(list(labels), confidence=0.1), labels))

            assert confident["accuracy"] == timid["accuracy"]
            assert confident["f1_weighted"] == timid["f1_weighted"]

    @pytest.mark.contracts
    class TestContracts:
        @settings(deadline=None, max_examples=40)
        @given(logits=LOGITS, data=st.data())
        def test_every_metric_stays_within_its_range(self, logits: np.ndarray, data: st.DataObject) -> None:
            labels = np.array(
                data.draw(st.lists(st.integers(0, 2), min_size=len(logits), max_size=len(logits))),
            )

            metrics = compute_metrics((logits, labels))

            for name, value in metrics.items():
                assert isnan(value) or 0.0 <= value <= 1.0, name

        @settings(deadline=None, max_examples=40)
        @given(labels=st.lists(st.integers(0, 2), min_size=4, max_size=12))
        def test_shuffling_the_rows_leaves_every_metric_unchanged(self, labels: list[int]) -> None:
            predictions = one_hot([(label + 1) % 3 for label in labels])
            order = np.random.default_rng(0).permutation(len(labels))

            straight = compute_metrics((predictions, np.array(labels)))
            shuffled = compute_metrics((predictions[order], np.array(labels)[order]))

            for name in METRIC_KEYS:
                assert (isnan(straight[name]) and isnan(shuffled[name])) or straight[name] == pytest.approx(
                    shuffled[name]
                ), name

        @settings(deadline=None, max_examples=40)
        @given(rows=SEPARATED_LOGIT_ROWS, shift=st.floats(min_value=-5, max_value=5))
        def test_shifting_every_logit_of_a_row_changes_nothing(self, rows: list[list[int]], shift: float) -> None:
            """Softmax is shift-invariant per row, and so is the argmax.

            The drawn logits are integers, so the decision has a margin of at
            least 1.0 and cannot flip on float32 rounding.
            """
            logits = np.array(rows, dtype=np.float32)
            labels = np.array([i % 3 for i in range(len(logits))])
            shifted = logits + np.float32(shift)

            straight = compute_metrics((logits, labels))
            translated = compute_metrics((shifted, labels))

            for name in METRIC_KEYS:
                assert (isnan(straight[name]) and isnan(translated[name])) or straight[name] == pytest.approx(
                    translated[name], abs=1e-6
                ), name

        @settings(deadline=None, max_examples=40)
        @given(labels=st.lists(st.integers(0, 2), min_size=4, max_size=12))
        def test_perfect_predictions_always_score_one(self, labels: list[int]) -> None:
            metrics = compute_metrics((one_hot(labels), np.array(labels)))

            assert metrics["accuracy"] == 1.0
            assert metrics["f1_weighted"] == pytest.approx(1.0)
