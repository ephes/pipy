"""Tests for MCP server configuration: validation, loading and editing.

Tests that mirror a case of the "MCP config" group of Pi's
``packages/coding-agent/test/mcp-extension.test.ts`` name it in a ``Pi:``
comment.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from pipy_harness.native.mcp.config import (
    LoadedMcpConfig,
    McpConfigError,
    add_mcp_server_config,
    get_mcp_tool_exposure,
    global_mcp_config_path,
    is_loopback_redirect_uri,
    load_mcp_config,
    mcp_namespace,
    parse_http_url,
    project_mcp_config_path,
    remove_mcp_server_config,
    update_mcp_server_config,
    validate_mcp_server_config,
)

# Config values are resolved at connect time, so the literal reference must
# survive loading.
TOKEN_HEADER = "Bearer ${TOKEN}"
HTTP = "https://a.example/mcp"


class Setup:
    def __init__(self, root: Path, user: object = None, project: object = None) -> None:
        self.config_home = root / "home"
        self.cwd = root / "project"
        self.user_file = self.config_home / "mcp.json"
        self.project_file = self.cwd / ".pipy" / "mcp.json"
        for file, content in ((self.user_file, user), (self.project_file, project)):
            file.parent.mkdir(parents=True)
            if content is not None:
                file.write_text(json.dumps(content), encoding="utf-8")

    def load(self, *, trusted: bool) -> LoadedMcpConfig:
        return load_mcp_config(
            config_home=self.config_home, cwd=self.cwd, project_trusted=trusted
        )


def load_user(tmp_path: Path, servers: dict[str, Any]) -> LoadedMcpConfig:
    return Setup(tmp_path, {"mcpServers": servers}).load(trusted=False)


def test_paths() -> None:
    assert global_mcp_config_path("/home/cfg") == Path("/home/cfg/mcp.json")
    assert project_mcp_config_path(Path("/work")) == Path("/work/.pipy/mcp.json")


# --- validation: one test per error text ------------------------------------------------

EXPOSURES = '"codemode", "deferred", "direct", "hidden"'
CALLBACK_ERROR = (
    "oauth.callbackUrl must be an http URI on localhost, 127.0.0.1, or [::1] "
    "without query or fragment"
)
BASE_URL_ERROR = (
    "oauth.clientMetadataBaseUrl must be an https URL without query or fragment"
)
METADATA_ERROR = (
    "oauth.authServerMetadataUrl must be an https URL, or http on localhost, "
    "127.0.0.1, or [::1]"
)
AUTH_URL_ERROR = "auth requires an https URL, or http on localhost, 127.0.0.1, or [::1]"
NEEDS = 'server "s" needs either "command" (stdio) or "url" (streamable HTTP)'
SSE = 'server "s": legacy SSE transport is not supported; use the streamable HTTP URL'


def oauth(**fields: object) -> dict[str, object]:
    return {"url": HTTP, "oauth": fields}


ENTRY_ERRORS: list[tuple[object, str]] = [
    # CFG-10
    ("text", 'server "s" must be an object'),
    (["command"], 'server "s" must be an object'),
    (None, 'server "s" must be an object'),
    # CFG-11
    ({}, NEEDS),
    ({"command": 1}, NEEDS),
    ({"url": HTTP, "type": "stdio"}, NEEDS),
    ({"command": "x", "type": "http"}, NEEDS),
    ({"command": "x", "type": "streamable-http"}, NEEDS),
    ({"command": "x", "type": None}, NEEDS),
    ({"command": "x", "type": "pipe"}, NEEDS),
    ({"type": "sse", "url": HTTP}, SSE),
    ({"type": "sse", "command": "x"}, SSE),
    # CFG-12
    ({"command": "x", "args": "a b"}, 'server "s": args must be an array of strings'),
    (
        {"command": "x", "args": ["a", 1]},
        'server "s": args must be an array of strings',
    ),
    ({"command": "x", "env": {"A": 1}}, 'server "s": env must map names to strings'),
    ({"command": "x", "env": ["A=1"]}, 'server "s": env must map names to strings'),
    ({"command": "x", "cwd": 1}, 'server "s": cwd must be a string'),
    # CFG-13
    ({"url": "example.com/mcp"}, 'server "s": url must be an http or https URL'),
    ({"url": "ftp://example.com/mcp"}, 'server "s": url must be an http or https URL'),
    ({"url": "https://"}, 'server "s": url must be an http or https URL'),
    ({"url": "https://a b/mcp"}, 'server "s": url must be an http or https URL'),
    ({"url": "https://a:99999/"}, 'server "s": url must be an http or https URL'),
    ({"url": "https://a:65536/"}, 'server "s": url must be an http or https URL'),
    ({"url": "https://a:+80/"}, 'server "s": url must be an http or https URL'),
    (
        {"url": "https://a:\u0668\u0660/"},
        'server "s": url must be an http or https URL',
    ),
    (
        {"url": "https://a:" + "9" * 5000 + "/"},
        'server "s": url must be an http or https URL',
    ),
    # "\u017f" lower-cases to "s" in a Unicode-aware match.
    (
        {"url": "http\u017f://a.example/"},
        'server "s": url must be an http or https URL',
    ),
    ({"url": "https://[::1/mcp"}, 'server "s": url must be an http or https URL'),
    ({"url": "https://[::1%en0]/"}, 'server "s": url must be an http or https URL'),
    (
        {"url": HTTP, "headers": {"A": 1}},
        'server "s": headers must map names to strings',
    ),
    ({"url": HTTP, "headers": "A: 1"}, 'server "s": headers must map names to strings'),
    # CFG-14
    (
        {"command": "x", "exposure": "model-only"},
        f'server "s": exposure must be one of {EXPOSURES}',
    ),
    (
        {"command": "x", "exposure": None},
        f'server "s": exposure must be one of {EXPOSURES}',
    ),
    # CFG-15
    (
        {"command": "x", "toolExposure": ["a"]},
        'server "s": toolExposure must map tool names to exposures',
    ),
    # JavaScript looks at keys that are array indexes first, in ascending order.
    (
        {
            "command": "x",
            "toolExposure": {"b": 1, "2": 1, "01": 1, "1": 1, "0": "direct"},
        },
        f'server "s": toolExposure "1" must be one of {EXPOSURES}',
    ),
    (
        {"command": "x", "toolExposure": {"4294967295": 1, "-1": 1, "4294967294": 1}},
        f'server "s": toolExposure "4294967294" must be one of {EXPOSURES}',
    ),
    (
        {"command": "x", "toolExposure": {"4294967295": 1, "-1": 1}},
        f'server "s": toolExposure "4294967295" must be one of {EXPOSURES}',
    ),
    (
        {"command": "x", "toolExposure": {"ok": "direct", "get_*": True}},
        f'server "s": toolExposure "get_*" must be one of {EXPOSURES}',
    ),
    ({"command": "x", "enabled": "no"}, 'server "s": enabled must be a boolean'),
    ({"command": "x", "enabled": 0}, 'server "s": enabled must be a boolean'),
    ({"command": "x", "description": 1}, 'server "s": description must be a string'),
    (
        {"command": "x", "timeout": 0},
        'server "s": timeout must be a positive number of seconds',
    ),
    (
        {"command": "x", "timeout": "60"},
        'server "s": timeout must be a positive number of seconds',
    ),
    (
        {"command": "x", "timeout": True},
        'server "s": timeout must be a positive number of seconds',
    ),
    # CFG-17
    ({"url": HTTP, "oauth": "on"}, 'server "s": oauth must be an object'),
    ({"url": HTTP, "oauth": None}, 'server "s": oauth must be an object'),
    (oauth(clientId=1), 'server "s": oauth.clientId must be a string'),
    (oauth(clientSecret=1), 'server "s": oauth.clientSecret must be a string'),
    (oauth(scope=["a"]), 'server "s": oauth.scope must be a string'),
    (oauth(callbackPort=0), 'server "s": oauth.callbackPort must be a port number'),
    (oauth(callbackPort=65536), 'server "s": oauth.callbackPort must be a port number'),
    (oauth(callbackPort=80.5), 'server "s": oauth.callbackPort must be a port number'),
    (oauth(callbackPort="80"), 'server "s": oauth.callbackPort must be a port number'),
    (oauth(callbackPort=True), 'server "s": oauth.callbackPort must be a port number'),
    (oauth(clientName=""), 'server "s": oauth.clientName must be a non-empty string'),
    (
        oauth(clientName="\ufeff\u00a0"),
        'server "s": oauth.clientName must be a non-empty string',
    ),
    (oauth(clientName=1), 'server "s": oauth.clientName must be a non-empty string'),
    # CFG-18
    (oauth(callbackUrl=1), f'server "s": {CALLBACK_ERROR}'),
    (oauth(callbackUrl="https://localhost/cb"), f'server "s": {CALLBACK_ERROR}'),
    (oauth(callbackUrl="http://example.com/cb"), f'server "s": {CALLBACK_ERROR}'),
    (oauth(callbackUrl="http://localhost/cb?x=1"), f'server "s": {CALLBACK_ERROR}'),
    (oauth(callbackUrl="http://localhost/cb#f"), f'server "s": {CALLBACK_ERROR}'),
    (
        oauth(callbackUrl="http://localhost.evil.example/cb"),
        f'server "s": {CALLBACK_ERROR}',
    ),
    (
        oauth(callbackUrl="http://localhost:81/cb", callbackPort=82),
        'server "s": oauth.callbackUrl and oauth.callbackPort name different ports',
    ),
    # CFG-19
    (
        oauth(clientRegistration="auto"),
        'server "s": oauth.clientRegistration must be "dcr" or "cimd"',
    ),
    (
        oauth(clientRegistration=None),
        'server "s": oauth.clientRegistration must be "dcr" or "cimd"',
    ),
    (
        oauth(clientRegistration="cimd", clientName="x"),
        'server "s": oauth.clientRegistration "cimd" cannot be combined with '
        "oauth.clientId or oauth.clientName",
    ),
    (
        oauth(clientRegistration="cimd", callbackUrl="http://[::1]/callback"),
        'server "s": oauth.clientRegistration "cimd" requires oauth.callbackUrl on '
        "localhost or 127.0.0.1 with path /callback",
    ),
    (
        oauth(clientRegistration="cimd", callbackUrl="http://localhost/callback"),
        'server "s": oauth.clientRegistration "cimd" requires '
        "oauth.clientMetadataBaseUrl",
    ),
    *(
        (oauth(clientMetadataBaseUrl=base), f'server "s": {BASE_URL_ERROR}')
        for base in (
            "http://id.example/oauth/",
            1,
            "https://id.example/oauth?tenant=a",
            "https://id.example/oauth#top",
            "https://user@id.example/oauth",
        )
    ),
    # CFG-20
    (
        oauth(authServerMetadataUrl="http://idp.example/m"),
        f'server "s": {METADATA_ERROR}',
    ),
    (oauth(authServerMetadataUrl="idp.example/m"), f'server "s": {METADATA_ERROR}'),
    (oauth(authServerMetadataUrl=1), f'server "s": {METADATA_ERROR}'),
    # CFG-21
    (
        {"url": HTTP, "auth": "radius"},
        'server "s": auth.provider must be a provider name',
    ),
    (
        {"url": HTTP, "auth": {"provider": ""}},
        'server "s": auth.provider must be a provider name',
    ),
    ({"url": HTTP, "auth": {}}, 'server "s": auth.provider must be a provider name'),
    (
        {"url": "http://a.example/mcp", "auth": {"provider": "p"}},
        f'server "s": {AUTH_URL_ERROR}',
    ),
]


@pytest.mark.parametrize(("entry", "message"), ENTRY_ERRORS)
def test_invalid_entries_give_their_error_text(entry: object, message: str) -> None:
    assert validate_mcp_server_config("s", entry) == message


@pytest.mark.parametrize("name", ["bad name", "", "a.b", "a/b", "\u00e4", "a\n"])
def test_invalid_server_names(name: str) -> None:
    assert validate_mcp_server_config(name, {"command": "x"}) == (
        f'invalid server name "{name}" (use letters, digits, "_" and "-")'
    )


VALID_ENTRIES: list[dict[str, Any]] = [
    {"command": "x"},
    {"command": "x", "type": "stdio", "args": [], "env": {}, "cwd": "~/work"},
    {"command": "npx -y server", "args": ["-y", "pkg"], "env": {"A": "${A}"}},
    {"url": HTTP},
    {"url": HTTP, "type": "http", "headers": {"Authorization": TOKEN_HEADER}},
    {"url": "http://example.com:8080/mcp?x=1#y", "type": "streamable-http"},
    {"url": "HTTPS://Example.COM"},
    {"url": "https://a.example:" + "0" * 5000 + "443/mcp"},
    {"url": "https://a.example:/mcp"},
    # `url` wins when an entry has both, and `type` picks the other one.
    {"url": HTTP, "command": "x", "args": "not checked"},
    {"url": HTTP, "command": "x", "type": "stdio", "headers": "not checked"},
    {"command": "x", "url": 1, "oauth": "not checked"},
    {"command": "x", "enabled": True, "description": "", "timeout": 0.5},
    {"command": "x", "timeout": 600, "toolExposure": {}, "unknown": {"kept": 1}},
    oauth(),
    oauth(clientId="id", clientSecret="!pass show x", scope="", callbackPort=65535),
    oauth(clientRegistration="dcr", clientId="id", clientName="Known Client"),
    # An empty query or fragment, a user name and upper case are accepted.
    oauth(callbackUrl="http://LOCALHOST/cb?"),
    oauth(callbackUrl="http://user@127.0.0.1:9000/oauth/callback#"),
    oauth(callbackUrl="http://[0:0:0:0:0:0:0:1]:9000/cb", callbackPort=9000),
    # The default port is no port, as in a WHATWG URL.
    oauth(callbackUrl="http://localhost:80/cb", callbackPort=9000),
    oauth(
        clientRegistration="cimd",
        clientMetadataBaseUrl="https://id.example/oauth/",
        callbackUrl="http://127.0.0.1:7000/callback",
        callbackPort=7000,
    ),
    oauth(authServerMetadataUrl="http://localhost:9000/.well-known/x"),
    oauth(authServerMetadataUrl="http://[::1]/m"),
    {"url": "http://127.0.0.1:8788/mcp", "auth": {"provider": "p"}},
    {"url": "http://[::1]:8788/mcp", "auth": {"provider": "p", "extra": 1}},
]


@pytest.mark.parametrize("entry", VALID_ENTRIES)
def test_valid_entries_are_returned_as_a_copy(entry: dict[str, Any]) -> None:
    before = json.dumps(entry)
    validated = validate_mcp_server_config("Server_1-a", entry)
    assert validated == entry
    assert validated is not entry
    assert json.dumps(entry) == before


def test_validation_resolves_aliases_in_a_deep_copy() -> None:
    raw = {
        "command": "x",
        "exposure": "codemode-deferred",
        "toolExposure": {"a": "codemode-deferred", "b": "hidden"},
        "env": {"A": "1"},
    }
    validated = validate_mcp_server_config("s", raw)
    assert isinstance(validated, dict)
    assert validated["exposure"] == "codemode"
    assert validated["toolExposure"] == {"a": "codemode", "b": "hidden"}
    assert raw["exposure"] == "codemode-deferred"
    assert raw["toolExposure"] == {"a": "codemode-deferred", "b": "hidden"}
    validated["env"]["A"] = "2"
    assert raw["env"] == {"A": "1"}


def test_an_entry_nested_too_deeply_to_copy_is_an_error() -> None:
    deep: list[Any] = []
    for _ in range(5_000):
        deep = [deep]
    assert validate_mcp_server_config("s", {"command": "x", "extra": deep}) == (
        'server "s" is nested too deeply'
    )


def test_a_whole_number_callback_port_becomes_an_int() -> None:
    validated = validate_mcp_server_config(
        "s", oauth(callbackPort=8080.0, callbackUrl="http://localhost:8080/cb")
    )
    assert isinstance(validated, dict)
    port = validated["oauth"]["callbackPort"]
    assert (port, type(port)) == (8080, int)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("http://localhost/cb", True),
        ("http://localhost", True),
        (" http://127.0.0.1:8080/oauth/callback\n", True),
        ("http://[::1]:1/", True),
        ("https://localhost/cb", False),
        ("http://localhost./cb", False),
        ("http://127.0.0.2/cb", False),
        # Every spelling of 127.0.0.1 is that host, as with a WHATWG parser.
        ("http://127.1/cb", True),
        # A WHATWG parser would see the host evil.example here.
        ("http://evil.example\\@localhost/cb", False),
        ("http://localhost@evil.example/cb", False),
        ("http://evil.example:80@localhost/cb", True),
        ("http://127.0.0.01/cb", True),
        ("http://2130706433/cb", True),
        ("http://0x7f.0.0.1/cb", True),
        ("http://%6cocalhost/cb", True),
        ("http://[0:0::1]/cb", True),
        ("http://127.0.0.256/cb", False),
        ("http://[::2]/cb", False),
        ("http://localhost/cb?a", False),
        ("http://localhost/cb#a", False),
        ("localhost/cb", False),
        ("http://localhost:x/cb", False),
        ("http://localhost:000000000080/cb", True),
        ("", False),
        (None, False),
    ],
)
def test_is_loopback_redirect_uri(value: object, expected: bool) -> None:
    assert is_loopback_redirect_uri(value) is expected


# What Node's `new URL(url)` gives for hostname, port and pathname
# (node 26); None where `URL.canParse` is false.
@pytest.mark.parametrize(
    ("url", "expected"),
    [
        # A host that ends in a number is an IPv4 address in any spelling.
        ("http://127.1:8080/mcp", ("127.0.0.1", 8080, "/mcp")),
        ("http://0x7f.0.0.1/", ("127.0.0.1", None, "/")),
        ("http://2130706433/", ("127.0.0.1", None, "/")),
        ("http://0177.0.0.1/", ("127.0.0.1", None, "/")),
        ("http://127.0.0.01/", ("127.0.0.1", None, "/")),
        ("http://127.0.0.1./", ("127.0.0.1", None, "/")),
        ("http://1.2.65535/", ("1.2.255.255", None, "/")),
        ("http://0x/", ("0.0.0.0", None, "/")),
        ("http://4294967295/", ("255.255.255.255", None, "/")),
        ("http://0x00000000000000001/", ("0.0.0.1", None, "/")),
        ("http://%31%32%37.1/", ("127.0.0.1", None, "/")),
        ("http://1.2.3.0x/", ("1.2.3.0", None, "/")),
        ("http://1.2.3.4.5/", None),
        ("http://256.1/", None),
        ("http://1.256.1.1/", None),
        ("http://1.2.65536/", None),
        ("http://4294967296/", None),
        ("http://99999999999999999999999/", None),
        ("http://" + "9" * 5000 + "/", None),
        ("http://09.1/", None),
        ("http://1.2.3.08/", None),
        ("http://1..2/", None),
        ("http://a.1/", None),
        # Not numbers: these are domains.
        ("http://a.0x1g/", ("a.0x1g", None, "/")),
        ("http://1.a/", ("1.a", None, "/")),
        # Slashes after the scheme, and backslashes.
        ("http:example.com/mcp", ("example.com", None, "/mcp")),
        ("http:///example.com", ("example.com", None, "/")),
        ("http:\\\\example.com\\a\\b", ("example.com", None, "/a/b")),
        ("https://a.example\\x", ("a.example", None, "/x")),
        # The host is percent-decoded.
        ("http://exa%6Dple.com/", ("example.com", None, "/")),
        ("http://%ff.com/", None),
        ("http://\ud800.com/", None),
        ("http://%ed%a0%80.com/", None),
        ("http://%EF%BF%BD.com/", None),
        ("http://h/\ud800", ("h", None, "/\ufffd")),
        ("http://a%20b/", None),
        ("http://a%2Fb/", None),
        # IPv6 hosts are written with hex pieces only.
        ("http://[::ffff:1.2.3.4]/", ("[::ffff:102:304]", None, "/")),
        ("http://[0:0:1:0:0:1:0:0]/", ("[::1:0:0:1:0:0]", None, "/")),
        ("http://[1:2:3:4:5:6:7::]/", ("[1:2:3:4:5:6:7:0]", None, "/")),
        ("http://[::1]x/", None),
        ("http://[::1/", None),
        ("http://a:1:2/", None),
        ("http://a:/", ("a", None, "/")),
        ("http://user@/", None),
    ],
)
def test_urls_are_read_as_a_whatwg_parser_reads_them(
    url: str, expected: tuple[str, int | None, str] | None
) -> None:
    parsed = parse_http_url(url)
    got = None if parsed is None else (parsed.hostname, parsed.port, parsed.pathname)
    assert got == expected


@pytest.mark.parametrize(
    ("url", "named"),
    [
        ("http://@h/", False),
        ("http://:@h/", False),
        ("http://a:@h/", True),
        ("http://:b@h/", True),
        ("http://a@b@h/", True),
        ("http://h/@x", False),
    ],
)
def test_only_a_user_or_password_counts_as_user_info(url: str, named: bool) -> None:
    parsed = parse_http_url(url)
    assert parsed is not None
    assert (parsed.hostname, parsed.has_user_info) == ("h", named)


def test_a_url_with_a_lone_surrogate_in_its_host_is_an_entry_error(
    tmp_path: Path,
) -> None:
    # JSON can spell a lone surrogate; the file itself is valid UTF-8.
    loaded = load_user(
        tmp_path, {"bad": {"url": "http://\ud800/mcp"}, "good": {"url": HTTP}}
    )
    assert [entry.name for entry in loaded.servers] == ["good"]
    assert len(loaded.errors) == 1
    assert loaded.errors[0].endswith('server "bad": url must be an http or https URL')


def test_a_shortened_ipv4_server_url_is_valid() -> None:
    entry = {"url": "http://127.1:8080/mcp", "auth": {"provider": "p"}}
    # 127.1 is the loopback host, where auth may use http.
    assert validate_mcp_server_config("s", entry) == entry


def test_tool_exposure_defaults_to_codemode() -> None:
    assert get_mcp_tool_exposure({"command": "x"}, "t") == "codemode"
    assert (
        get_mcp_tool_exposure({"command": "x", "exposure": "hidden"}, "t") == "hidden"
    )
    # Among patterns the first match in the object wins.
    config = {"command": "x", "toolExposure": {"*": "hidden", "a*": "direct"}}
    assert get_mcp_tool_exposure(config, "abc") == "hidden"
    reordered = {"command": "x", "toolExposure": {"a*": "direct", "*": "hidden"}}
    assert get_mcp_tool_exposure(reordered, "abc") == "direct"
    assert get_mcp_tool_exposure(reordered, "bc") == "hidden"


# --- loading --------------------------------------------------------------


# Pi: merges global and trusted project servers and validates entries
def test_merges_user_and_trusted_project_servers_and_validates(tmp_path: Path) -> None:
    setup = Setup(
        tmp_path,
        {
            "mcpServers": {
                "shared": {"command": "global-cmd"},
                "remote": {
                    "url": "https://example.com/mcp",
                    "headers": {"Authorization": TOKEN_HEADER},
                },
                "off": {"command": "x", "enabled": False},
                "bad": {"args": ["no command"]},
                "legacy": {"type": "sse", "url": "https://example.com/sse"},
                "badUrl": {"url": "example.com/mcp"},
                "bad name": {"command": "x"},
            }
        },
        {"mcpServers": {"shared": {"command": "project-cmd", "exposure": "direct"}}},
    )

    trusted = setup.load(trusted=True)
    # Disabled servers are kept so /mcp can enable them again.
    assert [(s.name, s.scope, s.config) for s in trusted.servers] == [
        ("shared", "project", {"command": "project-cmd", "exposure": "direct"}),
        (
            "remote",
            "global",
            {
                "url": "https://example.com/mcp",
                "headers": {"Authorization": TOKEN_HEADER},
            },
        ),
        ("off", "global", {"command": "x", "enabled": False}),
    ]
    assert trusted.servers[0].source == str(setup.project_file)
    assert trusted.servers[1].source == str(setup.user_file)
    assert trusted.project_config == str(setup.project_file)
    user = setup.user_file
    assert trusted.errors == [
        f'{user}: server "bad" needs either "command" (stdio) or "url" '
        "(streamable HTTP)",
        f'{user}: server "legacy": legacy SSE transport is not supported; '
        "use the streamable HTTP URL",
        f'{user}: server "badUrl": url must be an http or https URL',
        f'{user}: invalid server name "bad name" (use letters, digits, "_" and "-")',
    ]

    # Untrusted projects cannot add or override servers, since stdio servers
    # run commands.
    untrusted = setup.load(trusted=False)
    assert untrusted.servers[0].config == {"command": "global-cmd"}
    assert untrusted.servers[0].scope == "global"
    assert untrusted.project_config is None
    assert untrusted.errors == trusted.errors


# Pi: lets project entries override enabled and exposure of global servers
def test_project_entries_override_enabled_and_exposure(tmp_path: Path) -> None:
    setup = Setup(
        tmp_path,
        {"mcpServers": {"tools": {"command": "x", "env": {"TOKEN": "secret"}}}},
        # An override cannot change the command, which would run with the
        # user-level env.
        {
            "mcpServers": {
                "tools": {"enabled": False, "args": ["y"]},
                "missing": {"enabled": False},
            }
        },
    )
    project = setup.project_file
    loaded = setup.load(trusted=True)
    assert [(s.name, s.override, s.config) for s in loaded.servers] == [
        ("tools", None, {"command": "x", "env": {"TOKEN": "secret"}})
    ]
    assert loaded.errors == [
        f'{project}: server "tools": an override can only set enabled, exposure, '
        "toolExposure",
        f'{project}: server "missing" needs "command" or "url", or a global server '
        "to override",
    ]

    project.write_text(json.dumps({"mcpServers": {"tools": {"enabled": False}}}))
    [tools] = setup.load(trusted=True).servers
    assert tools.override == str(project)
    assert tools.config == {
        "command": "x",
        "env": {"TOKEN": "secret"},
        "enabled": False,
    }
    # The entry still names the file that defines the server.
    assert (tools.source, tools.scope) == (str(setup.user_file), "global")

    # Overrides keep `enabled: true`, since it replaces the user-level value.
    update_mcp_server_config(project, "tools", enabled=True)
    assert json.loads(project.read_text())["mcpServers"]["tools"] == {"enabled": True}


# Pi: rejects server names that differ only in - and _
def test_rejects_server_names_that_differ_only_in_dash_and_underscore(
    tmp_path: Path,
) -> None:
    setup = Setup(
        tmp_path,
        {
            "mcpServers": {
                "work-files": {"command": "a"},
                "work_files": {"command": "b"},
            }
        },
        {
            "mcpServers": {
                "work_files": {"command": "c"},
                "work-files": {"command": "d"},
            }
        },
    )
    untrusted = setup.load(trusted=False)
    assert [s.name for s in untrusted.servers] == ["work-files"]
    assert untrusted.errors == [
        f'{setup.user_file}: server "work_files" conflicts with "work-files"'
    ]

    # A project entry conflicts with a user-level name too, while the same
    # name replaces the user-level entry.
    trusted = setup.load(trusted=True)
    assert [(s.name, s.scope, s.config) for s in trusted.servers] == [
        ("work-files", "project", {"command": "d"})
    ]
    assert trusted.errors == [
        f'{setup.user_file}: server "work_files" conflicts with "work-files"',
        f'{setup.project_file}: server "work_files" conflicts with "work-files"',
    ]
    assert (
        mcp_namespace("work-files") == mcp_namespace("work_files") == "mcp__work_files"
    )


# Pi: validates exposure and reads autoEnableCodemode with project precedence
def test_validates_exposure_and_reads_auto_enable_codemode(tmp_path: Path) -> None:
    setup = Setup(
        tmp_path,
        {
            "autoEnableCodemode": False,
            "mcpServers": {
                "later": {"command": "x", "exposure": "deferred"},
                # `codemode-deferred` is an alias for `codemode`.
                "scripts": {
                    "command": "x",
                    "exposure": "codemode-deferred",
                    "toolExposure": {"a": "codemode-deferred"},
                },
                "off": {"command": "x", "exposure": "hidden"},
                "wrong": {"command": "x", "exposure": "model-only"},
                "described": {"command": "x", "description": "Docs search"},
                "badDescription": {"command": "x", "description": 1},
            },
        },
        {"autoEnableCodemode": "yes", "mcpServers": {}},
    )

    untrusted = setup.load(trusted=False)
    assert untrusted.auto_enable_codemode is False
    assert [(s.name, s.config.get("exposure")) for s in untrusted.servers] == [
        ("later", "deferred"),
        ("scripts", "codemode"),
        ("off", "hidden"),
        ("described", None),
    ]
    assert untrusted.servers[1].config["toolExposure"] == {"a": "codemode"}
    assert untrusted.servers[3].config["description"] == "Docs search"
    user = setup.user_file
    assert untrusted.errors == [
        f'{user}: server "wrong": exposure must be one of "codemode", "deferred", '
        '"direct", "hidden"',
        f'{user}: server "badDescription": description must be a string',
    ]

    trusted = setup.load(trusted=True)
    assert trusted.auto_enable_codemode is False
    assert (
        f"{setup.project_file}: autoEnableCodemode must be a boolean" in trusted.errors
    )


def test_project_auto_enable_codemode_overrides_the_user_level_value(
    tmp_path: Path,
) -> None:
    setup = Setup(tmp_path, {"autoEnableCodemode": False}, {"autoEnableCodemode": True})
    assert setup.load(trusted=True).auto_enable_codemode is True
    assert setup.load(trusted=False).auto_enable_codemode is False
    assert (
        Setup(tmp_path / "none", {}, {}).load(trusted=True).auto_enable_codemode is None
    )


# Pi: validates the OAuth callback URL, scope, and client name
def test_validates_the_oauth_callback_url_scope_and_client_name(tmp_path: Path) -> None:
    def http(**oauth: object) -> dict[str, object]:
        return {"url": HTTP, "oauth": oauth}

    loaded = load_user(
        tmp_path,
        {
            "ok": http(callbackUrl="http://localhost:8080/callback", scope="a b"),
            "ipv6": http(callbackUrl="http://[::1]/cb", callbackPort=9000),
            "same": http(callbackUrl="http://127.0.0.1:2/cb", callbackPort=2),
            "remote": http(callbackUrl="https://example.com/callback"),
            "both": http(callbackUrl="http://127.0.0.1:1/cb", callbackPort=2),
            "scope": http(scope=["a"]),
            "named": http(clientName="Claude Code"),
            "unnamed": http(clientName=" "),
            "metadata": http(authServerMetadataUrl="https://idp.example/m"),
            "plainMetadata": http(authServerMetadataUrl="http://idp.example/m"),
            # pipy needs the document base URL with cimd (DEV-06).
            "cimd": http(
                clientRegistration="cimd",
                callbackUrl="http://localhost/callback",
                clientMetadataBaseUrl="https://id.example/oauth/",
            ),
            "badRegistration": http(clientRegistration="auto"),
            "cimdClient": http(clientRegistration="cimd", clientId="x"),
            "cimdPath": http(
                clientRegistration="cimd", callbackUrl="http://127.0.0.1/cb"
            ),
        },
    )
    assert [s.name for s in loaded.servers] == [
        "ok",
        "ipv6",
        "same",
        "named",
        "metadata",
        "cimd",
    ]
    prefix = f"{tmp_path / 'home' / 'mcp.json'}: server "
    assert loaded.errors == [
        f'{prefix}"remote": oauth.callbackUrl must be an http URI on localhost, '
        "127.0.0.1, or [::1] without query or fragment",
        f'{prefix}"both": oauth.callbackUrl and oauth.callbackPort name different '
        "ports",
        f'{prefix}"scope": oauth.scope must be a string',
        f'{prefix}"unnamed": oauth.clientName must be a non-empty string',
        f'{prefix}"plainMetadata": oauth.authServerMetadataUrl must be an https URL, '
        "or http on localhost, 127.0.0.1, or [::1]",
        f'{prefix}"badRegistration": oauth.clientRegistration must be "dcr" or "cimd"',
        f'{prefix}"cimdClient": oauth.clientRegistration "cimd" cannot be combined '
        "with oauth.clientId or oauth.clientName",
        f'{prefix}"cimdPath": oauth.clientRegistration "cimd" requires '
        "oauth.callbackUrl on localhost or 127.0.0.1 with path /callback",
    ]


# Pi: resolves per-tool exposure from exact names, then patterns in order
def test_resolves_per_tool_exposure_from_exact_names_then_patterns(
    tmp_path: Path,
) -> None:
    loaded = load_user(
        tmp_path,
        {
            "gh": {
                "command": "x",
                "exposure": "deferred",
                "toolExposure": {
                    "get_*": "codemode",
                    "get_me": "direct",
                    "*delete*": "hidden",
                    "get_file.*": "direct",
                },
            },
            "bad": {"command": "x", "toolExposure": {"a": "visible"}},
        },
    )
    assert loaded.errors == [
        f'{tmp_path / "home" / "mcp.json"}: server "bad": toolExposure "a" must be '
        'one of "codemode", "deferred", "direct", "hidden"'
    ]
    config = loaded.servers[0].config
    assert get_mcp_tool_exposure(config, "get_me") == "direct"
    assert get_mcp_tool_exposure(config, "get_issue") == "codemode"
    assert get_mcp_tool_exposure(config, "get_delete_hint") == "codemode"
    assert get_mcp_tool_exposure(config, "delete_repo") == "hidden"
    assert get_mcp_tool_exposure(config, "list_issues") == "deferred"
    # Only `*` is special.
    only_star = {"command": "x", "toolExposure": {"get_file.*": "direct"}}
    assert get_mcp_tool_exposure(only_star, "get_file_x") == "codemode"
    assert get_mcp_tool_exposure(only_star, "get_file.x") == "direct"
    # A pattern covers the whole name.
    assert get_mcp_tool_exposure(only_star, "forget_file.x") == "codemode"
    assert get_mcp_tool_exposure(only_star, "get_file.x\n") == "codemode"


# Pi: validates provider auth and accepts it only in the global mcp.json
def test_validates_provider_auth_and_accepts_it_only_at_user_level(
    tmp_path: Path,
) -> None:
    setup = Setup(
        tmp_path,
        {
            "mcpServers": {
                "radius": {
                    "url": "https://radius.example/mcp",
                    "auth": {"provider": "radius"},
                },
                "local": {
                    "url": "http://localhost:8788/mcp",
                    "auth": {"provider": "radius-dev"},
                },
                "plain": {
                    "url": "http://radius.example/mcp",
                    "auth": {"provider": "radius"},
                },
                "empty": {
                    "url": "https://radius.example/mcp",
                    "auth": {"provider": ""},
                },
            }
        },
        {
            "mcpServers": {
                "radius": {
                    "url": "https://evil.example/mcp",
                    "auth": {"provider": "radius"},
                }
            }
        },
    )
    loaded = setup.load(trusted=True)
    # The project entry cannot replace the user-level one: it would send the
    # credential to its own URL.
    assert [(s.name, s.scope, s.config["url"]) for s in loaded.servers] == [
        ("radius", "global", "https://radius.example/mcp"),
        ("local", "global", "http://localhost:8788/mcp"),
    ]
    assert loaded.errors == [
        f'{setup.user_file}: server "plain": auth requires an https URL, or http on '
        "localhost, 127.0.0.1, or [::1]",
        f'{setup.user_file}: server "empty": auth.provider must be a provider name',
        f'{setup.project_file}: server "radius": auth is only allowed in the global '
        "mcp.json",
    ]


# --- files ---------------------------------------------------------------


def test_missing_files_are_not_an_error(tmp_path: Path) -> None:
    loaded = load_mcp_config(
        config_home=tmp_path / "nowhere", cwd=tmp_path / "void", project_trusted=True
    )
    assert loaded == LoadedMcpConfig(
        servers=[],
        errors=[],
        auto_enable_codemode=None,
        project_config=str(tmp_path / "void" / ".pipy" / "mcp.json"),
    )
    # A config home that is a file has no mcp.json either.
    (tmp_path / "file").write_text("")
    assert not load_mcp_config(
        config_home=tmp_path / "file", cwd=tmp_path, project_trusted=False
    ).errors


@pytest.mark.parametrize(
    "text",
    ["[]", '"servers"', "null", '{"mcpServers": []}', '{"mcpServers": null}'],
)
def test_a_file_of_the_wrong_shape_is_reported_and_the_other_still_loads(
    tmp_path: Path, text: str
) -> None:
    setup = Setup(tmp_path, None, {"mcpServers": {"p": {"command": "x"}}})
    setup.user_file.write_text(text)
    loaded = setup.load(trusted=True)
    assert loaded.errors == [
        f'{setup.user_file}: expected an object with an "mcpServers" object'
    ]
    assert [s.name for s in loaded.servers] == ["p"]


@pytest.mark.parametrize(
    "data",
    [
        b'{"mcpServers": {',
        b"",
        b'{"mcpServers": {}, "x": NaN}',
        b'{"mcpServers": {}, "x": -Infinity}',
        b'\xef\xbb\xbf{"mcpServers": {}}',
        b'{"mcpServers": {"caf\xe9": {}}}',
        b"[" * 200_000,
    ],
)
def test_invalid_json_is_reported_with_the_path_and_the_other_file_still_loads(
    tmp_path: Path, data: bytes
) -> None:
    setup = Setup(tmp_path, {"mcpServers": {"u": {"command": "x"}}})
    setup.project_file.write_bytes(data)
    loaded = setup.load(trusted=True)
    [error] = loaded.errors
    assert error.startswith(f"{setup.project_file}: ")
    assert len(error) > len(f"{setup.project_file}: ")
    assert [s.name for s in loaded.servers] == ["u"]
    assert setup.load(trusted=False).errors == []


def test_an_unreadable_file_is_reported(tmp_path: Path) -> None:
    setup = Setup(tmp_path, None, {"mcpServers": {"p": {"command": "x"}}})
    setup.user_file.mkdir()
    loaded = setup.load(trusted=True)
    [error] = loaded.errors
    assert error.startswith(f"{setup.user_file}: ")
    assert [s.name for s in loaded.servers] == ["p"]


def test_an_invalid_entry_is_skipped_and_the_rest_load(tmp_path: Path) -> None:
    loaded = load_user(
        tmp_path,
        {"a": {"command": "x"}, "b": 1, "c": {"command": "y"}, "d": {"enabled": False}},
    )
    user = tmp_path / "home" / "mcp.json"
    assert [s.name for s in loaded.servers] == ["a", "c"]
    assert loaded.errors == [
        f'{user}: server "b" must be an object',
        # Only a project file can override.
        f'{user}: server "d" needs either "command" (stdio) or "url" (streamable HTTP)',
    ]


def test_entries_are_read_in_javascript_key_order(tmp_path: Path) -> None:
    # Keys that are array indexes come first, so "1_0" conflicts with "1-0"
    # and not the other way round.
    loaded = load_user(
        tmp_path,
        {
            "b": {"command": "x"},
            "10": {"command": "x"},
            "1_0": {"command": "x"},
            "2": {"command": "x"},
            "1-0": {"command": "x"},
            "a": 1,
            "1": 1,
        },
    )
    user = tmp_path / "home" / "mcp.json"
    assert [s.name for s in loaded.servers] == ["2", "10", "b", "1_0"]
    assert loaded.errors == [
        f'{user}: server "1" must be an object',
        f'{user}: server "1-0" conflicts with "1_0"',
        f'{user}: server "a" must be an object',
    ]


def test_a_project_entry_that_defines_a_server_replaces_the_whole_entry(
    tmp_path: Path,
) -> None:
    setup = Setup(
        tmp_path,
        {
            "mcpServers": {
                "first": {"command": "x", "env": {"TOKEN": "secret"}, "enabled": False},
                "second": {"command": "y"},
                "third": {"url": HTTP, "headers": {"A": "b"}},
            }
        },
        {
            "mcpServers": {
                "third": {"type": "stdio", "command": "z"},
                "first": {"url": "https://b.example/mcp"},
                "new": {"command": "n"},
            }
        },
    )
    loaded = setup.load(trusted=True)
    assert loaded.errors == []
    # Replaced entries keep the position of the user-level entry.
    assert [(s.name, s.scope, s.override, s.config) for s in loaded.servers] == [
        ("first", "project", None, {"url": "https://b.example/mcp"}),
        ("second", "global", None, {"command": "y"}),
        ("third", "project", None, {"type": "stdio", "command": "z"}),
        ("new", "project", None, {"command": "n"}),
    ]


def test_an_override_merges_only_its_three_keys(tmp_path: Path) -> None:
    user_entry = {
        "url": HTTP,
        "headers": {"Authorization": TOKEN_HEADER},
        "oauth": {"clientId": "id"},
        "exposure": "direct",
        "toolExposure": {"a": "hidden"},
        "timeout": 5,
    }
    setup = Setup(
        tmp_path,
        {
            "mcpServers": {
                "docs": user_entry,
                "auth": {"url": HTTP, "auth": {"provider": "p"}},
                "empty": {"command": "x"},
            }
        },
        {
            "mcpServers": {
                "docs": {
                    "exposure": "codemode-deferred",
                    "toolExposure": {"b": "direct"},
                },
                # The user-level `auth` is kept under a project override.
                "auth": {"enabled": False},
                "empty": {},
            }
        },
    )
    docs, auth, empty = setup.load(trusted=True).servers
    assert docs.config == {
        **user_entry,
        "exposure": "codemode",
        "toolExposure": {"b": "direct"},
    }
    assert auth.config == {"url": HTTP, "auth": {"provider": "p"}, "enabled": False}
    assert (auth.scope, auth.override) == ("global", str(setup.project_file))
    assert (empty.config, empty.override) == ({"command": "x"}, str(setup.project_file))


def test_an_override_is_validated_after_the_merge(tmp_path: Path) -> None:
    setup = Setup(
        tmp_path,
        {"mcpServers": {"a": {"command": "x"}, "b": {"command": "y"}}},
        {"mcpServers": {"a": {"exposure": "loud"}, "b": {"enabled": "no"}}},
    )
    loaded = setup.load(trusted=True)
    assert [(s.name, s.override, s.config) for s in loaded.servers] == [
        ("a", None, {"command": "x"}),
        ("b", None, {"command": "y"}),
    ]
    assert loaded.errors == [
        f'{setup.project_file}: server "a": exposure must be one of {EXPOSURES}',
        f'{setup.project_file}: server "b": enabled must be a boolean',
    ]


def test_a_project_entry_with_a_null_type_is_not_an_override(tmp_path: Path) -> None:
    setup = Setup(
        tmp_path,
        {"mcpServers": {"a": {"command": "x"}}},
        {"mcpServers": {"a": {"type": None, "enabled": False}}},
    )
    loaded = setup.load(trusted=True)
    assert loaded.errors == [
        f'{setup.project_file}: server "a" needs either "command" (stdio) or "url" '
        "(streamable HTTP)"
    ]
    assert loaded.servers[0].config == {"command": "x"}


def test_project_auth_is_rejected_whenever_the_entry_has_a_url_key(
    tmp_path: Path,
) -> None:
    setup = Setup(
        tmp_path,
        {},
        {
            "mcpServers": {
                "http": {"url": "https://b.example/mcp", "auth": {"provider": "p"}},
                "stdio": {"command": "x", "url": 1, "auth": {}},
                "unset": {"command": "x", "url": 1, "auth": None},
                "plain": {"command": "x", "auth": {"provider": "p"}},
            }
        },
    )
    loaded = setup.load(trusted=True)
    assert [s.name for s in loaded.servers] == ["unset", "plain"]
    assert loaded.errors == [
        f'{setup.project_file}: server "{name}": auth is only allowed in the global '
        "mcp.json"
        for name in ("http", "stdio")
    ]


def test_disabled_servers_stay_in_the_loaded_list(tmp_path: Path) -> None:
    loaded = load_user(
        tmp_path, {"on": {"command": "x"}, "off": {"url": HTTP, "enabled": False}}
    )
    assert [(s.name, s.config.get("enabled")) for s in loaded.servers] == [
        ("on", None),
        ("off", False),
    ]


def test_loading_does_not_change_the_files(tmp_path: Path) -> None:
    setup = Setup(
        tmp_path,
        {"mcpServers": {"a": {"command": "x", "exposure": "codemode-deferred"}}},
        {"mcpServers": {"a": {"enabled": False}}},
    )
    before = (setup.user_file.read_bytes(), setup.project_file.read_bytes())
    setup.load(trusted=True)
    assert (setup.user_file.read_bytes(), setup.project_file.read_bytes()) == before


# --- editing -------------------------------------------------------------

FOUR_SPACES = """{
    "theme": "kept",
    "autoEnableCodemode": false,
    "mcpServers": {
        "docs": {
            "url": "https://a.example/mcp",
            "headers": {
                "Authorization": "Bearer ${TOKEN}"
            },
            "enabled": false,
            "exposure": "direct"
        },
        "files": {
            "command": "npx",
            "args": [
                "-y",
                "pkg"
            ],
            "empty": {},
            "none": [],
            "ratio": 1.0,
            "text": "caf\u00e9 \\ud83d\\ude00 \\u2028"
        }
    },
    "after": null
}
"""


def test_update_keeps_other_content_and_four_space_indentation(tmp_path: Path) -> None:
    file = tmp_path / "mcp.json"
    file.write_text(FOUR_SPACES, encoding="utf-8")

    update_mcp_server_config(file, "docs", exposure="deferred")
    assert file.read_text(encoding="utf-8") == FOUR_SPACES.replace(
        '"direct"', '"deferred"'
    ).replace("\\ud83d\\ude00 \\u2028", "\U0001f600 \u2028")

    # The defaults remove the key in an entry that defines the server.
    update_mcp_server_config(file, "docs", enabled=True, exposure="codemode")
    text = file.read_text(encoding="utf-8")
    assert json.loads(text)["mcpServers"]["docs"] == {
        "url": "https://a.example/mcp",
        "headers": {"Authorization": "Bearer ${TOKEN}"},
    }
    assert (
        json.loads(text)["mcpServers"]["files"]
        == (json.loads(FOUR_SPACES)["mcpServers"]["files"])
    )
    assert '\n            "ratio": 1.0,\n' in text
    assert list(json.loads(text)) == [
        "theme",
        "autoEnableCodemode",
        "mcpServers",
        "after",
    ]
    assert text.endswith("\n}\n")

    update_mcp_server_config(file, "docs", enabled=False)
    update_mcp_server_config(file, "files", exposure="hidden")
    servers = json.loads(file.read_text(encoding="utf-8"))["mcpServers"]
    assert servers["docs"]["enabled"] is False
    assert servers["files"]["exposure"] == "hidden"
    assert list(servers) == ["docs", "files"]


def test_update_keeps_tab_indentation(tmp_path: Path) -> None:
    file = tmp_path / "mcp.json"
    file.write_text(
        '{\n\t"mcpServers": {\n\t\t"a": {\n\t\t\t"command": "x"\n\t\t}\n\t}\n}'
    )
    update_mcp_server_config(file, "a", enabled=False)
    assert file.read_text() == (
        '{\n\t"mcpServers": {\n\t\t"a": {\n\t\t\t"command": "x",\n'
        '\t\t\t"enabled": false\n\t\t}\n\t}\n}\n'
    )


@pytest.mark.parametrize(
    ("text", "indent"),
    [
        ('{"mcpServers":{"a":{"command":"x"}}}', "  "),
        ('{\r\n   "mcpServers": {"a": {"command": "x"}}}', "   "),
        # The first indented line decides, and at most ten characters count.
        ('{\n \t"mcpServers": {\n      "a": {"command": "x"}}}', " \t"),
        ("{\n" + " " * 12 + '"mcpServers": {"a": {"command": "x"}}}', " " * 10),
        ('{\n\n  \n   "mcpServers": {"a": {"command": "x"}}}', "   "),
    ],
)
def test_update_uses_the_indentation_of_the_first_indented_line(
    tmp_path: Path, text: str, indent: str
) -> None:
    file = tmp_path / "mcp.json"
    file.write_bytes(text.encode())
    update_mcp_server_config(file, "a", exposure="direct")
    assert file.read_bytes().decode() == (
        "{\n"
        f'{indent}"mcpServers": {{\n'
        f'{indent * 2}"a": {{\n'
        f'{indent * 3}"command": "x",\n'
        f'{indent * 3}"exposure": "direct"\n'
        f"{indent * 2}}}\n"
        f"{indent}}}\n"
        "}\n"
    )


def test_update_writes_defaults_in_an_override_entry(tmp_path: Path) -> None:
    file = tmp_path / "mcp.json"
    file.write_text('{"mcpServers": {"a": {"enabled": false}, "b": {}}}')
    update_mcp_server_config(file, "a", enabled=True, exposure="codemode")
    update_mcp_server_config(file, "b", exposure="codemode")
    assert json.loads(file.read_text())["mcpServers"] == {
        "a": {"enabled": True, "exposure": "codemode"},
        "b": {"exposure": "codemode"},
    }


def test_update_with_override_creates_a_missing_entry(tmp_path: Path) -> None:
    file = tmp_path / "project" / ".pipy" / "mcp.json"
    update_mcp_server_config(file, "tools", enabled=True, override=True)
    assert (
        file.read_text()
        == '{\n  "mcpServers": {\n    "tools": {\n      "enabled": true\n    }\n  }\n}\n'
    )

    file.write_text(
        '{"autoEnableCodemode": true, "mcpServers": {"other": {"command": "x"}}}'
    )
    update_mcp_server_config(file, "tools", exposure="codemode", override=True)
    assert json.loads(file.read_text()) == {
        "autoEnableCodemode": True,
        "mcpServers": {"other": {"command": "x"}, "tools": {"exposure": "codemode"}},
    }

    # An entry that exists is changed as it is, override or not.
    update_mcp_server_config(file, "other", enabled=True, override=True)
    update_mcp_server_config(file, "other", exposure="hidden", override=True)
    assert json.loads(file.read_text())["mcpServers"]["other"] == {
        "command": "x",
        "exposure": "hidden",
    }


@pytest.mark.parametrize(
    "text",
    [
        '{"mcpServers": {"other": {"command": "x"}}}',
        "{}",
        '{"mcpServers": {"a": null}}',
        None,
    ],
)
def test_update_of_a_server_the_file_does_not_define(
    tmp_path: Path, text: str | None
) -> None:
    file = tmp_path / "mcp.json"
    if text is not None:
        file.write_text(text)
    with pytest.raises(McpConfigError) as raised:
        update_mcp_server_config(file, "a", enabled=False)
    assert str(raised.value) == f'{file} does not define MCP server "a"'
    assert (file.read_text() if file.exists() else None) == text


def test_update_rejects_an_unknown_exposure(tmp_path: Path) -> None:
    file = tmp_path / "mcp.json"
    file.write_text('{"mcpServers": {"a": {"command": "x"}}}')
    with pytest.raises(ValueError):
        update_mcp_server_config(file, "a", exposure="loud")  # type: ignore[arg-type]
    assert file.read_text() == '{"mcpServers": {"a": {"command": "x"}}}'


def test_add_creates_the_file_and_its_directory(tmp_path: Path) -> None:
    file = tmp_path / "new" / "deep" / "mcp.json"
    config = {"command": "npx", "args": ["-y", "pkg"]}
    assert add_mcp_server_config(file, "files", config) is False
    assert file.read_text() == (
        '{\n  "mcpServers": {\n    "files": {\n      "command": "npx",\n'
        '      "args": [\n        "-y",\n        "pkg"\n      ]\n    }\n  }\n}\n'
    )
    loaded = load_mcp_config(
        config_home=file.parent, cwd=tmp_path, project_trusted=False
    )
    assert [(s.name, s.config) for s in loaded.servers] == [("files", config)]


def test_add_replaces_an_entry_and_reports_it(tmp_path: Path) -> None:
    file = tmp_path / "mcp.json"
    file.write_text(
        '{\n    "x": [1],\n    "mcpServers": {"a": {"command": "old", "env": {}}, "b": null}\n}\n'
    )
    assert add_mcp_server_config(file, "a", {"url": HTTP}) is True
    assert add_mcp_server_config(file, "b", {"command": "b"}) is True
    assert add_mcp_server_config(file, "c", {"command": "c"}) is False
    text = file.read_text()
    assert json.loads(text) == {
        "x": [1],
        "mcpServers": {
            "a": {"url": HTTP},
            "b": {"command": "b"},
            "c": {"command": "c"},
        },
    }
    assert list(json.loads(text)["mcpServers"]) == ["a", "b", "c"]
    assert text.startswith('{\n    "x": [\n        1\n    ],\n')

    # A file without `mcpServers` gets one.
    other = tmp_path / "other.json"
    other.write_text('{"autoEnableCodemode": false}')
    assert add_mcp_server_config(other, "a", {"command": "x"}) is False
    assert json.loads(other.read_text()) == {
        "autoEnableCodemode": False,
        "mcpServers": {"a": {"command": "x"}},
    }


def test_add_writes_a_copy_of_the_config(tmp_path: Path) -> None:
    file = tmp_path / "mcp.json"
    config: dict[str, Any] = {"command": "x", "env": {"A": "caf\u00e9"}}
    add_mcp_server_config(file, "a", config)
    assert config == {"command": "x", "env": {"A": "caf\u00e9"}}
    assert "caf\u00e9" in file.read_text(encoding="utf-8")


def test_remove_reports_whether_the_entry_existed(tmp_path: Path) -> None:
    file = tmp_path / "mcp.json"
    assert remove_mcp_server_config(file, "a") is False
    assert not file.exists()

    text = (
        '{\n\t"mcpServers": {"a": {"command": "x"}, "b": {"command": "y"}},\n\t"x": 1}'
    )
    file.write_text(text)
    assert remove_mcp_server_config(file, "missing") is False
    # Nothing is rewritten when nothing was removed.
    assert file.read_text() == text
    assert remove_mcp_server_config(file, "a") is True
    assert file.read_text() == (
        '{\n\t"mcpServers": {\n\t\t"b": {\n\t\t\t"command": "y"\n\t\t}\n\t},\n\t"x": 1\n}\n'
    )
    assert remove_mcp_server_config(file, "a") is False

    file.write_text("{}")
    assert remove_mcp_server_config(file, "b") is False
    assert file.read_text() == "{}"


@pytest.mark.parametrize("text", ["[]", '{"mcpServers": []}', '"x"'])
def test_editing_a_file_of_the_wrong_shape_raises_and_keeps_it(
    tmp_path: Path, text: str
) -> None:
    file = tmp_path / "mcp.json"
    file.write_text(text)
    for edit in (
        lambda: update_mcp_server_config(file, "a", enabled=False, override=True),
        lambda: add_mcp_server_config(file, "a", {"command": "x"}),
        lambda: remove_mcp_server_config(file, "a"),
    ):
        with pytest.raises(McpConfigError) as raised:
            edit()
        assert str(raised.value) == (
            f'{file}: expected an object with an "mcpServers" object'
        )
    assert file.read_text() == text


@pytest.mark.parametrize(
    "data", [b'{"mcpServers": ', b'{"x": NaN}', b'{"mcpServers": {"caf\xe9": {}}}']
)
def test_editing_invalid_json_raises_and_keeps_the_file(
    tmp_path: Path, data: bytes
) -> None:
    file = tmp_path / "mcp.json"
    file.write_bytes(data)
    for edit in (
        lambda: update_mcp_server_config(file, "a", enabled=False),
        lambda: add_mcp_server_config(file, "a", {"command": "x"}),
        lambda: remove_mcp_server_config(file, "a"),
    ):
        with pytest.raises(McpConfigError) as raised:
            edit()
        assert str(raised.value)
    assert file.read_bytes() == data


def test_editing_keeps_a_file_whose_numbers_json_cannot_hold(tmp_path: Path) -> None:
    file = tmp_path / "mcp.json"
    text = '{"big": 1e999, "mcpServers": {"a": {"command": "x"}}}'
    file.write_text(text)
    with pytest.raises(McpConfigError) as raised:
        update_mcp_server_config(file, "a", enabled=False)
    assert str(raised.value).startswith(f"{file}: ")
    assert file.read_text() == text


def test_editing_escapes_a_lone_surrogate(tmp_path: Path) -> None:
    file = tmp_path / "mcp.json"
    file.write_text('{"note": "a\\ud800b", "mcpServers": {"a": {"command": "x"}}}')
    update_mcp_server_config(file, "a", enabled=False)
    text = file.read_text(encoding="utf-8")
    assert '"note": "a\\ud800b"' in text
    assert json.loads(text)["mcpServers"]["a"] == {"command": "x", "enabled": False}


def test_editing_writes_through_a_symlink(tmp_path: Path) -> None:
    target = tmp_path / "real.json"
    target.write_text('{"mcpServers": {"a": {"command": "x"}}}')
    link = tmp_path / "mcp.json"
    os.symlink(target, link)
    update_mcp_server_config(link, "a", enabled=False)
    assert link.is_symlink()
    assert json.loads(target.read_text())["mcpServers"]["a"]["enabled"] is False


def test_an_unreadable_file_raises_os_error_when_editing(tmp_path: Path) -> None:
    file = tmp_path / "mcp.json"
    file.mkdir()
    with pytest.raises(OSError):
        add_mcp_server_config(file, "a", {"command": "x"})
