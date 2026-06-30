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
1. **Select the gap.** Read `docs/pi-mono-gap-audit.md` (ranked) and
   `docs/backlog.md`; pick the highest-value incomplete slice, or accept an
   operator-supplied gap. Confirm it is a single reviewable slice (decompose if
   not). *Done-when:* one named gap with a one-paragraph scope and the relevant
   `~/src/pi-mono` reference path(s).
2. **Plan.** Read the pi-mono reference; write a short design/plan (what Pi does,
   how pipy matches it through pipy-owned Python boundaries, constraints from
   `AGENTS.md`). **Write the plan to a file** so it is reviewable. *Done-when:* a
   written plan file with done-when criteria. For metadata, auth, OAuth,
   provider, package manifest, extension API, or provider request-shape slices,
   the plan must pin the Pi reference field list, each field's optionality, any
   Pi-forced default values, whether those defaults diverge from the upstream API
   defaults, and any derived identifiers before implementation, so review catches
   preserved versus dropped future data or behavior instead of discovering it only
   after code exists.
   For any request-shape field gated by a Pi compat flag, also pin — per field —
   which compat flag(s) gate it and how each of those flags is independently
   resolved. Pi's `getCompat` resolves every compat field independently (explicit
   `compat.<flag>` wins, else that flag's own `detectCompat` predicate), so a
   field's format flag and any secondary gating flag are not coupled: e.g.
   `thinkingFormat` always emits `thinking:{type}`, but the top-level
   `reasoning_effort` is added only when `supportsReasoningEffort` is true, and an
   explicit `compat.thinkingFormat="deepseek"` on a baseUrl that `detectCompat`
   EXCLUDES from `supportsReasoningEffort`
   (isGrok/isZai/isMoonshot/isTogether/isCloudflareAiGateway/isNvidia/isAntLing)
   yields `thinkingFormat=deepseek` AND `supportsReasoningEffort=false` → Pi emits
   `thinking:{type:enabled}` with NO `reasoning_effort`. Implement each secondary
   flag as its own faithful bounded predicate (explicit bool wins, else the
   exclusion list) — a single bounded predicate, not a full `detectCompat` port —
   and add a test for the explicit-format-on-excluded-provider mismatch. Never
   default a secondary flag to True because the format flag's detection "implies"
   it; the different-family reviewer flags that coupling.
   Pin per-variant in the plan whether a secondary flag even APPLIES — not every
   `thinkingFormat` variant has one. The `enable_thinking` family (`zai`,
   explicit-only `qwen`, and the `qwen-chat-template` `chat_template_kwargs` shape)
   is a BARE-BOOLEAN branch: Pi emits `enable_thinking = !!options.reasoningEffort`
   (openai-completions.ts:556-563) and NOTHING else — it never consults
   `compat.supportsReasoningEffort` and never emits a top-level `reasoning_effort`,
   unlike the deepseek/together branches. So do NOT add a `supportsReasoningEffort`
   gate or a `reasoning_effort` emission to an `enable_thinking`-family branch; the
   omission is STRUCTURAL to the branch, not a consequence of any exclusion. Note
   the trap: `zai` (and `qwen`) ARE in `detectCompat`'s `supportsReasoningEffort`
   exclusion list, which tempts a "zai-excluded → omit `reasoning_effort`" framing
   — but the branch omits `reasoning_effort` because it never reads the flag, not
   because the flag resolves false. For the `enable_thinking` family, the right
   guard is the INVERSE test — force explicit `compat.supportsReasoningEffort=true`
   and assert the request STILL omits `reasoning_effort` (only `enable_thinking`
   appears) — instead of the deepseek-style explicit-format-on-excluded-provider
   mismatch test.
   When the field is resolved by porting one rung of a Pi `detectCompat`-style
   if/else-if DETECTION CHAIN (e.g. the `thinkingFormat` chain isDeepSeek > isZai >
   isTogether > isAntLing > isOpenRouter), pin each ported rung's POSITION relative
   to its Pi siblings — not just the set of providers pipy detects. pipy resolves
   the field through its own ordered if-chain, so appending a new rung (e.g.
   together) AFTER a rung that comes later in Pi (e.g. openrouter) makes collision
   rows — those matching two rungs at once — resolve differently from Pi. In the
   plan, place the new branch at its Pi-faithful position and account for the rungs
   pipy defers to the default (e.g. zai/ant-ling): deferred rungs must not silently
   reorder the rungs pipy does implement. Add a precedence test for a row that
   matches two rungs (e.g. a together provider on an openrouter.ai base URL →
   together shape). The different-family plan reviewer flags exactly this ordering
   bug.
   The INVERSE case is an EXPLICIT-COMPAT-ONLY variant — one with NO `detectCompat`
   rung — and it needs ZERO resolver work and NO precedence test. Pi's
   `thinkingFormat` `detectCompat` chain is isDeepSeek > isZai > isTogether >
   isAntLing > isOpenRouter > openai (openai-completions.ts:1126-1136); there is no
   `isQwen` or `isStringThinking`, so the `qwen`, `qwen-chat-template`, and
   `string-thinking` variants are reachable ONLY through an explicit
   `model.compat.thinkingFormat`. pipy's `_resolve_thinking_format` already returns
   any explicit `compat.thinkingFormat` verbatim in its first branch, so for these
   variants you add ONLY the request-shape `elif` branch in `provider_construction`
   and change NEITHER the resolver NOR its docstring detection order — and you write
   NO detection-chain/precedence test, because there is no collision row to
   disambiguate. Helper test specs for these variants must set
   `compat={thinkingFormat: <variant>}` explicitly. Contrast `ant-ling`, which IS in
   the chain (isAntLing) and therefore DOES need an ordered detection rung plus a
   precedence test when ported. So in the plan, pin per remaining variant whether it
   is auto-detected (needs a rung at its Pi-faithful chain position) or explicit-only
   (request-shape branch only), so the diff is not over-built with an unused
   detection rung. Also pin any constant companion field a variant's branch forces
   regardless of the reasoning state: e.g. `qwen-chat-template` emits a Pi-forced
   literal `preserve_thinking: true` present in BOTH the reasoning-on and
   reasoning-off sub-states, independent of the toggled `enable_thinking` boolean.
   When a `thinkingFormat` branch reuses pipy's `reasoning_value`
   (= `map_thinking_level`), pin the EXACT Pi value expression for that branch and
   check it for a `?? level` (a.k.a. `?? options.reasoningEffort`) fallback before
   reusing it. deepseek/together/openrouter/string-thinking all do
   `model.thinkingLevelMap?.[level] ?? level`, but `ant-ling`
   (openai-completions.ts:581-585) does a RAW `model.thinkingLevelMap?.[level]`
   lookup with NO fallback. pipy's `reasoning_value` falls back to the raw requested
   level when a model has no map, so reusing it for a no-fallback branch emits
   `reasoning:{effort:<raw level>}` where Pi emits nothing — add a dedicated
   string-only raw-lookup helper and a no-`thinkingLevelMap` on-state test asserting
   the field is omitted.
   Also guard the non-reasoning + `thinkingLevelMap` DEFAULT-BRANCH LEAK: a branch
   gated `elif thinking_format == X and bool(spec.reasoning):` can FALL THROUGH to
   the default `elif reasoning_value is not None: reasoning_effort = reasoning_value`
   for a NON-reasoning model that declares a `thinkingLevelMap`, because
   `map_thinking_level` keys off the map keys (`supported_thinking_levels`) and
   IGNORES `model.reasoning`, so `reasoning_value` is a non-None string for it. Pi
   gates BOTH its branch AND its default on `model.reasoning`, so it emits nothing;
   pipy leaks a top-level `reasoning_effort`. Make the new branch consume ALL of its
   `thinking_format` cases — drop `and bool(spec.reasoning)` from the `elif` and move
   the reasoning check INSIDE the value helper — and add a non-reasoning +
   `thinkingLevelMap` regression test asserting NEITHER `reasoning` NOR
   `reasoning_effort`. The existing zai/together/qwen branches still carry this
   latent divergence as a candidate follow-on.
   `ant-ling` is also the only emitting completions variant with a fully SILENT
   off-state: its branch is gated on `options.reasoningEffort`, so an off/unset
   reasoning state emits neither `reasoning` nor `reasoning_effort`.
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
   If a gap source groups multiple adapters, providers, or body-family paths
   together, verify each named path independently against the Pi source and pin
   per-path behavior in the plan; do not rely on the audit's collective wording as
   exact implementation scope. If one named path is already Pi-correct, correct
   the gap-source docs and keep the slice limited to the path that actually
   diverges.
3. **Review the plan (different family).** Use one explicit path:
   - **Diff-based:** the plan must be a **tracked or staged** file (e.g. a spec
     under `docs/superpowers/specs/`). `git add` it, then run the different-family
     review over the diff. The harness bundles staged/untracked content but
     **not gitignored** files, so a plan kept only under the gitignored
     `docs/parity-loop/runs/` must not use this path.
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
   *Done-when:* CLEAN verdict (or the Operator-override stop above).
4. **Write the implementation plan.** Turn the reviewed design into an ordered,
   testable task breakdown, written to a file. *Done-when:* numbered plan with
   acceptance criteria per task.
5. **Implement.** Execute on `main`, TDD where it applies, matching Pi behavior;
   remove pipy-only accretions per the no-deprecation policy. *Done-when:* code
   complete, focused tests written.
6. **Update docs (part of the change).** Bring docs + release notes + the parity
   docs (`docs/parity-plan.md`, `docs/pi-mono-gap-audit.md`, `docs/backlog.md`)
   in line with the change, *before* the review gate, so the reviewed diff is
   complete. *Done-when:* docs reflect behavior; the gap is struck from the gap
   source.
7. **Code-review loop until CLEAN (over the complete diff).** Each iteration: run
   `just check` (and `prek run --all-files` only if a `.pre-commit-config.yaml`
   is present — `just check` is pipy's real gate; `pre-commit` is not installed),
   and only when green run the different-family review over the **full diff —
   code and docs together**. The reviewer must be a single direct fresh context:
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
