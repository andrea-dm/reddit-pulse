"""Contract tests for :mod:`reddit.core.logging`.

The root logger is restored after every test by the autouse
``_isolated_root_logger`` fixture in the root ``conftest.py``.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from reddit.core.logging import LogLevel, MicrosecondFormatter, print_log, setup_logging

TIMESTAMP = r"\d{4}-\d{2}-\d{2}\.\d{2}:\d{2}:\d{2}\.\d{6}"
PREFIX = re.compile(rf"^\|\d{{8}}\|{TIMESTAMP}\|")

LEVEL_MARKERS: list[tuple[LogLevel | int, str]] = [
    (-1, "___DEBUG___"),
    ("debug", "___DEBUG___"),
    (0, "📄"),
    ("", "📄"),
    (1, "INFO"),
    ("info", "INFO"),
    (2, "✅"),
    ("success", "✅"),
    (3, "*WARNING"),
    ("warning", "*WARNING"),
    ("warn", "*WARNING"),
    (4, "**ERROR"),
    ("error", "**ERROR"),
    (5, "FAILED"),
    ("failure", "FAILED"),
    ("fail", "FAILED"),
    (6, "***CRITICAL"),
    ("critical", "***CRITICAL"),
]


class TestLoggingModule:
    """The progress line, the root-logger setup and the microsecond formatter."""

    @pytest.fixture
    def log_record(self) -> logging.LogRecord:
        """A minimal record for formatter tests."""
        return logging.LogRecord(
            name="reddit", level=logging.INFO, pathname=__file__, lineno=1, msg="hello", args=None, exc_info=None
        )

    @pytest.mark.unit
    class TestUnits:
        # ─────────────────────────────────────────────────────── print_log ──

        def test_progress_line_carries_pid_and_timestamp(self, capsys: pytest.CaptureFixture[str]) -> None:
            print_log("training started", pid=1234)

            out = capsys.readouterr().out.strip()
            assert out.startswith("|00001234|")
            assert PREFIX.match(out)
            assert out.endswith("training started")

        @pytest.mark.parametrize(("level", "marker"), LEVEL_MARKERS)
        def test_every_documented_level_renders_its_marker(
            self, level: LogLevel | int, marker: str, capsys: pytest.CaptureFixture[str]
        ) -> None:
            print_log("payload", level=level, pid=1)

            out = capsys.readouterr().out
            assert marker in out
            assert "payload" in out

        def test_level_none_prints_the_bare_message(self, capsys: pytest.CaptureFixture[str]) -> None:
            print_log("raw banner", level=None)

            assert capsys.readouterr().out == "raw banner\n"

        @pytest.mark.parametrize("level", [99, -7, "shout"])
        def test_an_unknown_level_falls_back_to_the_neutral_rendering(
            self, level: LogLevel | int, capsys: pytest.CaptureFixture[str]
        ) -> None:
            """An unrecognised level must never drop the message."""
            print_log("still visible", level=level, pid=1)

            out = capsys.readouterr().out
            assert "📄" in out
            assert "still visible" in out

        def test_a_missing_message_prints_only_the_prefix(self, capsys: pytest.CaptureFixture[str]) -> None:
            print_log(pid=42)

            out = capsys.readouterr().out.strip()
            assert PREFIX.match(out)
            assert out.endswith("|")

        def test_a_gpu_id_is_shown_next_to_the_pid(self, capsys: pytest.CaptureFixture[str]) -> None:
            print_log("on device", gid=3, pid=7)

            assert capsys.readouterr().out.startswith("|00000007 |3|")

        def test_gpu_id_zero_is_still_displayed(self, capsys: pytest.CaptureFixture[str]) -> None:
            print_log("on device", gid=0, pid=7)

            assert capsys.readouterr().out.startswith("|00000007 |0|")

        def test_the_current_process_id_is_used_by_default(self, capsys: pytest.CaptureFixture[str]) -> None:
            print_log("default pid")

            assert capsys.readouterr().out.startswith(f"|{os.getpid():0>8}|")

        # ────────────────────────────────────────── MicrosecondFormatter ──

        def test_formatter_supports_microseconds_in_the_date_format(self, log_record: logging.LogRecord) -> None:
            formatter = MicrosecondFormatter()

            rendered = formatter.formatTime(log_record, "%Y-%m-%d.%H:%M:%S.%f")

            assert re.fullmatch(TIMESTAMP, rendered)

        def test_formatter_default_date_format_includes_microseconds(self, log_record: logging.LogRecord) -> None:
            formatter = MicrosecondFormatter()

            assert re.fullmatch(TIMESTAMP, formatter.formatTime(log_record))

        def test_formatter_renders_the_full_record_layout(self, log_record: logging.LogRecord) -> None:
            formatter = MicrosecondFormatter(fmt="%(asctime)s [%(levelname)s] %(message)s")

            rendered = formatter.format(log_record)

            assert rendered.endswith("[INFO] hello")

    @pytest.mark.integration
    class TestIntegration:
        def test_dumping_appends_to_an_existing_log_file(
            self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
        ) -> None:
            log_file = tmp_path / "run.log"
            log_file.write_text("previous\n", encoding="utf-8")

            print_log("first", dump=True, filename=log_file, pid=1)
            print_log("second", dump=True, filename=log_file, pid=1)

            lines = log_file.read_text(encoding="utf-8").splitlines()
            assert lines[0] == "previous"
            assert lines[1].endswith("first")
            assert lines[2].endswith("second")
            assert "first" in capsys.readouterr().out

        def test_dumping_to_a_missing_file_is_silently_skipped(
            self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
        ) -> None:
            """The file is created by the caller (``log_file.touch()``), never here."""
            log_file = tmp_path / "absent.log"

            print_log("orphan", dump=True, filename=log_file, pid=1)

            assert not log_file.exists()
            assert "orphan" in capsys.readouterr().out

        def test_dump_disabled_never_writes(self, tmp_path: Path) -> None:
            log_file = tmp_path / "run.log"
            log_file.touch()

            print_log("not dumped", dump=False, filename=log_file, pid=1)

            assert log_file.read_text(encoding="utf-8") == ""

        def test_setup_logging_creates_the_log_file_and_its_parents(self, tmp_path: Path) -> None:
            log_file = tmp_path / "nested" / "deeper" / "run.log"

            setup_logging(log_file=log_file)

            assert log_file.is_file()

        def test_records_reach_the_log_file(self, tmp_path: Path) -> None:
            log_file = tmp_path / "run.log"
            setup_logging(log_file=log_file)

            logging.info("corpus labelled")

            content = log_file.read_text(encoding="utf-8")
            assert "corpus labelled" in content
            assert "[INFO]" in content

        def test_debug_records_reach_the_file_but_not_the_console(
            self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
        ) -> None:
            log_file = tmp_path / "run.log"
            setup_logging(log_level=logging.WARNING, log_file=log_file)

            logging.debug("verbose detail")
            logging.warning("something odd")

            captured = capsys.readouterr().err
            assert "verbose detail" not in captured
            assert "something odd" in captured
            assert "verbose detail" in log_file.read_text(encoding="utf-8")

        def test_repeated_setup_does_not_duplicate_records(self, tmp_path: Path) -> None:
            log_file = tmp_path / "run.log"
            setup_logging(log_file=log_file)
            setup_logging(log_file=log_file)

            logging.info("only once")

            assert log_file.read_text(encoding="utf-8").count("only once") == 1

        def test_setup_accepts_a_string_path(self, tmp_path: Path) -> None:
            setup_logging(log_file=str(tmp_path / "as_string.log"))

            logging.info("written")

            assert "written" in (tmp_path / "as_string.log").read_text(encoding="utf-8")

        def test_file_records_carry_module_and_line_metadata(self, tmp_path: Path) -> None:
            log_file = tmp_path / "run.log"
            setup_logging(log_file=log_file)

            logging.error("boom")

            line = log_file.read_text(encoding="utf-8").strip().splitlines()[-1]
            assert re.search(r"\|" + TIMESTAMP + r" \[ERROR\] test_logging:\d+ - boom$", line)

    @pytest.mark.contracts
    class TestContracts:
        @settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
        @given(
            message=st.text(min_size=1, max_size=40).filter(lambda s: s.strip() and "\n" not in s and "\r" not in s),
            level=st.sampled_from([lvl for lvl, _ in LEVEL_MARKERS]),
        )
        def test_any_message_survives_any_documented_level(
            self, message: str, level: LogLevel | int, capsys: pytest.CaptureFixture[str]
        ) -> None:
            print_log(message, level=level, pid=1)

            out = capsys.readouterr().out
            assert message in out
            assert PREFIX.match(out)

        @settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
        @given(pid=st.integers(min_value=1, max_value=10**9))
        def test_the_pid_field_is_zero_padded_to_at_least_eight_characters(
            self, pid: int, capsys: pytest.CaptureFixture[str]
        ) -> None:
            print_log("x", pid=pid)

            field = capsys.readouterr().out.split("|")[1]
            assert len(field) >= 8
            assert field.lstrip("0") == str(pid).lstrip("0")

        @settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
        @given(pid=st.integers(min_value=1, max_value=10**9), gid=st.integers(min_value=0, max_value=15))
        def test_the_gpu_field_never_collapses_into_the_pid_field(
            self, pid: int, gid: int, capsys: pytest.CaptureFixture[str]
        ) -> None:
            print_log("x", pid=pid, gid=gid)

            fields = capsys.readouterr().out.split("|")
            assert fields[1].endswith(" ")
            assert fields[2] == str(gid)

        def test_the_default_pid_is_the_current_process(self, capsys: pytest.CaptureFixture[str]) -> None:
            print_log("x")

            assert capsys.readouterr().out.startswith(f"|{os.getpid():0>8}|")

        def test_an_explicit_zero_pid_is_respected(self, capsys: pytest.CaptureFixture[str]) -> None:
            """Only ``pid=None`` means "mine"; ``0`` is a caller's value, not a gap."""
            print_log("x", pid=0)

            assert capsys.readouterr().out.startswith("|00000000|")
