---
name: docs-drift-detection
description: Detect drift between Python source code, Google-style docstrings, and the MkDocs site under docs/, and persist the findings as a versioned drift manifest. Read-only counterpart of the DocsReviewer agent's full authoring mode (docs_mode=full); this skill never rewrites docstrings, source, or docs/ pages. Use when you only need a drift report, e.g. an IntegrationChecker D1 gate.
argument-hint: "[target file or module] [optional input_manifest path]"
arguments: target input_manifest
context: fork
user-invocable: true
disable-model-invocation: true
allowed-tools: Read Grep Glob Bash(python3 *) Bash(git branch *) Bash(git rev-parse *)
---

# docs-drift-detection skill

A read-only documentation-drift detector that produces (or extends) a
versioned **drift manifest** under
`./manifests/skills/docs-drift-detection/`. The manifest is the only
side-effect: never edit docstrings, source code, or any page under `docs/`.

This skill is the forked twin of the `DocsReviewer` agent
(`docs_mode=drift`). Use the agent in `docs_mode=full` when docstrings and
`docs/` pages must actually be rewritten; use this skill when the caller
(typically `IntegrationChecker` D1) only needs the drift report.

## Current branch

!`git branch --show-current`

## Inputs (contract)

`$ARGUMENTS` carries the invocation. Map positional args as:

| Argument | Required | Meaning |
|---|---|---|
| `$target` | yes | File, module, or package to audit (e.g. `src/br_generator/executor/pipeline.py` or `br_generator.executor`). |
| `$input_manifest` | no | Path to an existing drift manifest to extend as a new revision. |
| `branch` | no | The value injected under "Current branch" above; treat empty output as `null`. |
| `parent_agent` | no | Caller identity; defaults to `"user"`. |

If `$target` is empty, ask the user for the target before proceeding.

## Workflow

1. **Discovery** (read-only, via Read/Grep/Glob):
   - Enumerate the public symbols in `$target` (modules, classes, public
     methods, public functions). Skip `_private` symbols unless explicitly
     requested.
   - For each symbol capture: signature, type hints, raised exceptions (from
     the implementation), and side-effects (I/O, logging, state mutation).
   - Read the existing docstring (if any) and the `docs/` page(s) that
     reference it via mkdocstrings (`::: <dotted.path>`) or explicit
     cross-links.
2. **Drift classification** per symbol (criteria: `references/docstring-conventions.md`):
   - `MISSING` — no docstring at all.
   - `STALE_SIGNATURE` — docstring `Args`/`Returns`/`Raises` does not match the
     current signature or implementation.
   - `STALE_BEHAVIOUR` — docstring narrative contradicts code behaviour
     evidenced by `path:Lstart-Lend`.
   - `MISSING_SECTION` — required Google-style section absent (`Args` for
     functions/methods, `Yields` for generators, etc., per AGENTS.md).
   - `DOCS_PAGE_MISSING` — public symbol not surfaced under `docs/` despite
     being part of the documented public API.
   - `DOCS_PAGE_STALE` — narrative page contradicts code or docstring.
   - `OK` — no drift detected (only after cross-checking docstring AND page).
3. **Manifest composition** — follow `references/MANIFEST_CONTRACT.md` exactly.
   The body sections (§5 of the contract) in order: `# <title>`, `## Description`,
   `## Rationale & motive`, `## Arguments & grounded evidence` (with
   `### Evidence`, `### Diagram`, `### Summary table`), `## Expected benefits`,
   `## Possible downsides & risks`, `## Proposed diffs`, `## Acceptance criteria`,
   `## Handover`, `## Manifest changelog`. Skill-specific requirements:
   - **`### Diagram`**: a Mermaid `flowchart` mapping
     `source symbol → docstring status → docs page status → drift type`.
   - **`### Summary table`**: first column = stable `Dn` IDs; columns
     *Finding ID (Dn) × Symbol × Docstring status × Docs page × Drift type ×
     `path:Lline`*. Reuse the `Dn` IDs verbatim in `## Proposed diffs`,
     `## Acceptance criteria`, and `## Handover`.
   - **`## Expected benefits`**: measurable, e.g. "fixes N undocumented public
     symbols", "restores bidirectional cross-linking for module X".
   - **`## Possible downsides & risks`**: non-empty Risks table
     (`# × Risk × Likelihood × Impact × Mitigation`), Likelihood/Impact each
     `Low|Medium|High`.
   - **`## Proposed diffs`**: optional. Mechanical, small fixes (e.g. add a
     missing `Args:` block) → one `<!-- diff -->` block + set
     `proposes_edits: true`. Larger structural rewrites authored by
     `@DocsReviewer` → `<!-- pseudodiff -->`. Defer full narrative rewrites to
     the agent and leave this section empty.
   - **`## Acceptance criteria`**: e.g. "no symbol with status `MISSING` in
     scope after handover". Optional `### Validation commands` sub-block
     (`uv run mkdocs build --strict`, `uv run pytest -q`).
   - **`## Handover`**: first line
     `[mandatory: @DocsReviewer] [optional: @CodeReviewer]`, then a numbered
     list referencing the `Dn` IDs.
   - **`## Manifest changelog`**: append one row
     `<UTC> | @DocsReviewer (skill, docs_mode=drift) | Initial drift report`.
4. **Manifest extension** (only when `$input_manifest` is given): first run
   `python3 ${CLAUDE_SKILL_DIR}/scripts/manifest_hash.py verify $input_manifest`.
   A non-zero exit is a hard stop — surface the mismatch and abort. On success,
   append `## Revision <n> — <new-sha7>` re-running contract sections 2–9 with
   the deltas; the changelog table is shared and appended-to.
5. **Persist & hash**:
   - Write the manifest to
     `./manifests/skills/docs-drift-detection/docs-drift-detection__<topic>__0000000.md`
     (`<topic>` = kebab-case of the target, ≤40 chars).
   - Run `python3 ${CLAUDE_SKILL_DIR}/scripts/manifest_hash.py commit <file>`.
     This rewrites `hash:` + `revisions[0]` and renames the file to its sha7.
6. **Return** to the user: the committed manifest path, the sha7, the verdict
   (`GO` when no drift; `NO-GO` when drift found and policy requires
   resolution; `ADVISORY` otherwise; `NEEDS-INPUT` if `$target` was
   unresolvable), and a 5–10 line abstract. There is no orchestrator agent in
   this environment, so the return is your final chat message, not a tool call.

## Hard rules

- Never edit docstrings, source files, or `docs/` pages. Edits are described in
  `## Proposed diffs` only, and only when the rewrite is mechanical.
- Never invoke another agent or skill.
- Every finding cites evidence (`path:Lstart-Lend`).
- Respect the Google-style conventions and mandatory-`Args` rule in
  `references/docstring-conventions.md`.
- Do not classify a symbol as `OK` without an explicit cross-check of both its
  docstring and its `docs/` page (when one exists).

## Required frontmatter for the produced manifest

```yaml
---
manifest_kind: skill-report
manifest_version: 1
skill: docs-drift-detection
topic: <kebab-case-topic>
scope: ["<path-or-module>", ...]
branch: <git-branch-or-null>
parent_agent: <invoking-agent-or-"user">
created_at: <ISO-8601-UTC>
hash: 0000000
parent: null
revisions:
  - sha: 0000000
    created_at: <ISO-8601-UTC>
    file: docs-drift-detection__<topic>__0000000.md
verdict: ADVISORY | GO | NO-GO | NEEDS-INPUT
proposes_edits: true | false
---
```

## Resources

- Manifest contract: `references/MANIFEST_CONTRACT.md`
- Docstring conventions: `references/docstring-conventions.md`
- Hash helper: `scripts/manifest_hash.py` (subcommands: `hash` / `verify` /
  `commit`; exit `0` ok, `1` verify mismatch, `2` structural error)

## Notes for this port

- `context: fork` runs the skill in an isolated subagent with no conversation
  history; everything it needs is in this file. The fork must keep write access
  (to persist the manifest), so do not pin `agent: Explore` — that agent is
  read-only and would block step 5. The default `general-purpose` agent is
  correct; override only with a custom agent that can write.
- `scripts/manifest_hash.py` is the repository's own helper, unchanged.
- `references/docstring-conventions.md` is the docstring section of `AGENTS.md`
  reconciled with `docs-reviewer.agent.md` §II; the rest of `AGENTS.md`
  (validation workflow, change boundaries) is deliberately excluded so no
  mutation/validation directives leak into this read-only fork.
