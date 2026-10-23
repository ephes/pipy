# Durable execution preservation checkpoint — 2026-10-23

This is a historical record of an owner-requested pause, followed by the owner's
request to continue on the same day. It is not the current delivery checklist.
Current status and acceptance gates belong to the
[backlog](../backlog.md) and [evidence ledger](2026-10-19-durable-execution-evidence.md#de04b1b3b-resumed-integration).

## State at the pause

The last delivered main commit was `33d39457`, the default-document-delta slice
DE04b1b3a. The unfinished DE04b1b3b implementation was preserved in WIP commit
`3b1c395d` on `wip/durable-de04-checkpoint-20261023`. The preservation commit was
not an independently reviewed delivery. Its complete source and summary-safe
receipts were archived locally outside temporary storage before the pause.
Historical attribution remains: Astra MAX implemented the checkpoint, callback
authority and unload mechanism; Codex coordinated metadata and final gates.

The preserved focused run reported **1260 passed, 18 skipped, 2 failed**. Its two
failures were stale Session closure hashes and the then-absent checkpoint
manifest. Architecture checks passed 348 cases. Python 3.11 and 3.12 each ran 20
barrier repetitions; the former skipped unavailable eager-task-factory cases.
A final actual Pi/native pair passed using a reused verified dependency closure.
That capture did not substitute for fresh cumulative acceptance chains, a full
check, documentation verification or independent review. None had passed for B
at the pause.

## Continuation

The owner subsequently requested continuation. Codex restored the whole WIP as
an uncommitted squash delta against delivered main, retaining the preservation
branch. The missing checkpoint manifest and current closure cascade were then
integrated, with the new bounded `session-checkpoints-j-v1` acceptance phase.
Shared usage docs and release notes were synchronized with the implementation.
Fresh validation and review outcomes are recorded in the evidence ledger.

The pinned actual Pi capture remains 20,738,601 raw bytes, SHA-256
`56f77e4d93a581d0c269eb46d0941a8aa108d853eb5fc3eef6740042a24fada9`.
Continuation repacked its compressed representation from zlib to LZMA, retaining
those exact raw bytes and their hash. The earlier full-base and default-delta
fixtures remain byte-identical historical evidence.

The owner's persisted-value decision remains binding: loaded callbacks, cache
and publications use the replayed persisted owned value; creation uses its final
value; empty operations retain the committed incarnation and count. History,
as-of reads, fork and migrations remain DE27. Product runtime defaults and the
43 open parity requirements are unaffected by this preservation record.
