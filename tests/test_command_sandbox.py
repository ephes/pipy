"""Focused tests for the shared safe command-execution substrate.

These tests pin both the string preflight *and* the runtime containment of
``pipy_harness.native.command_sandbox``. Several cases deliberately use
commands that contain no shell metacharacters (``cat .git/config``,
``cat link/config``) so a passing test proves the substrate contains the
attempt at execution-resolution time, not just via a string blocklist.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from pipy_harness.native.command_sandbox import (
    TRUNCATION_MARKER,
    CommandPolicy,
    CommandRejectionReason,
    CommandStatus,
    execute_allowlisted_argv,
    run_command,
)


def _policy(workspace: Path, **overrides: Any) -> CommandPolicy:
    return CommandPolicy(workspace_root=workspace.resolve(), **overrides)


def test_runs_allowed_command_and_captures_bounded_output(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("hello world\n", encoding="utf-8")
    result = run_command("cat a.txt", _policy(tmp_path))
    assert result.status is CommandStatus.COMPLETED
    assert result.exit_code == 0
    assert "hello world" in result.stdout
    assert result.reason is None
    # Safe metadata label is the executable basename only, never full args.
    assert result.argv_program == "cat"


def test_rejects_command_substitution(tmp_path: Path) -> None:
    result = run_command("echo $(cat a.txt)", _policy(tmp_path))
    assert result.status is CommandStatus.REJECTED
    assert result.reason is CommandRejectionReason.SHELL_METACHARACTERS


def test_rejects_backtick_substitution(tmp_path: Path) -> None:
    result = run_command("echo `id`", _policy(tmp_path))
    assert result.status is CommandStatus.REJECTED
    assert result.reason is CommandRejectionReason.SHELL_METACHARACTERS


def test_rejects_glob_expansion(tmp_path: Path) -> None:
    result = run_command("cat *.txt", _policy(tmp_path))
    assert result.status is CommandStatus.REJECTED
    assert result.reason is CommandRejectionReason.SHELL_METACHARACTERS


def test_rejects_pipes_redirects_and_chaining(tmp_path: Path) -> None:
    for command in (
        "cat a.txt | cat",
        "echo hi > b.txt",
        "echo a && echo b",
        "echo a ; echo b",
        "echo ${HOME}",
        "echo ~",
    ):
        result = run_command(command, _policy(tmp_path))
        assert result.status is CommandStatus.REJECTED, command
        assert result.reason is CommandRejectionReason.SHELL_METACHARACTERS, command


def test_rejects_control_characters(tmp_path: Path) -> None:
    result = run_command("echo hi\necho bye", _policy(tmp_path))
    assert result.status is CommandStatus.REJECTED
    assert result.reason in {
        CommandRejectionReason.SHELL_METACHARACTERS,
        CommandRejectionReason.CONTROL_CHARACTERS,
    }


def test_rejects_disallowed_executable(tmp_path: Path) -> None:
    result = run_command("python3 -c pass", _policy(tmp_path))
    assert result.status is CommandStatus.REJECTED
    assert result.reason is CommandRejectionReason.DISALLOWED_EXECUTABLE


def test_rejects_absolute_executable_path(tmp_path: Path) -> None:
    result = run_command("/bin/cat a.txt", _policy(tmp_path))
    assert result.status is CommandStatus.REJECTED
    assert result.reason is CommandRejectionReason.DISALLOWED_EXECUTABLE


def test_rejects_empty_command(tmp_path: Path) -> None:
    result = run_command("   ", _policy(tmp_path))
    assert result.status is CommandStatus.REJECTED
    assert result.reason is CommandRejectionReason.EMPTY_COMMAND


def test_blocks_git_access_through_direct_path(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("[core]\n", encoding="utf-8")
    # No shell metacharacters: containment must happen at resolution time.
    result = run_command("cat .git/config", _policy(tmp_path))
    assert result.status is CommandStatus.REJECTED
    assert result.reason is CommandRejectionReason.UNSAFE_PATH_ARGUMENT


def test_blocks_git_directory_access(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    result = run_command("cat .git", _policy(tmp_path))
    assert result.status is CommandStatus.REJECTED
    assert result.reason is CommandRejectionReason.UNSAFE_PATH_ARGUMENT


def test_blocks_parent_traversal(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (tmp_path / "secret.txt").write_text("data\n", encoding="utf-8")
    result = run_command("cat ../secret.txt", _policy(workspace))
    assert result.status is CommandStatus.REJECTED
    assert result.reason is CommandRejectionReason.UNSAFE_PATH_ARGUMENT


def test_blocks_absolute_path_outside_workspace(tmp_path: Path) -> None:
    result = run_command("cat /etc/hosts", _policy(tmp_path))
    assert result.status is CommandStatus.REJECTED
    assert result.reason is CommandRejectionReason.UNSAFE_PATH_ARGUMENT


def test_blocks_glued_flag_path(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("[core]\n", encoding="utf-8")
    # A glued flag+path (e.g. `-f.git/config`) must not bypass the path policy.
    result = run_command("cat -f.git/config", _policy(tmp_path))
    assert result.status is CommandStatus.REJECTED
    assert result.reason is CommandRejectionReason.UNSAFE_PATH_ARGUMENT


def test_blocks_symlink_escape_into_git(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("[core]\nsecret\n", encoding="utf-8")
    (tmp_path / "link").symlink_to(tmp_path / ".git")
    result = run_command("cat link/config", _policy(tmp_path))
    assert result.status is CommandStatus.REJECTED
    assert result.reason is CommandRejectionReason.UNSAFE_PATH_ARGUMENT


def test_times_out_long_running_command(tmp_path: Path) -> None:
    result = run_command(
        "sleep 5",
        _policy(
            tmp_path,
            allowed_executables=frozenset({"sleep"}),
            timeout_seconds=0.5,
        ),
    )
    assert result.status is CommandStatus.TIMED_OUT
    assert result.exit_code is None


def test_bounds_oversized_output(tmp_path: Path) -> None:
    (tmp_path / "big.txt").write_text("x" * 5000 + "\n", encoding="utf-8")
    result = run_command("cat big.txt", _policy(tmp_path, max_output_bytes=200))
    assert result.status is CommandStatus.COMPLETED
    assert result.truncated is True
    assert TRUNCATION_MARKER in result.stdout
    assert len(result.stdout.encode("utf-8")) <= 200 + len(TRUNCATION_MARKER) + 4


def test_redacts_secret_shaped_output(tmp_path: Path) -> None:
    (tmp_path / "creds.txt").write_text(
        "aws_key = AKIAIOSFODNN7EXAMPLE\n", encoding="utf-8"
    )
    result = run_command("cat creds.txt", _policy(tmp_path))
    assert result.status is CommandStatus.COMPLETED
    assert "AKIAIOSFODNN7EXAMPLE" not in result.stdout
    assert "redacted" in result.stdout.lower()


def test_environment_is_scrubbed(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "topsecret")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_dummy")
    captured: dict[str, Any] = {}

    def fake_runner(argv: Any, **kwargs: Any) -> subprocess.CompletedProcess[Any]:
        captured.update(kwargs)
        return subprocess.CompletedProcess(argv, 0, "", "")

    result = run_command("echo hi", _policy(tmp_path), runner=fake_runner)
    assert result.status is CommandStatus.COMPLETED
    env = captured["env"]
    assert "AWS_SECRET_ACCESS_KEY" not in env
    assert "GITHUB_TOKEN" not in env
    assert "PATH" in env
    # The substrate never invokes a shell.
    assert captured.get("shell", False) is False
    assert captured["cwd"] == tmp_path.resolve()


def test_execute_allowlisted_argv_preserves_devnull_shape(tmp_path: Path) -> None:
    calls: list[dict[str, Any]] = []

    def fake_runner(argv: Any, **kwargs: Any) -> subprocess.CompletedProcess[Any]:
        calls.append({"argv": argv, "kwargs": kwargs})
        return subprocess.CompletedProcess(argv, 0)

    execute_allowlisted_argv(("just", "check"), cwd=tmp_path, runner=fake_runner)
    assert len(calls) == 1
    assert calls[0]["argv"] == ("just", "check")
    kwargs = calls[0]["kwargs"]
    assert kwargs["cwd"] == tmp_path.resolve()
    assert kwargs["stdin"] == subprocess.DEVNULL
    assert kwargs["stdout"] == subprocess.DEVNULL
    assert kwargs["stderr"] == subprocess.DEVNULL
    assert kwargs["check"] is False
    # No timeout key when none is requested, preserving prior /verify behavior.
    assert "timeout" not in kwargs


# --- Regression: containment holes found in review (2026-05-30) -------------
#
# Each of these reproduces a concrete runtime bypass of the .git/mutation/
# allowlist boundary that an argv-token-only check missed.


def test_blocks_git_through_recursive_grep_of_cwd(tmp_path: Path) -> None:
    # `grep -R secret .` recurses into .git even though `.` is a "safe" token.
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("secret-value\n", encoding="utf-8")
    (tmp_path / "a.txt").write_text("hello\n", encoding="utf-8")
    result = run_command("grep -R secret-value .", _policy(tmp_path))
    assert result.status is CommandStatus.REJECTED
    assert "secret-value" not in result.stdout


def test_blocks_directory_listing_that_reveals_git(tmp_path: Path) -> None:
    # `ls -la .` lists .git; ls is a directory lister with no safe operand form.
    (tmp_path / ".git").mkdir()
    result = run_command("ls -la .", _policy(tmp_path))
    assert result.status is CommandStatus.REJECTED


def test_rejects_directory_operand(tmp_path: Path) -> None:
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "inner.txt").write_text("x\n", encoding="utf-8")
    result = run_command("cat sub", _policy(tmp_path))
    assert result.status is CommandStatus.REJECTED


def test_rejects_dot_directory_operand(tmp_path: Path) -> None:
    result = run_command("cat .", _policy(tmp_path))
    assert result.status is CommandStatus.REJECTED


def test_grep_removed_from_default_allowlist_blocks_every_recursion_form(
    tmp_path: Path,
) -> None:
    # grep is not in bash's default allowlist: argv inspection cannot robustly
    # contain its many recursion forms (`-r`/`-R`/`-rn`/`-d recurse`/no-path),
    # so recursive/literal search lives in the dedicated `grep` tool instead.
    # Every form must be refused and must never leak `.git` content — including
    # the `-d recurse` form and the pattern-token-resolves-to-a-file case.
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("needle-secret\n", encoding="utf-8")
    (tmp_path / "needle").write_text("decoy\n", encoding="utf-8")
    for command in (
        "grep -R needle .",
        "grep -R needle",
        "grep -rn needle",
        "grep -d recurse needle",
        "grep needle a.txt",
    ):
        result = run_command(command, _policy(tmp_path))
        assert result.status is CommandStatus.REJECTED, command
        assert (
            result.reason is CommandRejectionReason.DISALLOWED_EXECUTABLE
        ), command
        assert "needle-secret" not in result.stdout, command


def test_rg_not_in_default_allowlist(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("hello\n", encoding="utf-8")
    result = run_command("rg hello a.txt", _policy(tmp_path))
    assert result.status is CommandStatus.REJECTED
    assert result.reason is CommandRejectionReason.DISALLOWED_EXECUTABLE


def test_uniq_not_in_default_allowlist(tmp_path: Path) -> None:
    # uniq writes its second positional operand; drop it from the default set.
    (tmp_path / "a.txt").write_text("a\na\n", encoding="utf-8")
    result = run_command("uniq a.txt out.txt", _policy(tmp_path))
    assert result.status is CommandStatus.REJECTED
    assert not (tmp_path / "out.txt").exists()


def test_checksum_commands_removed_block_manifest_git_read(tmp_path: Path) -> None:
    # sha1sum/sha256sum/cksum check mode (`-c`/`--check`) reads a manifest and
    # opens every path it names, so a vetted manifest operand would smuggle
    # unvetted paths (here `.git/config`) past the path policy. They are dropped
    # from the default allowlist entirely; every invocation — plain digest or
    # check mode — must be refused and must never leak `.git` content.
    import hashlib

    (tmp_path / ".git").mkdir()
    secret = "secret-token-abc\n"
    (tmp_path / ".git" / "config").write_text(secret, encoding="utf-8")
    digest = hashlib.sha256(secret.encode()).hexdigest()
    (tmp_path / "manifest.txt").write_text(f"{digest}  .git/config\n", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("alpha\n", encoding="utf-8")
    for command in (
        "sha256sum -c manifest.txt",
        "sha1sum -c manifest.txt",
        "cksum --check manifest.txt",
        "sha256sum notes.txt",
        "cksum notes.txt",
    ):
        result = run_command(command, _policy(tmp_path))
        assert result.status is CommandStatus.REJECTED, command
        assert (
            result.reason is CommandRejectionReason.DISALLOWED_EXECUTABLE
        ), command
        assert "OK" not in (result.stdout or ""), command
        assert "secret-token" not in (result.stdout or ""), command


def test_sort_glued_output_flag_does_not_mutate(tmp_path: Path) -> None:
    target = tmp_path / "a.txt"
    target.write_text("b\na\n", encoding="utf-8")
    result = run_command("sort -oa.txt a.txt", _policy(tmp_path))
    assert result.status is CommandStatus.REJECTED
    assert target.read_text(encoding="utf-8") == "b\na\n"


def test_sort_clustered_output_flag_does_not_mutate(tmp_path: Path) -> None:
    target = tmp_path / "a.txt"
    target.write_text("b\na\n", encoding="utf-8")
    result = run_command("sort -no a.txt", _policy(tmp_path))
    assert result.status is CommandStatus.REJECTED
    assert target.read_text(encoding="utf-8") == "b\na\n"


def test_allowed_command_reads_named_file(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("one\ntwo\n", encoding="utf-8")
    result = run_command("cat a.txt", _policy(tmp_path))
    assert result.status is CommandStatus.COMPLETED
    assert "one" in result.stdout and "two" in result.stdout


def test_wc_removed_blocks_files0_from_manifest_git_read(tmp_path: Path) -> None:
    # GNU `wc --files0-from=F` reads input paths from inside `F`, so a vetted
    # manifest operand would smuggle unvetted paths (here `.git/config`) past
    # the path policy — the same content-indirection class as checksum `-c`.
    # `wc` is dropped from the default allowlist entirely; every form must be
    # refused and must never leak `.git` content.
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("secret-token\n", encoding="utf-8")
    (tmp_path / "files0").write_bytes(b".git/config\x00")
    (tmp_path / "a.txt").write_text("alpha\n", encoding="utf-8")
    for command in (
        "wc --files0-from=files0",
        "wc -l a.txt",
    ):
        result = run_command(command, _policy(tmp_path))
        assert result.status is CommandStatus.REJECTED, command
        assert (
            result.reason is CommandRejectionReason.DISALLOWED_EXECUTABLE
        ), command
        assert "secret-token" not in (result.stdout or ""), command


def test_executable_allowlist_not_bypassable_via_workspace_binary(
    tmp_path: Path, monkeypatch: Any
) -> None:
    import os
    import stat as stat_mod

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    planted = fake_bin / "cat"
    planted.write_text("#!/bin/sh\necho PWNED_BY_WORKSPACE_BINARY\n", encoding="utf-8")
    planted.chmod(
        planted.stat().st_mode
        | stat_mod.S_IEXEC
        | stat_mod.S_IXGRP
        | stat_mod.S_IXOTH
    )
    (tmp_path / "harmless.txt").write_text("real-content\n", encoding="utf-8")
    # Poison PATH so the workspace-planted `cat` is found first.
    monkeypatch.setenv("PATH", f"{fake_bin}{os.pathsep}{os.environ.get('PATH', '')}")

    result = run_command("cat harmless.txt", _policy(tmp_path))
    # Must execute the real system cat (or refuse) — never a workspace binary.
    assert "PWNED_BY_WORKSPACE_BINARY" not in result.stdout


def test_inspection_shell_cannot_mutate_via_sort_output_flag(tmp_path: Path) -> None:
    target = tmp_path / "a.txt"
    target.write_text("b\na\n", encoding="utf-8")
    result = run_command("sort -o a.txt a.txt", _policy(tmp_path))
    assert result.status is CommandStatus.REJECTED
    assert target.read_text(encoding="utf-8") == "b\na\n"


def test_sort_removed_blocks_write_and_helper_exec(tmp_path: Path) -> None:
    # sort is excluded from the default allowlist: it can write
    # (`-o`/`--output`/`--temporary-directory`) AND spawn a helper program
    # (`--compress-program=sh`), neither of which argv inspection can robustly
    # contain. Every form must be refused as a disallowed executable, and must
    # neither mutate the workspace nor execute the helper payload.
    payload = tmp_path / "a.txt"
    payload.write_text("touch PWNED\n", encoding="utf-8")
    for command in (
        "sort a.txt",
        "sort --output=a.txt a.txt",
        "sort -oa.txt a.txt",
        "sort --compress-program=sh -S 1K a.txt",
    ):
        result = run_command(command, _policy(tmp_path))
        assert result.status is CommandStatus.REJECTED, command
        assert (
            result.reason is CommandRejectionReason.DISALLOWED_EXECUTABLE
        ), command
    assert payload.read_text(encoding="utf-8") == "touch PWNED\n"
    assert not (tmp_path / "PWNED").exists()
