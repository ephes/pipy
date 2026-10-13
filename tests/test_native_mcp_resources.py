"""Tests for the resource helpers of the MCP package (MCP parity plan S23)."""

from __future__ import annotations

import threading
from typing import Any

import pytest

from pipy_harness.native.mcp.resources import is_mcp_app_resource, settle_all


@pytest.mark.parametrize(
    ("item", "expected"),
    [
        ({"uri": "ui://docs/viewer"}, True),
        ({"uriTemplate": "ui://docs/{view}"}, True),
        ({"uri": "docs://a", "mimeType": "text/html;profile=mcp-app"}, True),
        ({"uri": "docs://a", "mimeType": 'text/html; PROFILE = "mcp-app"'}, True),
        ({"uri": "docs://a", "mimeType": "text/html"}, False),
        ({"uri": "docs://ui://x"}, False),
        ({"uri": "docs://a", "uriTemplate": "ui://x"}, False),
        ({"mimeType": 3}, False),
        ({}, False),
    ],
)
def test_mcp_app_resources_are_recognized(item: dict[str, Any], expected: bool) -> None:
    assert is_mcp_app_resource(item) is expected


def test_settle_all_runs_the_calls_at_the_same_time_and_keeps_their_order() -> None:
    barrier = threading.Barrier(3, timeout=10)

    def meet(value: int) -> int:
        barrier.wait()
        return value

    def fail() -> int:
        barrier.wait()
        raise ValueError("no")

    results = settle_all([lambda: meet(1), fail, lambda: meet(3)])

    assert results[0] == 1 and results[2] == 3
    assert isinstance(results[1], ValueError)
    assert settle_all([]) == []


def test_settle_all_runs_one_after_another_when_no_thread_starts(monkeypatch) -> None:
    def refuse(self: threading.Thread) -> None:
        raise RuntimeError("can't start new thread")

    monkeypatch.setattr(threading.Thread, "start", refuse)

    assert settle_all([lambda: 1, lambda: 2]) == [1, 2]
