"""Contract tests for :mod:`reddit.modeling.trainer`.

``WeightedLossTrainer`` is exercised against a four-parameter CPU module: the
loss arithmetic is the contract, not the network.  No checkpoint is downloaded
and ``use_cpu=True`` keeps the Trainer off the GPU.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest
import torch
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from torch import nn
from torch.nn import functional as F
from transformers import TrainerControl, TrainerState, TrainingArguments
from transformers.modeling_outputs import SequenceClassifierOutput

from reddit.modeling.trainer import LogMetricsCallback, WeightedLossTrainer

NUM_CLASSES = 3
FEATURES = 4


class TinyClassifier(nn.Module):
    """A linear head that returns the loss the base Trainer expects."""

    def __init__(self) -> None:
        super().__init__()
        self.linear = nn.Linear(FEATURES, NUM_CLASSES)

    def forward(self, x: torch.Tensor, labels: torch.Tensor, **_: Any) -> SequenceClassifierOutput:
        logits = self.linear(x)
        loss = cast("Any", F.cross_entropy(logits, labels))
        return SequenceClassifierOutput(loss=loss, logits=logits)


class TestTrainerModule:
    """Class-weighted loss and the JSONL metrics callback."""

    @pytest.fixture
    def model(self) -> TinyClassifier:
        """A deterministic tiny classifier."""
        torch.manual_seed(0)
        return TinyClassifier()

    @pytest.fixture
    def training_arguments(self, tmp_path: Path) -> TrainingArguments:
        """CPU-only training arguments; ``fp16`` would require CUDA."""
        return TrainingArguments(
            output_dir=str(tmp_path / "run"),
            report_to=[],
            use_cpu=True,
            fp16=False,
            gradient_accumulation_steps=8,
        )

    @pytest.fixture
    def batch(self) -> dict[str, torch.Tensor]:
        """Five examples covering every class."""
        torch.manual_seed(1)
        return {"x": torch.randn(5, FEATURES), "labels": torch.tensor([0, 1, 2, 0, 1])}

    @pytest.mark.unit
    class TestUnits:
        def test_the_unweighted_loss_is_the_mean_cross_entropy(
            self, model: TinyClassifier, training_arguments: TrainingArguments, batch: dict[str, torch.Tensor]
        ) -> None:
            trainer = WeightedLossTrainer(model=model, args=training_arguments)
            expected = F.cross_entropy(model(**batch).logits, batch["labels"])

            loss = trainer.compute_loss(model, dict(batch))

            assert float(loss.detach()) == pytest.approx(float(expected.detach()), rel=1e-6)

        def test_class_weights_reproduce_the_weighted_mean(
            self, model: TinyClassifier, training_arguments: TrainingArguments, batch: dict[str, torch.Tensor]
        ) -> None:
            weights = [1.0, 2.0, 3.0]
            trainer = WeightedLossTrainer(model=model, args=training_arguments, class_weights=weights)
            expected = F.cross_entropy(model(**batch).logits, batch["labels"], weight=torch.tensor(weights))

            loss = trainer.compute_loss(model, dict(batch))

            assert float(loss.detach()) == pytest.approx(float(expected.detach()), rel=1e-6)

        def test_weighting_shifts_the_loss_away_from_the_unweighted_one(
            self, model: TinyClassifier, training_arguments: TrainingArguments, batch: dict[str, torch.Tensor]
        ) -> None:
            plain = WeightedLossTrainer(model=model, args=training_arguments)
            weighted = WeightedLossTrainer(model=model, args=training_arguments, class_weights=[1.0, 5.0, 9.0])

            assert float(plain.compute_loss(model, dict(batch)).detach()) != pytest.approx(
                float(weighted.compute_loss(model, dict(batch)).detach())
            )

        def test_gradient_accumulation_normalises_by_the_effective_batch(
            self, model: TinyClassifier, training_arguments: TrainingArguments, batch: dict[str, torch.Tensor]
        ) -> None:
            """The Trainer divides by the accumulation count, so pre-multiply by it."""
            trainer = WeightedLossTrainer(model=model, args=training_arguments)
            summed = F.cross_entropy(model(**batch).logits, batch["labels"], reduction="sum")
            expected = summed * training_arguments.gradient_accumulation_steps / 5

            loss = trainer.compute_loss(model, dict(batch), num_items_in_batch=5)

            assert float(loss.detach()) == pytest.approx(float(expected.detach()), rel=1e-6)

        def test_the_accumulated_loss_scales_with_the_effective_batch_size(
            self, model: TinyClassifier, training_arguments: TrainingArguments, batch: dict[str, torch.Tensor]
        ) -> None:
            trainer = WeightedLossTrainer(model=model, args=training_arguments)

            small = float(trainer.compute_loss(model, dict(batch), num_items_in_batch=5).detach())
            large = float(trainer.compute_loss(model, dict(batch), num_items_in_batch=10).detach())

            assert small == pytest.approx(2 * large, rel=1e-6)

        def test_requesting_outputs_returns_the_forward_pass(
            self, model: TinyClassifier, training_arguments: TrainingArguments, batch: dict[str, torch.Tensor]
        ) -> None:
            trainer = WeightedLossTrainer(model=model, args=training_arguments)

            loss, outputs = trainer.compute_loss(model, dict(batch), return_outputs=True)

            assert outputs.logits.shape == (5, NUM_CLASSES)
            assert float(loss.detach()) > 0

        def test_the_returned_loss_is_differentiable(
            self, model: TinyClassifier, training_arguments: TrainingArguments, batch: dict[str, torch.Tensor]
        ) -> None:
            trainer = WeightedLossTrainer(model=model, args=training_arguments)

            loss = trainer.compute_loss(model, dict(batch))
            loss.backward()

            assert model.linear.weight.grad is not None

        def test_a_single_example_batch_is_handled(
            self, model: TinyClassifier, training_arguments: TrainingArguments
        ) -> None:
            single = {"x": torch.randn(1, FEATURES), "labels": torch.tensor([2])}
            trainer = WeightedLossTrainer(model=model, args=training_arguments, class_weights=[1.0, 1.0, 1.0])

            loss = trainer.compute_loss(model, dict(single))

            expected = F.cross_entropy(model(**single).logits, single["labels"])
            assert float(loss.detach()) == pytest.approx(float(expected.detach()))

    @pytest.mark.integration
    class TestIntegration:
        def test_the_metrics_file_is_created_on_construction(self, tmp_path: Path) -> None:
            callback = LogMetricsCallback(log_dir=tmp_path, model_name="gemma2_9b", seed=7, date="20240102")

            assert (tmp_path / "gemma2_9b_training_logs.jsonl").is_file()
            assert callback.log_path == tmp_path / "gemma2_9b_training_logs.jsonl"

        def test_the_method_appears_in_the_filename_when_there_is_one(self, tmp_path: Path) -> None:
            LogMetricsCallback(
                log_dir=tmp_path, model_name="gemma2_9b", seed=7, date="20240102", finetuning_method="qdora"
            )

            assert (tmp_path / "gemma2_9b_qdora_training_logs.jsonl").is_file()

        def test_full_finetuning_leaves_the_filename_unqualified(self, tmp_path: Path) -> None:
            LogMetricsCallback(log_dir=tmp_path, model_name="finbert", seed=7, date="20240102", finetuning_method="-")

            assert (tmp_path / "finbert_training_logs.jsonl").is_file()

        def test_an_evaluation_log_is_appended_with_its_metadata(
            self, tmp_path: Path, training_arguments: TrainingArguments
        ) -> None:
            callback = LogMetricsCallback(
                log_dir=tmp_path, model_name="gemma2_9b", seed=7, date="20240102", finetuning_method="qdora"
            )
            state = TrainerState()
            state.epoch = 3.0
            state.global_step = 42

            callback.on_log(
                training_arguments,
                state,
                TrainerControl(),
                logs={"eval_loss": 0.25, "eval_f1_weighted": 0.9},
            )

            record = json.loads((tmp_path / "gemma2_9b_qdora_training_logs.jsonl").read_text(encoding="utf-8"))
            assert record["model"] == "gemma2_9b"
            assert record["seed"] == 7
            assert record["date"] == "20240102"
            assert record["finetuning_method"] == "qdora"
            assert record["split"] == "eval"
            assert record["epoch"] == 3.0
            assert record["step"] == 42
            assert record["loss"] == 0.25
            assert record["f1_weighted"] == 0.9
            assert "timestamp" in record

        def test_successive_evaluations_accumulate_as_jsonl(
            self, tmp_path: Path, training_arguments: TrainingArguments
        ) -> None:
            callback = LogMetricsCallback(log_dir=tmp_path, model_name="m", seed=1, date="20240102")

            for step in (1, 2, 3):
                state = TrainerState()
                state.global_step = step
                callback.on_log(training_arguments, state, TrainerControl(), logs={"eval_loss": 0.1 * step})

            lines = (tmp_path / "m_training_logs.jsonl").read_text(encoding="utf-8").splitlines()
            assert [json.loads(line)["step"] for line in lines] == [1, 2, 3]

        def test_training_logs_without_an_evaluation_are_ignored(
            self, tmp_path: Path, training_arguments: TrainingArguments
        ) -> None:
            callback = LogMetricsCallback(log_dir=tmp_path, model_name="m", seed=1, date="20240102")

            callback.on_log(training_arguments, TrainerState(), TrainerControl(), logs={"loss": 0.5})

            assert (tmp_path / "m_training_logs.jsonl").read_text(encoding="utf-8") == ""

        def test_an_absent_log_payload_is_ignored(self, tmp_path: Path, training_arguments: TrainingArguments) -> None:
            callback = LogMetricsCallback(log_dir=tmp_path, model_name="m", seed=1, date="20240102")

            callback.on_log(training_arguments, TrainerState(), TrainerControl(), logs=None)

            assert (tmp_path / "m_training_logs.jsonl").read_text(encoding="utf-8") == ""

        def test_replica_processes_never_write(self, tmp_path: Path, training_arguments: TrainingArguments) -> None:
            callback = LogMetricsCallback(log_dir=tmp_path, model_name="m", seed=1, date="20240102")
            state = TrainerState()
            state.is_world_process_zero = False

            callback.on_log(training_arguments, state, TrainerControl(), logs={"eval_loss": 0.5})

            assert (tmp_path / "m_training_logs.jsonl").read_text(encoding="utf-8") == ""

        def test_the_control_object_is_handed_back_unchanged(
            self, tmp_path: Path, training_arguments: TrainingArguments
        ) -> None:
            callback = LogMetricsCallback(log_dir=tmp_path, model_name="m", seed=1, date="20240102")
            control = TrainerControl()

            returned = callback.on_log(training_arguments, TrainerState(), control, logs={"eval_loss": 0.1})

            assert returned is control

        def test_an_unwritable_log_path_does_not_interrupt_training(
            self, tmp_path: Path, training_arguments: TrainingArguments, capsys: pytest.CaptureFixture[str]
        ) -> None:
            callback = LogMetricsCallback(log_dir=tmp_path, model_name="m", seed=1, date="20240102")
            callback.log_path.unlink()
            callback.log_path.mkdir()

            callback.on_log(training_arguments, TrainerState(), TrainerControl(), logs={"eval_loss": 0.1})

            assert "Error writing to log file" in capsys.readouterr().out

        def test_a_string_log_directory_is_accepted(self, tmp_path: Path) -> None:
            callback = LogMetricsCallback(log_dir=str(tmp_path), model_name="m", seed=1, date="20240102")

            assert callback.log_path.is_file()

    @pytest.mark.contracts
    class TestContracts:
        @settings(deadline=None, max_examples=20, suppress_health_check=[HealthCheck.function_scoped_fixture])
        @given(labels=st.lists(st.integers(0, NUM_CLASSES - 1), min_size=1, max_size=8))
        def test_the_weighted_loss_always_matches_torch_cross_entropy(
            self, labels: list[int], model: TinyClassifier, training_arguments: TrainingArguments
        ) -> None:
            weights = [1.0, 2.0, 4.0]
            inputs = {"x": torch.ones(len(labels), FEATURES), "labels": torch.tensor(labels)}
            trainer = WeightedLossTrainer(model=model, args=training_arguments, class_weights=weights)
            expected = F.cross_entropy(model(**inputs).logits, inputs["labels"], weight=torch.tensor(weights))

            loss = trainer.compute_loss(model, dict(inputs))

            assert float(loss.detach()) == pytest.approx(float(expected.detach()), rel=1e-5)

        @settings(deadline=None, max_examples=20, suppress_health_check=[HealthCheck.function_scoped_fixture])
        @given(
            labels=st.lists(st.integers(0, NUM_CLASSES - 1), min_size=1, max_size=8),
            items=st.integers(min_value=1, max_value=32),
        )
        def test_the_accumulated_loss_is_inversely_proportional_to_the_item_count(
            self, labels: list[int], items: int, model: TinyClassifier, training_arguments: TrainingArguments
        ) -> None:
            inputs = {"x": torch.ones(len(labels), FEATURES), "labels": torch.tensor(labels)}
            trainer = WeightedLossTrainer(model=model, args=training_arguments)
            reference = float(trainer.compute_loss(model, dict(inputs), num_items_in_batch=1).detach())

            loss = float(trainer.compute_loss(model, dict(inputs), num_items_in_batch=items).detach())

            assert loss == pytest.approx(reference / items, rel=1e-5)

        @settings(deadline=None, max_examples=20, suppress_health_check=[HealthCheck.function_scoped_fixture])
        @given(scale=st.floats(min_value=0.5, max_value=4.0))
        def test_scaling_every_class_weight_leaves_the_evaluation_loss_unchanged(
            self, scale: float, model: TinyClassifier, training_arguments: TrainingArguments
        ) -> None:
            """The evaluation path normalises by the summed weights."""
            inputs = {"x": torch.ones(6, FEATURES), "labels": torch.tensor([0, 1, 2, 0, 1, 2])}
            base = WeightedLossTrainer(model=model, args=training_arguments, class_weights=[1.0, 2.0, 3.0])
            scaled = WeightedLossTrainer(
                model=model, args=training_arguments, class_weights=[scale, 2 * scale, 3 * scale]
            )

            assert float(base.compute_loss(model, dict(inputs)).detach()) == pytest.approx(
                float(scaled.compute_loss(model, dict(inputs)).detach()), rel=1e-5
            )
