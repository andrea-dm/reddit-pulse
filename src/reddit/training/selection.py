"""Median-seed model selection.

After training a model over N seeds, keep the checkpoint whose test
performance is the (high) median and remove the others.
"""

from __future__ import annotations

import gc
import logging
from math import isfinite
from shutil import rmtree
from typing import TYPE_CHECKING, Any

from pandas import Timestamp

from reddit.core.utils import dump_object

if TYPE_CHECKING:
    from pathlib import Path

    from reddit.core.config import Config
    from reddit.core.protocols import LogFn
    from reddit.training.loop import SeedResult


def select_median(
    config: Config,
    model_name: str,
    results: dict[int, SeedResult],
    finetuning_method: str,
    log: LogFn,
) -> tuple[Path | None, Any | None]:
    """Pick the median-performing seed, delete the others, record the choice.

    Args:
        config: Project configuration (for ``results_dir`` and run date).
        model_name: Short model name, e.g. ``gemma2_27b``.
        results: ``{seed: SeedResult}`` as produced by :func:`reddit.training.loop.run_seeds`.
        finetuning_method: Method label recorded in the selection dump
            (``"-"`` for full BERT fine-tuning).
        log: Progress logger.

    Returns:
        ``(model_path, model_config)`` of the selected checkpoint, or
        ``(None, None)`` when nothing usable was trained.
    """
    if not results:
        log("No model was trained. Skipping...", level="warning")
        return None, None

    # Only seeds with a finite score can be ranked: a NaN performance made
    # `median_high` return NaN, which then matched no seed and raised
    # IndexError, aborting the whole family run.
    ranked = sorted(
        (r for r in results.values() if isfinite(r.performance)),
        key=lambda r: r.performance,
    )
    if not ranked:
        log("No seed produced a usable score. Skipping...", level="warning")
        return None, None

    # Upper median, preserving the previous `statistics.median_high` semantics.
    median = ranked[len(ranked) // 2]

    for result in results.values():
        if result.seed != median.seed:
            rmtree(result.model, ignore_errors=True)
    gc.collect()

    if median.model.exists():
        logging.info("Median seed: %s", median.seed)
        logging.info("Median metrics: %s", f"{median.performance:,.6f}")
        logging.info("Median model: `%s`", median.model.name)
        log(f"Selected model: `{median.model.name}` [{median.performance:,.6f}]", level="info")

        selection_dump = config.paths.results_dir / "selected_models_metrics.jsonl"
        selection_dump.touch(exist_ok=True)
        with open(selection_dump, "ab") as f:
            f.write(
                dump_object(
                    {
                        "inserted": Timestamp.now("Europe/Rome").strftime("%Y-%m-%d.%H:%M:%S"),
                        "date": config.system.date,
                        "model": model_name,
                        "method": finetuning_method,
                        "seed": median.seed,
                        "metrics": float(median.performance),
                    }
                )
            )
        return median.model, median.config

    log("No model available. Skipping...", level="warning")
    return None, None
