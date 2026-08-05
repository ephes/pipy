"""Custom extension payload coercion and rendering helpers.

This module owns the JSON-safe custom-message/entry payload boundary and the
fail-soft extension renderer adapters. It remains dependency-neutral from the
extension activation runtime; renderer registration declarations are imported
only for static typing until their contracts move to their dedicated owner.
"""

from __future__ import annotations

import inspect
import json
from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import TYPE_CHECKING, TypeAlias

from pipy_harness.native.extension_types import (
    EntryRenderContext,
    MessageRenderContext,
    QueuedCustomMessage,
    RenderedCustomEntry,
    _safe_diagnostic,
    is_valid_custom_entry_type,
)
from pipy_harness.native.extension_ui import (
    _CUSTOM_RENDER_MAX_CHARS,
    coerce_tool_render_lines,
)
from pipy_harness.native.extensions.session_views import _json_round_trip

if TYPE_CHECKING:
    from pipy_harness.native.extensions.contracts import (
        RegisteredEntryRenderer,
        RegisteredMessageRenderer,
    )
    from pipy_harness.native.session_tree import (
        CustomEntry as _CustomEntry,
    )
    from pipy_harness.native.session_tree import (
        CustomMessageEntry as _CustomMessageEntry,
    )

# Bound custom extension-rendered session entry text and data. Product native
# sessions intentionally store full user-visible content, but extension payloads
# should still be JSON-safe and capped so a bad renderer cannot grow the TUI or
# session file without bound.
_CUSTOM_ENTRY_DATA_MAX_CHARS: int = 64 * 1024


def coerce_custom_message(
    message: Mapping[str, object],
    options: Mapping[str, object] | None = None,
) -> QueuedCustomMessage:
    """Validate and bound a Pi-shaped custom message payload."""

    if not isinstance(message, Mapping):
        raise ValueError("custom message must be a mapping")
    custom_type = str(message.get("customType", message.get("custom_type", ""))).strip()
    if not is_valid_custom_entry_type(custom_type):
        raise ValueError("invalid custom message type")
    content = str(message.get("content", ""))
    if len(content) > _CUSTOM_ENTRY_DATA_MAX_CHARS:
        content = (
            content[: _CUSTOM_ENTRY_DATA_MAX_CHARS - 128]
            + "\n[pipy: custom message truncated]"
        )
    return QueuedCustomMessage(
        custom_type=custom_type,
        content=content,
        display=bool(message.get("display", True)),
        details=safe_custom_entry_data(message.get("details")),
        options=dict(options or {}),
    )


def safe_custom_entry_data(data: object | None) -> object | None:
    """Return JSON-safe, bounded custom-entry data for the product session."""

    if data is None:
        return None
    try:
        encoded, decoded = _json_round_trip(data)
    except (TypeError, ValueError):
        encoded = str(data)
        decoded = encoded
    if len(encoded) <= _CUSTOM_ENTRY_DATA_MAX_CHARS:
        return decoded
    return {
        "truncated": True,
        "text": encoded[: _CUSTOM_ENTRY_DATA_MAX_CHARS - 128],
    }


def _custom_message_renderer_payload(entry: _CustomMessageEntry) -> dict[str, object]:
    """Return the Pi-shaped payload passed to CustomMessageEntry renderers."""

    return {
        "customType": entry.custom_type,
        "content": entry.content,
        "display": entry.display,
        "details": safe_custom_entry_data(entry.details),
    }


def _custom_entry_renderer_payload(entry: _CustomEntry) -> dict[str, object]:
    """Return the Pi-shaped full stored entry passed to entry renderers."""

    return {
        "type": "custom",
        "id": entry.id,
        "parentId": entry.parent_id,
        "timestamp": entry.timestamp,
        "customType": entry.custom_type,
        "data": safe_custom_entry_data(entry.data),
    }


_CustomEntryRedrawRow: TypeAlias = (
    tuple[str, str, tuple[str, ...]]
    | tuple[
        str,
        str,
        tuple[str, ...],
        object | None,
        Mapping[str, "RegisteredMessageRenderer"]
        | Mapping[str, "RegisteredEntryRenderer"],
    ]
)


def _custom_entry_redraw_rows(
    branch: Iterable[object],
    render_custom_entry: Callable[[_CustomEntry], RenderedCustomEntry | None],
    render_custom_message_entry: Callable[[_CustomMessageEntry], RenderedCustomEntry]
    | None = None,
    *,
    render_metadata: Mapping[str, RegisteredMessageRenderer] | None = None,
    entry_render_metadata: Mapping[str, RegisteredEntryRenderer] | None = None,
) -> list[_CustomEntryRedrawRow]:
    """Build TUI redraw rows for active-branch extension custom entries."""

    from pipy_harness.native.session_tree import (
        CustomEntry as _CustomEntry,
    )
    from pipy_harness.native.session_tree import (
        CustomMessageEntry as _CustomMessageEntry,
    )

    rows: list[_CustomEntryRedrawRow] = []
    for entry in branch:
        if isinstance(entry, _CustomEntry):
            data = _custom_entry_renderer_payload(entry)
            rendered = render_custom_entry(entry)
            if rendered is None:
                continue
            row: _CustomEntryRedrawRow = (
                "entry",
                entry.custom_type,
                tuple(rendered.lines),
            )
            if entry_render_metadata is not None:
                row = (*row, data, entry_render_metadata)
            rows.append(row)
        elif isinstance(entry, _CustomMessageEntry) and entry.display:
            if render_custom_message_entry is not None:
                data = _custom_message_renderer_payload(entry)
                rendered = render_custom_message_entry(entry)
                row = (
                    "styled" if rendered.styled else "plain",
                    entry.custom_type,
                    tuple(rendered.lines),
                )
                if render_metadata is not None:
                    row = (*row, data, render_metadata)
                rows.append(row)
            else:
                rows.append(
                    (
                        "plain",
                        entry.custom_type,
                        tuple(entry.content.splitlines() or [""]),
                    )
                )
    return rows


def _renderer_wants_context(renderer: Callable[..., object]) -> bool:
    """True if ``renderer`` requires a second positional MessageRenderContext.

    Counts only REQUIRED positional params (those without a default): a 2-arg
    ``renderer(data, ctx)`` is context-aware, while the slice-16 capture-default
    idiom ``renderer(data, prefix=captured)`` stays 1-arg/plain so its default is
    never clobbered by the context. ``*args`` is treated as context-aware.
    Defaults to False (1-arg slice-16 form) when the signature is unavailable,
    so back-compat is the safe fallback."""

    try:
        sig = inspect.signature(renderer)
    except (TypeError, ValueError):
        return False
    positional = 0
    for param in sig.parameters.values():
        if param.kind in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        ):
            if param.default is inspect.Parameter.empty:
                positional += 1
        elif param.kind is inspect.Parameter.VAR_POSITIONAL:
            return True
    return positional >= 2


def _plain_message_render(value: object | None) -> RenderedCustomEntry:
    if value is None:
        return RenderedCustomEntry((), False)
    return RenderedCustomEntry((_bounded_render_text(value),), False)


def _invoke_message_renderer(
    registered: RegisteredMessageRenderer,
    detached: object | None,
    *,
    custom_type: str,
    wants_context: bool,
    width: int,
    expanded: bool,
    theme: object | None,
) -> object:
    if not wants_context:
        return registered.renderer(detached)
    context = MessageRenderContext(
        custom_type=custom_type,
        data=detached,
        expanded=expanded,
        width=width,
        theme=theme,
    )
    return registered.renderer(detached, context)


def _coerce_message_component(
    rendered: object,
    *,
    width: int,
    fallback: object | None,
) -> RenderedCustomEntry | None:
    """Render a context-aware component, or leave plain output to the caller."""

    render = getattr(rendered, "render", None)
    if not callable(render) or isinstance(rendered, (str, bytes, bytearray)):
        return None
    produced = render(width)
    coerced = coerce_tool_render_lines(produced)
    if coerced is None:
        return _plain_message_render(fallback)
    return RenderedCustomEntry(tuple(coerced), True)


def render_extension_message(
    renderers: Mapping[str, RegisteredMessageRenderer],
    custom_type: str,
    data: object | None,
    *,
    width: int = 80,
    expanded: bool = False,
    theme: object | None = None,
) -> RenderedCustomEntry:
    """Render a custom entry through its extension renderer, fail-soft.

    A renderer that accepts a second parameter receives a MessageRenderContext
    and may return a component (committed SGR-preserving, ``styled=True``).
    Text/lines returns and any failure fall back to plain rendering
    (``styled=False``)."""

    registered = renderers.get(custom_type)
    if registered is None:
        return _plain_message_render(data)
    detached = _copy_custom_entry_data(data)
    wants_context = _renderer_wants_context(registered.renderer)
    try:
        rendered = _invoke_message_renderer(
            registered,
            detached,
            custom_type=custom_type,
            wants_context=wants_context,
            width=width,
            expanded=expanded,
            theme=theme,
        )
        # A 1-arg renderer keeps exact plain-text behavior even when its return
        # object happens to expose a render() attribute.
        if wants_context:
            component = _coerce_message_component(
                rendered,
                width=width,
                fallback=detached,
            )
            if component is not None:
                return component
        return RenderedCustomEntry(_coerce_rendered_lines(rendered), False)
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException as err:  # noqa: BLE001 - bound bad renderer behavior
        return RenderedCustomEntry((f"render error: {_safe_diagnostic(err)}",), False)


def _close_unsupported_awaitable(value: object) -> bool:
    if not inspect.isawaitable(value):
        return False
    close = getattr(value, "close", None)
    if callable(close):
        close()
    return True


def _coerce_entry_component(
    rendered: object,
    *,
    width: int,
) -> RenderedCustomEntry | None:
    if _close_unsupported_awaitable(rendered):
        return None
    if rendered is None or isinstance(rendered, (str, bytes, bytearray)):
        return None
    render = getattr(rendered, "render", None)
    if not callable(render):
        return None
    produced = render(width)
    if _close_unsupported_awaitable(produced):
        return None
    coerced = coerce_tool_render_lines(produced)
    if coerced is None:
        return None
    return RenderedCustomEntry(tuple(coerced), True)


def render_extension_entry(
    renderers: Mapping[str, RegisteredEntryRenderer],
    entry: Mapping[str, object],
    *,
    width: int = 80,
    expanded: bool = False,
    theme: object | None = None,
) -> RenderedCustomEntry | None:
    """Render one stored custom entry for the product TUI, fail-soft.

    Pi's entry renderer is a component-only, interactive surface. Missing
    renderers, ``None`` returns, unsupported outputs, awaitables, and failures
    all omit the live row while leaving the durable session entry untouched.
    """

    custom_type = str(entry.get("customType", ""))
    registered = renderers.get(custom_type)
    if registered is None:
        return None
    detached = _copy_custom_entry_data(dict(entry))
    if not isinstance(detached, dict):
        return None
    try:
        rendered = registered.renderer(
            detached,
            EntryRenderContext(expanded=expanded, width=width, theme=theme),
        )
        return _coerce_entry_component(rendered, width=width)
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException:  # noqa: BLE001 - omit a bad live renderer safely
        return None


def _copy_custom_entry_data(data: object | None) -> object | None:
    if data is None:
        return None
    try:
        _encoded, decoded = _json_round_trip(data)
        return decoded
    except (TypeError, ValueError):
        return safe_custom_entry_data(data)


def _coerce_rendered_lines(rendered: object) -> tuple[str, ...]:
    if inspect.isawaitable(rendered):
        close = getattr(rendered, "close", None)
        if callable(close):
            close()
        return ("render error: unsupported awaitable",)
    if rendered is None:
        return ()
    if isinstance(rendered, str):
        lines = rendered.splitlines() or [""]
    elif isinstance(rendered, Sequence) and not isinstance(
        rendered, (bytes, bytearray)
    ):
        lines = [str(item) for item in rendered]
    else:
        lines = [_bounded_render_text(rendered)]
    text = "\n".join(lines)
    if len(text) > _CUSTOM_RENDER_MAX_CHARS:
        text = (
            text[: _CUSTOM_RENDER_MAX_CHARS - 64] + "\n[pipy: custom render truncated]"
        )
    return tuple(text.splitlines() or [""])


def _bounded_render_text(value: object) -> str:
    try:
        text = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError):
        text = str(value)
    if len(text) > _CUSTOM_RENDER_MAX_CHARS:
        return (
            text[: _CUSTOM_RENDER_MAX_CHARS - 64] + "\n[pipy: custom render truncated]"
        )
    return text
