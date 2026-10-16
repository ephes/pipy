"""Nested calls in the HTML export (goal-check M20, row RND-04).

Pi ``core/export-html/template.js`` ``renderNestedCalls``: a tool result
with a ``nestedCalls`` record (a codemode script's calls) shows
``Nested calls: <n>`` and, on a click, one line per call with its status,
arguments, duration and error. The page's script is run under Node with a
small stand-in for the DOM.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from test_native_export_tool_rows import _call, _result, _tree

from pipy_harness.native.agent import AgentAssistantMessage, ProductContent
from pipy_harness.native.export_distribution import export_native_session_to_html

_RUN_PAGE = r"""
const fs = require("node:fs");
const html = fs.readFileSync(process.argv[2], "utf8");
const data = /<script id="pipy-session-data"[^>]*>([^<]*)<\/script>/.exec(html)[1];
const code = /<script>\n([\s\S]*?)<\/script>/.exec(html)[1];
function element(tag) {
  const classes = new Set();
  return {
    tag, children: [], listeners: {}, textContent: "", innerHTML: "",
    get className() { return [...classes].join(" "); },
    set className(value) { classes.clear(); value.split(" ").filter(Boolean).forEach(c => classes.add(c)); },
    classList: { toggle(name) { if (classes.has(name)) classes.delete(name); else classes.add(name); } },
    append(...nodes) { this.children.push(...nodes); },
    addEventListener(type, listener) { this.listeners[type] = listener; },
  };
}
const nodes = { "pipy-session-data": { textContent: data }, entries: element("section"), meta: element("div") };
globalThis.document = { getElementById: id => nodes[id], createElement: element };
globalThis.window = { getSelection: () => ({ toString: () => "" }) };
new Function(code)();
// A click on every nested-calls section, to see that it expands.
const clicked = [];
(function visit(node) {
  if (node.className && node.className.split(" ").includes("nested-calls")) {
    node.listeners.click();
    clicked.push(node.className);
  }
  (node.children || []).forEach(visit);
})(nodes.entries);
function plain(node) {
  return { className: node.className, text: node.textContent, children: (node.children || []).map(plain) };
}
console.log(JSON.stringify({ entries: plain(nodes.entries), clicked }));
"""

_NESTED: dict[str, Any] = {
    "nestedCalls": {
        "calls": [
            {
                "id": "call-1/1",
                "name": "mcp__docs__search",
                "status": "ok",
                "durationMs": 12.4,
                "arguments": {"query": "pipy"},
            },
            {
                "id": "call-1/2",
                "name": "read",
                "status": "error",
                "durationMs": 3,
                "argumentsBytes": 9000,
                "error": "no such file\nat line 2",
            },
        ],
        "complete": False,
    }
}


def _page(tmp_path: Path, output: Path) -> dict[str, Any]:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not available")
    script = tmp_path / "run_page.js"
    script.write_text(_RUN_PAGE, encoding="utf-8")
    shown = subprocess.run(
        [node, str(script), str(output)],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    return json.loads(shown.stdout)


def _find(node: dict[str, Any], name: str) -> list[dict[str, Any]]:
    found = [node] if name in node["className"].split() else []
    for child in node["children"]:
        found += _find(child, name)
    return found


def _nested(node: dict[str, Any]) -> tuple[str, str]:
    (section,) = _find(node, "nested-calls")
    (preview,) = _find(section, "output-preview")
    (full,) = _find(section, "output-full")
    return preview["text"], full["text"]


def test_a_result_with_nested_calls_shows_them(tmp_path: Path) -> None:
    tree = _tree(
        tmp_path,
        AgentAssistantMessage(
            content=ProductContent(""),
            tool_calls=(_call("call-1", "codemode", '{"code": "print(1)"}'),),
        ),
        _result("call-1", "codemode", "Script completed", details=_NESTED),
    )
    page = _page(tmp_path, export_native_session_to_html(tree, tmp_path / "e.html"))
    results = [
        entry
        for entry in page["entries"]["children"]
        if entry["children"] and entry["children"][0]["text"] == "Tool result"
    ]
    assert len(results) == 1
    preview, full = _nested(results[0])
    assert preview == "Nested calls: 2 (incomplete record)\n... (4 more lines)"
    assert full.split("\n") == [
        "Nested calls: 2 (incomplete record)",
        '✓ mcp__docs__search {"query":"pipy"} 12ms',
        "✗ read [arguments omitted, 9000 bytes] 3ms",
        "    no such file",
        "    at line 2",
    ]
    assert page["clicked"] == ["nested-calls expanded"]


def test_left_out_arguments_show_their_preview(tmp_path: Path) -> None:
    # Goal check r8, D1: Pi's export draws codemode's row, whose calls show
    # the first 200 characters of their arguments (details.calls[].args);
    # pipy's page has no codemode row, so its nested-call lines show the
    # preview of arguments the record left out. A preview that is not text,
    # or names no call, is not shown.
    details = {
        **_NESTED,
        "argumentPreviews": {
            "call-1/2": '{"path":"recognizable.txt","content":"xx...',
            "call-1/1": 7,
            "constructor": "not a call",
        },
    }
    tree = _tree(
        tmp_path,
        AgentAssistantMessage(
            content=ProductContent(""),
            tool_calls=(_call("call-1", "codemode", '{"code": "print(1)"}'),),
        ),
        _result("call-1", "codemode", "Script completed", details=details),
    )
    page = _page(tmp_path, export_native_session_to_html(tree, tmp_path / "e.html"))
    _preview, full = _nested(page["entries"])
    assert full.split("\n")[1:3] == [
        '✓ mcp__docs__search {"query":"pipy"} 12ms',
        '✗ read {"path":"recognizable.txt","content":"xx... '
        "[arguments omitted, 9000 bytes] 3ms",
    ]


def test_a_drawn_call_shows_its_nested_calls_in_its_box(tmp_path: Path) -> None:
    # An MCP tool is drawn ahead (renderedTools); its nested calls go into
    # the call's box, where its result is.
    nested = {"nestedCalls": {"calls": _NESTED["nestedCalls"]["calls"][:1]}}
    nested["nestedCalls"]["complete"] = True
    tree = _tree(
        tmp_path,
        AgentAssistantMessage(
            content=ProductContent(""),
            tool_calls=(_call("call-1", "mcp__docs__search", '{"query": "pi"}'),),
        ),
        _result("call-1", "mcp__docs__search", "hit", details=nested),
    )
    page = _page(tmp_path, export_native_session_to_html(tree, tmp_path / "e.html"))
    (box,) = _find(page["entries"], "tool-rendered")
    preview, _full = _nested(box)
    assert preview == "Nested calls: 1\n... (1 more lines)"
    labels = [entry["children"][0]["text"] for entry in page["entries"]["children"]]
    assert "Tool result" not in labels


def test_a_result_without_nested_calls_shows_none(tmp_path: Path) -> None:
    tree = _tree(
        tmp_path,
        AgentAssistantMessage(
            content=ProductContent(""),
            tool_calls=(_call("call-1", "codemode", '{"code": "print(1)"}'),),
        ),
        _result("call-1", "codemode", "Script completed", details={"x": 1}),
    )
    page = _page(tmp_path, export_native_session_to_html(tree, tmp_path / "e.html"))
    assert _find(page["entries"], "nested-calls") == []


def test_malformed_calls_are_left_out_and_the_page_goes_on(tmp_path: Path) -> None:
    # An imported file can hold anything in `details`.
    calls = [
        {"name": {"toString": 0}, "status": "ok", "durationMs": 1},
        {"name": "read", "status": {"toString": 0}, "durationMs": 1},
        {"name": "read", "status": "constructor", "argumentsBytes": "x"},
        "not a call",
    ]
    tree = _tree(
        tmp_path,
        AgentAssistantMessage(
            content=ProductContent(""),
            tool_calls=(_call("call-1", "codemode", '{"code": "print(1)"}'),),
        ),
        _result(
            "call-1",
            "codemode",
            "done",
            details={"nestedCalls": {"calls": calls, "complete": True}},
        ),
        AgentAssistantMessage(content=ProductContent("after the result")),
    )
    page = _page(tmp_path, export_native_session_to_html(tree, tmp_path / "e.html"))
    _preview, full = _nested(page["entries"])
    assert full.split("\n") == [
        "Nested calls: 1",
        "? read [arguments omitted, ? bytes]",
    ]
    texts = [entry["children"][-1]["text"] for entry in page["entries"]["children"]]
    assert "after the result" in texts


def test_a_drawn_result_before_its_call_is_shown_on_its_own(tmp_path: Path) -> None:
    # A call id that names a property every object has.
    tree = _tree(
        tmp_path,
        _result("constructor", "mcp__docs__search", "early", details=_NESTED),
        AgentAssistantMessage(
            content=ProductContent(""),
            tool_calls=(_call("constructor", "mcp__docs__search", "{}"),),
        ),
        _result("constructor", "mcp__docs__search", "hit", details=_NESTED),
    )
    page = _page(tmp_path, export_native_session_to_html(tree, tmp_path / "e.html"))
    results = [
        entry
        for entry in page["entries"]["children"]
        if entry["children"] and entry["children"][0]["text"] == "Tool result"
    ]
    assert [entry["children"][1]["text"] for entry in results] == ["early"]
    assert len(_find(results[0], "nested-calls")) == 1
    (box,) = _find(page["entries"], "tool-rendered")
    assert len(_find(box, "nested-calls")) == 1
