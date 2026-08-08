"""Contract tests for :mod:`reddit.inference.corpus`.

``predict_corpus`` is exercised end to end against the real filesystem with a
CPU-only model/tokenizer double: no checkpoint is loaded and no CUDA call is
made beyond the empty-cache no-op the module performs itself.
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import torch
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from pandas import DataFrame, read_csv

from reddit.core.config import Config, LabelsConfig
from reddit.core.errors import CorpusUnavailableError
from reddit.inference.corpus import SUBREDDITS, CorpusJob, predict_corpus, process_batch, update_answers

from ..conftest import RecordingLog
from .conftest import FakeModel, FakeTokenizer


def labelled_frame(rows: int = 3, *, label_col: str = "tiny_label", trend_col: str = "tiny_trend") -> DataFrame:
    """A labelled frame joinable with the ``write_answers`` fixture output."""
    created: list[int] = []
    ids: list[str] = []
    for subreddit in SUBREDDITS:
        created.extend(1_600_000_000 + i for i in range(rows))
        ids.extend(f"{subreddit}_{i}" for i in range(rows))
    return DataFrame(
        {
            "created_utc": created,
            "id_sub": ids,
            "title_sub": [f"labelled text {i}" for i in range(len(ids))],
            label_col: ["up"] * len(ids),
            trend_col: [1] * len(ids),
        }
    )


class TestCorpusModule:
    """The corpus job description, batch inference and answers-file bookkeeping."""

    @pytest.mark.unit
    class TestUnits:
        # ─────────────────────────────────────────────────── CorpusJob ──

        def test_a_job_is_immutable(self, submissions_job: CorpusJob) -> None:
            with pytest.raises(dataclasses.FrozenInstanceError):
                submissions_job.batch_size = 1  # pyright: ignore[reportAttributeAccessIssue]

        def test_a_job_carries_no_undeclared_state(self, submissions_job: CorpusJob) -> None:
            with pytest.raises((AttributeError, TypeError)):
                submissions_job.extra = "value"  # pyright: ignore[reportAttributeAccessIssue]

        def test_behavioural_flags_default_to_the_llm_shape(self, bootstrapped_config: Config) -> None:
            job = CorpusJob(
                name="submissions",
                desc="d",
                csv_format="{}_final_jae.csv",
                output_filename="o.csv",
                answer_file="a.csv",
                dump_file=bootstrapped_config.paths.output_dir / "d.jsonl",
                label_col="l",
                trend_col="t",
                text_col="title_sub",
                cols=("created_utc",),
                batch_size=8,
                max_length=64,
            )

            assert job.half is False
            assert job.keep_text is False
            assert job.family_suffix is None

        # ───────────────────────────────────────────────── process_batch ──

        def test_predictions_are_decoded_through_the_label_authority(
            self, fake_tokenizer: FakeTokenizer, labels_config: LabelsConfig, submissions_job: CorpusJob
        ) -> None:
            model = FakeModel(predictions=[2, 0, 1])

            results = process_batch(
                texts=["a", "b", "c"],
                model=model,
                tokenizer=fake_tokenizer,
                labels=labels_config,
                device="cpu",
                job=submissions_job,
            )

            assert [r["tiny_label"] for r in results] == ["up", "down", "neutral"]
            assert [r["tiny_trend"] for r in results] == [1, -1, 0]

        def test_one_result_is_produced_per_text(
            self, fake_tokenizer: FakeTokenizer, labels_config: LabelsConfig, submissions_job: CorpusJob
        ) -> None:
            results = process_batch(
                texts=["a", "b", "c", "d"],
                model=FakeModel(),
                tokenizer=fake_tokenizer,
                labels=labels_config,
                device="cpu",
                job=submissions_job,
            )

            assert len(results) == 4
            assert all(set(r) == {"tiny_label", "tiny_trend"} for r in results)

        def test_the_job_supplies_the_truncation_length(
            self, fake_tokenizer: FakeTokenizer, labels_config: LabelsConfig, submissions_job: CorpusJob
        ) -> None:
            process_batch(
                texts=["a"],
                model=FakeModel(),
                tokenizer=fake_tokenizer,
                labels=labels_config,
                device="cpu",
                job=submissions_job,
            )

            call = fake_tokenizer.calls[0]
            assert call["max_length"] == submissions_job.max_length
            assert call["truncation"] is True
            assert call["padding"] is True
            assert call["return_tensors"] == "pt"

        def test_an_out_of_range_prediction_decodes_to_unknown(
            self, fake_tokenizer: FakeTokenizer, labels_config: LabelsConfig, submissions_job: CorpusJob
        ) -> None:
            """A checkpoint with more heads than declared labels must not crash."""
            model = FakeModel(predictions=[4], num_classes=5)

            results = process_batch(
                texts=["a"],
                model=model,
                tokenizer=fake_tokenizer,
                labels=labels_config,
                device="cpu",
                job=submissions_job,
            )

            assert results == [{"tiny_label": "unknown", "tiny_trend": "unknown"}]

        def test_an_empty_batch_produces_no_results(
            self, fake_tokenizer: FakeTokenizer, labels_config: LabelsConfig, submissions_job: CorpusJob
        ) -> None:
            results = process_batch(
                texts=[],
                model=FakeModel(),
                tokenizer=fake_tokenizer,
                labels=labels_config,
                device="cpu",
                job=submissions_job,
            )

            assert results == []

        def test_inference_runs_without_building_a_gradient_graph(
            self, fake_tokenizer: FakeTokenizer, labels_config: LabelsConfig, submissions_job: CorpusJob
        ) -> None:
            assert torch.is_grad_enabled()

            process_batch(
                texts=["a"],
                model=FakeModel(),
                tokenizer=fake_tokenizer,
                labels=labels_config,
                device="cpu",
                job=submissions_job,
            )

            assert torch.is_grad_enabled()

    @pytest.mark.integration
    class TestIntegration:
        # ──────────────────────────────────────────────── update_answers ──

        def test_labels_are_merged_into_the_answers_file_in_place(
            self, write_answers: Callable[..., Path], submissions_job: CorpusJob
        ) -> None:
            answers = write_answers()
            before = read_csv(answers)

            update_answers(answers, labelled_frame(), submissions_job)

            after = read_csv(answers)
            assert len(after) == len(before)
            assert list(after.columns) == [*before.columns, "tiny_label", "tiny_trend"]
            assert set(after["tiny_label"].dropna()) == {"up"}

        def test_the_text_column_never_enters_the_merge(
            self, write_answers: Callable[..., Path], submissions_job: CorpusJob
        ) -> None:
            """A duplicated name would make pandas emit `_x`/`_y` variants."""
            answers = write_answers()

            update_answers(answers, labelled_frame(), submissions_job)

            columns = list(read_csv(answers).columns)
            assert "title_sub" in columns
            assert not any(c.endswith(("_x", "_y")) for c in columns)

        def test_a_previous_run_of_the_same_model_is_replaced_not_duplicated(
            self, write_answers: Callable[..., Path], submissions_job: CorpusJob
        ) -> None:
            answers = write_answers(extra={"tiny_label": ["stale"] * (4 * len(SUBREDDITS))})

            update_answers(answers, labelled_frame(rows=4), submissions_job)

            after = read_csv(answers)
            assert list(after.columns).count("tiny_label") == 1
            assert "stale" not in set(after["tiny_label"])

        def test_rows_without_a_prediction_are_kept_with_an_empty_label(
            self, write_answers: Callable[..., Path], submissions_job: CorpusJob
        ) -> None:
            answers = write_answers(rows=4)

            update_answers(answers, labelled_frame(rows=2), submissions_job)

            after = read_csv(answers)
            assert len(after) == 4 * len(SUBREDDITS)
            assert after["tiny_label"].isna().sum() == 2 * len(SUBREDDITS)

        def test_no_temporary_file_survives_a_successful_update(
            self, write_answers: Callable[..., Path], submissions_job: CorpusJob
        ) -> None:
            answers = write_answers()

            update_answers(answers, labelled_frame(), submissions_job)

            assert list(answers.parent.glob("*.tmp")) == []

        def test_a_missing_answers_file_is_reported_as_corpus_unavailable(
            self, bootstrapped_config: Config, submissions_job: CorpusJob
        ) -> None:
            absent = bootstrapped_config.paths.results_dir / "all_final_jae.csv"
            frame = labelled_frame()

            with pytest.raises(CorpusUnavailableError, match="does not exist"):
                update_answers(absent, frame, submissions_job)

        def test_a_family_suffix_writes_to_a_per_family_copy(
            self, write_answers: Callable[..., Path], submissions_job: CorpusJob
        ) -> None:
            answers = write_answers()
            job = dataclasses.replace(submissions_job, family_suffix="gemma")

            update_answers(answers, labelled_frame(), job)

            per_family = answers.parent / "all_final_jae_gemma.csv"
            assert per_family.is_file()
            assert "tiny_label" in read_csv(per_family).columns
            assert "tiny_label" not in read_csv(answers).columns

        def test_a_second_family_run_accumulates_on_the_per_family_copy(
            self, write_answers: Callable[..., Path], submissions_job: CorpusJob
        ) -> None:
            answers = write_answers()
            first = dataclasses.replace(submissions_job, family_suffix="gemma")
            second = dataclasses.replace(first, label_col="other_label", trend_col="other_trend")

            update_answers(answers, labelled_frame(), first)
            update_answers(answers, labelled_frame(label_col="other_label", trend_col="other_trend"), second)

            per_family = read_csv(answers.parent / "all_final_jae_gemma.csv")
            assert {"tiny_label", "other_label"} <= set(per_family.columns)
            assert "tiny_label" not in read_csv(answers).columns

        def test_two_families_never_share_an_answers_copy(
            self, write_answers: Callable[..., Path], submissions_job: CorpusJob
        ) -> None:
            answers = write_answers()

            update_answers(answers, labelled_frame(), dataclasses.replace(submissions_job, family_suffix="gemma"))
            update_answers(answers, labelled_frame(), dataclasses.replace(submissions_job, family_suffix="qwen"))

            assert (answers.parent / "all_final_jae_gemma.csv").is_file()
            assert (answers.parent / "all_final_jae_qwen.csv").is_file()

        def test_a_missing_base_file_blocks_the_first_family_run(
            self, bootstrapped_config: Config, submissions_job: CorpusJob
        ) -> None:
            absent = bootstrapped_config.paths.results_dir / "all_final_jae.csv"
            job = dataclasses.replace(submissions_job, family_suffix="gemma")
            frame = labelled_frame()

            with pytest.raises(CorpusUnavailableError):
                update_answers(absent, frame, job)

        # ─────────────────────────────────────────────── predict_corpus ──

        def test_every_subreddit_is_labelled_and_persisted(
            self,
            bootstrapped_config: Config,
            write_corpus: Callable[..., Any],
            write_answers: Callable[..., Path],
            submissions_job: CorpusJob,
            fake_tokenizer: FakeTokenizer,
            recording_log: RecordingLog,
        ) -> None:
            write_corpus(rows=4)
            write_answers(rows=4)
            model = FakeModel(predictions=[0, 1, 2])

            predict_corpus(model, fake_tokenizer, bootstrapped_config, submissions_job, log=recording_log)

            labelled = read_csv(bootstrapped_config.paths.labels_dir / submissions_job.output_filename)
            assert len(labelled) == 4 * len(SUBREDDITS)
            assert set(labelled["tiny_label"]) <= {"down", "neutral", "up"}
            assert "12 texts successfully labelled" in recording_log.text_at("success")

        def test_the_dump_file_is_truncated_before_the_run(
            self,
            bootstrapped_config: Config,
            write_corpus: Callable[..., Any],
            write_answers: Callable[..., Path],
            submissions_job: CorpusJob,
            fake_tokenizer: FakeTokenizer,
            recording_log: RecordingLog,
        ) -> None:
            """A previous run's records would duplicate join keys in the merge."""
            submissions_job.dump_file.write_text('{"stale": true}\n', encoding="utf-8")
            write_corpus(rows=2)
            write_answers(rows=2)

            predict_corpus(FakeModel(), fake_tokenizer, bootstrapped_config, submissions_job, log=recording_log)

            records = [json.loads(line) for line in submissions_job.dump_file.read_text(encoding="utf-8").splitlines()]
            assert len(records) == 2 * len(SUBREDDITS)
            assert all("stale" not in record for record in records)

        def test_rows_without_text_are_skipped(
            self,
            bootstrapped_config: Config,
            write_corpus: Callable[..., Any],
            write_answers: Callable[..., Path],
            submissions_job: CorpusJob,
            fake_tokenizer: FakeTokenizer,
            recording_log: RecordingLog,
        ) -> None:
            write_corpus(rows=2, blank_rows=3)
            write_answers(rows=2)

            predict_corpus(FakeModel(), fake_tokenizer, bootstrapped_config, submissions_job, log=recording_log)

            assert f"{2 * len(SUBREDDITS)} texts successfully labelled" in recording_log.text_at("success")

        def test_keeping_the_text_column_is_governed_by_the_job(
            self,
            bootstrapped_config: Config,
            write_corpus: Callable[..., Any],
            write_answers: Callable[..., Path],
            submissions_job: CorpusJob,
            fake_tokenizer: FakeTokenizer,
            recording_log: RecordingLog,
        ) -> None:
            write_corpus(rows=2)
            write_answers(rows=2)
            job = dataclasses.replace(submissions_job, keep_text=False)

            predict_corpus(FakeModel(), fake_tokenizer, bootstrapped_config, job, log=recording_log)

            labelled = read_csv(bootstrapped_config.paths.labels_dir / job.output_filename)
            assert "title_sub" not in labelled.columns
            assert set(labelled.columns) == {"created_utc", "id_sub", "tiny_label", "tiny_trend"}

        def test_the_labelled_output_carries_the_join_keys_and_the_text(
            self,
            bootstrapped_config: Config,
            write_corpus: Callable[..., Any],
            write_answers: Callable[..., Path],
            submissions_job: CorpusJob,
            fake_tokenizer: FakeTokenizer,
            recording_log: RecordingLog,
        ) -> None:
            write_corpus(rows=2)
            write_answers(rows=2)

            predict_corpus(FakeModel(), fake_tokenizer, bootstrapped_config, submissions_job, log=recording_log)

            labelled = read_csv(bootstrapped_config.paths.labels_dir / submissions_job.output_filename)
            assert set(labelled.columns) == {"created_utc", "id_sub", "title_sub", "tiny_label", "tiny_trend"}
            assert "ignored_column" not in labelled.columns

        def test_the_answers_file_is_updated_from_the_labelled_output(
            self,
            bootstrapped_config: Config,
            write_corpus: Callable[..., Any],
            write_answers: Callable[..., Path],
            submissions_job: CorpusJob,
            fake_tokenizer: FakeTokenizer,
            recording_log: RecordingLog,
        ) -> None:
            write_corpus(rows=2)
            answers = write_answers(rows=2)

            predict_corpus(FakeModel(), fake_tokenizer, bootstrapped_config, submissions_job, log=recording_log)

            assert "tiny_label" in read_csv(answers).columns
            assert "Answers successfully updated" in recording_log.text_at("success")

        def test_a_failing_batch_does_not_abort_the_remaining_work(
            self,
            bootstrapped_config: Config,
            write_corpus: Callable[..., Any],
            write_answers: Callable[..., Path],
            submissions_job: CorpusJob,
            fake_tokenizer: FakeTokenizer,
            recording_log: RecordingLog,
        ) -> None:
            write_corpus(rows=4)
            write_answers(rows=4)
            model = FakeModel(failing_batches=[0])  # batch_size is 2, so one batch of r/economy

            predict_corpus(model, fake_tokenizer, bootstrapped_config, submissions_job, log=recording_log)

            assert "simulated CUDA failure" in recording_log.text_at("critical")
            assert f"{4 * len(SUBREDDITS) - 2} texts successfully labelled" in recording_log.text_at("success")

        def test_the_labelled_output_survives_a_broken_answers_file(
            self,
            bootstrapped_config: Config,
            write_corpus: Callable[..., Any],
            submissions_job: CorpusJob,
            fake_tokenizer: FakeTokenizer,
            recording_log: RecordingLog,
        ) -> None:
            """Hours of inference must not be discarded by answers bookkeeping."""
            write_corpus(rows=2)  # no answers file written

            predict_corpus(FakeModel(), fake_tokenizer, bootstrapped_config, submissions_job, log=recording_log)

            assert (bootstrapped_config.paths.labels_dir / submissions_job.output_filename).is_file()
            assert "Could not update the answers file" in recording_log.text_at("error")

        def test_a_missing_corpus_file_is_logged_and_skipped(
            self,
            bootstrapped_config: Config,
            write_corpus: Callable[..., Any],
            submissions_job: CorpusJob,
            fake_tokenizer: FakeTokenizer,
            recording_log: RecordingLog,
        ) -> None:
            """One absent subreddit CSV must not abort the remaining subreddits."""
            write_corpus(rows=2)
            missing = bootstrapped_config.paths.reddit_dir / submissions_job.csv_format.format(SUBREDDITS[0])
            missing.unlink()

            predict_corpus(FakeModel(), fake_tokenizer, bootstrapped_config, submissions_job, log=recording_log)

            assert f"Could not load the r/{SUBREDDITS[0]} corpus" in recording_log.text_at("error")
            labelled = read_csv(bootstrapped_config.paths.labels_dir / submissions_job.output_filename)
            assert sorted(labelled["id_sub"]) == sorted(f"{sub}_{i}" for sub in SUBREDDITS[1:] for i in range(2))

        def test_an_accelerate_dispatched_model_is_not_moved(
            self,
            bootstrapped_config: Config,
            write_corpus: Callable[..., Any],
            write_answers: Callable[..., Path],
            submissions_job: CorpusJob,
            fake_tokenizer: FakeTokenizer,
            recording_log: RecordingLog,
        ) -> None:
            """``device_map="auto"`` models are already placed; ``.to()`` is wrong for them."""
            write_corpus(rows=2)
            write_answers(rows=2)
            model = FakeModel(hf_device_map={"": 0})

            predict_corpus(model, fake_tokenizer, bootstrapped_config, submissions_job, log=recording_log)

            assert model.moved_to == []
            assert model.half_calls == 0
            assert model.eval_calls == 1

        def test_an_unplaced_model_is_moved_and_halved_when_the_job_asks(
            self,
            bootstrapped_config: Config,
            write_corpus: Callable[..., Any],
            write_answers: Callable[..., Path],
            submissions_job: CorpusJob,
            fake_tokenizer: FakeTokenizer,
            recording_log: RecordingLog,
        ) -> None:
            write_corpus(rows=2)
            write_answers(rows=2)
            model = FakeModel()
            job = dataclasses.replace(submissions_job, half=True)

            predict_corpus(model, fake_tokenizer, bootstrapped_config, job, log=recording_log)

            assert model.moved_to == ["cuda" if torch.cuda.is_available() else "cpu"]
            assert model.half_calls == 1
            assert model.eval_calls == 1

        def test_half_precision_is_skipped_when_the_job_does_not_ask(
            self,
            bootstrapped_config: Config,
            write_corpus: Callable[..., Any],
            write_answers: Callable[..., Path],
            submissions_job: CorpusJob,
            fake_tokenizer: FakeTokenizer,
            recording_log: RecordingLog,
        ) -> None:
            write_corpus(rows=2)
            write_answers(rows=2)
            model = FakeModel()

            predict_corpus(model, fake_tokenizer, bootstrapped_config, submissions_job, log=recording_log)

            assert model.half_calls == 0

        def test_batches_respect_the_configured_size(
            self,
            bootstrapped_config: Config,
            write_corpus: Callable[..., Any],
            write_answers: Callable[..., Path],
            submissions_job: CorpusJob,
            fake_tokenizer: FakeTokenizer,
            recording_log: RecordingLog,
        ) -> None:
            write_corpus(rows=5)
            write_answers(rows=5)
            model = FakeModel()

            predict_corpus(model, fake_tokenizer, bootstrapped_config, submissions_job, log=recording_log)

            # batch_size 2 over 5 rows per subreddit: 2 + 2 + 1
            assert model.batch_sizes == [2, 2, 1] * len(SUBREDDITS)

        def test_progress_is_reported_per_subreddit(
            self,
            bootstrapped_config: Config,
            write_corpus: Callable[..., Any],
            write_answers: Callable[..., Path],
            submissions_job: CorpusJob,
            fake_tokenizer: FakeTokenizer,
            recording_log: RecordingLog,
        ) -> None:
            write_corpus(rows=2)
            write_answers(rows=2)

            predict_corpus(FakeModel(), fake_tokenizer, bootstrapped_config, submissions_job, log=recording_log)

            info_lines = recording_log.text_at("info")
            for subreddit in SUBREDDITS:
                assert f"r/{subreddit}: 2 texts to be labelled" in info_lines

    @pytest.mark.contracts
    class TestContracts:
        @settings(deadline=None, max_examples=25, suppress_health_check=[HealthCheck.function_scoped_fixture])
        @given(rows=st.integers(min_value=1, max_value=6))
        def test_merging_the_same_labels_twice_changes_nothing(
            self, rows: int, write_answers: Callable[..., Path], submissions_job: CorpusJob
        ) -> None:
            """Idempotence: the drop-then-merge cycle is stable under repetition."""
            answers = write_answers(rows=rows)
            frame = labelled_frame(rows=rows)

            update_answers(answers, frame, submissions_job)
            once = answers.read_bytes()
            update_answers(answers, frame, submissions_job)

            assert answers.read_bytes() == once

        @settings(deadline=None, max_examples=25, suppress_health_check=[HealthCheck.function_scoped_fixture])
        @given(permutation=st.permutations(range(6)))
        def test_the_merge_is_independent_of_the_labelled_row_order(
            self, permutation: list[int], write_answers: Callable[..., Path], submissions_job: CorpusJob
        ) -> None:
            answers = write_answers(rows=2)
            frame = labelled_frame(rows=2)

            update_answers(answers, frame, submissions_job)
            ordered = read_csv(answers)

            answers.unlink()
            write_answers(rows=2)
            update_answers(answers, frame.iloc[list(permutation)].reset_index(drop=True), submissions_job)

            assert read_csv(answers).equals(ordered)

        @settings(deadline=None, max_examples=25, suppress_health_check=[HealthCheck.function_scoped_fixture])
        @given(predictions=st.lists(st.integers(min_value=0, max_value=2), min_size=1, max_size=8))
        def test_decoded_trends_always_agree_with_the_decoded_labels(
            self,
            predictions: list[int],
            fake_tokenizer: FakeTokenizer,
            labels_config: LabelsConfig,
            submissions_job: CorpusJob,
        ) -> None:
            results = process_batch(
                texts=["t"] * len(predictions),
                model=FakeModel(predictions=predictions),
                tokenizer=fake_tokenizer,
                labels=labels_config,
                device="cpu",
                job=submissions_job,
            )

            for result in results:
                assert labels_config.encodings[result["tiny_label"]] == result["tiny_trend"]

        @settings(deadline=None, max_examples=25, suppress_health_check=[HealthCheck.function_scoped_fixture])
        @given(texts=st.lists(st.text(max_size=20), min_size=0, max_size=10))
        def test_every_text_receives_exactly_one_decision(
            self,
            texts: list[str],
            fake_tokenizer: FakeTokenizer,
            labels_config: LabelsConfig,
            submissions_job: CorpusJob,
        ) -> None:
            results = process_batch(
                texts=texts,
                model=FakeModel(predictions=[0, 1, 2]),
                tokenizer=fake_tokenizer,
                labels=labels_config,
                device="cpu",
                job=submissions_job,
            )

            assert len(results) == len(texts)
            assert all(r["tiny_label"] in labels_config.labels for r in results)
