"""Contract tests for :mod:`reddit.cli`.

``main`` is exercised end to end with the subcommand implementations replaced,
so no pipeline is ever launched: what is verified is argument parsing, config
resolution, exit codes and the bootstrap order (directories, logging, then the
process environment).
"""

from __future__ import annotations

import logging
from argparse import Namespace
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from reddit import cli
from reddit.core.config import Config
from reddit.core.errors import ConfigError, CorpusUnavailableError, UnknownFamilyError


class FuncRecorder:
    """A subcommand double recording its ``(args, config)`` invocation."""

    def __init__(self, returns: int = 1, raises: BaseException | None = None) -> None:
        self.returns = returns
        self.raises = raises
        self.calls: list[tuple[Namespace, Config]] = []

    def __call__(self, args: Namespace, config: Config) -> int:
        self.calls.append((args, config))
        if self.raises is not None:
            raise self.raises
        return self.returns


class TestCliModule:
    """Argument parsing, configuration resolution and the ``main`` dispatcher."""

    @pytest.fixture
    def run_main(self, config_factory: Callable[..., Config], tmp_path: Path) -> Callable[..., int]:
        """Factory: invoke ``main`` against a config that lives in ``tmp_path``."""
        config_factory()  # materialises tmp_path/config.yml

        def _run(*argv: str) -> int:
            return cli.main([*argv, "--config", str(tmp_path / "config.yml")])

        return _run

    @pytest.fixture
    def patched_commands(self, monkeypatch: pytest.MonkeyPatch) -> dict[str, FuncRecorder]:
        """Replace the subcommand implementations before the parser binds them."""
        recorders = {"run": FuncRecorder(returns=1), "predict": FuncRecorder(returns=1)}
        monkeypatch.setattr(cli, "execute_run", recorders["run"])
        monkeypatch.setattr(cli, "execute_predict", recorders["predict"])
        return recorders

    @pytest.mark.unit
    class TestUnits:
        def test_a_subcommand_is_required(self) -> None:
            parser = cli.build_parser()

            with pytest.raises(SystemExit) as excinfo:
                parser.parse_args([])

            assert excinfo.value.code == 2

        def test_an_unknown_subcommand_is_rejected(self) -> None:
            parser = cli.build_parser()

            with pytest.raises(SystemExit):
                parser.parse_args(["evaluate"])

        @pytest.mark.parametrize("command", ["run", "train", "predict"])
        def test_every_documented_subcommand_exists(self, command: str) -> None:
            argv = ["-f", "gemma"] + (["-d", "models"] if command == "predict" else [])

            args = cli.build_parser().parse_args([command, *argv])

            assert args.command == command

        def test_run_dispatches_to_the_training_pipeline_with_labelling(self) -> None:
            args = cli.build_parser().parse_args(["run", "-f", "gemma"])

            assert args.func is cli.execute_run
            assert args.no_inference is False

        def test_train_is_run_without_the_labelling_stage(self) -> None:
            args = cli.build_parser().parse_args(["train", "-f", "gemma"])

            assert args.func is cli.execute_run
            assert args.no_inference is True

        def test_predict_dispatches_to_the_inference_task(self) -> None:
            args = cli.build_parser().parse_args(["predict", "-f", "bert", "-d", "models"])

            assert args.func is cli.execute_predict
            assert args.directory == "models"

        @pytest.mark.parametrize("command", ["run", "train", "predict"])
        def test_the_common_arguments_are_available_everywhere(self, command: str) -> None:
            argv = ["-f", "gemma"] + (["-d", "models"] if command == "predict" else [])

            args = cli.build_parser().parse_args([command, *argv, "--config", "other.yml", "--gpu", "0,1"])

            assert args.config == "other.yml"
            assert args.gpu == "0,1"

        @pytest.mark.parametrize("command", ["run", "train", "predict"])
        def test_the_common_arguments_default_to_unset(self, command: str) -> None:
            argv = ["-f", "gemma"] + (["-d", "models"] if command == "predict" else [])

            args = cli.build_parser().parse_args([command, *argv])

            assert args.config is None
            assert args.gpu is None

        def test_the_version_is_reported_and_exits_cleanly(self, capsys: pytest.CaptureFixture[str]) -> None:
            parser = cli.build_parser()

            with pytest.raises(SystemExit) as excinfo:
                parser.parse_args(["--version"])

            assert excinfo.value.code == 0
            assert "reddit" in capsys.readouterr().out

        def test_the_parser_is_rebuilt_on_every_call(self) -> None:
            first = cli.build_parser()
            second = cli.build_parser()

            assert first is not second

    @pytest.mark.integration
    class TestIntegration:
        def test_a_successful_run_exits_zero(
            self, run_main: Callable[..., int], patched_commands: dict[str, FuncRecorder]
        ) -> None:
            assert run_main("run", "-f", "llm_family") == 0
            assert len(patched_commands["run"].calls) == 1

        def test_the_loaded_configuration_is_handed_to_the_subcommand(
            self, run_main: Callable[..., int], patched_commands: dict[str, FuncRecorder], tmp_path: Path
        ) -> None:
            run_main("run", "-f", "llm_family")

            _, config = patched_commands["run"].calls[0]
            assert config.paths.cache_dir == tmp_path / "cache"

        def test_producing_nothing_exits_one(
            self, run_main: Callable[..., int], monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
        ) -> None:
            monkeypatch.setattr(cli, "execute_run", FuncRecorder(returns=0))

            with caplog.at_level(logging.ERROR):
                exit_code = run_main("run", "-f", "llm_family")

            assert exit_code == 1

        def test_a_runtime_domain_error_exits_one(
            self, run_main: Callable[..., int], monkeypatch: pytest.MonkeyPatch
        ) -> None:
            monkeypatch.setattr(cli, "execute_run", FuncRecorder(raises=CorpusUnavailableError("answers file missing")))

            assert run_main("run", "-f", "llm_family") == 1

        def test_a_configuration_error_becomes_a_usage_message(
            self, run_main: Callable[..., int], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
        ) -> None:
            monkeypatch.setattr(cli, "execute_run", FuncRecorder(raises=UnknownFamilyError("Unknown family `nope`")))

            with pytest.raises(SystemExit) as excinfo:
                run_main("run", "-f", "nope")

            assert excinfo.value.code == 2
            assert "Unknown family `nope`" in capsys.readouterr().err

        def test_an_unrelated_exception_is_not_swallowed(
            self, run_main: Callable[..., int], monkeypatch: pytest.MonkeyPatch
        ) -> None:
            monkeypatch.setattr(cli, "execute_run", FuncRecorder(raises=RuntimeError("driver crashed")))

            with pytest.raises(RuntimeError, match="driver crashed"):
                run_main("run", "-f", "llm_family")

        def test_a_missing_config_file_becomes_a_usage_message(
            self, tmp_path: Path, capsys: pytest.CaptureFixture[str], patched_commands: dict[str, FuncRecorder]
        ) -> None:
            argv = ["run", "-f", "gemma", "--config", str(tmp_path / "absent.yml")]

            with pytest.raises(SystemExit) as excinfo:
                cli.main(argv)

            assert excinfo.value.code == 2
            assert "Config file not found" in capsys.readouterr().err

        def test_the_config_in_the_working_directory_is_picked_up(
            self,
            config_factory: Callable[..., Config],
            patched_commands: dict[str, FuncRecorder],
            monkeypatch: pytest.MonkeyPatch,
            tmp_path: Path,
        ) -> None:
            config_factory()
            monkeypatch.chdir(tmp_path)

            assert cli.main(["run", "-f", "llm_family"]) == 0
            assert patched_commands["run"].calls[0][1].paths.cache_dir == tmp_path / "cache"

        def test_the_declared_directories_are_created(
            self, run_main: Callable[..., int], patched_commands: dict[str, FuncRecorder], tmp_path: Path
        ) -> None:
            run_main("run", "-f", "llm_family")

            assert (tmp_path / "cache").is_dir()
            assert (tmp_path / "logs").is_dir()

        def test_the_log_file_is_named_after_the_command_and_family(
            self, run_main: Callable[..., int], patched_commands: dict[str, FuncRecorder], tmp_path: Path
        ) -> None:
            run_main("run", "-f", "llm_family")

            log_file = tmp_path / "logs" / "run_llm_family.log"
            assert log_file.is_file()
            assert "Loaded config from" in log_file.read_text(encoding="utf-8")

        def test_the_predict_command_gets_its_own_log_file(
            self, run_main: Callable[..., int], patched_commands: dict[str, FuncRecorder], tmp_path: Path
        ) -> None:
            run_main("predict", "-f", "bert_family", "-d", "models")

            assert (tmp_path / "logs" / "predict_bert_family.log").is_file()

        def test_the_gpu_selection_is_exported_before_the_pipeline_runs(
            self, run_main: Callable[..., int], patched_commands: dict[str, FuncRecorder]
        ) -> None:
            import os

            run_main("run", "-f", "llm_family", "--gpu", "0,1")

            assert os.environ["CUDA_VISIBLE_DEVICES"] == "0,1"

        def test_the_configured_sleep_is_honoured(
            self,
            config_factory: Callable[..., Config],
            patched_commands: dict[str, FuncRecorder],
            monkeypatch: pytest.MonkeyPatch,
            tmp_path: Path,
        ) -> None:
            slept: list[float] = []

            def record_sleep(seconds: float) -> None:
                slept.append(seconds)

            monkeypatch.setattr(cli, "sleep", record_sleep)
            config_factory(system={"date": "20240102", "sleep_time": 7})

            cli.main(["run", "-f", "llm_family", "--config", str(tmp_path / "config.yml")])

            assert slept == [7]

        def test_no_sleep_happens_by_default(
            self,
            run_main: Callable[..., int],
            patched_commands: dict[str, FuncRecorder],
            monkeypatch: pytest.MonkeyPatch,
        ) -> None:
            slept: list[float] = []

            def record_sleep(seconds: float) -> None:
                slept.append(seconds)

            monkeypatch.setattr(cli, "sleep", record_sleep)

            run_main("run", "-f", "llm_family")

            assert slept == []

        def test_train_reaches_the_pipeline_with_labelling_disabled(
            self, run_main: Callable[..., int], patched_commands: dict[str, FuncRecorder]
        ) -> None:
            run_main("train", "-f", "llm_family")

            args, _ = patched_commands["run"].calls[0]
            assert args.no_inference is True

    @pytest.mark.integration
    class TestConfigFailures:
        r"""A bad configuration must become a usage message, not a traceback.

        ``load_config`` wraps pydantic's ``ValidationError`` and validator
        ``OSError``\ s into ``ConfigError``, which ``main`` turns into a
        ``parser.error`` (exit code 2).  Regression tests for the previously
        unreachable ``except ConfigError`` guard.
        """

        def test_an_invalid_config_becomes_a_usage_message(
            self,
            raw_config: dict[str, Any],
            write_config: Callable[..., Path],
            capsys: pytest.CaptureFixture[str],
        ) -> None:
            raw_config["labels"]["labels"] = {"down": 1, "neutral": 2, "up": 3}
            argv = ["run", "-f", "llm_family", "--config", str(write_config(raw_config, name="broken.yml"))]

            with pytest.raises(SystemExit) as excinfo:
                cli.main(argv)

            assert excinfo.value.code == 2
            assert "contiguous" in capsys.readouterr().err

        def test_a_missing_dataset_file_becomes_a_usage_message(
            self,
            raw_config: dict[str, Any],
            write_config: Callable[..., Path],
            tmp_path: Path,
        ) -> None:
            raw_config["dataset"]["path"] = str(tmp_path / "never_created.xlsx")
            argv = ["run", "-f", "llm_family", "--config", str(write_config(raw_config, name="no_dataset.yml"))]

            with pytest.raises(SystemExit) as excinfo:
                cli.main(argv)

            assert excinfo.value.code == 2


@pytest.mark.unit
def test_a_config_error_is_the_only_configuration_failure_the_cli_catches() -> None:
    """Documents the type the ``main`` guard is written against."""
    assert issubclass(ConfigError, Exception)
    assert not issubclass(ValidationError, ConfigError)
