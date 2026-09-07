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
    policy_section = markdown_section(
        spec, "Native Approval And Sandbox Enforcement Baseline"
    )
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
    assert (
        "`no-workspace-access`, `read-only-workspace`, and `mutating-workspace`"
        in compact_storage
    )
    assert "`workspace_read_allowed`" in compact_storage
    assert "`filesystem_mutation_allowed`" in compact_storage
    assert "`shell_execution_allowed`" in compact_storage
    assert "`network_access_allowed`" in compact_storage
    assert "read-only tools produce provider-visible repo context" in compact_storage
    assert "write tools or patch application" in compact_storage
    assert (
        "verification commands. The current native verification boundary supports only"
        in compact_storage
    )
    assert "`just-check` label mapped internally to `just check`" in compact_storage
    assert "The now-removed proposal-only REPL boundary" in compact_storage
    assert "`/propose-file`, `/apply-proposal`" in compact_storage
    assert "model-driven `write` / `edit` tools" in compact_storage
    assert "`propose_file_repl`" in compact_storage
    assert "metadata-only `native.patch.proposal.recorded` event" in compact_storage
    assert "applied no edits itself" in compact_storage
    assert "never enter provider lifecycle payloads" in compact_storage
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
    decision_section = markdown_section(
        spec, "OpenAI Subscription-Backed Native Auth Decision"
    )
    compact_decision = collapse_whitespace(decision_section)

    assert (
        "Decision: reopened and implemented as a distinct `openai-codex` provider."
        in (compact_decision)
    )
    assert "Decision date: 2026-05-07." in compact_decision
    assert "Decision update: reopened for a distinct `openai-codex` provider" in (
        compact_decision
    )
    assert "/Users/jochen/src/pi-mono" in decision_section
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
    assert (
        "subscription-backed sign-in path for Codex product clients" in compact_decision
    )
    assert (
        "do not document an official, stable, locally usable OAuth or device-code"
        in (compact_decision)
    )
    assert (
        "third-party native application call OpenAI models directly" in compact_decision
    )
    assert (
        "must not turn the existing `--native-provider openai` Responses API provider"
        in (compact_decision)
    )
    assert "OpenAI Platform API-key baseline" in compact_decision
    assert "`OPENAI_API_KEY` plus `--native-model`" in compact_decision
    assert "existing `openai` provider" in compact_decision
    assert "remain the OpenAI Platform API-key provider" in compact_decision

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

    assert "OpenRouter remains implemented and usable for manual smoke tests" in (
        compact_decision
    )
    assert "desired default real-provider direction" in compact_decision
    assert "`openai-codex` provider" in compact_decision
    assert "packages/ai/src/utils/oauth/openai-codex.ts" in compact_decision
    assert "packages/coding-agent/src/core/auth-storage.ts" in compact_decision
    assert "packages/ai/src/providers/openai-codex-responses.ts" in compact_decision
    assert "https://chatgpt.com/backend-api/codex/responses" in compact_decision
    assert "must also continue to reject credential-store scraping" in compact_decision
    assert "Historical provider priority after the original blocked decision" in (
        compact_decision
    )
    assert "first local model integration selected" in compact_decision
    assert "`ds4` DeepSeek V4 Flash provider" in compact_decision
    assert "live large-model smoke" in compact_decision
    assert "tool-loop smoke" in compact_decision
    assert "Anthropic subscription-backed native provider support was not promoted" in (
        compact_decision
    )
    assert (
        "focused tests for OAuth shape, credential storage, refresh" in compact_decision
    )
    assert "manual smoke confirms that live login, refresh, provider calls" in (
        compact_decision
    )


def test_openrouter_provider_baseline_is_documented():
    spec = read_repo_file("docs/harness-spec.md")
    runtime_section = markdown_section(spec, "Native Runtime Bootstrap")
    compact_runtime = collapse_whitespace(runtime_section)

    assert "The second real provider is the OpenRouter Chat Completions provider" in (
        compact_runtime
    )
    assert (
        "--native-provider openrouter --native-model <provider/model>"
        in compact_runtime
    )
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
    assert (
        "The third real provider is the distinct OpenAI Codex subscription provider"
        in (compact_runtime)
    )
    assert "--native-provider openai-codex" in compact_runtime
    assert "pipy auth openai-codex login" in compact_runtime
    assert "${PIPY_AUTH_DIR:-~/.local/state/pipy/auth}/openai-codex.json" in (
        compact_runtime
    )
    assert "https://chatgpt.com/backend-api/codex/responses" in compact_runtime
    assert "`originator: pipy`" in compact_runtime
    assert "`OpenAI-Beta: responses=experimental`" in compact_runtime
    assert "Auth material, authorization URLs, raw request bodies" in compact_runtime


def test_terminal_and_context_design_boundaries_remain_documented():
    spec = read_repo_file("docs/harness-spec.md")
    assert "Native Pi-Like REPL Startup Chrome" in spec
    assert "Pi Parity Direction" in spec
    assert "Native Terminal-Layer Direction Checkpoint" in spec
    assert "Native Prompt-Toolkit Line-Editor Feasibility Boundary" in spec
    assert "Native Prompt-Toolkit Slash-Command Completion Boundary" in spec
    assert "Native Prompt-Toolkit File/Path Completion Boundary" in spec
    assert "Native Prompt-Toolkit Multiline Input Boundary" in spec
    assert "Native Prompt-Toolkit Bottom-Toolbar Status Decision" in spec
    assert "Native Prompt-Toolkit Real-TTY Input Hardening" in spec
    assert "Native Prompt-Toolkit File Reference Completion Boundary" in spec
    assert "Native Explicit Multi-File Context Budget" in spec
    assert "bottom-toolbar decision defers footer behavior" in collapse_whitespace(spec)
    assert "disables prompt-toolkit cursor-position requests" in spec
    assert "Native User-Directed @file Context" in spec
    assert "originally shipped as completion-only" in collapse_whitespace(spec)
    assert "raises the successful explicit file-excerpt budget from one to two" in (
        collapse_whitespace(spec)
    )
    assert (
        "loads bounded UTF-8 excerpts for those files into the next provider request"
        in collapse_whitespace(spec)
    )
    assert "Prompt-toolkit is the best next candidate" in spec
    assert "Recommendation: yes, for local preview/build only." in spec
    assert "minimal Zensical setup with `zensical.toml`, `docs/index.md`" in (
        collapse_whitespace(spec)
    )


def test_implemented_repl_proposal_boundary_is_metadata_only_and_bounded():
    spec = read_repo_file("docs/harness-spec.md")
    proposal_section = markdown_section(
        spec, "Implemented REPL Boundary: Proposal-Only File Context"
    )
    compact_proposal = collapse_whitespace(proposal_section)

    assert "`/propose-file <workspace-relative-path> -- <change-request>`" in (
        compact_proposal
    )
    assert "`/ask-file` already proved one bounded explicit-file-excerpt read" in (
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
        "read-only sandbox policy",
        "explicit-file-excerpt tool",
        "workspace-relative target validation",
        "shared successful-read budget",
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


def test_selected_apply_proposal_repl_boundary_is_documented():
    spec = read_repo_file("docs/harness-spec.md")
    decision_section = markdown_section(
        spec, "Proposal Trial Outcome And Write Boundary Direction"
    )
    compact_decision = collapse_whitespace(decision_section)

    assert "/apply-proposal <workspace-relative-path>" in compact_decision
    assert "first public write-capable REPL boundary" in compact_decision
    assert "same-session `/propose-file" in compact_decision
    assert "exact same normalized workspace-relative path" in compact_decision
    assert "pending in-memory proposal draft" in compact_decision
    assert "`NativePatchApplyRequest`" in compact_decision
    assert "`NativePatchApplyTool`" in compact_decision
    assert "explicit slash command is the human review signal" in compact_decision
    assert "does not add a visible approval popup" in compact_decision
    assert "`native.patch.apply.recorded`" in compact_decision
    assert "Verification is now exposed" in compact_decision
    assert "`/verify just-check`" in compact_decision
    assert "must not run `just check`" in compact_decision
    assert "Review and smoke status" in compact_decision
    assert "propose/apply/verify success" in compact_decision
    assert "failing `just check` path" in compact_decision
    assert (
        "`pipy-session verify`, `list`, `search`, and `inspect` remained compatible"
        in (compact_decision)
    )

    for required_check in (
        "one pending proposal for one file and one operation",
        "request_source=pipy-owned-human-reviewed",
        "`mutating-workspace` sandbox policy",
        "workspace read and filesystem mutation allowed",
        "shell/network access forbidden",
        "expected SHA-256 hashes",
        "provider-selected paths",
        "multi-file plans",
        "multiple operations",
    ):
        assert required_check in compact_decision

    for allowed_metadata in (
        "`tool_request_id`",
        "`turn_index`",
        "status and reason labels",
        "file and operation counts",
        "closed operation labels",
        "approval/sandbox labels",
        "`workspace_mutated`",
        "false storage booleans",
    ):
        assert allowed_metadata in compact_decision

    for forbidden in (
        "raw proposal text",
        "raw patch text",
        "raw diffs",
        "replacement file contents",
        "target paths",
        "raw prompts",
        "model output",
        "provider responses",
        "provider-native payloads",
        "raw provider metadata",
        "raw tool payloads",
        "stdout",
        "stderr",
        "command output",
        "shell commands",
        "auth material",
        "secrets",
        "credentials",
        "API keys",
        "tokens",
        "private keys",
        "sensitive personal data",
    ):
        assert forbidden in compact_decision


def test_first_native_self_bootstrap_trial_outcome_is_documented():
    spec = read_repo_file("docs/harness-spec.md")
    outcome_section = markdown_section(
        spec, "First Native Self-Bootstrap Trial Outcome"
    )
    compact_outcome = collapse_whitespace(outcome_section)

    assert "2026-05-11" in compact_outcome
    assert "`openai-codex/gpt-5.2`" in compact_outcome
    assert "`pyproject.toml`" in compact_outcome
    assert "`/apply-proposal pyproject.toml`" in compact_outcome
    assert "`/verify just-check`" in compact_outcome
    assert "no runtime dependencies are declared" in compact_outcome
    assert "`pipy-session verify` reported `ok`" in compact_outcome
    assert "`native-self-bootstrap-trial`" in compact_outcome
    assert (
        "metadata-only lifecycle, provider, tool, patch-apply, and verification event types"
        in (compact_outcome)
    )
    assert "partial lifecycle metadata only" in compact_outcome

    for deferred in (
        "provider auth changes",
        "token storage changes",
        "provider routing changes",
        "model default changes",
        "arbitrary shell execution",
        "non-allowlisted verification commands",
        "multi-file reads",
        "multiple tool requests",
        "automatic write selection",
        "provider follow-up turns",
        "general model/tool loop",
    ):
        assert deferred in compact_outcome


def test_selected_read_failure_recovery_boundary_is_documented():
    spec = read_repo_file("docs/harness-spec.md")
    boundary_section = markdown_section(
        spec, "Read-Failure Recovery Boundary Direction"
    )
    repl_section = markdown_section(spec, "Native Interactive REPL")
    compact_boundary = collapse_whitespace(boundary_section)
    compact_repl = collapse_whitespace(repl_section)

    assert "bounded read-failure recovery for explicit REPL file commands" in (
        compact_boundary
    )
    assert "`native-self-bootstrap-trial`" in compact_boundary
    assert "metadata-only propose/apply/verify run" in compact_boundary
    assert "secret-looking target failed closed as intended" in compact_boundary
    assert "one-read session limit then blocked a second explicit target" in (
        compact_boundary
    )
    assert "one successful explicit file excerpt budget per REPL session" in (
        compact_boundary
    )
    assert "one narrowly bounded failed or skipped explicit-read attempt budget" in (
        compact_boundary
    )
    assert "`/read`, `/ask-file`, and `/propose-file`" in compact_boundary
    assert "before provider visibility" in compact_boundary
    assert "outside both budgets" in compact_boundary
    assert "existing metadata-only tool lifecycle and observation events" in (
        compact_boundary
    )
    assert "`Read-Failure Recovery Boundary Direction`" in compact_repl
    assert "two successful explicit file excerpts per REPL session" in (compact_repl)
    assert "one bounded failed or skipped read-attempt budget" in compact_repl
    assert "leaves the successful excerpt budget available" in (compact_repl)

    for failed_reason in (
        "unsafe target",
        "ignored/generated target",
        "binary or unreadable file",
        "unsupported encoding",
        "secret-looking content",
        "size or line limit failure",
        "tool-skipped status",
    ):
        assert failed_reason in compact_boundary

    for deferred in (
        "broad context loading",
        "provider-selected filesystem paths",
        "provider-side tools",
        "provider follow-up turns",
        "arbitrary shell execution",
        "non-allowlisted verification commands",
        "automatic write selection",
        "general model/tool loop",
    ):
        assert deferred in compact_boundary

    for forbidden in (
        "raw prompts",
        "excerpts",
        "model output",
        "provider responses",
        "proposal text",
        "patch text",
        "diffs",
        "file contents",
        "command stdout",
        "command stderr",
        "auth material",
        "secrets",
        "credentials",
        "API keys",
        "tokens",
        "private keys",
        "sensitive personal data",
    ):
        assert forbidden in compact_boundary


def test_selected_no_tool_repl_conversation_context_boundary_is_documented():
    spec = read_repo_file("docs/harness-spec.md")
    boundary_section = markdown_section(spec, "No-Tool REPL Conversation Context")
    compact_boundary = collapse_whitespace(boundary_section)

    assert (
        "retired no-tool REPL had bounded in-memory conversation context for ordinary no-tool REPL turns"
        in compact_boundary
    )
    assert "summary-safe archive evidence only" in compact_boundary
    assert "read-failure recovery review and smoke records show a clean closeout" in (
        compact_boundary
    )
    assert "fake-provider REPL smoke with finalized archive verification" in (
        compact_boundary
    )
    assert "ordinary non-command REPL turns" in compact_boundary
    assert "prior successful ordinary no-tool user prompts and provider final text" in (
        compact_boundary
    )
    assert "`NativeNoToolReplConversationContext`" in compact_boundary
    assert "under that REPL's provider-turn limit" in compact_boundary
    assert "4 KiB provider-visible history byte budget" in compact_boundary
    assert (
        "oldest no-tool exchanges were dropped before provider visibility"
        in compact_boundary
    )
    assert "cleared when provider/model selection changed" in compact_boundary
    assert "on login" in compact_boundary
    assert "on logout" in compact_boundary
    assert "after provider failure" in compact_boundary
    assert (
        "`/read`, `/ask-file`, `/propose-file`, `/apply-proposal`, and `/verify just-check`"
        in (compact_boundary)
    )


def test_no_tool_repl_conversation_context_review_and_next_clear_boundary_are_documented():
    spec = read_repo_file("docs/harness-spec.md")
    boundary_section = markdown_section(spec, "No-Tool REPL Conversation Context")
    compact_boundary = collapse_whitespace(boundary_section)
    review_section = markdown_section(
        spec,
        "No-Tool REPL Conversation Context Review And Smoke",
    )
    compact_review = collapse_whitespace(review_section)

    assert (
        "bounded no-tool REPL conversation context implementation was reviewed and smoke-tested before that shell was retired"
        in compact_review
    )
    assert "two-round independent review cycle" in compact_review
    assert "first round reported one warning and three suggestions" in compact_review
    assert "second round reported zero findings" in compact_review
    assert "two ordinary fake-provider REPL turns" in compact_review
    assert "`pipy-session verify`" in compact_review
    assert "`just check` passed" in compact_review
    assert "did not require implementation hardening" in compact_review
    clear_section = markdown_section(spec, "Native Local Clear REPL Command")
    compact_clear = collapse_whitespace(clear_section)
    clear_review_section = markdown_section(spec, "Native Local Clear Review And Smoke")
    compact_clear_review = collapse_whitespace(clear_review_section)

    assert (
        "retired native no-tool shell exposed a local `/clear` command" in compact_clear
    )
    assert "discarded any pending same-session proposal draft" in compact_clear
    assert "Malformed `/clear <text>` remained local" in compact_clear
    assert "did not clear retained no-tool history" in compact_clear
    assert "reset provider/model selection, auth state, read budgets" in (compact_clear)
    assert "verification availability, or provider turn indexes" in compact_clear
    assert "archived metadata only" in compact_clear
    assert "two-round independent review cycle" in compact_clear_review
    assert "two suggestion-level coverage items" in compact_clear_review
    assert "second review reported no findings" in compact_clear_review
    assert "`just check` passed" in compact_clear_review
    assert "fake-provider REPL smoke" in compact_clear_review
    assert (
        "next native work selected by the follow-up decision slice was a local `/status` command"
        in (compact_clear_review)
    )
    assert (
        "implementation was present only in the no-tool shell that was later retired"
        in compact_clear_review
    )
    assert (
        "current product session does not expose `/clear`, `/status`, or `/help`"
        in compact_review
    )
    assert "removed outright with no compatibility aliases" in compact_review
    assert "use `/new`, `/session`, and `/hotkeys`, respectively" in compact_review
    assert "metadata-only" in compact_boundary
    assert "Provider lifecycle events" in compact_boundary
    assert "history exchanges were forwarded" in compact_boundary
    assert "history bytes were forwarded" in compact_boundary
    assert "terminal session event" in compact_boundary
    assert "retained-at-end counters" in compact_boundary
    assert "how many exchanges remained retained" in compact_boundary

    for excluded_history in (
        "file excerpts",
        "`/ask-file` questions",
        "`/propose-file` change requests",
        "visible proposal drafts",
        "raw proposal text",
        "patch text",
        "diffs",
        "verification status or output",
        "command output",
        "provider metadata",
        "tool observations",
        "auth material",
        "local slash-command text",
    ):
        assert excluded_history in compact_boundary

    for deferred in (
        "persistent conversation history",
        "transcript export",
        "structured conversation stdout",
        "conversation archive events",
        "provider auth changes",
        "token storage changes",
        "provider routing changes",
        "model default changes",
        "arbitrary shell execution",
        "non-allowlisted verification commands",
        "multi-file reads",
        "second successful read/context handoff",
        "provider-selected filesystem paths",
        "automatic write selection",
        "provider-side tools",
        "general model/tool loop",
    ):
        assert deferred in compact_boundary

    for forbidden_archive in (
        "raw prompts",
        "provider final text",
        "model output",
        "provider responses",
        "provider-native payloads",
        "excerpts",
        "proposal text",
        "patch text",
        "diffs",
        "file contents",
        "command stdout",
        "command stderr",
        "auth material",
        "secrets",
        "credentials",
        "API keys",
        "tokens",
        "private keys",
        "sensitive personal data",
    ):
        assert forbidden_archive in compact_boundary


def test_selected_local_status_repl_boundary_is_documented():
    spec = read_repo_file("docs/harness-spec.md")
    status_section = markdown_section(spec, "Native Local Status REPL Command")
    compact_status = collapse_whitespace(status_section)

    assert (
        "retired native no-tool shell exposed a local `/status` command"
        in compact_status
    )
    assert "summary-safe archive evidence only" in compact_status
    assert "clean second review" in compact_status
    assert "later closeout audit also found no new issues" in compact_status
    assert "/status" in compact_status
    assert "listed by the then-supported `/help`" in compact_status
    assert "static supported-command usage diagnostics" in compact_status
    assert "safe state labels and counters to stderr" in compact_status
    assert "consume provider turns or explicit-read budgets" in compact_status
    assert (
        "change provider/model selection, auth state, or verification availability"
        in compact_status
    )
    assert "other similarly safe booleans" not in compact_status

    for allowed_status in (
        "provider/model selection labels",
        "provider turn count and limit",
        "retained no-tool history counts and byte counts",
        "explicit-read budget booleans",
        "pending proposal availability",
        "verification availability",
    ):
        assert allowed_status in compact_status

    for forbidden_effect in (
        "invoke providers",
        "tools",
        "reads",
        "writes",
        "patch apply",
        "verification commands",
        "shell commands",
        "network access",
        "provider-visible context handoff",
        "provider-side tools",
        "another provider turn",
        "mutate retained conversation context",
        "clear pending proposals",
        "emit archive events",
        "store raw command text",
    ):
        assert forbidden_effect in compact_status

    for forbidden_content in (
        "raw prompts",
        "provider final text",
        "model output",
        "provider responses",
        "provider-native payloads",
        "excerpts",
        "proposal text",
        "patch text",
        "diffs",
        "file contents",
        "command stdout",
        "command stderr",
        "shell commands",
        "auth material",
        "authorization URLs",
        "secrets",
        "credentials",
        "API keys",
        "tokens",
        "private keys",
        "sensitive personal data",
    ):
        assert forbidden_content in compact_status


def test_visible_prompt_foundation_is_not_threaded_into_runtime_paths():
    session_source = (ROOT / "src/pipy_harness/native/session.py").read_text(
        encoding="utf-8"
    )
    assert "class NativeHarnessCompatibilityRuntime" in session_source
    assert "NativeToolApprovalMode.REQUIRED" in session_source
    assert "resolve_read_only_workspace_approval" not in session_source
    assert "NativeInteractiveApprovalPromptResolver" not in session_source

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

    for source_path in (
        ROOT / "src/pipy_harness/adapters/native.py",
        ROOT / "src/pipy_harness/cli.py",
    ):
        source = source_path.read_text(encoding="utf-8")
        assert "resolve_read_only_workspace_approval" not in source
        assert "NativeInteractiveApprovalPromptResolver" not in source
