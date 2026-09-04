# 0001 — QDoRA+/xQDoRA+ as the PEFT recipe for decoder LLMs

## Status

Accepted

## Context

Fine-tuning decoder LLMs ranging from 0.5B to 27B parameters, across
multiple model families and multiple random seeds, needs to fit on the
available GPU budget while remaining close to full-fine-tuning accuracy.
Plain LoRA under-performs full fine-tuning at matched trainable-parameter
budgets in the published literature; a memory-only solution (quantization
alone) does not address that accuracy gap; and a single fixed adapter rank
does not let larger models trade some accuracy for materially faster
training when needed.

## Decision

Fine-tune every decoder LLM with a two-tier recipe registered in
`reddit.modeling.peft.peft_config`:

- **QDoRA+**: 4-bit NF4 quantization (`build_quantization_config`) + DoRA
  adapters (`use_dora=True`) on the attention projections, rank `r=32`,
  paired with a LoRA+ optimizer (`loraplus_lr_ratio=5`) in
  `reddit.training.llms.LlmSeedStrategy.optimizers`.
- **xQDoRA+**: identical recipe at rank `r=4` (an 8x/2³ reduction in
  adapter parameters), offered as a same-model efficiency-oriented
  alternative, not a fallback for a different (smaller/worse) model.

Both methods are fine-tuned for every LLM family unless a family overrides
`finetuning_methods` in `config.yml`.

## Consequences

- Every decoder-LLM family gets two checkpoints per seed (QDoRA+ and
  xQDoRA+) unless explicitly restricted, roughly doubling training wall
  time per family but yielding a like-for-like efficiency comparison
  point.
- `reddit.core.config.FineTuningMethod` reserves a third value,
  `"adalora"`, with no corresponding entry in `peft_config` — selecting it
  raises `reddit.core.errors.UnsupportedMethodError`
  (`reddit.training.llms.run_family`). This is intentional: AdaLoRA was
  evidently considered at the config-schema level but is not part of the
  accepted recipe.
- `reddit.modeling.loading.llm_model_args` (dtype, quantization,
  attention implementation, `device_map="auto"`) is the single owner of
  *how* a checkpoint is materialized, shared by both the training and
  inference paths, so the quantization/attention configuration cannot
  drift between the two. The dtype and attention implementation are
  resolved against the visible GPU (`DeviceProfile`): bfloat16 +
  flash-attention 2 on Ampere or newer, fp16 + SDPA on Turing — the
  recipe itself (NF4, DoRA, LoRA+) is device-independent.

## Alternatives considered

- **Plain LoRA** (no quantization, no weight decomposition) — rejected:
  higher memory footprint at the target model sizes, and a known accuracy
  gap versus full fine-tuning that DoRA specifically addresses.
- **Full fine-tuning of every decoder LLM** — rejected on compute grounds
  at the 9B–27B end of the roster; reserved instead for the BERT-family
  encoders (see [ADR 0002](0002-median-seed-selection.md) context and
  `reddit.training.bert.BertSeedStrategy`).
- **A single adapter rank for every model** — rejected in favor of offering
  both QDoRA+ and xQDoRA+, letting the rank/wall-time trade-off be made
  per model rather than fixed globally.

## Source

`reddit.modeling.peft`, `reddit.modeling.loading`,
`reddit.training.llms.LlmSeedStrategy`,
`reddit.core.config.FineTuningMethod`.
