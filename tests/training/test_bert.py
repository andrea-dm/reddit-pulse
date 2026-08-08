"""Contract tests for :mod:`reddit.training.bert`.

The headline contract is that the encoder pipeline reads ``training.bert``:
``training.learning_rate`` used to be silently inapplicable to it because the
values were hardcoded module constants.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from torch import nn
from transformers import DataCollatorWithPadding

from reddit.core.config import Config, ModelSpec
from reddit.training.bert import BertSeedStrategy, run_family
from reddit.training.loop import SeedContext

from ..conftest import RecordingLog
from .test_llms import ListTokenizer, make_bundle


def sentinel_labeller(**_kwargs: Any) -> None:
    """A ``Labeller``-shaped sentinel; it is only ever compared by identity."""
    raise AssertionError("the sentinel labeller must never be invoked")


class TestBertTrainingModule:
    """Full fine-tuning strategy and family orchestration for BERT encoders."""

    @pytest.fixture
    def seed_context(self, bootstrapped_config: Config, recording_log: RecordingLog) -> SeedContext:
        return SeedContext(
            config=bootstrapped_config,
            models=bootstrapped_config.family("bert_family"),
            model=ModelSpec(name="tiny_bert", id="acme/tiny-bert"),
            finetuning_method="-",
            run_name="tiny_bert_20240102_000000",
            cache_dir=bootstrapped_config.paths.cache_dir / "run",
            hf_cache=bootstrapped_config.paths.cache_dir / "hf",
            seeds=(11, 22),
            tokenizer=ListTokenizer(),
            log=recording_log,
        )

    @pytest.mark.unit
    class TestUnits:
        def test_padding_is_dynamic_not_to_the_full_window(self, seed_context: SeedContext) -> None:
            """Padding every title to 512 tokens spent most of the compute on padding."""
            collator = BertSeedStrategy().data_collator(seed_context)

            assert isinstance(collator, DataCollatorWithPadding)
            assert collator.pad_to_multiple_of == 8

        def test_the_trainer_builds_its_own_optimizer(self, seed_context: SeedContext) -> None:
            assert BertSeedStrategy().optimizers(seed_context, object()) == (None, None)

        def test_parameter_counts_are_summed_from_the_module(self, seed_context: SeedContext) -> None:
            model = nn.Sequential(nn.Linear(4, 3), nn.Linear(3, 2))

            trainable, total = BertSeedStrategy().parameter_counts(model)

            assert total == sum(p.numel() for p in model.parameters())
            assert trainable == total

        def test_frozen_parameters_are_excluded_from_the_trainable_count(self, seed_context: SeedContext) -> None:
            model = nn.Sequential(nn.Linear(4, 3), nn.Linear(3, 2))
            for parameter in model[0].parameters():
                parameter.requires_grad = False

            trainable, total = BertSeedStrategy().parameter_counts(model)

            assert trainable is not None and total is not None
            assert trainable < total

        def test_tokenization_keeps_the_raw_text_column(self, seed_context: SeedContext) -> None:
            """Unlike the LLM strategy, the encoder path does not drop ``text``."""
            tokenized = BertSeedStrategy().tokenize(seed_context, make_bundle())

            assert "text" in tokenized["train"].column_names
            assert {"input_ids", "attention_mask"} <= set(tokenized["train"].column_names)

        def test_tokenization_leaves_the_dataset_unformatted(self, seed_context: SeedContext) -> None:
            tokenized = BertSeedStrategy().tokenize(seed_context, make_bundle())

            assert tokenized["train"].format["type"] is None

    @pytest.mark.unit
    class TestTrainingArgumentsWiring:
        """The intended ``training.bert`` wiring — the reason that section exists.

        Regression guard for the transformers-5 removal of
        ``overwrite_output_dir``; see
        ``tests/training/test_llms.py::TestTrainingArgumentsWiring``.
        """

        def test_the_encoder_reads_its_own_hyperparameter_section(self, seed_context: SeedContext) -> None:
            arguments = BertSeedStrategy().training_arguments(seed_context)
            bert = seed_context.config.training.bert

            assert arguments.learning_rate == bert.learning_rate
            assert arguments.num_train_epochs == bert.num_train_epochs

        def test_the_llm_learning_rate_never_reaches_the_encoder(self, seed_context: SeedContext) -> None:
            """``training.learning_rate`` applies to the PEFT pipeline only."""
            arguments = BertSeedStrategy().training_arguments(seed_context)

            assert arguments.learning_rate != seed_context.config.training.learning_rate
            assert arguments.num_train_epochs != seed_context.config.training.num_train_epochs

        def test_a_configured_encoder_learning_rate_is_honoured(
            self, config_factory: Callable[..., Config], recording_log: RecordingLog
        ) -> None:
            config = config_factory(
                training={
                    "seeds": [11],
                    "learning_rate": 1.0e-4,
                    "bert": {"learning_rate": 3.0e-5, "num_train_epochs": 7},
                    "arguments": {"fp16": False, "report_to": None},
                }
            )
            context = SeedContext(
                config=config,
                models=config.family("bert_family"),
                model=ModelSpec(name="tiny_bert", id="acme/tiny-bert"),
                finetuning_method="-",
                run_name="run",
                cache_dir=config.paths.cache_dir / "run",
                hf_cache=config.paths.cache_dir / "hf",
                seeds=(11,),
                tokenizer=ListTokenizer(),
                log=recording_log,
            )

            arguments = BertSeedStrategy().training_arguments(context)

            assert arguments.learning_rate == 3.0e-5
            assert arguments.num_train_epochs == 7

        def test_the_pass_through_arguments_section_is_applied(self, seed_context: SeedContext) -> None:
            arguments = BertSeedStrategy().training_arguments(seed_context)
            declared = seed_context.config.training.arguments

            assert arguments.per_device_train_batch_size == declared.per_device_train_batch_size
            assert arguments.metric_for_best_model == declared.metric_for_best_model

        def test_no_gradient_accumulation_is_configured_for_encoders(self, seed_context: SeedContext) -> None:
            arguments = BertSeedStrategy().training_arguments(seed_context)

            assert arguments.gradient_accumulation_steps == 1
            assert arguments.gradient_checkpointing is False

    @pytest.mark.integration
    class TestIntegration:
        def test_every_model_in_the_family_is_trained_once(
            self, bootstrapped_config: Config, monkeypatch: pytest.MonkeyPatch
        ) -> None:
            trained: list[str] = []

            def capture(_c: Any, _ms: Any, model: Any, _log: Any, *, seeds: Any, labeller: Any = None) -> bool:
                trained.append(model.name)
                return True

            monkeypatch.setattr("reddit.training.bert.train_model", capture)

            selected = run_family(bootstrapped_config, bootstrapped_config.family("bert_family"))

            assert trained == ["tiny_bert"]
            assert selected == 1

        def test_no_finetuning_method_dimension_is_iterated(
            self, bootstrapped_config: Config, monkeypatch: pytest.MonkeyPatch
        ) -> None:
            """Encoders are fully fine-tuned: one run per model, not per method."""
            calls: list[Any] = []

            def capture(*args: Any, **_kwargs: Any) -> bool:
                calls.append(args)
                return True

            monkeypatch.setattr("reddit.training.bert.train_model", capture)

            run_family(bootstrapped_config, bootstrapped_config.family("bert_family"))

            assert len(calls) == 1

        def test_the_seed_limit_restricts_the_run(
            self, bootstrapped_config: Config, monkeypatch: pytest.MonkeyPatch
        ) -> None:
            captured: list[tuple[int, ...]] = []

            def capture(*_args: Any, seeds: tuple[int, ...], labeller: Any = None, **_kwargs: Any) -> bool:
                captured.append(seeds)
                return True

            monkeypatch.setattr("reddit.training.bert.train_model", capture)

            run_family(bootstrapped_config, bootstrapped_config.family("bert_family"), limit=1)

            assert captured == [(11,)]

        def test_an_unselected_model_is_not_counted(
            self, bootstrapped_config: Config, monkeypatch: pytest.MonkeyPatch
        ) -> None:
            def never_selects(*_args: Any, **_kwargs: Any) -> bool:
                return False

            monkeypatch.setattr("reddit.training.bert.train_model", never_selects)

            assert run_family(bootstrapped_config, bootstrapped_config.family("bert_family")) == 0

        def test_the_labeller_is_forwarded_to_each_model(
            self, bootstrapped_config: Config, monkeypatch: pytest.MonkeyPatch
        ) -> None:
            received: list[Any] = []

            def capture(*_args: Any, seeds: tuple[int, ...], labeller: Any = None) -> bool:
                received.append(labeller)
                return True

            monkeypatch.setattr("reddit.training.bert.train_model", capture)

            run_family(bootstrapped_config, bootstrapped_config.family("bert_family"), labeller=sentinel_labeller)

            assert received == [sentinel_labeller]

        def test_a_per_model_log_file_is_created(
            self, bootstrapped_config: Config, monkeypatch: pytest.MonkeyPatch
        ) -> None:
            def always_selects(*_args: Any, **_kwargs: Any) -> bool:
                return True

            monkeypatch.setattr("reddit.training.bert.train_model", always_selects)

            run_family(bootstrapped_config, bootstrapped_config.family("bert_family"))

            log_file = bootstrapped_config.paths.logs_dir / f"tiny_bert_{bootstrapped_config.system.date}.log"
            assert log_file.is_file()
            assert "Running `tiny_bert`..." in log_file.read_text(encoding="utf-8")

        def test_a_family_without_models_trains_nothing(self, config_factory: Callable[..., Config]) -> None:
            config = config_factory(families={"empty": {"kind": "bert", "models": []}})

            assert run_family(config, config.family("empty")) == 0
