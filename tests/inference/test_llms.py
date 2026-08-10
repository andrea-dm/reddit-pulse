"""Contract tests for :mod:`reddit.inference.llms`.

Only the job-building policy and the discovery/cleanup lifecycle are exercised:
the transformers auto-classes and ``predict_corpus`` are replaced at the module
boundary, so no checkpoint is ever materialised.
"""

from __future__ import annotations

import zipfile
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from reddit.core.config import Config
from reddit.inference import llms
from reddit.inference.corpus import CorpusJob
from reddit.modeling.loading import LLM_MAX_LENGTH


@pytest.fixture
def isolated_llm_boundaries(monkeypatch: pytest.MonkeyPatch) -> list[CorpusJob]:
    """Replace the transformers auto-classes and ``predict_corpus``."""
    executed: list[CorpusJob] = []

    def fake_from_pretrained(*_args: Any, **_kwargs: Any) -> SimpleNamespace:
        return SimpleNamespace(config=SimpleNamespace(pad_token_id=None))

    def fake_tokenizer(*_args: Any, **_kwargs: Any) -> SimpleNamespace:
        return SimpleNamespace(pad_token=None, eos_token="<eos>", pad_token_id=0, padding_side="left")

    def fake_config(*_args: Any, **_kwargs: Any) -> SimpleNamespace:
        return SimpleNamespace(use_cache=False, pad_token_id=None)

    def fake_predict_corpus(_model: Any, _tokenizer: Any, _config: Config, job: CorpusJob, **_kw: Any) -> None:
        executed.append(job)

    monkeypatch.setattr(
        llms, "AutoModelForSequenceClassification", SimpleNamespace(from_pretrained=fake_from_pretrained)
    )
    monkeypatch.setattr(llms, "AutoTokenizer", SimpleNamespace(from_pretrained=fake_tokenizer))
    monkeypatch.setattr(llms, "AutoConfig", SimpleNamespace(from_pretrained=fake_config))
    monkeypatch.setattr(llms, "predict_corpus", fake_predict_corpus)
    return executed


class TestLlmsInferenceModule:
    """Job construction and archive-driven corpus labelling for LLM checkpoints."""

    @pytest.fixture
    def archives(self, tmp_path: Path) -> Path:
        """A directory of ``{model}_{method}_{seed}.zip`` archives."""
        directory = tmp_path / "archives"
        directory.mkdir()
        for stem in ("tiny_llm_qdora_11", "tiny_llm_xqdora_22", "stranger_qdora_33"):
            with zipfile.ZipFile(directory / f"{stem}.zip", "w") as zf:
                zf.writestr("adapter_model.safetensors", "weights")
        return directory

    @pytest.mark.unit
    class TestUnits:
        def test_both_passes_are_built_by_default(self, config: Config) -> None:
            jobs = llms.build_jobs(config, "gemma", "gemma2_9b", "qdora")

            assert [job.name for job in jobs] == ["submissions", "comments"]

        def test_output_columns_carry_the_model_and_the_method(self, config: Config) -> None:
            jobs = llms.build_jobs(config, "gemma", "gemma2_9b", "qdora")

            assert all(job.label_col == "gemma2_9b_qdora_label" for job in jobs)
            assert all(job.trend_col == "gemma2_9b_qdora_trend" for job in jobs)

        def test_answers_are_written_to_a_per_family_copy(self, config: Config) -> None:
            jobs = llms.build_jobs(config, "gemma", "gemma2_9b", "qdora")

            assert all(job.family_suffix == "gemma" for job in jobs)

        def test_the_text_column_is_dropped_from_the_labelled_output(self, config: Config) -> None:
            jobs = llms.build_jobs(config, "gemma", "gemma2_9b", "qdora")

            assert all(job.keep_text is False for job in jobs)
            assert all(job.half is False for job in jobs)

        def test_the_llm_truncation_length_is_used(self, config: Config) -> None:
            jobs = llms.build_jobs(config, "gemma", "gemma2_9b", "qdora")

            assert all(job.max_length == LLM_MAX_LENGTH for job in jobs)

        def test_submissions_join_on_the_post_keys(self, config: Config) -> None:
            submissions, _ = llms.build_jobs(config, "gemma", "gemma2_9b", "qdora")

            assert submissions.cols == ("created_utc", "id_sub")
            assert submissions.text_col == "title_sub"
            assert submissions.csv_format == "{}_final_jae.csv"
            assert submissions.answer_file == "all_final_jae.csv"

        def test_comments_join_on_three_keys(self, config: Config) -> None:
            """Three keys; the BERT pipeline uses four."""
            _, comments = llms.build_jobs(config, "gemma", "gemma2_9b", "qdora")

            assert comments.cols == ("created_utc_com", "id_sub", "id_com")
            assert comments.text_col == "body_com"
            assert comments.csv_format == "{}_comments_final_jae.csv"
            assert comments.answer_file == "all_comments_final_jae.csv"

        def test_artefact_names_are_namespaced_by_model_and_method(self, config: Config) -> None:
            submissions, comments = llms.build_jobs(config, "gemma", "gemma2_9b", "xqdora")

            assert submissions.output_filename == "gemma2_9b_submissions_xqdora_predicted_labels.csv"
            assert comments.output_filename == "gemma2_9b_comments_xqdora_predicted_labels.csv"
            assert submissions.dump_file.name == "gemma2_9b_xqdora_labelled_submissions.jsonl"
            assert submissions.dump_file.parent == config.paths.output_dir

        def test_the_batch_size_comes_from_the_configuration(self, config: Config) -> None:
            jobs = llms.build_jobs(config, "gemma", "gemma2_9b", "qdora")

            assert all(job.batch_size == config.inference.batch_size for job in jobs)

        def test_disabling_submissions_leaves_only_the_comments_pass(
            self, config_factory: Callable[..., Config]
        ) -> None:
            config = config_factory(inference={"submissions": False, "comments": True})

            assert [job.name for job in llms.build_jobs(config, "gemma", "m", "qdora")] == ["comments"]

        def test_disabling_comments_leaves_only_the_submissions_pass(
            self, config_factory: Callable[..., Config]
        ) -> None:
            config = config_factory(inference={"submissions": True, "comments": False})

            assert [job.name for job in llms.build_jobs(config, "gemma", "m", "qdora")] == ["submissions"]

        def test_disabling_both_passes_builds_no_work(self, config_factory: Callable[..., Config]) -> None:
            config = config_factory(inference={"submissions": False, "comments": False})

            assert llms.build_jobs(config, "gemma", "m", "qdora") == []

    @pytest.mark.integration
    class TestIntegration:
        def test_only_archives_of_the_selected_family_are_labelled(
            self,
            bootstrapped_config: Config,
            archives: Path,
            isolated_llm_boundaries: list[CorpusJob],
            monkeypatch: pytest.MonkeyPatch,
            tmp_path: Path,
        ) -> None:
            monkeypatch.setenv("HF_HOME", str(tmp_path / "hf"))
            models = bootstrapped_config.family("llm_family")

            processed = llms.predict_from_archives(bootstrapped_config, models, archives)

            assert processed == 2  # tiny_llm x {qdora, xqdora}; `stranger` is skipped
            assert {job.label_col for job in isolated_llm_boundaries} == {
                "tiny_llm_qdora_label",
                "tiny_llm_xqdora_label",
            }

        def test_each_checkpoint_runs_both_passes(
            self,
            bootstrapped_config: Config,
            archives: Path,
            isolated_llm_boundaries: list[CorpusJob],
            monkeypatch: pytest.MonkeyPatch,
            tmp_path: Path,
        ) -> None:
            monkeypatch.setenv("HF_HOME", str(tmp_path / "hf"))

            llms.predict_from_archives(bootstrapped_config, bootstrapped_config.family("llm_family"), archives)

            assert [job.name for job in isolated_llm_boundaries] == [
                "submissions",
                "comments",
                "submissions",
                "comments",
            ]

        def test_only_the_declared_methods_are_scanned(
            self,
            bootstrapped_config: Config,
            archives: Path,
            isolated_llm_boundaries: list[CorpusJob],
            monkeypatch: pytest.MonkeyPatch,
            tmp_path: Path,
        ) -> None:
            monkeypatch.setenv("HF_HOME", str(tmp_path / "hf"))
            models = bootstrapped_config.family("single_method")  # xqdora only

            processed = llms.predict_from_archives(bootstrapped_config, models, archives)

            assert processed == 1
            assert {job.label_col for job in isolated_llm_boundaries} == {"tiny_llm_xqdora_label"}

        def test_a_per_model_log_file_is_created(
            self,
            bootstrapped_config: Config,
            archives: Path,
            isolated_llm_boundaries: list[CorpusJob],
            monkeypatch: pytest.MonkeyPatch,
            tmp_path: Path,
            capsys: pytest.CaptureFixture[str],
        ) -> None:
            monkeypatch.setenv("HF_HOME", str(tmp_path / "hf"))

            llms.predict_from_archives(bootstrapped_config, bootstrapped_config.family("llm_family"), archives)

            log_file = bootstrapped_config.paths.logs_dir / f"tiny_llm_{bootstrapped_config.system.date}.log"
            assert log_file.is_file()
            assert "Running `tiny_llm_qdora_" in log_file.read_text(encoding="utf-8")
            assert "Running `tiny_llm_qdora_" in capsys.readouterr().out

        def test_per_run_caches_are_cleaned_up(
            self,
            bootstrapped_config: Config,
            archives: Path,
            isolated_llm_boundaries: list[CorpusJob],
            monkeypatch: pytest.MonkeyPatch,
            tmp_path: Path,
        ) -> None:
            monkeypatch.setenv("HF_HOME", str(tmp_path / "hf"))

            llms.predict_from_archives(bootstrapped_config, bootstrapped_config.family("llm_family"), archives)

            assert list(bootstrapped_config.paths.cache_dir.iterdir()) == []
            assert not (tmp_path / "hf" / "tiny_llm").exists()

        def test_extracted_archives_leave_no_residue(
            self,
            bootstrapped_config: Config,
            archives: Path,
            isolated_llm_boundaries: list[CorpusJob],
            monkeypatch: pytest.MonkeyPatch,
            tmp_path: Path,
        ) -> None:
            monkeypatch.setenv("HF_HOME", str(tmp_path / "hf"))

            llms.predict_from_archives(bootstrapped_config, bootstrapped_config.family("llm_family"), archives)

            assert sorted(p.name for p in archives.iterdir()) == [
                "stranger_qdora_33.zip",
                "tiny_llm_qdora_11.zip",
                "tiny_llm_xqdora_22.zip",
            ]

        def test_an_empty_directory_labels_nothing(
            self,
            bootstrapped_config: Config,
            isolated_llm_boundaries: list[CorpusJob],
            monkeypatch: pytest.MonkeyPatch,
            tmp_path: Path,
        ) -> None:
            monkeypatch.setenv("HF_HOME", str(tmp_path / "hf"))
            empty = tmp_path / "empty"
            empty.mkdir()

            processed = llms.predict_from_archives(bootstrapped_config, bootstrapped_config.family("llm_family"), empty)

            assert processed == 0
            assert isolated_llm_boundaries == []

        def test_a_failing_labelling_pass_does_not_abort_the_directory_scan(
            self,
            bootstrapped_config: Config,
            archives: Path,
            monkeypatch: pytest.MonkeyPatch,
            tmp_path: Path,
        ) -> None:
            """Multi-day runs must survive a single broken checkpoint."""
            monkeypatch.setenv("HF_HOME", str(tmp_path / "hf"))

            def exploding(*_args: Any, **_kwargs: Any) -> None:
                raise RuntimeError("checkpoint is corrupt")

            monkeypatch.setattr(llms, "predict_corpus", exploding)
            monkeypatch.setattr(
                llms,
                "AutoModelForSequenceClassification",
                SimpleNamespace(from_pretrained=lambda *a, **k: SimpleNamespace(config=SimpleNamespace())),
            )
            monkeypatch.setattr(
                llms,
                "AutoTokenizer",
                SimpleNamespace(
                    from_pretrained=lambda *a, **k: SimpleNamespace(pad_token=None, eos_token="<e>", pad_token_id=0)
                ),
            )
            monkeypatch.setattr(
                llms, "AutoConfig", SimpleNamespace(from_pretrained=lambda *a, **k: SimpleNamespace(use_cache=False))
            )

            processed = llms.predict_from_archives(
                bootstrapped_config, bootstrapped_config.family("llm_family"), archives
            )

            assert processed == 2
