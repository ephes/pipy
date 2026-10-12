"""MCP server configuration: validation, loading and editing of ``mcp.json``.

Servers are read from ``mcp.json`` in the config home and, for trusted
projects, from ``<project>/.pipy/mcp.json``. Both use the ``mcpServers`` shape
shared by other MCP clients, so existing configurations can be copied over.
Project entries replace user-level entries with the same name.

A project entry without ``command``, ``url`` and ``type`` overrides only
``enabled``, ``exposure`` and ``toolExposure`` of the user-level server with
the same name, for example to turn it off in one project. The rest of the
user-level entry is kept, including credentials the project could not set
itself.

The top-level ``autoEnableCodemode`` (default true) activates the codemode
tool when a server with ``codemode`` exposure connects. A project value
overrides the user-level one.

A server config is the JSON object of its entry, with Pi's key names
(``toolExposure``, ``callbackUrl``). A key that is present with JSON ``null``
counts as present, as in Pi, so ``"exposure": null`` is an error and not the
default.

This module decides nothing about trust: the caller says whether the project
is trusted. Values such as ``${NAME}`` references are not resolved here; that
happens when a server connects.

Where this differs from Pi:

- URLs are checked by a small parser, not a WHATWG URL parser. It requires
  ``http://`` or ``https://`` followed by a host and does not repair a URL
  (missing slashes, backslashes, a shortened IPv4 address such as ``127.1``,
  dot segments in the path), so some URLs Pi accepts are config errors here.
  A host with non-ASCII characters is accepted as written, without IDNA
  processing, and is never a loopback host.
- ``oauth.clientMetadataBaseUrl`` exists only in pipy and is required with
  ``oauth.clientRegistration: "cimd"``.
- A file that is not valid UTF-8 is an error instead of being read with
  replacement characters, and the parser messages are Python's.
- Entries are read in JavaScript's order, in which keys that look like array
  indexes (``"1"``, ``"20"``) come first, but a rewritten file keeps the
  order of its keys where ``JSON.stringify`` would move those keys up.
- A whole-number ``oauth.callbackPort`` written as ``8080.0`` is an ``int`` in
  the validated config.
- Rewritten files print numbers the way Python does (``1.0`` stays ``1.0``).
"""

from __future__ import annotations

import copy
import ipaddress
import json
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final, Literal

MCP_CONFIG_FILENAME: Final = "mcp.json"
PROJECT_CONFIG_DIR_NAME: Final = ".pipy"

McpExposure = Literal["codemode", "deferred", "direct", "hidden"]
McpServerConfig = dict[str, Any]
McpConfigScope = Literal["global", "project", "extension"]

MCP_EXPOSURES: Final[tuple[str, ...]] = ("codemode", "deferred", "direct", "hidden")
DEFAULT_MCP_EXPOSURE: Final = "codemode"
# Older exposure names, accepted in configs and replaced by their current name
# when validated.
MCP_EXPOSURE_ALIASES: Final[Mapping[str, str]] = {"codemode-deferred": "codemode"}

OVERRIDE_KEYS: Final[tuple[str, ...]] = ("enabled", "exposure", "toolExposure")
LOOPBACK_HOSTS: Final[tuple[str, ...]] = ("localhost", "127.0.0.1", "[::1]")

_SERVER_NAME: Final = re.compile(r"[A-Za-z0-9_-]+")
_ARRAY_INDEX: Final = re.compile(r"0|[1-9][0-9]{0,9}")
_STRUCTURE_ERROR: Final = 'expected an object with an "mcpServers" object'
# What JavaScript's ``String.prototype.trim`` removes.
_JS_WHITESPACE: Final = (
    "\t\n\v\f\r \u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007"
    "\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000\ufeff"
)
# JavaScript's ``.`` does not match these.
_PATTERN_WILDCARD: Final = "[^\n\r\u2028\u2029]*"
# JSON.stringify uses at most this many characters of an indent string.
_MAX_INDENT_CHARS: Final = 10


class McpConfigError(Exception):
    """An ``mcp.json`` could not be edited: it is not valid, or lacks the server."""


@dataclass(frozen=True, slots=True)
class McpServerEntry:
    """One configured server."""

    name: str
    config: McpServerConfig
    # Config file that defined the entry, or the path of the extension that
    # registered it.
    source: str
    scope: McpConfigScope | None = None
    # Project ``mcp.json`` with an override of this user-level server's
    # ``enabled``, ``exposure`` or ``toolExposure``.
    override: str | None = None


@dataclass(frozen=True, slots=True)
class LoadedMcpConfig:
    """The servers of the user-level and (when trusted) the project file."""

    servers: list[McpServerEntry]
    errors: list[str]
    # None when no file sets it; the default is true.
    auto_enable_codemode: bool | None = None
    # The project ``mcp.json`` when the project is trusted, whether or not the
    # file exists. Project overrides are saved there.
    project_config: str | None = None


def _is_array_index(key: str) -> bool:
    return _ARRAY_INDEX.fullmatch(key) is not None and int(key) < 2**32 - 1


def _js_key_order(keys: Iterable[str]) -> list[str]:
    """Keys in the order JavaScript enumerates them.

    Keys that are array indexes (``"0"``, ``"12"``) come first, in ascending
    order, and the others follow in the order they were written. Which entry
    is looked at first decides which error is reported.
    """

    listed = list(keys)
    indexes = sorted(filter(_is_array_index, listed), key=int)
    return indexes + [key for key in listed if not _is_array_index(key)]


def mcp_namespace(server: str) -> str:
    """Namespace of a server's tools: ``mcp__<server>`` with ``-`` as ``_``."""

    return "mcp__" + server.replace("-", "_")


def _tool_pattern(pattern: str) -> re.Pattern[str]:
    """A tool name pattern in which ``*`` matches any characters."""

    return re.compile(_PATTERN_WILDCARD.join(map(re.escape, pattern.split("*"))))


def create_tool_name_matcher(entries: Iterable[str]) -> Callable[[str], bool]:
    """Whether a tool name matches any entry, each an exact name or a pattern."""

    listed = list(entries)
    names = frozenset(entry for entry in listed if "*" not in entry)
    patterns = [_tool_pattern(entry) for entry in listed if "*" in entry]

    def matches(name: str) -> bool:
        return name in names or any(pattern.fullmatch(name) for pattern in patterns)

    return matches


def get_mcp_tool_exposure(config: Mapping[str, Any], tool_name: str) -> str:
    """Exposure of one tool: its ``toolExposure`` entry, else the server's.

    An exact name wins over patterns; among patterns the first match in the
    object wins.
    """

    overrides = config.get("toolExposure")
    if isinstance(overrides, dict):
        exact = overrides.get(tool_name)
        if exact is not None:
            return str(exact)
        for pattern, exposure in overrides.items():
            if "*" in pattern and _tool_pattern(pattern).fullmatch(tool_name):
                return str(exposure)
    exposure = config.get("exposure")
    return DEFAULT_MCP_EXPOSURE if exposure is None else str(exposure)


@dataclass(frozen=True, slots=True)
class HttpUrl:
    """The parts of an ``http`` or ``https`` URL, as ``parse_http_url`` reads it."""

    scheme: str
    # Lower case; an IPv6 address is compressed and in brackets.
    hostname: str
    # None without a port and for the scheme's default port.
    port: int | None
    path: str
    query: str
    fragment: str

    @property
    def origin(self) -> str:
        port = "" if self.port is None else f":{self.port}"
        return f"{self.scheme}://{self.hostname}{port}"


# re.ASCII keeps the case-insensitive match to ASCII letters.
_URL: Final = re.compile(
    r"(https?)://([^/?#]*)([^?#]*)(?:\?([^#]*))?(?:#(.*))?",
    re.IGNORECASE | re.ASCII | re.DOTALL,
)
_PORT: Final = re.compile(r"0*([0-9]{0,5})")
_FORBIDDEN_IN_HOST: Final = re.compile(r"[\x00-\x20\x7f#%/:<>?@\[\\\]^|]")
_NUMBER_LABEL: Final = re.compile(r"[0-9]+|0[xX][0-9A-Fa-f]*")
_DEFAULT_PORTS: Final[Mapping[str, int]] = {"http": 80, "https": 443}


def _parse_host(text: str) -> str | None:
    if text.startswith("["):
        # A zone id ("%eth0") is not part of a URL host.
        if not text.endswith("]") or "%" in text:
            return None
        try:
            return f"[{ipaddress.IPv6Address(text[1:-1]).compressed}]"
        except ValueError:
            return None
    if not text or _FORBIDDEN_IN_HOST.search(text):
        return None
    host = text.lower()
    labels = host.removesuffix(".").split(".")
    if _NUMBER_LABEL.fullmatch(labels[-1]):
        # A host that ends in a number is an IPv4 address.
        try:
            return str(ipaddress.IPv4Address(host))
        except ValueError:
            return None
    return host


def parse_http_url(value: object) -> HttpUrl | None:
    """The parts of an ``http`` or ``https`` URL, or None for anything else."""

    if not isinstance(value, str):
        return None
    text = re.sub(r"[\t\n\r]", "", value.strip("".join(map(chr, range(0x21)))))
    # A WHATWG parser reads "\\" as "/", which moves the end of the host.
    match = None if "\\" in text else _URL.fullmatch(text)
    if match is None:
        return None
    scheme = match[1].lower()
    host_port = match[2].rpartition("@")[2]
    port: int | None = None
    port_match = re.search(r":([^:\]]*)\Z", host_port)
    if port_match is not None:
        host_port = host_port[: port_match.start()]
        if port_match[1]:
            digits = _PORT.fullmatch(port_match[1])
            port = int(digits[1] or "0") if digits else None
            if port is None or port > 65535:
                return None
    hostname = _parse_host(host_port)
    if hostname is None:
        return None
    if port == _DEFAULT_PORTS[scheme]:
        port = None
    return HttpUrl(
        scheme, hostname, port, match[3] or "/", match[4] or "", match[5] or ""
    )


def is_loopback_redirect_uri(value: object) -> bool:
    """Whether a redirect URI can be served by the loopback callback server."""

    url = parse_http_url(value)
    return (
        url is not None
        and url.scheme == "http"
        and url.hostname in LOOPBACK_HOSTS
        and url.query == ""
        and url.fragment == ""
    )


def _is_string(value: object) -> bool:
    return isinstance(value, str)


def _is_boolean(value: object) -> bool:
    return isinstance(value, bool)


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_positive_number(value: Any) -> bool:
    return _is_number(value) and bool(value > 0)


def _is_port_number(value: Any) -> bool:
    if not _is_number(value) or not 1 <= value <= 65535:
        return False
    return isinstance(value, int) or value.is_integer()


def _is_string_list(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _is_string_record(value: object) -> bool:
    return isinstance(value, dict) and all(
        isinstance(entry, str) for entry in value.values()
    )


def _is_non_blank_string(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip(_JS_WHITESPACE))


def _is_https_url(value: object) -> bool:
    url = parse_http_url(value)
    return url is not None and url.scheme == "https"


def _is_secure_or_loopback(url: HttpUrl | None) -> bool:
    return url is not None and (url.scheme == "https" or url.hostname in LOOPBACK_HOSTS)


def _is_secure_or_loopback_url(value: object) -> bool:
    return _is_secure_or_loopback(parse_http_url(value))


# A check looks at one object and returns what is wrong with it, or None. The
# checks of a list run in order and the first error is the one reported, so
# the order is part of the behaviour.
_Check = Callable[[dict[str, Any]], str | None]


def _typed(key: str, accepts: Callable[[Any], bool], message: str) -> _Check:
    """A check that the value of ``key``, when the key is present, is accepted."""

    def check(value: dict[str, Any]) -> str | None:
        return None if key not in value or accepts(value[key]) else message

    return check


def _first_error(value: dict[str, Any], checks: Iterable[_Check]) -> str | None:
    for check in checks:
        error = check(value)
        if error is not None:
            return error
    return None


def _check_callback_port(oauth: dict[str, Any]) -> str | None:
    if "callbackPort" not in oauth:
        return None
    if not _is_port_number(oauth["callbackPort"]):
        return "oauth.callbackPort must be a port number"
    oauth["callbackPort"] = int(oauth["callbackPort"])
    return None


def _check_callback_url(oauth: dict[str, Any]) -> str | None:
    if "callbackUrl" not in oauth:
        return None
    callback = parse_http_url(oauth["callbackUrl"])
    if callback is None or not is_loopback_redirect_uri(oauth["callbackUrl"]):
        return (
            "oauth.callbackUrl must be an http URI on localhost, 127.0.0.1, "
            "or [::1] without query or fragment"
        )
    # Runs after the port check, so a port that is present is an int.
    if callback.port is not None and oauth.get("callbackPort", callback.port) != (
        callback.port
    ):
        return "oauth.callbackUrl and oauth.callbackPort name different ports"
    return None


def _check_client_registration(oauth: dict[str, Any]) -> str | None:
    registration = oauth.get("clientRegistration", "dcr")
    if registration == "dcr":
        return None
    if registration != "cimd":
        return 'oauth.clientRegistration must be "dcr" or "cimd"'
    if "clientId" in oauth or "clientName" in oauth:
        return (
            'oauth.clientRegistration "cimd" cannot be combined with '
            "oauth.clientId or oauth.clientName"
        )
    # Runs after the callback URL check, so a URL that is present parses.
    callback = parse_http_url(oauth.get("callbackUrl"))
    if callback is not None and (
        callback.hostname == "[::1]" or callback.path != "/callback"
    ):
        return (
            'oauth.clientRegistration "cimd" requires oauth.callbackUrl on '
            "localhost or 127.0.0.1 with path /callback"
        )
    if "clientMetadataBaseUrl" not in oauth:
        return 'oauth.clientRegistration "cimd" requires oauth.clientMetadataBaseUrl'
    return None


_OAUTH_CHECKS: Final[tuple[_Check, ...]] = (
    _typed("clientId", _is_string, "oauth.clientId must be a string"),
    _typed("clientSecret", _is_string, "oauth.clientSecret must be a string"),
    _check_callback_port,
    _check_callback_url,
    _typed("scope", _is_string, "oauth.scope must be a string"),
    _typed(
        "clientName",
        _is_non_blank_string,
        "oauth.clientName must be a non-empty string",
    ),
    _typed(
        "clientMetadataBaseUrl",
        _is_https_url,
        "oauth.clientMetadataBaseUrl must be an https URL",
    ),
    _check_client_registration,
    _typed(
        "authServerMetadataUrl",
        _is_secure_or_loopback_url,
        "oauth.authServerMetadataUrl must be an https URL, or http on localhost, "
        "127.0.0.1, or [::1]",
    ),
)

_EXPOSURE_LIST: Final = ", ".join(f'"{exposure}"' for exposure in MCP_EXPOSURES)


def _resolve_exposure_alias(value: object) -> object:
    return MCP_EXPOSURE_ALIASES.get(value, value) if isinstance(value, str) else value


def _is_exposure(value: object) -> bool:
    return isinstance(value, str) and value in MCP_EXPOSURES


def _check_exposure(config: dict[str, Any]) -> str | None:
    if "exposure" not in config:
        return None
    config["exposure"] = _resolve_exposure_alias(config["exposure"])
    if not _is_exposure(config["exposure"]):
        return f"exposure must be one of {_EXPOSURE_LIST}"
    return None


def _check_tool_exposure(config: dict[str, Any]) -> str | None:
    if "toolExposure" not in config:
        return None
    tool_exposure = config["toolExposure"]
    if not isinstance(tool_exposure, dict):
        return "toolExposure must map tool names to exposures"
    for tool in _js_key_order(tool_exposure):
        tool_exposure[tool] = _resolve_exposure_alias(tool_exposure[tool])
        if not _is_exposure(tool_exposure[tool]):
            return f'toolExposure "{tool}" must be one of {_EXPOSURE_LIST}'
    return None


def _check_not_sse(config: dict[str, Any]) -> str | None:
    if config.get("type") == "sse":
        return "legacy SSE transport is not supported; use the streamable HTTP URL"
    return None


def _check_url(config: dict[str, Any]) -> str | None:
    if parse_http_url(config["url"]) is None:
        return "url must be an http or https URL"
    return None


def _check_oauth(config: dict[str, Any]) -> str | None:
    if "oauth" not in config:
        return None
    oauth = config["oauth"]
    if not isinstance(oauth, dict):
        return "oauth must be an object"
    return _first_error(oauth, _OAUTH_CHECKS)


def _check_auth(config: dict[str, Any]) -> str | None:
    if "auth" not in config:
        return None
    auth = config["auth"]
    provider = auth.get("provider") if isinstance(auth, dict) else None
    if not isinstance(provider, str) or not provider:
        return "auth.provider must be a provider name"
    if not _is_secure_or_loopback_url(config["url"]):
        return "auth requires an https URL, or http on localhost, 127.0.0.1, or [::1]"
    return None


_COMMON_CHECKS: Final[tuple[_Check, ...]] = (
    _check_exposure,
    _check_tool_exposure,
    _typed("enabled", _is_boolean, "enabled must be a boolean"),
    _typed("description", _is_string, "description must be a string"),
    _typed(
        "timeout",
        _is_positive_number,
        "timeout must be a positive number of seconds",
    ),
    _check_not_sse,
)
_HTTP_CHECKS: Final[tuple[_Check, ...]] = (
    _check_url,
    _typed("headers", _is_string_record, "headers must map names to strings"),
    _check_oauth,
    _check_auth,
)
_STDIO_CHECKS: Final[tuple[_Check, ...]] = (
    _typed("args", _is_string_list, "args must be an array of strings"),
    _typed("env", _is_string_record, "env must map names to strings"),
    _typed("cwd", _is_string, "cwd must be a string"),
)


def _transport_checks(config: Mapping[str, Any]) -> tuple[_Check, ...] | None:
    """The checks of the transport an entry selects, or None when it selects none.

    A string ``url`` selects streamable HTTP and a string ``command`` stdio,
    each unless ``type`` names something else; ``url`` is looked at first.
    """

    server_type = config.get("type")
    untyped = "type" not in config
    if isinstance(config.get("url"), str) and (
        untyped or server_type in ("http", "streamable-http")
    ):
        return _HTTP_CHECKS
    if isinstance(config.get("command"), str) and (untyped or server_type == "stdio"):
        return _STDIO_CHECKS
    return None


def validate_mcp_server_config(name: str, raw: object) -> McpServerConfig | str:
    """Validate one server entry of the ``mcpServers`` shape.

    Returns a deep copy of the entry with exposure aliases replaced by their
    current names, or the error message.
    """

    if not _SERVER_NAME.fullmatch(name):
        return f'invalid server name "{name}" (use letters, digits, "_" and "-")'
    if not isinstance(raw, dict):
        return f'server "{name}" must be an object'
    try:
        config: McpServerConfig = copy.deepcopy(raw)
    except RecursionError:
        return f'server "{name}" is nested too deeply'
    error = _first_error(config, _COMMON_CHECKS)
    if error is None:
        checks = _transport_checks(config)
        if checks is None:
            return (
                f'server "{name}" needs either "command" (stdio) or "url" '
                "(streamable HTTP)"
            )
        error = _first_error(config, checks)
    return config if error is None else f'server "{name}": {error}'


def global_mcp_config_path(config_home: Path | str) -> Path:
    return Path(config_home) / MCP_CONFIG_FILENAME


def project_mcp_config_path(cwd: Path | str) -> Path:
    return Path(cwd) / PROJECT_CONFIG_DIR_NAME / MCP_CONFIG_FILENAME


def _is_override(value: Mapping[str, Any]) -> bool:
    """Whether an entry overrides a server defined elsewhere, not defines one."""

    return "command" not in value and "url" not in value and "type" not in value


def _reject_constant(name: str) -> object:
    raise ValueError(f"{name} is not valid JSON")


def _read_text(path: Path) -> str | None:
    """The file's text, or None when there is no such file."""

    try:
        data = path.read_bytes()
    except (FileNotFoundError, NotADirectoryError):
        return None
    return data.decode("utf-8")


def _parse_json(text: str) -> object:
    try:
        return json.loads(text, parse_constant=_reject_constant)
    except RecursionError:
        raise ValueError("the JSON is nested too deeply") from None


def _has_mcp_servers_shape(parsed: object) -> bool:
    return isinstance(parsed, dict) and (
        "mcpServers" not in parsed or isinstance(parsed["mcpServers"], dict)
    )


@dataclass(slots=True)
class _ConfigState:
    servers: dict[str, McpServerEntry] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    auto_enable_codemode: bool | None = None


def _is_js_truthy(value: object) -> bool:
    if value is None or isinstance(value, bool):
        return value is True
    return not (isinstance(value, (int, float, str)) and not value)


def _read_document(path: Path, errors: list[str]) -> dict[str, Any] | None:
    """The file's JSON object, or None when it is missing or was reported."""

    try:
        text = _read_text(path)
        if text is None:
            return None
        parsed = _parse_json(text)
    except (OSError, ValueError) as exc:
        errors.append(f"{path}: {exc}")
        return None
    if not isinstance(parsed, dict) or not _has_mcp_servers_shape(parsed):
        errors.append(f"{path}: {_STRUCTURE_ERROR}")
        return None
    return parsed


def _override_server(
    path: Path, name: str, value: dict[str, Any], state: _ConfigState
) -> str | None:
    """Merge a project override over its user-level server; returns the error."""

    base = state.servers.get(name)
    if base is None:
        return (
            f'server "{name}" needs "command" or "url", or a global server to override'
        )
    if any(key not in OVERRIDE_KEYS for key in value):
        return f'server "{name}": an override can only set {", ".join(OVERRIDE_KEYS)}'
    config = validate_mcp_server_config(name, {**base.config, **value})
    if isinstance(config, str):
        return config
    state.servers[name] = McpServerEntry(
        name, config, base.source, base.scope, str(path)
    )
    return None


def _define_server(
    path: Path,
    scope: Literal["global", "project"],
    name: str,
    value: object,
    state: _ConfigState,
) -> str | None:
    """Add or replace the server an entry defines; returns the error."""

    config = validate_mcp_server_config(name, value)
    if isinstance(config, str):
        return config
    # Names that differ only in "-" and "_" would share a namespace.
    namespace = mcp_namespace(name)
    for other in state.servers:
        if other != name and mcp_namespace(other) == namespace:
            return f'server "{name}" conflicts with "{other}"'
    if scope == "project" and "url" in config and _is_js_truthy(config.get("auth")):
        return f'server "{name}": auth is only allowed in the global mcp.json'
    state.servers[name] = McpServerEntry(name, config, str(path), scope)
    return None


def _read_config_file(
    path: Path, scope: Literal["global", "project"], state: _ConfigState
) -> None:
    parsed = _read_document(path, state.errors)
    if parsed is None:
        return
    if "autoEnableCodemode" in parsed:
        if isinstance(parsed["autoEnableCodemode"], bool):
            state.auto_enable_codemode = parsed["autoEnableCodemode"]
        else:
            state.errors.append(f"{path}: autoEnableCodemode must be a boolean")
    entries = parsed.get("mcpServers", {})
    for name in _js_key_order(entries):
        value = entries[name]
        if scope == "project" and isinstance(value, dict) and _is_override(value):
            error = _override_server(path, name, value, state)
        else:
            error = _define_server(path, scope, name, value, state)
        if error is not None:
            state.errors.append(f"{path}: {error}")


def load_mcp_config(
    *, config_home: Path | str, cwd: Path | str, project_trusted: bool
) -> LoadedMcpConfig:
    """Load the user-level and, when trusted, the project MCP configuration.

    Disabled servers are included with ``enabled: false``, so they can be
    enabled again. Nothing raises: what is wrong with a file or an entry is
    reported in ``errors`` as ``<path>: <reason>`` and the rest still loads.
    """

    state = _ConfigState()
    _read_config_file(global_mcp_config_path(config_home), "global", state)
    project_config = project_mcp_config_path(cwd) if project_trusted else None
    if project_config is not None:
        _read_config_file(project_config, "project", state)
    return LoadedMcpConfig(
        servers=list(state.servers.values()),
        errors=state.errors,
        auto_enable_codemode=state.auto_enable_codemode,
        project_config=None if project_config is None else str(project_config),
    )


_INDENT: Final = re.compile(r"(?:\A|[\n\r\u2028\u2029])([ \t]+)\S")
_SURROGATE: Final = re.compile("[\ud800-\udfff]")


def _edit_mcp_servers(
    path: Path, edit: Callable[[dict[str, Any] | None, dict[str, Any]], bool]
) -> None:
    """Rewrite an ``mcp.json`` after ``edit`` changed it.

    ``edit`` gets the ``mcpServers`` object (None when the file has none) and
    the whole document (empty when there is no file), and returns whether to
    write. The file is written with its own indentation, two spaces when it
    has none, and other content is kept.
    """

    try:
        text = _read_text(path)
        parsed = {} if text is None else _parse_json(text)
    except ValueError as exc:
        raise McpConfigError(str(exc)) from exc
    if not isinstance(parsed, dict) or not _has_mcp_servers_shape(parsed):
        raise McpConfigError(f"{path}: {_STRUCTURE_ERROR}")
    if not edit(parsed.get("mcpServers"), parsed):
        return
    found = _INDENT.search(text) if text else None
    indent = found[1][:_MAX_INDENT_CHARS] if found else "  "
    try:
        output = json.dumps(parsed, indent=indent, ensure_ascii=False, allow_nan=False)
    except ValueError as exc:
        # A number such as 1e999 is read as infinity, which JSON cannot hold.
        raise McpConfigError(f"{path}: {exc}") from exc
    # A lone surrogate cannot be encoded; JSON.stringify escapes it as well.
    output = _SURROGATE.sub(lambda match: f"\\u{ord(match[0]):04x}", output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(f"{output}\n".encode())


def update_mcp_server_config(
    path: Path | str,
    name: str,
    *,
    enabled: bool | None = None,
    exposure: McpExposure | None = None,
    override: bool = False,
) -> None:
    """Change one server's ``enabled`` or ``exposure`` in an ``mcp.json``.

    ``path`` is the file that defines or overrides the server. In a defining
    entry the defaults (``enabled: true``, ``exposure: "codemode"``) remove
    the key. An override entry keeps them, since they replace the user-level
    server's values. With ``override``, a missing entry is added as an
    override. Raises ``McpConfigError`` when the file is not a valid
    ``mcp.json`` or does not define the server, and ``OSError`` when it cannot
    be read or written.
    """

    if exposure is not None and exposure not in MCP_EXPOSURES:
        raise ValueError(f"unknown MCP exposure {exposure!r}")
    file = Path(path)

    def edit(servers: dict[str, Any] | None, parsed: dict[str, Any]) -> bool:
        if (servers is None or name not in servers) and override:
            servers = parsed["mcpServers"] = {**(servers or {}), name: {}}
        server = None if servers is None else servers.get(name)
        if not isinstance(server, dict):
            raise McpConfigError(f'{file} does not define MCP server "{name}"')
        keep_defaults = _is_override(server)
        if enabled is not None:
            if enabled and not keep_defaults:
                server.pop("enabled", None)
            else:
                server["enabled"] = enabled
        if exposure is not None:
            if exposure == DEFAULT_MCP_EXPOSURE and not keep_defaults:
                server.pop("exposure", None)
            else:
                server["exposure"] = exposure
        return True

    _edit_mcp_servers(file, edit)


def add_mcp_server_config(
    path: Path | str, name: str, config: Mapping[str, Any]
) -> bool:
    """Add a server to an ``mcp.json``, creating the file when missing.

    An entry with the same name is replaced; returns whether one was. The
    config is written as given: the caller validates it. Raises as
    ``update_mcp_server_config`` does.
    """

    replaced = False

    def edit(servers: dict[str, Any] | None, parsed: dict[str, Any]) -> bool:
        nonlocal replaced
        target = {} if servers is None else servers
        replaced = name in target
        target[name] = copy.deepcopy(dict(config))
        parsed["mcpServers"] = target
        return True

    _edit_mcp_servers(Path(path), edit)
    return replaced


def remove_mcp_server_config(path: Path | str, name: str) -> bool:
    """Remove a server from an ``mcp.json``.

    Returns false, and leaves the file alone, when there is no file or it
    does not have the entry. Raises as ``update_mcp_server_config`` does.
    """

    removed = False

    def edit(servers: dict[str, Any] | None, parsed: dict[str, Any]) -> bool:
        nonlocal removed
        if servers is None or name not in servers:
            return False
        del servers[name]
        removed = True
        return True

    _edit_mcp_servers(Path(path), edit)
    return removed
