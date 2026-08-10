# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.1.0] - 2026-08-10

### Added

- `reddit run`/`train`/`predict` now accept `-m/--model <name>...` to
  select individual models across families, and `--all-families`
  (alias `--all-models`) to run every declared family in one
  invocation, alongside the existing `-f/--family <name>...`
  (`src/reddit/cli.py`, `src/reddit/tasks/run.py`,
  `src/reddit/tasks/predict.py`).
- `Config.resolve_families()` and `Config.resolve_models()`
  (`src/reddit/core/config.py`) resolve the new CLI selection flags
  into one `Models` group per family, rejecting `--model` names that
  no family declares or that more than one family declares.
- `UnknownModelError` (`src/reddit/core/errors.py`), raised by
  `Config.resolve_models()` for unknown or ambiguous `--model` names.

### Changed

- `config.yml` drops the GPU-split helper families (`gemma_27`,
  `small_part1`, `small_part2`, `medium_part1`, `medium_part2`); split
  work across GPUs with a disjoint `--model` subset per `--gpu`
  invocation instead (`config.yml`, `README.md`,
  `docs/how_to/using-the-cli.md`,
  `docs/how_to/fine-tuning-decoder-llms.md`).

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
