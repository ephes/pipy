# Audit: Native Session + REPL Control Flow

Scope:
- `src/pipy_harness/native/session.py` (3,546 lines)
- `src/pipy_harness/native/tool_loop_session.py` (1,544 lines)
- `src/pipy_harness/native/repl_input.py` (1,149 lines)
- `src/pipy_harness/native/repl_state.py` (542 lines)
- `src/pipy_harness/native/conversation.py` (527 lines)

Comparison:
- `packages/agent/src/agent.ts` (557 lines)
- `packages/agent/src/agent-loop.ts` (742 lines)
- `packages/agent/src/harness/agent-harness.ts` (995 lines)
- `packages/agent/src/harness/types.ts` (815 lines)
- `packages/coding-agent/src/core/agent-session.ts` (3,089 lines)
- `packages/coding-agent/src/modes/interactive/interactive-mode.ts` (5,564 lines)
- `packages/coding-agent/src/core/slash-commands.ts` (40 lines)

## Summary

Pipy's native session layer is roughly 5x the size of pi-mono's equivalent control flow even though it implements a much smaller feature set (one bootstrap turn, one bounded no-tool REPL, one tool-loop REPL with eight tools — vs. pi-mono's full coding agent). The bulk comes from three classic AI-slop patterns: (1) a ~600-line metadata-projector apparatus in `session.py` that exists only to defensively sanitize provider metadata that no real provider in this repo ever emits — only the fake provider does (`PROVIDER_TOOL_INTENT_METADATA_KEY` etc. are written by `fake.py` and parsed back by `session.py`, a pipe leading nowhere); (2) the bootstrap `NativeAgentSession.run` (lines 480–691) is a single linear 210-line waterfall of nested `if` branches that re-implements a hand-rolled state machine instead of using a small loop — and the entire fixture-driven tool/patch/verification ladder it walks is exercised only by the deterministic fake; (3) `NativeNoToolReplSession.run` is a 600-line `while`-with-`if/elif/elif/elif` chain (lines 931–1378) that dispatches twelve slash commands inline with full footer re-paint at the top of every branch, whereas pi-mono uses a single declarative `BUILTIN_SLASH_COMMANDS` table.

Top three concerns: **(A)** `NativeAgentSession` (session.py:466–816) and its supporting ~1,500 lines of metadata-key allowlists, projectors, "unsafe/unsupported" enum reasons, parsed-fixture dataclasses, and `_skipped_*` factories are an over-engineered fortress around an attack surface that does not exist in production — real providers never write to `pipy_native_tool_intent`, so all of these guards fire only against pipy's own fake; **(B)** the four REPL command-handling layers (`_print_repl_*`, `_handle_repl_*`, `_read_repl_*`, `_pending_repl_apply_draft`) intermingle parsing, side-effect printing, budget bookkeeping, provider calls and conversation-state mutation in one 600-line function (session.py:931–1378) — adding a slash command requires editing four files and threading two budget objects, three "command used" flags, and a `pending_apply_draft` through every `continue`; **(C)** there are three almost-parallel REPL session implementations (`NativeAgentSession`, `NativeNoToolReplSession`, `NativeToolReplSession`) and only the last is actually pi-shaped (real tool loop) — the first two are a bootstrap fixture path and a "no-tool" REPL with bolted-on `/read`, `/ask-file`, `/propose-file`, `/apply-proposal`, `/verify` commands that re-implement, in a heavily metadata-gated way, the same tools the tool-loop session calls directly. The no-tool REPL appears to be a parallel-universe parity track that should either be deleted or absorbed into the tool-loop session.

## Findings

### F1: Defensive provider-metadata projector exists for an attack surface that does not exist
- **Where**: `src/pipy_harness/native/session.py:188–282`, `:2397–2430`, `:2433–2480`, `:2482–2527`, `:2530–2551`, `:2573–2667`, `:2678–2785`, `:2803–2845`; plus the `_ALLOWED_*_KEYS`, `_SUPPORTED_*`, `_SAFE_*` constant blocks at `:111–195`.
- **Symptom**: ~600 lines projecting, allowlisting and "unsupported-labeling" provider metadata that — verified by grep — is only ever produced by `fake.py:88–96`. Real providers (`openai_codex_provider`, `anthropic_provider`, `google_provider`, etc.) never set `PROVIDER_TOOL_INTENT_METADATA_KEY`, `PROVIDER_TOOL_OBSERVATION_FIXTURE_METADATA_KEY`, `PROVIDER_READ_ONLY_TOOL_FIXTURE_METADATA_KEY`, or `PROVIDER_PATCH_PROPOSAL_METADATA_KEY`. The comments at `:200–207` and `:2402–2413` justify the apparatus by invoking a "hostile or future ProviderPort" that might smuggle a system prompt back through allowlisted fields — but every real adapter pipy ships is written by pipy.
- **Article principle**: "AI over-engineers — handles every malformed edge case instead of preventing them." Also: "make bad state impossible." If pipy treated provider metadata as opaque scalars (or required real providers to never carry these keys via type), the entire layer would disappear.
- **Pi comparison**: pi-mono's `agent-loop.ts:155–270` (`runLoop`) just consumes `AssistantMessage.content` (typed); there is no "is this provider metadata an attack vector against my archive" layer. pi-mono's `harness/messages.ts` (164 lines total) and `agent.ts:31–35` `defaultConvertToLlm` simply filter by role.
- **Suggested fix**: Delete the entire fixture-metadata channel. `ProviderResult.metadata` should not carry `pipy_native_*` keys at all — those should be parameters on `FakeNativeProvider` that the test wires straight into the `NativeAgentSession` it constructs. Then `_parse_tool_intent`, `_parse_tool_observation_fixture`, `_parse_read_only_tool_fixture`, `_parse_patch_proposal`, all `_unsafe_*`/`_unsupported_*` helpers, and `_SAFE_PROVIDER_METADATA_PROJECTORS` go away. ~600 lines.
- **Severity**: high

### F2: `NativeAgentSession.run` is a 210-line waterfall encoding a state machine inline
- **Where**: `src/pipy_harness/native/session.py:480–691`.
- **Symptom**: A single linear method threads `tool_result`, `read_only_result`, `observation_failure_reason`, `follow_up_provider_result`, `follow_up_provider_usage`, `patch_apply_result`, and `verification_result` through five nested `if` levels. The "intent → tool → observation → follow-up provider → patch apply → verification" ladder is inlined rather than expressed as a sequence. The deepest happy-path branch is six levels of indentation (`:569–586`).
- **Article principle**: "Volume = noise" and "Plausible-but-wrong" — the function reads correctly but reduces to a fixed straight-line plan that should be a list of stages.
- **Pi comparison**: `packages/agent/src/agent-loop.ts:155–269` (`runLoop`) is 115 lines and runs an actual loop with explicit step boundaries; it does not need to enumerate the post-tool stages because tools and follow-up turns are just more iterations.
- **Suggested fix**: Express the bootstrap path as a sequence of small named steps (`run_initial_turn`, `maybe_run_read_only_tool`, `maybe_run_follow_up_turn`, `maybe_apply_patch`, `maybe_verify`), each returning a sum-type stage outcome. Or, once F1 is fixed, fold the bootstrap path into a single-iteration call of the tool-loop driver.
- **Severity**: high

### F3: Three parallel session implementations with overlapping responsibilities
- **Where**: `session.py:466–816` (`NativeAgentSession`), `session.py:819–1438` (`NativeNoToolReplSession`), `tool_loop_session.py:336–741` (`NativeToolReplSession`).
- **Symptom**: All three implement "build composed system prompt → call provider → react to result → emit events" but in different shapes. `NativeAgentSession` runs exactly one provider turn plus an optional follow-up; `NativeNoToolReplSession` runs many one-shot provider turns with slash commands for tools; `NativeToolReplSession` runs the actual model-driven tool loop. The first two are fixture/parity scaffolding; the third is the real implementation. The no-tool REPL even re-implements `/read`/`/ask-file`/`/propose-file`/`/apply-proposal`/`/verify` — i.e. an opt-in, slash-driven shadow of the same tools the tool-loop session calls automatically.
- **Article principle**: "Volume = noise" and "AI slop aesthetics: redundant abstractions, layers added 'just in case'." This is two implementations grandfathered in to keep an early parity track alive after the real one shipped.
- **Pi comparison**: pi-mono has exactly one tool-loop driver (`agent-loop.ts:runLoop`). Bootstrap-style "send one prompt and stop" is achieved by passing tools=[] and exiting after the first assistant message — not by maintaining a parallel session class.
- **Suggested fix**: Delete `NativeAgentSession` and `NativeNoToolReplSession`; route both behaviors (single-turn fixture mode, conversational REPL) through `NativeToolReplSession` with `tool_registry={}` and an appropriate stop condition. The `/read`/`/ask-file`/`/propose-file`/`/apply-proposal`/`/verify` commands become unnecessary because the model uses the real tools.
- **Severity**: high

### F4: REPL command dispatch is an `if/elif` chain instead of a table
- **Where**: `src/pipy_harness/native/session.py:983–1329` (the body of `NativeNoToolReplSession.run`'s `while` loop).
- **Symptom**: Twelve `if _is_repl_command_invocation(command, ...)` branches, each with its own footer-repaint, its own "command used" flag mutation, its own `pending_apply_draft = None` reset, and its own `continue`. Adding a 13th slash command requires editing this 350-line block plus `_REPL_COMMAND_GROUPS` plus `DEFAULT_REPL_SLASH_COMMAND_COMPLETIONS` plus `DEFAULT_REPL_COMMAND_DESCRIPTIONS` plus the test fixture wiring.
- **Article principle**: "AI slop aesthetics: redundant abstractions" and "make bad state impossible" (the "command used" flags only exist because the dispatch isn't centralized).
- **Pi comparison**: `packages/coding-agent/src/core/slash-commands.ts:18–40` declares `BUILTIN_SLASH_COMMANDS` as a single table of `{name, description}`. Dispatch in pi-mono routes by name through a command registry rather than per-branch `if`s.
- **Suggested fix**: Replace the chain with a `{command_name: handler}` registry. Each handler returns a small `ReplStepOutcome` (continue / break / break-with-error). The handler reads its arguments out of `command[len(name):].strip()` itself. Footer re-paint moves to the top of the loop body, not inside every branch.
- **Severity**: medium

### F5: Footer re-paint is duplicated at six sites with identical argument lists
- **Where**: `src/pipy_harness/native/session.py:900–909`, `:920–929`, `:968–977`, and the call sites inside command branches; `_print_repl_footer` itself is at `:1775–1799`; `_repl_footer_text` at `:1694–1720`; in `tool_loop_session.py` the same shape recurs at `:448–457`, `:492–499`, `:522–530`, `:585–594`, `:622–631`.
- **Symptom**: Every command handler in both REPL sessions repeats the same 8–10 keyword arguments (`provider_state`, `run_input`, `conversation_state`, `read_budgets`, `no_tool_context`, `pending_apply_draft`, `verify_after_apply_available`) into `_print_repl_footer`. Even the helper `_repl_footer_text` and `_repl_display_state` take the same argument tuple. There is also a redundant pre-loop `if repl_input.runtime_label != "slash-menu"` footer paint guard duplicated in both REPL session classes.
- **Article principle**: "AI over-engineers" and "Volume = noise."
- **Pi comparison**: pi-mono's `interactive-mode.ts` carries the equivalent state in a `InteractiveMode` instance and re-renders by re-running its render function — there is no per-branch repaint with explicit argument threading because the render is reactive to state, not pushed.
- **Suggested fix**: Hold the footer-relevant state on a `ReplLoopState` dataclass that the loop carries by reference. `_print_repl_footer(state)` becomes a one-argument call. Better: paint the footer exactly once per iteration at the top of the loop, before reading input, and remove all in-branch repaints — there is no observed reason to repaint after a no-op command.
- **Severity**: medium

### F6: Permissive `except Exception:` blocks around object construction, login, logout, repl_input.close, tool dispatch, provider calls
- **Where**: `src/pipy_harness/native/session.py:715–716`, `:767–768`, `:793–795`, `:813–815`, `:1036–1038`, `:1051–1053`, `:1415–1418`, `:1529–1530`, `:1582–1583`, `:2039–2042`, `:2319–2320`. `src/pipy_harness/native/repl_input.py:265`, `:299`, `:787`, `:847`, `:856`, `:865`. `src/pipy_harness/native/tool_loop_session.py:703–704`, `:726–727`.
- **Symptom**: Most of these swallow any exception and substitute a "skipped/failed" result or silently fall through to a fallback adapter. For example `session.py:1036` catches every exception from `provider_state.login()` and renders a generic message; `session.py:1415–1418` swallows any close error from `repl_input`; `repl_input.py:847–866` walks four fallback runtimes, each in its own `try/except Exception: pass`, and only `PlainNativeReplInput` survives. None of these distinguish recoverable I/O / runtime-unavailability errors from genuine programmer-error exceptions.
- **Article principle**: "Permissive error handling is a smell" — broad `except Exception:` in critical paths.
- **Pi comparison**: `agent-loop.ts:578–625` catches errors only at the tool boundary and turns them into `createErrorToolResult` (a structured value), not generic swallow. The fallback chain in `repl_input.py` is also a smell: pi-mono picks an input strategy from a single typed selector, not by trying four constructors in sequence and discarding errors.
- **Suggested fix**: Narrow each `except` to the concrete class of failure it expects (`OSError`, `ImportError`, `ReplInputUnavailableError`, `json.JSONDecodeError`). Let unexpected exceptions propagate; they signal a bug, not a recovery case. For `native_repl_input_for`, replace the four nested try/except fallbacks with a single capability probe (which it already has: `_slash_menu_streams_supported`, `_prompt_toolkit_streams_supported`, `_readline_streams_supported`).
- **Severity**: medium

### F7: `_unsafe_intent_reason` validates fields that real providers do not write
- **Where**: `src/pipy_harness/native/session.py:2482–2527`.
- **Symptom**: 46 lines checking for `unsafe_tool_intent_keys`, `unsafe_tool_intent_request_id`, `unsafe_tool_intent_turn_index`, `unsafe_tool_intent_source`, `unsafe_tool_intent_policy`, including nested type-narrowing for `approval_policy`, `sandbox_policy`, `workspace_read_allowed`, `filesystem_mutation_allowed`, etc. All of these inputs come from `Mapping` data the fake handed us; the real adapters never set the `pipy_native_tool_intent` key.
- **Article principle**: "Make bad state impossible." A typed dataclass instead of a `Mapping[str, object]` for tool intents would eliminate the entire layer.
- **Pi comparison**: pi-mono has no equivalent because tool calls are typed (`AgentToolCall`, `ProviderToolCall`) end-to-end; there is no "is this raw dict shaped right?" pass.
- **Suggested fix**: Make `FakeNativeProvider` build a `NativeToolIntent` object directly (it is already in scope at `models.py`), and pass it as a typed parameter from the fake to the session instead of via `Mapping` round-trip. The "unsafe shape" defense becomes unrepresentable.
- **Severity**: high

### F8: Unused parameters and helpers indicating speculative design
- **Where**:
  - `session.py:1683–1690` — `_print_repl_startup_chrome` accepts six keyword arguments and immediately `del`s all six.
  - `session.py:1771` — `_repl_effort_label` accepts a state arg, `del`s it, and always returns `"default"`. Docstring promises a future selector.
  - `session.py:1828–1830` — `_repl_prompt_label` accepts state, `del`s it, returns `">"`.
  - `tool_loop_session.py:814–830` — `_estimated_context_tokens` admits in its docstring that it is a stub estimator until "authoritative provider usage telemetry is a separate follow-up."
  - `session.py:2370` raises if `provider_turn_label` is `None`, but the helper that produces it (`_append_provider_turn`) always passes one — the `None` branch is unreachable.
- **Article principle**: "Dead/half-finished code, 'for future X' code" and "AI slop aesthetics: half-finished implementations."
- **Pi comparison**: pi-mono's equivalent helpers either render the value (no stub) or are absent.
- **Suggested fix**: Delete the unused arguments; inline the constants where they are used. Pin the future-effort selector and the future-usage-telemetry to a tracked TODO outside the source.
- **Severity**: low

### F9: `_pending_repl_apply_draft` re-validates a proposal the model already returned, then ignores it
- **Where**: `src/pipy_harness/native/session.py:2087–2113` and the apply-proposal text parser at `:2115–2220`.
- **Symptom**: When `parse_patch_proposal` succeeds, `_pending_repl_apply_draft` then re-derives all the same information by re-parsing the provider's free-form text (the fenced `pipy-apply-proposal-v1` block), and compares the two. If they disagree it returns `None`. This is plausible-but-wrong: it means the fenced text in the model's response, not the structured metadata, is treated as the source of truth, while the structured metadata is reduced to a consistency check. Real providers don't emit the metadata key, so the metadata branch is never exercised, and the entire layer collapses to "parse the fenced block."
- **Article principle**: "Plausible-but-wrong" — the model of which channel is authoritative is upside-down.
- **Pi comparison**: pi-mono uses structured tool calls (`ProviderToolCall.arguments_json`); it does not parse free-form fenced text in the assistant response to determine intent. See `agent-loop.ts:203` (`toolCalls = message.content.filter((c) => c.type === "toolCall")`).
- **Suggested fix**: Drop the fenced-block protocol once the no-tool REPL is removed (F3); the tool-loop session already represents proposals as structured tool calls.
- **Severity**: medium

### F10: Conversation-state validation prevents states that no caller can construct
- **Where**: `src/pipy_harness/native/conversation.py:105–106`, `:118–127`, `:162–195`, `:259–269`, `:305–322`, `:413–432`, `:502–527`.
- **Symptom**: `NativeTurnMetadata.__post_init__` validates every `*_stored` boolean against the literal value `False` (`:193–195`), validates the role/status are enum instances rather than relying on the type system, and the conversation state checks contiguous turn indices in `:316–322`. The "safe label" guard at `:515–527` rejects strings containing `/`, `\`, `~`, leading `.`, or control characters — but the only labels in scope are produced by pipy-internal constants like `INITIAL_PROVIDER_TURN_LABEL = "initial"`.
- **Article principle**: "Make bad state impossible." The dataclass `__post_init__` is enforcing invariants that the type signature would already enforce — except where it isn't, e.g. `*_stored: bool = False` is a default that is then checked at runtime to still be `False`.
- **Pi comparison**: pi-mono's `harness/types.ts:1-815` is mostly types and discriminated unions; runtime validation is reserved for the tool-input boundary. There is no equivalent of "validate that `prompt_stored` is still false."
- **Suggested fix**: Replace `*_stored: bool = False`-with-runtime-check with `ClassVar` constants on the dataclass. Drop `_validate_safe_label` from internal label fields; keep it only at the external (provider-supplied) boundary.
- **Severity**: low

### F11: Read-budget bookkeeping has a one-off "recovery after failure" axis that is conceptually orthogonal to the limit
- **Where**: `src/pipy_harness/native/session.py:397–439` (`_ReplReadBudgets`) and call sites at `:1086–1108`, `:1115–1119`, `:1186–1191`, `:1209`.
- **Symptom**: The budget object tracks three independent counters: `successful_excerpt_count`, `failed_or_skipped_attempt_used`, and `recovery_attempt_after_failure_used`. `can_attempt` is true if successful remaining > 0 **and not** (failed used **and** recovery used). The meaning of "you can fail twice but only if you succeed once in between" is encoded implicitly across `.after()` and `can_attempt`. The status output at `:1869–1877` exposes six budget metrics to the user. No tests or docs explain why a user should be limited to one recovery attempt after one failure.
- **Article principle**: "AI over-engineers" and "Plausible-but-wrong" — this is the AI inventing a multi-axis policy because "we have a budget" without a clear product reason.
- **Pi comparison**: pi-mono's tool-loop in `agent-loop.ts:155–269` simply gives the model a tool budget per user turn (`MAX_TOOL_BUDGET` in `tool_loop_session.py:363` mirrors this single-axis design). There is no "recovery after failure" axis.
- **Suggested fix**: Reduce to one counter (`attempts_remaining`). Drop `failed_or_skipped_attempt_used` and `recovery_attempt_after_failure_used`. Or — better — delete the budget entirely once F3 collapses the no-tool REPL into the tool-loop REPL (which already enforces a tool budget).
- **Severity**: low

### F12: Provider-metadata enum allowlists vs. the existing sanitizer
- **Where**: `src/pipy_harness/native/session.py:208–266`, `:269–282`.
- **Symptom**: `_PROVIDER_RESPONSE_STATUS_ALLOWED`, `_PROVIDER_RESPONSE_OBJECT_ALLOWED`, `_PROVIDER_FINISH_REASON_ALLOWED` are projected via `_make_enum_projector`, which replaces anything outside the set with `"<unsupported>"`. The comments at `:200–207` say this is to prevent providers from smuggling a system prompt back via a "short string that fits inside any previous length cap." But `sanitize_text` (used throughout) already handles this: the threat model assumes a hostile provider can choose values, but `_safe_provider_metadata` itself was applied via an explicit `archive_provider_metadata: bool = True` parameter that callers turn off (e.g. session.py:1162, :1235, :1356) for the REPL turns where leakage matters most.
- **Article principle**: "Plausible-but-wrong" — the defense applies most narrowly where the threat is least (real provider integration tests with `archive_provider_metadata=True`) and is disabled where the threat is broadest.
- **Pi comparison**: pi-mono has no symmetric defense; provider metadata is treated as observability data, not as input to the model.
- **Suggested fix**: Move the metadata-sanitization decision into one place (the archiver / event sink), so it is impossible to forget. Or delete the projector entirely (see F1).
- **Severity**: medium

### F13: Tool-loop renderer hand-paints user-message panel by stepping up over readline echo
- **Where**: `src/pipy_harness/native/tool_loop_session.py:1220–1256` (`render_user_message`, `_user_message_panel_line`).
- **Symptom**: After the `repl_input.read_line()` returns, the renderer rewinds the cursor with `\x1b[1A\x1b[2K\r` once per logical input line and re-paints the user's typed message on a colored panel. This depends on the input adapter having echoed exactly one line per `\n`, which is a fragile coupling between the input adapter and the renderer. Pipy already owns the slash-menu adapter (which writes the buffer to error_stream in `_finalize_and_print_buffer`, `repl_input.py:718–728`); a cleaner design would suppress the echo and let the renderer own the user-message paint. The current design also breaks for multi-line input on the readline adapter, which echoes with its own continuation prompt.
- **Article principle**: "Plausible-but-wrong" and "Leaky boundaries" — the renderer reaches across the input-adapter boundary by knowing how many cursor-ups to send to overwrite the echo.
- **Pi comparison**: pi-mono's input adapter and panel renderer share a layout component; the input cursor is conceptually a child of the panel, not a sibling that another component overwrites after the fact.
- **Suggested fix**: Have the slash-menu adapter accept a `paint_submitted_line` callback (or just not echo the submitted line and pass it back as a return value), and have the readline adapter not invoke `render_user_message` at all (its echo already shows the user's text). Drop the cursor-rewind dance.
- **Severity**: medium

### F14: REPL-input runtime fallback chain swallows real configuration errors
- **Where**: `src/pipy_harness/native/repl_input.py:840–867`.
- **Symptom**: The auto-resolver tries slash-menu, then prompt-toolkit, then readline, then plain — each wrapped in `except Exception: pass`. If the user sets `PIPY_INPUT_RUNTIME=prompt-toolkit` and prompt-toolkit fails to initialize for a real reason (e.g. a bug in `_prompt_toolkit_multiline_key_bindings`), they silently get the plain adapter and never know.
- **Article principle**: "Permissive error handling is a smell."
- **Pi comparison**: pi-mono uses Ink, which is single-runtime; there is no fallback chain. When pipy needs to fall back, it should do so on the basis of capability detection (which it already has) and only swallow `ReplInputUnavailableError`, not any `Exception`.
- **Suggested fix**: Replace `except Exception:` with `except ReplInputUnavailableError:`. Any other failure propagates and surfaces the bug.
- **Severity**: medium

### F15: `repl_state.py` provider-availability fan-out duplicates per-provider env probing three times
- **Where**: `src/pipy_harness/native/repl_state.py:144–252` (`model_options`), `:312–355` (`_provider_available`), `:357–380` (`_provider_unavailable_message`), `:435–513` (`AUTO_DEFAULT_PROVIDER_PRIORITY`, `_provider_available_in_env`).
- **Symptom**: Five lists of providers, each manually maintained: `SUPPORTED_NATIVE_PROVIDERS` (frozenset), `DEFAULT_NATIVE_MODELS` (dict), `model_options` (12 hand-written `NativeModelOption(...)` blocks each computing `available` inline), `_provider_available` (12-branch chain), `_provider_unavailable_message` (12-branch chain), `AUTO_DEFAULT_PROVIDER_PRIORITY` (tuple), `_provider_available_in_env` (12-branch chain). Adding a provider requires editing all seven. Each per-provider branch also re-evaluates the same env check (e.g. `bool(self._env().get("OPENAI_API_KEY"))`) — at lines `:158`, `:204`, `:318`, `:331`, `:492`, `:493`.
- **Article principle**: "Volume = noise" and "AI slop aesthetics: redundant abstractions" and "Make bad state impossible." A single per-provider record would prevent the lists from diverging.
- **Pi comparison**: pi-mono `core/model-registry.ts` (958 lines) holds a single registry table keyed by provider id, with `getApiKeyAndHeaders` and availability resolution centralized; adding a provider is one entry.
- **Suggested fix**: Replace the seven structures with one `NATIVE_PROVIDER_REGISTRY: Mapping[str, NativeProviderRegistration]` where the registration carries `default_model`, `env_check: Callable[[Mapping[str, str]], bool]`, and `unavailable_message_template`. `model_options`, `_provider_available`, `_provider_unavailable_message`, and `_provider_available_in_env` all become 5-line functions over the registry.
- **Severity**: medium

### F16: `_call_provider_turn` accepts six optional knobs but two are mutually exclusive
- **Where**: `src/pipy_harness/native/session.py:2263–2354`.
- **Symptom**: Signature: `system_prompt`, `archive_provider_metadata`, `no_tool_repl_context`, `stream_sink`, `tool_observation`, `provider_turn`. `no_tool_repl_context` is set only for the no-tool REPL turn (one call site, `:1357`); `tool_observation` is set only for the bootstrap follow-up path. `archive_provider_metadata=True` is the default but every REPL call site overrides it to `False` (lines 1162, 1235, 1356) — i.e. the default is "wrong" for the REPL.
- **Article principle**: "Plausible-but-wrong" (defaults are inverted), "make bad state impossible" (the two contexts cannot both be set).
- **Pi comparison**: pi-mono's `streamAssistantResponse` in `agent-loop.ts:275–340` takes a single typed `AgentContext` that already carries everything; there is no "is this a REPL turn or a bootstrap follow-up turn" flag matrix.
- **Suggested fix**: Once F1/F3 collapse the bootstrap path, this function shrinks to "build ProviderRequest, call provider, normalize result." The `archive_provider_metadata` flag and the two mutually-exclusive optional contexts disappear.
- **Severity**: low

### F17: `_final_status` / `_native_error_type` / `_native_error_message` duplicate the same dispatch ladder three times
- **Where**: `src/pipy_harness/native/session.py:3432–3458`, `:3460–3486`, `:3488–3509`.
- **Symptom**: Three sibling functions each examine the same six inputs in the same order (`provider_result`, `tool_result`, `observation_failure_reason`, `follow_up_provider_result`, `patch_apply_result`, `verification_result`) and return one of `status`, `error_type`, `error_message` per branch. Adding a new stage requires editing all three.
- **Article principle**: "Volume = noise" and "Duplication."
- **Pi comparison**: pi-mono returns one structured `AssistantMessage` (or `errorMessage`) per turn; the per-stage error fan-out does not exist because the loop is iterative.
- **Suggested fix**: Once F2 turns the bootstrap into a sequence of named stages, each stage returns a `StageOutcome` that already contains `status`, `error_type`, `error_message`. The three switch functions become one `.outcome()` accessor.
- **Severity**: low

### F18: `slash-menu` cursor management is open-coded with magic ANSI sequences
- **Where**: `src/pipy_harness/native/repl_input.py:447–728` (`_SlashMenuLineEditor`).
- **Symptom**: The slash-menu adapter is a hand-written line editor with cbreak mode, manual UTF-8 decoding (`:574–582`), manual key parsing (`:530–570` walks `\x1b[A`/`\x1b[B`/`\x1b[C`/`\x1b[D`/`\x1b[H`/`\x1b[F` + ctrl-c/d/u/home/end), and manual cursor positioning (`:654–694` uses `\x1b[NB`, `\x1b[NA`, `\x1b[J`, `\x1b[NG`). It re-implements what `prompt-toolkit` already does — and prompt-toolkit is the next fallback in the chain (`:849`).
- **Article principle**: "AI over-engineers" — the AI built a parallel line editor rather than configure the existing one to draw a slash menu. "Volume = noise" — 280 lines for one slash-menu UI.
- **Pi comparison**: pi-mono uses Ink (the React-for-CLI renderer) for everything UI; there's no hand-rolled cbreak editor.
- **Suggested fix**: Either make the slash-menu a prompt-toolkit `Completer` (a smaller change), or delete it and rely on the prompt-toolkit fallback. Holding a custom editor is not earning its 280 lines unless it does something prompt-toolkit can't.
- **Severity**: medium

### F19: Two ANSI-style channels (`chrome.py` and `tool_loop_session.py`) own overlapping color palettes
- **Where**: `src/pipy_harness/native/tool_loop_session.py:932–1538` (`_ToolLoopRenderer`) and the user-message bg, tool-panel bg, ANSI bold/dim/italic, cursor controls; plus `repl_input.py:654–694` which also writes raw ANSI (`\x1b[7m`, `\x1b[K`, etc.). `chrome.py` is the documented chrome layer.
- **Symptom**: Three components emit ANSI directly with their own copies of the escape sequences. There is no central style table; truecolor-detection logic is duplicated (`tool_loop_session.py:1018–1034` vs whatever `chrome.py:chrome_style_for` does).
- **Article principle**: "Leaky boundaries" and "Duplication."
- **Pi comparison**: pi-mono uses one theme file (`packages/coding-agent/src/modes/interactive/theme/`) consumed by Ink components.
- **Suggested fix**: Centralize all ANSI in `chrome.py`. Renderers take a `Style` object, not a literal sequence.
- **Severity**: low

### F20: Repeated trivial wrappers exist solely to forward to a stdlib or chrome helper
- **Where**: `src/pipy_harness/native/session.py:1802–1803` (`_print_repl_input_separator` → `print_input_separator`), `:1957–1958` (`_workspace_label` → `sanitize_text(cwd.name)`), `:1961–1962` (`_status_bool` → `"true" if value else "false"`), `:1828–1830` (`_repl_prompt_label`), `:2259–2260` (`_build_system_prompt` returns a constant), `:2368–2371` (`_required_provider_turn_label`).
- **Symptom**: Single-line wrappers that obscure the call site without adding behavior.
- **Article principle**: "AI slop aesthetics: redundant abstractions."
- **Pi comparison**: not applicable (pi-mono just uses the constant/expression inline).
- **Suggested fix**: Inline these at their (few) call sites.
- **Severity**: low

## Cross-cutting observations

- **`session.py` is doing at least eight different jobs.** A clean split would land roughly as: `bootstrap_session.py` (F2/F3 collapsed), `repl_session.py` (no-tool REPL or, after F3, deleted), `repl_commands.py` (the slash-command registry — F4), `repl_chrome.py` (footer + status — F5), `provider_call.py` (`_call_provider_turn` — F16), `metadata_sanitize.py` (if kept — F1), `apply_proposal_text.py` (the fenced parser — F9), and `tool_skips.py` (the `_skipped_*` / `_failed_*` factories).
- **The fake provider drives almost all of pipy's defensive code.** Every "fixture metadata key" path in `session.py` exists to consume what `fake.py:88–96` writes. Removing the fixture-metadata channel and replacing it with typed parameters on `NativeAgentSession` (F1, F7) is the single highest-impact cleanup.
- **There is no shared session driver.** `agent-loop.ts:runLoop` is 115 lines and powers everything in pi-mono. Pipy has three driver implementations (`NativeAgentSession.run`, `NativeNoToolReplSession.run`, `NativeToolReplSession.run`) totaling ~1,400 lines. Two of them exist to model fixture states that the real loop doesn't need.
