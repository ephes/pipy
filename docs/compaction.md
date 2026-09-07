# Compaction

Compaction reduces the provider-visible context for long sessions while keeping
the native session tree durable and navigable.

## What compaction does

Pipy combines a deterministic whole-group cut with a provider-generated summary:

1. It cuts history only at user-turn boundaries, so tool results are not orphaned
   from the assistant tool calls that produced them.
2. It keeps the most recent user-turn groups verbatim for the next provider
   request.
3. It asks the current provider to summarize the exact older groups together
   with any previous summary, preserving goals, constraints, decisions, files,
   verified results and unfinished work. The request has no tools or attachments
   and excludes request-only overlays. Its text is private and does not stream
   into the transcript.
4. After checking that the captured context is still current, it replaces the
   older groups with the combined summary in the system prompt.
5. It appends a `compaction` entry to the native session JSONL file using the
   exact first retained entry resolved before generation or live acceptance. A
   durable session refuses a cut whose retained entry cannot be resolved.
   If that mismatch persists, the automatic threshold check can report the
   refusal again on later requests; it leaves live context unchanged.

The native session file still contains the full transcript entries that were
written before compaction. Compaction changes what future provider requests see;
it does not rewrite the append-only session file.

## Manual compaction

Run compaction from the interactive session:

```text
/compact
```

If there is not enough history to compact, pipy reports that there is nothing to
compact. Otherwise it reports how many earlier exchange groups were dropped from
provider-visible context and how many recent groups were kept.

Generation failure or cancellation leaves the existing context intact. Escape or
Ctrl-C can cancel summary work in the terminal. A successful summary is published
only if its captured context remains current. Manual stale work reports refusal;
automatic stale work closes the session through the guarded-run exception path
rather than allowing the older run to overwrite newer context. This conservative
stop also applies to extension writes during automatic summarization that only
add a custom message, change a label, or rename the session: those tree changes
invalidate the captured summary even if provider history is unchanged. The
extension write remains accepted; the session closes without publishing the
summary. A refused reload publication window has the same effect.

Manual `/compact` has no later agent-run settlement. When the command completes,
queued steering and follow-up messages become deliverable through the ordinary
input queue, including after a failed, stale or provider-cancelled summary.
Steering Enter cancels the summary and runs steering before follow-up; a local
command runs locally before pending prompts. Escape or Ctrl-C instead restores
pending text to the editor for editing and explicit submission. A persistence
exception still escapes after acceptance without settlement or recovery.

Only the bare `/compact` command is currently accepted. A trailing prompt such
as `/compact summarize decisions` is rejected as an unhandled command rather
than used as custom compaction instructions. The current implementation uses
pipy's built-in semantic-summary instructions.

## Automatic compaction

Automatic compaction is enabled by default. The tool-loop session checks history
between turns and normally compacts when message count or byte thresholds are
exceeded. Extension `deliverAs=nextTurn` context is a detached, identity-anchored
request overlay: every provider iteration of exactly the next accepted run sees
it immediately after that run's real user message. Automatic compaction continues
to operate on the durable history during that run, while the overlay stays out of
the canonical run result, additional product `MessageEntry` records, and the
metadata-only archive. The original bounded extension `CustomMessageEntry`
remains part of the native product session. These overlays also stay out of
semantic-summary requests. Public session formats are unchanged.

Settings expose the current compaction controls:

```json
{
  "compaction": {
    "enabled": true,
    "reserveTokens": 16384,
    "keepRecentTokens": 20000
  }
}
```

`enabled` controls the automatic path. The token-related settings are part of
the Pi-shaped settings surface and are displayed in `/settings`; the current
stdlib compactor primarily uses message/byte thresholds plus a fixed recent-turn
retention policy.

## Durable session behavior

When compaction changes history, pipy appends a `compaction` tree entry with the
summary and the first retained entry ID. That boundary may precede the previous
compaction entry: a second cut works even after only one new user group. On
resume, `/tree` navigation, fork, clone, and import, pipy rebuilds the active branch
with that compaction boundary honored. Coding context restores the summary in
the system suffix and keeps real messages separate, so a summary does not become
another user group. An uncompacted destination or `/new` clears the suffix.

Compaction counters describe the current run, independently of a restored branch
summary. Startup can restore a summary with zero compactions in the new run;
navigation replaces the summary while preserving cumulative run counters.

Persistence remains state-first: live history, summary, and counters advance
before the synchronous tree append. A write failure propagates before the success
diagnostic and does not roll back or retry. Tree memory can also advance before
the file write fails; this is not an atomic durability guarantee.

This means:

- continuing after compaction sends the reduced active context, not all older
  turns;
- sibling branches remain available in the native session tree;
- exports can still include the full product session data because the JSONL file
  is append-only;
- `pipy-session` catalog records remain metadata-only and do not become the
  product session source.

## Extension gates

Extensions can observe or block session compaction through the
`session_before_compact` hook. If an extension blocks compaction, pipy reports a
safe diagnostic such as `compact blocked by extension` and applies no compaction
cut. The hook may still perform its separately authorized model mutation.

The gate continues to offer `ctx.set_model(...)`. If that call successfully
replaces the model during automatic compaction, it replaces the binding and
clears history while an accepted coding run is active. The stale run then raises
`CodingContextChangedError` and closes the session through existing exceptional
cleanup; it cannot submit an ordinary provider request or republish its old
history. Manual `/compact` runs outside an accepted coding run, so the same gate
mutation remains valid and the next prompt can use the selected model. Per-provider
and tool hooks continue to deny `set_model` by returning `False`.

## Limitations and follow-ons

- Auxiliary summary tokens and cost are not yet included in the session usage
  totals, footer cost meter or run result, so those totals under-report provider
  spend when compaction runs.
- A failed automatic summary leaves the threshold condition unchanged. A later
  provider iteration can therefore attempt another summary and repeat the bounded
  failure notice. Retry policy and auxiliary attempt accounting remain later work.
- The terminal currently shows no working indicator during summary generation;
  Escape and Ctrl-C still cancel it.
- Semantic summaries are lossy provider output. Synthetic tests cover the supplied
  facts and request/reopen continuity; live-provider summary quality remains unverified.
- `/compact <custom instructions>` is not accepted yet; use bare `/compact`.
  Only the built-in summary instructions are used.
- Compaction is lossy for future provider requests: older details may no longer
  be in context unless you navigate or resume from a branch point that includes
  them before the compaction boundary.

For the broader session model, see [Sessions](sessions.md) and the maintainer
spec [Session Tree](session-tree.md).

## D1 implementation contract

D1a implements structural provenance, repeated durable cuts and destination-summary
rebuilds. D1b implements canonical semantic generation, cancellation and conditional
acceptance. Task selection and status live only in
[the backlog](backlog.md). D1 changes semantic continuity at existing whole-user-
group boundaries, with no budgeting, within-run cuts, retries, custom `/compact`
instructions, or RPC controls.

The coding/product composition owns one auxiliary no-tool summary operation.
Keep `native.agent.history` mechanical and its positive dropped-group invariant.
Use the existing branch-summary request capability as the starting seam, but run
D1's completion through `ProviderTurnExecutor` with canonical cancellation, a
private no-op event sink and `ProviderTurnDeltaPolicy(text=False, reasoning=False)`.
Do not copy the direct `provider.complete` call or run tools in a
second agent loop. The shared branch helper captures one provider binding for
request labels and execution, including across header-callback acquisition.
Broader branch-navigation changes are outside this slice.

The summary input contains the previous summary and exact dropped conversation
prefix, derived from the captured immutable history using the canonical result's
`dropped_message_count`. Do not recompute user-group boundaries in the coding layer. Instructions preserve goals, decisions, constraints, relevant files,
verified results, and unfinished work, distinguishing facts from unresolved
questions. Conversation content is data to summarize. Retained groups remain
verbatim, with complete tool exchanges and correlation identity. Accept only a
successful, nonempty text result without tool calls. Replace the previous summary
with the combined summary rather than stacking summaries. A deterministic test
can prove supplied facts and request continuity; it cannot establish live summary
quality.

`CodingProductSessionContext` carries a separate optional prior summary and
parallel origin entry IDs. The product loader resolves the summary structurally
from the latest active compaction entry, never by recognizing prose, and supplies
real retained messages and their originating entry IDs separately.
The guarded `CodingSessionState.rebuild_history` transition accepts
an explicit summary suffix whose default is empty. The coordinator passes the
loaded destination summary to that transition for **every** tree rebuild:
startup/resume, `/tree`, `/fork`, `/clone`, `/import` and `/new`. An uncompacted or
empty destination clears the suffix; a compacted destination installs only its
own summary. Never carry the source branch's suffix into the destination.

This intentionally replaces unconditional suffix clearing for a compacted
destination while preserving the default clear behavior.
`test_rebuild_loads_exact_context_and_preserves_cumulative_counters` covers both
cases; the session-tree suite pins compacted/uncompacted destination replacement.
The two persistence-failure tests below are additional requirements, not
an exhaustive list of affected tests.

Immediate and rebuilt coding context must have equivalent summary placement and
real retained history, so the synthetic summary user message in the current tree
projection does not count as another user group. Preserve other tree clients'
existing projection and the JSONL schema where possible; do not change custom-
message or branch-summary semantics incidentally. Capture the exact first retained entry from the active
projected history, including entries retained from before the last compaction.
The durable writer consumes that identity instead of counting only users since
the last compaction. If a durable mapping cannot be established, refuse before
acceptance; ephemeral sessions still compact without a disk requirement.

### Guarded snapshot and acceptance

Use existing `coding_effects.lock` (also `mutation_io_lock` and the tree lock)
then the session/generation mutex. Never acquire them in reverse order.

| Snapshot family | Owner / guarded readers | Invalidating writers |
| --- | --- | --- |
| Exact provider binding | `CodingSessionState` binding/provider/model readers and new snapshot reader | Begin, refresh/rebind/unavailable, model/reload publication |
| History and previous summary | State history/suffix readers and snapshot/acceptance APIs | Begin, append, mirror, clear, rebuild, rebind and accepted compaction |
| Active tree and branch | `RunControlState` tree section and all `NativeSessionTree` context/branch readers | Tree replacement, append, navigation and branch/leaf changes |
| Generation and admission | `SessionGenerationRef` plus coding-effect lifecycle owner | Publication/gate changes, retirement and terminal close |
| Pending summary | One caller-owned operation; worker returns a detached result only | Cancellation, refusal, acceptance and disposal |

Capture exact binding, tree and generation identities, branch position, previous
summary, history and lifecycle eligibility coherently. An owner-issued revision
or equivalent identity must detect equal-content replacement and change-away/
change-back history; all affected writers and snapshot readers participate in
that guard. Tree snapshot identity must likewise detect intervening navigation
or mutation rather than relying only on a leaf that could be restored.

Run provider I/O, header/extension callbacks and painting outside both locks.
Do not hold an exclusive effect lease across summary I/O if a callback can need
that lease. Reacquire the established lock order for the final freshness and
liveness check plus state acceptance in one uninterrupted section. Refuse stale,
cancelled or terminal work. After acceptance, release the session mutex before
the synchronous persistence callback, retaining the outer tree mutation ordering
until it completes. Provider workers never publish or append independently.
The freshness identities and their guards are explicit:

- `CodingSessionState.compaction_snapshot()` and `compaction_matches()` read the
  exact binding and history epoch under the session mutex. Append, mirror, clear,
  rebuild and accepted compaction advance the history epoch, including equal
  writes. Fresh binding identity covers begin/refresh/rebind/unavailable and
  assignment-only model/reload publication. D1b0's run witness remains separate.
- `NativeSessionTree.mutation_epoch` uses the tree lock. `_load_entries`,
  `_append_entry`, `branch`, `reset_leaf`, `set_leaf` and `branch_with_summary`
  advance it before fallible work, detecting restored leaves and failed writes.
- `RunControlState.tree_pointer_epoch` uses the outer tree/effect lock; every
  `session_tree` assignment advances it, including changes away and back.
- `SessionGenerationRef.publication_epoch` uses the session mutex and advances
  when `publishing()` opens and closes, including refused or failed publication.
  Exact generation identity/id and terminal admission also participate.

Header-callback acquisition is followed by another freshness check before
provider admission. Deterministic tests cover these readers and writers.

Use the current terminal interruption and external-abort bridges. Summary output
does not stream as ordinary assistant transcript content. Late provider completion
cannot mutate state or disk. Queued input observed during summary work remains
with the existing input owner; no summary-specific queue may consume and lose it.
Preserve the canonical executor's completion/cancellation ordering and already-
admitted callback semantics.

### Automatic-generation cancellation

D1b propagates cancellation out of automatic summary generation before
constructing the ordinary provider request, invoking its request hooks, or
refreshing tool renderers. Returning only a diagnostic would let terminal Escape
fall through into that request. Use the typed cancellation alternative in the
[harness request-preparation contract](harness-spec.md#d1b-request-preparation-cancellation-contract)
and the canonical loop's existing cancellation settlement. Keep the accepted
message anchor and exclude request-only overlays in the returned history.
Steering and local commands stay with the existing input owner; no pending
cancellation side channel or synthetic request is needed. Manual cancellation
ends `/compact` without publication and leaves queued input deliverable.

Reuse the branch-summary request/result capability through a small shared helper,
while preserving branch-navigation behavior. For compaction, include prior summary
as labeled content only in the auxiliary summary request, the exact dropped
prefix, and a final explicit
summary instruction in `messages`: provider adapters can ignore `user_prompt`
when messages are present. Execute with the captured provider binding and the
existing run-owned canonical executor, not a newly resolved provider or a second
agent loop. Summary requests and results stay private product content.

### Guarded run context and stale automatic summaries

D1b depends on the implemented D1b0 [guarded coding-run publication contract](harness-spec.md#guarded-coding-run-publication-contract).
A retained authorized model control can replace the binding and clear history
between the coding run's initial history read and its preparation mirror. This
lost update is prevented by the run witness before semantic summary generation
adds its longer freshness window. Guarding only the final history mirror or only the
summary result would miss earlier canonical message and history writes.

The state-owned run witness distinguishes context replacement from legitimate
canonical appends, mirrors, usage and compaction. Every such mutable publication
validates a present witness under the state mutex. With no active run witness,
existing session-thread-owned shell-context and manual-compaction writes remain
admitted. Run-dependent history/binding reads validate it too, including the
prepared-history read-back after request hooks. The composition append wrapper
holds the outer tree/effect lock across live acceptance and synchronous durable
append, releasing the state mutex before filesystem I/O. Context replacement
still succeeds through its current owner; a stale run's next guarded write raises
a bounded coding-context exception before mutation.

D1b additionally checks its complete summary freshness snapshot. Any freshness
loss during automatic summarization raises the same bounded exception before
ordinary request construction or turn-start publication, even when cancellation
and mutation coincide. Unchanged-context cancellation uses the normal typed
preparation alternative. Manual stale compaction reports refusal without summary
publication. The broader summary snapshot still detects same-context appends,
metadata/tree changes and generation windows that need not replace a run context.

The exception escapes `CodingSession.run`; existing controller exception cleanup
closes the session lifetime. It does not return to the prompt, produce a normal
finalized result, or manufacture the ordinary provider-cancellation event path.
Existing callers retain their current exception handling; D1b0/D1b add no new
CLI/RPC error-to-result conversion or traceback-hiding promise. Error text carries
no summary or provider failure body. Live recovery after context replacement is
not selected.

Tests preserve the newer binding/history/tree/usage at guarded write boundaries,
including the pre-summary preparation mirror, canonical append, summary acceptance
and final mirror; they also pin exceptional lifetime cleanup and no ordinary
provider request after an automatic stale summary. Already admitted synchronous
observer/render callbacks retain their existing semantics; the guard does not
retract callback effects or replay earlier tools.

### Failure and validation

Generation exceptions, failed/empty/tool-requesting results, cancellation,
extension veto and stale snapshots publish no history, suffix, counters or tree
entry. Extension veto invokes no provider. Diagnostics remain bounded and do not
copy provider failures or summary bodies into workflow metadata.

Retain the current **state-first persistence** contract: accepted live history,
suffix and counters advance once, then the durable callback runs. Persistence
failure propagates before success diagnostics/footer and does not roll back live
state or retry the append. The tree also updates memory before its file write,
so disk may differ from both live and tree memory after failure. This is not an
atomic durability promise. Preserve the tests
`test_compaction_callback_failure_propagates_after_state_advances` and
`test_manual_compaction_persistence_failure_precedes_diagnostic_and_footer`.

Decisive D1 acceptance covers prior-summary input; exact retained tool exchanges;
a second cut after only one added group; reopen after both cuts; no synthetic
summary group; failure/veto/cancel and manual stale refusal; automatic-stale
exceptional lifetime cleanup with no ordinary provider request or stale mutable
publication; late completion; each guarded
invalidating writer; unchanged request-only extension overlays; automatic
compaction affecting the same next request; and state-first persistence failure.
Full product summaries stay private in provider context and native JSONL.
Workflow events remain counts/labels only.

Expected owners are coding state/product-context coordination, a bounded summary
service, `repl/provider_selection.py`, `repl/collaborators.py`, and wiring, with
narrow tree provenance and cancellation adapter changes as needed. Do not move
provider construction into the headless coding package. Update these docs,
relevant harness/session-tree/session-storage contracts and release notes with
the behavior. Run focused history/state/product-session/compaction/tree/overlay
and cancellation tests, `just check`, `just docs-build`, `git diff --check`, and
PTY smoke when interactive cancellation changes.
