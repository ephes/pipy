#!/usr/bin/env python3
"""Client under test for the official MCP client conformance suite.

The suite starts a scenario server and runs this script with the server URL
as the last argument, the scenario name in ``MCP_CONFORMANCE_SCENARIO`` and
scenario data in ``MCP_CONFORMANCE_CONTEXT`` (Pi
``test/mcp-conformance/client.ts``).

It drives the code pipy runs for an HTTP server in ``mcp.json``:
``McpServerConnection`` connects and calls tools, and ``sign_in_mcp_server``
runs the OAuth sign-in that ``/mcp`` starts. The browser is simulated: the
authorization URL is fetched without following its redirect, and the redirect
is delivered to pipy's loopback callback server. Credentials are kept in a
temporary directory that is removed at the end.

Run through ``run.py``, which reads the outcome from the file named by
``PIPY_MCP_CONFORMANCE_REPORT``.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, TypeVar

from pipy_harness.native.mcp.config import McpServerEntry
from pipy_harness.native.mcp.connection import McpServerConnection
from pipy_harness.native.mcp.credentials import McpOAuthCredentialStore
from pipy_harness.native.mcp.sign_in import sign_in_mcp_server
from pipy_harness.native.version_check import pipy_version

_T = TypeVar("_T")

# Sign-ins pipy asks the user for are not limited, so a user who keeps
# approving them would loop forever against a server that never accepts the
# granted scope. This simulated user gives up after three, the limit
# ``auth/scope-retry-limit`` checks.
MAX_SIGN_INS = 3
REQUEST_TIMEOUT_SECONDS = 20
SERVER_NAME = "conformance"
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})

ToolCall = tuple[str, dict[str, Any]]


def tool_calls(
    scenario: str, tools: Sequence[Mapping[str, Any]]
) -> list[ToolCall] | None:
    """The tool calls of a scenario after connecting; ``None`` for a
    scenario this client does not know."""

    if scenario.startswith("auth/"):
        return [("test-tool", {})]
    if scenario == "initialize":
        return []
    if scenario == "tools_call":
        return [("add_numbers", {"a": 2, "b": 3})]
    if scenario == "sse-retry":
        return [("test_reconnection", {})]
    if scenario == "elicitation-sep1034-client-defaults":
        return [("test_client_elicitation_defaults", {})]
    if scenario == "json-schema-2020-12-preservation":
        # The server compares the echoed schema with the one it listed, to
        # detect dropped keywords.
        for tool in tools:
            if tool.get("name") == "json_schema_2020_12_tool":
                return [("json_schema_echo", {"schema": tool.get("inputSchema")})]
        raise RuntimeError("json_schema_2020_12_tool was not listed")
    return None


def log(message: str) -> None:
    sys.stderr.write(f"[pipy-conformance] {message}\n")
    sys.stderr.flush()


def read_context(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError("MCP_CONFORMANCE_CONTEXT is not an object")
    return parsed


def server_config(server_url: str, context: Mapping[str, Any]) -> dict[str, Any]:
    """The ``mcp.json`` entry of the scenario server."""

    config: dict[str, Any] = {"url": server_url, "timeout": REQUEST_TIMEOUT_SECONDS}
    client_id = context.get("client_id")
    if isinstance(client_id, str):
        # Pre-registered clients are configured in ``mcp.json``.
        oauth: dict[str, Any] = {"clientId": client_id}
        secret = context.get("client_secret")
        if isinstance(secret, str):
            oauth["clientSecret"] = secret
        config["oauth"] = oauth
    return config


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


def _origin_and_path(url: str) -> str:
    parts = urllib.parse.urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}{parts.path}"


def visit_authorization_url(url: str) -> None:
    """What a browser does with the authorization URL when the user approves
    at once."""

    opener = urllib.request.build_opener(_NoRedirect)
    try:
        response = opener.open(url, timeout=REQUEST_TIMEOUT_SECONDS)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        status = response.status
        location = response.headers.get("Location")
    if not location:
        raise RuntimeError(
            f"Authorization endpoint answered {status} without a redirect"
        )
    callback = urllib.parse.urljoin(url, location)
    parts = urllib.parse.urlsplit(callback)
    if parts.scheme != "http" or parts.hostname not in _LOOPBACK_HOSTS:
        raise RuntimeError(
            f"Authorization endpoint redirected to {parts.scheme}://{parts.netloc}, "
            "not to the loopback callback"
        )
    log(f"delivering authorization response to {_origin_and_path(callback)}")
    with urllib.request.urlopen(callback, timeout=REQUEST_TIMEOUT_SECONDS) as page:
        page.read()


class SimulatedBrowser:
    """A sign-in prompt whose user approves in a browser at once."""

    def __init__(self, visit: Callable[[str], None] = visit_authorization_url) -> None:
        self._visit = visit
        self._visited = threading.Event()
        self._failure: Exception | None = None

    def show_authorization_url(self, url: str) -> None:
        log(f"authorization URL: {_origin_and_path(url)}")
        threading.Thread(
            target=self._run, args=(url,), name="conformance-browser", daemon=True
        ).start()

    def _run(self, url: str) -> None:
        try:
            self._visit(url)
        except Exception as error:  # noqa: BLE001 - cross-thread capture: reported by the prompt
            self._failure = error
        finally:
            self._visited.set()

    def prompt_for_redirect_url(self, cancel_event: threading.Event) -> str | None:
        while not self._visited.wait(0.05):
            if cancel_event.is_set():
                return None
        if self._failure is not None:
            # Cancels the sign-in instead of waiting for a callback that
            # never comes.
            log(f"browser failed: {self._failure}")
            return None
        cancel_event.wait()
        return None


def run(server_url: str, scenario: str, context: Mapping[str, Any], home: Path) -> None:
    entry = McpServerEntry(
        name=SERVER_NAME, config=server_config(server_url, context), source=SERVER_NAME
    )
    credentials = McpOAuthCredentialStore(home / "mcp-auth.json")
    connection = McpServerConnection(
        entry=entry,
        cwd=home,
        client_version=pipy_version(),
        on_tools=lambda _connection: None,
        credentials=credentials,
    )
    sign_ins = 0

    def with_sign_in(operation: Callable[[], _T]) -> _T:
        """Run ``operation``, signing in like a user answering ``/mcp``
        whenever the server asks for it."""

        nonlocal sign_ins
        while True:
            try:
                return operation()
            except Exception:
                url = connection.oauth_url
                if connection.state != "needs-auth" or url is None:
                    raise
                if sign_ins >= MAX_SIGN_INS:
                    raise RuntimeError(
                        f"Still requires sign-in after {sign_ins} sign-ins"
                    ) from None
                sign_ins += 1
                challenge = connection.challenge
                scope = None if challenge is None else challenge.scope
                log(f"sign-in {sign_ins}" + (f" (scope: {scope})" if scope else ""))
                sign_in_mcp_server(
                    server_url=url,
                    store=credentials.for_server(entry.name, url),
                    settings=connection.oauth_settings(),
                    challenge=challenge,
                    prompt=SimulatedBrowser(),
                )
                connection.clear_challenge()

    try:
        with_sign_in(connection.get_client)
        names = ", ".join(str(tool.get("name")) for tool in connection.tools)
        log(f"connected, tools: {names or '(none)'}")
        calls = tool_calls(scenario, connection.tools)
        if calls is None:
            raise RuntimeError(f"Unknown scenario {scenario}")
        for name, arguments in calls:
            result = with_sign_in(
                lambda name=name, arguments=arguments: connection.call_tool(
                    name, arguments
                )
            )
            content = json.dumps(result.get("content"))
            log(f"{name}: {content}")
            if result.get("isError"):
                raise RuntimeError(f"Tool {name} failed: {content}")
    finally:
        connection.close()


def main(argv: Sequence[str], environ: Mapping[str, str]) -> int:
    scenario = environ.get("MCP_CONFORMANCE_SCENARIO")
    report_path = environ.get("PIPY_MCP_CONFORMANCE_REPORT")
    report: dict[str, Any]
    if len(argv) < 2 or not scenario:
        report = {
            "success": False,
            "error": "Usage: MCP_CONFORMANCE_SCENARIO=<scenario> client.py <server-url>",
        }
    else:
        try:
            context = read_context(environ.get("MCP_CONFORMANCE_CONTEXT"))
            with tempfile.TemporaryDirectory(prefix="pipy-mcp-conformance-") as home:
                run(argv[-1], scenario, context, Path(home))
            report = {"success": True}
        except Exception as error:  # noqa: BLE001 - the outcome of the scenario is the report
            report = {"success": False, "error": str(error) or type(error).__name__}
    if "error" in report:
        log(f"error: {report['error']}")
    if report_path:
        Path(report_path).write_text(json.dumps(report, indent=2) + "\n")
    return 0 if report["success"] else 1


if __name__ == "__main__":
    code = main(sys.argv, os.environ)
    sys.stdout.flush()
    sys.stderr.flush()
    # Exit without waiting for threads: a failed scenario can leave a request
    # to the server under test pending.
    os._exit(code)
