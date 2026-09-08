"""Headless synchronous coordination for native product-session persistence."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol, cast, runtime_checkable

from pipy_harness.native.agent.content import ProductContent
from pipy_harness.native.agent.messages import AgentMessage
from pipy_harness.native.coding.state import (
    CodingSessionState,
    require_exact_agent_message,
    require_exact_agent_messages,
)


@dataclass(frozen=True, slots=True)
class CodingProductSessionContext:
    """Exact full-content provider history loaded from product persistence."""

    messages: tuple[AgentMessage, ...]
    prior_summary: ProductContent | None = None
    entry_ids: tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        _require_context(self)


@dataclass(frozen=True, slots=True)
class CodingProductSessionCompaction:
    """One full-content compaction transition and its durable projection."""

    retained_messages: tuple[AgentMessage, ...]
    summary_suffix: ProductContent
    durable_summary: ProductContent
    dropped_group_count: int
    dropped_message_count: int
    measure_before: int
    first_kept_entry_id: str | None = None
    retained_user_entry_id: str | None = None

    def __post_init__(self) -> None:
        _require_compaction(self)


@runtime_checkable
class CodingProductSessionPort(Protocol):
    """Synchronous product-persistence effects required by the coordinator."""

    def load_active_history(self) -> CodingProductSessionContext:
        """Load the active branch's exact canonical provider context."""

        ...

    def append_message(self, message: AgentMessage) -> None:
        """Persist one already-applied canonical message transition."""

        ...

    def apply_compaction(self, action: CodingProductSessionCompaction) -> None:
        """Persist one already-applied full-content compaction transition."""

        ...


@dataclass(frozen=True, slots=True)
class CodingProductSessionCallbacks:
    """Typed callback adapter for existing product-session write owners."""

    load_active_history_callback: Callable[[], CodingProductSessionContext]
    append_message_callback: Callable[[AgentMessage], None]
    apply_compaction_callback: Callable[[CodingProductSessionCompaction], None]

    def __post_init__(self) -> None:
        _require_callback(
            self.load_active_history_callback,
            "load_active_history_callback",
        )
        _require_callback(self.append_message_callback, "append_message_callback")
        _require_callback(
            self.apply_compaction_callback,
            "apply_compaction_callback",
        )

    def load_active_history(self) -> CodingProductSessionContext:
        callback = self.load_active_history_callback
        _require_callback(callback, "load_active_history_callback")
        return callback()

    def append_message(self, message: AgentMessage) -> None:
        callback = self.append_message_callback
        _require_callback(callback, "append_message_callback")
        result = callback(message)
        _require_none(result, "append_message_callback")

    def apply_compaction(self, action: CodingProductSessionCompaction) -> None:
        callback = self.apply_compaction_callback
        _require_callback(callback, "apply_compaction_callback")
        result = callback(action)
        _require_none(result, "apply_compaction_callback")


class CodingProductSessionCoordinator:
    """Coordinate state-first transitions with synchronous durable effects."""

    __slots__ = ("_port", "_state", "_loaded_context")

    def __init__(
        self,
        *,
        state: CodingSessionState,
        port: CodingProductSessionPort,
    ) -> None:
        if type(state) is not CodingSessionState:
            raise TypeError("state must be an exact CodingSessionState")
        if not isinstance(port, CodingProductSessionPort):
            raise TypeError("port must implement CodingProductSessionPort")
        self._state = state
        self._port = port
        self._loaded_context: CodingProductSessionContext | None = None

    def append_message(self, message: AgentMessage) -> None:
        """Apply a canonical message, then synchronously persist that object."""

        require_exact_agent_message(message, "message")
        self._state.append_message(message)
        append_message = cast(
            Callable[[AgentMessage], object], self._port.append_message
        )
        result = append_message(message)
        _require_none(result, "port.append_message")

    def rebuild_active_history(self) -> None:
        """Load, validate, and atomically replace active provider history."""

        context = self._port.load_active_history()
        _require_context(context)
        with self._state.state_lock:
            self._state.rebuild_history(
                context.messages,
                summary_suffix=(
                    f"\n\n{context.prior_summary.value}"
                    if context.prior_summary is not None
                    else ""
                ),
            )
            self._loaded_context = context

    def resolve_entry_id(
        self, message: AgentMessage, active: CodingProductSessionContext
    ) -> str | None:
        """Resolve identity against active entries or the last loaded projection.

        Loaded custom/branch messages are newly projected objects. Their stored
        origin is checked against the active projection, never found by text.
        """

        _require_context(active)
        if active.entry_ids is None:
            return None
        with self._state.state_lock:
            direct = [
                entry_id
                for item, entry_id in zip(
                    active.messages, active.entry_ids, strict=True
                )
                if item is message
            ]
            if direct:
                return direct[0] if len(direct) == 1 else None
            loaded = self._loaded_context
            if loaded is None or loaded.entry_ids is None:
                return None
            origins = [
                entry_id
                for item, entry_id in zip(
                    loaded.messages, loaded.entry_ids, strict=True
                )
                if item is message
            ]
            if len(origins) != 1:
                return None
            origin = origins[0]
            for item, entry_id in zip(active.messages, active.entry_ids, strict=True):
                if entry_id == origin:
                    return origin if item == message else None
            return None

    def apply_compaction(self, action: CodingProductSessionCompaction) -> None:
        """Apply full-content compaction, then synchronously persist it."""

        self.accept_compaction(action)
        self.persist_compaction(action)

    def accept_compaction(self, action: CodingProductSessionCompaction) -> None:
        """Apply validated live state; composition holds its acceptance guards."""

        _require_compaction(action)
        self._state.apply_compaction(
            action.retained_messages,
            summary_suffix=action.summary_suffix.value,
            dropped_group_count=action.dropped_group_count,
            dropped_message_count=action.dropped_message_count,
        )

    def persist_compaction(self, action: CodingProductSessionCompaction) -> None:
        """Persist an accepted action after the session mutex is released."""

        _require_compaction(action)
        apply_compaction = cast(
            Callable[[CodingProductSessionCompaction], object],
            self._port.apply_compaction,
        )
        result = apply_compaction(action)
        _require_none(result, "port.apply_compaction")


def _require_context(context: object) -> None:
    if type(context) is not CodingProductSessionContext:
        raise TypeError("loaded context must be an exact CodingProductSessionContext")
    require_exact_agent_messages(context.messages, "context.messages")
    if context.prior_summary is not None:
        _require_non_empty_product_content(context.prior_summary, "prior_summary")
    if context.entry_ids is not None:
        if type(context.entry_ids) is not tuple:
            raise TypeError("entry_ids must be an exact tuple or None")
        if len(context.entry_ids) != len(context.messages):
            raise ValueError("entry_ids must correspond to every message")
        for entry_id in context.entry_ids:
            _require_entry_id(entry_id)
        if len(set(context.entry_ids)) != len(context.entry_ids):
            raise ValueError("entry_ids must be unique")


def _require_compaction(action: object) -> None:
    if type(action) is not CodingProductSessionCompaction:
        raise TypeError("action must be an exact CodingProductSessionCompaction")
    require_exact_agent_messages(action.retained_messages, "retained_messages")
    _require_non_empty_product_content(action.summary_suffix, "summary_suffix")
    _require_non_empty_product_content(action.durable_summary, "durable_summary")
    _require_non_negative_int(action.dropped_group_count, "dropped_group_count")
    _require_non_negative_int(action.dropped_message_count, "dropped_message_count")
    if action.dropped_message_count == 0:
        raise ValueError("dropped_message_count must be positive")
    _require_non_negative_int(action.measure_before, "measure_before")
    if action.first_kept_entry_id is not None:
        _require_entry_id(action.first_kept_entry_id)
    if action.retained_user_entry_id is not None:
        _require_entry_id(action.retained_user_entry_id)
        if action.first_kept_entry_id is None:
            raise ValueError("anchored compaction requires first_kept_entry_id")


def _require_entry_id(entry_id: object) -> None:
    if type(entry_id) is not str:
        raise TypeError("entry id must be an exact string")
    if not entry_id:
        raise ValueError("entry id must not be empty")


def _require_non_empty_product_content(content: object, field_name: str) -> None:
    if type(content) is not ProductContent:
        raise TypeError(f"{field_name} must be an exact ProductContent")
    if type(content.value) is not str:
        raise TypeError(f"{field_name}.value must be an exact string")
    if not content.value:
        raise ValueError(f"{field_name} must not be empty")


def _require_non_negative_int(value: object, field_name: str) -> None:
    if type(value) is not int:
        raise TypeError(f"{field_name} must be an exact integer")
    if value < 0:
        raise ValueError(f"{field_name} must not be negative")


def _require_callback(callback: object, field_name: str) -> None:
    if not callable(callback):
        raise TypeError(f"{field_name} must be callable")


def _require_none(value: object, operation: str) -> None:
    if value is not None:
        raise TypeError(f"{operation} must complete synchronously and return None")
