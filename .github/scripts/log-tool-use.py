#!/usr/bin/env python3
# pyright: basic
"""
PreToolUse hook logger for VS Code / GitHub Copilot.

Design goals:
- Read hook payload JSON from stdin.
- Always return {"continue": true} on stdout.
- Never write anything except the hook response to stdout.
- Write detailed debug logs and structured audit logs.
- Work on Windows without PowerShell quirks.
- Use orjson for parsing/serialization, with a safe fallback to standard json.
"""

from __future__ import annotations

import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

# 1. Graceful Fallback Import
try:
    import orjson
    HAS_ORJSON = True
except ImportError:
    import json
    HAS_ORJSON = False

CONTINUE_RESPONSE = b'{"continue":true}\n'
# 2. Aggressive Lock Timings (to beat VS Code's 5s kill switch)
LOCK_TIMEOUT_SECONDS = 2.0
LOCK_STALE_SECONDS = 3.0
LOCK_POLL_INTERVAL_SECONDS = 0.05


def serialize_json(record: Mapping[str, Any]) -> bytes:
    """Safely serialize JSON, utilizing orjson if available."""
    if HAS_ORJSON:
        return orjson.dumps(record, option=orjson.OPT_APPEND_NEWLINE)
    return (json.dumps(record, separators=(",", ":")) + "\n").encode("utf-8")


def parse_json(raw: bytes) -> Any:
    """Safely parse JSON bytes, utilizing orjson if available."""
    if HAS_ORJSON:
        return orjson.loads(raw)
    return json.loads(raw.decode("utf-8"))


def now_iso() -> str:
    return datetime.now().astimezone().isoformat()


def emit_continue() -> None:
    sys.stdout.buffer.write(CONTINUE_RESPONSE)
    sys.stdout.buffer.flush()


def ensure_parent_dir(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def try_unlink(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        pass


class FileLock:
    """
    Simple cross-platform sidecar lock file.

    Lock file lifecycle:
    - Acquire by atomically creating "<target>.lock"
    - Release by deleting it
    - If a lock file becomes stale, remove it
    """

    def __init__(
        self,
        target_path: Path,
        timeout_seconds: float = LOCK_TIMEOUT_SECONDS,
        stale_seconds: float = LOCK_STALE_SECONDS,
    ) -> None:
        self.target_path = target_path
        self.lock_path = Path(str(target_path) + ".lock")
        self.timeout_seconds = timeout_seconds
        self.stale_seconds = stale_seconds
        self.acquired = False

    def acquire(self) -> None:
        deadline = time.monotonic() + self.timeout_seconds

        while True:
            try:
                fd = os.open(
                    str(self.lock_path),
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                )
                try:
                    payload = (
                        f"pid={os.getpid()} "
                        f"time={now_iso()} "
                        f"target={self.target_path}\n"
                    ).encode("utf-8", errors="replace")
                    os.write(fd, payload)
                finally:
                    os.close(fd)

                self.acquired = True
                return

            except FileExistsError:
                try:
                    age = time.time() - self.lock_path.stat().st_mtime
                    if age > self.stale_seconds:
                        try_unlink(self.lock_path)
                        continue
                except FileNotFoundError:
                    continue
                except OSError:
                    pass

                if time.monotonic() >= deadline:
                    raise TimeoutError(f"Timed out acquiring lock: {self.lock_path}")

                time.sleep(LOCK_POLL_INTERVAL_SECONDS)

    def release(self) -> None:
        if self.acquired:
            try_unlink(self.lock_path)
            self.acquired = False

    def __enter__(self) -> "FileLock":
        self.acquire()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()


def append_safely(path: Path, data: bytes) -> None:
    ensure_parent_dir(path)
    with FileLock(path):
        with path.open("ab") as fh:
            fh.write(data)
            if not data.endswith(b"\n"):
                fh.write(b"\n")


def safe_debug_append(debug_log: Path, message: str) -> None:
    try:
        append_safely(debug_log, message.encode("utf-8", errors="replace"))
    except Exception:
        # Never break the hook because debug logging failed.
        pass


def safe_audit_append(audit_log: Path, record: Mapping[str, Any], debug_log: Path) -> None:
    try:
        append_safely(audit_log, serialize_json(record))
    except Exception as exc:
        safe_debug_append(
            debug_log,
            f"[{now_iso()}] audit_write_error={type(exc).__name__}: {exc}",
        )


def normalize_raw_input(raw: bytes) -> bytes:
    """
    Preserve the payload as much as possible.
    Only normalize:
    - leading UTF-8 BOM
    - leading/trailing NUL characters
    """
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    return raw.strip(b"\x00")


def first_present(obj: Mapping[str, Any], *names: str) -> Any:
    for name in names:
        if name in obj:
            value = obj[name]
            if value is None:
                continue
            if isinstance(value, str) and not value.strip():
                continue
            return value
    return None


def get_tool_input(obj: Mapping[str, Any]) -> Any:
    if "tool_input" in obj:
        return obj["tool_input"]
    if "toolArgs" in obj:
        return obj["toolArgs"]
    return None


def build_debug_header(raw_input: bytes) -> str:
    raw_preview = raw_input.decode("utf-8", errors="replace")
    return "\n".join(
        [
            f"[{now_iso()}] pid={os.getpid()}",
            f"[{now_iso()}] executable={sys.executable}",
            f"[{now_iso()}] python_version={sys.version.replace(os.linesep, ' ')}",
            f"[{now_iso()}] platform={sys.platform}",
            f"[{now_iso()}] cwd={os.getcwd()}",
            f"[{now_iso()}] raw_stdin={raw_preview}",
        ]
    )


def main() -> int:
    timestamp = now_iso()
    script_dir = Path(__file__).resolve().parent

    default_audit_log = (script_dir / ".." / ".." / "logs" / "agent-audit.jsonl").resolve()
    audit_log = Path(os.environ.get("AUDIT_LOG", str(default_audit_log))).resolve()

    debug_log_dir = Path(
        os.environ.get("DEBUG_LOG_DIR", str((script_dir / ".." / ".." / "logs").resolve()))
    ).resolve()
    # debug_log = debug_log_dir / f"agent-debug-{os.getpid()}.log"
    debug_log = debug_log_dir / f"agent-debug.log"

    raw_input = sys.stdin.buffer.read()

    safe_debug_append(debug_log, build_debug_header(raw_input))

    if not raw_input.strip():
        safe_audit_append(
            audit_log,
            {
                "timestamp": timestamp,
                "event": "stdin_empty",
                "session": "unknown",
                "tool": "unknown",
                "toolUseId": "unknown",
                "input": {},
            },
            debug_log,
        )
        emit_continue()
        return 0

    normalized_input = normalize_raw_input(raw_input)

    try:
        parsed = parse_json(normalized_input)
    except Exception as exc:
        safe_debug_append(
            debug_log,
            f"[{now_iso()}] parse_error={type(exc).__name__}: {exc}",
        )
        safe_audit_append(
            audit_log,
            {
                "timestamp": timestamp,
                "event": "parse_error",
                "session": "unknown",
                "tool": "unknown",
                "toolUseId": "unknown",
                "input": {},
                "error": f"{type(exc).__name__}: {exc}",
            },
            debug_log,
        )
        emit_continue()
        return 0

    if not isinstance(parsed, dict):
        safe_debug_append(
            debug_log,
            f"[{now_iso()}] schema_error=top_level_json_is_{type(parsed).__name__}, expected object",
        )
        safe_audit_append(
            audit_log,
            {
                "timestamp": timestamp,
                "event": "schema_error",
                "session": "unknown",
                "tool": "unknown",
                "toolUseId": "unknown",
                "input": {},
                "error": f"Expected top-level JSON object, got {type(parsed).__name__}",
            },
            debug_log,
        )
        emit_continue()
        return 0

    hook_event = first_present(parsed, "hook_event_name", "hookEventName", "hookEvent") or "unknown"
    session_id = first_present(parsed, "session_id", "sessionId") or "unknown"
    tool_name = first_present(parsed, "tool_name", "toolName") or "unknown"
    tool_use_id = first_present(parsed, "tool_use_id", "toolUseId") or "unknown"
    tool_input = get_tool_input(parsed)

    record: dict[str, Any] = {
        "timestamp": timestamp,
        "event": hook_event,
        "session": session_id,
        "tool": tool_name,
        "toolUseId": tool_use_id,
        "input": tool_input if tool_input is not None else {},
    }

    source_timestamp = first_present(parsed, "timestamp")
    transcript_path = first_present(parsed, "transcript_path", "transcriptPath")
    cwd = first_present(parsed, "cwd")

    if source_timestamp is not None:
        record["hookTimestamp"] = source_timestamp
    if transcript_path is not None:
        record["transcriptPath"] = transcript_path
    if cwd is not None:
        record["cwd"] = cwd

    safe_audit_append(audit_log, record, debug_log)

    emit_continue()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as exc:
        try:
            script_dir = Path(__file__).resolve().parent
            fallback_debug = ((script_dir / ".." / ".." / "logs") / f"agent-debug-{os.getpid()}.log").resolve()
            safe_debug_append(
                fallback_debug,
                f"[{now_iso()}] fatal_error={type(exc).__name__}: {exc}",
            )
        finally:
            emit_continue()
            raise SystemExit(0)
