## Release X.Y.Z — <!-- one-line summary -->

Milestone: %"<!-- milestone name -->"

### Version

| | Value |
|---|---|
| Previous version | <!-- e.g. 0.3.0 --> |
| New version | <!-- e.g. 0.4.0 --> |
| Bump type | <!-- MAJOR / MINOR / PATCH --> |

### Description

<!-- 2-3 sentence overview based only on verified changes. -->

### Changes

<!-- Paste the changelog excerpt for this version. -->

### Deprecations

<!-- List deprecated features/APIs and their recommended replacements, or write `None`. -->

None

### Breaking Changes

<!-- List confirmed breaking changes, or write `None`. -->

None

### Migration Guide

<!--
  Required when Breaking Changes is not `None`.
  Describe step-by-step what consumers must do to upgrade.
  Delete this section if there are no breaking changes.
-->

### Security Fixes

<!-- List security-related fixes with CVE references if applicable, or write `None`. -->

None

### Risk Assessment

<!-- Overall risk level for this release. -->

| Dimension | Level | Notes |
|---|---|---|
| Scope of changes | <!-- Low / Medium / High --> | <!-- e.g. "3 modules touched" --> |
| Public API impact | <!-- None / Compatible / Breaking --> | <!-- e.g. "new public class added" --> |
| Rollback complexity | <!-- Low / Medium / High --> | <!-- e.g. "revert tag + redeploy docs" --> |

### Quality

| Check | Status | Evidence |
|---|---|---|
| IntegrationChecker verdict | <!-- GO / NO-GO / Not available --> | <!-- gate summary or "accepted from prior run" --> |
| Ruff (lint + format) | <!-- Passed / Failed / Not run / Unknown --> | <!-- observed evidence --> |
| Pyright | <!-- Passed / Failed / Not run / Unknown --> | <!-- observed evidence --> |
| Pytest | <!-- Passed / Failed / Not run / Unknown --> | <!-- coverage % --> |
| SonarQube Quality Gate | <!-- OK / ERROR / WARN / Unknown --> | <!-- gate status + condition summary --> |
| Dependency audit (`pip-audit`) | <!-- Passed / Failed / Not run / Unknown --> | <!-- vulnerability count or "clean" --> |
| Dependency consistency (`deptry`) | <!-- Passed / Failed / Not run / Unknown --> | <!-- violation count or "consistent" --> |
| Lock file (`uv lock --check`) | <!-- Consistent / Stale / Unknown --> | <!-- observed evidence --> |
| Docs build | <!-- Passed / Failed / Not run / Unknown --> | <!-- observed evidence --> |

### Compatibility

| Dimension | Value |
|---|---|
| `requires-python` | <!-- value from pyproject.toml --> |
| CI test matrix | <!-- Python versions from .gitlab-ci.yml --> |

### Pre-merge Checklist

- [ ] Version bumped in `pyproject.toml`
- [ ] `CHANGELOG.md` updated
- [ ] `README.md` updated (if applicable)
- [ ] `CONTRIBUTING.md` verified (if applicable)
- [ ] `docs/` scanned for stale versions
- [ ] Snippet inclusions verified
- [ ] Lock file consistent
- [ ] IntegrationChecker verdict: GO
- [ ] CI pipeline green on source branch

### Post-merge Actions

- [ ] Tag `vX.Y.Z` created and pushed
- [ ] Tag pipeline completed successfully
- [ ] Versioned docs deployed (`mike deploy` + `stable` alias)
- [ ] GitLab Release created with changelog notes
- [ ] Milestone closed (if applicable)
