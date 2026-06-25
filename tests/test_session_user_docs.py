from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_session_user_docs_match_current_slash_dispatcher_arguments() -> None:
    """Guard the shipped session docs against stale optional slash args."""

    usage = _read("docs/usage.md")
    sessions = _read("docs/sessions.md")
    dispatcher = _read("src/pipy_harness/native/tool_loop_session.py")

    assert 'if command_text == "/compact":' in dispatcher
    assert 'command_text.startswith("/compact ")' not in dispatcher
    assert "`/compact [prompt]`" not in usage
    assert "`/compact` | Compact context when enough history exists" in usage

    assert 'Path(path_arg).suffix.lower() == ".jsonl"' in dispatcher
    assert "when `file` ends in `.jsonl`" in usage
    assert "when `file` ends in `.jsonl`" in sessions

    assert "`--verbose`" in usage
    assert "`--offline`" in usage
    assert "`--verbose`" in sessions
    assert "`--offline`" in sessions
