"""Fetch and print a GitHub issue from work-in-progress response."""

from __future__ import annotations

import sys

from _common import (
    get_json_string_member,
    read_json_file,
)
from _constants import TMP_DIR


def main(argv: list[str] | None = None) -> int:
    """Parses and prints key details from a cached GitHub issue response.

    Extracts structural components such as the issue local identifier, title,
    current state, and web URL from the local tracking file and outputs them
    to standard output.

    Args:
        argv: Optional CLI arguments (currently unused).

    Returns:
        int: An exit code where 0 indicates success and 1 indicates a runtime failure.

    Raises:
        SystemExit: If the underlying issue JSON tracking file cannot be found.
    """
    data = read_json_file(TMP_DIR, "issue_response.json")

    iid = data.get("number") or data.get("iid")
    title = get_json_string_member(data, "title") or "<untitled>"
    state = get_json_string_member(data, "state") or "unknown"
    web_url = (
        get_json_string_member(data, "html_url")
        or get_json_string_member(data, "web_url")
        or ""
    )

    print(f"Issue #{iid}: {title}")
    print(f"  State: {state}")
    print(f"  URL:   {web_url}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
