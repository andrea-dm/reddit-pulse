---
name: integration-check
description: Run the repository's pre-commit validation pipeline (Ruff, formatter, Pyright, pytest, SonarQube, plus a documentation-drift check) in advisory mode and persist a deterministic GO / NO-GO verdict as a versioned manifest under manifests/skills/integration-check/. Use as the final pre-MR gate when the parent agent only needs the verdict + evidence and not the verbose tool transcripts.
argument-hint: "[scope: 'tree' | 'changed' | <glob>] [optional input_manifest path]"
arguments: scope input_manifest
context: fork
user-invocable: true
disable-model-invocation: true
allowed-tools: Read Grep Glob Bash
---

# integration-check skill

A read-only verdict-only integration-gate skill that runs the same validation
pipeline as the `IntegrationChecker` agent (gates G0–G6 + D1) and persists a
deterministic verdict as a versioned manifest under
`./manifests/skills/integration-check/`. The manifest is the only
side-effect (besides ephemeral diagnostic files which the skill must clean
up before returning).

This skill is the forked twin of the `IntegrationChecker` agent. Use the
agent when remediation must be dispatched to specialist subagents
(`@LinterSpecialist`, `@TestDesigner`, etc.); use this skill when the parent
agent (typically `@ProjectDeveloper` at the final pre-MR gate) only needs
the verdict and the evidence pointers.

## Inputs (contract)

| Argument | Required | Meaning |
|---|---|---|
| `scope` | no | `"tree"` (default) for the whole working tree, `"changed"` for files changed vs. the merge base, or a glob. |
| `input_manifest` | no | Path to an existing integration-check manifest to extend as a new revision. |
| `branch` | no | Defaults to `git branch --show-current`. Passed to all SonarQube calls. |
| `parent_agent` | no | Caller identity (defaults to `"user"`). |

## Workflow

1. **Run the gate pipeline** (mirrors `IntegrationChecker` exactly):
   - **G0** — End-of-line / encoding sanity.
   - **G1** — Dependency consistency (deptry, lockfile vs. pyproject).
   - **G2** — Lint (`uv run ruff check`).
   - **G3** — Format (`uv run ruff format --check`).
   - **G4** — Type-check (`uv run pyright`, strict where configured).
   - **G5** — Tests (`uv run pytest`, scope-respecting).
   - **G6** — SonarQube (project + branch scoped — always pass `branch=` per the SonarQube MCP rules (optional — see port notes)).
   - **D1** — Documentation drift (skill-level check; deeper authoring is
     the `docs-drift-detection` skill / `DocsReviewer` agent's job).
2. **Aggregate** results into a gate matrix. Capture, for each gate:
   - status (`PASS`, `FAIL`, `SKIP`, `ERROR`),
   - tool output excerpt or path to the saved log,
   - failed file × rule × location (if applicable),
   - the agent that should remediate it (e.g. `@LinterSpecialist`,
     `@TestDesigner`, `@CodeReviewer`, `@DocsReviewer`).
3. **Compute the verdict**:
   - `GO` if every gate is `PASS` or intentionally `SKIP`-with-rationale.
   - `NO-GO` if any gate is `FAIL` or `ERROR`.
   - `NEEDS-INPUT` if a tool requires a credential or interactive choice.
4. **Manifest composition** following `references/MANIFEST_CONTRACT.md`:
   - **§4 Diagram**: a `flowchart` of `G0 → G1 → G2 → G3 → G4 → G5 → G6` and
     `D1`, with each node coloured by status.
   - **§4 Summary table**: columns
     *Gate ID (G0…G6, D1) × Status × Evidence path × Delegated owner × Notes*.
     The Gate IDs are the stable identifiers reused in §8 and §9.
   - **§5 Expected benefits**: e.g. "blocks introduction of N lint
     violations", "guarantees Pyright strict on changed files".
   - **§6 Possible downsides & risks**: required Risks table
     (`# × Risk × Likelihood × Impact × Mitigation`); cover tool-specific
     caveats (e.g. Sonar server staleness, branch-scope traps).
   - **§7 Proposed diffs**: omit (this skill only reports). Set
     `proposes_edits: false`.
   - **§8 Acceptance criteria**: usually `verdict == GO` and per-gate
     thresholds. Include a `### Validation commands` sub-block with the
     exact `uv run …` commands the orchestrator can re-execute.
   - **§9 Handover**: first line
     `[mandatory: @ProjectDeveloper] [optional: @LinterSpecialist, @TestDesigner, @CodeReviewer, @DocsReviewer]`,
     followed by per-gate routing for `NO-GO` results referencing the Gate
     IDs (e.g. "G2 failures → `@LinterSpecialist`").
   - **§10 Manifest changelog**: append one row
     `<UTC> | @IntegrationChecker (skill) | Initial gate run — verdict <GO|NO-GO|NEEDS-INPUT>`.
5. **Manifest extension** (only when `input_manifest` is provided): verify
   integrity then append `## Revision <n> — <new-sha7>`.
6. **Persist & hash**:
   - Write to
     `./manifests/skills/integration-check/integration-check__<topic>__0000000.md`.
   - Run `python3 ${CLAUDE_SKILL_DIR}/scripts/manifest_hash.py commit <file>`.
7. **Cleanup**: delete every temporary diagnostic file (e.g.
   `pytest_output.txt`, `report.xml`) created during the run. The manifest
   stores their content excerpts; the originals must not pollute the working
   tree.
8. **Report** the result (to the calling agent if one invoked you, otherwise as your final message): manifest path, sha7, verdict, and a
   5–10 line abstract listing failing gates.

## Hard rules

- Never edit source, tests, docs, or config. Edits are reported via the
  delegated owner only, in §9.
- Never invoke another agent or skill. The orchestrator dispatches
  remediation based on §9.
- Always pass `branch=` to every SonarQube MCP call — never omit it (see port notes — SonarQube is an optional MCP server here).
- Use small (3–5 keys) `get_component_measures` batches; one bad metric
  poisons the whole batch.
- After fixing — wait, this skill **never fixes** — there is no post-fix
  loop here. Remediation is the orchestrator's job.
- `proposes_edits` is always `false`.

## Required frontmatter for the produced manifest

```yaml
---
manifest_kind: skill-report
manifest_version: 1
skill: integration-check
topic: <kebab-case-topic>      # e.g. branch name + "pre-mr"
scope: ["tree" | "changed" | "<glob>"]
branch: <git-branch-or-null>
parent_agent: <invoking-agent-or-"user">
created_at: <ISO-8601-UTC>
hash: 0000000
parent: null
revisions:
  - sha: 0000000
    created_at: <ISO-8601-UTC>
    file: integration-check__<topic>__0000000.md
verdict: GO | NO-GO | NEEDS-INPUT
proposes_edits: false
---
```

## Resources

- Manifest contract: `references/MANIFEST_CONTRACT.md`
- Hash helper: `scripts/manifest_hash.py`
- SonarQube usage rules: the SonarQube MCP server rules (optional — see port notes)
- Twin agent: `.claude/agents/integration-checker.md` (twin subagent)

## Notes for this port

- `context: fork` runs this in an isolated subagent with no chat history; everything it needs is in this file. The fork must keep write access to persist the manifest, so do not pin `agent: Explore` (read-only) — the default `general-purpose` agent is correct.
- `scripts/manifest_hash.py` is the repository's own helper, unchanged; `references/MANIFEST_CONTRACT.md` is the canonical contract, bundled verbatim.
- Twin subagent: `.claude/agents/integration-checker.md`. The two coexist; Claude picks the skill for a report-only hand-off and the agent for interactive work.
- The pipeline shells out to `uv run ruff/pyright/pytest`; those must be available in the project. **G6 (SonarQube)** maps to an optional MCP server — if you have not configured a SonarQube MCP server, treat G6 as `SKIP` with rationale rather than `ERROR`.
