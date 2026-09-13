# Contributing

## Setup

```bash
uv venv && uv pip install -e .
# optional, needs a CUDA toolchain:
uv pip install flash-attn --no-build-isolation
```

Secrets (`HUGGINGFACEHUB_API_TOKEN`) are read from the dotenv file configured
under `environment.dotenv` in `config.yml`; never commit tokens or `.env`
files.

## Quality gates

Every change must pass the same checks CI runs
(`.github/workflows/dependency-architecture-checks.yml`):

```bash
ruff check               # lint + import order (paths from [tool.ruff] include)
pyright                  # strict type checking (pyrightconfig.json)
tach check                # module-boundary layering (tach.toml)
deptry src                 # dependency hygiene (pyproject.toml [tool.deptry])
pytest tests/ -q            # unit / integration / contracts suites
```

`tach.toml` declares the allowed import layering between `core`, `data`,
`modeling`, `training`, `inference`, `tasks` and `cli`; in particular,
`training` must never import `inference` (and vice versa) — their only
meeting point is `reddit.tasks`, the composition root. Run `uv run tach
check` before opening a change that adds a new cross-package import.

## Documentation

Public classes, methods and functions are documented with Google-style
docstrings, rendered via `mkdocstrings` into the API reference
(`docs/api.md`). When you add or change public API surface:

- Add/update the docstring (`Args`, `Returns`, `Raises`, `Notes` as
  applicable).
- If the change affects a documented workflow, update the matching
  `docs/how_to/` and/or `docs/advanced/` page.
- Verify the site builds:
  `uv sync --only-group docs --no-install-project && uv run --no-sync mkdocs build --strict`
  (the `docs` group is deliberately independent of the heavy runtime
  dependency set — `mkdocstrings` resolves `src/reddit/**` via static
  analysis, not by importing it).

## Commit style

Commit subjects follow a `type(scope): summary` convention (see `git log`
for examples): `feat`, `fix`, `refactor`, `chore`, each optionally scoped to
a subpackage, e.g. `fix(tests): decouple config/dataset tests from the
gitignored gold dataset`. Keep subjects imperative and under ~72 characters;
use the body for the "why" when it is not obvious from the diff.

## Pull requests

- Keep changes scoped to one logical concern.
- Update `CHANGELOG.md` under `[Unreleased]` (or the next version heading)
  for user-visible changes.
- Do not commit generated artefacts (`cache/`, `dumps/`, `outputs/`,
  `results/`, `models/`, `logs/`, `labelled/`) or data files under `data/`.
