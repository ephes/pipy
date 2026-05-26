"""Unit tests for the Pi-parity terminal chrome helpers.

These cover the deterministic pieces of the native REPL chrome rendering
that do not require a live pty: bottom status line layout, resource
discovery (project-local plus user-home globals), and startup chrome
content. The pty-based behavioural tests live in
``tests/test_native_repl_pty_chrome.py``.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from pipy_harness.native import chrome


def test_format_bottom_status_line_aligns_left_and_right() -> None:
    fields = chrome.BottomStatusFields(
        cwd_label="",
        cost_label="$0.000",
        plan_label="sub",
        context_used_pct=0.0,
        context_budget_label="272k",
        context_budget_suffix="auto",
        provider_name="openai-codex",
        model_id="gpt-5.5",
        effort_label="high",
    )
    line = chrome.format_bottom_status_line(80, fields)
    assert line.startswith("$0.000 (sub) 0.0%/272k (auto)")
    assert line.endswith("(openai-codex) gpt-5.5 • high")
    assert len(line) == 80


def test_format_bottom_status_line_appends_attention_after_effort() -> None:
    fields = chrome.BottomStatusFields(
        cwd_label="",
        cost_label="$0.000",
        plan_label="api",
        context_used_pct=0.0,
        context_budget_label="4k",
        context_budget_suffix="bytes",
        provider_name="fake",
        model_id="fake-native-bootstrap",
        effort_label="default",
        attention="proposal ready · verify ready",
    )
    line = chrome.format_bottom_status_line(120, fields)
    assert line.endswith(
        "(fake) fake-native-bootstrap • default · proposal ready · verify ready"
    )
    assert "$0.000 (api) 0.0%/4k (bytes)" in line


def test_format_bottom_status_line_width_matches_separator_at_terminal_width() -> None:
    """Status-line length must equal the requested width so it aligns with
    the purple separator drawn at the same width — regression for the
    tool-loop footer once incorrectly fixed to the 88-col fallback.
    """

    fields = chrome.BottomStatusFields(
        cwd_label="",
        cost_label="$0.000",
        plan_label="sub",
        context_used_pct=0.0,
        context_budget_label="10",
        context_budget_suffix="tools",
        provider_name="openai-codex",
        model_id="gpt-5.5",
        effort_label="default",
    )
    for width in (80, 100, 120, 160):
        line = chrome.format_bottom_status_line(width, fields)
        assert len(line) == width, (
            f"status line at width={width} returned {len(line)} chars: {line!r}"
        )


def test_format_bottom_status_line_emits_token_arrows_after_a_turn() -> None:
    fields = chrome.BottomStatusFields(
        cwd_label="",
        cost_label="$0.012",
        plan_label="sub",
        context_used_pct=0.6,
        context_budget_label="272k",
        context_budget_suffix="auto",
        provider_name="openai-codex",
        model_id="gpt-5.5",
        effort_label="high",
        tokens_in=1700,
        tokens_out=35,
    )
    line = chrome.format_bottom_status_line(100, fields)
    assert line.startswith("↑1.7k ↓35")
    assert "$0.012 (sub) 0.6%/272k (auto)" in line
    assert line.endswith("(openai-codex) gpt-5.5 • high")


def test_discover_loaded_resource_names_returns_local_and_global(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "AGENTS.md").write_text("hi", encoding="utf-8")
    local_skill = workspace / ".pipy" / "skills" / "lint-fix"
    local_skill.mkdir(parents=True)

    fake_home = tmp_path / "home"
    global_skill = fake_home / ".pipy" / "skills" / "review-handoff"
    global_skill.mkdir(parents=True)
    # Hidden dotfile dirs must be filtered out.
    (fake_home / ".pipy" / "skills" / ".system").mkdir(parents=True)
    monkeypatch.setattr(chrome.Path, "home", classmethod(lambda cls: fake_home))

    context_names = chrome.discover_loaded_resource_names(workspace, "context")
    assert "AGENTS.md" in context_names

    skill_names = chrome.discover_loaded_resource_names(workspace, "skills")
    assert "lint-fix" in skill_names
    assert "review-handoff" in skill_names
    assert ".system" not in skill_names


def test_discover_does_not_leak_neighbor_tool_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pipy is a separate product — Claude/Codex/Pi configs must not leak."""

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "CLAUDE.md").write_text("claude-only", encoding="utf-8")
    (workspace / ".claude" / "skills" / "claude-skill").mkdir(parents=True)
    (workspace / ".codex" / "skills" / "codex-skill").mkdir(parents=True)

    fake_home = tmp_path / "home"
    (fake_home / ".claude" / "CLAUDE.md").parent.mkdir(parents=True)
    (fake_home / ".claude" / "CLAUDE.md").write_text("claude-home", encoding="utf-8")
    monkeypatch.setattr(chrome.Path, "home", classmethod(lambda cls: fake_home))

    context_names = chrome.discover_loaded_resource_names(workspace, "context")
    skill_names = chrome.discover_loaded_resource_names(workspace, "skills")

    assert "CLAUDE.md" not in context_names
    assert "~/.claude/CLAUDE.md" not in context_names
    assert "claude-skill" not in skill_names
    assert "codex-skill" not in skill_names


def test_print_startup_chrome_renders_context_and_skills(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_home = tmp_path / "home"
    (fake_home / ".pipy" / "skills" / "alpha").mkdir(parents=True)
    monkeypatch.setattr(chrome.Path, "home", classmethod(lambda cls: fake_home))

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "AGENTS.md").write_text("hi", encoding="utf-8")

    stream = io.StringIO()
    chrome.print_startup_chrome(stream, cwd=workspace)
    output = stream.getvalue()

    assert "pipy v" in output
    assert "escape interrupt" in output
    assert "[Context]" in output
    assert "AGENTS.md" in output
    assert "[Skills]" in output
    assert "alpha" in output


def test_print_bottom_status_block_emits_two_dim_rows() -> None:
    stream = io.StringIO()
    chrome.print_bottom_status_block(
        stream, cwd_label="/tmp/foo", status_line="$0.000 (sub) ..."
    )
    output = stream.getvalue()
    rows = [row for row in output.splitlines() if row.strip()]
    assert rows == ["/tmp/foo", "$0.000 (sub) ..."]
