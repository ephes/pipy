"""Unit tests for the transcript component (history + live stream owner).

These drive ``TranscriptComponent`` directly against counting effect callables
(no terminal shell, no PTY) to prove commit/stream transitions, the Ctrl+T
thinking-fold defer/reveal contract, the Ctrl+O expanded-flag rerender
bundling, custom-entry branch replacement, and the no-repaint verbs used by
enclosing chrome transitions. Full-frame behavior stays covered by the
existing TUI and real-PTY suites.
"""

from __future__ import annotations

from pipy_harness.extensions import lines_component
from pipy_harness.native.extensions.contracts import (
    RegisteredMessageRenderer,
)
from pipy_harness.native.ui.components.transcript import (
    DEFAULT_HIDDEN_THINKING_LABEL,
    TranscriptComponent,
)
from pipy_harness.native.ui.paint_lock import PaintLock


class _Harness:
    """A component wired to counting effect callables."""

    def __init__(self) -> None:
        self.repaints = 0
        self.resets = 0
        self.component = TranscriptComponent(
            PaintLock(),
            self._repaint,
            reset_scrollback=self._reset_scrollback,
            frame_width=lambda: 80,
            render_theme=lambda: None,
        )

    def _repaint(self) -> None:
        self.repaints += 1

    def _reset_scrollback(self) -> None:
        self.resets += 1


def _kinds(component: TranscriptComponent) -> list[str]:
    return [kind for kind, _lines in component.history_blocks]


def test_submit_user_message_commits_block_and_clears_live_buffers() -> None:
    harness = _Harness()
    t = harness.component
    t.append_assistant("stale")
    t.set_working("working")
    t.submit_user_message("hello\nworld")
    assert t.history_blocks[-1] == ("user", ("hello", "world"))
    assert t.assistant_text == ""
    assert t.working_text == ""


def test_settle_assistant_commits_buffer_and_falls_back_to_final_text() -> None:
    harness = _Harness()
    t = harness.component
    t.append_assistant("chunk-a ")
    t.append_assistant("chunk-b")
    t.settle_assistant()
    assert t.history_blocks[-1] == ("assistant", ("chunk-a chunk-b",))
    assert t.assistant_text == ""
    # An unstreamed completion settles through the final-text fallback.
    t.settle_assistant("full answer")
    assert t.history_blocks[-1] == ("assistant", ("full answer",))


def test_show_operation_aborted_commits_partial_then_error() -> None:
    harness = _Harness()
    t = harness.component
    t.append_assistant("partial")
    t.show_operation_aborted()
    assert _kinds(t)[-2:] == ["assistant", "error"]
    assert t.history_blocks[-1] == ("error", ("Operation aborted",))


def test_reasoning_settles_into_history_when_visible() -> None:
    harness = _Harness()
    t = harness.component
    t.append_reasoning("first **bold** thought")
    assert t.reasoning_text == "first bold thought"
    t.settle_reasoning()
    assert t.history_blocks[-1] == ("reasoning", ("first bold thought",))
    assert t.reasoning_text == ""


def test_folded_reasoning_defers_and_unfolding_reveals() -> None:
    harness = _Harness()
    t = harness.component
    t.set_thinking_hidden(True)
    t.append_reasoning("DEFER-ME")
    t.settle_reasoning()
    assert t.deferred_reasoning == ["DEFER-ME"]
    assert "reasoning" not in _kinds(t)
    repaints_before = harness.repaints
    t.set_thinking_hidden(False)
    assert t.deferred_reasoning == []
    assert t.history_blocks[-1] == ("reasoning", ("DEFER-ME",))
    assert harness.repaints == repaints_before + 1


def test_set_thinking_hidden_without_deferred_reasoning_never_repaints() -> None:
    harness = _Harness()
    harness.component.set_thinking_hidden(True)
    harness.component.set_thinking_hidden(False)
    assert harness.repaints == 0


def test_hidden_thinking_label_set_and_default_reset() -> None:
    harness = _Harness()
    t = harness.component
    t.set_hidden_thinking_label("Still thinking")
    assert t.hidden_thinking_label == "Still thinking"
    t.set_hidden_thinking_label()
    assert t.hidden_thinking_label == DEFAULT_HIDDEN_THINKING_LABEL


def test_no_repaint_verbs_for_enclosing_chrome_transitions() -> None:
    harness = _Harness()
    t = harness.component
    t.set_working("busy")
    t.set_hidden_thinking_label("Custom")
    repaints = harness.repaints
    t.discard_working_text()
    t.reset_hidden_thinking_label()
    assert t.working_text == ""
    assert t.hidden_thinking_label == DEFAULT_HIDDEN_THINKING_LABEL
    assert harness.repaints == repaints


def test_clear_working_repaints_only_when_something_cleared() -> None:
    harness = _Harness()
    harness.component.clear_working()
    assert harness.repaints == 0
    harness.component.set_working("busy")
    harness.component.clear_working()
    assert harness.repaints == 2


def test_add_tool_call_compacts_read_headers() -> None:
    harness = _Harness()
    t = harness.component
    t.add_tool_call("read src/x.py:1-40 (ctrl+o to expand)")
    assert t.history_blocks[-1] == ("tool_read", ("read src/x.py",))
    t.add_tool_call("bash: pytest -q")
    assert t.history_blocks[-1] == ("tool", ("bash: pytest -q",))


def test_append_tool_output_keeps_bounded_live_tail() -> None:
    harness = _Harness()
    t = harness.component
    t.set_working("busy")
    t.append_tool_output("x" * 9000)
    assert t.working_text == ""
    assert len(t.tool_output_text) == 8 * 1024
    t.append_tool_output("")
    assert len(t.tool_output_text) == 8 * 1024


def test_add_tool_result_marks_errors_and_duration() -> None:
    harness = _Harness()
    t = harness.component
    t.add_tool_result(lines=["out"], is_error=True, duration_seconds=1.25)
    assert t.history_blocks[-1] == (
        "tool_result",
        ("out", "[error] tool reported a failure", "", "Took 1.2s"),
    )
    assert t.tool_output_text == ""


def test_add_custom_entry_sanitizes_and_labels() -> None:
    harness = _Harness()
    t = harness.component
    t.add_custom_entry("note\x1b[31m", ["body\x1b[0m"])
    kind, lines = t.history_blocks[-1]
    assert kind == "custom"
    assert "\x1b" not in "".join(lines)
    assert lines[0].startswith("[note")


def test_seed_history_applies_once_and_keeps_existing_transcript() -> None:
    harness = _Harness()
    t = harness.component
    t.seed_history([("title", ("pipy",))])
    t.seed_history([("title", ("other",))])
    assert t.history_blocks == [("title", ("pipy",))]
    assert harness.repaints == 0


def _card_renderers() -> dict[str, RegisteredMessageRenderer]:
    def render(data: dict[str, object], ctx: object) -> object:
        return lines_component([f"expanded={getattr(ctx, 'expanded', None)}:BODY"])

    return {"card": RegisteredMessageRenderer("card", render, "ext")}


def test_set_tools_expanded_bundles_rerender_and_resets_scrollback() -> None:
    harness = _Harness()
    t = harness.component
    t.add_custom_entry_styled(
        ("expanded=False:BODY",),
        custom_type="card",
        data={},
        renderers=_card_renderers(),
    )
    t.set_tools_expanded(True)
    assert t.tools_expanded is True
    assert t.custom_entry_blocks() == (
        ("custom_message_custom", ("expanded=True:BODY",)),
    )
    # The retained-row refresh replaced committed rows: full redraw, not paint.
    assert harness.resets == 1


def test_set_tools_expanded_without_rich_rows_repaints_in_place() -> None:
    harness = _Harness()
    t = harness.component
    t.add_custom_entry("note", ["plain"])
    repaints = harness.repaints
    t.set_tools_expanded(True)
    assert harness.resets == 0
    assert harness.repaints == repaints + 1


def test_redraw_custom_entries_replaces_previous_branch_in_place() -> None:
    harness = _Harness()
    t = harness.component
    t.add_notice("keep-me")
    t.add_custom_entry("note", ["old-branch"])
    t.redraw_custom_entries([("plain", "note", ("new-branch",))])
    assert t.custom_entry_blocks() == (("custom", ("[note]", "new-branch")),)
    # The notice stays committed ahead of the replaced custom rows.
    assert _kinds(t)[0] == "notice"
    assert harness.resets == 1
