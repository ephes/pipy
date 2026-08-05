"""Characterization contracts for native ``/fork`` and ``/clone`` commands."""

from __future__ import annotations

import io
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import TextIO, TypedDict, Unpack

import pytest

from pipy_harness.models import HarnessStatus
from pipy_harness.native import (
    NativeToolReplSession,
    ProviderRequest,
    ProviderResult,
)
from pipy_harness.native.agent import (
    AgentAssistantMessage,
    AgentUserMessage,
    ProductContent,
)
from pipy_harness.native.chrome import _ChromeFooterEffects
from pipy_harness.native.coding.input_queue import CodingInputQueue
from pipy_harness.native.coding.product_session import CodingProductSessionCoordinator
from pipy_harness.native.diagnostics import emit_diagnostic
from pipy_harness.native.extension_runtime import SessionDecision
from pipy_harness.native.session_tree import (
    CompactionEntry,
    LabelEntry,
    MessageEntry,
    NativeSessionTree,
    SessionInfoEntry,
)
from pipy_harness.native.tui import ToolLoopTerminalUi
from pipy_harness.native.ui.components.transcript import TranscriptComponent


class _RecordingProvider:
    name = "fake"
    supports_tool_calls = True
    model_id = "fake-native-bootstrap"

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
            final_text="ok",
            tool_calls=(),
        )


class _ForkKwargs(TypedDict, total=False):
    leaf_id: str | None
    session_dir: Path | None
    state_root: Path | None
    persist: bool
    session_id: str | None


def _workspace(tmp_path: Path) -> Path:
    cwd = tmp_path / "workspace"
    cwd.mkdir()
    return cwd


def _run(session: NativeToolReplSession, cwd: Path, inputs: str) -> tuple[str, str]:
    output = io.StringIO()
    error = io.StringIO()
    session.run(
        workspace_root=cwd,
        input_stream=io.StringIO(inputs),
        output_stream=output,
        error_stream=error,
    )
    return output.getvalue(), error.getvalue()


def _persistent_tree(tmp_path: Path) -> tuple[Path, Path, NativeSessionTree]:
    cwd = _workspace(tmp_path)
    session_dir = tmp_path / "sessions"
    tree = NativeSessionTree.create(cwd, session_dir=session_dir)
    return cwd, session_dir, tree


def _child_tree(source: NativeSessionTree, session_dir: Path) -> NativeSessionTree:
    assert source.path is not None
    paths = [path for path in session_dir.glob("*.jsonl") if path != source.path]
    assert len(paths) == 1
    return NativeSessionTree.open(paths[0])


def _request_users(request: ProviderRequest) -> list[str]:
    return [
        message.content.value
        for message in request.messages
        if isinstance(message, AgentUserMessage)
    ]


def _write_fork_gate(cwd: Path, body: str, *, target: str | None) -> None:
    extension = cwd / ".pipy" / "extensions" / "fork_gate.py"
    extension.parent.mkdir(parents=True)
    extension.write_text(
        "from pipy_harness.extensions import SessionDecision\n"
        "def activate(api):\n"
        "    @api.on('session_before_fork')\n"
        "    def fork_gate(event, ctx):\n"
        "        assert event.operation == 'fork'\n"
        f"        assert event.target == {target!r}\n"
        f"{body}"
        "    @api.on('session_before_switch')\n"
        "    def wrong_gate(event, ctx):\n"
        "        raise AssertionError('session_before_switch must not run')\n",
        encoding="utf-8",
    )


def _install_terminal(
    monkeypatch: pytest.MonkeyPatch,
    *,
    cwd: Path,
    commands: Sequence[str],
    read_trace: list[str] | None = None,
) -> ToolLoopTerminalUi:
    terminal = ToolLoopTerminalUi(
        input_stream=io.StringIO(), terminal_stream=io.StringIO(), cwd=cwd
    )
    scripted = iter(commands)

    def build(self: NativeToolReplSession, **_kwargs: object) -> ToolLoopTerminalUi:
        del self
        return terminal

    def read_line(
        self: ToolLoopTerminalUi, prompt_label: str, *, footer: object = None
    ) -> str:
        del self, prompt_label, footer
        if read_trace is not None:
            read_trace.append("footer")
        return next(scripted)

    def wait_for_turn(
        self: ToolLoopTerminalUi,
        done_event: object,
        abort_event: object,
        **_kwargs: object,
    ) -> str:
        del self, done_event, abort_event
        return "settled"

    monkeypatch.setattr(NativeToolReplSession, "_build_terminal_ui", build)
    monkeypatch.setattr(ToolLoopTerminalUi, "read_line", read_line)
    monkeypatch.setattr(
        ToolLoopTerminalUi, "wait_for_active_turn_interrupt", wait_for_turn
    )
    return terminal


def test_fork_requires_persistence_before_resolution_or_hooks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import pipy_harness.native.repl.extension_operations as ops_module
    import pipy_harness.native.repl.session_commands as commands_module

    cwd = _workspace(tmp_path)
    tree = NativeSessionTree.create(cwd, persist=False)
    trace: list[str] = []
    monkeypatch.setattr(
        commands_module,
        "resolve_entry_ref",
        lambda *_args, **_kwargs: trace.append("resolve"),
    )
    monkeypatch.setattr(
        ops_module,
        "dispatch_session_before_hooks",
        lambda *_args, **_kwargs: trace.append("gate"),
    )
    provider = _RecordingProvider()

    _output, error = _run(
        NativeToolReplSession(provider=provider, native_session=tree),
        cwd,
        "/fork missing\n/exit\n",
    )

    assert "requires a persistent native session" in error
    assert trace == []
    assert provider.requests == []


def test_unresolved_fork_target_stops_before_gate_and_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import pipy_harness.native.repl.extension_operations as ops_module

    cwd, _session_dir, tree = _persistent_tree(tmp_path)
    trace: list[str] = []

    def gate(
        _hooks: object,
        *,
        operation: str,
        target: str | None = None,
        **_kwargs: object,
    ) -> SessionDecision:
        trace.append(f"gate:{operation}:{target}")
        return SessionDecision()

    monkeypatch.setattr(ops_module, "dispatch_session_before_hooks", gate)
    monkeypatch.setattr(
        NativeSessionTree,
        "fork_from",
        staticmethod(lambda *_args, **_kwargs: trace.append("fork")),
    )

    _output, error = _run(
        NativeToolReplSession(provider=_RecordingProvider(), native_session=tree),
        cwd,
        "/fork missing\n/exit\n",
    )

    assert "no tree entry matched 'missing'" in error
    assert trace == []


@pytest.mark.parametrize("command", ["/fork", "/clone"])
def test_bare_fork_and_clone_gate_on_current_leaf(tmp_path: Path, command: str) -> None:
    cwd, session_dir, tree = _persistent_tree(tmp_path)
    tree.append_message(AgentUserMessage(content=ProductContent("ROOT")))
    leaf = tree.append_message(AgentAssistantMessage(content=ProductContent("ANSWER")))
    _write_fork_gate(
        cwd, "        return SessionDecision(allow=True)\n", target=leaf.id
    )
    provider = _RecordingProvider()

    _run(
        NativeToolReplSession(provider=provider, native_session=tree),
        cwd,
        f"{command}\n/exit\n",
    )

    child = _child_tree(tree, session_dir)
    assert [message.content.value for message in child.build_context().messages] == [
        "ROOT",
        "ANSWER",
    ]
    assert provider.requests == []


def test_fork_accepts_resolvable_assistant_entry_as_explicit_target(
    tmp_path: Path,
) -> None:
    cwd, session_dir, tree = _persistent_tree(tmp_path)
    tree.append_message(AgentUserMessage(content=ProductContent("ROOT")))
    assistant = tree.append_message(
        AgentAssistantMessage(content=ProductContent("ANSWER"))
    )
    tree.append_message(AgentUserMessage(content=ProductContent("LATER")))
    _write_fork_gate(
        cwd, "        return SessionDecision(allow=True)\n", target=assistant.id
    )

    _run(
        NativeToolReplSession(provider=_RecordingProvider(), native_session=tree),
        cwd,
        f"/fork {assistant.id}\n/exit\n",
    )

    child = _child_tree(tree, session_dir)
    assert [message.content.value for message in child.build_context().messages] == [
        "ROOT",
        "ANSWER",
    ]


def test_clone_accepts_empty_persistent_tree_and_none_target(tmp_path: Path) -> None:
    cwd, session_dir, tree = _persistent_tree(tmp_path)
    _write_fork_gate(cwd, "        return SessionDecision(allow=True)\n", target=None)

    _run(
        NativeToolReplSession(provider=_RecordingProvider(), native_session=tree),
        cwd,
        "/clone\n/exit\n",
    )

    child = _child_tree(tree, session_dir)
    assert child.get_entries() == []
    assert child.get_header().parent_session == str(tree.path)


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        ("        return SessionDecision(allow=False, reason='stay')\n", "stay"),
        (
            "        raise RuntimeError('private content')\n",
            "extension fork hook error",
        ),
    ],
)
def test_fork_gate_denial_or_error_has_standard_footer_and_no_copy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    body: str,
    reason: str,
) -> None:
    cwd, session_dir, tree = _persistent_tree(tmp_path)
    leaf = tree.append_message(AgentUserMessage(content=ProductContent("ROOT")))
    _write_fork_gate(cwd, body, target=leaf.id)
    footers: list[None] = []
    monkeypatch.setattr(
        _ChromeFooterEffects,
        "_print_footer",
        lambda *_args, **_kwargs: footers.append(None),
    )

    _output, error = _run(
        NativeToolReplSession(provider=_RecordingProvider(), native_session=tree),
        cwd,
        "/fork\n/exit\n",
    )

    assert f"fork blocked by extension: {reason}" in error
    assert len(list(session_dir.glob("*.jsonl"))) == 1
    assert footers == [None, None]


@pytest.mark.parametrize("fatal", [KeyboardInterrupt, SystemExit])
def test_fork_gate_fatal_cuts_off_copy_and_command_footer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fatal: type[BaseException],
) -> None:
    cwd, session_dir, tree = _persistent_tree(tmp_path)
    leaf = tree.append_message(AgentUserMessage(content=ProductContent("ROOT")))
    _write_fork_gate(cwd, f"        raise {fatal.__name__}()\n", target=leaf.id)
    footers: list[None] = []
    monkeypatch.setattr(
        _ChromeFooterEffects,
        "_print_footer",
        lambda *_args, **_kwargs: footers.append(None),
    )

    with pytest.raises(fatal):
        _run(
            NativeToolReplSession(provider=_RecordingProvider(), native_session=tree),
            cwd,
            "/fork\n",
        )

    assert len(list(session_dir.glob("*.jsonl"))) == 1
    assert footers == [None]


def test_fork_success_order_fresh_history_and_no_custom_redraw(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import pipy_harness.native.repl.extension_operations as ops_module

    cwd, _session_dir, tree = _persistent_tree(tmp_path)
    root = tree.append_message(AgentUserMessage(content=ProductContent("ROOT")))
    leaf = tree.append_message(AgentAssistantMessage(content=ProductContent("ANSWER")))
    tree.set_leaf(root.id)
    tree.append_message(AgentUserMessage(content=ProductContent("SIBLING")))
    tree.set_leaf(leaf.id)
    trace: list[str] = []
    _install_terminal(
        monkeypatch,
        cwd=cwd,
        commands=("/fork", "FRESH", "/exit"),
        read_trace=trace,
    )
    original_fork = NativeSessionTree.fork_from
    original_rebuild = CodingProductSessionCoordinator.rebuild_active_history
    original_clear = CodingInputQueue.clear_extension_inputs
    original_diag = emit_diagnostic
    rebuild_count = 0

    def gate(
        _hooks: object,
        *,
        operation: str,
        target: str | None = None,
        **_kwargs: object,
    ) -> SessionDecision:
        trace.append(f"gate:{operation}:{target}")
        return SessionDecision()

    def fork(
        source: Path, target: Path, **kwargs: Unpack[_ForkKwargs]
    ) -> NativeSessionTree:
        trace.append("fork-from")
        return original_fork(source, target, **kwargs)

    def rebuild(self: CodingProductSessionCoordinator) -> None:
        nonlocal rebuild_count
        rebuild_count += 1
        if rebuild_count > 1:
            trace.append("rebuild-new")
        original_rebuild(self)

    def clear(self: CodingInputQueue) -> None:
        trace.append("clear-extension")
        original_clear(self)

    def diagnostic(ui: ToolLoopTerminalUi | None, stream: TextIO, message: str) -> None:
        if message.startswith("pipy: forked into"):
            trace.append("diagnostic")
        original_diag(ui, stream, message)

    monkeypatch.setattr(ops_module, "dispatch_session_before_hooks", gate)
    monkeypatch.setattr(NativeSessionTree, "fork_from", staticmethod(fork))
    monkeypatch.setattr(
        CodingProductSessionCoordinator, "rebuild_active_history", rebuild
    )
    monkeypatch.setattr(CodingInputQueue, "clear_extension_inputs", clear)
    # `/fork` reports through the collaborators' injected `diag`, so that is
    # where `emit_diagnostic` binds.
    monkeypatch.setattr(
        "pipy_harness.native.repl.collaborators.emit_diagnostic", diagnostic
    )
    monkeypatch.setattr(
        TranscriptComponent,
        "redraw_custom_entries",
        lambda *_args, **_kwargs: trace.append("redraw"),
    )
    provider = _RecordingProvider()

    _run(NativeToolReplSession(provider=provider, native_session=tree), cwd, "")

    command_start = trace.index(f"gate:fork:{leaf.id}")
    assert trace[command_start : command_start + 6] == [
        f"gate:fork:{leaf.id}",
        "fork-from",
        "rebuild-new",
        "clear-extension",
        "diagnostic",
        "footer",
    ]
    assert "redraw" not in trace
    assert _request_users(provider.requests[0]) == ["ROOT", "FRESH"]


def test_fork_copies_only_active_branch_with_fresh_identity_and_metadata(
    tmp_path: Path,
) -> None:
    cwd, session_dir, tree = _persistent_tree(tmp_path)
    root = tree.append_message(AgentUserMessage(content=ProductContent("PRIVATE ROOT")))
    main = tree.append_message(AgentAssistantMessage(content=ProductContent("MAIN")))
    tree.set_leaf(root.id)
    sibling = tree.append_message(
        AgentAssistantMessage(content=ProductContent("SIBLING"))
    )
    tree.set_leaf(main.id)
    tree.append_model_change("provider", "model")
    tree.append_thinking_level_change("high")
    keep = tree.append_message(AgentUserMessage(content=ProductContent("KEEP")))
    tree.append_compaction(
        summary="PRIVATE SUMMARY", first_kept_entry_id=keep.id, tokens_before=9
    )
    tree.append_label_change(keep.id, "kept-label")
    tree.append_session_info("source-name")
    archive = tmp_path / "metadata-archive.jsonl"
    archive.write_bytes(b'{"safe":"sentinel"}\n')
    before = archive.read_bytes()
    assert tree.path is not None

    _run(
        NativeToolReplSession(provider=_RecordingProvider(), native_session=tree),
        cwd,
        "/fork\n/exit\n",
    )

    child = _child_tree(tree, session_dir)
    source_ids = {entry.id for entry in tree.get_entries()}
    child_ids = {entry.id for entry in child.get_entries()}
    child_text = child.path.read_text(encoding="utf-8") if child.path else ""
    assert source_ids.isdisjoint(child_ids)
    assert child.get_header().parent_session == str(tree.path)
    assert child.name == "source-name"
    assert (
        child.get_label(
            next(
                entry.id
                for entry in child.get_entries()
                if isinstance(entry, MessageEntry)
                and isinstance(entry.message, AgentUserMessage)
                and entry.message.content.value == "KEEP"
            )
        )
        == "kept-label"
    )
    assert any(isinstance(entry, CompactionEntry) for entry in child.get_entries())
    assert any(isinstance(entry, SessionInfoEntry) for entry in child.get_entries())
    assert any(isinstance(entry, LabelEntry) for entry in child.get_entries())
    assert "PRIVATE ROOT" in child_text and "PRIVATE SUMMARY" in child_text
    assert '"provider": "provider"' in child_text and '"modelId": "model"' in child_text
    assert '"thinkingLevel": "high"' in child_text
    assert "SIBLING" not in child_text and sibling.id not in child_text
    assert archive.read_bytes() == before


@pytest.mark.parametrize("failure_stage", ["fork", "open", "write", "rebuild", "clear"])
def test_fork_failure_timing_cuts_off_later_effects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_stage: str,
) -> None:
    cwd, session_dir, tree = _persistent_tree(tmp_path)
    tree.append_message(AgentUserMessage(content=ProductContent("ROOT")))
    trace: list[str] = []
    original_fork = NativeSessionTree.fork_from
    original_rebuild = CodingProductSessionCoordinator.rebuild_active_history
    rebuild_count = 0

    def fork(
        source: Path, target: Path, **kwargs: Unpack[_ForkKwargs]
    ) -> NativeSessionTree:
        trace.append("fork")
        if failure_stage == "fork":
            raise RuntimeError("fork failed")
        return original_fork(source, target, **kwargs)

    def open_tree(path: Path, *, persist: bool = True) -> NativeSessionTree:
        trace.append("open")
        raise RuntimeError("open failed")

    def write_header(self: NativeSessionTree) -> None:
        trace.append("write")
        raise RuntimeError("write failed")

    def rebuild(self: CodingProductSessionCoordinator) -> None:
        nonlocal rebuild_count
        rebuild_count += 1
        if rebuild_count == 2:
            trace.append("rebuild")
            if failure_stage == "rebuild":
                raise RuntimeError("rebuild failed")
        original_rebuild(self)

    def clear(self: CodingInputQueue) -> None:
        trace.append("clear")
        if failure_stage == "clear":
            raise RuntimeError("clear failed")

    monkeypatch.setattr(NativeSessionTree, "fork_from", staticmethod(fork))
    if failure_stage == "open":
        monkeypatch.setattr(NativeSessionTree, "open", staticmethod(open_tree))
    if failure_stage == "write":
        monkeypatch.setattr(NativeSessionTree, "_write_header", write_header)
    monkeypatch.setattr(
        CodingProductSessionCoordinator, "rebuild_active_history", rebuild
    )
    monkeypatch.setattr(CodingInputQueue, "clear_extension_inputs", clear)
    monkeypatch.setattr(
        "pipy_harness.native.repl.wiring.emit_diagnostic",
        lambda *_args, **_kwargs: trace.append("diagnostic"),
    )
    monkeypatch.setattr(
        _ChromeFooterEffects,
        "_print_footer",
        lambda *_args, **_kwargs: trace.append("footer"),
    )

    with pytest.raises(RuntimeError, match=f"{failure_stage} failed"):
        _run(
            NativeToolReplSession(provider=_RecordingProvider(), native_session=tree),
            cwd,
            "/fork\n",
        )

    expected = ["footer", "fork"]
    if failure_stage in {"open", "write"}:
        expected.append(failure_stage)
    if failure_stage in {"rebuild", "clear"}:
        expected.append("rebuild")
    if failure_stage == "clear":
        expected.append("clear")
    assert trace == expected
    expected_files = 1 if failure_stage in {"fork", "open", "write"} else 2
    assert len(list(session_dir.glob("*.jsonl"))) == expected_files


def test_fork_diagnostic_sanitizes_returned_session_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import pipy_harness.native.session_tree as tree_module

    cwd, _session_dir, tree = _persistent_tree(tmp_path)
    tree.append_message(AgentUserMessage(content=ProductContent("ROOT")))
    monkeypatch.setattr(tree_module, "_new_session_id", lambda: "EV\x1bIL\x07X")
    provider = _RecordingProvider()

    _output, error = _run(
        NativeToolReplSession(provider=provider, native_session=tree),
        cwd,
        "/fork\n/exit\n",
    )

    assert "forked into new native session EV IL X" in error
    assert "\x1b" not in error and "\x07" not in error
    assert provider.requests == []
