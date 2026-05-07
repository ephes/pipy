"""In-memory native conversation and turn state."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import StrEnum
from math import isfinite
from typing import ClassVar

from pipy_harness.capture import sanitize_text

NATIVE_TURN_STORAGE_KEYS = frozenset(
    {
        "prompt_stored",
        "model_output_stored",
        "provider_responses_stored",
        "tool_payloads_stored",
        "stdout_stored",
        "stderr_stored",
        "diffs_stored",
        "file_contents_stored",
        "raw_transcript_imported",
    }
)
NATIVE_TURN_METADATA_KEYS = frozenset(
    {
        "conversation_id",
        "turn_id",
        "turn_index",
        "role",
        "status",
        "provider_turn_label",
        "provider_name",
        "model_id",
        "tool_name",
        "tool_kind",
        "duration_seconds",
        *NATIVE_TURN_STORAGE_KEYS,
    }
)
NATIVE_TURN_PAYLOAD_KEYS = NATIVE_TURN_METADATA_KEYS


class NativeTurnRole(StrEnum):
    """Closed role labels for native conversation turns."""

    SYSTEM = "system"
    USER = "user"
    PROVIDER = "provider"
    TOOL = "tool"


class NativeTurnStatus(StrEnum):
    """Closed lifecycle labels for native conversation turns."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass(frozen=True, slots=True)
class NativeConversationIdentity:
    """Pipy-owned in-memory conversation identity.

    This value is a correlation label, not a transcript id and not a prompt- or
    provider-derived value.
    """

    value: str

    MAX_LENGTH: ClassVar[int] = 118

    @classmethod
    def bootstrap(cls) -> "NativeConversationIdentity":
        return cls("native-conversation-0001")

    def __post_init__(self) -> None:
        _validate_safe_label("conversation_id", self.value, max_length=self.MAX_LENGTH)


@dataclass(frozen=True, slots=True)
class NativeTurnIdentity:
    """Pipy-owned turn identity inside one native conversation."""

    conversation_id: NativeConversationIdentity
    turn_index: int

    MAX_TURN_INDEX: ClassVar[int] = 7

    def __post_init__(self) -> None:
        if not isinstance(self.conversation_id, NativeConversationIdentity):
            raise ValueError("turn identity requires a native conversation identity")
        _validate_bounded_integer(
            "turn_index",
            self.turn_index,
            lower_bound=0,
            upper_bound=self.MAX_TURN_INDEX,
        )

    @property
    def turn_id(self) -> str:
        return f"{self.conversation_id.value}-turn-{self.turn_index:04d}"


@dataclass(frozen=True, slots=True)
class NativeTurnMetadata:
    """Metadata-only native turn state.

    Raw prompts, model output, provider payloads, tool observations, command
    output, diffs, and file contents are intentionally not fields here.
    """

    conversation_id: str
    turn_id: str
    turn_index: int
    role: NativeTurnRole
    status: NativeTurnStatus
    provider_turn_label: str | None = None
    provider_name: str | None = None
    model_id: str | None = None
    tool_name: str | None = None
    tool_kind: str | None = None
    duration_seconds: float | None = None
    prompt_stored: bool = False
    model_output_stored: bool = False
    provider_responses_stored: bool = False
    tool_payloads_stored: bool = False
    stdout_stored: bool = False
    stderr_stored: bool = False
    diffs_stored: bool = False
    file_contents_stored: bool = False
    raw_transcript_imported: bool = False

    def __post_init__(self) -> None:
        _validate_safe_label("conversation_id", self.conversation_id)
        _validate_safe_label("turn_id", self.turn_id)
        _validate_bounded_integer(
            "turn_index",
            self.turn_index,
            lower_bound=0,
            upper_bound=NativeTurnIdentity.MAX_TURN_INDEX,
        )
        if not isinstance(self.role, NativeTurnRole):
            raise ValueError("turn role must use a native turn role label")
        if not isinstance(self.status, NativeTurnStatus):
            raise ValueError("turn status must use a native turn status label")
        for field_name in (
            "provider_turn_label",
            "provider_name",
            "model_id",
            "tool_name",
            "tool_kind",
        ):
            value = getattr(self, field_name)
            if value is not None:
                _validate_safe_label(field_name, value)
        if self.duration_seconds is not None:
            if (
                not isinstance(self.duration_seconds, int | float)
                or isinstance(self.duration_seconds, bool)
                or self.duration_seconds < 0
                or not isfinite(self.duration_seconds)
            ):
                raise ValueError("duration_seconds must be a finite non-negative number")
        for field_name in NATIVE_TURN_STORAGE_KEYS:
            if getattr(self, field_name) is not False:
                raise ValueError(f"{field_name} must remain false for native turn metadata")

    @classmethod
    def from_identity(
        cls,
        identity: NativeTurnIdentity,
        *,
        role: NativeTurnRole,
        status: NativeTurnStatus = NativeTurnStatus.PENDING,
        provider_turn_label: str | None = None,
        provider_name: str | None = None,
        model_id: str | None = None,
        tool_name: str | None = None,
        tool_kind: str | None = None,
        duration_seconds: float | None = None,
    ) -> "NativeTurnMetadata":
        return cls(
            conversation_id=identity.conversation_id.value,
            turn_id=identity.turn_id,
            turn_index=identity.turn_index,
            role=role,
            status=status,
            provider_turn_label=provider_turn_label,
            provider_name=provider_name,
            model_id=model_id,
            tool_name=tool_name,
            tool_kind=tool_kind,
            duration_seconds=duration_seconds,
        )

    def archive_payload(self) -> dict[str, object]:
        """Return the metadata-only allowlisted shape for future archive events."""

        return {
            "conversation_id": self.conversation_id,
            "turn_id": self.turn_id,
            "turn_index": self.turn_index,
            "role": self.role.value,
            "status": self.status.value,
            "provider_turn_label": self.provider_turn_label,
            "provider_name": self.provider_name,
            "model_id": self.model_id,
            "tool_name": self.tool_name,
            "tool_kind": self.tool_kind,
            "duration_seconds": self.duration_seconds,
            "prompt_stored": False,
            "model_output_stored": False,
            "provider_responses_stored": False,
            "tool_payloads_stored": False,
            "stdout_stored": False,
            "stderr_stored": False,
            "diffs_stored": False,
            "file_contents_stored": False,
            "raw_transcript_imported": False,
        }


@dataclass(frozen=True, slots=True)
class NativeConversationTurn:
    """One in-memory native conversation turn."""

    identity: NativeTurnIdentity
    metadata: NativeTurnMetadata

    def __post_init__(self) -> None:
        if not isinstance(self.identity, NativeTurnIdentity):
            raise ValueError("conversation turns require a native turn identity")
        if not isinstance(self.metadata, NativeTurnMetadata):
            raise ValueError("conversation turns require native turn metadata")
        if self.metadata.conversation_id != self.identity.conversation_id.value:
            raise ValueError("turn metadata conversation_id must match turn identity")
        if self.metadata.turn_id != self.identity.turn_id:
            raise ValueError("turn metadata turn_id must match turn identity")
        if self.metadata.turn_index != self.identity.turn_index:
            raise ValueError("turn metadata turn_index must match turn identity")

    @property
    def role(self) -> NativeTurnRole:
        return self.metadata.role

    @property
    def status(self) -> NativeTurnStatus:
        return self.metadata.status

    def with_status(
        self,
        status: NativeTurnStatus,
        *,
        duration_seconds: float | None = None,
    ) -> "NativeConversationTurn":
        return replace(
            self,
            metadata=replace(
                self.metadata,
                status=status,
                duration_seconds=duration_seconds,
            ),
        )


@dataclass(frozen=True, slots=True)
class NativeConversationState:
    """Immutable bounded in-memory state for native provider turns."""

    conversation_id: NativeConversationIdentity = field(default_factory=NativeConversationIdentity.bootstrap)
    turns: tuple[NativeConversationTurn, ...] = ()
    max_turns: int = 2

    MAX_TURNS: ClassVar[int] = 8

    def __post_init__(self) -> None:
        if not isinstance(self.conversation_id, NativeConversationIdentity):
            raise ValueError("conversation state requires a native conversation identity")
        _validate_bounded_integer(
            "max_turns",
            self.max_turns,
            lower_bound=1,
            upper_bound=self.MAX_TURNS,
        )
        if len(self.turns) > self.max_turns:
            raise ValueError("conversation turn count exceeds the configured bound")
        for expected_index, turn in enumerate(self.turns):
            if not isinstance(turn, NativeConversationTurn):
                raise ValueError("conversation state requires native conversation turns")
            if turn.identity.conversation_id != self.conversation_id:
                raise ValueError("conversation turn identity must match conversation state")
            if turn.identity.turn_index != expected_index:
                raise ValueError("conversation turns must be contiguous and ordered")

    @property
    def turn_count(self) -> int:
        return len(self.turns)

    @property
    def provider_turn_count(self) -> int:
        return sum(1 for turn in self.turns if turn.role == NativeTurnRole.PROVIDER)

    def next_turn_identity(self) -> NativeTurnIdentity:
        if len(self.turns) >= self.max_turns:
            raise ValueError("conversation turn bound reached")
        return NativeTurnIdentity(
            conversation_id=self.conversation_id,
            turn_index=len(self.turns),
        )

    def append_turn(self, turn: NativeConversationTurn) -> "NativeConversationState":
        if len(self.turns) >= self.max_turns:
            raise ValueError("conversation turn bound reached")
        if turn.identity.conversation_id != self.conversation_id:
            raise ValueError("conversation turn identity must match conversation state")
        expected_identity = self.next_turn_identity()
        if turn.identity != expected_identity:
            raise ValueError("conversation turns must use the next pipy-owned turn identity")
        return replace(self, turns=(*self.turns, turn))

    def append_provider_turn(
        self,
        *,
        provider_turn_label: str,
        status: NativeTurnStatus = NativeTurnStatus.RUNNING,
        provider_name: str | None = None,
        model_id: str | None = None,
    ) -> "NativeConversationState":
        identity = self.next_turn_identity()
        turn = NativeConversationTurn(
            identity=identity,
            metadata=NativeTurnMetadata.from_identity(
                identity,
                role=NativeTurnRole.PROVIDER,
                status=status,
                provider_turn_label=provider_turn_label,
                provider_name=provider_name,
                model_id=model_id,
            ),
        )
        return replace(self, turns=(*self.turns, turn))

    def metadata_payloads(self) -> tuple[dict[str, object], ...]:
        return tuple(turn.metadata.archive_payload() for turn in self.turns)


def _validate_bounded_integer(
    field_name: str,
    value: int,
    *,
    lower_bound: int,
    upper_bound: int,
) -> None:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{field_name} must be an integer")
    if value < lower_bound or value > upper_bound:
        raise ValueError(f"{field_name} must be between {lower_bound} and {upper_bound}")


def _validate_safe_label(field_name: str, value: str, *, max_length: int = 128) -> None:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    if not value or len(value) > max_length:
        raise ValueError(f"{field_name} must be a short non-empty label")
    if sanitize_text(value) == "[REDACTED]":
        raise ValueError(f"{field_name} must not contain sensitive data")
    if any(separator in value for separator in ("/", "\\", "~")):
        raise ValueError(f"{field_name} must not be a filesystem path")
    if value in {".", ".."} or value.startswith("."):
        raise ValueError(f"{field_name} must not be a filesystem path")
    if any(ord(character) < 32 for character in value):
        raise ValueError(f"{field_name} must be a single-line label")
