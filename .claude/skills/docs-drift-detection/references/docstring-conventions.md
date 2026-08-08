# Google-style docstring conventions (drift reference)

This file is the authority the drift classifier checks docstrings against. It
is distilled from `AGENTS.md` (§ Docstrings and comments) and
`docs-reviewer.agent.md` (§II, Documentation Standards). It is intentionally
scoped to docstring/docs drift: the rest of `AGENTS.md` (tach, the
ruff/pyright/pytest validation workflow, change boundaries) is
authoring/validation guidance that must not enter a read-only, report-only
fork. If `AGENTS.md` changes, reconcile its docstring section here — this file
is the single source the classifier reads.

The skill uses these rules **only to detect drift**. It never rewrites
docstrings or pages; authoring is the `DocsReviewer` agent's job
(`docs_mode=full`).

## 1. Format

- Google Python Style Guide, strictly, so `mkdocstrings`/`griffe` can parse it;
  cross-references use Sphinx/RST roles (see §5), so the result is both
  MkDocs- and Sphinx-compatible.
- Recognised sections: `Args`, `Returns`, `Raises`, `Yields`, `Notes`,
  `Warnings`, `See Also`, `Attributes`.
- Every public module, class, method, and function that is created or modified
  carries a docstring. Modules and classes get a concise description (no `Args`).
- 88-character line limit for docstrings and comments.

## 2. Mandatory sections (drives `MISSING_SECTION`)

- **`Args`** is mandatory for every public function or method that takes
  parameters (excluding `self`/`cls`). A public callable with parameters and no
  `Args` block is `MISSING_SECTION`.
- **`Returns`** is required when the callable returns a non-`None` value.
- **`Yields`** is required for generator functions/methods (any body containing
  `yield`); for a generator it takes the place of `Returns`. A generator
  documented with `Returns` instead of `Yields`, or missing both, is drift.
- **`Raises`** is required when the implementation can raise an exception that
  is part of the contract (an explicit `raise`, or a documented propagation).
- **`Examples`** belongs **only** on public symbols (no leading `_`). A private
  symbol carrying an `Examples` section is itself a (minor) drift signal; a
  public symbol missing one is *not* automatically drift unless your policy
  requires it.

## 3. Signature agreement (drives `STALE_SIGNATURE`)

Compare the docstring against the live signature and implementation:

- Every parameter in the signature appears in `Args`, and vice versa (no
  phantom or missing parameters).
- Documented types are consistent with the PEP 484 type hints.
- `Returns` matches the actual return annotation/behaviour.
- A generator's value section is `Yields`, not `Returns`; the wrong one (or a
  return-type docstring on a generator) is `STALE_SIGNATURE`.
- `Raises` lists exactly the contract-level exceptions the body can raise.

## 4. Behaviour & side-effect agreement (drives `STALE_BEHAVIOUR`)

Scan the implementation for these patterns and confirm the docstring reflects
them; a contradiction or omission is `STALE_BEHAVIOUR`:

- **In-place mutation** — `.append()`, `.update()`, `.pop()`, direct index
  assignment on an input argument → that parameter must be annotated
  `(mutated in-place)` in `Args`.
- **I/O** — `open()`, `pathlib`, `requests`, `httpx`, etc. → a `Notes:` section
  describing the I/O.
- **Logging** — a logger or `print` → `Notes:` mentioning emitted logs.
- **Global/class state** — mutation of `self.` attributes outside `__init__`,
  or `global` writes → `Notes:` that internal state is updated.
- **Threading** — `threading.Lock`/`RLock` → class docstring states whether the
  class is thread-safe.
- **Ambiguity** — when a side effect is plausible but not definitive, the
  compliant docstring marks it `[AMBIGUOUS]` (in `Args` or `Warnings:`). Treat a
  missing `[AMBIGUOUS]` marker on a genuinely ambiguous symbol as drift only if
  the ambiguity is material.

## 5. Cross-references (drives `DOCS_PAGE_MISSING` / `DOCS_PAGE_STALE`)

- Intra-project links inside docstrings use RST/Sphinx roles, not Markdown:
  `:class:`, `:meth:`, `:func:`, `:data:`, and the `~` short-name prefix
  (e.g. `:class:`~module.ClassName``). A plain Markdown link inside a docstring
  is a convention violation.
- A public symbol that is part of the documented public API but is **not**
  surfaced under `docs/` (no `::: dotted.path` mkdocstrings directive and no
  narrative reference) is `DOCS_PAGE_MISSING`.
- A `docs/` narrative page whose description contradicts the current code or
  docstring is `DOCS_PAGE_STALE`.
- `See Also:` is expected when a public symbol has a meaningful relationship to
  another (delegation to private helpers, membership in an orchestrator, a
  related sibling, or a corresponding narrative `docs/` section). Its absence is
  a soft signal, not a hard drift type.

## 6. `Returns:` continuation rule (parsing trap)

Never place a blank line at the *start* of a `Returns:` continuation block:
griffe treats a blank line at the section's indentation as a section
terminator, orphaning the rest into the main body. For multi-item returns, put
a dash-list immediately after the summary line; a blank line *after* the
summary line is safe.

## 7. Classification precedence

When a symbol qualifies for more than one drift type, report the most specific:
`MISSING` (no docstring at all) outranks `MISSING_SECTION`, which is distinct
from `STALE_SIGNATURE` / `STALE_BEHAVIOUR`. A symbol is `OK` only after an
explicit cross-check of **both** its docstring and its `docs/` page (when one
exists) — never default to `OK`.
