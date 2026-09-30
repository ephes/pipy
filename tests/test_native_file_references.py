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


def test_resolve_out_of_workspace_git_and_ignored_paths_like_pi_read(
    tmp_path: Path,
) -> None:
    # READ2: `@file` reads through Pi's `read`, which resolves any path.
    workspace = tmp_path / "ws"
    (workspace / ".git").mkdir(parents=True)
    (workspace / ".git" / "HEAD").write_text("ref: main\n", encoding="utf-8")
    (workspace / ".gitignore").write_text("ignored.txt\n", encoding="utf-8")
    (workspace / "ignored.txt").write_text("ignored content\n", encoding="utf-8")
    outside = tmp_path / "notes.txt"
    outside.write_text("outside content\n", encoding="utf-8")

    prompt = f"read @../notes.txt @{outside} @.git/HEAD @ignored.txt"
    resolution = resolve_file_references(prompt, workspace_root=workspace)

    assert resolution.loaded_count == 4
    assert resolution.failed_count == 0
    augmented = resolution.augmented_prompt(prompt)
    assert "outside content" in augmented
    assert "ref: main" in augmented
    assert "ignored content" in augmented


def test_resolve_secret_shaped_file_loads_like_pi_read(tmp_path: Path) -> None:
    # READ1: `@file` reads through Pi's `read`, which has no content filter.
    body = "AWS_SECRET_ACCESS_KEY=AKIAIOSFODNN7EXAMPLEKEYDATA1234567890ABCD\n"
    (tmp_path / "creds.env").write_text(body, encoding="utf-8")

    resolution = resolve_file_references(
        "check @creds.env",
        workspace_root=tmp_path,
    )

    assert resolution.loaded_count == 1
    assert resolution.failed_count == 0
    assert resolution.references[0].text == body


def test_resolve_large_file_loads_first_2000_lines_with_notice(
    tmp_path: Path,
) -> None:
    # Formerly refused (over 256 KB); now Pi's read truncation applies.
    rows = "".join(f"{i}\n" for i in range(1, 60001))
    (tmp_path / "big.txt").write_text(rows, encoding="utf-8")
    assert (tmp_path / "big.txt").stat().st_size > 256 * 1024

    resolution = resolve_file_references("see @big.txt", workspace_root=tmp_path)

    assert resolution.loaded_count == 1
    text = resolution.references[0].text
    assert text is not None
    assert text.endswith(
        "\n1999\n2000\n\n[Showing lines 1-2000 of 60001. Use offset=2001 to continue.]"
    )


def test_resolve_two_near_limit_files_exceed_context_budget(tmp_path: Path) -> None:
    # Each reference loads about 50 KB; the second no longer fits in 64 KB.
    row = "x" * 99 + "\n"
    (tmp_path / "a.csv").write_text(row * 900, encoding="utf-8")
    (tmp_path / "b.csv").write_text(row * 900, encoding="utf-8")

    resolution = resolve_file_references(
        "compare @a.csv and @b.csv", workspace_root=tmp_path
    )

    first, second = resolution.references
    assert first.loaded is True
    assert first.text is not None
    assert first.text.endswith("(50.0KB limit). Use offset=513 to continue.]")
    assert second.loaded is False
    assert second.reason == "context_budget_exhausted"


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
        "see @ok.txt and @secret.env and @missing.env",
        workspace_root=tmp_path,
    )
    metadata = resolution.safe_metadata()
    serialized = json.dumps(metadata)

    assert metadata["file_reference_count"] == 3
    assert metadata["file_reference_loaded_count"] == 2
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
