"""Contract tests for :mod:`reddit.inference.discovery`.

The two iterators are the only mapping from on-disk artefact names to
``(model, method, seed)`` triples, so filename parsing and extraction cleanup
are the contract under test.
"""

from __future__ import annotations

import logging
import zipfile
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from reddit.inference.discovery import iter_model_archives, iter_model_dirs

MODEL_NAMES = st.text(alphabet="abcdefghij0123456789._", min_size=1, max_size=12).filter(lambda s: not s.endswith("."))
METHODS = st.sampled_from(["qdora", "xqdora"])
SEEDS = st.integers(min_value=0, max_value=2**32 - 1)


def make_archive(directory: Path, stem: str, payload: str = "adapter") -> Path:
    """Write a minimal zip archive named ``{stem}.zip`` into ``directory``."""
    directory.mkdir(parents=True, exist_ok=True)
    archive = directory / f"{stem}.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("adapter_model.safetensors", payload)
    return archive


class TestDiscoveryModule:
    """Checkpoint discovery from zip archives and from checkpoint directories."""

    @pytest.fixture
    def archive_dir(self, tmp_path: Path) -> Path:
        """A models directory holding one archive per method for two models."""
        directory = tmp_path / "models"
        for stem in (
            "gemma2_9b_qdora_107935903",
            "gemma2_9b_xqdora_228277762",
            "qwen2.5_0.5b_qdora_239080115",
        ):
            make_archive(directory, stem)
        return directory

    @pytest.fixture
    def checkpoint_dir(self, tmp_path: Path) -> Path:
        """A models directory holding ``{model}_{seed}`` checkpoint folders."""
        directory = tmp_path / "checkpoints"
        for name in ("finbert_107935903", "bert_base_228277762", "inflabert_239080115"):
            (directory / name).mkdir(parents=True)
        return directory

    @pytest.mark.unit
    class TestUnits:
        # ────────────────────────────────────────────── iter_model_dirs ──

        def test_checkpoint_directories_yield_name_and_path(self, checkpoint_dir: Path) -> None:
            found = list(iter_model_dirs(checkpoint_dir))

            assert [name for name, _ in found] == ["bert_base", "finbert", "inflabert"]
            assert all(Path(path).is_dir() for _, path in found)

        def test_checkpoint_names_keep_their_internal_underscores(self, tmp_path: Path) -> None:
            (tmp_path / "qwen2.5_0.5b_123").mkdir()

            assert list(iter_model_dirs(tmp_path)) == [("qwen2.5_0.5b", str(tmp_path / "qwen2.5_0.5b_123"))]

        def test_checkpoint_directories_are_yielded_in_sorted_order(self, tmp_path: Path) -> None:
            for name in ("c_3", "a_1", "b_2"):
                (tmp_path / name).mkdir()

            assert [name for name, _ in iter_model_dirs(tmp_path)] == ["a", "b", "c"]

        @pytest.mark.parametrize("name", ["no_seed_here", "trailing_", "plain", "model_12a"])
        def test_directories_without_a_numeric_seed_are_ignored(self, tmp_path: Path, name: str) -> None:
            (tmp_path / name).mkdir()

            assert list(iter_model_dirs(tmp_path)) == []

        def test_regular_files_are_never_reported_as_checkpoints(self, tmp_path: Path) -> None:
            (tmp_path / "model_42").write_text("not a checkpoint", encoding="utf-8")

            assert list(iter_model_dirs(tmp_path)) == []

        def test_a_missing_directory_reports_an_error_instead_of_raising(
            self, tmp_path: Path, caplog: pytest.LogCaptureFixture
        ) -> None:
            with caplog.at_level(logging.ERROR):
                found = list(iter_model_dirs(tmp_path / "absent"))

            assert found == []
            assert "Directory not found" in caplog.text

        def test_a_file_given_instead_of_a_directory_yields_nothing(self, tmp_path: Path) -> None:
            regular_file = tmp_path / "models.txt"
            regular_file.write_text("x", encoding="utf-8")

            assert list(iter_model_dirs(regular_file)) == []

        def test_directory_iteration_accepts_a_string_path(self, checkpoint_dir: Path) -> None:
            assert len(list(iter_model_dirs(str(checkpoint_dir)))) == 3

        # ─────────────────────────────────────────── iter_model_archives ──

        def test_archives_yield_model_method_and_extraction_path(self, archive_dir: Path) -> None:
            found = list(iter_model_archives(archive_dir))

            assert [(name, method) for name, method, _ in found] == [
                ("gemma2_9b", "qdora"),
                ("gemma2_9b", "xqdora"),
                ("qwen2.5_0.5b", "qdora"),
            ]

        def test_only_declared_methods_are_reported(self, archive_dir: Path) -> None:
            found = list(iter_model_archives(archive_dir, methods=["xqdora"]))

            assert [(name, method) for name, method, _ in found] == [("gemma2_9b", "xqdora")]

        def test_an_empty_method_set_matches_nothing(self, archive_dir: Path) -> None:
            assert list(iter_model_archives(archive_dir, methods=[])) == []

        @pytest.mark.parametrize(
            "stem",
            [
                pytest.param("model_qdora_notanumber", id="non-numeric-seed"),
                pytest.param("model_unknownmethod_42", id="unregistered-method"),
                pytest.param("model_42", id="too-few-parts"),
                pytest.param("model", id="no-separator"),
            ],
        )
        def test_archives_with_an_unparseable_name_are_skipped_with_a_warning(
            self, tmp_path: Path, stem: str, caplog: pytest.LogCaptureFixture
        ) -> None:
            make_archive(tmp_path, stem)

            with caplog.at_level(logging.WARNING):
                found = list(iter_model_archives(tmp_path))

            assert found == []
            assert "incorrect name format" in caplog.text

        def test_non_zip_files_are_not_considered(self, tmp_path: Path) -> None:
            (tmp_path / "model_qdora_42.tar").write_bytes(b"x")
            (tmp_path / "model_qdora_42").mkdir()

            assert list(iter_model_archives(tmp_path)) == []

        def test_a_missing_archive_directory_reports_an_error(
            self, tmp_path: Path, caplog: pytest.LogCaptureFixture
        ) -> None:
            with caplog.at_level(logging.ERROR):
                found = list(iter_model_archives(tmp_path / "absent"))

            assert found == []
            assert "Directory not found" in caplog.text

        def test_archive_iteration_accepts_a_string_path(self, archive_dir: Path) -> None:
            assert len(list(iter_model_archives(str(archive_dir)))) == 3

    @pytest.mark.integration
    class TestIntegration:
        def test_the_archive_is_extracted_while_the_consumer_holds_it(self, archive_dir: Path) -> None:
            for _, _, path in iter_model_archives(archive_dir, methods=["qdora"]):
                assert Path(path).is_dir()
                assert (Path(path) / "adapter_model.safetensors").read_text(encoding="utf-8") == "adapter"
                break

        def test_extraction_folders_are_removed_after_the_consumer_advances(self, archive_dir: Path) -> None:
            paths = [path for _, _, path in iter_model_archives(archive_dir)]

            assert paths
            assert not any(Path(path).exists() for path in paths)
            assert sorted(p.name for p in archive_dir.iterdir()) == [
                "gemma2_9b_qdora_107935903.zip",
                "gemma2_9b_xqdora_228277762.zip",
                "qwen2.5_0.5b_qdora_239080115.zip",
            ]

        def test_the_archive_itself_is_never_deleted(self, archive_dir: Path) -> None:
            list(iter_model_archives(archive_dir))

            assert len(list(archive_dir.glob("*.zip"))) == 3

        def test_abandoning_the_generator_still_cleans_up(self, archive_dir: Path) -> None:
            generator = iter_model_archives(archive_dir)
            _, _, path = next(generator)
            assert Path(path).is_dir()

            generator.close()

            assert not Path(path).exists()

        def test_only_one_archive_is_extracted_at_a_time(self, archive_dir: Path) -> None:
            seen: list[int] = []

            for _, _, _path in iter_model_archives(archive_dir):
                seen.append(sum(1 for item in archive_dir.iterdir() if item.is_dir()))

            assert seen == [1, 1, 1]

    @pytest.mark.contracts
    class TestContracts:
        @settings(deadline=None, max_examples=40, suppress_health_check=[HealthCheck.function_scoped_fixture])
        @given(model=MODEL_NAMES, method=METHODS, seed=SEEDS)
        def test_a_well_formed_archive_name_round_trips(
            self, model: str, method: str, seed: int, tmp_path: Path
        ) -> None:
            directory = tmp_path / f"case_{model}_{method}_{seed}"
            make_archive(directory, f"{model}_{method}_{seed}")

            found = list(iter_model_archives(directory))

            assert [(name, found_method) for name, found_method, _ in found] == [(model, method)]

        @settings(deadline=None, max_examples=40, suppress_health_check=[HealthCheck.function_scoped_fixture])
        @given(
            model=MODEL_NAMES,
            method=st.text(alphabet="abcdefg", min_size=1, max_size=6).filter(lambda m: m not in {"qdora", "xqdora"}),
            seed=SEEDS,
        )
        def test_an_unregistered_method_is_never_yielded(
            self, model: str, method: str, seed: int, tmp_path: Path
        ) -> None:
            directory = tmp_path / f"case_{model}_{method}_{seed}"
            make_archive(directory, f"{model}_{method}_{seed}")

            assert list(iter_model_archives(directory)) == []

        @settings(deadline=None, max_examples=40, suppress_health_check=[HealthCheck.function_scoped_fixture])
        @given(model=MODEL_NAMES, seed=SEEDS)
        def test_a_well_formed_checkpoint_directory_round_trips(self, model: str, seed: int, tmp_path: Path) -> None:
            directory = tmp_path / f"case_{model}_{seed}"
            (directory / f"{model}_{seed}").mkdir(parents=True, exist_ok=True)

            assert [name for name, _ in iter_model_dirs(directory)] == [model]

        @settings(deadline=None, max_examples=40, suppress_health_check=[HealthCheck.function_scoped_fixture])
        @given(
            model=MODEL_NAMES,
            suffix=st.text(alphabet="abcdefg", min_size=1, max_size=5),
        )
        def test_a_non_numeric_suffix_is_never_a_checkpoint(self, model: str, suffix: str, tmp_path: Path) -> None:
            directory = tmp_path / f"case_{model}_{suffix}"
            (directory / f"{model}_{suffix}").mkdir(parents=True, exist_ok=True)

            assert list(iter_model_dirs(directory)) == []

        @settings(deadline=None, max_examples=30, suppress_health_check=[HealthCheck.function_scoped_fixture])
        @given(stems=st.lists(st.tuples(MODEL_NAMES, METHODS, SEEDS), min_size=1, max_size=5, unique=True))
        def test_discovery_order_is_independent_of_creation_order(
            self, stems: list[tuple[str, str, int]], tmp_path: Path
        ) -> None:
            forward = tmp_path / "forward"
            backward = tmp_path / "backward"
            for model, method, seed in stems:
                make_archive(forward, f"{model}_{method}_{seed}")
            for model, method, seed in reversed(stems):
                make_archive(backward, f"{model}_{method}_{seed}")

            assert [(n, m) for n, m, _ in iter_model_archives(forward)] == [
                (n, m) for n, m, _ in iter_model_archives(backward)
            ]
