# Operations

This section covers the operational surface of `reddit`: what feeds into
the pipeline, what it feeds out to, the detailed call-chain of every public
entry point, and the CI process that guards changes to it.

## Runtime pipelines

- **[Upstreams](upstreams.md)** — the gold dataset and per-subreddit corpus
  CSVs this package consumes, and (explicitly) what upstream
  filtering/acquisition steps it does *not* perform.
- **[Downstreams](downstreams.md)** — the answers CSVs, checkpoints and
  metrics artefacts this package produces, and what (outside this
  repository) consumes them.
- **[Workflows](workflows.md)** — detailed, code-level walkthroughs of
  every public class, method and function exposed by `reddit`, starting
  from each CLI entry point and tracing the call chain down to the lowest
  relevant layer.

## DevOps / SRE

- **[CI/CD](ci_cd.md)** — the `ruff`/`tach`/`deptry`/`pyright`/`pytest`
  quality gate that runs on every pull request and push to `main`.

No other DevOps page (release engineering, infrastructure-as-code,
runbooks, incident response, monitoring/alerting) is included: this
repository has no versioned release process beyond `CHANGELOG.md`, no
infrastructure-as-code, and no deployed service to run books, page on-call
against, or monitor in the SLI/SLO sense — see [Implementation Design:
Deployment](../implementation_design/deployment.md#what-this-package-does-not-deploy)
for why.

## Where to go next

New to the pipeline? Start at [How To](../../how_to/index.md) for
task-oriented usage before reading the detailed workflows here.
