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
from types import SimpleNamespace
from typing import Any

import pytest
import torch
from pandas import DataFrame
from transformers import TrainingArguments, set_seed

from reddit.core.config import Config, ModelSpec
from reddit.core.errors import ConfigError
from reddit.modeling.loading import DeviceProfile
from reddit.training import loop
from reddit.training.loop import FATAL_ERRORS, SeedContext, SeedResult, run_seeds

from ..conftest import RecordingLog

TURING = DeviceProfile(capability=(7, 5), flash_attention=False)
AMPERE = DeviceProfile(capability=(8, 0), flash_attention=False)


class ExplodingStrategy:
    """A :class:`reddit.training.loop.SeedStrategy` that fails at model build."""

    def __init__(self, message: str = "no GPU available", error: type[Exception] = RuntimeError) -> None:
        self.message = message
        self.error = error
        self.build_calls = 0

    def build_model(self, ctx: Any, bundle: Any) -> tuple[Any, Any]:
        self.build_calls += 1
        raise self.error(self.message)

    def parameter_counts(self, model: Any) -> tuple[int | None, int | None]:  # pragma: no cover - unreachable
        return None, None

    def tokenize(self, ctx: Any, bundle: Any) -> Any:  # pragma: no cover - unreachable
        return bundle

    def data_collator(self, ctx: Any) -> Any:
        return None

    def training_arguments(self, ctx: Any) -> Any:
        # Resolved once by the preflight, before any seed; a stand-in carrying
        # the fixed model-init/training seed and no reporting integration
        # (what a real `TrainingArguments(report_to="none")` resolves to) is
        # enough.
        return SimpleNamespace(seed=42, report_to=[])

    def optimizers(self, ctx: Any, model: Any) -> tuple[Any, Any]:  # pragma: no cover - unreachable
        return None, None


class InitProbeStrategy(ExplodingStrategy):
    """Records the RNG state model initialisation would see, then fails."""

    def __init__(self, fixed_seed: int = 42) -> None:
        super().__init__("probe only")
        self.fixed_seed = fixed_seed
        self.draws: list[float] = []

    def training_arguments(self, ctx: Any) -> Any:
        return SimpleNamespace(seed=self.fixed_seed, report_to=[])

    def build_model(self, ctx: Any, bundle: Any) -> tuple[Any, Any]:
        self.draws.append(torch.rand(()).item())
        return super().build_model(ctx, bundle)


class MisconfiguredStrategy(ExplodingStrategy):
    """Fails the way ``TrainingArguments`` does on an unknown pass-through key."""

    def training_arguments(self, ctx: Any) -> Any:
        raise TypeError("__init__() got an unexpected keyword argument 'overwrite_output_dir'")


class UnknownTrackerStrategy(ExplodingStrategy):
    """Real ``TrainingArguments`` naming an experiment tracker transformers does not know.

    ``TrainingArguments`` itself accepts any string here; only the Trainer's
    constructor rejects it — which is what a YAML ``null`` (wrapped as
    ``[None]`` by transformers 5) used to trigger once per seed.
    """

    def training_arguments(self, ctx: Any) -> Any:
        return TrainingArguments(output_dir=str(ctx.cache_dir), report_to="no_such_tracker", seed=42)


def with_precision(config: Config, *, bf16: bool, fp16: bool) -> Config:
    """A copy of ``config`` with the two precision flags set as given."""
    arguments = config.training.arguments.model_copy(update={"bf16": bf16, "fp16": fp16})
    training = config.training.model_copy(update={"arguments": arguments})
    return config.model_copy(update={"training": training})


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

        def test_only_environment_level_failures_are_fatal(self) -> None:
            """A missing package or an unreachable checkpoint; never a per-seed blow-up."""
            assert set(FATAL_ERRORS) == {ImportError, OSError}
            assert not issubclass(RuntimeError, FATAL_ERRORS)

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

        # ──────────────────────────────────────────────── seed protocol ──

        def test_model_initialisation_is_seeded_identically_for_every_split(
            self, seed_context: SeedContext, gold_dataset: Path
        ) -> None:
            """The split is the run's only variable; init must not follow the split seed."""
            strategy = InitProbeStrategy(fixed_seed=7)

            run_seeds(seed_context, strategy)

            set_seed(7)
            expected = torch.rand(()).item()
            assert strategy.draws == [expected] * len(seed_context.seeds)

        def test_the_split_seed_never_reaches_model_initialisation(
            self, seed_context: SeedContext, gold_dataset: Path
        ) -> None:
            strategy = InitProbeStrategy(fixed_seed=7)

            run_seeds(seed_context, strategy)

            for split_seed in seed_context.seeds:
                set_seed(split_seed)
                assert torch.rand(()).item() not in strategy.draws

        # ─────────────────────────────────────────────── fatal failures ──

        @pytest.mark.parametrize("error", [ImportError, OSError])
        def test_a_fatal_failure_aborts_the_remaining_seeds(
            self, seed_context: SeedContext, gold_dataset: Path, recording_log: RecordingLog, error: type[Exception]
        ) -> None:
            """Retrying a missing package once per seed only re-downloads the checkpoint."""
            strategy = ExplodingStrategy("flash_attn is not installed", error=error)

            results = run_seeds(seed_context, strategy)

            assert results == {}
            assert strategy.build_calls == 1
            critical = recording_log.text_at("critical")
            assert "flash_attn is not installed" in critical
            assert f"aborting `{seed_context.run_name}` with 1 seed(s) left" in critical

        def test_a_fatal_failure_keeps_the_seeds_completed_before_it(
            self, seed_context: SeedContext, gold_dataset: Path, monkeypatch: pytest.MonkeyPatch
        ) -> None:
            outcomes = iter([SeedResult(seed=11, performance=0.5, method="qdora", model=Path("m")), OSError("gated")])

            def run_one(ctx: Any, strategy: Any, plan: Any, seed: int, k: int) -> SeedResult:
                outcome = next(outcomes)
                if isinstance(outcome, Exception):
                    raise outcome
                return outcome

            monkeypatch.setattr(loop, "_run_one_seed", run_one)

            results = run_seeds(seed_context, ExplodingStrategy())

            assert set(results) == {11}

        # ─────────────────────────────────────────────────── preflight ──

        def test_the_device_profile_is_announced_before_the_first_seed(
            self, seed_context: SeedContext, gold_dataset: Path, recording_log: RecordingLog
        ) -> None:
            run_seeds(seed_context, ExplodingStrategy())

            assert "Device profile:" in recording_log.text_at("info")

        def test_bf16_is_rejected_on_a_gpu_without_native_bf16(
            self, seed_context: SeedContext, gold_dataset: Path, monkeypatch: pytest.MonkeyPatch
        ) -> None:
            monkeypatch.setattr(loop, "detect_device_profile", lambda: TURING)
            context = dataclasses.replace(
                seed_context, config=with_precision(seed_context.config, bf16=True, fp16=False)
            )
            strategy = ExplodingStrategy()

            with pytest.raises(ConfigError, match="bf16"):
                run_seeds(context, strategy)

            assert strategy.build_calls == 0

        def test_bf16_is_accepted_on_ampere(
            self, seed_context: SeedContext, gold_dataset: Path, monkeypatch: pytest.MonkeyPatch
        ) -> None:
            monkeypatch.setattr(loop, "detect_device_profile", lambda: AMPERE)
            context = dataclasses.replace(
                seed_context, config=with_precision(seed_context.config, bf16=True, fp16=False)
            )
            strategy = ExplodingStrategy()

            run_seeds(context, strategy)

            assert strategy.build_calls == len(context.seeds)

        def test_fp16_is_accepted_everywhere(
            self, seed_context: SeedContext, gold_dataset: Path, monkeypatch: pytest.MonkeyPatch
        ) -> None:
            monkeypatch.setattr(loop, "detect_device_profile", lambda: TURING)
            strategy = ExplodingStrategy()

            run_seeds(
                dataclasses.replace(seed_context, config=with_precision(seed_context.config, bf16=False, fp16=True)),
                strategy,
            )

            assert strategy.build_calls == len(seed_context.seeds)

        def test_an_invalid_training_argument_is_reported_once_as_a_config_error(
            self, seed_context: SeedContext, gold_dataset: Path
        ) -> None:
            """Previously the `TypeError` was swallowed once per seed, failing them all."""
            strategy = MisconfiguredStrategy()

            with pytest.raises(ConfigError, match="overwrite_output_dir"):
                run_seeds(seed_context, strategy)

            assert strategy.build_calls == 0

        def test_an_unknown_reporting_integration_is_reported_once_as_a_config_error(
            self, seed_context: SeedContext, gold_dataset: Path
        ) -> None:
            """The Trainer only resolves `report_to` in its constructor, i.e. once per seed.

            A `report_to: null` in `config.yml` hit exactly this path under
            transformers 5 and failed every seed of a run the same way.
            """
            strategy = UnknownTrackerStrategy()

            with pytest.raises(ConfigError, match="no_such_tracker is not supported"):
                run_seeds(seed_context, strategy)

            assert strategy.build_calls == 0
