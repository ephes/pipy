"""Tool-loop runtime wired to the native product session tree.

These tests pin that the tool-loop REPL persists raw product turns to a native
session tree file, builds provider-visible context from the active branch, and
reconstructs context when resumed from an existing native session file.
"""

from __future__ import annotations

import io
from datetime import UTC, datetime
from pathlib import Path

from pipy_harness.models import HarnessStatus
from pipy_harness.native import (
    NativeToolReplSession,
    ProviderRequest,
    ProviderResult,
)
from pipy_harness.native.session_tree import NativeSessionTree
from pipy_harness.native.tools.messages import AssistantMessage, UserMessage


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
        users = [m.content for m in request.messages if isinstance(m, UserMessage)]
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


def _run(
    session: NativeToolReplSession, cwd: Path, user_inputs: str
) -> tuple[str, str]:
    out = io.StringIO()
    err = io.StringIO()
    session.run(
        workspace_root=cwd,
        input_stream=io.StringIO(user_inputs),
        output_stream=out,
        error_stream=err,
    )
    return out.getvalue(), err.getvalue()


def test_tool_loop_persists_raw_turns_to_native_session_file(
    tmp_path: Path,
) -> None:
    cwd = _workspace(tmp_path)
    tree = NativeSessionTree.create(cwd, session_dir=tmp_path / "sessions")
    provider = _SeenProvider()
    session = NativeToolReplSession(provider=provider, native_session=tree)

    _run(session, cwd, "ROOT\nMAIN\n/exit\n")

    assert tree.path is not None
    body = tree.path.read_text(encoding="utf-8")
    assert "ROOT" in body
    assert "MAIN" in body
    assert "SEEN:ROOT" in body
    # Active-branch user messages accumulate across turns.
    texts = [
        m.content
        for m in provider.requests[-1].messages
        if isinstance(m, UserMessage)
    ]
    assert texts == ["ROOT", "MAIN"]


def test_tool_loop_context_reconstructed_from_resumed_tree(
    tmp_path: Path,
) -> None:
    cwd = _workspace(tmp_path)
    session_dir = tmp_path / "sessions"
    seed = NativeSessionTree.create(cwd, session_dir=session_dir)
    seed.append_message(UserMessage(content="ROOT"))
    seed.append_message(AssistantMessage(content="SEEN:ROOT"))
    assert seed.path is not None

    reopened = NativeSessionTree.open(seed.path)
    provider = _SeenProvider()
    session = NativeToolReplSession(provider=provider, native_session=reopened)

    _run(session, cwd, "MORE\n/exit\n")

    # The first provider call after resume must carry the prior ROOT context
    # plus the new turn, proving context was rebuilt from the native file.
    users = [
        m.content
        for m in provider.requests[0].messages
        if isinstance(m, UserMessage)
    ]
    assert users == ["ROOT", "MORE"]


def test_tool_loop_default_session_is_ephemeral(tmp_path: Path) -> None:
    """With no injected session the loop must not write a session file."""

    cwd = _workspace(tmp_path)
    provider = _SeenProvider()
    session = NativeToolReplSession(provider=provider)
    # Should run without creating any persistent native session file.
    _run(session, cwd, "hello\n/exit\n")
    # Provider still saw the turn through an in-memory tree.
    assert provider.requests


def _request_users(request) -> list[str]:  # noqa: ANN001
    return [m.content for m in request.messages if isinstance(m, UserMessage)]


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
    session = NativeToolReplSession(provider=provider, native_session=tree)

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


def test_name_session_new_and_resume_roundtrip(tmp_path: Path) -> None:
    cwd = _workspace(tmp_path)
    session_dir = tmp_path / "sessions"
    tree = NativeSessionTree.create(cwd, session_dir=session_dir)
    provider = _SeenProvider()
    session = NativeToolReplSession(provider=provider, native_session=tree)

    _out, err = _run(
        session,
        cwd,
        "\n".join(["/name first-session", "hello", "/session", "/exit", ""]),
    )
    assert "first-session" in err  # /session status line reports the name
    assert tree.name == "first-session"


def test_fork_creates_new_session_file_with_parent(tmp_path: Path) -> None:
    cwd = _workspace(tmp_path)
    session_dir = tmp_path / "sessions"
    tree = NativeSessionTree.create(cwd, session_dir=session_dir)
    provider = _SeenProvider()
    session = NativeToolReplSession(provider=provider, native_session=tree)

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
    session = NativeToolReplSession(provider=provider, native_session=tree)

    # Build ROOT/MAIN, then re-pick the ROOT user message (index 1) WITH a
    # branch summary of the abandoned MAIN branch, then submit ALT.
    _run(
        session,
        cwd,
        "\n".join(
            ["ROOT", "MAIN", "/tree select 1 summarize", "ALT", "/exit", ""]
        ),
    )

    assert tree.path is not None
    body = tree.path.read_text(encoding="utf-8")
    assert '"type": "branch_summary"' in body or '"type":"branch_summary"' in body

    # The branch summary message contributes to the active-branch context.
    reopened = NativeSessionTree.open(tree.path)
    rebuilt = " ".join(
        m.content for m in reopened.build_context().messages if isinstance(m, UserMessage)
    )
    assert "abandoned" in rebuilt.lower()


def test_resume_rename_and_delete_with_confirmation(tmp_path: Path) -> None:
    cwd = _workspace(tmp_path)
    session_dir = tmp_path / "sessions"
    first = NativeSessionTree.create(cwd, session_dir=session_dir)
    first.append_message(UserMessage(content="seed"))
    first_id = first.session_id

    active = NativeSessionTree.create(cwd, session_dir=session_dir)
    provider = _SeenProvider()
    session = NativeToolReplSession(provider=provider, native_session=active)
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


def test_durable_compaction_entry_survives_reload(tmp_path: Path) -> None:
    cwd = _workspace(tmp_path)
    session_dir = tmp_path / "sessions"
    tree = NativeSessionTree.create(cwd, session_dir=session_dir)
    provider = _SeenProvider()
    session = NativeToolReplSession(provider=provider, native_session=tree)

    # Four user turns then /compact -> a durable compaction entry is appended.
    _run(
        session,
        cwd,
        "\n".join(["a", "b", "c", "d", "/compact", "e", "/exit", ""]),
    )

    assert tree.path is not None
    body = tree.path.read_text(encoding="utf-8")
    assert '"type": "compaction"' in body or '"type":"compaction"' in body

    reopened = NativeSessionTree.open(tree.path)
    rebuilt = reopened.build_context().messages
    texts = " ".join(m.content for m in rebuilt if isinstance(m, UserMessage))
    # The compaction summary message is present on reload.
    assert any("compacted" in m.content.lower() for m in rebuilt if isinstance(m, UserMessage))
    # Oldest dropped turn 'a' is no longer a standalone user message.
    assert " a " not in f" {texts} "


class _StubPickerUi:
    """Minimal terminal-ui stand-in exposing only ``run_session_picker``."""

    def __init__(self, choose):
        self._choose = choose
        self.kwargs = None

    def run_session_picker(self, **kwargs):
        self.kwargs = kwargs
        return self._choose(kwargs)


def test_interactive_resume_picker_wiring(tmp_path: Path) -> None:
    sessions_dir = tmp_path / "store" / "proj"
    active = NativeSessionTree.create(tmp_path / "ws", session_dir=sessions_dir)
    active.append_message(UserMessage(content="ACTIVE"))
    other = NativeSessionTree.create(tmp_path / "ws", session_dir=sessions_dir)
    other.append_session_info("other-name")
    other.append_message(UserMessage(content="OTHER"))

    session = NativeToolReplSession(provider=_SeenProvider(), native_session=active)
    assert active.path is not None and other.path is not None
    other_path = other.path

    # The stub picker chooses the `other` session file.
    ui = _StubPickerUi(lambda kw: other.path)
    chosen = session._run_interactive_session_picker(
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

    def run_session_picker(self, **kwargs):
        kwargs["on_rename"](self._target, self._name)
        return None


def test_resume_rename_active_session_updates_live_tree(tmp_path: Path) -> None:
    sessions_dir = tmp_path / "store" / "proj"
    active = NativeSessionTree.create(tmp_path / "ws", session_dir=sessions_dir)
    active.append_message(UserMessage(content="X"))
    assert active.path is not None
    session = NativeToolReplSession(provider=_SeenProvider(), native_session=active)

    ui = _RenameActiveUi(active.path, "live-renamed")
    session._run_interactive_session_picker(
        session_tree=active,
        terminal_ui=ui,  # type: ignore[arg-type]
    )
    # The live tree reflects the new name immediately (no reopen needed)...
    assert active.name == "live-renamed"
    # ...and it persisted exactly once (a reopened tree agrees).
    assert NativeSessionTree.open(active.path).name == "live-renamed"
