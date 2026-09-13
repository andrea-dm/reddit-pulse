"""Contract tests for :mod:`reddit.tasks.upload`.

Discovery runs against real zip archives and checkpoint directories under
``tmp_path``; the Hub license lookup and the upload itself are replaced at
the module boundary.
"""

from __future__ import annotations

import zipfile
from argparse import ArgumentParser, Namespace
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from reddit.core.config import Config
from reddit.core.environment import bootstrap_directories
from reddit.core.errors import ConfigError, HubError
from reddit.tasks import upload as upload_task
from reddit.tasks.upload import checkpoint_conflicts, discover, execute_upload, setup_upload


class PublishRecorder:
    """Stands in for :func:`reddit.hub.upload.publish`."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.collected: list[tuple[str, str, str | None]] = []

    def __call__(self, staging: Path, repo_id: str, *, private: bool, token: str | None) -> str:
        self.calls.append({"staging": staging, "repo_id": repo_id, "private": private, "token": token})
        return f"https://huggingface.co/{repo_id}"

    def collect(self, repo_id: str, collection: str, *, token: str | None) -> str:
        """Stands in for :func:`reddit.hub.upload.add_to_collection`."""
        self.collected.append((repo_id, collection, token))
        return f"{collection}-0123456789abcdef01234567"


def make_archive(directory: Path, stem: str) -> Path:
    """A ``{model}_{method}_{seed}.zip`` holding a self-describing adapter."""
    directory.mkdir(parents=True, exist_ok=True)
    archive = directory / f"{stem}.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("adapter_config.json", '{"base_model_name_or_path": "acme/tiny-llm", "task_type": "SEQ_CLS"}')
        zf.writestr("adapter_model.safetensors", "weights")
        zf.writestr("config.json", '{"model_type": "llama", "num_labels": 3}')
        zf.writestr("tokenizer.json", "{}")
        zf.writestr("training_args.bin", "pickle")
    return archive


def arguments(**overrides: Any) -> Namespace:
    values: dict[str, Any] = {
        "family": ["llm_family"],
        "model": None,
        "all_families": False,
        "directory": "models",
        "dry_run": True,
        "private": None,
    }
    values.update(overrides)
    return Namespace(**values)


class TestUploadTaskModule:
    """CLI argument registration, discovery and the stage/publish flow."""

    @pytest.fixture
    def parser(self) -> ArgumentParser:
        parser = ArgumentParser(prog="reddit-upload")
        setup_upload(parser)
        return parser

    @pytest.fixture
    def hub_config(self, config_factory: Callable[..., Config]) -> Config:
        """A configuration with a Hub namespace and every directory materialised."""
        config = config_factory(hub={"namespace": "acme"})
        bootstrap_directories(config)
        return config

    @pytest.fixture
    def archives(self, hub_config: Config) -> Path:
        """One selected ``tiny_llm`` archive per method, plus its metric dumps."""
        directory = hub_config.paths.models_dir
        make_archive(directory, "tiny_llm_qdora_22")
        make_archive(directory, "tiny_llm_xqdora_33")
        make_archive(directory, "other_model_qdora_44")
        dumps = hub_config.paths.output_dir / "llm_family_20240102"
        dumps.mkdir(parents=True)
        (dumps / "dist_tiny_llm_test_metrics.jsonl").write_text(
            '{"seed": 22, "finetuning_method": "qdora", "f1_weighted": 0.61, "accuracy": 0.62}\n'
            '{"seed": 33, "finetuning_method": "xqdora", "f1_weighted": 0.59, "accuracy": 0.60}\n',
            encoding="utf-8",
        )
        return directory

    @pytest.fixture
    def offline(self, monkeypatch: pytest.MonkeyPatch) -> PublishRecorder:
        """No network: a fixed base license and a recording publisher."""

        def fixed_license(*args: object, **kwargs: object) -> str:
            del args, kwargs
            return "mit"

        def no_license_files(*args: object, **kwargs: object) -> dict[str, str]:
            del args, kwargs
            return {"LICENSE": "MIT License"}

        monkeypatch.setattr(upload_task, "base_model_license", fixed_license)
        monkeypatch.setattr(upload_task, "base_license_files", no_license_files)
        recorder = PublishRecorder()
        monkeypatch.setattr(upload_task, "publish", recorder)
        monkeypatch.setattr(upload_task, "add_to_collection", recorder.collect)
        return recorder

    @pytest.mark.unit
    class TestUnits:
        def test_the_directory_defaults_to_models_and_dry_run_is_off(self, parser: ArgumentParser) -> None:
            args = parser.parse_args(["-m", "tiny_llm"])

            assert (args.directory, args.dry_run, args.private) == ("models", False, None)

        def test_visibility_flags_are_exclusive(self, parser: ArgumentParser) -> None:
            assert parser.parse_args(["-m", "tiny_llm", "--public"]).private is False
            assert parser.parse_args(["-m", "tiny_llm", "--private"]).private is True
            with pytest.raises(SystemExit):
                parser.parse_args(["-m", "tiny_llm", "--public", "--private"])

        def test_discovery_yields_only_the_selected_models(self, hub_config: Config, archives: Path) -> None:
            found = [(c.model.name, c.method, c.seed) for c in discover(hub_config.family("llm_family"), archives)]

            assert found == [("tiny_llm", "qdora", 22), ("tiny_llm", "xqdora", 33)]

        def test_encoder_checkpoints_are_directories_without_a_method(self, hub_config: Config) -> None:
            directory = hub_config.paths.models_dir
            (directory / "tiny_bert_11").mkdir()
            (directory / "tiny_bert_11" / "model.safetensors").write_text("w", encoding="utf-8")

            found = list(discover(hub_config.family("bert_family"), directory))

            assert [(c.model.id, c.method, c.seed) for c in found] == [("acme/tiny-bert", "-", 11)]
            assert found[0].path == directory / "tiny_bert_11"

        def test_publishing_without_a_token_is_refused_up_front(
            self, hub_config: Config, offline: PublishRecorder, monkeypatch: pytest.MonkeyPatch
        ) -> None:
            monkeypatch.delenv("HF_TOKEN", raising=False)
            monkeypatch.delenv("HF_WRITE_TOKEN", raising=False)

            with pytest.raises(HubError, match="write access"):
                execute_upload(arguments(dry_run=False), hub_config)
            assert offline.calls == []

        @pytest.mark.parametrize("dry_run", [True, False])
        def test_two_checkpoints_for_one_repository_are_refused_before_anything_is_staged(
            self,
            hub_config: Config,
            archives: Path,
            offline: PublishRecorder,
            monkeypatch: pytest.MonkeyPatch,
            dry_run: bool,
        ) -> None:
            make_archive(archives, "tiny_llm_qdora_11")
            monkeypatch.setenv("HF_WRITE_TOKEN", "hf_write")

            with pytest.raises(HubError, match=r"tiny_llm qdora: tiny_llm_qdora_11\.zip, tiny_llm_qdora_22\.zip"):
                execute_upload(arguments(directory=str(archives), dry_run=dry_run), hub_config)

            assert offline.calls == []
            assert list((hub_config.paths.output_dir / "hub").glob("*")) == []

        def test_unselected_models_and_one_checkpoint_per_method_do_not_conflict(
            self, hub_config: Config, archives: Path
        ) -> None:
            make_archive(archives, "other_model_qdora_55")

            assert checkpoint_conflicts(hub_config.family("llm_family"), archives) == {}

        def test_encoder_checkpoints_of_one_model_conflict_without_a_method(self, hub_config: Config) -> None:
            directory = hub_config.paths.models_dir
            for name in ("tiny_bert_11", "tiny_bert_12"):
                (directory / name).mkdir()

            assert checkpoint_conflicts(hub_config.family("bert_family"), directory) == {
                ("tiny_bert", "-"): ["tiny_bert_11", "tiny_bert_12"]
            }

        def test_a_missing_directory_has_no_conflicts(self, hub_config: Config, tmp_path: Path) -> None:
            assert checkpoint_conflicts(hub_config.family("llm_family"), tmp_path / "absent") == {}

        def test_a_missing_namespace_is_a_config_error(
            self, bootstrapped_config: Config, offline: PublishRecorder, archives_for: None
        ) -> None:
            del archives_for
            with pytest.raises(ConfigError, match=r"hub\.namespace"):
                execute_upload(arguments(directory=str(bootstrapped_config.paths.models_dir)), bootstrapped_config)

    @pytest.fixture
    def archives_for(self, bootstrapped_config: Config) -> None:
        make_archive(bootstrapped_config.paths.models_dir, "tiny_llm_qdora_22")

    @pytest.mark.integration
    class TestIntegration:
        def test_a_dry_run_stages_every_checkpoint_and_uploads_nothing(
            self, hub_config: Config, archives: Path, offline: PublishRecorder
        ) -> None:
            staged = execute_upload(arguments(directory=str(archives)), hub_config)

            assert staged == 2
            assert offline.calls == []
            folder = hub_config.paths.output_dir / "hub" / "reddit-pulse-tiny_llm-qdora"
            card = (folder / "README.md").read_text(encoding="utf-8")
            assert "acme/reddit-pulse-tiny_llm-qdora" in card
            assert "license: mit" in card
            assert "| **test** | 0.620 | 0.610 |" in card
            assert "| `LICENSE` | license / use-policy notice" in card
            assert (folder / "LICENSE").read_text(encoding="utf-8") == "MIT License"
            assert (folder / "adapter_model.safetensors").exists()
            assert (folder / "evaluation" / "seeds_test_metrics.csv").exists()
            assert not (folder / "training_args.bin").exists()
            assert (hub_config.paths.output_dir / "hub" / "reddit-pulse-tiny_llm-xqdora").is_dir()

        def test_publishing_hands_each_staged_folder_to_the_hub(
            self, hub_config: Config, archives: Path, offline: PublishRecorder, monkeypatch: pytest.MonkeyPatch
        ) -> None:
            monkeypatch.setenv("HF_TOKEN", "hf_write")

            published = execute_upload(arguments(directory=str(archives), dry_run=False), hub_config)

            assert published == 2
            assert [c["repo_id"] for c in offline.calls] == [
                "acme/reddit-pulse-tiny_llm-qdora",
                "acme/reddit-pulse-tiny_llm-xqdora",
            ]
            assert all(c["private"] is True and c["token"] == "hf_write" for c in offline.calls)
            assert offline.calls[0]["staging"] == hub_config.paths.output_dir / "hub" / "reddit-pulse-tiny_llm-qdora"

        def test_the_visibility_flag_overrides_the_configuration(
            self, hub_config: Config, archives: Path, offline: PublishRecorder, monkeypatch: pytest.MonkeyPatch
        ) -> None:
            monkeypatch.setenv("HF_TOKEN", "hf_write")

            execute_upload(arguments(directory=str(archives), dry_run=False, private=False), hub_config)

            assert all(c["private"] is False for c in offline.calls)

        def test_the_write_token_wins_over_the_read_token(
            self, hub_config: Config, archives: Path, offline: PublishRecorder, monkeypatch: pytest.MonkeyPatch
        ) -> None:
            monkeypatch.setenv("HF_TOKEN", "hf_read")
            monkeypatch.setenv("HF_WRITE_TOKEN", "hf_write")

            execute_upload(arguments(directory=str(archives), dry_run=False), hub_config)

            assert all(c["token"] == "hf_write" for c in offline.calls)

        def test_published_repositories_join_the_configured_collection(
            self, config_factory: Callable[..., Config], offline: PublishRecorder, monkeypatch: pytest.MonkeyPatch
        ) -> None:
            config = config_factory(hub={"namespace": "acme", "collection": "acme/pulse"})
            bootstrap_directories(config)
            make_archive(config.paths.models_dir, "tiny_llm_qdora_22")
            monkeypatch.setenv("HF_WRITE_TOKEN", "hf_write")

            execute_upload(arguments(directory=str(config.paths.models_dir), dry_run=False), config)

            assert offline.collected == [("acme/reddit-pulse-tiny_llm-qdora", "acme/pulse", "hf_write")]

        def test_a_dry_run_joins_no_collection(
            self, config_factory: Callable[..., Config], offline: PublishRecorder
        ) -> None:
            config = config_factory(hub={"namespace": "acme", "collection": "acme/pulse"})
            bootstrap_directories(config)
            make_archive(config.paths.models_dir, "tiny_llm_qdora_22")

            execute_upload(arguments(directory=str(config.paths.models_dir)), config)

            assert offline.collected == []

        def test_an_empty_directory_publishes_nothing(self, hub_config: Config, offline: PublishRecorder) -> None:
            assert execute_upload(arguments(directory=str(hub_config.paths.models_dir)), hub_config) == 0
