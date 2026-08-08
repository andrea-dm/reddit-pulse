"""Contract tests for :mod:`reddit.tasks.run`.

``execute_run`` is the composition root that keeps ``training`` free of any
dependency on ``inference``: which pipeline and which labeller it wires up is
the contract.  The pipelines themselves are replaced at their module boundary,
so nothing is ever trained here.
"""

from __future__ import annotations

from argparse import ArgumentParser, Namespace
from typing import Any

import pytest

from reddit.core.config import Config
from reddit.core.errors import UnknownFamilyError
from reddit.inference import bert as inference_bert
from reddit.inference import llms as inference_llms
from reddit.tasks.run import execute_run, setup_run
from reddit.training import bert as training_bert
from reddit.training import llms as training_llms


class Recorder:
    """Captures the arguments a patched ``run_family`` receives."""

    def __init__(self, returns: int = 1) -> None:
        self.returns = returns
        self.calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def __call__(self, *args: Any, **kwargs: Any) -> int:
        self.calls.append((args, kwargs))
        return self.returns

    @property
    def kwargs(self) -> dict[str, Any]:
        return self.calls[0][1]


class TestRunTaskModule:
    """CLI argument registration and pipeline dispatch for ``run``/``train``."""

    @pytest.fixture
    def parser(self) -> ArgumentParser:
        """A bare parser carrying only the run/train arguments."""
        parser = ArgumentParser(prog="reddit-run")
        setup_run(parser)
        return parser

    @pytest.fixture
    def patched_pipelines(self, monkeypatch: pytest.MonkeyPatch) -> dict[str, Recorder]:
        """Replace both ``run_family`` implementations with recorders."""
        recorders = {"llm": Recorder(returns=3), "bert": Recorder(returns=2)}
        monkeypatch.setattr(training_llms, "run_family", recorders["llm"])
        monkeypatch.setattr(training_bert, "run_family", recorders["bert"])
        return recorders

    @pytest.mark.unit
    class TestUnits:
        def test_the_family_is_required(self, parser: ArgumentParser) -> None:
            with pytest.raises(SystemExit) as excinfo:
                parser.parse_args([])

            assert excinfo.value.code == 2

        def test_the_family_can_be_given_long_or_short(self, parser: ArgumentParser) -> None:
            assert parser.parse_args(["-f", "gemma"]).family == "gemma"
            assert parser.parse_args(["--family", "gemma"]).family == "gemma"

        def test_the_seed_limit_defaults_to_all_seeds(self, parser: ArgumentParser) -> None:
            assert parser.parse_args(["-f", "gemma"]).limit == 0

        def test_the_seed_limit_is_parsed_as_an_integer(self, parser: ArgumentParser) -> None:
            assert parser.parse_args(["-f", "gemma", "--limit", "3"]).limit == 3
            assert parser.parse_args(["-f", "gemma", "-l", "3"]).limit == 3

        def test_a_non_numeric_limit_is_rejected(self, parser: ArgumentParser) -> None:
            with pytest.raises(SystemExit):
                parser.parse_args(["-f", "gemma", "--limit", "many"])

        def test_inference_is_enabled_by_default(self, parser: ArgumentParser) -> None:
            assert parser.parse_args(["-f", "gemma"]).no_inference is False

        def test_inference_can_be_switched_off(self, parser: ArgumentParser) -> None:
            assert parser.parse_args(["-f", "gemma", "--no-inference"]).no_inference is True

    @pytest.mark.integration
    class TestIntegration:
        def test_an_llm_family_runs_the_peft_pipeline(
            self, bootstrapped_config: Config, patched_pipelines: dict[str, Recorder]
        ) -> None:
            args = Namespace(family="llm_family", limit=0, no_inference=False)

            produced = execute_run(args, bootstrapped_config)

            assert produced == 3
            assert patched_pipelines["bert"].calls == []
            assert patched_pipelines["llm"].calls[0][0][1].family == "llm_family"

        def test_an_llm_family_is_labelled_by_the_llm_labeller(
            self, bootstrapped_config: Config, patched_pipelines: dict[str, Recorder]
        ) -> None:
            execute_run(Namespace(family="llm_family", limit=0, no_inference=False), bootstrapped_config)

            assert patched_pipelines["llm"].kwargs["labeller"] is inference_llms.label_corpus

        def test_a_bert_family_runs_the_full_finetuning_pipeline(
            self, bootstrapped_config: Config, patched_pipelines: dict[str, Recorder]
        ) -> None:
            produced = execute_run(Namespace(family="bert_family", limit=0, no_inference=False), bootstrapped_config)

            assert produced == 2
            assert patched_pipelines["llm"].calls == []

        def test_a_bert_family_is_labelled_by_the_bert_labeller(
            self, bootstrapped_config: Config, patched_pipelines: dict[str, Recorder]
        ) -> None:
            execute_run(Namespace(family="bert_family", limit=0, no_inference=False), bootstrapped_config)

            assert patched_pipelines["bert"].kwargs["labeller"] is inference_bert.label_corpus

        def test_disabling_inference_injects_no_labeller(
            self, bootstrapped_config: Config, patched_pipelines: dict[str, Recorder]
        ) -> None:
            execute_run(Namespace(family="llm_family", limit=0, no_inference=True), bootstrapped_config)

            assert patched_pipelines["llm"].kwargs["labeller"] is None

        def test_an_absent_no_inference_flag_keeps_labelling_on(
            self, bootstrapped_config: Config, patched_pipelines: dict[str, Recorder]
        ) -> None:
            """``reddit run`` never sets the flag; only ``reddit train`` does."""
            execute_run(Namespace(family="llm_family", limit=0), bootstrapped_config)

            assert patched_pipelines["llm"].kwargs["labeller"] is inference_llms.label_corpus

        def test_the_seed_limit_is_forwarded(
            self, bootstrapped_config: Config, patched_pipelines: dict[str, Recorder]
        ) -> None:
            execute_run(Namespace(family="llm_family", limit=4, no_inference=False), bootstrapped_config)

            assert patched_pipelines["llm"].kwargs["limit"] == 4

        @pytest.mark.parametrize("limit", [0, -1, -100])
        def test_a_negative_limit_is_clamped_to_all_seeds(
            self, bootstrapped_config: Config, patched_pipelines: dict[str, Recorder], limit: int
        ) -> None:
            execute_run(Namespace(family="llm_family", limit=limit, no_inference=False), bootstrapped_config)

            assert patched_pipelines["llm"].kwargs["limit"] == 0

        def test_an_unknown_family_is_reported_before_any_import(
            self, bootstrapped_config: Config, patched_pipelines: dict[str, Recorder]
        ) -> None:
            args = Namespace(family="nope", limit=0, no_inference=False)

            with pytest.raises(UnknownFamilyError):
                execute_run(args, bootstrapped_config)

            assert patched_pipelines["llm"].calls == []
            assert patched_pipelines["bert"].calls == []

        def test_the_resolved_family_is_handed_to_the_pipeline(
            self, bootstrapped_config: Config, patched_pipelines: dict[str, Recorder]
        ) -> None:
            execute_run(Namespace(family="single_method", limit=0, no_inference=False), bootstrapped_config)

            models = patched_pipelines["llm"].calls[0][0][1]
            assert models.finetuning_methods == ["xqdora"]
