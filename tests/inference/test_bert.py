"""Contract tests for :mod:`reddit.inference.bert`.

The BERT labeller deliberately diverges from the LLM one (in-place answers
update, no method suffix, four comment join keys, half precision); those
divergences are the contract asserted here.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from reddit.core.config import Config
from reddit.core.logging import LogLevel
from reddit.inference import bert, llms
from reddit.inference.corpus import CorpusJob
from reddit.modeling.loading import BERT_MAX_LENGTH


@pytest.fixture
def isolated_bert_boundaries(monkeypatch: pytest.MonkeyPatch) -> list[CorpusJob]:
    """Replace the transformers auto-classes and ``predict_corpus``."""
    executed: list[CorpusJob] = []

    def fake_predict_corpus(_model: Any, _tokenizer: Any, _config: Config, job: CorpusJob, **_kw: Any) -> None:
        executed.append(job)

    monkeypatch.setattr(
        bert,
        "AutoModelForSequenceClassification",
        SimpleNamespace(from_pretrained=lambda *a, **k: SimpleNamespace(config=SimpleNamespace())),
    )
    monkeypatch.setattr(
        bert,
        "AutoTokenizer",
        SimpleNamespace(
            from_pretrained=lambda *a, **k: SimpleNamespace(pad_token=None, eos_token="<e>", pad_token_id=0)
        ),
    )
    monkeypatch.setattr(
        bert, "AutoConfig", SimpleNamespace(from_pretrained=lambda *a, **k: SimpleNamespace(use_cache=False))
    )
    monkeypatch.setattr(bert, "predict_corpus", fake_predict_corpus)
    return executed


class TestBertInferenceModule:
    """Job construction and directory-driven corpus labelling for BERT checkpoints."""

    @pytest.fixture
    def checkpoints(self, tmp_path: Path) -> Path:
        """A directory of ``{model}_{seed}`` checkpoint folders."""
        directory = tmp_path / "checkpoints"
        for name in ("tiny_bert_11", "tiny_bert_22", "stranger_33"):
            (directory / name).mkdir(parents=True)
        return directory

    @pytest.mark.unit
    class TestUnits:
        def test_both_passes_are_built_by_default(self, config: Config) -> None:
            jobs = bert.build_jobs(config, "finbert")

            assert [job.name for job in jobs] == ["submissions", "comments"]

        def test_output_columns_carry_no_method_suffix(self, config: Config) -> None:
            jobs = bert.build_jobs(config, "finbert")

            assert all(job.label_col == "finbert_label" for job in jobs)
            assert all(job.trend_col == "finbert_trend" for job in jobs)

        def test_answers_are_updated_in_place(self, config: Config) -> None:
            """No ``family_suffix``: the consolidated answers file is rewritten."""
            jobs = bert.build_jobs(config, "finbert")

            assert all(job.family_suffix is None for job in jobs)

        def test_the_text_column_is_kept_in_the_labelled_output(self, config: Config) -> None:
            """The text column is kept in the labelled output."""
            jobs = bert.build_jobs(config, "finbert")

            assert all(job.keep_text is True for job in jobs)

        def test_the_model_is_run_in_half_precision(self, config: Config) -> None:
            jobs = bert.build_jobs(config, "finbert")

            assert all(job.half is True for job in jobs)

        def test_the_encoder_truncation_length_is_used(self, config: Config) -> None:
            jobs = bert.build_jobs(config, "finbert")

            assert all(job.max_length == BERT_MAX_LENGTH for job in jobs)

        def test_submissions_join_on_the_post_keys(self, config: Config) -> None:
            submissions, _ = bert.build_jobs(config, "finbert")

            assert submissions.cols == ("created_utc", "id_sub")
            assert submissions.text_col == "title_sub"
            assert submissions.answer_file == "all_final_jae.csv"

        def test_comments_join_on_four_keys(self, config: Config) -> None:
            """One key more than the LLM pipeline, deliberately."""
            _, comments = bert.build_jobs(config, "finbert")

            assert comments.cols == ("created_utc_sub", "created_utc_com", "id_sub", "id_com")
            assert comments.text_col == "body_com"
            assert comments.answer_file == "all_comments_final_jae.csv"

        def test_artefact_names_are_namespaced_by_model_only(self, config: Config) -> None:
            submissions, comments = bert.build_jobs(config, "finbert")

            assert submissions.output_filename == "finbert_submissions_predicted_labels.csv"
            assert comments.output_filename == "finbert_comments_predicted_labels.csv"
            assert submissions.dump_file.name == "finbert_labelled_submissions.jsonl"
            assert submissions.dump_file.parent == config.paths.output_dir

        def test_disabling_submissions_leaves_only_the_comments_pass(
            self, config_factory: Callable[..., Config]
        ) -> None:
            config = config_factory(inference={"submissions": False, "comments": True})

            assert [job.name for job in bert.build_jobs(config, "finbert")] == ["comments"]

        def test_disabling_both_passes_builds_no_work(self, config_factory: Callable[..., Config]) -> None:
            config = config_factory(inference={"submissions": False, "comments": False})

            assert bert.build_jobs(config, "finbert") == []

        # ───────────────────────────── deliberate divergence from the LLMs ──

        def test_the_two_pipelines_disagree_on_the_comment_join_keys(self, config: Config) -> None:
            _, bert_comments = bert.build_jobs(config, "m")
            _, llm_comments = llms.build_jobs(config, "family", "m", "qdora")

            assert set(llm_comments.cols) < set(bert_comments.cols)
            assert set(bert_comments.cols) - set(llm_comments.cols) == {"created_utc_sub"}

        def test_the_two_pipelines_agree_on_the_submission_join_keys(self, config: Config) -> None:
            bert_submissions, _ = bert.build_jobs(config, "m")
            llm_submissions, _ = llms.build_jobs(config, "family", "m", "qdora")

            assert bert_submissions.cols == llm_submissions.cols
            assert bert_submissions.csv_format == llm_submissions.csv_format

        def test_the_two_pipelines_disagree_on_text_retention_and_precision(self, config: Config) -> None:
            bert_job, _ = bert.build_jobs(config, "m")
            llm_job, _ = llms.build_jobs(config, "family", "m", "qdora")

            assert (bert_job.keep_text, bert_job.half) == (True, True)
            assert (llm_job.keep_text, llm_job.half) == (False, False)

    @pytest.mark.integration
    class TestIntegration:
        def test_only_checkpoints_of_the_selected_family_are_labelled(
            self,
            bootstrapped_config: Config,
            checkpoints: Path,
            isolated_bert_boundaries: list[CorpusJob],
            monkeypatch: pytest.MonkeyPatch,
            tmp_path: Path,
        ) -> None:
            monkeypatch.setenv("HF_HOME", str(tmp_path / "hf"))
            models = bootstrapped_config.family("bert_family")

            processed = bert.predict_from_directories(bootstrapped_config, models, checkpoints)

            assert processed == 2  # two seeds of tiny_bert; `stranger` is skipped
            assert [job.name for job in isolated_bert_boundaries] == [
                "submissions",
                "comments",
                "submissions",
                "comments",
            ]

        def test_the_labeller_ignores_family_and_method(
            self,
            bootstrapped_config: Config,
            checkpoints: Path,
            isolated_bert_boundaries: list[CorpusJob],
            monkeypatch: pytest.MonkeyPatch,
            tmp_path: Path,
        ) -> None:
            """Interface parity with the LLM labeller must not leak into the jobs."""
            monkeypatch.setenv("HF_HOME", str(tmp_path / "hf"))

            def silent_log(message: str | None = None, *, level: LogLevel | int | None = 0) -> None:
                return None

            bert.label_corpus(
                config=bootstrapped_config,
                family="ignored_family",
                model_name="tiny_bert",
                model_path=str(checkpoints / "tiny_bert_11"),
                model_conf=SimpleNamespace(),
                tokenizer=SimpleNamespace(),
                finetuning_method="ignored_method",
                log=silent_log,
            )

            assert all(job.family_suffix is None for job in isolated_bert_boundaries)
            assert all("ignored" not in job.label_col for job in isolated_bert_boundaries)

        def test_a_per_model_log_file_is_created(
            self,
            bootstrapped_config: Config,
            checkpoints: Path,
            isolated_bert_boundaries: list[CorpusJob],
            monkeypatch: pytest.MonkeyPatch,
            tmp_path: Path,
        ) -> None:
            monkeypatch.setenv("HF_HOME", str(tmp_path / "hf"))

            bert.predict_from_directories(bootstrapped_config, bootstrapped_config.family("bert_family"), checkpoints)

            log_file = bootstrapped_config.paths.logs_dir / f"tiny_bert_{bootstrapped_config.system.date}.log"
            assert log_file.is_file()
            assert "Labelling submissions..." in log_file.read_text(encoding="utf-8")

        def test_per_run_caches_are_cleaned_up(
            self,
            bootstrapped_config: Config,
            checkpoints: Path,
            isolated_bert_boundaries: list[CorpusJob],
            monkeypatch: pytest.MonkeyPatch,
            tmp_path: Path,
        ) -> None:
            monkeypatch.setenv("HF_HOME", str(tmp_path / "hf"))

            bert.predict_from_directories(bootstrapped_config, bootstrapped_config.family("bert_family"), checkpoints)

            assert list(bootstrapped_config.paths.cache_dir.iterdir()) == []
            assert not (tmp_path / "hf" / "tiny_bert").exists()

        def test_the_checkpoint_directories_are_never_removed(
            self,
            bootstrapped_config: Config,
            checkpoints: Path,
            isolated_bert_boundaries: list[CorpusJob],
            monkeypatch: pytest.MonkeyPatch,
            tmp_path: Path,
        ) -> None:
            monkeypatch.setenv("HF_HOME", str(tmp_path / "hf"))

            bert.predict_from_directories(bootstrapped_config, bootstrapped_config.family("bert_family"), checkpoints)

            assert sorted(p.name for p in checkpoints.iterdir()) == ["stranger_33", "tiny_bert_11", "tiny_bert_22"]

        def test_an_empty_directory_labels_nothing(
            self,
            bootstrapped_config: Config,
            isolated_bert_boundaries: list[CorpusJob],
            monkeypatch: pytest.MonkeyPatch,
            tmp_path: Path,
        ) -> None:
            monkeypatch.setenv("HF_HOME", str(tmp_path / "hf"))
            empty = tmp_path / "empty"
            empty.mkdir()

            processed = bert.predict_from_directories(
                bootstrapped_config, bootstrapped_config.family("bert_family"), empty
            )

            assert processed == 0
            assert isolated_bert_boundaries == []

        def test_a_failing_labelling_pass_does_not_abort_the_directory_scan(
            self,
            bootstrapped_config: Config,
            checkpoints: Path,
            isolated_bert_boundaries: list[CorpusJob],
            monkeypatch: pytest.MonkeyPatch,
            tmp_path: Path,
        ) -> None:
            monkeypatch.setenv("HF_HOME", str(tmp_path / "hf"))

            def exploding(*_args: Any, **_kwargs: Any) -> None:
                raise RuntimeError("checkpoint is corrupt")

            monkeypatch.setattr(bert, "predict_corpus", exploding)

            processed = bert.predict_from_directories(
                bootstrapped_config, bootstrapped_config.family("bert_family"), checkpoints
            )

            assert processed == 2
