"""Contract tests for :mod:`reddit.training.selection`.

Median-seed selection is destructive (it deletes the losing checkpoints), so
every checkpoint here lives under ``tmp_path``.
"""

from __future__ import annotations

import json
import statistics
from collections.abc import Callable
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from reddit.core.config import Config
from reddit.training.loop import SeedResult
from reddit.training.selection import select_median

from ..conftest import RecordingLog

SeedResults = Callable[..., dict[int, SeedResult]]


class TestSelectionModule:
    """Upper-median seed selection, loser cleanup and the selection record."""

    @pytest.mark.unit
    class TestUnits:
        def test_the_upper_median_seed_is_selected(
            self, bootstrapped_config: Config, seed_results: SeedResults, recording_log: RecordingLog
        ) -> None:
            results = seed_results([0.10, 0.50, 0.30])

            model_path, model_config = select_median(bootstrapped_config, "tiny_llm", results, "qdora", recording_log)

            assert model_path == results[102].model  # 0.30 is the median of {0.10, 0.30, 0.50}
            assert model_config == "config-of-102"

        def test_an_even_number_of_seeds_takes_the_upper_middle(
            self, bootstrapped_config: Config, seed_results: SeedResults, recording_log: RecordingLog
        ) -> None:
            """Preserves the previous ``statistics.median_high`` semantics."""
            results = seed_results([0.10, 0.20, 0.30, 0.40])

            model_path, _ = select_median(bootstrapped_config, "tiny_llm", results, "qdora", recording_log)

            assert model_path == results[102].model  # 0.30, not 0.20

        def test_a_single_seed_is_its_own_median(
            self, bootstrapped_config: Config, seed_results: SeedResults, recording_log: RecordingLog
        ) -> None:
            results = seed_results([0.42])

            model_path, _ = select_median(bootstrapped_config, "tiny_llm", results, "qdora", recording_log)

            assert model_path == results[100].model

        def test_no_training_results_produce_no_selection(
            self, bootstrapped_config: Config, recording_log: RecordingLog
        ) -> None:
            assert select_median(bootstrapped_config, "tiny_llm", {}, "qdora", recording_log) == (None, None)
            assert "No model was trained" in recording_log.text_at("warning")

        def test_only_unusable_scores_produce_no_selection(
            self, bootstrapped_config: Config, seed_results: SeedResults, recording_log: RecordingLog
        ) -> None:
            """A NaN performance used to make ``median_high`` match no seed at all."""
            results = seed_results([float("nan"), float("nan")])

            assert select_median(bootstrapped_config, "tiny_llm", results, "qdora", recording_log) == (None, None)
            assert "No seed produced a usable score" in recording_log.text_at("warning")

        def test_non_finite_scores_are_excluded_from_the_ranking(
            self, bootstrapped_config: Config, seed_results: SeedResults, recording_log: RecordingLog
        ) -> None:
            results = seed_results([float("nan"), 0.20, float("inf"), 0.60, float("-inf")])

            model_path, _ = select_median(bootstrapped_config, "tiny_llm", results, "qdora", recording_log)

            assert model_path == results[103].model  # upper median of the finite {0.20, 0.60}

        def test_the_selected_score_is_reported_to_the_progress_log(
            self, bootstrapped_config: Config, seed_results: SeedResults, recording_log: RecordingLog
        ) -> None:
            results = seed_results([0.10, 0.50, 0.30])

            select_median(bootstrapped_config, "tiny_llm", results, "qdora", recording_log)

            assert "Selected model: `tiny_llm_qdora_102` [0.300000]" in recording_log.text_at("info")

        def test_a_vanished_checkpoint_yields_no_selection(
            self, bootstrapped_config: Config, seed_results: SeedResults, recording_log: RecordingLog
        ) -> None:
            results = seed_results([0.10, 0.50, 0.30], materialise=False)

            assert select_median(bootstrapped_config, "tiny_llm", results, "qdora", recording_log) == (None, None)
            assert "No model available" in recording_log.text_at("warning")

        def test_selection_is_deterministic_under_ties(
            self, bootstrapped_config: Config, seed_results: SeedResults, recording_log: RecordingLog
        ) -> None:
            first = select_median(
                bootstrapped_config, "tiny_llm", seed_results([0.5, 0.5, 0.5]), "qdora", recording_log
            )
            second = select_median(
                bootstrapped_config, "tiny_llm", seed_results([0.5, 0.5, 0.5]), "qdora", recording_log
            )

            assert first[0] == second[0]

    @pytest.mark.integration
    class TestIntegration:
        def test_the_losing_checkpoints_are_deleted(
            self, bootstrapped_config: Config, seed_results: SeedResults, recording_log: RecordingLog
        ) -> None:
            results = seed_results([0.10, 0.50, 0.30])

            select_median(bootstrapped_config, "tiny_llm", results, "qdora", recording_log)

            assert results[102].model.is_dir()
            assert not results[100].model.exists()
            assert not results[101].model.exists()

        def test_unusable_seeds_lose_their_checkpoints_too(
            self, bootstrapped_config: Config, seed_results: SeedResults, recording_log: RecordingLog
        ) -> None:
            results = seed_results([float("nan"), 0.20, 0.60])

            select_median(bootstrapped_config, "tiny_llm", results, "qdora", recording_log)

            assert not results[100].model.exists()
            assert results[102].model.is_dir()

        def test_no_checkpoint_is_deleted_when_nothing_is_usable(
            self, bootstrapped_config: Config, seed_results: SeedResults, recording_log: RecordingLog
        ) -> None:
            """No ranking happened, so nothing was deleted either."""
            results = seed_results([float("nan"), float("nan")])

            select_median(bootstrapped_config, "tiny_llm", results, "qdora", recording_log)

            assert all(result.model.is_dir() for result in results.values())

        def test_the_selection_is_recorded_as_jsonl(
            self,
            bootstrapped_config: Config,
            seed_results: SeedResults,
            selection_dump: Path,
            recording_log: RecordingLog,
        ) -> None:
            results = seed_results([0.10, 0.50, 0.30])

            select_median(bootstrapped_config, "tiny_llm", results, "qdora", recording_log)

            record = json.loads(selection_dump.read_text(encoding="utf-8"))
            assert record["model"] == "tiny_llm"
            assert record["method"] == "qdora"
            assert record["seed"] == 102
            assert record["metrics"] == pytest.approx(0.30)
            assert record["date"] == bootstrapped_config.system.date
            assert "inserted" in record

        def test_full_finetuning_records_a_dash_as_the_method(
            self,
            bootstrapped_config: Config,
            seed_results: SeedResults,
            selection_dump: Path,
            recording_log: RecordingLog,
        ) -> None:
            select_median(bootstrapped_config, "finbert", seed_results([0.4], method="-"), "-", recording_log)

            assert json.loads(selection_dump.read_text(encoding="utf-8"))["method"] == "-"

        def test_successive_selections_append_rather_than_overwrite(
            self,
            bootstrapped_config: Config,
            seed_results: SeedResults,
            selection_dump: Path,
            recording_log: RecordingLog,
        ) -> None:
            select_median(bootstrapped_config, "tiny_llm", seed_results([0.4]), "qdora", recording_log)
            select_median(
                bootstrapped_config, "tiny_llm", seed_results([0.8], method="xqdora"), "xqdora", recording_log
            )

            lines = selection_dump.read_text(encoding="utf-8").splitlines()
            assert [json.loads(line)["method"] for line in lines] == ["qdora", "xqdora"]

        def test_nothing_is_recorded_when_no_seed_is_selected(
            self, bootstrapped_config: Config, selection_dump: Path, recording_log: RecordingLog
        ) -> None:
            select_median(bootstrapped_config, "tiny_llm", {}, "qdora", recording_log)

            assert not selection_dump.exists()

        def test_an_existing_record_file_is_reused(
            self,
            bootstrapped_config: Config,
            seed_results: SeedResults,
            selection_dump: Path,
            recording_log: RecordingLog,
        ) -> None:
            selection_dump.write_text('{"legacy": true}\n', encoding="utf-8")

            select_median(bootstrapped_config, "tiny_llm", seed_results([0.4]), "qdora", recording_log)

            lines = selection_dump.read_text(encoding="utf-8").splitlines()
            assert json.loads(lines[0]) == {"legacy": True}
            assert json.loads(lines[1])["model"] == "tiny_llm"

    @pytest.mark.contracts
    class TestContracts:
        @settings(deadline=None, max_examples=30, suppress_health_check=[HealthCheck.function_scoped_fixture])
        @given(
            performances=st.lists(
                st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False),
                min_size=1,
                max_size=8,
                unique=True,
            )
        )
        def test_the_selected_score_is_always_the_high_median(
            self,
            performances: list[float],
            bootstrapped_config: Config,
            seed_results: SeedResults,
            recording_log: RecordingLog,
        ) -> None:
            results = seed_results(performances)

            model_path, _ = select_median(bootstrapped_config, "tiny_llm", results, "qdora", recording_log)

            selected = next(r for r in results.values() if r.model == model_path)
            assert selected.performance == statistics.median_high(performances)

        @settings(deadline=None, max_examples=30, suppress_health_check=[HealthCheck.function_scoped_fixture])
        @given(
            performances=st.lists(
                st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False),
                min_size=1,
                max_size=6,
                unique=True,
            )
        )
        def test_exactly_one_checkpoint_survives(
            self,
            performances: list[float],
            bootstrapped_config: Config,
            seed_results: SeedResults,
            recording_log: RecordingLog,
        ) -> None:
            results = seed_results(performances)

            select_median(bootstrapped_config, "tiny_llm", results, "qdora", recording_log)

            assert sum(1 for result in results.values() if result.model.exists()) == 1

        @settings(deadline=None, max_examples=30, suppress_health_check=[HealthCheck.function_scoped_fixture])
        @given(
            finite=st.lists(
                st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False),
                min_size=1,
                max_size=5,
                unique=True,
            ),
            unusable=st.lists(st.sampled_from([float("nan"), float("inf"), float("-inf")]), min_size=0, max_size=3),
        )
        def test_unusable_scores_never_influence_the_choice(
            self,
            finite: list[float],
            unusable: list[float],
            bootstrapped_config: Config,
            seed_results: SeedResults,
            recording_log: RecordingLog,
        ) -> None:
            results = seed_results([*finite, *unusable])

            model_path, _ = select_median(bootstrapped_config, "tiny_llm", results, "qdora", recording_log)

            selected = next(r for r in results.values() if r.model == model_path)
            assert selected.performance == statistics.median_high(finite)
