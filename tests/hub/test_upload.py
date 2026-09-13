"""Contract tests for :mod:`reddit.hub.upload`.

The Hub client is replaced by a recorder: nothing here talks to the network.
"""

from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from huggingface_hub.errors import HfHubHTTPError

from reddit.core.errors import HubError
from reddit.hub.upload import (
    add_to_collection,
    base_license_files,
    base_model_license,
    publish,
    resolve_collection,
    stage_checkpoint,
)

FULL_SLUG = "acme/pulse-6aa5925828eb6c14fb97a164"


class FakeHub:
    """Records repository creation and folder uploads; optionally fails."""

    def __init__(self, error: Exception | None = None, license_id: str | None = "gemma") -> None:
        self.error = error
        self.license_id = license_id
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def create_repo(self, repo_id: str, **kwargs: Any) -> None:
        if self.error is not None:
            raise self.error
        self.calls.append(("create_repo", {"repo_id": repo_id, **kwargs}))

    def upload_folder(self, **kwargs: Any) -> Any:
        self.calls.append(("upload_folder", kwargs))
        return SimpleNamespace(commit_url="https://huggingface.co/acme/repo/commit/abc")

    def model_info(self, repo_id: str) -> Any:
        if self.error is not None:
            raise self.error
        return SimpleNamespace(card_data={"license": self.license_id} if self.license_id else None)

    def get_collection(self, slug: str) -> Any:
        if slug != FULL_SLUG:
            raise OSError(f"404: {slug}")
        return SimpleNamespace(slug=FULL_SLUG)

    def list_collections(self, owner: str) -> list[Any]:
        if self.error is not None:
            raise self.error
        return [SimpleNamespace(slug=FULL_SLUG), SimpleNamespace(slug=f"{owner}/other-0123456789abcdef01234567")]

    def add_collection_item(self, slug: str, **kwargs: Any) -> None:
        if self.error is not None:
            raise self.error
        self.calls.append(("add_collection_item", {"slug": slug, **kwargs}))

    def list_repo_files(self, repo_id: str) -> list[str]:
        if self.error is not None:
            raise self.error
        return ["config.json", "LICENSE.txt", "USE_POLICY.md", "README.md", "model.safetensors"]


class TestUploadModule:
    """Staging and publishing a repository folder."""

    @pytest.fixture
    def checkpoint(self, tmp_path: Path) -> Path:
        """A saved adapter checkpoint, pickled training arguments included."""
        directory = tmp_path / "tiny_llm_qdora_22"
        directory.mkdir()
        for name in ("adapter_config.json", "adapter_model.safetensors", "config.json", "training_args.bin"):
            (directory / name).write_text(name, encoding="utf-8")
        return directory

    @pytest.mark.unit
    class TestUnits:
        def test_staging_copies_the_checkpoint_minus_the_pickle_and_adds_the_texts(
            self, checkpoint: Path, tmp_path: Path
        ) -> None:
            staging = tmp_path / "staging"

            files = stage_checkpoint(
                checkpoint, staging, card="# card", companions={"evaluation/x.csv": "a,b", "training_args.json": "{}"}
            )

            assert files == [
                "README.md",
                "adapter_config.json",
                "adapter_model.safetensors",
                "config.json",
                "evaluation/x.csv",
                "training_args.json",
            ]
            assert (staging / "README.md").read_text(encoding="utf-8") == "# card"
            assert not (staging / "training_args.bin").exists()
            assert (checkpoint / "training_args.bin").exists()

        def test_restaging_replaces_a_stale_folder(self, checkpoint: Path, tmp_path: Path) -> None:
            staging = tmp_path / "staging"
            staging.mkdir()
            (staging / "stale.txt").write_text("old", encoding="utf-8")

            files = stage_checkpoint(checkpoint, staging, card="# card", companions={})

            assert "stale.txt" not in files
            assert not (staging / "stale.txt").exists()

        def test_publishing_creates_the_repository_then_uploads_the_folder(self, tmp_path: Path) -> None:
            hub = FakeHub()

            url = publish(tmp_path, "acme/repo", private=True, token="hf_write", api=hub)

            assert url == "https://huggingface.co/acme/repo/commit/abc"
            assert hub.calls[0] == (
                "create_repo",
                {"repo_id": "acme/repo", "repo_type": "model", "private": True, "exist_ok": True},
            )
            assert hub.calls[1][1]["folder_path"] == str(tmp_path)
            assert hub.calls[1][1]["repo_id"] == "acme/repo"

        def test_publishing_without_a_token_is_refused_before_any_request(self, tmp_path: Path) -> None:
            hub = FakeHub()

            with pytest.raises(HubError, match="write access"):
                publish(tmp_path, "acme/repo", private=True, token=None, api=hub)
            assert hub.calls == []

        @pytest.mark.parametrize(
            "error",
            [
                HfHubHTTPError(
                    "403 Forbidden",
                    response=httpx.Response(403, request=httpx.Request("POST", "https://huggingface.co/api/repos")),
                ),
                OSError("connection reset"),
            ],
        )
        def test_a_hub_failure_surfaces_as_a_hub_error(self, tmp_path: Path, error: Exception) -> None:
            with pytest.raises(HubError, match="acme/repo"):
                publish(tmp_path, "acme/repo", private=False, token="hf_write", api=FakeHub(error=error))

        def test_the_base_license_comes_from_the_base_card(self) -> None:
            assert base_model_license("google/gemma-2-2b", api=FakeHub(license_id="gemma")) == "gemma"

        def test_a_base_card_without_a_license_yields_none(self) -> None:
            assert base_model_license("ProsusAI/finbert", api=FakeHub(license_id=None)) is None

        def test_an_unreachable_hub_yields_none_and_a_warning(self, caplog: pytest.LogCaptureFixture) -> None:
            with caplog.at_level(logging.WARNING):
                result = base_model_license("acme/base", api=FakeHub(error=OSError("offline")))

            assert result is None
            assert "acme/base" in caplog.text


class TestCollectionsAndLicenses:
    """Collection membership and the base model's license files."""

    @pytest.mark.unit
    class TestUnits:
        @pytest.mark.parametrize(
            "reference",
            [
                FULL_SLUG,
                "acme/pulse",
                "https://huggingface.co/collections/acme/pulse",
                "https://huggingface.co/collections/acme/pulse/",
            ],
        )
        def test_every_spelling_of_a_collection_resolves_to_its_full_slug(self, reference: str) -> None:
            assert resolve_collection(reference, api=FakeHub()) == FULL_SLUG

        def test_a_name_matching_no_collection_is_a_hub_error(self) -> None:
            with pytest.raises(HubError, match="exactly one"):
                resolve_collection("acme/missing", api=FakeHub())

        def test_a_malformed_reference_is_a_hub_error(self) -> None:
            with pytest.raises(HubError, match="not a collection slug"):
                resolve_collection("pulse", api=FakeHub())

        def test_a_repository_is_added_to_the_resolved_collection(self) -> None:
            hub = FakeHub()

            slug = add_to_collection("acme/reddit-pulse-tiny", "acme/pulse", token="hf_write", api=hub)

            assert slug == FULL_SLUG
            assert hub.calls[-1] == (
                "add_collection_item",
                {"slug": FULL_SLUG, "item_id": "acme/reddit-pulse-tiny", "item_type": "model", "exists_ok": True},
            )

        def test_a_rejected_collection_update_is_a_hub_error(self) -> None:
            with pytest.raises(HubError, match="acme/pulse-6aa"):
                add_to_collection("acme/x", FULL_SLUG, token="hf_write", api=FakeHub(error=OSError("403")))

        def test_license_files_are_fetched_by_name(self, tmp_path: Path) -> None:
            fetched: list[tuple[str, str]] = []

            def download(repo_id: str, filename: str) -> str:
                fetched.append((repo_id, filename))
                target = tmp_path / filename
                target.write_text(f"text of {filename}", encoding="utf-8")
                return str(target)

            files = base_license_files("meta-llama/Llama-3.2-3B", api=FakeHub(), download=download)

            assert files == {"LICENSE.txt": "text of LICENSE.txt", "USE_POLICY.md": "text of USE_POLICY.md"}
            assert fetched == [("meta-llama/Llama-3.2-3B", "LICENSE.txt"), ("meta-llama/Llama-3.2-3B", "USE_POLICY.md")]

        def test_an_unreachable_base_yields_no_files(self, caplog: pytest.LogCaptureFixture) -> None:
            with caplog.at_level(logging.WARNING):
                files = base_license_files("acme/base", api=FakeHub(error=OSError("offline")))

            assert files == {}
            assert "acme/base" in caplog.text
