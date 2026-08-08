"""Fetch and print a GitHub pull request from work-in-progress metadata."""

from __future__ import annotations

import sys

from _common import (
    get_json_object_member,
    get_json_string_member,
    read_json_file,
)
from _constants import WORKING_DIR


def main(argv: list[str] | None = None) -> int:
    """Parses and prints metrics from a local pull request payload.

    Extracts structural data fields including the pull request tracking number,
    title, system operational state, resolution timestamps, merging actor
    username, and web URL from disk to output to standard output.

    Args:
        argv: Optional CLI arguments (currently unused).

    Returns:
        int: An exit code where 0 indicates success and 1 indicates a runtime failure.

    Raises:
        SystemExit: If the targeted pull request metadata configuration file is absent.
    """
    data = read_json_file(WORKING_DIR, "mr.json")

    iid = data.get("number") or data.get("iid")
    title = get_json_string_member(data, "title") or "<untitled>"
    state = get_json_string_member(data, "state") or "unknown"
    merged_at = get_json_string_member(data, "merged_at") or "not merged"

    # GitHub exposes the username under 'login'
    merged_by = (
        get_json_string_member(get_json_object_member(data, "merged_by"), "login")
        or get_json_string_member(get_json_object_member(data, "merged_by"), "username")
        or "N/A"
    )
    web_url = (
        get_json_string_member(data, "html_url")
        or get_json_string_member(data, "web_url")
        or ""
    )

    print(f"PR #{iid}: {title}")
    print(f"  State:     {state}")
    print(f"  Merged at: {merged_at}")
    print(f"  Merged by: {merged_by}")
    print(f"  URL:       {web_url}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
