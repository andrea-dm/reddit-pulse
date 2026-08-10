# Security

## Threat model

`reddit` is a single-tenant, manually launched batch CLI processing
publicly posted Reddit content (submissions/comments) and using
third-party model weights from the Hugging Face Hub. The realistic threats
in scope are: (1) accidental secret disclosure via a shared/world-readable
filesystem mount, and (2) supply-chain trust in downloaded model weights.
There is no network-facing service, no multi-tenant access control, and no
user-submitted input beyond the corpus files/config themselves — so
injection, authn/authz and request-smuggling classes of threat do not
apply.

## Secrets handling

The Hugging Face Hub token (`HUGGINGFACEHUB_API_TOKEN`/`HF_TOKEN`, or a
dotenv file at `environment.dotenv`) is read by
`reddit.core.environment.prepare_environment` and exported as `HF_TOKEN`
for the process's own environment — **deliberately not** written to disk
via `huggingface_hub.login()`. The inline rationale in
`prepare_environment` is explicit about why: `HF_HOME` (the directory
`login()` would persist the token under) commonly points at a
world-readable (mode `777`) shared Azure ML CIFS mount in this project's
actual deployment, and `login()`'s on-disk token file would be readable by
any co-tenant process on that mount. Exporting an environment variable
instead grants Hub access to this process tree only.

No secret is logged: `prepare_environment` logs only the *absence* of a
token (`logging.warning`), never its value.

## Model supply chain

Every model weight is pulled from the Hugging Face Hub by hub id
(`config.yml` `families:*:models:*:id`) via `transformers.AutoModel*`.
This package applies no additional signature/hash verification beyond
whatever `huggingface_hub`/`transformers` perform internally; trust in a
given checkpoint is delegated entirely to the Hub id configured. Gated
model families (Gemma-2, Llama-3) additionally require the operator to
have accepted the model license on the Hub with the account behind the
configured token.

## Destructive operations

Two functions are explicitly destructive and documented as such:

- `reddit.core.utils.clear_hf_cache` — deletes a model's cached weights
  from every `HF_HOME`-style root passed to it (plus the resolved default),
  never a cache root the current process did not actually request.
- `reddit.training.selection.select_median` — deletes every
  non-median-seed checkpoint directory after selection.

Both are intentional space-management operations, not incidental data
loss — but both are irreversible, so any operator-facing tooling built on
top of this package should treat the checkpoint/cache directories it
targets as ephemeral.

## Data sensitivity

The corpus and gold-dataset content are publicly posted Reddit
submissions/comments (per the paper's corpus, r/economy, r/Economics,
r/wallstreetbets); this package applies no PII scrubbing or anonymization
of its own — any such handling, if performed, happens upstream of this
package's scope (see [System Overview: Scope
boundary](system_overview.md#scope-boundary)).

## Dependency hygiene

`deptry` (`pyproject.toml` `[tool.deptry]`) and `tach check`
(`tach.toml`) run in CI to catch unused/undeclared dependencies and
import-layering violations respectively — a supply-chain and
maintainability control, not a runtime security boundary.
