"""Focused tests for the workspace-context instruction loader.

Slice 2 of the Workspace Context Loading Parity Track. These tests pin
the discovery rules listed in
`pipy_harness.native.workspace_context`. They never wire the loader into
a provider, the REPL, or the session archive; slice 3 adds those tests.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

import pipy_harness.native.workspace_context as workspace_context
from pipy_harness.native.workspace_context import (
    DEFAULT_PER_FILE_BYTE_CAP,
    DEFAULT_TOTAL_BYTE_CAP,
    GLOBAL_PATH_LABEL_PREFIX,
    INSTRUCTION_CANDIDATE_FILENAMES,
    PER_FILE_TRUNCATION_MARKER_TEMPLATE,
    PIPY_CONFIG_DIR_NAME,
    PIPY_CONFIG_HOME_ENV,
    TOTAL_BYTE_CAP_MARKER_PATH_LABEL,
    TOTAL_BYTE_CAP_NOTICE,
    XDG_CONFIG_HOME_ENV,
    WorkspaceInstructionDiscovery,
    discover_workspace_instructions,
    resolve_global_instruction_root,
)


def _empty_env() -> dict[str, str]:
    return {}


def _filesystem_is_case_sensitive(directory: Path) -> bool:
    """Return True when `directory` lives on a case-sensitive filesystem."""

    probe = directory / "_pipy_case_probe.tmp"
    probe.write_text("probe", encoding="utf-8")
    try:
        upper = directory / "_PIPY_CASE_PROBE.TMP"
        return not upper.exists()
    finally:
        probe.unlink()


def _case_sensitive_only(directory: Path) -> None:
    if not _filesystem_is_case_sensitive(directory):
        pytest.skip("case-insensitive filesystem; case-precedence assertions skipped")


def _discover(
    workspace: Path,
    *,
    env: dict[str, str] | None = None,
    home_dir: Path | None = None,
    per_file_byte_cap: int = DEFAULT_PER_FILE_BYTE_CAP,
    total_byte_cap: int = DEFAULT_TOTAL_BYTE_CAP,
) -> WorkspaceInstructionDiscovery:
    return discover_workspace_instructions(
        workspace,
        env=env if env is not None else _empty_env(),
        home_dir=home_dir if home_dir is not None else workspace,
        per_file_byte_cap=per_file_byte_cap,
        total_byte_cap=total_byte_cap,
    )


# -- candidate precedence ----------------------------------------------------


def test_candidate_list_matches_pi_load_context_file_from_dir() -> None:
    # Pi resource-loader.ts `loadContextFileFromDir` candidates, in order.
    assert INSTRUCTION_CANDIDATE_FILENAMES == (
        "AGENTS.override.md",
        "AGENTS.md",
        "AGENTS.MD",
        "CLAUDE.md",
        "CLAUDE.MD",
    )


def test_agents_override_wins_over_agents_md(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.override.md").write_text("override\n", encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text("agents\n", encoding="utf-8")
    (tmp_path / "CLAUDE.md").write_text("claude\n", encoding="utf-8")

    result = _discover(tmp_path)

    assert [e.path_label for e in result.instructions] == ["AGENTS.override.md"]
    assert result.instructions[0].content == "override\n"


def test_agents_md_wins_over_claude_md(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("from-AGENTS.md\n", encoding="utf-8")
    (tmp_path / "CLAUDE.md").write_text("from-CLAUDE.md\n", encoding="utf-8")

    result = _discover(tmp_path)

    assert len(result.instructions) == 1
    only = result.instructions[0]
    assert only.path_label == "AGENTS.md"
    assert only.content == "from-AGENTS.md\n"


def test_per_directory_precedence_falls_through_in_declared_order(
    tmp_path: Path,
) -> None:
    _case_sensitive_only(tmp_path)
    for index, candidate in enumerate(INSTRUCTION_CANDIDATE_FILENAMES):
        for present in INSTRUCTION_CANDIDATE_FILENAMES[index:]:
            (tmp_path / present).write_text(f"from-{present}\n", encoding="utf-8")
        result = _discover(tmp_path)
        assert len(result.instructions) == 1
        assert result.instructions[0].path_label == candidate
        assert result.instructions[0].content == f"from-{candidate}\n"
        for present in INSTRUCTION_CANDIDATE_FILENAMES[index:]:
            (tmp_path / present).unlink()


def test_claude_md_loads_when_it_is_the_only_context_file(tmp_path: Path) -> None:
    (tmp_path / "CLAUDE.md").write_text("claude-only\n", encoding="utf-8")

    result = _discover(tmp_path)

    assert [e.path_label for e in result.instructions] == ["CLAUDE.md"]
    assert result.instructions[0].content == "claude-only\n"
    assert result.instructions[0].absolute_path == str(tmp_path / "CLAUDE.md")


def test_claude_md_in_an_ancestor_loads(tmp_path: Path) -> None:
    workspace = tmp_path / "repo" / "ws"
    workspace.mkdir(parents=True)
    (tmp_path / "repo" / "CLAUDE.md").write_text("ancestor\n", encoding="utf-8")
    (workspace / "AGENTS.md").write_text("workspace\n", encoding="utf-8")

    result = _discover(workspace)

    assert [e.path_label for e in result.instructions] == ["../CLAUDE.md", "AGENTS.md"]


def test_pipy_md_is_no_longer_a_context_file(tmp_path: Path) -> None:
    (tmp_path / "pipy.md").write_text("from-pipy.md\n", encoding="utf-8")
    (tmp_path / "PIPY.md").write_text("from-PIPY.md\n", encoding="utf-8")

    result = _discover(tmp_path)

    assert result.instructions == ()


def test_directory_named_like_a_candidate_falls_through(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").mkdir()
    (tmp_path / "CLAUDE.md").write_text("claude\n", encoding="utf-8")

    result = _discover(tmp_path)

    assert [e.path_label for e in result.instructions] == ["CLAUDE.md"]


def test_leading_bom_is_stripped(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_bytes(b"\xef\xbb\xbfhello\n")

    result = _discover(tmp_path)

    assert result.instructions[0].content == "hello\n"


# -- parent walk ordering ----------------------------------------------------


def test_parent_walk_root_most_first_workspace_last(tmp_path: Path) -> None:
    grand = tmp_path / "grand"
    parent = grand / "parent"
    workspace = parent / "ws"
    workspace.mkdir(parents=True)
    (grand / "AGENTS.md").write_text("grand\n", encoding="utf-8")
    (parent / "AGENTS.md").write_text("parent\n", encoding="utf-8")
    (workspace / "AGENTS.md").write_text("workspace\n", encoding="utf-8")

    result = _discover(workspace)
    labels = [entry.path_label for entry in result.instructions]
    contents = [entry.content.strip() for entry in result.instructions]

    # tmp_path also has no AGENTS.md, so only the three we wrote appear.
    # Root-most ancestor first, workspace itself last.
    assert "AGENTS.md" in labels
    assert labels[-1] == "AGENTS.md"
    assert contents[-1] == "workspace"
    assert "../AGENTS.md" in labels
    assert "../../AGENTS.md" in labels
    # Ordering: grandparent (..) is the root-most of the three, then parent (..), then ws (.).
    assert (
        labels.index("../../AGENTS.md")
        < labels.index("../AGENTS.md")
        < labels.index("AGENTS.md")
    )


def test_missing_files_do_not_fail(tmp_path: Path) -> None:
    # Nothing exists anywhere under tmp_path or its parents (up until tmp_path itself).
    result = _discover(tmp_path)
    # Some real ancestor (the test harness's tmp root) might still have nothing, so this
    # test passes when the result is empty or only contains entries from real ancestors.
    # The contract is that the call completes without exception.
    assert isinstance(result, WorkspaceInstructionDiscovery)
    assert result.total_byte_cap_reached is False
    # No synthetic terminator when the cap is not reached.
    assert all(
        entry.path_label != TOTAL_BYTE_CAP_MARKER_PATH_LABEL
        for entry in result.instructions
    )


# -- dedup by canonical path -------------------------------------------------


def test_read_failure_falls_through_to_next_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Pi `loadContextFileFromDir` warns on a read error and tries the next name.
    (tmp_path / "AGENTS.md").write_text("unreadable\n", encoding="utf-8")
    (tmp_path / "CLAUDE.md").write_text("fallback\n", encoding="utf-8")
    original_read = workspace_context._read_capped_bytes
    read_attempts: list[str] = []
    labels: list[str] = []

    def fail_first_read(
        path: Path, *, per_file_byte_cap: int
    ) -> tuple[bytes, int, str]:
        read_attempts.append(path.name)
        if path.name.lower() == "agents.md":
            raise OSError("simulated read failure")
        return original_read(path, per_file_byte_cap=per_file_byte_cap)

    def label(filename: str) -> str:
        labels.append(filename)
        return filename

    monkeypatch.setattr(workspace_context, "_read_capped_bytes", fail_first_read)

    loaded = workspace_context._load_first_candidate(
        tmp_path,
        per_file_byte_cap=DEFAULT_PER_FILE_BYTE_CAP,
        path_label_for=label,
    )

    assert loaded is not None
    entry, canonical_path = loaded
    assert entry.path_label == "CLAUDE.md"
    assert entry.content == "fallback\n"
    assert read_attempts[-1] == "CLAUDE.md"
    assert labels == ["CLAUDE.md"]
    assert canonical_path == (tmp_path / "CLAUDE.md").resolve()


def test_already_loaded_directory_file_does_not_fall_through(tmp_path: Path) -> None:
    # Pi dedups the directory's chosen context file; a seen file makes the
    # directory contribute nothing rather than falling back to CLAUDE.md.
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "AGENTS.md").write_text("global copy\n", encoding="utf-8")
    (workspace / "CLAUDE.md").write_text("never reached\n", encoding="utf-8")

    result = _discover(workspace, env={PIPY_CONFIG_HOME_ENV: str(workspace)})

    labels = [entry.path_label for entry in result.instructions]
    assert labels == [f"{GLOBAL_PATH_LABEL_PREFIX}AGENTS.md"]


def test_dedup_by_canonical_path_via_symlinked_ancestor(tmp_path: Path) -> None:
    real_parent = tmp_path / "real_parent"
    workspace = real_parent / "ws"
    workspace.mkdir(parents=True)
    (real_parent / "AGENTS.md").write_text("shared\n", encoding="utf-8")

    # The workspace contains a symlink AGENTS.md -> ../AGENTS.md (a *valid* symlink to
    # a file inside the workspace would be required for inclusion, so to test dedup we
    # rely on the real parent and a duplicate via a hardlink-style equivalent: a workspace
    # subdirectory whose AGENTS.md is a symlink to the parent's AGENTS.md fails the
    # "resolved inside dir" check. The cleanest dedup test uses two ancestor levels
    # symlinked to the same real dir.)
    sibling = tmp_path / "alias_parent"
    sibling.symlink_to(real_parent, target_is_directory=True)

    # Walk from alias_parent/ws so the parent chain visits the alias first; the loader
    # resolves both alias_parent/AGENTS.md and real_parent/AGENTS.md to the same real
    # path and includes it only once.
    alias_workspace = sibling / "ws"
    result = _discover(alias_workspace)
    matching = [
        entry for entry in result.instructions if entry.path_label.endswith("AGENTS.md")
    ]
    assert len(matching) == 1
    assert matching[0].content.strip() == "shared"


# -- symlink defense ---------------------------------------------------------


def test_symlink_resolving_outside_directory_is_skipped(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    outside = tmp_path / "outside"
    outside.mkdir()
    workspace.mkdir()
    secret = outside / "secrets.md"
    secret.write_text("never load me\n", encoding="utf-8")
    (workspace / "AGENTS.md").symlink_to(secret)

    result = _discover(workspace)

    # The unsafe symlink is skipped; the workspace dir contributes no
    # instruction file.
    assert all(
        entry.content.strip() != "never load me" for entry in result.instructions
    )
    assert all(entry.path_label != "AGENTS.md" for entry in result.instructions)


def test_symlink_falls_through_to_next_safe_candidate(tmp_path: Path) -> None:
    # An unsafe symlink at the highest-precedence slot does not block a lower
    # candidate from the same directory; the safer fallback wins.
    workspace = tmp_path / "ws"
    outside = tmp_path / "outside"
    outside.mkdir()
    workspace.mkdir()
    secret = outside / "secrets.md"
    secret.write_text("never load me\n", encoding="utf-8")
    (workspace / "AGENTS.md").symlink_to(secret)
    (workspace / "CLAUDE.md").write_text("legitimate\n", encoding="utf-8")

    result = _discover(workspace)
    workspace_entry = next(
        entry for entry in result.instructions if entry.path_label == "CLAUDE.md"
    )
    assert workspace_entry.content.strip() == "legitimate"
    assert all(
        entry.content.strip() != "never load me" for entry in result.instructions
    )


# -- global root resolution --------------------------------------------------


def test_global_root_PIPY_CONFIG_HOME_overrides_xdg(tmp_path: Path) -> None:
    pipy_home = tmp_path / "pipy-home"
    pipy_home.mkdir()
    (pipy_home / "AGENTS.md").write_text("from-pipy-home\n", encoding="utf-8")
    xdg_pipy = tmp_path / "xdg" / PIPY_CONFIG_DIR_NAME
    xdg_pipy.mkdir(parents=True)
    (xdg_pipy / "AGENTS.md").write_text("from-xdg\n", encoding="utf-8")
    home = tmp_path / "home"
    home_pipy = home / ".config" / PIPY_CONFIG_DIR_NAME
    home_pipy.mkdir(parents=True)
    (home_pipy / "AGENTS.md").write_text("from-home\n", encoding="utf-8")

    env = {
        PIPY_CONFIG_HOME_ENV: str(pipy_home),
        XDG_CONFIG_HOME_ENV: str(tmp_path / "xdg"),
    }

    workspace = tmp_path / "ws"
    workspace.mkdir()
    result = _discover(workspace, env=env, home_dir=home)

    global_entries = [
        entry
        for entry in result.instructions
        if entry.path_label.startswith(GLOBAL_PATH_LABEL_PREFIX)
    ]
    assert len(global_entries) == 1
    assert "from-pipy-home" in global_entries[0].content
    assert global_entries[0].path_label == f"{GLOBAL_PATH_LABEL_PREFIX}AGENTS.md"


def test_global_root_XDG_CONFIG_HOME_with_pipy_subdir(tmp_path: Path) -> None:
    xdg_root = tmp_path / "xdg"
    xdg_pipy = xdg_root / PIPY_CONFIG_DIR_NAME
    xdg_pipy.mkdir(parents=True)
    (xdg_pipy / "AGENTS.md").write_text("from-xdg\n", encoding="utf-8")
    home = tmp_path / "home"
    home_pipy = home / ".config" / PIPY_CONFIG_DIR_NAME
    home_pipy.mkdir(parents=True)
    (home_pipy / "AGENTS.md").write_text("from-home\n", encoding="utf-8")

    env = {XDG_CONFIG_HOME_ENV: str(xdg_root)}
    workspace = tmp_path / "ws"
    workspace.mkdir()
    result = _discover(workspace, env=env, home_dir=home)

    global_entries = [
        entry
        for entry in result.instructions
        if entry.path_label.startswith(GLOBAL_PATH_LABEL_PREFIX)
    ]
    assert len(global_entries) == 1
    assert "from-xdg" in global_entries[0].content


def test_global_root_default_home_config_pipy(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home_pipy = home / ".config" / PIPY_CONFIG_DIR_NAME
    home_pipy.mkdir(parents=True)
    (home_pipy / "AGENTS.md").write_text("from-home\n", encoding="utf-8")

    workspace = tmp_path / "ws"
    workspace.mkdir()
    result = _discover(workspace, env={}, home_dir=home)

    global_entries = [
        entry
        for entry in result.instructions
        if entry.path_label.startswith(GLOBAL_PATH_LABEL_PREFIX)
    ]
    assert len(global_entries) == 1
    assert "from-home" in global_entries[0].content


def test_global_root_missing_does_not_fail(tmp_path: Path) -> None:
    home = tmp_path / "home-no-config"
    # No .config dir at all.
    workspace = tmp_path / "ws"
    workspace.mkdir()
    result = _discover(workspace, env={}, home_dir=home)

    assert all(
        not entry.path_label.startswith(GLOBAL_PATH_LABEL_PREFIX)
        for entry in result.instructions
    )


def test_resolve_global_instruction_root_uses_env_then_default() -> None:
    explicit = resolve_global_instruction_root(
        env={PIPY_CONFIG_HOME_ENV: "/explicit/path"},
        home_dir=Path("/home/fake"),
    )
    assert explicit == Path("/explicit/path")

    xdg = resolve_global_instruction_root(
        env={XDG_CONFIG_HOME_ENV: "/xdg"},
        home_dir=Path("/home/fake"),
    )
    assert xdg == Path("/xdg") / PIPY_CONFIG_DIR_NAME

    default = resolve_global_instruction_root(env={}, home_dir=Path("/home/fake"))
    assert default == Path("/home/fake/.config") / PIPY_CONFIG_DIR_NAME


# -- byte caps ---------------------------------------------------------------


def test_per_file_byte_cap_truncates_with_marker(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    payload = "x" * 4096
    (workspace / "AGENTS.md").write_text(payload, encoding="utf-8")

    result = _discover(workspace, per_file_byte_cap=1024)

    entry = next(e for e in result.instructions if e.path_label == "AGENTS.md")
    assert entry.truncated is True
    assert entry.byte_length == 4096
    assert entry.sha256 == hashlib.sha256(payload.encode("utf-8")).hexdigest()
    assert PER_FILE_TRUNCATION_MARKER_TEMPLATE.format(cap=1024) in entry.content
    # Content keeps the first 1024 bytes plus the marker, not the entire file.
    assert len(entry.content) < len(payload)


def test_total_byte_cap_stops_with_synthetic_marker(tmp_path: Path) -> None:
    grand = tmp_path / "grand"
    parent = grand / "parent"
    workspace = parent / "ws"
    workspace.mkdir(parents=True)
    payload = "a" * 800
    (grand / "AGENTS.md").write_text(payload, encoding="utf-8")
    (parent / "AGENTS.md").write_text(payload, encoding="utf-8")
    (workspace / "AGENTS.md").write_text(payload, encoding="utf-8")

    # Per-file cap large enough to keep each file intact; total cap small enough
    # to fit only one full file plus the marker (the order is grand, parent, ws).
    result = _discover(
        workspace,
        per_file_byte_cap=4096,
        total_byte_cap=1000,
    )

    assert result.total_byte_cap_reached is True
    labels = [entry.path_label for entry in result.instructions]
    assert TOTAL_BYTE_CAP_MARKER_PATH_LABEL in labels
    marker = result.instructions[-1]
    assert marker.path_label == TOTAL_BYTE_CAP_MARKER_PATH_LABEL
    assert marker.content == TOTAL_BYTE_CAP_NOTICE
    assert marker.sha256 == ""
    assert marker.byte_length == 0
    # At least one file plus the marker.
    assert len(result.instructions) >= 2


def test_per_file_cap_rejects_zero_and_total_cap_rejects_zero(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    with pytest.raises(ValueError):
        discover_workspace_instructions(
            workspace, env={}, home_dir=tmp_path, per_file_byte_cap=0
        )
    with pytest.raises(ValueError):
        discover_workspace_instructions(
            workspace, env={}, home_dir=tmp_path, total_byte_cap=0
        )


# -- path label shapes -------------------------------------------------------


def test_path_label_workspace_relative_for_workspace_file(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "AGENTS.md").write_text("workspace\n", encoding="utf-8")
    result = _discover(workspace)
    workspace_entry = next(
        e for e in result.instructions if e.content.strip() == "workspace"
    )
    assert workspace_entry.path_label == "AGENTS.md"


def test_path_label_ancestor_dotdot_relative(tmp_path: Path) -> None:
    parent = tmp_path / "parent"
    workspace = parent / "ws"
    workspace.mkdir(parents=True)
    (parent / "CLAUDE.md").write_text("parent-pipy\n", encoding="utf-8")

    result = _discover(workspace)
    parent_entry = next(
        e for e in result.instructions if e.content.strip() == "parent-pipy"
    )
    assert parent_entry.path_label == "../CLAUDE.md"


def test_path_label_global_prefix(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home_pipy = home / ".config" / PIPY_CONFIG_DIR_NAME
    home_pipy.mkdir(parents=True)
    (home_pipy / "AGENTS.md").write_text("global\n", encoding="utf-8")
    workspace = tmp_path / "ws"
    workspace.mkdir()
    result = _discover(workspace, env={}, home_dir=home)
    global_entry = next(
        e
        for e in result.instructions
        if e.path_label.startswith(GLOBAL_PATH_LABEL_PREFIX)
    )
    assert global_entry.path_label == f"{GLOBAL_PATH_LABEL_PREFIX}AGENTS.md"


# -- safe decoding -----------------------------------------------------------


def test_invalid_utf8_decodes_with_replacement(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    raw = b"valid prefix \xff\xfe binary tail"
    (workspace / "AGENTS.md").write_bytes(raw)
    result = _discover(workspace)
    entry = next(e for e in result.instructions if e.path_label == "AGENTS.md")
    assert "valid prefix" in entry.content
    assert entry.byte_length == len(raw)
    assert entry.sha256 == hashlib.sha256(raw).hexdigest()


# -- ordering: global first, then ancestors descending --------------------


def test_global_first_then_ancestors_then_workspace(tmp_path: Path) -> None:
    parent = tmp_path / "parent"
    workspace = parent / "ws"
    workspace.mkdir(parents=True)
    (parent / "AGENTS.md").write_text("parent\n", encoding="utf-8")
    (workspace / "AGENTS.md").write_text("workspace\n", encoding="utf-8")
    home = tmp_path / "home"
    home_pipy = home / ".config" / PIPY_CONFIG_DIR_NAME
    home_pipy.mkdir(parents=True)
    (home_pipy / "AGENTS.md").write_text("global\n", encoding="utf-8")

    result = _discover(workspace, env={}, home_dir=home)
    contents = [entry.content.strip() for entry in result.instructions]

    # Global first, then ancestors descending (parent), then workspace last.
    workspace_index = contents.index("workspace")
    parent_index = contents.index("parent")
    global_index = contents.index("global")
    assert global_index < parent_index < workspace_index


# -- Pi project_context prompt section ----------------------------------------


def test_compose_system_prompt_renders_pi_project_context_section(
    tmp_path: Path,
) -> None:
    parent = tmp_path / "parent"
    workspace = parent / "ws"
    workspace.mkdir(parents=True)
    (parent / "CLAUDE.md").write_text("parent rules\n", encoding="utf-8")
    (workspace / "AGENTS.md").write_text("ws rules", encoding="utf-8")

    discovery = _discover(workspace)
    prompt = workspace_context.compose_system_prompt("BASE\n", discovery)

    parent_path = parent / "CLAUDE.md"
    ws_path = workspace / "AGENTS.md"
    assert prompt == (
        "BASE\n\n"
        "<project_context>\n"
        "Project-specific instructions and guidelines:\n\n"
        f'<project_instructions path="{parent_path}">\n'
        "parent rules\n\n"
        "</project_instructions>\n\n"
        f'<project_instructions path="{ws_path}">\n'
        "ws rules\n"
        "</project_instructions>\n"
        "</project_context>"
    )


def test_compose_system_prompt_without_context_returns_base_verbatim() -> None:
    empty = WorkspaceInstructionDiscovery(instructions=(), total_byte_cap_reached=False)
    assert workspace_context.compose_system_prompt("BASE\n", empty) == "BASE\n"


def test_safe_metadata_never_carries_absolute_paths_or_bodies(tmp_path: Path) -> None:
    (tmp_path / "CLAUDE.md").write_text("secret body\n", encoding="utf-8")

    discovery = _discover(tmp_path)
    metadata = workspace_context.workspace_instruction_safe_metadata(discovery)

    serialized = repr(metadata)
    assert str(tmp_path) not in serialized
    assert "secret body" not in serialized
    assert metadata["workspace_instruction_files"] == [
        {
            "path_label": "CLAUDE.md",
            "sha256": hashlib.sha256(b"secret body\n").hexdigest(),
            "byte_length": len(b"secret body\n"),
            "truncated": False,
        }
    ]


# -- linked-worktree shadowing (Pi findShadowedContextFile) -------------------


def _make_repo(root: Path) -> Path:
    git_dir = root / ".git"
    git_dir.mkdir(parents=True)
    (git_dir / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    return git_dir


def _make_linked_worktree(common_git_dir: Path, worktree_root: Path, name: str) -> None:
    admin = common_git_dir / "worktrees" / name
    admin.mkdir(parents=True)
    (admin / "HEAD").write_text("ref: refs/heads/feat\n", encoding="utf-8")
    (admin / "commondir").write_text("../..\n", encoding="utf-8")
    worktree_root.mkdir(parents=True, exist_ok=True)
    (worktree_root / ".git").write_text(f"gitdir: {admin}\n", encoding="utf-8")


def test_nested_linked_worktree_shadows_main_repo_same_named_file(
    tmp_path: Path,
) -> None:
    main = tmp_path / "main"
    common = _make_repo(main)
    worktree = main / ".claude" / "worktrees" / "wt"
    _make_linked_worktree(common, worktree, "wt")
    (main / "AGENTS.md").write_text("main copy\n", encoding="utf-8")
    (worktree / "AGENTS.md").write_text("worktree copy\n", encoding="utf-8")
    cwd = worktree / "src"
    cwd.mkdir()

    result = _discover(cwd)

    assert [e.content for e in result.instructions] == ["worktree copy\n"]


def test_nested_worktree_different_basename_is_not_shadowed(tmp_path: Path) -> None:
    main = tmp_path / "main"
    common = _make_repo(main)
    worktree = main / "wts" / "wt"
    _make_linked_worktree(common, worktree, "wt")
    (main / "AGENTS.md").write_text("main agents\n", encoding="utf-8")
    (worktree / "CLAUDE.md").write_text("worktree claude\n", encoding="utf-8")

    result = _discover(worktree)

    assert [e.content for e in result.instructions] == [
        "main agents\n",
        "worktree claude\n",
    ]


def test_sibling_worktree_does_not_shadow(tmp_path: Path) -> None:
    main = tmp_path / "main"
    common = _make_repo(main)
    worktree = tmp_path / "feat"
    _make_linked_worktree(common, worktree, "feat")
    (main / "AGENTS.md").write_text("main\n", encoding="utf-8")
    (worktree / "AGENTS.md").write_text("feat\n", encoding="utf-8")

    assert workspace_context._find_shadowed_context_file(worktree) is None
    result = _discover(worktree)
    assert [e.content for e in result.instructions] == ["feat\n"]


def test_ordinary_repo_keeps_ancestor_inheritance(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _make_repo(repo)
    sub = repo / "pkg"
    sub.mkdir()
    (repo / "AGENTS.md").write_text("repo\n", encoding="utf-8")
    (sub / "AGENTS.md").write_text("pkg\n", encoding="utf-8")

    assert workspace_context._find_shadowed_context_file(sub) is None
    result = _discover(sub)
    assert [e.content for e in result.instructions] == ["repo\n", "pkg\n"]


def test_bare_layout_worktree_does_not_shadow(tmp_path: Path) -> None:
    proj = tmp_path / "proj"
    bare = proj / ".bare"
    bare.mkdir(parents=True)
    (bare / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    worktree = proj / "main"
    _make_linked_worktree(bare, worktree, "main")
    (proj / "AGENTS.md").write_text("proj\n", encoding="utf-8")
    (worktree / "AGENTS.md").write_text("main worktree\n", encoding="utf-8")

    assert workspace_context._find_shadowed_context_file(worktree) is None
    result = _discover(worktree)
    assert [e.content for e in result.instructions] == ["proj\n", "main worktree\n"]


def test_submodule_does_not_shadow(tmp_path: Path) -> None:
    superproject = tmp_path / "super"
    super_git = _make_repo(superproject)
    module_git = super_git / "modules" / "sub"
    module_git.mkdir(parents=True)
    (module_git / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    submodule = superproject / "sub"
    submodule.mkdir()
    (submodule / ".git").write_text("gitdir: ../.git/modules/sub\n", encoding="utf-8")
    (superproject / "AGENTS.md").write_text("super\n", encoding="utf-8")
    (submodule / "AGENTS.md").write_text("sub\n", encoding="utf-8")

    assert workspace_context._find_shadowed_context_file(submodule) is None
    result = _discover(submodule)
    assert [e.content for e in result.instructions] == ["super\n", "sub\n"]
