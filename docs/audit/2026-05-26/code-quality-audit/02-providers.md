# Audit: Provider Adapters

Scope:
- `src/pipy_harness/native/provider.py`
- `src/pipy_harness/native/fake.py`
- `src/pipy_harness/native/openai_provider.py`
- `src/pipy_harness/native/openai_completions_provider.py`
- `src/pipy_harness/native/openai_codex_provider.py`
- `src/pipy_harness/native/openrouter_provider.py`
- `src/pipy_harness/native/anthropic_provider.py`
- `src/pipy_harness/native/google_provider.py`
- `src/pipy_harness/native/google_vertex_provider.py`
- `src/pipy_harness/native/mistral_provider.py`
- `src/pipy_harness/native/bedrock_provider.py`
- `src/pipy_harness/native/azure_openai_provider.py`
- `src/pipy_harness/native/cloudflare_provider.py`
- `src/pipy_harness/native/retry.py`
- `src/pipy_harness/native/usage.py`
- `src/pipy_harness/native/dynamic_provider.py`

Comparison: `packages/ai/src/providers/{openai-completions,openai-responses-shared,openai-codex-responses,openai-responses,anthropic,google,google-vertex,mistral,amazon-bedrock,azure-openai-responses,cloudflare}.ts`, `packages/ai/src/models.ts`, `packages/ai/src/models.generated.ts` in `/Users/jochen/src/pi-mono`.

## Summary

The 12 provider adapters total ~7,000 lines but encode roughly four wire shapes (OpenAI Responses, OpenAI Chat Completions, Anthropic Messages, Gemini `generateContent`). Every adapter independently redeclares the same `JsonResponse`/`JsonHTTPClient`/`UrllibJsonHTTPClient` boundary, the same four-class exception hierarchy, the same `_decode_json_object`/`_failed_result`/`_utc_now`/`_safe_response_label` helpers, the same envelope-to-wire serializer, and a near-identical tool-call extractor — pi-mono consolidates the equivalent logic in `openai-responses-shared.ts` (551L) and shares it across three Responses-family providers, while pipy's chat-completions family (OpenRouter / OpenAI Completions / Mistral / Cloudflare) has no shared module at all. Several of these copies have already diverged: Google and Google-Vertex synthesize tool-call correlation IDs from the loop index and silently throw away the real id; Anthropic-shape providers hardcode `max_tokens=4096` even though pi-mono drives it from per-model `model.maxTokens`; the Codex provider buries OAuth, an HTTP callback server, SSE parsing, and tool-call assembly in one 1,260L file even though pi-mono's equivalent reuses `convertResponsesMessages`/`convertResponsesTools` and only owns Codex-specific transport. Streaming and reasoning sinks are accepted everywhere via the `ProviderPort` protocol but immediately discarded with `del stream_sink, reasoning_sink` in 10 of 11 real adapters — only `openai-codex` actually wires them. `retry.py` is well-designed and isolated but is invoked from exactly one call site, so its policy-driven behavior is not exercised by the other 10 providers that are equally subject to 429/5xx. The default model registry in `repl_state.py` lists `gpt-5.5` for both `openai` and `openai-codex`, which is not a real OpenAI model id — a textbook "plausible-but-wrong" default.

## Findings

### F1: Twelve copies of the same HTTP client boundary
- **Where**: `JsonResponse`, `JsonHTTPClient`, `UrllibJsonHTTPClient`, and the surrounding `urllib.request.urlopen` block are redeclared verbatim in `src/pipy_harness/native/openai_provider.py:32-83`, `openai_completions_provider.py:41-98`, `openrouter_provider.py:35-86`, `anthropic_provider.py:35-92`, `mistral_provider.py:35-83`, `google_provider.py:36-86`, `google_vertex_provider.py:58-115`, `azure_openai_provider.py:41-99`, `cloudflare_provider.py:36-88`, and `bedrock_provider.py:48-105`. The `openai_codex_provider.py:90-188` variant follows the same pattern but for SSE. Each copy only differs by which `*HTTPStatusError`/`*TransportError` exception class it raises.
- **Symptom**: ~50 LOC × 11 providers ≈ 550 LOC of mechanical duplication. Any fix (proxy support, custom headers, timeout policy, retries, certificate pinning) has to be made 11 times.
- **Article principle**: "AI slop aesthetics: redundant abstractions … Volume = noise."
- **Pi comparison**: pi-mono uses a single Node `fetch` call inside provider-specific code and centralizes the auth/header concerns in `openai-responses-shared.ts:90-268` (`convertResponsesMessages`, `convertResponsesTools`) and provider-neutral `utils/event-stream.ts`. There is no `JsonHTTPClient` boundary because none is needed — tests stub `fetch` once.
- **Suggested fix**: Move `JsonResponse`, `JsonHTTPClient`, `UrllibJsonHTTPClient`, `_decode_json_object`, and the `urllib.error.HTTPError`/`URLError` translation into a `native/http_client.py`. Let each provider parameterize the error classes it raises (or, simpler, raise a shared `HTTPStatusError`/`TransportError` and let the provider attach a `provider_name` field).
- **Severity**: high

### F2: Eleven copies of the exception hierarchy and `_failed_result`/`_utc_now`/`_safe_response_label`
- **Where**: Each non-fake real adapter defines four sibling classes (`*ProviderError`, `*HTTPStatusError`, `*TransportError`, `*ResponseParseError`) with the same shape: `openai_provider.py:297-331`, `openai_completions_provider.py:220-261`, `openrouter_provider.py:203-234`, `anthropic_provider.py:283-317`, `mistral_provider.py:200-231`, `google_provider.py:215-252`, `google_vertex_provider.py:288-329`, `azure_openai_provider.py:342-385`, `cloudflare_provider.py:224-258`, `bedrock_provider.py:350-389`, and `openai_codex_provider.py:719-761` (adds `OpenAICodexAuthError`/`OpenAICodexOAuthError`). The `_failed_result` builder (`openai_provider.py:459-478` and 10 near-clones) and `_utc_now`/`_safe_response_label` helpers are likewise copy-pasted.
- **Symptom**: Provider error metadata cannot be inspected polymorphically — `tool_loop_session.py` catches each class by import. Sanitization rules drift: `openai_provider.py:319-322` records `api_error_type` raw (no `sanitize_text`), while `openai_completions_provider.py:247` and `openai_codex_provider.py:750` apply `sanitize_text`. The bug surface for "fix all providers to sanitize the same way" is 10×.
- **Article principle**: "Make bad states impossible" / "AI over-engineers: handles every malformed edge case instead of preventing them."
- **Pi comparison**: pi-mono throws plain `Error` or one provider-local `CodexApiError`/`CodexProtocolError` (`openai-codex-responses.ts:488-510`) and lets the caller in `streamX` translate. Sanitization is a single helper (`utils/sanitize-unicode.ts`).
- **Suggested fix**: One `ProviderError` base in `provider.py` with subclasses `ProviderHTTPStatusError`, `ProviderTransportError`, `ProviderResponseParseError`, `ProviderAuthError`. Adapters pass `provider_name` and `metadata`; `_failed_result` becomes a single helper in `provider.py`.
- **Severity**: high

### F3: Three copies of the OpenAI Chat-Completions wire serialization
- **Where**: `openai_completions_provider.py:264-460` (full parse + envelope-to-message + tool extract + usage), `openrouter_provider.py:237-426` (same), `mistral_provider.py:234-444` (same), `cloudflare_provider.py:261-454` (same). Every line of `_chat_messages`, `_envelope_to_chat_message`, `_serialize_tool_for_openai`, `_extract_tool_calls`, `_extract_text_content`, and `_parse_response` is functionally identical — only error class names differ.
- **Symptom**: ~190 × 4 = ~760 LOC of duplicated parsing for what pi-mono treats as one provider with different `baseUrl`/headers.
- **Article principle**: "Volume = noise: large surface area with low signal-to-noise makes maintenance worse."
- **Pi comparison**: pi-mono routes all OpenAI Chat-Completions providers (OpenAI, OpenRouter, Mistral, Cloudflare Workers AI, Cloudflare AI Gateway, Vercel AI Gateway, z.ai, …) through `providers/openai-completions.ts` and selects per-provider quirks with `model.baseUrl`/`model.compat` flags (`openai-completions.ts:1066-1100`). Cloudflare's adapter is 36 lines (`providers/cloudflare.ts:1-36`).
- **Suggested fix**: Collapse OpenRouter/Mistral/OpenAI-Completions/Cloudflare to one `OpenAICompatibleChatProvider` class that takes `name`, `endpoint`, `header_factory(api_key)`, `usage_field_map`. The four files become ~80 LOC each (config + factory).
- **Severity**: high

### F4: Two copies of the Anthropic Messages wire serialization (Anthropic + Bedrock)
- **Where**: `anthropic_provider.py:200-431` and `bedrock_provider.py:268-503` carry essentially identical `_messages_payload`, `_envelope_to_message`, `_serialize_tool_for_anthropic` (named `_serialize_tool_for_bedrock`), `_extract_final_text`, `_extract_tool_calls`, and `_extract_usage`. Bedrock then adds 184 lines of pure-stdlib SigV4 signing (`bedrock_provider.py:549-727`) which is the only thing it should own.
- **Symptom**: Two places to fix any Anthropic shape change. `bedrock_provider.py:35` even adds `"anthropic_version": "bedrock-2023-05-31"` to the body — pi-mono knows this is a Bedrock wrapper around the Anthropic shape and doesn't fork the parser.
- **Article principle**: "Make bad states impossible" — the two parsers can disagree silently.
- **Pi comparison**: pi-mono's `providers/amazon-bedrock.ts:1-953` imports from `providers/anthropic.ts` and only owns the SigV4 transport / Bedrock invoke envelope.
- **Suggested fix**: Move all Anthropic Messages serialization/parsing to an `anthropic_shared.py`. `bedrock_provider.py` keeps only `_sigv4_sign`, endpoint construction, and the `anthropic_version` body field.
- **Severity**: high

### F5: Google/Vertex tool-call IDs are fabricated from the loop index, dropping real ids
- **Where**: `google_provider.py:430-467` (`correlation = f"google-tool-{index}"`) and `google_vertex_provider.py:508-545` (`correlation = f"google-vertex-tool-{index}"`).
- **Symptom**: When Gemini ever emits a `functionCall` with a stable id (or when multiple sibling calls need to be matched to tool results), pipy throws that information away. A tool-result message carrying the original id will not match `google-tool-0`/`google-vertex-tool-0`. Pi-mono's reverse path (`google_vertex_provider.py:429-440` `_lookup_tool_name`) confirms the issue: pipy already has to scan prior `AssistantMessage` envelopes to reverse-look-up tool names because the synthetic id loses information.
- **Article principle**: "Plausible-but-wrong: code reads correctly but has subtle bugs or wrong models of the problem."
- **Pi comparison**: pi-mono's `google.ts`/`google-shared.ts` treats the provider's id as authoritative and only synthesizes one when truly absent.
- **Suggested fix**: Check `function_call.get("id")` (Gemini does emit `id` for `functionCall` parts in newer schemas) and use it; only fall back to an index-based id when missing.
- **Severity**: medium

### F6: Streaming and reasoning sinks are part of the protocol but discarded in 10 of 11 adapters
- **Where**: `del stream_sink, reasoning_sink` at `openai_provider.py:116`, `openai_completions_provider.py:132`, `openrouter_provider.py:118`, `anthropic_provider.py:126`, `mistral_provider.py:116`, `google_provider.py:127`, `google_vertex_provider.py:162`, `azure_openai_provider.py:139`, `cloudflare_provider.py:126`, `bedrock_provider.py:146`. Only `openai_codex_provider.py:477-588` actually consumes them. The protocol docstring at `provider.py:46-67` claims providers "ignore the keyword" until streaming lands — but every chat-completions provider's underlying API supports SSE streaming today.
- **Symptom**: Users get streaming output only for one model family. The protocol's promise is technically honored but practically misleading; the docstring (`provider.py:11-21`) makes it sound like a polished opt-in.
- **Article principle**: "AI slop aesthetics: 'for future X' code" — the parameter exists as decoration on 10 implementations that will never use it without a rewrite.
- **Pi comparison**: pi-mono streams every provider (`openai-completions.ts:165-360`, `anthropic.ts`, `google.ts`, etc.). Streaming is the primary path; the buffered variant is derived from it.
- **Suggested fix**: Either implement streaming for the four wire shapes (one streaming impl per shape, reused by all OpenAI-compatible providers / both Anthropic-shape providers / both Gemini-shape providers), or remove the `stream_sink`/`reasoning_sink` parameters from the protocol and accept a `streaming=False` baseline. Promising a capability you don't deliver is worse than not promising.
- **Severity**: high

### F7: `retry.py` exists, is well-tested, and is wired into exactly one provider
- **Where**: `retry.py:89-126` defines `retry_with_backoff(operation, policy=...)`. Only `openai_codex_provider.py:559` calls it. None of `openai`, `openai-completions`, `openrouter`, `anthropic`, `mistral`, `google`, `google-vertex`, `azure-openai`, `cloudflare`, or `bedrock` retries on 429/5xx, even though every one of them can return those statuses transiently.
- **Symptom**: A 503 from Anthropic or a 429 from Mistral fails the whole turn with no retry, while the same kind of failure on Codex retries 4×. The retry policy is policy-as-data but it's only enforced in one place.
- **Article principle**: "Permissive error handling is a smell" — but the inverse is also a smell: deliberately *not* applying a centralized policy. Asymmetric retry is "plausible-but-wrong" behavior.
- **Pi comparison**: pi-mono includes inline retry in `openai-codex-responses.ts:103-110, 258-306` and provider-level retry semantics elsewhere; consistency is enforced by the single point of HTTP code.
- **Suggested fix**: Wrap the HTTP client (per F1) with `retry_with_backoff` once. All providers inherit retry semantics; the policy can be overridden per-provider but defaults uniformly.
- **Severity**: high

### F8: Anthropic `max_tokens` is hardcoded to 4096 across all Claude models
- **Where**: `anthropic_provider.py:26` and `bedrock_provider.py:37` define `*_DEFAULT_MAX_TOKENS = 4096`. The provider dataclasses (`anthropic_provider.py:113`, `bedrock_provider.py:132`) expose `max_tokens: int = 4096` and never look at the selected model id.
- **Symptom**: Claude 3.5 Sonnet supports 8192 output tokens and Claude Sonnet 4 supports up to 64,000; both are silently capped at 4096. Users see truncated responses for any non-trivial task.
- **Article principle**: "Plausible-but-wrong defaults" — 4096 reads like a sensible number but is wrong for every modern Claude model.
- **Pi comparison**: `anthropic.ts:899` uses `options?.maxTokens ?? model.maxTokens` — a per-model limit driven by the 16,000-line `models.generated.ts` registry.
- **Suggested fix**: Drive `max_tokens` from a per-model lookup (the models registry, see F9). If the registry is intentionally minimal in pipy, at least bump the Anthropic-family default to the highest supported by the listed default model, and expose a CLI/env override.
- **Severity**: medium

### F9: No per-model registry — defaults table at `repl_state.py:35-48` lists `gpt-5.5`
- **Where**: `repl_state.py:35-48` `DEFAULT_NATIVE_MODELS`. Defaults include `"openai": "gpt-5.5"` and `"openai-codex": "gpt-5.5"`.
- **Symptom**: `gpt-5.5` is not a published OpenAI model id (as of the audit date the released family is `gpt-5` / `gpt-5.1`). The default selection will hit `model_not_found` against the real API. Other defaults are also stale (`gemini-2.0-flash-exp` was a preview superseded by `gemini-2.5-*`; `mistral-large-latest` is still valid; `@cf/meta/llama-3.1-8b-instruct` is fine).
- **Article principle**: "Plausible-but-wrong: code reads correctly but has subtle bugs or wrong models of the problem." Hardcoded model strings without a registry is also "Volume = noise."
- **Pi comparison**: pi-mono ships `models.generated.ts` (16,115 lines, 924 model entries) with per-model `maxTokens`, `inputCost`, `outputCost`, `thinkingLevelMap`, `compat` flags. Defaults are derived; not invented.
- **Suggested fix**: Either (a) generate a `models.py` from upstream pi-mono `models.generated.ts` (preserves parity), or (b) drive defaults from environment variables that the user is expected to supply, and remove invented model ids from the source. At minimum, fix `gpt-5.5` to a real id (`gpt-5.1-codex` or whatever was intended).
- **Severity**: high

### F10: OAuth refresh path silently re-extracts account id from the refreshed token
- **Where**: `openai_codex_provider.py:369-389` (`get_credentials` → `refresh` → `_credentials_from_token_response`) and `openai_codex_provider.py:819-846` (`_extract_account_id`). On every refresh, the account id is re-derived by JWT decoding the new access token. If the auth server ever returns a refreshed token without `chatgpt_account_id` in the JWT claim, `_credentials_from_token_response` raises `OpenAICodexOAuthError`, the unhandled exception bubbles out of `auth_manager.get_credentials()` in `complete()` at line 495-504, and the stored credentials are *not* deleted — the next run will load the stale credential and refresh again, looping on the same failure.
- **Symptom**: A transient auth-server bug or rotated JWT format becomes a sticky "Codex login required" error with no clean recovery.
- **Article principle**: "Permissive error handling is a smell: 'best-effort' recovery in critical paths" — combined with "Make bad states impossible." The refresh path *should* persist the new token before re-deriving the account id, or treat account_id as known once on first login and not require re-derivation on refresh.
- **Pi comparison**: pi-mono's Codex OAuth flow stores the account id at login and treats refresh as a token-only update (`openai-codex-responses.ts` keeps account state in the cached session, not in the JWT claim).
- **Suggested fix**: Keep `account_id` as a separate persisted field that is set at login. On refresh, only update `access_token`/`refresh_token`/`expires_at`. If `_extract_account_id` fails on a refreshed token, log it but keep the existing account_id.
- **Severity**: medium

### F11: `urlopen` `finally` cleanup uses bare `except Exception`
- **Where**: `openai_codex_provider.py:178-182`:
  ```python
  finally:
      try:
          response.close()
      except Exception:  # noqa: BLE001 - best-effort cleanup
          pass
  ```
- **Symptom**: A real bug in `response.close()` (memory error, OS error) is swallowed in production. The `noqa` comment acknowledges the smell but doesn't remove it.
- **Article principle**: "Permissive error handling is a smell: swallowed exceptions, broad try/except."
- **Pi comparison**: pi-mono lets the runtime own connection cleanup via `await response.body?.cancel()` in catch blocks, not generic swallowing.
- **Suggested fix**: Catch only `OSError` (what `close()` actually raises) or use a context manager. Don't wrap in `try/except Exception/pass` "just in case."
- **Severity**: low

### F12: 11 copies of the "swallowed `ValueError` on `ProviderToolCall` construction" pattern
- **Where**: `openai_provider.py:429`, `openai_completions_provider.py:361`, `openrouter_provider.py:330`, `anthropic_provider.py:400`, `mistral_provider.py:327`, `google_provider.py:465`, `google_vertex_provider.py:543`, `azure_openai_provider.py:492`, `cloudflare_provider.py:356`, `bedrock_provider.py:472`, `openai_codex_provider.py:1064` all wrap the `ProviderToolCall(...)` constructor call in `try: ... except ValueError: continue`.
- **Symptom**: If `ProviderToolCall.__post_init__` ever rejects a value (e.g., a name with a forbidden character), the tool call is silently dropped — the model thinks it called the tool, the loop sees no call, the user sees nothing. The same `if not isinstance(name, str) or not name: continue` is already done above each block, so the ValueError catcher is defensive coverage for length truncation, but the silent-drop is the wrong behavior.
- **Article principle**: "AI over-engineers: handles every malformed edge case instead of preventing them" + "Make bad states impossible."
- **Pi comparison**: pi-mono builds `toolCall` blocks without a ValueError catcher; bounds checks are in `models.ts` and types, not runtime swallows.
- **Suggested fix**: Move the validation logic into a single `ProviderToolCall.from_raw(name, arguments, correlation_id)` factory that either returns a valid call or raises a domain-specific error. Let the loop log and surface, don't silently drop.
- **Severity**: medium

### F13: `_extract_usage` is forked 11× with slightly different field maps and one synthesizes totals
- **Where**: Every provider has its own `_extract_usage` (`openai_provider.py:434-446`, `openai_completions_provider.py:453-459`, `openrouter_provider.py:420-426`, `anthropic_provider.py:405-430`, `mistral_provider.py:417-424`, `google_provider.py:470-476`, `google_vertex_provider.py:548-554`, `azure_openai_provider.py:497-510`, `cloudflare_provider.py:446-453`, `bedrock_provider.py:477-503`, `openai_codex_provider.py:1129-1141`). Anthropic synthesizes `total_tokens = input + output` when omitted (`anthropic_provider.py:421-428`) but Bedrock does not, despite carrying the same Anthropic-shape body.
- **Symptom**: Usage reporting differs across providers for no documented reason. Tests on Anthropic pass with synthesized totals; Bedrock returns `usage` without `total_tokens` in archived metadata, silently breaking the cost-tracking story.
- **Article principle**: "Plausible-but-wrong: subtle bugs."
- **Pi comparison**: pi-mono's `models.ts` carries `calculateCost(model, usage)`; field maps live in shared utilities, not per-provider helpers.
- **Suggested fix**: One `extract_usage(body, field_map)` helper in `usage.py` that takes the `(provider_field → normalized_field)` mapping. Synthesis rules (`total = input + output`) are policy in one place.
- **Severity**: medium

### F14: Codex SSE parser has two implementations of the same logic (streaming + after-the-fact)
- **Where**: `openai_codex_provider.py:191-242` (`_iter_sse_stream`, streaming) and `openai_codex_provider.py:1087-1108` (`_iter_sse_events`, parses a string buffer). Both handle `data:` lines, blank-line separation, `[DONE]`, and JSON decoding — but the streaming version yields `Iterator[Mapping]`, the buffered version returns `list[Mapping]`. The parser at line 866-1038 then picks one or the other (`event_stream if event_stream is not None else iter(_iter_sse_events(body))`).
- **Symptom**: Two parse paths to keep in sync. A bug in one (e.g., handling of `event:` fields) won't be caught by tests that only exercise the other. The comment at line 56-65 acknowledges this dual-path design as a test affordance, which is the smell.
- **Article principle**: "AI over-engineers: redundant abstractions" — the buffered path exists only so tests can pass a `body` string instead of an iterator.
- **Pi comparison**: pi-mono has one SSE parser (`openai-codex-responses.ts:559-617`) and tests pump events into the same iterator.
- **Suggested fix**: Delete `_iter_sse_events` (the string parser). Tests pass an `Iterator[Mapping]` directly, or a generator that yields from a list. The production path stays as the only path.
- **Severity**: medium

### F15: Codex reasoning summary inserts hardcoded `\n\n` paragraph breaks
- **Where**: `openai_codex_provider.py:889-894`:
  ```python
  if event_type == "response.reasoning_summary_part.added":
      if reasoning_sink is not None:
          reasoning_sink("\n\n")
      continue
  ```
- **Symptom**: The provider mixes presentation logic (paragraph cadence for the renderer) into the wire-protocol parser. Renderers cannot turn this off; tests cannot easily distinguish "the model emitted a blank line" from "the parser inserted a separator." Comment at line 890-891 admits this is for "Pi-equivalent" rendering — that's a renderer concern.
- **Article principle**: "AI slop aesthetics: redundant abstractions" — the parser owns a thing it shouldn't.
- **Pi comparison**: pi-mono's `processResponsesStream` (`openai-responses-shared.ts`) yields raw delta events; renderers in the `pi` package decide cadence.
- **Suggested fix**: Yield a structured "reasoning_part_boundary" event (or a typed delta) and let the renderer decide whether to print a blank line. Keep the parser one-to-one with the wire.
- **Severity**: low

### F16: `_responses_input` duplicates the same envelope-to-Responses translator in 3 files
- **Where**: `openai_provider.py:190-262` `_responses_input` + `_envelope_to_input_items`, `azure_openai_provider.py:237-312` (verbatim same), `openai_codex_provider.py:592-670` (verbatim same with `_responses_input_messages` rename).
- **Symptom**: Same code, three copies. Pi-mono's `convertResponsesMessages` (`openai-responses-shared.ts:90-265`) is used by all three.
- **Article principle**: "Volume = noise."
- **Pi comparison**: `openai-responses-shared.ts:90-265` is invoked by `openai-responses.ts`, `openai-codex-responses.ts`, and `azure-openai-responses.ts`.
- **Suggested fix**: Extract `responses_serializer.py` with `responses_input(request)` and `envelope_to_input_items(envelope)` shared by the three OpenAI Responses-family providers (`openai`, `azure-openai`, `openai-codex`).
- **Severity**: high

### F17: Cloudflare Workers AI advertises `supports_tool_calls=True` but Workers AI tool-call support is model-dependent and undocumented in this adapter
- **Where**: `cloudflare_provider.py:113` `supports_tool_calls: bool = True` plus the OpenAI Chat-Completions-shape tool extractor at `cloudflare_provider.py:318-358`. The default model `@cf/meta/llama-3.1-8b-instruct` (`repl_state.py:47`) does not reliably emit OpenAI-style `tool_calls` arrays — only some Workers AI models do, and the Cloudflare OpenAI-compatible surface explicitly notes function-calling support is "varies by model."
- **Symptom**: The capability flag lies for the configured default. A `--repl-mode tool-loop` session against the default Cloudflare model will fail in unpredictable ways or silently fall through to text-only responses.
- **Article principle**: "Plausible-but-wrong: subtle bugs or wrong models of the problem."
- **Pi comparison**: pi-mono treats tool-call capability per-model via `models.generated.ts` (`supportsToolUse: true/false`), so a Llama-3.1-8B entry would correctly carry `supportsToolUse: false` while Llama-3.3-70B carries `true`. The capability is a property of the model, not the provider.
- **Suggested fix**: Make `supports_tool_calls` a property of the resolved model (driven from a model registry — see F9) rather than the provider class. Or, at minimum, change the Cloudflare default to a model that documents tool-call support and document the constraint.
- **Severity**: medium

### F18: `dynamic_provider.py` is a 140-line wrapper around `state.select_model` that the parent could call directly
- **Where**: `dynamic_provider.py:52-138` `swap_provider`/`swap_model`. Both functions do: parse name, look up `DEFAULT_NATIVE_MODELS`, call `state.select_model(f"{provider}/{model}")`, package the outcome.
- **Symptom**: Indirection without behavior — the wrapper does no real work that `state.select_model` couldn't accept directly. The `SwapOutcome` dataclass duplicates the `(success, message)` tuple that `select_model` already returns, just with the selection attached.
- **Article principle**: "AI slop aesthetics: redundant abstractions."
- **Pi comparison**: pi-mono's REPL slash-commands invoke the model registry directly; no swap wrapper.
- **Suggested fix**: Inline the swap logic into the `/provider` and `/model` REPL handlers, or expose `select_provider(provider_name, model_id=None)` on `NativeReplProviderState` and delete `dynamic_provider.py`.
- **Severity**: low

### F19: `_envelope_to_chat_message` and `_envelope_to_message` raise provider-specific `*ResponseParseError` on unsupported envelopes — but the type universe is closed
- **Where**: `openai_completions_provider.py:412` raises `OpenAICompletionsResponseParseError(f"unsupported message envelope: {type(envelope).__name__}")` — and the same dead-branch raise in `openrouter_provider.py:379-381`, `mistral_provider.py` (in `_envelope_to_chat_message`), `cloudflare_provider.py`, `anthropic_provider.py:252-254`, `bedrock_provider.py:319-321`, `google_provider.py`, `google_vertex_provider.py:424-426`, `azure_openai_provider.py:310-312`, `openai_provider.py:260-262`, `openai_codex_provider.py:668-670`. The set of envelope types is closed: `UserMessage`, `AssistantMessage`, `ToolResultMessage` (defined in `tools/messages.py` as a `LoopMessage` union).
- **Symptom**: Defensive validation at every level for a state the type system already prevents. If a new envelope type is added, 11 places need updates; if it's not, the branch is unreachable.
- **Article principle**: "AI over-engineers: handles every malformed edge case instead of preventing them" — the type system makes this state impossible.
- **Pi comparison**: pi-mono uses TypeScript's exhaustiveness checks (`never` returns) to prove the union is closed at compile time; no runtime raise.
- **Suggested fix**: Use Python `match` statements with `case _` that hits `assert_never` from `typing`. Or simply drop the raise — `mypy --strict` will flag unhandled cases at type-check time. Move envelope-shape validation to the `LoopMessage` constructor, not every consumer.
- **Severity**: low

### F20: `_safe_response_label` is duplicated in 7 provider files with identical bodies
- **Where**: `openai_completions_provider.py:476-480`, `openrouter_provider.py:439-443`, `mistral_provider.py:436-444`, `cloudflare_provider.py:469-474`, `google_provider.py:489-493`, `google_vertex_provider.py:571-575`, `openai_codex_provider.py:1154-1158`. Function body in every copy:
  ```python
  if not isinstance(value, str) or not value:
      return default
  sanitized = sanitize_text(value)
  return sanitized if sanitized != "[REDACTED]" else default
  ```
- **Symptom**: One-line helper, seven copies. The other providers (`anthropic_provider.py`, `bedrock_provider.py`, `azure_openai_provider.py`, `openai_provider.py`) don't have it but solve the same problem inline with `sanitize_text(...) if isinstance(...) else "unknown"` (`anthropic_provider.py:321-326`, `openai_provider.py:335-337`).
- **Article principle**: "Volume = noise" and "AI slop aesthetics."
- **Pi comparison**: pi-mono uses `sanitizeSurrogates` in one place (`utils/sanitize-unicode.ts`) consumed across providers.
- **Suggested fix**: Move `_safe_response_label` into `capture.py` next to `sanitize_text` and import once.
- **Severity**: low
