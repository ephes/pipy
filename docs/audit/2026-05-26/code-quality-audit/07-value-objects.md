# Audit: Value Objects + Conversation + State

Scope:
- `src/pipy_harness/native/models.py` (798 lines)
- `src/pipy_harness/native/conversation.py` (527 lines)
- `src/pipy_harness/native/repl_state.py` (542 lines)
- `src/pipy_harness/native/usage.py` (44 lines)
- `src/pipy_harness/native/dynamic_provider.py` (140 lines)
- `src/pipy_harness/native/retry.py` (126 lines)
- `src/pipy_harness/native/image_attachment.py` (196 lines)
- `src/pipy_harness/native/approval_prompt.py` (410 lines)
- `src/pipy_harness/native/session_resume.py` (274 lines)
- `src/pipy_harness/native/session_compaction.py` (210 lines)
- `src/pipy_harness/native/session_branching.py` (157 lines)

Comparison:
- `packages/ai/src/types.ts` (pi-mono)
- `packages/agent/src/harness/types.ts` (pi-mono)
- `packages/coding-agent/src/core/auth-storage.ts` (pi-mono)
- `packages/{agent,coding-agent}/src/.../compaction/*.ts` (pi-mono)

## Summary

The value objects defend their invariants almost exclusively at *construction time* via `__post_init__` runtime checks, and almost every "closed" choice is in fact a `StrEnum` that re-validates its discriminator. That style protects shapes once they exist, but it leaks across the whole module as defensive runtime guards rather than impossible-to-construct types: `ProviderResult` and `NativeToolResult` can carry `status=SUCCEEDED` together with a populated `error_message`; `NativeToolSandboxPolicy` lets you build `mode=NO_WORKSPACE_ACCESS, workspace_read_allowed=True`; `NativeRunInput.system_prompt_id` and `system_prompt_version` accept any string while exactly one value is wired in production. The auxiliary helpers fall into two camps: `retry.py`, `usage.py`, and `conversation.py` are tight and mostly correct, while `approval_prompt.py` (410 lines), `dynamic_provider.py`, `session_compaction.py`, `session_branching.py`, and `image_attachment.py` together are roughly **1100 lines of code with zero production call sites outside their own tests** and one CLI-only entry point for resume. That is article principle #5 ("aspiration backed by tests, no caller") at scale. Pi-mono solves the same surface with discriminated unions (`AuthCredential = ApiKey | OAuth`, `StopReason` literal, `FileErrorCode` literal), a single `Result<T, E>` envelope, and stable error-code enums; pipy chooses runtime validation, string-typed boundaries, and many almost-identical metadata-only "future" objects.

## Findings

### F1: `ProviderResult` admits success-with-error and pending-on-completion
- **Where**: `src/pipy_harness/native/models.py:206-220`
- **Symptom**: `status: HarnessStatus` is the full lifecycle enum (`PENDING`, `RUNNING`, `SUCCEEDED`, `FAILED`, `ABORTED`), `final_text` and `error_message` are both optional, and there is no `__post_init__`. Constructing `ProviderResult(status=SUCCEEDED, final_text=None, error_type="ApiError", error_message="429")` or `status=PENDING` on a completed call is legal. `tool_calls` is also allowed on a `FAILED` result.
- **Article principle**: "Make the bad state impossible." A result shape is the prototypical place for a discriminated union.
- **Pi comparison**: `packages/ai/src/types.ts:269` defines `StopReason = "stop" | "length" | "toolUse" | "error" | "aborted"`, and the event stream protocol at `types.ts:347-359` splits terminal events into `{ type: "done"; reason: ... ; message }` and `{ type: "error"; reason: ... ; error }` so `errorMessage` only exists on the error branch.
- **Suggested fix**: Replace with `ProviderResult = ProviderSuccess | ProviderFailure | ProviderAborted` (Python `match`-friendly tagged classes). `ProviderSuccess` carries `final_text: str` (non-optional) and `tool_calls`; `ProviderFailure` carries `error_type: str`, `error_message: str` and no `final_text`. The shared lifecycle enum should not be the result discriminator.
- **Severity**: high

### F2: `NativeToolSandboxPolicy` and `NativeToolApprovalPolicy` accept incoherent (mode, booleans) combinations
- **Where**: `src/pipy_harness/native/models.py:359-371` plus enforcement scattered in `models.py:462-477`, `652-720`, and `753-769`
- **Symptom**: The `NativeToolSandboxPolicy` dataclass is happy with `mode=NO_WORKSPACE_ACCESS, workspace_read_allowed=True, filesystem_mutation_allowed=True`. The five booleans are independent fields with no `__post_init__`. Whenever a higher-level request needs a coherent shape (`NativeReadOnlyToolRequest`, `NativePatchApplyRequest`, `NativeVerificationRequest`), it re-checks each boolean against the mode by hand — three near-identical guard blocks.
- **Article principle**: Defensive validation in three call sites is the prototypical fix-up-at-the-edges that the article warns against. The mode is supposed to *be* the capability bundle.
- **Pi comparison**: pi-mono does not duplicate the capability bundle as five booleans + an enum; sandbox capability decisions are gated by the executor that runs the tool, not by a free-form value object.
- **Suggested fix**: Drop the booleans. Either make `NativeToolSandboxPolicy` just `mode: NativeToolSandboxMode` (with the per-mode capabilities defined as a property/lookup) or make the four sandbox modes their own classes (`NoWorkspaceAccess`, `ReadOnlyWorkspace`, `MutatingWorkspace`, `ShellExecutionWorkspace`) with their booleans fixed at the class. The downstream `__post_init__` re-checks in `models.py` go away.
- **Severity**: high

### F3: `NativeRunInput.system_prompt_id` / `system_prompt_version` are open strings with exactly one production value each
- **Where**: `src/pipy_harness/native/models.py:124-125`; constants `SYSTEM_PROMPT_ID = "pipy-native-bootstrap"`, `SYSTEM_PROMPT_VERSION = "1"` at `src/pipy_harness/native/session.py:105-106`; passthrough only at `src/pipy_harness/adapters/native.py:97-98,197-198`
- **Symptom**: Two `str` fields that always carry the same literal pair, validated nowhere, only ever assigned from module-level constants and only ever copied straight into archive metadata.
- **Article principle**: Article principle #4 — slop aesthetics (redundant fields, plumbed-through "configuration" with no axis of variation).
- **Pi comparison**: pi-mono does not surface a `systemPromptId`/`systemPromptVersion` pair on its run inputs at all; system prompt text is composed and the constants stay where the composition happens.
- **Suggested fix**: Drop both fields from `NativeRunInput`. If they are useful in the archive, emit the constants at the archive event boundary directly. If you want to keep the field, narrow it to `Literal["pipy-native-bootstrap"]` so the type system reflects reality.
- **Severity**: medium

### F4: `NativeToolRequest` is unvalidated and uses `str` for `tool_kind`
- **Where**: `src/pipy_harness/native/models.py:374-383`
- **Symptom**: `tool_name: str`, `tool_kind: str`, `metadata: dict[str, Any] | None`, no `__post_init__`. Every other Native* request in the file has a `__post_init__`; this one is the gap, even though it is the public port shape. `tool_kind` should be a closed label set (`read_only_workspace`, `mutating_workspace`, `verification_command`, ...).
- **Article principle**: "Bad state impossible" — a request that crosses the tool port should not be constructible with `tool_kind=""` or `tool_kind="anything I want"`.
- **Pi comparison**: pi-mono's `Tool` (`packages/ai/src/types.ts:327`) carries a `name` and a `parameters: TSchema`; the *kind* is not part of the type because the dispatcher uses `name`.
- **Suggested fix**: Either drop `tool_kind` (it adds no entropy beyond `tool_name`) or convert it to a `StrEnum` of the actually used kinds. Either way, give the dataclass a `__post_init__` with the same sanity checks as its siblings.
- **Severity**: medium

### F5: `NativeTurnMetadata.archive_payload()` re-asserts storage booleans as `False` literals
- **Where**: `src/pipy_harness/native/conversation.py:225-249` (and storage booleans at `134-160` enforced by `__post_init__` at `193-195`)
- **Symptom**: The dataclass already forbids storage booleans from being `True` (line 193-195 raises). `archive_payload()` then hard-codes them to `False` regardless of the instance value. Either the booleans are redundant fields (since they can only be `False`) or the literal-`False` writeback is a vestige of an older design where the booleans could vary.
- **Article principle**: Article principle #5 — half-finished/duplicated invariant; the type is contradicting itself.
- **Pi comparison**: pi-mono's session entries are discriminated by `entry.type` and do not carry nine "storage" booleans that are tautologically `False`; the absence of payload fields *is* the absence of the data.
- **Suggested fix**: Drop the nine storage booleans from `NativeTurnMetadata`. If the archive event format requires them on the wire, write them at the archive event layer with the constant `False`.
- **Severity**: medium

### F6: 27 frozensets of metadata keys are typed as Python strings, not enums
- **Where**: `src/pipy_harness/native/models.py:28-113`, `src/pipy_harness/native/conversation.py:13-63`
- **Symptom**: Constants like `NATIVE_TOOL_OBSERVATION_STORAGE_KEYS = frozenset({"tool_payloads_stored", "stdout_stored", ...})` redeclare the same field names that already exist as dataclass attributes. `archive_payload()` and the `__post_init__` storage guards iterate these frozensets and call `getattr(self, field_name)`. If a dataclass field is renamed and a frozenset entry isn't, the runtime guard silently stops protecting that field.
- **Article principle**: Plausible-but-wrong abstraction (#2). The validation lookup talks about field names as data but the dataclass declares them as code; they drift.
- **Pi comparison**: pi-mono never enumerates archive field names as strings — the event payload is the dataclass projection.
- **Suggested fix**: Replace each frozenset with `tuple(dataclasses.fields(<Klass>))` filtered by a marker (e.g. a `metadata={"storage_boolean": True}` on the field) or by a `Literal` discriminator, so a rename is a type error rather than a silent guard-skip.
- **Severity**: medium

### F7: `NativeConversationState.MAX_TURNS = 8` is the hard ceiling for an interactive REPL
- **Where**: `src/pipy_harness/native/conversation.py:303,116`, `src/pipy_harness/native/session.py:825,931`
- **Symptom**: `NativeNoToolReplSession.max_turns` defaults to `NativeConversationState.MAX_TURNS` (8). The session's `while conversation_state.turn_count < self.max_turns` loop (line 931) hard-stops after 8 user turns with no rotation, no summary, no resume hook. `NativeTurnIdentity.MAX_TURN_INDEX = 7` reinforces the same number with a `__post_init__` check that *raises* on the 9th turn.
- **Article principle**: Bad-state-impossible done so aggressively that the legitimate state is impossible too. "Bounded" was meant to keep an automated test deterministic, not to make a chat REPL crash on turn 9.
- **Pi comparison**: pi-mono's session loop in `packages/agent/src/harness/agent-harness.ts` doesn't cap conversation length at a constant; compaction is the mechanism (`packages/agent/src/harness/compaction/compaction.ts:101-116`) and the threshold is token-based.
- **Suggested fix**: Decouple the *archive* bound (which can legitimately stay small for the bootstrap session) from the *interactive REPL* bound. Either remove the `MAX_TURN_INDEX` cap entirely for the REPL path or thread `compact_loop_messages()` in once it earns its keep.
- **Severity**: high

### F8: `repl_state.py` repeats the 13-way provider switch four times
- **Where**: `src/pipy_harness/native/repl_state.py:144-252` (`model_options`), `:290-310` (`_resolve_model_reference`), `:312-355` (`_provider_available`), `:357-380` (`_provider_unavailable_message`), `:482-513` (`_provider_available_in_env`)
- **Symptom**: Five almost-identical switch chains over the 13 provider names. Each adds the same boilerplate (`if env.get("X"): return ...`) per provider. A new provider has to be added in all five places plus in `SUPPORTED_NATIVE_PROVIDERS`, `DEFAULT_NATIVE_MODELS`, and `AUTO_DEFAULT_PROVIDER_PRIORITY` — eight call sites guaranteed.
- **Article principle**: Plausible-but-wrong abstraction (#2). The model is "provider name is a string, switch on it"; the real domain object is "credential source spec" (an env-var pair, an OAuth file, an AWS credential pair, ...).
- **Pi comparison**: `packages/coding-agent/src/core/auth-storage.ts:24-35` defines `AuthCredential = ApiKeyCredential | OAuthCredential` as a discriminated union, and `findEnvKeys` / `getEnvApiKey` are registry-driven so a new provider is one registry entry. Pipy mirrors none of this.
- **Suggested fix**: Define a `ProviderDescriptor` dataclass (`name`, `default_model`, `credential_check: Callable[[Env], bool]`, `unavailable_reason: str`) and a single `PROVIDER_REGISTRY: tuple[ProviderDescriptor, ...]`. All five functions collapse to a generator + lookup. `SUPPORTED_NATIVE_PROVIDERS`, `DEFAULT_NATIVE_MODELS`, `AUTO_DEFAULT_PROVIDER_PRIORITY` become projections.
- **Severity**: high

### F9: `DEFAULT_NATIVE_MODELS` hard-codes models that are typos or do not exist
- **Where**: `src/pipy_harness/native/repl_state.py:35-48`
- **Symptom**: `"openai": "gpt-5.5"`, `"openai-codex": "gpt-5.5"`, `"openrouter": "openai/gpt-5.1-codex"`. `gpt-5.5` is not a real OpenAI model name; `gpt-5.1-codex` is similarly speculative. Other entries (`gemini-2.0-flash-exp`) are stale; the live model is `gemini-2.5-...` family.
- **Article principle**: Slop aesthetic (#4) — defaults that look plausible but are not real.
- **Pi comparison**: pi-mono uses a generated `models.generated.ts` (`packages/ai/src/`) so the defaults match what the SDK can route to.
- **Suggested fix**: Either generate the defaults from a known catalog (the same trick pi-mono uses), or anchor them on the most recent provider-documented small-fast model and add a CI lint that calls a smoke list. As-is the table will be wrong on day one for several providers.
- **Severity**: medium

### F10: `dynamic_provider.py` is 140 lines that re-export `select_model`, used only by its own test
- **Where**: `src/pipy_harness/native/dynamic_provider.py:1-141`
- **Symptom**: The module's own docstring says it "exposes a small wrapper around the existing `NativeReplProviderState.select_model` flow so a future `/provider` slash command (and the existing `/model` command) can swap the active `ProviderPort`." `swap_provider`/`swap_model` are not called anywhere outside `tests/test_native_dynamic_provider.py`. The CLI `/model` path still calls `state.select_model` directly. `SwapOutcome` adds a `previous_selection` field that no live caller observes.
- **Article principle**: #5 — half-finished feature behind "future command wiring." The article specifically calls out this pattern.
- **Pi comparison**: pi-mono swaps provider via slash commands that call into the agent session manager directly; there is no parallel `swap_provider` shim.
- **Suggested fix**: Delete the file and its test. When a `/provider` command actually lands, wire it to `select_model` directly. If a `previous → next` outcome is needed, return it from `select_model` itself.
- **Severity**: medium

### F11: `approval_prompt.py` (410 lines) has zero non-test, non-reexport call sites
- **Where**: `src/pipy_harness/native/approval_prompt.py:1-411`; re-export at `src/pipy_harness/native/__init__.py:3-12`; sole consumers `tests/test_native_approval_prompt.py` and `tests/test_native_approval_sandbox_policy.py`. The CLI tests that mention it (`tests/test_harness_native_cli.py:1463`, `:1600`) assert the prompt is **not** shown.
- **Symptom**: 6-state `NativeApprovalPromptStatus` (`PENDING`, `ALLOWED`, `DENIED`, `SKIPPED`, `FAILED`), 10-state `NativeApprovalPromptReason`, 3 value objects (`NativeApprovalSandboxPrompt`, `NativeApprovalSandboxDecision`, `NativeReadOnlyApprovalResolution`), an injected `NativeApprovalPromptResolver` protocol, a stream-based resolver, and a "fail-closed" gate. The REPL's `read`, `ask-file`, etc. explicitly skip the prompt path. No production code calls `resolve_read_only_workspace_approval`.
- **Article principle**: #1 (over-engineered edge-case handling) plus #5 (test-only feature). 410 lines for a UI prompt that the live REPL doesn't render.
- **Pi comparison**: pi-mono's approval flow lives in the coding-agent tool dispatcher and is rendered inline with the tool call; there is no parallel "metadata-only sanitized prompt resolver" abstraction.
- **Suggested fix**: Delete the module and its two tests, or, if you want the approval surface to exist, wire one tool through it end to end and let that pin down the data shape. The current shape is invented from `NativeReadOnlyToolRequest` (also unwired) and will not survive a real approval surface.
- **Severity**: high

### F12: `session_compaction.py` is fully implemented but never invoked
- **Where**: `src/pipy_harness/native/session_compaction.py:101-172`; sole consumer `tests/test_native_session_compaction.py`
- **Symptom**: `compact_loop_messages` is a complete bounded-summary call wrapping a `ProviderPort`, but the tool-loop session (`tool_loop_session.py`) never calls it. The REPL caps at `MAX_TURNS=8` (F7) so the compaction threshold (default 12) cannot fire in the only loop that exists.
- **Article principle**: #5 — feature shipped behind a callable, no wiring.
- **Pi comparison**: pi-mono's `packages/agent/src/harness/compaction/compaction.ts` is invoked inside the agent loop with token-budget-driven thresholds; the wiring is the whole point.
- **Suggested fix**: Either wire `compact_loop_messages` into `NativeToolLoopSession.run` with a token estimate, or move it under `docs/` as design notes and delete the code until the loop has enough headroom to call it.
- **Severity**: medium

### F13: `session_branching.py` is a value object with no recorder integration
- **Where**: `src/pipy_harness/native/session_branching.py:46-157`; sole consumer `tests/test_native_session_branching.py`
- **Symptom**: `SessionBranchReference` mints a 32-hex child id and exposes `archive_metadata()`. The module docstring (line 22-24) admits the recorder does not yet emit `session.branched_from`. So the data shape is invented in isolation. `branch_from` and `fork_from` are literal aliases (line 145-157) "for parity with Pi terminology," doubling the public API.
- **Article principle**: #5 (deferred wiring) and slop aesthetic — two aliases for the same function are a redundancy tell.
- **Pi comparison**: pi-mono's branching is implemented in `packages/coding-agent/src/core/agent-session.ts` with concrete recorder hooks; there is no parallel "value object now, recorder later" split.
- **Suggested fix**: Pick one verb (`branch_from`) and delete `fork_from`. Defer the value object until the recorder learns the event. Keep one short note in the design doc instead of 157 lines of code.
- **Severity**: medium

### F14: `image_attachment.py` declares a vision boundary that no provider consumes
- **Where**: `src/pipy_harness/native/image_attachment.py:46-196`; `ProviderRequest.image_attachments` field at `src/pipy_harness/native/models.py:150`; sole consumer `tests/test_native_image_attachment.py`
- **Symptom**: `ImageAttachment` carries `(absolute_path, workspace_relative_path, sha256, byte_length, mime_type)` and `read_image_attachment_bytes` re-reads bytes at "provider serialization time." No native provider class — including Anthropic, Google, OpenAI Responses — references `image_attachments`. The module docstring acknowledges "Live wiring to a real vision provider is deferred" (line 19-23).
- **Article principle**: #5 — boundary shipped before there is a caller; the data shape will be wrong for the first real consumer.
- **Pi comparison**: pi-mono's `ImageContent` (`packages/ai/src/types.ts:240-244`) is `{type: "image", data: string (base64), mimeType: string}` — the bytes live on the content payload, not on a separate path-shaped value object. Pipy chose a different shape with no consumer to validate it.
- **Suggested fix**: Either wire one provider (e.g. Anthropic) to actually consume `image_attachments` and serialize bytes, or remove the field from `ProviderRequest` and the module. The current state plumbs `image_attachments=()` through every code path for free.
- **Severity**: medium

### F15: `retry.py` docstring claims usage by openai/openrouter providers but only openai-codex calls it
- **Where**: `src/pipy_harness/native/retry.py:1-15`; sole consumer `src/pipy_harness/native/openai_codex_provider.py:28,465,559`
- **Symptom**: The module docstring says "it can be shared by the native OpenAI and OpenRouter providers (and any future JSON-over-HTTP provider that follows the `OpenAIProviderError` / `OpenRouterProviderError` pattern)." `openai_provider.py`, `openrouter_provider.py`, `anthropic_provider.py`, `google_provider.py`, `mistral_provider.py`, `azure_openai_provider.py`, `bedrock_provider.py`, `cloudflare_provider.py` do not import it. Transient-error retry is therefore openai-codex-only; every other provider fails on the first 429/503.
- **Article principle**: #3 (permissive error handling — and the inverse: missing retry where it would apply uniformly) plus #4 (docstring describes a shape that doesn't exist in code).
- **Pi comparison**: pi-mono's `StreamOptions.maxRetries` / `maxRetryDelayMs` (`packages/ai/src/types.ts:128-138`) are observed by every SDK provider through the upstream SDK; the harness does not maintain a parallel retry shim.
- **Suggested fix**: Either wire `retry_with_backoff` through `openai_provider`, `openrouter_provider`, `anthropic_provider`, etc. (their HTTP-status exceptions already satisfy the `RetryableStatusError` protocol), or shrink the docstring to reflect reality and acknowledge that other providers depend on their SDK's retry policy.
- **Severity**: medium

### F16: `NativeVerificationRequest.command_label` is `Enum | str`, allowing 80-char free strings into a "safe label" position
- **Where**: `src/pipy_harness/native/models.py:732,761-766`; downstream allowlist at `src/pipy_harness/native/verification.py:103-108`
- **Symptom**: The field accepts `NativeVerificationCommand | str`, validated only as "non-empty, ≤ 80 chars." The verification *result* on the other hand requires the label to be one of `{"just-check", "unsupported", "unsafe"}`. The asymmetry means a caller can build a perfectly legal `NativeVerificationRequest(command_label="rm -rf /")` and only fail at the result boundary.
- **Article principle**: Bad state impossible — the request is the boundary, not the result. If you accept `"rm -rf /"` at the request, the verification tool's safe-label invariant is downstream defensive coding.
- **Pi comparison**: pi-mono's bash executor uses `Tool.parameters: TSchema` for arguments; the verification tool would have a typed parameter, not a stringly-typed `command_label`.
- **Suggested fix**: Tighten to `command_label: NativeVerificationCommand`. If "unsupported"/"unsafe" outcomes are needed, encode them on the *result* (where they belong) and keep the request closed.
- **Severity**: medium

### F17: `NativeToolRequestIdentity` is bolted to a single (turn=0, request=0) bound
- **Where**: `src/pipy_harness/native/models.py:312-345`
- **Symptom**: `CURRENT_TURN_INDEX = 0`, `CURRENT_REQUEST_POSITION = 0`. `__post_init__` raises unless both are zero. The `request_id` property "formula" `f"native-tool-{request_number:04d}"` is documented as "valid only under the current one-turn/one-request bound. Future multi-turn or multi-request work must replace the identity shape." Three downstream value objects (`NativeReadOnlyToolRequest`, `NativePatchProposal`, `NativePatchApplyRequest`, `NativeVerificationRequest`) call `current_noop()` to assert identity equality.
- **Article principle**: #5 — half-finished identity layer (the formula is admittedly wrong outside `(0,0)`) and #1 (over-engineered scaffolding for one row).
- **Pi comparison**: pi-mono's tool call ids are minted by the provider (`ToolCall.id: string`) at `packages/ai/src/types.ts:246-252` and the harness uses them directly; pipy maintains a parallel pipy-owned id layer that doesn't yet have a multi-turn future.
- **Suggested fix**: Drop the value object until tool calls are actually emitted. If the parallel id is genuinely needed (the `tool_loop_session` already mints `make_tool_request_id()`), make it a freshly generated UUID and have downstream requests carry the id directly; the `current_noop()` factory exists only to make four post-inits pass.
- **Severity**: medium

### F18: `usage.py` silently drops vendor-specific token counters
- **Where**: `src/pipy_harness/native/usage.py:11-44`
- **Symptom**: `NORMALIZED_PROVIDER_USAGE_KEYS = ("input_tokens", "output_tokens", "total_tokens", "cached_tokens", "reasoning_tokens")`. Any provider counter not in this set is silently dropped. Anthropic emits `cache_creation_input_tokens` and `cache_read_input_tokens`; Google emits `prompt_token_count`, `candidates_token_count`, `cache_content_token_count`; Bedrock emits `cacheWriteInputTokens`; pipy normalizes none of these. The "rejected" Booleans and negatives are correct; the missing key map is the slop.
- **Article principle**: Plausible-but-wrong abstraction (#2) and lost data (article principle "permissive error handling" inverted — overly strict allowlist that silently loses data).
- **Pi comparison**: `packages/ai/src/types.ts:254-267` defines a richer `Usage` with `input`, `output`, `cacheRead`, `cacheWrite`, `totalTokens`, `cost.{input,output,cacheRead,cacheWrite,total}`. Each provider adapter maps into this canonical shape rather than dropping cache writes.
- **Suggested fix**: Mirror pi's `Usage` shape (or at least add `cache_read`, `cache_write`). Have each provider translate into the canonical names; then `normalize_provider_usage` is just a type-tightening filter on a shape that already matches.
- **Severity**: medium

### F19: `NativeNoToolReplConversationContext._bounded` drops oldest exchanges silently
- **Where**: `src/pipy_harness/native/conversation.py:451-495`
- **Symptom**: `append_successful_exchange` calls `_bounded(exchanges)` which trims `bounded = exchanges[-max_exchanges:]` and then drops more from the head until the byte budget is met. The dropped turns disappear with no event, no metadata flag, no log. `safe_metadata()` reports the *current* count but no "dropped_exchanges" counter.
- **Article principle**: #1 — silent edge-case handling. If the boundary matters, it should be observable.
- **Pi comparison**: pi-mono's compaction emits a `CompactionEntry` recording exactly which entries were folded (`packages/agent/src/harness/compaction/compaction.ts`). Pipy chooses silent drop.
- **Suggested fix**: Track `dropped_exchanges: int` on the value object (or emit a `no_tool_context_truncated` event from the REPL when the trim happens), so the user has a way to learn that earlier context is gone.
- **Severity**: low

### F20: `ProviderToolCall.arguments_json` defers JSON parsing to every caller
- **Where**: `src/pipy_harness/native/models.py:153-203`; live decode at `src/pipy_harness/native/tool_loop_session.py:879-893`
- **Symptom**: The value object stores the raw string (good) and `__post_init__` caps length (good), but the downstream tool-loop has to `json.loads(...)` and schema-validate inside the loop, then map `JSONDecodeError` to a fake error observation. Each provider adapter that produces a `ProviderToolCall` parses arguments differently (some providers already hand back parsed dicts and the adapter re-serializes them just to satisfy this shape).
- **Article principle**: Plausible-but-wrong abstraction (#2). The string-typed JSON forces a re-parse, and the round-trip can lose precision (large ints, non-string keys would already have failed) — providers' parsed shapes are the source of truth.
- **Pi comparison**: `packages/ai/src/types.ts:246-252` carries `arguments: Record<string, any>` already-parsed, with the parsing happening once at the provider boundary.
- **Suggested fix**: Switch to `arguments: Mapping[str, Any]` (parsed, immutable) or to `arguments: Mapping[str, Any] | None` with a parallel `arguments_json` only for the providers that genuinely need the raw text. Parsing once at the provider boundary is the right place — that's where the wire encoding is known.
- **Severity**: low

### F21: `session_resume.py` accepts the legacy `provider_name` key on payload but the rest of the codebase emits `provider`
- **Where**: `src/pipy_harness/native/session_resume.py:54-60,222`
- **Symptom**: `SAFE_PAYLOAD_KEYS` includes both `"provider"` and `"provider_name"`, and `_scan_lifecycle_metadata` prefers `provider` then falls back to `provider_name`. Comments say the long-form key is "a documented fallback for adapters that prefer the long-form key" — but the native session itself emits `provider` (see `_repl_safe_context`). The fallback is for ghost adapters that don't exist in the repo.
- **Article principle**: #1 (over-engineered edge case) and #3 (permissive — accept-anything design without a real source for the alternate key).
- **Pi comparison**: pi-mono uses a single field name per concept across the codebase.
- **Suggested fix**: Drop `provider_name` from `SAFE_PAYLOAD_KEYS` until a real adapter exists. The comment can stay as guidance for when one shows up.
- **Severity**: low

### F22: `NativePatchApplyRequest` enforces `request_source == "pipy-owned-human-reviewed"` via a free-form string
- **Where**: `src/pipy_harness/native/models.py:629,658-659` (also `:733,759-760` for `NativeVerificationRequest`)
- **Symptom**: `request_source: str = "pipy-owned-human-reviewed"` with `__post_init__` raising if the string deviates. A field whose only legal value is one literal string is a `Literal["pipy-owned-human-reviewed"]` masquerading as a `str`.
- **Article principle**: Stringly-typed labels with runtime validation — exactly the article's example of slop.
- **Pi comparison**: pi-mono uses tagged dataclasses or literal types for single-value discriminators; runtime equality on a magic string is not idiomatic.
- **Suggested fix**: Either type the field as `Literal["pipy-owned-human-reviewed"]`, drop the field entirely (it adds no entropy — every call site passes the default), or model "request origin" as an enum if a second source is ever needed.
- **Severity**: low

### F23: `RetryPolicy.retriable_statuses` defaults to a frozenset, but error responses with no status escape entirely
- **Where**: `src/pipy_harness/native/retry.py:77-87,114-116`
- **Symptom**: `_extract_http_status` returns `None` if the exception has no `metadata` attribute or no `http_status`. `retry_with_backoff` then re-raises immediately. This is correct for non-status errors, but network-layer failures (`ConnectionError`, `TimeoutError`, `ssl.SSLError`, `socket.gaierror`) — which are the most common transient errors — also fall through with no retry. Only providers that wrap them into an `OpenAIProviderError`-shaped exception with a synthetic `http_status` get retried.
- **Article principle**: #3 — permissive at the wrong layer (retries everything with a status, never anything without).
- **Pi comparison**: pi-mono's retries are inside the SDKs which know to retry on network errors.
- **Suggested fix**: Add a tuple of retriable exception classes to `RetryPolicy` (default: `(ConnectionError, TimeoutError)`) and check it before falling through. Document that the helper does not retry SSL handshake errors unless they are wrapped.
- **Severity**: low

### F24: Three near-identical `_validate_safe_label` / `_validate_scope_label` helpers across modules
- **Where**: `src/pipy_harness/native/models.py:790-798`, `src/pipy_harness/native/conversation.py:515-527`, `src/pipy_harness/native/approval_prompt.py:391-410`
- **Symptom**: Three functions named `_validate_safe_label` / `_validate_scope_label` with slightly different bodies (the `approval_prompt` version forbids spaces; the others don't), all private to their modules, all encoding the same intuition about "no path separators, no leading dot, no control chars."
- **Article principle**: Plausible-but-wrong abstraction (#2) and slop aesthetic (#4) — duplicated near-identical validators are a confidence-loss signal.
- **Pi comparison**: pi-mono centralizes path/label sanitization in `packages/coding-agent/src/utils/paths.ts`.
- **Suggested fix**: Move to a single `pipy_harness.native._labels` module with one canonical `validate_safe_label(value, *, allow_spaces: bool = False, max_length: int = 128)` and import it from the three call sites.
- **Severity**: low

## What's good (so the audit isn't all negative)

- `usage.py` correctly rejects bools-as-ints and non-finite floats (#2 lookalike avoided).
- `retry.py`'s `RetryPolicy.__post_init__` validates `max_attempts`, `initial_delay`, `max_delay`, `multiplier`, `jitter` at construction time — exactly the discipline the article advocates.
- `ImageAttachment.archive_metadata()` correctly omits `absolute_path`. The metadata-only contract is enforced *structurally* rather than by developer discipline — even though the whole module is unwired (F14), the data shape itself is sound.
- `NativeConversationState` chains `__post_init__` checks: turns must reference the state's identity, be ordered, and stay below `max_turns`. That is the right place to enforce invariants, and `append_turn` returns a *new* state rather than mutating — `frozen=True, slots=True` is used consistently.
- `ProviderToolCall.__post_init__` (models.py:174-203) is exemplary: it validates type and bounded length up front and fails loudly. The criticism in F20 is about the design choice to keep arguments as a string, not about the validation.

## Summary count

- **High severity**: F1, F2, F7, F8, F11 (five)
- **Medium**: F3, F4, F5, F6, F9, F10, F12, F13, F14, F15, F16, F17, F18 (thirteen)
- **Low**: F19, F20, F21, F22, F23, F24 (six)

Total: 24 findings.
