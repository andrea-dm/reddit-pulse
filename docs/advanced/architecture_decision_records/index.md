# Architecture Decision Records

This project records significant, hard-to-reverse design decisions as ADRs
— one Markdown file per decision, numbered with a 4-digit prefix and a
kebab-case slug (`NNNN-<slug>.md`), following a lightweight
MADR/Nygard-style template: Status, Context, Decision, Consequences,
Alternatives considered, and links to related ADRs where applicable.

**Accepted ADRs are immutable.** Superseding a decision means writing a new
ADR that references the old one; the old ADR's body is preserved and only
its Status line is updated to `Superseded by NNNN`.

The decisions below were reconstructed from the rationale already recorded
in source-code docstrings and comments (each ADR cites its source
location); none introduce a new decision beyond what the code already
reflects.

| # | Title | Status | Date |
|---|---|---|---|
| [0001](0001-qdora-plus-xqdora-plus-peft-recipe.md) | QDoRA+/xQDoRA+ as the PEFT recipe for decoder LLMs | Accepted | 2026-08-09 |
| [0002](0002-median-seed-selection.md) | Median-seed selection over best-seed selection | Accepted | 2026-08-09 |
| [0003](0003-shared-seed-loop-strategy-pattern.md) | Shared multi-seed loop via a `SeedStrategy` protocol | Accepted | 2026-08-09 |
| [0004](0004-training-inference-boundary-via-tasks.md) | Training/inference isolation via a `reddit.tasks` composition root | Accepted | 2026-08-09 |
