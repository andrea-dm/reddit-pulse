"""Discovery of trained checkpoints on disk.

LLM checkpoints are stored as ``{model}_{method}_{seed}.zip`` archives
(created by :func:`reddit.core.utils.archive_model`); BERT checkpoints as
plain ``{model}_{seed}`` directories.
"""

from __future__ import annotations

import logging
from collections.abc import Generator, Iterable
from pathlib import Path
from shutil import rmtree
from zipfile import ZipFile


def iter_model_archives(
    directory_path: str | Path,
    methods: Iterable[str] = ("qdora", "xqdora"),
) -> Generator[tuple[str, str, str], None, None]:
    """Yield ``(model_name, finetuning_method, unzipped_path)`` from zip archives.

    Archives are extracted one at a time and the extraction folder is removed
    after the consumer advances the generator.
    """
    parent_dir = Path(directory_path)
    if not parent_dir.is_dir():
        logging.error(f"Directory not found at '{directory_path}'")
        return

    methods = set(methods)
    logging.info(f"Scanning directory '{directory_path}' for model archives...")
    for zip_path in sorted(parent_dir.glob("*.zip")):
        stem = zip_path.stem
        parts = stem.rsplit("_", 2)

        if len(parts) == 3 and parts[1] in methods and parts[2].isdigit():
            model_name, finetuning_method, seed = parts
            logging.info(f"Found model: `{model_name}` trained on {seed=} via {finetuning_method}")
            unzip_path = zip_path.parent / stem
            try:
                with ZipFile(zip_path, "r") as zip_ref:
                    zip_ref.extractall(unzip_path)
                logging.info(f"`{zip_path.name}` unzipped to `{unzip_path.name}`.")
                yield (model_name, finetuning_method, str(unzip_path))
            finally:
                if unzip_path.exists():
                    rmtree(unzip_path)
                    logging.info(f"`{unzip_path.name}` cleaned up.")
        else:
            logging.warning(f"Skipping file with incorrect name format: `{zip_path.name}`")

    logging.info(f"Scanning directory `{directory_path}` for models... done!")


def iter_model_dirs(directory_path: str | Path) -> Generator[tuple[str, str], None, None]:
    """Yield ``(model_name, path)`` from ``{model}_{seed}`` checkpoint directories."""
    parent_dir = Path(directory_path)
    if not parent_dir.is_dir():
        logging.error(f"Directory not found at '{directory_path}'")
        return

    logging.info(f"Scanning directory `{directory_path}` for models...")
    for item_path in sorted(parent_dir.iterdir()):
        if item_path.is_dir():
            parts = item_path.name.rsplit("_", 1)
            if len(parts) == 2 and parts[1].isdigit():
                model_name, seed = parts
                logging.info(f"Found model: `{model_name}` trained on {seed=} at {item_path}")
                yield (model_name, str(item_path))

    logging.info(f"Scanning directory `{directory_path}` for models... done!")
