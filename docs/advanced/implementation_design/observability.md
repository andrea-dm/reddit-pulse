# Observability

There is no metrics/tracing backend, no dashboard, and no alert routing in
this repository — observability is entirely file-based, structured for a
human reviewing run logs after the fact rather than for a monitoring
system.

## Logging

`reddit.core.logging.setup_logging` (called once, from `reddit.cli.main`,
before the pipeline runs) configures the **root logger** with two handlers:

- **Console** (stderr), level `INFO` by default, plain
  `"%(asctime)s [%(levelname)s] %(message)s"` format.
- **File** (`logs/{command}_{family}.log`), level `DEBUG`, a
  `MicrosecondFormatter` including process name, module and line number.

Calling `setup_logging` twice clears and replaces the handler list rather
than layering handlers — it is meant to be called exactly once per
process.

`reddit.core.logging.print_log` is the second, complementary logging
surface: a human-oriented progress line (with an emoji/level prefix, e.g.
`👉` for info, `⛔` for error), printed to stdout and optionally appended to
a *per-model* log file (`logs/{model}_{date}.log`) when bound via
`functools.partial(print_log, dump=True, filename=..., pid=os.getpid())` —
this is `reddit.core.protocols.LogFn`, threaded through every training and
inference pipeline as the `log` parameter.

## Metrics

Two independent JSONL metrics streams, both written by
`reddit.training.loop.run_seeds`/`reddit.modeling.trainer.LogMetricsCallback`:

- **Per-seed train/test metrics**
  (`outputs/{family}_{date}/dist_{model}_{train,test}_metrics.jsonl`) — one
  record per seed, combining run identity, parameter counts, wall-time and
  every metric from `reddit.modeling.metrics.compute_metrics`.
- **Per-eval-step metrics** (`dumps/{model}[_{method}]_training_logs.jsonl`)
  — one record per `Trainer` evaluation logging event, from
  `LogMetricsCallback.on_log` (main process only).

`reddit.training.selection.select_median` appends one further record per
(model, method) selection to `results/selected_models_metrics.jsonl`.

None of these streams are shipped anywhere (no push to a metrics backend);
they are meant to be read directly (e.g. with `pandas.read_json(lines=True)`)
or tailed during a long-running sweep.

## Error visibility

- `reddit.cli.main` distinguishes user-fixable configuration errors
  (`ConfigError` → `parser.error(...)`, no traceback) from unexpected
  pipeline errors (`RedditError` → logged with `exc_info=True`, exit code
  `1`).
- Every per-seed and per-batch failure inside `run_seeds`/`predict_corpus`
  is caught, logged at `critical`/`error` with a traceback, and the loop
  continues — see [Runtime Behavior](runtime_behavior.md).

## What is intentionally absent

No tracing, no metrics export (Prometheus/OpenTelemetry/etc.), no alerting
integration, no dashboards. Given the deployment model (a manually launched
batch CLI on a single GPU host — see [Deployment](deployment.md)), the
JSONL-file-plus-log-file approach is the system's actual, working
observability posture, not a gap against an assumed monitoring stack; see
[Operations](../operations/index.md) for why no
`monitoring_and_alerting.md` page was added.
