# Implementation Design

## Abstract

`reddit` is a seven-subpackage Python library (`core`, `data`, `modeling`,
`training`, `inference`, `tasks`, plus the `cli` entry point) that
implements two stages of the pipeline described in Del Monaco, Longo,
Marcucci & Tafani, "Reddit's 'pulse' on US inflation: forecasting with
large language models" (Banca d'Italia Questioni di Economia e Finanza,
No. 1028, June 2026): (1) multi-seed fine-tuning of directional
inflation-expectation classifiers (decoder LLMs via QDoRA+/xQDoRA+ PEFT,
BERT-family encoders via full fine-tuning) over a hand-labelled gold
dataset, with median-seed selection; and (2) full-corpus batched inference,
labelling every submission and comment in the r/economy, r/Economics and
r/wallstreetbets corpora with the resulting checkpoints. A `tach.toml`
layering contract keeps the training and inference stacks mutually
independent, meeting only at the `reddit.tasks` composition root that the
CLI dispatches into.

## Introduction

### Purpose and scope

This report documents `src/reddit/` as it exists in this repository: what
each subpackage is responsible for, its public contracts, its data model,
its runtime behavior, how it is packaged and deployed, what it observes
about its own execution, and its security posture. Every claim in these
pages is traceable to source code, configuration (`config.yml`,
`pyproject.toml`, `tach.toml`, `pyrightconfig.json`) or an observable test
artefact under `tests/`.

### Scope boundary

**This is the single most important fact about this report.** The paper
above describes an end-to-end pipeline: corpus acquisition (Pushshift +
PRAW), a keyword-lexicon filter, an LLM-based geographic classifier, a
comment-timeliness filter, a human-in-the-loop gold-label construction
process, fine-tuning, full-corpus inference, and finally aggregation of the
per-row directional labels into backward-looking moving-average monthly
regressors used in a separate inflation-nowcasting exercise. **`src/reddit`
implements only the fine-tuning and full-corpus-inference stages** —
concretely:

| Paper stage | Implemented in `src/reddit`? | Where (if not here) |
|---|---|---|
| Pushshift/PRAW corpus acquisition | No | Not present in this repository. |
| Keyword-lexicon filter | No | Possibly `legacy/src/data_processing.py` (not audited as part of this package). |
| LLM geographic (US/non-US/unclear) classification | No | Possibly `notebooks/reddit_agentic_filtering.ipynb`. |
| Comment two-week timeliness filter | No | Same as above. |
| Human/LLaMA-70B/LLaMA-8B/ChatGPT-assisted gold-label construction | No | Produces `data/labelled.xlsx`, which this package only *consumes* (`reddit.data.preparation`). |
| Gold-dataset preparation (split) | **Yes** | `reddit.data.preparation`. |
| QDoRA+/xQDoRA+ fine-tuning of decoder LLMs | **Yes** | `reddit.training.llms`, `reddit.modeling.peft`. |
| Full fine-tuning of BERT-family encoders | **Yes** | `reddit.training.bert`. |
| Median-seed selection | **Yes** | `reddit.training.selection`. |
| Full-corpus batched inference | **Yes** | `reddit.inference.*`. |
| LLaMA-70B zero-shot benchmark labelling | No | Not present; only the fine-tuned SLM checkpoints are labelled by this package. |
| Backward-looking MA signal construction (1–360-day windows, 288-indicator grid) | No | Not present in this repository. |
| Inflation nowcasting/forecasting regressions | No | Explicitly out of scope for this package (paper's discussion of downstream use, per project brief). |

Do not treat this repository as a full replication package for the paper's
empirical results without independently locating and auditing the stages
marked "No" above.

### Report structure

- [System Overview](system_overview.md) — architecture and bounded contexts.
- [Components](components.md) — per-subpackage technical contracts.
- [Interfaces](interfaces.md) — public APIs, protocols, configuration schema.
- [Data Model](data_model.md) — entities, encodings, file-format invariants.
- [Runtime Behavior](runtime_behavior.md) — concurrency, lifecycles, crash safety.
- [Deployment](deployment.md) — packaging, environments, infra contracts.
- [Observability](observability.md) — logging, metrics, alerting posture.
- [Security](security.md) — secrets handling, threat model, controls.
