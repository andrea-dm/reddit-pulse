"""Set a GitHub issue status label through the REST API."""

from __future__ import annotations

import os
import sys

from _common import GitHubAPI, read_json_file
from _constants import TMP_DIR


def main(argv: list[str] | None = None) -> int:
    """Modifies a GitHub issue's status using the REST API.

    Looks up the corresponding issue number using its local tracking configuration
    and applies a status label (retrieved from the SET_ISSUE_STATUS environment
    variable) to the issue.

    Args:
        argv: Optional CLI arguments (currently unused).

    Returns:
        int: An exit code where 0 indicates success and 1 indicates a configuration
            error or backend rejection.

    Raises:
        SystemExit: If the GitHub token is missing from the environment, the
            issue configuration file is missing, or the API returns an error.
    """
    del argv  # reserved for future flags; every input comes from files and the environment
    issue_data = read_json_file(TMP_DIR, "issue_response.json")
    issue_iid = issue_data.get("number") or issue_data.get("iid")

    if not isinstance(issue_iid, int):
        raise SystemExit(f"Invalid issue number: {issue_iid}")

    status = os.environ.get("SET_ISSUE_STATUS", "In progress")
    api = GitHubAPI()

    print(f"Setting issue #{issue_iid} status to {status!r}…")

    # GitHub standard issues track status via labels
    # rather than a complex GraphQL statusWidget
    payload = {"labels": [status]}
    api.RequestObject(f"issues/{issue_iid}/labels", method="POST", payload=payload)

    print(f"✓ Issue #{issue_iid} status label set to {status!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
