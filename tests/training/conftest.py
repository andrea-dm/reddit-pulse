"""Fixtures shared by the ``reddit.training`` sub-suite."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from reddit.core.config import Config
from reddit.training.loop import SeedResult


@pytest.fixture
def seed_results(bootstrapped_config: Config) -> Callable[..., dict[int, SeedResult]]:
    """Factory: build ``{seed: SeedResult}`` with a real checkpoint directory each.

    ``performances`` maps positionally onto seeds ``100, 101, ...``; passing
    ``materialise=False`` leaves the checkpoint directories absent.
    """

    def _build(
        performances: list[float],
        *,
        method: str = "qdora",
        materialise: bool = True,
        model_config: Any = None,
    ) -> dict[int, SeedResult]:
        results: dict[int, SeedResult] = {}
        for offset, performance in enumerate(performances):
            seed = 100 + offset
            checkpoint = bootstrapped_config.paths.models_dir / f"tiny_llm_{method}_{seed}"
            if materialise:
                checkpoint.mkdir(parents=True, exist_ok=True)
                (checkpoint / "adapter_model.safetensors").write_text("weights", encoding="utf-8")
            results[seed] = SeedResult(
                seed=seed,
                performance=performance,
                method=method,
                model=checkpoint,
                config=model_config if model_config is not None else f"config-of-{seed}",
            )
        return results

    return _build


@pytest.fixture
def selection_dump(bootstrapped_config: Config) -> Path:
    """Path of the append-only median-selection record."""
    return bootstrapped_config.paths.results_dir / "selected_models_metrics.jsonl"
