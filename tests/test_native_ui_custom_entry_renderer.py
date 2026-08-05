"""Unit tests for the custom-entry renderer component's terminal wiring.

These drive ``CustomEntryRenderer`` directly against a real
``TranscriptComponent`` (no terminal shell, no PTY) to prove the injected
:class:`CustomEntryTerminalTarget` seam: rendered custom entries and custom
messages commit straight to the transcript with the injected width, stream,
and live ``tools_expanded`` flag; a ``None`` target is headless and degrades
displayable messages to a sanitized diagnostic on the error stream. Delivery
semantics (locks, generation snapshots, drain ordering) keep their coverage in
``test_native_coding_effects.py`` and
``test_native_session_extension_generation.py``; the real-PTY coverage lives
in ``test_native_extension_custom_ui_pty.py`` and
``test_native_extension_message_renderer_pty.py``.
"""

from __future__ import annotations

import io
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

from pipy_harness.extensions import lines_component
from pipy_harness.native.coding import CodingInputQueue
from pipy_harness.native.coding.effects import CodingEffectCoordinator
from pipy_harness.native.extension_runtime import (
    QueuedCustomMessage,
)
from pipy_harness.native.extensions.contracts import (
    RegisteredEntryRenderer,
    RegisteredMessageRenderer,
)
from pipy_harness.native.session_tree import NativeSessionTree
from pipy_harness.native.ui.components.custom_entry_renderer import (
    CustomEntryRenderer,
    CustomEntryTerminalTarget,
    CustomRendererProjectionSnapshot,
)
from pipy_harness.native.ui.components.transcript import TranscriptComponent
from pipy_harness.native.ui.paint_lock import PaintLock


class _Harness:
    """A renderer wired to a real transcript, counting shell effects."""

    def __init__(self, tmp_path: Path, *, attached: bool = True) -> None:
        self.repaints = 0
        self.scrollback_resets = 0
        self.widths: list[int] = []
        self.transcript = TranscriptComponent(
            PaintLock(),
            self._repaint,
            reset_scrollback=self._reset_scrollback,
            frame_width=lambda: 80,
            render_theme=lambda: None,
        )
        self.error_stream = io.StringIO()
        coordinator = CodingEffectCoordinator()
        self.tree = NativeSessionTree.create(tmp_path, persist=False)
        self.tree.bind_mutation_lock(coordinator.lock)
        self.input_queue = CodingInputQueue(mutation_lock=coordinator.lock)
        self.renderer = CustomEntryRenderer(
            ctl=SimpleNamespace(
                session_tree=self.tree,
                extension_message_outbox=[],
                extension_custom_message_outbox=[],
                extension_in_agent_turn=False,
            ),
            terminal=(
                CustomEntryTerminalTarget(
                    transcript=self.transcript,
                    terminal_stream=io.StringIO(),
                    frame_width=self._frame_width,
                )
                if attached
                else None
            ),
            coding_input_queue=self.input_queue,
            coding_effects=coordinator,
            error_stream=self.error_stream,
        )

    def _repaint(self) -> None:
        self.repaints += 1

    def _reset_scrollback(self) -> None:
        self.scrollback_resets += 1

    def _frame_width(self) -> int:
        self.widths.append(72)
        return 72


def _entry_projection(
    seen: list[dict[str, object]] | None = None,
) -> CustomRendererProjectionSnapshot:
    def render(entry: dict[str, object], ctx: object) -> object:
        if seen is not None:
            seen.append(entry)
        return lines_component([f"entry:{entry['data']}"])

    return CustomRendererProjectionSnapshot(
        {}, {"card": RegisteredEntryRenderer("card", render, "ext")}
    )


def _message_projection(label: str) -> CustomRendererProjectionSnapshot:
    def render(data: dict[str, object], ctx: object) -> object:
        return lines_component([f"{label}:{data['content']}"])

    return CustomRendererProjectionSnapshot(
        {"note": RegisteredMessageRenderer("note", render, "ext")}, {}
    )


def test_attached_entry_render_commits_to_transcript_with_injected_inputs(
    tmp_path: Path,
) -> None:
    harness = _Harness(tmp_path)
    entry = harness.tree.append_custom("card", {"n": 1})
    seen: list[dict[str, object]] = []

    harness.renderer.add_rendered_custom_entry_to_terminal(
        entry, _entry_projection(seen)
    )

    assert harness.transcript.custom_entry_blocks() == (
        ("custom_message_custom", ("entry:{'n': 1}",)),
    )
    # Width came from the injected callable, not a terminal driver.
    assert harness.widths == [72]
    assert harness.repaints == 1
    assert seen and seen[0]["customType"] == "card"


def test_headless_entry_render_is_a_no_op(tmp_path: Path) -> None:
    harness = _Harness(tmp_path, attached=False)
    entry = harness.tree.append_custom("card", {"n": 1})

    harness.renderer.add_rendered_custom_entry_to_terminal(entry, _entry_projection())

    assert harness.transcript.custom_entry_blocks() == ()
    assert harness.repaints == 0


def test_attached_message_delivery_commits_styled_row_and_routes_input(
    tmp_path: Path,
) -> None:
    harness = _Harness(tmp_path)

    appended = harness.renderer._deliver_custom_message(
        QueuedCustomMessage("note", "payload", True, None, {"triggerTurn": True}),
        _message_projection("styled"),
    )

    assert appended is not None
    assert harness.transcript.custom_entry_blocks() == (
        ("custom_message_custom", ("styled:payload",)),
    )
    # deliverAs routing still runs: triggerTurn outside a turn enqueues a prompt.
    assert harness.input_queue.take_next() is not None
    assert harness.error_stream.getvalue() == ""


def test_headless_display_message_degrades_to_sanitized_diagnostic(
    tmp_path: Path,
) -> None:
    harness = _Harness(tmp_path, attached=False)

    harness.renderer._deliver_custom_message(
        QueuedCustomMessage("note", "body\x1b[31m", True, None, {}),
        _message_projection("plain"),
    )

    printed = harness.error_stream.getvalue()
    assert printed.startswith("note:")
    assert "plain:body" in printed
    assert "\x1b" not in printed
    assert harness.transcript.custom_entry_blocks() == ()


def test_redraw_for_active_branch_replaces_transcript_rows(tmp_path: Path) -> None:
    harness = _Harness(tmp_path)
    harness.transcript.add_custom_entry("stale", ["OLD-BODY"])
    harness.tree.append_custom("card", {"n": 2})
    projection = _entry_projection()
    snapshot = SimpleNamespace(
        generation=SimpleNamespace(
            projection=SimpleNamespace(
                renderers=SimpleNamespace(
                    messages=projection.messages, entries=projection.entries
                )
            )
        )
    )
    renderer = replace(
        harness.renderer, generation_snapshot=cast(Any, lambda: snapshot)
    )

    renderer.redraw_custom_entries_for_active_branch()

    assert harness.transcript.custom_entry_blocks() == (
        ("custom_message_custom", ("entry:{'n': 2}",)),
    )
    # Wholesale row replacement goes through the shell's scrollback reset.
    assert harness.scrollback_resets == 1
