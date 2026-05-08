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
    assert "visible approval prompt foundation" in compact_policy
    assert "sandbox enforcement" in compact_policy
    assert "broad interactive tools" in compact_policy
    assert "provider-visible context" in compact_policy
    assert "sanitized fixtures" in compact_policy
    assert "one follow-up provider turn" in compact_policy

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

    assert "Approval and sandbox records must stay metadata-only." in compact_storage
    assert "first native visible approval prompt foundation" in compact_storage
    assert "does not add a JSONL event" in compact_storage
    assert "`pending`, `allowed`, `denied`, `skipped`, and `failed`" in compact_storage
    assert "`no-workspace-access`, `read-only-workspace`, and `mutating-workspace`" in compact_storage
    assert "`workspace_read_allowed`" in compact_storage
    assert "`filesystem_mutation_allowed`" in compact_storage
    assert "`shell_execution_allowed`" in compact_storage
    assert "`network_access_allowed`" in compact_storage
    assert "read-only tools produce provider-visible repo context" in compact_storage
    assert "write tools or patch application" in compact_storage
    assert "verification commands. The current native verification boundary supports only" in compact_storage
    assert "`just-check` label mapped internally to `just check`" in compact_storage
    assert "The proposal-only REPL boundary is available" in compact_storage
    assert "`/propose-file <workspace-relative-path> -- <change-request>`" in compact_storage
    assert "`propose_file_repl`" in compact_storage
    assert "metadata-only `native.patch.proposal.recorded` event" in compact_storage
    assert "native.tool.observation.recorded native.provider.started # label propose_file_repl" in (
        compact_storage
    )
    assert "must not apply edits" in compact_storage
    assert "must not be copied into provider lifecycle payloads" in compact_storage
    assert "approval required/resolved booleans" in compact_storage
    assert "`tool_request_id`" in compact_storage
    assert "`turn_index`" in compact_storage
    assert "`duration_seconds`" in compact_storage
    assert "exit codes" in compact_storage
    assert "safe command labels" in compact_storage
    assert "full file contents" in compact_storage
    assert "shell commands" in compact_storage
    assert "command output" in compact_storage
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
    deferred = backlog[backlog.index("## Deferred") :]
    compact_done = collapse_whitespace(done)
    compact_next_slice = collapse_whitespace(next_slice)
    compact_near_term = collapse_whitespace(near_term)
    compact_deferred = collapse_whitespace(deferred)

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
    assert "Native bounded post-tool provider turn against synthetic sanitized observations" in compact_done
    assert "Native bounded read-only tool observation into follow-up provider turn" in compact_done
    assert "Native patch proposal boundary before writes" in compact_done
    assert "Native supervised patch apply boundary" in compact_done
    assert "NativePatchApplyRequest" in compact_done
    assert "native.patch.apply.recorded" in compact_done
    assert "Native allowlisted verification-command boundary" in compact_done
    assert "NativeVerificationRequest" in compact_done
    assert "native.verification.recorded" in compact_done
    assert "First supervised self-bootstrap trial implementation" in compact_done
    assert "test-only trial" in compact_done
    assert "First supervised self-bootstrap review" in compact_done
    assert "Product-direction checkpoint after first native smoke test" in compact_done
    assert "Pi-like native shell" in compact_done
    assert "Native conversation state and bounded provider-turn loop foundation" in compact_done
    assert "pipy_harness.native.conversation" in compact_done
    assert "metadata-only per-turn payloads" in compact_done
    assert "Native one-shot run rebased on conversation state" in compact_done
    assert "provider turn indexes and labels" in compact_done
    assert "per-run in-memory native conversation identity/state" in compact_done
    assert "Native minimal no-tool REPL" in compact_done
    assert "`pipy repl --agent pipy-native`" in compact_done
    assert "`no_tool_repl`" in compact_done
    assert "Native visible approval and sandbox prompt foundation" in compact_done
    assert "stream-based approval resolver" in compact_done
    assert "attempted capability escalation" in compact_done
    assert "Native interactive read-only REPL command behind the prompt gate" in compact_done
    assert "`/read <workspace-relative-path>`" in compact_done
    assert "records only metadata-only tool lifecycle events" in compact_done
    assert "Native explicit provider-visible `/ask-file` REPL boundary" in compact_done
    assert "`/ask-file <workspace-relative-path> -- <question>`" in compact_done
    assert "labeled `ask_file_repl`" in compact_done
    assert "Native `/ask-file` smoke and separator hardening" in compact_done
    assert "whitespace-delimited `--` separator" in compact_done
    assert "OpenRouter smoke was skipped" in compact_done
    assert "Native REPL command help and usage diagnostics" in compact_done
    assert "local `/help` command" in compact_done
    assert "unsupported slash commands" in compact_done
    assert "Native REPL command help and usage diagnostics review" in compact_done
    assert "second review reported no findings" in compact_done
    assert "All four were accepted and fixed" in compact_done
    assert "Native REPL next-boundary decision" in compact_done
    assert "selected a proposal-only" in compact_done
    assert "`/propose-file <workspace-relative-path> -- <change-request>`" in compact_done
    assert "No runtime behavior" in compact_done
    assert "Native proposal-only `/propose-file` REPL boundary" in compact_done
    assert "now accepts `/propose-file <workspace-relative-path> -- <change-request>`" in (
        compact_done
    )
    assert "labeled `propose_file_repl`" in compact_done
    assert "Native proposal-only `/propose-file` review and smoke" in compact_done
    assert "fake-provider terminal smoke" in compact_done
    assert "No implementation hardening was required" in compact_done
    assert "Native REPL next-boundary decision after proposal-only review" in compact_done
    assert "selected a human-applied proposal trial" in compact_done
    assert "public REPL stays proposal-only" in compact_done
    assert "### Native human-applied `/propose-file` trial" in next_slice
    assert "exercise the reviewed proposal-only REPL path as a real workflow" in (
        compact_next_slice
    )
    assert "let a human apply or translate the suggested change outside the REPL" in (
        compact_next_slice
    )
    assert "`propose_file_repl`" in compact_next_slice
    assert "OpenRouter smoke when `OPENROUTER_API_KEY` is available" in (
        compact_next_slice
    )
    assert "fake-provider terminal smoke" in compact_next_slice
    assert "record a summary-safe evaluation" in compact_next_slice
    assert "applying, writing, creating, deleting, renaming, or editing files from the public REPL" in (
        compact_next_slice
    )
    assert "adding `/apply`, `/apply-file`, `/verify`" in compact_next_slice
    assert "multiple tool requests" in compact_next_slice
    assert "provider-side built-in tools" in compact_next_slice
    assert "Pi-like interactive shell" in compact_near_term
    assert "architecture-first" in compact_near_term
    assert "OpenRouter-first" in compact_near_term
    assert "No-tool provider-turn REPL gate: available now" in compact_near_term
    assert "`pipy repl --agent pipy-native`" in compact_near_term
    assert "Visible approval prompt gate: available now" in compact_near_term
    assert "Narrow read-only shell command gate: available now" in compact_near_term
    assert "Provider-visible interactive context gate: available now" in compact_near_term
    assert "`/ask-file <workspace-relative-path> -- <question>`" in compact_near_term
    assert "whitespace-delimited `--` separator" in compact_near_term
    assert "Command help and usage-diagnostic gate: available now" in compact_near_term
    assert "Run a native human-applied `/propose-file` trial" in compact_near_term
    assert "Proposal-only interactive file gate: available now" in compact_near_term
    assert "`/propose-file <workspace-relative-path> -- <change-request>`" in (
        compact_near_term
    )
    assert "labeled `propose_file_repl`" in compact_near_term
    assert "Proposal-only review gate: available now" in compact_near_term
    assert "Human-applied proposal trial gate: selected next" in compact_near_term
    assert "Self-bootstrap readiness gates remain historical context" in compact_near_term
    assert "Full tool-capable native pipy agent runtime" in compact_deferred
    assert "General native model/tool loop beyond bounded provider turns" in compact_deferred
    assert "Interactive TUI" in compact_deferred
    assert "RPC mode" in compact_deferred
    assert "### Run the first supervised self-bootstrap trial" not in next_slice
    assert "### Review the first supervised self-bootstrap trial" not in next_slice
    assert "### Approval And Sandbox Enforcement Baseline" not in next_slice
    assert "### Decide OpenAI subscription-backed native auth path" not in next_slice
    assert "### Add OpenRouter provider support with explicit model selection" not in next_slice
    assert "### Add an allowlisted verification-command slice" not in next_slice
    assert "### Define native conversation state and turn loop" not in next_slice
    assert "### Add a minimal no-tool `pipy-native` REPL over the same core" not in next_slice
    assert "### Choose the next interactive provider-visible context boundary" not in next_slice
    assert "### Review and smoke proposal-only `/propose-file` REPL boundary" not in next_slice


def test_implemented_repl_proposal_boundary_is_metadata_only_and_bounded():
    spec = read_repo_file("docs/harness-spec.md")
    proposal_section = markdown_section(
        spec, "Implemented REPL Boundary: Proposal-Only File Context"
    )
    compact_proposal = collapse_whitespace(proposal_section)

    assert "`/propose-file <workspace-relative-path> -- <change-request>`" in (
        compact_proposal
    )
    assert "`/ask-file` already proved one approved explicit-file-excerpt read" in (
        compact_proposal
    )
    assert "`propose_file_repl`" in compact_proposal
    assert "`pipy_native_patch_proposal`" in compact_proposal
    assert "`native.tool.observation.recorded`" in compact_proposal
    assert "same metadata-only `native.tool.observation.recorded` lifecycle event" in (
        compact_proposal
    )
    assert "`native.patch.proposal.recorded` event" in compact_proposal
    assert "at most one" in compact_proposal
    assert "existing metadata-only proposal payload allowlist" in compact_proposal
    assert "hard-stops" in compact_proposal
    assert "provider result and proposal parse" in compact_proposal

    for required_boundary in (
        "read-only approval/sandbox prompt",
        "explicit-file-excerpt tool",
        "workspace-relative target validation",
        "one-read per-session limit",
    ):
        assert required_boundary in compact_proposal

    for deferred in (
        "workspace mutation",
        "verification",
        "shell execution",
        "broad search",
        "multiple file context",
        "provider-side tools",
        "general model/tool loop",
        "network access",
        "another provider turn",
    ):
        assert deferred in compact_proposal

    for allowed_metadata in (
        "`tool_request_id`",
        "`turn_index`",
        "`status`",
        "`reason_label`",
        "file and operation counts",
        "closed operation labels",
        "false storage booleans",
    ):
        assert allowed_metadata in compact_proposal

    for forbidden in (
        "raw patch text",
        "raw diffs",
        "replacement file contents",
        "model-selected paths",
        "raw provider proposal objects",
        "raw provider metadata",
        "raw prompts",
        "model output",
        "provider responses",
        "provider-native payloads",
        "raw approval prompts",
        "raw tool arguments",
        "raw tool results",
        "stdout",
        "stderr",
        "command output",
        "auth material",
        "secrets",
        "credentials",
        "API keys",
        "tokens",
        "private keys",
        "sensitive personal data",
    ):
        assert forbidden in compact_proposal


def test_visible_prompt_foundation_is_threaded_only_into_the_repl_command_path():
    session_source = (ROOT / "src/pipy_harness/native/session.py").read_text(encoding="utf-8")
    assert "resolve_read_only_workspace_approval" in session_source
    assert "NativeInteractiveApprovalPromptResolver" in session_source
    assert "READ_ONLY_REPL_COMMAND" in session_source

    forbidden_runtime_terms = {
        "approval.requested",
        "approval.resolved",
        '"native.approval',
        "sandbox_enforcer",
        "SandboxEnforcer",
        "enforce_sandbox",
        "sandbox_check",
    }
    runtime_sources = [
        ROOT / "src/pipy_harness/native/session.py",
        ROOT / "src/pipy_harness/adapters/native.py",
        ROOT / "src/pipy_harness/cli.py",
    ]

    for source_path in runtime_sources:
        source = source_path.read_text(encoding="utf-8")
        for term in forbidden_runtime_terms:
            assert term not in source, f"{term!r} found in {source_path}"

    for source_path in (ROOT / "src/pipy_harness/adapters/native.py", ROOT / "src/pipy_harness/cli.py"):
        source = source_path.read_text(encoding="utf-8")
        assert "resolve_read_only_workspace_approval" not in source
        assert "NativeInteractiveApprovalPromptResolver" not in source
