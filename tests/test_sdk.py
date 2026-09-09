"""SDK module contract tests (E7 parity)."""

from __future__ import annotations

from typing import get_type_hints

import pytest

import pipy_harness
from pipy_harness import models, sdk
from pipy_harness.models import AdapterResult, HarnessStatus, RunResult
from pipy_harness.native import NativeRunOutput, ProviderResult
from pipy_harness.native.coding.result import CodingSessionResult
from pipy_harness.status import HarnessStatus as CanonicalHarnessStatus
from pipy_session.recorder import SessionRecord


def test_sdk_exports_expected_surface() -> None:
    expected = {
        "AgentEvent",
        "AgentEventSink",
        "CodingSessionResultSnapshot",
        "ProductSession",
        "ProductSessionTarget",
        "ProductSessionTransitionError",
        "ProductSessionTransitionFailure",
        "ProductSessionTransitionResult",
        "ProviderPort",
        "create_product_session",
        "open_product_session",
    }
    assert set(sdk.__all__) == expected
    for name in expected:
        assert hasattr(sdk, name)


@pytest.mark.parametrize(
    "name",
    (
        "CapturePolicy",
        "DEFAULT_NATIVE_AGENT",
        "DEFAULT_NATIVE_SLUG",
        "HarnessRunner",
        "HarnessStatus",
        "RunRequest",
        "RunResult",
        "StreamChunkSink",
        "make_native_run_request",
        "run_native",
    ),
)
def test_sdk_retired_names_are_absent_and_cannot_be_imported(name: str) -> None:
    assert not hasattr(sdk, name)
    with pytest.raises(ImportError):
        exec(f"from pipy_harness.sdk import {name}", {})


def test_harness_status_public_exports_keep_canonical_identity() -> None:
    assert HarnessStatus is CanonicalHarnessStatus
    assert models.HarnessStatus is CanonicalHarnessStatus
    assert pipy_harness.HarnessStatus is CanonicalHarnessStatus


def test_public_run_model_type_hints_resolve_at_runtime() -> None:
    assert get_type_hints(AdapterResult)["status"] is CanonicalHarnessStatus
    assert get_type_hints(RunResult)["status"] is CanonicalHarnessStatus
    assert get_type_hints(RunResult)["record"] is SessionRecord
    assert get_type_hints(ProviderResult)["status"] is CanonicalHarnessStatus
    assert get_type_hints(NativeRunOutput)["status"] is CanonicalHarnessStatus
    assert get_type_hints(CodingSessionResult)["status"] is CanonicalHarnessStatus
