"""Product-path tests for resource loading in the no-tool REPL.

These drive `NativeNoToolReplSession.run` end to end through the real
dispatch boundary: listing/loading a skill, running a prompt template
with arguments, running a custom slash command, rejecting an unknown
resource (fail closed, no provider turn), and asserting the metadata
archive never receives resource bodies or expanded prompts.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path

from pipy_harness.models import HarnessStatus
from pipy_harness.native import NativeRunInput, ProviderRequest, ProviderResult
from pipy_harness.native.session import (
    NativeNoToolReplSession,
    SYSTEM_PROMPT_ID,
    SYSTEM_PROMPT_VERSION,
)


class RecordingSink:
    def __init__(self) -> None:
        self.events: list[tuple[str, str, dict[str, object] | None]] = []

    def emit(
        self,
        event_type: str,
        *,
        summary: str,
        payload: Mapping[str, object] | None = None,
    ) -> None:
        self.events.append(
            (event_type, summary, dict(payload) if payload is not None else None)
        )


@dataclass
class SequentialCapturingProvider:
    results: list[ProviderResult]
    captured_requests: list[ProviderRequest] | None = None

    @property
    def name(self) -> str:
        return "capturing-fake"

    @property
    def model_id(self) -> str:
        return "capturing-model"

    @property
    def supports_tool_calls(self) -> bool:
        return False

    def complete(self, request: ProviderRequest, **_kwargs: object) -> ProviderResult:
        if self.captured_requests is None:
            self.captured_requests = []
        self.captured_requests.append(request)
        if not self.results:
            raise RuntimeError("unexpected extra provider call")
        return self.results.pop(0)


def _result(final_text: str) -> ProviderResult:
    now = datetime(2026, 5, 3, 12, 0, tzinfo=UTC)
    return ProviderResult(
        status=HarnessStatus.SUCCEEDED,
        provider_name="capturing-fake",
        model_id="capturing-model",
        started_at=now,
        ended_at=now,
        final_text=final_text,
        usage=None,
        metadata=None,
    )


def _write(directory: Path, filename: str, *, name: str, description: str, body: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    text = f"---\nname: {name}\ndescription: {description}\n---\n\n{body}"
    (directory / filename).write_text(text, encoding="utf-8")


def _seed_resources(tmp_path: Path) -> None:
    pipy = tmp_path / ".pipy"
    _write(
        pipy / "skills",
        "lint.md",
        name="lint",
        description="Run linters",
        body="SKILL_BODY_apply_lint_rules\n",
    )
    _write(
        pipy / "templates",
        "review.md",
        name="review",
        description="Review the diff",
        body="TEMPLATE_review $ARGUMENTS now\n",
    )
    _write(
        pipy / "commands",
        "deploy.md",
        name="deploy",
        description="Deploy summary",
        body="COMMAND_summarize_deploy_for $ARGUMENTS\n",
    )


def _run(tmp_path: Path, monkeypatch, script: str) -> tuple[
    SequentialCapturingProvider, RecordingSink, StringIO, StringIO
]:
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "empty-global"))
    provider = SequentialCapturingProvider(
        results=[
            _result("LINT_ANSWER"),
            _result("REVIEW_ANSWER"),
            _result("DEPLOY_ANSWER"),
        ]
    )
    sink = RecordingSink()
    output_stream = StringIO()
    error_stream = StringIO()
    NativeNoToolReplSession(provider=provider, max_turns=8).run(
        NativeRunInput(
            goal="Native no-tool REPL resources",
            cwd=tmp_path,
            provider_name=provider.name,
            model_id=provider.model_id,
            system_prompt_id=SYSTEM_PROMPT_ID,
            system_prompt_version=SYSTEM_PROMPT_VERSION,
        ),
        sink,
        input_stream=StringIO(script),
        output_stream=output_stream,
        error_stream=error_stream,
    )
    return provider, sink, output_stream, error_stream


def test_no_tool_lists_loads_runs_and_rejects(tmp_path, monkeypatch):
    _seed_resources(tmp_path)
    script = (
        "/skill\n"
        "/skill lint\n"
        "/template review the auth module\n"
        "/deploy staging\n"
        "/skill nope\n"
        "/exit\n"
    )
    provider, sink, output_stream, error_stream = _run(tmp_path, monkeypatch, script)

    # Exactly three provider turns: the two list/reject commands issue none.
    assert provider.captured_requests is not None
    prompts = [request.user_prompt for request in provider.captured_requests]
    assert len(prompts) == 3
    assert "SKILL_BODY_apply_lint_rules" in prompts[0]
    assert prompts[1].strip() == "TEMPLATE_review the auth module now"
    assert prompts[2].strip() == "COMMAND_summarize_deploy_for staging"

    err = error_stream.getvalue()
    # Listing surfaced the skill name but never the body.
    assert "lint: Run linters" in err
    assert "SKILL_BODY_apply_lint_rules" not in err
    # The unknown skill failed closed.
    assert "no skill named 'nope'" in err

    # Provider answers were printed to stdout.
    out = output_stream.getvalue()
    assert "LINT_ANSWER" in out
    assert "REVIEW_ANSWER" in out
    assert "DEPLOY_ANSWER" in out


def test_no_tool_archive_records_only_safe_resource_metadata(tmp_path, monkeypatch):
    _seed_resources(tmp_path)
    script = "/skill lint\n/template review X\n/deploy Y\n/skill nope\n/exit\n"
    _provider, sink, _out, _err = _run(tmp_path, monkeypatch, script)

    serialized = json.dumps([event[2] for event in sink.events], sort_keys=True)
    # No resource body or expanded prompt text reaches the archive.
    for forbidden in (
        "SKILL_BODY_apply_lint_rules",
        "TEMPLATE_review",
        "COMMAND_summarize_deploy_for",
        "Run linters",
        "Review the diff",
        "Deploy summary",
    ):
        assert forbidden not in serialized, forbidden

    invoked = [
        payload
        for event_type, _s, payload in sink.events
        if event_type == "native.resource.invoked"
    ]
    assert len(invoked) == 3
    kinds = {payload["resource_kind"] for payload in invoked}
    assert kinds == {"skill", "prompt_template", "custom_command"}
    for payload in invoked:
        assert set(["name", "path_label", "sha256", "byte_length", "truncated"]).issubset(
            payload.keys()
        )
        assert isinstance(payload["sha256"], str) and len(payload["sha256"]) == 64

    rejected = [
        payload
        for event_type, _s, payload in sink.events
        if event_type == "native.resource.rejected"
    ]
    assert len(rejected) == 1
    assert rejected[0]["resource_label"] == "skill:nope"

    completed = [
        payload
        for event_type, _s, payload in sink.events
        if event_type == "native.session.completed"
    ][0]
    assert completed["resource_invocation_count"] == 3
