# Explicit historical tool adjudication (DE13c2)

An old blocked tool may have applied an effect even when it has no saved result.
`adjudicate_interrupted` explicitly ends that uncertain attempt without invoking
it again. It acknowledges a committed decision; it does not claim success,
completion, an absent effect or a provider answer. Ordinary automatic resume
continues to hold historical blocked records.

## SDK and CLI

Open the existing product store with its matching workspace and provider profile.
Use the task ID reported by passive inspection and a new 32-character lowercase
hexadecimal nonce. Keep that nonce for acknowledgement recovery:

```python
from uuid import uuid4

nonce = uuid4().hex
ack = await product.adjudicate_interrupted(task_id, nonce=nonce)
# ack.to_tree(): {"task_id": task_id, "decision": "interrupted"} or "receipt"
result = await product.resume()  # A separate authorization to continue.
```

```bash
pipy durable adjudicate-interrupted --store ./run.sqlite --workspace . \
  --provider openai-codex --model gpt-6.1-sol \
  --task 8 --nonce 0123456789abcdef0123456789abcdef --format json
pipy durable resume --store ./run.sqlite --workspace . \
  --provider openai-codex --model gpt-6.1-sol --format json
# Read-only recovery of the same committed decision needs no profile:
pipy durable adjudicate-interrupted --store ./run.sqlite --workspace . \
  --task 8 --nonce 0123456789abcdef0123456789abcdef --format json
```

IDs are illustrative. CLI acknowledgement is always the bounded JSON object,
including with `--format text`; it never implicitly resumes. Exit 0 means the
decision was acknowledged, exit 1 is a fixed operation refusal, and invalid
arguments exit 2. Refusals print `Durable command unavailable: <code>` without
private payloads. The SDK raises `DurableProductError` with the fixed `code`.

Fresh admission requires a matching profile. Profile construction may read
credentials from the environment, AuthStore or models.json; the decision does
not use or refresh them and invokes no provider. Actual material and transport
checks remain at the next PREPARE/request. Same-nonce winner replay can omit the
profile; a supplied profile with a different selection is still `incompatible`.
No winner without a profile is `profile_required`, not fresh authorization.

## Evidence and refusals

Only a supported model-owned blocked child in a valid waiting tools round is
eligible. Parent, CID, call, key, saved offer, slot, prefix and later-control
checks remain mandatory. Abort, background, nested synthesis ownership and
retained terminate restrictions remain closed. A reason such as `invalid_result`
does not distinguish a tool failure from reporting infrastructure loss.

| Actual receipt evidence | Decision |
| --- | --- |
| No document, with intact saved prepared intent | Commit an uncertain interrupted receipt. |
| Known exact default-empty document, with intact intent | Explicitly replace that empty default with an interrupted receipt. |
| Supported valid receipt with matching non-null intent | Preserve the actual first winner and return `receipt`. |
| Opaque, newer, malformed or mismatched receipt | `receipt_held_needs_repair`; preserve it unchanged. |
| Absent/default-empty receipt and no intact intent | `not_supported`; no fabricated preparation or key. |

A valid first winner keeps its complete saved value and counters even if current
progress or restart metadata is invalid. New synthesis instead requires strict
matching progress, preserves its output/details/diagnostics and validates current
restart metadata. Missing counters remain null. This supplies no cumulative
restart policy or exactly-once effect guarantee.

Missing/changed current tools can admit only this noninvoking decision. Every
other unresolved state must satisfy the existing C1 noninvoking recovery rules.
Pre-intent calls, old provider requests, other blocked work and mixed rounds
still requiring safe replay refuse. Restoring the original catalog is a control.
The next request keeps only unchanged current capabilities from the saved offer,
in saved order; new tools cannot enlarge authority. The header is not rewritten.

## Ownership and nonce recovery

The product control lease pauses and drains an already opened healthy scheduler;
it does not open one merely to decide. Failed or unopened cached owners refuse
`closed`. Capture and revalidation use the same Session line. One atomic commit
writes a separate private conversation audit, preserves or installs the actual
receipt, and moves the same child to settlement. Existing settlement alone
produces the tool entry; the audit is not another result carrier.

Same nonce and proposal recover the decision. A different task with that nonce
is `conflict`; a second nonce cannot apply a second decision. Authority changed
between capture and commit is `stale`. Retry has a separate nonce namespace and
retains its ANY-receipt fence and NEW-attempt meaning. If a retry-successor root
is involved, later resume still requires its existing `DurableRunSelection` or
CLI `--task <successor> --resolution-nonce <retry-nonce>`.

Acknowledgement recovery validates actual state: the still-present receipt
before settlement, the single actual receipt2 carrier after v2 settlement, or
the normal output/call/key projection after v1 settlement. After a real scheduler
fault or abort it acknowledges only the recorded decision. A supported validation
reblock also needs the unchanged receipt document identity and full value.
Unknown, replaced or corrupted bindings refuse `incompatible`; replay does not
clear a block or permit execution. Caller cancellation does not detach an
admitted commit or release its lease early. Uncertain commit failure produces
no invented acknowledgement.

Audit authority, prepared arguments, workspace, keys and fingerprints stay
private. Inspection, completed messages, CLI output and provider requests do not
export them. The existing fact1, fact2 and canonical formats remain unchanged.

## Scope and remaining work

C1 landed at `5897353`; this bounded C2 operation landed at `06823ad`.
Unsupported intent-less/opaque historical authority remains owned by later DE13
repair. DE14a restart parking is a separate active increment; broader DE14
policy, P15/P16, nested DE24, ownership/retention and whole DE13/P21 parity
remain open. The
[implementation contract](specs/2026-10-27-de13c2-explicit-historical-adjudication-plan.md)
pins actual historical producers, transaction races and replay witnesses; a
contract alone is not runtime or review acceptance.
