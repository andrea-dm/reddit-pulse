"""Create a GitHub issue from work-in-progress payload files."""

from __future__ import annotations

import sys
import urllib.parse
from typing import cast

from _common import (
    APIResponseObject,
    GitHubAPI,
    get_json_nested_object_list,
    get_json_string_member,
    get_json_value_list_member,
    read_json_file,
    read_text_file,
    write_json_file,
)
from _constants import GITHUB_REPO, TMP_DIR, WORKING_DIR


def _dedup_url(search_term: str) -> str:
    q = f"repo:{GITHUB_REPO} is:issue state:open {search_term}"
    query = urllib.parse.urlencode({"q": q, "per_page": "20"})
    return f"search/issues?{query}"


def _assignee_usernames(issue: APIResponseObject) -> list[str]:
    assignees = get_json_nested_object_list(issue, "assignees")
    return [username for user in assignees if (username := get_json_string_member(user, "login"))]


def _label_list(meta: APIResponseObject) -> list[str]:
    raw = get_json_value_list_member(meta, "labels")
    return [item for item in raw if isinstance(item, str)]


def main() -> int:
    """Creates a new GitHub issue from local work-in-progress payload files.

    Reads the issue title and labels from `work_in_progress/issue.json`
    and the description body from `work_in_progress/issue.md`. Uses the
    first five words of the description to query the REST API for potential
    duplicate open issues, then POSTs the full payload and writes the API
    response to `tmp/issue_response.json` with pipeline compatibility mappings.

    Returns:
        int: An exit code where 0 indicates success and 1 indicates a runtime failure.

    Raises:
        SystemExit: If the GitHub token is missing from the environment or any
            required payload file cannot be located.
    """
    api = GitHubAPI()
    meta = cast(APIResponseObject, read_json_file(WORKING_DIR, "issue.json"))
    description = read_text_file(WORKING_DIR, "issue.md")

    title = get_json_string_member(meta, "title")
    if not title:
        raise SystemExit("issue.json is missing a non-empty 'title' field")

    search_terms = description.split()[:5]
    search_term = " ".join(search_terms) if search_terms else "issue"

    existing = cast(
        list[APIResponseObject],
        api.RequestObject(_dedup_url(search_term), "GET", output_type=list),
    )

    if existing:
        print(f"⚠ Found {len(existing)} existing issue(s) with similar terms:")
        for issue in existing:
            iid = issue.get("number")
            existing_title = get_json_string_member(issue, "title") or "<untitled>"
            print(f"  #{iid}: {existing_title}")
        print("Proceeding anyway (use git reset if this is wrong)")

    payload: dict[str, object] = {
        "title": title,
        "body": description,
    }

    labels = _label_list(meta)
    if labels:
        payload["labels"] = labels

    data = cast(
        APIResponseObject,
        api.RequestObject("issues", "POST", payload=payload),
    )

    # Inject backward compatibility mapping for pipeline contracts
    data_dict = dict(data)
    data_dict["iid"] = data.get("number")
    data_dict["web_url"] = get_json_string_member(data, "html_url")

    print("✓ Issue created:")
    print(f"  IID: {data_dict.get('iid')}")
    print(f"  URL: {data_dict.get('web_url')}")
    print(f"  Assignees: {_assignee_usernames(data_dict)}")

    write_json_file(TMP_DIR, "issue_response.json", data_dict)
    return 0


if __name__ == "__main__":
    sys.exit(main())
