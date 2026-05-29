"""Unit tests for user-directed ``@file`` reference resolution.

These pin the shared parser/resolver that both pipy-native REPL modes use to
turn workspace-relative ``@path`` references in a genuine user prompt into a
bounded, fail-closed, provider-visible context appendix. The resolver reuses
the existing bounded ``ReadTool`` read/read-root policy; it adds no new reader.
"""

from __future__ import annotations

import json
from pathlib import Path

from pipy_harness.native.file_references import (
    MAX_FILE_REFERENCES_PER_TURN,
    FileReferenceResolution,
    parse_file_references,
    resolve_file_references,
)


def test_parse_extracts_single_reference() -> None:
    assert parse_file_references("look at @src/app.py please") == ("src/app.py",)


def test_parse_extracts_multiple_references_in_order() -> None:
    assert parse_file_references("@a.py and @b/c.py") == ("a.py", "b/c.py")


def test_parse_dedupes_preserving_first_order() -> None:
    assert parse_file_references("@a.py @b.py @a.py") == ("a.py", "b.py")


def test_parse_ignores_email_addresses() -> None:
    assert parse_file_references("ping me at jo@example.com now") == ()


def test_parse_ignores_bare_at_and_midword_at() -> None:
    assert parse_file_references("just @ a space and foo@bar text") == ()


def test_parse_matches_after_open_punctuation_and_trims_trailing() -> None:
    assert parse_file_references("see (@src/app.py), then @b.py.") == (
        "src/app.py",
        "b.py",
    )


def test_resolve_loads_excerpt_and_preserves_prompt_text(tmp_path: Path) -> None:
    (tmp_path / "notes.txt").write_text("line one\nline two\n", encoding="utf-8")

    resolution = resolve_file_references(
        "summarize @notes.txt for me",
        workspace_root=tmp_path,
    )

    assert resolution.loaded_count == 1
    assert resolution.failed_count == 0
    augmented = resolution.augmented_prompt("summarize @notes.txt for me")
    # User's literal prompt text is preserved verbatim.
    assert augmented.startswith("summarize @notes.txt for me")
    # Bounded excerpt content reaches the provider-visible context.
    assert "line one\nline two" in augmented
    assert "notes.txt" in augmented


def test_resolve_handles_multiple_files(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("alpha\n", encoding="utf-8")
    (tmp_path / "b.txt").write_text("bravo\n", encoding="utf-8")

    resolution = resolve_file_references(
        "compare @a.txt and @b.txt",
        workspace_root=tmp_path,
    )

    assert resolution.loaded_count == 2
    augmented = resolution.augmented_prompt("compare @a.txt and @b.txt")
    assert "alpha" in augmented
    assert "bravo" in augmented


def test_resolve_missing_file_fails_closed(tmp_path: Path) -> None:
    resolution = resolve_file_references(
        "read @nope.txt",
        workspace_root=tmp_path,
    )

    assert resolution.loaded_count == 0
    assert resolution.failed_count == 1
    # Prompt is returned unchanged when nothing loads; the user's text stays.
    assert resolution.augmented_prompt("read @nope.txt") == "read @nope.txt"
    # A safe local diagnostic is produced (no file content).
    assert any("nope.txt" in line for line in resolution.diagnostics())


def test_resolve_out_of_workspace_fails_closed(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    outside = tmp_path / "secret.txt"
    outside.write_text("outside content\n", encoding="utf-8")

    resolution = resolve_file_references(
        "read @../secret.txt",
        workspace_root=workspace,
    )

    assert resolution.loaded_count == 0
    assert resolution.failed_count == 1
    assert "outside content" not in resolution.augmented_prompt("read @../secret.txt")


def test_resolve_secret_shaped_file_does_not_leak(tmp_path: Path) -> None:
    (tmp_path / "creds.env").write_text(
        "AWS_SECRET_ACCESS_KEY=AKIAIOSFODNN7EXAMPLEKEYDATA1234567890ABCD\n",
        encoding="utf-8",
    )

    resolution = resolve_file_references(
        "check @creds.env",
        workspace_root=tmp_path,
    )

    assert resolution.loaded_count == 0
    assert resolution.failed_count == 1
    augmented = resolution.augmented_prompt("check @creds.env")
    assert "AKIA" not in augmented
    assert "SECRET" not in augmented.replace("@creds.env", "")


def test_resolve_one_bad_reference_does_not_block_good_one(tmp_path: Path) -> None:
    (tmp_path / "good.txt").write_text("good content\n", encoding="utf-8")

    resolution = resolve_file_references(
        "use @good.txt and @missing.txt",
        workspace_root=tmp_path,
    )

    assert resolution.loaded_count == 1
    assert resolution.failed_count == 1
    augmented = resolution.augmented_prompt("use @good.txt and @missing.txt")
    assert "good content" in augmented


def test_resolve_no_references_returns_prompt_unchanged(tmp_path: Path) -> None:
    resolution = resolve_file_references("plain prompt", workspace_root=tmp_path)

    assert resolution.reference_count == 0
    assert resolution.used is False
    assert resolution.augmented_prompt("plain prompt") == "plain prompt"


def test_resolve_caps_references_per_turn(tmp_path: Path) -> None:
    refs = []
    for index in range(MAX_FILE_REFERENCES_PER_TURN + 3):
        name = f"f{index}.txt"
        (tmp_path / name).write_text(f"content {index}\n", encoding="utf-8")
        refs.append(f"@{name}")
    prompt = "look " + " ".join(refs)

    resolution = resolve_file_references(prompt, workspace_root=tmp_path)

    assert resolution.loaded_count <= MAX_FILE_REFERENCES_PER_TURN
    assert resolution.over_budget_count == 3


def test_safe_metadata_is_counters_only(tmp_path: Path) -> None:
    (tmp_path / "secret.env").write_text(
        "API_KEY=sk-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n",
        encoding="utf-8",
    )
    (tmp_path / "ok.txt").write_text("hello\n", encoding="utf-8")

    resolution = resolve_file_references(
        "see @ok.txt and @secret.env",
        workspace_root=tmp_path,
    )
    metadata = resolution.safe_metadata()
    serialized = json.dumps(metadata)

    assert metadata["file_reference_count"] == 2
    assert metadata["file_reference_loaded_count"] == 1
    assert metadata["file_reference_failed_count"] == 1
    # No raw paths, file contents, or secrets in the archive-safe metadata.
    assert "ok.txt" not in serialized
    assert "secret.env" not in serialized
    assert "hello" not in serialized
    assert "sk-" not in serialized


def test_resolution_is_returned_type(tmp_path: Path) -> None:
    resolution = resolve_file_references("plain", workspace_root=tmp_path)
    assert isinstance(resolution, FileReferenceResolution)


def test_diagnostics_strip_control_characters(tmp_path: Path) -> None:
    # A token carrying an ANSI/control sequence must not be echoed verbatim
    # into a local diagnostic, where it could clear or manipulate the terminal.
    resolution = resolve_file_references(
        "look @\x1b[2Jboom\x07 there",
        workspace_root=tmp_path,
    )

    assert resolution.failed_count == 1
    for line in resolution.diagnostics():
        assert "\x1b" not in line
        assert "\x07" not in line
        assert not any(ord(char) < 32 for char in line if char not in "\t")
