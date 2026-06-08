"""Unit tests for the pipy-owned editor completion core (Pi parity).

These pin the exact/prefix/substring scorer (mirroring Pi's ``scoreEntry``),
the ``@``-token extractor, the workspace-bounded ``@`` candidate walk, the
path-like Tab trigger, and the single-directory path completion. They are the
pure-logic foundation the TUI editor wires into; the live-region behavior is
proven separately by the real-PTY tests.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pipy_harness.native.editor_completion import (
    at_candidates,
    extract_at_token,
    extract_path_prefix,
    path_candidates,
    score_entry,
)


class TestScoreEntry:
    def test_exact_filename_match_scores_highest(self) -> None:
        assert score_entry("src/config.ts", "config.ts", is_directory=False) == 100

    def test_filename_prefix_scores_eighty(self) -> None:
        assert score_entry("src/config.ts", "conf", is_directory=False) == 80

    def test_filename_substring_scores_fifty(self) -> None:
        assert score_entry("src/config.ts", "fig", is_directory=False) == 50

    def test_full_path_substring_scores_thirty(self) -> None:
        assert score_entry("src/tui/config.ts", "tui", is_directory=False) == 30

    def test_directory_bonus_adds_ten_when_matched(self) -> None:
        assert score_entry("src/widgets", "widg", is_directory=True) == 90

    def test_directory_bonus_not_added_when_no_match(self) -> None:
        assert score_entry("src/widgets", "zzz", is_directory=True) == 0

    def test_case_insensitive(self) -> None:
        assert score_entry("src/Config.ts", "config.ts", is_directory=False) == 100

    def test_non_substring_query_scores_zero(self) -> None:
        # Pi is exact/prefix/substring, NOT fuzzy subsequence: "srctuiconfig"
        # is not an ordered substring of "src/tui/config.ts".
        assert score_entry("src/tui/config.ts", "srctuiconfig", is_directory=False) == 0


class TestExtractAtToken:
    def test_bare_at_at_cursor(self) -> None:
        assert extract_at_token("@") == (0, "")

    def test_at_token_with_query(self) -> None:
        assert extract_at_token("look at @conf") == (8, "conf")

    def test_no_at_token_returns_none(self) -> None:
        assert extract_at_token("hello world") is None

    def test_at_token_after_space_only(self) -> None:
        # A space after the token ends the @ context.
        assert extract_at_token("@conf ") is None

    def test_quoted_at_token_allows_spaces(self) -> None:
        assert extract_at_token('@"my dir') == (0, "my dir")

    def test_slash_command_is_not_an_at_token(self) -> None:
        assert extract_at_token("/model") is None


class TestAtCandidates:
    @pytest.fixture()
    def workspace(self, tmp_path: Path) -> Path:
        (tmp_path / "src" / "tui").mkdir(parents=True)
        (tmp_path / "src" / "tui" / "config.py").write_text("x\n")
        (tmp_path / "src" / "config.py").write_text("y\n")
        (tmp_path / "README.md").write_text("z\n")
        (tmp_path / ".git").mkdir()
        (tmp_path / ".git" / "HEAD").write_text("ref\n")
        (tmp_path / "my dir").mkdir()
        (tmp_path / "my dir" / "note.txt").write_text("n\n")
        return tmp_path

    def test_ranks_by_score_descending(self, workspace: Path) -> None:
        items = at_candidates(workspace, "config")
        labels = [item.label for item in items]
        # config.py files (prefix=80) rank above README (no match dropped).
        assert "config.py" in labels
        assert "README.md" not in labels

    def test_non_substring_query_does_not_match(self, workspace: Path) -> None:
        # @srctuiconfig must NOT match src/tui/config.py (no fuzzy).
        items = at_candidates(workspace, "srctuiconfig")
        assert items == []

    def test_git_is_default_denied(self, workspace: Path) -> None:
        items = at_candidates(workspace, "HEAD")
        assert all(".git" not in item.value for item in items)

    def test_directory_keeps_trailing_slash(self, workspace: Path) -> None:
        items = at_candidates(workspace, "tui")
        dir_items = [item for item in items if item.label.endswith("/")]
        assert dir_items
        assert all(item.value.rstrip('@"').endswith("/") or item.value.endswith("/") for item in dir_items)

    def test_space_path_is_quoted(self, workspace: Path) -> None:
        items = at_candidates(workspace, "my dir")
        assert items
        assert any(item.value.startswith('@"') for item in items)

    def test_candidate_value_is_at_prefixed(self, workspace: Path) -> None:
        items = at_candidates(workspace, "config")
        assert all(item.value.startswith("@") for item in items)

    def test_symlink_escaping_workspace_is_not_offered(self, tmp_path: Path) -> None:
        import os

        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "escape-config.py").write_text("secret\n")
        ws = tmp_path / "ws"
        ws.mkdir()
        (ws / "config.py").write_text("inside\n")
        # A symlink inside the workspace pointing at an outside file.
        try:
            os.symlink(outside / "escape-config.py", ws / "linked-config.py")
        except (OSError, NotImplementedError):
            import pytest

            pytest.skip("symlinks unavailable on this platform")
        items = at_candidates(ws, "config")
        labels = {item.label for item in items}
        # The contained file is offered; the escaping symlink is not.
        assert "config.py" in labels
        assert "linked-config.py" not in labels


class TestExtractPathPrefix:
    def test_natural_trigger_on_slash(self) -> None:
        assert extract_path_prefix("./src/co", force=False) == (0, "./src/co")

    def test_natural_trigger_on_dot(self) -> None:
        assert extract_path_prefix(".env", force=False) == (0, ".env")

    def test_natural_trigger_on_tilde_slash(self) -> None:
        assert extract_path_prefix("~/pr", force=False) == (0, "~/pr")

    def test_prose_is_not_a_path_prefix(self) -> None:
        assert extract_path_prefix("hello", force=False) is None

    def test_empty_token_after_trailing_space_is_a_no_op(self) -> None:
        # `hello <Tab>` must not list the working directory — Tab is a no-op in
        # prose, not a forced directory listing.
        assert extract_path_prefix("hello ", force=False) is None

    def test_force_returns_last_token(self) -> None:
        assert extract_path_prefix("hello", force=True) == (0, "hello")

    def test_prefix_starts_after_last_space(self) -> None:
        assert extract_path_prefix("cat ./src", force=False) == (4, "./src")

    def test_quoted_prefix_with_space_is_taken_whole(self) -> None:
        # A quoted path with an embedded space must not split on the space, so
        # progressive completion survives (regression: it used to see `dir/"`).
        assert extract_path_prefix('cat "./my dir/', force=False) == (4, '"./my dir/')

    def test_quoted_prefix_mid_segment(self) -> None:
        assert extract_path_prefix('"./my dir/fi', force=False) == (0, '"./my dir/fi')


class TestPathCandidates:
    @pytest.fixture()
    def workspace(self, tmp_path: Path) -> Path:
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "alpha.py").write_text("a\n")
        (tmp_path / "src" / "beta").mkdir()
        (tmp_path / "scripts").mkdir()
        return tmp_path

    def test_completes_directory_contents(self, workspace: Path) -> None:
        items = path_candidates(workspace, "./src/")
        labels = {item.label for item in items}
        assert "alpha.py" in labels
        assert "beta/" in labels

    def test_directories_sort_before_files(self, workspace: Path) -> None:
        items = path_candidates(workspace, "./src/")
        kinds = [item.label.endswith("/") for item in items]
        # all directory-trues come before file-falses
        assert kinds == sorted(kinds, reverse=True)

    def test_filename_fragment_is_case_insensitive(self, workspace: Path) -> None:
        # The filename fragment matches case-insensitively (Pi getFileSuggestions),
        # while the directory portion resolves case-sensitively (so this uses the
        # real "src" dir but an upper-case "AL" fragment) — portable across
        # case-sensitive and case-insensitive filesystems.
        items = path_candidates(workspace, "./src/AL")
        assert any(item.label == "alpha.py" for item in items)

    def test_preserves_dot_slash_prefix_in_value(self, workspace: Path) -> None:
        items = path_candidates(workspace, "./sc")
        assert any(item.value == "./scripts/" for item in items)

    def test_quoted_directory_with_space_completes_and_requotes(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "my dir").mkdir()
        (tmp_path / "my dir" / "file.txt").write_text("x\n")
        items = path_candidates(tmp_path, '"my dir/')
        assert any(item.value == '"my dir/file.txt"' for item in items)


class TestQuotedReferenceResolution:
    """The @path / @image: resolvers must load quoted, space-containing paths
    (the picker, Tab completion, and drag-drop all emit quoted refs)."""

    def test_quoted_at_path_with_space_resolves(self, tmp_path: Path) -> None:
        from pipy_harness.native.file_references import resolve_file_references

        (tmp_path / "my dir").mkdir()
        (tmp_path / "my dir" / "note.txt").write_text("hello-quoted\n")
        result = resolve_file_references(
            'see @"my dir/note.txt" please', workspace_root=tmp_path
        )
        assert result.reference_count == 1
        assert result.loaded_count == 1

    def test_quoted_at_image_with_space_resolves(self, tmp_path: Path) -> None:
        from pipy_harness.native.image_attachment import resolve_image_attachments

        png = tmp_path / "a b.png"
        png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
        result = resolve_image_attachments(
            'look @image:"a b.png" here', workspace_root=tmp_path
        )
        assert result.reference_count == 1
        assert result.loaded_count == 1

    def test_unquoted_reference_still_trims_prose_punctuation(
        self, tmp_path: Path
    ) -> None:
        from pipy_harness.native.file_references import parse_file_references

        assert parse_file_references("(@a.py),") == ("a.py",)


class TestQuotedDirectoryContinuation:
    """A spaced directory completion keeps the quote open so the next Tab can
    continue inside it (regression: a closing quote broke continuation)."""

    def test_spaced_directory_completion_is_open_quoted(self, tmp_path: Path) -> None:
        (tmp_path / "my dir").mkdir()
        (tmp_path / "my dir" / "sub").mkdir()
        items = path_candidates(tmp_path, '"my ')
        dir_item = next(i for i in items if i.label == "my dir/")
        # Open quote (no closing) so a subsequent Tab still sees an unmatched
        # quote and continues inside the directory.
        assert dir_item.value == '"my dir/'
        # The open-quoted value round-trips back through extract_path_prefix.
        extracted = extract_path_prefix(dir_item.value, force=False)
        assert extracted is not None
        _start, prefix = extracted
        assert prefix == '"my dir/'
        # ...and completes the directory's contents.
        nested = path_candidates(tmp_path, "my dir/")
        assert any(i.label == "sub/" for i in nested)


class TestPathCompletionGitDeny:
    def test_listing_into_git_dir_is_denied(self, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        (tmp_path / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
        # `.git/<Tab>` must not list the .git directory's contents.
        assert path_candidates(tmp_path, ".git/") == []

    def test_listing_into_git_subdir_is_denied(self, tmp_path: Path) -> None:
        (tmp_path / ".git" / "refs").mkdir(parents=True)
        (tmp_path / ".git" / "refs" / "tagfile").write_text("x\n")
        assert path_candidates(tmp_path, ".git/refs/") == []

    def test_normal_dir_still_lists(self, tmp_path: Path) -> None:
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "a.py").write_text("x\n")
        labels = {item.label for item in path_candidates(tmp_path, "src/")}
        assert "a.py" in labels
