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
    WorkspaceInstructionDiscovery,
    XDG_CONFIG_HOME_ENV,
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


def test_per_directory_precedence_AGENTS_md_wins_over_pipy_md(
    tmp_path: Path,
) -> None:
    (tmp_path / "AGENTS.md").write_text("from-AGENTS.md\n", encoding="utf-8")
    (tmp_path / "pipy.md").write_text("from-pipy.md\n", encoding="utf-8")

    result = _discover(tmp_path)

    assert len(result.instructions) == 1
    only = result.instructions[0]
    assert only.path_label == "AGENTS.md"
    assert "from-AGENTS.md" in only.content
    assert "from-pipy.md" not in only.content


def test_per_directory_precedence_case_variants_when_filesystem_is_case_sensitive(
    tmp_path: Path,
) -> None:
    _case_sensitive_only(tmp_path)
    (tmp_path / "AGENTS.md").write_text("from-AGENTS.md\n", encoding="utf-8")
    (tmp_path / "AGENTS.MD").write_text("from-AGENTS.MD\n", encoding="utf-8")
    (tmp_path / "pipy.md").write_text("from-pipy.md\n", encoding="utf-8")
    (tmp_path / "PIPY.md").write_text("from-PIPY.md\n", encoding="utf-8")

    result = _discover(tmp_path)

    assert len(result.instructions) == 1
    only = result.instructions[0]
    assert only.path_label == "AGENTS.md"
    assert "from-AGENTS.md" in only.content
    assert "from-AGENTS.MD" not in only.content
    assert "from-pipy.md" not in only.content


def test_per_directory_precedence_falls_through_in_declared_order(
    tmp_path: Path,
) -> None:
    _case_sensitive_only(tmp_path)
    expected_payloads = {
        "AGENTS.md": "from-AGENTS.md\n",
        "AGENTS.MD": "from-AGENTS.MD\n",
        "pipy.md": "from-pipy.md\n",
        "PIPY.md": "from-PIPY.md\n",
    }
    assert INSTRUCTION_CANDIDATE_FILENAMES == (
        "AGENTS.md",
        "AGENTS.MD",
        "pipy.md",
        "PIPY.md",
    )

    for index, candidate in enumerate(INSTRUCTION_CANDIDATE_FILENAMES):
        for present in INSTRUCTION_CANDIDATE_FILENAMES[index:]:
            (tmp_path / present).write_text(expected_payloads[present], encoding="utf-8")
        result = _discover(tmp_path)
        assert len(result.instructions) == 1
        assert result.instructions[0].path_label == candidate
        for present in INSTRUCTION_CANDIDATE_FILENAMES[index:]:
            (tmp_path / present).unlink()


def test_per_directory_falls_through_AGENTS_to_pipy_md_on_any_filesystem(
    tmp_path: Path,
) -> None:
    (tmp_path / "pipy.md").write_text("from-pipy.md\n", encoding="utf-8")
    result = _discover(tmp_path)
    assert len(result.instructions) == 1
    assert result.instructions[0].path_label == "pipy.md"
    assert "from-pipy.md" in result.instructions[0].content


def test_claude_md_is_ignored_so_pipy_does_not_leak_neighbor_config(
    tmp_path: Path,
) -> None:
    """Pipy must not load Claude Code's CLAUDE.md into its system prompt."""

    (tmp_path / "CLAUDE.md").write_text("claude-only\n", encoding="utf-8")
    result = _discover(tmp_path)
    labels = [entry.path_label for entry in result.instructions]
    assert "CLAUDE.md" not in labels
    assert "claude-only" not in "".join(entry.content for entry in result.instructions)


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
    assert labels.index("../../AGENTS.md") < labels.index("../AGENTS.md") < labels.index(
        "AGENTS.md"
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
    (workspace / "pipy.md").write_text("legitimate\n", encoding="utf-8")

    result = _discover(workspace)
    workspace_entry = next(
        entry for entry in result.instructions if entry.path_label == "pipy.md"
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
    (parent / "pipy.md").write_text("parent-pipy\n", encoding="utf-8")

    result = _discover(workspace)
    parent_entry = next(
        e for e in result.instructions if e.content.strip() == "parent-pipy"
    )
    assert parent_entry.path_label == "../pipy.md"


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
