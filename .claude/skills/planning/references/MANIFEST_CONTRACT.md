# Skill manifest contract

> **Scope.** This document is the canonical specification for all manifests
> produced by the fork-skills under `.github/skills/`. Every `SKILL.md` in this
> tree must comply with it. Orchestrator agents (`@ProjectArchitect`,
> `@ProjectDeveloper`, `@ReleaseManager`, `@IntegrationChecker`) consume these
> manifests as the sole hand-off artefact from a forked sub-context.

## 1. Design principle

```
Fork = report.   Agent = mutate.
```

A skill running in a forked sub-context (`context: fork`) is **never** allowed
to edit source code, tests, configuration, documentation, or any file outside
`./manifests/skills/`. The skill's only side-effect is the manifest. Whether
the proposed change is applied is the orchestrator's (or the user's)
decision.

## 2. Storage layout

```
manifests/
└── skills/
    ├── planning/
    │   └── planning__<topic>__<sha7>.md
    ├── code-review/
    │   └── code-review__<topic>__<sha7>.md
    ├── integration-check/
    │   └── integration-check__<topic>__<sha7>.md
    └── docs-drift-detection/
        └── docs-drift-detection__<topic>__<sha7>.md
```

- `<slug>` matches the `name:` field in the originating `SKILL.md` and the
  parent skill folder under `.github/skills/`.
- `<topic>` is a kebab-case identifier of the subject (target file, branch,
  scope), max 40 characters.
- `<sha7>` is the first 7 hex characters of the SHA-256 of the canonical body
  (see §4).

## 3. Frontmatter

```yaml
---
manifest_kind: skill-report
manifest_version: 1
skill: <slug>
topic: <kebab-case-topic>
scope: ["<path-or-spec>", ...]
branch: <git-branch-or-null>
parent_agent: <invoking-agent-or-"user">
created_at: <ISO-8601-UTC>
hash: <sha7>
parent: <previous-sha7-or-null>
revisions:
  - sha: <sha7>
    created_at: <ISO-8601-UTC>
    file: <slug>__<topic>__<sha7>.md
verdict: ADVISORY | GO | NO-GO | NEEDS-INPUT
proposes_edits: true | false
---
```

- `revisions:` is newest-first. Every `commit` of the manifest prepends a new
  entry and updates `hash:` + `revisions[0]`.
- `proposes_edits: true` requires §5.7 (Proposed diffs) to be present.

## 4. Hashing & versioning

The `sha7` is computed by `.github/scripts/manifest_hash.py` over the
**canonical body** (the manifest content after the closing `---` marker, with
trailing whitespace stripped from every line, trailing blank lines removed,
and a single LF appended). The frontmatter is metadata about the body and is
**not** included in the digest, so editing `status:` or `verdict:` does not
invalidate the hash.

Skill workflows MUST invoke the helper rather than recomputing the digest by
hand:

| Subcommand | Purpose | Exit codes |
|---|---|---|
| `python .github/scripts/manifest_hash.py hash <file>` | Print the recomputed sha7. | `0` |
| `python .github/scripts/manifest_hash.py verify <file>` | Verify declared `hash:` matches the body. | `0` ok / `1` mismatch / `2` structural error |
| `python .github/scripts/manifest_hash.py commit <file>` | Recompute, rewrite `hash:` and `revisions[0]`, rename file. | `0` ok / `2` structural error |

When extending an existing manifest, the skill MUST first run `verify` on the
input. A `verify` failure is a hard stop — the skill must surface the
mismatch and abort instead of overwriting potentially tampered content.

## 5. Body sections (mandatory, in order)

1. **`# <Title>`** — one-line, imperative, lowercase first letter (Angular
   convention used elsewhere in this repository).
2. **`## Description`** — what the skill ran on, the inputs it consumed, and
   the perimeter (files / branch / scope).
3. **`## Rationale & motive`** — *why* the report exists, tied to a concrete
   user goal or upstream manifest.
4. **`## Arguments & grounded evidence`** — the persuasion section. Required
   sub-blocks:
   - **`### Evidence`** — bullet list. Every claim cited as
     `path:Lstart-Lend`, commit SHA, or tool output reference. No unsupported
     assertions (Zero-Trust policy).
   - **`### Diagram`** — at least one Mermaid diagram (`flowchart`,
     `sequenceDiagram`, `classDiagram`, or `erDiagram`) summarising the
     finding or proposed change.
   - **`### Summary table`** — at least one Markdown table aggregating the
     skill output. The first column MUST be a **stable identifier** (e.g.
     `S1, S2, …` for plan steps, `F1, F2, …` for code-review findings,
     `G0…G6, D1` for integration gates, `D1, D2, …` for drift findings).
     These IDs MUST be reused verbatim in §7 (Proposed diffs), §8 (Acceptance
     criteria), and §9 (Handover) so cross-references are unambiguous. For
     `planning`, append a one-line **effort summary roll-up** immediately
     below the table in the form `S×n, M×n, L×n, XL×n — total: <Small | Medium | Large | XL>`.
5. **`## Expected benefits`** — bullet list. Each item must be measurable or
   observable (e.g. "−8 deferred imports in `pipeline.py`",
   "Sonar bug count: 3 → 0").
6. **`## Possible downsides & risks`** — Markdown table with the schema
   below. Must be non-empty; write a single row with `Risk = none identified`
   and a written justification in `Mitigation` only when truly true.

   ```
   | # | Risk | Likelihood | Impact | Mitigation |
   |---|------|------------|--------|------------|
   ```

   `Likelihood` and `Impact` MUST each be one of `Low | Medium | High`.
7. **`## Proposed diffs`** — *required when `proposes_edits: true`*. Each
   fenced ` ```diff ` block MUST be preceded by exactly one of the following
   HTML-comment markers on its own line:

   - `<!-- diff -->` — apply-ready unified diff. The block, taken verbatim,
     applies cleanly with `git apply`. The skill MUST NOT apply it.
   - `<!-- pseudodiff -->` — structural sketch only (signatures, imports,
     class skeletons; bodies are `...`). Used when the final implementation
     is delegated to the consuming orchestrator and authoring the body in
     the manifest would be speculative.

   Mixing the two within a single block is forbidden — split into two blocks
   with their respective markers.
8. **`## Acceptance criteria`** — checklist of conditions the orchestrator
   must verify before treating the manifest as `done`. May optionally end
   with a `### Validation commands` sub-block containing one or more fenced
   shell snippets (`powershell` or `bash`) that re-run the verdict
   deterministically. Commands MUST be idempotent and read-only relative to
   the working tree.
9. **`## Handover`** — explicit next step. The first line MUST carry the
   ownership tags `[mandatory: @AgentA[, @AgentB]] [optional: @AgentC[, @AgentD]]`,
   followed by a numbered list of obligations for the mandatory agent(s).
   `[optional: …]` may be omitted when no auxiliary involvement is expected.
10. **`## Manifest changelog`** — append-only Markdown table with the schema
    below. Each manifest commit (initial or revision) MUST add exactly one
    row capturing the agent, ISO-8601 UTC timestamp, and a one-sentence
    description of the change. The newest row goes at the bottom.

    ```
    | Date (UTC) | Agent | Change |
    |------------|-------|--------|
    ```

For revisions ≥ 2, append a `## Revision <n> — <sha7>` section that re-runs
sections 2–9 with the revision-specific deltas. Older revisions stay verbatim
above for auditability. The `## Manifest changelog` table is shared across
all revisions and is appended-to (never duplicated per revision).

## 6. Per-skill specialisation

| Skill | Diagram type | Summary table (first column = stable ID) |
|---|---|---|
| `planning` | `flowchart` of change steps + dependencies | `Step ID (Sn) × File × Risk × Reversibility × Effort (S/M/L/XL)` + effort roll-up |
| `code-review` | `classDiagram` (current vs proposed) or `flowchart` | `Finding ID (Fn) × Severity × Category × Location (path:Lline) × Suggested action` |
| `integration-check` | `flowchart` of G0→G6 + D1 with pass/fail nodes | `Gate ID (G0…G6, D1) × Status × Evidence path × Delegated owner × Notes` |
| `docs-drift-detection` | `flowchart` mapping symbol → docstring → docs page | `Finding ID (Dn) × Symbol × Docstring status × Docs page × Drift type × path:Lline` |

## 7. Twin-artifact rule

Every skill in this tree has a **twin agent** under `.github/agents/`:

| Skill (`.github/skills/<slug>/SKILL.md`) | Twin agent | Skill's role | Agent's role |
|---|---|---|---|
| `planning` | `planner.agent.md` | one-shot decision-grade plan → manifest | interactive iteration / handoffs |
| `code-review` | `code-reviewer.agent.md` | read-only design audit → manifest | apply approved edits |
| `integration-check` | `integration-checker.agent.md` | verdict-only G0–G6 + D1 → manifest | dispatch remediation to specialists |
| `docs-drift-detection` | `docs-reviewer.agent.md` | drift detection → manifest | full authoring (`docs_mode=full`) |

Orchestrators MUST prefer the **skill** when they only need a report and the
**agent** when they need iterative interaction or workspace edits.

## 8. Experimental flag

Forked context requires the VS Code setting:

```jsonc
"github.copilot.chat.skillTool.enabled": true
```

Without the flag the `context: fork` field is silently ignored and the skill
loads inline into the parent context, defeating the purpose of the
twin-artifact split. Treat this flag as a hard prerequisite for the workflow.
