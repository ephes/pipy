"""The transcript redraw of the active session branch (DF1-F2).

Pi ``renderInitialMessages`` renders ``buildContextEntries()`` at startup and
after a session switch, fork or tree navigation. These tests pin pipy's
equivalent: the render entries, the rows each entry produces (the same rows a
live turn commits), the collapsible summary and tool-result rows, and how the
live transcript swaps its conversation.
"""

from __future__ import annotations

import io
import json
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

from pipy_harness.extensions import lines_component
from pipy_harness.native.agent import (
    AgentAssistantMessage,
    AgentCancellationReason,
    AgentStopReason,
    AgentToolCall,
    AgentToolResultMessage,
    AgentUserMessage,
    ProductContent,
)
from pipy_harness.native.coding import CodingInputQueue
from pipy_harness.native.coding.effects import CodingEffectCoordinator
from pipy_harness.native.extension_chrome_state import ExtensionChromeState
from pipy_harness.native.extensions.contracts import (
    RegisteredEntryRenderer,
    RegisteredMessageRenderer,
)
from pipy_harness.native.local_shell_record import (
    format_local_shell_record,
    parse_local_shell_record,
)
from pipy_harness.native.session_tree import (
    CompactionEntry,
    NativeSessionTree,
    build_context_entries,
)
from pipy_harness.native.ui.components.custom_entry_renderer import (
    CustomEntryRenderer,
    CustomEntryTerminalTarget,
)
from pipy_harness.native.ui.components.session_history import SessionHistoryRenderer
from pipy_harness.native.ui.components.tool_loop_renderer import TuiToolLoopRenderer
from pipy_harness.native.ui.components.transcript import TranscriptComponent
from pipy_harness.native.ui.paint_lock import PaintLock
from pipy_harness.native.ui.screen import ScreenRenderInputs


def _user(text: str) -> AgentUserMessage:
    return AgentUserMessage(content=ProductContent(text))


def _call(corr: str, tool: str, args: dict[str, Any]) -> AgentToolCall:
    return AgentToolCall(corr, tool, ProductContent(json.dumps(args)))


def _assistant(text: str, *calls: AgentToolCall) -> AgentAssistantMessage:
    return AgentAssistantMessage(content=ProductContent(text), tool_calls=calls)


def _result(
    corr: str, tool: str, text: str, *, is_error: bool = False
) -> AgentToolResultMessage:
    return AgentToolResultMessage(
        tool_request_id=f"pipy-tool-{corr}",
        tool_name=tool,
        content=ProductContent(text),
        provider_correlation_id=corr,
        is_error=is_error,
    )


class _Terminal:
    """A live transcript plus the renderers the terminal session wires to it."""

    def __init__(self, tmp_path: Path) -> None:
        self.repaints = 0
        self.resets = 0
        self.scrollback_clears = 0
        self.inputs = ScreenRenderInputs(
            lambda: 80, io.StringIO(), lambda: self.transcript.tools_expanded
        )
        self.paint_lock = PaintLock(threading.RLock())
        self.transcript = TranscriptComponent(
            self.paint_lock,
            self._repaint,
            reset_scrollback=self._reset,
            render_inputs=self.inputs,
            replace_scrollback=self._clear,
        )
        coordinator = CodingEffectCoordinator()
        self.tree = NativeSessionTree.create(tmp_path, persist=False)
        self.tree.bind_mutation_lock(coordinator.lock)
        self.tool_renderer = TuiToolLoopRenderer(
            transcript=self.transcript,
            chrome=ExtensionChromeState(),
            render_inputs=self.inputs,
        )
        self.entry_renderers: dict[str, RegisteredEntryRenderer] = {}
        self.message_renderers: dict[str, RegisteredMessageRenderer] = {}
        projection = SimpleNamespace(
            renderers=SimpleNamespace(
                messages=self.message_renderers, entries=self.entry_renderers
            )
        )
        self.custom = CustomEntryRenderer(
            ctl=SimpleNamespace(
                session_tree=self.tree,
                extension_message_outbox=[],
                extension_custom_message_outbox=[],
                extension_in_agent_turn=False,
            ),
            terminal=CustomEntryTerminalTarget(
                transcript=self.transcript, render_inputs=self.inputs
            ),
            coding_input_queue=CodingInputQueue(mutation_lock=coordinator.lock),
            coding_effects=coordinator,
            error_stream=io.StringIO(),
            generation_snapshot=lambda: cast(
                Any,
                SimpleNamespace(generation=SimpleNamespace(projection=projection)),
            ),
        )
        self.history = SessionHistoryRenderer(
            session_tree=lambda: self.tree,
            transcript=self.transcript,
            paint_lock=self.paint_lock,
            render_inputs=self.inputs,
            tool_renderer=self.tool_renderer,
            custom_renderer=self.custom,
        )

    def _repaint(self) -> None:
        self.repaints += 1

    def _reset(self) -> None:
        self.resets += 1

    def _clear(self) -> None:
        self.scrollback_clears += 1

    def rows(self) -> list[tuple[str, tuple[str, ...]]]:
        return [(kind, lines) for kind, lines in self.transcript.history_blocks]


def _live_rows(tmp_path: Path, drive: Any) -> list[tuple[str, tuple[str, ...]]]:
    terminal = _Terminal(tmp_path)
    drive(terminal.tool_renderer)
    return terminal.rows()


# -- render entries (Pi buildContextEntries) ---------------------------------


def test_context_entries_without_compaction_are_the_active_path(
    tmp_path: Path,
) -> None:
    tree = NativeSessionTree.create(tmp_path, persist=False)
    tree.append_model_change("fake", "fake-tools")
    first = tree.append_message(_user("one"))
    tree.append_message(_assistant("reply one"))
    abandoned = tree.append_message(_user("abandoned"))
    tree.branch(first.id)
    tree.append_message(_assistant("reply on branch"))

    entries = tree.build_context_entries()

    assert [entry.id for entry in entries] == [e.id for e in tree.get_branch()]
    assert abandoned.id not in {entry.id for entry in entries}


def test_context_entries_put_the_latest_compaction_first(tmp_path: Path) -> None:
    tree = NativeSessionTree.create(tmp_path, persist=False)
    tree.append_message(_user("old"))
    tree.append_message(_assistant("old reply"))
    kept = tree.append_message(_user("kept"))
    tree.append_message(_assistant("kept reply"))
    compaction = tree.append_compaction(
        summary="summary text", first_kept_entry_id=kept.id, tokens_before=1234
    )
    after = tree.append_message(_user("after"))

    entries = build_context_entries(tree.get_entries(), tree.get_leaf_id())

    assert entries[0] == compaction
    branch_ids = [e.id for e in tree.get_branch()]
    kept_index = branch_ids.index(kept.id)
    # From the first kept entry up to the compaction, then everything after.
    assert [e.id for e in entries[1:]] == [
        *branch_ids[kept_index : branch_ids.index(compaction.id)],
        after.id,
    ]


def test_context_entries_follow_an_anchored_compaction_cut(tmp_path: Path) -> None:
    tree = NativeSessionTree.create(tmp_path, persist=False)
    anchor = tree.append_message(_user("task"))
    tree.append_message(_assistant("dropped", _call("c1", "read", {"path": "a"})))
    tree.append_message(_result("c1", "read", "old"))
    suffix = tree.append_message(_assistant("kept", _call("c2", "read", {"path": "b"})))
    kept_result = tree.append_message(_result("c2", "read", "new"))
    compaction = tree.append_compaction(
        summary="s",
        first_kept_entry_id=suffix.id,
        tokens_before=10,
        retained_user_entry_id=anchor.id,
    )

    entries = tree.build_context_entries()

    assert entries[0] == compaction
    assert [e.id for e in entries[1:]] == [anchor.id, suffix.id, kept_result.id]
    # The coding projection keeps exactly the same retained entries.
    assert tree.build_coding_context().entry_ids == (
        anchor.id,
        suffix.id,
        kept_result.id,
    )


# -- rows ---------------------------------------------------------------------


def test_restored_turn_rows_equal_the_rows_a_live_turn_commits(
    tmp_path: Path,
) -> None:
    bash = _call("c1", "bash", {"command": "ls", "timeout": 5})
    read = _call("c2", "read", {"path": "a.txt"})
    failing = _call("c3", "bash", {"command": "false"})
    output = "\n".join(f"line {n}" for n in range(9))

    def drive(renderer: TuiToolLoopRenderer) -> None:
        renderer.render_user_message("list files")
        renderer.render_buffered_assistant_text("Looking.", has_tool_calls=True)
        renderer.complete_assistant_message(has_tool_calls=True)
        renderer.render_tool_call(bash)
        renderer.render_tool_result(output_text=output, is_error=False)
        renderer.render_tool_call(read)
        renderer.render_tool_result(output_text="file body", is_error=False)
        renderer.render_tool_call(failing)
        renderer.render_tool_result(output_text="boom", is_error=True)
        renderer.render_buffered_assistant_text("Done.", has_tool_calls=False)
        renderer.complete_assistant_message(has_tool_calls=False)

    live = _live_rows(tmp_path / "live", drive)

    terminal = _Terminal(tmp_path / "restored")
    terminal.tree.append_message(_user("list files"))
    terminal.tree.append_message(_assistant("Looking.", bash, read, failing))
    terminal.tree.append_message(_result("c1", "bash", output))
    terminal.tree.append_message(_result("c2", "read", "file body"))
    terminal.tree.append_message(_result("c3", "bash", "boom", is_error=True))
    terminal.tree.append_message(_assistant("Done."))
    terminal.history.render_active_branch()

    assert terminal.rows() == live
    kinds = [kind for kind, _ in live]
    # A successful read shows only its call row, like the live turn.
    assert kinds == [
        "user",
        "assistant",
        "tool",
        "tool_result",
        "tool_read",
        "tool",
        "tool_result",
        "assistant",
    ]
    assert live[3][1][0] == "... (4 earlier lines, ctrl+o to expand)"
    assert live[6][1][-1] == "[error] tool reported a failure"


def test_a_call_without_a_result_renders_only_its_call_row(tmp_path: Path) -> None:
    terminal = _Terminal(tmp_path)
    terminal.tree.append_message(_user("go"))
    terminal.tree.append_message(
        _assistant("", _call("c1", "bash", {"command": "sleep 9"}))
    )
    terminal.tree.append_message(_user("again"))

    terminal.history.render_active_branch()

    assert terminal.rows() == [
        ("user", ("go",)),
        ("tool", ("sleep 9",)),
        ("user", ("again",)),
    ]


def test_a_result_after_an_intervening_entry_still_completes_its_call(
    tmp_path: Path,
) -> None:
    # Pi keeps a call pending for the whole render (renderSessionItems).
    terminal = _Terminal(tmp_path)
    terminal.tree.append_message(_user("go"))
    terminal.tree.append_message(_assistant("", _call("c1", "bash", {"command": "ls"})))
    terminal.tree.append_custom_message("plain", "in between")
    terminal.tree.append_message(_result("c1", "bash", "a.py"))

    terminal.history.render_active_branch()

    assert terminal.rows() == [
        ("user", ("go",)),
        ("tool", ("ls",)),
        ("tool_result", ("a.py",)),
        ("custom", ("[plain]", "in between")),
    ]


def test_a_shell_record_renders_as_the_shell_rows(tmp_path: Path) -> None:
    terminal = _Terminal(tmp_path)
    record = format_local_shell_record("ls tests", "exit code: 0", "a.py\nb.py")
    terminal.tree.append_message(_user(record))
    terminal.tree.append_message(
        _user(format_local_shell_record("false", "exit code: 1", "(no output)"))
    )

    terminal.history.render_active_branch()

    assert terminal.rows() == [
        ("tool", ("ls tests",)),
        ("tool_result", ("exit code: 0", "a.py", "b.py")),
        ("tool", ("false",)),
        (
            "tool_result",
            ("exit code: 1", "(no output)", "[error] tool reported a failure"),
        ),
    ]


def test_shell_record_parsing_is_exact() -> None:
    text = format_local_shell_record("echo hi", "(cancelled by escape)", "")
    parsed = parse_local_shell_record(text)
    assert parsed is not None
    assert (parsed.command, parsed.status_line, parsed.output) == (
        "echo hi",
        "(cancelled by escape)",
        "",
    )
    assert not parsed.is_error
    timed_out = parse_local_shell_record(
        format_local_shell_record("sleep", "(timed out)", "x")
    )
    assert timed_out is not None and timed_out.is_error
    assert parse_local_shell_record("I ran a shell command, honestly") is None
    assert parse_local_shell_record("plain user text") is None


def test_summary_rows_are_collapsed_and_toggle_with_ctrl_o(tmp_path: Path) -> None:
    terminal = _Terminal(tmp_path)
    kept = terminal.tree.append_message(_user("kept"))
    terminal.tree.append_compaction(
        summary="## Goal\nfix it", first_kept_entry_id=kept.id, tokens_before=12345
    )
    first = terminal.tree.append_message(_assistant("after"))
    terminal.tree.branch_with_summary(first.id, "tried another way")

    terminal.history.render_active_branch()

    rows = terminal.rows()
    assert rows[0] == (
        "custom",
        ("[compaction]", "", "Compacted from 12,345 tokens (ctrl+o to expand)"),
    )
    assert rows[-1] == (
        "custom",
        ("[branch]", "", "Branch summary (ctrl+o to expand)"),
    )

    terminal.transcript.set_tools_expanded(True)

    rows = terminal.rows()
    assert rows[0] == (
        "custom",
        ("[compaction]", "", "Compacted from 12,345 tokens", "", "## Goal", "fix it"),
    )
    assert rows[-1] == (
        "custom",
        ("[branch]", "", "Branch Summary", "", "tried another way"),
    )
    assert terminal.resets == 1


def test_rows_render_expanded_when_ctrl_o_is_on(tmp_path: Path) -> None:
    terminal = _Terminal(tmp_path)
    terminal.transcript.set_tools_expanded(True)
    output = "\n".join(str(n) for n in range(8))
    terminal.tree.append_message(_user("go"))
    terminal.tree.append_message(
        _assistant("", _call("c1", "bash", {"command": "seq"}))
    )
    terminal.tree.append_message(_result("c1", "bash", output))

    terminal.history.render_active_branch()

    assert terminal.rows()[-1] == ("tool_result", tuple(output.splitlines()))


def test_live_and_restored_tool_results_toggle_with_ctrl_o(tmp_path: Path) -> None:
    terminal = _Terminal(tmp_path)
    call = _call("c1", "bash", {"command": "seq 8"})
    output = "\n".join(str(n) for n in range(8))
    terminal.tool_renderer.render_tool_call(call)
    terminal.tool_renderer.render_tool_result(
        output_text=output, is_error=False, duration_seconds=1.25
    )
    collapsed = (
        "... (3 earlier lines, ctrl+o to expand)",
        "3",
        "4",
        "5",
        "6",
        "7",
        "",
        "Took 1.2s",
    )
    assert terminal.rows()[-1] == ("tool_result", collapsed)

    terminal.transcript.set_tools_expanded(True)
    assert terminal.rows()[-1] == (
        "tool_result",
        (*output.splitlines(), "", "Took 1.2s"),
    )
    terminal.transcript.set_tools_expanded(False)
    assert terminal.rows()[-1] == ("tool_result", collapsed)
    assert terminal.resets == 2


def test_extension_entries_render_in_branch_order(tmp_path: Path) -> None:
    terminal = _Terminal(tmp_path)
    terminal.entry_renderers["card"] = RegisteredEntryRenderer(
        "card", lambda entry, ctx: lines_component([f"card:{entry['data']}"]), "ext"
    )
    terminal.message_renderers["note"] = RegisteredMessageRenderer(
        "note", lambda data, ctx: lines_component([f"note:{data['content']}"]), "ext"
    )
    terminal.tree.append_message(_user("hi"))
    terminal.tree.append_custom("card", 7)
    terminal.tree.append_custom_message("note", "shown")
    terminal.tree.append_custom_message("note", "hidden", display=False)
    terminal.tree.append_custom_message("plain", "no renderer")

    terminal.history.render_active_branch()

    assert terminal.rows() == [
        ("user", ("hi",)),
        ("custom_message_custom", ("card:7",)),
        ("custom_message_custom", ("note:shown",)),
        ("custom", ("[plain]", "no renderer")),
    ]


# -- replacing the conversation ------------------------------------------------


def test_startup_render_appends_after_the_startup_rows(tmp_path: Path) -> None:
    terminal = _Terminal(tmp_path)
    terminal.transcript.seed_history([("notice", ("header",))])
    terminal.tree.append_message(_user("hello"))

    terminal.history.render_active_branch()

    assert terminal.rows() == [("notice", ("header",)), ("user", ("hello",))]
    # Nothing was on screen after the startup rows: a plain paint, no clear.
    assert terminal.scrollback_clears == 0
    assert terminal.repaints >= 1


def test_replacement_keeps_startup_rows_and_clears_the_rest(tmp_path: Path) -> None:
    terminal = _Terminal(tmp_path)
    terminal.transcript.seed_history([("notice", ("header",))])
    terminal.transcript.add_notice("changelog line")
    terminal.transcript.submit_user_message("old conversation")
    terminal.transcript.append_assistant("streaming")
    terminal.transcript.append_reasoning("thinking")
    terminal.tree.append_message(_user("new"))
    terminal.tree.append_message(_assistant("answer"))

    terminal.history.render_active_branch()

    assert terminal.rows() == [
        ("notice", ("header",)),
        ("user", ("new",)),
        ("assistant", ("answer",)),
    ]
    assert terminal.scrollback_clears == 1
    assert terminal.transcript.assistant_text == ""
    assert terminal.transcript.reasoning_text == ""


def test_an_empty_branch_clears_the_conversation(tmp_path: Path) -> None:
    terminal = _Terminal(tmp_path)
    terminal.transcript.seed_history([("notice", ("header",))])
    terminal.transcript.submit_user_message("old")

    terminal.history.render_active_branch()

    assert terminal.rows() == [("notice", ("header",))]
    assert terminal.scrollback_clears == 1


def test_compaction_entry_type_is_rendered_not_projected(tmp_path: Path) -> None:
    tree = NativeSessionTree.create(tmp_path, persist=False)
    kept = tree.append_message(_user("kept"))
    tree.append_compaction(summary="s", first_kept_entry_id=kept.id, tokens_before=1)
    assert isinstance(tree.build_context_entries()[0], CompactionEntry)
    # The provider projection still carries the summary separately.
    assert tree.build_coding_context().prior_summary == "s"


# -- aborted and failed turns (DF1-F6) ----------------------------------------


def test_restored_aborted_turn_matches_the_live_abort_rows(tmp_path: Path) -> None:
    """Pi draws the partial text, then ``Operation aborted`` (error color)."""

    def drive(renderer: TuiToolLoopRenderer) -> None:
        renderer.render_user_message("write a poem")
        renderer.start_assistant_message()
        renderer.stream_sink("Roses are")
        renderer.cancel_assistant_message(AgentCancellationReason.OPERATOR_ABORT)

    live = _live_rows(tmp_path / "live", drive)

    terminal = _Terminal(tmp_path / "restored")
    terminal.tree.append_message(_user("write a poem"))
    terminal.tree.append_message(
        AgentAssistantMessage(
            ProductContent("Roses are"), stop_reason=AgentStopReason.ABORTED
        )
    )
    terminal.history.render_active_branch()

    assert terminal.rows() == live
    assert live == [
        ("user", ("write a poem",)),
        ("assistant", ("Roses are",)),
        ("error", ("Operation aborted",)),
    ]


def test_restored_stopped_turn_markers_follow_pi(tmp_path: Path) -> None:
    terminal = _Terminal(tmp_path)
    terminal.tree.append_message(_user("one"))
    terminal.tree.append_message(
        AgentAssistantMessage(ProductContent(""), stop_reason=AgentStopReason.ABORTED)
    )
    terminal.tree.append_message(_user("two"))
    terminal.tree.append_message(
        AgentAssistantMessage(
            ProductContent("half"),
            stop_reason=AgentStopReason.ERROR,
            error_message="rate limited",
        )
    )
    terminal.tree.append_message(_user("three"))
    terminal.tree.append_message(
        AgentAssistantMessage(ProductContent(""), stop_reason=AgentStopReason.ERROR)
    )
    terminal.tree.append_message(_user("four"))
    terminal.tree.append_message(
        AgentAssistantMessage(
            ProductContent(""),
            stop_reason=AgentStopReason.ABORTED,
            error_message="Request was aborted",
        )
    )
    terminal.history.render_active_branch()

    assert terminal.rows() == [
        ("user", ("one",)),
        ("error", ("Operation aborted",)),
        ("user", ("two",)),
        ("assistant", ("half",)),
        ("error", ("Error: rate limited",)),
        ("user", ("three",)),
        ("error", ("Error: Unknown error",)),
        ("user", ("four",)),
        ("error", ("Operation aborted",)),
    ]
