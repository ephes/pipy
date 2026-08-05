"""Terminal frame ownership, painting, modal driving, and resize handling."""

from __future__ import annotations

import select
import threading
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, replace
from typing import Generic, Literal, Protocol, TextIO, TypeVar

from pipy_harness.native.chrome import ChromeStyle, chrome_style_for
from pipy_harness.native.frame_renderer import (
    ChromeSnapshot,
    FrameBlock,
    FrameLine,
    FrameSnapshot,
    InputSnapshot,
    PaintPlan,
    PaintState,
    ResolvedCustomEditorLine,
    block_lines,
    build_paint_plan,
    clip_text,
    input_index,
    pad_text,
    render_full_frame,
    render_live_region,
    style_line,
)
from pipy_harness.native.overlay_state import OverlayState
from pipy_harness.native.terminal_driver import _RESIZE_POLL_SECONDS, TerminalDriver
from pipy_harness.native.tool_renderers import build_tool_render_theme
from pipy_harness.native.ui.components.custom_overlay import custom_overlay_region_lines
from pipy_harness.native.ui.components.model_selector import (
    model_selector_region_lines,
)
from pipy_harness.native.ui.components.scoped_models_selector import (
    scoped_models_region_lines,
)
from pipy_harness.native.ui.components.session_picker import (
    session_picker_region_lines,
)
from pipy_harness.native.ui.components.settings_dialog import (
    settings_dialog_region_lines,
)
from pipy_harness.native.ui.components.tree_selector import tree_selector_region_lines
from pipy_harness.native.ui.paint_lock import PaintLock

T = TypeVar("T")
ContributorName = Literal[
    "popup",
    "pending",
    "status",
    "header",
    "above_editor",
    "below_editor",
    "footer",
    "custom_editor",
]
OverlayName = Literal[
    "custom",
    "settings",
    "project_trust",
    "session_picker",
    "tree",
    "scoped_models",
    "model",
]


class TranscriptFrameSource(Protocol):
    history_blocks: list[tuple[str, tuple[str, ...]]]
    assistant_text: str
    reasoning_text: str
    tool_output_text: str
    working_text: str
    thinking_hidden: bool
    hidden_thinking_label: str
    tools_expanded: bool


class InputFrameSource(Protocol):
    def snapshot(
        self, custom_lines: tuple[ResolvedCustomEditorLine, ...] | None
    ) -> InputSnapshot: ...

    def stage_paste(self, text: str) -> None: ...


@dataclass(frozen=True, slots=True)
class ScreenRenderInputs:
    """The live render values owned beside the terminal driver."""

    width: Callable[[], int]
    stream: TextIO
    expanded: Callable[[], bool]

    def theme(self) -> object:
        return build_tool_render_theme(chrome_style_for(self.stream))


@dataclass(slots=True)
class ScreenState:
    """All mutable terminal-frame bookkeeping guarded by one paint lock."""

    closed: bool = False
    painted_block_count: int = 0
    live_height: int = 0
    live_input_row: int = 0
    painting: bool = False
    paint_requested_during_paint: bool = False
    last_painted_size: tuple[int, int] = (0, 0)


@dataclass(frozen=True, slots=True)
class StandardFrameParts:
    popup: tuple[FrameLine, ...] = ()
    pending: tuple[FrameLine, ...] = ()
    status: tuple[FrameLine, ...] = ()
    header: tuple[FrameLine, ...] = ()
    above: tuple[FrameLine, ...] = ()
    below: tuple[FrameLine, ...] = ()
    footer: tuple[FrameLine, ...] = ()
    custom: tuple[ResolvedCustomEditorLine, ...] | None = None


@dataclass(frozen=True, slots=True)
class FrameRegionSources:
    """Narrow callbacks for each effectful ordinary frame region."""

    popup: Callable[[int, int], Sequence[FrameLine]]
    pending: Callable[[int], Sequence[FrameLine]]
    status: Callable[[int], Sequence[FrameLine]]
    header: Callable[[int], Sequence[FrameLine]]
    above_editor: Callable[[int], Sequence[FrameLine]]
    below_editor: Callable[[int], Sequence[FrameLine]]
    footer: Callable[[int], Sequence[FrameLine] | None]
    custom_editor: Callable[[int], Sequence[ResolvedCustomEditorLine] | None]


@dataclass(frozen=True, slots=True)
class FrameSources:
    """Explicit live buffers and contributors consumed by frame snapshots."""

    transcript: TranscriptFrameSource
    input_editor: InputFrameSource
    regions: FrameRegionSources
    footer_lines: Callable[[], tuple[str, str]]
    poll_idle: Callable[[], None]


@dataclass(frozen=True, slots=True)
class FrameRequest:
    width: int
    height: int


@dataclass(frozen=True, slots=True)
class FrameContributor:
    name: ContributorName
    contribute: Callable[[FrameRequest, StandardFrameParts], StandardFrameParts]


@dataclass(frozen=True, slots=True)
class OverlayContributor:
    name: OverlayName
    render: Callable[[FrameRequest], Sequence[FrameLine]]


@dataclass(frozen=True, slots=True)
class OrderedFrameContributors:
    """Pinned execution order for ordinary and overlay frame contributors."""

    ordinary: tuple[FrameContributor, ...]
    overlays: tuple[OverlayContributor, ...]


@dataclass(frozen=True, slots=True)
class DriveResult(Generic[T]):
    value: T


@dataclass(frozen=True, slots=True)
class DriveOwner(Generic[T]):
    """Typed lifecycle consumed by the screen's one modal key loop."""

    open: Callable[[], DriveResult[T] | None]
    handle_key: Callable[[str | None], DriveResult[T] | None]
    is_finished: Callable[[], bool] = lambda: False
    dispose: Callable[[], T] | None = None
    consume_paste: Callable[[], object] | None = None


class Screen:
    """Own one inline terminal screen and the lock shared by all UI owners."""

    def __init__(
        self,
        driver: TerminalDriver,
        overlays: OverlayState,
        terminal_stream: TextIO,
        input_fd: Callable[[], int],
    ) -> None:
        self._driver = driver
        self._overlays = overlays
        self._terminal_stream = terminal_stream
        self._input_fd = input_fd
        self._paint_lock = PaintLock(threading.RLock())
        self._state = ScreenState()
        self._sources: FrameSources | None = None
        self.render_inputs = ScreenRenderInputs(
            self.frame_width, terminal_stream, self.tools_expanded
        )
        self.contributors = self._build_contributors()

    @property
    def paint_lock(self) -> PaintLock:
        return self._paint_lock

    @property
    def state(self) -> ScreenState:
        return self._state

    def bind(self, sources: FrameSources) -> None:
        with self._paint_lock:
            if self._sources is not None:
                raise RuntimeError("screen frame sources are already bound")
            self._sources = sources

    def _require_sources(self) -> FrameSources:
        if self._sources is None:
            raise RuntimeError("screen frame sources are not bound")
        return self._sources

    def frame_width(self) -> int:
        return self._driver.size()[0]

    def tools_expanded(self) -> bool:
        return self._require_sources().transcript.tools_expanded

    def _build_contributors(self) -> OrderedFrameContributors:
        ordinary = (
            FrameContributor("popup", self._contribute_popup),
            FrameContributor("pending", self._contribute_pending),
            FrameContributor("status", self._contribute_status),
            FrameContributor("header", self._contribute_header),
            FrameContributor("above_editor", self._contribute_above),
            FrameContributor("below_editor", self._contribute_below),
            FrameContributor("footer", self._contribute_footer),
            FrameContributor("custom_editor", self._contribute_custom),
        )
        overlays = (
            OverlayContributor("custom", self._custom_overlay),
            OverlayContributor("settings", self._settings_overlay),
            OverlayContributor("project_trust", self._settings_overlay),
            OverlayContributor("session_picker", self._session_overlay),
            OverlayContributor("tree", self._tree_overlay),
            OverlayContributor("scoped_models", self._scoped_overlay),
            OverlayContributor("model", self._model_overlay),
        )
        return OrderedFrameContributors(ordinary, overlays)

    def _contribute_popup(
        self, request: FrameRequest, parts: StandardFrameParts
    ) -> StandardFrameParts:
        source = self._require_sources().regions.popup
        return replace(parts, popup=tuple(source(request.width, request.height)))

    def _contribute_pending(
        self, request: FrameRequest, parts: StandardFrameParts
    ) -> StandardFrameParts:
        source = self._require_sources().regions.pending
        return replace(parts, pending=tuple(source(request.width)))

    def _contribute_status(
        self, request: FrameRequest, parts: StandardFrameParts
    ) -> StandardFrameParts:
        source = self._require_sources().regions.status
        return replace(parts, status=tuple(source(request.width)))

    def _contribute_header(
        self, request: FrameRequest, parts: StandardFrameParts
    ) -> StandardFrameParts:
        source = self._require_sources().regions.header
        return replace(parts, header=tuple(source(request.width)))

    def _contribute_above(
        self, request: FrameRequest, parts: StandardFrameParts
    ) -> StandardFrameParts:
        source = self._require_sources().regions.above_editor
        return replace(parts, above=tuple(source(request.width)))

    def _contribute_below(
        self, request: FrameRequest, parts: StandardFrameParts
    ) -> StandardFrameParts:
        source = self._require_sources().regions.below_editor
        return replace(parts, below=tuple(source(request.width)))

    def _contribute_footer(
        self, request: FrameRequest, parts: StandardFrameParts
    ) -> StandardFrameParts:
        custom = self._require_sources().regions.footer(request.width)
        return replace(parts, footer=self._footer_lines(request.width, custom))

    def _footer_lines(
        self, width: int, custom: Sequence[FrameLine] | None
    ) -> tuple[FrameLine, ...]:
        if custom is not None:
            return tuple(custom)
        first, second = self._require_sources().footer_lines()
        return (
            FrameLine(clip_text(first, width), "footer"),
            FrameLine(clip_text(second, width), "footer"),
        )

    def _contribute_custom(
        self, request: FrameRequest, parts: StandardFrameParts
    ) -> StandardFrameParts:
        custom = self._require_sources().regions.custom_editor(request.width)
        return replace(parts, custom=None if custom is None else tuple(custom))

    def _overlay_footer(self) -> tuple[str, str]:
        return self._require_sources().footer_lines()

    def _custom_overlay(self, request: FrameRequest) -> Sequence[FrameLine]:
        return custom_overlay_region_lines(
            self._overlays, width=request.width, height=request.height
        )

    def _settings_overlay(self, request: FrameRequest) -> Sequence[FrameLine]:
        return settings_dialog_region_lines(
            self._overlays,
            width=request.width,
            height=request.height,
            footer_lines=self._overlay_footer(),
        )

    def _session_overlay(self, request: FrameRequest) -> Sequence[FrameLine]:
        return session_picker_region_lines(
            self._overlays,
            width=request.width,
            height=request.height,
            footer_lines=self._overlay_footer(),
        )

    def _tree_overlay(self, request: FrameRequest) -> Sequence[FrameLine]:
        return tree_selector_region_lines(
            self._overlays,
            width=request.width,
            height=request.height,
            footer_lines=self._overlay_footer(),
        )

    def _scoped_overlay(self, request: FrameRequest) -> Sequence[FrameLine]:
        return scoped_models_region_lines(
            self._overlays,
            width=request.width,
            height=request.height,
            footer_lines=self._overlay_footer(),
        )

    def _model_overlay(self, request: FrameRequest) -> Sequence[FrameLine]:
        return model_selector_region_lines(
            self._overlays,
            width=request.width,
            height=request.height,
            footer_lines=self._overlay_footer(),
        )

    def force_full_redraw(self) -> None:
        with self._paint_lock:
            if not self._driver.write_deferred("\x1b[2J\x1b[H"):
                return
            self._reset_live_state()
            # Keep the deferred clear, reset, and reentrant paint in one
            # transaction so no terminal owner can observe the blank screen.
            self.paint()

    def _reset_live_state(self) -> None:
        self._state.painted_block_count = 0
        self._state.live_height = 0
        self._state.live_input_row = 0

    def render_lines(
        self, *, width: int | None = None, height: int | None = None, pad: bool = True
    ) -> list[str]:
        return [
            line.text for line in self._frame_lines(width=width, height=height, pad=pad)
        ]

    def _frame_lines(
        self,
        *,
        width: int | None = None,
        height: int | None = None,
        pad: bool = True,
    ) -> list[FrameLine]:
        resolved = self._driver.size(width=width, height=height)
        snapshot = self._frame_snapshot(
            width=resolved[0], height=resolved[1], include_session_picker=False
        )
        return list(render_full_frame(snapshot, pad=pad))

    def request_render(self) -> None:
        with self._paint_lock:
            if self._state.closed:
                return
            if self._state.painting:
                self._state.paint_requested_during_paint = True
                return
        self.paint()

    def paint(self) -> None:
        with self._paint_lock:
            if self._state.closed:
                return
            if self._state.painting:
                self._state.paint_requested_during_paint = True
                return
            self._run_paint_transaction()

    def _run_paint_transaction(self) -> None:
        self._state.painting = True
        try:
            self._paint()
            self._paint_again_if_requested()
        finally:
            self._state.painting = False
            self._state.paint_requested_during_paint = False

    def _paint_again_if_requested(self) -> None:
        if self._state.paint_requested_during_paint and not self._state.closed:
            self._state.paint_requested_during_paint = False
            self._paint()

    def _paint(self) -> None:
        width, height = self._driver.size()
        snapshot = self._frame_snapshot(
            width=width, height=height, include_session_picker=True
        )
        plan = build_paint_plan(snapshot, self._paint_state(), self._style())
        self._record_attempted_plan(plan)
        self._write_plan(plan)

    def _paint_state(self) -> PaintState:
        return PaintState(
            painted_block_count=self._state.painted_block_count,
            live_height=self._state.live_height,
            live_input_row=self._state.live_input_row,
        )

    def _style(self) -> ChromeStyle:
        return chrome_style_for(self._terminal_stream)

    def _record_attempted_plan(self, plan: PaintPlan) -> None:
        self._state.painted_block_count = plan.painted_block_count
        self._state.live_height = plan.live_height
        self._state.live_input_row = plan.live_input_row
        self._state.last_painted_size = plan.painted_size

    def _write_plan(self, plan: PaintPlan) -> None:
        self._driver.write_frame(
            prior_live_height=plan.prior_live_height,
            prior_live_input_row=plan.prior_live_input_row,
            committed_rows=tuple(
                (row.text, row.erase_tail) for row in plan.committed_rows
            ),
            live_rows=tuple((row.text, row.erase_tail) for row in plan.live_rows),
            cursor_lines_up=plan.cursor_lines_up,
            cursor_col=plan.cursor_col,
            cursor_visible=plan.cursor_visible,
        )

    def _live_region_lines(self, *, width: int, height: int) -> list[FrameLine]:
        snapshot = self._frame_snapshot(
            width=width, height=height, include_session_picker=True
        )
        return list(render_live_region(snapshot))

    def _frame_snapshot(
        self, *, width: int, height: int, include_session_picker: bool
    ) -> FrameSnapshot:
        request = FrameRequest(width, height)
        overlay = self._active_overlay(request, include_session_picker)
        parts = (
            self._standard_frame_inputs(request)
            if overlay is None
            else StandardFrameParts()
        )
        return self._freeze_snapshot(request, overlay, parts)

    def _standard_frame_inputs(self, request: FrameRequest) -> StandardFrameParts:
        parts = StandardFrameParts()
        for contributor in self.contributors.ordinary:
            parts = contributor.contribute(request, parts)
        return parts

    def _active_overlay(
        self, request: FrameRequest, include_session_picker: bool
    ) -> tuple[FrameLine, ...] | None:
        active = self._overlays.active
        if active == "session_picker" and not include_session_picker:
            return None
        for contributor in self.contributors.overlays:
            if contributor.name == active:
                return tuple(contributor.render(request))
        return None

    def _freeze_snapshot(
        self,
        request: FrameRequest,
        overlay: tuple[FrameLine, ...] | None,
        parts: StandardFrameParts,
    ) -> FrameSnapshot:
        sources = self._require_sources()
        transcript = sources.transcript
        history = tuple(
            FrameBlock(kind, tuple(lines)) for kind, lines in transcript.history_blocks
        )
        return FrameSnapshot(
            width=request.width,
            height=request.height,
            history=history,
            assistant_text=transcript.assistant_text,
            reasoning_text=transcript.reasoning_text,
            tool_output_text=transcript.tool_output_text,
            working_text=transcript.working_text,
            thinking_hidden=transcript.thinking_hidden,
            hidden_thinking_label=transcript.hidden_thinking_label,
            tools_expanded=transcript.tools_expanded,
            input=sources.input_editor.snapshot(parts.custom),
            popup=parts.popup,
            pending=parts.pending,
            chrome=ChromeSnapshot(
                parts.header, parts.above, parts.below, parts.footer, parts.status
            ),
            overlay=overlay,
            cursor_visible=self._overlays.active is None,
        )

    def read_driver_key(self, key: str | None) -> str | None:
        if key == "paste":
            self._require_sources().input_editor.stage_paste(
                self._driver.consume_paste()
            )
        return key

    def read_key_polling_resize(self, fd: int) -> str | None:
        while True:
            self._poll_idle_sources()
            if self._driver.has_pending_input():
                return self.read_driver_key(self._driver.read_key(fd))
            readable, _, _ = select.select([fd], [], [], _RESIZE_POLL_SECONDS)
            if fd in readable:
                return self.read_driver_key(self._driver.read_key(fd))

    def _poll_idle_sources(self) -> None:
        self._require_sources().poll_idle()
        self.poll_resize_repaint()

    def poll_resize_repaint(self) -> bool:
        pending = self._driver.take_resize_pending()
        if pending or self._driver.size() != self._state.last_painted_size:
            self._repaint_after_resize()
            return True
        return False

    def _repaint_after_resize(self) -> None:
        with self._paint_lock:
            if self._state.closed:
                return
            if not self._driver.write_deferred("\x1b[2J\x1b[H"):
                return
            self._reset_live_state()
            self._paint()

    def drive(self, owner: DriveOwner[T]) -> T:
        result: DriveResult[T] | None = None
        raw_mode_acquired = False
        try:
            result = owner.open()
            if result is None:
                fd = self._input_fd()
                self._driver.enter_raw_mode()
                raw_mode_acquired = True
                result = self._drive_keys(owner, fd)
        finally:
            if owner.dispose is not None:
                result = DriveResult(owner.dispose())
            if raw_mode_acquired:
                self._driver.restore_terminal_mode()
        if result is None:
            raise RuntimeError("modal owner finished without a result")
        return result.value

    def _drive_keys(self, owner: DriveOwner[T], fd: int) -> DriveResult[T] | None:
        while not owner.is_finished():
            key = self.read_key_polling_resize(fd)
            if key == "paste" and owner.consume_paste is not None:
                owner.consume_paste()
                continue
            result = owner.handle_key(key)
            if result is not None:
                return result
        return None

    def close(self) -> None:
        self._driver.force_restore_terminal_mode()
        self._driver.remove_resize_handler()
        with self._paint_lock:
            if self._state.closed:
                return
            self._state.closed = True
            self._driver.write(self._close_output())

    def _close_output(self) -> str:
        output: list[str] = []
        if self._state.live_height > 0:
            below = (self._state.live_height - 1) - self._state.live_input_row
            if below > 0:
                output.append(f"\x1b[{below}B")
            output.append("\r")
        output.append("\x1b[?25h\n")
        return "".join(output)

    @contextmanager
    def external_io_suspension(self) -> Iterator[None]:
        with self._paint_lock:
            self._suspend_external_io()
        try:
            yield
        finally:
            with self._paint_lock:
                repaint = self._driver.resume_terminal_mode()
            if repaint:
                self.paint()

    def _suspend_external_io(self) -> None:
        self._driver.suspend_terminal_mode()
        self._driver.write(self._suspension_output())
        self._state.live_height = 0
        self._state.live_input_row = 0

    def _suspension_output(self) -> str:
        output: list[str] = []
        if self._state.live_height > 0:
            if self._state.live_input_row > 0:
                output.append(f"\x1b[{self._state.live_input_row}A")
            output.append("\r\x1b[J")
        output.append("\x1b[?25h")
        return "".join(output)

    def _styled_line(self, line: FrameLine, *, style: ChromeStyle, width: int) -> str:
        return style_line(line, style, width)

    def _block_frame_lines(
        self, kind: str, lines: Sequence[str], *, width: int | None = None
    ) -> list[FrameLine]:
        resolved_width = width or self.frame_width()
        return list(block_lines(FrameBlock(kind, tuple(lines)), resolved_width))

    @staticmethod
    def _input_index(lines: list[FrameLine]) -> int:
        return input_index(tuple(lines))

    @staticmethod
    def _clip(text: str, width: int) -> str:
        return clip_text(text, width)

    @staticmethod
    def _pad(text: str, width: int) -> str:
        return pad_text(text, width)
