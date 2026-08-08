#!/usr/bin/env python3
# pyright: basic
"""
PreToolUse hook for enforcing VS Code files.exclude policies.

Goals:
- Read hook payload JSON from stdin.
- Return only the hook permission decision JSON on stdout.
- Enforce files.exclude rules from:
  - *.code-workspace -> settings.files.exclude
  - .vscode/settings.json -> files.exclude
- Support both snake_case and camelCase payload variants.
- Use orjson for speed, with a safe fallback to standard json.
- Use state-machine JSONC stripping for maximum accuracy.
- Path-aware glob matching with conservative substring fallback.
- Fail open on unexpected errors to prevent agent bricking.
"""

from __future__ import annotations

import fnmatch
import os
import sys
from pathlib import Path, PurePosixPath
from typing import Any

# 1. Graceful Fallback (Never disable security if orjson is missing)
try:
    import orjson
    HAS_ORJSON = True
except ImportError:
    import json
    HAS_ORJSON = False


TARGET_TOOLS = {
    "replace_string_in_file",
    "multi_replace_string_in_file",
    "create_file",
    "read_file",
    "grep_search",
    "file_search",
    "semantic_search",
}

ALLOW_RESPONSE_BYTES = (
    b'{"hookSpecificOutput":{"hookEvent":"PreToolUse","permissionDecision":"allow"}}\n'
)

PATH_FIELD_NAMES = {
    "path",
    "paths",
    "filepath",
    "filepaths",
    "file_path",
    "file_paths",
    "relativepath",
    "relativepaths",
    "relative_path",
    "relative_paths",
    "directory",
    "directories",
    "dir",
    "dirs",
    "folder",
    "folders",
    "rootpath",
    "root_path",
}


# --- JSON Abstraction ---

def dump_json_bytes(obj: Any) -> bytes:
    if HAS_ORJSON:
        return orjson.dumps(obj)
    return json.dumps(obj, separators=(",", ":")).encode("utf-8")

def load_json_bytes(raw: bytes) -> Any:
    if HAS_ORJSON:
        return orjson.loads(raw)
    return json.loads(raw.decode("utf-8"))


# --- Core I/O ---

def emit_allow() -> int:
    sys.stdout.buffer.write(ALLOW_RESPONSE_BYTES)
    sys.stdout.buffer.flush()
    return 0


def emit_deny(reason: str) -> int:
    payload = {
        "hookSpecificOutput": {
            "hookEvent": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }
    sys.stdout.buffer.write(dump_json_bytes(payload) + b"\n")
    sys.stdout.buffer.flush()
    return 0


def normalize_stdin(raw: bytes) -> bytes:
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    return raw.strip(b"\x00")


def parse_payload(raw: bytes) -> dict[str, Any] | None:
    if not raw.strip():
        return None

    try:
        parsed = load_json_bytes(normalize_stdin(raw))
    except Exception:
        return None

    return parsed if isinstance(parsed, dict) else None


def first_present(obj: dict[str, Any], *names: str) -> Any:
    for name in names:
        if name in obj:
            return obj[name]
    return None


def get_tool_name(payload: dict[str, Any]) -> str:
    value = first_present(payload, "tool_name", "toolName")
    return value if isinstance(value, str) else ""


def get_tool_input(payload: dict[str, Any]) -> Any:
    if "tool_input" in payload:
        return payload["tool_input"]
    if "toolInput" in payload:
        return payload["toolInput"]
    if "toolArgs" in payload:
        return payload["toolArgs"]
    return {}


def serialize_json(value: Any) -> str:
    try:
        return dump_json_bytes(value).decode("utf-8", errors="replace")
    except Exception:
        return "{}"


# --- JSONC State Machine ---

def strip_jsonc_comments(text: str) -> str:
    """
    Remove // and /* */ comments without touching content inside JSON strings.
    This state-machine guarantees URLs and strings are safely preserved.
    """
    result: list[str] = []
    i = 0
    n = len(text)

    in_string = False
    escaping = False
    in_line_comment = False
    in_block_comment = False

    while i < n:
        ch = text[i]
        nxt = text[i + 1] if i + 1 < n else ""

        if in_line_comment:
            if ch == "\n" or ch == "\r":
                in_line_comment = False
                result.append(ch)
            i += 1
            continue

        if in_block_comment:
            if ch == "*" and nxt == "/":
                in_block_comment = False
                i += 2
            else:
                i += 1
            continue

        if in_string:
            result.append(ch)

            if escaping:
                escaping = False
            elif ch == "\\":
                escaping = True
            elif ch == '"':
                in_string = False

            i += 1
            continue

        if ch == '"':
            in_string = True
            result.append(ch)
            i += 1
            continue

        if ch == "/" and nxt == "/":
            in_line_comment = True
            i += 2
            continue

        if ch == "/" and nxt == "*":
            in_block_comment = True
            i += 2
            continue

        result.append(ch)
        i += 1

    return "".join(result)


def read_text_file(path: Path) -> str | None:
    try:
        if not path.is_file():
            return None
        return path.read_text(encoding="utf-8")
    except Exception:
        return None


def parse_jsonc_file(path: Path) -> dict[str, Any] | None:
    raw = read_text_file(path)
    if not raw:
        return None

    cleaned = strip_jsonc_comments(raw)

    try:
        parsed = load_json_bytes(cleaned.encode("utf-8"))
    except Exception:
        return None

    return parsed if isinstance(parsed, dict) else None


# --- Policy Extraction ---

def extract_true_excludes(node: Any) -> set[str]:
    if not isinstance(node, dict):
        return set()

    patterns: set[str] = set()
    for pattern, enabled in node.items():
        if isinstance(pattern, str) and pattern.strip() and enabled is True:
            patterns.add(pattern)
    return patterns


def find_workspace_files(repo_root: Path) -> list[Path]:
    try:
        return sorted(
            p for p in repo_root.glob("*.code-workspace")
            if p.is_file()
        )
    except Exception:
        return []


def get_excluded_patterns(repo_root: Path) -> list[str]:
    patterns: set[str] = set()

    for workspace_file in find_workspace_files(repo_root):
        parsed = parse_jsonc_file(workspace_file)
        if not parsed:
            continue

        settings = parsed.get("settings")
        if isinstance(settings, dict):
            patterns.update(extract_true_excludes(settings.get("files.exclude")))

    vscode_settings = repo_root / ".vscode" / "settings.json"
    parsed_settings = parse_jsonc_file(vscode_settings)
    if parsed_settings:
        patterns.update(extract_true_excludes(parsed_settings.get("files.exclude")))

    return sorted(patterns)


# --- Path Resolution & Matching ---

def normalize_path_text(value: str) -> str:
    value = value.strip().strip('"').strip("'")
    value = value.replace("\\", "/")
    while "//" in value:
        value = value.replace("//", "/")
    value = value.strip()

    if os.name == "nt":
        value = value.lower()

    return value


def normalize_pattern(pattern: str) -> str:
    pattern = pattern.strip()
    pattern = pattern.replace("\\", "/")

    if os.name == "nt":
        pattern = pattern.lower()

    return pattern


def simplify_pattern(pattern: str) -> str:
    base = normalize_pattern(pattern)

    while base.startswith("**/") or base.startswith("**\\"):
        base = base[3:]

    while base.endswith("/**") or base.endswith("\\**"):
        base = base[:-3]

    while base.endswith("*"):
        base = base[:-1]

    return base.strip("/").strip()


def candidate_path_variants(candidate: str, repo_root: Path) -> set[str]:
    variants: set[str] = set()
    raw = normalize_path_text(candidate)

    if not raw:
        return variants

    variants.add(raw)

    try:
        p = Path(candidate)
        if not p.is_absolute():
            p = (repo_root / p).resolve()
        else:
            p = p.resolve()

        abs_norm = normalize_path_text(str(p))
        if abs_norm:
            variants.add(abs_norm)

        try:
            rel = p.relative_to(repo_root.resolve())
            rel_norm = normalize_path_text(str(rel))
            if rel_norm:
                variants.add(rel_norm)
        except Exception:
            pass
    except Exception:
        pass

    return variants


def path_like_key(name: str) -> bool:
    key = name.strip().lower()
    return (
        key in PATH_FIELD_NAMES
        or key.endswith("path")
        or key.endswith("paths")
    )


def collect_candidate_paths(value: Any, parent_key: str | None = None) -> set[str]:
    candidates: set[str] = set()

    if isinstance(value, dict):
        for key, child in value.items():
            if isinstance(key, str):
                if path_like_key(key):
                    if isinstance(child, str) and child.strip():
                        candidates.add(child)
                    elif isinstance(child, list):
                        for item in child:
                            if isinstance(item, str) and item.strip():
                                candidates.add(item)

                candidates.update(collect_candidate_paths(child, key))

    elif isinstance(value, list):
        for item in value:
            candidates.update(collect_candidate_paths(item, parent_key))

    return candidates


def pattern_matches_variant(pattern: str, path_variant: str) -> bool:
    norm_pattern = normalize_pattern(pattern)
    norm_variant = normalize_path_text(path_variant)

    if not norm_pattern or not norm_variant:
        return False

    pure_variant = PurePosixPath(norm_variant.lstrip("./"))

    try:
        if pure_variant.match(norm_pattern):
            return True
    except Exception:
        pass

    if fnmatch.fnmatchcase(norm_variant, norm_pattern):
        return True

    base = simplify_pattern(norm_pattern)
    if base and base in norm_variant:
        return True

    return False


def find_matching_pattern_for_paths(
    candidate_paths: set[str],
    excluded_patterns: list[str],
    repo_root: Path,
) -> str | None:
    for candidate in candidate_paths:
        variants = candidate_path_variants(candidate, repo_root)
        for pattern in excluded_patterns:
            for variant in variants:
                if pattern_matches_variant(pattern, variant):
                    return pattern
    return None


def find_matching_pattern_in_serialized_input(
    tool_input: Any,
    excluded_patterns: list[str],
) -> str | None:
    payload = serialize_json(tool_input)
    if not payload:
        return None

    normalized_payload = normalize_path_text(payload)

    for pattern in excluded_patterns:
        base = simplify_pattern(pattern)
        if base and base in normalized_payload:
            return pattern

    return None


# --- Main Execution ---

def main() -> int:
    raw_input = sys.stdin.buffer.read()

    if not raw_input.strip():
        return 0

    payload = parse_payload(raw_input)
    if payload is None:
        return emit_allow()

    tool_name = get_tool_name(payload)
    if not tool_name:
        return emit_allow()

    if tool_name not in TARGET_TOOLS:
        return emit_allow()

    tool_input = get_tool_input(payload)

    script_dir = Path(__file__).resolve().parent
    repo_root = (script_dir / ".." / "..").resolve()

    excluded_patterns = get_excluded_patterns(repo_root)
    if not excluded_patterns:
        return emit_allow()

    candidate_paths = collect_candidate_paths(tool_input)
    matched_pattern = find_matching_pattern_for_paths(
        candidate_paths=candidate_paths,
        excluded_patterns=excluded_patterns,
        repo_root=repo_root,
    )

    if matched_pattern:
        return emit_deny(
            f"Access to excluded path denied by files.exclude policy: {matched_pattern}"
        )

    matched_pattern = find_matching_pattern_in_serialized_input(
        tool_input=tool_input,
        excluded_patterns=excluded_patterns,
    )

    if matched_pattern:
        return emit_deny(
            f"Access to excluded path denied by files.exclude policy: {matched_pattern}"
        )

    return emit_allow()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:
        # Absolute last-resort hook stability guarantee.
        emit_allow()
        raise SystemExit(0)
