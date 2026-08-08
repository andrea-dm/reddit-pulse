"""Create a GitHub pull request from work-in-progress payload files."""

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
from _constants import TMP_DIR, WORKING_DIR


def main(argv: list[str] | None = None) -> int:
    """Creates a GitHub pull request from local configuration and payload files.

    Aggregates JSON metadata options and a Markdown body from the local
    work-in-progress directory, executes a POST request against the pull
    requests endpoint, and caches the response details.

    Args:
        argv: Optional CLI arguments (currently unused).

    Returns:
        int: An exit code where 0 indicates success and 1 indicates a runtime failure.

    Raises:
        SystemExit: If the GitHub token is missing from the environment or any
            required input payload metadata files are missing.
    """
    payload = read_json_file(WORKING_DIR, "mr.json")
    payload["body"] = read_text_file(WORKING_DIR, "mr.md")

    api = GitHubAPI()
    data = cast(
        APIResponseObject,
        api.RequestObject("pulls", method="POST", payload=payload),
    )

    # Inject backward compatibility mapping for pipeline contracts
    data_dict = dict(data)
    data_dict["iid"] = data.get("number")
    data_dict["web_url"] = get_json_string_member(data, "html_url")

    iid = data_dict.get("iid")
    web_url = data_dict.get("web_url") or ""
    print(f"✓ PR #{iid} created: {web_url}")

    write_json_file(TMP_DIR, "mr_response.json", data_dict)
    return 0


if __name__ == "__main__":
    sys.exit(main())
