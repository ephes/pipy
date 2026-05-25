"""Round-trip tests for the workspace-context system-prompt wiring.

Slice 3 of the Workspace Context Loading Parity Track. These tests pin
the following invariants across the three native execution surfaces
(`pipy run` one-shot, `--repl-mode no-tool`, `--repl-mode tool-loop`):

- The AGENTS.md content discovered in the workspace reaches
  `ProviderRequest.system_prompt`, so a real model would see it
  end-to-end.
- The same body never leaks into the finalized session JSONL, the
  Markdown summary, or the opt-in `--archive-transcript` sidecar.
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
    PipyNativeReplAdapter,
    PipyNativeToolReplAdapter,
)
from pipy_harness.capture import CapturePolicy
from pipy_harness.models import HarnessStatus, RunRequest
from pipy_harness.native.models import (
    ProviderRequest,
    ProviderResult,
)
from pipy_harness.native.transcripts import TranscriptSink
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

    Mirrors `pipy_harness.native.fake.FakeNativeProvider` for the no-tool /
    one-shot path; for tool-loop we use the real `FakeNativeProvider` with
    `programmable_tool_calls=()` so the loop terminates on the first call.
    """

    name = "capturing-fake"

    def __init__(self, *, model_id: str = "capturing-model", final_text: str = "done") -> None:
        self.model_id = model_id
        self.final_text = final_text
        self.captured_requests: list[ProviderRequest] = []
        self.supports_tool_calls = False

    def complete(self, request: ProviderRequest) -> ProviderResult:
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


class _EchoingFakeProvider:
    """A hostile fake provider that echoes prompt-bearing fields back into
    `ProviderResult.metadata` so `_safe_provider_metadata` is exercised on the
    full set of request/prompt keys. Used by the regression test that pins
    the archive-privacy boundary.
    """

    name = "echoing-fake"

    def __init__(self, *, model_id: str = "echoing-model", final_text: str = "done") -> None:
        self.model_id = model_id
        self.final_text = final_text
        self.captured_requests: list[ProviderRequest] = []
        self.supports_tool_calls = False

    def complete(self, request: ProviderRequest) -> ProviderResult:
        self.captured_requests.append(request)
        now = datetime.now(UTC)
        leaky_metadata = {
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
            "provider_response_store_requested": False,
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

    def __init__(self, *, model_id: str = "capturing-tool-model", final_text: str = "done") -> None:
        self.model_id = model_id
        self.final_text = final_text
        self.captured_requests: list[ProviderRequest] = []
        self.supports_tool_calls = True

    def complete(self, request: ProviderRequest) -> ProviderResult:
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
    adapter = PipyNativeToolReplAdapter(
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
    assert "## Workspace Instructions" in first_system_prompt

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
        event
        for event in events
        if event["type"] == "native.workspace_context.loaded"
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


# -- no-tool REPL -----------------------------------------------------------


def test_no_tool_repl_round_trip_AGENTS_md_reaches_system_prompt_and_archive_excludes_body(
    workspace_with_agents_md: Path, tmp_path: Path
) -> None:
    provider = _CapturingFakeProvider()
    adapter = PipyNativeReplAdapter(
        provider=provider,
        input_stream=io.StringIO("hello\n/exit\n"),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
        instruction_loader=_hermetic_loader,
    )

    root = tmp_path / "sessions"
    result = HarnessRunner(
        adapter=adapter,
        id_factory=lambda: "no-tool-repl-round-trip",
    ).run(
        RunRequest(
            agent="pipy-native",
            slug="ws-instr-round-trip-no-tool",
            command=[],
            cwd=workspace_with_agents_md,
            root=root,
            goal="round-trip",
            capture_policy=CapturePolicy(),
        )
    )

    assert result.exit_code == 0
    assert provider.captured_requests
    first_system_prompt = provider.captured_requests[0].system_prompt
    assert _LEAK_MARKER in first_system_prompt
    assert "AGENTS.md" in first_system_prompt

    assert result.record.markdown_path is not None
    jsonl_text = result.record.jsonl_path.read_text(encoding="utf-8")
    md_text = result.record.markdown_path.read_text(encoding="utf-8")
    assert _LEAK_MARKER not in jsonl_text
    assert _LEAK_MARKER not in md_text
    assert "Do not record raw prompts" not in jsonl_text
    assert "Do not record raw prompts" not in md_text

    events = _read_jsonl(result.record.jsonl_path)
    session_completed = [
        event for event in events if event["type"] == "native.session.completed"
    ]
    assert session_completed
    payload = session_completed[0]["payload"]
    instruction_files = payload.get("workspace_instruction_files")
    assert isinstance(instruction_files, list)
    assert len(instruction_files) == 1
    only = instruction_files[0]
    assert only["path_label"] == "AGENTS.md"
    assert only["sha256"] == _expected_agents_md_sha256()
    assert only["byte_length"] == len(_AGENTS_MD.encode("utf-8"))


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


# -- transcript sidecar privacy --------------------------------------------


def test_tool_loop_transcript_sidecar_does_not_contain_instruction_body(
    workspace_with_agents_md: Path, tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv(
        "PIPY_TRANSCRIPT_DIR", str(tmp_path / "transcripts")
    )
    sink = TranscriptSink(directory=tmp_path / "transcripts")
    provider = _CapturingToolFakeProvider()
    adapter = PipyNativeToolReplAdapter(
        provider=provider,
        input_stream=io.StringIO("hello\n"),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
        transcript_sink=sink,
        instruction_loader=_hermetic_loader,
    )

    root = tmp_path / "sessions"
    HarnessRunner(
        adapter=adapter,
        id_factory=lambda: "tool-loop-transcript",
    ).run(
        RunRequest(
            agent="pipy-native",
            slug="ws-instr-transcript",
            command=[],
            cwd=workspace_with_agents_md,
            root=root,
            goal="transcript",
            capture_policy=CapturePolicy(),
        )
    )

    assert sink.path.exists()
    sidecar_text = sink.path.read_text(encoding="utf-8")
    assert _LEAK_MARKER not in sidecar_text
    assert "Do not record raw prompts" not in sidecar_text


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

    # Confirm the provider_metadata sub-dict (where unsafe keys would have
    # leaked) does not carry any of the echoed prompt fields.
    provider_completed = [
        event for event in events if event["type"] == "native.provider.completed"
    ]
    assert provider_completed
    provider_metadata = provider_completed[0]["payload"].get("provider_metadata", {})
    for unsafe_key in (
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
    ):
        assert unsafe_key not in provider_metadata, (
            f"unsafe key {unsafe_key!r} leaked into provider_metadata: "
            f"{provider_metadata}"
        )


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
    assert "## Workspace Instructions" not in system_prompt

    events = _read_jsonl(result.record.jsonl_path)
    session_started = [
        event for event in events if event["type"] == "native.session.started"
    ]
    assert session_started
    payload = session_started[0]["payload"]
    assert payload.get("workspace_instruction_files") == []
    assert payload.get("workspace_instruction_total_byte_cap_reached") is False
