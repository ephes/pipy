"""Tests for the ``pipy mcp`` shell commands.

Mirrors the cases of Pi's ``mcp-command.test.ts`` except the ones about
``login`` and ``logout``, against the scripted fake stdio server.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

import pipy_harness.cli as cli
from pipy_harness.native.mcp.shell_cli import (
    HELP,
    HELP_HINT,
    McpCommandOptions,
    run_mcp_command,
)
from pipy_harness.native.project_trust import ProjectTrustStore

_FAKE_SERVER = Path(__file__).parent / "fixtures" / "mcp_fake_server.py"
_MISSING = "pipy-test-missing-mcp-server"


class _Home:
    """A config home that is also the working directory, as in Pi's test."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.global_config = path / "mcp.json"
        self.project_config = path / ".pipy" / "mcp.json"
        self.trust = ProjectTrustStore(path / "trust.json")
        self.output: list[str] = []
        self.errors: list[str] = []

    def write(self, servers: dict[str, Any], *, project: bool = False) -> None:
        path = self.project_config if project else self.global_config
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"mcpServers": servers}), encoding="utf-8")

    def run(self, *args: str) -> int:
        self.output.clear()
        self.errors.clear()
        return run_mcp_command(
            list(args),
            McpCommandOptions(
                cwd=self.path,
                config_home=self.path,
                client_version="0.0.0-test",
                log=self.output.append,
                error=self.errors.append,
                trust_store=self.trust,
            ),
        )

    @property
    def text(self) -> str:
        return "\n".join(self.output)

    def read(self, *, project: bool = False) -> Any:
        path = self.project_config if project else self.global_config
        return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture
def home(tmp_path: Path) -> _Home:
    directory = tmp_path / "home"
    directory.mkdir()
    return _Home(directory)


def _fixture(tmp_path: Path, name: str = "scenario", **scenario: Any) -> dict[str, Any]:
    """The config of a fake server with one ``echo`` tool unless told otherwise."""

    scenario.setdefault("tools", [{"name": "echo", "inputSchema": {"type": "object"}}])
    path = tmp_path / f"{name}.json"
    path.write_text(json.dumps(scenario), encoding="utf-8")
    return {
        "command": sys.executable,
        "args": ["-I", str(_FAKE_SERVER), "--scenario", str(path)],
    }


def test_lists_servers_with_state_tools_and_errors_and_fails_while_anything_is_wrong(
    home: _Home, tmp_path: Path
) -> None:
    fixture = _fixture(tmp_path)
    home.write(
        {
            "fixture": fixture,
            "broken": {"command": _MISSING},
            "parked": {**fixture, "enabled": False},
            "bad": {"args": ["no command"]},
        }
    )

    assert home.run("list") == 1

    assert home.errors == []
    lines = home.output
    start = lines.index("fixture: connected, 1 tool (codemode, global)")
    assert lines[start + 1] == "  " + " ".join([fixture["command"], *fixture["args"]])
    assert lines[start + 2] == "  tools: echo"
    broken = lines.index("broken: failed (codemode, global)")
    assert lines[broken + 1] == f"  {_MISSING}"
    assert lines[broken + 2].startswith("  ") and _MISSING in lines[broken + 2]
    assert "parked: disabled (codemode, global)" in lines
    assert any(
        line.startswith(f"config error: {home.global_config}: ")
        and 'server "bad" needs either "command"' in line
        for line in lines
    )

    home.write({"fixture": fixture})
    assert home.run("list") == 0


def test_prints_json_for_scripts(home: _Home, tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    home.write({"fixture": fixture, "parked": {**fixture, "enabled": False}})

    assert home.run("list", "--json") == 0

    assert len(home.output) == 1
    parsed = json.loads(home.text)
    assert parsed == {
        "servers": [
            {
                "name": "fixture",
                "scope": "global",
                "source": str(home.global_config),
                "enabled": True,
                "exposure": "codemode",
                "transport": " ".join([fixture["command"], *fixture["args"]]),
                "state": "connected",
                "tools": ["echo"],
            },
            {
                "name": "parked",
                "scope": "global",
                "source": str(home.global_config),
                "enabled": False,
                "exposure": "codemode",
                "transport": " ".join([fixture["command"], *fixture["args"]]),
                "state": "disabled",
                "tools": [],
            },
        ],
        "errors": [],
    }
    # Pi's key order, two spaces of indentation.
    assert list(parsed["servers"][0]) == [
        "name",
        "scope",
        "source",
        "enabled",
        "exposure",
        "transport",
        "state",
        "tools",
    ]
    assert home.text.startswith('{\n  "servers": [\n    {\n      "name": "fixture",')


def test_json_reports_failures_errors_and_the_ignored_project_file(
    home: _Home, tmp_path: Path
) -> None:
    home.write({"broken": {"command": _MISSING}, "bad": {"args": []}})
    home.write({"local": _fixture(tmp_path)}, project=True)

    assert home.run("list", "--json") == 1

    parsed = json.loads(home.text)
    assert [server["name"] for server in parsed["servers"]] == ["broken"]
    assert parsed["servers"][0]["state"] == "failed"
    assert _MISSING in parsed["servers"][0]["error"]
    assert len(parsed["errors"]) == 1 and '"bad"' in parsed["errors"][0]
    assert parsed["note"] == (
        f"{home.project_config} is ignored because the project is not trusted. "
        "Start pipy in the project to trust it."
    )
    assert list(parsed) == ["servers", "errors", "note"]


def test_list_marks_tools_whose_exposure_differs_and_names_the_override(
    home: _Home, tmp_path: Path
) -> None:
    tools = [
        {"name": name, "inputSchema": {"type": "object"}}
        for name in ("echo", "get_issue", "delete_issue")
    ]
    home.write(
        {
            "tracker": {
                **_fixture(tmp_path, tools=tools),
                "exposure": "deferred",
                "toolExposure": {"get_*": "direct", "delete_issue": "hidden"},
            }
        }
    )
    home.write({"tracker": {"exposure": "direct"}}, project=True)
    home.trust.set(home.path, True)

    assert home.run("list") == 0
    assert home.output[0] == "tracker: connected, 3 tools (direct, global)"
    assert home.output[2:] == [
        f"  project override: {home.project_config}",
        "  tools: echo, get_issue, delete_issue [hidden]",
    ]

    assert home.run("list", "--json") == 0
    report = json.loads(home.text)["servers"][0]
    assert report["override"] == str(home.project_config)
    assert report["toolExposure"] == {"delete_issue": "hidden"}
    assert list(report)[3] == "override"
    assert list(report)[-1] == "toolExposure"


def test_list_counts_resources_and_templates_of_a_server_that_offers_them(
    home: _Home, tmp_path: Path
) -> None:
    # CMDL-08 and CMDL-09: counted without MCP App entries; a server without
    # the capability has no line and no fields.
    resources = _fixture(
        tmp_path,
        "resources",
        capabilities={"tools": {}, "resources": {}},
        resources=[
            {"uri": "docs://intro", "name": "intro"},
            {"uri": "docs://faq", "name": "faq"},
            {"uri": "ui://docs/viewer", "name": "viewer"},
        ],
        resourceTemplates=[{"uriTemplate": "docs://pages/{slug}", "name": "page"}],
    )
    empty = _fixture(tmp_path, "empty", capabilities={"tools": {}, "resources": {}})
    home.write(
        {"docs": resources, "empty": empty, "plain": _fixture(tmp_path, "plain")}
    )

    assert home.run("list") == 0

    lines = home.output
    start = lines.index("docs: connected, 1 tool (codemode, global)")
    assert lines[start + 2 : start + 4] == [
        "  tools: echo",
        "  resources: 2, URI templates: 1",
    ]
    start = lines.index("empty: connected, 1 tool (codemode, global)")
    assert lines[start + 3] == "  resources: 0, URI templates: 0"
    assert lines.count("  tools: echo") == 3
    assert len([line for line in lines if line.startswith("  resources:")]) == 2

    assert home.run("list", "--json") == 0

    servers = {server["name"]: server for server in json.loads(home.text)["servers"]}
    assert (servers["docs"]["resources"], servers["docs"]["resourceTemplates"]) == (
        2,
        1,
    )
    # After the tools, before an error.
    assert list(servers["docs"])[-3:] == ["tools", "resources", "resourceTemplates"]
    assert (servers["empty"]["resources"], servers["empty"]["resourceTemplates"]) == (
        0,
        0,
    )
    assert "resources" not in servers["plain"]
    assert "resourceTemplates" not in servers["plain"]


def test_list_reads_the_project_file_only_in_a_trusted_project(
    home: _Home, tmp_path: Path
) -> None:
    fixture = _fixture(tmp_path)
    home.write({"local": fixture}, project=True)
    note = (
        f"{home.project_config} is ignored because the project is not trusted. "
        "Start pipy in the project to trust it."
    )

    # No decision and a saved "no" both ignore the file; nothing asks.
    assert home.run("list") == 0
    assert home.output == [
        f"No MCP servers configured. Add them to {home.global_config} "
        "or .pipy/mcp.json.",
        note,
    ]
    home.trust.set(home.path, False)
    assert home.run("list") == 0
    assert home.output[-1] == note

    home.trust.set(home.path, True)
    assert home.run("list") == 0
    assert home.output == [
        "local: connected, 1 tool (codemode, project)",
        "  " + " ".join([fixture["command"], *fixture["args"]]),
        "  tools: echo",
    ]


def test_list_without_servers_says_where_to_add_them(home: _Home) -> None:
    assert home.run("list") == 0
    assert home.output == [
        f"No MCP servers configured. Add them to {home.global_config} "
        "or .pipy/mcp.json."
    ]

    home.global_config.write_text("not json", encoding="utf-8")
    assert home.run("list") == 1
    assert len(home.output) == 1
    assert home.output[0].startswith(f"config error: {home.global_config}: ")


def test_list_reports_an_http_server_as_failed(home: _Home) -> None:
    home.write({"docs": {"url": "https://example.com/mcp", "exposure": "direct"}})

    assert home.run("list") == 1
    assert home.output == [
        "docs: failed (direct, global)",
        "  https://example.com/mcp",
        '  MCP server "docs" is an HTTP server; pipy connects only to stdio '
        "servers yet",
    ]


def test_list_indents_every_line_of_an_error_and_keeps_stderr(
    home: _Home, tmp_path: Path
) -> None:
    home.write(
        {
            "noisy": _fixture(
                tmp_path, startup=[{"stderr": "first\nsecond\n"}, {"exit": 3}]
            )
        }
    )

    assert home.run("list") == 1
    assert home.output[0] == "noisy: failed (codemode, global)"
    error = home.output[2]
    assert error.startswith("  ")
    assert error.endswith("\n  first\n  second")


def test_list_closes_every_server_before_it_returns(
    home: _Home, tmp_path: Path
) -> None:
    records = [tmp_path / f"record-{index}.jsonl" for index in range(3)]
    home.write(
        {
            f"server{index}": _fixture(
                tmp_path,
                f"scenario-{index}",
                record=str(record),
                onStdinClose=[{"record": "stdin-closed"}, {"exit": 0}],
            )
            for index, record in enumerate(records)
        }
    )

    assert home.run("list") == 0

    assert [line.split(":")[0] for line in home.output[::3]] == [
        "server0",
        "server1",
        "server2",
    ]
    for record in records:
        events = [
            json.loads(line).get("event")
            for line in record.read_text(encoding="utf-8").splitlines()
        ]
        assert "stdin-closed" in events


def test_list_connects_the_servers_at_the_same_time(
    home: _Home, tmp_path: Path
) -> None:
    slow = {"methods": {"initialize": [{"sleep": 1.0}, {"default": True}]}}
    records = [tmp_path / f"record-{index}.jsonl" for index in range(2)]
    home.write(
        {
            f"slow{index}": _fixture(
                tmp_path, f"slow-{index}", record=str(record), **slow
            )
            for index, record in enumerate(records)
        }
    )

    assert home.run("list") == 0

    def initialize_time(record: Path) -> float:
        for line in record.read_text(encoding="utf-8").splitlines():
            entry = json.loads(line)
            if entry.get("received", {}).get("method") == "initialize":
                return float(entry["t"])
        raise AssertionError("no initialize recorded")

    first, second = map(initialize_time, records)
    # One after the other, the second would start a second later.
    assert abs(first - second) < 0.9


def test_rejects_arguments_of_list(home: _Home) -> None:
    assert home.run("list", "extra") == 1
    assert home.errors == [f"Usage: pipy mcp list [--json]\n{HELP_HINT}"]
    assert home.run("list", "--verbose") == 1
    assert home.errors == [f"Unknown option --verbose.\n{HELP_HINT}"]


@pytest.mark.parametrize(
    "args",
    [(), ("help",), ("--help",), ("-h",), ("list", "--help"), ("add", "x", "-h")],
)
def test_prints_the_usage_text(home: _Home, args: tuple[str, ...]) -> None:
    assert home.run(*args) == 0
    assert home.output == [HELP]
    assert home.errors == []
    assert not home.global_config.exists()


def test_rejects_unknown_commands_and_the_commands_that_are_not_built(
    home: _Home,
) -> None:
    assert home.run("frobnicate") == 1
    assert home.errors == [f'Unknown mcp command "frobnicate".\n{HELP_HINT}']
    assert HELP_HINT == 'Use "pipy mcp --help" for usage.'

    assert home.run("login", "docs") == 1
    assert home.errors == ["pipy mcp login is not available yet."]
    assert home.run("logout", "docs") == 1
    assert home.errors == ["pipy mcp logout is not available yet."]
    assert "login" not in HELP and "logout" not in HELP


def test_adds_stdio_servers_and_passes_options_after_the_command_through(
    home: _Home,
) -> None:
    assert (
        home.run(
            "add",
            "--env",
            "A=1",
            "--env",
            "B=x=y",
            "files",
            "--",
            "npx",
            "-y",
            "server",
            "--root",
            ".",
        )
        == 0
    )
    assert home.output == [
        f'Added global MCP server "files" in {home.global_config}.',
        "Check it with: pipy mcp list",
    ]
    assert home.read() == {
        "mcpServers": {
            "files": {
                "command": "npx",
                "args": ["-y", "server", "--root", "."],
                "env": {"A": "1", "B": "x=y"},
            }
        }
    }
    assert home.global_config.read_text(encoding="utf-8").startswith(
        '{\n  "mcpServers": {\n    "files": {\n      "command": "npx",'
    )

    # Without `--`, options after the command belong to the command too.
    assert home.run("add", "files", "node", "server.js", "--port", "1", "-l") == 0
    assert home.output[0] == (
        f'Replaced global MCP server "files" in {home.global_config}.'
    )
    assert home.read() == {
        "mcpServers": {
            "files": {"command": "node", "args": ["server.js", "--port", "1", "-l"]}
        }
    }
    assert not home.project_config.exists()

    assert home.run("add", "work", "--cwd", "~/src", "--", "serve", "--", "-x") == 0
    assert home.read()["mcpServers"]["work"] == {
        "command": "serve",
        "args": ["--", "-x"],
        "cwd": "~/src",
    }


def test_adds_http_servers_and_keeps_other_content_of_the_file(home: _Home) -> None:
    home.global_config.write_text(
        json.dumps(
            {"autoEnableCodemode": False, "mcpServers": {"fixture": {"command": "x"}}},
            indent="\t",
        ),
        encoding="utf-8",
    )

    assert (
        home.run(
            "add",
            "docs",
            "--url",
            "https://example.com/mcp",
            "--bearer-token-env-var",
            "DOCS_TOKEN",
            "--header",
            "X-Team=core",
            "--exposure",
            "direct",
            "--description",
            "Product docs",
        )
        == 0
    )
    assert "mcp login" not in home.text
    assert home.read() == {
        "autoEnableCodemode": False,
        "mcpServers": {
            "fixture": {"command": "x"},
            "docs": {
                "url": "https://example.com/mcp",
                "headers": {"X-Team": "core", "Authorization": "Bearer ${DOCS_TOKEN}"},
                "exposure": "direct",
                "description": "Product docs",
            },
        },
    }
    assert '\n\t"mcpServers"' in home.global_config.read_text(encoding="utf-8")

    assert (
        home.run(
            "add",
            "sentry",
            "--url",
            "https://mcp.sentry.dev/mcp",
            "--oauth-client-id",
            "pi",
            "--oauth-client-secret",
            "${SENTRY_SECRET}",
            "--oauth-callback-port",
            "8123",
            "--oauth-client-name",
            "Claude Code",
        )
        == 0
    )
    assert home.output[-1] == (
        "Check it with: pipy mcp list. If it requires sign-in: pipy mcp login sentry"
    )
    assert home.read()["mcpServers"]["sentry"] == {
        "url": "https://mcp.sentry.dev/mcp",
        "oauth": {
            "clientId": "pi",
            "clientSecret": "${SENTRY_SECRET}",
            "callbackPort": 8123,
            "clientName": "Claude Code",
        },
    }

    # Another spelling of the header name still counts as an Authorization header.
    assert (
        home.run(
            "add",
            "plain",
            "--url",
            "http://localhost:1/mcp",
            "--header",
            "authorization=x",
        )
        == 0
    )
    assert home.output[-1] == "Check it with: pipy mcp list"


@pytest.mark.parametrize(
    ("args", "error"),
    [
        (
            ("add", "x"),
            "Usage: pipy mcp add <server> [options] "
            f"(--url <url> | -- <command> [args...])\n{HELP_HINT}",
        ),
        (
            ("add", "x", "--url", "https://example.com", "--", "cmd"),
            "Usage: pipy mcp add <server> [options] "
            f"(--url <url> | -- <command> [args...])\n{HELP_HINT}",
        ),
        (
            ("add", "--url", "https://example.com"),
            "Usage: pipy mcp add <server> [options] "
            f"(--url <url> | -- <command> [args...])\n{HELP_HINT}",
        ),
        (
            ("add", "bad name", "--", "cmd"),
            'invalid server name "bad name" (use letters, digits, "_" and "-")',
        ),
        (
            ("add", "x", "--url", "ftp://example.com"),
            'server "x": url must be an http or https URL',
        ),
        (
            ("add", "x", "--env", "A=1", "--url", "https://example.com"),
            "--env only applies to stdio servers.",
        ),
        (
            ("add", "x", "--cwd", "/tmp", "--url", "https://example.com"),
            "--cwd only applies to stdio servers.",
        ),
        (
            ("add", "x", "--header", "A=1", "--", "cmd"),
            "--header only applies to HTTP servers (--url).",
        ),
        (
            ("add", "x", "--oauth-client-name", "n", "--", "cmd"),
            "--oauth-client-name only applies to HTTP servers (--url).",
        ),
        (
            ("add", "x", "--env", "NOVALUE", "--", "cmd"),
            '--env expects KEY=VALUE, got "NOVALUE".',
        ),
        (
            ("add", "x", "--env", "=value", "--", "cmd"),
            '--env expects KEY=VALUE, got "=value".',
        ),
        (
            ("add", "x", "--url", "https://example.com", "--header", "novalue"),
            '--header expects KEY=VALUE, got "novalue".',
        ),
        (
            ("add", "x", "--exposure", "loud", "--", "cmd"),
            'server "x": exposure must be one of "codemode", "deferred", "direct", '
            '"hidden"',
        ),
        (
            ("add", "x", "--url", "https://example.com", "--oauth-callback-port", "x"),
            'server "x": oauth.callbackPort must be a port number',
        ),
        (
            ("add", "x", "--url", "https://example.com", "--oauth-callback-port", "0"),
            'server "x": oauth.callbackPort must be a port number',
        ),
        (("add", "x", "--frobnicate", "--", "cmd"), None),
        (("add", "x", "--env"), "--env needs a value."),
        (("add", "x", "--url"), "--url needs a value."),
    ],
)
def test_rejects_invalid_add_invocations_without_writing(
    home: _Home, args: tuple[str, ...], error: str | None
) -> None:
    assert home.run(*args) == 1
    if error is None:
        error = f"Unknown option --frobnicate.\n{HELP_HINT}"
    assert home.errors == [error]
    assert home.output == []
    assert not home.global_config.exists()
    assert not home.project_config.exists()


def test_adds_and_removes_project_servers(home: _Home) -> None:
    assert home.run("add", "-l", "local", "--", "node", "server.js") == 0
    assert home.output == [
        f'Added project MCP server "local" in {home.project_config}.',
        f"The project is not trusted, so {home.project_config} is ignored until "
        "you start pipy in the project and trust it.",
        "Check it with: pipy mcp list",
    ]
    assert home.read(project=True) == {
        "mcpServers": {"local": {"command": "node", "args": ["server.js"]}}
    }
    assert not home.global_config.exists()

    assert home.run("remove", "local") == 1
    assert home.errors == [
        f'No global MCP server named "local" in {home.global_config}. '
        f"It is defined in {home.project_config}; use --local."
    ]

    assert home.run("remove", "local", "--local") == 0
    assert home.output == [
        f'Removed project MCP server "local" from {home.project_config}.'
    ]
    assert home.read(project=True) == {"mcpServers": {}}

    home.trust.set(home.path, True)
    assert home.run("add", "--local", "other", "cmd") == 0
    assert home.output == [
        f'Added project MCP server "other" in {home.project_config}.',
        "Check it with: pipy mcp list",
    ]


def test_removes_global_servers(home: _Home) -> None:
    home.write(
        {
            "fixture": {"command": "a"},
            "broken": {"command": _MISSING},
            "parked": {"command": "a", "enabled": False},
            "bad": {"args": ["no command"]},
        }
    )

    assert home.run("remove", "broken") == 0
    assert home.output == [
        f'Removed global MCP server "broken" from {home.global_config}.'
    ]
    assert list(home.read()["mcpServers"]) == ["fixture", "parked", "bad"]

    assert home.run("remove", "broken") == 1
    assert home.errors == [
        f'No global MCP server named "broken" in {home.global_config}.'
    ]

    assert home.run("remove", "fixture", "-l") == 1
    assert home.errors == [
        f'No project MCP server named "fixture" in {home.project_config}. '
        f"It is defined in {home.global_config}; omit --local."
    ]
    assert not home.project_config.exists()


@pytest.mark.parametrize("args", [("remove",), ("remove", "a", "b"), ("remove", "")])
def test_rejects_invalid_remove_invocations(home: _Home, args: tuple[str, ...]) -> None:
    assert home.run(*args) == 1
    assert home.errors == [f"Usage: pipy mcp remove <server> [-l]\n{HELP_HINT}"]


def test_reports_a_file_that_cannot_be_updated(home: _Home) -> None:
    home.global_config.write_text("[]", encoding="utf-8")

    assert home.run("add", "x", "cmd") == 1
    assert home.errors == [
        f"Could not update {home.global_config}: {home.global_config}: "
        'expected an object with an "mcpServers" object'
    ]
    assert home.run("remove", "x") == 1
    assert home.errors[0].startswith(f"Could not update {home.global_config}: ")
    assert home.global_config.read_text(encoding="utf-8") == "[]"


def test_a_trust_store_that_cannot_be_read_fails_without_writing(home: _Home) -> None:
    home.trust.path.write_text("not json", encoding="utf-8")

    assert home.run("add", "-l", "local", "cmd") == 1
    assert len(home.errors) == 1
    assert home.errors[0].startswith(f"failed to read trust store {home.trust.path}")
    assert not home.project_config.exists()
    assert home.run("list") == 1
    assert home.output == []

    # The global file needs no trust decision.
    assert home.run("add", "x", "cmd") == 0


def test_the_trust_decision_is_read_from_the_commands_config_home(
    home: _Home, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Another config home, the one the environment names, trusts the project.
    ambient = tmp_path / "ambient"
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(ambient))
    ProjectTrustStore().set(home.path, True)
    assert ProjectTrustStore().path == ambient / "trust.json"
    home.write({"local": {"command": _MISSING}}, project=True)
    output: list[str] = []
    options = McpCommandOptions(
        cwd=home.path,
        config_home=home.path,
        client_version="0.0.0-test",
        log=output.append,
        error=output.append,
    )

    assert run_mcp_command(["list"], options) == 0
    assert "is ignored because the project is not trusted" in output[-1]
    assert not any(line.startswith("local:") for line in output)

    ProjectTrustStore(home.path / "trust.json").set(home.path, True)
    output.clear()
    assert run_mcp_command(["list"], options) == 1
    assert output[0] == "local: failed (codemode, project)"


def test_pipy_mcp_runs_without_a_session_in_the_current_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    config_home = tmp_path / "config"
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(config_home))
    monkeypatch.chdir(project)
    # The switches that turn MCP off for a session do not apply.
    config_home.mkdir()
    (config_home / "settings.json").write_text(
        json.dumps({"extensions": ["-builtin:mcp"]}), encoding="utf-8"
    )

    assert cli.main(["mcp", "add", "files", _MISSING, "-y", "server", "--json"]) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    assert f'Added global MCP server "files" in {config_home / "mcp.json"}.' in (
        captured.out
    )
    assert json.loads((config_home / "mcp.json").read_text(encoding="utf-8")) == {
        "mcpServers": {
            "files": {"command": _MISSING, "args": ["-y", "server", "--json"]}
        }
    }

    assert cli.main(["mcp", "add", "-l", "local", "--", "cmd"]) == 0
    assert (Path.cwd() / ".pipy" / "mcp.json").exists()
    capsys.readouterr()

    assert cli.main(["mcp", "list", "--json"]) == 1
    captured = capsys.readouterr()
    listed = json.loads(captured.out)
    assert [server["name"] for server in listed["servers"]] == ["files"]
    assert "is ignored because the project is not trusted" in listed["note"]

    assert cli.main(["mcp", "--help"]) == 0
    assert capsys.readouterr().out == HELP + "\n"
    assert cli.main(["mcp", "frobnicate"]) == 1
    assert capsys.readouterr().err == (
        f'Unknown mcp command "frobnicate".\n{HELP_HINT}\n'
    )


def test_pipy_help_lists_the_mcp_command(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        cli.main(["--help"])
    assert "Add, remove and check MCP servers" in capsys.readouterr().out
