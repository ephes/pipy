"""Tool-loop runtime wired to the native product session tree.

These tests pin that the tool-loop REPL persists raw product turns to a native
session tree file, builds provider-visible context from the active branch, and
reconstructs context when resumed from an existing native session file.
"""

from __future__ import annotations

import inspect
import io
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Protocol, TextIO

import pytest

from pipy_harness.models import HarnessStatus
from pipy_harness.native import (
    ProviderRequest,
    ProviderResult,
)
from pipy_harness.native.agent import (
    AgentAssistantMessage,
    AgentMessage,
    AgentToolResultMessage,
    AgentUserMessage,
    ProductContent,
)
from pipy_harness.native.agent.usage import AgentUsageAccumulator
from pipy_harness.native.chrome import _ChromeFooterEffects
from pipy_harness.native.coding.input_queue import CodingInputQueue
from pipy_harness.native.coding.product_session import (
    CodingProductSessionContext,
    CodingProductSessionCoordinator,
)
from pipy_harness.native.coding.session import CodingSession
from pipy_harness.native.diagnostics import NoticeSink, emit_diagnostic
from pipy_harness.native.extension_types import (
    ExtensionModelRuntimeControl,
    ExtensionUiDriver,
    SessionDecision,
)
from pipy_harness.native.extensions.contracts import (
    HookHandler,
)
from pipy_harness.native.repl.collaborators import SessionCollaborators
from pipy_harness.native.repl.session_commands import (
    SessionCommandEffects,
    run_interactive_session_picker,
)
from pipy_harness.native.repl.session_transition import (
    CanonicalSessionLeaseRegistry,
    CanonicalSessionLeaseSlot,
    ProductSessionTransitionError,
    SessionTransitionCoordinator,
)
from pipy_harness.native.session_tree import (
    BranchSummaryEntry,
    CompactionEntry,
    MessageEntry,
    NativeSessionTree,
    PreparedBranchSummary,
    SessionEntry,
    SessionHeader,
    SessionInfoEntry,
)
from pipy_harness.native.session_tree_commands import (
    BranchSummarySelectionResult,
    TreeCommandOutcome,
)
from pipy_harness.native.tui import TerminalUi
from pipy_harness.native.ui.components.transcript import TranscriptComponent


class _SeenProvider:
    """Deterministic provider that echoes the user messages it can see.

    Returns ``SEEN:<comma-joined active-branch user messages>`` so a test can
    assert exactly which user turns reached the provider on each call.
    """

    name = "fake"
    supports_tool_calls = True
    model_id = "fake-native-bootstrap"

    def __init__(self) -> None:
        self.requests: list[ProviderRequest] = []

    def complete(self, request: ProviderRequest, **_kwargs: object) -> ProviderResult:
        self.requests.append(request)
        users = [
            m.content.value for m in request.messages if isinstance(m, AgentUserMessage)
        ]
        now = datetime.now(UTC)
        return ProviderResult(
            status=HarnessStatus.SUCCEEDED,
            provider_name=self.name,
            model_id=self.model_id,
            started_at=now,
            ended_at=now,
            final_text="SEEN:" + ",".join(users),
            tool_calls=(),
        )


def _workspace(tmp_path: Path) -> Path:
    cwd = tmp_path / "workspace"
    cwd.mkdir()
    return cwd


def _canonical_history_bytes(message: AgentMessage) -> int:
    total = len(message.content.value.encode("utf-8"))
    if isinstance(message, AgentAssistantMessage):
        total += sum(
            len(call.tool_name.encode("utf-8"))
            + len(call.arguments_json.value.encode("utf-8"))
            for call in message.tool_calls
        )
    return total


def _run(session: CodingSession, cwd: Path, user_inputs: str) -> tuple[str, str]:
    out = io.StringIO()
    err = io.StringIO()
    session.run(
        workspace_root=cwd,
        input_stream=io.StringIO(user_inputs),
        output_stream=out,
        error_stream=err,
    )
    return out.getvalue(), err.getvalue()


def _write_tree_gate(cwd: Path, body: str, *, target: str) -> None:
    extension = cwd / ".pipy" / "extensions" / "tree_gate.py"
    extension.parent.mkdir(parents=True)
    extension.write_text(
        "from pipy_harness.extensions import SessionDecision\n"
        "def activate(api):\n"
        "    @api.on('session_before_tree')\n"
        "    def gate(event, ctx):\n"
        "        assert event.operation == 'tree'\n"
        f"        assert event.target == {target!r}\n"
        f"{body}",
        encoding="utf-8",
    )


class _SessionGate(Protocol):
    def __call__(
        self,
        hooks: Sequence[HookHandler],
        *,
        operation: str,
        cwd: str,
        has_ui: bool,
        target: str | None = None,
        trigger: str | None = None,
        notify_sink: Callable[[str, str], None] | None = None,
        ui_driver: ExtensionUiDriver | None = None,
        model_runtime: ExtensionModelRuntimeControl | None = None,
        flags: Mapping[str, object] | None = None,
        project_trusted: bool = False,
    ) -> SessionDecision: ...


class _TracingSessionGate:
    def __init__(
        self, trace: list[str], delegate: _SessionGate, *, include_details: bool
    ) -> None:
        self._trace = trace
        self._delegate = delegate
        self._include_details = include_details

    def __call__(
        self,
        hooks: Sequence[HookHandler],
        *,
        operation: str,
        cwd: str,
        has_ui: bool,
        target: str | None = None,
        trigger: str | None = None,
        notify_sink: Callable[[str, str], None] | None = None,
        ui_driver: ExtensionUiDriver | None = None,
        model_runtime: ExtensionModelRuntimeControl | None = None,
        flags: Mapping[str, object] | None = None,
        project_trusted: bool = False,
    ) -> SessionDecision:
        event = f"hook:{operation}:{target}" if self._include_details else "hook"
        self._trace.append(event)
        return self._delegate(
            hooks,
            operation=operation,
            cwd=cwd,
            has_ui=has_ui,
            target=target,
            trigger=trigger,
            notify_sink=notify_sink,
            ui_driver=ui_driver,
            model_runtime=model_runtime,
            flags=flags,
            project_trusted=project_trusted,
        )


def test_tool_loop_persists_raw_turns_to_native_session_file(
    tmp_path: Path,
) -> None:
    cwd = _workspace(tmp_path)
    tree = NativeSessionTree.create(cwd, session_dir=tmp_path / "sessions")
    provider = _SeenProvider()
    session = CodingSession(provider=provider, native_session=tree)

    _run(session, cwd, "ROOT\nMAIN\n/exit\n")

    assert tree.path is not None
    body = tree.path.read_text(encoding="utf-8")
    assert "ROOT" in body
    assert "MAIN" in body
    assert "SEEN:ROOT" in body
    # Active-branch user messages accumulate across turns.
    texts = [
        m.content.value
        for m in provider.requests[-1].messages
        if isinstance(m, AgentUserMessage)
    ]
    assert texts == ["ROOT", "MAIN"]


def test_tool_loop_context_reconstructed_from_resumed_tree(
    tmp_path: Path,
) -> None:
    cwd = _workspace(tmp_path)
    session_dir = tmp_path / "sessions"
    seed = NativeSessionTree.create(cwd, session_dir=session_dir)
    seed.append_message(AgentUserMessage(content=ProductContent("ROOT")))
    seed.append_message(AgentAssistantMessage(content=ProductContent("SEEN:ROOT")))
    assert seed.path is not None

    reopened = NativeSessionTree.open(seed.path)
    provider = _SeenProvider()
    session = CodingSession(provider=provider, native_session=reopened)

    _run(session, cwd, "MORE\n/exit\n")

    # The first provider call after resume must carry the prior ROOT context
    # plus the new turn, proving context was rebuilt from the native file.
    users = [
        m.content.value
        for m in provider.requests[0].messages
        if isinstance(m, AgentUserMessage)
    ]
    assert users == ["ROOT", "MORE"]


def test_tool_loop_default_session_is_ephemeral(tmp_path: Path) -> None:
    """With no injected session the loop must not write a session file."""

    cwd = _workspace(tmp_path)
    provider = _SeenProvider()
    session = CodingSession(provider=provider)
    # Should run without creating any persistent native session file.
    _run(session, cwd, "hello\n/exit\n")
    # Provider still saw the turn through an in-memory tree.
    assert provider.requests


def _request_users(request) -> list[str]:  # noqa: ANN001
    return [
        m.content.value for m in request.messages if isinstance(m, AgentUserMessage)
    ]


def test_canonical_tree_branch_scenario(tmp_path: Path) -> None:
    """The docs/session-tree.md canonical scenario, driven like a user.

    ROOT/MAIN build the main branch; ``/tree select`` re-picks the MAIN user
    message and ALT submits a sibling branch; then we navigate back to the
    MAIN branch leaf and continue. Provider context must follow only the
    active branch at each step.
    """

    cwd = _workspace(tmp_path)
    tree = NativeSessionTree.create(cwd, session_dir=tmp_path / "sessions")
    provider = _SeenProvider()
    session = CodingSession(provider=provider, native_session=tree)

    # default-filter visible order after ROOT/MAIN:
    #   1 ROOT(user) 2 SEEN:ROOT(asst) 3 MAIN(user) 4 SEEN:ROOT,MAIN(asst)
    # select 3 -> re-pick MAIN user message, then submit ALT (sibling branch).
    # After ALT, DFS order adds 5 ALT(user) 6 SEEN:ROOT,ALT(asst).
    # select 4 -> SEEN:ROOT,MAIN leaf (non-user), then CONT continues MAIN.
    _run(
        session,
        cwd,
        "\n".join(
            [
                "/name conformance-tree",
                "ROOT",
                "MAIN",
                "/tree select 3",
                "ALT",
                "/tree select 4",
                "CONT",
                "/exit",
                "",
            ]
        ),
    )

    # Native file contains both sibling branches.
    assert tree.path is not None
    body = tree.path.read_text(encoding="utf-8")
    assert "SEEN:ROOT,MAIN" in body
    assert "SEEN:ROOT,ALT" in body
    assert "conformance-tree" in body

    user_sets = [_request_users(r) for r in provider.requests]

    # ALT request: ROOT + ALT, never MAIN.
    alt_requests = [u for u in user_sets if "ALT" in u]
    assert alt_requests
    for users in alt_requests:
        assert "ROOT" in users
        assert "MAIN" not in users

    # CONT request (continuing the MAIN branch): ROOT + MAIN, never ALT.
    cont_requests = [u for u in user_sets if "CONT" in u]
    assert cont_requests
    for users in cont_requests:
        assert "ROOT" in users
        assert "MAIN" in users
        assert "ALT" not in users


def test_tree_outer_dispatch_gate_matrix_preserves_full_argument_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import pipy_harness.native.repl.extension_operations as ops_module

    cwd = _workspace(tmp_path)
    tree = NativeSessionTree.create(cwd, persist=False)
    trace: list[str] = []
    original_gate = ops_module.dispatch_session_before_hooks
    monkeypatch.setattr(
        ops_module,
        "dispatch_session_before_hooks",
        _TracingSessionGate(trace, original_gate, include_details=True),
    )

    provider = _SeenProvider()
    _run(
        CodingSession(provider=provider, native_session=tree),
        cwd,
        "\n".join(
            [
                "/tree",
                "/tree mystery alpha",
                "/tree select   missing",
                "/tree LABEL missing",
                "/tree filter nonsense",
                "/exit",
                "",
            ]
        ),
    )

    assert trace == [
        "hook:tree:select   missing",
        "hook:tree:LABEL missing",
        "hook:tree:filter nonsense",
    ]
    assert provider.requests == []


def test_bare_tree_with_live_terminal_ui_passes_through_serial_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import pipy_harness.native.repl.extension_operations as ops_module
    import pipy_harness.native.repl.session_commands as commands_module

    cwd = _workspace(tmp_path)
    tree = NativeSessionTree.create(cwd, persist=False)
    terminal_ui = TerminalUi(
        input_stream=io.StringIO(),
        terminal_stream=io.StringIO(),
        cwd=cwd,
    )
    trace: list[str] = []
    handled_arguments: list[str] = []
    scripted = iter(("/tree", "/exit"))
    original_gate = ops_module.dispatch_session_before_hooks

    def build_terminal_ui(self: CodingSession, **_kwargs: object) -> TerminalUi:
        del self
        return terminal_ui

    def read_line(self: TerminalUi, prompt_label: str, *, footer: object = None) -> str:
        del self, prompt_label, footer
        return next(scripted)

    def handle_tree(argument: str, **_kwargs: object) -> TreeCommandOutcome:
        handled_arguments.append(argument)
        return TreeCommandOutcome()

    monkeypatch.setattr(
        ops_module,
        "dispatch_session_before_hooks",
        _TracingSessionGate(trace, original_gate, include_details=True),
    )
    monkeypatch.setattr(CodingSession, "_build_terminal_ui", build_terminal_ui)
    monkeypatch.setattr(TerminalUi, "read_line", read_line)
    monkeypatch.setattr(commands_module, "run_tree_command", handle_tree)

    provider = _SeenProvider()
    _run(CodingSession(provider=provider, native_session=tree), cwd, "")

    assert trace == ["hook:tree:None"]
    assert handled_arguments == [""]
    assert provider.requests == []


@pytest.mark.parametrize(
    ("body", "expected_reason"),
    [
        ("        return SessionDecision(allow=False, reason='stay')\n", "stay"),
        ("        raise RuntimeError('private body')\n", "extension tree hook error"),
    ],
)
def test_tree_gate_denial_or_error_cuts_off_mutation_with_standard_footer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    body: str,
    expected_reason: str,
) -> None:
    cwd = _workspace(tmp_path)
    _write_tree_gate(cwd, body, target="label 1 changed")
    tree = NativeSessionTree.create(cwd, persist=False)
    entry = tree.append_message(AgentUserMessage(content=ProductContent("ROOT")))
    footer_calls: list[None] = []

    def record_footer(*_args: object, **_kwargs: object) -> None:
        footer_calls.append(None)

    monkeypatch.setattr(_ChromeFooterEffects, "_print_footer", record_footer)
    provider = _SeenProvider()
    _out, err = _run(
        CodingSession(provider=provider, native_session=tree),
        cwd,
        "/tree label 1 changed\n/exit\n",
    )

    assert f"tree blocked by extension: {expected_reason}" in err
    assert tree.get_label(entry.id) is None
    assert len(tree.get_entries()) == 1
    assert footer_calls == [None, None]
    assert provider.requests == []


@pytest.mark.parametrize("fatal", [KeyboardInterrupt, SystemExit])
def test_tree_gate_controlled_fatal_cuts_off_handler_and_footer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fatal: type[BaseException],
) -> None:
    cwd = _workspace(tmp_path)
    _write_tree_gate(
        cwd, f"        raise {fatal.__name__}()\n", target="label 1 changed"
    )
    tree = NativeSessionTree.create(cwd, persist=False)
    entry = tree.append_message(AgentUserMessage(content=ProductContent("ROOT")))
    footer_calls: list[None] = []

    def record_footer(*_args: object, **_kwargs: object) -> None:
        footer_calls.append(None)

    monkeypatch.setattr(_ChromeFooterEffects, "_print_footer", record_footer)

    with pytest.raises(fatal):
        _run(
            CodingSession(provider=_SeenProvider(), native_session=tree),
            cwd,
            "/tree label 1 changed\n",
        )

    assert tree.get_label(entry.id) is None
    assert len(tree.get_entries()) == 1
    assert footer_calls == [None]


def test_tree_handler_outcome_is_applied_before_footer_and_next_iteration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import pipy_harness.native.repl.session_commands as commands_module

    cwd = _workspace(tmp_path)
    tree = NativeSessionTree.create(cwd, persist=False)
    trace: list[str] = []
    original_diag = emit_diagnostic

    def handle_tree(
        argument: str,
        *,
        session_tree: NativeSessionTree,
        terminal_ui: TerminalUi | None,
        error_stream: TextIO,
        repl_input: object,
        filter_mode: str,
        rebuild_messages: Callable[[], None],
        branch_summary_selection: Callable[[object, str], object] | None = None,
    ) -> TreeCommandOutcome:
        del session_tree, terminal_ui, error_stream, repl_input
        del rebuild_messages, branch_summary_selection
        trace.append(f"handler:{argument}:{filter_mode}")
        if argument == "filter default":
            return TreeCommandOutcome(prefill="RESTORED", filter_mode="all")
        return TreeCommandOutcome()

    def diagnostic(ui: NoticeSink | None, stream: TextIO, message: str) -> None:
        if "editor rehydrated" in message:
            trace.append("prefill")
        original_diag(ui, stream, message)

    def record_footer(*_args: object, **_kwargs: object) -> None:
        trace.append("footer")

    monkeypatch.setattr(commands_module, "run_tree_command", handle_tree)
    for emitter in (
        "pipy_harness.native.repl.session_commands.emit_diagnostic",
        "pipy_harness.native.repl.collaborators.emit_diagnostic",
    ):
        monkeypatch.setattr(emitter, diagnostic)
    monkeypatch.setattr(_ChromeFooterEffects, "_print_footer", record_footer)

    _out, err = _run(
        CodingSession(provider=_SeenProvider(), native_session=tree),
        cwd,
        "/tree filter default\n/tree mystery\n/exit\n",
    )

    assert trace == [
        "footer",
        "handler:filter default:default",
        "footer",
        "prefill",
        "handler:mystery:all",
        "footer",
    ]
    assert "  > RESTORED" in err


def test_tree_noop_selection_still_rebuilds_then_clears_extension_inputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cwd = _workspace(tmp_path)
    tree = NativeSessionTree.create(cwd, persist=False)
    tree.append_message(AgentUserMessage(content=ProductContent("ROOT")))
    leaf = tree.append_message(AgentAssistantMessage(content=ProductContent("ANSWER")))
    trace: list[str] = []
    original_rebuild = CodingProductSessionCoordinator.rebuild_active_history
    original_clear = CodingInputQueue.clear_extension_inputs
    original_diag = emit_diagnostic

    def rebuild(self: CodingProductSessionCoordinator) -> None:
        trace.append("rebuild")
        original_rebuild(self)

    def clear(self: CodingInputQueue) -> None:
        trace.append("clear-extension")
        original_clear(self)

    def diagnostic(ui: NoticeSink | None, stream: TextIO, message: str) -> None:
        trace.append("diagnostic")
        original_diag(ui, stream, message)

    def record_footer(*_args: object, **_kwargs: object) -> None:
        trace.append("footer")

    monkeypatch.setattr(
        CodingProductSessionCoordinator, "rebuild_active_history", rebuild
    )
    monkeypatch.setattr(CodingInputQueue, "clear_extension_inputs", clear)
    for emitter in (
        "pipy_harness.native.repl.session_commands.emit_diagnostic",
        "pipy_harness.native.repl.collaborators.emit_diagnostic",
    ):
        monkeypatch.setattr(emitter, diagnostic)
    monkeypatch.setattr(_ChromeFooterEffects, "_print_footer", record_footer)

    _run(
        CodingSession(provider=_SeenProvider(), native_session=tree),
        cwd,
        f"/tree select {leaf.id}\n/exit\n",
    )

    assert tree.get_leaf_id() == leaf.id
    assert trace == [
        "rebuild",
        "footer",
        "rebuild",
        "clear-extension",
        "diagnostic",
        "footer",
    ]


def test_tree_rebuild_failure_preserves_leaf_and_cuts_off_later_effects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cwd = _workspace(tmp_path)
    tree = NativeSessionTree.create(cwd, persist=False)
    selected = tree.append_message(AgentUserMessage(content=ProductContent("ROOT")))
    tree.append_message(AgentAssistantMessage(content=ProductContent("ANSWER")))
    trace: list[str] = []
    original_rebuild = CodingProductSessionCoordinator.rebuild_active_history
    rebuild_calls = 0

    def rebuild(self: CodingProductSessionCoordinator) -> None:
        nonlocal rebuild_calls
        rebuild_calls += 1
        trace.append("rebuild")
        if rebuild_calls == 2:
            raise RuntimeError("rebuild failed")
        original_rebuild(self)

    def clear(self: CodingInputQueue) -> None:
        trace.append("clear-extension")

    def diagnostic(_ui: TerminalUi | None, _stream: TextIO, _message: str) -> None:
        trace.append("diagnostic")

    def record_footer(*_args: object, **_kwargs: object) -> None:
        trace.append("footer")

    monkeypatch.setattr(
        CodingProductSessionCoordinator, "rebuild_active_history", rebuild
    )
    monkeypatch.setattr(CodingInputQueue, "clear_extension_inputs", clear)
    for emitter in (
        "pipy_harness.native.repl.session_commands.emit_diagnostic",
        "pipy_harness.native.repl.collaborators.emit_diagnostic",
    ):
        monkeypatch.setattr(emitter, diagnostic)
    monkeypatch.setattr(_ChromeFooterEffects, "_print_footer", record_footer)

    with pytest.raises(RuntimeError, match="rebuild failed"):
        _run(
            CodingSession(provider=_SeenProvider(), native_session=tree),
            cwd,
            f"/tree select {selected.id}\n",
        )

    assert tree.get_leaf_id() is None
    assert trace == ["rebuild", "footer", "rebuild"]


def test_name_session_new_and_resume_roundtrip(tmp_path: Path) -> None:
    cwd = _workspace(tmp_path)
    session_dir = tmp_path / "sessions"
    tree = NativeSessionTree.create(cwd, session_dir=session_dir)
    provider = _SeenProvider()
    session = CodingSession(provider=provider, native_session=tree)

    _out, err = _run(
        session,
        cwd,
        "\n".join(["/name first-session", "hello", "/session", "/exit", ""]),
    )
    assert "first-session" in err  # /session status line reports the name
    assert tree.name == "first-session"


def test_new_command_preserves_switch_order_store_and_fresh_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import pipy_harness.native.repl.extension_operations as ops_module

    cwd = _workspace(tmp_path)
    session_dir = tmp_path / "sessions"
    active = NativeSessionTree.create(cwd, session_dir=session_dir)
    active.append_message(AgentUserMessage(content=ProductContent("OLD")))
    assert active.path is not None
    active_path = active.path.resolve()
    trace: list[str] = []
    rebuild_calls = 0

    class TracingProvider(_SeenProvider):
        def complete(
            self, request: ProviderRequest, **kwargs: object
        ) -> ProviderResult:
            trace.append("provider")
            return super().complete(request, **kwargs)

    original_gate = ops_module.dispatch_session_before_hooks
    original_create = NativeSessionTree.create
    original_claim = CanonicalSessionLeaseRegistry.claim
    original_rebuild = CodingProductSessionCoordinator.rebuild_active_history
    original_clear = CodingInputQueue.clear_extension_inputs
    original_diag = emit_diagnostic

    def create(
        path: Path,
        *,
        session_dir: Path | None = None,
        state_root: Path | None = None,
        persist: bool = True,
        session_id: str | None = None,
        parent_session: str | None = None,
        timestamp: str | None = None,
    ) -> NativeSessionTree:
        trace.append(f"create:{session_dir}:{persist}")
        tree = original_create(
            path,
            session_dir=session_dir,
            state_root=state_root,
            persist=persist,
            session_id=session_id,
            parent_session=parent_session,
            timestamp=timestamp,
        )
        tree.header = SessionHeader(
            "EV\x1bIL\x07X", tree.header.timestamp, tree.header.cwd
        )
        return tree

    def claim(path: Path) -> object:
        trace.append(f"claim:{path.resolve()}")
        return original_claim(path)

    def rebuild(self: CodingProductSessionCoordinator) -> None:
        nonlocal rebuild_calls
        rebuild_calls += 1
        trace.append("rebuild")
        if rebuild_calls == 2:
            released_source_during_rebuild = original_claim(active_path)
            released_source_during_rebuild.finish()
        original_rebuild(self)

    def clear(self: CodingInputQueue) -> None:
        trace.append("clear-extension")
        original_clear(self)

    def diagnostic(ui: NoticeSink | None, stream: TextIO, message: str) -> None:
        trace.append("diagnostic")
        original_diag(ui, stream, message)

    monkeypatch.setattr(
        ops_module,
        "dispatch_session_before_hooks",
        _TracingSessionGate(trace, original_gate, include_details=True),
    )
    monkeypatch.setattr(NativeSessionTree, "create", staticmethod(create))
    monkeypatch.setattr(CanonicalSessionLeaseRegistry, "claim", staticmethod(claim))
    monkeypatch.setattr(
        CodingProductSessionCoordinator, "rebuild_active_history", rebuild
    )
    monkeypatch.setattr(CodingInputQueue, "clear_extension_inputs", clear)
    for emitter in (
        "pipy_harness.native.repl.session_commands.emit_diagnostic",
        "pipy_harness.native.repl.collaborators.emit_diagnostic",
    ):
        monkeypatch.setattr(emitter, diagnostic)
    monkeypatch.setattr(
        _ChromeFooterEffects, "_print_footer", lambda *_a, **_k: trace.append("footer")
    )

    provider = TracingProvider()
    _out, err = _run(
        CodingSession(provider=provider, native_session=active),
        cwd,
        "/new\nFRESH\n/exit\n",
    )

    assert trace[:2] == ["rebuild", "footer"]
    initial_claim = f"claim:{active_path}"
    assert trace[2] == initial_claim
    candidate_path = next(
        path.resolve()
        for path in session_dir.glob("*.jsonl")
        if path.resolve() != active_path
    )
    assert trace[3:10] == [
        "hook:switch:new",
        f"create:{session_dir}:True",
        f"claim:{candidate_path}",
        "rebuild",
        "clear-extension",
        "diagnostic",
        "footer",
    ]
    assert trace[10:] == ["provider", "footer"]
    assert _request_users(provider.requests[0]) == ["FRESH"]
    assert len(list(session_dir.glob("*.jsonl"))) == 2
    assert "\x1b" not in err and "\x07" not in err and "EV IL X" in err
    released_source = CanonicalSessionLeaseRegistry.claim(active_path)
    released_source.finish()
    released_candidate = CanonicalSessionLeaseRegistry.claim(candidate_path)
    released_candidate.finish()


def test_new_command_preserves_ephemeral_session_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cwd = _workspace(tmp_path)
    active = NativeSessionTree.create(cwd, persist=False)
    calls: list[tuple[Path | None, bool]] = []
    original_create = NativeSessionTree.create

    def create(
        path: Path,
        *,
        session_dir: Path | None = None,
        state_root: Path | None = None,
        persist: bool = True,
        session_id: str | None = None,
        parent_session: str | None = None,
        timestamp: str | None = None,
    ) -> NativeSessionTree:
        calls.append((session_dir, persist))
        return original_create(
            path,
            session_dir=session_dir,
            state_root=state_root,
            persist=persist,
            session_id=session_id,
            parent_session=parent_session,
            timestamp=timestamp,
        )

    monkeypatch.setattr(NativeSessionTree, "create", staticmethod(create))
    monkeypatch.setattr(
        CanonicalSessionLeaseRegistry,
        "claim",
        staticmethod(
            lambda _path: pytest.fail("ephemeral /new must not claim a lease")
        ),
    )
    provider = _SeenProvider()
    _run(
        CodingSession(provider=provider, native_session=active),
        cwd,
        "/new\n/exit\n",
    )

    assert calls == [(None, False)]
    assert provider.requests == []
    assert list(tmp_path.rglob("*.jsonl")) == []


def test_terminal_new_publication_failure_aborts_candidate_and_retains_source(
    tmp_path: Path,
) -> None:
    source = NativeSessionTree.create(tmp_path, session_dir=tmp_path / "sessions")
    assert source.path is not None
    source_path = source.path.resolve()
    current = [source]
    slot = CanonicalSessionLeaseSlot(CanonicalSessionLeaseRegistry.claim(source_path))
    coordinator = SessionTransitionCoordinator(
        workspace=tmp_path.resolve(),
        get_tree=lambda: current[0],
        set_tree=lambda _tree: (_ for _ in ()).throw(
            LookupError("injected terminal publication failure")
        ),
        session_before_fork=lambda _entry: True,
        session_before_switch=lambda _target: True,
        rebuild=lambda: None,
        clear_extension_inputs=lambda: None,
    )

    try:
        with pytest.raises(LookupError, match="terminal publication failure"):
            coordinator.new_terminal(leases=slot, before_switch=lambda _target: True)
        assert current == [source]
        assert slot.current_path() == source_path
        candidate_path = next(
            path.resolve()
            for path in (tmp_path / "sessions").glob("*.jsonl")
            if path.resolve() != source_path
        )
    finally:
        slot.finish()

    released_source = CanonicalSessionLeaseRegistry.claim(source_path)
    released_source.finish()
    released_candidate = CanonicalSessionLeaseRegistry.claim(candidate_path)
    released_candidate.finish()


def test_new_command_delegates_tree_ownership_to_transition_port() -> None:
    source = inspect.getsource(SessionCommandEffects._execute_new)
    terminal_source, _marker, _fallback = source.partition(
        "# Non-terminal command carriers"
    )

    assert "NativeSessionTree.create" not in terminal_source
    assert ".session_tree =" not in terminal_source
    assert "transition()" in terminal_source


def test_new_lease_conflict_keeps_old_tree_usable_then_releases_source_on_teardown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cwd = _workspace(tmp_path)
    session_dir = tmp_path / "sessions"
    active = NativeSessionTree.create(cwd, session_dir=session_dir)
    active.append_message(AgentUserMessage(content=ProductContent("OLD")))
    assert active.path is not None
    active_path = active.path.resolve()
    original_claim = CanonicalSessionLeaseRegistry.claim

    def claim(path: Path) -> object:
        if path.resolve() != active_path:
            raise RuntimeError("persistent product session is already active")
        return original_claim(path)

    monkeypatch.setattr(CanonicalSessionLeaseRegistry, "claim", staticmethod(claim))
    provider = _SeenProvider()
    _out, err = _run(
        CodingSession(provider=provider, native_session=active),
        cwd,
        "/new\nFRESH\n/exit\n",
    )

    assert "new native session is already active" in err
    assert _request_users(provider.requests[0]) == ["OLD", "FRESH"]
    assert len(list(session_dir.glob("*.jsonl"))) == 2
    released_active = CanonicalSessionLeaseRegistry.claim(active.path)
    released_active.finish()


def _write_new_switch_gate(cwd: Path, body: str) -> None:
    extension = cwd / ".pipy" / "extensions" / "new_gate.py"
    extension.parent.mkdir(parents=True)
    extension.write_text(
        "from pipy_harness.extensions import SessionDecision\n"
        "def activate(api):\n"
        "    @api.on('session_before_switch')\n"
        "    def gate(event, ctx):\n"
        "        assert event.operation == 'switch'\n"
        "        assert event.target == 'new'\n"
        f"{body}",
        encoding="utf-8",
    )


@pytest.mark.parametrize(
    ("body", "expected_reason"),
    [
        ("        return SessionDecision(allow=False, reason='stay')\n", "stay"),
        ("        raise RuntimeError('private body')\n", "extension switch hook error"),
    ],
)
def test_new_switch_gate_blocks_before_create_and_applies_footer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    body: str,
    expected_reason: str,
) -> None:
    cwd = _workspace(tmp_path)
    _write_new_switch_gate(cwd, body)
    session_dir = tmp_path / "sessions"
    active = NativeSessionTree.create(cwd, session_dir=session_dir)
    footer_calls: list[None] = []
    monkeypatch.setattr(
        _ChromeFooterEffects,
        "_print_footer",
        lambda *_args, **_kwargs: footer_calls.append(None),
    )
    provider = _SeenProvider()

    _out, err = _run(
        CodingSession(provider=provider, native_session=active),
        cwd,
        "/new\n/exit\n",
    )

    assert f"switch blocked by extension: {expected_reason}" in err
    assert len(list(session_dir.glob("*.jsonl"))) == 1
    assert footer_calls == [None, None]
    assert provider.requests == []


@pytest.mark.parametrize("fatal", ["KeyboardInterrupt", "SystemExit"])
def test_new_switch_gate_controlled_fatal_cuts_off_create_and_footer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fatal: str
) -> None:
    cwd = _workspace(tmp_path)
    _write_new_switch_gate(cwd, f"        raise {fatal}()\n")
    session_dir = tmp_path / "sessions"
    active = NativeSessionTree.create(cwd, session_dir=session_dir)
    footer_calls: list[None] = []
    monkeypatch.setattr(
        _ChromeFooterEffects,
        "_print_footer",
        lambda *_args, **_kwargs: footer_calls.append(None),
    )

    with pytest.raises(
        KeyboardInterrupt if fatal == "KeyboardInterrupt" else SystemExit
    ):
        _run(
            CodingSession(provider=_SeenProvider(), native_session=active),
            cwd,
            "/new\n",
        )

    assert len(list(session_dir.glob("*.jsonl"))) == 1
    assert footer_calls == [None]


@pytest.mark.parametrize("failure_stage", ["create", "rebuild", "clear"])
def test_new_storage_failure_cuts_off_later_effects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_stage: str,
) -> None:
    import pipy_harness.native.repl.extension_operations as ops_module

    cwd = _workspace(tmp_path)
    session_dir = tmp_path / "sessions"
    active = NativeSessionTree.create(cwd, session_dir=session_dir)
    active.append_message(AgentUserMessage(content=ProductContent("OLD")))
    trace: list[str] = []
    original_gate = ops_module.dispatch_session_before_hooks
    original_create = NativeSessionTree.create
    original_rebuild = CodingProductSessionCoordinator.rebuild_active_history
    rebuild_calls = 0

    def create(
        path: Path,
        *,
        session_dir: Path | None = None,
        state_root: Path | None = None,
        persist: bool = True,
        session_id: str | None = None,
        parent_session: str | None = None,
        timestamp: str | None = None,
    ) -> NativeSessionTree:
        trace.append("create")
        if failure_stage == "create":
            raise RuntimeError("create failed")
        return original_create(
            path,
            session_dir=session_dir,
            state_root=state_root,
            persist=persist,
            session_id=session_id,
            parent_session=parent_session,
            timestamp=timestamp,
        )

    def rebuild(self: CodingProductSessionCoordinator) -> None:
        nonlocal rebuild_calls
        rebuild_calls += 1
        if rebuild_calls == 2 and failure_stage == "rebuild":
            trace.append("rebuild")
            raise RuntimeError("rebuild failed")
        original_rebuild(self)

    def clear(self: CodingInputQueue) -> None:
        trace.append("clear-extension")
        if failure_stage == "clear":
            raise RuntimeError("clear failed")

    monkeypatch.setattr(
        ops_module,
        "dispatch_session_before_hooks",
        _TracingSessionGate(trace, original_gate, include_details=False),
    )
    monkeypatch.setattr(NativeSessionTree, "create", staticmethod(create))
    monkeypatch.setattr(
        CodingProductSessionCoordinator, "rebuild_active_history", rebuild
    )
    monkeypatch.setattr(
        CodingInputQueue,
        "clear_extension_inputs",
        clear,
    )
    monkeypatch.setattr(
        "pipy_harness.native.repl.wiring.emit_diagnostic",
        lambda *_args: trace.append("diagnostic"),
    )
    monkeypatch.setattr(
        _ChromeFooterEffects,
        "_print_footer",
        lambda *_args, **_kwargs: trace.append("footer"),
    )

    with pytest.raises(RuntimeError, match=f"{failure_stage} failed"):
        _run(
            CodingSession(provider=_SeenProvider(), native_session=active),
            cwd,
            "/new\n",
        )

    later = {
        "create": [],
        "rebuild": ["rebuild"],
        "clear": ["clear-extension"],
    }[failure_stage]
    assert trace == ["footer", "hook", "create"] + later
    expected_files = 1 if failure_stage == "create" else 2
    assert len(list(session_dir.glob("*.jsonl"))) == expected_files
    if failure_stage in {"rebuild", "clear"}:
        assert active.path is not None
        candidate_path = next(
            path for path in session_dir.glob("*.jsonl") if path != active.path
        )
        released_candidate = CanonicalSessionLeaseRegistry.claim(candidate_path)
        released_candidate.finish()


def test_name_command_queries_sets_and_persists_without_provider_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cwd = _workspace(tmp_path)
    tree = NativeSessionTree.create(cwd, session_dir=tmp_path / "sessions")
    provider = _SeenProvider()
    footer_calls: list[None] = []

    def record_footer(*_args: object, **_kwargs: object) -> None:
        footer_calls.append(None)

    monkeypatch.setattr(_ChromeFooterEffects, "_print_footer", record_footer)
    unsafe_name = "nm\x1b[31mEVIL\x07"
    _out, err = _run(
        CodingSession(provider=provider, native_session=tree),
        cwd,
        f"/name\n/name alpha   beta\n/name\n/name {unsafe_name}\n/exit\n",
    )

    name_diagnostics = [
        line
        for line in err.splitlines()
        if line.startswith("pipy: current session name:")
        or line.startswith("pipy: session named ")
    ]
    assert name_diagnostics == [
        "pipy: current session name: (unnamed)",
        "pipy: session named 'alpha   beta'.",
        "pipy: current session name: alpha   beta",
        "pipy: session named 'nm\\x1b[31mEVIL\\x07'.",
    ]
    rendered_diagnostics = "\n".join(name_diagnostics)
    assert "\x1b" not in rendered_diagnostics
    assert "\x07" not in rendered_diagnostics
    assert provider.requests == []
    assert footer_calls == [None, None, None, None, None]
    assert [
        entry.name for entry in tree.get_branch() if isinstance(entry, SessionInfoEntry)
    ] == ["alpha   beta", unsafe_name]
    assert tree.path is not None
    assert NativeSessionTree.open(tree.path).name == unsafe_name


def test_fork_creates_new_session_file_with_parent(tmp_path: Path) -> None:
    cwd = _workspace(tmp_path)
    session_dir = tmp_path / "sessions"
    tree = NativeSessionTree.create(cwd, session_dir=session_dir)
    provider = _SeenProvider()
    session = CodingSession(provider=provider, native_session=tree)

    _run(session, cwd, "\n".join(["ROOT", "MAIN", "/fork 1", "/exit", ""]))

    files = sorted((session_dir).glob("*.jsonl"))
    assert len(files) == 2  # original + forked
    # The forked file references the source as parentSession.
    forked = [f for f in files if f != tree.path]
    assert forked
    body = forked[0].read_text(encoding="utf-8")
    assert "parentSession" in body
    assert "ROOT" in body


def test_tree_select_with_summary_records_branch_summary(tmp_path: Path) -> None:
    cwd = _workspace(tmp_path)
    tree = NativeSessionTree.create(cwd, session_dir=tmp_path / "sessions")
    provider = _SeenProvider()
    session = CodingSession(provider=provider, native_session=tree)

    # Build ROOT/MAIN, then re-pick the ROOT user message (index 1) WITH a
    # branch summary of the abandoned MAIN branch, then submit ALT.
    _run(
        session,
        cwd,
        "\n".join(
            ["ROOT", "MAIN", "/tree select 1 summarize:unfinished", "ALT", "/exit", ""]
        ),
    )

    assert tree.path is not None
    body = tree.path.read_text(encoding="utf-8")
    assert '"type": "branch_summary"' in body or '"type":"branch_summary"' in body

    # The branch summary message contributes to the active-branch context.
    reopened = NativeSessionTree.open(tree.path)
    rebuilt = " ".join(
        m.content.value
        for m in reopened.build_context().messages
        if isinstance(m, AgentUserMessage)
    )
    assert "abandoned" in rebuilt.lower()
    summary_request = next(
        request
        for request in provider.requests
        if request.system_prompt.startswith("Summarize the following abandoned")
    )
    assert (summary_request.provider_name, summary_request.model_id) == (
        provider.name,
        provider.model_id,
    )
    assert "Focus on: unfinished." in summary_request.system_prompt
    assert summary_request.user_prompt == "Provide the branch summary now."
    assert summary_request.available_tools == ()


@pytest.mark.parametrize(
    "mutation",
    ["tree", "pointer", "generation", "history", "binding", "publishing"],
)
def test_tree_summary_rejects_provider_callback_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    cwd = _workspace(tmp_path)
    tree = NativeSessionTree.create(cwd, persist=False)

    class MutatingSummaryProvider(_SeenProvider):
        def complete(
            self, request: ProviderRequest, **kwargs: object
        ) -> ProviderResult:
            if request.system_prompt.startswith("Summarize the following abandoned"):
                if mutation == "tree":
                    tree.append_custom("concurrent", {"accepted": True})
                elif mutation == "pointer":
                    assert collaborators[0] is not None
                    collaborators[0].ctl.session_tree = tree
                elif mutation == "generation":
                    assert collaborators[0] is not None
                    generation = collaborators[0].ctl.generation_ref.current
                    collaborators[0].ctl.generation_ref.publish(generation)
                elif mutation == "history":
                    assert collaborators[0] is not None
                    collaborators[0].coding_state.mirror_history(
                        collaborators[0].coding_state.messages
                    )
                elif mutation == "binding":
                    assert collaborators[0] is not None
                    collaborators[0].coding_state.rebind_provider(
                        _SeenProvider(),
                        provider_name="replacement",
                        model_id="replacement-model",
                        usage_accumulator=AgentUsageAccumulator(),
                    )
                else:
                    assert collaborators[0] is not None
                    with collaborators[0].ctl.generation_ref.publishing():
                        pass
            return super().complete(request, **kwargs)

    collaborators: list[SessionCollaborators | None] = [None]
    original_header = SessionCollaborators.active_provider_header_callback

    def capture(owner: SessionCollaborators) -> object:
        collaborators[0] = owner
        return original_header(owner)

    monkeypatch.setattr(
        SessionCollaborators, "active_provider_header_callback", capture
    )
    _out, err = _run(
        CodingSession(provider=MutatingSummaryProvider(), native_session=tree),
        cwd,
        "\n".join(["ROOT", "MAIN", "/tree select 1 summarize", "/exit", ""]),
    )

    assert not any(
        isinstance(entry, BranchSummaryEntry) for entry in tree.get_entries()
    )
    assert "branch summary refused; context changed" in err


@pytest.mark.parametrize(
    "mutation", ["tree", "pointer", "generation", "history", "binding"]
)
def test_tree_summary_rejects_header_callback_staleness_before_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    cwd = _workspace(tmp_path)
    tree = NativeSessionTree.create(cwd, persist=False)
    provider = _SeenProvider()
    original_header = SessionCollaborators.active_provider_header_callback
    original_select = SessionCollaborators.select_with_branch_summary
    in_branch_summary = False
    mutation_count = 0

    def select(
        collaborators: SessionCollaborators, target: SessionEntry, directive: str
    ) -> BranchSummarySelectionResult:
        nonlocal in_branch_summary
        in_branch_summary = True
        try:
            return original_select(collaborators, target, directive)
        finally:
            in_branch_summary = False

    def mutate(collaborators: SessionCollaborators) -> object:
        nonlocal mutation_count
        if not in_branch_summary:
            return original_header(collaborators)
        mutation_count += 1
        if mutation == "tree":
            tree.set_leaf(tree.get_leaf_id())
        elif mutation == "pointer":
            collaborators.ctl.session_tree = tree
        elif mutation == "generation":
            generation = collaborators.ctl.generation_ref.current
            collaborators.ctl.generation_ref.publish(generation)
        elif mutation == "history":
            collaborators.coding_state.mirror_history(
                collaborators.coding_state.messages
            )
        else:
            collaborators.coding_state.refresh_provider(_SeenProvider())
        return original_header(collaborators)

    monkeypatch.setattr(SessionCollaborators, "select_with_branch_summary", select)
    monkeypatch.setattr(SessionCollaborators, "active_provider_header_callback", mutate)
    _out, err = _run(
        CodingSession(provider=provider, native_session=tree),
        cwd,
        "\n".join(["ROOT", "MAIN", "/tree select 1 summarize", "/exit", ""]),
    )

    assert not any(
        request.system_prompt.startswith("Summarize the following abandoned")
        for request in provider.requests
    )
    assert not any(
        isinstance(entry, BranchSummaryEntry) for entry in tree.get_entries()
    )
    assert mutation_count == 1
    assert "branch summary refused; context changed" in err


def test_tree_summary_candidate_failure_precedes_memory_acceptance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cwd = _workspace(tmp_path)
    tree = NativeSessionTree.create(cwd, persist=False)
    accepted: list[object] = []

    original_prepare = NativeSessionTree.prepare_branch_summary

    def invalid_prepare(
        active_tree: NativeSessionTree, parent: str | None, summary: str
    ) -> PreparedBranchSummary:
        prepared = original_prepare(active_tree, parent, summary)
        return replace(
            prepared,
            context=replace(prepared.context, entry_ids=()),
        )

    def unexpected_accept(*args: object, **kwargs: object) -> None:
        accepted.append((args, kwargs))

    monkeypatch.setattr(NativeSessionTree, "prepare_branch_summary", invalid_prepare)
    monkeypatch.setattr(
        CodingProductSessionCoordinator, "accept_active_history", unexpected_accept
    )

    with pytest.raises(ValueError, match="entry_ids must correspond"):
        _run(
            CodingSession(provider=_SeenProvider(), native_session=tree),
            cwd,
            "\n".join(["ROOT", "MAIN", "/tree select 1 summarize", ""]),
        )

    assert accepted == []
    assert not any(
        isinstance(entry, BranchSummaryEntry) for entry in tree.get_entries()
    )


def test_tree_summary_guard_phases_and_post_unlock_diagnostic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cwd = _workspace(tmp_path)
    tree = NativeSessionTree.create(cwd, persist=False)
    owner: list[SessionCollaborators] = []
    trace: list[str] = []
    original_header = SessionCollaborators.active_provider_header_callback
    original_prepare = NativeSessionTree.prepare_branch_summary
    original_tree_accept = NativeSessionTree.accept_prepared_branch_summary
    original_product_accept = CodingProductSessionCoordinator.accept_active_history
    original_clear = CodingInputQueue.clear_extension_inputs
    original_persist = NativeSessionTree.persist_prepared_branch_summary
    original_diag = emit_diagnostic

    def owned(lock: object) -> bool:
        return bool(getattr(lock, "_is_owned")())

    def capture(collaborators: SessionCollaborators) -> object:
        owner.append(collaborators)
        return original_header(collaborators)

    def prepare(
        active_tree: NativeSessionTree, parent: str | None, summary: str
    ) -> object:
        assert owned(active_tree.mutation_lock)
        assert not owned(owner[-1].coding_state.state_lock)
        trace.append("prepare")
        return original_prepare(active_tree, parent, summary)

    def tree_accept(
        active_tree: NativeSessionTree, prepared: PreparedBranchSummary
    ) -> object:
        assert owned(active_tree.mutation_lock)
        assert owned(owner[-1].coding_state.state_lock)
        trace.append("tree-accept")
        return original_tree_accept(active_tree, prepared)

    def product_accept(
        product: CodingProductSessionCoordinator,
        context: CodingProductSessionContext,
    ) -> None:
        assert owned(tree.mutation_lock)
        assert owned(owner[-1].coding_state.state_lock)
        trace.append("product-accept")
        original_product_accept(product, context)

    def clear(queue: CodingInputQueue) -> None:
        assert owned(tree.mutation_lock)
        assert not owned(owner[-1].coding_state.state_lock)
        trace.append("clear")
        original_clear(queue)

    def persist(active_tree: NativeSessionTree, entry: BranchSummaryEntry) -> None:
        assert owned(active_tree.mutation_lock)
        assert not owned(owner[-1].coding_state.state_lock)
        assert active_tree.get_entry(entry.id) is entry
        trace.append("persist")
        original_persist(active_tree, entry)

    def diagnostic(ui: NoticeSink | None, stream: TextIO, message: str) -> None:
        if "recorded branch summary" in message:
            assert not owned(tree.mutation_lock)
            assert not owned(owner[-1].coding_state.state_lock)
            trace.append("diagnostic")
        original_diag(ui, stream, message)

    monkeypatch.setattr(
        SessionCollaborators, "active_provider_header_callback", capture
    )
    monkeypatch.setattr(NativeSessionTree, "prepare_branch_summary", prepare)
    monkeypatch.setattr(
        NativeSessionTree, "accept_prepared_branch_summary", tree_accept
    )
    monkeypatch.setattr(
        CodingProductSessionCoordinator, "accept_active_history", product_accept
    )
    monkeypatch.setattr(CodingInputQueue, "clear_extension_inputs", clear)
    monkeypatch.setattr(NativeSessionTree, "persist_prepared_branch_summary", persist)
    monkeypatch.setattr(
        "pipy_harness.native.repl.session_commands.emit_diagnostic", diagnostic
    )

    _run(
        CodingSession(provider=_SeenProvider(), native_session=tree),
        cwd,
        "\n".join(["ROOT", "MAIN", "/tree select 1 summarize", "/exit", ""]),
    )

    assert trace == [
        "prepare",
        "tree-accept",
        "product-accept",
        "clear",
        "persist",
        "diagnostic",
    ]


def test_tree_summary_append_failure_keeps_accepted_state_and_clears_inputs_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cwd = _workspace(tmp_path)
    tree = NativeSessionTree.create(cwd, persist=False)
    trace: list[str] = []
    original_clear = CodingInputQueue.clear_extension_inputs
    original_accept = CodingProductSessionCoordinator.accept_active_history
    accepted: list[
        tuple[CodingProductSessionCoordinator, CodingProductSessionContext]
    ] = []
    collaborators: list[SessionCollaborators] = []
    original_header = SessionCollaborators.active_provider_header_callback

    def capture(owner: SessionCollaborators) -> object:
        collaborators.append(owner)
        return original_header(owner)

    def clear(queue: CodingInputQueue) -> None:
        trace.append("clear")
        original_clear(queue)

    def fail_persist(active_tree: NativeSessionTree, entry: BranchSummaryEntry) -> None:
        assert active_tree is tree
        assert active_tree.get_entry(entry.id) is entry
        owner, context = accepted[-1]
        assert collaborators[-1].coding_state.messages == context.messages
        fresh = active_tree.build_coding_context()
        fresh_context = CodingProductSessionContext(
            messages=fresh.messages,
            prior_summary=(
                ProductContent(fresh.prior_summary)
                if fresh.prior_summary is not None
                else None
            ),
            entry_ids=fresh.entry_ids,
        )
        assert (
            owner.resolve_entry_id(
                collaborators[-1].coding_state.messages[-1], fresh_context
            )
            == entry.id
        )
        trace.append("persist")
        raise OSError("branch append failed")

    def accept(
        owner: CodingProductSessionCoordinator, context: CodingProductSessionContext
    ) -> None:
        original_accept(owner, context)
        accepted.append((owner, context))

    monkeypatch.setattr(CodingInputQueue, "clear_extension_inputs", clear)
    monkeypatch.setattr(
        SessionCollaborators, "active_provider_header_callback", capture
    )
    monkeypatch.setattr(
        NativeSessionTree, "persist_prepared_branch_summary", fail_persist
    )
    monkeypatch.setattr(
        CodingProductSessionCoordinator, "accept_active_history", accept
    )

    with pytest.raises(OSError, match="branch append failed"):
        _run(
            CodingSession(provider=_SeenProvider(), native_session=tree),
            cwd,
            "\n".join(["ROOT", "MAIN", "/tree select 1 summarize", ""]),
        )

    summaries = [
        entry for entry in tree.get_entries() if isinstance(entry, BranchSummaryEntry)
    ]
    assert len(summaries) == 1
    assert len(accepted) == 1
    assert tree.get_leaf_id() == summaries[0].id
    assert trace == ["clear", "persist"]


def test_resume_rename_and_delete_with_confirmation(tmp_path: Path) -> None:
    cwd = _workspace(tmp_path)
    session_dir = tmp_path / "sessions"
    first = NativeSessionTree.create(cwd, session_dir=session_dir)
    first.append_message(AgentUserMessage(content=ProductContent("seed")))
    first_id = first.session_id

    active = NativeSessionTree.create(cwd, session_dir=session_dir)
    provider = _SeenProvider()
    session = CodingSession(provider=provider, native_session=active)
    _out, err = _run(
        session,
        cwd,
        "\n".join(
            [
                f"/resume rename {first_id[:6]} renamed-session",
                # Delete without confirmation is refused, then confirmed.
                f"/resume delete {first_id[:6]}",
                f"/resume delete {first_id[:6]} --yes",
                "/exit",
                "",
            ]
        ),
    )
    assert "renamed" in err
    assert "needs confirmation" in err
    # The first session file is gone; the active session file remains.
    remaining = {p.name for p in session_dir.glob("*.jsonl")}
    assert active.path is not None
    assert active.path.name in remaining
    assert all(first_id not in name for name in remaining)


def _resume_fixture(
    tmp_path: Path,
) -> tuple[Path, Path, NativeSessionTree, NativeSessionTree]:
    cwd = _workspace(tmp_path)
    session_dir = tmp_path / "native-sessions"
    active = NativeSessionTree.create(cwd, session_dir=session_dir)
    active.append_message(AgentUserMessage(content=ProductContent("ACTIVE")))
    selected = NativeSessionTree.create(cwd, session_dir=session_dir)
    selected.append_session_info("selected")
    selected.append_message(AgentUserMessage(content=ProductContent("SELECTED")))
    assert active.path is not None and selected.path is not None
    return cwd, session_dir, active, selected


def _install_resume_terminal(
    monkeypatch: pytest.MonkeyPatch,
    *,
    cwd: Path,
    commands: Sequence[str],
) -> TerminalUi:
    terminal_ui = TerminalUi(
        input_stream=io.StringIO(),
        terminal_stream=io.StringIO(),
        cwd=cwd,
    )
    scripted = iter(commands)

    def build_terminal_ui(self: CodingSession, **_kwargs: object) -> TerminalUi:
        del self
        return terminal_ui

    def read_line(self: TerminalUi, prompt_label: str, *, footer: object = None) -> str:
        del self, prompt_label, footer
        return next(scripted)

    def wait_for_turn(
        self: TerminalUi,
        done_event: object,
        abort_event: object,
        *,
        poll_seconds: float = 0.05,
        accept_queue: bool = False,
        accept_commands: bool = False,
    ) -> str:
        del self, done_event, abort_event, poll_seconds
        del accept_queue, accept_commands
        return "settled"

    monkeypatch.setattr(CodingSession, "_build_terminal_ui", build_terminal_ui)
    monkeypatch.setattr(TerminalUi, "read_line", read_line)
    monkeypatch.setattr(TerminalUi, "wait_for_active_turn_interrupt", wait_for_turn)
    return terminal_ui


def _write_resume_switch_gate(cwd: Path, body: str, *, target: Path) -> None:
    extension = cwd / ".pipy" / "extensions" / "resume_gate.py"
    extension.parent.mkdir(parents=True, exist_ok=True)
    extension.write_text(
        "from pipy_harness.extensions import SessionDecision\n"
        "def activate(api):\n"
        "    @api.on('session_before_switch')\n"
        "    def gate(event, ctx):\n"
        "        assert event.operation == 'switch'\n"
        f"        assert event.target == {str(target)!r}\n"
        f"{body}",
        encoding="utf-8",
    )


def test_resume_local_management_is_ungated_and_archive_safe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import pipy_harness.native.repl.extension_operations as ops_module

    cwd, session_dir, active, selected = _resume_fixture(tmp_path)
    deleted = NativeSessionTree.create(cwd, session_dir=session_dir)
    deleted.append_message(AgentUserMessage(content=ProductContent("DELETE")))
    assert selected.path is not None and deleted.path is not None
    archive = tmp_path / "metadata-archive" / "record.jsonl"
    archive.parent.mkdir()
    archive.write_bytes(b'{"safe":"sentinel"}\n')
    before = archive.read_bytes()
    trace: list[str] = []
    original_gate = ops_module.dispatch_session_before_hooks
    monkeypatch.setattr(
        ops_module,
        "dispatch_session_before_hooks",
        _TracingSessionGate(trace, original_gate, include_details=True),
    )
    provider = _SeenProvider()

    _run(
        CodingSession(provider=provider, native_session=active),
        cwd,
        "\n".join(
            [
                "/resume",
                "/resume NaMeD",
                f"/resume rename {selected.session_id[:8]} renamed locally",
                f"/resume delete {deleted.session_id[:8]} --yes",
                "/exit",
                "",
            ]
        ),
    )

    assert trace == []
    assert NativeSessionTree.open(selected.path).name == "renamed locally"
    assert not deleted.path.exists()
    assert archive.read_bytes() == before
    assert provider.requests == []


@pytest.mark.parametrize("picker_result", ["cancel", "current"])
def test_live_resume_cancel_and_current_selection_are_ungated_noops(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    picker_result: str,
) -> None:
    import pipy_harness.native.repl.extension_operations as ops_module
    import pipy_harness.native.repl.session_commands as commands_module

    cwd, _session_dir, active, _selected = _resume_fixture(tmp_path)
    _install_resume_terminal(monkeypatch, cwd=cwd, commands=("/resume", "/exit"))
    trace: list[str] = []
    open_calls: list[Path] = []
    original_gate = ops_module.dispatch_session_before_hooks
    original_open = NativeSessionTree.open

    def pick(
        *,
        session_tree: NativeSessionTree,
        terminal_ui: TerminalUi,
    ) -> Path | None:
        del terminal_ui
        return None if picker_result == "cancel" else session_tree.path

    def open_tree(
        path: Path, *, persist: bool = True, strict: bool = False
    ) -> NativeSessionTree:
        open_calls.append(path)
        return original_open(path, persist=persist, strict=strict)

    monkeypatch.setattr(
        ops_module,
        "dispatch_session_before_hooks",
        _TracingSessionGate(trace, original_gate, include_details=True),
    )
    monkeypatch.setattr(commands_module, "run_interactive_session_picker", pick)
    monkeypatch.setattr(NativeSessionTree, "open", staticmethod(open_tree))
    provider = _SeenProvider()

    _run(CodingSession(provider=provider, native_session=active), cwd, "")

    assert trace == []
    assert open_calls == []
    assert provider.requests == []


@pytest.mark.parametrize("selection_mode", ["picker", "direct"])
def test_resume_switch_order_gate_and_fresh_history(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    selection_mode: str,
) -> None:
    import pipy_harness.native.repl.extension_operations as ops_module
    import pipy_harness.native.repl.session_commands as commands_module

    cwd, _session_dir, active, selected = _resume_fixture(tmp_path)
    selected_path = selected.path
    assert selected_path is not None
    command = "/resume" if selection_mode == "picker" else f"/resume {selected.path}"
    _install_resume_terminal(monkeypatch, cwd=cwd, commands=(command, "FRESH", "/exit"))
    trace: list[str] = []
    original_gate = ops_module.dispatch_session_before_hooks
    original_open = NativeSessionTree.open
    original_rebuild = CodingProductSessionCoordinator.rebuild_active_history
    original_clear = CodingInputQueue.clear_extension_inputs
    original_diag = emit_diagnostic

    def pick(
        *,
        session_tree: NativeSessionTree,
        terminal_ui: TerminalUi,
    ) -> Path | None:
        del session_tree, terminal_ui
        return selected.path

    def open_tree(
        path: Path, *, persist: bool = True, strict: bool = False
    ) -> NativeSessionTree:
        trace.append("open")
        return original_open(path, persist=persist, strict=strict)

    def rebuild(self: CodingProductSessionCoordinator) -> None:
        trace.append("rebuild")
        original_rebuild(self)

    def clear(self: CodingInputQueue) -> None:
        trace.append("clear-extension")
        original_clear(self)

    def redraw(self: TranscriptComponent, entries: object) -> None:
        del self, entries
        trace.append("redraw")

    def diagnostic(ui: NoticeSink | None, stream: TextIO, message: str) -> None:
        if message.startswith("pipy: resumed native session"):
            trace.append("diagnostic")
        original_diag(ui, stream, message)

    monkeypatch.setattr(
        ops_module,
        "dispatch_session_before_hooks",
        _TracingSessionGate(trace, original_gate, include_details=True),
    )
    monkeypatch.setattr(commands_module, "run_interactive_session_picker", pick)
    monkeypatch.setattr(NativeSessionTree, "open", staticmethod(open_tree))
    monkeypatch.setattr(
        CodingProductSessionCoordinator, "rebuild_active_history", rebuild
    )
    monkeypatch.setattr(CodingInputQueue, "clear_extension_inputs", clear)
    monkeypatch.setattr(TranscriptComponent, "redraw_custom_entries", redraw)
    for emitter in (
        "pipy_harness.native.repl.session_commands.emit_diagnostic",
        "pipy_harness.native.repl.collaborators.emit_diagnostic",
    ):
        monkeypatch.setattr(emitter, diagnostic)
    monkeypatch.setattr(
        _ChromeFooterEffects,
        "_print_footer",
        lambda *_args, **_kwargs: trace.append("footer"),
    )
    provider = _SeenProvider()

    _run(CodingSession(provider=provider, native_session=active), cwd, "")

    expected_switch = [
        f"hook:switch:{selected.path}",
        "open",
        "rebuild",
        "clear-extension",
        "redraw",
        "diagnostic",
    ]
    assert trace == ["rebuild", *expected_switch]
    assert _request_users(provider.requests[0]) == ["SELECTED", "FRESH"]


def test_direct_resume_of_active_path_is_a_noop_before_gate_or_load(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import pipy_harness.native.repl.extension_operations as ops_module

    cwd, _session_dir, active, _selected = _resume_fixture(tmp_path)
    assert active.path is not None
    trace: list[str] = []
    footer_calls: list[None] = []
    original_gate = ops_module.dispatch_session_before_hooks
    original_open = NativeSessionTree.open

    def open_tree(
        path: Path, *, persist: bool = True, strict: bool = False
    ) -> NativeSessionTree:
        trace.append(f"open:{path}")
        return original_open(path, persist=persist, strict=strict)

    monkeypatch.setattr(
        ops_module,
        "dispatch_session_before_hooks",
        _TracingSessionGate(trace, original_gate, include_details=True),
    )
    monkeypatch.setattr(NativeSessionTree, "open", staticmethod(open_tree))
    monkeypatch.setattr(
        _ChromeFooterEffects,
        "_print_footer",
        lambda *_args, **_kwargs: footer_calls.append(None),
    )
    provider = _SeenProvider()

    _run(
        CodingSession(provider=provider, native_session=active),
        cwd,
        f"/resume {active.path.parent / '.' / active.path.name}\n/exit\n",
    )

    assert trace == []
    assert footer_calls == [None, None]
    assert provider.requests == []


def test_resume_lease_conflict_keeps_old_tree_usable_and_releases_adopted_path(
    tmp_path: Path,
) -> None:
    cwd, _session_dir, active, selected = _resume_fixture(tmp_path)
    assert active.path is not None and selected.path is not None
    held = CanonicalSessionLeaseRegistry.claim(selected.path)
    provider = _SeenProvider()
    try:
        _out, err = _run(
            CodingSession(provider=provider, native_session=active),
            cwd,
            f"/resume {selected.path}\nFRESH\n/exit\n",
        )
        assert "selected native session is already active" in err
        assert _request_users(provider.requests[0]) == ["ACTIVE", "FRESH"]
    finally:
        held.finish()

    _run(
        CodingSession(provider=_SeenProvider(), native_session=active),
        cwd,
        f"/resume {selected.path}\n/exit\n",
    )
    released_active = CanonicalSessionLeaseRegistry.claim(active.path)
    released_active.finish()
    released_selected = CanonicalSessionLeaseRegistry.claim(selected.path)
    released_selected.finish()


def test_resume_foreign_workspace_keeps_old_tree_usable_and_releases_candidate(
    tmp_path: Path,
) -> None:
    cwd, session_dir, active, _selected = _resume_fixture(tmp_path)
    assert active.path is not None
    foreign_workspace = tmp_path / "foreign-workspace"
    foreign_workspace.mkdir()
    foreign = NativeSessionTree.create(foreign_workspace, session_dir=session_dir)
    assert foreign.path is not None
    provider = _SeenProvider()

    _out, err = _run(
        CodingSession(provider=provider, native_session=active),
        cwd,
        f"/resume {foreign.path}\nFRESH\n/exit\n",
    )

    assert "unable to load the selected native session" in err
    assert _request_users(provider.requests[0]) == ["ACTIVE", "FRESH"]
    released_active = CanonicalSessionLeaseRegistry.claim(active.path)
    released_active.finish()
    released_foreign = CanonicalSessionLeaseRegistry.claim(foreign.path)
    released_foreign.finish()


def test_resume_claims_candidate_before_strict_load(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cwd, _session_dir, active, selected = _resume_fixture(tmp_path)
    selected_path = selected.path
    assert selected_path is not None
    import pipy_harness.native.repl.session_transition as transition_module

    trace: list[str] = []
    original_claim = CanonicalSessionLeaseRegistry.claim
    original_open = NativeSessionTree.open

    def claim(path: Path) -> object:
        trace.append(f"claim:{path.resolve()}")
        return original_claim(path)

    def open_tree(
        path: Path, *, persist: bool = True, strict: bool = False
    ) -> NativeSessionTree:
        assert strict is True
        assert trace[-1] == f"claim:{selected_path.resolve()}"
        trace.append("open")
        return original_open(path, persist=persist, strict=strict)

    monkeypatch.setattr(
        transition_module.CanonicalSessionLeaseRegistry, "claim", staticmethod(claim)
    )
    monkeypatch.setattr(NativeSessionTree, "open", staticmethod(open_tree))

    _run(
        CodingSession(provider=_SeenProvider(), native_session=active),
        cwd,
        f"/resume {selected_path}\n/exit\n",
    )

    assert trace[-2:] == [f"claim:{selected_path.resolve()}", "open"]


@pytest.mark.parametrize(
    ("body", "expected_reason"),
    [
        ("        return SessionDecision(allow=False, reason='stay')\n", "stay"),
        ("        raise RuntimeError('private body')\n", "extension switch hook error"),
    ],
)
def test_resume_switch_gate_denial_or_error_cuts_off_open_with_footer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    body: str,
    expected_reason: str,
) -> None:
    cwd, _session_dir, active, selected = _resume_fixture(tmp_path)
    assert selected.path is not None
    _write_resume_switch_gate(cwd, body, target=selected.path)
    open_calls: list[Path] = []
    footer_calls: list[None] = []
    original_open = NativeSessionTree.open

    def open_tree(
        path: Path, *, persist: bool = True, strict: bool = False
    ) -> NativeSessionTree:
        open_calls.append(path)
        return original_open(path, persist=persist, strict=strict)

    monkeypatch.setattr(NativeSessionTree, "open", staticmethod(open_tree))
    monkeypatch.setattr(
        _ChromeFooterEffects,
        "_print_footer",
        lambda *_args, **_kwargs: footer_calls.append(None),
    )
    provider = _SeenProvider()

    _out, err = _run(
        CodingSession(provider=provider, native_session=active),
        cwd,
        f"/resume {selected.path}\n/exit\n",
    )

    assert f"switch blocked by extension: {expected_reason}" in err
    assert open_calls == []
    assert footer_calls == [None, None]
    assert provider.requests == []


@pytest.mark.parametrize("fatal", [KeyboardInterrupt, SystemExit])
def test_resume_switch_gate_fatal_cuts_off_open_and_footer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fatal: type[BaseException],
) -> None:
    cwd, _session_dir, active, selected = _resume_fixture(tmp_path)
    assert selected.path is not None
    _write_resume_switch_gate(
        cwd, f"        raise {fatal.__name__}()\n", target=selected.path
    )
    open_calls: list[Path] = []
    footer_calls: list[None] = []
    original_open = NativeSessionTree.open

    def open_tree(
        path: Path, *, persist: bool = True, strict: bool = False
    ) -> NativeSessionTree:
        open_calls.append(path)
        return original_open(path, persist=persist, strict=strict)

    monkeypatch.setattr(NativeSessionTree, "open", staticmethod(open_tree))
    monkeypatch.setattr(
        _ChromeFooterEffects,
        "_print_footer",
        lambda *_args, **_kwargs: footer_calls.append(None),
    )

    with pytest.raises(fatal):
        _run(
            CodingSession(provider=_SeenProvider(), native_session=active),
            cwd,
            f"/resume {selected.path}\n",
        )

    assert open_calls == []
    assert footer_calls == [None]


@pytest.mark.parametrize("failure_stage", ["open", "rebuild", "clear", "redraw"])
def test_resume_switch_failure_timing_cuts_off_later_effects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_stage: str,
) -> None:
    import pipy_harness.native.repl.extension_operations as ops_module

    cwd, _session_dir, active, selected = _resume_fixture(tmp_path)
    assert selected.path is not None
    _install_resume_terminal(
        monkeypatch, cwd=cwd, commands=(f"/resume {selected.path}", "/exit")
    )
    trace: list[str] = []
    rebuild_calls = 0
    original_gate = ops_module.dispatch_session_before_hooks
    original_open = NativeSessionTree.open
    original_rebuild = CodingProductSessionCoordinator.rebuild_active_history

    def open_tree(
        path: Path, *, persist: bool = True, strict: bool = False
    ) -> NativeSessionTree:
        trace.append("open")
        if failure_stage == "open":
            raise RuntimeError("open failed")
        return original_open(path, persist=persist, strict=strict)

    def rebuild(self: CodingProductSessionCoordinator) -> None:
        nonlocal rebuild_calls
        rebuild_calls += 1
        if rebuild_calls == 2:
            trace.append("rebuild")
            if failure_stage == "rebuild":
                raise RuntimeError("rebuild failed")
        original_rebuild(self)

    def clear(self: CodingInputQueue) -> None:
        trace.append("clear")
        if failure_stage == "clear":
            raise RuntimeError("clear failed")

    def redraw(self: TranscriptComponent, entries: object) -> None:
        del self, entries
        trace.append("redraw")
        if failure_stage == "redraw":
            raise RuntimeError("redraw failed")

    monkeypatch.setattr(
        ops_module,
        "dispatch_session_before_hooks",
        _TracingSessionGate(trace, original_gate, include_details=False),
    )
    monkeypatch.setattr(NativeSessionTree, "open", staticmethod(open_tree))
    monkeypatch.setattr(
        CodingProductSessionCoordinator, "rebuild_active_history", rebuild
    )
    monkeypatch.setattr(CodingInputQueue, "clear_extension_inputs", clear)
    monkeypatch.setattr(TranscriptComponent, "redraw_custom_entries", redraw)
    monkeypatch.setattr(
        _ChromeFooterEffects,
        "_print_footer",
        lambda *_args, **_kwargs: trace.append("footer"),
    )

    if failure_stage == "open":
        _run(CodingSession(provider=_SeenProvider(), native_session=active), cwd, "")
    else:
        expected_exception = (
            ProductSessionTransitionError
            if failure_stage in {"rebuild", "clear"}
            else RuntimeError
        )
        with pytest.raises(expected_exception):
            _run(
                CodingSession(provider=_SeenProvider(), native_session=active),
                cwd,
                "",
            )

    expected = ["hook", "open"]
    if failure_stage != "open":
        expected.append("rebuild")
    if failure_stage in {"clear", "redraw"}:
        expected.append("clear")
    if failure_stage == "redraw":
        expected.append("redraw")
    assert trace == expected
    assert active.path is not None and selected.path is not None
    released_active = CanonicalSessionLeaseRegistry.claim(active.path)
    released_active.finish()
    released_selected = CanonicalSessionLeaseRegistry.claim(selected.path)
    released_selected.finish()


def test_resume_success_diagnostic_is_sanitized_and_local(
    tmp_path: Path,
) -> None:
    cwd, _session_dir, active, selected = _resume_fixture(tmp_path)
    assert selected.path is not None
    selected.append_session_info("nm\x1b[31mEVIL\x07")
    provider = _SeenProvider()

    _out, err = _run(
        CodingSession(provider=provider, native_session=active),
        cwd,
        f"/resume {selected.path}\n/exit\n",
    )

    assert "resumed native session" in err and "EVIL" in err
    assert "\x1b" not in err and "\x07" not in err
    assert provider.requests == []


def test_durable_compaction_entry_survives_reload(tmp_path: Path) -> None:
    cwd = _workspace(tmp_path)
    session_dir = tmp_path / "sessions"
    tree = NativeSessionTree.create(cwd, session_dir=session_dir)
    provider = _SeenProvider()
    session = CodingSession(provider=provider, native_session=tree)

    # Four user turns then /compact -> a durable compaction entry is appended.
    _run(
        session,
        cwd,
        "\n".join(["a", "b", "c", "d", "/compact", "e", "/exit", ""]),
    )

    assert tree.path is not None
    body = tree.path.read_text(encoding="utf-8")
    assert '"type": "compaction"' in body or '"type":"compaction"' in body

    branch = tree.get_branch()
    compaction = next(entry for entry in branch if isinstance(entry, CompactionEntry))
    compaction_index = branch.index(compaction)
    messages_before = [
        entry for entry in branch[:compaction_index] if isinstance(entry, MessageEntry)
    ]
    users_before = [
        entry
        for entry in messages_before
        if isinstance(entry.message, AgentUserMessage)
    ]
    assert compaction.first_kept_entry_id == users_before[-2].id
    assert compaction.summary == "SEEN:a,b,Provide the combined context summary now."
    canonical_messages_before: list[AgentMessage] = []
    for entry in messages_before:
        if isinstance(
            entry.message,
            (AgentUserMessage, AgentAssistantMessage, AgentToolResultMessage),
        ):
            canonical_messages_before.append(entry.message)
    assert len(canonical_messages_before) == len(messages_before)
    expected_bytes = 0
    for message in canonical_messages_before:
        expected_bytes += _canonical_history_bytes(message)
    assert compaction.tokens_before == expected_bytes

    reopened = NativeSessionTree.open(tree.path)
    rebuilt = reopened.build_context().messages
    texts = " ".join(
        m.content.value for m in rebuilt if isinstance(m, AgentUserMessage)
    )
    # The compaction summary message is present on reload.
    assert any(
        "compacted" in m.content.value.lower()
        for m in rebuilt
        if isinstance(m, AgentUserMessage)
    )
    # Oldest dropped turn 'a' is no longer a standalone user message.
    assert " a " not in f" {texts} "


@pytest.mark.parametrize("entry_kind", ["custom", "branch"])
def test_compaction_resolves_duplicate_projected_users_by_loaded_origin(
    tmp_path: Path, entry_kind: str
) -> None:
    cwd = _workspace(tmp_path)
    tree = NativeSessionTree.create(cwd, session_dir=tmp_path / "sessions")
    tree.append_message(AgentUserMessage(ProductContent("old")))
    if entry_kind == "custom":
        dropped_id = tree.append_custom_message("note", "duplicate").id
        kept_id = tree.append_custom_message("note", "duplicate").id
    else:
        dropped_id = tree.branch_with_summary(tree.get_leaf_id(), "duplicate").id
        kept_id = tree.branch_with_summary(tree.get_leaf_id(), "duplicate").id
    tree.append_message(AgentUserMessage(ProductContent("tail")))
    session = CodingSession(provider=_SeenProvider(), native_session=tree)
    _run(session, cwd, "/compact\n/exit\n")
    assert session._coding_state.compaction_count == 1
    compaction = tree.get_leaf_entry()
    assert isinstance(compaction, CompactionEntry)
    assert compaction.first_kept_entry_id == kept_id
    assert compaction.first_kept_entry_id != dropped_id
    assert tree.path is not None
    reopened = NativeSessionTree.open(tree.path).build_coding_context()
    assert reopened.messages == session._coding_state.messages
    assert reopened.entry_ids[0] == kept_id


def test_resume_and_new_replace_destination_summary_without_resetting_run_counters(
    tmp_path: Path,
) -> None:
    cwd = _workspace(tmp_path)
    compacted = NativeSessionTree.create(cwd, session_dir=tmp_path / "sessions")
    compacted.append_message(AgentUserMessage(ProductContent("discarded")))
    retained = compacted.append_message(AgentUserMessage(ProductContent("retained")))
    compacted.append_compaction(
        summary="destination summary", first_kept_entry_id=retained.id, tokens_before=1
    )
    plain = NativeSessionTree.create(cwd, session_dir=tmp_path / "sessions")
    plain.append_message(AgentUserMessage(ProductContent("plain")))
    assert compacted.path is not None and plain.path is not None
    provider = _SeenProvider()
    session = CodingSession(provider=provider)
    _run(
        session,
        cwd,
        (
            "a\nb\nc\nd\n/compact\n"
            f"/resume {compacted.path}\ncontinued\n"
            f"/resume {plain.path}\nplain continuation\n"
            f"/resume {compacted.path}\n/new\nfresh\n/exit\n"
        ),
    )
    assert session._coding_state.compaction_count == 1
    assert session._coding_state.compaction_dropped_group_count == 2
    continued, uncompacted, fresh = provider.requests[-3:]
    assert continued.system_prompt.endswith("\n\ndestination summary")
    assert [
        m.content.value for m in continued.messages if isinstance(m, AgentUserMessage)
    ] == ["retained", "continued"]
    assert "destination summary" not in uncompacted.system_prompt
    assert [
        m.content.value for m in uncompacted.messages if isinstance(m, AgentUserMessage)
    ] == ["plain", "plain continuation"]
    assert "destination summary" not in fresh.system_prompt
    assert [
        m.content.value for m in fresh.messages if isinstance(m, AgentUserMessage)
    ] == ["fresh"]


class _StubPickerUi:
    """Minimal terminal-ui stand-in exposing only ``run_session_picker``."""

    def __init__(self, choose):
        self._choose = choose
        self.kwargs = None
        self.components = SimpleNamespace(modals=self)

    def run_session_picker(self, **kwargs):
        self.kwargs = kwargs
        return self._choose(kwargs)


def test_interactive_resume_picker_wiring(tmp_path: Path) -> None:
    sessions_dir = tmp_path / "store" / "proj"
    active = NativeSessionTree.create(tmp_path / "ws", session_dir=sessions_dir)
    active.append_message(AgentUserMessage(content=ProductContent("ACTIVE")))
    other = NativeSessionTree.create(tmp_path / "ws", session_dir=sessions_dir)
    other.append_session_info("other-name")
    other.append_message(AgentUserMessage(content=ProductContent("OTHER")))

    assert active.path is not None and other.path is not None
    other_path = other.path

    # The stub picker chooses the `other` session file.
    ui = _StubPickerUi(lambda kw: other.path)
    chosen = run_interactive_session_picker(
        session_tree=active,
        terminal_ui=ui,  # type: ignore[arg-type]
    )
    assert chosen == other.path
    # The picker received both project sessions and the active session as current.
    listed_ids = {e.session_id for e in ui.kwargs["project_sessions"]}
    assert {active.session_id, other.session_id} <= listed_ids
    assert ui.kwargs["current_path"] == active.path

    # The rename callback persists a session name through the native store.
    ui.kwargs["on_rename"](other_path, "renamed-other")
    assert NativeSessionTree.open(other_path).name == "renamed-other"

    # The delete callback removes only the native session file.
    ok, _detail = ui.kwargs["on_delete"](other_path)
    assert ok
    assert not other_path.exists()
    assert active.path.exists()


class _RenameActiveUi:
    """Picker stub that renames a target session then cancels."""

    def __init__(self, target: Path, name: str) -> None:
        self._target = target
        self._name = name
        self.components = SimpleNamespace(modals=self)

    def run_session_picker(self, **kwargs):
        kwargs["on_rename"](self._target, self._name)
        return None


def test_resume_rename_active_session_updates_live_tree(tmp_path: Path) -> None:
    sessions_dir = tmp_path / "store" / "proj"
    active = NativeSessionTree.create(tmp_path / "ws", session_dir=sessions_dir)
    active.append_message(AgentUserMessage(content=ProductContent("X")))
    assert active.path is not None

    ui = _RenameActiveUi(active.path, "live-renamed")
    run_interactive_session_picker(
        session_tree=active,
        terminal_ui=ui,  # type: ignore[arg-type]
    )
    # The live tree reflects the new name immediately (no reopen needed)...
    assert active.name == "live-renamed"
    # ...and it persisted exactly once (a reopened tree agrees).
    assert NativeSessionTree.open(active.path).name == "live-renamed"


def test_session_status_and_resume_listing_sanitize_name(tmp_path: Path) -> None:
    """A session name with control bytes must not reach diagnostics raw."""

    cwd = _workspace(tmp_path)
    session_dir = tmp_path / "store" / "proj"
    tree = NativeSessionTree.create(cwd, session_dir=session_dir)
    # A name with an ESC sequence (e.g. set via --name or a shared file).
    tree.append_session_info("nm\x1b[31mEVIL\x07")
    session = CodingSession(provider=_SeenProvider(), native_session=tree)

    _out, err = _run(session, cwd, "/session\n/resume\n/exit\n")
    assert "\x1b" not in err
    assert "\x07" not in err
    # The visible (sanitized) text is still present.
    assert "EVIL" in err


def test_diagnostics_sanitize_crafted_session_file_fields(tmp_path: Path) -> None:
    """Header id, entry id/content, and the file name from an untrusted session
    file must not reach /session, /tree, or the /resume listing raw."""

    import json

    cwd = _workspace(tmp_path)
    session_dir = tmp_path / "store" / "proj"
    session_dir.mkdir(parents=True)
    header = {
        "type": "session",
        "version": 1,
        "id": "id\x1b[2Jx",
        "timestamp": "2026-01-01T00-00-00+00-00",
        "cwd": str(cwd),
    }
    entry = {
        "type": "message",
        "id": "e\x1b1",
        "parentId": None,
        "timestamp": "t",
        "message": {"role": "user", "content": "hi\x1b[5mEVIL\x07"},
    }
    crafted = session_dir / "evil\x1bname.jsonl"
    crafted.write_text(
        json.dumps(header) + "\n" + json.dumps(entry) + "\n", encoding="utf-8"
    )
    tree = NativeSessionTree.open(crafted)
    session = CodingSession(provider=_SeenProvider(), native_session=tree)

    _out, err = _run(session, cwd, "/session\n/tree\n/resume\n/exit\n")
    assert "\x1b" not in err
    assert "\x07" not in err


def test_resume_open_rename_and_tree_notices_sanitize_crafted_id(
    tmp_path: Path,
) -> None:
    """Opening/renaming a crafted-id session and labelling/selecting its entries
    must not echo the untrusted id/entry-id raw in any notice."""

    import json

    cwd = _workspace(tmp_path)
    session_dir = tmp_path / "store" / "proj"
    session_dir.mkdir(parents=True)
    # A crafted sibling file with control bytes in the header id and entry id.
    header = {
        "type": "session",
        "version": 1,
        "id": "id\x1b[2Jx",
        "timestamp": "2026-01-01T00-00-00+00-00",
        "cwd": str(cwd),
    }
    entry = {
        "type": "message",
        "id": "ent\x1bid",
        "parentId": None,
        "timestamp": "t",
        "message": {"role": "user", "content": "hi"},
    }
    crafted = session_dir / "crafted.jsonl"
    crafted.write_text(
        json.dumps(header) + "\n" + json.dumps(entry) + "\n", encoding="utf-8"
    )
    active = NativeSessionTree.create(cwd, session_dir=session_dir)
    session = CodingSession(provider=_SeenProvider(), native_session=active)

    # Open the crafted file by absolute path (a relative sibling ref does not
    # resolve), then report status, label + select its entry, and rename it —
    # each notice echoes the crafted id / entry id, which must be sanitized.
    crafted_abs = str(crafted)
    _out, err = _run(
        session,
        cwd,
        "\n".join(
            [
                "/resume",  # listing (echoes crafted id + file name)
                f"/resume {crafted_abs}",  # open it (echoes crafted header id)
                "/session",  # echoes crafted id + leaf
                "/tree label 1 pin",  # echoes crafted entry id
                "/tree select 1",  # echoes chosen crafted entry id
                f"/resume rename {crafted_abs} renamed-crafted",  # echoes id
                "/exit",
                "",
            ]
        ),
    )
    assert "\x1b" not in err
    # Strict terminal adoption refuses crafted headers rather than carrying
    # their identifiers into the active product tree.
    assert "unable to load the selected native session" in err
    # The rename persisted the new name to the crafted file.
    assert NativeSessionTree.open(crafted).name == "renamed-crafted"


def test_delete_detail_sanitizes_oserror_text(tmp_path, monkeypatch) -> None:
    import shutil

    from pipy_harness.native.session_tree_commands import delete_native_session

    target = tmp_path / "s.jsonl"
    target.write_text("{}", encoding="utf-8")

    # Force the direct-unlink path (no `trash`) and make it fail with control
    # bytes in the OSError text.
    monkeypatch.setattr(shutil, "which", lambda _name: None)

    def _boom(self):
        raise OSError("[Errno 1] failed: bad\x1b[2Jpath")

    monkeypatch.setattr(Path, "unlink", _boom)
    ok, detail = delete_native_session(target)
    assert ok is False
    assert "\x1b" not in detail
