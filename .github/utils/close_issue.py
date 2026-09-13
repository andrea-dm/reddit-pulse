"""Close a GitHub issue from work-in-progress response."""

from __future__ import annotations

import sys
from typing import cast

from _common import (
    APIResponseObject,
    GitHubAPI,
    get_json_string_member,
    read_json_file,
)
from _constants import TMP_DIR


def main(argv: list[str] | None = None) -> int:
    """Closes an open GitHub issue using metadata from a cached response file.

    Reads the issue number from the temporary response file, verifies
    its current state via the GitHub REST API, and issues a close transition
    event via a PATCH request if the issue is currently open.

    Args:
        argv: Optional CLI arguments (currently unused).

    Returns:
        int: An exit code where 0 indicates success and 1 indicates a runtime failure.

    Raises:
        SystemExit: If the GitHub token is missing from the environment, the
            underlying response file does not exist, or the cached issue number
            is malformed.
    """
    del argv  # reserved for future flags; every input comes from files and the environment
    issue_data = read_json_file(TMP_DIR, "issue_response.json")
    issue_iid = issue_data.get("number") or issue_data.get("iid")

    if not isinstance(issue_iid, int):
        raise SystemExit(f"Invalid issue number: {issue_iid}")

    api = GitHubAPI()
    url = f"issues/{issue_iid}"

    issue = cast(APIResponseObject, api.RequestObject(url, method="GET"))
    state = get_json_string_member(issue, "state") or ""
    print(f"Issue #{issue_iid} current state: {state}")

    if state == "closed":
        print("  Already closed ✓")
        return 0

    print(f"Closing issue #{issue_iid}…")
    api.RequestObject(url, method="PATCH", payload={"state": "closed"}, timeout=30)
    print(f"  ✓ Issue #{issue_iid} closed")

    return 0


if __name__ == "__main__":
    sys.exit(main())
