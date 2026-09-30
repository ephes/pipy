"""The ignore rules pipy's `find` and `grep` fallback share with rg and fd.

Unit tests pin the line compiler; the differential tests build a tree, run
the real ``fd``/``rg`` with Pi's flags when they are installed, and compare
their file sets with pipy's `find` and grep fallback (READ2).
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from pipy_harness.native.tools import ToolContext, ToolRequest, make_tool_request_id
from pipy_harness.native.tools.find import FindTool
from pipy_harness.native.tools.grep import GrepTool
from pipy_harness.native.tools.ignore_walk import (
    compile_gitignore_line,
    global_excludes_path,
)

_FD = shutil.which("fd")
_RG = shutil.which("rg")
_GIT = shutil.which("git")


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """pipy and the real binaries read the same (empty) global git config."""

    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))


@pytest.mark.parametrize(
    ("line", "path", "is_dir", "expected"),
    [
        ("foo", "foo", False, True),
        ("foo", "a/b/foo", False, True),
        ("foo", "foo", True, True),
        ("/foo", "foo", False, True),
        ("/foo", "nested/foo", False, False),
        ("foo/", "foo", True, True),
        ("foo/", "foo", False, False),
        ("/foo/", "nested/foo", True, False),
        ("a/b", "a/b", False, True),
        ("a/b", "x/a/b", False, False),
        ("*.{log,tmp}", "x/trace.log", False, True),
        ("*.{log,tmp}", "trace.tmp", False, True),
        ("*.{log,tmp}", "trace.txt", False, False),
        ("[ab].txt", "b.txt", False, True),
        ("[!ab].txt", "b.txt", False, False),
        ("**/deep", "a/b/deep", True, True),
        ("logs/**", "logs", True, False),
        ("logs/**", "logs/x/y.txt", False, True),
        ("a/**/b", "a/b", False, True),
        ("a/**/b", "a/x/y/b", False, True),
        ("*.txt", "dir/x.txt", False, True),
        ("dir/*.txt", "dir/sub/x.txt", False, False),
        ("\\#hash", "#hash", False, True),
        ("with\\ ", "with ", False, True),
        ("trailing   ", "trailing", False, True),
        # Measured: rg and fd read an unclosed `[` literally.
        ("[foo", "[foo", False, True),
        ("[foo", "sub/[foo", False, True),
        ("*.[ch", "x.[ch", False, True),
        ("[]x]", "]", False, True),
        ("[!]x]", "y", False, True),
        # A `[` inside a closed class is a member, not an unclosed class.
        ("[[]foo", "[foo", False, True),
        ("[[]foo", "\\foo", False, False),
    ],
)
def test_compile_gitignore_line_matches_git_rules(
    line: str, path: str, is_dir: bool, expected: bool
) -> None:
    rule = compile_gitignore_line(line)
    assert rule is not None
    assert rule.matches(path, is_dir=is_dir) is expected


@pytest.mark.parametrize(
    # An unclosed `{` fails to parse; rg and fd skip that line.
    "line",
    ["", "   ", "# comment", "!", "/", "{bar", "ba{z"],
)
def test_compile_gitignore_line_skips_empty_and_comment_lines(line: str) -> None:
    assert compile_gitignore_line(line) is None


def test_compile_gitignore_line_negation_and_escape() -> None:
    negated = compile_gitignore_line("!keep.log")
    literal = compile_gitignore_line("\\!bang")
    assert negated is not None and negated.negated
    assert negated.matches("keep.log", is_dir=False)
    assert literal is not None and not literal.negated
    assert literal.matches("!bang", is_dir=False)


# ---------------------------------------------------------------------------
# differential tests against the real binaries


def _write(root: Path, relative: str, text: str = "needle\n") -> None:
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def _tool_env(tmp_path: Path) -> dict[str, str]:
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    env = dict(os.environ)
    env.update(
        {
            "HOME": str(home),
            "XDG_CONFIG_HOME": str(home / ".config"),
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
        }
    )
    env.pop("RIPGREP_CONFIG_PATH", None)
    return env


def _fd_files(search: Path, env: dict[str, str], cwd: Path | None = None) -> set[str]:
    """Pi find.ts argv; files only (directories are compared via their files).

    ``cwd`` is the directory fd runs in (Pi's cwd, pipy's workspace root).
    """

    assert _FD is not None
    args = [_FD, "--glob", "--color=never", "--hidden"]
    if not any((d / ".git").exists() for d in (search, *search.parents)):
        args.append("--no-require-git")
    args += ["--max-results", "1000", "--type", "f", "--", "*", str(search)]
    out = subprocess.run(
        args, capture_output=True, text=True, env=env, cwd=cwd or search, check=True
    )
    return {os.path.relpath(line, search) for line in out.stdout.splitlines()}


def _find_files(search: Path, workspace: Path) -> set[str]:
    result = FindTool().invoke(
        ToolRequest(
            tool_request_id=make_tool_request_id(),
            tool_name="find",
            arguments={"pattern": "*", "path": str(search)},
        ),
        ToolContext(workspace_root=workspace),
    )
    assert result.is_error is False, result.output_text
    if result.output_text == "No files found matching pattern":
        return set()
    return {line for line in result.output_text.splitlines() if not line.endswith("/")}


def _rg_files(search: Path, cwd: Path, env: dict[str, str]) -> set[str]:
    assert _RG is not None
    out = subprocess.run(
        [
            _RG,
            "--files-with-matches",
            "--hidden",
            "--color=never",
            "--",
            "needle",
            str(search),
        ],
        capture_output=True,
        text=True,
        env=env,
        cwd=cwd,
        check=False,
    )
    return {os.path.relpath(line, search) for line in out.stdout.splitlines()}


def _fallback_files(
    search: Path, workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> set[str]:
    monkeypatch.setattr(
        "pipy_harness.native.tools.grep.shutil.which", lambda _name: None
    )
    result = GrepTool().invoke(
        ToolRequest(
            tool_request_id=make_tool_request_id(),
            tool_name="grep",
            arguments={"pattern": "needle", "path": str(search), "limit": 1000},
        ),
        ToolContext(workspace_root=workspace),
    )
    assert result.is_error is False, result.output_text
    if result.output_text == "No matches found":
        return set()
    return {line.split(":", 1)[0] for line in result.output_text.splitlines()}


def _repo_tree(root: Path) -> Path:
    """A tree with every rule class; ``root/outer/repo`` is a git repository."""

    outer = root / "outer"
    repo = outer / "repo"
    _write(outer, ".ignore", "by_parent_ignore.txt\n")
    _write(outer, ".gitignore", "by_parent_gitignore.txt\n")
    (repo / ".git" / "info").mkdir(parents=True)
    _write(repo, ".git/HEAD", "needle\n")
    _write(repo, ".git/info/exclude", "by_exclude.txt\n")
    _write(
        repo,
        ".gitignore",
        "ignored.txt\nlogs/\n/anchored.txt\n*.{log,tmp}\nre/*\n!re/keep.txt\n"
        "[bracket\n{brace\n[[]member\n",
    )
    # A leading byte order mark is dropped by rg and fd.
    _write(repo, ".ignore", "\ufeffby_ignore.txt\n!unignored_by_ignore.txt\n")
    _write(repo, ".rgignore", "by_rgignore.txt\n")
    _write(repo, ".fdignore", "by_fdignore.txt\n")
    _write(repo, "sub/.gitignore", "nested_only.txt\n/sub_anchored.txt\n")
    for name in (
        "keep.txt",
        ".hidden.txt",
        "ignored.txt",
        "logs/a.txt",
        "anchored.txt",
        "sub/anchored.txt",
        "trace.log",
        "trace.tmp",
        "re/drop.txt",
        "re/keep.txt",
        "by_ignore.txt",
        "unignored_by_ignore.txt",
        "by_rgignore.txt",
        "by_fdignore.txt",
        "by_exclude.txt",
        "by_parent_ignore.txt",
        "by_parent_gitignore.txt",
        "nested_only.txt",
        "sub/nested_only.txt",
        "sub/sub_anchored.txt",
        "sub/deeper/sub_anchored.txt",
        "sub/deeper/ok.txt",
        "[bracket",
        "sub/[bracket",
        "{brace",
        "[member",
        "\\member",
    ):
        _write(repo, name)
    _write(repo, "ignored_again.txt")
    (repo / ".gitignore").write_text(
        (repo / ".gitignore").read_text() + "unignored_by_ignore.txt\n"
    )
    return repo


def _plain_tree(root: Path) -> Path:
    """Outside any repository, with a nested repository below it."""

    plain = root / "plain"
    _write(plain, ".gitignore", "*.gen\nbuild/\n")
    _write(plain, ".ignore", "by_ignore.txt\n")
    for name in (
        "keep.txt",
        "a.gen",
        "build/out.txt",
        "by_ignore.txt",
        "nested/.git/HEAD",
        "nested/hidden.gen",
        "nested/keep.txt",
    ):
        _write(plain, name)
    _write(plain, "nested/.gitignore", "local.txt\n")
    _write(plain, "nested/local.txt")
    return plain


@pytest.mark.skipif(_FD is None, reason="fd is not installed")
@pytest.mark.parametrize("subdir", ["", "sub"])
def test_find_matches_fd_in_a_repository(tmp_path: Path, subdir: str) -> None:
    repo = _repo_tree(tmp_path)
    search = repo / subdir if subdir else repo

    expected = _fd_files(search, _tool_env(tmp_path))
    assert _find_files(search, repo) == expected
    assert "keep.txt" in expected or subdir


@pytest.mark.skipif(_FD is None, reason="fd is not installed")
def test_find_matches_fd_outside_a_repository(tmp_path: Path) -> None:
    plain = _plain_tree(tmp_path)

    expected = _fd_files(plain, _tool_env(tmp_path))
    assert _find_files(plain, plain) == expected
    # fd --no-require-git keeps the root *.gen rule inside the nested repo.
    assert "nested/hidden.gen" not in expected


@pytest.mark.skipif(_RG is None, reason="rg is not installed")
@pytest.mark.parametrize("subdir", ["", "sub"])
def test_grep_fallback_matches_rg_in_a_repository(
    tmp_path: Path, subdir: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo_tree(tmp_path)
    search = repo / subdir if subdir else repo

    expected = _rg_files(search, repo, _tool_env(tmp_path))
    assert _fallback_files(search, repo, monkeypatch) == expected


@pytest.mark.skipif(_RG is None, reason="rg is not installed")
def test_grep_fallback_matches_rg_outside_a_repository(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plain = _plain_tree(tmp_path)

    expected = _rg_files(plain, plain, _tool_env(tmp_path))
    assert _fallback_files(plain, plain, monkeypatch) == expected


@pytest.mark.skipif(
    _FD is None or _RG is None or _GIT is None, reason="fd, rg or git missing"
)
def test_linked_worktree_uses_the_main_repository_exclude_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = _tool_env(tmp_path)
    main = tmp_path / "main"
    main.mkdir()

    def git(*args: str, cwd: Path) -> None:
        assert _GIT is not None
        subprocess.run(
            [_GIT, "-c", "user.email=t@example.com", "-c", "user.name=t", *args],
            cwd=cwd,
            env=env,
            check=True,
            capture_output=True,
        )

    git("init", "-q", ".", cwd=main)
    _write(main, "tracked.txt")
    git("add", "tracked.txt", cwd=main)
    git("commit", "-q", "-m", "init", cwd=main)
    linked = tmp_path / "linked"
    git("worktree", "add", "-q", str(linked), cwd=main)
    _write(main, ".git/info/exclude", "excluded.txt\n")
    _write(linked, "excluded.txt")
    _write(linked, "kept.txt")

    fd_expected = _fd_files(linked, env)
    rg_expected = _rg_files(linked, linked, env)
    assert "excluded.txt" not in fd_expected
    assert "excluded.txt" not in rg_expected
    assert _find_files(linked, linked) == fd_expected
    assert _fallback_files(linked, linked, monkeypatch) == rg_expected


def _global_tree(tmp_path: Path) -> tuple[Path, Path]:
    """A repository and a plain directory, plus a global excludes file."""

    home = tmp_path / "home"
    _write(home, ".gitconfig", "[core]\n\texcludesFile = ~/global-ignore\n")
    _write(home, "global-ignore", "skip.txt\n/anch.txt\nsubd/\n!kept.log\n*.log\n")
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    plain = tmp_path / "plain"
    for root in (repo, repo / "sub", plain):
        for name in (
            "skip.txt",
            "anch.txt",
            "keep.txt",
            "subd/f.txt",
            "a.log",
            "kept.log",
        ):
            _write(root, name)
    return repo, plain


@pytest.mark.skipif(_FD is None or _RG is None, reason="fd or rg is not installed")
@pytest.mark.parametrize("cwd_name", ["repo", "repo/sub", "home"])
def test_global_excludes_match_fd_and_rg_in_a_repository(
    tmp_path: Path, cwd_name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, _plain = _global_tree(tmp_path)
    cwd = tmp_path / cwd_name
    env = _tool_env(tmp_path)

    fd_expected = _fd_files(repo, env, cwd)
    rg_expected = _rg_files(repo, cwd, env)
    assert "skip.txt" not in fd_expected
    assert "skip.txt" not in rg_expected
    assert _find_files(repo, cwd) == fd_expected
    assert _fallback_files(repo, cwd, monkeypatch) == rg_expected


@pytest.mark.skipif(_FD is None or _RG is None, reason="fd or rg is not installed")
def test_global_excludes_outside_a_repository(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _repo, plain = _global_tree(tmp_path)
    env = _tool_env(tmp_path)

    fd_expected = _fd_files(plain, env, plain)
    rg_expected = _rg_files(plain, plain, env)
    # fd --no-require-git applies them; rg outside a repository does not.
    assert "skip.txt" not in fd_expected
    assert "skip.txt" in rg_expected
    assert _find_files(plain, plain) == fd_expected
    assert _fallback_files(plain, plain, monkeypatch) == rg_expected


def test_global_excludes_path_follows_the_ignore_crate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    config = home / ".config"
    assert global_excludes_path() == config / "git" / "ignore"
    _write(config, "git/config", "[core]\n  ExcludesFile = ~/x/~y\n")
    assert global_excludes_path() == Path(f"{home}/x/{home}y")
    # ~/.gitconfig wins over the XDG config.
    _write(home, ".gitconfig", "[other]\nexcludesfile=/abs/path\n")
    assert global_excludes_path() == Path("/abs/path")
    # An empty XDG_CONFIG_HOME falls back to ~/.config.
    monkeypatch.setenv("XDG_CONFIG_HOME", "")
    (home / ".gitconfig").unlink()
    assert global_excludes_path() == Path(f"{home}/x/{home}y")
    (config / "git" / "config").unlink()
    assert global_excludes_path() == home / ".config" / "git" / "ignore"
