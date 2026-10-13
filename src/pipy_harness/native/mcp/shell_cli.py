"""``pipy mcp``: add, remove and check MCP servers outside a session.

Agents run these commands through bash to configure servers and to verify an
``mcp.json`` they wrote. Nothing here starts a session or loads extensions,
so only the servers of the two ``mcp.json`` files are seen, and the switches
that turn MCP off for a session (``--no-mcp``, ``-builtin:mcp``) do not apply.

``add`` and ``remove`` only write a file. ``list`` connects to every enabled
server, at the same time, and closes each connection again. A server that
signs in with OAuth is sent the token stored in ``mcp-auth.json`` of the
config home, refreshed when needed; one that wants a sign-in is listed as
``needs sign-in``.

The project file is read only when a trust decision for the project is
already saved; the commands never ask.

Where this differs from Pi:

- ``login`` and ``logout`` are not built yet and say so.
- ``--oauth-callback-port`` is read as a decimal number; JavaScript's other
  number spellings (``0x50``) are not ports here.
- The usage text is plain, without bold or dim characters.
- A trust store that cannot be read is an error line and exit code 1.
"""

from __future__ import annotations

import json
import re
import sys
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final, Literal

from pipy_harness.native.fuzzy import js_trim
from pipy_harness.native.mcp.config import (
    DEFAULT_MCP_EXPOSURE,
    LoadedMcpConfig,
    McpConfigError,
    McpServerEntry,
    add_mcp_server_config,
    get_mcp_tool_exposure,
    global_mcp_config_path,
    load_mcp_config,
    project_mcp_config_path,
    remove_mcp_server_config,
    validate_mcp_server_config,
)
from pipy_harness.native.mcp.connection import (
    McpServerConnection,
    McpServerConnectionError,
    McpTransportFactory,
    create_default_transport,
)
from pipy_harness.native.mcp.credentials import (
    McpOAuthCredentialStore,
    mcp_auth_path,
)
from pipy_harness.native.mcp.log import McpServerLog, mcp_log_path
from pipy_harness.native.mcp.protocol import McpAbortError
from pipy_harness.native.project_trust import (
    TRUST_FILENAME,
    ProjectTrustError,
    ProjectTrustStore,
)

APP_NAME: Final = "pipy"

HELP: Final = f"""Usage:
  {APP_NAME} mcp add <server> [options] -- <command> [args...]
  {APP_NAME} mcp add <server> [options] --url <url>
  {APP_NAME} mcp remove <server> [-l]
  {APP_NAME} mcp list [--json]

Configure and check MCP servers without starting a session.
Reads mcp.json in the {APP_NAME} config home and, in trusted projects, .pipy/mcp.json.

Commands:
  add <server>            Add or replace a server in mcp.json
  remove <server>         Remove a server from mcp.json
  list                    Show state, tools, and errors (exits 1 on failure)

Options for add and remove:
  -l, --local             Use .pipy/mcp.json in the current project instead of the global file

Options for add:
  --url <url>             Streamable HTTP server URL (instead of a command)
  --env <KEY=VALUE>       Environment variable for a stdio server (repeatable)
  --cwd <dir>             Working directory for a stdio server
  --header <KEY=VALUE>    HTTP header (repeatable)
  --bearer-token-env-var <NAME>
                          Send "Authorization: Bearer ${{NAME}}"
  --oauth-client-id <id>  Pre-registered OAuth client id
  --oauth-client-secret <secret>
                          OAuth client secret (may be ${{NAME}} or !command)
  --oauth-callback-port <port>
                          Fixed OAuth callback port
  --oauth-client-name <name>
                          Client name sent when registering with the OAuth server
  --exposure <mode>       codemode (default), deferred, direct, or hidden
  --description <text>    What the server offers, shown in the system prompt

Other options:
  --json                  Print the list as JSON"""

HELP_HINT: Final = f'Use "{APP_NAME} mcp --help" for usage.'

# Commands of Pi that need OAuth, which is not built yet.
_NOT_BUILT: Final = ("login", "logout")

Sink = Callable[[str], None]
_OptionKind = Literal["flag", "value", "list"]

# Short spellings of options. ``-l``/``--local`` match ``pipy install``.
_OPTION_ALIASES: Final[Mapping[str, str]] = {"-l": "--local"}

_ADD_OPTIONS: Final[Mapping[str, _OptionKind]] = {
    "local": "flag",
    "url": "value",
    "env": "list",
    "cwd": "value",
    "header": "list",
    "bearer-token-env-var": "value",
    "oauth-client-id": "value",
    "oauth-client-secret": "value",
    "oauth-callback-port": "value",
    "oauth-client-name": "value",
    "exposure": "value",
    "description": "value",
}
_HTTP_ONLY: Final = (
    "header",
    "bearer-token-env-var",
    "oauth-client-id",
    "oauth-client-secret",
    "oauth-callback-port",
    "oauth-client-name",
)
_STDIO_ONLY: Final = ("env", "cwd")
# The OAuth options that are written as they are, with their config key.
_OAUTH_TEXT_OPTIONS: Final = (
    ("oauth-client-id", "clientId"),
    ("oauth-client-secret", "clientSecret"),
)

_DECIMAL: Final = re.compile(r"[+-]?(?:[0-9]+\.?[0-9]*|\.[0-9]+)(?:[eE][+-]?[0-9]+)?")
_SURROGATE: Final = re.compile("[\ud800-\udfff]")


@dataclass(frozen=True, slots=True)
class McpCommandOptions:
    """Where a ``pipy mcp`` command runs and where it reports.

    ``config_home`` holds the user-level ``mcp.json``, ``mcp.log`` and
    ``mcp-auth.json``.
    ``client_version`` is the version sent to servers. ``log`` gets the
    command's output and ``error`` its error lines; they default to stdout and
    stderr. ``trust_store`` defaults to the store in ``config_home``.
    """

    cwd: Path
    config_home: Path
    client_version: str
    log: Sink | None = None
    error: Sink | None = None
    trust_store: ProjectTrustStore | None = None
    create_transport: McpTransportFactory = create_default_transport


def _printable(text: str) -> str:
    """Text a stream can encode: a lone surrogate becomes U+FFFD, as in Node."""

    return _SURROGATE.sub("�", text)


def _to_stdout(line: str) -> None:
    print(_printable(line))


def _to_stderr(line: str) -> None:
    print(_printable(line), file=sys.stderr)


@dataclass(slots=True)
class _ParsedOptions:
    positional: list[str] = field(default_factory=list)
    values: dict[str, str | bool] = field(default_factory=dict)
    # Values of ``list`` options, in order.
    lists: dict[str, list[str]] = field(default_factory=dict)

    def has(self, option: str) -> bool:
        return option in self.values or option in self.lists

    def text(self, option: str) -> str | None:
        found = self.values.get(option)
        return found if isinstance(found, str) else None


def _parse_options(
    args: list[str],
    known: Mapping[str, _OptionKind],
    error: Sink,
    max_positionals: int | None = None,
) -> _ParsedOptions | None:
    """Parse ``--name value`` options; reports an unknown one and returns None.

    ``--`` ends the options, as does reaching ``max_positionals`` positional
    arguments: the remaining arguments are positional, so a command's own
    options (``add <server> <command> --flag``) are passed through.
    """

    parsed = _ParsedOptions()
    index = 0
    while index < len(args):
        arg = _OPTION_ALIASES.get(args[index], args[index])
        full = max_positionals is not None and len(parsed.positional) >= max_positionals
        if arg == "--" or full:
            parsed.positional.extend(args[index + 1 if arg == "--" else index :])
            break
        index += 1
        if not arg.startswith("--"):
            parsed.positional.append(arg)
            continue
        name = arg[2:]
        kind = known.get(name)
        if kind is None:
            error(f"Unknown option {arg}.\n{HELP_HINT}")
            return None
        if kind == "flag":
            parsed.values[name] = True
            continue
        if index >= len(args):
            error(f"{arg} needs a value.")
            return None
        value = args[index]
        index += 1
        if kind == "list":
            parsed.lists.setdefault(name, []).append(value)
        else:
            parsed.values[name] = value
    return parsed


def _parse_pairs(
    option: str, pairs: list[str] | None, error: Sink
) -> dict[str, str] | None:
    """The ``KEY=VALUE`` pairs of a repeatable option as a dict."""

    record: dict[str, str] = {}
    for pair in pairs or ():
        key, separator, value = pair.partition("=")
        if not separator or not key:
            error(f'--{option} expects KEY=VALUE, got "{pair}".')
            return None
        record[key] = value
    return record


def _port_number(text: str) -> int | float:
    """The number a port option names, or NaN, which no port check accepts."""

    trimmed = js_trim(text)
    if not _DECIMAL.fullmatch(trimmed):
        return float("nan")
    number = float(trimmed)
    return int(number) if number.is_integer() else number


def _is_trusted(options: McpCommandOptions) -> bool:
    """Whether a decision to trust the project is saved. Never asks."""

    store = options.trust_store or ProjectTrustStore(
        options.config_home / TRUST_FILENAME
    )
    return store.get(options.cwd) is True


def run_mcp_command(args: list[str], options: McpCommandOptions) -> int:
    """Run ``pipy mcp <args>`` and return the exit code."""

    log = options.log or _to_stdout
    error = options.error or _to_stderr
    if not args or args[0] == "help" or "--help" in args or "-h" in args:
        log(HELP)
        return 0
    command, rest = args[0], args[1:]
    try:
        if command == "add":
            return _add(rest, options, log, error)
        if command == "remove":
            return _remove(rest, options, log, error)
        if command == "list":
            return _list_command(rest, options, log, error)
    except ProjectTrustError as exc:
        error(str(exc))
        return 1
    if command in _NOT_BUILT:
        error(f"{APP_NAME} mcp {command} is not available yet.")
        return 1
    error(f'Unknown mcp command "{command}".\n{HELP_HINT}')
    return 1


def _http_config(
    parsed: _ParsedOptions, url: str, error: Sink
) -> dict[str, Any] | None:
    headers = _parse_pairs("header", parsed.lists.get("header"), error)
    if headers is None:
        return None
    bearer = parsed.text("bearer-token-env-var")
    if bearer is not None:
        headers["Authorization"] = f"Bearer ${{{bearer}}}"
    oauth: dict[str, Any] = {}
    for option, key in _OAUTH_TEXT_OPTIONS:
        value = parsed.text(option)
        if value is not None:
            oauth[key] = value
    port = parsed.text("oauth-callback-port")
    if port is not None:
        oauth["callbackPort"] = _port_number(port)
    client_name = parsed.text("oauth-client-name")
    if client_name is not None:
        oauth["clientName"] = client_name
    config: dict[str, Any] = {"url": url}
    if headers:
        config["headers"] = headers
    if oauth:
        config["oauth"] = oauth
    return config


def _stdio_config(
    parsed: _ParsedOptions, command: list[str], error: Sink
) -> dict[str, Any] | None:
    env = _parse_pairs("env", parsed.lists.get("env"), error)
    if env is None:
        return None
    config: dict[str, Any] = {"command": command[0]}
    if len(command) > 1:
        config["args"] = command[1:]
    if env:
        config["env"] = env
    cwd = parsed.text("cwd")
    if cwd is not None:
        config["cwd"] = cwd
    return config


def _target(parsed: _ParsedOptions, options: McpCommandOptions) -> tuple[Path, str]:
    """The file ``add`` and ``remove`` change, and its scope."""

    if parsed.has("local"):
        return project_mcp_config_path(options.cwd), "project"
    return global_mcp_config_path(options.config_home), "global"


def _entry_to_add(
    parsed: _ParsedOptions, error: Sink
) -> tuple[str, dict[str, Any]] | None:
    """The name and the validated config ``add`` was asked to write."""

    name = parsed.positional[0] if parsed.positional else ""
    command = parsed.positional[1:]
    url = parsed.text("url")
    if not name or (url is None) == (not command):
        error(
            f"Usage: {APP_NAME} mcp add <server> [options] "
            f"(--url <url> | -- <command> [args...])\n{HELP_HINT}"
        )
        return None
    misplaced = next(
        filter(parsed.has, _HTTP_ONLY if url is None else _STDIO_ONLY), None
    )
    if misplaced is not None:
        applies_to = "HTTP servers (--url)" if url is None else "stdio servers"
        error(f"--{misplaced} only applies to {applies_to}.")
        return None
    if url is None:
        config = _stdio_config(parsed, command, error)
    else:
        config = _http_config(parsed, url, error)
    if config is None:
        return None
    for option in ("exposure", "description"):
        value = parsed.text(option)
        if value is not None:
            config[option] = value
    validated = validate_mcp_server_config(name, config)
    if isinstance(validated, str):
        error(validated)
        return None
    return name, validated


def _add(args: list[str], options: McpCommandOptions, log: Sink, error: Sink) -> int:
    parsed = _parse_options(args, _ADD_OPTIONS, error, 2)
    entry = None if parsed is None else _entry_to_add(parsed, error)
    if parsed is None or entry is None:
        return 1
    name, validated = entry
    path, scope = _target(parsed, options)
    # Looked up before the file is written: a trust store that cannot be read
    # fails the command with nothing changed.
    ignored = scope == "project" and not _is_trusted(options)
    try:
        replaced = add_mcp_server_config(path, name, validated)
    except (McpConfigError, OSError) as exc:
        error(f"Could not update {path}: {exc}")
        return 1
    log(f'{"Replaced" if replaced else "Added"} {scope} MCP server "{name}" in {path}.')
    if ignored:
        log(
            f"The project is not trusted, so {path} is ignored until you start "
            f"{APP_NAME} in the project and trust it."
        )
    # HTTP servers without an Authorization header may use OAuth.
    may_need_sign_in = "url" in validated and not any(
        header.lower() == "authorization" for header in validated.get("headers") or {}
    )
    hint = (
        f". If it requires sign-in: {APP_NAME} mcp login {name}"
        if may_need_sign_in
        else ""
    )
    log(f"Check it with: {APP_NAME} mcp list{hint}")
    return 0


def _remove(args: list[str], options: McpCommandOptions, log: Sink, error: Sink) -> int:
    parsed = _parse_options(args, {"local": "flag"}, error)
    if parsed is None:
        return 1
    if len(parsed.positional) != 1 or not parsed.positional[0]:
        error(f"Usage: {APP_NAME} mcp remove <server> [-l]\n{HELP_HINT}")
        return 1
    name = parsed.positional[0]
    path, scope = _target(parsed, options)
    try:
        removed = remove_mcp_server_config(path, name)
    except (McpConfigError, OSError) as exc:
        error(f"Could not update {path}: {exc}")
        return 1
    if removed:
        log(f'Removed {scope} MCP server "{name}" from {path}.')
        return 0
    # Both files are looked at, whatever the trust, to say where the server is.
    loaded = load_mcp_config(
        config_home=options.config_home, cwd=options.cwd, project_trusted=True
    )
    other = next(
        (
            server
            for server in loaded.servers
            if server.name == name and server.scope != scope
        ),
        None,
    )
    where = ""
    if other is not None:
        advice = "use --local" if other.scope == "project" else "omit --local"
        where = f" It is defined in {other.source}; {advice}."
    error(f'No {scope} MCP server named "{name}" in {path}.{where}')
    return 1


def _list_command(
    args: list[str], options: McpCommandOptions, log: Sink, error: Sink
) -> int:
    parsed = _parse_options(args, {"json": "flag"}, error)
    if parsed is None:
        return 1
    if parsed.positional:
        error(f"Usage: {APP_NAME} mcp list [--json]\n{HELP_HINT}")
        return 1
    trusted = _is_trusted(options)
    loaded = load_mcp_config(
        config_home=options.config_home, cwd=options.cwd, project_trusted=trusted
    )
    project_config = project_mcp_config_path(options.cwd)
    note = (
        f"{project_config} is ignored because the project is not trusted. "
        f"Start {APP_NAME} in the project to trust it."
        if not trusted and project_config.exists()
        else None
    )
    reports = _probe_servers(loaded, options)
    failed = bool(loaded.errors) or any(
        report["enabled"] and report["state"] != "connected" for report in reports
    )
    if parsed.has("json"):
        document: dict[str, Any] = {"servers": reports, "errors": loaded.errors}
        if note is not None:
            document["note"] = note
        text = json.dumps(document, indent=2, ensure_ascii=False)
        # JSON.stringify escapes a lone surrogate too.
        log(_SURROGATE.sub(lambda match: f"\\u{ord(match[0]):04x}", text))
        return 1 if failed else 0
    if not reports and not loaded.errors:
        log(
            "No MCP servers configured. Add them to "
            f"{global_mcp_config_path(options.config_home)} or .pipy/mcp.json."
        )
    for report in reports:
        for line in _report_lines(report):
            log(line)
    for config_error in loaded.errors:
        log(f"config error: {config_error}")
    if note is not None:
        log(note)
    return 1 if failed else 0


def _describe_transport(entry: McpServerEntry) -> str:
    config = entry.config
    if "url" in config:
        return str(config["url"])
    return " ".join([config["command"], *(config.get("args") or ())])


def _new_report(entry: McpServerEntry) -> dict[str, Any]:
    """What is known of a server without connecting, in the order it is printed."""

    config = entry.config
    report: dict[str, Any] = {
        "name": entry.name,
        "scope": entry.scope or "global",
        "source": entry.source,
    }
    if entry.override:
        report["override"] = entry.override
    exposure = config.get("exposure")
    report.update(
        enabled=config.get("enabled") is not False,
        exposure=DEFAULT_MCP_EXPOSURE if exposure is None else exposure,
        transport=_describe_transport(entry),
        state="disabled",
        tools=[],
    )
    return report


def _probe(connection: McpServerConnection, report: dict[str, Any]) -> None:
    """Connect once, write what the connection found into ``report``, close."""

    try:
        try:
            connection.get_client()
        except (McpServerConnectionError, McpAbortError):
            pass  # The connection records the state and the error.
        view = connection.view()
        report["state"] = view.state
        report["tools"] = [str(tool["name"]) for tool in view.tools]
        overrides = {
            name: exposure
            for name in report["tools"]
            if (exposure := get_mcp_tool_exposure(connection.entry.config, name))
            != report["exposure"]
        }
        if overrides:
            report["toolExposure"] = overrides
        if view.has_resources:
            report["resources"] = len(view.resources)
            report["resourceTemplates"] = len(view.resource_templates)
        if view.state != "connected" and view.error:
            report["error"] = view.error
    finally:
        connection.close()


def _probe_servers(
    loaded: LoadedMcpConfig, options: McpCommandOptions
) -> list[dict[str, Any]]:
    """The report of every server; the enabled ones are connected at once."""

    log = McpServerLog(mcp_log_path(options.config_home))
    credentials = McpOAuthCredentialStore(mcp_auth_path(options.config_home))
    reports = [_new_report(entry) for entry in loaded.servers]
    connections: list[McpServerConnection] = []
    probes: list[threading.Thread] = []
    try:
        for entry, report in zip(loaded.servers, reports, strict=True):
            if not report["enabled"]:
                continue
            connection = McpServerConnection(
                entry=entry,
                cwd=options.cwd,
                client_version=options.client_version,
                on_tools=lambda _connection: None,
                create_transport=options.create_transport,
                log=log,
                credentials=credentials,
            )
            connections.append(connection)
            probe = threading.Thread(
                target=_probe,
                args=(connection, report),
                name="mcp-list",
                daemon=True,
            )
            probes.append(probe)
            probe.start()
        for probe in probes:
            probe.join()
    finally:
        # Reached with probes still running only when the wait was
        # interrupted: no server process may outlive the command.
        for connection in connections:
            connection.close()
    return reports


def _report_lines(report: Mapping[str, Any]) -> list[str]:
    name, state, tools = report["name"], report["state"], report["tools"]
    if state == "connected":
        described = f"connected, {len(tools)} tool{'' if len(tools) == 1 else 's'}"
    elif state == "needs-auth":
        described = "needs sign-in"
    else:
        described = state
    lines = [
        f"{name}: {described} ({report['exposure']}, {report['scope']})",
        f"  {report['transport']}",
    ]
    if "override" in report:
        lines.append(f"  project override: {report['override']}")
    if state == "needs-auth":
        lines.append(f"  sign in with: {APP_NAME} mcp login {name}")
    if tools:
        overrides = report.get("toolExposure", {})
        listed = [
            f"{tool} [{overrides[tool]}]" if tool in overrides else tool
            for tool in tools
        ]
        lines.append(f"  tools: {', '.join(listed)}")
    if "resources" in report:
        lines.append(
            f"  resources: {report['resources']}, "
            f"URI templates: {report.get('resourceTemplates', 0)}"
        )
    if report.get("error"):
        lines.append("  " + "\n  ".join(report["error"].split("\n")))
    return lines
