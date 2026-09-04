"""Shared GitHub API helpers for housekeeping scripts."""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Mapping
from http import HTTPStatus
from pathlib import Path
from typing import Any, Literal, cast
from urllib.request import Request, urlopen

from _constants import GITHUB_BASE_URL, GITHUB_REPO, TOKEN_ENV_VAR

type APIResponseObject = Mapping[str, Any]
"""Read-only view of a JSON object returned by GitHub."""


def get_json_object_member(source: APIResponseObject, key: str) -> APIResponseObject:
    value = source.get(key)
    if isinstance(value, dict):
        return cast(APIResponseObject, value)
    return {}


def get_json_nested_object(source: APIResponseObject, *keys: str) -> APIResponseObject:
    current = source
    for key in keys:
        current = get_json_object_member(current, key)
        if not current:
            return {}
    return current


def get_json_object_list_member(source: APIResponseObject, key: str) -> list[APIResponseObject]:
    value = source.get(key)
    if not isinstance(value, list):
        return []
    items = cast(list[object], value)
    return [cast(APIResponseObject, item) for item in items if isinstance(item, dict)]


def get_json_nested_object_list(source: APIResponseObject, *keys: str) -> list[APIResponseObject]:
    key_path = list(keys)
    if not key_path:
        return []
    list_key = key_path.pop()
    parent = get_json_nested_object(source, *key_path) if key_path else source
    return get_json_object_list_member(parent, list_key)


def get_json_value_list_member(source: APIResponseObject, key: str) -> list[object]:
    value = source.get(key)
    if isinstance(value, list):
        return cast(list[object], value)
    return []


def get_json_string_member(source: APIResponseObject, key: str) -> str | None:
    value = source.get(key)
    return value if isinstance(value, str) else None


def load_json_object_list(payload: bytes) -> list[APIResponseObject]:
    loaded = cast(object, json.loads(payload.decode("utf-8")))
    if not isinstance(loaded, list):
        raise TypeError("Expected a JSON list response")
    items = cast(list[object], loaded)
    return [cast(APIResponseObject, item) for item in items if isinstance(item, dict)]


def request_json_object_list(
    request: Request,
    timeout: float,
) -> list[APIResponseObject]:
    with urlopen(request, timeout=timeout) as response:
        return load_json_object_list(response.read())


def read_json_file(directory: str, filename: str) -> dict[str, object]:
    filepath = Path(directory) / filename
    if not filepath.is_file():
        raise SystemExit(f"File not found: {filepath}")
    return cast(dict[str, object], json.loads(filepath.read_text()))


def write_json_file(directory: str, filename: str, data: object) -> None:
    target_dir = Path(directory)
    target_dir.mkdir(parents=True, exist_ok=True)
    filepath = target_dir / filename
    filepath.write_text(f"{json.dumps(data, indent=2)}\n")


def read_text_file(directory: str, filename: str) -> str:
    filepath = Path(directory) / filename
    if not filepath.is_file():
        raise SystemExit(f"File not found: {filepath}")
    return filepath.read_text()


def _get_proxy() -> str:
    try:
        return subprocess.check_output(
            ["git", "config", "--global", "--get", "https.proxy"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except subprocess.CalledProcessError:
        return ""


def _get_token(token_var: str) -> str:
    try:
        token = subprocess.check_output(
            ["cmd", "/c", f"echo %{token_var}%"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
        if token and token != f"%{token_var}%":
            return token
    except (FileNotFoundError, OSError):
        pass
    return os.environ.get(token_var, "")


class GitHubAPI:
    def __init__(self) -> None:
        """Initialize the API client and validate the environment token."""
        if not (token := _get_token(TOKEN_ENV_VAR)):
            raise SystemExit(f"GitHub token not found. Set the '{TOKEN_ENV_VAR}' environment variable.")
        self._token = token

    @staticmethod
    def _errors(response: APIResponseObject) -> list[object]:
        """Extract top-level errors from a GraphQL response."""
        value = response.get("errors")
        if isinstance(value, list):
            return cast(list[object], value)
        return []

    def graphql_endpoint(self) -> str:
        """Build the GitHub GraphQL endpoint URL."""
        api_base_url = GITHUB_BASE_URL.rstrip("/")
        return f"{api_base_url}/graphql"

    #: Top-level API endpoints that are not scoped under a repository.
    _UNSCOPED_PREFIXES = ("repos/", "search/", "graphql")

    @staticmethod
    def _build_url(path: str) -> str:
        """Build the GitHub API URL from a path."""
        if path.startswith("http"):
            return path
        if GITHUB_REPO and not path.startswith(GitHubAPI._UNSCOPED_PREFIXES):
            return f"{GITHUB_BASE_URL}/repos/{GITHUB_REPO.rstrip('/')}/{path}"
        return f"{GITHUB_BASE_URL}/{path}"

    def _build_cmd(
        self,
        method: str,
        url: str,
        payload: APIResponseObject | None,
    ) -> list[str]:
        """Build a curl command for the GitHub API request."""
        proxy = _get_proxy()
        cmd: list[str] = ["curl"]
        if proxy:
            cmd += ["--proxy", proxy, "--proxy-anyauth"]

        cmd += [
            "-s",
            "-w",
            "\nHTTP_STATUS:%{http_code}",
            "-X",
            method,
            url,
            "-H",
            f"Authorization: Bearer {self._token}",
            "-H",
            "Accept: application/vnd.github+json",
            "-H",
            "X-GitHub-Api-Version: 2022-11-28",
        ]

        if payload is not None:
            cmd += ["-H", "Content-Type: application/json", "-d", json.dumps(payload)]

        return cmd

    @staticmethod
    def _parse_response(output: str) -> tuple[dict[str, Any], int]:
        """Parse HTTP status code and JSON body from curl output."""
        if "\nHTTP_STATUS:" in output:
            resp_body, _, status_part = output.rpartition("\nHTTP_STATUS:")
            status_code = int(status_part.strip())
        else:
            resp_body = output
            status_code = 0

        parsed: dict[str, Any] = {}
        if resp_body.strip():
            try:
                parsed = json.loads(resp_body)
            except json.JSONDecodeError as e:
                raise SystemExit(f"Failed to parse JSON response: {resp_body}") from e

        return parsed, status_code

    @staticmethod
    def _convert_output(
        parsed: dict[str, Any] | list[APIResponseObject],
        output_type: type,
    ) -> APIResponseObject | list[APIResponseObject]:
        """Convert parsed response to requested output type."""
        if output_type is list:
            if isinstance(parsed, dict) and "items" in parsed:
                return cast(list[APIResponseObject], parsed["items"])
            if isinstance(parsed, dict) and len(parsed) == 0:
                return cast(list[APIResponseObject], [])
            if not isinstance(parsed, list):
                raise TypeError("Expected a JSON list response")
            return parsed

        return cast(APIResponseObject, parsed)

    def RequestObject(
        self,
        path: str,
        method: Literal["GET", "POST", "PATCH", "PUT", "DELETE"] = "GET",
        *,
        payload: APIResponseObject | None = None,
        output_type: type = dict,
        timeout: float = 30,
    ) -> APIResponseObject | list[APIResponseObject]:
        """Execute a request and parse a JSON object response via curl."""
        url = self._build_url(path)
        cmd = self._build_cmd(method, url, payload)

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)

        parsed, status_code = self._parse_response(result.stdout)

        if status_code >= HTTPStatus.BAD_REQUEST:
            raise SystemExit(f"HTTPError {status_code}: {parsed}")

        return self._convert_output(parsed, output_type)

    def ExecuteGQLStatement(self, query: str, label: str = "") -> APIResponseObject:
        """Run a GraphQL query and raise on top-level errors."""
        response = cast(
            APIResponseObject,
            self.RequestObject(
                self.graphql_endpoint(),
                method="POST",
                payload={"query": query},
                timeout=60,
            ),
        )
        if errs := self._errors(response):
            details = f" ({label})" if label else ""
            msg = f"GraphQL errors{details}: {errs}"
            raise SystemExit(msg)
        return response


__all__ = [
    "APIResponseObject",
    "GitHubAPI",
    "get_json_nested_object",
    "get_json_nested_object_list",
    "get_json_object_list_member",
    "get_json_object_member",
    "get_json_string_member",
    "get_json_value_list_member",
    "load_json_object_list",
    "read_json_file",
    "read_text_file",
    "request_json_object_list",
    "write_json_file",
]
