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


def assert_terms_in_order(text: str, terms: list[str]) -> None:
    cursor = -1
    for term in terms:
        index = text.index(term)
        assert index > cursor, f"{term!r} did not appear after previous gate term"
        cursor = index


def test_native_approval_and_sandbox_baseline_is_documented():
    spec = read_repo_file("docs/harness-spec.md")
    policy_section = markdown_section(spec, "Native Approval And Sandbox Enforcement Baseline")
    compact_policy = collapse_whitespace(policy_section)

    assert "### Native Approval And Sandbox Enforcement Baseline" in compact_policy
    assert "does not add live approval prompts" in compact_policy
    assert "sandbox enforcement" in compact_policy
    assert "real repo reads" in compact_policy
    assert "provider-visible repo context forwarding" in compact_policy
    assert "live observation emission" in compact_policy
    assert "post-tool provider call" in compact_policy

    for decision in ("`pending`", "`allowed`", "`denied`", "`skipped`", "`failed`"):
        assert decision in compact_policy

    for operation in (
        "read-only workspace tools",
        "provider-visible repo context production",
        "write tools",
        "patch proposal apply",
        "shell execution",
        "network access",
        "verification commands",
    ):
        assert operation in compact_policy

    for sandbox_mode in (
        "`no-workspace-access`",
        "`read-only-workspace`",
        "`mutating-workspace`",
    ):
        assert sandbox_mode in compact_policy

    for capability in (
        "`workspace_read_allowed`",
        "`filesystem_mutation_allowed`",
        "`shell_execution_allowed`",
        "`network_access_allowed`",
    ):
        assert capability in compact_policy

    assert_terms_in_order(
        compact_policy,
        [
            "Policy validation",
            "Request normalization and identity",
            "Approval gate",
            "Sandbox capability gate",
            "Path and context validation",
            "Execution gate",
            "Observation and provider-context gate",
        ],
    )

    for fail_closed_case in (
        "Missing policy",
        "unsupported approval mode",
        "unsupported sandbox mode",
        "denied approval",
        "unavailable approval UI",
        "sandbox mismatch",
        "unsafe request data",
        "model-selected paths",
        "attempted capability escalation",
    ):
        assert fail_closed_case in compact_policy
    assert "must not execute" in compact_policy

    for allowed_metadata in (
        "policy labels",
        "approval required/resolved booleans",
        "decision labels",
        "safe reason labels",
        "`tool_request_id`",
        "`turn_index`",
        "`duration_seconds`",
        "storage booleans",
    ):
        assert allowed_metadata in compact_policy

    for forbidden in (
        "raw prompts",
        "model output",
        "provider responses",
        "provider-native payloads",
        "raw tool payloads",
        "stdout",
        "stderr",
        "diffs",
        "patches",
        "full file contents",
        "shell commands",
        "raw args",
        "model-selected paths",
        "secrets",
        "credentials",
        "API keys",
        "tokens",
        "private keys",
        "sensitive personal data",
    ):
        assert forbidden in compact_policy


def test_session_storage_matches_approval_sandbox_archive_boundary():
    storage = read_repo_file("docs/session-storage.md")
    compact_storage = collapse_whitespace(storage)

    assert "Future approval and sandbox records must stay metadata-only." in compact_storage
    assert "`pending`, `allowed`, `denied`, `skipped`, and `failed`" in compact_storage
    assert "`no-workspace-access`, `read-only-workspace`, and `mutating-workspace`" in compact_storage
    assert "`workspace_read_allowed`" in compact_storage
    assert "`filesystem_mutation_allowed`" in compact_storage
    assert "`shell_execution_allowed`" in compact_storage
    assert "`network_access_allowed`" in compact_storage
    assert "read-only tools produce provider-visible repo context" in compact_storage
    assert "write tools or patch application" in compact_storage
    assert "verification commands such as an allowlisted `just check`" in compact_storage
    assert "approval required/resolved booleans" in compact_storage
    assert "`tool_request_id`" in compact_storage
    assert "`turn_index`" in compact_storage
    assert "`duration_seconds`" in compact_storage
    assert "full file contents" in compact_storage
    assert "shell commands" in compact_storage
    assert "raw args" in compact_storage
    assert "attempted capability escalation must fail closed" in compact_storage


def test_openai_subscription_auth_decision_is_documented():
    spec = read_repo_file("docs/harness-spec.md")
    decision_section = markdown_section(spec, "OpenAI Subscription-Backed Native Auth Decision")
    compact_decision = collapse_whitespace(decision_section)

    assert "Decision: `blocked-for-now`" in compact_decision
    assert "Decision date: 2026-05-07." in compact_decision
    for source_url in (
        "https://developers.openai.com/api/reference/overview",
        "https://help.openai.com/en/articles/6950777-what-is-chatgpt-plus",
        "https://help.openai.com/en/articles/9039756",
        "https://developers.openai.com/codex/auth",
        "https://developers.openai.com/codex/pricing",
        "https://developers.openai.com/codex/sdk",
    ):
        assert source_url in decision_section

    for checked in (
        "ChatGPT subscription versus OpenAI API billing",
        "OpenAI API authentication",
        "Codex CLI authentication and device-code sign-in behavior",
        "Codex pricing",
        "Codex SDK surface",
    ):
        assert checked in compact_decision

    assert "API-key path for direct API calls" in compact_decision
    assert "subscription-backed sign-in path for Codex product clients" in compact_decision
    assert "do not document an official, stable, locally usable OAuth or device-code" in (
        compact_decision
    )
    assert "third-party native application call OpenAI models directly" in compact_decision
    assert "`--native-provider openai` Responses API provider remains the OpenAI baseline" in (
        compact_decision
    )
    assert "`OPENAI_API_KEY` plus `--native-model`" in compact_decision

    assert "Rejected approaches:" in decision_section
    for rejected in (
        "scraping or reusing ChatGPT, browser, Codex CLI, or IDE extension credential stores",
        "access tokens",
        "refresh tokens",
        "cookies",
        "authorization headers",
        "cached `auth.json` values",
        "reverse engineering private product endpoints",
        "wrapping Codex, ChatGPT, Claude Code, or another product UI/CLI",
        "raw provider response",
    ):
        assert rejected in compact_decision

    assert "OpenRouter provider support with explicit model selection" in compact_decision
    assert "Local model provider integrations remain deferred pending benchmark work" in (
        compact_decision
    )
    assert "Anthropic subscription-backed native provider support is not promoted" in (
        compact_decision
    )


def test_openrouter_provider_baseline_is_documented():
    spec = read_repo_file("docs/harness-spec.md")
    runtime_section = markdown_section(spec, "Native Runtime Bootstrap")
    compact_runtime = collapse_whitespace(runtime_section)

    assert "The second real provider is the OpenRouter Chat Completions provider" in (
        compact_runtime
    )
    assert "--native-provider openrouter --native-model <provider/model>" in compact_runtime
    assert "`OPENROUTER_API_KEY`" in compact_runtime
    assert "https://openrouter.ai/api/v1/chat/completions" in runtime_section
    assert "system` and `user` chat messages" in compact_runtime
    assert "`prompt_tokens`, `completion_tokens`, and `total_tokens`" in compact_runtime
    assert "`input_tokens`, `output_tokens`, and `total_tokens`" in compact_runtime
    for forbidden in (
        "provider routing preferences",
        "plugins",
        "tools",
        "function calling",
        "streaming",
        "retries",
        "fallback routing",
        "OAuth",
        "provider-side tool settings",
        "raw request bodies",
        "raw provider responses",
        "provider response ids",
        "auth material",
    ):
        assert forbidden in compact_runtime


def test_backlog_records_done_completion_and_provider_priority_order():
    backlog = read_repo_file("docs/backlog.md")
    done = backlog[: backlog.index("## Next Slice")]
    next_slice = backlog[backlog.index("## Next Slice") : backlog.index("## Near Term")]
    near_term = backlog[backlog.index("## Near Term") : backlog.index("## Deferred")]
    compact_done = collapse_whitespace(done)
    compact_next_slice = collapse_whitespace(next_slice)
    compact_near_term = collapse_whitespace(near_term)

    assert "Native approval and sandbox enforcement baseline" in done
    assert "Native inert read-only tool request value objects" in done
    assert "Native explicit file excerpt read-only tool implementation" in done
    assert "OpenAI subscription-backed native auth decision" in compact_done
    assert "`blocked-for-now` on 2026-05-07" in compact_done
    assert "unsupported credential scraping" in compact_done
    assert "CLI/product wrapping are rejected" in compact_done
    assert "Native OpenRouter Chat Completions provider" in compact_done
    assert "`--native-provider openrouter --native-model <provider/model>`" in compact_done
    assert "`OPENROUTER_API_KEY`" in compact_done
    assert (
        "### Add bounded post-tool provider turn against synthetic sanitized observations"
        in next_slice
    )
    assert "synthetic sanitized observation fixtures" in compact_next_slice
    assert "OpenRouter support with explicit model selection" in compact_near_term
    assert "bounded post-tool provider turn against synthetic sanitized" in compact_near_term
    assert_terms_in_order(
        near_term,
        [
            "bounded post-tool provider turn against synthetic sanitized",
            "bounded read-only tool observation",
        ],
    )
    assert "### Approval And Sandbox Enforcement Baseline" not in next_slice
    assert "### Decide OpenAI subscription-backed native auth path" not in next_slice
    assert "### Add OpenRouter provider support with explicit model selection" not in next_slice


def test_approval_and_sandbox_baseline_is_not_threaded_into_native_runtime():
    forbidden_runtime_terms = {
        "approval_prompt",
        "ApprovalPrompt",
        "approval_ui",
        "ApprovalUi",
        "approval.requested",
        "approval.resolved",
        "native.approval",
        "sandbox_enforcer",
        "SandboxEnforcer",
        "enforce_sandbox",
        "sandbox_check",
    }
    native_sources = sorted((ROOT / "src/pipy_harness/native").glob("*.py"))

    assert native_sources
    for source_path in native_sources:
        source = source_path.read_text(encoding="utf-8")
        for term in forbidden_runtime_terms:
            assert term not in source, f"{term!r} found in {source_path}"
