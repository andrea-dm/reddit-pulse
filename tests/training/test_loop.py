"""Contract tests for :mod:`reddit.training.loop`.

Real fine-tuning is out of scope (no checkpoint download, no GPU): what is
verified here is the loop's *resilience* contract — a seed that blows up must
not abort the remaining seeds — plus the immutability of the two dataclasses
the strategies exchange.
"""

from __future__ import annotations

import dataclasses
import logging
from pathlib import Path
from typing import Any

import pytest
from pandas import DataFrame

from reddit.core.config import Config, ModelSpec
from reddit.training.loop import SeedContext, SeedResult, run_seeds

from ..conftest import RecordingLog


class ExplodingStrategy:
    """A :class:`reddit.training.loop.SeedStrategy` that fails at model build."""

    def __init__(self, message: str = "no GPU available") -> None:
        self.message = message
        self.build_calls = 0

    def build_model(self, ctx: Any, bundle: Any) -> tuple[Any, Any]:
        self.build_calls += 1
        raise RuntimeError(self.message)

    def parameter_counts(self, model: Any) -> tuple[int | None, int | None]:  # pragma: no cover - unreachable
        return None, None

    def tokenize(self, ctx: Any, bundle: Any) -> Any:  # pragma: no cover - unreachable
        return bundle

    def data_collator(self, ctx: Any) -> Any:  # pragma: no cover - unreachable
        return None

    def training_arguments(self, ctx: Any) -> Any:  # pragma: no cover - unreachable
        raise NotImplementedError

    def optimizers(self, ctx: Any, model: Any) -> tuple[Any, Any]:  # pragma: no cover - unreachable
        return None, None


class TestLoopModule:
    """The multi-seed fine-tuning loop and its data carriers."""

    @pytest.fixture
    def gold_dataset(self, bootstrapped_config: Config) -> Path:
        """A small, balanced gold file at the configured dataset path."""
        rows = 60
        frame = DataFrame(
            {
                "title": [f"headline number {i}" for i in range(rows)],
                "Label": [["down", "neutral", "up"][i % 3] for i in range(rows)],
            }
        )
        frame.to_excel(bootstrapped_config.dataset.path, index=False)
        return bootstrapped_config.dataset.path

    @pytest.fixture
    def seed_context(self, bootstrapped_config: Config, recording_log: RecordingLog) -> SeedContext:
        """A context for two seeds of one LLM model."""
        return SeedContext(
            config=bootstrapped_config,
            models=bootstrapped_config.family("llm_family"),
            model=ModelSpec(name="tiny_llm", id="acme/tiny-llm"),
            finetuning_method="qdora",
            run_name="tiny_llm_qdora_20240102_000000",
            cache_dir=bootstrapped_config.paths.cache_dir / "run",
            hf_cache=bootstrapped_config.paths.cache_dir / "hf",
            seeds=(11, 22),
            tokenizer=object(),
            log=recording_log,
        )

    @pytest.mark.unit
    class TestUnits:
        def test_a_seed_context_is_immutable(self, seed_context: SeedContext) -> None:
            with pytest.raises(dataclasses.FrozenInstanceError):
                seed_context.finetuning_method = "xqdora"  # pyright: ignore[reportAttributeAccessIssue]

        def test_a_seed_result_is_immutable(self, tmp_path: Path) -> None:
            result = SeedResult(seed=1, performance=0.5, method="qdora", model=tmp_path)

            with pytest.raises(dataclasses.FrozenInstanceError):
                result.performance = 0.9  # pyright: ignore[reportAttributeAccessIssue]

        def test_a_seed_result_defaults_to_no_model_config(self, tmp_path: Path) -> None:
            assert SeedResult(seed=1, performance=0.5, method="qdora", model=tmp_path).config is None

        def test_seed_results_compare_by_value(self, tmp_path: Path) -> None:
            first = SeedResult(seed=1, performance=0.5, method="qdora", model=tmp_path)
            second = SeedResult(seed=1, performance=0.5, method="qdora", model=tmp_path)

            assert first == second

    @pytest.mark.integration
    class TestIntegration:
        def test_a_failing_seed_does_not_abort_the_remaining_seeds(
            self, seed_context: SeedContext, gold_dataset: Path, recording_log: RecordingLog
        ) -> None:
            strategy = ExplodingStrategy()

            results = run_seeds(seed_context, strategy)

            assert results == {}
            assert strategy.build_calls == len(seed_context.seeds)

        def test_each_failure_is_reported_once(
            self, seed_context: SeedContext, gold_dataset: Path, recording_log: RecordingLog
        ) -> None:
            run_seeds(seed_context, ExplodingStrategy("no GPU available"))

            critical = recording_log.messages_at("critical")
            assert len(critical) == len(seed_context.seeds)
            assert all("no GPU available" in message for message in critical)
            assert all(seed_context.run_name in message for message in critical)

        def test_the_failure_is_also_recorded_in_the_root_log(
            self, seed_context: SeedContext, gold_dataset: Path, caplog: pytest.LogCaptureFixture
        ) -> None:
            with caplog.at_level(logging.CRITICAL):
                run_seeds(seed_context, ExplodingStrategy())

            assert "Something unexpected occurred" in caplog.text

        def test_the_metric_dumps_are_created_per_family_and_date(
            self, seed_context: SeedContext, gold_dataset: Path
        ) -> None:
            config = seed_context.config

            run_seeds(seed_context, ExplodingStrategy())

            output_dir = config.paths.output_dir / f"llm_family_{config.system.date}"
            assert (output_dir / "dist_tiny_llm_train_metrics.jsonl").is_file()
            assert (output_dir / "dist_tiny_llm_test_metrics.jsonl").is_file()

        def test_the_metric_dumps_stay_empty_when_no_seed_completes(
            self, seed_context: SeedContext, gold_dataset: Path
        ) -> None:
            config = seed_context.config

            run_seeds(seed_context, ExplodingStrategy())

            output_dir = config.paths.output_dir / f"llm_family_{config.system.date}"
            assert (output_dir / "dist_tiny_llm_train_metrics.jsonl").read_bytes() == b""

        def test_an_existing_metric_dump_is_not_truncated(self, seed_context: SeedContext, gold_dataset: Path) -> None:
            config = seed_context.config
            output_dir = config.paths.output_dir / f"llm_family_{config.system.date}"
            output_dir.mkdir(parents=True)
            (output_dir / "dist_tiny_llm_train_metrics.jsonl").write_text('{"old": 1}\n', encoding="utf-8")

            run_seeds(seed_context, ExplodingStrategy())

            assert (output_dir / "dist_tiny_llm_train_metrics.jsonl").read_text(encoding="utf-8") == '{"old": 1}\n'

        def test_no_checkpoint_is_written_when_every_seed_fails(
            self, seed_context: SeedContext, gold_dataset: Path
        ) -> None:
            run_seeds(seed_context, ExplodingStrategy())

            assert list(seed_context.config.paths.models_dir.iterdir()) == []

        def test_an_empty_seed_tuple_runs_nothing(
            self, seed_context: SeedContext, gold_dataset: Path, recording_log: RecordingLog
        ) -> None:
            strategy = ExplodingStrategy()

            results = run_seeds(dataclasses.replace(seed_context, seeds=()), strategy)

            assert results == {}
            assert strategy.build_calls == 0
            assert recording_log.messages_at("critical") == []
