"""Compute and manage sha7 content hashes for skill manifest files.

Skill manifests live under ``manifests/skills/<skill-slug>/`` and follow the
naming convention ``<slug>__<topic>__<sha7>.md``. The ``sha7`` is the first
seven hex characters of the SHA-256 digest of the manifest's *canonical body*
(see :func:`canonical_body`). Frontmatter fields such as ``hash:`` and
``revisions:`` are metadata about the body and are intentionally excluded from
the digest, so editing those fields does not invalidate the hash.

The CLI exposes three subcommands used by the skill workflows defined under
``.github/skills/``:

* ``hash <file>`` prints the recomputed sha7.
* ``verify <file>`` exits 0 when the declared ``hash:`` matches the recomputed
  sha7, 1 on mismatch, 2 on structural errors.
* ``commit <file>`` recomputes the sha7, rewrites the ``hash:`` field plus the
  first entry of ``revisions:``, and renames the file so that its ``<sha7>``
  segment matches the new digest.

The script depends only on the Python standard library so it can run in any
environment that has a ``python3`` interpreter, including local pre-commit
hooks and CI jobs.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Final

# Type alias for CLI subcommand handlers.
_Handler = Callable[[Path], int]

# ════════════════════════════════════════════════════════ MODULE CONSTANTS ════════ #

FRONTMATTER_OPEN: Final = "---\n"
FRONTMATTER_CLOSE: Final = "\n---\n"

# Matches a top-level ``hash: <value>`` line in the YAML frontmatter. The value
# may be empty (e.g. ``hash:`` placeholder for a freshly authored manifest).
HASH_FIELD_PATTERN: Final = re.compile(r"^hash:[ \t]*(\S*)[ \t]*$", re.MULTILINE)

# Matches the first ``- sha: <value>`` entry under a ``revisions:`` list. The
# pattern is anchored on the literal ``revisions:`` line so that we never
# rewrite a ``sha:`` field that lives outside the revisions block.
REVISIONS_FIRST_SHA_PATTERN: Final = re.compile(
    r"(^revisions:[ \t]*\n[ \t]*-[ \t]+sha:[ \t]*)\S+",
    re.MULTILINE,
)

# Matches the ``file: <slug>__<topic>__<sha>.md`` line of the first revision
# entry. The non-greedy block in the middle skips over optional metadata lines
# such as ``created_at:`` that may appear between ``- sha:`` and ``file:``.
REVISIONS_FIRST_FILE_PATTERN: Final = re.compile(
    r"(^revisions:[ \t]*\n(?:[ \t]+[^\n]*\n)*?[ \t]+file:[ \t]*\S+?__\S+?__)"
    r"\S+?(\.md\b)",
    re.MULTILINE,
)

SHA_LENGTH: Final = 7

# Number of ``__``-separated segments in a canonical manifest filename:
# ``<slug>__<topic>__<sha>``.
_FILENAME_SEGMENTS: Final = 3

# ════════════════════════════════════════════════════════════ CORE HELPERS ════════ #


def split_frontmatter(text: str) -> tuple[str, str]:
    """Split *text* into ``(frontmatter, body)``.

    Line endings are normalised to LF before splitting. The returned
    ``frontmatter`` includes both the opening and the closing ``---`` markers
    plus the trailing newline; ``body`` is everything after that boundary.

    Args:
        text: Full manifest content as read from disk.

    Returns:
        A tuple ``(frontmatter, body)`` of LF-normalised strings.

    Raises:
        ValueError: If *text* does not start with a YAML frontmatter block, or
            if the frontmatter block is not properly closed.
    """
    normalised = text.replace("\r\n", "\n").replace("\r", "\n")
    if not normalised.startswith(FRONTMATTER_OPEN):
        msg = "manifest is missing YAML frontmatter (must start with '---')"
        raise ValueError(msg)
    close_at = normalised.find(FRONTMATTER_CLOSE, len(FRONTMATTER_OPEN))
    if close_at == -1:
        msg = "manifest YAML frontmatter is not closed (missing trailing '---')"
        raise ValueError(msg)
    boundary = close_at + len(FRONTMATTER_CLOSE)
    return normalised[:boundary], normalised[boundary:]


def canonical_body(body: str) -> str:
    """Return the canonical form of *body* used for hashing.

    The canonical form strips trailing whitespace from every line, removes
    trailing blank lines, and ensures the result ends with exactly one LF.
    This makes the digest stable across editors that disagree on trailing
    whitespace and final-newline conventions.

    Args:
        body: Manifest body (everything after the closing ``---`` marker).

    Returns:
        The canonical UTF-8 string used as the SHA-256 input.
    """
    lines = [line.rstrip() for line in body.split("\n")]
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines) + "\n"


def compute_sha7(text: str) -> str:
    """Return the 7-char hex SHA-256 of the canonical body of *text*.

    Args:
        text: Full manifest content as read from disk.

    Returns:
        The first :data:`SHA_LENGTH` hex characters of the SHA-256 digest.

    Raises:
        ValueError: Propagated from :func:`split_frontmatter`.
    """
    _, body = split_frontmatter(text)
    digest = hashlib.sha256(canonical_body(body).encode("utf-8")).hexdigest()
    return digest[:SHA_LENGTH]


def update_frontmatter_hash(frontmatter: str, sha: str) -> str:
    """Rewrite the ``hash:`` field and first ``revisions[].sha`` to *sha*.

    Args:
        frontmatter: The original frontmatter block including the ``---``
            markers, as returned by :func:`split_frontmatter`.
        sha: The new sha7 to embed.

    Returns:
        The updated frontmatter block.

    Raises:
        ValueError: If the frontmatter does not contain a ``hash:`` field.
    """
    if HASH_FIELD_PATTERN.search(frontmatter) is None:
        msg = "frontmatter is missing the 'hash:' field"
        raise ValueError(msg)
    updated = HASH_FIELD_PATTERN.sub(f"hash: {sha}", frontmatter, count=1)
    updated = REVISIONS_FIRST_SHA_PATTERN.sub(rf"\g<1>{sha}", updated, count=1)
    return REVISIONS_FIRST_FILE_PATTERN.sub(rf"\g<1>{sha}\g<2>", updated, count=1)


def filename_for(slug: str, topic: str, sha: str) -> str:
    """Build the canonical filename ``<slug>__<topic>__<sha>.md``."""
    return f"{slug}__{topic}__{sha}.md"


def parse_filename(name: str) -> tuple[str, str, str]:
    """Parse a manifest filename into ``(slug, topic, sha)``.

    Args:
        name: The bare filename (with or without the ``.md`` suffix).

    Returns:
        Tuple ``(slug, topic, sha)``.

    Raises:
        ValueError: If *name* does not match ``<slug>__<topic>__<sha>.md``.
    """
    stem = name.removesuffix(".md")
    parts = stem.split("__")
    if len(parts) != _FILENAME_SEGMENTS or not all(parts):
        msg = (
            f"filename {name!r} does not match the canonical pattern "
            "'<slug>__<topic>__<sha>.md'"
        )
        raise ValueError(msg)
    slug, topic, sha = parts
    return slug, topic, sha


# ═════════════════════════════════════════════════════════════ CLI HANDLERS ════════ #


def _read_declared_hash(frontmatter: str) -> str | None:
    """Return the value of the ``hash:`` field, or ``None`` if missing."""
    match = HASH_FIELD_PATTERN.search(frontmatter)
    if match is None:
        return None
    return match.group(1)


def cmd_hash(path: Path) -> int:
    """Implement ``manifest_hash hash <path>``."""
    text = path.read_text(encoding="utf-8")
    sys.stdout.write(compute_sha7(text) + "\n")
    return 0


def cmd_verify(path: Path) -> int:
    """Implement ``manifest_hash verify <path>``.

    Returns:
        ``0`` when the declared and recomputed sha7 match, ``1`` on mismatch,
        and ``2`` when the manifest is structurally invalid.
    """
    text = path.read_text(encoding="utf-8")
    frontmatter, _ = split_frontmatter(text)
    declared = _read_declared_hash(frontmatter)
    if declared is None:
        sys.stderr.write("verify: frontmatter is missing the 'hash:' field\n")
        return 2
    actual = compute_sha7(text)
    if declared == actual:
        sys.stdout.write(f"OK {actual}\n")
        return 0
    sys.stderr.write(f"MISMATCH declared={declared} actual={actual}\n")
    return 1


def cmd_commit(path: Path) -> int:
    """Implement ``manifest_hash commit <path>``.

    Recomputes the sha7, rewrites the frontmatter, writes the file under its
    canonical name, and removes the original file when the rename actually
    moves the content.
    """
    text = path.read_text(encoding="utf-8")
    frontmatter, body = split_frontmatter(text)
    sha = compute_sha7(text)
    new_frontmatter = update_frontmatter_hash(frontmatter, sha)
    new_text = new_frontmatter + body
    slug, topic, _ = parse_filename(path.name)
    new_path = path.with_name(filename_for(slug, topic, sha))
    new_path.write_text(new_text, encoding="utf-8", newline="\n")
    if new_path != path:
        path.unlink()
    sys.stdout.write(f"{new_path}\n")
    return 0


# ═════════════════════════════════════════════════════════════════ ENTRYPOINT ════ #


def _build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog="manifest_hash",
        description="Compute and manage sha7 hashes for skill manifests.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    for name, help_text in (
        ("hash", "Print the recomputed sha7 of the manifest."),
        ("verify", "Verify that the declared 'hash:' matches the body."),
        ("commit", "Recompute the sha7, rewrite frontmatter, and rename file."),
    ):
        sub_parser = sub.add_parser(name, help=help_text)
        sub_parser.add_argument("path", type=Path, help="Path to the manifest file.")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the CLI and return the exit code."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    handlers: dict[str, _Handler] = {
        "hash": cmd_hash,
        "verify": cmd_verify,
        "commit": cmd_commit,
    }
    return handlers[args.command](args.path)


if __name__ == "__main__":
    raise SystemExit(main())
