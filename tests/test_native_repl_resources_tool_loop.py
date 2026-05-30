"""Product-path tests for resource loading in the bounded tool loop.

These drive `NativeToolReplSession.run` with captured streams (no TTY,
so no terminal UI) through the real dispatch boundary, proving that:

- a skill / template / custom command produces the intended bounded
  provider-visible message (the expanded/instruction text);
- listing and rejection issue no provider turn and fail closed;
- prompt history, the transcript sidecar, and the returned metadata
  result never receive the resource body or expanded prompt.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path

from pipy_harness.models import HarnessStatus
from pipy_harness.native import ProviderRequest, ProviderResult
from pipy_harness.native.prompt_history import PromptHistoryStore
from pipy_harness.native.resources import WorkspaceResources
from pipy_harness.native.tool_loop_session import (
    NativeToolReplSession,
    _tool_loop_command_names,
)
from pipy_harness.native.transcripts import TranscriptSink


@dataclass
class CapturingToolProvider:
    """Tool-capable provider that records each request and returns text."""

    final_text: str = "OK"
    requests: list[ProviderRequest] = field(default_factory=list)

    @property
    def name(self) -> str:
        return "capturing-tool-fake"

    @property
    def model_id(self) -> str:
        return "capturing-tool-model"

    @property
    def supports_tool_calls(self) -> bool:
        return True

    def complete(self, request: ProviderRequest, **_kwargs: object) -> ProviderResult:
        self.requests.append(request)
        now = datetime(2026, 5, 3, 12, 0, tzinfo=UTC)
        return ProviderResult(
            status=HarnessStatus.SUCCEEDED,
            provider_name=self.name,
            model_id=self.model_id,
            started_at=now,
            ended_at=now,
            final_text=self.final_text,
            usage=None,
            metadata=None,
            tool_calls=(),
        )


def _write(directory: Path, filename: str, *, name: str, description: str, body: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    text = f"---\nname: {name}\ndescription: {description}\n---\n\n{body}"
    (directory / filename).write_text(text, encoding="utf-8")


def _seed(tmp_path: Path) -> None:
    pipy = tmp_path / ".pipy"
    _write(
        pipy / "skills",
        "lint.md",
        name="lint",
        description="Run linters",
        body="SKILL_BODY_lint_rules\n",
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
        body="COMMAND_deploy_for $ARGUMENTS\n",
    )


def _run(tmp_path, monkeypatch, script, *, history=None, sink=None):
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "empty-global"))
    provider = CapturingToolProvider()
    session = NativeToolReplSession(
        provider=provider,
        tool_registry={},
        prompt_history_store=history,
        transcript_sink=sink,
    )
    result = session.run(
        workspace_root=tmp_path,
        input_stream=StringIO(script),
        output_stream=StringIO(),
        error_stream=StringIO(),
    )
    return provider, result


def test_tool_loop_runs_skill_template_command_and_lists_and_rejects(tmp_path, monkeypatch):
    _seed(tmp_path)
    script = (
        "/skill\n"
        "/skill lint\n"
        "/template review the auth module\n"
        "/deploy staging\n"
        "/skill nope\n"
    )
    provider, result = _run(tmp_path, monkeypatch, script)

    # Three runs => three provider turns; list + reject issue none.
    user_prompts = [request.user_prompt for request in provider.requests]
    assert len(user_prompts) == 3
    assert "SKILL_BODY_lint_rules" in user_prompts[0]
    assert user_prompts[1].strip() == "TEMPLATE_review the auth module now"
    assert user_prompts[2].strip() == "COMMAND_deploy_for staging"

    # The provider also sees the bounded text as the latest UserMessage.
    last_messages = provider.requests[2].messages
    assert last_messages[-1].content.strip() == "COMMAND_deploy_for staging"

    assert result.resource_invocation_count == 3
    assert result.user_turn_count == 3


def test_tool_loop_resource_runs_never_touch_history_or_sidecar(
    tmp_path, monkeypatch
):
    _seed(tmp_path)
    history = PromptHistoryStore(path=tmp_path / "history.txt")
    history.set_enabled(True)
    sink = TranscriptSink(transcript_id="resource-test", directory=tmp_path)
    script = "/skill lint\n/template review X\n/deploy Y\nplain prompt\n"
    _provider, result = _run(
        tmp_path, monkeypatch, script, history=history, sink=sink
    )

    # Only the genuine prompt is persisted to local history; resource
    # invocations are not.
    entries = list(history.entries())
    assert entries == ["plain prompt"]

    # The sidecar records the genuine prompt only — no resource bodies or
    # expanded text.
    transcript = sink.path.read_text(encoding="utf-8")
    assert "plain prompt" in transcript
    for forbidden in (
        "SKILL_BODY_lint_rules",
        "TEMPLATE_review",
        "COMMAND_deploy_for",
    ):
        assert forbidden not in transcript, forbidden

    # The returned metadata result carries only the counter — no bodies.
    assert result.resource_invocation_count == 3
    serialized = str(result)
    for forbidden in ("SKILL_BODY_lint_rules", "TEMPLATE_review", "COMMAND_deploy_for"):
        assert forbidden not in serialized


def test_tool_loop_menu_command_set_is_honest(tmp_path, monkeypatch):
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "empty-global"))
    _seed(tmp_path)
    resources = WorkspaceResources.discover(
        tmp_path, config_home_env={}, home_dir=tmp_path
    )
    names = _tool_loop_command_names(resources)
    # Resource entry points and the discovered custom command are advertised.
    for executable in ("/help", "/model", "/skill", "/template", "/deploy"):
        assert executable in names
    # No-tool-only commands never appear in the tool-loop menu.
    for absent in ("/read", "/ask-file", "/propose-file", "/apply-proposal", "/verify"):
        assert absent not in names
