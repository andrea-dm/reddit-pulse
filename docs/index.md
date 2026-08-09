# reddit

`reddit` fine-tunes decoder LLMs (QDoRA+/xQDoRA+ PEFT) and BERT-family
encoders as three-way directional inflation-expectation classifiers
(UP/DOWN/NEUTRAL) over a hand-labelled gold dataset, selects the
median-performing seed per model, and labels the full corpus of submissions
and comments from r/economy, r/Economics and r/wallstreetbets.

This package implements the **fine-tuning and full-corpus inference**
stages of the pipeline described in Del Monaco, Longo, Marcucci & Tafani,
["Reddit's 'pulse' on US inflation: forecasting with large language
models"](https://www.bancaditalia.it/pubblicazioni/qef/) (Banca d'Italia
Questioni di Economia e Finanza, No. 1028, June 2026). It does **not**
implement the paper's corpus-filtering, seed-label-construction, or
signal-aggregation stages — see [Implementation Design: System
Overview](advanced/implementation_design/system_overview.md#scope-boundary)
for the exact stage-by-stage boundary before relying on this repository as
a full replication package.

## Where to start

This site is organised in three tiers, by audience:

- **[Getting Started](getting_started/index.md)** — install the package and
  run a first end-to-end smoke test. Start here if you are new to the
  project.
- **[How To](how_to/index.md)** — task-oriented guides for each capability
  (configuring a run, fine-tuning, labelling the corpus, ...). Start here if
  you know what you want to do and need the shortest path there.
- **[Advanced](advanced/implementation_design/index.md)** — implementation
  design, architecture decision records, and operational runbooks. Start
  here if you are reviewing, auditing, or extending the codebase.

The **[API Reference](api.md)** documents every public class, method and
function; every narrative page below links back into it.
