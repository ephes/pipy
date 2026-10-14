"""What a codemode script receives from a tool call (MCP parity plan S04).

Rows CMD-03, CMD-04, CMD-25 and the script half of EXT-02, with extension
tools and the real CPython-on-WASI guest: a tool that declares an output
schema resolves to its structured content, also for an error result that
carries one; any other tool resolves to its text; a failed, blocked or invalid
call raises ``ToolError``; a ``tool_result`` hook that replaces the content
decides the structured content; and a 5 MB structured result reaches the
script intact.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from test_native_codemode_delivery import (
    runtime,  # noqa: F401 - the real-runtime fixture
)
from test_native_codemode_extension_tools import _output, _run_script

_TOOLS = """from pipy_harness.extensions import (
    ExtensionTool, ToolBlock, ToolResult, ToolResultTransform,
)

_KEY = {'type': 'object', 'properties': {'key': {'type': 'string'}},
        'additionalProperties': False}
_STATS = {'type': 'object', 'properties': {'files': {'type': 'number'}}}


def big_value():
    return {'rows': [{'id': n, 'name': 'row-%d-ü' % n, 'pad': 'p' * 100}
                     for n in range(40000)]}


def stats(ctx, params):
    if params.get('key') == 'missing':
        # A failure that still carries data (Pi `isError`).
        return ToolResult(content='no such key', is_error=True,
                          structured_content={'error': 'no such key'})
    if params.get('key') == 'broken':
        return ToolResult(content='stats is broken', is_error=True)
    if params.get('key') == 'text-only':
        return ToolResult(content='2 files, no structure')
    return ToolResult(content='2 files',
                      structured_content={'files': 2, 'names': ['a', 'b']})


def activate(api):
    def tool(name, handler, **metadata):
        api.register_tool(ExtensionTool(
            name=name, description=name, input_schema=_KEY, handler=handler,
            exposure='codemode', **metadata))

    tool('stats', stats, output_schema=_STATS)
    # Structured content without a declared output schema stays with the host.
    tool('plain', stats)
    tool('silent', lambda ctx, params: ToolResult(content='', is_error=True))
    # Null is structured content too (Pi: only `undefined` is none).
    tool('nothing', lambda ctx, params: ToolResult(
        content='no stats', structured_content=None,
        is_error=params.get('key') == 'broken'),
        output_schema={'type': 'null'})
    tool('audited', stats, output_schema=_STATS)
    tool('redacted', stats, output_schema=_STATS)
    tool('big', lambda ctx, params: ToolResult(
        content='40000 rows', structured_content=big_value()),
        output_schema={'type': 'object'})

    @api.on('tool_call')
    def gate(event, ctx):
        if event.tool_name == 'stats' and event.input.get('key') == 'forbidden':
            return ToolBlock(reason='stats of forbidden is blocked')

    @api.on('tool_result')
    def audit(event, ctx):
        if event.tool_name == 'audited':
            assert event.structured_content == {'files': 2, 'names': ['a', 'b']}
            return ToolResultTransform(
                content='0 files', structured_content={'files': 0, 'names': []})
        if event.tool_name == 'redacted':
            return ToolResultTransform(content='redacted')

    # A later handler that changes nothing keeps what the first one set.
    @api.on('tool_result')
    def observe(event, ctx):
        if event.tool_name == 'audited':
            assert event.structured_content == {'files': 0, 'names': []}
"""


def _write_tools(workspace: Path) -> None:
    directory = workspace / ".pipy" / "extensions"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "shapes.py").write_text(_TOOLS, encoding="utf-8")


def test_script_receives_structured_content_text_or_a_tool_error(
    tmp_path,
    monkeypatch,
    runtime,  # noqa: F811
) -> None:
    _write_tools(tmp_path)
    code = """def error(call):
    try:
        return {'returned': call()}
    except ToolError as failure:
        return {'raised': str(failure)}

structured = tools.stats(key='a')
text({
    'structured': structured,
    'type': type(structured).__name__,
    'error_with_structure': tools.stats(key='missing'),
    'declared_without_structure': tools.stats(key='text-only'),
    'plain': tools.plain(key='a'),
    'error_without_structure': error(lambda: tools.stats(key='broken')),
    'plain_error': error(lambda: tools.plain(key='missing')),
    'silent': error(lambda: tools.silent()),
    'blocked': error(lambda: tools.stats(key='forbidden')),
    'invalid': error(lambda: tools.stats(key=3)),
    'audited': tools.audited(key='a'),
    'redacted': tools.redacted(key='a'),
    'null': error(lambda: tools.nothing()),
    'null_error': error(lambda: tools.nothing(key='broken')),
})
"""

    parent, _, _ = _run_script(tmp_path, code, monkeypatch)

    assert not parent.is_error, parent.content.value
    output = json.loads(_output(parent))
    # CMD-03: the structured content, as a dict, also for an error result.
    assert output["structured"] == {"files": 2, "names": ["a", "b"]}
    assert output["type"] == "dict"
    assert output["error_with_structure"] == {"error": "no such key"}
    # CMD-04: every other result is its text; a failure raises ToolError.
    assert output["declared_without_structure"] == "2 files, no structure"
    assert output["plain"] == "2 files"
    assert output["error_without_structure"] == {"raised": "stats is broken"}
    assert output["plain_error"] == {"raised": "no such key"}
    assert output["silent"] == {"raised": 'Tool "silent" failed'}
    assert "stats of forbidden is blocked" in output["blocked"]["raised"]
    assert "key" in output["invalid"]["raised"]
    # CMD-25: the hook that replaced the content decided the structured
    # content; replacing the content alone dropped it.
    assert output["audited"] == {"files": 0, "names": []}
    assert output["redacted"] == "redacted"
    # M6: a null structured result is the value None, also for a failure.
    assert output["null"] == {"returned": None}
    assert output["null_error"] == {"returned": None}
    # The record follows the tool's outcome and keeps no result of any shape.
    record = parent.details["nestedCalls"]
    assert [(call["name"], call["status"]) for call in record["calls"]] == [
        ("stats", "ok"),
        ("stats", "error"),
        ("stats", "ok"),
        ("plain", "ok"),
        ("stats", "error"),
        ("plain", "error"),
        ("silent", "error"),
        ("stats", "error"),
        ("stats", "error"),
        ("audited", "ok"),
        ("redacted", "ok"),
        ("nothing", "ok"),
        ("nothing", "error"),
    ]
    assert record["calls"][1]["error"] == "no such key"
    assert record["calls"][6]["error"] == 'Tool "silent" failed'
    assert '"names"' not in json.dumps(record)


def test_a_five_megabyte_structured_result_reaches_the_script_intact(
    tmp_path,
    monkeypatch,
    runtime,  # noqa: F811
) -> None:
    # Plan 4.9: a tool reply is bounded by ``ScriptLimits.reply_bytes``
    # (16 MiB), not by the 1 MiB limit on the lines a guest sends.
    _write_tools(tmp_path)
    expected = {
        "rows": [
            {"id": n, "name": f"row-{n}-ü", "pad": "p" * 100} for n in range(40000)
        ]
    }
    canonical = json.dumps(expected, sort_keys=True, separators=(",", ":"))
    assert len(canonical.encode()) > 5_000_000
    code = """import hashlib, json
value = tools.big()
canonical = json.dumps(value, sort_keys=True, separators=(',', ':'))
text({'bytes': len(canonical.encode()), 'rows': len(value['rows']),
      'last': value['rows'][-1]['name'],
      'sha256': hashlib.sha256(canonical.encode()).hexdigest()})
"""

    parent, _, _ = _run_script(tmp_path, code, monkeypatch)

    assert not parent.is_error, parent.content.value
    assert json.loads(_output(parent)) == {
        "bytes": len(canonical.encode()),
        "rows": 40000,
        "last": "row-39999-ü",
        "sha256": hashlib.sha256(canonical.encode()).hexdigest(),
    }
    assert parent.details["nestedCalls"]["calls"][0]["status"] == "ok"
    # Nothing of the nested result is kept on the parent.
    assert len(json.dumps(parent.details)) < 2000


def test_a_script_gets_bashs_structured_result_also_for_a_failed_command(
    tmp_path,
    monkeypatch,
    runtime,  # noqa: F811
) -> None:
    # Goal-check finding M15, Pi docs/codemode.md "Call tools": `bash`
    # resolves to { output, truncated, full_output_path?, exit_code,
    # wall_time_seconds }, also for non-zero exit codes.
    code = """ok = tools.bash(command='echo hello')
failed = tools.bash(command='echo oops; exit 4')
text({'ok': ok, 'failed': failed, 'type': type(failed).__name__})
"""

    parent, _, _ = _run_script(tmp_path, code, monkeypatch)

    assert not parent.is_error, parent.content.value
    output = json.loads(_output(parent))
    assert output["type"] == "dict"
    for name, text, code_ in (("ok", "hello\n", 0), ("failed", "oops\n", 4)):
        value = output[name]
        assert value["output"] == text
        assert value["truncated"] is False
        assert value["exit_code"] == code_
        assert isinstance(value["wall_time_seconds"], float)
    # The record follows the tool's outcome: the failed command is an error.
    record = parent.details["nestedCalls"]
    assert [(call["name"], call["status"]) for call in record["calls"]] == [
        ("bash", "ok"),
        ("bash", "error"),
    ]
