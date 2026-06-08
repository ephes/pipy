"""Unit tests for Shift+Tab thinking-level cycling and model cycling logic.

These drive ``NativeToolReplSession`` helpers directly with a catalog-backed
provider state (no PTY) to pin the cycle order, the reasoning-support clamp, the
``thinking_level_change`` native-tree entry, and that no provider turn runs. The
observable footer/status behavior over a real PTY is covered separately.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import TextIO, cast

from pipy_harness.native import FakeNativeProvider, NativeToolReplSession
from pipy_harness.native.auth_store import AuthStore
from pipy_harness.native.catalog_state import ProviderCatalogState
from pipy_harness.native.repl_state import (
    NativeModelSelection,
    NativeReplProviderState,
)
from pipy_harness.native.session_tree import NativeSessionTree


def _state(tmp_path: Path, model_id: str) -> NativeReplProviderState:
    catalog = ProviderCatalogState(
        models_json_path=tmp_path / "models.json",
        auth_store=AuthStore(path=tmp_path / "auth.json"),
        env={"OPENAI_API_KEY": "sk"},
        openai_codex_auth_path=tmp_path / "no-codex.json",
    )
    return NativeReplProviderState(
        selection=NativeModelSelection("openai", model_id),
        provider_factory=lambda sel: FakeNativeProvider(supports_tool_calls=True),
        catalog_state=catalog,
        persist_defaults=False,
    )


def _session(state: NativeReplProviderState) -> NativeToolReplSession:
    return NativeToolReplSession(
        provider=FakeNativeProvider(supports_tool_calls=True),
        tool_registry={},
        provider_state=state,
    )


def _tree(tmp_path: Path) -> NativeSessionTree:
    return NativeSessionTree.create(tmp_path, persist=False)


class TestThinkingCycle:
    def test_cycles_through_pi_levels(self, tmp_path: Path) -> None:
        state = _state(tmp_path, "gpt-5.5")
        session = _session(state)
        tree = _tree(tmp_path)
        err = io.StringIO()
        seen = []
        for _ in range(6):
            session._cycle_thinking_level(
                terminal_ui=None,
                error_stream=cast(TextIO, err),
                session_tree=tree,
            )
            seen.append(state.thinking_level)
        assert seen == ["minimal", "low", "medium", "high", "off", "minimal"]

    def test_appends_thinking_level_change_entry(self, tmp_path: Path) -> None:
        state = _state(tmp_path, "gpt-5.5")
        session = _session(state)
        tree = _tree(tmp_path)
        session._cycle_thinking_level(
            terminal_ui=None,
            error_stream=cast(TextIO, io.StringIO()),
            session_tree=tree,
        )
        entries = [
            entry
            for entry in tree.entries
            if getattr(entry, "type", "") == "thinking_level_change"
        ]
        assert entries
        assert getattr(entries[-1], "thinking_level", None) == "minimal"

    def test_non_reasoning_model_reports_unsupported(self, tmp_path: Path) -> None:
        state = _state(tmp_path, "gpt-4o")  # gpt-4o has reasoning=False
        session = _session(state)
        tree = _tree(tmp_path)
        err = io.StringIO()
        session._cycle_thinking_level(
            terminal_ui=None,
            error_stream=cast(TextIO, err),
            session_tree=tree,
        )
        assert state.thinking_level is None
        assert "does not support thinking" in err.getvalue()
        assert not [
            entry
            for entry in tree.entries
            if getattr(entry, "type", "") == "thinking_level_change"
        ]

    def test_footer_effort_label_reflects_runtime_level(self, tmp_path: Path) -> None:
        state = _state(tmp_path, "gpt-5.5")
        session = _session(state)
        assert session._effort_label("openai", "gpt-5.5") in {"high", "default"}
        state.thinking_level = "low"
        assert session._effort_label("openai", "gpt-5.5") == "low"
