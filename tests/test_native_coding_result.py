"""Contracts for the headless shutdown run->result projection."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import cast

import pytest

from pipy_harness.models import HarnessStatus
from pipy_harness.native.agent import AgentFailure, ProductContent
from pipy_harness.native.agent.results import AgentUsage
from pipy_harness.native.coding.result import (
    CodingSessionResult,
    build_coding_session_result,
)
from pipy_harness.native.coding.state import CodingSessionResultSnapshot

_STARTED_AT = datetime(2026, 7, 21, 12, 0, 0, tzinfo=UTC)
_ENDED_AT = datetime(2026, 7, 21, 12, 5, 0, tzinfo=UTC)


def _populated_snapshot(
    *, provider_failure: AgentFailure | None = None
) -> CodingSessionResultSnapshot:
    """A snapshot with every counter, the image counters, and a failure set.

    The image counters and ``provider_failure`` are deliberately non-default so
    the FAILED vs SUCCEEDED subsets diverge observably.
    """

    return CodingSessionResultSnapshot(
        provider_name="provider",
        model_id="model",
        messages=(),
        usage=AgentUsage(),
        user_turn_count=7,
        tool_invocation_count=11,
        resource_invocation_count=3,
        malformed_argument_count=4,
        consecutive_malformed_streak=2,
        budget_exhausted_count=1,
        file_reference_count=5,
        file_reference_loaded_count=3,
        file_reference_failed_count=2,
        image_attachment_count=6,
        image_attachment_loaded_count=4,
        image_attachment_failed_count=2,
        compaction_count=2,
        compaction_dropped_group_count=1,
        provider_failure=provider_failure,
    )


def test_failed_projection_maps_non_image_subset_and_carries_error() -> None:
    snapshot = _populated_snapshot(
        provider_failure=AgentFailure("Ignored", ProductContent("ignored"))
    )

    result = build_coding_session_result(
        snapshot,
        status=HarnessStatus.FAILED,
        exit_code=1,
        started_at=_STARTED_AT,
        ended_at=_ENDED_AT,
        error_type="MalformedFatal",
        error_message="tool-loop ended",
    )

    assert result == CodingSessionResult(
        status=HarnessStatus.FAILED,
        exit_code=1,
        started_at=_STARTED_AT,
        ended_at=_ENDED_AT,
        provider_name="provider",
        model_id="model",
        user_turn_count=7,
        tool_invocation_count=11,
        resource_invocation_count=3,
        malformed_argument_count=4,
        consecutive_malformed_streak=2,
        budget_exhausted_count=1,
        file_reference_count=5,
        file_reference_loaded_count=3,
        file_reference_failed_count=2,
        compaction_count=2,
        compaction_dropped_group_count=1,
        error_type="MalformedFatal",
        error_message="tool-loop ended",
    )
    # The FAILED subset omits the image counters and the provider-failure fields
    # even when the snapshot carries them.
    assert result.image_attachment_count == 0
    assert result.image_attachment_loaded_count == 0
    assert result.image_attachment_failed_count == 0
    assert result.provider_failure_type is None
    assert result.provider_failure_message is None


def test_succeeded_projection_maps_image_and_provider_failure() -> None:
    snapshot = _populated_snapshot(
        provider_failure=AgentFailure("ProviderFailure", ProductContent("boom"))
    )

    result = build_coding_session_result(
        snapshot,
        status=HarnessStatus.SUCCEEDED,
        exit_code=0,
        started_at=_STARTED_AT,
        ended_at=_ENDED_AT,
    )

    assert result == CodingSessionResult(
        status=HarnessStatus.SUCCEEDED,
        exit_code=0,
        started_at=_STARTED_AT,
        ended_at=_ENDED_AT,
        provider_name="provider",
        model_id="model",
        user_turn_count=7,
        tool_invocation_count=11,
        resource_invocation_count=3,
        malformed_argument_count=4,
        consecutive_malformed_streak=2,
        budget_exhausted_count=1,
        file_reference_count=5,
        file_reference_loaded_count=3,
        file_reference_failed_count=2,
        image_attachment_count=6,
        image_attachment_loaded_count=4,
        image_attachment_failed_count=2,
        compaction_count=2,
        compaction_dropped_group_count=1,
        provider_failure_type="ProviderFailure",
        provider_failure_message="boom",
    )
    # The SUCCEEDED subset never populates the terminate-path error fields.
    assert result.error_type is None
    assert result.error_message is None


def test_succeeded_projection_without_provider_failure() -> None:
    snapshot = _populated_snapshot(provider_failure=None)

    result = build_coding_session_result(
        snapshot,
        status=HarnessStatus.SUCCEEDED,
        exit_code=0,
        started_at=_STARTED_AT,
        ended_at=_ENDED_AT,
    )

    assert result.provider_failure_type is None
    assert result.provider_failure_message is None
    assert result.image_attachment_count == 6


def test_projection_rejects_non_exact_snapshot() -> None:
    class _SnapshotSubclass(CodingSessionResultSnapshot):
        pass

    imposter = _SnapshotSubclass(
        provider_name="provider",
        model_id="model",
        messages=(),
        usage=AgentUsage(),
    )

    with pytest.raises(TypeError):
        build_coding_session_result(
            imposter,
            status=HarnessStatus.SUCCEEDED,
            exit_code=0,
            started_at=_STARTED_AT,
            ended_at=_ENDED_AT,
        )

    with pytest.raises(TypeError):
        build_coding_session_result(
            cast(CodingSessionResultSnapshot, object()),
            status=HarnessStatus.SUCCEEDED,
            exit_code=0,
            started_at=_STARTED_AT,
            ended_at=_ENDED_AT,
        )


def test_projection_rejects_unsupported_status() -> None:
    snapshot = _populated_snapshot()

    with pytest.raises(ValueError):
        build_coding_session_result(
            snapshot,
            status=HarnessStatus.RUNNING,
            exit_code=0,
            started_at=_STARTED_AT,
            ended_at=_ENDED_AT,
        )
