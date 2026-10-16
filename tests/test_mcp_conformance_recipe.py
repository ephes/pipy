"""Tests for the MCP conformance recipe (scripts/mcp_conformance, MCP parity
plan S40, DEV-12).

The official suite needs Node and the network, so ``just mcp-conformance`` is
a manual recipe. These tests cover what does not need the suite: how the
runner reads and compares results, the committed baseline, and the client
under test against the repository's fake OAuth MCP server.
"""

from __future__ import annotations

import importlib.util
import json
import shlex
import sys
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

_DIR = Path(__file__).resolve().parents[1] / "scripts" / "mcp_conformance"

sys.path.insert(0, str(Path(__file__).parent / "fixtures"))

from mcp_fake_oauth import FakeOAuthMcpServer  # noqa: E402


def _load(name: str) -> Any:
    spec = importlib.util.spec_from_file_location(
        f"mcp_conformance_{name}", _DIR / f"{name}.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # dataclasses resolve annotations through sys.modules, so register first.
    sys.modules.setdefault(spec.name, module)
    spec.loader.exec_module(module)
    return module


run: Any = _load("run")
client: Any = _load("client")

# The checks pipy does not pass, each for the reason README.md gives. Pi's
# baseline (test/mcp-conformance/baseline.json) lists the same ones, with
# ``pi-client`` where this one has ``pipy-client``.
KNOWN_FAILURES = {
    ("2025-11-25", "elicitation-sep1034-client-defaults"): [
        "client-elicitation-sep1034-boolean-default",
        "client-elicitation-sep1034-enum-default",
        "client-elicitation-sep1034-general",
        "client-elicitation-sep1034-integer-default",
        "client-elicitation-sep1034-number-default",
        "client-elicitation-sep1034-string-default",
    ],
    ("2025-11-25", "auth/basic-cimd"): ["cimd-client-id-used"],
    ("2025-11-25", "auth/scope-retry-limit"): ["pipy-client"],
}


def _result(mode: str, scenario: str, **checks: str) -> Any:
    result = run.ScenarioResult(mode, scenario)
    for check, status in checks.items():
        result.record(check.replace("_", "-"), status)
    return result


def _baseline() -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads((_DIR / "baseline.json").read_text())
    return loaded


# --- the committed baseline -------------------------------------------------


def test_the_baseline_is_for_the_pinned_suite_and_every_mode() -> None:
    baseline = _baseline()

    assert baseline["conformance"] == run.CONFORMANCE_PACKAGE
    assert tuple(baseline["modes"]) == run.MODES
    assert [len(scenarios) for scenarios in baseline["modes"].values()] == [2, 5, 19]


def test_the_baseline_fails_only_the_known_checks() -> None:
    failing = {
        (mode, scenario): checks["failing"]
        for mode, scenarios in _baseline()["modes"].items()
        for scenario, checks in scenarios.items()
        if checks["failing"]
    }

    assert failing == KNOWN_FAILURES


def test_the_client_completes_every_other_baselined_scenario() -> None:
    for mode, scenarios in _baseline()["modes"].items():
        for scenario, checks in scenarios.items():
            if (mode, scenario) == ("2025-11-25", "auth/scope-retry-limit"):
                continue
            assert run.CLIENT_CHECK in checks["passing"], (mode, scenario)


# --- reading results --------------------------------------------------------


def test_scenario_names_are_read_from_the_list_output() -> None:
    output = (
        "Client scenarios (test against a client):\n"
        "  - initialize [2025-06-18,2025-11-25]\n"
        "  - auth/scope-step-up [2025-11-25,2026-07-28]\n"
    )

    assert run.parse_scenario_list(output) == ["initialize", "auth/scope-step-up"]


def test_a_repeated_check_passes_only_when_every_instance_passes() -> None:
    result = run.ScenarioResult("2025-11-25", "tools_call")

    run.record_checks(
        result,
        [
            {"id": "token-request", "status": "SUCCESS"},
            {"id": "token-request", "status": "FAILURE", "errorMessage": "no verifier"},
            {"id": "token-request", "status": "SUCCESS"},
            {"id": "trace", "status": "INFO"},
            {"id": "later", "status": "SKIPPED"},
            {"name": "by-name", "status": "success"},
            {"id": "no-status"},
            {"id": "warned", "status": "WARNING", "description": "should not"},
        ],
    )

    assert result.checks == {
        "token-request": "fail",
        "by-name": "pass",
        "no-status": "fail",
        "warned": "fail",
    }
    assert result.messages == {"token-request": "no verifier", "warned": "should not"}


def test_checks_that_are_no_list_fail_the_runner_check() -> None:
    result = run.ScenarioResult("2025-11-25", "tools_call")

    run.record_checks(result, {"checks": []})

    assert result.checks == {"runner": "fail"}


@pytest.mark.parametrize(
    ("content", "status", "message"),
    [
        ('{"success": true}', "pass", ""),
        ('{"success": false, "error": "gave up"}', "fail", "gave up"),
        ('{"success": "yes"}', "fail", ""),
        ("[]", "fail", "client.py wrote a report that is no object"),
        ("{", "fail", "client.py wrote a report that is not JSON"),
        (None, "fail", "client.py wrote no report"),
    ],
)
def test_the_client_report_is_one_more_check(
    tmp_path: Path, content: str | None, status: str, message: str
) -> None:
    path = tmp_path / "pipy-client.json"
    if content is not None:
        path.write_text(content)
    result = run.ScenarioResult("2025-11-25", "initialize")

    run.record_client_report(result, path)

    assert result.checks == {"pipy-client": status}
    assert result.messages.get("pipy-client", "") == message


# --- comparing with the baseline --------------------------------------------

_MODES = {
    "2025-11-25": {
        "initialize": {"passing": ["init", "pipy-client"], "failing": []},
        "auth/basic-cimd": {"passing": ["pipy-client"], "failing": ["cimd"]},
    }
}


def test_results_that_equal_the_baseline_are_no_regression() -> None:
    results = [
        _result("2025-11-25", "initialize", init="pass", pipy_client="pass"),
        _result("2025-11-25", "auth/basic-cimd", pipy_client="pass", cimd="fail"),
    ]

    assert run.compare(results, _MODES) == ([], [])
    assert run.to_baseline(results)["modes"] == _MODES


def test_a_failing_check_the_baseline_does_not_list_is_a_regression() -> None:
    results = [
        _result(
            "2025-11-25", "initialize", init="fail", pipy_client="pass", new="fail"
        ),
        _result("2025-11-25", "auth/basic-cimd", pipy_client="pass", cimd="fail"),
        _result("2025-11-25", "unknown", pipy_client="fail"),
    ]
    results[0].messages["init"] = "wrong version"

    regressions, fixed = run.compare(results, _MODES)

    assert regressions == [
        "2025-11-25 initialize: init fails: wrong version",
        "2025-11-25 initialize: new fails",
        "2025-11-25 unknown: pipy-client fails",
    ]
    assert fixed == []


def test_a_passing_check_that_is_missing_is_a_regression() -> None:
    results = [
        _result("2025-11-25", "initialize", pipy_client="pass"),
        _result("2025-11-25", "auth/basic-cimd", pipy_client="pass"),
    ]

    regressions, _fixed = run.compare(results, _MODES)

    # A baselined failure that is no longer reported is not one.
    assert regressions == ["2025-11-25 initialize: init was not reported"]


def test_a_baselined_scenario_that_did_not_run_is_a_regression() -> None:
    results = [_result("2025-11-25", "initialize", init="pass", pipy_client="pass")]

    regressions, _fixed = run.compare(results, _MODES)

    assert regressions == ["2025-11-25 auth/basic-cimd: scenario did not run"]


def test_a_baselined_failure_that_passes_is_reported_as_fixed() -> None:
    results = [
        _result("2025-11-25", "initialize", init="pass", pipy_client="pass"),
        _result("2025-11-25", "auth/basic-cimd", pipy_client="pass", cimd="pass"),
    ]

    assert run.compare(results, _MODES) == ([], ["2025-11-25 auth/basic-cimd: cimd"])


def test_a_run_of_a_subset_is_compared_with_that_part_of_the_baseline() -> None:
    results = [_result("2025-11-25", "initialize", init="pass", pipy_client="pass")]

    scoped = run.scoped_baseline(_MODES, ["2025-11-25"], ["initialize"])

    assert scoped == {"2025-11-25": {"initialize": _MODES["2025-11-25"]["initialize"]}}
    assert run.compare(results, scoped) == ([], [])
    assert run.scoped_baseline(_MODES, ["2025-06-18"], None) == {"2025-06-18": {}}
    assert run.scoped_baseline(_MODES, ["2025-11-25"], None) == _MODES


def test_update_baseline_refuses_a_run_of_a_subset(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    # Also on a machine without Node: the invocation is judged first.
    monkeypatch.setattr(run.shutil, "which", lambda _name: None)

    code = run.main(["--update-baseline", "--scenario", "initialize"])

    assert code == 1
    assert "--update-baseline needs a full run" in capsys.readouterr().err


def test_a_run_needs_npx(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(run.shutil, "which", lambda _name: None)

    code = run.main([])

    assert code == 1
    assert "needs Node's npx on PATH" in capsys.readouterr().err


def test_the_launcher_runs_the_client_with_this_interpreter(tmp_path: Path) -> None:
    launcher = run.write_launcher(tmp_path)

    assert " " not in launcher.name
    assert launcher.stat().st_mode & 0o111
    shebang, command = launcher.read_text().splitlines()
    assert shebang == "#!/bin/sh"
    assert shlex.split(command) == [
        "exec",
        sys.executable,
        str(_DIR / "client.py"),
        "$@",
    ]
    # The arguments reach the client as they are.
    assert command.endswith(' "$@"')


def test_the_launcher_quotes_paths_a_shell_would_split(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(run.sys, "executable", "/opt/my python/bin/python$3")

    command = run.write_launcher(tmp_path).read_text().splitlines()[1]

    assert shlex.split(command)[1] == "/opt/my python/bin/python$3"


# --- the client under test --------------------------------------------------


def test_the_tool_calls_of_each_scenario() -> None:
    schema = {"type": "object", "$defs": {"a": {"type": "string"}}}
    tools = [
        {"name": "json_schema_echo"},
        {"name": "json_schema_2020_12_tool", "inputSchema": schema},
    ]

    assert client.tool_calls("initialize", []) == []
    assert client.tool_calls("tools_call", []) == [("add_numbers", {"a": 2, "b": 3})]
    assert client.tool_calls("sse-retry", []) == [("test_reconnection", {})]
    assert client.tool_calls("auth/scope-step-up", []) == [("test-tool", {})]
    assert client.tool_calls("elicitation-sep1034-client-defaults", []) == [
        ("test_client_elicitation_defaults", {})
    ]
    assert client.tool_calls("json-schema-2020-12-preservation", tools) == [
        ("json_schema_echo", {"schema": schema})
    ]
    assert client.tool_calls("something-new", tools) is None
    with pytest.raises(RuntimeError, match="was not listed"):
        client.tool_calls("json-schema-2020-12-preservation", [])


def test_a_pre_registered_client_is_configured_like_in_mcp_json() -> None:
    url = "http://127.0.0.1:1/mcp"

    assert client.server_config(url, {}) == {"url": url, "timeout": 20}
    assert client.server_config(url, {"client_id": "abc"})["oauth"] == {
        "clientId": "abc"
    }
    assert client.server_config(url, {"client_id": "abc", "client_secret": "s3"})[
        "oauth"
    ] == {"clientId": "abc", "clientSecret": "s3"}
    assert client.read_context(None) == {}
    assert client.read_context('{"client_id": "abc"}') == {"client_id": "abc"}
    with pytest.raises(ValueError, match="not an object"):
        client.read_context("[]")


@pytest.fixture
def server() -> Iterator[FakeOAuthMcpServer]:
    with FakeOAuthMcpServer() as fake:
        yield fake


def _run_client(
    server: FakeOAuthMcpServer, scenario: str, tmp_path: Path
) -> tuple[int, dict[str, Any]]:
    report = tmp_path / "report.json"
    code = client.main(
        ["client.py", server.url],
        {
            "MCP_CONFORMANCE_SCENARIO": scenario,
            "PIPY_MCP_CONFORMANCE_REPORT": str(report),
        },
    )
    loaded: dict[str, Any] = json.loads(report.read_text())
    return code, loaded


def test_the_client_signs_in_through_the_simulated_browser_and_calls_the_tool(
    server: FakeOAuthMcpServer, tmp_path: Path
) -> None:
    code, report = _run_client(server, "auth/metadata-default", tmp_path)

    assert (code, report) == (0, {"success": True})
    token = server.log[-1].removeprefix("call ")
    assert server.log == ["401 none", "register", "token code", f"call {token}"]
    # The connection is closed with the session's DELETE.
    assert server.deletes == [token]


def test_the_simulated_user_gives_up_after_three_sign_ins(
    server: FakeOAuthMcpServer, tmp_path: Path
) -> None:
    server.insufficient_scope = "admin"

    code, report = _run_client(server, "auth/scope-retry-limit", tmp_path)

    assert code == 1
    assert report == {
        "success": False,
        "error": "Still requires sign-in after 3 sign-ins",
    }
    assert server.log.count("token code") == 3
    assert [request["scope"] for request in server.authorizations[1:]] == [
        "admin",
        "admin",
    ]


def test_a_scenario_the_client_does_not_know_fails_after_connecting(
    server: FakeOAuthMcpServer, tmp_path: Path
) -> None:
    code, report = _run_client(server, "something-new", tmp_path)

    assert code == 1
    assert report == {"success": False, "error": "Unknown scenario something-new"}


def test_the_client_needs_a_scenario_and_a_server_url(tmp_path: Path) -> None:
    report = tmp_path / "report.json"

    code = client.main(["client.py"], {"PIPY_MCP_CONFORMANCE_REPORT": str(report)})

    assert code == 1
    assert json.loads(report.read_text())["error"].startswith("Usage:")
    assert client.main(["client.py", "http://127.0.0.1:1/mcp"], {}) == 1


def test_a_browser_that_fails_cancels_the_sign_in() -> None:
    def visit(url: str) -> None:
        raise RuntimeError("no redirect")

    browser = client.SimulatedBrowser(visit)
    browser.show_authorization_url("http://127.0.0.1:1/authorize?state=x")

    assert browser.prompt_for_redirect_url(threading.Event()) is None


def test_a_redirect_away_from_the_loopback_callback_is_not_followed(
    server: FakeOAuthMcpServer,
) -> None:
    from mcp_fake_oauth import Reply

    server.route(
        "/elsewhere",
        lambda _request: Reply(302, {"Location": "https://example.com/cb"}),
    )
    server.route("/nowhere", lambda _request: Reply(200))

    with pytest.raises(RuntimeError, match="not to the loopback callback"):
        client.visit_authorization_url(f"{server.origin}/elsewhere")
    with pytest.raises(RuntimeError, match="answered 200 without a redirect"):
        client.visit_authorization_url(f"{server.origin}/nowhere")
