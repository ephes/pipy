# Audit: CLI + Runner + Adapters

Scope:
- `/Users/jochen/projects/pipy/src/pipy_harness/cli.py` (853L)
- `/Users/jochen/projects/pipy/src/pipy_harness/runner.py` (412L)
- `/Users/jochen/projects/pipy/src/pipy_harness/models.py` (79L — note: audit prompt said 798L, that figure is actually `native/models.py`)
- `/Users/jochen/projects/pipy/src/pipy_harness/sdk.py` (125L)
- `/Users/jochen/projects/pipy/src/pipy_harness/capture.py` (185L)
- `/Users/jochen/projects/pipy/src/pipy_harness/adapters/base.py` (41L)
- `/Users/jochen/projects/pipy/src/pipy_harness/adapters/native.py` (406L — three adapter classes)
- `/Users/jochen/projects/pipy/src/pipy_harness/adapters/subprocess.py` (111L)
- `/Users/jochen/projects/pipy/src/pipy_harness/adapters/__init__.py`
- `/Users/jochen/projects/pipy/src/pipy_harness/__init__.py`

Comparison:
- `/Users/jochen/src/pi-mono/packages/coding-agent/src/cli.ts` (21L thin shim)
- `/Users/jochen/src/pi-mono/packages/coding-agent/src/main.ts` (722L)
- `/Users/jochen/src/pi-mono/packages/coding-agent/src/cli/args.ts` (355L — single canonical parser+help)
- `/Users/jochen/src/pi-mono/packages/coding-agent/src/core/sdk.ts` (422L — real SDK consumed by main.ts)
- `/Users/jochen/src/pi-mono/packages/agent/src/harness/agent-harness.ts` (995L — runs the actual loop)

## Summary

`cli.py`, `runner.py`, and `models.py` are the strongest part of the fork: small, dataclass-driven, with a clean recorder/event-sink separation that pi-mono does not have a direct equivalent for. The damage is concentrated in three places. First, the CLI has the structural shape of pi-mono — one giant module with three subcommands and a flat `try`/`except` ladder — but reproduces every provider list four times (two `choices=[...]`, one `if provider in {...}` set, one `SUPPORTED_NATIVE_PROVIDERS` frozenset, one if/elif factory) and has dead trailing `parser.error` code that argparse's `required=True` makes unreachable. Second, the adapter layer presents three near-parallel `PipyNative*Adapter` classes that share the name string `"pipy-native"` but are not related by inheritance and largely re-implement the same prepare/select/run scaffolding; `SubprocessAdapter` is a different shape entirely, used only by tests and an undocumented `pipy run --agent <something-else>` path that the docs call a "support path." Third, capture/event semantics drift from the names: `record_argv`/`record_stdout`/`record_stderr`/`import_raw_transcript` are public `CapturePolicy` fields that are never flipped to `True` by any caller in src/ or tests/ — they exist purely to be archived as `False` strings, which is exactly the "abstractions with one (false) entry" smell. The `HarnessStatus` enum has `PENDING` and `RUNNING` members; `PENDING` is never used and `RUNNING` exists only as an intermediate label inside the runner's first event payload. The lifecycle ordering itself is broadly correct but leans on a `try`/`except Exception` envelope with no `finally`, so a recorder-write failure during the failure-event emit will skip finalization.

## Findings

### F1: Three near-parallel native adapter classes share a name but no base class
- **Where**: `src/pipy_harness/adapters/native.py:39-129` (`PipyNativeAdapter`), `132-226` (`PipyNativeReplAdapter`), `229-406` (`PipyNativeToolReplAdapter`)
- **Symptom**: All three classes hardcode `name = "pipy-native"` (L42, L135, L244). All three define a near-identical `prepare()` (expanduser/resolve/exists/is_dir checks + `if request.command: raise`) at L57-77, L158-176, L288-308. All three define a near-identical `_current_selection()` and a near-identical `run()` that ends in an `AdapterResult(...)` with the same metadata keys. There is no shared base class, no shared helper. The `EventSink`/`AgentPort` protocols in `adapters/base.py` advertise polymorphism, but at the CLI dispatch site `cli.py:335` types it as `repl_adapter: PipyNativeReplAdapter | PipyNativeToolReplAdapter` — i.e. the caller statically knows which variant it asked for.
- **Article principle**: Volume = noise; abstractions with one (or three near-clones) implementation.
- **Pi comparison**: pi-mono runs everything through one `AgentHarness` (`packages/agent/src/harness/agent-harness.ts`, 995L) parameterized by mode. The dispatch is at `main.ts:680-721` (`runRpcMode` / `InteractiveMode` / `runPrintMode`), not via three sibling adapter classes.
- **Suggested fix**: Collapse to one `PipyNativeAdapter` with `mode: Literal["run", "no_tool_repl", "tool_loop_repl"]`, factor the shared `prepare()` validation into a single helper, and stop pretending these are independent ports.
- **Severity**: high

### F2: `AgentPort` protocol exists for a polymorphism that no caller exercises
- **Where**: `src/pipy_harness/adapters/base.py:24-41`, used at `runner.py:88` (`adapter: AgentPort`) and `cli.py:511` (`-> SubprocessAdapter | PipyNativeAdapter`)
- **Symptom**: The protocol claims to abstract "concrete agent adapters." In practice the runner is always called with one of the four concrete classes that the CLI itself constructed in the same call. Tests construct adapters directly. The SDK (`sdk.py:120`) constructs `PipyNativeAdapter` directly. No production code branches on `adapter.name`; the runner just records it as metadata. `SubprocessAdapter` and `PipyNativeAdapter` do not share a useful behavior: subprocess has `Popen` lifecycle handling, native runs through `NativeAgentSession`. They are not interchangeable from the user's perspective either — the CLI gates them on `args.agent`.
- **Article principle**: Ports-and-adapters theatre; abstractions with effectively one consumer per implementation.
- **Pi comparison**: pi-mono has no equivalent dispatcher protocol; it builds an `AgentSession` then calls `runPrintMode`/`runRpcMode`/`InteractiveMode` directly.
- **Suggested fix**: Drop `AgentPort` and let the runner take a single concrete dependency, or — if you want to keep one seam — keep only `AgentPort` and delete `EventSink` (or vice versa).
- **Severity**: high

### F3: Provider list duplicated four times across cli.py
- **Where**: `cli.py:88-101` (run subcommand `choices=`), `cli.py:167-180` (repl subcommand `choices=`), `cli.py:515-527` (`if native_provider in {...}` real-provider set), `cli.py:802-849` (`_native_provider_for_selection` if/elif chain). The canonical set lives at `native/repl_state.py:19-34` as `SUPPORTED_NATIVE_PROVIDERS`.
- **Symptom**: Adding a provider requires editing five places. Three of them are already inconsistent: the `choices=` lists must match `SUPPORTED_NATIVE_PROVIDERS` but are typed by hand; the L515 set covers the same 11 names again to gate `--native-model` requirement; the dispatch chain at L802 has no compile-time check against the choices.
- **Article principle**: Plausible-but-wrong (these can drift without a test catching it); volume = noise.
- **Pi comparison**: pi-mono's `ModelRegistry` is the single canonical source (`packages/coding-agent/src/core/model-registry.ts`); the CLI accepts any string and resolves through the registry (`main.ts:306-327`).
- **Suggested fix**: Build `choices` from `sorted(SUPPORTED_NATIVE_PROVIDERS)` at parser construction. Replace L515 set with `provider != "fake"`. Replace L802 if/elif with a `dict[str, Callable[[str], ProviderPort]]` table whose keys are also asserted equal to `SUPPORTED_NATIVE_PROVIDERS - {"fake"}` at import time.
- **Severity**: high

### F4: Unreachable trailing `parser.error("unknown command")` after the dispatch ladder
- **Where**: `cli.py:406-407`
- **Symptom**: `subparsers.add_subparsers(dest="command", required=True)` (L57) means `argparse` exits the process if `args.command` is missing or unknown. The three `if args.command == "auth"/"run"/"repl"` branches in `main()` are exhaustive. The final `parser.error("unknown command"); return 2` is dead code — `parser.error` calls `sys.exit(2)` internally and never returns. The `return 2` after it is doubly dead.
- **Article principle**: AI slop aesthetics; defensive code for an impossible state.
- **Pi comparison**: pi-mono's `main.ts` returns/exits inline; there is no trailing "unknown command" branch.
- **Suggested fix**: Delete L406-407, or replace with `raise AssertionError(f"argparse let unknown command through: {args.command}")` if you really want a tripwire.
- **Severity**: low

### F5: `CapturePolicy` fields that are never set to True
- **Where**: `src/pipy_harness/capture.py:18-26` defines `record_argv`, `record_stdout`, `record_stderr`, `import_raw_transcript`, `workspace_path_mode`. Search across `src/` and `tests/` confirms none of `record_argv=True`, `record_stdout=True`, `record_stderr=True`, `import_raw_transcript=True` are ever assigned. `runner.py:338-341` reads them only to archive `"<x>_stored": False` strings.
- **Symptom**: A dataclass that exists to be serialized as a fixed metadata stamp. The only flag that ever flips is `record_file_paths` (in `cli.py` via `--record-files` and in `sdk.py`).
- **Article principle**: AI slop — abstractions with one (false) implementation; "for future X" code.
- **Pi comparison**: pi-mono does not surface "I will not store stdout" as a struct field; it just doesn't store stdout.
- **Suggested fix**: Reduce `CapturePolicy` to `record_file_paths: bool = False`. Inline the four `False` literals where the metadata is built. If you want the metadata stamp for forensics, build it as a constant `_PARTIAL_CAPTURE_FALSE_STAMP` dict.
- **Severity**: medium

### F6: `HarnessStatus.PENDING` is never used; `RUNNING` is used only once
- **Where**: `src/pipy_harness/models.py:19-23`; `runner.py:128` uses `HarnessStatus.RUNNING` to label the started-event payload, then never again.
- **Symptom**: A five-state enum where only three states (`SUCCEEDED`, `FAILED`, `ABORTED`) are terminal outputs and only one of the remaining two ever appears in code. `PENDING` is dead.
- **Article principle**: Make impossible states impossible — currently `RunResult.status` is typed as `HarnessStatus` so a caller has to handle `PENDING` and `RUNNING` defensively even though the runner cannot produce them.
- **Pi comparison**: pi-mono's `assistantMessage.stopReason` is `"end_turn" | "tool_use" | "max_tokens" | "aborted" | "error"` (`packages/ai/src/types.ts`) — a closed terminal vocabulary.
- **Suggested fix**: Split into `RunStatus = Literal["succeeded","failed","aborted"]` for terminal results and a separate internal label for the "started" event payload (or just emit `"status": "running"` inline as a string).
- **Severity**: low

### F7: `session.finalized` event is emitted *before* `recorder.finalize()` runs
- **Where**: `runner.py:207-215` emits `"session.finalized"`, then L216-232 calls `self.recorder.finalize(...)`.
- **Symptom**: The event name promises that finalization happened. If `recorder.finalize` raises, the active record contains a `session.finalized` event but is never actually finalized — the next reader sees a partially-finalized file with a misleading terminal event. The summary text on L209 hedges this with "Session finalization requested" but the event type is the unconditional past-tense `session.finalized`.
- **Article principle**: Plausible-but-wrong; permissive error handling is a smell — here it's absent error handling that hides behind an optimistic event.
- **Pi comparison**: pi-mono's session writer is synchronous; there's no analogous "promise of finalization" event.
- **Suggested fix**: Rename to `session.finalization.requested` (match the summary), or move the emit after `self.recorder.finalize` returns successfully.
- **Severity**: medium

### F8: No `finally` around finalization — finalize is skipped if a sink-write raises in the failure path
- **Where**: `runner.py:120-232`. The `try`/`except KeyboardInterrupt`/`except Exception` block at L120-205 catches and emits failure events, but those emits go through `sink.emit` (L195-205) which can itself raise (`recorder.append` does file I/O). If that happens, control escapes `main.run` without `recorder.finalize` running, leaving the session permanently in active state.
- **Symptom**: The lifecycle that the docs claim is "robust by construction" is actually robust only when `recorder.append` never fails inside the exception handler.
- **Article principle**: Permissive error handling is a smell; make bad states impossible.
- **Pi comparison**: pi-mono uses session-write queues with explicit `pendingSessionWrites` tracking (`agent-harness.ts` `PendingSessionWrite` type) — write failures are surfaced, not silently dropped.
- **Suggested fix**: Wrap L120-215 in `try`/`finally`, move `self.recorder.finalize(...)` into the `finally` so it always runs. Catch and log (to stderr) sink-write failures from inside the error-path emits.
- **Severity**: medium

### F9: `_resolve_repl_mode` instantiates a provider just to probe `supports_tool_calls`
- **Where**: `cli.py:605-633`
- **Symptom**: To resolve `--repl-mode auto`, `_resolve_repl_mode` calls `_native_provider_for_selection(selection)` which actually constructs a provider (which may do network/IO at import or `__init__` time for some providers — e.g. `OpenAICodexResponsesProvider`). The result is discarded after one attribute read. A bare `except Exception:` (L629) then silently falls back to `"no-tool"`, hiding genuine misconfiguration.
- **Article principle**: Permissive error handling is a smell; over-engineering.
- **Pi comparison**: pi-mono's `model.api.supportsTools` is a static capability on the registry entry — no constructor side-effect.
- **Suggested fix**: Put `supports_tool_calls` capability on a static table keyed by provider name (the same table that drives `_native_provider_for_selection`). Then `_resolve_repl_mode` is `TABLE[provider].supports_tool_calls` — no instantiation, no try/except.
- **Severity**: medium

### F10: Inconsistent eager vs lazy provider imports in the dispatch table
- **Where**: `cli.py:21-40` imports `OpenAICodexResponsesProvider`, `OpenAIResponsesProvider`, `OpenRouterChatCompletionsProvider`, `FakeNativeProvider` eagerly. `cli.py:805-846` imports `OpenAIChatCompletionsProvider`, `AnthropicProvider`, `GoogleGenerativeAIProvider`, `GoogleVertexProvider`, `MistralProvider`, `AmazonBedrockProvider`, `AzureOpenAIResponsesProvider`, `CloudflareWorkersAIProvider` lazily.
- **Symptom**: The split is arbitrary — there is no documented reason why `openai` is eager but `anthropic` is lazy. CLI startup pays for openai-codex even if the user is invoking anthropic. Every new provider has to pick a side.
- **Article principle**: Volume = noise; AI slop aesthetics.
- **Pi comparison**: pi-mono lazily imports provider SDKs through the `ai` package's model resolver — uniform, not per-provider taste.
- **Suggested fix**: Pick one: either all eager (and accept the import cost) or all lazy (with a registry that maps `"anthropic" -> "pipy_harness.native.anthropic_provider:AnthropicProvider"`).
- **Severity**: low

### F11: `pipy auth` has only one terminal verb (`login`), no `logout` or `status`
- **Where**: `cli.py:59-74`. The `auth` subparser nests `openai-codex` which nests only `login`. The REPL has `/logout openai-codex` (`native/session.py:295,1046`), but the CLI does not expose it.
- **Symptom**: Asymmetric surface. `pipy auth openai-codex login` works; `pipy auth openai-codex logout` does not. A user who logged in via CLI cannot log out the same way; they must enter the REPL just to invoke `/logout`. The `auth` subparser also has only one provider (`openai-codex`) so the two extra dispatch levels are single-entry registries.
- **Article principle**: AI slop aesthetics; registries with one entry.
- **Pi comparison**: pi-mono auth is managed via `AuthStorage` and `migratedAuthProviders` plus interactive flows, with no `pi auth ...` subcommand tree.
- **Suggested fix**: Either flatten to `pipy login [--provider openai-codex]` / `pipy logout [--provider openai-codex]`, or add a real `logout` action so the asymmetry resolves. Either way, stop nesting `auth_provider` when there's exactly one.
- **Severity**: medium

### F12: `parser.parse_args(["repl"])` silently substituted when argv is empty
- **Where**: `cli.py:259-262`
- **Symptom**: Calling `pipy` (no args) implicitly becomes `pipy repl`. This is documented in `architecture.md:134` but is a footgun: `pipy --help` works (argparse) but `pipy` does *not* print help; it starts a REPL. A user typing `pipy` to see "what does this do" instead lands in an interactive provider session with whatever default model is configured.
- **Article principle**: Plausible-but-wrong — convenient for the author, surprising to a first-time user.
- **Pi comparison**: pi-mono's `cli.ts:20` calls `main(process.argv.slice(2))`; bare `pi` also defaults to interactive (`main.ts:109`), so this is a deliberate parity match. Acceptable, but inherit pi's `--help` precedence.
- **Suggested fix**: If you keep the default-to-repl behavior, also handle `--help`/`-h` at the top of `main()` before the empty-argv defaulting, so `pipy --help` from a fresh user works without first parsing `repl`.
- **Severity**: low

### F13: `_native_command` accepts a phantom `--` separator that argparse already strips
- **Where**: `cli.py:444-453`
- **Symptom**: `run_parser.add_argument("native_command", nargs=argparse.REMAINDER, ...)` (L139) on argparse REMAINDER preserves a leading `--` in the captured list, so `_native_command` strips it (L445-446). Then the function double-validates: it raises if `agent == "pipy-native"` and command is non-empty, and raises if it's not pipy-native and the command is empty. The error message at L449 says "do not accept a command after --" but the user's command might also have been passed without a `--`.
- **Article principle**: Volume = noise; over-engineering for an argparse quirk.
- **Pi comparison**: pi-mono has no equivalent "run a child agent process" subcommand — the whole `--agent codex|claude|pi` story is a pipy-only construct (docs/pi-parity.md L49 calls it "support path, not the product runtime").
- **Suggested fix**: If `SubprocessAdapter` truly is a "support path" not used by anyone, delete the `--agent` branch in `run` and just keep `pipy run --agent pipy-native`. That kills `_native_command`, `SubprocessAdapter`, and `agent`-vs-`pipy-native` branching across the file.
- **Severity**: medium

### F14: `SubprocessAdapter` is documented as "support path" and used only by tests
- **Where**: `src/pipy_harness/adapters/subprocess.py:17-92`. Production callers: `cli.py:550` (`return SubprocessAdapter()` when `--agent != pipy-native`). Test callers: `tests/test_harness_runner.py` (×7), `tests/test_harness_subprocess_adapter.py` (×4). `docs/pi-parity.md:49` admits it's "implemented as support path... not the product runtime."
- **Symptom**: An entire adapter class kept alive to demonstrate that the `AgentPort` protocol has more than one implementation. The CLI's `--agent codex|claude|pi` path that uses it is undocumented in `pipy --help` (the `--agent` flag is `required=True` and accepts any string). There's no CLI choice list constraining it; users discover this only by reading docs.
- **Article principle**: Abstractions with one real implementation; "for future X" code.
- **Pi comparison**: pi-mono never wraps a child agent process; it *is* the agent process.
- **Suggested fix**: Delete `adapters/subprocess.py` and the `--agent`-not-pipy-native branch. Update tests to use a fake adapter that exercises the runner contract directly. If you genuinely need lifecycle capture around foreign agents in the future, reintroduce it then.
- **Severity**: medium

### F15: `RunResult.metadata: dict[str, Any] | None` plus `dict[str, Any]` event payloads carry the typing hole
- **Where**: `src/pipy_harness/models.py:65,79` (`metadata: dict[str, Any] | None`), `runner.py:283-308` (`_initial_session_fields`, `_event_payload_metadata` return `dict[str, Any]`)
- **Symptom**: Anything that crosses the recorder boundary lives in `dict[str, Any]`. `RunResult.metadata` is `Optional` — but it's only `None` when `adapter_result is None`, which happens only on early failure. The CLI then does `metadata or {}` (`cli.py:457`) and `metadata.get("adapter")` defensively (L465). That's the optional-vs-empty-dict ambiguity the article calls out.
- **Article principle**: Make bad states impossible; permissive error handling is a smell.
- **Pi comparison**: pi-mono's `AgentHarnessOptions` and event payload types are strict TS shapes (`packages/agent/src/harness/types.ts`, 815L — actually typed); no opaque `Record<string, unknown>` blobs.
- **Suggested fix**: Replace `RunResult.metadata` with a typed dataclass (`provider: str | None`, `model_id: str | None`, `usage: SafeUsage`, plus the small set of known label fields). Drop the `Optional` — return an empty default if the adapter never produced one. Same for `AdapterResult.metadata` (L65 of models.py).
- **Severity**: medium

### F16: Event-name overload — `harness.run.failed` is emitted from two different code paths with different semantics
- **Where**: `runner.py:162-175` (happy path, `status == FAILED`), `runner.py:190-205` (exception path)
- **Symptom**: One event name is emitted both when the adapter ran cleanly and returned a non-success status, and when an exception escaped the adapter. The summary text differs ("Harness run finished" vs "Harness run failed before a native process result was available") but the type does not. Consumers cannot distinguish "adapter ran, exit_code != 0" from "adapter threw before producing a result." The `harness.run.completed` name is also misleading on the failure-status branch since the same code path emits `harness.run.failed` instead.
- **Article principle**: Plausible-but-wrong event vocabulary.
- **Pi comparison**: pi-mono's `AgentEvent` type has distinct events for streaming completion, abort, error (`packages/agent/src/types.ts`).
- **Suggested fix**: Use three distinct events: `harness.run.completed` (terminal success), `harness.run.failed` (adapter ran, status != succeeded), `harness.run.errored` (uncaught exception before adapter completion). Or include an `outcome_source: "adapter" | "harness_exception"` field on the payload.
- **Severity**: low

### F17: `runner.py` imports `Mapping`/`Protocol` from `typing` (deprecated location)
- **Where**: `runner.py:12` (`from typing import Any, Mapping, Protocol`). `adapters/base.py:5` (`from typing import Mapping, Protocol`). `models.py:9` (`from typing import Any, Sequence`).
- **Symptom**: Since Python 3.9 `Mapping`/`Sequence` should come from `collections.abc`; only `Protocol` and `Any` remain in `typing`. The rest of the codebase mostly does this correctly (`capture.py:10` uses `collections.abc` via `Mapping, Sequence` from `typing` too — actually inconsistent). Mixed style.
- **Article principle**: Volume = noise; codebase consistency.
- **Pi comparison**: not applicable.
- **Suggested fix**: Use `from collections.abc import Mapping, Sequence` consistently; keep only `Any`, `Protocol`, `Final`, etc. in `typing` imports.
- **Severity**: low

### F18: Dev-process labels leak into production docstrings ("slice 5", "slice 12")
- **Where**: `cli.py:611` ("Resolve the effective REPL mode for slice 12 of the parity track"), `adapters/native.py:232-242` ("Slice 5 of the Tool-Loop Parity Track wires this adapter..."), `adapters/subprocess.py:1` ("Generic subprocess adapter for the first pipy harness slice"), `capture.py` lacks the labels but the rest of the module docstrings reference parity track milestones.
- **Symptom**: Docstrings describe internal milestone names ("slice 5", "Tool-Loop Parity Track Slice 12") that mean nothing to a future reader and will be wrong six months after the parity track closes.
- **Article principle**: AI slop aesthetics — process notes that read like changelog entries leaked into reference docstrings.
- **Pi comparison**: pi-mono docstrings describe what the code does, not the PR milestone that introduced it.
- **Suggested fix**: Strip "Slice N of X" phrases. Keep behavior descriptions; move historical context to `docs/pi-parity.md` where it already lives.
- **Severity**: low

### F19: `_resolve_reference_roots` scans hard-coded doc files to auto-discover `~/`-paths
- **Where**: `cli.py:683-799` (`_AUTO_REFERENCE_ROOT_DOCS`, `_resolve_reference_roots`, `_scan_workspace_reference_roots`)
- **Symptom**: 115 lines of CLI helper to grep `docs/parity-criterion.md`, `docs/pi-parity.md`, `AGENTS.md` for `~/foo` patterns and inject those as read-only roots into the tool-loop adapter. The skip-list at L765 is the same `.claude/.codex/.local/.config/.pipy` set hard-coded twice (once in `_scan_workspace_reference_roots`, once in `_resolve_root_entries` defensively). It's a "scan some files I happen to know about" heuristic dressed up as configuration.
- **Article principle**: AI over-engineers; plausible-but-wrong (couples the CLI to specific doc filenames that only exist in this fork's parity-track moment).
- **Pi comparison**: pi-mono has no such heuristic; reference roots come from explicit flags or settings.
- **Suggested fix**: Require explicit `--read-root` or `PIPY_READ_ROOTS`; if no roots configured, just pass empty `reference_roots=()` and let the tool loop refuse paths outside the workspace. Delete the auto-discovery entirely.
- **Severity**: medium

### F20: `sdk.py` is a real but barely-used surface; `__init__.py` already re-exports the same primitives
- **Where**: `src/pipy_harness/sdk.py:1-125`, `src/pipy_harness/__init__.py:1-15`
- **Symptom**: `sdk.py` exists to provide `run_native` + `make_native_run_request` plus re-exports of `RunRequest`, `RunResult`, `HarnessStatus`, `CapturePolicy`, `HarnessRunner`, `ProviderPort`, `StreamChunkSink`. But `pipy_harness/__init__.py` already exports `RunRequest`, `RunResult`, `HarnessStatus`, `CapturePolicy`, `HarnessRunner`, `AdapterResult` at the top level. The only `sdk.py` consumer in the tree is `tests/test_sdk.py`. The docstring (L1-29) explicitly markets this as "the named SDK entry point" but its production usage is one test file.
- **Article principle**: Phantom API; abstractions with no real consumer.
- **Pi comparison**: pi-mono's `coding-agent/src/core/sdk.ts` (422L) is consumed by `main.ts:33,288-380` to build session options — it is the actual production glue. The pipy equivalent leaks into `cli.py` directly (`cli.py:298-310` builds `RunRequest` and runs `HarnessRunner` inline).
- **Suggested fix**: Either (a) refactor `cli.py:277-325` to call `sdk.run_native`/`sdk.make_native_run_request` so the SDK is the real glue, or (b) delete `sdk.py` and document that the SDK surface *is* `from pipy_harness import HarnessRunner, RunRequest, PipyNativeAdapter`. Don't keep both.
- **Severity**: medium

### F21: `print(run_output.final_text, file=sys.stdout)` in the adapter violates the "adapter doesn't own stdout" contract
- **Where**: `adapters/native.py:102-114`
- **Symptom**: `PipyNativeAdapter.run` writes the final text to `sys.stdout` directly (L107 `print(run_output.final_text, file=sys.stdout)` and L113 `sys.stdout.write("\n")`). This couples the adapter to a global stream and mixes presentation into a class whose docstring says it just runs a turn through a provider. The CLI itself also writes to `sys.stdout` (only at L324 for JSON output), but the textual stdout path runs through the adapter.
- **Article principle**: Plausible-but-wrong model — presentation in the model layer.
- **Pi comparison**: pi-mono's `runPrintMode` (in `modes/`) owns the stdout writes; adapters/sessions return strings.
- **Suggested fix**: Have `PipyNativeAdapter.run` return `final_text` through `AdapterResult.metadata` (or a typed `final_text` field) and let the CLI's `run` branch print it. Pass `sys.stdout` in explicitly if needed.
- **Severity**: medium

### F22: `record_file_paths` summary text fabricates a reason when no paths are found
- **Where**: `runner.py:385-392`
- **Symptom**: When `record_file_paths` is true and `changed_paths` is empty, the markdown summary says `"none collected because the run did not complete"` if `error_type` is set, else `"none"`. The first message is a guess — the adapter may have completed cleanly with zero changed paths *and* a previous error_type (e.g. KeyboardInterrupt after success). It's a heuristic dressed as a precise reason.
- **Article principle**: Plausible-but-wrong.
- **Pi comparison**: not applicable.
- **Suggested fix**: Just print `"- Changed file paths recorded: yes"` followed by `"  - none"` regardless of error state — the actual reason is encoded in the status/exit_code two lines up.
- **Severity**: low
