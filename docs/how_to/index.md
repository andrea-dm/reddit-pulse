# How To

This tier is for readers who know what they want the pipeline to do and
need the shortest path there, without implementation-level detail. If you
have not installed the package yet, start at
[Getting Started](../getting_started/index.md) instead.

## Capability map

| Page | Covers |
|---|---|
| [Using the CLI](using-the-cli.md) | The `reddit run`/`train`/`predict` subcommands and their flags. |
| [Configuring a Run](configuring-a-run.md) | `config.yml`: paths, labels, model families, training/inference knobs. |
| [Preparing the Gold Dataset](preparing-the-gold-dataset.md) | Turning the hand-labelled Excel file into stratified train/val/test splits. |
| [Fine-Tuning Decoder LLMs](fine-tuning-decoder-llms.md) | QDoRA+/xQDoRA+ PEFT fine-tuning of Gemma-2/Llama-3/Qwen2.5 classifiers. |
| [Fine-Tuning BERT Encoders](fine-tuning-bert-encoders.md) | Full fine-tuning of BERT-base/FinBERT/InflaBERT classifiers. |
| [Labelling the Corpus](labelling-the-corpus.md) | Running a trained checkpoint over the full per-subreddit corpus. |

## Where to go next

- New to the project? Go to [Getting Started](../getting_started/index.md)
  for installation and a first run.
- Need implementation detail, the methodology behind a design choice, or an
  operational runbook? Go to
  [Advanced](../advanced/implementation_design/index.md) — every page below
  links to its matching Advanced counterpart.
