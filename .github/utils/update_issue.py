"""Update an existing GitHub issue from work-in-progress payload files."""

from __future__ import annotations

import sys
from typing import cast

from _common import (
    APIResponseObject,
    GitHubAPI,
    get_json_string_member,
    read_json_file,
    read_text_file,
    write_json_file,
)
from _constants import GITHUB_REPO, TMP_DIR, WORKING_DIR


def _resolve_issue_number() -> int:
    """Resolve the target issue number from local state files.

    Prefers ``tmp/issue_response.json`` (the cache written by
    ``create_issue.py``); falls back to ``work_in_progress/issue.json``
    when the cache is absent. Both carry a ``number`` (or legacy ``iid``)
    member.
    """
    for directory, filename in (
        (TMP_DIR, "issue_response.json"),
        (WORKING_DIR, "issue.json"),
    ):
        try:
            data = cast(APIResponseObject, read_json_file(directory, filename))
        except SystemExit:
            continue
        number = data.get("number") or data.get("iid")
        if isinstance(number, int):
            return number
    raise SystemExit(
        "Cannot resolve the issue number: neither tmp/issue_response.json "
        "nor work_in_progress/issue.json carries a 'number' field"
    )


def main() -> int:
    """Updates an existing GitHub issue from local work-in-progress files.

    Reads the replacement description body from `work_in_progress/issue.md`
    and an optional replacement title from `work_in_progress/issue.json`,
    resolves the issue number from the local response cache, then PATCHes
    the issue and refreshes `tmp/issue_response.json` with pipeline
    compatibility mappings. Labels, state, and assignees are untouched.

    Returns:
        int: An exit code where 0 indicates success and 1 indicates a runtime failure.

    Raises:
        SystemExit: If the GitHub token is missing from the environment, a
            required payload file cannot be located, or the issue number
            cannot be resolved.
    """
    api = GitHubAPI()
    body = read_text_file(WORKING_DIR, "issue.md")
    if not body.strip():
        raise SystemExit("issue.md is empty — refusing to blank the issue body")

    issue_iid = _resolve_issue_number()

    payload: dict[str, object] = {"body": body}
    try:
        meta = cast(APIResponseObject, read_json_file(WORKING_DIR, "issue.json"))
    except SystemExit:
        meta = cast(APIResponseObject, {})
    title = get_json_string_member(meta, "title")
    if title:
        payload["title"] = title

    print(f"Updating issue #{issue_iid} ({len(body)} body chars)…")

    # Full "repos/…" path sidesteps the GITHUB_REPO trailing-slash join bug
    # that corrupts relative paths built by _build_url.
    path = f"repos/{GITHUB_REPO.strip('/')}/issues/{issue_iid}"
    data = cast(APIResponseObject, api.RequestObject(path, "PATCH", payload=payload))

    data_dict = dict(data)
    data_dict["iid"] = data.get("number")
    data_dict["web_url"] = get_json_string_member(data, "html_url")

    print("✓ Issue updated:")
    print(f"  IID: {data_dict.get('iid')}")
    print(f"  URL: {data_dict.get('web_url')}")

    write_json_file(TMP_DIR, "issue_response.json", data_dict)
    return 0


if __name__ == "__main__":
    sys.exit(main())
