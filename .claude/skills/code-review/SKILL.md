---
name: code-review
description: Produce a read-only design audit of a Python module or selection (SOLID, GoF/refactoring patterns, complexity, error handling, type usage) and persist it as a versioned manifest under manifests/skills/code-review/. Use when an orchestrator or the user needs an architectural verdict and proposed diffs without polluting the parent chat with the audit transcript.
argument-hint: "[target file or glob] [optional input_manifest path]"
arguments: target input_manifest
context: fork
user-invocable: true
disable-model-invocation: true
allowed-tools: Read Grep Glob Bash(python3 *) Bash(git branch *) Bash(git rev-parse *)
---

# code-review skill

A read-only design-audit skill that produces (or extends) a versioned
**code-review manifest** under `./manifests/skills/code-review/`. The
manifest is the only side-effect: this skill must never edit the audited
code.

This skill is the forked twin of the `CodeReviewer` agent. Use the agent
when you want the review and the refactor in the same session (with explicit
user approval); use this skill when the caller only needs the audit as a
hand-off artefact and wants the long audit transcript kept out of the parent
context.

## Inputs (contract)

| Argument | Required | Meaning |
|---|---|---|
| `target` | yes | File, module, glob, or selection to audit. |
| `input_manifest` | no | Path to an existing code-review manifest to extend as a new revision. |
| `branch` | no | Defaults to `git branch --show-current`. |
| `parent_agent` | no | Caller identity (defaults to `"user"`). |

## Workflow

1. **Audit** under the Zero-Trust policy (only executable code is evidence):
   - Public API surface, type system usage, SOLID adherence, complexity,
     error handling, async patterns, naming intention, architectural fit.
   - Rate findings by severity (`blocker`, `major`, `minor`, `info`) and by
     category (SRP/OCP/LSP/ISP/DIP, complexity, error handling, etc.).
   - Cite every finding with `path:Lstart-Lend`.
2. **Manifest composition** following `references/MANIFEST_CONTRACT.md`:
   - **§4 Diagram**: a `classDiagram` (or `flowchart` for control-flow
     refactors) showing *current* vs *proposed* structure.
   - **§4 Summary table**: columns *Finding ID (Fn) × Severity × Category
     × Location (`path:Lline`) × Suggested action*. Reuse the `Fn` IDs
     verbatim in §7, §8, and §9.
   - **§5 Expected benefits**: e.g. "−1 cyclic import",
     "Pyright strict-mode violations: 4 → 0", "complexity 18 → 8".
   - **§6 Possible downsides & risks**: required Risks table
     (`# × Risk × Likelihood × Impact × Mitigation`).
   - **§7 Proposed diffs**: required when the audit recommends concrete
     edits. Use `<!-- diff -->` for apply-ready unified diffs, or
     `<!-- pseudodiff -->` for signature-only structural sketches when the
     final body must be authored by `@ProjectDeveloper`. Mark
     `proposes_edits: true` in the frontmatter.
   - **§8 Acceptance criteria**: checklist for the orchestrator (e.g.
     "all proposed diffs apply cleanly", "Pyright strict passes",
     "tests still pass"). Optionally include a `### Validation commands`
     sub-block with the re-verification snippets.
   - **§9 Handover**: first line
     `[mandatory: @ProjectDeveloper] [optional: @CodeReviewer, @LinterSpecialist]`
     (adjust to context), followed by per-finding routing referencing the
     `Fn` IDs.
   - **§10 Manifest changelog**: append one row
     `<UTC> | @CodeReviewer (skill) | Initial design audit`.
3. **Manifest extension** (only when `input_manifest` is provided):
   - Verify integrity with
     `python3 ${CLAUDE_SKILL_DIR}/scripts/manifest_hash.py verify <input_manifest>`.
     A mismatch is a hard stop.
   - Append a `## Revision <n> — <new-sha7>` section.
4. **Persist & hash**:
   - Write to
     `./manifests/skills/code-review/code-review__<topic>__0000000.md`.
   - Run `python3 ${CLAUDE_SKILL_DIR}/scripts/manifest_hash.py commit <file>`.
5. **Report** the result (to the calling agent if one invoked you, otherwise as your final message): manifest path, sha7, verdict
   (`GO` if no blockers, `NO-GO` if blockers, `ADVISORY` for non-blocking
   reviews, `NEEDS-INPUT` when the audit cannot proceed without
   clarification), and a 5–10 line abstract.

## Hard rules

- Never edit the audited code. Edits are described in §7 only.
- Never invoke another agent or skill.
- Every finding cites evidence.
- Defer Ruff / Pyright / SonarQube *rule violations* to the
  `LinterSpecialist` agent — this skill judges *design*, not mechanical
  compliance.
- Never weaken or skip tests. Tests are read-only here.

## Required frontmatter for the produced manifest

```yaml
---
manifest_kind: skill-report
manifest_version: 1
skill: code-review
topic: <kebab-case-topic>
scope: ["<path-or-glob>", ...]
branch: <git-branch-or-null>
parent_agent: <invoking-agent-or-"user">
created_at: <ISO-8601-UTC>
hash: 0000000
parent: null
revisions:
  - sha: 0000000
    created_at: <ISO-8601-UTC>
    file: code-review__<topic>__0000000.md
verdict: ADVISORY | GO | NO-GO | NEEDS-INPUT
proposes_edits: true | false
---
```

## Resources

- Manifest contract: `references/MANIFEST_CONTRACT.md`
- Hash helper: `scripts/manifest_hash.py`
- Twin agent: `.claude/agents/code-reviewer.md` (twin subagent)

## Notes for this port

- `context: fork` runs this in an isolated subagent with no chat history; everything it needs is in this file. The fork must keep write access to persist the manifest, so do not pin `agent: Explore` (read-only) — the default `general-purpose` agent is correct.
- `scripts/manifest_hash.py` is the repository's own helper, unchanged; `references/MANIFEST_CONTRACT.md` is the canonical contract, bundled verbatim.
- Twin subagent: `.claude/agents/code-reviewer.md`. The two coexist; Claude picks the skill for a report-only hand-off and the agent for interactive work.
