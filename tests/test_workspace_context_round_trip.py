"""Round-trip tests for the workspace-context system-prompt wiring.

Slice 3 of the Workspace Context Loading Parity Track. These tests pin
the following invariants across the native execution surfaces
(`pipy run` one-shot and the tool-loop product REPL):

- The AGENTS.md content discovered in the workspace reaches
  `ProviderRequest.system_prompt`, so a real model would see it
  end-to-end.
- The same body never leaks into the finalized session JSONL or the
  Markdown summary.
- The per-file metadata (workspace-relative path label, sha256, byte
  length) reaches the session safe-context events that pipy-session
  surfaces.
"""

from __future__ import annotations

import io
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from pipy_harness.adapters import (
    PipyNativeAdapter,
)
from pipy_harness.adapters.native import CodingSessionAdapter
from pipy_harness.capture import CapturePolicy
from pipy_harness.models import HarnessStatus, RunRequest
from pipy_harness.native.models import (
    ProviderRequest,
    ProviderResult,
)
from pipy_harness.native.workspace_context import (
    WorkspaceInstructionDiscovery,
    discover_workspace_instructions,
)
from pipy_harness.runner import HarnessRunner

_LEAK_MARKER = "NEVER_RECORD_THIS_INSTRUCTION_BODY"
_AGENTS_MD = (
    "# Workspace Instructions\n"
    f"{_LEAK_MARKER}\n"
    "Do not record raw prompts in any session archive.\n"
)


class _CapturingFakeProvider:
    """A fake provider that records `ProviderRequest` and returns a noop intent.

    Mirrors `pipy_harness.native.fake.FakeNativeProvider` for the
    one-shot path; for tool-loop we use the real `FakeNativeProvider` with
    `programmable_tool_calls=()` so the loop terminates on the first call.
    """

    name = "capturing-fake"

    def __init__(
        self, *, model_id: str = "capturing-model", final_text: str = "done"
    ) -> None:
        self.model_id = model_id
        self.final_text = final_text
        self.captured_requests: list[ProviderRequest] = []
        self.supports_tool_calls = False

    def complete(self, request: ProviderRequest, **_kwargs: object) -> ProviderResult:
        self.captured_requests.append(request)
        now = datetime.now(UTC)
        return ProviderResult(
            status=HarnessStatus.SUCCEEDED,
            provider_name=self.name,
            model_id=self.model_id,
            started_at=now,
            ended_at=now,
            final_text=self.final_text,
            usage={"input_tokens": 1, "output_tokens": 2, "total_tokens": 3},
            metadata=None,
        )


_SHORT_LEAK_MARKER = "DO_NOT_ARCHIVE"


class _ShortMarkerLeakingProvider:
    """A hostile fake provider that returns a short leak marker — short
    enough to fit inside any reasonable length cap — under every allowlisted
    string-shaped metadata key. The enum projection in
    `_safe_provider_metadata` is the only thing that closes this vector.
    """

    name = "short-marker-fake"

    def __init__(
        self, *, model_id: str = "short-marker-model", final_text: str = "done"
    ) -> None:
        self.model_id = model_id
        self.final_text = final_text
        self.captured_requests: list[ProviderRequest] = []
        self.supports_tool_calls = False

    def complete(self, request: ProviderRequest, **_kwargs: object) -> ProviderResult:
        self.captured_requests.append(request)
        now = datetime.now(UTC)
        return ProviderResult(
            status=HarnessStatus.SUCCEEDED,
            provider_name=self.name,
            model_id=self.model_id,
            started_at=now,
            ended_at=now,
            final_text=self.final_text,
            usage={"input_tokens": 1, "output_tokens": 2, "total_tokens": 3},
            metadata={
                "response_status": _SHORT_LEAK_MARKER,
                "response_object": _SHORT_LEAK_MARKER,
                "finish_reason": _SHORT_LEAK_MARKER,
            },
        )


class _EchoingFakeProvider:
    """A hostile fake provider that echoes prompt-bearing fields back into
    `ProviderResult.metadata` so `_safe_provider_metadata` is exercised on the
    full set of request/prompt keys. Used by the regression test that pins
    the archive-privacy boundary.
    """

    name = "echoing-fake"

    def __init__(
        self, *, model_id: str = "echoing-model", final_text: str = "done"
    ) -> None:
        self.model_id = model_id
        self.final_text = final_text
        self.captured_requests: list[ProviderRequest] = []
        self.supports_tool_calls = False

    def complete(self, request: ProviderRequest, **_kwargs: object) -> ProviderResult:
        self.captured_requests.append(request)
        now = datetime.now(UTC)
        leaky_metadata = {
            # Direct top-level prompt-bearing keys.
            "system_prompt": request.system_prompt,
            "instructions": request.system_prompt,
            "input": request.system_prompt,
            "user_prompt": request.user_prompt,
            "composed_system_prompt": request.system_prompt,
            "messages": [{"role": "system", "content": request.system_prompt}],
            "tools": [{"name": "leak", "system_prompt_echo": request.system_prompt}],
            "available_tools": [request.system_prompt],
            "workspace_instruction_files": [
                {"path_label": "AGENTS.md", "leaked_content": request.system_prompt}
            ],
            "prompt": request.system_prompt,
            "request_body": {"system_prompt": request.system_prompt},
            "raw_provider_response": request.system_prompt,
            # Nested-wrapper variants that a top-level denylist would
            # not catch. The allowlist projection in
            # `_safe_provider_metadata` drops all of these.
            "request": {"system_prompt": request.system_prompt},
            "wrapper": {
                "nested": {
                    "instructions": request.system_prompt,
                }
            },
            "echoed_call": {"arguments": {"system_prompt": request.system_prompt}},
            # Allowlisted keys stuffed with prompt-bearing values so the
            # per-key projector in `_safe_provider_metadata` is exercised:
            # the booleans, ints, and short labels coerce; everything
            # else is replaced with a deterministic sentinel.
            "provider_response_store_requested": request.system_prompt,
            "http_status": request.system_prompt,
            "response_status": request.system_prompt,
            "response_object": request.system_prompt,
            "finish_reason": request.system_prompt,
        }
        return ProviderResult(
            status=HarnessStatus.SUCCEEDED,
            provider_name=self.name,
            model_id=self.model_id,
            started_at=now,
            ended_at=now,
            final_text=self.final_text,
            usage={"input_tokens": 1, "output_tokens": 2, "total_tokens": 3},
            metadata=leaky_metadata,
        )


class _CapturingToolFakeProvider:
    """A tool-capable fake provider that records `ProviderRequest`.

    Returns no tool calls on the first complete() call so the tool-loop
    finishes after one provider exchange. The captured request carries the
    composed system prompt the loop would send to a real model.
    """

    name = "capturing-fake-tool"

    def __init__(
        self, *, model_id: str = "capturing-tool-model", final_text: str = "done"
    ) -> None:
        self.model_id = model_id
        self.final_text = final_text
        self.captured_requests: list[ProviderRequest] = []
        self.supports_tool_calls = True

    def complete(self, request: ProviderRequest, **_kwargs: object) -> ProviderResult:
        self.captured_requests.append(request)
        now = datetime.now(UTC)
        return ProviderResult(
            status=HarnessStatus.SUCCEEDED,
            provider_name=self.name,
            model_id=self.model_id,
            started_at=now,
            ended_at=now,
            final_text=self.final_text,
            usage={"input_tokens": 1, "output_tokens": 2, "total_tokens": 3},
            metadata=None,
            tool_calls=(),
        )


@pytest.fixture
def workspace_with_agents_md(tmp_path: Path) -> Path:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "AGENTS.md").write_text(_AGENTS_MD, encoding="utf-8")
    return workspace


def _hermetic_loader(workspace_root: Path) -> WorkspaceInstructionDiscovery:
    """Discovery loader scoped so global root and ancestors are empty."""

    return discover_workspace_instructions(
        workspace_root,
        env={},
        home_dir=workspace_root.parent / "fake-home",
    )


def _read_jsonl(path: Path) -> list[Mapping[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _expected_agents_md_sha256() -> str:
    import hashlib

    return hashlib.sha256(_AGENTS_MD.encode("utf-8")).hexdigest()


# -- tool-loop --------------------------------------------------------------


def test_tool_loop_round_trip_AGENTS_md_reaches_system_prompt_and_archive_excludes_body(
    workspace_with_agents_md: Path, tmp_path: Path
) -> None:
    provider = _CapturingToolFakeProvider()
    adapter = CodingSessionAdapter(
        provider=provider,
        input_stream=io.StringIO("hello\n"),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
        instruction_loader=_hermetic_loader,
    )

    root = tmp_path / "sessions"
    result = HarnessRunner(
        adapter=adapter,
        id_factory=lambda: "tool-loop-round-trip",
    ).run(
        RunRequest(
            agent="pipy-native",
            slug="ws-instr-round-trip-tool-loop",
            command=[],
            cwd=workspace_with_agents_md,
            root=root,
            goal="round-trip",
            capture_policy=CapturePolicy(),
        )
    )

    assert result.exit_code == 0
    assert provider.captured_requests
    # The composed system prompt reaches the provider.
    first_system_prompt = provider.captured_requests[0].system_prompt
    assert _LEAK_MARKER in first_system_prompt
    assert "AGENTS.md" in first_system_prompt
    assert "<project_context>" in first_system_prompt

    # AdapterResult metadata exposes the per-file metadata, not the body.
    assert result.record.markdown_path is not None
    jsonl_text = result.record.jsonl_path.read_text(encoding="utf-8")
    md_text = result.record.markdown_path.read_text(encoding="utf-8")
    assert _LEAK_MARKER not in jsonl_text
    assert _LEAK_MARKER not in md_text
    assert "Do not record raw prompts" not in jsonl_text
    assert "Do not record raw prompts" not in md_text

    # Metadata fields are exposed via the dedicated workspace-context event.
    events = _read_jsonl(result.record.jsonl_path)
    instruction_events = [
        event for event in events if event["type"] == "native.workspace_context.loaded"
    ]
    assert instruction_events
    payload = instruction_events[0]["payload"]
    instruction_files = payload.get("workspace_instruction_files")
    assert isinstance(instruction_files, list)
    assert len(instruction_files) == 1
    only = instruction_files[0]
    assert only["path_label"] == "AGENTS.md"
    assert only["sha256"] == _expected_agents_md_sha256()
    assert only["byte_length"] == len(_AGENTS_MD.encode("utf-8"))
    assert only["truncated"] is False
    assert payload.get("workspace_instruction_total_byte_cap_reached") is False
    assert payload.get("repl_mode") == "tool-loop"


# -- one-shot ---------------------------------------------------------------


def test_one_shot_round_trip_AGENTS_md_reaches_system_prompt_and_archive_excludes_body(
    workspace_with_agents_md: Path, tmp_path: Path
) -> None:
    provider = _CapturingFakeProvider()
    adapter = PipyNativeAdapter(
        provider=provider,
        instruction_loader=_hermetic_loader,
    )

    root = tmp_path / "sessions"
    result = HarnessRunner(
        adapter=adapter,
        id_factory=lambda: "one-shot-round-trip",
    ).run(
        RunRequest(
            agent="pipy-native",
            slug="ws-instr-round-trip-one-shot",
            command=[],
            cwd=workspace_with_agents_md,
            root=root,
            goal="round-trip-goal",
            capture_policy=CapturePolicy(),
        )
    )

    assert result.exit_code == 0
    assert provider.captured_requests
    system_prompt = provider.captured_requests[0].system_prompt
    assert _LEAK_MARKER in system_prompt
    assert "AGENTS.md" in system_prompt

    assert result.record.markdown_path is not None
    jsonl_text = result.record.jsonl_path.read_text(encoding="utf-8")
    md_text = result.record.markdown_path.read_text(encoding="utf-8")
    assert _LEAK_MARKER not in jsonl_text
    assert _LEAK_MARKER not in md_text
    assert "Do not record raw prompts" not in jsonl_text
    assert "Do not record raw prompts" not in md_text

    events = _read_jsonl(result.record.jsonl_path)
    session_started = [
        event for event in events if event["type"] == "native.session.started"
    ]
    assert session_started
    payload = session_started[0]["payload"]
    instruction_files = payload.get("workspace_instruction_files")
    assert isinstance(instruction_files, list)
    assert len(instruction_files) == 1
    only = instruction_files[0]
    assert only["path_label"] == "AGENTS.md"
    assert only["sha256"] == _expected_agents_md_sha256()


# -- provider metadata privacy regression -----------------------------------


def test_provider_metadata_echoing_system_prompt_does_not_leak_to_archive(
    workspace_with_agents_md: Path, tmp_path: Path
) -> None:
    """Regression for the first-review Critical finding: a provider that
    echoes prompt-bearing fields back through `ProviderResult.metadata`
    must not leak the AGENTS.md body into the JSONL via
    `native.provider.completed` payloads.
    """

    provider = _EchoingFakeProvider()
    adapter = PipyNativeAdapter(
        provider=provider,
        instruction_loader=_hermetic_loader,
    )

    root = tmp_path / "sessions"
    result = HarnessRunner(
        adapter=adapter,
        id_factory=lambda: "echoing-leak-regression",
    ).run(
        RunRequest(
            agent="pipy-native",
            slug="ws-instr-echo-regression",
            command=[],
            cwd=workspace_with_agents_md,
            root=root,
            goal="echo-leak-regression",
            capture_policy=CapturePolicy(),
        )
    )

    assert result.exit_code == 0
    assert provider.captured_requests
    assert _LEAK_MARKER in provider.captured_requests[0].system_prompt

    assert result.record.markdown_path is not None
    jsonl_text = result.record.jsonl_path.read_text(encoding="utf-8")
    md_text = result.record.markdown_path.read_text(encoding="utf-8")
    assert _LEAK_MARKER not in jsonl_text
    assert _LEAK_MARKER not in md_text
    assert "Do not record raw prompts" not in jsonl_text
    assert "Do not record raw prompts" not in md_text

    # Confirm the safe payload still includes the legitimate
    # workspace_instruction_files metadata (sourced from safe_context, not from
    # provider_metadata) so the regression fix does not strip the per-file
    # path/sha256/byte_length record.
    events = _read_jsonl(result.record.jsonl_path)
    session_started = [
        event for event in events if event["type"] == "native.session.started"
    ]
    assert session_started
    payload = session_started[0]["payload"]
    instruction_files = payload.get("workspace_instruction_files")
    assert isinstance(instruction_files, list)
    assert len(instruction_files) == 1
    only = instruction_files[0]
    assert only["path_label"] == "AGENTS.md"
    assert only["sha256"] == _expected_agents_md_sha256()
    assert "leaked_content" not in only

    # Confirm the provider_metadata sub-dict projects to exactly the
    # allowlisted safe keys with safe sentinel values: any other key,
    # top level or nested, is dropped, and an allowlisted key whose value
    # has the wrong type or is too long is replaced with a deterministic
    # `<unsupported>` / `<oversized>` sentinel rather than passing through
    # the composed system prompt.
    provider_completed = [
        event for event in events if event["type"] == "native.provider.completed"
    ]
    assert provider_completed
    provider_metadata = provider_completed[0]["payload"].get("provider_metadata", {})
    assert provider_metadata == {
        "provider_response_store_requested": "<unsupported>",
        "http_status": "<unsupported>",
        "response_status": "<unsupported>",
        "response_object": "<unsupported>",
        "finish_reason": "<unsupported>",
    }
    for unsafe_key in (
        # Top-level prompt-bearing keys.
        "system_prompt",
        "instructions",
        "input",
        "user_prompt",
        "composed_system_prompt",
        "messages",
        "tools",
        "available_tools",
        "workspace_instruction_files",
        "prompt",
        "request_body",
        "raw_provider_response",
        # Nested-wrapper variants.
        "request",
        "wrapper",
        "echoed_call",
    ):
        assert unsafe_key not in provider_metadata, (
            f"unsafe key {unsafe_key!r} leaked into provider_metadata: "
            f"{provider_metadata}"
        )

    # Defensive check: confirm the leak marker does not appear in
    # `provider_metadata` even when serialized as JSON (catches any
    # value-side leak through an allowlisted key, including the
    # whitespace-collapsed truncation window in `_project_safe_label_metadata`).
    assert _LEAK_MARKER not in json.dumps(provider_metadata)


def test_short_string_in_allowlisted_metadata_field_is_replaced_with_unsupported_sentinel(
    workspace_with_agents_md: Path, tmp_path: Path
) -> None:
    """Regression for the fourth-review Critical finding: a provider that
    returns a *short* string value under `response_status` /
    `response_object` / `finish_reason` must not archive that string. Only
    the closed enum sets in `_safe_provider_metadata` survive; any other
    string — including a short literal like "DO_NOT_ARCHIVE" — projects to
    `<unsupported>`.
    """

    provider = _ShortMarkerLeakingProvider()
    adapter = PipyNativeAdapter(
        provider=provider,
        instruction_loader=_hermetic_loader,
    )

    root = tmp_path / "sessions"
    result = HarnessRunner(
        adapter=adapter,
        id_factory=lambda: "short-marker-leak-regression",
    ).run(
        RunRequest(
            agent="pipy-native",
            slug="ws-instr-short-marker-regression",
            command=[],
            cwd=workspace_with_agents_md,
            root=root,
            goal="short-marker-leak-regression",
            capture_policy=CapturePolicy(),
        )
    )

    assert result.exit_code == 0
    assert result.record.markdown_path is not None
    jsonl_text = result.record.jsonl_path.read_text(encoding="utf-8")
    md_text = result.record.markdown_path.read_text(encoding="utf-8")
    assert _SHORT_LEAK_MARKER not in jsonl_text
    assert _SHORT_LEAK_MARKER not in md_text

    events = _read_jsonl(result.record.jsonl_path)
    provider_completed = [
        event for event in events if event["type"] == "native.provider.completed"
    ]
    assert provider_completed
    provider_metadata = provider_completed[0]["payload"].get("provider_metadata", {})
    assert provider_metadata == {
        "response_status": "<unsupported>",
        "response_object": "<unsupported>",
        "finish_reason": "<unsupported>",
    }


# -- empty discovery default -------------------------------------------------


def test_session_with_no_instructions_records_empty_metadata_block(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    provider = _CapturingFakeProvider()
    adapter = PipyNativeAdapter(
        provider=provider,
        instruction_loader=_hermetic_loader,
    )

    root = tmp_path / "sessions"
    result = HarnessRunner(
        adapter=adapter,
        id_factory=lambda: "empty-instructions",
    ).run(
        RunRequest(
            agent="pipy-native",
            slug="ws-instr-empty",
            command=[],
            cwd=workspace,
            root=root,
            goal="empty",
            capture_policy=CapturePolicy(),
        )
    )

    assert result.exit_code == 0
    system_prompt = provider.captured_requests[0].system_prompt
    assert _LEAK_MARKER not in system_prompt
    assert "<project_context>" not in system_prompt

    events = _read_jsonl(result.record.jsonl_path)
    session_started = [
        event for event in events if event["type"] == "native.session.started"
    ]
    assert session_started
    payload = session_started[0]["payload"]
    assert payload.get("workspace_instruction_files") == []
    assert payload.get("workspace_instruction_total_byte_cap_reached") is False


@pytest.mark.parametrize("trusted", [True, False])
@pytest.mark.parametrize(
    ("overrides", "file_override"), [(False, False), (True, False), (True, True)]
)
def test_reload_refreshes_prompt_files_and_preserves_history(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    trusted: bool,
    overrides: bool,
    file_override: bool,
) -> None:
    from pipy_harness.native.settings import SettingsManager

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config = tmp_path / "config"
    config.mkdir()
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(config))
    monkeypatch.setenv("PIPY_NATIVE_SESSIONS_ROOT", str(tmp_path / "native-sessions"))
    project = workspace / ".pipy"
    project.mkdir()
    agents = workspace / "AGENTS.md"
    agents.write_text("OLD_CONTEXT")
    append = project / "APPEND_SYSTEM.md"
    append.write_text("OLD_PROJECT_APPEND")
    system = project / "SYSTEM.md"
    system.write_text("OLD_PROJECT_BASE")
    global_append = config / "APPEND_SYSTEM.md"
    global_append.write_text("OLD_GLOBAL_APPEND")
    global_base = config / "SYSTEM.md"
    global_base.write_text("OLD_GLOBAL_BASE")
    explicit_append = workspace / "explicit-append.md"
    explicit_append.write_text("OLD_EXPLICIT_APPEND")
    explicit_base = workspace / "explicit-base.md"
    explicit_base.write_text("EXPLICIT_BASE")

    class EditingProvider(_CapturingToolFakeProvider):
        def complete(
            self, request: ProviderRequest, **kwargs: object
        ) -> ProviderResult:
            result = super().complete(request, **kwargs)
            if len(self.captured_requests) == 1:
                agents.write_text("NEW_CONTEXT")
                append.write_text("NEW_PROJECT_APPEND")
                system.write_text("NEW_PROJECT_BASE")
                global_append.write_text("NEW_GLOBAL_APPEND")
                global_base.write_text("NEW_GLOBAL_BASE")
                explicit_append.write_text("NEW_EXPLICIT_APPEND")
                explicit_base.write_text("NEW_EXPLICIT_BASE")
            if len(self.captured_requests) == 2:
                # Deletion must remove stale text and restore discovery fallback.
                agents.unlink()
                append.unlink()
                system.unlink()
                if not trusted:
                    global_append.unlink()
                    global_base.unlink()
                if file_override:
                    explicit_base.unlink()
            return result

    provider = EditingProvider()
    adapter = CodingSessionAdapter(
        provider=provider,
        input_stream=io.StringIO("first\n/reload\nsecond\n/reload \nthird\n"),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
        instruction_loader=_hermetic_loader,
        settings_manager=SettingsManager.for_workspace(
            workspace, project_trusted=trusted
        ),
        system_prompt_source=(str(explicit_base) if file_override else "EXPLICIT_BASE")
        if overrides
        else None,
        append_system_prompt_sources=[str(explicit_append), "EXPLICIT_LITERAL"]
        if overrides
        else None,
    )
    context = adapter.prepare_session_context(workspace)
    session = adapter.build_session(context)
    result = session.run(
        workspace_root=workspace,
        input_stream=adapter.input_stream,
        output_stream=adapter.output_stream,
        error_stream=adapter.error_stream,
        system_prompt=context.system_prompt,
    )
    assert result.exit_code == 0
    assert len(provider.captured_requests) == 3
    first, second, third = [r.system_prompt for r in provider.captured_requests]
    assert "OLD_CONTEXT" in first
    assert "NEW_CONTEXT" in second
    assert "OLD_CONTEXT" not in second
    assert "NEW_CONTEXT" not in third
    scope = "EXPLICIT" if overrides else "PROJECT" if trusted else "GLOBAL"
    assert f"OLD_{scope}_APPEND" in first
    assert f"NEW_{scope}_APPEND" in second
    assert "OLD_" not in second
    if overrides:
        if file_override:
            assert "NEW_EXPLICIT_BASE" in second
            assert "NEW_EXPLICIT_BASE" not in third
            assert str(explicit_base) in third
        else:
            assert all("EXPLICIT_BASE" in prompt for prompt in (first, second, third))
        assert "NEW_EXPLICIT_APPEND" in third
        assert "EXPLICIT_LITERAL" in second
        assert "PROJECT_APPEND" not in second
    else:
        assert f"NEW_{scope}_BASE" in second
        if trusted:
            assert "NEW_GLOBAL_BASE" in third
            assert "NEW_GLOBAL_APPEND" in third
        else:
            assert "NEW_" not in third
    if not trusted:
        assert "PROJECT_APPEND" not in second
        assert "PROJECT_BASE" not in second
    # The request still carries the previous conversation after both reloads.
    from pipy_harness.native.agent import AgentUserMessage

    history = [
        message.content.value
        for message in provider.captured_requests[-1].messages
        if isinstance(message, AgentUserMessage)
    ]
    assert "first" in history
    assert "second" in history


@pytest.mark.parametrize("failure", ["decode", "unreadable", "loader"])
def test_reload_prompt_read_failure_uses_session_diagnostics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    from pipy_harness.native.settings import SettingsManager

    config = tmp_path / "config"
    config.mkdir()
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(config))
    monkeypatch.setenv("PIPY_NATIVE_SESSIONS_ROOT", str(tmp_path / "native"))
    append = config / "APPEND_SYSTEM.md"
    append.write_text("ORIGINAL_APPEND")
    read_text = Path.read_text

    def fail_read(path: Path, *args: Any, **kwargs: Any) -> str:
        if path == append:
            raise OSError("synthetic read failure")
        return read_text(path, *args, **kwargs)

    loader_calls = 0

    def load_instructions(cwd: Path) -> WorkspaceInstructionDiscovery:
        nonlocal loader_calls
        loader_calls += 1
        if failure == "loader" and loader_calls == 2:
            raise OSError("synthetic instruction-loader failure")
        return _hermetic_loader(cwd)

    class EditingProvider(_CapturingToolFakeProvider):
        def complete(
            self, request: ProviderRequest, **kwargs: object
        ) -> ProviderResult:
            result = super().complete(request, **kwargs)
            if len(self.captured_requests) == 1:
                if failure == "decode":
                    append.write_bytes(b"\xff")
                elif failure == "unreadable":
                    monkeypatch.setattr(Path, "read_text", fail_read)
                else:
                    append.write_text("NEW_APPEND")
            return result

    provider = EditingProvider()
    errors = io.StringIO()
    adapter = CodingSessionAdapter(
        provider=provider,
        instruction_loader=load_instructions,
        settings_manager=SettingsManager.for_workspace(tmp_path, project_trusted=True),
    )
    context = adapter.prepare_session_context(tmp_path)
    result = adapter.build_session(context).run(
        workspace_root=tmp_path,
        input_stream=io.StringIO("first\n/reload\nsecond\n"),
        output_stream=io.StringIO(),
        error_stream=errors,
        system_prompt=context.system_prompt,
    )
    assert result.exit_code == 0
    assert len(provider.captured_requests) == 2
    if failure in {"decode", "loader"}:
        assert "ORIGINAL_APPEND" in provider.captured_requests[1].system_prompt
        assert "keeping previous prompt inputs" in errors.getvalue()
        assert "reloaded settings, keybindings, and resources" in errors.getvalue()
        assert loader_calls == {"decode": 1, "loader": 2}[failure]
    else:
        # Preserve the existing unreadable explicit/discovered input fallback.
        assert str(append) in provider.captured_requests[1].system_prompt
        assert "synthetic read failure" in errors.getvalue()


@pytest.mark.parametrize("fixed_template", [False, True])
def test_reload_preserves_disabled_context_or_fixed_template(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fixed_template: bool
) -> None:
    from dataclasses import replace

    from pipy_harness.native.system_prompt_sections import SystemPromptTemplate

    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("PIPY_NATIVE_SESSIONS_ROOT", str(tmp_path / "native"))
    (tmp_path / "AGENTS.md").write_text("EXCLUDED_CONTEXT")
    provider = _CapturingToolFakeProvider()
    # Default adapter loader is empty, like --no-context-files.
    adapter = CodingSessionAdapter(provider=provider)
    context = adapter.prepare_session_context(tmp_path)
    if fixed_template:
        context = replace(
            context,
            system_prompt_sections=SystemPromptTemplate(
                cwd=str(tmp_path),
                project_context="FIXED_CONTEXT",
                addendum="FIXED_APPEND",
            ),
        )
    result = adapter.build_session(context).run(
        workspace_root=tmp_path,
        input_stream=io.StringIO("first\n/reload\nsecond\n"),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
        system_prompt=context.system_prompt,
    )
    assert result.exit_code == 0
    first, second = provider.captured_requests
    assert first.system_prompt == second.system_prompt
    assert "EXCLUDED_CONTEXT" not in second.system_prompt
    if fixed_template:
        assert "FIXED_CONTEXT" in second.system_prompt
        assert "FIXED_APPEND" in second.system_prompt
