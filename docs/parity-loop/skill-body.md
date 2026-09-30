# Pipy Parity Loop — Workflow

This is the canonical body of the `pipy-parity-loop` skill. Each agent's
wrapper points here; do not duplicate this content into the wrappers.

Drive **one parity gap end to end**. Do not advance to another gap in a single
invocation — that outer loop is deferred (Phase 2). Reference checkout:
`~/src/pi-mono`. Work on trunk (`main`); do not create feature branches.

## Hard rules (read before starting)

- **Never self-grade.** Every review is a fresh, *different model family* context
  (implementer Opus → review with Pi/GPT; implementer GPT → review with Opus).
- **Review directly; never delegate the review.** The different-family reviewer
  must evaluate the supplied plan/diff in its own context. It must not spawn
  subagents, use Claude Code `Agent`/Task-style delegation, or fan out parallel
  reviewers. If the reviewer cannot complete the review directly within the
  provided bundle/context, treat that as `BLOCKED`, not CLEAN.
- **Never weaken or delete tests** to pass a gate.
- **The commit gate requires the last CLEAN review to cover the exact diff being
  committed.** If any fix (including docs) changes files after a CLEAN verdict,
  re-run `just check`, prek (only if a `.pre-commit-config.yaml` is present), and
  the review gate before committing.
- **Operator override is an escalation, not a pass.** A CLEAN different-family
  review is mandatory; nothing marks a gap "done" without one. The only override
  is when the reviewer CLI is genuinely *unavailable* after retries — and then
  you **stop**, record it in the run note, and surface to the operator. An
  override may never bypass an ISSUES verdict and may never mark a gap complete
  on its own.
- **Quota-constrained reviewer override is explicit and auditable.** If the
  operator explicitly sets `REVIEWER_AGENT=pi` or `REVIEWER_AGENT=opus` to avoid
  an unavailable or quota-constrained provider, run that external reviewer even
  when it cannot be verified as a different model family. This is an explicit
  operator tradeoff, not self-grading: the selected reviewer must still be a
  separate direct context, must return CLEAN, and may never bypass an ISSUES
  verdict. Record a `Caveat: quota-constrained reviewer override ...` line in
  the run evidence, including the implementer family if known and the selected
  reviewer.

## Reviewer selection

The review gate is configurable but must still satisfy the hard different-family
rule.

- `REVIEWER_AGENT` may be `auto`, `pi`, or `opus`; unset is `auto`.
- `REVIEWER_MODEL` may override the selected review harness model when that
  harness supports `--model`.
- `auto` selects by implementer family:
  - Claude/Opus-family implementer -> `pi-review-loop`.
  - GPT/Codex/Pi-family implementer -> `opus-review-loop`.
  - `pipy` implementer -> inspect the active native model/provider; if it is
    GPT/OpenAI-Codex-family, use `opus-review-loop`; if it is Claude/Opus-family,
    use `pi-review-loop`; if the family cannot be determined, stop as `BLOCKED`
    rather than guessing.
- Explicit `REVIEWER_AGENT=pi` forces the Pi review harness; explicit
  `REVIEWER_AGENT=opus` forces the Opus review harness. Before accepting an
  explicit override, verify it is still a different model family from the
  implementer when possible. If it is not different-family or cannot be verified
  as different-family, continue only under the quota-constrained reviewer
  override rule above and record the required caveat; otherwise stop as
  `BLOCKED`.

Use these commands for the selected gate, adding `--model "$REVIEWER_MODEL"`
when `REVIEWER_MODEL` is set:

```bash
# Pi/GPT-family reviewer
python3 ~/projects/agent-stuff/claude/skills/pi-review-loop/bin/pi-review-loop \
  --repo "$PWD" --run-dir "$(mktemp -d)/pi-review"

# Opus/Claude-family reviewer
python3 ~/projects/agent-stuff/codex/skills/opus-review-loop/bin/opus-review-loop \
  --repo "$PWD" --run-dir "$(mktemp -d)/opus-review"
```

## Phases

0. **Load context & drain the lesson backlog.** Read this body (it now carries
   applied improvements) and run
   `python3 scripts/parity_lessons.py list --status open`. **If the open-lesson
   count is ≥ the threshold (default 5), you MUST run the `parity-improve` skill
   to drain ALL open lessons to zero before selecting a new gap** — each open
   lesson must end `applied` or `rejected`. If the count is below threshold, note
   the open lessons and proceed; the run-end backstop drains the rest.
   *Done-when:* either the count was below threshold (noted, proceeding), or it
   met the threshold and `parity-improve` drove the open count to zero.
1. **Select the gap.** Read `docs/backlog.md` as the sole active task index.
   Use `docs/pi-mono-gap-audit.md` and `docs/parity-plan.md` as historical
   comparison evidence, not competing queues. Select one ready implementation
   gap from the index, or accept an operator-supplied gap. Read-only investigation
   tasks are not implementation gaps. If neither is available, report that there
   is no eligible task and stop; do not promote an audit into implementation or
   choose an old historical ranking automatically. Parity work is in
   maintenance mode (owner decision, 2026-09-30): prefer gaps with user-visible
   impact, such as new models or bugs found while dogfooding, over pixel or text
   equivalence, and stop when a slice would mainly add deviation lists. Confirm
   the gap is a single reviewable slice (decompose if not). *Done-when:* one
   named gap with a one-paragraph scope and the relevant
   `~/src/pi-mono` reference path(s), or an explicit report that no eligible
   implementation gap exists.
2. **Plan.** Read the pi-mono reference; write a short design/plan (what Pi does,
   how pipy matches it through pipy-owned Python boundaries, constraints from
   `AGENTS.md`). **Write the plan to a file** so it is reviewable. *Done-when:* a
   written plan file with done-when criteria. For metadata, auth, OAuth,
   provider, package manifest, extension API, or provider request-shape slices,
   the plan must pin the Pi reference field list, each field's optionality, any
   Pi-forced default values, whether those defaults diverge from the upstream API
   defaults, and any derived identifiers before implementation, so review catches
   preserved versus dropped future data or behavior instead of discovering it only
   after code exists. For extension API callback surfaces, also pin callback
   arity, ownership, disposal, and subscription lifetime from Pi before coding;
   e.g. `FooterData.onBranchChange` callbacks are zero-argument functions kept in
   a `Set` until disposed, so factory re-renders must not accidentally accumulate
   stale registrations or invoke callbacks with branch data Pi never supplies.
   For any provider request-shape slice that displaces or synthesizes a text block
   beside structured content such as a tool reference, pin and test the output
   matrix explicitly: empty string, whitespace-only, and nonblank text. Empty and
   whitespace-only output must not emit a sibling text block or disturb the valid
   structured content; nonblank output must remain present in its Pi-faithful
   position. Testing only `""` is insufficient because providers such as Anthropic
   also reject a whitespace-only text sibling.
   For any provider-native deferred-tool slice, define one materialization-
   eligibility predicate and use it both to remove a definition from the
   immediate request and to emit that definition at a later load marker. Include
   every provider-specific prerequisite in that predicate, such as a required
   provider correlation id: an incomplete marker that cannot materialize a tool
   later must leave it immediate rather than make it disappear. Pin this invariant
   in the plan and add tests for a missing prerequisite plus multiple ordered load
   markers with overlapping tool names, proving marker order and cross-marker
   first-load deduplication. When placing tool cache breakpoints, name up front
   how pipy's `tool_reference` deferred path differs from Pi's native-tool-changes
   path (Pi's `__pi_deferred_placeholder__` keeps the tool prefix stable when a
   deferred tool loads; pipy's first activation misses the cache once).
   For trust/provenance slices, inventory the concrete loader entry points and
   source shapes before specifying protected-resource detection or prompts.
   Protect only inputs the runtime can actually discover: a resource contributed
   through an installed package does not imply that a same-named standalone
   workspace directory is loadable. Pin each claimed source shape to its loader
   and add a test that exercises that exact path, so dead detectors cannot create
   trust prompts for inputs pipy would never consume.
   When an extension callback surface is available in headless JSON, RPC, or
   print modes, pin the no-UI behavior for every UI method, not only field
   presence or `has_ui`: selection/input calls must return immediately without
   reading stdin or writing protocol stdout, and any diagnostic notification
   path must be explicitly stderr-only.
   For any ordered resolver with a mode-specific final fallback, scope the mode
   matrix explicitly to that final branch. Pin the earlier precedence steps
   separately (such as explicit overrides, no-resource cases, extension
   decisions, saved decisions, and global defaults), so a broad row such as
   `headless = untrusted` cannot bypass a valid earlier result.
   For a request-shape field gated by a Pi compat flag or a thinking level, pin
   per field and per variant which flags gate it (each resolved independently),
   the detection-rung position, the value expression's `?? level` fallback, the
   three `off` states, and clamping, using the rules in
   `docs/parity-loop/porting-notes.md` ("Completions `thinkingFormat`").
   First locate where Pi computes each request-shape field: catalog/model-registry
   metadata, construction-time mapping, provider-local model-id logic, or a
   delegated SDK/runtime helper. Match that ownership boundary in pipy; do not
   add catalog fields or construction outputs for behavior Pi computes inside the
   provider. For provider-local logic, pin the exact Pi predicates, emitted values
   including sentinel values, and existing pipy intent fields to reuse before
   adding new cross-boundary state.
   When Pi delegates the concrete request shape to a vendored SDK or runtime
   helper, the Pi source file is not enough: inspect the delegated implementation
   under the reference checkout's `node_modules` (normally
   `~/src/pi-mono/node_modules`) or the relevant vendored/runtime path and pin
   the resulting host, URL path, auth header, and request-body fields in the plan
   with exact source citations. Do not infer those wire fields from wrapper call
   names alone.
   If a provider request-shape slice changes only selected fields in a larger Pi
   request path, label the pinned list as the fields this slice changes instead
   of "complete" unless it really is complete; explicitly scope adjacent Pi fields
   on that path as already matched, intentionally deferred, or known separate
   gaps. When explaining a forced-default versus upstream-default divergence, cite
   the exact Pi source/comment scope instead of generalizing beyond it.
   For an extension hook over provider request state, add a per-provider ownership
   matrix to the plan: enumerate the exact fields present and mutable when the hook
   fires, fields derived afterward by provider auth/signing/transport code, and any
   reserved fields that are filtered or overwritten. Do not make a uniform claim
   such as "assembled headers" across multiple adapters unless every named adapter
   exposes that same assembled set at dispatch. Pin focused tests for both sides of
   each distinct seam (a hook-visible field can be changed or deleted; a
   provider-owned post-hook field cannot), including retry, delegation, and signing
   variants where their ownership differs.
   If a gap source groups multiple adapters, providers, or body-family paths
   together, verify each named path independently against the Pi source and pin
   per-path behavior in the plan; do not rely on the audit's collective wording as
   exact implementation scope. If one named path is already Pi-correct, correct
   the gap-source docs and keep the slice limited to the path that actually
   diverges.
   For an automation/session-event slice (an event Pi emits to every subscriber,
   such as `agent_settled`, or a true-idle hook), synthesize it per mode at each
   mode's idle boundary, never in the shared `AutomationEmitter`, and capture
   the exact Pi sequence with `automation_pi_comparison.py` before coding
   (`docs/parity-loop/porting-notes.md`, "Automation and session events").
   For session persistence/history slices, check Pi's persistence timing first:
   Pi writes a session file lazily (first assistant flush) while pipy writes the
   tree eagerly, so append session-start entries such as `model_change` just
   before the branch's first message, keeping empty sessions empty. When porting
   a re-render of stored history, port Pi's pairing state exactly
   (`renderSessionItems` keeps each unanswered tool call pending across all
   intervening entries until the first later `toolResult` with its id).
   pipy compacts before a request, while the accepted prompt is not yet in the
   tree (Pi compacts after `agent_end` or on overflow), so a mid-run redraw from
   `build_context_entries()` must also draw `active_input.accepted_message`,
   skipped by identity once the branch holds it; PTY-check a compaction on a
   turn's first request and one mid tool loop.
   Reproduce compaction/budget bugs with a persistent `NativeSessionTree`
   (`create_product_session(tree=...)`), not only an ephemeral SDK session whose
   durable-origin refusals never fire: parametrize tests over persisted and
   ephemeral sessions and do a stub-provider PTY run before the plan is final.
   For a catalog/model-data slice, regenerate Pi's catalog into scratch, run
   `just catalog-drift --pi-data <scratch-dir>` before and after editing
   `catalog_data.py` (expect 0 drift, 0 stale), compare every compat key pipy
   reads, and pin the startup thinking default and model-switch clamp
   (porting-notes, "Catalog and model data").
   For a resource-discovery slice (skills, prompts, themes, extensions, context
   files), pin every Pi helper the walk calls, not only the top-level collector —
   e.g. `collectSkillEntries` also applies `addIgnoreRules`/`prefixIgnorePattern`
   — and enumerate every consumer of any shared root resolver the slice changes
   (e.g. `resolve_global_resource_root` also feeds `models.json` lookup), so a
   root change cannot silently move an unrelated resource. Before loading a new
   file shape or porting a typed frontmatter field, run every scalar form
   through Pi `parseFrontmatter` under Node and pin the matrix as a test
   (porting-notes, "Frontmatter parsing").
   Pin behavior per Pi call site, including request options such as `sessionId`
   and `cacheRetention`, and check that each site is reachable from the CLI
   path; never trust a backlog or audit summary. Pi's compaction and
   branch-summary calls pass no `sessionId` (a fresh routing id), and Pi calls
   `_restoreToolsFromTranscript` at construction only when
   `initialActiveToolNames` is undefined, but `sdk.ts` always passes an array,
   so the CLI restores tools on `/tree` navigation, not on resume.
   For a slice that changes a contract or state with several consumers, trace
   every consumer in the plan, not the first one found:
   - Built-in tool ports: grep every consumer hard-coding the old contract
     (`tool_renderers.py` headers such as a baked `:1-200` range, tool lines in
     the `session.py` system prompt, `@file` references, `parity_score.sh` test
     ids), and inventory every refusal layer (path, suffix, content) against Pi:
     `read_only_tool._is_ignored_or_generated` also refuses generated dirs and
     suffixes such as `.png`, so an image test named `x.png` never reaches the
     image check. Transcribe Pi arithmetic literally (Python slicing matches
     `Array.prototype.slice` for negative ends), but JS `toFixed` rounds ties up
     (1280 B is `1.3KB`): use `Decimal` `ROUND_HALF_UP` and pin a tie value.
     For a binary Pi shells out to (rg, fd), keep its resource limits and write
     the differential test against the real binary first; port the whole Node
     contract of each builtin a helper calls (`fileURLToPath`, `path.join`,
     UTF-16 strings) and diff against Pi's own TypeScript run by Node
     (`docs/parity-loop/porting-notes.md`).
   - Pi TUI component → inline-scrollback rows: list every input that changes
     the drawing (result, Ctrl+O, width, async callbacks) before coding; keep the
     row live until it settles, re-render on expand and resize, run Pi async
     work off the loop thread, and keep each Pi text/thinking block boundary as
     its own row, restored or streamed (porting-notes).
   - Provider-history filters: grep every `ProviderRequest(` with `messages=`,
     not only `materialize_provider_request` (compaction's
     `build_summary_request` bypasses it). A Pi JSON projection field must also
     reach streamed `message_update` partials (`automation/agent_events.py`).
     A new request carrier must survive every transform between build and
     adapter: `provider_replay_messages` drops stopped turns (re-anchor
     positions), `before_provider_request` narrows tools (pass the hidden set
     explicitly) or replaces the prompt (drop the carrier), and
     `estimate_request` counts what the carrier adds on the wire.
   - A transcript-only message kind (Pi system messages) gets its own union
     beside provider-history `AgentMessage`, so mypy finds every consumer;
     non-appending publications still need `require_current_run`. If Pi's
     default `/tree` filter shows it, grep tests and `scripts/parity_checks` for
     hard-coded `/tree select N`, `/fork N` and Pi role lists first; update them
     to the new Pi shape, never loosen the check. Build the out-of-band system
     prompt from the transcript replay (Pi `getCurrentSystemMessage` keeps Map
     order, so a later section goes last), and plan a live mid-session
     section-addition turn (resume with a new `--append-system-prompt`).
     Render a section that depends on runtime state (active tools, loaded
     skills) per run from session-owned state, after the input hooks, never
     from a startup snapshot; test `/reload` and an input-hook
     `set_active_tools` path.
   - Pi error-text classifiers: pipy messages are sanitized, so feed each
     adapter family's error type/code and transport retryable flags, and port
     Pi's earlier `isContextOverflow` veto (overflow text `50000` matches `500`).
   - Providers bake some values at construction (e.g.
     `OpenAIResponsesProvider.reasoning_effort`), so a state-only assignment can
     update the footer while requests keep the old value. Route every interactive
     change through the prepare/commit path that calls
     `coding_state.refresh_provider`, and test the bound provider's identity and
     field after the mutation. A model switch commits `model_change` and a
     clamped `thinking_level_change` together (Pi `setModel` calls
     `setThinkingLevel`). Derive capabilities from the same row the UI uses
     (`ModelRuntime.resolve_spec(selection).reasoning`), not from a parallel
     source such as `model_options`/`supports_thinking`.
   - A ported availability filter (Pi `filterModels`) applies to every selection
     path: `get_available()`, the REPL `/model` options (`model_options`), and
     direct `/model <ref>` resolution (`_resolve_model_reference`), each with a
     per-row reason.
   - OAuth providers (porting-notes, "OAuth providers"): refresh short-lived
     tokens per request through a wrapper; persist rotating refresh tokens
     under `auth_file_lock` rather than in the in-memory cache; run the
     callback listener on `ThreadingHTTPServer`; and never pass fixed flow
     text through `sanitize_text` (it redacts "token" URLs), but sanitize
     every value interpolated from outside.
   - Per-keystroke paths (autocomplete, argument completion) must never
     construct providers: construction runs credential `!command` helpers and
     extension factories. A user-typed regex must also catch `OverflowError` and
     `RecursionError`, not only `re.error`.
   - Cost/usage: confirm the price is actually consumed (`repl/turn_leaves.py`
     `pricing_for`) and enumerate every adapter usage extractor against the Pi
     adapter that normalizes it (uncached input = prompt minus inclusive cache
     counters, reasoning inside output, Gemini thoughts added to output, cache
     reads/writes per API) before pricing. Per-turn telemetry on
     `AgentAssistantMessage` is `compare=False`; session totals sum every entry
     on every branch like Pi `getEntries()` (porting-notes).
   - When a previously loaded-but-unused value becomes live input (models.json
     cost rates), re-check load-time validation against the consumer's
     invariants (negative, NaN/Infinity, `OverflowError` from `float(int)`) with
     path-qualified errors.
3. **Review the plan (different family).** Use one explicit path:
   - **Diff-based:** the plan must be a **tracked or staged** file (e.g. a spec
     under `docs/specs/`). `git add` it, then run the different-family
     review over the diff. The harness bundles staged/untracked content but
     **not gitignored** files, so a plan kept only under the gitignored
     `docs/parity-loop/runs/` must not use this path. Stage the `docs/backlog.md`
     follow-on for every deferred divergence in the same plan diff, or the
     reviewer keeps flagging it.
   - **Direct handoff:** for an untracked/gitignored plan note, use a
     `handoff-review` prompt that includes the plan content inline, or run a
     review mode whose tools can read the path. A tools-disabled reviewer given
     only a file path cannot inspect that file and must not be expected to return
     a verdict.
   Diff-based review bundles can be diff-only: unchanged imports, helpers, and
   nearby declarations may be invisible to the reviewer. Make new module-level
   constructs diff-local when practical, for example by placing new constants
   beside an existing same-kind construct that uses the same imports or helper
   dependencies, so the hunk itself refutes import/context false positives.
   **Pass Pi sources as evidence.** A reviewer that works in a repo copy (e.g. the
   `codex-review-loop` harness) cannot read `~/src/pi-mono`. Pass the touched Pi
   files — the adapter plus relevant caller excerpts such as compaction,
   `agent-session`, or generator rules — with `--evidence-file` (repeatable) on
   BOTH the plan review and every code-review round. `codex-review-loop` rejects
   `--staged-only` with `--baseline-ref`; to re-review a staged plan while
   unstaged implementation work exists, run `--staged-only` alone (full plan)
   plus a context file listing the prior findings and repairs.
   *Done-when:* CLEAN verdict (or the Operator-override stop above).
4. **Write the implementation plan.** Turn the reviewed design into an ordered,
   testable task breakdown, written to a file. *Done-when:* numbered plan with
   acceptance criteria per task.
5. **Implement.** Execute on `main`, TDD where it applies, matching Pi behavior;
   remove pipy-only accretions per the no-deprecation policy. Verify each edit
   landed (grep/Read) before running gates: some hosts' guards refuse shell edits
   without applying them. Before editing a native module or adding a
   `CodingSession` field, check the line ceilings and audit pins in
   porting-notes ("Line ceilings and audit pins"). Exercise UI, cost, tool-call,
   and restored-session behavior without live credentials in a real tmux PTY
   using `docs/parity-loop/pty-evidence.md` (isolated env — no gate, test or PTY
   run may touch the real `~/.pipy` — colour-capable tmux env, side-by-side tmux
   scripting, byte recordings, fake providers, local completions stub, `pipy -r`
   restore, offline real-Pi comparison, cheap live `--mode json` checks).
   *Done-when:* code complete, focused tests written.
6. **Update docs (part of the change).** Bring docs + release notes + the parity
   docs (`docs/parity-plan.md`, `docs/pi-mono-gap-audit.md`, `docs/backlog.md`)
   in line with the change, *before* the review gate, so the reviewed diff is
   complete. *Done-when:* docs reflect behavior; the gap is struck from the gap
   source.
7. **Code-review loop until CLEAN (over the complete diff).** Each iteration: run
   `just check` (and `prek run --all-files` only if a `.pre-commit-config.yaml`
   is present — `just check` is pipy's real gate; `pre-commit` is not installed).
   If full `just check` fails on macOS with `Too many open files` after focused
   tests have passed, first check whether the diff touches fd-owning areas such
   as PTY, subprocess, socket, file-handle, or resource-management code; if it
   does, treat the failure as a real regression until disproven. Otherwise retry
   the unchanged gate with a raised descriptor limit such as
   `ulimit -n 4096; just check` before treating it as a code failure; still rerun
   any individually reported flaky PTY test to distinguish environmental
   pressure from a real regression. Run PTY gates under `timeout 600` (they can
   hang uninterruptibly on macOS), never beside a review or other heavy job, and
   rerun a failure once, alone, before investigating (pty-evidence). Only when
   green, run the different-family review over the **full diff — code and docs together**. The
   reviewer must be a single direct fresh context:
   no subagents, `Agent` tool, Task-style delegation, or parallel reviewer fanout.
   On an ISSUES verdict, fix and **return to the top of
   this iteration** (re-run `just check` and prek before the next review), so
   every review — including the final CLEAN one — is taken over a diff whose
   gates currently pass.
   *Done-when:* `just check` green, prek green (or absent), and review CLEAN over
   the complete diff in the *same* iteration.
8. **Mark done & report.** Commit (trunk; clean message, no self-reference).
   Record an evidence summary: what changed, gates passed, review verdict.
   *Done-when:* committed, gap marked complete.
9. **Reflect (capture lessons).** Locate this session's transcript with the
   per-agent locator below, then read it with the gap's diff, the review
   verdicts, and how many review rounds it took. Distill 0–N reusable,
   summary-safe lessons (a recurring review finding, a gate failure, a wrong turn
   that cost time, or a better approach — never raw transcript or secrets) and
   append each (after checking `list` to avoid duplicates):

   ```bash
   python3 scripts/parity_lessons.py append --json \
     '{"skill":"pipy-parity-loop","gap":"<gap>",
       "agent":"<host agent: claude|codex|pi|pipy>",
       "trigger":"recurring-review-finding","lesson":"<distilled, summary-safe>",
       "target_area":"<skill-body|wrapper|docs|tests|harness>"}'
   ```

   Set `agent` to the host agent actually running this loop (not a literal), and
   set `target_area` to the artifact the lesson is about — it decides the sign-off
   path and which file a later `applied` commit must touch, so do not leave it as
   `skill-body` by default. The helper assigns the `id` and `status: open` and
   refuses near-duplicates.
   Commit the ledger change as a small `chore(lessons): …` commit. *Done-when:*
   lessons appended (or none worth keeping) and committed.

   **Per-agent transcript locator** (the host agent is known — it is the one
   running this loop):
   - claude: newest `*.jsonl` in `~/.claude/projects/-Users-jochen-projects-pipy/`.
   - pi: newest `*.jsonl` in `~/.pi/agent/sessions/--Users-jochen-projects-pipy--/`.
   - pipy: newest `*.jsonl` in `~/.local/state/pipy/native-sessions/--<cwd>--/`.
   - codex: `~/.codex/history.jsonl` is a single global, untyped log — extract the
     contiguous tail of records sharing the last record's `session_id` (coarser;
     documented limitation).

**Run-end backstop.** A parity-loop run must not conclude with any `open`
lessons. Before finishing, run `parity-improve` until
`python3 scripts/parity_lessons.py list --status open` is empty, so every
captured lesson is consumed (materialized or, with sign-off, rejected).

## Runner single-gap mode

When the invocation prompt contains the marker `runner single-gap mode` (the
parity-runner sets it), run only the single gap — Phases 1–8 plus the **Phase 9
capture** — and **defer Phase 0's drain-enforcement, the `parity-improve` step,
and the run-end backstop to the caller** (the parity-runner owns batch-level
lesson draining). Still capture lessons in Phase 9 as usual; do **not** apply,
drain, or reject them in this mode. Everything else (gates, different-family
review, commit) is unchanged. This exists so an unattended single-gap run never
blocks on a sign-off-needing lesson; lesson application is the runner's job.

## Reuse

- Plan/review/impl framing: the `goal-handoff`, `handoff-impl`, `handoff-review`
  skills.
- The different-family review gate: `pi-review-loop` (review with Pi/GPT) or
  `opus-review-loop` (review with Opus), selected by the reviewer-selection
  rules above.

## Claude Code CLI Hygiene

When invoking Claude Code noninteractively, be explicit about the prompt channel:

- If the prompt is a positional argument, put it after `--` and close child stdin
  with `/dev/null` (for example:
  `claude -p --model opus --no-session-persistence --tools "" --disable-slash-commands -- "$PROMPT" </dev/null`).
- If the prompt is piped or supplied as a prompt file on stdin, do not also pass a
  positional prompt; the pipe/file must be the only prompt source.
- Never place a positional prompt immediately after variadic flags such as
  `--tools ""`; without `--`, Claude may treat the prompt as another tool value.
- For read-only Opus reviews, prefer the `opus-review-loop` harness or direct
  `claude -p` with tools disabled. Do not use `claude-yolo` solely for a
  read-only review. A read-only review must be a direct single-context review:
  no `Agent` tool, Task-style delegation, subagents, or parallel reviewer fanout.
- When tools are disabled, the prompt or review bundle must contain the actual
  plan/diff content being reviewed. Do not ask Claude to review only a path,
  commit name, or unstaged local file reference that it cannot read.
- For write-capable unattended implementation runners, do not replace permission
  bypass with a weaker mode such as `default`, `plan`, or `acceptEdits` unless
  that exact runner has been verified to perform edits, shell commands, and
  commits without prompting.
- Bound availability checks: after a small fixed number of smoke-test/retry
  attempts, stop and report the reviewer CLI as unavailable instead of looping.
