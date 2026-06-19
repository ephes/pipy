"""Tests for the example `answer` extension (port of Pi's answer.ts).

Covers the pure pieces (JSON extraction parsing, the Q&A component's
navigation / compilation) and the full command-handler flow driven through
`dispatch_extension_command` with a fake completion backend + a fake custom-UI
driver (no real TTY). The live TUI rendering is verified separately under tmux.
"""

from __future__ import annotations

import importlib.util
import io
from pathlib import Path
from typing import TextIO, cast

from pipy_harness.native.extension_runtime import (
    activate_extensions,
    dispatch_extension_command,
    extension_command_map,
)
from pipy_harness.native.extensions import discover_extensions
from pipy_harness.native.tui import ToolLoopTerminalUi
from pipy_harness.native.tools.messages import AssistantMessage

_EXAMPLE = (
    Path(__file__).resolve().parents[1]
    / "docs"
    / "examples"
    / "extensions"
    / "answer.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location("answer_example", _EXAMPLE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


answer = _load_module()


# -- parsing ------------------------------------------------------------------


def test_parse_plain_json() -> None:
    result = answer.parse_extraction_result(
        '{"questions": [{"question": "DB?", "context": "mysql/pg"}]}'
    )
    assert result == [{"question": "DB?", "context": "mysql/pg"}]


def test_parse_fenced_json() -> None:
    text = "Sure!\n```json\n{\"questions\": [{\"question\": \"TS or JS?\"}]}\n```\n"
    assert answer.parse_extraction_result(text) == [{"question": "TS or JS?"}]


def test_parse_empty_questions() -> None:
    assert answer.parse_extraction_result('{"questions": []}') == []


def test_parse_malformed_returns_none() -> None:
    assert answer.parse_extraction_result("not json at all") is None
    assert answer.parse_extraction_result('{"nope": 1}') is None


# -- the Q&A component --------------------------------------------------------


def _drive(component, keys: list[str]) -> None:
    for key in keys:
        component.handle_input(key)


def test_component_renders_question_progress_and_footer() -> None:
    done: list = []
    comp = answer.QnAComponent(
        [{"question": "Which database?", "context": "only mysql/pg"}],
        done.append,
    )
    frame = "\n".join(comp.render(80))
    assert "Questions" in frame
    assert "Which database?" in frame
    assert "only mysql/pg" in frame
    assert "cancel" in frame  # footer controls


def test_component_tui_custom_overlay_preserves_safe_sgr(tmp_path: Path) -> None:
    done: list = []
    comp = answer.QnAComponent(
        [{"question": "Which database?", "context": "only mysql/pg"}],
        done.append,
    )
    ui = ToolLoopTerminalUi(
        input_stream=cast(TextIO, io.StringIO()),
        terminal_stream=cast(TextIO, io.StringIO()),
        cwd=tmp_path,
    )
    ui._custom_component = comp
    ui.custom_overlay_open = True

    frame = "\n".join(ui.render_lines(width=80, height=14))
    plain = answer._ANSI_RE.sub("", frame)

    assert "\x1b[2m" in frame
    assert "Questions" in plain
    assert "Which database?" in plain
    assert "[2m" not in plain
    assert "\r" not in frame


def test_component_navigation_and_submit_compiles_answers() -> None:
    captured: list = []
    comp = answer.QnAComponent(
        [
            {"question": "DB?", "context": "mysql/pg"},
            {"question": "TS or JS?"},
        ],
        captured.append,
    )
    # Type the first answer, Enter advances to Q2, type the second answer,
    # Enter on the last question asks to confirm, Enter confirms -> submit.
    _drive(comp, list("postgres") + ["enter"] + list("typescript") + ["enter", "enter"])
    assert captured, "submit never fired"
    compiled = captured[0]
    assert "Q: DB?" in compiled
    assert "> mysql/pg" in compiled
    assert "A: postgres" in compiled
    assert "Q: TS or JS?" in compiled
    assert "A: typescript" in compiled


def test_component_esc_cancels_with_none() -> None:
    captured: list = []
    comp = answer.QnAComponent([{"question": "DB?"}], captured.append)
    comp.handle_input("esc")
    assert captured == [None]


def test_component_unanswered_question_records_no_answer() -> None:
    captured: list = []
    comp = answer.QnAComponent([{"question": "DB?"}], captured.append)
    comp.handle_input("enter")  # last question -> confirm
    comp.handle_input("enter")  # confirm -> submit
    assert "A: (no answer)" in captured[0]


# -- full handler flow (no real TTY) ------------------------------------------


def _activate_answer(tmp_path):
    ext_dir = tmp_path / ".pipy" / "extensions"
    ext_dir.mkdir(parents=True)
    (ext_dir / "answer.py").write_text(
        _EXAMPLE.read_text(encoding="utf-8"), encoding="utf-8"
    )
    descriptors = discover_extensions(tmp_path, config_home_env={}, home_dir=tmp_path)
    outbox: list = []
    activated = activate_extensions(descriptors, message_outbox=outbox)
    return extension_command_map(activated), outbox


def test_answer_handler_extracts_and_submits(tmp_path) -> None:
    command_map, outbox = _activate_answer(tmp_path)

    completions: list = []

    def fake_complete(system_prompt: str, user_text: str) -> str:
        completions.append((system_prompt, user_text))
        return '{"questions": [{"question": "Which DB?", "context": "mysql/pg"}]}'

    def fake_driver(factory):
        captured: list = []
        component = factory(captured.append)
        # Answer the single question and submit (Enter -> confirm, Enter -> ok).
        for key in list("postgres") + ["enter", "enter"]:
            component.handle_input(key)
        return captured[0]

    dispatch = dispatch_extension_command(
        "/answer",
        command_map,
        cwd=str(tmp_path),
        has_ui=True,
        messages=[AssistantMessage(content="To proceed: Which DB? (mysql/pg)")],
        complete_fn=fake_complete,
        ui_custom_driver=fake_driver,
    )
    assert dispatch is not None and dispatch.ran
    # The extraction used the system prompt + last assistant text.
    assert completions and completions[0][0] == answer.SYSTEM_PROMPT
    assert "Which DB?" in completions[0][1]
    # The compiled answers were submitted as a new user message (a turn).
    assert len(outbox) == 1
    assert "I answered your questions" in outbox[0].content
    assert "A: postgres" in outbox[0].content


def test_answer_handler_requires_interactive_ui(tmp_path) -> None:
    command_map, outbox = _activate_answer(tmp_path)
    dispatch = dispatch_extension_command(
        "/answer",
        command_map,
        cwd=str(tmp_path),
        has_ui=False,
        messages=[AssistantMessage(content="anything?")],
    )
    assert dispatch is not None and dispatch.ran
    assert ("error", "answer requires interactive mode") in dispatch.messages
    assert outbox == []


def test_answer_handler_no_assistant_message(tmp_path) -> None:
    command_map, _ = _activate_answer(tmp_path)
    dispatch = dispatch_extension_command(
        "/answer", command_map, cwd=str(tmp_path), has_ui=True, messages=[]
    )
    assert dispatch is not None and dispatch.ran
    assert ("error", "No assistant messages found") in dispatch.messages


def test_answer_handler_no_questions_found(tmp_path) -> None:
    command_map, outbox = _activate_answer(tmp_path)
    dispatch = dispatch_extension_command(
        "/answer",
        command_map,
        cwd=str(tmp_path),
        has_ui=True,
        messages=[AssistantMessage(content="A complete answer with no questions.")],
        complete_fn=lambda _s, _u: '{"questions": []}',
    )
    assert dispatch is not None and dispatch.ran
    assert ("info", "No questions found in the last message") in dispatch.messages
    assert outbox == []
