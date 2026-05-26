"""SDK module contract tests (E7 parity)."""

from __future__ import annotations

from pathlib import Path

import pytest

from pipy_harness import sdk
from pipy_harness.models import HarnessStatus, RunRequest, RunResult
from pipy_harness.native.fake import FakeNativeProvider


def test_sdk_exports_expected_surface() -> None:
    expected = {
        "CapturePolicy",
        "DEFAULT_NATIVE_AGENT",
        "DEFAULT_NATIVE_SLUG",
        "HarnessRunner",
        "HarnessStatus",
        "ProviderPort",
        "RunRequest",
        "RunResult",
        "StreamChunkSink",
        "make_native_run_request",
        "run_native",
    }
    assert expected.issubset(set(sdk.__all__))
    for name in expected:
        assert hasattr(sdk, name)


def test_make_native_run_request_fills_pipy_native_defaults(tmp_path: Path) -> None:
    request = sdk.make_native_run_request(goal="GOAL", cwd=tmp_path)

    assert isinstance(request, RunRequest)
    assert request.agent == sdk.DEFAULT_NATIVE_AGENT
    assert request.slug == sdk.DEFAULT_NATIVE_SLUG
    assert request.goal == "GOAL"
    assert request.cwd == tmp_path
    assert request.command == []
    assert request.capture_policy.record_file_paths is False
    assert request.native_provider is None
    assert request.native_model is None


def test_make_native_run_request_requires_non_empty_goal(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        sdk.make_native_run_request(goal="", cwd=tmp_path)


def test_make_native_run_request_requires_path_cwd() -> None:
    with pytest.raises(TypeError):
        sdk.make_native_run_request(goal="GOAL", cwd="/tmp")  # type: ignore[arg-type]


def test_run_native_with_fake_provider_returns_succeeded_run_result(tmp_path: Path) -> None:
    root = tmp_path / "sessions"
    request = sdk.make_native_run_request(
        goal="sdk smoke",
        cwd=tmp_path,
        root=root,
    )

    result = sdk.run_native(request)

    assert isinstance(result, RunResult)
    assert result.exit_code == 0


def test_run_native_uses_supplied_provider(tmp_path: Path) -> None:
    captured: list[object] = []

    class SDKObservingProvider:
        name = "fake"
        model_id = "fake-native-bootstrap"
        supports_tool_calls = False

        def complete(self, request, **_kwargs):
            captured.append(request)
            from datetime import UTC, datetime

            from pipy_harness.native.models import ProviderResult

            now = datetime.now(UTC)
            return ProviderResult(
                status=HarnessStatus.SUCCEEDED,
                provider_name=self.name,
                model_id=self.model_id,
                started_at=now,
                ended_at=now,
                final_text="sdk-final",
                usage={},
                metadata=None,
            )

    root = tmp_path / "sessions"
    request = sdk.make_native_run_request(
        goal="sdk goal", cwd=tmp_path, root=root
    )

    result = sdk.run_native(request, provider=SDKObservingProvider())

    assert len(captured) == 1
    assert result.exit_code == 0


def test_run_native_rejects_non_native_agent(tmp_path: Path) -> None:
    root = tmp_path / "sessions"
    request = RunRequest(
        agent="custom",
        slug="x",
        command=["echo", "hi"],
        cwd=tmp_path,
        goal="goal",
        root=root,
        capture_policy=sdk.CapturePolicy(),
    )

    with pytest.raises(ValueError):
        sdk.run_native(request)


def test_run_native_threads_stream_sink_to_provider(tmp_path: Path) -> None:
    captured: list[str] = []
    provider = FakeNativeProvider(
        programmable_text_chunks=("chunk-1", "chunk-2"),
    )
    root = tmp_path / "sessions"
    request = sdk.make_native_run_request(
        goal="streaming sdk goal",
        cwd=tmp_path,
        root=root,
    )

    result = sdk.run_native(
        request,
        provider=provider,
        stream_sink=captured.append,
    )

    assert captured == ["chunk-1", "chunk-2"]
    assert result.exit_code == 0
