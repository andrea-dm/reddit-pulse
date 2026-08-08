"""Contract tests for :mod:`reddit.training.llms`.

The strategy's wiring (which configuration value reaches which
``TrainingArguments`` field) and the method-registry guard are testable without
loading a checkpoint; ``build_model`` and ``optimizers`` are not, because both
require a real quantized model.
"""

# `datasets` ships no type stubs; the resulting Unknowns are confined to this file.
# pyright: reportMissingTypeStubs=false, reportUnknownVariableType=false
# pyright: reportUnknownArgumentType=false

from __future__ import annotations

import inspect
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any, ClassVar

import pytest
import torch
from datasets import ClassLabel, Dataset, DatasetDict
from pandas import DataFrame
from torch import nn
from transformers import DataCollatorWithPadding, TrainingArguments

from reddit.core.config import ArgumentsConfig, Config, ModelSpec
from reddit.core.errors import ConfigError, UnsupportedMethodError
from reddit.data.preparation import DataBundle
from reddit.training.llms import LlmSeedStrategy, run_family
from reddit.training.loop import SeedContext

from ..conftest import RecordingLog


class ListTokenizer:
    """A tokenizer double returning plain lists, as ``datasets.map`` expects."""

    model_input_names: ClassVar[list[str]] = ["input_ids", "attention_mask"]
    pad_token_id = 0
    padding_side = "right"

    def __call__(self, texts: list[str], **kwargs: Any) -> dict[str, list[list[int]]]:
        return {
            "input_ids": [[1, 2, 3] for _ in texts],
            "attention_mask": [[1, 1, 1] for _ in texts],
        }

    def pad(self, *args: Any, **kwargs: Any) -> Any:  # pragma: no cover - collator plumbing
        raise NotImplementedError


def make_bundle() -> DataBundle:
    """A tiny prepared dataset with the columns the strategies expect."""
    frame = DataFrame(
        {
            "text": ["a headline", "another headline", "a third one", "and a fourth"],
            "label_str": ["up", "down", "neutral", "up"],
            "label": [2, 0, 1, 2],
        }
    )
    splits = DatasetDict({split: Dataset.from_pandas(frame, preserve_index=False) for split in ("train", "validation")})
    splits = splits.cast_column("label", ClassLabel(names=["down", "neutral", "up"]))
    return DataBundle(dataset=splits, num_labels=3, id2label={0: "down", 1: "neutral", 2: "up"}, label2id={})


class TestLlmsTrainingModule:
    """PEFT fine-tuning strategy and family orchestration for decoder LLMs."""

    @pytest.fixture
    def seed_context(self, bootstrapped_config: Config, recording_log: RecordingLog) -> SeedContext:
        return SeedContext(
            config=bootstrapped_config,
            models=bootstrapped_config.family("llm_family"),
            model=ModelSpec(name="tiny_llm", id="acme/tiny-llm"),
            finetuning_method="qdora",
            run_name="tiny_llm_qdora_20240102_000000",
            cache_dir=bootstrapped_config.paths.cache_dir / "run",
            hf_cache=bootstrapped_config.paths.cache_dir / "hf",
            seeds=(11, 22),
            tokenizer=ListTokenizer(),
            log=recording_log,
        )

    @pytest.mark.unit
    class TestUnits:
        def test_the_collator_pads_dynamically_to_a_multiple_of_eight(self, seed_context: SeedContext) -> None:
            collator = LlmSeedStrategy().data_collator(seed_context)

            assert isinstance(collator, DataCollatorWithPadding)
            assert collator.pad_to_multiple_of == 8
            assert collator.tokenizer is seed_context.tokenizer

        def test_parameter_counts_come_from_the_peft_wrapper(self) -> None:
            model = SimpleNamespace(get_nb_trainable_parameters=lambda: (1_024, 8_000_000))

            assert LlmSeedStrategy().parameter_counts(model) == (1_024, 8_000_000)

        def test_an_unwrapped_model_reports_unknown_parameter_counts(self) -> None:
            """A plain ``nn.Module`` has no PEFT accounting; the loop records ``None``."""
            assert LlmSeedStrategy().parameter_counts(nn.Linear(2, 2)) == (None, None)

        def test_tokenization_drops_the_raw_text_and_switches_to_tensors(self, seed_context: SeedContext) -> None:
            tokenized = LlmSeedStrategy().tokenize(seed_context, make_bundle())

            assert "text" not in tokenized["train"].column_names
            assert {"input_ids", "attention_mask", "label"} <= set(tokenized["train"].column_names)
            assert tokenized["train"].format["type"] == "torch"
            assert isinstance(tokenized["train"][0]["input_ids"], torch.Tensor)

        def test_every_split_is_tokenized(self, seed_context: SeedContext) -> None:
            tokenized = LlmSeedStrategy().tokenize(seed_context, make_bundle())

            assert set(tokenized) == {"train", "validation"}
            assert all("input_ids" in tokenized[split].column_names for split in tokenized)

    @pytest.mark.unit
    class TestTrainingArgumentsWiring:
        """The ``TrainingArguments`` wiring both seed strategies rely on.

        Regression guard for the transformers-5 removal of
        ``overwrite_output_dir``: every ``ArgumentsConfig`` field is splatted
        into ``TrainingArguments``, so a single stale key raises ``TypeError``
        inside the per-seed loop and a full run completes having trained
        nothing.
        """

        def test_every_declared_training_argument_is_accepted_by_transformers(self) -> None:
            accepted = set(inspect.signature(TrainingArguments.__init__).parameters)

            assert set(ArgumentsConfig().model_dump()) <= accepted

        def test_the_llm_hyperparameters_reach_the_trainer(self, seed_context: SeedContext) -> None:
            arguments = LlmSeedStrategy().training_arguments(seed_context)
            training = seed_context.config.training

            assert arguments.learning_rate == training.learning_rate
            assert arguments.num_train_epochs == training.num_train_epochs
            assert arguments.gradient_accumulation_steps == training.gradient_accumulation_steps

        def test_the_run_name_and_cache_directory_are_propagated(self, seed_context: SeedContext) -> None:
            arguments = LlmSeedStrategy().training_arguments(seed_context)

            assert arguments.run_name == seed_context.run_name
            assert arguments.output_dir == str(seed_context.cache_dir)

        def test_memory_saving_options_are_enabled_for_quantized_training(self, seed_context: SeedContext) -> None:
            arguments = LlmSeedStrategy().training_arguments(seed_context)

            assert arguments.gradient_checkpointing is True
            assert arguments.gradient_checkpointing_kwargs == {"use_reentrant": True}
            assert arguments.lr_scheduler_type == "cosine"

        def test_the_pass_through_arguments_section_is_applied(self, seed_context: SeedContext) -> None:
            arguments = LlmSeedStrategy().training_arguments(seed_context)
            declared = seed_context.config.training.arguments

            assert arguments.per_device_train_batch_size == declared.per_device_train_batch_size
            assert arguments.metric_for_best_model == declared.metric_for_best_model
            assert arguments.save_total_limit == declared.save_total_limit

    @pytest.mark.integration
    class TestIntegration:
        def test_a_method_without_an_implementation_is_rejected_upfront(
            self, config_factory: Callable[..., Config]
        ) -> None:
            config = config_factory(
                families={"exotic": {"finetuning_methods": ["adalora"], "models": [{"name": "m", "id": "a/m"}]}}
            )

            models = config.family("exotic")

            with pytest.raises(UnsupportedMethodError, match="adalora"):
                run_family(config, models)

        def test_the_rejection_is_a_user_fixable_configuration_error(
            self, config_factory: Callable[..., Config]
        ) -> None:
            config = config_factory(
                families={"exotic": {"finetuning_methods": ["adalora"], "models": [{"name": "m", "id": "a/m"}]}}
            )

            models = config.family("exotic")

            with pytest.raises(ConfigError):
                run_family(config, models)

        def test_the_rejection_message_lists_the_available_methods(self, config_factory: Callable[..., Config]) -> None:
            config = config_factory(
                families={"exotic": {"finetuning_methods": ["adalora"], "models": [{"name": "m", "id": "a/m"}]}}
            )

            models = config.family("exotic")

            with pytest.raises(UnsupportedMethodError) as excinfo:
                run_family(config, models)

            assert "qdora, xqdora" in str(excinfo.value)

        def test_nothing_is_created_before_the_method_check(self, config_factory: Callable[..., Config]) -> None:
            config = config_factory(
                families={"exotic": {"finetuning_methods": ["adalora"], "models": [{"name": "m", "id": "a/m"}]}}
            )
            config.paths.cache_dir.mkdir(parents=True)
            models = config.family("exotic")

            with pytest.raises(UnsupportedMethodError):
                run_family(config, models)

            assert list(config.paths.cache_dir.iterdir()) == []

        def test_a_family_without_models_selects_nothing(self, config_factory: Callable[..., Config]) -> None:
            config = config_factory(families={"empty": {"models": []}})

            assert run_family(config, config.family("empty")) == 0

        def test_the_seed_limit_restricts_the_run(
            self, bootstrapped_config: Config, monkeypatch: pytest.MonkeyPatch
        ) -> None:
            """``--limit`` truncates the configured seed list before training."""
            captured: list[tuple[int, ...]] = []

            def fake_run_model(
                _config: Config,
                _models: Any,
                _model: Any,
                _method: str,
                _log: Any,
                *,
                seeds: tuple[int, ...],
                labeller: Any = None,
            ) -> bool:
                captured.append(seeds)
                return True

            monkeypatch.setattr("reddit.training.llms.run_model", fake_run_model)
            models = bootstrapped_config.family("single_method")

            selected = run_family(bootstrapped_config, models, limit=2)

            assert captured == [(11, 22)]
            assert selected == 1

        def test_no_limit_uses_every_configured_seed(
            self, bootstrapped_config: Config, monkeypatch: pytest.MonkeyPatch
        ) -> None:
            captured: list[tuple[int, ...]] = []

            def capture(*_args: Any, seeds: tuple[int, ...], labeller: Any = None, **_kwargs: Any) -> bool:
                captured.append(seeds)
                return True

            monkeypatch.setattr("reddit.training.llms.run_model", capture)

            run_family(bootstrapped_config, bootstrapped_config.family("single_method"))

            assert captured == [tuple(bootstrapped_config.training.seeds)]

        def test_every_model_and_method_combination_is_run(
            self, bootstrapped_config: Config, monkeypatch: pytest.MonkeyPatch
        ) -> None:
            combinations: list[tuple[str, str]] = []

            def capture(
                _c: Any, _ms: Any, model: Any, method: str, _log: Any, *, seeds: Any, labeller: Any = None
            ) -> bool:
                combinations.append((model.name, method))
                return False

            monkeypatch.setattr("reddit.training.llms.run_model", capture)

            selected = run_family(bootstrapped_config, bootstrapped_config.family("llm_family"))

            assert combinations == [("tiny_llm", "qdora"), ("tiny_llm", "xqdora")]
            assert selected == 0

        def test_a_per_model_log_file_is_created(
            self, bootstrapped_config: Config, monkeypatch: pytest.MonkeyPatch
        ) -> None:
            def always_selects(*_args: Any, **_kwargs: Any) -> bool:
                return True

            monkeypatch.setattr("reddit.training.llms.run_model", always_selects)

            run_family(bootstrapped_config, bootstrapped_config.family("single_method"))

            log_file = bootstrapped_config.paths.logs_dir / f"tiny_llm_{bootstrapped_config.system.date}.log"
            assert log_file.is_file()
            assert "Running `tiny_llm-xqdora`" in log_file.read_text(encoding="utf-8")
