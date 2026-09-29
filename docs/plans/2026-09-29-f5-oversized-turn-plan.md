# F5: an oversized turn must not wedge the session

Backlog follow-on DF1-F5; DF1 acceptance scenario 8. Pi reference:
`~/src/pi-mono` @ `4df157433`, `packages/coding-agent/src/core/compaction/`
(`utils.ts` `serializeConversation`/`truncateForSummary`, `compaction.ts`
`findProjectedCutPoint`/`prepareCompaction`/`compact`) and
`core/agent-session.ts` (`_checkCompaction`, `_runAutoCompaction`).

## The bug, reproduced

A deterministic SDK run (fake tool provider, `compaction.contextWindow` = base
request + 5000 estimated tokens, `reserveTokens: 0`) reproduces it:

1. `read big.txt` (24 KB) inside a turn. The next iteration of that turn is
   over the window. Automatic compaction drops the older groups and keeps the
   current one; that group has one tool cycle, so there is no cycle cut. The
   request is refused (`request_budget`). This part is expected.
2. Every later prompt is refused. Automatic compaction now wants to drop the
   oversized group, but the summary request carries the removed messages
   verbatim, including the 24 KB tool result, so the auxiliary preflight
   refuses it (`compact refused: estimated summary request exceeds the context
   window`). The ordinary request is then refused again. `/compact` keeps two
   groups and says `nothing to compact yet`. Only `/new` recovers.

The wedge is step 2: the summary input for a removed group is as large as the
group it is meant to replace.

A real PTY run (persistent session, local chat-completions stub,
`compaction.contextWindow: 12000`, `reserveTokens: 3000`) shows a second,
independent wedge on the same step. With truncation alone, every later prompt
still ends with `compact refused: retained history has no durable origin`.
The latest-group cut keeps only the new prompt, whose user entry is persisted
only at turn-start settlement after preparation, so the cut has no persisted
first-kept entry and is refused (`provider_selection.py`
`_capture_compaction_locked`). The next prompt has the same shape, so the
session never recovers. `docs/compaction.md` documents this refusal as
deliberate ("no entry is invented"). The SDK test above missed it because its
tree was not persistent.

## What Pi does

- The summary request never carries full tool output.
  `serializeConversation` (`utils.ts:114-153`) turns the removed messages into
  text and cuts every tool result with `truncateForSummary(content, 2000)`:
  the first 2000 characters, then `\n\n[... N more characters truncated]`
  (`utils.ts:94-104`). Branch summaries use the same serializer
  (`branch-summarization.ts:324`). So one oversized tool result no longer
  makes the summary request oversized, and the next prompt's compaction
  removes it (see the scope limit below for what truncation does not bound).
- Pi's threshold compaction for a new prompt runs before the prompt is
  appended (`agent-session.ts:742`, `:798`), and a compaction that keeps no
  earlier entry is representable: `SessionManager.appendCompaction(summary,
  firstKeptEntryId: string | null, …)` stores `firstKeptEntryId ?? id`, the
  compaction's own id (`session-manager.ts:1262-1283`). Projection keeps the
  entries from `firstKeptEntryId` up to the compaction, which for its own id
  is none, plus every later entry (`buildContextEntries`,
  `session-manager.ts:476-510`). Extension boundary drafts use it
  (`agent-session.ts:925-936`), and session cloning keeps a self-referencing
  id pointing at the (new) compaction itself (`session-manager.ts:1658-1663`).
- Pi cuts by `keepRecentTokens`, and can cut inside a turn (split turn with a
  turn-prefix summary). If the trailing tool results alone exceed the budget,
  it keeps the assistant tool call that produced them
  (`compaction.ts:846-850`). So when the newest tool result alone overflows,
  and that result still fits once truncated to 2000 characters,
  Pi fails too: one compact-and-retry, then `Context overflow recovery failed
  after one compact-and-retry attempt. Try reducing context or switching to a
  larger-context model.` (`agent-session.ts:2930-2950`). Recovery comes on the
  next prompt, whose threshold compaction summarizes the oversized result.

## Change

1. `coding/compaction.py` `build_summary_request` (shared by compaction and
   branch summaries) passes each `AgentToolResultMessage` through Pi's
   `truncateForSummary` with Pi's limit 2000 and Pi's marker text. Other
   messages, correlation identity, `is_error` and order are unchanged. The
   summary preflight (`provider_selection.py`) measures this same request, so
   the preflight admits it.
2. Durable self boundary (Pi `firstKeptEntryId ?? id`). In
   `_capture_compaction_locked`, when the tree persists and the cut's first
   retained message has no entry id, accept the cut only if it is the
   automatic latest-group cut whose retained messages are exactly the run's
   accepted user message (identity), with no retained-user anchor. Everything
   else keeps today's `no durable origin` refusal. Such a cut records "keeps
   no earlier entry" (a new `keeps_no_prior_entries` flag on the work item
   and on `CodingProductSessionCompaction`, validated as exclusive with both
   entry ids). The durable writer calls
   `NativeSessionTree.append_compaction(first_kept_entry_id=None)`, which
   stores the new entry's own id, as Pi does. The accepted user is then
   persisted after the compaction entry by the unchanged turn-start (or
   refusal/cancel) settlement, so live and reopened context match: summary,
   then that user and everything after it.
   Session tree support for a self-referencing `firstKeptEntryId`:
   - projection (`_legacy_retained_context_entries`, also under
     `strict_compaction_ancestry`, and the anchored effective-path loop):
     keep no entry before that compaction, then every later entry;
   - clone/fork remap (`_remap_compaction_references`): a self reference
     maps to the new entry's own id, like Pi's clone;
   - load: no change needed (the id format check already passes).
   The same append order holds for every settlement, so a refused or
   cancelled first iteration still leaves the compaction entry before the
   user entry. The existing test
   `test_first_iteration_durable_anchor_refusal_settles_user_and_can_recover`
   pins the old refusal; it changes to pin the Pi behavior (compaction entry
   with its own id, request admitted, reopen and fork equal live context).
3. No change to cut selection, the ordinary admission check or its notice.
   The mid-turn refusal in step 1 stays (Pi also fails that turn). Inside a
   turn, the older-cycle cut already summarizes older cycles; its summary
   request now also carries truncated results.
4. Docs: `docs/compaction.md` (summary input truncation; the self boundary
   replacing the first-iteration refusal; the recovery path
   after an oversized-tool-result refusal is the next prompt; the remaining
   unrecoverable case and `/new`), CHANGELOG `[Unreleased]`, backlog: move
   DF1-F5 to Done and add follow-on F5b for the remaining case, acceptance
   note scenario 8 pointer.

## Scope limit: what still cannot recover (same as Pi)

The truncation bounds each tool result, not the whole summary request. The
summary input can still be too large when the removed range has many tool
results, a large user message (a paste), large tool-call arguments (a big
`write`), or a long assistant answer. Pi has the same limit: it truncates only
tool results, and its summary request has no overall cap, so the provider
rejects it and every later prompt fails the same way (`Auto-compaction failed`
/ `Context overflow recovery failed`). pipy usually refuses these locally in
the summary preflight. Its estimate (`ceil(bytes / 3)` plus 25 %) is a
heuristic, not a tokenizer: it is usually cautious, but it can undercount, so
a summary request can also pass preflight and then be rejected by the
provider. That path is unchanged: the summary fails, nothing is published, the
existing bounded failure notice is shown, and the ordinary request is then
admitted or refused as before (`docs/compaction.md`, failure and validation).
This slice
does not add a recovery path Pi lacks (for example dropping history without a
summary). `docs/compaction.md` states the remaining case and that `/new` is
the way out; the backlog records it as a follow-on (F5b) instead of claiming
every oversized turn recovers. F5 moves to Done for the reported wedge: one
oversized tool result.

## Deliberate deviations kept

- pipy still sends the removed messages as a message list, not Pi's single
  serialized `<conversation>` text; only the tool-result truncation is ported.
- Truncation counts Python code points; Pi's `String.length` counts UTF-16
  units. Output differs only for astral-plane characters.
- Cut selection stays group/cycle based (`keepRecentTokens` inactive), manual
  `/compact` keeps two groups, and pipy refuses locally before sending instead
  of reacting to a provider overflow error. Porting Pi's token-based cut and
  overflow-error recovery is a larger slice, not needed to remove the wedge.
- DF1-F6 (aborted turns persist no assistant message) is not included: Pi
  persists the aborted message with `stopReason: "aborted"` and filters it from
  provider replay (`ai/src/api/transform-messages.ts:195-201`). Porting that
  needs a stop-reason field on persisted assistant messages, a replay filter in
  every adapter, and resumed-transcript rendering, which another slice (F2) owns.

## Tests (done-when)

- New regression test (SDK, fake tool provider, small `contextWindow`), run
  with an ephemeral and with a persistent tree: the oversized-turn refusal
  happens, then the next prompt compacts the oversized group away and is
  admitted and answered; a following prompt also succeeds. For the
  persistent tree the compaction entry's `firstKeptEntryId` is its own id,
  the user entry follows it, and reopen and fork rebuild the live context.
  Fails before the change (both later prompts refused).
- Session tree unit tests: self-referencing compaction projection (plain,
  strict reopen, after an anchored compaction, and a later anchored
  compaction after a self one), and clone/fork remap to the new id.
- A persisted first iteration whose compaction is followed by a refused or
  cancelled request still leaves the compaction entry before the user.
- Unit test: summary request truncation matrix — result shorter than, equal
  to and longer than 2000 characters (exact marker text and count), non-tool
  messages untouched, correlation id and `is_error` preserved; branch-summary
  requests are truncated too.
- Scope-limit test: a removed group whose summary input stays over the
  window after truncation (a large user message) is still refused with the
  summary-preflight notice, and context is unchanged (pins the documented
  limit rather than implying recovery).
- `just check` green. A real PTY run with the fake provider and an isolated
  `PIPY_CONFIG_HOME`/`PIPY_SESSION_DIR`: an oversized `read` is refused, and
  the next prompt is answered.
