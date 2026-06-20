"""CLI-level tests for Pi-style native-session startup flags.

Covers ``--session-id`` (open-exact-or-create), ``--name``/``-n``,
``--session-dir`` root override, the ``--fork``/``--session-id`` mutual-exclusion
errors, and the cross-project ``--session`` fork prompt — all driven through
``pipy.cli.main`` and verified against the written native session files.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path

from pipy_harness.cli import main
from pipy_harness.models import HarnessStatus
from pipy_harness.native.models import ProviderRequest, ProviderResult
from pipy_harness.native.session_tree import default_native_session_dir
from pipy_harness.native.session_tree_commands import (
    list_native_sessions,
)


class _FakeReplProvider:
    name = "fake"
    supports_tool_calls = True

    def __init__(self, model_id: str) -> None:
        self.model_id = model_id

    def complete(self, request: ProviderRequest, **_kwargs: object) -> ProviderResult:
        now = datetime.now(UTC)
        return ProviderResult(
            status=HarnessStatus.SUCCEEDED,
            provider_name=self.name,
            model_id=self.model_id,
            started_at=now,
            ended_at=now,
            final_text="OUT",
            tool_calls=(),
        )


_run_counter = [0]


def _repl(
    monkeypatch, tmp_path: Path, cwd: Path, *extra: str, stdin: str = "/exit\n"
) -> int:
    monkeypatch.setattr("pipy_harness.cli.FakeNativeProvider", _FakeReplProvider)
    monkeypatch.setattr(sys, "stdin", StringIO(stdin))
    _run_counter[0] += 1
    return main(
        [
            "repl",
            "--agent",
            "pipy-native",
            "--slug",
            f"startup-{_run_counter[0]}",
            "--root",
            str(tmp_path / "archive"),
            "--cwd",
            str(cwd),
            *extra,
        ]
    )


def _project_dir(cwd: Path, sessions_root: Path) -> Path:
    return default_native_session_dir(cwd.expanduser().resolve(), sessions_root=sessions_root)


def test_session_id_creates_then_reopens(tmp_path, monkeypatch) -> None:
    sessions_root = tmp_path / "store"
    cwd = tmp_path / "ws"
    cwd.mkdir()

    code = _repl(
        monkeypatch, tmp_path, cwd, "--session-dir", str(sessions_root),
        "--session-id", "fixed-id-xyz",
    )
    assert code == 0
    sessions = list_native_sessions(_project_dir(cwd, sessions_root))
    assert [s.session_id for s in sessions] == ["fixed-id-xyz"]

    # Re-running with the same id reopens it (no second file).
    code = _repl(
        monkeypatch, tmp_path, cwd, "--session-dir", str(sessions_root),
        "--session-id", "fixed-id-xyz",
    )
    assert code == 0
    sessions = list_native_sessions(_project_dir(cwd, sessions_root))
    assert [s.session_id for s in sessions] == ["fixed-id-xyz"]


def test_name_flag_is_applied(tmp_path, monkeypatch) -> None:
    sessions_root = tmp_path / "store"
    cwd = tmp_path / "ws"
    cwd.mkdir()
    code = _repl(
        monkeypatch, tmp_path, cwd, "--session-dir", str(sessions_root),
        "-n", "named-session",
    )
    assert code == 0
    sessions = list_native_sessions(_project_dir(cwd, sessions_root))
    assert len(sessions) == 1
    assert sessions[0].name == "named-session"


def test_fork_continue_mutually_exclusive(tmp_path, monkeypatch, capfd) -> None:
    cwd = tmp_path / "ws"
    cwd.mkdir()
    code = _repl(
        monkeypatch, tmp_path, cwd, "--fork", "abc", "--continue",
    )
    err = capfd.readouterr().err
    assert code == 2
    assert "--fork cannot be combined with --continue" in err


def test_session_id_session_mutually_exclusive(tmp_path, monkeypatch, capfd) -> None:
    cwd = tmp_path / "ws"
    cwd.mkdir()
    code = _repl(
        monkeypatch, tmp_path, cwd, "--session-id", "x", "--session", "y",
    )
    err = capfd.readouterr().err
    assert code == 2
    assert "--session-id cannot be combined with --session" in err


def _seed_other_project_session(tmp_path, monkeypatch, sessions_root: Path) -> tuple[Path, str]:
    other = tmp_path / "other"
    other.mkdir()
    code = _repl(
        monkeypatch, tmp_path, other, "--session-dir", str(sessions_root),
        "-n", "from-other",
    )
    assert code == 0
    sessions = list_native_sessions(_project_dir(other, sessions_root))
    return other, sessions[0].session_id


def test_cross_project_session_aborts_when_declined(tmp_path, monkeypatch, capfd) -> None:
    sessions_root = tmp_path / "store"
    _other, sid = _seed_other_project_session(tmp_path, monkeypatch, sessions_root)
    here = tmp_path / "here"
    here.mkdir()

    code = _repl(
        monkeypatch, tmp_path, here, "--session-dir", str(sessions_root),
        "--session", sid[:8], stdin="n\n/exit\n",
    )
    err = capfd.readouterr().err
    assert code == 0
    assert "different project" in err
    assert "aborted" in err.lower()
    # No session was forked into `here`.
    assert list_native_sessions(_project_dir(here, sessions_root)) == []


def test_cross_project_session_forks_when_confirmed(tmp_path, monkeypatch, capfd) -> None:
    sessions_root = tmp_path / "store"
    _other, sid = _seed_other_project_session(tmp_path, monkeypatch, sessions_root)
    here = tmp_path / "here"
    here.mkdir()

    code = _repl(
        monkeypatch, tmp_path, here, "--session-dir", str(sessions_root),
        "--session", sid[:8], stdin="y\n/exit\n",
    )
    assert code == 0
    forked = list_native_sessions(_project_dir(here, sessions_root))
    assert len(forked) == 1
    assert forked[0].session_id != sid


def test_resume_flag_non_tty_continues_most_recent(tmp_path, monkeypatch) -> None:
    sessions_root = tmp_path / "store"
    cwd = tmp_path / "ws"
    cwd.mkdir()
    # Seed a session, then `-r` on a non-TTY must continue it (not create new).
    code = _repl(
        monkeypatch, tmp_path, cwd, "--session-dir", str(sessions_root),
        "-n", "seed",
    )
    assert code == 0
    before = list_native_sessions(_project_dir(cwd, sessions_root))
    assert len(before) == 1

    code = _repl(
        monkeypatch, tmp_path, cwd, "--session-dir", str(sessions_root), "-r",
    )
    assert code == 0
    after = list_native_sessions(_project_dir(cwd, sessions_root))
    assert [s.session_id for s in after] == [s.session_id for s in before]


def test_pipy_session_dir_env_does_not_redirect_native_store(
    tmp_path, monkeypatch
) -> None:
    """$PIPY_SESSION_DIR is the metadata archive root, never the native store.

    Reusing it would leak native product transcripts into the archive's
    directory tree. The native session must land under the native store root.
    """

    archive_dir = tmp_path / "archive-sessions"
    native_root = tmp_path / "native-root"
    cwd = tmp_path / "ws"
    cwd.mkdir()
    monkeypatch.setenv("PIPY_SESSION_DIR", str(archive_dir))
    monkeypatch.setenv("PIPY_NATIVE_SESSIONS_ROOT", str(native_root))

    code = _repl(monkeypatch, tmp_path, cwd)  # no --session-dir
    assert code == 0

    native_files = list((native_root / "native-sessions").glob("**/*.jsonl"))
    assert native_files, "native session not written under the native store root"
    # Nothing native landed inside the metadata-archive session dir.
    assert not list(archive_dir.glob("**/*native-sessions*"))


def test_resume_flag_tty_cancel_aborts(tmp_path, monkeypatch, capfd) -> None:
    """Cancelling the `-r` startup picker on a TTY aborts cleanly (exit 0),
    instead of silently continuing the most recent session."""

    sessions_root = tmp_path / "store"
    cwd = tmp_path / "ws"
    cwd.mkdir()
    # Seed a session so the interactive picker path runs.
    code = _repl(
        monkeypatch, tmp_path, cwd, "--session-dir", str(sessions_root), "-n", "seed",
    )
    assert code == 0

    # Force the TTY path and a cancelled picker (returns None).
    monkeypatch.setattr("pipy_harness.cli._startup_stdin_is_tty", lambda: True)
    monkeypatch.setattr(
        "pipy_harness.native.tui.run_startup_session_picker",
        lambda **kwargs: None,
    )
    before = [s.session_id for s in list_native_sessions(_project_dir(cwd, sessions_root))]
    code = _repl(
        monkeypatch, tmp_path, cwd, "--session-dir", str(sessions_root), "-r",
    )
    err = capfd.readouterr().err
    assert code == 0
    assert "aborted" in err.lower()
    # No new session created by the aborted run.
    after = [s.session_id for s in list_native_sessions(_project_dir(cwd, sessions_root))]
    assert after == before


def test_unsafe_session_id_is_rejected(tmp_path, monkeypatch, capfd) -> None:
    """A --session-id with path-traversal syntax is rejected (it becomes part
    of the session filename), never written to disk."""

    sessions_root = tmp_path / "store"
    cwd = tmp_path / "ws"
    cwd.mkdir()
    code = _repl(
        monkeypatch, tmp_path, cwd, "--session-dir", str(sessions_root),
        "--session-id", "../../evil",
    )
    err = capfd.readouterr().err
    assert code == 2
    assert "session id must be" in err
    # Nothing escaped the store.
    assert not list(tmp_path.glob("**/evil*"))


def test_retired_resume_branch_flags_rejected_with_message(tmp_path, monkeypatch, capfd) -> None:
    cwd = tmp_path / "ws"
    cwd.mkdir()
    for flag, value in (("--resume", "rec"), ("--branch", "lbl")):
        code = _repl(
            monkeypatch, tmp_path, cwd, "--session-dir", str(tmp_path / "store"),
            flag, value,
        )
        err = capfd.readouterr().err
        assert code == 2, flag
        # Not silently abbreviated to --resume-session: an explicit retirement.
        assert "retired" in err, flag


def test_empty_session_id_is_rejected_not_ignored(tmp_path, monkeypatch, capfd) -> None:
    cwd = tmp_path / "ws"
    cwd.mkdir()
    sessions_root = tmp_path / "store"
    # An explicit empty --session-id must not be silently ignored.
    code = _repl(
        monkeypatch, tmp_path, cwd, "--session-dir", str(sessions_root),
        "--session-id", "",
    )
    err = capfd.readouterr().err
    assert code == 2
    assert "session-id" in err.lower() or "session id" in err.lower()
    # No session was created for the rejected run.
    assert list_native_sessions(_project_dir(cwd, sessions_root)) == []


def test_empty_session_id_still_triggers_mutual_exclusion(tmp_path, monkeypatch, capfd) -> None:
    cwd = tmp_path / "ws"
    cwd.mkdir()
    code = _repl(
        monkeypatch, tmp_path, cwd, "--session-id", "", "--continue",
    )
    err = capfd.readouterr().err
    assert code == 2
    assert "--session-id cannot be combined with --continue" in err


def test_empty_session_and_fork_targets_are_rejected(tmp_path, monkeypatch, capfd) -> None:
    cwd = tmp_path / "ws"
    cwd.mkdir()
    for flag in ("--session", "--fork"):
        code = _repl(
            monkeypatch, tmp_path, cwd, "--session-dir", str(tmp_path / "store"),
            flag, "",
        )
        err = capfd.readouterr().err
        assert code == 2, flag
        assert "requires a path or id" in err, (flag, err)


def test_empty_fork_with_session_id_does_not_silently_open(tmp_path, monkeypatch, capfd) -> None:
    cwd = tmp_path / "ws"
    cwd.mkdir()
    sessions_root = tmp_path / "store"
    code = _repl(
        monkeypatch, tmp_path, cwd, "--session-dir", str(sessions_root),
        "--fork", "", "--session-id", "foo",
    )
    capfd.readouterr()
    assert code == 2
    # The supplied --fork is not ignored; no 'foo' session was created.
    assert all(
        s.session_id != "foo"
        for s in list_native_sessions(_project_dir(cwd, sessions_root))
    )


def test_retired_resume_flag_rejected(tmp_path, monkeypatch, capfd) -> None:
    cwd = tmp_path / "ws"
    cwd.mkdir()
    code = _repl(
        monkeypatch, tmp_path, cwd, "--session-dir", str(tmp_path / "store"),
        "--resume", "rec",
    )
    err = capfd.readouterr().err
    assert code == 2
    assert "retired" in err


def test_cross_project_prompt_sanitizes_other_cwd(monkeypatch, capsys) -> None:
    import io as _io

    from pipy_harness.cli import _confirm_cross_project_fork

    monkeypatch.setattr("sys.stdin", _io.StringIO("n\n"))
    result = _confirm_cross_project_fork("/proj\x1b[31mEVIL\x07")
    err = capsys.readouterr().err
    assert result is False
    assert "\x1b" not in err
    assert "\x07" not in err
    assert "EVIL" in err
