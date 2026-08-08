"""Contract tests for :mod:`reddit.core.environment`.

``prepare_environment`` mutates ``os.environ`` in place; the autouse
``_isolated_environ`` fixture in the root ``conftest.py`` snapshots and
restores the whole process environment around every test.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from reddit.core.config import Config, PathsConfig
from reddit.core.environment import bootstrap_directories, prepare_environment

PATH_FIELDS = tuple(PathsConfig.model_fields)
MANAGED_VARS = ("CUDA_VISIBLE_DEVICES", "HF_HOME", "PYTORCH_CUDA_ALLOC_CONF", "HF_TOKEN", "HUGGINGFACEHUB_API_TOKEN")


@pytest.fixture
def blank_slate(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove every variable ``prepare_environment`` manages."""
    for name in MANAGED_VARS:
        monkeypatch.delenv(name, raising=False)


class TestEnvironmentModule:
    """Directory bootstrap and process-environment preparation."""

    @pytest.mark.unit
    class TestUnits:
        def test_a_gpu_selection_is_exported(self, config: Config, blank_slate: None) -> None:
            prepare_environment(config, gpu="0,1")

            assert os.environ["CUDA_VISIBLE_DEVICES"] == "0,1"

        def test_no_gpu_selection_leaves_the_current_setting_untouched(
            self, config: Config, monkeypatch: pytest.MonkeyPatch
        ) -> None:
            monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "3")

            prepare_environment(config, gpu=None)

            assert os.environ["CUDA_VISIBLE_DEVICES"] == "3"

        def test_no_gpu_selection_does_not_invent_the_variable(self, config: Config, blank_slate: None) -> None:
            prepare_environment(config)

            assert "CUDA_VISIBLE_DEVICES" not in os.environ

        def test_a_gpu_selection_overrides_an_existing_value(
            self, config: Config, monkeypatch: pytest.MonkeyPatch
        ) -> None:
            monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "3")

            prepare_environment(config, gpu="0")

            assert os.environ["CUDA_VISIBLE_DEVICES"] == "0"

        def test_the_allocator_configuration_is_exported(self, config: Config, blank_slate: None) -> None:
            prepare_environment(config)

            assert os.environ["PYTORCH_CUDA_ALLOC_CONF"] == "expandable_segments:True"

        def test_an_exported_allocator_configuration_wins(
            self, config: Config, monkeypatch: pytest.MonkeyPatch
        ) -> None:
            monkeypatch.setenv("PYTORCH_CUDA_ALLOC_CONF", "garbage_collection_threshold:0.8")

            prepare_environment(config)

            assert os.environ["PYTORCH_CUDA_ALLOC_CONF"] == "garbage_collection_threshold:0.8"

        def test_a_null_allocator_configuration_exports_nothing(
            self, config_factory: Callable[..., Config], blank_slate: None
        ) -> None:
            config = config_factory(environment={"pytorch_alloc_conf": None})

            prepare_environment(config)

            assert "PYTORCH_CUDA_ALLOC_CONF" not in os.environ

        def test_the_configured_hf_home_is_exported(
            self, config_factory: Callable[..., Config], tmp_path: Path, blank_slate: None
        ) -> None:
            config = config_factory(environment={"hf_home": str(tmp_path / "hf")})

            prepare_environment(config)

            assert os.environ["HF_HOME"] == str(tmp_path / "hf")

        def test_an_exported_hf_home_wins_over_the_configured_one(
            self, config_factory: Callable[..., Config], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
        ) -> None:
            monkeypatch.setenv("HF_HOME", str(tmp_path / "exported"))
            config = config_factory(environment={"hf_home": str(tmp_path / "configured")})

            prepare_environment(config)

            assert os.environ["HF_HOME"] == str(tmp_path / "exported")

        def test_hf_home_falls_back_to_the_platform_default(
            self, config: Config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, blank_slate: None
        ) -> None:
            monkeypatch.setenv("HOME", str(tmp_path / "home"))

            prepare_environment(config)

            assert os.environ["HF_HOME"] == str(tmp_path / "home" / ".cache" / "huggingface")

        def test_the_effective_hf_home_agrees_with_the_config_property(
            self, config_factory: Callable[..., Config], tmp_path: Path, blank_slate: None
        ) -> None:
            """``Config.hf_home`` mirrors the precedence established here."""
            config = config_factory(environment={"hf_home": str(tmp_path / "hf")})

            prepare_environment(config)

            assert config.hf_home == Path(os.environ["HF_HOME"])

        @pytest.mark.parametrize("variable", ["HUGGINGFACEHUB_API_TOKEN", "HF_TOKEN"])
        def test_a_hub_token_is_re_exported_as_hf_token(
            self, config: Config, monkeypatch: pytest.MonkeyPatch, blank_slate: None, variable: str
        ) -> None:
            monkeypatch.setenv(variable, "secret-token")

            prepare_environment(config)

            assert os.environ["HF_TOKEN"] == "secret-token"

        def test_the_legacy_token_variable_takes_precedence(
            self, config: Config, monkeypatch: pytest.MonkeyPatch, blank_slate: None
        ) -> None:
            monkeypatch.setenv("HUGGINGFACEHUB_API_TOKEN", "legacy")
            monkeypatch.setenv("HF_TOKEN", "current")

            prepare_environment(config)

            assert os.environ["HF_TOKEN"] == "legacy"

        def test_a_missing_token_is_warned_about(
            self, config: Config, blank_slate: None, caplog: pytest.LogCaptureFixture
        ) -> None:
            with caplog.at_level(logging.WARNING):
                prepare_environment(config)

            assert "No Hugging Face token found" in caplog.text
            assert "HF_TOKEN" not in os.environ

        def test_no_token_file_is_written_to_the_cache(
            self,
            config_factory: Callable[..., Config],
            tmp_path: Path,
            monkeypatch: pytest.MonkeyPatch,
            blank_slate: None,
        ) -> None:
            """``login()`` would persist the secret onto a world-readable mount."""
            hf_home = tmp_path / "hf"
            hf_home.mkdir()
            monkeypatch.setenv("HUGGINGFACEHUB_API_TOKEN", "secret-token")
            config = config_factory(environment={"hf_home": str(hf_home)})

            prepare_environment(config)

            assert list(hf_home.iterdir()) == []

    @pytest.mark.integration
    class TestIntegration:
        def test_every_declared_directory_is_created(self, config: Config) -> None:
            bootstrap_directories(config)

            assert all(getattr(config.paths, key).is_dir() for key in PATH_FIELDS)

        def test_bootstrap_is_idempotent(self, config: Config) -> None:
            bootstrap_directories(config)
            bootstrap_directories(config)

            assert config.paths.logs_dir.is_dir()

        def test_bootstrap_preserves_existing_content(self, config: Config) -> None:
            config.paths.logs_dir.mkdir(parents=True)
            (config.paths.logs_dir / "previous.log").write_text("kept", encoding="utf-8")

            bootstrap_directories(config)

            assert (config.paths.logs_dir / "previous.log").read_text(encoding="utf-8") == "kept"

        def test_missing_intermediate_parents_are_created(
            self, config_factory: Callable[..., Config], tmp_path: Path
        ) -> None:
            config = config_factory(paths={key: f"deep/nested/{key}" for key in PATH_FIELDS})

            bootstrap_directories(config)

            assert (tmp_path / "deep" / "nested" / "logs_dir").is_dir()

        def test_bootstrap_creates_nothing_outside_the_declared_paths(self, config: Config, tmp_path: Path) -> None:
            bootstrap_directories(config)

            created = {p for p in tmp_path.iterdir() if p.is_dir()}
            assert created == {getattr(config.paths, key) for key in PATH_FIELDS}

        def test_a_dotenv_file_is_loaded(
            self, config_factory: Callable[..., Config], tmp_path: Path, blank_slate: None
        ) -> None:
            dotenv = tmp_path / ".env"
            dotenv.write_text("REDDIT_TEST_SECRET=from-dotenv\n", encoding="utf-8")
            config = config_factory(environment={"dotenv": str(dotenv)})

            prepare_environment(config)

            assert os.environ["REDDIT_TEST_SECRET"] == "from-dotenv"

        def test_a_token_declared_in_the_dotenv_is_re_exported(
            self, config_factory: Callable[..., Config], tmp_path: Path, blank_slate: None
        ) -> None:
            dotenv = tmp_path / ".env"
            dotenv.write_text("HUGGINGFACEHUB_API_TOKEN=dotenv-token\n", encoding="utf-8")
            config = config_factory(environment={"dotenv": str(dotenv)})

            prepare_environment(config)

            assert os.environ["HF_TOKEN"] == "dotenv-token"

        def test_a_missing_dotenv_file_is_tolerated(
            self, config_factory: Callable[..., Config], tmp_path: Path, blank_slate: None
        ) -> None:
            config = config_factory(environment={"dotenv": str(tmp_path / "absent.env")})

            prepare_environment(config)

            assert "HF_TOKEN" not in os.environ

        def test_preparing_the_environment_creates_no_directories(
            self, config_factory: Callable[..., Config], tmp_path: Path, blank_slate: None
        ) -> None:
            config = config_factory(environment={"hf_home": str(tmp_path / "hf")})

            prepare_environment(config)

            assert not (tmp_path / "hf").exists()

    @pytest.mark.contracts
    class TestContracts:
        @settings(deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
        @given(runs=st.integers(min_value=1, max_value=5))
        def test_bootstrapping_any_number_of_times_is_idempotent(self, runs: int, config: Config) -> None:
            for _ in range(runs):
                bootstrap_directories(config)

            assert all(getattr(config.paths, key).is_dir() for key in PATH_FIELDS)

        @settings(deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
        @given(gpu=st.text(alphabet="0123456789,", min_size=1, max_size=5))
        def test_any_gpu_string_is_exported_verbatim(self, gpu: str, config: Config) -> None:
            prepare_environment(config, gpu=gpu)

            assert os.environ["CUDA_VISIBLE_DEVICES"] == gpu

        @settings(deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
        @given(token=st.text(alphabet="abcdefABCDEF0123456789_-", min_size=1, max_size=20))
        def test_any_token_is_re_exported_unchanged(
            self, token: str, config: Config, monkeypatch: pytest.MonkeyPatch
        ) -> None:
            monkeypatch.setenv("HUGGINGFACEHUB_API_TOKEN", token)

            prepare_environment(config)

            assert os.environ["HF_TOKEN"] == token
