"""Tests for MCP server configuration: validation of entries."""

from __future__ import annotations

import json
from typing import Any

import pytest

from pipy_harness.native.mcp.config import (
    create_tool_name_matcher,
    get_mcp_tool_exposure,
    is_loopback_redirect_uri,
    mcp_namespace,
    validate_mcp_server_config,
)

# Config values are resolved at connect time, so the literal reference must
# survive validation.
TOKEN_HEADER = "Bearer ${TOKEN}"
HTTP = "https://a.example/mcp"


def test_namespace() -> None:
    assert (
        mcp_namespace("work-files") == mcp_namespace("work_files") == "mcp__work_files"
    )


# --- validation: one test per error text ------------------------------------------------

EXPOSURES = '"codemode", "deferred", "direct", "hidden"'
CALLBACK_ERROR = (
    "oauth.callbackUrl must be an http URI on localhost, 127.0.0.1, or [::1] "
    "without query or fragment"
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
    ({"url": "https://a.example\\x"}, 'server "s": url must be an http or https URL'),
    ({"url": "https://127.1/mcp"}, 'server "s": url must be an http or https URL'),
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
    (
        oauth(clientMetadataBaseUrl="http://id.example/oauth/"),
        'server "s": oauth.clientMetadataBaseUrl must be an https URL',
    ),
    (
        oauth(clientMetadataBaseUrl=1),
        'server "s": oauth.clientMetadataBaseUrl must be an https URL',
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
        ("http://127.1/cb", False),
        # A WHATWG parser would see the host evil.example here.
        ("http://evil.example\\@localhost/cb", False),
        ("http://localhost@evil.example/cb", False),
        ("http://evil.example:80@localhost/cb", True),
        ("http://127.0.0.01/cb", False),
        ("http://2130706433/cb", False),
        ("http://%6cocalhost/cb", False),
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


def test_tool_name_matcher() -> None:
    matches = create_tool_name_matcher(["read", "mcp__docs__*", "*_admin", "a.b"])
    assert matches("read")
    assert matches("mcp__docs__search")
    assert matches("mcp__docs__")
    assert matches("drop_admin")
    assert matches("a.b")
    assert not matches("reader")
    assert not matches("xmcp__docs__search")
    assert not matches("axb")
    assert not create_tool_name_matcher([])("read")
