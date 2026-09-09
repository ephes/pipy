"""Characterization contracts for native ``/fork`` and ``/clone`` commands."""

from __future__ import annotations

import io
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import TextIO

import pytest

from pipy_harness.models import HarnessStatus
from pipy_harness.native import (
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
from pipy_harness.native.coding.session import CodingSession
from pipy_harness.native.diagnostics import NoticeSink, emit_diagnostic
from pipy_harness.native.extension_types import SessionDecision
from pipy_harness.native.repl.session_commands import SessionCommandEffects
from pipy_harness.native.repl.session_transition import (
    CanonicalSessionLeaseRegistry,
    CanonicalSessionLeaseSlot,
    SessionTransitionCoordinator,
)
from pipy_harness.native.session_tree import (
    BranchSummaryEntry,
    CompactionEntry,
    LabelEntry,
    MessageEntry,
    NativeSessionTree,
    SessionInfoEntry,
)
from pipy_harness.native.tui import TerminalUi
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


def _workspace(tmp_path: Path) -> Path:
    cwd = tmp_path / "workspace"
    cwd.mkdir()
    return cwd


def _run(session: CodingSession, cwd: Path, inputs: str) -> tuple[str, str]:
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
) -> TerminalUi:
    terminal = TerminalUi(
        input_stream=io.StringIO(), terminal_stream=io.StringIO(), cwd=cwd
    )
    scripted = iter(commands)

    def build(self: CodingSession, **_kwargs: object) -> TerminalUi:
        del self
        return terminal

    def read_line(self: TerminalUi, prompt_label: str, *, footer: object = None) -> str:
        del self, prompt_label, footer
        if read_trace is not None:
            read_trace.append("footer")
        return next(scripted)

    def wait_for_turn(
        self: TerminalUi,
        done_event: object,
        abort_event: object,
        **_kwargs: object,
    ) -> str:
        del self, done_event, abort_event
        return "settled"

    monkeypatch.setattr(CodingSession, "_build_terminal_ui", build)
    monkeypatch.setattr(TerminalUi, "read_line", read_line)
    monkeypatch.setattr(TerminalUi, "wait_for_active_turn_interrupt", wait_for_turn)
    return terminal


def test_fork_requires_persistence_before_resolution_or_hooks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import pipy_harness.native.repl.extension_operations as ops_module
    import pipy_harness.native.repl.session_commands as commands_module

    cwd = _workspace(tmp_path)
    tree = NativeSessionTree.create(cwd, persist=False)
    trace: list[str] = []
    footers: list[None] = []
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
    monkeypatch.setattr(
        _ChromeFooterEffects,
        "_print_footer",
        lambda *_args, **_kwargs: footers.append(None),
    )
    provider = _RecordingProvider()

    _output, error = _run(
        CodingSession(provider=provider, native_session=tree),
        cwd,
        "/fork missing\n/exit\n",
    )

    assert "requires a persistent native session" in error
    assert trace == []
    assert footers == [None, None]
    assert provider.requests == []


def test_unresolved_fork_target_stops_before_gate_and_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import pipy_harness.native.repl.extension_operations as ops_module

    cwd, _session_dir, tree = _persistent_tree(tmp_path)
    trace: list[str] = []
    footers: list[None] = []

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
        "fork_from_snapshot",
        staticmethod(lambda *_args, **_kwargs: trace.append("fork")),
    )
    monkeypatch.setattr(
        _ChromeFooterEffects,
        "_print_footer",
        lambda *_args, **_kwargs: footers.append(None),
    )

    _output, error = _run(
        CodingSession(provider=_RecordingProvider(), native_session=tree),
        cwd,
        "/fork missing\n/exit\n",
    )

    assert "no tree entry matched 'missing'" in error
    assert trace == []
    assert footers == [None, None]


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
        CodingSession(provider=provider, native_session=tree),
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
        CodingSession(provider=_RecordingProvider(), native_session=tree),
        cwd,
        f"/fork {assistant.id}\n/exit\n",
    )

    child = _child_tree(tree, session_dir)
    assert [message.content.value for message in child.build_context().messages] == [
        "ROOT",
        "ANSWER",
    ]


@pytest.mark.parametrize(
    ("command", "message"),
    [
        ("/fork", "pipy: nothing to fork yet."),
        ("/clone", "pipy: nothing to clone yet."),
    ],
)
def test_bare_fork_and_clone_refuse_empty_persistent_tree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    command: str,
    message: str,
) -> None:
    cwd, session_dir, tree = _persistent_tree(tmp_path)
    footers: list[None] = []
    monkeypatch.setattr(
        _ChromeFooterEffects,
        "_print_footer",
        lambda *_args, **_kwargs: footers.append(None),
    )

    _output, error = _run(
        CodingSession(provider=_RecordingProvider(), native_session=tree),
        cwd,
        f"{command}\n/exit\n",
    )

    assert message in error
    assert list(session_dir.glob("*.jsonl")) == [tree.path]
    assert footers == [None, None]


@pytest.mark.parametrize(
    ("command", "success_fragment"),
    [("/fork", "forked"), ("/clone", "cloned")],
)
def test_fork_and_clone_lease_conflict_keep_source_usable_with_one_footer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    command: str,
    success_fragment: str,
) -> None:
    cwd, session_dir, source = _persistent_tree(tmp_path)
    source.append_message(AgentUserMessage(ProductContent("ROOT")))
    assert source.path is not None
    source_path = source.path.resolve()
    original_claim = CanonicalSessionLeaseRegistry.claim
    footers: list[None] = []

    def claim(path: Path) -> object:
        if path.resolve() != source_path:
            raise RuntimeError("persistent product session is already active")
        return original_claim(path)

    monkeypatch.setattr(CanonicalSessionLeaseRegistry, "claim", staticmethod(claim))
    monkeypatch.setattr(
        _ChromeFooterEffects,
        "_print_footer",
        lambda *_args, **_kwargs: footers.append(None),
    )
    provider = _RecordingProvider()

    _output, error = _run(
        CodingSession(provider=provider, native_session=source),
        cwd,
        f"{command}\nFRESH\n/exit\n",
    )

    assert "pipy: new native session is already active." in error
    assert success_fragment not in error
    assert _request_users(provider.requests[0]) == ["ROOT", "FRESH"]
    assert len(list(session_dir.glob("*.jsonl"))) == 2
    assert footers == [None, None, None]
    released_source = original_claim(source_path)
    released_source.finish()


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
        CodingSession(provider=_RecordingProvider(), native_session=tree),
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
            CodingSession(provider=_RecordingProvider(), native_session=tree),
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
    original_fork = NativeSessionTree.fork_from_snapshot
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
        source: NativeSessionTree,
        target: Path,
        *,
        leaf_id: str | None,
        session_dir: Path,
    ) -> NativeSessionTree:
        trace.append("fork-snapshot")
        return original_fork(source, target, leaf_id=leaf_id, session_dir=session_dir)

    def rebuild(self: CodingProductSessionCoordinator) -> None:
        nonlocal rebuild_count
        rebuild_count += 1
        if rebuild_count > 1:
            trace.append("rebuild-new")
        original_rebuild(self)

    def clear(self: CodingInputQueue) -> None:
        trace.append("clear-extension")
        original_clear(self)

    def diagnostic(ui: NoticeSink | None, stream: TextIO, message: str) -> None:
        if message.startswith("pipy: forked into"):
            trace.append("diagnostic")
        original_diag(ui, stream, message)

    monkeypatch.setattr(ops_module, "dispatch_session_before_hooks", gate)
    monkeypatch.setattr(NativeSessionTree, "fork_from_snapshot", staticmethod(fork))
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

    _run(CodingSession(provider=provider, native_session=tree), cwd, "")

    command_start = trace.index(f"gate:fork:{leaf.id}")
    assert trace[command_start : command_start + 6] == [
        f"gate:fork:{leaf.id}",
        "fork-snapshot",
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
        CodingSession(provider=_RecordingProvider(), native_session=tree),
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


def test_fork_snapshot_copies_exact_non_user_branch_with_remapped_references(
    tmp_path: Path,
) -> None:
    cwd, session_dir, source = _persistent_tree(tmp_path)
    source.append_message(AgentUserMessage(ProductContent("ROOT")))
    retained = source.append_message(AgentUserMessage(ProductContent("KEEP")))
    compaction = source.append_compaction(
        summary="SUMMARY",
        first_kept_entry_id=retained.id,
        tokens_before=7,
    )
    summary = source.branch_with_summary(compaction.id, "BRANCH SUMMARY")
    selected = source.append_label_change(summary.id, "selected-label")
    source.append_session_info("copied-name")
    source_before = source.path.read_bytes() if source.path is not None else b""

    child = NativeSessionTree.fork_from_snapshot(
        source, cwd, leaf_id=selected.id, session_dir=session_dir
    )

    assert source.path is not None and child.path is not None
    assert child.header.parent_session == str(source.path.resolve())
    assert source.path.read_bytes() == source_before
    source_ids = {entry.id for entry in source.get_entries()}
    child_entries = child.get_entries()
    child_ids = {entry.id for entry in child_entries}
    assert source_ids.isdisjoint(child_ids)
    assert child.name == "copied-name"
    child_compaction = next(
        entry for entry in child_entries if isinstance(entry, CompactionEntry)
    )
    child_summary = next(
        entry for entry in child_entries if isinstance(entry, BranchSummaryEntry)
    )
    child_label = next(
        entry for entry in child_entries if isinstance(entry, LabelEntry)
    )
    assert child_compaction.first_kept_entry_id in child_ids
    assert child_compaction.retained_user_entry_id is None
    assert child_summary.from_id == child_compaction.id
    assert child_label.target_id == child_summary.id
    assert child.get_label(child_summary.id) == "selected-label"


@pytest.mark.parametrize("failure_stage", ["snapshot", "write", "rebuild", "clear"])
def test_fork_failure_timing_cuts_off_later_effects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_stage: str,
) -> None:
    cwd, session_dir, tree = _persistent_tree(tmp_path)
    tree.append_message(AgentUserMessage(content=ProductContent("ROOT")))
    trace: list[str] = []
    original_fork = NativeSessionTree.fork_from_snapshot
    original_rebuild = CodingProductSessionCoordinator.rebuild_active_history
    rebuild_count = 0

    def fork(
        source: NativeSessionTree,
        target: Path,
        *,
        leaf_id: str | None,
        session_dir: Path,
    ) -> NativeSessionTree:
        trace.append("snapshot")
        if failure_stage == "snapshot":
            raise RuntimeError("snapshot failed")
        return original_fork(source, target, leaf_id=leaf_id, session_dir=session_dir)

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

    monkeypatch.setattr(NativeSessionTree, "fork_from_snapshot", staticmethod(fork))
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
            CodingSession(provider=_RecordingProvider(), native_session=tree),
            cwd,
            "/fork\n",
        )

    expected = ["footer", "snapshot"]
    if failure_stage == "write":
        expected.append(failure_stage)
    if failure_stage in {"rebuild", "clear"}:
        expected.append("rebuild")
    if failure_stage == "clear":
        expected.append("clear")
    assert trace == expected
    expected_files = 1 if failure_stage in {"snapshot", "write"} else 2
    assert len(list(session_dir.glob("*.jsonl"))) == expected_files
    if failure_stage in {"rebuild", "clear"}:
        assert tree.path is not None
        child_path = next(
            path for path in session_dir.glob("*.jsonl") if path != tree.path
        )
        released_child = CanonicalSessionLeaseRegistry.claim(child_path)
        released_child.finish()


def test_fork_diagnostic_sanitizes_returned_session_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import pipy_harness.native.session_tree as tree_module

    cwd, _session_dir, tree = _persistent_tree(tmp_path)
    tree.append_message(AgentUserMessage(content=ProductContent("ROOT")))
    monkeypatch.setattr(tree_module, "_new_session_id", lambda: "EV\x1bIL\x07X")
    provider = _RecordingProvider()

    _output, error = _run(
        CodingSession(provider=provider, native_session=tree),
        cwd,
        "/fork\n/exit\n",
    )

    assert "forked into new native session EV IL X" in error
    assert "\x1b" not in error and "\x07" not in error
    assert provider.requests == []


def test_terminal_fork_releases_source_before_rebuild_and_holds_child_lease(
    tmp_path: Path,
) -> None:
    cwd, session_dir, source = _persistent_tree(tmp_path)
    source.append_message(AgentUserMessage(ProductContent("ROOT")))
    assert source.path is not None
    source_path = source.path.resolve()
    current = [source]
    slot = CanonicalSessionLeaseSlot(CanonicalSessionLeaseRegistry.claim(source_path))
    trace: list[str] = []

    def rebuild() -> None:
        trace.append("rebuild")
        released_source = CanonicalSessionLeaseRegistry.claim(source_path)
        released_source.finish()
        assert current[0].path is not None
        with pytest.raises(RuntimeError, match="already active"):
            CanonicalSessionLeaseRegistry.claim(current[0].path)

    def silent_gate(entry: str | None) -> bool:
        trace.append(f"gate:{entry}")
        return True

    def terminal_gate(entry: str | None) -> bool:
        trace.append(f"terminal-gate:{entry}")
        return True

    coordinator = SessionTransitionCoordinator(
        workspace=cwd,
        get_tree=lambda: current[0],
        set_tree=lambda tree: current.__setitem__(0, tree),
        session_before_fork=silent_gate,
        session_before_switch=lambda _target: True,
        rebuild=rebuild,
        clear_extension_inputs=lambda: trace.append("clear"),
    )

    try:
        result = coordinator.fork_terminal(
            None,
            operation="clone",
            leases=slot,
            before_fork=terminal_gate,
        )
        assert result.status == "completed"
        assert result.previous is not None
        assert result.previous.session_path == source_path
        assert current[0].path is not None
        child_path = current[0].path.resolve()
        assert slot.current_path() == child_path
        assert trace == [
            f"terminal-gate:{source.leaf_id}",
            "rebuild",
            "clear",
        ]
    finally:
        slot.finish()

    released_child = CanonicalSessionLeaseRegistry.claim(child_path)
    released_child.finish()
    assert len(list(session_dir.glob("*.jsonl"))) == 2


@pytest.mark.parametrize(
    ("command", "success_fragment"),
    [("/fork", "forked into"), ("/clone", "cloned active branch")],
)
def test_terminal_fork_adopts_child_before_later_new_transition(
    tmp_path: Path, command: str, success_fragment: str
) -> None:
    cwd, session_dir, source = _persistent_tree(tmp_path)
    source.append_message(AgentUserMessage(ProductContent("ROOT")))

    _output, error = _run(
        CodingSession(provider=_RecordingProvider(), native_session=source),
        cwd,
        f"{command}\n/new\n/exit\n",
    )

    assert success_fragment in error
    assert "started a new native session" in error
    assert len(list(session_dir.glob("*.jsonl"))) == 3


def test_terminal_fork_publication_failure_aborts_child_claim_and_keeps_source(
    tmp_path: Path,
) -> None:
    cwd, session_dir, source = _persistent_tree(tmp_path)
    source.append_message(AgentUserMessage(ProductContent("ROOT")))
    assert source.path is not None
    source_path = source.path.resolve()
    slot = CanonicalSessionLeaseSlot(CanonicalSessionLeaseRegistry.claim(source_path))
    coordinator = SessionTransitionCoordinator(
        workspace=cwd,
        get_tree=lambda: source,
        set_tree=lambda _tree: (_ for _ in ()).throw(LookupError("publish failed")),
        session_before_fork=lambda _entry: True,
        session_before_switch=lambda _target: True,
        rebuild=lambda: pytest.fail("must not rebuild after publication failure"),
        clear_extension_inputs=lambda: pytest.fail(
            "must not clear after publication failure"
        ),
    )

    try:
        with pytest.raises(LookupError, match="publish failed"):
            coordinator.fork_terminal(
                None,
                operation="fork",
                leases=slot,
                before_fork=lambda _entry: True,
            )
        assert slot.current_path() == source_path
        child_path = next(
            path.resolve()
            for path in session_dir.glob("*.jsonl")
            if path.resolve() != source_path
        )
    finally:
        slot.finish()

    released_child = CanonicalSessionLeaseRegistry.claim(child_path)
    released_child.finish()
    released_source = CanonicalSessionLeaseRegistry.claim(source_path)
    released_source.finish()


def test_terminal_fork_handler_delegates_tree_ownership_to_transition_port() -> None:
    import inspect

    source = inspect.getsource(SessionCommandEffects._execute_fork_or_clone)
    terminal_source, _marker, _fallback = source.partition(
        "if fork_target_resolved and self.extension_session_allows"
    )

    assert "fork_transition" in terminal_source
    assert "fork_from" not in terminal_source
    assert ".session_tree =" not in terminal_source
