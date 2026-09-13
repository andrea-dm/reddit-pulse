# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [2.0.0] - 2026-09-13

### Added

- `scripts/train_queue.sh`: trains a cohort as a memory-aware queue over
  the local GPUs (one `reddit train` per model, largest measured footprint
  first, each to the card with the most budget left, smaller jobs filling
  the gaps as others exit), then publishes the selected checkpoints with one
  `reddit upload` (`JOBS`, `GPUS`, `BUDGET_GB`, `COMMAND`, `UPLOAD`, `DRY`).
- `reddit upload`: publishes selected checkpoints to the Hugging Face Hub,
  one repository per checkpoint (`hub.namespace` / `hub.repo_name` in
  `config.yml`; the template defaults to `reddit-pulse-{slug}` after the
  hand-published `andreadm/reddit-pulse-bert`), staging the folder under
  `outputs/hub/` first (`--dry-run` stops there), then adds it to
  `hub.collection`. It refuses to start, dry run included, when a selected
  model and method has more than one checkpoint in the scanned directory,
  since both would publish to the same repository
  (`reddit.tasks.upload.checkpoint_conflicts`, on top of
  `reddit.inference.discovery.parse_archive_name`/`parse_dir_name`).
  The token comes from `HF_WRITE_TOKEN` in the dotenv
  (`HF_TOKEN` as fallback). Each repository holds the weights, tokenizer
  and `config.json`, a generated model card in the layout of the reference
  card (front matter with `base_model`/`model-index`, a notice that the
  checkpoint was retrained and its metrics may differ from the paper's,
  labels, usage snippet, seed protocol, hyperparameters, per-seed
  evaluation, corpus label shares when the checkpoint labelled the corpus,
  files, reproducing,
  dual citation, license), `training_args.json` (local paths removed), a
  `training_config.yml` extract, `evaluation/*.csv` and the base model's
  own `LICENSE*`/`USE_POLICY*` files (`src/reddit/hub/`,
  `src/reddit/tasks/upload.py`, `src/reddit/cli.py`).
- `reddit.core.utils.read_jsonl`: parses JSONL dumps whether or not their
  records are newline-terminated (the 2025 dumps glued records together).
- `reddit.modeling.loading.adapter_base_model` and
  `reddit.data.preparation.label_counts`.
- `training.gradient_checkpointing` (default `true`, the previous
  hardcoded behaviour): reentrant activation checkpointing for decoder-LLM
  training, applied consistently to `peft.prepare_model_for_kbit_training`
  and `TrainingArguments` — the Trainer only ever switches checkpointing
  on, so the knob has to reach both (`src/reddit/core/config.py`,
  `src/reddit/training/llms.py`, `config.yml`).
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

#### Breaking changes

- **Removed** `reddit.modeling.peft.quantization_config` (a module-level
  `BitsAndBytesConfig` singleton, previously exported from
  `reddit.modeling.__init__.__all__`). It is replaced by
  `build_quantization_config(compute_dtype)`, so the 4-bit compute dtype
  always matches the loaded weight dtype (the singleton was hardcoded to
  `float16`, which under the `bfloat16` weights this release now loads by
  default ran the compute dtype through emulation). **Migration:** replace
  `from reddit.modeling import quantization_config` (or
  `reddit.modeling.peft.quantization_config`) with
  `from reddit.modeling import build_quantization_config`, then call
  `build_quantization_config(compute_dtype)` with the dtype the base model
  is loaded in (`src/reddit/modeling/peft.py`,
  `src/reddit/modeling/__init__.py`).

- `LICENSE.md` holds only the MIT license text, so GitHub detects the
  license; the paper's disclaimer (the views are the authors', the license
  covers the software only) moves to `NOTICE.md`, which ships in the
  package's `license-files` and is included in the License docs page.
- `config.yml` now selects `bf16` mixed precision (was `fp16`): the A100
  target loads and de-quantizes the decoder LLMs in bfloat16
  (`reddit.modeling.loading`), fp16 autocast on top needed a `GradScaler`
  and is the precision Gemma-2 is known to overflow in. Gradient
  checkpointing is off and `dataloader_num_workers` is `0` for the sub-3B
  cohort: 16 micro-batches of short titles per epoch neither need
  activation recomputation on an 80 GB card nor amortise a worker pool
  forked every epoch. `llama3.2_1b` is declared again.
- The seed protocol is now what the paper describes — the split is the
  run's only variable. `training.arguments.seed` (pinned to `42` in
  `config.yml` and `ArgumentsConfig`, previously the unpinned transformers
  default) seeds both model initialisation and the Trainer's own
  shuffling/dropout; `run_seeds` re-seeds from it right before building
  the model, so PEFT-adapter and classification-head init no longer follow
  the split seed (`src/reddit/training/loop.py`, `src/reddit/core/config.py`).
- Lint and type gates hardened to the `carbon_pledges` sibling project's
  policy: ruff now runs the SonarLint-parity families (`ARG`, `DTZ`, `ERA`,
  `FURB`, `G`, `N`, `PERF`, `PGH`, `S`, `SLF`, `T20`, `TC`, `TRY`, `UP`, ...),
  Google-style `D1` docstring presence, the six complexity gates
  (`C901`, `PLR0911/12/14/15/16`, `PLR1702`) and `BLE001`; pyright adds
  `reportUnreachable`, `reportUnnecessaryComparison`,
  `reportPropertyTypeMismatch`, `reportUninitializedInstanceVariable`,
  `reportImplicitOverride`, `reportMissingSuperCall` and
  `deprecateTypingAliases`. Every house-style exemption is now an explicit,
  commented `ignore`/`per-file-ignores` entry (`pyproject.toml`,
  `pyrightconfig.json`). The code was brought into line: logging calls use
  `%s`-style arguments, type-only imports live in `TYPE_CHECKING` blocks,
  overriding methods carry `@override`, `print_log` is table-driven, and
  `.github/utils/` is linted and type-checked too.
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

- Corpus labelling reloaded PEFT checkpoints through transformers' adapter
  shortcut (`AutoModelForSequenceClassification.from_pretrained(<adapter
  dir>)`), which on transformers 5 rebuilds the DoRA adapter into a model
  whose logits differ from the trained one by up to 17 on the same titles
  (predictions flipped). `reddit.inference.llms.load_classifier` now
  reloads the 4-bit base model and applies `PeftModel.from_pretrained` on
  top, reproducing the Trainer's model; the base weights are read from the
  per-model cache the training stage already filled
  (`src/reddit/inference/llms.py`).
- PEFT checkpoints now carry `config.json` (head size, label names, pad
  token): `Trainer.save_model` on a `PeftModel` writes the adapter only,
  which left `reddit predict` unable to rebuild the model config
  (`AutoConfig` rejected the directory) and a Hub user unable to reload
  the adapter without re-deriving all three (`src/reddit/training/loop.py`).
  The saved config drops the `quantization_config` block the 4-bit load
  left on it (`reddit.modeling.loading.strip_quantization`): fed back into
  `from_pretrained` next to an explicit 4-bit request it made transformers
  treat the base weights as pre-quantized and PEFT could not attach the
  adapter. `_predict_one` falls back to the base model's config for older
  archives.
- `--model qwen2.5_0.5b` was rejected as ambiguous: the smoke-test `test`
  family declared the same model name as the `qwen` family. The smoke
  model is now `qwen2.5_0.5b_smoke` (same checkpoint), so its artefacts
  also stop mixing with a real run's (`config.yml`).
- `config.yml` keys the schema does not declare are rejected at load time
  (`extra="forbid"` on every section) instead of silently dropped: a
  `gradient_checkpointing: false` under `training.arguments` used to be
  accepted and ignored (`src/reddit/core/config.py`).
- `setup_logging` raises `httpx`/`httpcore` (transitively used by
  `huggingface_hub` for every Hub request) to `WARNING`: one INFO-level
  line per `HEAD`/`GET` request was flooding both the console and the
  per-run log file, indistinguishable from this project's own progress
  lines (`src/reddit/core/logging.py`).
- `training.arguments.report_to` defaults to `"none"` and a YAML `null` is
  coerced to it: transformers 5 wraps `None` as `[None]` and the Trainer
  then rejected it as an unknown integration on every seed, so a `reddit
  run` trained nothing. `run_seeds` now also validates the reporting
  integrations once in its preflight, so an unknown tracker is a single
  `ConfigError` rather than N swallowed per-seed failures
  (`src/reddit/core/config.py`, `src/reddit/training/loop.py`, `config.yml`).
- `update_answers` serialises its read-merge-write through a lock
  directory (`<answers>.lock/`, `mkdir`-atomic so it works on the CIFS
  mount): two `reddit` processes labelling different models of the same
  family — the documented way to split work across GPUs — could
  previously overwrite each other's columns in the per-family answers
  file, the second writer silently dropping the first one's
  (`src/reddit/inference/corpus.py`).
- `predict_corpus` deletes the crash-safety JSONL dump once the labelled
  CSV is on disk (a failed CSV write keeps it); one full-corpus dump per
  model and method was accumulating under `output_dir`.
- `run_model` (LLM) reports a failed checkpoint archive as an error naming
  the unzipped directory, instead of ignoring `archive_model`'s result;
  `reddit predict` scans for `*.zip` only.
- An out-of-range prediction now decodes to trend `None` rather than the
  string `"unknown"`, keeping the trend column numeric.
- `test_every_unknown_family_name_raises_a_config_error` no longer trips
  Hypothesis's 200 ms deadline on a loaded box.
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
