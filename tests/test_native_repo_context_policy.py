from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).parents[1]


def read_repo_file(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def collapse_whitespace(text: str) -> str:
    return " ".join(text.split())


def markdown_section(text: str, heading: str) -> str:
    start_marker = f"### {heading}"
    start = text.index(start_marker)
    next_heading = text.find("\n### ", start + len(start_marker))
    if next_heading == -1:
        return text[start:]
    return text[start:next_heading]


def test_native_provider_visible_repo_context_policy_is_documented():
    spec = read_repo_file("docs/harness-spec.md")
    policy_section = markdown_section(spec, "Native Provider-Visible Repo Context Policy")
    compact_policy = collapse_whitespace(policy_section)

    assert "### Native Provider-Visible Repo Context Policy" in compact_policy
    assert "Provider-visible repo context is future provider input, not archive content." in compact_policy
    assert "bounded explicit file excerpts" in compact_policy
    assert "bounded search-result excerpts" in compact_policy
    assert "explicit per-turn workspace summaries" in compact_policy
    assert "short user-provided goal metadata" in compact_policy
    assert "sanitized tool-observation summaries" in compact_policy

    forbidden_terms = {
        "broad repo maps",
        "unbounded file contents",
        "persistent workspace summaries",
        "raw diffs",
        "patches",
        "stdout",
        "stderr",
        "shell command output",
        "raw tool payloads",
        "raw tool arguments",
        "raw provider responses",
        "model-selected paths",
        "secrets",
        "credentials",
        "API keys",
        "tokens",
        "private keys",
        "sensitive personal data",
    }
    for term in forbidden_terms:
        assert term in compact_policy

    assert "per excerpt: 4 KiB and 80 lines" in compact_policy
    assert "per source file per provider turn: 8 KiB and 160 lines" in compact_policy
    assert "total provider-visible repo context per provider turn: 24 KiB and 480 lines" in compact_policy
    assert "maximum excerpts per provider turn: 12" in compact_policy
    assert "maximum distinct source files per provider turn: 6" in compact_policy
    assert "normalized relative workspace path" in compact_policy
    assert "source label plus a stable path hash or omit the path" in compact_policy
    assert "must fail closed" in compact_policy
    assert "Unsafe data must be dropped or skipped in memory before provider-visible context" in compact_policy
    assert "JSONL, Markdown, and `--native-output json` may record only" in compact_policy
    assert "`tool_request_id`" in compact_policy
    assert "`turn_index`" in compact_policy
    assert "`native.tool.observation.recorded`" in compact_policy
    assert "does not add a post-tool provider call" in compact_policy


def test_session_storage_matches_repo_context_archive_boundary():
    storage = read_repo_file("docs/session-storage.md")
    compact_storage = collapse_whitespace(storage)

    assert "Future provider-visible repo context is not archive content." in compact_storage
    assert "bounded explicit file excerpts" in compact_storage
    assert "bounded search-result excerpts" in compact_storage
    assert "explicit per-turn workspace summaries" in compact_storage
    assert "sanitized tool-observation summaries" in compact_storage
    assert "broad repo maps" in compact_storage
    assert "unbounded file contents" in compact_storage
    assert "model-selected paths" in compact_storage
    assert "dropped or skipped before provider visibility" in compact_storage
    assert "metadata-only context fields" in compact_storage
    assert "raw excerpt text" in compact_storage
    assert "The current `pipy-native` runtime still does not read, archive, or forward live repo context" in compact_storage


def test_backlog_records_repo_context_policy_as_done():
    backlog = read_repo_file("docs/backlog.md")
    done = backlog[: backlog.index("## Next Slice")]

    assert "Native provider-visible repo context policy" in done
    assert "### Provider-Visible Repo Context Policy" not in backlog


def test_provider_visible_repo_context_policy_is_not_threaded_into_native_runtime():
    forbidden_runtime_terms = {
        "provider-visible repo context",
        "repo_context",
        "provider_visible_context",
        "RepoContext",
        "NativeRepoContext",
        "bounded explicit file excerpts",
        "search-result excerpts",
    }
    native_sources = sorted((ROOT / "src/pipy_harness/native").glob("*.py"))

    assert native_sources
    for source_path in native_sources:
        source = source_path.read_text(encoding="utf-8")
        for term in forbidden_runtime_terms:
            assert term not in source, f"{term!r} found in {source_path}"
