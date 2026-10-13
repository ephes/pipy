"""Execute the checked-in codemode and MCP embedding example itself (MCP
parity plan S38, row SDK-04)."""

from __future__ import annotations

import importlib.util
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import pytest

from pipy_harness.native.codemode import selftest
from pipy_harness.native.codemode.selftest import Availability
from pipy_harness.native.fake import FakeNativeProvider
from pipy_harness.native.models import ProviderRequest


def _load_example() -> ModuleType:
    example_path = (
        Path(__file__).resolve().parents[1]
        / "docs"
        / "examples"
        / "sdk_codemode_mcp.py"
    )
    spec = importlib.util.spec_from_file_location(
        "sdk_codemode_mcp_example", example_path
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _transport_threads(before: set[threading.Thread]) -> list[str]:
    return [
        thread.name
        for thread in threading.enumerate()
        if thread not in before and thread.name.startswith("mcp")
    ]


def test_the_example_runs_a_prompt_with_all_three(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    isolated_home = tmp_path / "home"
    isolated_config = tmp_path / "config"
    isolated_home.mkdir()
    isolated_config.mkdir()
    monkeypatch.setenv("HOME", str(isolated_home))
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(isolated_config))
    # The declaration of `codemode` does not need its runtime.
    monkeypatch.setattr(selftest, "availability", lambda: Availability(True))
    requests: list[ProviderRequest] = []
    complete = FakeNativeProvider.complete

    def capture(self: Any, request: ProviderRequest, **kwargs: Any) -> Any:
        requests.append(request)
        return complete(self, request, **kwargs)

    monkeypatch.setattr(FakeNativeProvider, "complete", capture)
    module = _load_example()
    run_example = cast(Callable[[Path], Any], getattr(module, "run_example"))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    before = set(threading.enumerate())

    result = run_example(workspace)

    assert result.tool_results == (
        "Loaded 1 tool. They are available from your next call:\n"
        "- mcp__docs__lookup: Look a term up in the product docs.",
        "pipy: a coding agent",
    )
    assert result.snapshot.user_turn_count == 1
    assert result.snapshot.tool_invocation_count == 2
    assert result.snapshot.provider_failure is None
    declared = [[tool.name for tool in request.available_tools] for request in requests]
    # All three: both tools are declared from the first request, and the
    # server's tool once the search loaded it.
    assert {"codemode", "tool_search"} <= set(declared[0])
    assert "mcp__docs__lookup" not in declared[0]
    assert "mcp__docs__lookup" in declared[1]
    # Nothing of the example reads or writes the caller's configuration.
    assert list(isolated_config.iterdir()) == []
    # The session's end closed the connection to the server.
    deadline = time.monotonic() + 10
    while _transport_threads(before) and time.monotonic() < deadline:
        time.sleep(0.01)
    assert _transport_threads(before) == []
