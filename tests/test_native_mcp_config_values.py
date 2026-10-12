"""Tests for the values of an MCP server config that name a variable or a command."""

from __future__ import annotations

import pytest

from pipy_harness.native.mcp.config_values import (
    McpConfigValueError,
    resolve_config_headers,
    resolve_config_value,
)

_ENV = {"TOKEN": "t0k", "HOST": "example.com", "EMPTY": ""}


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("plain", "plain"),
        ("", ""),
        ("${TOKEN}", "t0k"),
        ("$TOKEN", "t0k"),
        ("Bearer ${TOKEN}", "Bearer t0k"),
        ("https://${HOST}/mcp?key=$TOKEN&x=1", "https://example.com/mcp?key=t0k&x=1"),
        ("${TOKEN}${TOKEN}", "t0kt0k"),
        # ``$$`` is a dollar sign and ``$!`` an exclamation mark.
        ("$$TOKEN", "$TOKEN"),
        ("cost: 5$$", "cost: 5$"),
        ("$!echo", "!echo"),
        # What is not a reference stays as written.
        ("${TOKEN", "${TOKEN"),
        ("${not a name}", "${not a name}"),
        ("${}", "${}"),
        ("a $ b", "a $ b"),
        ("100$", "100$"),
        ("$1", "$1"),
        # The name ends at the first character a name cannot contain.
        ("$TOKEN-suffix", "t0k-suffix"),
        ("a!b", "a!b"),
    ],
)
def test_templates_replace_environment_references(value: str, expected: str) -> None:
    assert resolve_config_value(value, "the value", env=_ENV) == expected


def test_one_missing_variable_is_named() -> None:
    with pytest.raises(McpConfigValueError) as raised:
        resolve_config_value(
            "Bearer ${MISSING}", 'MCP server "docs" env "API_KEY"', env=_ENV
        )
    assert str(raised.value) == (
        'Failed to resolve MCP server "docs" env "API_KEY" '
        "from environment variable: MISSING"
    )


def test_an_empty_variable_counts_as_missing() -> None:
    with pytest.raises(McpConfigValueError, match="environment variable: EMPTY$"):
        resolve_config_value("${EMPTY}", "x", env=_ENV)


def test_several_missing_variables_are_named_once_each_in_order() -> None:
    with pytest.raises(McpConfigValueError) as raised:
        resolve_config_value("$B/${A}/$B/${TOKEN}", "x", env=_ENV)
    assert str(raised.value) == "Failed to resolve x from environment variables: B, A"


def test_the_process_environment_is_the_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PIPY_TEST_MCP_VALUE", "from-env")
    monkeypatch.delenv("PIPY_TEST_MCP_UNSET", raising=False)
    assert resolve_config_value("${PIPY_TEST_MCP_VALUE}", "x") == "from-env"
    with pytest.raises(McpConfigValueError, match="PIPY_TEST_MCP_UNSET"):
        resolve_config_value("${PIPY_TEST_MCP_UNSET}", "x")


def test_a_command_value_is_its_trimmed_output() -> None:
    commands: list[str] = []

    def run(command: str) -> str | None:
        commands.append(command)
        return "  secret\n"

    assert resolve_config_value("!pass show $X", "x", run_command=run) == "secret"
    # The whole value after ``!`` is the command; nothing in it is replaced.
    assert commands == ["pass show $X"]


def test_a_command_runs_in_a_shell_on_every_call() -> None:
    assert resolve_config_value("!printf 'a b'", "x") == "a b"
    assert resolve_config_value("!printf 'a b'", "x") == "a b"


@pytest.mark.parametrize("command", ["exit 1", "true", "printf '  '"])
def test_a_command_that_fails_or_prints_nothing_is_an_error(command: str) -> None:
    with pytest.raises(McpConfigValueError) as raised:
        resolve_config_value(f"!{command}", 'MCP server "s" oauth.clientSecret')
    assert str(raised.value) == (
        f'Failed to resolve MCP server "s" oauth.clientSecret from shell command: {command}'
    )


def test_only_a_leading_exclamation_mark_makes_a_command() -> None:
    assert resolve_config_value(" !exit 1", "x", env={}) == " !exit 1"


def test_headers_are_resolved_and_named_in_errors() -> None:
    headers = {"Authorization": "Bearer ${TOKEN}", "X-Plain": "1"}
    assert resolve_config_headers(headers, 'MCP server "docs"', env=_ENV) == {
        "Authorization": "Bearer t0k",
        "X-Plain": "1",
    }
    with pytest.raises(McpConfigValueError) as raised:
        resolve_config_headers({"X-Key": "${NOPE}"}, 'MCP server "docs"', env=_ENV)
    assert str(raised.value) == (
        'Failed to resolve MCP server "docs" header "X-Key" '
        "from environment variable: NOPE"
    )


def test_no_headers_resolve_to_none() -> None:
    assert resolve_config_headers(None, "x") is None
    assert resolve_config_headers({}, "x") is None
