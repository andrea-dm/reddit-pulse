#!/usr/bin/env python3
# pyright: basic
"""PostToolUse hook that auto-fixes section separators in edited Python files.

Standalone script — all constants, detectors, builders, and the rewriter are
inlined so that nothing is imported from ``housekeeping/``.

Protocol
--------
* **stdin**: JSON payload from VS Code Copilot (PostToolUse event).
* **stdout**: JSON hook response (``{"continue": true}`` always).
* **exit 0**: success — VS Code parses stdout.

The hook only acts on file-editing tool calls whose target is a ``.py`` file
under ``src/dqmp/`` or ``tests/``.  All other invocations are passed through.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

# --- JSON abstraction (orjson with stdlib fallback) -----------------------

try:
    import orjson

    _HAS_ORJSON = True
except ImportError:  # pragma: no cover
    import json

    _HAS_ORJSON = False


def _load_json(raw: bytes) -> Any:
    if _HAS_ORJSON:
        return orjson.loads(raw)
    return json.loads(raw.decode("utf-8"))


def _dump_json(obj: Any) -> bytes:
    if _HAS_ORJSON:
        return orjson.dumps(obj)
    return json.dumps(obj, separators=(",", ":")).encode("utf-8")


# --- Hook I/O helpers -----------------------------------------------------

_CONTINUE: bytes = b'{"continue":true}\n'

_FILE_EDIT_TOOLS: frozenset[str] = frozenset(
    {
        "create_file",
        "replace_string_in_file",
        "multi_replace_string_in_file",
    },
)

_SCOPE_PREFIXES: tuple[str, ...] = ("src/reddit/", "tests/")


def _emit(payload: bytes) -> int:
    sys.stdout.buffer.write(payload)
    sys.stdout.buffer.flush()
    return 0


def _emit_continue() -> int:
    return _emit(_CONTINUE)


def _emit_with_context(message: str) -> int:
    resp: dict[str, Any] = {
        "continue": True,
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": message,
        },
    }
    return _emit(_dump_json(resp) + b"\n")


# --- Payload parsing ------------------------------------------------------


def _first_present(obj: dict[str, Any], *keys: str) -> Any:
    for k in keys:
        if k in obj:
            return obj[k]
    return None


def _extract_file_paths(payload: dict[str, Any]) -> list[str]:
    """Return all candidate file paths from the tool input."""
    tool_input: Any = _first_present(payload, "tool_input", "toolInput", "toolArgs")
    if not isinstance(tool_input, dict):
        return []

    paths: list[str] = []

    # Single-file tools: filePath / file_path
    for key in ("filePath", "file_path"):
        val = tool_input.get(key)
        if isinstance(val, str) and val.strip():
            paths.append(val.strip())

    # multi_replace_string_in_file stores paths inside replacements[]
    replacements: Any = tool_input.get("replacements")
    if isinstance(replacements, list):
        for item in replacements:
            if isinstance(item, dict):
                for key in ("filePath", "file_path"):
                    val = item.get(key)
                    if isinstance(val, str) and val.strip():
                        paths.append(val.strip())

    return paths


def _in_scope(file_path: str, repo_root: Path) -> bool:
    """Return True if *file_path* is a ``.py`` file under a scoped prefix."""
    if not file_path.lower().endswith(".py"):
        return False
    try:
        resolved = Path(file_path).resolve()
        rel = resolved.relative_to(repo_root.resolve()).as_posix()
    except (ValueError, OSError):
        return False
    return any(rel.startswith(prefix) for prefix in _SCOPE_PREFIXES)


# ── Separator constants (inlined from housekeeping/_common.py) ────────────

EQ: str = "\u2550"
THIN: str = "\u2500"
BORDER: str = "# " + THIN * 84 + " #"
MODULE_SEP_WIDTH: int = 88
WITHIN_SEP_WIDTH: int = 68

_IMPORT_TAIL_RE: re.Pattern[str] = re.compile(
    r"^(from |import |\)$|if TYPE_CHECKING)",
)


# ── Builders (inlined from housekeeping/fix_separators.py) ────────────────


def _title_line(title: str) -> str:
    suffix: str = f" {title} {EQ * 3} #"
    fill: int = MODULE_SEP_WIDTH - 2 - len(suffix)
    return "# " + EQ * fill + suffix


def _within_line(title: str, indent: int) -> str:
    prefix: str = f"# --- {title} "
    fill: int = WITHIN_SEP_WIDTH - len(prefix)
    return " " * indent + prefix + "-" * max(fill, 1)


# ── Detectors (inlined from housekeeping/fix_separators.py) ──────────────


def _mod_eq_title(line: str) -> str | None:
    stripped: str = line.rstrip()
    if stripped != stripped.lstrip() or EQ not in stripped:
        return None
    if not (stripped.startswith("# ") and stripped.endswith(" #")):
        return None
    title: str = stripped[2:-2].strip(EQ).strip()
    return title or None


def _mod_thin_title(line: str) -> str | None:
    stripped: str = line.rstrip()
    if stripped != stripped.lstrip() or THIN not in stripped or EQ in stripped:
        return None
    if not (stripped.startswith("# ") and stripped.endswith(" #")):
        return None
    title: str = stripped[2:-2].strip(THIN).strip()
    return title or None


def _mod_dash_title(line: str) -> str | None:
    stripped: str = line.rstrip()
    if stripped != stripped.lstrip():
        return None
    match: re.Match[str] | None = re.match(
        r"^# -{3,}\s+(.+?)\s+-{3,}$",
        stripped,
    )
    return match.group(1) if match else None


def _within_title(line: str) -> tuple[int, str] | None:
    raw: str = line.rstrip()
    if raw == raw.lstrip():
        return None
    stripped: str = raw.lstrip()
    indent: int = len(raw) - len(stripped)

    if stripped.startswith("# ") and THIN in stripped:
        inner: str = stripped[2:]
        if inner.endswith(" #"):
            inner = inner[:-2]
        elif inner.endswith("#"):
            inner = inner[:-1]
        title: str = inner.strip(THIN).strip()
        if title:
            return indent, title

    match: re.Match[str] | None = re.match(
        r"^# -{3,}\s+(.+?)\s+-{3,}$",
        stripped,
    )
    if match:
        return indent, match.group(1)

    return None


# ── Transforms (inlined from housekeeping/fix_separators.py) ─────────────


def _emit_module_block(out: list[str], title: str) -> None:
    changed: bool = True
    while changed:
        changed = False
        while out and out[-1].strip() == "":
            out.pop()
            changed = True
        if out and out[-1] == BORDER:
            out.pop()
            changed = True
    blanks_above: int = 1 if out and _IMPORT_TAIL_RE.match(out[-1]) else 2
    out.extend(
        [""] * blanks_above + [BORDER, _title_line(title), BORDER, "", ""],
    )


def _process(path: Path) -> int:
    """Rewrite separators in *path*.  Return number of separators fixed."""
    text: str = path.read_text("utf-8")
    lines: list[str] = text.split("\n")
    out: list[str] = []
    changes: int = 0
    idx: int = 0

    while idx < len(lines):
        raw: str = lines[idx]

        eq_result: str | None = _mod_eq_title(raw)
        if eq_result:
            _emit_module_block(out, eq_result)
            idx += 1
            while idx < len(lines) and lines[idx].strip() == BORDER:
                idx += 1
            while idx < len(lines) and lines[idx].strip() == "":
                idx += 1
            changes += 1
            continue

        title_str: str | None = _mod_thin_title(raw)
        if title_str:
            _emit_module_block(out, title_str)
            idx += 1
            while idx < len(lines) and lines[idx].strip() == BORDER:
                idx += 1
            while idx < len(lines) and lines[idx].strip() == "":
                idx += 1
            changes += 1
            continue

        title_str = _mod_dash_title(raw)
        if title_str:
            out.append(_within_line(title_str, 0))
            idx += 1
            changes += 1
            continue

        result: tuple[int, str] | None = _within_title(raw)
        if result:
            indent, title = result
            out.append(_within_line(title, indent))
            idx += 1
            changes += 1
            continue

        out.append(raw)
        idx += 1

    new_text: str = "\n".join(out)
    if new_text != text:
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(new_text)
        return changes
    return 0


# ── Entry point ──────────────────────────────────────────────────────────


def _parse_stdin() -> dict[str, Any] | None:
    """Read and parse the JSON hook payload from stdin."""
    raw_input: bytes = sys.stdin.buffer.read()
    if not raw_input.strip():
        return None
    try:
        parsed: Any = _load_json(raw_input.lstrip(b"\xef\xbb\xbf").strip(b"\x00"))
    except Exception:
        return None
    return parsed if isinstance(parsed, dict) else None


def _resolve_targets(payload: dict[str, Any], repo_root: Path) -> list[Path]:
    """Return deduplicated, in-scope target paths from the payload."""
    file_paths: list[str] = _extract_file_paths(payload)
    return [
        Path(fp).resolve()
        for fp in dict.fromkeys(file_paths)  # deduplicate, preserve order
        if _in_scope(fp, repo_root)
    ]


def main() -> int:
    """Read a PostToolUse payload from stdin and auto-fix separators."""
    payload: dict[str, Any] | None = _parse_stdin()
    if payload is None:
        return _emit_continue()

    tool_name: Any = _first_present(payload, "tool_name", "toolName")
    if not isinstance(tool_name, str) or tool_name not in _FILE_EDIT_TOOLS:
        return _emit_continue()

    script_dir: Path = Path(__file__).resolve().parent
    repo_root: Path = (script_dir / ".." / "..").resolve()

    targets: list[Path] = _resolve_targets(payload, repo_root)
    if not targets:
        return _emit_continue()

    total_fixed: int = 0
    files_fixed: list[str] = []
    for target in targets:
        try:
            fixed: int = _process(target)
        except Exception:
            continue
        if fixed:
            total_fixed += fixed
            try:
                rel: str = target.relative_to(repo_root).as_posix()
            except ValueError:
                rel = str(target)
            files_fixed.append(rel)

    if total_fixed:
        summary: str = f"Auto-fixed {total_fixed} separator(s) in: " + ", ".join(
            files_fixed
        )
        return _emit_with_context(summary)

    return _emit_continue()


if __name__ == "__main__":
    sys.exit(main())
