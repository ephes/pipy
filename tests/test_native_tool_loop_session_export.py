"""Characterization contracts for the native ``/export`` command."""

from __future__ import annotations

import base64
import io
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import TextIO

import pytest

from pipy_harness.adapters import PipyNativeToolReplAdapter
from pipy_harness.models import HarnessStatus, RunRequest
from pipy_harness.native import (
    NativeToolReplResult,
    NativeToolReplSession,
    ProviderRequest,
    ProviderResult,
)
from pipy_harness.native.agent import (
    AgentAssistantMessage,
    AgentEvent,
    AgentUserMessage,
    ProductContent,
)
from pipy_harness.native.coding.input_queue import CodingInputQueue
from pipy_harness.native.export_distribution import NativeExportError
from pipy_harness.native.prompt_history import PromptHistoryStore
from pipy_harness.native.session_tree import NativeSessionTree
from pipy_harness.native.settings import SettingsManager
from pipy_harness.runner import HarnessRunner

_CONTENT_MARKER = "PIPY_PRIVATE_EXPORT_CONTENT_41d8e37a"
_ALT_CONTENT_MARKER = "PIPY_PRIVATE_EXPORT_ALTERNATE_9a72c05b"
_PATH_MARKER = "PIPY_PRIVATE_EXPORT_PATH_c43f167e"
_CREDENTIAL_MARKER = "ghp_" + "EXPORTAUTHMARKER123456789"


class _RecordingProvider:
    name = "fake"
    model_id = "fake-native-bootstrap"
    supports_tool_calls = True

    def __init__(self) -> None:
        self.requests: list[ProviderRequest] = []

    def complete(self, request: ProviderRequest, **_kwargs: object) -> ProviderResult:
        self.requests.append(request)
        now = datetime.now(UTC)
        return ProviderResult(
            status=HarnessStatus.SUCCEEDED,
            provider_name=self.name,
            model_id=self.model_id,
            started_at=now,
            ended_at=now,
            final_text="unexpected provider turn",
            tool_calls=(),
        )


class _RecordingAgentSink:
    def __init__(self) -> None:
        self.events: list[AgentEvent] = []

    def emit(self, event: AgentEvent) -> None:
        self.events.append(event)


def _workspace(tmp_path: Path) -> Path:
    cwd = tmp_path / "workspace"
    cwd.mkdir()
    return cwd


def _settings(tmp_path: Path, cwd: Path) -> SettingsManager:
    return SettingsManager(
        global_path=tmp_path / "config" / "settings.json",
        project_path=cwd / ".pipy" / "settings.json",
        env={},
        overrides={"quietStartup": True},
        project_trusted=False,
    )


def _branched_tree(tmp_path: Path, *, persist: bool = True) -> NativeSessionTree:
    cwd = _workspace(tmp_path)
    tree = NativeSessionTree.create(
        cwd,
        session_dir=tmp_path / "product-sessions",
        persist=persist,
        session_id="export-characterization",
    )
    tree.append_message(AgentUserMessage(content=ProductContent("ROOT prompt")))
    branch_point = tree.append_message(
        AgentAssistantMessage(content=ProductContent("ROOT answer"))
    )
    tree.append_message(
        AgentUserMessage(
            content=ProductContent(
                f"MAIN {_CONTENT_MARKER} password=private-value {_CREDENTIAL_MARKER}"
            )
        )
    )
    tree.append_message(AgentAssistantMessage(content=ProductContent("MAIN answer")))
    main_leaf = tree.get_leaf_id()
    tree.branch(branch_point.id)
    tree.append_message(
        AgentUserMessage(content=ProductContent(f"ALT {_ALT_CONTENT_MARKER}"))
    )
    tree.append_message(AgentAssistantMessage(content=ProductContent("ALT answer")))
    assert main_leaf is not None
    tree.branch(main_leaf)
    return tree


def _run_captured(
    session: NativeToolReplSession,
    cwd: Path,
    commands: str,
    *,
    system_prompt: str = "",
) -> tuple[NativeToolReplResult, str]:
    errors = io.StringIO()
    result = session.run(
        workspace_root=cwd,
        input_stream=io.StringIO(commands),
        output_stream=io.StringIO(),
        error_stream=errors,
        system_prompt=system_prompt,
    )
    return result, errors.getvalue()


def _decode_html_payload(path: Path) -> dict[str, object]:
    html = path.read_text(encoding="utf-8")
    marker = '<script id="pipy-session-data" type="application/pipy-session+base64">'
    encoded = html.split(marker, 1)[1].split("</script>", 1)[0]
    payload = json.loads(base64.b64decode(encoded).decode("utf-8"))
    assert isinstance(payload, dict)
    return payload


def _jsonl_objects(path: Path) -> list[dict[str, object]]:
    objects = [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
    ]
    assert all(isinstance(item, dict) for item in objects)
    return objects


def test_composition_preserves_raw_bubbles_and_export_path_routing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import pipy_harness.native.tool_loop_session as loop_module

    tree = _branched_tree(tmp_path)
    cwd = Path(tree.get_header().cwd)
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    absolute = tmp_path / "absolute.JSONL"
    bubbles: list[str] = []
    footer_usage: list[bool] = []

    def render_user_message(_renderer: object, text: str) -> None:
        bubbles.append(text)

    def footer(
        _session: NativeToolReplSession,
        _error_stream: TextIO,
        **kwargs: object,
    ) -> None:
        footer_usage.append(kwargs.get("usage_snapshot") is not None)

    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setattr(
        loop_module._ToolLoopRenderer, "render_user_message", render_user_message
    )
    monkeypatch.setattr(NativeToolReplSession, "_print_footer", footer)
    commands = (
        ' \t/export "quoted export.jsonl" ignored \t\n'
        "/export 'quoted page.html' trailing\n"
        "/export plain.jsonl more\n"
        f"/export {absolute}\n"
        "/export compound.jsonl.backup\n"
        "/export ~/home-relative.jsonl\n"
        "/export\n"
    )

    result, rendered = _run_captured(
        NativeToolReplSession(
            provider=_RecordingProvider(),
            native_session=tree,
            settings_manager=_settings(tmp_path, cwd),
            tool_registry={},
        ),
        cwd,
        commands,
    )

    assert tree.path is not None
    default_path = cwd / f"pipy-session-{tree.path.stem}.html"
    assert bubbles == commands.splitlines()
    assert footer_usage == [True] + [False] * 7
    assert result.user_turn_count == 0
    assert result.tool_invocation_count == 0
    assert (cwd / "quoted export.jsonl").is_file()
    assert (cwd / "quoted page.html").is_file()
    assert (cwd / "plain.jsonl").is_file()
    assert absolute.is_file()
    assert (cwd / "compound.jsonl.backup").is_file()
    assert (fake_home / "home-relative.jsonl").is_file()
    assert default_path.is_file()
    assert rendered.count("exported native session JSONL") == 4
    assert rendered.count("exported native session HTML") == 3


def test_exports_preserve_full_product_content_shape_and_redact_credentials(
    tmp_path: Path,
) -> None:
    tree = _branched_tree(tmp_path)
    cwd = Path(tree.get_header().cwd)
    html_path = cwd / "full-tree.html"
    jsonl_path = cwd / "active-branch.jsonl"

    _run_captured(
        NativeToolReplSession(
            provider=_RecordingProvider(),
            native_session=tree,
            settings_manager=_settings(tmp_path, cwd),
            tool_registry={},
        ),
        cwd,
        f"/export {html_path.name}\n/export {jsonl_path.name}\n",
        system_prompt="EXPORT SYSTEM PROMPT",
    )

    html_payload = _decode_html_payload(html_path)
    html_serialized = json.dumps(html_payload, sort_keys=True)
    jsonl_objects = _jsonl_objects(jsonl_path)
    jsonl_serialized = json.dumps(jsonl_objects, sort_keys=True)
    entries = html_payload["entries"]
    assert isinstance(entries, list)
    assert len(entries) == len(tree.get_entries())
    assert html_payload["leafId"] == tree.get_leaf_id()
    assert html_payload["systemPrompt"] == "EXPORT SYSTEM PROMPT"
    assert html_payload["tools"] == []
    assert _CONTENT_MARKER in html_serialized
    assert _ALT_CONTENT_MARKER in html_serialized
    assert _CONTENT_MARKER in jsonl_serialized
    assert _ALT_CONTENT_MARKER not in jsonl_serialized
    assert "private-value" not in html_serialized
    assert "private-value" not in jsonl_serialized
    assert _CREDENTIAL_MARKER not in html_serialized
    assert _CREDENTIAL_MARKER not in jsonl_serialized
    assert "[REDACTED]" in html_serialized
    assert "[REDACTED]" in jsonl_serialized
    assert [item.get("parentId") for item in jsonl_objects[1:]] == [
        None,
        *[item.get("id") for item in jsonl_objects[1:-1]],
    ]


@pytest.mark.parametrize(
    ("persist", "command", "expected"),
    [
        (True, "/export", "Nothing to export yet."),
        (True, "/export empty.jsonl", "Nothing to export yet."),
        (False, "/export", "Cannot export in-memory session to HTML."),
    ],
)
def test_empty_and_in_memory_exports_report_controlled_errors_then_footer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    persist: bool,
    command: str,
    expected: str,
) -> None:
    cwd = _workspace(tmp_path)
    tree = NativeSessionTree.create(
        cwd,
        session_dir=tmp_path / "product-sessions",
        persist=persist,
    )
    if not persist:
        tree.append_message(AgentUserMessage(content=ProductContent("in memory")))
    trace: list[str] = []

    def footer(
        _session: NativeToolReplSession,
        error_stream: TextIO,
        **_kwargs: object,
    ) -> None:
        trace.append("footer")
        print("FOOTER", file=error_stream)

    monkeypatch.setattr(NativeToolReplSession, "_print_footer", footer)
    _result, rendered = _run_captured(
        NativeToolReplSession(
            provider=_RecordingProvider(),
            native_session=tree,
            settings_manager=_settings(tmp_path, cwd),
            tool_registry={},
        ),
        cwd,
        f"{command}\n",
    )

    assert trace == ["footer", "footer"]
    assert rendered.index(f"pipy: {expected}") < rendered.rindex("FOOTER")


def test_controlled_export_error_is_terminal_sanitized_before_standard_footer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import pipy_harness.native.repl.session_transfer as transfer_module

    tree = _branched_tree(tmp_path)
    cwd = Path(tree.get_header().cwd)
    footer_calls: list[str] = []

    def fail_export(*_args: object, **_kwargs: object) -> Path:
        raise NativeExportError("unsafe\x1b[31m path\nsecond line")

    def footer(
        _session: NativeToolReplSession,
        error_stream: TextIO,
        **_kwargs: object,
    ) -> None:
        footer_calls.append("footer")
        print("STANDARD FOOTER", file=error_stream)

    monkeypatch.setattr(transfer_module, "export_native_session_to_html", fail_export)
    monkeypatch.setattr(NativeToolReplSession, "_print_footer", footer)

    _result, rendered = _run_captured(
        NativeToolReplSession(
            provider=_RecordingProvider(),
            native_session=tree,
            settings_manager=_settings(tmp_path, cwd),
            tool_registry={},
        ),
        cwd,
        "/export controlled.html\n",
    )

    assert footer_calls == ["footer", "footer"]
    assert "\x1b" not in rendered
    assert "pipy: unsafe [31m path\nsecond line" in rendered
    assert rendered.index("pipy: unsafe") < rendered.rindex("STANDARD FOOTER")


@pytest.mark.parametrize("retained_body", ["PARTIAL", "COMPLETE"])
def test_uncontrolled_export_failure_cuts_off_diagnostic_and_retains_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    retained_body: str,
) -> None:
    import pipy_harness.native.repl.session_transfer as transfer_module

    tree = _branched_tree(tmp_path)
    cwd = Path(tree.get_header().cwd)
    target = cwd / f"retained-{retained_body.lower()}.html"
    provider = _RecordingProvider()
    footer_calls: list[str] = []

    def fail_after_write(
        _tree: NativeSessionTree,
        output_path: Path,
        **_kwargs: object,
    ) -> Path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(retained_body, encoding="utf-8")
        raise OSError(f"unexpected {retained_body.lower()} write failure")

    def footer(
        _session: NativeToolReplSession,
        _error_stream: TextIO,
        **_kwargs: object,
    ) -> None:
        footer_calls.append("footer")

    monkeypatch.setattr(
        transfer_module, "export_native_session_to_html", fail_after_write
    )
    monkeypatch.setattr(NativeToolReplSession, "_print_footer", footer)
    errors = io.StringIO()
    session = NativeToolReplSession(
        provider=provider,
        native_session=tree,
        settings_manager=_settings(tmp_path, cwd),
        tool_registry={},
    )

    with pytest.raises(
        OSError, match=f"unexpected {retained_body.lower()} write failure"
    ):
        session.run(
            workspace_root=cwd,
            input_stream=io.StringIO(f"/export {target.name}\n"),
            output_stream=io.StringIO(),
            error_stream=errors,
        )

    assert target.read_text(encoding="utf-8") == retained_body
    assert "exported native session" not in errors.getvalue()
    assert footer_calls == ["footer"]
    assert provider.requests == []


def test_export_has_no_input_session_history_agent_or_provider_effects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import pipy_harness.native.repl.extension_operations as ops_module

    tree = _branched_tree(tmp_path)
    cwd = Path(tree.get_header().cwd)
    assert tree.path is not None
    tree_before = tree.path.read_bytes()
    entries_before = tree.get_entries()
    history = PromptHistoryStore(tmp_path / "prompt-history.json")
    history.set_enabled(True)
    history_before = history.path.read_bytes()
    provider = _RecordingProvider()
    agent_sink = _RecordingAgentSink()

    def reject_input_hook(*_args: object, **_kwargs: object) -> str:
        raise AssertionError("/export must not dispatch extension input hooks")

    def reject_input_clear(_queue: CodingInputQueue) -> None:
        raise AssertionError("/export must not clear extension input")

    monkeypatch.setattr(ops_module, "dispatch_input_hooks", reject_input_hook)
    monkeypatch.setattr(CodingInputQueue, "clear_extension_inputs", reject_input_clear)
    result, _rendered = _run_captured(
        NativeToolReplSession(
            provider=provider,
            native_session=tree,
            prompt_history_store=history,
            settings_manager=_settings(tmp_path, cwd),
            tool_registry={},
            agent_event_sink=agent_sink,
        ),
        cwd,
        "/export side-effect-check.jsonl\n/exit\n",
    )

    assert provider.requests == []
    assert agent_sink.events == []
    assert result.user_turn_count == 0
    assert result.tool_invocation_count == 0
    assert tree.get_entries() == entries_before
    assert tree.path.read_bytes() == tree_before
    assert history.entries() == []
    assert history.path.read_bytes() == history_before


def test_live_export_content_and_path_stay_out_of_finalized_metadata_archive(
    tmp_path: Path,
) -> None:
    tree = _branched_tree(tmp_path)
    cwd = Path(tree.get_header().cwd)
    assert tree.path is not None
    tree_before = tree.path.read_bytes()
    export_path = cwd / f"{_PATH_MARKER}.html"
    provider = _RecordingProvider()
    errors = io.StringIO()
    adapter = PipyNativeToolReplAdapter(
        provider=provider,
        tool_registry={},
        input_stream=io.StringIO(f'/export "{export_path}"\n/exit\n'),
        output_stream=io.StringIO(),
        error_stream=errors,
        native_session=tree,
        settings_manager=_settings(tmp_path, cwd),
    )
    result = HarnessRunner(
        adapter=adapter,
        id_factory=lambda: "export-archive-privacy",
    ).run(
        RunRequest(
            agent="pipy-native",
            slug="export-archive-privacy",
            command=[],
            cwd=cwd,
            goal="export archive privacy characterization",
            root=tmp_path / "workflow-archive",
        )
    )

    product_payload = _decode_html_payload(export_path)
    product_serialized = json.dumps(product_payload, sort_keys=True)
    assert _CONTENT_MARKER in product_serialized
    assert _PATH_MARKER in str(export_path)
    assert _PATH_MARKER in errors.getvalue()
    assert _CREDENTIAL_MARKER not in product_serialized
    assert tree.path.read_bytes() == tree_before
    assert provider.requests == []

    result_metadata = json.dumps(result.metadata, sort_keys=True)
    result_repr = repr(result)
    assert result.record.markdown_path is not None
    archive_jsonl = result.record.jsonl_path.read_text(encoding="utf-8")
    archive_markdown = result.record.markdown_path.read_text(encoding="utf-8")
    for marker in (_CONTENT_MARKER, _PATH_MARKER, _CREDENTIAL_MARKER):
        assert marker not in result_metadata
        assert marker not in result_repr
        assert marker not in archive_jsonl
        assert marker not in archive_markdown

    assert result.status is HarnessStatus.SUCCEEDED
    assert isinstance(result.metadata, dict)
    assert result.metadata["adapter"] == "pipy-native"
    assert result.metadata["provider"] == provider.name
    assert result.metadata["model_id"] == provider.model_id
    assert result.metadata["tool_invocation_count"] == 0
    assert result.metadata["user_turn_count"] == 0
    assert ".in-progress" not in result.record.jsonl_path.parts
    assert ".in-progress" not in result.record.markdown_path.parts
    events = _jsonl_objects(result.record.jsonl_path)
    assert events[-1]["type"] == "session.finalized"
    assert events[-1]["run_id"] == result.run_id


def test_export_effect_is_owned_by_the_typed_interpreter() -> None:
    source = (
        Path(__file__).parents[1]
        / "src"
        / "pipy_harness"
        / "native"
        / "tool_loop_session.py"
    ).read_text(encoding="utf-8")

    assert 'if command_text == "/export"' not in source
