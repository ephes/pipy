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

    assert "Decision: reopened and implemented as a distinct `openai-codex` provider." in (
        compact_decision
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
    assert "subscription-backed sign-in path for Codex product clients" in compact_decision
    assert "do not document an official, stable, locally usable OAuth or device-code" in (
        compact_decision
    )
    assert "third-party native application call OpenAI models directly" in compact_decision
    assert "must not turn the existing `--native-provider openai` Responses API provider" in (
        compact_decision
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
    assert "Local model provider integrations remained deferred pending benchmark work" in (
        compact_decision
    )
    assert "Anthropic subscription-backed native provider support was not promoted" in (
        compact_decision
    )
    assert "focused tests for OAuth shape, credential storage, refresh" in compact_decision
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
    assert "The third real provider is the distinct OpenAI Codex subscription provider" in (
        compact_runtime
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
    assert "OpenAI Codex OAuth provider correction from Pi reference" in compact_done
    assert "distinct `openai-codex` provider path" in compact_done
    assert "packages/ai/src/utils/oauth/openai-codex.ts" in compact_done
    assert "packages/ai/src/providers/openai-codex-responses.ts" in compact_done
    assert "https://chatgpt.com/backend-api/codex/responses" in compact_done
    assert "Pi-like no-approval shell direction correction" in compact_done
    assert "No permission popups" in compact_done
    assert "packages/coding-agent/src/core/tools/read.ts" in compact_done
    assert "Native REPL approval prompt removal" in compact_done
    assert "`not-required` approval policy data" in compact_done
    assert "no longer wired into the normal product REPL path" in compact_done
    assert "Native `openai-codex` OAuth provider from Pi reference" in compact_done
    assert "`--native-provider openai-codex --native-model <model>`" in compact_done
    assert "`pipy auth openai-codex login`" in compact_done
    assert "`${PIPY_AUTH_DIR:-~/.local/state/pipy/auth}/openai-codex.json`" in (
        compact_done
    )
    assert "Native OpenAI Codex provider SSE transport correction" in compact_done
    assert "SSE Responses request with `stream: true`" in compact_done
    assert "`https://chatgpt.com/backend-api/codex/responses`" in compact_done
    assert "Native REPL auth/model commands and late-bound provider selection" in compact_done
    assert "`pipy` now starts the native REPL" in compact_done
    assert "`/login [openai-codex]`, `/logout [openai-codex]`" in compact_done
    assert "model selection is resolved before each provider-visible turn" in compact_done
    assert "Native human-applied `/propose-file` trial through shell auth/model commands" in (
        compact_done
    )
    assert "`/model openai-codex/gpt-5.2`" in compact_done
    assert "secret_looking_content" in compact_done
    assert "useful enough to justify a narrow write-capable boundary design slice" in (
        compact_done
    )
    assert "Native one-file `/apply-proposal` REPL command" in compact_done
    assert "/apply-proposal <workspace-relative-path>" in compact_done
    assert "same-session `/propose-file`" in compact_done
    assert "NativePatchApplyRequest" in compact_done
    assert "native.patch.apply.recorded" in compact_done
    assert "Native REPL `/verify just-check` command" in compact_done
    assert "NativeVerificationRequest" in compact_done
    assert "native.verification.recorded" in compact_done
    assert "Native REPL `/verify just-check` review and smoke" in compact_done
    assert "Fake-provider terminal smoke runs exercised propose/apply/verify success" in (
        compact_done
    )
    assert "`pipy-session verify`, `list`, `search`, and `inspect` remained compatible" in (
        compact_done
    )
    assert "Native first pipy-applied, pipy-verified tiny change" in compact_done
    assert "2026-05-11" in compact_done
    assert "`openai-codex/gpt-5.2`" in compact_done
    assert "`/propose-file pyproject.toml -- <change-request>`" in compact_done
    assert "`/apply-proposal pyproject.toml`" in compact_done
    assert "`/verify just-check`" in compact_done
    assert "`native-self-bootstrap-trial`" in compact_done
    assert "no runtime dependencies are declared" in compact_done
    assert "Native next-boundary decision after the first self-bootstrap trial" in (
        compact_done
    )
    assert "summary-safe inspection of the finalized `native-self-bootstrap-trial`" in (
        compact_done
    )
    assert "The selected next boundary is therefore a failed-read recovery slice" in (
        compact_done
    )
    assert "Native bounded read-failure recovery for explicit REPL file commands" in (
        compact_done
    )
    assert "one failed or skipped read attempt can happen before that successful excerpt" in (
        compact_done
    )
    assert "Archive payloads remain metadata-only and add only safe budget booleans" in (
        compact_done
    )
    assert "Native bounded read-failure recovery review and smoke" in compact_done
    assert "split-budget implementation aligned with the selected contract" in (
        compact_done
    )
    assert "local `/help`, `/model`, `/apply-proposal`, and `/verify just-check`" in (
        compact_done
    )
    assert "fake-provider REPL smoke exercised failed-read recovery" in compact_done
    assert "Native no-tool REPL conversation-context decision after read-failure recovery review" in (
        compact_done
    )
    assert "bounded in-memory context for ordinary no-tool REPL turns" in compact_done
    assert "under explicit turn and byte limits" in compact_done
    assert "file excerpts, proposal drafts, patch text, verification output" in (
        compact_done
    )
    assert "The decision slice changed no runtime behavior" in compact_done
    assert "Native bounded no-tool REPL conversation context" in compact_done
    assert "`NativeNoToolReplConversationContext`" in compact_done
    assert "4 KiB provider-visible byte budget" in compact_done
    assert "clears on login, logout, provider/model changes" in compact_done
    assert "raw prompts, provider final text, excerpts" in compact_done
    assert "Native bounded no-tool REPL conversation context review and smoke" in (
        compact_done
    )
    assert "two-round independent review cycle" in compact_done
    assert "second round reported zero findings" in compact_done
    assert "implementer-side closeout audit" in compact_done
    assert "fake-provider REPL smoke with two ordinary turns" in compact_done
    assert "The next selected native-shell boundary is a local `/clear` command" in (
        compact_done
    )
    assert "Native local `/clear` REPL command" in compact_done
    assert "now accepts `/clear` as a local command" in compact_done
    assert "malformed `/clear <text>` stays local and does not clear history" in (
        compact_done
    )
    assert "does not reset provider/model selection, auth state, read budgets" in compact_done
    assert "Native local `/clear` review and smoke" in compact_done
    assert "two-round independent review cycle" in compact_done
    assert "two suggestion-level test coverage items" in compact_done
    assert "both were accepted and fixed" in compact_done
    assert "post-clear verification availability coverage" in compact_done
    assert "second review found no findings" in compact_done
    assert "fake-provider `/clear` REPL smoke" in compact_done
    assert "Native next-boundary decision after `/clear` review and smoke" in compact_done
    assert "summary-safe archive reflection found the `/clear` implementation review cycle clean" in (
        compact_done
    )
    assert "The selected next boundary is a local `/status` REPL command" in compact_done
    assert "This decision slice changed no runtime behavior" in compact_done
    assert "Native local `/status` REPL command" in compact_done
    assert "now accepts `/status` as a local command" in compact_done
    assert "pending proposal availability, and verification availability" in compact_done
    assert "archive raw command text" in compact_done
    assert "### Native next-boundary decision after `/status`" in next_slice
    assert "decide the next small native shell boundary" in compact_next_slice
    assert "This is a planning slice" in compact_next_slice
    assert "do not implement another native shell feature inside the decision slice" in (
        compact_next_slice
    )
    assert "Pi-like interactive shell" in compact_near_term
    assert "next-boundary decision after the implemented local `/status` command" in (
        compact_near_term
    )
    assert "no permission popups for normal interactive use" in compact_near_term
    assert "OpenAI Codex subscription auth as the preferred near-term real-provider path" in (
        compact_near_term
    )
    assert "OpenRouter remains implemented and useful for immediate manual smoke testing" in (
        compact_near_term
    )
    assert "No-tool provider-turn REPL gate: available now" in compact_near_term
    assert "`pipy repl --agent pipy-native`" in compact_near_term
    assert "Later ordinary no-tool turns now receive bounded in-memory history" in (
        compact_near_term
    )
    assert "Historical visible approval prompt gate" in compact_near_term
    assert "Narrow read-only shell command gate: available now" in compact_near_term
    assert "Provider-visible interactive context gate: available now" in compact_near_term
    assert "`/ask-file <workspace-relative-path> -- <question>`" in compact_near_term
    assert "whitespace-delimited `--` separator" in compact_near_term
    assert "Command help and usage-diagnostic gate: available now" in compact_near_term
    assert "public mutation command is `/apply-proposal <workspace-relative-path>`" in (
        compact_near_term
    )
    assert "Proposal-only interactive file gate: available now" in compact_near_term
    assert "`/propose-file <workspace-relative-path> -- <change-request>`" in (
        compact_near_term
    )
    assert "labeled `propose_file_repl`" in compact_near_term
    assert "Proposal-only review gate: available now" in compact_near_term
    assert (
        "implemented, reviewed, and trialed with a real `openai-codex` provider turn"
        in compact_near_term
    )
    assert "One-file write-boundary decision gate: available now" in compact_near_term
    assert "/apply-proposal <workspace-relative-path>" in compact_near_term
    assert "Allowlisted verification gate: available now" in compact_near_term
    assert "Local conversation clear gate: available now" in compact_near_term
    assert "available now through `/clear`" in compact_near_term
    assert "reviewed and smoked" in compact_near_term
    assert "Next-boundary decision gate after local clear: available now" in compact_near_term
    assert "selected a local `/status` command as the next native-shell boundary" in (
        compact_near_term
    )
    assert "Local status command gate: available now through `/status`" in compact_near_term
    assert "retained no-tool history counts and byte counts" in compact_near_term
    assert "explicit-read budget booleans" in compact_near_term
    assert "pending proposal availability, and verification availability" in (
        compact_near_term
    )
    assert "Read-failure recovery review gate: available now" in compact_near_term
    assert "removed from the normal product REPL path" in compact_near_term
    assert "Self-bootstrap readiness gates remain historical context" in compact_near_term
    assert "Full tool-capable native pipy agent runtime" in compact_deferred
    assert "General native model/tool loop beyond bounded provider turns" in compact_deferred
    assert "Generic OpenAI subscription-backed native provider auth beyond the distinct" in (
        compact_deferred
    )
    assert "additional OAuth providers" in compact_deferred
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
    assert "### Choose the next native shell boundary after the self-bootstrap trial" not in (
        next_slice
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
    decision_section = markdown_section(spec, "Proposal Trial Outcome And Write Boundary Direction")
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
    assert "`pipy-session verify`, `list`, `search`, and `inspect` remained compatible" in (
        compact_decision
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
    outcome_section = markdown_section(spec, "First Native Self-Bootstrap Trial Outcome")
    compact_outcome = collapse_whitespace(outcome_section)

    assert "2026-05-11" in compact_outcome
    assert "`openai-codex/gpt-5.2`" in compact_outcome
    assert "`pyproject.toml`" in compact_outcome
    assert "`/apply-proposal pyproject.toml`" in compact_outcome
    assert "`/verify just-check`" in compact_outcome
    assert "no runtime dependencies are declared" in compact_outcome
    assert "`pipy-session verify` reported `ok`" in compact_outcome
    assert "`native-self-bootstrap-trial`" in compact_outcome
    assert "metadata-only lifecycle, provider, tool, patch-apply, and verification event types" in (
        compact_outcome
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
    boundary_section = markdown_section(spec, "Read-Failure Recovery Boundary Direction")
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
    assert "one successful explicit file excerpt budget per REPL session" in (
        compact_repl
    )
    assert "one bounded failed or skipped read-attempt budget" in compact_repl
    assert "leaves the one successful excerpt budget available" in (
        compact_repl
    )

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
        "multi-file context",
        "second successful read",
        "broad search",
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

    assert "now has bounded in-memory conversation context for ordinary no-tool REPL turns" in (
        compact_boundary
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
    assert "existing REPL provider-turn limit" in compact_boundary
    assert "4 KiB provider-visible history byte budget" in compact_boundary
    assert "oldest no-tool exchanges are dropped before provider visibility" in compact_boundary
    assert "cleared when provider/model selection changes" in compact_boundary
    assert "on login" in compact_boundary
    assert "on logout" in compact_boundary
    assert "after provider failure" in compact_boundary
    assert "`/read`, `/ask-file`, `/propose-file`, `/apply-proposal`, and `/verify just-check`" in (
        compact_boundary
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

    assert "bounded no-tool REPL conversation context implementation is reviewed and smoked" in (
        compact_review
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

    assert "native shell exposes a local `/clear` command" in compact_clear
    assert "discards any pending same-session proposal draft" in compact_clear
    assert "Malformed `/clear <text>` remains local" in compact_clear
    assert "does not clear retained no-tool history" in compact_clear
    assert "does not reset provider/model selection, auth state, read budgets" in (
        compact_clear
    )
    assert "verification availability, or provider turn indexes" in compact_clear
    assert "archives metadata-only" in compact_clear
    assert "two-round independent review cycle" in compact_clear_review
    assert "two suggestion-level coverage items" in compact_clear_review
    assert "second review reported no findings" in compact_clear_review
    assert "`just check` passed" in compact_clear_review
    assert "fake-provider REPL smoke" in compact_clear_review
    assert "next native work selected by the follow-up decision slice was a local `/status` command" in (
        compact_clear_review
    )
    assert "implementation is now present" in compact_clear_review
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

    assert "native shell exposes a local `/status` command" in compact_status
    assert "summary-safe archive evidence only" in compact_status
    assert "clean second review" in compact_status
    assert "later closeout audit also found no new issues" in compact_status
    assert "/status" in compact_status
    assert "listed by `/help`" in compact_status
    assert "static supported-command usage diagnostics" in compact_status
    assert "safe state labels and counters to stderr" in compact_status
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
        "consume provider turns",
        "consume explicit-read budgets",
        "mutate retained conversation context",
        "clear pending proposals",
        "change provider/model selection",
        "change auth state",
        "change verification availability",
        "emits no archive events",
        "stores no raw command text",
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
    session_source = (ROOT / "src/pipy_harness/native/session.py").read_text(encoding="utf-8")
    assert "READ_ONLY_REPL_COMMAND" in session_source
    assert "NativeToolApprovalMode.NOT_REQUIRED" in session_source
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

    for source_path in (ROOT / "src/pipy_harness/adapters/native.py", ROOT / "src/pipy_harness/cli.py"):
        source = source_path.read_text(encoding="utf-8")
        assert "resolve_read_only_workspace_approval" not in source
        assert "NativeInteractiveApprovalPromptResolver" not in source
