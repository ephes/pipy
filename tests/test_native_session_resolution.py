"""Cross-project native-session listing and Pi-shaped reference resolution.

These back the Pi-equivalent ``--session``/``--session-id``/``--fork`` startup
flags: exact-then-prefix lookup, local-first then global (cross-project)
search, and the typed resolution result the CLI uses to decide open-vs-fork.
"""

from __future__ import annotations

from pathlib import Path

from pipy_harness.native.session_tree import (
    NativeSessionTree,
    native_sessions_root,
)
from pipy_harness.native.session_tree_commands import (
    StartupSessionAborted,
    list_all_native_sessions,
    resolve_session_ref,
    resolve_startup_session,
)
from pipy_harness.native.tools.messages import UserMessage
import pytest


def _make(cwd: Path, sessions_root: Path) -> NativeSessionTree:
    session_dir = sessions_root / _encoded(cwd)
    tree = NativeSessionTree.create(cwd, session_dir=session_dir)
    tree.append_message(UserMessage(content="hi"))
    return tree


def _encoded(cwd: Path) -> str:
    from pipy_harness.native.session_tree import encode_cwd_dir_name

    return encode_cwd_dir_name(cwd.expanduser().resolve())


def test_native_sessions_root_prefers_explicit_session_dir(tmp_path: Path) -> None:
    override = tmp_path / "custom"
    assert native_sessions_root(session_dir=override) == override
    root = tmp_path / "state"
    assert native_sessions_root(state_root=root) == root / "native-sessions"


def test_list_all_native_sessions_spans_projects(tmp_path: Path) -> None:
    sessions_root = tmp_path / "root"
    proj_a = tmp_path / "a"
    proj_a.mkdir()
    proj_b = tmp_path / "b"
    proj_b.mkdir()
    a = _make(proj_a, sessions_root)
    b = _make(proj_b, sessions_root)

    listed = list_all_native_sessions(sessions_root)
    ids = {e.session_id for e in listed}
    assert a.session_id in ids
    assert b.session_id in ids
    cwds = {e.cwd for e in listed}
    assert str(proj_a.resolve()) in cwds
    assert str(proj_b.resolve()) in cwds


def test_resolve_ref_path_form(tmp_path: Path) -> None:
    sessions_root = tmp_path / "root"
    proj = tmp_path / "p"
    proj.mkdir()
    tree = _make(proj, sessions_root)
    result = resolve_session_ref(proj, str(tree.path), sessions_root=sessions_root)
    assert result.kind == "path"
    assert result.path == tree.path


def test_resolve_ref_local_exact_then_prefix(tmp_path: Path) -> None:
    sessions_root = tmp_path / "root"
    proj = tmp_path / "p"
    proj.mkdir()
    tree = _make(proj, sessions_root)
    sid = tree.session_id

    exact = resolve_session_ref(proj, sid, sessions_root=sessions_root)
    assert exact.kind == "local"
    assert exact.path == tree.path

    prefix = resolve_session_ref(proj, sid[:6], sessions_root=sessions_root)
    assert prefix.kind == "local"
    assert prefix.path == tree.path


def test_resolve_ref_global_cross_project(tmp_path: Path) -> None:
    sessions_root = tmp_path / "root"
    here = tmp_path / "here"
    here.mkdir()
    other = tmp_path / "other"
    other.mkdir()
    other_tree = _make(other, sessions_root)

    # No local sessions for `here`; the id resolves only in `other`.
    result = resolve_session_ref(
        here, other_tree.session_id[:8], sessions_root=sessions_root
    )
    assert result.kind == "global"
    assert result.path == other_tree.path
    assert result.cwd == str(other.resolve())


def test_resolve_ref_not_found(tmp_path: Path) -> None:
    sessions_root = tmp_path / "root"
    proj = tmp_path / "p"
    proj.mkdir()
    _make(proj, sessions_root)
    result = resolve_session_ref(proj, "deadbeef", sessions_root=sessions_root)
    assert result.kind == "not_found"
    assert result.arg == "deadbeef"


def test_resolve_ref_local_wins_over_global(tmp_path: Path) -> None:
    sessions_root = tmp_path / "root"
    here = tmp_path / "here"
    here.mkdir()
    other = tmp_path / "other"
    other.mkdir()
    local_tree = _make(here, sessions_root)
    _make(other, sessions_root)
    # A prefix that matches the local session must resolve local, not global.
    result = resolve_session_ref(
        here, local_tree.session_id[:8], sessions_root=sessions_root
    )
    assert result.kind == "local"
    assert result.path == local_tree.path


def test_startup_session_id_opens_existing_then_creates(tmp_path: Path) -> None:
    sessions_root = tmp_path / "root"
    proj = tmp_path / "p"
    proj.mkdir()
    existing = _make(proj, sessions_root)
    sid = existing.session_id

    reopened = resolve_startup_session(
        proj, mode="session-id", target=sid, sessions_root=sessions_root
    )
    assert reopened is not None
    assert reopened.session_id == sid

    created = resolve_startup_session(
        proj, mode="session-id", target="fixed-id-123", sessions_root=sessions_root
    )
    assert created is not None
    assert created.session_id == "fixed-id-123"


def test_startup_name_is_applied(tmp_path: Path) -> None:
    sessions_root = tmp_path / "root"
    proj = tmp_path / "p"
    proj.mkdir()
    tree = resolve_startup_session(
        proj, mode="new", name="my-name", sessions_root=sessions_root
    )
    assert tree is not None
    assert tree.name == "my-name"


def test_startup_cross_project_forks_when_confirmed(tmp_path: Path) -> None:
    sessions_root = tmp_path / "root"
    here = tmp_path / "here"
    here.mkdir()
    other = tmp_path / "other"
    other.mkdir()
    other_tree = _make(other, sessions_root)

    seen: list[str] = []

    def confirm(ref: object) -> bool:
        seen.append(getattr(ref, "cwd", ""))
        return True

    forked = resolve_startup_session(
        here,
        mode="session",
        target=other_tree.session_id[:8],
        sessions_root=sessions_root,
        confirm_fork=confirm,
    )
    assert forked is not None
    # Forked into the current project, new id, parentSession recorded.
    assert forked.session_id != other_tree.session_id
    assert str(here.resolve()) == forked.get_header().cwd
    assert seen == [str(other.resolve())]


def test_startup_cross_project_aborts_when_declined(tmp_path: Path) -> None:
    sessions_root = tmp_path / "root"
    here = tmp_path / "here"
    here.mkdir()
    other = tmp_path / "other"
    other.mkdir()
    other_tree = _make(other, sessions_root)

    with pytest.raises(StartupSessionAborted):
        resolve_startup_session(
            here,
            mode="session",
            target=other_tree.session_id[:8],
            sessions_root=sessions_root,
            confirm_fork=lambda ref: False,
        )


def test_startup_session_dir_override_is_used(tmp_path: Path) -> None:
    sessions_root = tmp_path / "explicit-root"
    proj = tmp_path / "p"
    proj.mkdir()
    tree = resolve_startup_session(
        proj, mode="new", sessions_root=sessions_root
    )
    assert tree is not None
    assert tree.path is not None
    assert sessions_root in tree.path.parents


def test_validate_session_id_accepts_safe_rejects_unsafe() -> None:
    from pipy_harness.native.session_tree import validate_session_id

    assert validate_session_id("fixed-id_123") == "fixed-id_123"
    assert validate_session_id("a" * 32) == "a" * 32
    for bad in ("../evil", "/abs", "a/b", "a\\b", "..", "", "a b", "x\x1by", "a." * 70):
        with pytest.raises(ValueError):
            validate_session_id(bad)


def test_resolve_ref_ambiguous_prefix_is_not_found(tmp_path) -> None:
    sessions_root = tmp_path / "root"
    proj = tmp_path / "p"
    proj.mkdir()
    session_dir = sessions_root / _encoded(proj)
    NativeSessionTree.create(proj, session_dir=session_dir, session_id="sharedaa-one")
    NativeSessionTree.create(proj, session_dir=session_dir, session_id="sharedaa-two")
    # A prefix matching both sessions must not silently pick one.
    result = resolve_session_ref(proj, "sharedaa", sessions_root=sessions_root)
    assert result.kind == "not_found"
    # An unambiguous prefix still resolves.
    exact = resolve_session_ref(proj, "sharedaa-one", sessions_root=sessions_root)
    assert exact.kind == "local"


def test_resolve_ref_missing_path_is_not_found(tmp_path) -> None:
    sessions_root = tmp_path / "root"
    proj = tmp_path / "p"
    proj.mkdir()
    result = resolve_session_ref(
        proj, str(tmp_path / "nope" / "missing.jsonl"), sessions_root=sessions_root
    )
    assert result.kind == "not_found"


def test_resolve_ref_relative_path_uses_workspace_cwd(tmp_path) -> None:
    import os

    sessions_root = tmp_path / "root"
    proj = tmp_path / "p"
    proj.mkdir()
    tree = _make(proj, sessions_root)
    assert tree.path is not None
    rel = os.path.relpath(tree.path, proj)
    # A relative path-like ref resolves against the workspace cwd, not the
    # process cwd.
    result = resolve_session_ref(proj, rel, sessions_root=sessions_root)
    assert result.kind == "path"
    assert result.path is not None
    assert result.path.resolve() == tree.path.resolve()


def test_startup_fork_with_existing_session_id_is_rejected(tmp_path) -> None:
    sessions_root = tmp_path / "root"
    proj = tmp_path / "p"
    proj.mkdir()
    src = _make(proj, sessions_root)
    NativeSessionTree.create(
        proj, session_dir=sessions_root / _encoded(proj), session_id="taken-id"
    )
    with pytest.raises(ValueError, match="already exists"):
        resolve_startup_session(
            proj,
            mode="fork",
            target=src.session_id[:8],
            session_id="taken-id",
            sessions_root=sessions_root,
        )
