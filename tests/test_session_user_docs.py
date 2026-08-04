from __future__ import annotations

from pathlib import Path

from pipy_harness.native.agent import ProductContent
from pipy_harness.native.coding.command_registry import classify_coding_command
from pipy_harness.native.coding.commands import (
    CodingCommandAction,
    CodingCommandOutcomeKind,
)

ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_session_user_docs_match_current_slash_dispatcher_arguments() -> None:
    """Guard the shipped session docs against stale optional slash args."""

    usage = _read("docs/usage.md")
    sessions = _read("docs/sessions.md")
    # `/export` chooses its format by suffix, and that choice lives with the
    # transfer verbs rather than at the composition root.
    transfer = _read("src/pipy_harness/native/repl/session_transfer.py")

    compact = classify_coding_command(ProductContent("/compact"))
    compact_with_argument = classify_coding_command(ProductContent("/compact prompt"))
    assert compact.action is CodingCommandAction.COMPACT
    assert compact_with_argument.kind is CodingCommandOutcomeKind.UNHANDLED
    assert "`/compact [prompt]`" not in usage
    assert "`/compact` | Compact context when enough history exists" in usage

    assert 'Path(path_arg).suffix.lower() == ".jsonl"' in transfer
    assert "when `file` ends in `.jsonl`" in usage
    assert "when `file` ends in `.jsonl`" in sessions

    assert "`--verbose`" in usage
    assert "`--offline`" in usage
    assert "`--verbose`" in sessions
    assert "`--offline`" in sessions
