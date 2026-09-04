"""Discovery of trained checkpoints on disk.

LLM checkpoints are stored as ``{model}_{method}_{seed}.zip`` archives
(created by :func:`reddit.core.utils.archive_model`); BERT checkpoints as
plain ``{model}_{seed}`` directories.
"""

from __future__ import annotations

import logging
from pathlib import Path
from shutil import rmtree
from typing import TYPE_CHECKING
from zipfile import ZipFile

if TYPE_CHECKING:
    from collections.abc import Generator, Iterable

# `{model}_{method}_{seed}` (LLM archives) and `{model}_{seed}` (BERT
# directories): the number of `_`-separated parts a valid checkpoint name has.
LLM_ARCHIVE_PARTS = 3
BERT_DIR_PARTS = 2


def iter_model_archives(
    directory_path: str | Path,
    methods: Iterable[str] = ("qdora", "xqdora"),
) -> Generator[tuple[str, str, str], None, None]:
    """Yield ``(model_name, finetuning_method, unzipped_path)`` from zip archives.

    Archives are extracted one at a time and the extraction folder is removed
    after the consumer advances the generator.

    Args:
        directory_path: Directory to scan for ``{model}_{method}_{seed}.zip``
            archives (as produced by :func:`reddit.core.utils.archive_model`).
        methods: Fine-tuning method tags to accept (matched against the
            archive filename's second-to-last ``_``-separated component);
            defaults to ``("qdora", "xqdora")``.

    Yields:
        ``(model_name, finetuning_method, unzip_path)`` for every archive
        whose filename matches the ``{model}_{method}_{seed}.zip`` pattern
        and whose method is in ``methods``.

    Notes:
        Each archive is extracted to a sibling directory and that directory
        is removed (I/O) once the caller resumes the generator after
        consuming a yielded item — so only one checkpoint's files are ever
        on disk unzipped at a time.
    """
    parent_dir = Path(directory_path)
    if not parent_dir.is_dir():
        logging.error("Directory not found at '%s'", directory_path)
        return

    methods = set(methods)
    logging.info("Scanning directory '%s' for model archives...", directory_path)
    for zip_path in sorted(parent_dir.glob("*.zip")):
        stem = zip_path.stem
        parts = stem.rsplit("_", 2)

        if len(parts) == LLM_ARCHIVE_PARTS and parts[1] in methods and parts[2].isdigit():
            model_name, finetuning_method, seed = parts
            logging.info("Found model: `%s` trained on %s via %s", model_name, seed, finetuning_method)
            unzip_path = zip_path.parent / stem
            try:
                with ZipFile(zip_path, "r") as zip_ref:
                    zip_ref.extractall(unzip_path)
                logging.info("`%s` unzipped to `%s`.", zip_path.name, unzip_path.name)
                yield (model_name, finetuning_method, str(unzip_path))
            finally:
                if unzip_path.exists():
                    rmtree(unzip_path)
                    logging.info("`%s` cleaned up.", unzip_path.name)
        else:
            logging.warning("Skipping file with incorrect name format: `%s`", zip_path.name)

    logging.info("Scanning directory `%s` for models... done!", directory_path)


def iter_model_dirs(directory_path: str | Path) -> Generator[tuple[str, str], None, None]:
    """Yield ``(model_name, path)`` from ``{model}_{seed}`` checkpoint directories.

    Args:
        directory_path: Directory to scan for ``{model}_{seed}`` checkpoint
            directories (the BERT save layout; no fine-tuning-method
            component since BERT models are fully fine-tuned, not PEFT).

    Yields:
        ``(model_name, path)`` for every subdirectory whose name matches the
        ``{model}_{seed}`` pattern, in sorted order.
    """
    parent_dir = Path(directory_path)
    if not parent_dir.is_dir():
        logging.error("Directory not found at '%s'", directory_path)
        return

    logging.info("Scanning directory `%s` for models...", directory_path)
    for item_path in sorted(parent_dir.iterdir()):
        if item_path.is_dir():
            parts = item_path.name.rsplit("_", 1)
            if len(parts) == BERT_DIR_PARTS and parts[1].isdigit():
                model_name, seed = parts
                logging.info("Found model: `%s` trained on %s at %s", model_name, seed, item_path)
                yield (model_name, str(item_path))

    logging.info("Scanning directory `%s` for models... done!", directory_path)
