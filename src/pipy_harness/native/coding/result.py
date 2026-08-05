"""Headless run->result projection for one product coding session.

This module owns the shutdown transition's ownership boundary: the bounded,
metadata-only ``CodingSessionResult`` returned by ``CodingSession.run``
and the pure projection that maps a :class:`CodingSessionResultSnapshot` into it.
No prompts, model text, tool payloads, file contents, or diffs cross this
boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from pipy_harness.models import HarnessStatus
from pipy_harness.native.coding.state import CodingSessionResultSnapshot


@dataclass(frozen=True, slots=True)
class CodingSessionResult:
    """Bounded result returned by `CodingSession.run`.

    The fields are deliberately small and metadata-only. No prompts, model
    text, tool payloads, file contents, or diffs cross this boundary.
    """

    status: HarnessStatus
    exit_code: int
    started_at: datetime
    ended_at: datetime
    provider_name: str
    model_id: str
    user_turn_count: int = 0
    tool_invocation_count: int = 0
    resource_invocation_count: int = 0
    malformed_argument_count: int = 0
    consecutive_malformed_streak: int = 0
    budget_exhausted_count: int = 0
    file_reference_count: int = 0
    file_reference_loaded_count: int = 0
    file_reference_failed_count: int = 0
    image_attachment_count: int = 0
    image_attachment_loaded_count: int = 0
    image_attachment_failed_count: int = 0
    compaction_count: int = 0
    compaction_dropped_group_count: int = 0
    error_type: str | None = None
    error_message: str | None = None
    provider_failure_type: str | None = None
    provider_failure_message: str | None = None


def build_coding_session_result(
    snapshot: CodingSessionResultSnapshot,
    *,
    status: HarnessStatus,
    exit_code: int,
    started_at: datetime,
    ended_at: datetime,
    error_type: str | None = None,
    error_message: str | None = None,
) -> CodingSessionResult:
    """Project a coding-session result snapshot into a bounded run result.

    ``build_coding_session_result`` reproduces the two shutdown transitions of
    ``CodingSession.run`` exactly:

    * The terminate-session ``FAILED`` path maps the non-image counter subset
      and carries the loop failure through ``error_type``/``error_message``; it
      leaves the image-attachment counters and provider-failure fields at their
      defaults, exactly as the prior inline builder did.
    * The ``SUCCEEDED`` path maps the full counter set including image
      attachments and projects the snapshot's optional provider failure into
      ``provider_failure_type``/``provider_failure_message``.

    ``error_type``/``error_message`` are plain strings so the projection stays
    free of provider/agent failure types; the caller unpacks the loop failure at
    the terminate site.
    """

    if type(snapshot) is not CodingSessionResultSnapshot:
        raise TypeError("snapshot must be an exact CodingSessionResultSnapshot")
    if status is HarnessStatus.FAILED:
        return CodingSessionResult(
            status=HarnessStatus.FAILED,
            exit_code=exit_code,
            started_at=started_at,
            ended_at=ended_at,
            provider_name=snapshot.provider_name,
            model_id=snapshot.model_id,
            user_turn_count=snapshot.user_turn_count,
            tool_invocation_count=snapshot.tool_invocation_count,
            resource_invocation_count=snapshot.resource_invocation_count,
            malformed_argument_count=snapshot.malformed_argument_count,
            consecutive_malformed_streak=snapshot.consecutive_malformed_streak,
            budget_exhausted_count=snapshot.budget_exhausted_count,
            file_reference_count=snapshot.file_reference_count,
            file_reference_loaded_count=snapshot.file_reference_loaded_count,
            file_reference_failed_count=snapshot.file_reference_failed_count,
            compaction_count=snapshot.compaction_count,
            compaction_dropped_group_count=snapshot.compaction_dropped_group_count,
            error_type=error_type,
            error_message=error_message,
        )
    if status is HarnessStatus.SUCCEEDED:
        provider_failure = snapshot.provider_failure
        return CodingSessionResult(
            status=HarnessStatus.SUCCEEDED,
            exit_code=exit_code,
            started_at=started_at,
            ended_at=ended_at,
            provider_name=snapshot.provider_name,
            model_id=snapshot.model_id,
            user_turn_count=snapshot.user_turn_count,
            tool_invocation_count=snapshot.tool_invocation_count,
            resource_invocation_count=snapshot.resource_invocation_count,
            malformed_argument_count=snapshot.malformed_argument_count,
            consecutive_malformed_streak=snapshot.consecutive_malformed_streak,
            budget_exhausted_count=snapshot.budget_exhausted_count,
            file_reference_count=snapshot.file_reference_count,
            file_reference_loaded_count=snapshot.file_reference_loaded_count,
            file_reference_failed_count=snapshot.file_reference_failed_count,
            image_attachment_count=snapshot.image_attachment_count,
            image_attachment_loaded_count=snapshot.image_attachment_loaded_count,
            image_attachment_failed_count=snapshot.image_attachment_failed_count,
            compaction_count=snapshot.compaction_count,
            compaction_dropped_group_count=snapshot.compaction_dropped_group_count,
            provider_failure_type=(
                provider_failure.error_type if provider_failure else None
            ),
            provider_failure_message=(
                provider_failure.message.value if provider_failure else None
            ),
        )
    raise ValueError(f"unsupported repl-result status: {status!r}")


__all__ = [
    "CodingSessionResult",
    "build_coding_session_result",
]
