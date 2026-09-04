"""Shared helpers: JSONL dumping, model archiving and cache hygiene.

Timestamp convention: :func:`now` returns a UTC ISO-8601 instant and backs the
machine-readable ``timestamp`` field of the training-log records.  The
``inserted`` field of the metrics and selection dumps instead carries a naive
``Europe/Rome`` wall-clock stamp, which is how the run logs are read by hand.
Both are deliberate; they are not interchangeable.
"""

from __future__ import annotations

import datetime
import logging
import os
from pathlib import Path
from shutil import make_archive, rmtree

import orjson


def fmt_td(nanoseconds: int) -> str:
    """Human-readable elapsed time from a ``monotonic_ns`` delta.

    The input is a difference of two ``time.monotonic_ns()`` readings and is
    therefore non-negative by construction; output for negative values is
    unspecified.  Durations are decomposed arithmetically: reinterpreting the
    delta as an absolute timestamp silently wrapped every run longer than
    24 hours.

    Args:
        nanoseconds: Non-negative duration in nanoseconds.

    Returns:
        A string of the form ``"H hour(s), M minute(s) and S.ffffff seconds"``.
    """
    microseconds, _ = divmod(int(nanoseconds), 1_000)
    seconds, microsecond = divmod(microseconds, 1_000_000)
    minutes, second = divmod(seconds, 60)
    hour, minute = divmod(minutes, 60)
    return f"{hour} hour(s), {minute} minute(s) and {second}.{microsecond:06d} seconds"


def now() -> str:
    """UTC ISO-8601 instant, microsecond precision.

    Returns:
        The current UTC instant as ``datetime.isoformat(timespec="microseconds")``.
    """
    return datetime.datetime.now(tz=datetime.UTC).isoformat(timespec="microseconds")


def dump_object(d: dict[str, object]) -> bytes:
    r"""Serialize a dict to one UTF-8 JSON line (a JSONL record) using orjson.

    Args:
        d: The record to serialize.

    Returns:
        The JSON-encoded record as UTF-8 bytes, terminated with ``b"\n"``.

    Raises:
        TypeError: ``d`` contains a value ``orjson`` cannot encode.
    """
    try:
        return orjson.dumps(d) + b"\n"
    except orjson.JSONEncodeError as e:
        raise TypeError(f"Failed to encode to JSON: {e}") from e


def archive_model(model_path: str | Path, archive_name: str) -> bool:
    """Zip a saved model directory then remove the directory.

    Args:
        model_path: Directory to archive and then delete.
        archive_name: Destination archive path, without the ``.zip``
            extension (``shutil.make_archive`` appends it).

    Returns:
        ``True`` when the archive was written (and the directory removed),
        ``False`` when zipping failed — in which case ``model_path`` is left
        untouched, so a failed archive can never cost the checkpoint itself.

    Notes:
        Writes ``{archive_name}.zip`` and deletes ``model_path`` (I/O).
        Both steps log a warning and continue on failure rather than
        raising, so a zip/cleanup error cannot abort the caller.
    """
    try:
        make_archive(archive_name, "zip", model_path)
    except Exception as e:
        logging.warning("Could not zip the dumped model `%s`: %s", Path(model_path).name, e, exc_info=True)
        return False
    try:
        rmtree(model_path, ignore_errors=True)
    except Exception as e:
        logging.warning("Could not clear the dumped model `%s`: %s", Path(model_path).name, e, exc_info=True)
    return True


def clear_hf_cache(model_id: str, extra_cache_dirs: list[Path] | None = None) -> None:
    """Delete a model's folder from the Hugging Face hub cache(s).

    WARNING: destructive; other processes sharing a swept cache lose the
    download.  Only the cache root this process is actually using (``HF_HOME``,
    falling back to the platform default) plus any explicitly passed extras are
    swept — never the user default cache *in addition to* an overridden
    ``HF_HOME``, which previously deleted downloads belonging to co-tenant
    processes that this run never touched.

    Args:
        model_id: The hub identifier, e.g. ``Qwen/Qwen2.5-0.5B``.
        extra_cache_dirs: Additional HF_HOME-style roots to sweep explicitly.

    Notes:
        This call is destructive: it permanently deletes the model's cached
        weights from every swept cache root (I/O), logging progress at each
        step.
    """
    model_dir_name = f"models--{model_id.replace('/', '--')}"
    paths_to_check = {
        Path(os.getenv("HF_HOME", Path.home() / ".cache" / "huggingface")),
        *(extra_cache_dirs or []),
    }
    for path in paths_to_check:
        model_path = path / "hub" / model_dir_name
        if model_path.is_dir():
            logging.info("Found model `%s` in cache at `%s`", model_id, model_path)
            try:
                rmtree(model_path)
                logging.info("Successfully deleted `%s` from cache.", model_id)
            except OSError as e:
                logging.info("Error deleting `%s` from cache: %s", model_path, e)
        else:
            logging.info("Model `%s` not found in cache at `%s`.", model_id, path / "hub")
