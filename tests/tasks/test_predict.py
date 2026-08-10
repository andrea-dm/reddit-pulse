"""Contract tests for :mod:`reddit.tasks.predict`.

``execute_predict`` chooses between archive discovery (LLM families) and
directory discovery (BERT families); the discovery implementations are replaced
at their module boundary.
"""

from __future__ import annotations

from argparse import ArgumentParser, Namespace
from typing import Any

import pytest

from reddit.core.config import Config
from reddit.core.errors import UnknownFamilyError
from reddit.inference import bert as inference_bert
from reddit.inference import llms as inference_llms
from reddit.tasks.predict import execute_predict, setup_predict


class Recorder:
    """Captures the arguments a patched discovery entry point receives."""

    def __init__(self, returns: int = 1) -> None:
        self.returns = returns
        self.calls: list[tuple[Any, ...]] = []

    def __call__(self, *args: Any) -> int:
        self.calls.append(args)
        return self.returns


class TestPredictTaskModule:
    """CLI argument registration and discovery dispatch for ``predict``."""

    @pytest.fixture
    def parser(self) -> ArgumentParser:
        """A bare parser carrying only the predict arguments."""
        parser = ArgumentParser(prog="reddit-predict")
        setup_predict(parser)
        return parser

    @pytest.fixture
    def patched_discovery(self, monkeypatch: pytest.MonkeyPatch) -> dict[str, Recorder]:
        """Replace both corpus-labelling entry points with recorders."""
        recorders = {"archives": Recorder(returns=5), "directories": Recorder(returns=7)}
        monkeypatch.setattr(inference_llms, "predict_from_archives", recorders["archives"])
        monkeypatch.setattr(inference_bert, "predict_from_directories", recorders["directories"])
        return recorders

    @pytest.mark.unit
    class TestUnits:
        def test_both_the_family_and_the_directory_are_required(self, parser: ArgumentParser) -> None:
            with pytest.raises(SystemExit):
                parser.parse_args(["-f", "gemma"])
            with pytest.raises(SystemExit):
                parser.parse_args(["-d", "models"])

        def test_arguments_are_available_long_and_short(self, parser: ArgumentParser) -> None:
            short = parser.parse_args(["-f", "gemma", "-d", "models"])
            long = parser.parse_args(["--family", "gemma", "--directory", "models"])

            assert (short.family, short.directory) == (["gemma"], "models")
            assert (long.family, long.directory) == (["gemma"], "models")

        def test_the_family_accepts_multiple_values(self, parser: ArgumentParser) -> None:
            args = parser.parse_args(["-f", "gemma", "bert", "-d", "models"])
            assert args.family == ["gemma", "bert"]

        def test_the_model_accepts_multiple_values(self, parser: ArgumentParser) -> None:
            args = parser.parse_args(["-m", "gemma-2b", "bert-base", "-d", "models"])
            assert args.model == ["gemma-2b", "bert-base"]
            assert args.family is None

        def test_all_families_and_all_models_set_the_same_flag(self, parser: ArgumentParser) -> None:
            assert parser.parse_args(["--all-families", "-d", "models"]).all_families is True
            assert parser.parse_args(["--all-models", "-d", "models"]).all_families is True

        def test_the_directory_is_kept_as_written(self, parser: ArgumentParser) -> None:
            """Relative paths are resolved by the discovery iterators, not here."""
            assert parser.parse_args(["-f", "g", "-d", "../models"]).directory == "../models"

    @pytest.mark.integration
    class TestIntegration:
        def test_an_llm_family_is_labelled_from_archives(
            self, bootstrapped_config: Config, patched_discovery: dict[str, Recorder]
        ) -> None:
            args = Namespace(family=["llm_family"], model=None, all_families=False, directory="models")

            labelled = execute_predict(args, bootstrapped_config)

            assert labelled == 5
            assert patched_discovery["directories"].calls == []

        def test_a_bert_family_is_labelled_from_checkpoint_directories(
            self, bootstrapped_config: Config, patched_discovery: dict[str, Recorder]
        ) -> None:
            args = Namespace(family=["bert_family"], model=None, all_families=False, directory="models")

            labelled = execute_predict(args, bootstrapped_config)

            assert labelled == 7
            assert patched_discovery["archives"].calls == []

        def test_the_directory_is_forwarded_verbatim(
            self, bootstrapped_config: Config, patched_discovery: dict[str, Recorder]
        ) -> None:
            execute_predict(
                Namespace(family=["llm_family"], model=None, all_families=False, directory="/data/models"),
                bootstrapped_config,
            )

            assert patched_discovery["archives"].calls[0][2] == "/data/models"

        def test_the_resolved_family_is_handed_to_the_discovery(
            self, bootstrapped_config: Config, patched_discovery: dict[str, Recorder]
        ) -> None:
            execute_predict(
                Namespace(family=["single_method"], model=None, all_families=False, directory="models"),
                bootstrapped_config,
            )

            config_arg, models_arg, _ = patched_discovery["archives"].calls[0]
            assert config_arg is bootstrapped_config
            assert models_arg.finetuning_methods == ["xqdora"]

        def test_an_unknown_family_is_reported_before_any_import(
            self, bootstrapped_config: Config, patched_discovery: dict[str, Recorder]
        ) -> None:
            args = Namespace(family=["nope"], model=None, all_families=False, directory="models")

            with pytest.raises(UnknownFamilyError):
                execute_predict(args, bootstrapped_config)

            assert patched_discovery["archives"].calls == []
            assert patched_discovery["directories"].calls == []
