---
name: planning
description: Generate a decision-grade implementation plan for a coding task and persist it as a versioned manifest under manifests/skills/planning/. Use when the user, an orchestrator agent, or another skill needs a read-only roadmap (scope, files, risks, alternatives, validation, rollback) before any code is written.
argument-hint: "[target] [optional input_manifest path]"
arguments: target input_manifest
context: fork
user-invocable: true
disable-model-invocation: true
allowed-tools: Read Grep Glob Bash(python3 *) Bash(git branch *) Bash(git rev-parse *)
---

# planning skill

A read-only planning skill that produces (or extends) a versioned **planning
manifest** under `./manifests/skills/planning/`. The manifest is the only
side-effect: this skill must never edit source, tests, docs, or
configuration.

This skill is the forked twin of the `Planner` agent. Use the agent for
interactive iteration; use this skill when the caller only needs the final
plan as a hand-off artefact.

## Inputs (contract)

| Argument | Required | Meaning |
|---|---|---|
| `target` | yes | Free-text description of the change to plan, plus pointers to relevant files / branch / scope. |
| `input_manifest` | no | Path to an existing planning manifest under `./manifests/skills/planning/` to extend as a new revision. |
| `branch` | no | Defaults to `git branch --show-current`. |
| `parent_agent` | no | Caller identity (defaults to `"user"`). |

## Workflow

1. **Discovery** (read-only, ≤15 file reads):
   - Read the relevant source, tests, and docs.
   - Map current implementation, dependencies, and existing patterns.
   - Cite every factual claim with `path:Lstart-Lend`.
2. **Plan composition** following the manifest contract sections 1–10 (see
   `references/MANIFEST_CONTRACT.md`). Specifically:
   - **§4 Diagram**: a `flowchart` of change steps with edges showing
     dependencies and parallelism opportunities.
   - **§4 Summary table**: columns *Step ID (Sn) × File × Risk ×
     Reversibility × Effort (S/M/L/XL)*. Append a one-line **effort roll-up**
     directly below the table: `S×n, M×n, L×n, XL×n — total: <Small | Medium | Large | XL>`.
   - **§5 Expected benefits**: measurable (LOC delta, removed dependencies,
     improved metric).
   - **§6 Possible downsides & risks**: required Risks table
     (`# × Risk × Likelihood × Impact × Mitigation`) per the contract.
   - **§7 Proposed diffs**: omit unless the plan includes a trivial
     bootstrap edit; this skill prefers a roadmap over a diff dump. When
     present, prefix structural sketches with `<!-- pseudodiff -->` and
     apply-ready diffs with `<!-- diff -->`.
   - **§8 Acceptance criteria**: include a `### Validation commands`
     sub-block with the read-only commands the orchestrator should run
     (e.g. `uv run pytest -q tests/<scoped>`).
   - **§9 Handover**: first line carries
     `[mandatory: @ProjectDeveloper] [optional: @CodeReviewer, @TestDesigner]`
     (adjust to context), followed by a numbered obligations list.
   - **§10 Manifest changelog**: append one row
     `<UTC> | @Planner (skill) | Initial planning manifest`.
3. **Manifest extension** (only when `input_manifest` is provided):
   - Run `python3 ${CLAUDE_SKILL_DIR}/scripts/manifest_hash.py verify <input_manifest>`.
     A mismatch is a hard stop.
   - Append a `## Revision <n> — <new-sha7>` section instead of rewriting
     existing content.
4. **Persist & hash**:
   - Write the file to
     `./manifests/skills/planning/planning__<topic>__0000000.md`
     (placeholder sha).
   - Run `python3 ${CLAUDE_SKILL_DIR}/scripts/manifest_hash.py commit <file>` to compute
     the real sha7, rewrite frontmatter, and rename the file.
5. **Report** the result (to the calling agent if one invoked you, otherwise as your final message): absolute path of the manifest, the new
   `sha7`, the verdict (`ADVISORY` for plans), and a 5–10 line abstract.

## Hard rules

- Never edit source, tests, docs, or config. Edits are described in §7 only,
  and only when strictly necessary.
- Never invoke another agent or skill. A fork has no orchestration authority.
- Every claim cites evidence (`path:Lstart-Lend`, commit SHA, or tool output).
- The manifest is the only side-effect.
- `verdict` is always `ADVISORY` for this skill.
- `proposes_edits` is `false` unless §7 is actually populated.

## Required frontmatter for the produced manifest

```yaml
---
manifest_kind: skill-report
manifest_version: 1
skill: planning
topic: <kebab-case-topic>
scope: ["<path-or-spec>", ...]
branch: <git-branch-or-null>
parent_agent: <invoking-agent-or-"user">
created_at: <ISO-8601-UTC>
hash: 0000000           # placeholder, rewritten by `manifest_hash commit`
parent: null            # or previous sha7 when extending
revisions:
  - sha: 0000000
    created_at: <ISO-8601-UTC>
    file: planning__<topic>__0000000.md
verdict: ADVISORY
proposes_edits: false
---
```

## Resources

- Manifest contract: `references/MANIFEST_CONTRACT.md`
- Hash helper: `scripts/manifest_hash.py`
- Twin agent: `.claude/agents/planner.md` (twin subagent)

## Notes for this port

- `context: fork` runs this in an isolated subagent with no chat history; everything it needs is in this file. The fork must keep write access to persist the manifest, so do not pin `agent: Explore` (read-only) — the default `general-purpose` agent is correct.
- `scripts/manifest_hash.py` is the repository's own helper, unchanged; `references/MANIFEST_CONTRACT.md` is the canonical contract, bundled verbatim.
- Twin subagent: `.claude/agents/planner.md`. The two coexist; Claude picks the skill for a report-only hand-off and the agent for interactive work.
