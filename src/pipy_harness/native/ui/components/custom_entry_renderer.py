"""Custom-entry rendering and the extension outbox drain behind the transcript.

Same ownership contract as the sibling components: the renderer holds no
terminal state of its own. Rendered rows land on the
:class:`~pipy_harness.native.ui.components.transcript.TranscriptComponent`,
and the two live values a render needs beyond the transcript — the frame
width and the styling stream — arrive as an injected
:class:`CustomEntryTerminalTarget` built by the terminal shell. A ``None``
target is the headless mode: durable tree appends and message routing still
run, and displayable messages degrade to a sanitized diagnostic on the error
stream instead of a frame row.

Renderer operations take one published generation snapshot
(``generation_snapshot``) so a message never renders half under an old
extension generation and half under its ``/reload`` successor; the live-state
protocol (:class:`CustomEntryRunState`) retains only the session tree,
agent-turn state, and R4a's legacy/harness direct-drain outboxes.

Delivery ordering is owned elsewhere: ``coding_effects`` gates every accepted
effect behind the coding-session lifecycle, and the durable tree append runs
under ``coding_effects.lock`` before any rendering or input routing, exactly
as the pre-component behavior did.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Protocol, TextIO

from pipy_harness.native.agent import ProductContent
from pipy_harness.native.coding import CodingInputQueue
from pipy_harness.native.coding.effects import CodingEffectCoordinator
from pipy_harness.native.diagnostics import emit_diagnostic
from pipy_harness.native.extension_runtime import (
    QueuedCustomMessage,
    QueuedUserMessage,
    RegisteredEntryRenderer,
    RegisteredMessageRenderer,
    RenderedCustomEntry,
    drain_custom_messages,
    drain_user_messages,
    is_valid_custom_entry_type,
)
from pipy_harness.native.extensions.command_context import (
    ExtensionCapabilityError,
)
from pipy_harness.native.extensions.custom_payloads import (
    _custom_entry_redraw_rows,
    _custom_entry_renderer_payload,
    _custom_message_renderer_payload,
    render_extension_entry,
    render_extension_message,
    safe_custom_entry_data,
)
from pipy_harness.native.session_generation import SessionGenerationSnapshot
from pipy_harness.native.session_tree import (
    CustomEntry,
    CustomMessageEntry,
    NativeSessionTree,
)
from pipy_harness.native.ui.components.transcript import TranscriptComponent


class CustomEntryRunState(Protocol):
    """Live run-state fields read by the custom-entry terminal adapter."""

    @property
    def session_tree(self) -> NativeSessionTree: ...

    @property
    def extension_message_outbox(self) -> list[QueuedUserMessage]: ...

    @property
    def extension_custom_message_outbox(self) -> list[QueuedCustomMessage]: ...

    @property
    def extension_in_agent_turn(self) -> bool: ...


def _route_legacy_custom_message_input(
    content: str,
    options: Mapping[str, object],
    *,
    in_agent_turn: bool,
    enqueue_next_turn: Callable[[ProductContent], None],
    enqueue_steering: Callable[[ProductContent], None],
    enqueue_follow_up: Callable[[ProductContent], None],
    enqueue_prompt: Callable[[ProductContent], None],
) -> None:
    """Preserve established custom-message routing for live and accepted paths."""

    routed = ProductContent(content)
    deliver_as = options.get("deliverAs")
    if deliver_as is None:
        deliver_as = options.get("deliver_as")
    if deliver_as == "nextTurn":
        enqueue_next_turn(routed)
    elif deliver_as == "steer":
        enqueue_steering(routed)
    elif deliver_as in {"followUp", "follow_up"}:
        enqueue_follow_up(routed)
    elif not in_agent_turn and (
        options.get("triggerTurn") is True or options.get("trigger_turn") is True
    ):
        enqueue_prompt(routed)


@dataclass(frozen=True, slots=True, kw_only=True)
class AcceptedCustomMessageSinks:
    """Direct accepted-message sinks; deliberately has no custom outbox."""

    append_durable: Callable[[QueuedCustomMessage], object]
    render_or_diagnose: Callable[[QueuedCustomMessage, object], None]
    enqueue_next_turn: Callable[[ProductContent], None]
    enqueue_steering: Callable[[ProductContent], None]
    enqueue_follow_up: Callable[[ProductContent], None]
    enqueue_prompt: Callable[[ProductContent], None]
    in_agent_turn: Callable[[], bool]

    def deliver(self, message: QueuedCustomMessage) -> None:
        """Dispatch tree, optional render/diagnostic, then coding input."""

        appended = self.append_durable(message)
        if message.display:
            self.render_or_diagnose(message, appended)
        _route_legacy_custom_message_input(
            message.content,
            message.options,
            in_agent_turn=self.in_agent_turn(),
            enqueue_next_turn=self.enqueue_next_turn,
            enqueue_steering=self.enqueue_steering,
            enqueue_follow_up=self.enqueue_follow_up,
            enqueue_prompt=self.enqueue_prompt,
        )


@dataclass(frozen=True, slots=True)
class CustomRendererProjectionSnapshot:
    """One generation's message/entry renderer maps, captured together."""

    messages: Mapping[str, RegisteredMessageRenderer]
    entries: Mapping[str, RegisteredEntryRenderer]


@dataclass(frozen=True, slots=True, kw_only=True)
class CustomEntryTerminalTarget:
    """Where rendered custom entries land while a terminal frame is attached.

    The transcript owns the committed rows and the Ctrl+O ``tools_expanded``
    flag; ``frame_width`` and ``terminal_stream`` are the two live render
    inputs the shell still owns (the terminal driver's size and the styling
    stream). Built by the terminal shell, which keeps its driver private.
    """

    transcript: TranscriptComponent
    terminal_stream: TextIO
    frame_width: Callable[[], int]


@dataclass(frozen=True, slots=True, kw_only=True)
class CustomEntryRenderer:
    """Render custom entries and drain extension outboxes into terminal state.

    Renderer operations take one published generation snapshot; the live-state
    protocol retains only the session tree, agent-turn state, and R4a's
    legacy/harness direct-drain outboxes. ``terminal`` is ``None`` when the
    session runs headless.
    """

    ctl: CustomEntryRunState
    terminal: CustomEntryTerminalTarget | None
    coding_input_queue: CodingInputQueue
    coding_effects: CodingEffectCoordinator
    error_stream: TextIO
    generation_snapshot: Callable[[], SessionGenerationSnapshot | None] | None = None

    def _snapshot(self) -> SessionGenerationSnapshot | None:
        provider = self.generation_snapshot
        if provider is None:
            return None
        try:
            return provider()
        except Exception:  # noqa: BLE001 - a failed generation snapshot degrades to no projection
            return None

    def _renderer_projection(self) -> CustomRendererProjectionSnapshot:
        snapshot = self._snapshot()
        if snapshot is None or (projection := snapshot.generation.projection) is None:
            raise RuntimeError("published extension generation has no projection")
        return CustomRendererProjectionSnapshot(
            projection.renderers.messages,
            projection.renderers.entries,
        )

    def render_extension_custom_message(
        self,
        custom_type: str,
        data: object | None,
        *,
        width: int,
        expanded: bool,
        stream: TextIO,
        renderer_projection: CustomRendererProjectionSnapshot | None = None,
    ) -> RenderedCustomEntry:
        # Local import: the render-theme machinery is only needed on the
        # rarely hit custom-entry path, so keep it off this module's hot
        # import path (mirrors the tool-renderer ``_dispatch_render`` sites).
        from pipy_harness.native.chrome import chrome_style_for
        from pipy_harness.native.tool_renderers import build_tool_render_theme

        style = chrome_style_for(stream)
        return render_extension_message(
            (renderer_projection or self._renderer_projection()).messages,
            custom_type,
            data,
            width=width,
            expanded=expanded,
            theme=build_tool_render_theme(style),
        )

    def render_extension_custom_entry(
        self,
        entry: CustomEntry,
        *,
        width: int,
        expanded: bool,
        stream: TextIO,
        renderer_projection: CustomRendererProjectionSnapshot | None = None,
    ) -> RenderedCustomEntry | None:
        from pipy_harness.native.chrome import chrome_style_for
        from pipy_harness.native.tool_renderers import build_tool_render_theme

        return render_extension_entry(
            (renderer_projection or self._renderer_projection()).entries,
            _custom_entry_renderer_payload(entry),
            width=width,
            expanded=expanded,
            theme=build_tool_render_theme(chrome_style_for(stream)),
        )

    def add_rendered_custom_entry_to_terminal(
        self,
        entry: CustomEntry,
        renderer_projection: CustomRendererProjectionSnapshot | None = None,
    ) -> None:
        target = self.terminal
        if target is None:
            return
        renderers = renderer_projection or self._renderer_projection()
        rendered = self.render_extension_custom_entry(
            entry,
            width=target.frame_width(),
            expanded=target.transcript.tools_expanded,
            stream=target.terminal_stream,
            renderer_projection=renderers,
        )
        if rendered is None:
            return
        target.transcript.add_entry_renderer_component(
            rendered.lines,
            custom_type=entry.custom_type,
            entry=_custom_entry_renderer_payload(entry),
            renderers=renderers.entries,
        )

    def render_custom_message_entry(
        self,
        entry: CustomMessageEntry,
        *,
        width: int,
        expanded: bool,
        stream: TextIO,
        renderer_projection: CustomRendererProjectionSnapshot | None = None,
    ) -> RenderedCustomEntry:
        renderers = renderer_projection or self._renderer_projection()
        if entry.custom_type not in renderers.messages:
            return RenderedCustomEntry(tuple(entry.content.splitlines() or [""]), False)
        return self.render_extension_custom_message(
            entry.custom_type,
            _custom_message_renderer_payload(entry),
            width=width,
            expanded=expanded,
            stream=stream,
            renderer_projection=renderers,
        )

    def add_rendered_entry_to_terminal(
        self,
        custom_type: str,
        rendered: RenderedCustomEntry,
        data: object | None,
        renderer_projection: CustomRendererProjectionSnapshot | None = None,
    ) -> None:
        target = self.terminal
        if target is None:
            return
        if rendered.styled:
            target.transcript.add_custom_entry_styled(
                rendered.lines,
                custom_type=custom_type,
                data=data,
                renderers=(renderer_projection or self._renderer_projection()).messages,
            )
        else:
            target.transcript.add_custom_entry(custom_type, rendered.lines)

    def add_custom_message_entry_to_terminal(
        self,
        entry: CustomMessageEntry,
        renderer_projection: CustomRendererProjectionSnapshot | None = None,
    ) -> None:
        target = self.terminal
        if target is None or not entry.display:
            return
        renderers = renderer_projection or self._renderer_projection()
        rendered = self.render_custom_message_entry(
            entry,
            width=target.frame_width(),
            expanded=target.transcript.tools_expanded,
            stream=target.terminal_stream,
            renderer_projection=renderers,
        )
        self.add_rendered_entry_to_terminal(
            entry.custom_type,
            rendered,
            _custom_message_renderer_payload(entry),
            renderers,
        )

    def replay_custom_entries_to_terminal(self) -> None:
        if self.terminal is not None:
            renderers = self._renderer_projection()
            for entry in self.ctl.session_tree.get_branch():
                if isinstance(entry, CustomEntry):
                    self.add_rendered_custom_entry_to_terminal(entry, renderers)
                elif isinstance(entry, CustomMessageEntry) and entry.display:
                    self.add_custom_message_entry_to_terminal(entry, renderers)

    def redraw_custom_entries_for_active_branch(self) -> None:
        target = self.terminal
        if target is None:
            return
        renderers = self._renderer_projection()

        def render_for_redraw(entry: CustomEntry) -> RenderedCustomEntry | None:
            return self.render_extension_custom_entry(
                entry,
                width=target.frame_width(),
                expanded=target.transcript.tools_expanded,
                stream=target.terminal_stream,
                renderer_projection=renderers,
            )

        def render_message_for_redraw(
            entry: CustomMessageEntry,
        ) -> RenderedCustomEntry:
            return self.render_custom_message_entry(
                entry,
                width=target.frame_width(),
                expanded=target.transcript.tools_expanded,
                stream=target.terminal_stream,
                renderer_projection=renderers,
            )

        target.transcript.redraw_custom_entries(
            _custom_entry_redraw_rows(
                self.ctl.session_tree.get_branch(),
                render_for_redraw,
                render_message_for_redraw,
                render_metadata=renderers.messages,
                entry_render_metadata=renderers.entries,
            )
        )

    def extension_append_entry(
        self, custom_type: str, data: object | None = None
    ) -> object:
        with self._accepted_coding_effect():
            safe_type = str(custom_type).strip()
            if not is_valid_custom_entry_type(safe_type):
                raise ValueError("invalid custom entry type")
            safe_data = safe_custom_entry_data(data)
            renderers = (
                self._renderer_projection() if self.terminal is not None else None
            )
            with self.coding_effects.lock:
                appended = self.ctl.session_tree.append_custom(safe_type, safe_data)
            if self.terminal is not None:
                self.add_rendered_custom_entry_to_terminal(appended, renderers)
            return appended.id

    def extension_send_message(
        self,
        custom_type: str,
        content: str,
        display: bool,
        options: Mapping[str, object],
        details: object | None = None,
    ) -> object:
        with self._accepted_coding_effect():
            renderers = self._renderer_projection() if display else None
            return self._deliver_custom_message_effects(
                QueuedCustomMessage(custom_type, content, display, details, options),
                renderers,
            )

    def _deliver_custom_message(
        self,
        message: QueuedCustomMessage,
        renderer_projection: CustomRendererProjectionSnapshot | None = None,
    ) -> object:
        with self._accepted_coding_effect():
            return self._deliver_custom_message_effects(message, renderer_projection)

    def _deliver_custom_message_effects(
        self,
        message: QueuedCustomMessage,
        renderer_projection: CustomRendererProjectionSnapshot | None = None,
    ) -> object:
        with self.coding_effects.lock:
            appended = self.ctl.session_tree.append_custom_message(
                message.custom_type,
                message.content,
                display=message.display,
                details=message.details,
            )
        if message.display:
            if self.terminal is not None:
                self.add_custom_message_entry_to_terminal(appended, renderer_projection)
            else:
                rendered = self.render_custom_message_entry(
                    appended,
                    width=80,
                    expanded=False,
                    stream=self.error_stream,
                    renderer_projection=renderer_projection,
                )
                lines = "\n".join(str(line) for line in rendered.lines)
                # Headless by construction (no terminal target): route through
                # the diagnostic helper so its stderr path owns sanitization.
                emit_diagnostic(
                    None,
                    self.error_stream,
                    f"{message.custom_type}:\n{lines}"
                    if lines
                    else message.custom_type,
                )
        _route_legacy_custom_message_input(
            message.content,
            message.options,
            in_agent_turn=self.ctl.extension_in_agent_turn,
            enqueue_next_turn=self.coding_input_queue.enqueue_next_turn_context,
            enqueue_steering=self.coding_input_queue.enqueue_extension_steering,
            enqueue_follow_up=self.coding_input_queue.enqueue_extension_follow_up,
            enqueue_prompt=self.coding_input_queue.enqueue_extension_prompt,
        )
        return appended.id

    @contextmanager
    def _accepted_coding_effect(self) -> Iterator[None]:
        with self.coding_effects.effect() as admitted:
            if not admitted:
                raise ExtensionCapabilityError("coding session is closed")
            yield

    def drain_extension_outboxes(self) -> None:
        """Move one coherent generation's scheduled messages into session queues."""

        if self.generation_snapshot is None:
            self._drain_extension_outboxes_direct(
                self.ctl.extension_message_outbox,
                self.ctl.extension_custom_message_outbox,
            )
            return
        snapshot = self._snapshot()
        if snapshot is None or (projection := snapshot.generation.projection) is None:
            raise RuntimeError("published extension generation has no projection")
        queues = projection.queues
        if queues.message_routing.route_drain(self._deliver_extension_outbox_batch):
            return
        self._drain_extension_outboxes_direct(
            queues.user.storage, queues.custom.storage
        )

    def _drain_extension_outboxes_direct(
        self,
        user_outbox: list[QueuedUserMessage],
        custom_outbox: list[QueuedCustomMessage],
    ) -> None:
        self._deliver_extension_outbox_batch(
            tuple(drain_user_messages(user_outbox)),
            tuple(drain_custom_messages(custom_outbox)),
        )

    def _deliver_extension_outbox_batch(
        self,
        user_messages: tuple[QueuedUserMessage, ...],
        custom_messages: tuple[QueuedCustomMessage, ...],
    ) -> None:
        for message in user_messages:
            self.coding_input_queue.enqueue_extension_prompt(
                ProductContent(message.content)
            )
        for custom_message in custom_messages:
            self._deliver_custom_message(custom_message)
