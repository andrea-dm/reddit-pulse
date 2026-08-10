# Deployment

## Packaging

Standard `hatchling`-backed PEP 621 package (`pyproject.toml`), source
layout under `src/reddit/`, installed editable in development
(`uv pip install -e .`). The console-script entry point
`reddit = "reddit.cli:main"` is the only distributed executable surface;
`python -m reddit` (`reddit.__main__`) is the equivalent module form.

Dependency groups (`[dependency-groups]` in `pyproject.toml`):

- **base** (`dependencies`) — the full runtime set (`torch`, `transformers`,
  `peft`, `bitsandbytes`, `accelerate`, `datasets`, ...), always installed.
- **`flash`** (optional extra) — `flash-attn`, requiring a CUDA build
  toolchain; installed explicitly, not part of the default install.
- **`dev`** — `pytest`, `hypothesis`, `ruff`, `pyright`, `tach`, `deptry`,
  `pandas-stubs`.
- **`lint`** — the CI lint toolchain (same as `dev` minus `pytest`/`hypothesis`).
- **`test`** — `pytest`/`hypothesis` plus the light-weight subset of the
  base dependencies the test suite actually needs without a GPU.

## Environments

This project runs on Azure ML compute, mounted identically at two paths
(`/home/azureuser/cloudfiles/code/Users/...` and `/mnt/batch/.../code/Users/...`
— the same underlying share). `reddit.core.config.load_config` resolves
every relative path in `config.yml` against the config file's own
directory specifically so the project is relocatable across these mount
points without editing `config.yml`.

## Runtime environment variables

Exported by `reddit.core.environment.prepare_environment`, called by
`reddit.cli.main` *before* any pipeline module (and therefore `torch`) is
imported — order matters, because CUDA device visibility must be fixed
before CUDA initializes:

| Variable | Source | Purpose |
|---|---|---|
| `CUDA_VISIBLE_DEVICES` | `--gpu` CLI flag | GPU selection for this process. |
| `PYTORCH_CUDA_ALLOC_CONF` | `environment.pytorch_alloc_conf` (default `"expandable_segments:True"`) | Reduces CUDA allocator fragmentation across the many large checkpoints a sweep loads. |
| `HF_HOME` | `environment.hf_home`, else `~/.cache/huggingface` | Hugging Face Hub cache root. |
| `HF_TOKEN` | `HUGGINGFACEHUB_API_TOKEN`/`HF_TOKEN` env var, or `environment.dotenv` | Hub authentication for gated model families. |

## Filesystem contract

Every directory under `config.yml`'s `paths:` section is created on first
run by `reddit.core.environment.bootstrap_directories`
(`mkdir(parents=True, exist_ok=True)`) — nothing needs to pre-exist except
the gold dataset and the corpus CSVs themselves. See [Getting Started:
Installation](../../getting_started/installation.md) for the full
directory list.

## CI

`.github/workflows/dependency-architecture-checks.yml` runs `ruff`, `tach
check`, `deptry`, `pyright` (full dependency install, strict mode) and
`pytest` on every pull request and push to `main` — see [Operations:
CI/CD](../operations/ci_cd.md) for the job-by-job breakdown.

## What this package does *not* deploy

There is no server, no API, no scheduled job, and no infrastructure-as-code
in this repository — `reddit` is a batch CLI tool invoked manually (or by
an external orchestration script) against a GPU host. There is accordingly
no `infrastructure.md`/`runbooks.md`/`incident_response.md`/
`monitoring_and_alerting.md` page in [Operations](../operations/index.md):
those DevOps concerns do not materially apply to a single-process batch CLI
with no deployed service to run books or page against.
