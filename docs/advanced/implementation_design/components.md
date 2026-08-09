# Components

Per-module technical contracts, in `tach.toml` layering order (foundation
first). Every public symbol named below is cross-linked to its full API
entry.

## `reddit.core`

Foundation layer; no internal dependencies.

- `core.config` — the typed, frozen (pydantic `ConfigDict(frozen=True)`)
  configuration schema (`Config` and its sections) and `load_config`.
  Single authority for the label set (`LabelsConfig`).
- `core.environment` — `bootstrap_directories` (filesystem side effect,
  separated from config loading) and `prepare_environment` (CUDA/HF
  env-var export, dotenv load, HF login).
- `core.errors` — `RedditError` hierarchy: `ConfigError`,
  `UnknownFamilyError`, `UnsupportedMethodError`, `UndeclaredLabelError`,
  `CorpusUnavailableError`. The CLI catches `ConfigError` and turns it into
  a usage message rather than a traceback.
- `core.logging` — `setup_logging` (root-logger console+file handlers) and
  `print_log` (human-oriented progress line, optionally dumped to a
  per-run log file).
- `core.protocols` — `LogFn` and `Labeller` structural protocols that let
  `reddit.training` inject a corpus-labelling step without importing
  `reddit.inference`.
- `core.utils` — `fmt_td`, `now`, `dump_object` (JSONL), `archive_model`
  (zip + cleanup), `clear_hf_cache` (destructive cache sweep).

## `reddit.data`

Depends on `core` only.

- `data.preparation` — `load_and_prepare_data` loads the gold Excel file,
  validates its labels against `LabelsConfig`, and produces a
  class-stratified `DataBundle` (train/validation/test `DatasetDict`).

## `reddit.modeling`

Depends on `core` only.

- `modeling.peft` — `quantization_config` (4-bit NF4 `BitsAndBytesConfig`)
  and `peft_config` (the `"qdora"`/`"xqdora"` `LoraConfig` registry — QDoRA+
  and xQDoRA+).
- `modeling.loading` — `llm_model_args`/`bert_model_args`: the single
  owner of *how* a checkpoint is materialized (dtype, quantization,
  attention implementation), shared by training and inference so the two
  cannot drift.
- `modeling.metrics` — `compute_metrics`: accuracy, weighted/macro F1,
  macro recall/precision, macro one-vs-rest ROC-AUC.
- `modeling.trainer` — `WeightedLossTrainer` (class-weighted,
  gradient-accumulation-safe cross-entropy) and `LogMetricsCallback`
  (per-step JSONL metrics).

## `reddit.training`

Depends on `core`, `data`, `modeling`. Never imports `inference`.

- `training.loop` — `run_seeds`, the shared multi-seed loop; `SeedContext`,
  `SeedResult`, and the `SeedStrategy` protocol both kind-specific
  strategies implement.
- `training.llms` — `LlmSeedStrategy` (QDoRA+/xQDoRA+ PEFT fine-tuning) and
  `run_family`/`run_model`.
- `training.bert` — `BertSeedStrategy` (full fine-tuning) and
  `run_family`/`train_model`.
- `training.selection` — `select_median`: keeps the (high-)median test-F1
  checkpoint, deletes the rest, records the choice.

## `reddit.inference`

Depends on `core`, `modeling`. Never imports `training`.

- `inference.corpus` — `CorpusJob`, `predict_corpus`, `update_answers`: the
  shared batching/crash-safety/merge machinery both kind-specific
  labellers build jobs for.
- `inference.discovery` — `iter_model_archives`/`iter_model_dirs`:
  checkpoint discovery on disk.
- `inference.llms` — `build_jobs`/`label_corpus`/`predict_from_archives`
  for decoder-LLM checkpoints.
- `inference.bert` — `build_jobs`/`label_corpus`/`predict_from_directories`
  for BERT-family checkpoints.

## `reddit.tasks`

Depends on `core`, `training`, `inference` — the composition root.

- `tasks.run` — `setup_run`/`execute_run`: the `run`/`train` subcommands,
  the only place a training pipeline and an inference labeller are wired
  together.
- `tasks.predict` — `setup_predict`/`execute_predict`: the `predict`
  subcommand.

## `reddit.cli`

Depends on `core`, `tasks`.

- `cli.build_parser`/`cli.main` — argument parsing, config resolution,
  environment bootstrap, subcommand dispatch, top-level error handling.

## See also

- [Interfaces](interfaces.md) for the full public signatures.
- [How To](../../how_to/index.md) for task-oriented usage of each
  component.
