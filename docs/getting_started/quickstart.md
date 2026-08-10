# Quickstart

This walks through the smallest real invocation: fine-tuning one small
model (`qwen2.5_0.5b`, the `test` family in `config.yml`) on a single seed,
then labelling the corpus with the selected checkpoint.

## Prerequisites

- Completed [Installation](installation.md).
- A GPU visible to the process.
- `data/labelled.xlsx` (the hand-labelled gold dataset) present at the path
  configured under `dataset.path` in `config.yml`.
- The per-subreddit corpus CSVs present under `paths.reddit_dir`, named
  `{subreddit}_final_jae.csv` / `{subreddit}_comments_final_jae.csv` for
  `economy`, `economics`, `wallstreetbets` (see
  [Labelling the Corpus](../how_to/labelling-the-corpus.md) for the exact
  schema). Both `data/labelled.xlsx` and the corpus CSVs are inputs this
  package consumes — it does not construct them (see [System
  Overview](../advanced/implementation_design/system_overview.md#scope-boundary)).

## Run

```bash
reddit run --family test --limit 1 --gpu 0
```

- `--family test` selects the `test` family (`qwen2.5_0.5b` only) from
  `config.yml`.
- `--limit 1` fine-tunes on only the first configured seed instead of the
  full list — fast, but not representative of the paper's multi-seed
  median-selection methodology (see
  [Fine-Tuning Decoder LLMs](../how_to/fine-tuning-decoder-llms.md)).
- `--gpu 0` exports `CUDA_VISIBLE_DEVICES=0`.

## What happens

1. `reddit.cli.main` loads and validates `config.yml`, creates every
   `paths:` directory, configures logging to
   `logs/qwen2.5_0.5b_test.log`, and exports the GPU/HF environment
   variables.
2. `reddit.training.llms.run_family` loads the gold dataset, fine-tunes
   `qwen2.5_0.5b` with the QDoRA+ and xQDoRA+ recipes (both default
   methods for a family that does not override `finetuning_methods`), and
   writes a checkpoint plus JSONL metrics under `models/` and
   `outputs/test_{date}/`.
3. `reddit.training.selection.select_median` picks the (only, with
   `--limit 1`) checkpoint and records the selection in
   `results/selected_models_metrics.jsonl`.
4. `reddit.inference.llms.label_corpus` labels every configured subreddit's
   submissions and comments with the selected checkpoint, writing
   `labelled/qwen2.5_0.5b_submissions_qdora_predicted_labels.csv` (and the
   `xqdora`/comments equivalents) and merging the results into
   `results/all_final_jae_test.csv` / `results/all_comments_final_jae_test.csv`.

## Expected output

The process exits `0` and logs a line of the form:

```text
Selected model: `qwen2.5_0.5b_qdora_107935903` [0.812345]
```

(the exact score varies by seed/run) followed by a labelling summary. A
non-zero exit means either a `ConfigError` (printed as a usage message) or
that the run finished without producing any checkpoint — check
`logs/qwen2.5_0.5b_test.log` for details.

## Train-only variant

```bash
reddit train --family test --limit 1 --gpu 0
```

runs the same pipeline but stops after median-seed selection, skipping
corpus labelling — useful when you only want a checkpoint. Label it later
with:

```bash
reddit predict --family test --directory models --gpu 0
```

See [Using the CLI](../how_to/using-the-cli.md) for the full subcommand
reference.
