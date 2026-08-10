# 0003 — Shared multi-seed loop via a `SeedStrategy` protocol

## Status

Accepted

## Context

The decoder-LLM (PEFT) and BERT-family (full fine-tuning) training
pipelines need the same overall lifecycle — load and re-split the gold
dataset per seed, build a model, tokenize, train under early stopping,
evaluate, test, save, record metrics, select the median seed — but differ
in *how* the model is built (quantization/adapters vs. plain), tokenized
(padding side, max length), and optimized (LoRA+ vs. plain AdamW).
Per the module docstring of `reddit.training.loop`, these two lifecycles
were previously implemented as two near-identical ~200-line loops that had
already drifted apart in observable ways: different metrics-dump
directories, different progress logging, one rebuilding the model config
per seed and the other sharing a single mutated instance across seeds.

## Decision

Extract one shared loop, `reddit.training.loop.run_seeds`, parameterized by
a `reddit.training.loop.SeedStrategy` structural protocol (`build_model`,
`parameter_counts`, `tokenize`, `data_collator`, `training_arguments`,
`optimizers`). `reddit.training.llms.LlmSeedStrategy` and
`reddit.training.bert.BertSeedStrategy` implement only the six methods
that genuinely differ between the two kinds; everything else (the seed
loop, metrics JSONL assembly, GPU cleanup, error handling per seed) lives
once, in `run_seeds`.

## Consequences

- The two kinds cannot drift on lifecycle behavior (metrics schema,
  early-stopping wiring, crash isolation per seed) — a fix to `run_seeds`
  benefits both `training.llms` and `training.bert` simultaneously.
- Adding a third kind of fine-tuning strategy in the future requires only
  a new `SeedStrategy` implementation, not a new copy of the loop.
- The `SeedStrategy` protocol is a hard interface: any future kind must be
  expressible as those six methods, or the loop itself needs to grow a new
  extension point (a real, if currently theoretical, constraint).

## Alternatives considered

- **Keep the two independent loops, fix the drift manually** — rejected:
  does not prevent the same class of drift from recurring on the next
  change to either pipeline.
- **A single loop with `if kind == "llm": ... else: ...` branches inline**
  — rejected in favor of the strategy pattern: inline branching would grow
  `run_seeds` itself every time a kind-specific behavior changes, instead
  of confining that change to the relevant `SeedStrategy` implementation.

## Source

`reddit.training.loop` (module docstring and `SeedStrategy` protocol),
`reddit.training.llms.LlmSeedStrategy`,
`reddit.training.bert.BertSeedStrategy`. See [Implementation Design:
System Overview](../implementation_design/system_overview.md#two-independent-execution-paths-one-shared-spine).
