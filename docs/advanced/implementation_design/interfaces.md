# Interfaces

## CLI surface

`reddit.cli.build_parser` registers three subcommands
(`reddit --help` for the live text):

| Subcommand | Required flags | Optional flags | Dispatches to |
|---|---|---|---|
| `run` | `-f/--family` | `-l/--limit`, `--no-inference`, `-c/--config`, `-g/--gpu` | `reddit.tasks.run.execute_run` |
| `train` | `-f/--family` | `-l/--limit`, `-c/--config`, `-g/--gpu` | `reddit.tasks.run.execute_run` (`no_inference=True`) |
| `predict` | `-f/--family`, `-d/--directory` | `-c/--config`, `-g/--gpu` | `reddit.tasks.predict.execute_predict` |

`reddit.cli.main` returns `0` on success, `1` if a `reddit.core.errors.RedditError`
propagates or the pipeline produced no checkpoint; a `ConfigError`
(including an unknown `--family`) is surfaced as `parser.error(...)`
(exit code `2`, argparse convention), never a Python traceback.

## Configuration schema

`reddit.core.config.Config`, loaded by `load_config`, is the single typed
entry point into every pipeline. Every nested model is frozen
(`ConfigDict(frozen=True)`): no pipeline can mutate the configuration it
was handed. Top-level sections: `system`, `dataset`, `paths`, `labels`,
`training`, `inference`, `environment`, `families`. See [Data
Model](data_model.md) for the label-encoding contract and
[Configuring a Run](../../how_to/configuring-a-run.md) for a worked
example.

## Structural protocols

Two `typing.Protocol` classes in `reddit.core.protocols` decouple
`reddit.training` from `reddit.inference`:

- **`LogFn`** — `(message, *, level) -> None`. Concretely a
  `functools.partial` of `reddit.core.logging.print_log`.
- **`Labeller`** — `(*, config, family, model_name, model_path, model_conf,
  tokenizer, finetuning_method, log) -> None`. Implemented by
  `reddit.inference.llms.label_corpus` and
  `reddit.inference.bert.label_corpus`; injected into
  `reddit.training.llms.run_family`/`reddit.training.bert.run_family` as
  the `labeller` keyword argument only by `reddit.tasks.run`.

## Extension points

- **`reddit.training.loop.SeedStrategy`** (`Protocol`) — the six methods a
  kind must implement to plug into `run_seeds`: `build_model`,
  `parameter_counts`, `tokenize`, `data_collator`, `training_arguments`,
  `optimizers`. Implemented by `LlmSeedStrategy` and `BertSeedStrategy`.
- **`reddit.inference.corpus.CorpusJob`** (frozen dataclass) — the
  behavioural knobs one labelling pass needs, built by
  `reddit.inference.llms.build_jobs` / `reddit.inference.bert.build_jobs`
  and consumed by `reddit.inference.corpus.predict_corpus`.

## File-based interfaces

Every cross-process/cross-run interface in this system is a file, not an
API: the gold Excel file, the per-subreddit corpus CSVs, the checkpoint
archives/directories under `models/`, the JSONL metrics/crash-safety dumps
under `dumps/`/`outputs/`, and the consolidated answers CSVs under
`results/`. See [Data Model](data_model.md) for schemas and
[Downstreams](../operations/downstreams.md) for what consumes the answers
CSVs (outside this package).

## See also

- [Components](components.md) for which module owns each symbol above.
- API reference: `reddit.cli`, `reddit.core.config`,
  `reddit.core.protocols`, `reddit.training.loop`,
  `reddit.inference.corpus`.
