"""Logging setup: console + file handlers and the human-oriented progress line."""

from __future__ import annotations

import datetime
import logging
import sys
from os import getpid
from pathlib import Path
from typing import Literal

from pandas import Timestamp

type LogLevel = Literal["", "debug", "info", "success", "warning", "warn", "error", "critical", "fail", "failure"]


class MicrosecondFormatter(logging.Formatter):
    """Formatter that supports %f (microseconds) in datefmt."""

    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        dt = datetime.datetime.fromtimestamp(record.created)
        if datefmt:
            return dt.strftime(datefmt)
        return dt.strftime("%Y-%m-%d.%H:%M:%S.%f")


def setup_logging(log_level: int = logging.INFO, log_file: str | Path = "run.log") -> None:
    """Configure the root logger to log to the console (stderr) and a file."""
    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG)

    if logger.hasHandlers():
        logger.handlers.clear()

    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setLevel(log_level)
    console_handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d.%H:%M:%S")
    )
    logger.addHandler(console_handler)

    log_path = Path(log_file)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    file_handler = logging.FileHandler(log_path)
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(
        MicrosecondFormatter(fmt="%(processName)s|%(asctime)s [%(levelname)s] %(module)s:%(lineno)d - %(message)s")
    )
    logger.addHandler(file_handler)


def print_log(
    message: str | None = None,
    *,
    level: LogLevel | int | None = 0,
    dump: bool = False,
    filename: Path | str = "logs.txt",
    pid: int | None = None,
    gid: int | None = None,
) -> None:
    """Human-oriented progress line, optionally appended to a per-run log file."""
    now = Timestamp.now(tz="Europe/Rome").strftime("%Y-%m-%d.%H:%M:%S.%f")
    # `pid or getpid()` swallowed an explicit pid=0; only None means "mine".
    pid = getpid() if pid is None else pid
    gpu = f" |{gid}" if gid is not None else ""
    out = f"|{pid:0>8}{gpu}|{now}|"
    if message:
        match level:
            case -1 | "debug":
                out += f"  ___DEBUG___  🐞  {message}"
            case 0 | "":
                out += f"               📄  {message}"
            case 1 | "info":
                out += f"         INFO  👉  {message}"
            case 2 | "success":
                out += f"               ✅  {message}"
            case 3 | "warning" | "warn":
                out += f"     *WARNING  ⚠️  {message}"
            case 4 | "error":
                out += f"      **ERROR  ⛔  {message}"
            case 5 | "failure" | "fail":
                out += f"       FAILED  😰  {message}"
            case 6 | "critical":
                out += f"  ***CRITICAL  😱  {message}"
            case level if level is None:
                out = message
            case _other:
                # Unknown level: fall back to the neutral rendering rather than
                # dropping the message entirely.
                out += f"               📄  {message}"
    print(out)
    if dump and Path(filename).exists():
        with open(filename, "a", encoding="utf-8") as f:
            f.write(out + "\n")
