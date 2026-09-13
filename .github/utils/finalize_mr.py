"""Finalize a merged PR: verify merge, close issue, set Done label,
update manifest status on develop, and sync the local develop branch."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import cast

from _common import (
    APIResponseObject,
    GitHubAPI,
    get_json_object_member,
    get_json_string_member,
    read_json_file,
    write_json_file,
)
from _constants import TMP_DIR, WORKING_DIR


def _verify_mr_merged(api: GitHubAPI, mr_iid: int) -> APIResponseObject:
    url = f"pulls/{mr_iid}"
    print(f"Verifying PR #{mr_iid} state…")
    data = cast(APIResponseObject, api.RequestObject(url, method="GET"))

    state = get_json_string_member(data, "state") or ""
    merged_at = get_json_string_member(data, "merged_at") or "N/A"
    merged_by = get_json_string_member(get_json_object_member(data, "merged_by"), "login") or "N/A"

    print(f"  State:     {state}")
    print(f"  Merged at: {merged_at}")
    print(f"  Merged by: {merged_by}")

    if not data.get("merged"):
        raise SystemExit(f"PR #{mr_iid} is not merged (state={state!r})")
    return data


def _close_issue(api: GitHubAPI, issue_iid: int) -> None:
    url = f"issues/{issue_iid}"
    print(f"\nChecking issue #{issue_iid} state…")

    issue = cast(APIResponseObject, api.RequestObject(url, method="GET"))
    state = get_json_string_member(issue, "state") or ""
    print(f"  State: {state}")

    if state == "closed":
        print("  Already closed ✓")
        return

    print(f"  Closing issue #{issue_iid}…")
    api.RequestObject(url, "PATCH", payload={"state": "closed"})
    print(f"  ✓ Issue #{issue_iid} closed")


def _set_issue_done(api: GitHubAPI, issue_iid: int) -> None:
    """Apply the 'Done' status label to the issue."""
    print(f"\nSetting issue #{issue_iid} status to 'Done'…")
    api.RequestObject(
        f"issues/{issue_iid}/labels",
        method="POST",
        payload={"labels": ["Done"]},
    )
    print(f"  ✓ Issue #{issue_iid} labelled 'Done'")


def _run(cmd: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run a shell command, printing it before execution."""
    print(f"  $ {' '.join(cmd)}")
    result = subprocess.run(cmd, text=True, capture_output=True, check=False)
    if check and result.returncode != 0:
        raise SystemExit(f"Command failed: {' '.join(cmd)}\n" + (result.stderr.strip() or result.stdout.strip()))
    return result


def _sync_develop() -> None:
    """Fetch origin/develop, ensure local branch exists and tracks it, ff-only pull."""
    print("\nSyncing develop branch…")
    _run(["git", "fetch", "origin", "develop"])

    exists = subprocess.run(
        ["git", "show-ref", "--verify", "--quiet", "refs/heads/develop"],
        capture_output=True,
        check=False,
    )
    if exists.returncode == 0:
        _run(["git", "switch", "develop"])
    else:
        _run(["git", "switch", "-c", "develop", "--track", "origin/develop"])

    # Self-heal upstream tracking if it was wiped (e.g. after IDE branch reset).
    upstream = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "develop@{upstream}"],
        capture_output=True,
        check=False,
        text=True,
    )
    if upstream.returncode != 0:
        _run(["git", "branch", "--set-upstream-to=origin/develop", "develop"])

    pull = _run(["git", "pull", "--ff-only", "origin", "develop"], check=False)
    if pull.returncode != 0:
        raise SystemExit(
            "git pull --ff-only failed — develop has diverged from origin/develop.\n"
            + (pull.stderr.strip() or pull.stdout.strip())
            + "\nResolve the divergence manually before re-running."
        )
    print("  ✓ develop is up to date")


def _update_manifest(branch: str, mr_iid: int) -> bool:
    """Set status: done in the manifest frontmatter and commit the change on develop.

    Returns True when a commit was created, False when the manifest was absent
    or the status field was already 'done'.
    """
    manifest_path = Path("manifests") / f"{branch}.md"
    if not manifest_path.is_file():
        print(f"\n⚠  Manifest not found at {manifest_path} — skipping update")
        return False

    lines = manifest_path.read_text().splitlines(keepends=True)
    in_frontmatter = False
    past_frontmatter = False
    new_lines: list[str] = []
    changed = False

    for i, line in enumerate(lines):
        if i == 0 and line.rstrip() == "---":
            in_frontmatter = True
            new_lines.append(line)
        elif in_frontmatter and not past_frontmatter:
            if line.rstrip() == "---":
                past_frontmatter = True
                new_lines.append(line)
            elif line.startswith("status:") and "done" not in line:
                new_lines.append("status: done\n")
                changed = True
            else:
                new_lines.append(line)
        else:
            new_lines.append(line)

    if not changed:
        print(f"\n  {manifest_path}: status already 'done' — no commit needed")
        return False

    manifest_path.write_text("".join(new_lines))
    print(f"\n  ✓ {manifest_path} → status: done")

    _run(["git", "add", str(manifest_path)])
    _run(
        [
            "git",
            "commit",
            "-m",
            f"chore(manifest): backport finalized manifest for #{mr_iid}",
        ]
    )
    print("  ✓ Manifest status committed on develop")
    return True


def main(argv: list[str] | None = None) -> int:
    """Finalize a merged pull request end-to-end.

    Reads the cached PR and issue numbers from ``tmp/``, then in order:
    verifies the PR is merged, closes the linked issue, applies the 'Done'
    status label, fetches and switches to ``develop`` (ff-only), updates the
    manifest ``status: done``, and commits the change. Writes a summary to
    ``work_in_progress/finalize_results.json``.

    Args:
        argv: Optional CLI arguments (currently unused).

    Returns:
        int: 0 on success.

    Raises:
        SystemExit: If the token is missing, caches are absent, the PR is not
            merged, or any git operation fails.
    """
    del argv  # reserved for future flags; every input comes from files and the environment
    mr_data = read_json_file(TMP_DIR, "mr_response.json")
    issue_data = read_json_file(TMP_DIR, "issue_response.json")

    mr_iid = mr_data.get("number") or mr_data.get("iid")
    issue_iid = issue_data.get("number") or issue_data.get("iid")

    if not isinstance(mr_iid, int) or not isinstance(issue_iid, int):
        raise SystemExit(f"Invalid numbers: PR={mr_iid}, Issue={issue_iid} (expected integers)")

    branch = get_json_string_member(get_json_object_member(mr_data, "head"), "ref") or ""

    api = GitHubAPI()

    # --- API operations (abort early if PR not merged) ---
    mr_result = _verify_mr_merged(api, mr_iid)
    _close_issue(api, issue_iid)
    _set_issue_done(api, issue_iid)

    # --- Git operations ---
    _sync_develop()
    manifest_committed = _update_manifest(branch, mr_iid) if branch else False

    result: dict[str, object] = {
        "mrMerged": True,
        "mergedAt": get_json_string_member(mr_result, "merged_at"),
        "mergedBy": get_json_string_member(get_json_object_member(mr_result, "merged_by"), "login"),
        "issueClosed": True,
        "issueStatusDone": True,
        "manifestCommitted": manifest_committed,
        "sourceBranch": branch or None,
    }

    write_json_file(WORKING_DIR, "finalize_results.json", result)
    print(f"\n✓ Finalization complete. Results saved to {WORKING_DIR}/finalize_results.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
