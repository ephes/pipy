# Audit: Tools Layer

Scope:
- `src/pipy_harness/native/tool.py` (archive-safe `ToolPort`)
- `src/pipy_harness/native/tools/__init__.py`
- `src/pipy_harness/native/tools/base.py` (contracts + hand-rolled JSON-schema validator)
- `src/pipy_harness/native/tools/messages.py`
- `src/pipy_harness/native/tools/read.py`
- `src/pipy_harness/native/tools/ls.py`
- `src/pipy_harness/native/tools/grep.py`
- `src/pipy_harness/native/tools/find.py`
- `src/pipy_harness/native/tools/write.py`
- `src/pipy_harness/native/tools/edit.py`
- `src/pipy_harness/native/tools/edit_diff.py`
- `src/pipy_harness/native/tools/truncate.py`
- `src/pipy_harness/native/tools/bash.py` (declared unregistered)
- `src/pipy_harness/native/read_only_tool.py` (shared validators + legacy bounded read tool)
- `src/pipy_harness/native/patch_apply.py` (legacy archive-side patch tool)
- `src/pipy_harness/native/verification.py` (legacy `just check` boundary)

Comparison:
- `packages/coding-agent/src/core/tools/{read,write,edit,edit-diff,bash,grep,find,ls,truncate,path-utils,tool-definition-wrapper}.ts`
- `packages/agent/src/{types.ts,harness/types.ts,harness/agent-harness.ts}`

## Summary

The tools layer carries roughly 4,800 lines of Python where pi-mono's equivalent is ~3,900 lines of TypeScript, and the extra weight is entirely defensive scaffolding rather than function. Two parallel `ToolPort` interfaces with the same name (`native/tool.py` versus `native/tools/base.py`) front two parallel tool families: one archive-side family (`read_only_tool.py`, `patch_apply.py`, `verification.py` — ~1,500 lines built around enums of "skip reasons" and `archive_metadata` dicts) that the model never sees, and a model-driven family (`tools/*.py`) that re-implements every constraint from scratch. The hand-rolled JSON-schema subset validator in `base.py` reinvents a small fraction of `pydantic`/`typebox` (~310 lines) while still letting each tool re-validate the same arguments at runtime, and the shared "validators" exported from `read_only_tool.py` are imported as private names (`_is_ignored_or_generated`, `_resolved_relative_label`, `_validate_workspace_relative_path`, `_is_relative_to`, `_CONTROL_CHARS`) by every model-driven tool. `truncate` is a registered model-visible tool that pi treats as an internal post-processing utility, `bash` is 391 lines of unregistered code shipped under "not safe yet", and there are two competing secret-detection functions (`looks_sensitive` substring matcher and `has_secret_shaped_content` regex matcher) applied in different places.

## Findings

### F1: Dual `ToolPort` Protocols sharing one name across two unrelated tool families

- **Where**: `src/pipy_harness/native/tool.py:10-18` and `src/pipy_harness/native/tools/base.py:253-269`
- **Symptom**: Two `class ToolPort(Protocol):` declarations, in two different modules, with two different `invoke()` signatures. `native/tool.py:ToolPort.invoke(self, request: NativeToolRequest) -> NativeToolResult` is the archive-side boundary (no context, archive-shaped result), while `native/tools/base.py:ToolPort.invoke(self, request: ToolRequest, context: ToolContext) -> ToolExecutionResult` is the model-driven loop boundary. Neither imports the other; nothing forbids a future tool from claiming to implement "ToolPort" without specifying which one. `tools/__init__.py:22` reexports only the model-driven one.
- **Article principle**: "Volume = noise"; "redundant abstractions". The duplicated name is the cleanest possible signal that two systems were built side-by-side without a unifying design.
- **Pi comparison**: pi-mono has exactly one tool boundary: `AgentTool<TParameters>` in `packages/agent/src/types.ts:361` and `ToolDefinition<TParameters, TDetails>` in `packages/coding-agent/src/core/extensions/types.ts`, related by `wrapToolDefinition` in `packages/coding-agent/src/core/tools/tool-definition-wrapper.ts:5-19`. No archive vs model split.
- **Suggested fix**: Pick one. The archive-side `NativeReadOnlyToolRequest`/`NativeExplicitFileExcerptTool`/`NativePatchApplyTool`/`NativeVerificationTool` family appears to be vestigial from the pre-model-driven design (it never returns a model-visible payload, and the model-driven `read`/`edit`/`grep` cover its functionality). Either rename one Protocol (e.g. `ArchiveToolPort` versus `ToolPort`) and clearly delete the unused one, or delete the entire `read_only_tool.NativeExplicitFileExcerptTool` / `patch_apply.NativePatchApplyTool` / `verification.NativeVerificationTool` stack and keep their helpers as plain functions for the model-driven tools that already use them.
- **Severity**: high

### F2: `read_only_tool.py` is two unrelated modules merged into one (~715 lines)

- **Where**: `src/pipy_harness/native/read_only_tool.py:1-716`
- **Symptom**: The file mixes (a) a legacy archive-side bounded-read tool (`NativeReadOnlyApprovalDecision`, `NativeExplicitFileExcerptReason`, `NativeReadOnlyGateDecision`, `NativeExplicitFileExcerptTarget`, `NativeInMemoryFileExcerpt`, `NativeExplicitFileExcerptResult`, `NativeExplicitFileExcerptTool`, `_ResultBuilder`, `_request_gate_reason`, `archive_metadata`, ~360 lines) with (b) a path/security toolkit (`_validate_workspace_relative_path`, `_is_ignored_or_generated`, `_resolved_relative_label`, `_matches_root_ignore`, `_is_relative_to`, `_byte_limit`, `_line_count`, `_source_label`, `_source_hash`, `has_secret_shaped_content`, `ResolvedToolPath`, `resolve_tool_path`, `_resolve_against_roots`, ~350 lines) used by every model-driven tool. Every model-driven tool imports the toolkit via private `_`-prefixed names, which is the lint-banned form of "this is internal but I depend on it".
- **Article principle**: "AI over-engineers"; "redundant abstractions" — the archive tool and its 18-value `NativeExplicitFileExcerptReason` enum exist in parallel with the simpler `tools/read.py:ReadTool` which collapses all of them to plain `is_error=True` strings.
- **Pi comparison**: pi-mono splits these cleanly: `packages/coding-agent/src/core/tools/path-utils.ts` (118 lines, only path-resolution) and `packages/coding-agent/src/utils/paths.ts`. There is no archive-side read tool — read is one shape, with one schema.
- **Suggested fix**: Either extract `path-utils.py` from `read_only_tool.py` and delete the archive-side `NativeExplicitFileExcerptTool` family, or — if the archive-side tool is genuinely required for the metadata-only event stream — keep it isolated in its own module. The current "everyone imports `_foo` from a 700-line file" arrangement is the canonical AI-slop layout.
- **Severity**: high

### F3: Hand-rolled JSON-schema subset validator reinvents typebox

- **Where**: `src/pipy_harness/native/tools/base.py:48-60, 283-590` (constants, `validate_arguments`, `_validate_schema_shape`, `_validate_value`, `_validate_object`, `_validate_array`, `_validate_string`, `_validate_integer`, `_validate_boolean`)
- **Symptom**: ~310 lines implement a deliberately tiny subset of JSON-schema by walking a `Mapping` with a recursive helper, then re-checking the same subset at definition time. The docstring justifies it as "no `pydantic`, no third-party schema runtime". Every tool then re-validates the same arguments at runtime anyway (`tools/edit.py:101-112`, `tools/write.py:99-104`, `tools/bash.py:138-142`, `tools/grep.py:142-147`) because the schema doesn't express "non-empty after schema-level minLength" or the `replace_all` boolean default.
- **Article principle**: "AI over-engineers"; "make bad states impossible rather than handle them". Pi handles this by *not* writing a validator at all — it uses `typebox` to generate both the model-visible schema and the static input type, so the tool's `execute` already receives a typed value.
- **Pi comparison**: `packages/coding-agent/src/core/tools/read.ts:20-26` is `const readSchema = Type.Object({ path: Type.String(...), offset: Type.Optional(Type.Number(...)), limit: Type.Optional(Type.Number(...)) }); export type ReadToolInput = Static<typeof readSchema>;`. One line of validator wiring, full static + runtime guarantees, schema serializable for the provider.
- **Suggested fix**: Adopt `pydantic` v2 for the input shape (it ships with FastAPI/anthropic-sdk dependencies already), or `msgspec`, or — if the avoidance is religious — at least drop the runtime re-checks in each tool's `invoke`. The current "we validate twice and still need a third manual check" pattern is the worst of every option.
- **Severity**: high

### F4: Every model-driven tool re-imports four private helpers and re-runs the same validation prologue

- **Where**: `tools/read.py:21-26`, `tools/ls.py:20-23`, `tools/grep.py:25-31`, `tools/find.py:17-20`, `tools/write.py:18-23`, `tools/edit.py:16-22`, `tools/edit_diff.py:24-29` all import some subset of `_validate_workspace_relative_path`, `_is_relative_to`, `_resolved_relative_label`, `_is_ignored_or_generated`, `_CONTROL_CHARS`, `has_secret_shaped_content`, `resolve_tool_path`. The path-validation prologue in mutation tools is essentially identical: e.g. `tools/edit.py:114-127` and `tools/write.py:106-134` and `tools/edit_diff.py:134-147` each manually do `workspace = context.workspace_root.resolve(); candidate = (workspace / path_arg).resolve(); if not _is_relative_to(...) ...; resolved_label = _resolved_relative_label(...); if _is_ignored_or_generated(path_arg, workspace) or _is_ignored_or_generated(resolved_label, workspace) ...`.
- **Symptom**: Three duplicated copies of the same 12-line workspace-path prologue with the same `_error` shape. Read-only tools call `resolve_tool_path` (which centralizes the logic) but mutation tools bypass it because they only accept workspace-relative paths, so they re-implement a workspace-only variant inline.
- **Article principle**: "Redundant abstractions"; "volume = noise". The two paths express the same intent ("validate, resolve, gitignore-check") in two incompatible idioms.
- **Pi comparison**: `packages/coding-agent/src/core/tools/path-utils.ts:48-118` exposes one `resolveToCwd(filePath, cwd)` and one `resolveReadPathAsync(filePath, cwd)`. Both read and write call the same `resolveToCwd`; there is no separate "mutation path resolver".
- **Suggested fix**: Add `resolve_workspace_mutation_path(path_arg, *, workspace_root) -> ResolvedToolPath` that captures the prologue once. Each mutation tool then becomes the actual mutation logic and nothing else.
- **Severity**: medium

### F5: `truncate` is a registered model-visible tool; pi keeps it internal

- **Where**: `src/pipy_harness/native/tools/truncate.py:1-296`, registered at `src/pipy_harness/native/tool_loop_session.py:298, 309`
- **Symptom**: A 296-line model-visible tool whose entire purpose is "shrink some text the model already has in context". The model has to call `truncate(text=<huge string>, max_bytes=..., max_lines=...)` to receive a head+marker+tail composition. In practice the model already has the text in its context; the tool returns *more text* through the same context window. The promised invariant ("deterministic, no I/O") is correct, but the tool itself is a pure function that should not have been exposed.
- **Article principle**: "AI over-engineers"; "dead branches" — a tool that consumes context to produce slightly less context.
- **Pi comparison**: pi-mono's `packages/coding-agent/src/core/tools/truncate.ts` has the same head-truncation algorithm but it is consumed by `read.ts:302`, `grep.ts:334`, `find.ts:191`, `ls.ts:186`, `bash-executor.ts:16`, `output-accumulator.ts:5` — i.e. as an internal post-processing helper applied to *tool output before it reaches the model*, never as a tool the model invokes.
- **Suggested fix**: Delete `TruncateTool` from the production registry and the export list. Keep `_truncate_text` as a private utility that the read-only tools apply to their own output (today they each implement their own ad-hoc truncation in `read.py:147-153`, `ls.py:140-178`, `grep.py:201-207`, `find.py:182-189`).
- **Severity**: medium

### F6: 391-line `bash.py` shipped as "unregistered until a real sandbox exists"

- **Where**: `src/pipy_harness/native/tools/bash.py:1-392`, registry decision documented at `src/pipy_harness/native/tool_loop_session.py:287-290` ("`bash` is intentionally not registered")
- **Symptom**: A complete bash tool (with bounded buffers, selector-based output draining, `.git`-glob preflight, `shlex` token analysis, `_DENIED_SHELL_EXPANSION_MARKERS = ("$", "`")`) exists in the registered tool family alongside `bash.py:1-15` doc that the tool is *not* registered. The "preflight" guards (`_token_invokes_git`, `_token_mentions_dot_git`, `_token_globs_to_dot_git`) read defensively but the docstring at `tools/bash.py:351-358` admits "recursive readers can still enter sensitive directories at runtime". So 391 lines exist for a fence that doesn't fence.
- **Article principle**: "Volume = noise"; "permissive error handling is a smell"; "plausible-but-wrong" — the file presents itself as a working safety boundary while disclaiming exactly that.
- **Pi comparison**: pi-mono `packages/coding-agent/src/core/tools/bash.ts` ships *registered* (`name: "bash"` at `bash.ts:277`), backed by a real shell, and pi accepts the security/audit model that goes with that.
- **Suggested fix**: Either delete the file (preferred) and re-add a small one when a real sandbox arrives, or move it to `tools/_experimental/bash.py` so it cannot accidentally be imported by `production_tool_registry()`. The current pattern — 391 lines of "we know this isn't safe, but here's the code" — is exactly the volume-as-noise pattern the article calls out.
- **Severity**: high

### F7: `verification.py` over-engineers a single-command allowlist

- **Where**: `src/pipy_harness/native/verification.py:1-317`. Allowlist is `_JUST_CHECK_ARGV = ("just", "check")` at `:26`. The dispatch in `invoke` (`:161-205`) handles exactly that one tuple.
- **Symptom**: 316 lines to call `subprocess.run(("just", "check"), check=False, stdin/out/err=DEVNULL)`. The file defines `NativeVerificationApprovalDecision`, `NativeVerificationReason` (eight-label enum), `NativeVerificationGateDecision`, `NativeVerificationResult` (with `archive_metadata`, `__post_init__` that re-validates labels against the same enum, twelve `*_stored=False` flags that must remain false), `_ResultBuilder`, `_command_reason`, `safe_verification_command_label`, `_request_gate_reason`. The "allowlist" lives implicitly in `_JUST_CHECK_ARGV` plus a check in `_command_reason`. Every other element of the file enforces the invariant "we are only ever going to run `just check` and metadata cannot leak", but the invariant is already a function of being the only argv ever passed to `subprocess.run`.
- **Article principle**: "Make bad states impossible rather than handle them". The bad state being defended against (running a non-allowlisted command) is impossible by construction; the file is defending an invariant that follows from the line `subprocess.run(_JUST_CHECK_ARGV, ...)`.
- **Pi comparison**: pi-mono has no equivalent of this tool at all. Verification is the user running `just check` themselves.
- **Suggested fix**: If verification must stay, it is at most ~40 lines: `def run_just_check(workspace: Path) -> int: return subprocess.run(("just", "check"), cwd=workspace, ...).returncode`. The enum hierarchy and `archive_metadata` should be removed unless an actual archive event still depends on them — and even then, generated from the call site, not from a 316-line class.
- **Severity**: high

### F8: `patch_apply.py` is a parallel write/edit tool the model never invokes

- **Where**: `src/pipy_harness/native/patch_apply.py:1-457`, particularly `NativePatchApplyTool` at `:162-211` with its operation labels (`CREATE`, `MODIFY`, `DELETE`, `RENAME`) and gate decision authority `_HUMAN_REVIEWED_AUTHORITY = "pipy-owned-human-reviewed"` at `:31`.
- **Symptom**: A second mutation tool exists in the archive-side family with its own `NativePatchApplyApprovalDecision`, `NativePatchApplyReason` (sixteen-label enum), `NativePatchApplyResult` (`__post_init__` runtime-validates every storage flag at `:117-127`), `_PlannedOperation`, `_ResultBuilder`, `_plan_operation`, `_plan_create`, `_plan_modify`, `_plan_delete`, `_plan_rename`, `_apply_planned_operation`, `_existing_file_reason`, `_expected_hash_reason`, `_new_text_reason`, `_is_sha256`. The model-driven `WriteTool`/`EditTool`/`EditDiffTool` cover create/modify/delete/rename. The legacy `NativePatchApplyTool` is the same surface in archive-side dress.
- **Article principle**: "Redundant abstractions"; "AI over-engineers". The double `__post_init__` validation at `:117-127` re-checks at runtime that the enum values are enum values — already guaranteed by Python's type system.
- **Pi comparison**: pi-mono has no archive-side patch-apply tool. `edit.ts`, `write.ts`, `edit-diff.ts` are the entire mutation surface.
- **Suggested fix**: Audit whether `NativePatchApplyTool` is still invoked from any production code path. If not, delete it and its enums. If a `human-reviewed` apply boundary genuinely exists (e.g. for `/apply-proposal`), it should reuse `EditTool`/`WriteTool` and just wrap them with an approval check.
- **Severity**: high

### F9: Two competing secret-detectors with subtly different semantics

- **Where**: `src/pipy_harness/native/read_only_tool.py:539-575` defines `has_secret_shaped_content` (regex-based, stricter); `src/pipy_harness/capture.py:168-174` defines `looks_sensitive` (substring matcher on the words "api_key, apikey, secret, token, password, credential"). The two detectors are applied inconsistently: model-driven `tools/read.py:144`, `tools/grep.py:331` use `has_secret_shaped_content`. Archive-side `read_only_tool.py:326, 441, 530` and `patch_apply.py:439` use `looks_sensitive`. `_validate_workspace_relative_path` at `read_only_tool.py:441` rejects path *parts* whose name contains the substring "token" — meaning model-driven tools that route through `resolve_tool_path` will reject e.g. `auth/token_refresh.py` paths as "must not look sensitive".
- **Symptom**: Plausible-but-wrong. `tools/read.py:144` won't refuse a doc that mentions "token" (good), but `resolve_tool_path` will refuse to *find* the file `docs/auth-token.md` (bad) because the *path part* still goes through `looks_sensitive`. The comment in `read_only_tool.py:556-570` explicitly notes the substring matcher is overly broad for documents — yet still applies it to paths.
- **Article principle**: "Plausible-but-wrong code".
- **Pi comparison**: pi-mono does not refuse content by secret detection inside the tools layer at all; secrets are a capture/audit concern, not a read/grep concern.
- **Suggested fix**: Pick one detector and apply it consistently, *or* drop secret refusal from the model-driven read path entirely (the user can always grep their own repo) and keep `looks_sensitive` only for capture-side argv/env scrubbing. The bandaid in `_validate_workspace_relative_path:441` (rejecting path parts that contain "token") should be deleted; it produces real false positives.
- **Severity**: medium

### F10: Argument runtime re-validation after schema validation

- **Where**: `tools/edit.py:101-112`, `tools/edit_diff.py:127-132`, `tools/grep.py:142-147`, `tools/find.py:95-113`, `tools/write.py:99-104`, `tools/bash.py:138-142, 155-164`, `tools/truncate.py:108-120, 142-157`
- **Symptom**: Every tool re-checks `isinstance(old_string, str) and old_string` (`edit.py:101`), `isinstance(pattern, str) and pattern` (`grep.py:142`, `find.py:95`), `isinstance(command, str) and command.strip()` (`bash.py:138`), even though `tools/base.py:validate_arguments` already ran with the same minLength/type checks from the schema. The `find` tool then *additionally* checks `pattern.startswith("/")`, `"\\" in pattern`, `".." in PurePosixPath(pattern).parts` (`find.py:101-113`) — i.e. a third validation pass for the same field.
- **Article principle**: "AI over-engineers"; "make bad states impossible rather than handle them".
- **Pi comparison**: `packages/coding-agent/src/core/tools/find.ts:19-25` declares the schema and `execute()` receives a typed value with no re-checks. Path-shape constraints that don't fit in JSON-schema are not modelled at all — pi prefers a smaller surface.
- **Suggested fix**: Move runtime-only invariants (`pattern.startswith("/")`, the "no `..`" check) into the schema (via `pattern` regex constraints, or `not_starting_with_slash`) or drop them entirely; the `_validate_workspace_relative_path` call already happens for any path-shaped argument that flows through `resolve_tool_path`. Remove the `isinstance(x, str)` and `not x` checks — the schema validator already guarantees both.
- **Severity**: medium

### F11: `_validate_workspace_relative_path` is over-eager and not used at the same layer it lives in

- **Where**: `src/pipy_harness/native/read_only_tool.py:421-442`. Used by archive-side (`NativeExplicitFileExcerptTarget:130-134`, `patch_apply.py:294`), and by model-driven mutation tools (`tools/write.py:94`, `tools/edit.py:96`, `tools/edit_diff.py:122`), and indirectly by `resolve_tool_path:665`.
- **Symptom**: The function: rejects empty/whitespace, shell expansion markers (`~`, `$`, `` ` ``, `*`, `?`, `[`, `]`, `{`, `}`, `|`, `;`, `&`, `<`, `>`), backslashes, NULs, ASCII control chars, both POSIX and Windows absolute paths, leading `./`, any `..`, any path *part* that `looks_sensitive` substring-matches. Several of these (`$`, `` ` ``, glob chars) are not problems on the receiving end: `(workspace / value).resolve()` does not expand globs or shell substitutions. The `looks_sensitive` rejection at `:441` makes `auth_token.py` an invalid *path*. Eleven different `raise ValueError` cases, all bundled into one helper, all caught and reformatted as `ToolArgumentError("path", str(exc))` by the caller.
- **Article principle**: "Defensive validation at every level"; "permissive error handling is a smell" (the inverse: paranoid validation that creates false positives is the other side of the same coin).
- **Pi comparison**: `packages/coding-agent/src/utils/paths.ts` does basic normalization, expansion of `~`, and Unicode NFD/NFC variants — but not shell-expansion-marker refusal. Pi assumes the cwd-bounded `path.resolve()` is the boundary, not the path text shape.
- **Suggested fix**: Split this helper into two: `validate_path_shape` (NULs, control chars, backslashes, absolute) — actual must-haves — and the rest (shell markers, `looks_sensitive`, `./`-prefix) demoted to either a separate "policy" pass or removed. Removing `looks_sensitive` at the path-shape level is the highest-priority fix; it produces false positives today.
- **Severity**: medium

### F12: `tools/base.py` definition validation duplicates schema-shape checks at runtime

- **Where**: `src/pipy_harness/native/tools/base.py:106-126` (`ToolDefinition.__post_init__`), plus `_validate_schema_shape:315-396`.
- **Symptom**: At `ToolDefinition` construction time, `__post_init__` runs `_validate_schema_shape(self.input_schema, top_level=True)` which traverses the schema validating its own *shape* — checking that `type` is in the supported set, that `properties` is a mapping, that `required` is a list of strings, that `additionalProperties` is bool, that string `enum` is a list of strings, that integer bounds are ints. Then `validate_arguments:283-312` runs at call time and walks the same schema again. The schema-shape checks at definition time exist only to catch programmer errors at module import — but every `ToolDefinition` in the codebase is hand-written code in the same package, so the "program-time validation of program-author data" is paid for at every process start.
- **Article principle**: "Defensive validation at every level".
- **Pi comparison**: `typebox` validates schema shape at *type-check* time via TypeScript, not at runtime.
- **Suggested fix**: Either drop `_validate_schema_shape` and let `validate_arguments` surface the same errors at first invocation, or run it under an `if __debug__:` guard so it disappears in `python -O`. The current "every import re-traverses every tool's schema" is pure import-time waste.
- **Severity**: low

### F13: Inconsistent symlink-resolved relative-label check across tools

- **Where**: `tools/read.py:115-118` uses `_is_ignored_or_generated(resolved.relative_label, resolved.root)` once. `tools/edit.py:121-123`, `tools/write.py:113-119`, `tools/edit_diff.py:141-147` each check *both* the model-supplied label and the post-resolve label: `_is_ignored_or_generated(path_arg, workspace) or _is_ignored_or_generated(resolved_label, workspace)`. `tools/ls.py:144-155` and `tools/grep.py:288-298, 354-377` apply the resolved-label check inside their per-entry walk loops.
- **Symptom**: Three different patterns for "is this resolved path inside `.git` or `.gitignore`-matched?". The double check in mutation tools is intended to catch the "model gave us `foo` but `foo -> .git/config`" case. The read tool relies on `resolve_tool_path` already having `.resolve()`'d the candidate, so its single check is symlink-safe. But there is no docstring explaining the difference, so a future tool author has three precedents to copy from.
- **Article principle**: "Plausible-but-wrong"; the inconsistency means future tools will likely use the wrong pattern by accident.
- **Pi comparison**: pi-mono does not implement default-deny `.git` at all; it relies on the user setting up `fd`/`rg` to skip `.git` via tool flags.
- **Suggested fix**: Centralize in a single helper `is_ignored_path(*, model_label: str | None, resolved_path: Path, root: Path) -> bool` that does the symlink-safe check once. Replace all six call sites.
- **Severity**: medium

### F14: `tools/messages.py` re-declares `ToolResultMessage` whose shape is identical to `ToolExecutionResult`

- **Where**: `src/pipy_harness/native/tools/messages.py:78-129` and `src/pipy_harness/native/tools/base.py:166-208`
- **Symptom**: `ToolResultMessage` has fields `tool_request_id`, `output_text`, `is_error`, `provider_correlation_id`, `OUTPUT_TEXT_MAX_LENGTH = 64*1024`. `ToolExecutionResult` has exactly the same four fields and the same length cap. The docstring at `messages.py:80-91` explains "this is the serialization view of a tool result and is deliberately distinct from `ToolExecutionResult` (the tool boundary return value the loop already has in memory)". The loop must construct one from the other (`messages.py:90-91`) — a no-op copy.
- **Article principle**: "Redundant abstractions"; "volume = noise".
- **Pi comparison**: pi-mono has one type for tool output (`{content: TextContent[], details?: T}`) that flows through both the tool boundary and the provider message envelope.
- **Suggested fix**: Drop `ToolResultMessage`. The envelope can carry `ToolExecutionResult` directly. If a serialization view truly differs from the boundary type, it should differ in fields, not just in name.
- **Severity**: medium

### F15: `EditDiffTool` reimplements unified-diff parsing and application from scratch

- **Where**: `src/pipy_harness/native/tools/edit_diff.py:258-495`
- **Symptom**: ~240 lines of hand-written unified-diff parser (`_parse_unified_diff`, `_parse_hunk_header_and_body`, `_parse_hunk_header`, `_parse_range`, `_apply_hunks`, `_lines_equal`, `_atomic_write`) plus `_Hunk`, `_DiffParseError`. The parser handles edge cases like "\\ No newline at end of file" markers, optional trailing tab+timestamps in headers, `keepends`-vs-no-`keepends` differences. Each branch is plausible.
- **Article principle**: "AI over-engineers"; "plausible-but-wrong" — hand-rolled patch parsers are notoriously fragile (CRLF, missing newline at EOF, overlapping hunks, fuzzy context).
- **Pi comparison**: pi-mono's `packages/coding-agent/src/core/tools/edit-diff.ts` (454 lines) is also custom but in a typed language with thorough tests in the same package. The Python rewrite has no equivalent test coverage that this audit could surface.
- **Suggested fix**: Two options: (1) use the stdlib `difflib`/`patch` semantics via `unidiff` (pure Python, ~300 lines as a dependency); (2) keep `EditTool` (which does string-replace) as the only mutation primitive and drop `EditDiffTool` entirely. Multi-edit can be expressed as a list of `EditTool` invocations. The "hand-rolled patch" + "minimal pipy" pairing is the worst combination.
- **Severity**: medium

### F16: `ResolvedToolPath` carries five fields, three of them derived

- **Where**: `src/pipy_harness/native/read_only_tool.py:586-607`
- **Symptom**: `ResolvedToolPath(resolved, root, relative_label, display_label, is_workspace)`. `relative_label = resolved.relative_to(root).as_posix()`, `display_label = relative_label if is_workspace else f"{root.name}/{relative_label}"`, `is_workspace = (root == workspace)`. All three of `relative_label`, `display_label`, `is_workspace` are pure functions of `resolved` and `root`.
- **Article principle**: "Volume = noise"; redundant state to maintain in lock-step.
- **Pi comparison**: pi-mono's resolution returns just `string` (an absolute path) from `resolveToCwd` — no record type.
- **Suggested fix**: Reduce to `ResolvedToolPath(resolved, root)` with `@property` accessors for the derived labels. Or just return `tuple[Path, Path]` and compute labels at the call site.
- **Severity**: low

### F17: Mutation tools fold archive-event semantics into the model-driven tool boundary via `stderr_sink`

- **Where**: `src/pipy_harness/native/tools/base.py:212-250` (`ToolContext.stderr_sink`), used at `tools/write.py:141-143`, `tools/edit.py:182-183`, `tools/edit_diff.py:200-201`.
- **Symptom**: `ToolContext` carries an optional callable that mutation tools invoke with a unified diff. The docstring at `base.py:217-221` says "diffs never cross [the archive boundary] from inside the tool". So `stderr_sink` is the path for diffs to escape the tool — but only sometimes (defaults to None, which means diffs are silently discarded; `write.py:142` only calls it `if context.stderr_sink is not None and diff_text`). Whether the model-visible loop ever sees the diff depends on whether the harness wired the sink.
- **Article principle**: "Permissive error handling is a smell" — silently dropping output when the sink is unset is the canonical "be liberal in what you accept" failure mode.
- **Pi comparison**: pi-mono's edit/write tools return the diff as `details: { diff: string, patch: string }` in the tool result (`edit.ts:60-67`). Diff visibility is part of the result type, not a context callback.
- **Suggested fix**: Either make the diff part of `ToolExecutionResult.output_text` or split it into a `details: dict | None` field on `ToolExecutionResult` (mirroring pi). Drop `stderr_sink` from `ToolContext`.
- **Severity**: medium

### F18: `find` uses `Path.glob` which already filters but tool re-filters per match

- **Where**: `src/pipy_harness/native/tools/find.py:158-181`
- **Symptom**: `matches = sorted(search_root.glob(pattern))` then for each `match`: re-resolve, re-`relative_to(root)`, re-check `_is_ignored_or_generated`, re-check `relative_prefix` containment. The "containment" check at `:172-176` is needed only because the `search_root.glob` result *may* return absolute paths if pattern starts with `/` — but the tool already rejected `pattern.startswith("/")` at `:101`. So the containment guard is dead.
- **Article principle**: "Dead branches".
- **Pi comparison**: pi-mono `find.ts` shells out to `fd` and filters per row only because `fd` may include directories outside the search root by default flags.
- **Suggested fix**: Delete the containment check at `find.py:172-176`. It cannot fire given the pattern guard at `:101`.
- **Severity**: low

### F19: `tools/find.py` re-validates `pattern` after schema validation, but redundantly

- **Where**: `src/pipy_harness/native/tools/find.py:95-113`
- **Symptom**: Schema declares `pattern` minLength=1 maxLength=512. `find.py:95-100` checks `not isinstance(pattern, str) or not pattern`. `find.py:101-106` checks `pattern.startswith("/") or "\\" in pattern`. `find.py:107-113` parses `PurePosixPath(pattern).parts` and rejects `..`. The first guard is dead (schema enforces it). The third guard could be in the schema as a regex constraint, or — more cleanly — could be enforced by `Path.relative_to(workspace)` after globbing, since the model is asking for *file paths* not arbitrary path expressions.
- **Article principle**: "AI over-engineers".
- **Pi comparison**: `packages/coding-agent/src/core/tools/find.ts:19-25` schema is three optional fields. No runtime re-validation of `pattern`.
- **Suggested fix**: Remove the `isinstance(pattern, str) or not pattern` check at `find.py:95-100`. Document the `..` and `/` rejections in the description and trust the schema otherwise.
- **Severity**: low

### F20: `bash._command_safety_error` uses `shlex.split` and `glob.glob` for security checks but documents that they don't actually secure

- **Where**: `src/pipy_harness/native/tools/bash.py:351-392`
- **Symptom**: The "preflight" tokenizes the command with `shlex.split(command, posix=True)`, then walks tokens checking for `_token_invokes_git`, `_token_mentions_dot_git`, `_token_globs_to_dot_git`. The function's own docstring at `:351-358` warns: "recursive readers can still enter sensitive directories at runtime. The model-visible production registry does not expose `bash`". So the function is for a tool that is documented as not registered, and the function admits it does not enforce the invariant it appears to.
- **Article principle**: "Plausible-but-wrong"; "AI over-engineers" — a defense that the author has documented does not defend.
- **Pi comparison**: pi-mono's bash assumes the shell is the trust boundary (real sandbox at the OS layer).
- **Suggested fix**: Delete the preflight function along with the rest of `tools/bash.py` (see F6). If `bash.py` survives, drop the preflight and let the documentation say "this is unsafe; do not register".
- **Severity**: medium

### F21: `ToolExecutionResult.OUTPUT_TEXT_MAX_LENGTH = 64 KB` enforces total length but tools individually cap at lower values inconsistently

- **Where**: `src/pipy_harness/native/tools/base.py:180` declares 64KB cap. `tools/read.py:43-47` declares `MAX_BYTE_LIMIT = 32*1024`, `MAX_CONTENT_BYTES = 256*1024`. `tools/grep.py:48` declares `max_output_bytes = 32*1024`, `HARD_MAX_OUTPUT_BYTES = 256*1024`. `tools/ls.py:42-43` declares `DEFAULT_MAX_ENTRIES = 200, HARD_MAX_ENTRIES = 1000` — entry-based, not byte-based.
- **Symptom**: Each tool picks its own byte/line/entry limit independently. The `ToolExecutionResult` 64KB cap is the outermost gate; some tools (`grep`, `read`) can produce 32KB which fits, but `ls` and `find` use entry caps that don't translate to byte caps and could in principle exceed 64KB on long path names. There is no test that the inner caps and outer cap are consistent.
- **Article principle**: "Defensive validation at every level" without coordination.
- **Pi comparison**: `packages/coding-agent/src/core/tools/truncate.ts:11-13` defines `DEFAULT_MAX_LINES = 2000`, `DEFAULT_MAX_BYTES = 50*1024` as shared constants. Every tool imports and uses them.
- **Suggested fix**: Promote a single set of bounds (e.g. `DEFAULT_MAX_BYTES`, `DEFAULT_MAX_LINES`) into `tools/base.py` and have every tool reference them. Or assert in `ToolExecutionResult.__post_init__` that `output_text` already fits all per-tool caps (it currently doesn't check).
- **Severity**: low

### F22: `BashTool` declares an output-buffering helper class but mixes mutable defaults with `frozen=True` dataclass

- **Where**: `src/pipy_harness/native/tools/bash.py:47-92` (the `BashTool` frozen dataclass with `__post_init__` that validates `default_timeout_seconds`, `max_timeout_seconds`, `max_stdout_bytes`, `max_stderr_bytes`) and `:319-339` (`_BoundedBuffer` with `slots=True` but not `frozen` because the buffer mutates).
- **Symptom**: A frozen `BashTool` dataclass owns four mutable bounds checked in `__post_init__`, then constructs a non-frozen `_BoundedBuffer(self.max_stdout_bytes)` per invocation. The inner buffer's append-only mutation contract (`:328-335`) is sound, but the pattern of frozen-outer/mutable-inner with validators on every numeric field is symptomatic of "frozen dataclass everywhere as a stylistic preference, not a correctness one".
- **Article principle**: "AI over-engineers".
- **Pi comparison**: pi-mono `bash.ts` does not introduce a `BoundedBuffer` class; it uses `OutputAccumulator` from `output-accumulator.ts:1-222` which is shared across tools.
- **Suggested fix**: Lift `_BoundedBuffer` to a shared `output_buffer.py` and use it from `grep`, `bash`, and any future stdout-capturing tool. Drop the `frozen=True` ceremony on `BashTool`/`GrepTool`/etc. if they have no real immutability story.
- **Severity**: low

### F23: Tool registries are nested in functions and re-imported each call

- **Where**: `src/pipy_harness/native/tool_loop_session.py:284-310` (`production_tool_registry()` does `from pipy_harness.native.tools.edit import EditTool` etc inside the function body).
- **Symptom**: Eight imports inside the function body, executed every time `production_tool_registry()` is called. Python caches module imports, so this is not a correctness issue — but it is symptomatic of "we are afraid of import cycles" rather than fixing the cycle.
- **Article principle**: Style smell, not severe.
- **Pi comparison**: `packages/coding-agent/src/index.ts:274` re-exports `truncateHead` at module top; pi tool wiring is module-top imports plus a single registry array.
- **Suggested fix**: Move the imports to the module top; if a real cycle exists, restructure (e.g. extract `production_tool_registry` to its own module).
- **Severity**: low

### F24: `ToolArgumentError` re-validates its own constructor inputs

- **Where**: `src/pipy_harness/native/tools/base.py:71-87`
- **Symptom**: `ToolArgumentError.__init__` checks `if not tool_name: raise ValueError("requires non-empty tool_name")` and `if not message: raise ValueError("requires non-empty message")`. These are caller-supplied — but every caller in the codebase passes a hard-coded tool name and a hard-coded message. The validation cannot fail at runtime; it can fail only at code-review time, where it should be caught by reading the code, not by a runtime exception that re-raises from inside another exception.
- **Article principle**: "Defensive validation at every level".
- **Pi comparison**: pi-mono's `throw new Error(...)` does not validate the error message.
- **Suggested fix**: Drop the two checks. If a future caller passes empty values, the resulting error string `tool_name: message` will simply show `: ` and that is an acceptable surface for what is itself an error path.
- **Severity**: low

---

## Cross-cutting observations

- The dual `ToolPort` (F1) and dual mutation tool family (F8) together account for ~1,200 lines of code that has no counterpart in pi-mono.
- Removing F1, F2, F5, F6, F7, F8 alone would cut the tools surface from ~4,800 to roughly ~2,800 lines — closer to pi-mono's footprint, with no loss of model-visible capability.
- The hand-rolled schema validator (F3, F10, F12) is the most invasive AI-slop signature in the layer: every tool re-validates the same fields, every schema is re-traversed at import, and the static-type win that `typebox` gives pi is absent here.
- The repeated `_validate_workspace_relative_path` over-eager checks (F11) plus the two competing secret detectors (F9) create real false positives that the audit could not enumerate but that the documentation in `read_only_tool.py:556-575` already acknowledges in passing.
