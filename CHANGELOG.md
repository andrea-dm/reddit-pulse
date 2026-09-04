# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- `reddit.modeling.loading.DeviceProfile` / `detect_device_profile`: the
  model-loading policy is now resolved against the visible GPU. Decoder
  LLMs load in `bfloat16` with flash-attention 2 on Ampere-or-newer
  devices (when `flash-attn` is installed) and in `float16` with SDPA
  attention on Turing/Volta (e.g. Tesla T4), where the previous
  unconditional `flash_attention_2` request raised `ImportError` on every
  load and `bfloat16` ran through emulation. The detected profile is
  logged at the start of every run (`src/reddit/modeling/loading.py`,
  `src/reddit/training/loop.py`).
- `reddit.training.loop.FATAL_ERRORS`: an `ImportError`/`OSError` raised by
  a seed (missing package, unreachable or gated checkpoint, disk error)
  now aborts the remaining seeds of that run instead of being retried —
  and re-downloading the checkpoint — once per seed.
- `reddit.training.llms.validate_methods`: the fine-tuning methods of
  every selected LLM family are validated before any training starts
  (`src/reddit/tasks/run.py`), not only when that family's turn comes.
- `ArgumentsConfig` rejects `bf16: true` together with `fp16: true`, and
  `run_seeds` rejects `bf16: true` on a GPU without native bfloat16 before
  the first seed (`ConfigError`).

### Changed

- `reddit.modeling.peft.quantization_config` (a module-level singleton) is
  replaced by `build_quantization_config(compute_dtype)`, so the 4-bit
  compute dtype always matches the loaded weight dtype (previously fixed
  at `float16` under `bfloat16` weights).
- `bert_model_args` loads encoders in `float32`; mixed precision is left
  to the Trainer's `fp16`/`bf16` flag (fp16 autocast needs fp32 master
  weights, and the previous `bfloat16` weights kept the optimizer state at
  an 8-bit mantissa).
- `run_seeds` builds `TrainingArguments` and the collator once, before the
  seed loop (an invalid `training.arguments` key is now one `ConfigError`
  rather than N swallowed per-seed failures), runs each seed in its own
  frame so the previous seed's model is released before the next is
  built, and collects garbage before `torch.cuda.empty_cache()`.
- `predict_corpus` keeps the JSONL dump open for the whole run (one
  open/close per batch before) and reloads it in chunks via
  `pandas.read_json` instead of materializing every record as a Python
  dict first (`src/reddit/inference/corpus.py`).
- `LogMetricsCallback` reports a failed metrics write through `logging`
  instead of `print`.
- `reddit.cli.main` no longer forces the `spawn` multiprocessing start
  method: nothing in the package uses `multiprocessing`, and the setting
  only made every `DataLoader` worker pay a full interpreter start-up.
- Dead `truncation`/`max_length`/`padding` keyword arguments to
  `AutoTokenizer.from_pretrained` are dropped (`src/reddit/training/llms.py`,
  `src/reddit/inference/llms.py`); they were never applied.

### Fixed

- Decoder-LLM training now truncates at `LLM_MAX_LENGTH` (1024 tokens),
  matching inference; the length was previously parked in the tokenizer's
  `init_kwargs` and never applied, so training truncated at the model's
  own limit (8k–131k tokens).
- `label_corpus` (LLM) no longer enables the KV cache (`use_cache=True`)
  for a classification forward pass that never generates.
- `python -m reddit` now exits with `main()`'s return code (it always
  exited 0 before).
- `load_config` raises `ConfigError` for an empty or non-mapping YAML file
  instead of a raw `AttributeError`.
- `archive_model` no longer deletes the checkpoint directory when zipping
  it failed; it now returns whether the archive was written.

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
