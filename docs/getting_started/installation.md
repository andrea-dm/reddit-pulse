# Installation

## Prerequisites

- **Python 3.12+** (`requires-python = ">=3.12"` in `pyproject.toml`).
- **[uv](https://docs.astral.sh/uv/)** for dependency management; every
  command below assumes it is on `PATH`.
- **A CUDA-capable GPU** for anything beyond `--help`/config validation.
  `reddit.core.environment.prepare_environment` exports
  `CUDA_VISIBLE_DEVICES` from `--gpu`, and both the LLM (4-bit
  quantization + PEFT) and BERT (bf16) training/inference paths assume a
  CUDA device is present; there is no CPU-only fallback path exercised in
  production use (only `reddit.inference.corpus._prepare_model_device`
  falls back to CPU, primarily so the code is testable off-GPU).
- **A Hugging Face account with access to the gated model families** you
  intend to fine-tune (Gemma-2, Llama-3 require accepting their model
  licenses on the Hub).

## Install

```bash
uv venv && uv pip install -e .
```

This installs the base runtime dependency set: `torch`, `transformers`,
`peft`, `bitsandbytes`, `accelerate`, `datasets`, `huggingface-hub`,
`pydantic`, `pyyaml`, `python-dotenv`, `orjson`, `scikit-learn`, `pandas`,
`openpyxl` (the `pandas.read_excel` engine for the gold dataset) and `tqdm`.

### Optional: flash-attention

`reddit.modeling.loading.llm_model_args` resolves the attention
implementation and the half-precision dtype against the visible GPU
(`reddit.modeling.loading.detect_device_profile`):

| GPU | dtype | attention |
| :-- | :-- | :-- |
| Ampere or newer (`sm_80+`, e.g. A100/H100) with `flash-attn` installed | `bfloat16` | `flash_attention_2` |
| Ampere or newer without `flash-attn` | `bfloat16` | `sdpa` |
| Turing/Volta (`sm_75`/`sm_70`, e.g. Tesla T4/V100) | `float16` | `sdpa` |
| no CUDA device | `float32` | `sdpa` |

Gemma-2 was trained in bfloat16 and is known to overflow to NaN in float16,
so on a Turing box the `gemma` family is suitable for smoke tests only; the
A100 production target resolves to bfloat16 and is unaffected. Qwen2.5 and
Llama-3 train fine in float16.

`flash-attn` therefore only matters on Ampere-or-newer GPUs — its kernels
do not run on Turing at all, so on a T4 the package is neither needed nor
usable. It needs a CUDA toolchain at *build* time, so it is not a plain
dependency; install it explicitly on such a box after the base install:

```bash
uv pip install flash-attn --no-build-isolation
```

The detected profile is logged at the start of every run
(`Device profile: cuda sm_75: float16, sdpa attention.`), so a run that
silently fell back to SDPA is visible in the log. Note that the
`training.arguments.bf16`/`fp16` flags in `config.yml` must agree with the
device: `bf16: true` on a GPU without native bfloat16 is rejected before
the first seed starts (`ConfigError`).

## Secrets

The Hugging Face Hub token is read from the environment
(`HUGGINGFACEHUB_API_TOKEN` or `HF_TOKEN`) or from a dotenv file pointed to
by `environment.dotenv` in `config.yml`
(`reddit.core.environment.prepare_environment`). If neither is set, a
warning is logged and gated models (Gemma-2, Llama-3) will fail to
download; ungated models still work. The token is exported as `HF_TOKEN`
for the process tree rather than persisted via `huggingface_hub.login()`,
because `HF_HOME` may point at a world-readable shared mount (see
[Security](../advanced/implementation_design/security.md)).

## Configuration and directories

A single `config.yml` at the project root (or the path passed via
`-c/--config`) declares every path, model family and hyperparameter — see
[Configuring a Run](../how_to/configuring-a-run.md). Relative paths in
`config.yml` are resolved against the directory containing the config
file, so the project is relocatable (this matters on Azure ML compute,
where the same share is mounted at multiple paths). Every directory under
`paths:` is created on first run by
`reddit.core.environment.bootstrap_directories`; you do not need to
pre-create `cache/`, `dumps/`, `outputs/`, `results/`, `models/`,
`labelled/` or `logs/`.

## Platform notes

- Developed and CI-tested on Linux (`ubuntu-latest` in
  `.github/workflows/dependency-architecture-checks.yml`); the project has
  no documented Windows/macOS support path (`bitsandbytes` 4-bit
  quantization alone makes macOS unsuitable for the LLM path).
- The package spawns no worker processes of its own; the `DataLoader`
  workers configured by `training.arguments.dataloader_num_workers` use
  the platform default start method (`fork` on Linux), which is safe
  because they never touch CUDA.

## Verify the install

```bash
uv run reddit --help
```

should print the `run`/`train`/`predict` subcommands without touching the
network or a GPU. Proceed to the [Quickstart](quickstart.md) for a first
real (small) end-to-end run.
