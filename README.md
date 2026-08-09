# reddit

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
  cli.py              # `reddit` entry point (run / train / predict)
  core/               # config schema, logging, env bootstrap, shared utils
  data/               # gold-dataset preparation (split -> DatasetDict)
  modeling/           # quantization + PEFT configs, metrics, weighted trainer
  training/           # multi-seed pipelines (llms, bert) + median selection
  inference/          # corpus labelling, checkpoint discovery (zip/dirs)
  tasks/              # CLI task modules (setup_*/execute_* pairs)
data/                 # subreddit CSVs + labelled.xlsx gold set
models/               # trained checkpoints ({model}_{method}_{seed}[.zip])
outputs/ results/ labelled/ logs/   # run artefacts
```

## Setup

```bash
uv venv && uv pip install -e .
# optional, needs a CUDA toolchain:
uv pip install flash-attn --no-build-isolation
```

Secrets (`HUGGINGFACEHUB_API_TOKEN`) are read from the dotenv file configured
under `environment.dotenv` in `config.yml`.

Quality gates (also run in CI — see `.github/workflows/`):

```bash
ruff check src/ tests/
pyright              # strict; config + venv pinned in pyrightconfig.json
tach check           # module boundaries (layering declared in tach.toml)
deptry src           # dependency hygiene (config in pyproject.toml)
pytest tests/ -q
```

## Usage

```bash
reddit run     --family gemma     --gpu 0        # train + select + label corpus
reddit run     --family test --limit 1 --gpu 0   # smoke test (1 seed)
reddit train   --family gemma_27  --gpu 0        # train + select only
reddit predict --family gemma_27 --directory models   # label with saved checkpoints
reddit predict --family bert     --directory models
```

`--family` picks a group from `families:` in `config.yml`; `--gpu` sets
`CUDA_VISIBLE_DEVICES`. The `*_part1` / `*_part2` families split a cohort
across two GPUs — run one command per GPU.

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

See [`LICENSE.md`](LICENSE.md).
