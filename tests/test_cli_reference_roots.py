"""Tests for the CLI reference-root resolver and auto-discovery.

These tests pin the boundary between `--read-root` CLI arguments, the
`PIPY_READ_ROOTS` environment variable, and the workspace-doc scan that
auto-discovers `~/<dir>` reference paths mentioned in
``docs/parity-criterion.md`` and friends.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pipy_harness.cli import (
    PIPY_READ_ROOTS_ENV,
    _resolve_reference_roots,
)


def test_resolve_reference_roots_returns_empty_when_unconfigured(
    tmp_path: Path,
):
    workspace = tmp_path / "ws"
    workspace.mkdir()

    resolved = _resolve_reference_roots([], cwd=workspace)

    assert resolved == ()


def test_resolve_reference_roots_uses_explicit_cli_values(tmp_path: Path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    ref = tmp_path / "ref"
    ref.mkdir()

    resolved = _resolve_reference_roots([str(ref)], cwd=workspace)

    assert resolved == (ref.resolve(),)


def test_resolve_reference_roots_reads_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    ref_a = tmp_path / "ref-a"
    ref_a.mkdir()
    ref_b = tmp_path / "ref-b"
    ref_b.mkdir()
    monkeypatch.setenv(PIPY_READ_ROOTS_ENV, f"{ref_a}:{ref_b}")

    resolved = _resolve_reference_roots([], cwd=workspace)

    assert resolved == (ref_a.resolve(), ref_b.resolve())


def test_resolve_reference_roots_skips_missing_paths(tmp_path: Path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    missing = tmp_path / "missing"
    real = tmp_path / "real"
    real.mkdir()

    resolved = _resolve_reference_roots(
        [str(missing), str(real)], cwd=workspace
    )

    assert resolved == (real.resolve(),)


def test_resolve_reference_roots_deduplicates(tmp_path: Path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    ref = tmp_path / "ref"
    ref.mkdir()

    resolved = _resolve_reference_roots(
        [str(ref), str(ref)], cwd=workspace
    )

    assert resolved == (ref.resolve(),)


def test_resolve_reference_roots_auto_discovers_from_parity_doc(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    docs = workspace / "docs"
    docs.mkdir()
    # Simulate a project that references a sibling repo via ~/<dir>
    sibling = tmp_path / "src-fake-pi"
    sibling.mkdir()
    (docs / "parity-criterion.md").write_text(
        "Source of truth: every `.ts` file under `~/src-fake-pi/packages/`\n",
        encoding="utf-8",
    )
    # Override home so the ~ in the doc resolves into tmp_path.
    monkeypatch.setenv("HOME", str(tmp_path))

    resolved = _resolve_reference_roots(None, cwd=workspace)

    assert sibling.resolve() in resolved


def test_resolve_reference_roots_explicit_overrides_auto_discovery(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    docs = workspace / "docs"
    docs.mkdir()
    sibling = tmp_path / "src-fake-pi"
    sibling.mkdir()
    (docs / "parity-criterion.md").write_text(
        "Source of truth: `~/src-fake-pi/`\n", encoding="utf-8"
    )
    explicit = tmp_path / "explicit"
    explicit.mkdir()
    monkeypatch.setenv("HOME", str(tmp_path))

    resolved = _resolve_reference_roots([str(explicit)], cwd=workspace)

    assert resolved == (explicit.resolve(),)


def test_resolve_reference_roots_skips_dot_config_directories(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    docs = workspace / "docs"
    docs.mkdir()
    fake_claude = tmp_path / ".claude"
    fake_claude.mkdir()
    (docs / "pi-parity.md").write_text(
        "Skills live under `~/.claude/skills`\n", encoding="utf-8"
    )
    monkeypatch.setenv("HOME", str(tmp_path))

    resolved = _resolve_reference_roots(None, cwd=workspace)

    assert fake_claude.resolve() not in resolved
