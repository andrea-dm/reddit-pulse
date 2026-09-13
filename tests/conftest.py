"""Building-block fixtures shared across the whole suite.

Everything here is importable with only the light ``test`` dependency group
(pytest, pydantic, pyyaml, python-dotenv, pandas): no torch, transformers,
datasets or sklearn.  Sub-suites that need the heavy stack gate themselves
with ``pytest.importorskip`` in their own modules.

Two safety fixtures are autouse for the entire session:

* ``_isolated_environ`` — production code calls ``os.environ[...] = ...``
  directly (``prepare_environment``), which ``monkeypatch.setenv`` cannot
  always undo; the process environment is snapshotted and restored verbatim.
* ``_isolated_root_logger`` — ``setup_logging`` clears and repopulates the
  root logger and opens file handlers, and raises the level of a few noisy
  third-party loggers (``httpx``/``httpcore``); handlers are closed and
  every mutated level restored.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Iterator
from copy import deepcopy
from importlib.util import find_spec
from pathlib import Path
from typing import Any

import pytest
from yaml import safe_dump, safe_load

from reddit.core.config import Config, LabelsConfig, load_config
from reddit.core.environment import bootstrap_directories
from reddit.core.logging import _NOISY_THIRD_PARTY_LOGGERS, LogLevel

REPO_ROOT = Path(__file__).resolve().parents[1]
PROJECT_CONFIG = REPO_ROOT / "config.yml"

# The CI `pytest` job installs only the light `test` dependency group (see
# .github/workflows/dependency-architecture-checks.yml): no torch, transformers,
# datasets or sklearn.  Those sub-suites are simply not collected there; in a
# full environment nothing is skipped.
collect_ignore: list[str] = []
if find_spec("torch") is None:
    collect_ignore += ["inference", "modeling", "training", "tasks"]
if find_spec("datasets") is None or find_spec("sklearn") is None:
    collect_ignore += ["data"]

PATH_KEYS = (
    "reddit_dir",
    "cache_dir",
    "dumps_dir",
    "output_dir",
    "results_dir",
    "models_dir",
    "labels_dir",
    "logs_dir",
)


# ────────────────────────────────────────────────────────────────── isolation ───


@pytest.fixture(autouse=True)
def _isolated_environ() -> Iterator[None]:
    """Restore ``os.environ`` verbatim after every test."""
    saved = os.environ.copy()
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(saved)


@pytest.fixture(autouse=True)
def _safe_hf_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point ``HF_HOME`` inside ``tmp_path`` for every test.

    ``clear_hf_cache`` and the training/inference cleanup paths delete whole
    directories under the effective Hugging Face home; no test may ever aim
    them at the real cache.  Tests that exercise the precedence rules override
    or delete the variable themselves.
    """
    monkeypatch.setenv("HF_HOME", str(tmp_path / "hf_home"))


@pytest.fixture(autouse=True)
def _isolated_root_logger() -> Iterator[None]:
    """Restore the root logger's handlers/level and the noisy third-party levels."""
    root = logging.getLogger()
    saved_handlers = list(root.handlers)
    saved_level = root.level
    saved_third_party = {name: logging.getLogger(name).level for name in _NOISY_THIRD_PARTY_LOGGERS}
    try:
        yield
    finally:
        for handler in list(root.handlers):
            if handler not in saved_handlers:
                handler.close()
        root.handlers[:] = saved_handlers
        root.setLevel(saved_level)
        for name, level in saved_third_party.items():
            logging.getLogger(name).setLevel(level)


# ──────────────────────────────────────────────── the repository's own config ───


@pytest.fixture(scope="session")
def repo_root() -> Path:
    """Absolute path of the project root (read-only)."""
    return REPO_ROOT


@pytest.fixture(scope="session")
def project_config_path() -> Path:
    """Path of the real ``config.yml`` shipped with the repository (read-only)."""
    return PROJECT_CONFIG


@pytest.fixture(scope="session")
def _project_config_baseline() -> dict[str, Any]:
    """Immutable parse of the shipped ``config.yml``; use ``project_config_raw``."""
    return safe_load(PROJECT_CONFIG.read_text(encoding="utf-8"))


@pytest.fixture
def project_config_raw(_project_config_baseline: dict[str, Any]) -> dict[str, Any]:
    """Freely mutable deep copy of the shipped ``config.yml`` mapping."""
    return deepcopy(_project_config_baseline)


@pytest.fixture(scope="session")
def shipped_dataset_path(project_config_path: Path, _project_config_baseline: dict[str, Any]) -> Path:
    """Absolute path of the real gold dataset ``config.yml`` points ``dataset.path`` at.

    Deliberately gitignored (``/data/`` — proprietary, too large for git), so
    absent from a bare CI checkout. Tests that need the file's actual rows
    (not just its existence) skip via this fixture; see ``project_config``.
    """
    raw = os.path.expandvars(os.path.expanduser(str(_project_config_baseline["dataset"]["path"])))
    resolved = Path(raw)
    return resolved if resolved.is_absolute() else project_config_path.parent / resolved


@pytest.fixture
def project_config(
    project_config_path: Path,
    shipped_dataset_path: Path,
    _project_config_baseline: dict[str, Any],
    write_config: Callable[..., Path],
    tmp_path: Path,
) -> Config:
    """The shipped configuration, loaded. Loading is a pure read (no side effects).

    When the real gold dataset isn't present in this environment, validate a
    copy with an empty placeholder dataset instead: every consumer here except
    the one integration test that reads the file's actual rows only needs the
    config to *parse*, not the real data to exist (see ``shipped_dataset_path``).
    """
    if shipped_dataset_path.exists():
        return load_config(project_config_path)

    placeholder = tmp_path / "placeholder-gold.xlsx"
    placeholder.touch()
    raw = deepcopy(_project_config_baseline)
    raw["dataset"]["path"] = str(placeholder)
    return load_config(write_config(raw, directory=tmp_path / "shipped-copy"))


# ─────────────────────────────────────────────── self-contained tmp_path config ───


@pytest.fixture
def dataset_file(tmp_path: Path) -> Path:
    """Placeholder gold-dataset file: ``DatasetConfig`` only checks existence."""
    path = tmp_path / "gold.xlsx"
    path.touch()
    return path


@pytest.fixture
def raw_config(dataset_file: Path) -> dict[str, Any]:
    """A minimal but complete raw config mapping, anchored inside ``tmp_path``.

    Paths stay *relative* so that the anchoring behaviour of ``load_config``
    (resolve against the config file's own directory) is exercised.
    """
    return {
        "system": {"date": "20240102", "sleep_time": 0},
        "environment": {"pytorch_alloc_conf": "expandable_segments:True"},
        "dataset": {
            "path": str(dataset_file),
            "text_column": "title",
            "label_column": "Label",
            "test_size": 0.10,
            "validation_size": 0.19,
        },
        "paths": {key: key.removesuffix("_dir") for key in PATH_KEYS},
        "labels": {
            "labels": {"down": 0, "neutral": 1, "up": 2},
            "encodings": {"down": -1, "neutral": 0, "up": 1},
        },
        "training": {
            "seeds": [11, 22, 33],
            "finetuning_methods": ["qdora", "xqdora"],
            "arguments": {"fp16": False, "report_to": None},
        },
        "inference": {"batch_size": 2, "submissions": True, "comments": True},
        "families": {
            "llm_family": {"models": [{"name": "tiny_llm", "id": "acme/tiny-llm"}]},
            "bert_family": {
                "kind": "bert",
                "models": [{"name": "tiny_bert", "id": "acme/tiny-bert"}],
            },
            "single_method": {
                "finetuning_methods": ["xqdora"],
                "models": [{"name": "tiny_llm", "id": "acme/tiny-llm"}],
            },
        },
    }


@pytest.fixture
def write_config(tmp_path: Path) -> Callable[..., Path]:
    """Factory: dump a raw mapping to a YAML file inside ``tmp_path``."""

    def _write(raw: dict[str, Any], name: str = "config.yml", directory: Path | None = None) -> Path:
        target = (directory or tmp_path) / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(safe_dump(raw, sort_keys=False), encoding="utf-8")
        return target

    return _write


@pytest.fixture
def config_factory(write_config: Callable[..., Path], raw_config: dict[str, Any]) -> Callable[..., Config]:
    """Factory: build a :class:`Config` from ``raw_config`` plus section overrides.

    ``config_factory(labels={...})`` replaces the whole ``labels`` section;
    unspecified sections keep the ``raw_config`` defaults.
    """

    def _factory(name: str = "config.yml", directory: Path | None = None, **sections: Any) -> Config:
        raw = deepcopy(raw_config)
        raw.update(sections)
        return load_config(write_config(raw, name=name, directory=directory))

    return _factory


@pytest.fixture
def config(config_factory: Callable[..., Config]) -> Config:
    """A valid configuration whose every path lives under ``tmp_path``."""
    return config_factory()


@pytest.fixture
def bootstrapped_config(config: Config) -> Config:
    """``config`` with every declared directory materialised."""
    bootstrap_directories(config)
    return config


@pytest.fixture
def labels_config(config: Config) -> LabelsConfig:
    """The three-label authority (down=0, neutral=1, up=2)."""
    return config.labels


# ───────────────────────────────────────────────────────────── log recording ───


class RecordingLog:
    """A :class:`reddit.core.protocols.LogFn` double that records its calls."""

    def __init__(self) -> None:
        self.calls: list[tuple[str | None, LogLevel | int | None]] = []

    def __call__(self, message: str | None = None, *, level: LogLevel | int | None = 0) -> None:
        self.calls.append((message, level))

    @property
    def messages(self) -> list[str]:
        return [m for m, _ in self.calls if m is not None]

    def messages_at(self, level: LogLevel | int | None) -> list[str]:
        return [m for m, lvl in self.calls if m is not None and lvl == level]

    def text_at(self, level: LogLevel | int | None) -> str:
        return "\n".join(self.messages_at(level))

    @property
    def text(self) -> str:
        return "\n".join(self.messages)


@pytest.fixture
def recording_log() -> RecordingLog:
    """A ``LogFn`` stub that captures ``(message, level)`` pairs."""
    return RecordingLog()
