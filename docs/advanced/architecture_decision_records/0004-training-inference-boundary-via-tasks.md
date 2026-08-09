# 0004 — Training/inference isolation via a `reddit.tasks` composition root

## Status

Accepted

## Context

The `run` subcommand needs both training (`reddit.training`) and corpus
labelling (`reddit.inference`) — train, select the median seed, then label
the corpus with it. A naive implementation would have `reddit.training`
import `reddit.inference` directly to call the labeller after selection.
That would make the training stack (and anything depending only on it, such
as a future training-only deployment or a unit test) transitively depend on
the corpus-labelling machinery, and vice versa for anything depending on
`reddit.inference` alone.

## Decision

`reddit.training` never imports `reddit.inference` (and vice versa) —
enforced by `tach.toml`'s module-boundary declarations and checked in CI
(`uv run tach check`). Instead:

- `reddit.core.protocols.Labeller` declares the structural interface a
  labelling function must satisfy.
- `reddit.training.llms.run_family`/`reddit.training.bert.run_family`
  accept an optional `labeller: Labeller | None` parameter, calling it
  after `select_median` only if provided.
- `reddit.tasks.run.execute_run` — the **only** module allowed to import
  both `reddit.training` and `reddit.inference` (per `tach.toml`) — passes
  `reddit.inference.llms.label_corpus`/`reddit.inference.bert.label_corpus`
  as the concrete `labeller` when wiring the `run` subcommand.

## Consequences

- `reddit.training` is importable (e.g. in a unit test, or a
  training-only batch job) without pulling in `transformers`' corpus
  I/O paths or `reddit.inference`'s dependencies.
- Symmetrically, `reddit.inference` (e.g. the `predict` subcommand) never
  needs the training loop, dataset preparation, or PEFT configuration at
  all.
- Every place a labelling step is invoked from training code goes through
  the same narrow `Labeller` protocol, so `reddit.inference.bert.label_corpus`
  and `reddit.inference.llms.label_corpus` must both satisfy the exact same
  call signature even though their internals differ (the BERT
  implementation ignores `family`/`finetuning_method`, accepted only for
  interface parity).
- Any new pipeline wanting to combine training and inference must do so
  from `reddit.tasks`, or `tach check` will fail in CI.

## Alternatives considered

- **Direct import of `reddit.inference` from `reddit.training`** —
  rejected: creates a hard bidirectional-feeling coupling (training now
  "knows about" labelling) that the paper's own separation of fine-tuning
  from full-corpus inference does not require.
- **A shared "pipeline" superpackage merging both** — rejected: would
  remove the ability to depend on just one half, and blurs the module
  boundary `tach.toml` is meant to make explicit and enforceable.

## Source

`reddit.core.protocols.Labeller`, `reddit.tasks.run.execute_run`,
`tach.toml`. See [Implementation Design: System
Overview](../implementation_design/system_overview.md) and [Using the
CLI](../../how_to/using-the-cli.md).
