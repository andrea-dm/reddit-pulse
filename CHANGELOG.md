# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] - 2026-08-09

### Added

- `reddit` CLI (`src/reddit/cli.py`, entry point `reddit`) with `run`,
  `train`, and `predict` subcommands, plus `-c/--config`, `-g/--gpu`,
  and `-V/--version` flags.
- `reddit.core` package: unified YAML config schema and loader
  (`config.py`), environment bootstrap and CUDA/HF environment setup
  (`environment.py`), a typed error hierarchy (`errors.py`), file and
  console logging setup (`logging.py`), and shared protocols/utilities
  (`protocols.py`, `utils.py`).
- `reddit.data.preparation` module (`src/reddit/data/preparation.py`)
  for splitting the hand-labelled gold dataset (`data/labelled.xlsx`)
  into a `DatasetDict`.
- `reddit.modeling` package: 4-bit quantization and PEFT (QDoRA/xQDoRA)
  configuration (`peft.py`, `loading.py`), classification metrics
  (`metrics.py`), and a weighted `Trainer` subclass (`trainer.py`).
- `reddit.training` package: multi-seed training pipelines for decoder
  LLMs (`llms.py`) and BERT-family encoders (`bert.py`), a shared
  training loop (`loop.py`), and median-seed selection
  (`selection.py`).
- `reddit.inference` package: full-corpus labelling over r/economy,
  r/Economics and r/wallstreetbets (`corpus.py`, with merge-cardinality
  validation against duplicate join keys), BERT/LLM inference backends
  (`bert.py`, `llms.py`), and checkpoint discovery across zip archives
  and directories (`discovery.py`).
- `reddit.tasks` package: CLI task wiring for the `run`/`train` and
  `predict` subcommands (`run.py`, `predict.py`).
- Module-boundary enforcement (`tach.toml`) and dependency-hygiene
  checks (`deptry`, `pyproject.toml` `[tool.deptry]`), wired into CI
  alongside `ruff` and `pyright`
  (`.github/workflows/dependency-architecture-checks.yml`).
- Test suite (`tests/`) covering `core`, `data`, `modeling`,
  `training`, `inference`, `tasks`, and the CLI, using `pytest` and
  `hypothesis`.
