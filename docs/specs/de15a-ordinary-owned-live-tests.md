# DE15a ordinary-owned-live test matrix

This is the complete bounded requirement matrix, not a claim that every row has
executed. At the 2026-10-29 core checkpoint, 42 new core cases and 245 retained
cases passed, with 238 affected consumer cases separately passing. The immutable
source epoch and qualifications are in the [evidence](../evidence/de15a-ordinary-owned-live.md).
Broad, paired reference and final integration evidence were still pending at
that checkpoint. Every graph must label whether its candidate actually commits;
native/Pi paired states are bounded projections.

| Family | Required witness |
|---|---|
| Direct ordinary work | Genuine root/child in Memory+SQLite; pending/running/waiting/completing count, terminal does not. Root itself never counts. Query-only storage/counter/revision equality. |
| Transitive ended path | Genuine Tx-created parent→child→owned conversation graph; native intermediate tasks end through direct real tx.set_task terminalization after graph construction, not scheduler execution. Later foreground task keeps true. Settle it, false despite unrelated background sibling. Reopen SQLite and repeat. |
| Background boundary | Genuine conversation-owned background task and its otherwise ordinary subtree excluded; foreground sibling included. Explicit background root query tests start-node semantics. Direct task-owned background is rejected by create_task but genuinely committable through separate set_task: establish it in an earlier real commit, then capture/query in a new transaction and exclude its subtree. This is native-only committed-state evidence, not a malformed/kernel-only case or Pi ownership-mutation parity. A background flip staged after capture remains a refused candidate change. |
| Candidate view | Capture beforewrites, create actual child/conversation, query sees staged graph; replace child with terminal via real tx.set_task and see live-membership removal. Terminal intermediate still exposes live descendant. SameTx later candidate change yields fresh answer, no retained effective cache. Compare immediately before/after query while intentional staged mutations remain constant. |
| Candidate authority | No caller overlay/done/tombstone inputs. Join each actual staged task to the same private _tasks write tag/record under lifecycle gate; genuine create+later set_task retains create authority. Phantom replace and every replacement of a committed terminal task refuse before traversal (include terminal→terminal and attempted revival). Both parent and background replacement flips refuse in each direction. Test these with real set_task calls; abort the surrounding transaction after observing refusal, without fabricating internal entries. Inconsistent tag/tuple cases alone are labelled kernel controls. Capture-afterwrite ReadAfterWrite, including failed write argument prologue. No graph reads aftercapture. Real surrounding rollback discards staged candidates; no stale view reused in next transaction. |
| Create authority versus assembly | Real create→set_task owner/background changes retain the genuine create tag; valid final topology is classified as creation, with no original-history fingerprint or Pi mutation-parity claim. Observe the query result and unchanged staged mutations separately from the actual surrounding commit or explicit rollback. Real new task and new owned-conversation cases then change their owner to terminal/completing/abort-marked before the query: assert the graph answer without an owner-state/abort-mark restriction, then observe the genuine outer Tx assembly refusal and durable rollback. This is not a query promise of commit readiness. |
| Projection retention | A labelled kernel witness supplies valid typed rows with irrelevant payload sentinels and verifies the query-owned captured/staged graph stores only IDs/edges/background/terminal classification, with no retained row/payload references after projection. Existing real Memory/SQLite graph cases still establish behavior. This is not an RSS assertion or a promise that the surrounding transaction/backend releases its own objects. Construction cannot accept overlays; has after settlement/wrong thread gates before graph or argument access. |
| Lifecycle | Capture/has only on actual SyncTx Session callback/line; aftersettlement and wrongthread fail before answer, real close/cancellation preserves existing Session joins. No new worker/actor family required. |
| Validation | Real separate set_task commits establish dangling owner, cross-conversation owner, self-owner and multi-task cycle, one isolated case per graph; a later capture/query refuses globally even for an unrelated valid root. Include an offending endpoint beyond the first 128-row page and an otherwise early-true root. These are genuinely committed native states, not kernel-only fixtures. Duplicate/repeating/nonmonotone cursors, wrong typed-row/protocol returns and ID-collision cases that genuine APIs cannot produce remain explicitly labelled kernel controls; no SQL corruption presented as genuine producer. Unknown root/error precedence exact. |
| Deep graph | Genuine constructible deep acyclic graph exceeds recursion depth; iterative traversal, background cut and lateforeground sibling; validate complete view before earlytrue. No invented arbitrary size/count/cap. |
| Nonownership edges | conversation.parent forklineage, waiting.on, trace/links do not create descendants; real legitimate values wherepossible, typed controls where publiccreation cannotproduce. |
| Retained admission | Existing _owned and B4–B7 strictLeaf implementation/selected tests byteheld. A live-query false does not make strictLeaf true; terminal/background owned descendants remain strictLeaf refusals. No Product abort/completing dispatch. |
| Public Pi oracle | Adapt pinned harness-ownership test's held P→ended B→ended A→owned C→foreground D/background Z using actual Pi FullHarness public completion/state and barriers. Native B/A terminal states come from explicit real tx.set_task commits, not native execution; retain distinct timestamps/outcomes/lifecycle and claim only the bounded graph projection. Preserve raw held/completed records, reports/callbacks. Native true/false only corresponds to witnessed ordinary-work blocking; no private #ownedLive probe or unsupported native completing. |
| Integration | Ordinary wholequality, meaningful core+realgraph tests and sourceclosed bounded comparator on their actual integrated source; existing final gates remain separate. No future test counts or PASS assumed. |

## Core checkpoint coverage and remaining witnesses

The 42 core cases exercise scalar liveness and root exclusion, transitive ended
nodes and owned conversations, background boundaries, genuine staged create and
replacement authority, global bad-owner graphs, ID and transaction lifecycle
errors, create-side assembly separation, and labelled projection/protocol
controls. Query-only no-write windows start after intentional minting/staging;
legitimate mint consumption is not a rollback defect.

Deep/later-page and cold graphs, broader FIFO/close/cancellation/NOACK witnesses,
and nonownership-edge controls belong to the separate broad partition. The
public Pi held-completion comparison belongs to the reference partition. The
core result does not establish those pending witnesses, mutation completeness,
Pi execution parity, or final whole-feature acceptance. Existing retained fence
checks are separate from new query assertions; a false live query does not
relax strict-root-leaf admission.

## Isolated overlay checkpoint — 2026-10-29

The separate Broad2 epoch passed 22 cases and the complete Reference7 module
passed 23 cases. The paired oracle exercised three native/Pi schedule traces
TTF/TFF/TTF, public held/release boundaries and genuine cold reopening. Their
actual source boundaries, failed fixture epochs and observed provenance are
recorded in the [evidence](../evidence/de15a-ordinary-owned-live.md). These results
do not retroactively extend Core42 or establish the final integrated gates.
