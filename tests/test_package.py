"""Contract tests for the ``reddit`` package surface.

Covers ``reddit/__init__.py`` (version metadata), ``reddit/__main__.py``
(``python -m reddit``) and the re-export lists of the light subpackages.  The
heavy subpackages (``inference``, ``modeling``, ``training``) are checked in
their own sub-suites, which are only collected when torch is installed.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

import reddit
from reddit import core, data, tasks


class TestPackageSurface:
    """Version metadata, module entry point and public re-exports."""

    @pytest.mark.unit
    class TestUnits:
        def test_the_package_reports_a_version(self) -> None:
            assert isinstance(reddit.__version__, str)
            assert reddit.__version__

        def test_the_version_lookup_helpers_are_not_leaked(self) -> None:
            """``__init__`` deletes its imports so the namespace stays clean."""
            assert not hasattr(reddit, "version")
            assert not hasattr(reddit, "PackageNotFoundError")

        @pytest.mark.parametrize(
            "name",
            [
                "Config",
                "ConfigError",
                "CorpusUnavailableError",
                "Labeller",
                "LogFn",
                "ModelSpec",
                "Models",
                "RedditError",
                "UndeclaredLabelError",
                "UnknownFamilyError",
                "UnsupportedMethodError",
                "bootstrap_directories",
                "load_config",
                "prepare_environment",
                "print_log",
                "setup_logging",
            ],
        )
        def test_core_re_exports_its_declared_names(self, name: str) -> None:
            assert name in core.__all__
            assert hasattr(core, name)

        def test_core_declares_nothing_it_does_not_export(self) -> None:
            assert sorted(core.__all__) == list(core.__all__)

        def test_the_data_package_exports_the_preparation_entry_point(self) -> None:
            assert set(data.__all__) == {"DataBundle", "load_and_prepare_data"}
            assert all(hasattr(data, name) for name in data.__all__)

        def test_the_tasks_package_exports_every_setup_execute_pair(self) -> None:
            assert set(tasks.__all__) == {"execute_predict", "execute_run", "setup_predict", "setup_run"}
            assert all(hasattr(tasks, name) for name in tasks.__all__)

        def test_importing_core_does_not_pull_in_torch(self) -> None:
            """``core`` must stay importable without the heavy ML stack."""
            import importlib.util  # noqa: PLC0415 — scoped to this test

            for module in ("reddit.core.config", "reddit.core.errors", "reddit.core.utils"):
                spec = importlib.util.find_spec(module)
                assert spec is not None

    @pytest.mark.integration
    class TestIntegration:
        def test_the_module_entry_point_prints_its_help(self, repo_root: Path) -> None:
            completed = subprocess.run(
                [sys.executable, "-m", "reddit", "--help"],
                capture_output=True,
                text=True,
                cwd=repo_root,
                timeout=180,
                check=False,
            )

            assert completed.returncode == 0
            assert "run" in completed.stdout
            assert "predict" in completed.stdout

        def test_the_module_entry_point_reports_its_version(self, repo_root: Path) -> None:
            completed = subprocess.run(
                [sys.executable, "-m", "reddit", "--version"],
                capture_output=True,
                text=True,
                cwd=repo_root,
                timeout=180,
                check=False,
            )

            assert completed.returncode == 0
            assert completed.stdout.strip().startswith("reddit ")

        def test_an_unknown_subcommand_exits_with_a_usage_error(self, repo_root: Path) -> None:
            completed = subprocess.run(
                [sys.executable, "-m", "reddit", "evaluate"],
                capture_output=True,
                text=True,
                cwd=repo_root,
                timeout=180,
                check=False,
            )

            assert completed.returncode == 2
            assert "invalid choice" in completed.stderr
