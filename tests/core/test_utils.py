"""Contract tests for :mod:`reddit.core.utils`.

``clear_hf_cache`` and ``archive_model`` are destructive; every test here
points them exclusively at ``tmp_path``.
"""

from __future__ import annotations

import datetime
import json
import logging
import zipfile
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from reddit.core.utils import archive_model, clear_hf_cache, dump_object, fmt_td, now

JSONABLE = st.recursive(
    st.none() | st.booleans() | st.integers(min_value=-(2**53), max_value=2**53) | st.text(max_size=20),
    lambda children: st.lists(children, max_size=4) | st.dictionaries(st.text(max_size=8), children, max_size=4),
    max_leaves=8,
)


class TestUtilsModule:
    """Elapsed-time formatting, timestamps, JSONL dumping, archiving, cache hygiene."""

    @pytest.fixture
    def hub_cache(self, tmp_path: Path) -> Path:
        """An ``HF_HOME``-style cache root holding one downloaded model folder."""
        root = tmp_path / "hf_home"
        (root / "hub" / "models--Qwen--Qwen2.5-0.5B").mkdir(parents=True)
        (root / "hub" / "models--Qwen--Qwen2.5-0.5B" / "weights.bin").write_bytes(b"x")
        return root

    @pytest.mark.unit
    class TestUnits:
        # ────────────────────────────────────────────────────────── fmt_td ──

        def test_zero_elapsed_time_renders_all_components(self) -> None:
            assert fmt_td(0) == "0 hour(s), 0 minute(s) and 0.000000 seconds"

        @pytest.mark.parametrize(
            ("nanoseconds", "expected"),
            [
                (1_000, "0 hour(s), 0 minute(s) and 0.000001 seconds"),
                (1_500_000, "0 hour(s), 0 minute(s) and 0.001500 seconds"),
                (1_000_000_000, "0 hour(s), 0 minute(s) and 1.000000 seconds"),
                (61_000_000_000, "0 hour(s), 1 minute(s) and 1.000000 seconds"),
                (3_661_000_000_000, "1 hour(s), 1 minute(s) and 1.000000 seconds"),
            ],
        )
        def test_durations_decompose_into_hours_minutes_and_seconds(self, nanoseconds: int, expected: str) -> None:
            assert fmt_td(nanoseconds) == expected

        @pytest.mark.parametrize("hours", [24, 25, 49, 100])
        def test_durations_beyond_a_day_do_not_wrap(self, hours: int) -> None:
            """Reinterpreting the delta as an absolute timestamp used to wrap at 24h."""
            assert fmt_td(hours * 3_600_000_000_000).startswith(f"{hours} hour(s), 0 minute(s)")

        def test_sub_microsecond_precision_is_truncated_not_rounded(self) -> None:
            assert fmt_td(1_999) == "0 hour(s), 0 minute(s) and 0.000001 seconds"

        def test_microseconds_are_always_six_digits(self) -> None:
            assert fmt_td(1_000_000_000 + 1_000).endswith("1.000001 seconds")

        # ───────────────────────────────────────────────────────────── now ──

        def test_now_is_an_iso_utc_instant_with_microsecond_precision(self) -> None:
            stamp = now()

            parsed = datetime.datetime.fromisoformat(stamp)
            assert parsed.tzinfo is not None
            assert parsed.utcoffset() == datetime.timedelta(0)
            assert stamp.endswith("+00:00")

        def test_now_is_non_decreasing(self) -> None:
            first, second = now(), now()

            assert first <= second

        # ───────────────────────────────────────────────────── dump_object ──

        def test_dump_object_produces_one_newline_terminated_json_line(self) -> None:
            payload: dict[str, object] = {"model": "gemma2_9b", "seed": 7, "metrics": 0.5}

            line = dump_object(payload)

            assert isinstance(line, bytes)
            assert line.endswith(b"\n")
            assert line.count(b"\n") == 1
            assert json.loads(line) == payload

        def test_dump_object_preserves_key_insertion_order(self) -> None:
            line = dump_object({"b": 1, "a": 2, "c": 3})

            assert list(json.loads(line)) == ["b", "a", "c"]

        def test_dump_object_serializes_an_empty_record(self) -> None:
            assert dump_object({}) == b"{}\n"

        def test_dump_object_rejects_unserializable_values_with_a_type_error(self) -> None:
            with pytest.raises(TypeError, match="Failed to encode to JSON"):
                dump_object({"bad": object()})

        def test_dump_object_error_keeps_the_original_cause(self) -> None:
            with pytest.raises(TypeError) as excinfo:
                dump_object({"bad": {1, 2, 3}})

            assert excinfo.value.__cause__ is not None

    @pytest.mark.integration
    class TestIntegration:
        # ─────────────────────────────────────────────────── archive_model ──

        def test_archiving_replaces_the_directory_with_a_zip(self, tmp_path: Path) -> None:
            model_dir = tmp_path / "gemma2_9b_qdora_42"
            model_dir.mkdir()
            (model_dir / "adapter.bin").write_bytes(b"weights")

            archive_model(model_dir, str(model_dir))

            archive = tmp_path / "gemma2_9b_qdora_42.zip"
            assert archive.is_file()
            assert not model_dir.exists()
            with zipfile.ZipFile(archive) as zf:
                assert zf.namelist() == ["adapter.bin"]

        def test_archiving_accepts_a_string_path(self, tmp_path: Path) -> None:
            model_dir = tmp_path / "checkpoint"
            model_dir.mkdir()
            (model_dir / "f.txt").write_text("x", encoding="utf-8")

            archive_model(str(model_dir), str(tmp_path / "out"))

            assert (tmp_path / "out.zip").is_file()

        def test_archiving_a_missing_directory_warns_instead_of_raising(
            self, tmp_path: Path, caplog: pytest.LogCaptureFixture
        ) -> None:
            missing = tmp_path / "never_trained"

            with caplog.at_level(logging.WARNING):
                archive_model(missing, str(tmp_path / "out"))

            assert "Could not zip the dumped model" in caplog.text
            assert not (tmp_path / "out.zip").exists()

        # ──────────────────────────────────────────────────── clear_hf_cache ──

        def test_cached_model_folder_is_deleted_from_hf_home(
            self, hub_cache: Path, monkeypatch: pytest.MonkeyPatch
        ) -> None:
            monkeypatch.setenv("HF_HOME", str(hub_cache))

            clear_hf_cache("Qwen/Qwen2.5-0.5B")

            assert not (hub_cache / "hub" / "models--Qwen--Qwen2.5-0.5B").exists()
            assert (hub_cache / "hub").is_dir()

        def test_an_absent_model_is_reported_and_nothing_is_removed(
            self, hub_cache: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
        ) -> None:
            monkeypatch.setenv("HF_HOME", str(hub_cache))

            with caplog.at_level(logging.INFO):
                clear_hf_cache("google/gemma-2-9b")

            assert "not found in cache" in caplog.text
            assert (hub_cache / "hub" / "models--Qwen--Qwen2.5-0.5B").is_dir()

        def test_extra_cache_roots_are_swept_as_well(
            self, hub_cache: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
        ) -> None:
            extra = tmp_path / "shared_cache"
            (extra / "hub" / "models--Qwen--Qwen2.5-0.5B").mkdir(parents=True)
            monkeypatch.setenv("HF_HOME", str(hub_cache))

            clear_hf_cache("Qwen/Qwen2.5-0.5B", extra_cache_dirs=[extra])

            assert not (hub_cache / "hub" / "models--Qwen--Qwen2.5-0.5B").exists()
            assert not (extra / "hub" / "models--Qwen--Qwen2.5-0.5B").exists()

        def test_the_user_default_cache_is_spared_when_hf_home_is_overridden(
            self, hub_cache: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
        ) -> None:
            """Co-tenant downloads under ``~/.cache/huggingface`` must survive."""
            home = tmp_path / "home"
            default_cache = home / ".cache" / "huggingface" / "hub" / "models--Qwen--Qwen2.5-0.5B"
            default_cache.mkdir(parents=True)
            monkeypatch.setenv("HOME", str(home))
            monkeypatch.setenv("HF_HOME", str(hub_cache))

            clear_hf_cache("Qwen/Qwen2.5-0.5B")

            assert default_cache.is_dir()
            assert not (hub_cache / "hub" / "models--Qwen--Qwen2.5-0.5B").exists()

        def test_the_user_default_cache_is_used_when_hf_home_is_unset(
            self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
        ) -> None:
            home = tmp_path / "home"
            default_cache = home / ".cache" / "huggingface" / "hub" / "models--Qwen--Qwen2.5-0.5B"
            default_cache.mkdir(parents=True)
            monkeypatch.delenv("HF_HOME", raising=False)
            monkeypatch.setenv("HOME", str(home))

            clear_hf_cache("Qwen/Qwen2.5-0.5B")

            assert not default_cache.exists()

        def test_the_same_root_listed_twice_is_swept_once(
            self, hub_cache: Path, monkeypatch: pytest.MonkeyPatch
        ) -> None:
            monkeypatch.setenv("HF_HOME", str(hub_cache))

            clear_hf_cache("Qwen/Qwen2.5-0.5B", extra_cache_dirs=[hub_cache])

            assert not (hub_cache / "hub" / "models--Qwen--Qwen2.5-0.5B").exists()

        def test_a_nonexistent_cache_root_is_tolerated(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
            monkeypatch.setenv("HF_HOME", str(tmp_path / "never_created"))

            clear_hf_cache("Qwen/Qwen2.5-0.5B", extra_cache_dirs=[tmp_path / "also_absent"])

    @pytest.mark.contracts
    class TestContracts:
        @given(nanoseconds=st.integers(min_value=0, max_value=10**16))
        def test_formatted_components_add_back_up_to_the_elapsed_microseconds(self, nanoseconds: int) -> None:
            rendered = fmt_td(nanoseconds)

            hours, rest = rendered.split(" hour(s), ")
            minutes, rest = rest.split(" minute(s) and ")
            seconds, microseconds = rest.removesuffix(" seconds").split(".")
            total_us = (
                int(hours) * 3_600_000_000 + int(minutes) * 60_000_000 + int(seconds) * 1_000_000 + int(microseconds)
            )

            assert total_us == nanoseconds // 1_000

        @given(nanoseconds=st.integers(min_value=0, max_value=10**16))
        def test_minutes_and_seconds_never_leave_their_range(self, nanoseconds: int) -> None:
            rendered = fmt_td(nanoseconds)
            _, rest = rendered.split(" hour(s), ")
            minutes, rest = rest.split(" minute(s) and ")
            seconds, microseconds = rest.removesuffix(" seconds").split(".")

            assert 0 <= int(minutes) < 60
            assert 0 <= int(seconds) < 60
            assert len(microseconds) == 6

        @given(
            a=st.integers(min_value=0, max_value=10**15),
            b=st.integers(min_value=0, max_value=10**15),
        )
        def test_longer_durations_never_render_as_fewer_hours(self, a: int, b: int) -> None:
            """Monotonicity: the rendering is order-preserving on whole hours."""
            low, high = min(a, b), max(a, b)

            hours_low = int(fmt_td(low).split(" hour(s)")[0])
            hours_high = int(fmt_td(high).split(" hour(s)")[0])

            assert hours_low <= hours_high

        @settings(max_examples=50)
        @given(payload=st.dictionaries(st.text(max_size=10), JSONABLE, max_size=5))
        def test_dump_object_round_trips_through_json(self, payload: dict[str, object]) -> None:
            assert json.loads(dump_object(payload)) == payload

        @settings(max_examples=50)
        @given(payload=st.dictionaries(st.text(max_size=10), JSONABLE, max_size=5))
        def test_dump_object_is_a_single_jsonl_record(self, payload: dict[str, object]) -> None:
            line = dump_object(payload)

            assert line.endswith(b"\n")
            assert line[:-1].count(b"\n") == 0
