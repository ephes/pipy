# Opt-in durable headless CLI (DE12)

## Start, resume and inspect

`pipy durable` is an explicitly reserved command family. It selects the native
durable SDK before legacy argument parsing, signal handling, settings, resources
or JSONL session resolution. Existing interactive, JSON, RPC and synchronous SDK
defaults stay unchanged until DE44. No CLI/TypeScript wrapper executes a product
request; one native SQLite PathOwner, Session and scheduler remain authoritative.

```bash
pipy durable start --store ./run.sqlite --workspace . \
  --provider openai-codex --model gpt-6.1-sol --thinking low --no-tools \
  --prompt 'Explain this isolated example' --format json
pipy durable resume --store ./run.sqlite --workspace . --format json
pipy durable inspect --store ./run.sqlite --format json
pipy durable inspect --store ./run.sqlite --task 5 --format json
```

Store paths are explicit. Start creates a new store; resume and inspect open
exact existing paths and never create/import/fall back to a JSONL file. The
workspace is explicit for execution, and the SDK validates its captured binding.
Inspect is passive allowlisted metadata, with optional numeric `--task` or
`--conversation`. It never constructs a catalog, reads credentials/material,
initializes/migrates task code, refreshes auth or enables execution. Terminal
resume without a profile reuses committed output without handlers or credentials.

For active resume, supply the same argument family as start: `--provider`,
`--model`, optional `--thinking` and `--no-tools`. Authentication uses the normal
existing environment/AuthStore authority. This command family has no credential
flags; unsupported/invalid values receive a fixed error without token echoes.
Unsupported Milestone A compaction, tree/fork/reset, queued input, extensions,
RPC/streaming and full controls refuse before store creation/admission/effects.
There is no implicit second input or new runtime host.

Start accepts exactly one `--prompt` or `--stdin`. Stdin supports a UTF-8 pipe,
FIFO or bounded regular file, preserves CRLF/content bytes after decoding, and
requires nonempty input within the normal user-message bound. A TTY or other
unsupported descriptor refuses before opening a store; `--prompt` works from a
normal TTY. EOF with empty input is an error. A crash after empty identity
creation but before atomic input admission leaves a real empty store: resume
returns fixed `no_input`, never success. Recover explicitly through the public
SDK `open_durable_product_session` → guarded `submit` → `resume`, or start at a
different new path. Do not delete/overwrite the old store or infer accepted input
from an empty identity.

## Recovery and explicit authorization

Current profile authority applies before first prepare. Once prepare commits,
the existing private request envelope pins provider/API/model, target, thinking,
options, pricing, cache/session and cutoff facts. Credentials remain live through
their normal authority. Prepared target/default drift refuses before transport;
terminal receipt reuse performs no old request/preparation and never reprices
saved usage. A pre-provider-receipt crash can reissue a possibly paid request:
one committed task/usage settlement does not prove one HTTP/paid attempt.

Intact recovered unsafe executes use the SDK's C1 uncertain interrupted result
without invoking the tool again. `resume --no-tools` can use this exception only
when all unresolved work has closed noninvoking authority; it does not authorize
pre-intent or old-request dispatch. Historical blocked intent/effect uncertainty
stays held even if no effect is observed. An operator can explicitly authorize a
distinct new attempt under the unchanged retry guards:

```bash
pipy durable authorize-retry --store ./run.sqlite --workspace . \
  --provider openai-codex --model gpt-6.1-sol --thinking low --no-tools \
  --task 8 --nonce 0123456789abcdef0123456789abcdef --format json
pipy durable resume --store ./run.sqlite --workspace . \
  --provider openai-codex --model gpt-6.1-sol --thinking low --no-tools \
  --task 9 --resolution-nonce 0123456789abcdef0123456789abcdef --format json
```

Use the returned numeric winner for resume; example IDs are illustrative.
Both nonce flags require exactly 32 lowercase hexadecimal characters, such as
`uuid.uuid4().hex`; reuse the same nonce for winner recovery.
Authorization alone does not execute. Same-nonce retry recovers its winner;
stale states, receipt-bearing blocked tasks and retained terminate controls do
not become safe replay. Old uncertainty stays private and is never forged into
old success. Nonfinal terminate can hold a waiting parent/later pending call;
final/only terminate can leave generation pending/blocked. Inspect derives the
retained-control reason. C1 does not migrate historical blocked states; C2 owns
that repair/adjudication boundary. Missing/current capabilities never waive
provider/material guards or authorize replay.

## Output, signals and physical lifetime

For start/resume, `--format text` prints the final completed assistant text;
`--format json` emits the completed envelope. Inspect and authorize-retry always
emit their closed JSON report, including when `--format text` is supplied.
JSON run format version2
emits `pipy-durable-output`: committed completed messages/rounds, saved usage,
safe task outcomes and metadata. It preserves the current public DTO's complete
content blocks, IDs/correlation, optional zero/absence values, rich provider/tool
receipt payloads and structured-content absence versus null. Normal [tool details](tool-details.md) preserve scalar/array/null/absent values
with an explicit details_state, and optional finite nested evidence without
changing the original value. Historical canonical v1 unknown presence remains
unknown; this is DE13a normal-value support, not interrupted-error continuation.
Inspect/authorize-retry formats are unchanged. Requests/settings/private auth
material/operator audit and arbitrary error strings never appear in output.
JSON emission adds no nesting cutoff to otherwise admitted OwnedJson values.
This full-content output is product data, not a metadata-only workflow event.

| Exit | Meaning after actual cleanup |
| --- | --- |
| 0 | Completed committed outcome, successful output/flush and physical close |
| 1 | Failed/faulted/aborted outcome or fixed runtime refusal |
| 2 | Invalid/unsupported command, signal domain or stdin descriptor |
| 3 | Durable held/uncertain work |
| 128 + signal | First handled TERM/HUP/INT (INT is130) |
| 141 | Broken output pipe |

The CLI supports main-thread default signal dispositions and preserves inherited
SIG_IGN, including nohup/background ignores. Custom callable handlers or off-main
invocation refuse before Runner/catalog/store work; embeddings use the async
SDK. Non-raising TERM/HUP/INT handlers retain the first exit disposition and
remain installed through real close/join and caller-loop cleanup. Exact prior
dispositions restore afterward. Repeated signals cannot inject SystemExit or
KeyboardInterrupt into cleanup. EOF/cancel/broken pipe/output backpressure and
loop teardown do not detach accepted acquisition, invocation or finalizers.
Noncooperation keeps close/path ownership pending; there is no timeout-success.
CLI code never changes inherited caller-controlled file-status flags. Readiness is advisory;
the existing owned-call seam retains actual read/write/flush work off the caller
loop. Cleanup stops further I/O admission and joins an accepted call/finalizer
before releasing the product/backend lease. A peer can invalidate readiness,
and a noncooperating syscall still keeps physical ownership pending.
Output flushes any existing caller-buffered prefix before writing the command
payload, then flushes again before reporting success.

Inspection is logically read-only under the existing same-owner lease/schema
policy: effective-WAL RO validation can create absent empty WAL/SHM or update
lock diagnostics; normal supported RW open/close can checkpoint sidecars. No
blanket byte-preservation or lock-bypass claim follows. Missing/pre-lock refusals
retain their no-creation contract. See [storage](session-storage.md) and
[inspection](durable-inspection.md).

## Evidence and remaining scope

Native CLI tests use real subprocess SIGKILL at admission, provider in-flight/
response/receipt, assistant, tool intent/effect/receipt and final outcome. Actual
reopen counts distinguish requests, applications, prepares and usage. Fake
wires cover all supported construction families, SSE framing/terminal behavior,
credential rotation, drift and privacy; fresh-process openers test real held
invoke/finalizer/acquisition/output/cleanup ownership. A bounded synthetic live
Codex start plus resource-free terminal resume/inspect also ran; HTTP attempts,
paid count and unobserved provider session facts are not inferred from it. Full
supported live lifecycle acceptance remains with later product adoption.

The source-closed `headless-cli-v1` oracle uses genuine pinned public Harness
creation/submit/wait/reopen and COMMIT/SIGKILL/reopen request phases. Pi exposes
no durable CLI. The common profile compares only committed logical message,
tool order/effect counts, saved usage and unchanged terminal reuse. Exact raw
observations remain retained. Pi looks up the current model again at request;
native protected prepared-target refusal is a separately checked policy
difference (`pi-current-model-vs-native-prepared-target-refusal-v1`), not raw
target/API equivalence or whole-P16 closure. Generated IDs normalize only by
actual within-side correlation. Schema-valid CLI0/1 controls and malformed CLI2
bodies remain distinct, and every pure API attester runs in its own fresh
process, imports explicit pure lazy roots, rejects loaded-unlisted/source drift
and never reruns effects. All mutant bodies live under `mutants/`; all process
receipts/streams under `processes/`; producer/recipe files remain immutable.

Original DE12/Milestone A acceptance and all43 whole OPEN parity rows remain.
DE14–16 graph/version/cleanup, DE17 retries, DE18/19 queues, DE20 context,
DE24/25/30 nested/extensions/full controls, DE26 streaming, DE27/28 history/
views and DE27/28/41 publication budgets, and DE41–44 broader adoption/defaults
retain their owners. Pi successor-generation versus native same-parent graph
and quadratic retained request history remain explicit DE15/28 and DE27/28 debt.
