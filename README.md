# Reddit pulse

Fine-tunes decoder LLMs (QDoRA+/xQDoRA+ PEFT on 4-bit bases) and BERT-family
encoders as three-way **directional inflation-expectation classifiers**
(UP/DOWN/NEUTRAL — not generic sentiment) over a hand-labelled gold dataset
(`data/labelled.xlsx`), selects the median-performing seed per model, and
labels the full corpus of submissions and comments from r/economy,
r/Economics and r/wallstreetbets.

This is the fine-tuning and full-corpus-inference stage of the pipeline
described in Del Monaco, Longo, Marcucci & Tafani, ["Reddit's 'pulse' on US
inflation: forecasting with large language models"](https://www.bancaditalia.it/pubblicazioni/qef/)
(Banca d'Italia Questioni di Economia e Finanza, No. 1028, June 2026). It
does **not** implement the paper's corpus-filtering, seed-label-construction
or signal-aggregation stages — see the [full documentation](docs/index.md)
for the exact stage-by-stage scope boundary before relying on this
repository as a full replication package.

## Layout

```
config.yml            # unified configuration (paths, training, families)
mkdocs.yml, docs/      # documentation site (build/serve instructions below)
src/reddit/
  cli.py              # `reddit` entry point (run / train / predict / upload)
  core/               # config schema, logging, env bootstrap, shared utils
  data/               # gold-dataset preparation (split -> DatasetDict)
  modeling/           # quantization + PEFT configs, metrics, weighted trainer
  training/           # multi-seed pipelines (llms, bert) + median selection
  inference/          # corpus labelling, checkpoint discovery (zip/dirs)
  hub/                # model cards, staging and upload to the Hugging Face Hub
  tasks/              # CLI task modules (setup_*/execute_* pairs)
data/                 # subreddit CSVs + labelled.xlsx gold set
models/               # trained checkpoints ({model}_{method}_{seed}[.zip])
outputs/ results/ labelled/ logs/   # run artefacts
```

## Setup

```bash
uv venv && uv pip install -e .
# optional, Ampere-or-newer GPUs only (needs a CUDA toolchain to build;
# older GPUs such as the T4 use SDPA attention automatically):
uv pip install flash-attn --no-build-isolation
```

Secrets (`HUGGINGFACEHUB_API_TOKEN`) are read from the dotenv file configured
under `environment.dotenv` in `config.yml`.

Quality gates (also run in CI — see `.github/workflows/`):

```bash
ruff check           # paths come from [tool.ruff] include in pyproject.toml
pyright              # strict; config + venv pinned in pyrightconfig.json
tach check           # module boundaries (layering declared in tach.toml)
deptry src           # dependency hygiene (config in pyproject.toml)
pytest tests/ -q
```

## Usage

```bash
reddit run     --family gemma       --gpu 0        # train + select + label corpus
reddit run     --family test --limit 1 --gpu 0     # smoke test (1 seed)
reddit train   --model gemma2_27b   --gpu 0        # train + select only, one model
reddit predict --model gemma2_27b --directory models   # label with saved checkpoints
reddit predict --family bert        --directory models
reddit run     --all-families       --gpu 0        # every model in every family
reddit upload  --model gemma2_2b    --dry-run       # stage Hub repos under outputs/hub/, push without --dry-run
```

`--family`/`--model` accept one or more values and pick from `families:` in
`config.yml`; `--all-families` (alias `--all-models`) runs everything.
`--gpu` sets `CUDA_VISIBLE_DEVICES` — split a cohort across GPUs by giving
each invocation a disjoint `--model` subset.

## Changelog

See [`CHANGELOG.md`](CHANGELOG.md).


## Published models

The selected checkpoints are public on the Hugging Face Hub, grouped in the
[**Reddit Infla-pulse** collection](https://huggingface.co/collections/andreadm/reddit-infla-pulse).
Each repository holds the weights (a PEFT adapter for the decoder LLMs), a
model card with usage snippets, the per-seed evaluation tables and the
training configuration that produced it.

| Base model | QDoRA+ | xQDoRA+ |
|---|---|---|
| Gemma 2 2B | [`reddit-pulse-gemma2_2b-qdora`](https://huggingface.co/andreadm/reddit-pulse-gemma2_2b-qdora) | [`reddit-pulse-gemma2_2b-xqdora`](https://huggingface.co/andreadm/reddit-pulse-gemma2_2b-xqdora) |
| Llama 3.2 1B | [`reddit-pulse-llama3.2_1b-qdora`](https://huggingface.co/andreadm/reddit-pulse-llama3.2_1b-qdora) | [`reddit-pulse-llama3.2_1b-xqdora`](https://huggingface.co/andreadm/reddit-pulse-llama3.2_1b-xqdora) |
| Llama 3.2 3B | [`reddit-pulse-llama3.2_3b-qdora`](https://huggingface.co/andreadm/reddit-pulse-llama3.2_3b-qdora) | [`reddit-pulse-llama3.2_3b-xqdora`](https://huggingface.co/andreadm/reddit-pulse-llama3.2_3b-xqdora) |
| Qwen2.5 0.5B | [`reddit-pulse-qwen2.5_0.5b-qdora`](https://huggingface.co/andreadm/reddit-pulse-qwen2.5_0.5b-qdora) | [`reddit-pulse-qwen2.5_0.5b-xqdora`](https://huggingface.co/andreadm/reddit-pulse-qwen2.5_0.5b-xqdora) |
| Qwen2.5 1.5B | [`reddit-pulse-qwen2.5_1.5b-qdora`](https://huggingface.co/andreadm/reddit-pulse-qwen2.5_1.5b-qdora) | [`reddit-pulse-qwen2.5_1.5b-xqdora`](https://huggingface.co/andreadm/reddit-pulse-qwen2.5_1.5b-xqdora) |
| InflaBERT (full fine-tune) | [`reddit-pulse-bert`](https://huggingface.co/andreadm/reddit-pulse-bert) | |

`reddit upload` stages and publishes these repositories; see
[Publishing to the Hub](docs/how_to/publishing-to-the-hub.md).

## Documentation

Full documentation — getting started, task-oriented how-to guides, and an
advanced tier (implementation design, architecture decision records,
operations) — lives under `docs/` and is not yet hosted; build and browse
it locally:

```bash
uv sync --only-group docs --no-install-project
uv run --no-sync mkdocs serve   # http://127.0.0.1:8000
```

See [`docs/index.md`](docs/index.md) to start reading without building the
site, and [`CONTRIBUTING.md`](CONTRIBUTING.md) for the full contributor
workflow (quality gates, commit style, docs verification).

## Citation

If you use this code, please cite the paper it implements:

```bibtex
@techreport{delmonaco2026reddit,
  title  = {Reddit's `pulse' on {US} inflation: forecasting with large language models},
  author = {Del Monaco, Andrea and Longo, Luigi and Marcucci, Juri and Tafani, Irene},
  institution = {Banca d'Italia},
  series = {Questioni di Economia e Finanza (Occasional Papers)},
  number = {1028},
  year   = {2026},
  month  = jun,
  doi    = {10.32057/0.QEF.2026.1028},
}
```

## License

MIT; see [`LICENSE.md`](LICENSE.md). [`NOTICE.md`](NOTICE.md) states how the license relates to the paper.
