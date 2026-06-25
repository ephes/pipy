"""Tests for the `--mode json` / `--print` one-shot drivers and resolution.

`resolve_app_mode` mirrors Pi's ``resolveAppMode`` precedence
(`packages/coding-agent/src/main.ts`): ``--mode rpc`` > ``--mode json`` >
(``--print`` or non-TTY stdin) > interactive. The product ``pipy`` CLI overrides
the non-TTY stdin branch so bare piped input remains interactive unless
``--print``/``--mode`` is explicit. ``run_json_mode`` drives the real tool-loop
adapter for one prompt, emits the native session header line first, then the
full Pi-shaped event stream, then exits. ``run_print_mode`` prints only the
final assistant text.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from pipy_harness.adapters.native import PipyNativeToolReplAdapter
from pipy_harness.native.fake import FakeNativeProvider
from pipy_harness.native.automation.run_modes import (
    resolve_app_mode,
    run_json_mode,
    run_print_mode,
)
from pipy_harness.native.session_tree import NativeSessionTree


@pytest.mark.parametrize(
    ("mode", "print_flag", "stdin_is_tty", "expected"),
    [
        ("rpc", False, True, "rpc"),
        ("rpc", True, False, "rpc"),  # rpc wins over print/non-tty
        ("json", False, True, "json"),
        ("json", True, False, "json"),  # json wins over print/non-tty
        ("text", True, True, "print"),  # --print selects text one-shot
        (None, True, True, "print"),
        (None, False, False, "print"),  # Low-level Pi resolver; CLI overrides.
        (None, False, True, "interactive"),
        ("text", False, True, "interactive"),
    ],
)
def test_resolve_app_mode_matches_pi_precedence(
    mode: str | None, print_flag: bool, stdin_is_tty: bool, expected: str
) -> None:
    assert (
        resolve_app_mode(mode=mode, print_flag=print_flag, stdin_is_tty=stdin_is_tty)
        == expected
    )


def _tool_adapter() -> PipyNativeToolReplAdapter:
    provider = FakeNativeProvider(
        supports_tool_calls=True,
        programmable_text_chunks=("SEEN:", "ROOT"),
    )
    return PipyNativeToolReplAdapter(provider=provider)


def test_run_json_mode_emits_header_then_event_stream(tmp_path: Path) -> None:
    adapter = _tool_adapter()
    tree = NativeSessionTree.create(tmp_path, persist=False)
    stdout = io.BytesIO()

    exit_code = run_json_mode(
        adapter=adapter,
        prompt="ROOT",
        cwd=tmp_path,
        native_session=tree,
        stdout_buffer=stdout,
        error_stream=io.StringIO(),
    )

    assert exit_code == 0
    lines = stdout.getvalue().decode("utf-8").splitlines()
    records = [json.loads(line) for line in lines]

    # First line is the native session header.
    header = records[0]
    assert header["type"] == "session"
    assert header["id"] == tree.header.id
    assert header["cwd"] == tree.header.cwd
    assert header["version"] == tree.header.version

    types = [record["type"] for record in records[1:]]
    assert types == [
        "agent_start",
        "turn_start",
        "message_start",
        "message_end",
        "message_start",
        "message_update",
        "message_update",
        "message_end",
        "turn_end",
        "agent_end",
    ]

    # Full-content surface: the assistant text appears in its message_end.
    message_end = next(
        r
        for r in records
        if r["type"] == "message_end" and r["message"]["role"] == "assistant"
    )
    assert message_end["message"]["content"] == [{"type": "text", "text": "SEEN:ROOT"}]
    # Pi metadata-only schema is NOT emitted.
    assert all(r.get("schema") != "pipy.native_output" for r in records)


def test_run_json_mode_does_not_emit_raw_final_text_on_stdout(tmp_path: Path) -> None:
    adapter = _tool_adapter()
    tree = NativeSessionTree.create(tmp_path, persist=False)
    stdout = io.BytesIO()

    run_json_mode(
        adapter=adapter,
        prompt="ROOT",
        cwd=tmp_path,
        native_session=tree,
        stdout_buffer=stdout,
        error_stream=io.StringIO(),
    )

    # Every stdout line must be a JSON record (no stray plain-text final answer).
    for line in stdout.getvalue().decode("utf-8").splitlines():
        json.loads(line)


def test_json_mode_multiline_prompt_is_a_single_turn(tmp_path: Path) -> None:
    adapter = _tool_adapter()
    tree = NativeSessionTree.create(tmp_path, persist=False)
    stdout = io.BytesIO()

    run_json_mode(
        adapter=adapter,
        prompt="line one\nline two\nline three",
        cwd=tmp_path,
        native_session=tree,
        stdout_buffer=stdout,
        error_stream=io.StringIO(),
    )

    records = [json.loads(line) for line in stdout.getvalue().decode("utf-8").splitlines()]
    types = [r["type"] for r in records]
    # A multiline prompt is one non-interactive turn, not three.
    assert types.count("agent_start") == 1
    assert types.count("agent_end") == 1
    user_start = next(
        r
        for r in records
        if r["type"] == "message_start" and r["message"]["role"] == "user"
    )
    assert user_start["message"]["content"] == [
        {"type": "text", "text": "line one\nline two\nline three"}
    ]


def test_run_print_mode_emits_only_final_assistant_text(tmp_path: Path) -> None:
    adapter = _tool_adapter()
    stdout = io.StringIO()

    exit_code = run_print_mode(
        adapter=adapter,
        prompt="ROOT",
        cwd=tmp_path,
        stdout=stdout,
        error_stream=io.StringIO(),
    )

    assert exit_code == 0
    assert stdout.getvalue().strip() == "SEEN:ROOT"
