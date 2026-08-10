# 0002 — Median-seed selection over best-seed selection

## Status

Accepted

## Context

Every (model, method) combination is fine-tuned across many random seeds
(18 configured in `config.yml`), each re-splitting the gold dataset
independently. A single checkpoint must be chosen to represent that
(model, method) combination for corpus labelling. Choosing the
best-performing seed reports an optimistic, best-case outcome that may not
be representative of the model's typical behavior under data-split and
initialization variance.

## Decision

`reddit.training.selection.select_median` ranks every seed that produced a
finite test `f1_weighted` score and keeps the **upper median**
(`ranked[len(ranked) // 2]`, matching `statistics.median_high` semantics)
— not the maximum. Every other seed's checkpoint directory is deleted
(`shutil.rmtree`) once the median is chosen, and the choice is recorded in
`results/selected_models_metrics.jsonl`.

Seeds with a non-finite score (`NaN`) are excluded from ranking entirely,
rather than being treated as a score of zero or aborting the run — a
`NaN` previously matched no seed under `statistics.median_high` and raised
an unhandled `IndexError`, aborting the entire family sweep over one bad
seed.

## Consequences

- Reported/labelled-corpus performance is representative-typical rather
  than best-case, which is the right property for a checkpoint that will
  go on to label a multi-year, multi-million-row corpus.
- Non-selected checkpoints are deleted, not archived — bounding disk usage
  across a sweep of many (model, method, seed) combinations, at the cost
  of being unable to later inspect a non-median seed's checkpoint without
  retraining it.
- The selection criterion (`f1_weighted` on the held-out test split) is
  the same axis used to select `metric_for_best_model` during training
  (`config.training.arguments.metric_for_best_model`), so there is no
  metric mismatch between within-run early-stopping/checkpointing and
  across-run seed selection.

## Alternatives considered

- **Best-seed selection** — rejected: risks overstating typical
  reliability, and is more sensitive to a single favorable data split.
- **Ensembling all seeds** — rejected (not implemented): would multiply
  corpus-labelling compute cost by the seed count for a benefit not
  established as necessary for this task; also complicates the answers-CSV
  schema (one label column per checkpoint vs. per (model, method)).
- **Keeping every seed's checkpoint on disk** — rejected on storage
  grounds at the scale of this sweep (many models × two methods × up to 20
  seeds — `config.training.seeds` in `config.yml`, 15 of which are
  currently active).

## Source

`reddit.training.selection.select_median`,
`reddit.modeling.metrics.compute_metrics`.
