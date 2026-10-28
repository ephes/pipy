# DE14b2 — owner-local restart-policy publication

Adopted after actual delivered DE14b1 `2d3c7c0cda15c19be9333eba500f1220ba3c4fd3` source/API refresh. The normative body below is unchanged from reviewed contract `49951fb51ecb525d5829471e278f37e822427157b29fd62ac355482ff1053520`. The independent plan gate passed after its lifecycle and exact-policy-input clarifications. This is an implementation contract, not a claim of whole-code, runtime or parent acceptance. Source line numbers in the inventory are delivered-baseline anchors.

## Finite API and behavior

Add only these private-runtime methods on `TaskScheduler`:

- `set_restart_limit(value: object) -> bool`
- `async aset_restart_limit(value: object) -> bool`

They publish one normalized immutable `RestartPolicy` for the same opened, healthy owner. Return `True` if the normalized limit changed and `False` if already equal. This acknowledgement attests in-memory publication only. Preserve existing `RestartPolicy` normalization exactly: an exact `RestartPolicy` instance copies its limit, and exact builtin `int`/`float` values use the existing numeric rules, including invalid-input fallback, exact binary64 integer treatment and positive infinity represented internally by `limit=None`. Other types, including `bool`, numeric subclasses, `RestartPolicy` subclasses and hostile conversion objects, keep the constructor's default-five fallback without invoking their conversion hooks. No new DTO, persisted policy, nonce, audit, setting registry or durable write.

The methods **do not call `_notify`, enable, pause, reset a counter, invoke work or alter an active invocation**. A pending independently triggered pass may observe the new policy; an already enabled drain may use it at its next reservation. The caller uses existing `resume()` to request a pass deterministically, including an enabled-but-idle owner. Paused owners remain paused. Lowering the limit cannot revoke an invocation already admitted. Updating a limit is distinct from `retry`/`aretry`, whose parked-only counter clearing and enable-after-success semantics remain intact.

Public Product/SDK/CLI expose no new update operation. Their captured policy remains immutable for their lifetime. Calling the private setter through an internal Product scheduler is not a supported Product API; public inspection and reset must not silently read a stale mirror of an exposed live setting. Product live configuration requires its own later contract.

## Publication and lifecycle ownership

Use the existing Session line to serialize publication with reservation, retry and owner inspection, then the existing owner condition for the policy reference and lifecycle metadata. Do not introduce another lock or lifecycle flag. Never wait for the Session line while holding the condition.

The implementation should use a narrow scheduler-owned job on the existing private Session line primitive (`_on_line_sync` or equivalent existing ownership path). It must not create an empty transaction, call Storage.commit, emit commit listeners, or mislabel mutation as a public `read_on_line` read-only job. No generic Session/Storage API is added.

Before handing off work, refuse same-line synchronous/asynchronous reentry with the existing Session guard. The sync method refuses event-loop-thread blocking, directing callers to the async method. Normalize with the unchanged `RestartPolicy` constructor outside the condition. Acquire the real Session ticket; preserve existing health-before-line-seal admission and grant checks. Under the condition, recheck actual owner failure first (`SessionFailed`), then line seal or retired host (`SessionClosedError`), then `_opened` (false refuses with bounded `RuntimeError`), before comparing/replacing the reference. Capture any error reference under the condition and construct/raise the bounded error outside it. No exception payload enters inspection output. A refusal publishes nothing.

`_opened=True`, together with the health/close checks, is the positive readiness predicate. `_opening` is a one-shot open-admission marker: it remains true after successful recovery and must not veto a usable owner. When `_opened=False`, neither value of `_opening` establishes whether recovery is active, cancelled, failed or abandoned; do not infer such outcomes from these flags or add a mirror lifecycle flag. A known Session/host failure or close takes the precedence above. A still-healthy, unsealed but not-opened owner simply receives the not-opened refusal, including a stranded admission with no established completion. Cancelling an `aopen` waiter does not cancel its owned recovery; once that recovery actually publishes `_opened=True`, the same healthy owner is eligible. Do not rewrite recovery or the one-shot-open contract.

The assignment is the publication point. Retain the displaced policy in a local and release it outside the condition. A close that wins before publication refuses it; a close/error delivered while leaving the line after publication does not undo it. A normal returned boolean means publication/no-op completed, not that the owner remains open afterward.

Async transport must own the actual line job through completion, using the existing shielded/joined off-thread ownership behavior rather than a detached `to_thread` task. Cancellation after accepted work may still allow publication; it is never an undo acknowledgement. Loop teardown/close must join the real worker and retain lease ownership until its ticket retires. Retrieve late exceptions. No cancellation path recreates an old policy to simulate rollback.

## Complete current policy-reader inventory and required alignment

| Current site | Contract for the new mutable reference |
| --- | --- |
| `scheduler.__init__`, line488 | Initial immutable value; construction precedes publication. |
| `scheduler._reserve` / `_candidate`, line951 | Capture policy under the condition **inside** the reservation Session-line callback, before candidate scanning; use that one value for all candidates in that pass. Pass the captured value explicitly to the parking decision. Do not refresh after awaits or per candidate. |
| `scheduler.aretry` callback, line714 | Capture under the condition while owning the retry transaction's Session line; use that immutable value for parked-only clearing through that transaction. A later publication cannot retroactively change the decision. |
| `inspection._inspect_owned_task`, lines295/309 | After acquiring the Session line, capture policy with definition/failure metadata under the condition. Use one value for terminal/completing and live results; preserve terminal no-code behavior. |
| New setter | Sole post-construction writer, on Session line then condition; no other assignments/direct mutable-property exposure. |
| Standalone `inspect_task`, `inspect_live`, async/store wrappers | Continue taking an explicit limit and constructing a local immutable policy. Do not pretend standalone inspection knows an owner's live value. |
| `DurableProduct.__init__` line192; inspection345/351; scheduler construction389; preflight562/577/593; reset670 | Remain an independent **immutable** Product policy, with no setter/public exposure in this child. Preserve all existing values and authority checks. |
| `RestartPolicy.parked/cleared`, inspection projection | Pure operations on the captured immutable value; no global mutable policy lookup. |

`inspect()` returns committed task records only and does not acquire policy authority. Tests/source inventory must reject unnoticed additional reader/writer sites. All replacements release displaced references outside the condition. No application callback, migrator or user getter runs under it.

## Unchanged durable authority

No schema/codec/numeric-record-ABI changes. Counters are neither fabricated, cleared nor made cumulative. Structural progress still resets the streak according to the existing kernel; document/output chatter and healthy close retain their current semantics. Valid finite over-limit records park even with a saved receipt. Raising/unlimiting the budget may permit later normal settlement, but never changes the saved receipt's payload/counters or C1/C2 first-winner, historical hold, malformed-progress/counter, catalog, nonce, abort or lease fences. Migration-failure identity/cache is untouched. A same-value update cannot retry a failed migration.

## Executable acceptance, kept bounded

Use Memory and SQLite for publication/no-op, inspection and ordering. Arm zero-write checks **after** allowed open/recovery and quiescence: real commit-hook counts plus transaction-consistent all-table/revision equality, with a positive revision-only oracle control. Separate expected subsequent resume/task writes from the setter itself.

Pin these material cuts:

1. Same owner parked at the default: raise finite or infinity, observe changed owner inspection with byte-identical task/receipt/counter, prove no new wake/enable, then explicit resume permits the same task. Lower before the next admission; no-op returns false. Fresh owner uses its supplied constructor policy, not an unrecorded durable setting.
2. Active invocation held while policy changes: no cancellation/overlap/authority rewrite; its next admission obeys the captured later policy. Paused owner does not run. Enabled idle owner receives no setter-generated wake; an independently caused pass may still proceed.
3. Ordered publication-versus-reservation and publication-versus-retry barriers establish which real Session ticket won. Reservation's entire multi-candidate pass and retry's parked-only decision use one snapshot. Owner inspection never combines policy epochs. Preserve same-pass ordinary migration-failure/later-task/reconcile liveness.
4. Reentry from a line callback, not-opened/failed/closed owner, held publication with cancellation, and close/loop teardown: bounded refusal or truthful post-publication outcome, actual worker retirement and lease join, no rollback fiction. A deterministic test barrier holds actual recovery after its real `acommit` has released the Session line but before `_opened=True` publication; a setter acquiring the line in that window refuses without changing policy. Release the barrier and prove the successful owner accepts publication even though `_opening` remains true. Separately cancel an `aopen` waiter while recovery is held and prove recovery still completes; cover actual failed/closed recovery with the established owner state, not fabricated flag meanings. Barriers must not hold the metadata condition. Lost acknowledgement followed by the same value is an idempotent false on the same still-live owner.
5. Existing numeric edge vectors and C1/C2 saved-winner/absent/invalid metadata controls remain; default-limit receipt parking and explicit higher/unlimited settlement preserve their original payload/privacy assertions. Product APIs stay unchanged; closed SDK/DTO consumer guards run early.

Pinned Pi scheduler.ts1395–1417 and harness-blocked.test.ts269–298 support raised policy observed on a later pass with explicit resume. A narrow comparison may exercise that common behavior and unchanged record/counter authority. Python setter acknowledgement/no automatic wake, raw numeric-domain differences and standalone-versus-owner inspection are explicit side-specific facts; no invented Pi setter API or full public retry parity.

Before final implementation acceptance: actual delivered refresh, finite independent review, source-closed focused/reference evidence and required full/platform/resource gates. Docs/releases/backlog/coverage must record actual b1 delivery and this bounded new policy cell without claiming DE14/DE14b/P closure. Generic registry publication, active handover, public Product configuration/retry-enable parity, arbitrary settings/listeners, orphan/abort/cleanup and DE15/16/23 remain open.
