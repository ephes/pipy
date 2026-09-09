# Pi-Style Session Tree Workflow

Status: **shipped** (2026-06-02). The native product session tree described
below is implemented and is the product session source of truth for
pipy-native. The deterministic conformance gate
`scripts/parity_checks/session_tree_conformance.py --json` proves the full
workflow end to end through the product runtime; see the Verification Plan at
the end. This document remains the behavioral specification.

Implementation map:

- Native tree core: `src/pipy_harness/native/session_tree.py`
  (`NativeSessionTree`, entry value objects, JSONL parse/write, `get_branch`,
  `get_tree`, `build_context`, `fork_from`, `continue_recent`,
  `native_sessions_root`/`list_session_dirs` for the cross-project store).
- Command helpers: `src/pipy_harness/native/session_tree_commands.py`
  (selection semantics, filters, rendering, `resolve_startup_session`,
  `resolve_session_ref` (local-first then global cross-project lookup),
  `list_all_native_sessions`, and the pure picker helpers
  `build_session_picker_rows`/`format_session_picker_label`/`sanitize_label_text`).
- Typed classification for `/session`, `/name`, `/new`, `/tree`, `/resume`,
  `/fork`, `/clone`, and `/compact`:
  `src/pipy_harness/native/coding/commands.py`. Concrete private-tree writes,
  navigation, extension gates, rendering, and diagnostics remain composition
  concerns.
- Runtime wiring + concrete effects for `/session`, `/name`, `/new`, `/tree`,
  `/resume`, `/fork`, `/clone`, durable `/compact`, and branch summaries:
  `src/pipy_harness/native/coding/session.py`.
- Live-TTY `/tree` selector and the interactive session picker overlay
  (`TerminalUi.run_tree_selector`, `run_session_picker`, and the
  standalone `run_startup_session_picker`) in
  `src/pipy_harness/native/tui.py`.
- CLI startup flags (`-c`/`-r`/`--session`/`--session-id`/`--session-dir`/
  `-n`/`--name`/`--fork`/`--no-session`): `src/pipy_harness/cli.py`
  (`_resolve_native_startup_session`, `_validate_native_session_flags`,
  `_run_startup_resume_picker`), with `--no-session` selecting
  `NullSessionRecorder` to suppress the metadata archive record too.

Shipped follow-ons (previously deferred, now complete):

- The interactive `/resume` **picker overlay** (type-to-search, `Tab`
  current-project/all-projects scope, `Ctrl+P` path column, `Ctrl+S` sort,
  `Ctrl+N` named-only, `Ctrl+R` rename, `Ctrl+X` delete with confirmation,
  `Esc`/`Ctrl+C`/`Ctrl+D` cancel) and the `-r` interactive **startup picker**
  ship through `TerminalUi.run_session_picker` /
  `run_startup_session_picker`. On a non-TTY (captured) stream `/resume` keeps
  the deterministic listing plus the `named`, `rename <ref> <name>`, and
  `delete <ref> --yes` subcommands, and `-r` continues the most recent native
  session. The startup and in-session pickers share the same engine.
- Pi-equivalent startup-session flags ship: `--session-id <id>`
  (open-exact-or-create), `--session-dir <dir>` (native store root override),
  `-n`/`--name <name>` (name the session at startup), the Pi mutual-exclusion
  errors (`--fork`/`--session-id` vs `--session`/`--continue`/`--resume-session`/
  `--no-session`), and the cross-project `--session <partial-id>` fork prompt.
  The old metadata-only `--resume RECORD` / `--branch LABEL` repl flags are
  retired (the native tree is the product session source; `pipy-session
  resume-info` stays the separate archive utility).

Shipped related follow-ons:

- `/export` (HTML/JSONL), `/import`, `/share`, and top-level `--export` now
  ship through the export/distribution track and operate on this native product
  session tree.

Original research basis (still accurate as the behavioral target):

This document defines the pipy target for Pi-compatible `/tree` behavior. It is
based on the local reference checkout at `/Users/jochen/src/pi-mono`, especially:

- `packages/coding-agent/docs/sessions.md`
- `packages/coding-agent/docs/session-format.md`
- `packages/coding-agent/src/core/session-manager.ts`
- `packages/coding-agent/src/core/agent-session.ts`
- `packages/coding-agent/src/modes/interactive/components/tree-selector.ts`
- `packages/coding-agent/test/agent-session-tree-navigation.test.ts`
- `packages/coding-agent/test/session-manager/tree-traversal.test.ts`
- `packages/coding-agent/test/tree-selector.test.ts`

Pipy should match Pi's user-facing workflow through pipy-owned Python
boundaries. This is not a TypeScript port, but it does require a real durable
conversation tree. A metadata-only archive cannot implement Pi-style `/tree`,
product resume, branch switching, fork/clone, or compaction replay by itself.

This is also a bug-fix direction for pipy-native: product sessions must work
like Pi sessions. The existing metadata-only `pipy-session resume-info` path is
useful as a conservative archive/catalog utility, but it is not sufficient as
the product session source for Pi-style workflows.

## Target Outcome

`pipy` / `pipy repl` opens and maintains a
raw, private, durable native session tree, analogous to Pi's
`~/.pi/agent/sessions/...` files. In a live session, `/tree` opens an
interactive selector over the current session's full history. Selecting a prior
point moves the active leaf inside the same session file, optionally writes a
branch summary, and lets the user continue from that point without creating a
new session file.

The native session tree is the product source of truth for full-history resume,
context reconstruction, `/tree`, `/fork`, `/clone`, `/resume`, `/new`, and
durable compaction. `pipy-session` remains a separate metadata/archive surface
and must not be used as the product-session substitute for these workflows.

Python embeddings can reopen one exact durable tree through
`pipy_harness.sdk.open_product_session(...)`. That public boundary strict-loads
the JSONL file and verifies that its absolute header cwd resolves to the supplied
workspace. It refuses malformed or unknown records, duplicate headers or entry
IDs, and invalid parent or anchored-compaction ancestry; ordinary CLI and
internal `NativeSessionTree.open(...)` callers retain their permissive recovery
mode by default. Public embedded lifetimes also hold one process-local canonical
file-path lease, so aliases cannot concurrently append through separate facades.
This is not a cross-process lock.

### D6b public transition contract

**D6b3 shipped:** persistent public product sessions and the JSONL RPC lifetime
can fork an exact active in-memory entry, clone their current leaf, create an
empty sibling tree, or
strictly replace their active tree through the native coordinator. Terminal
command adoption remains deferred.

The in-place public transition operations are owned by one native
`SessionTransitionCoordinator`, composed once by `native/repl/wiring.py`, not
by `NativeSessionTree` factories or the outer `ProductSession` facade. The tree
keeps creation, strict open, branch projection and durable append ownership. The
coordinator receives the run-control setter, extension gate and state rebuild
ports from wiring; `product_api.py`, RPC, and eventual terminal adoption call
its typed port without a native import of the outer facade.

The same neutral module owns the process-local registry and one guarded
`CanonicalSessionLeaseSlot` per active lifetime. A prepared handoff holds a
candidate claim alongside the slot's old claim. Failure before pointer
publication aborts only the candidate; after the setter returns, a non-failing
publish swaps the slot to the candidate exactly once and releases the old claim.
Native wiring claims the RPC lifetime's initial persistent tree before readiness
and binds the slot's idempotent finisher to the controller lifetime. The
encompassing public or RPC lifecycle finishes the adopted slot on teardown.
No result value exposes the lease, and no other reader or writer accesses the
slot's mutable current claim.

D6c3 adopts terminal `/resume` while retaining the D6c0 inventory's separation
for the remaining commands. Resolved numeric, ID, path and different-picker
targets now use the coordinator's canonical claim, strict load and workspace
check; the controller lifetime holds the terminal slot. D6c4 selects `/new` as
the next bounded adoption because its direct tree replacement does not update
that slot. Picker cancellation/current selection, list/rename/delete, `/tree`,
import/export, and fork/clone remain separate presentation or transition
regions.

### D6c2/D6c3 terminal `/resume` adoption contract

D6c2 fixed this contract and D6c3 implements it. The
stream-driven terminal lifetime owns one `CanonicalSessionLeaseSlot`. After
successful native composition and before `session_start` or any command can run,
it canonical-claims the initial persistent tree and binds the slot's idempotent
finisher to the existing `CodingSessionController` lifetime. An ephemeral
initial tree gets an empty slot. Initial claim conflict refuses startup; a
failure after the claim but before lifetime start releases it. Normal exit,
fatal exit, startup failure after attachment, and a failure after switching all
finish the slot exactly once after the existing shutdown, extension-session and
chrome cleanup. Product and RPC lifetimes retain their already shipped binding
paths; D6c3 adds no second lease owner.

`SessionCommandEffects` continues to classify `/resume` input. Bare captured
input lists sessions; `named`, `rename`, and confirmed `delete` retain their
current store/presentation behavior; bare live input retains the existing
picker. Numeric, ID-prefix, path-stem and explicit-path resolution also remain
in the terminal adapter. Only a resolved direct target or a different picker
target enters the existing `SessionTransitionCoordinator`; neither the
coordinator nor lease slot lists sessions, runs a picker, resolves a reference,
or writes terminal output.

For a resolved target, canonical equality with the active persistent path is a
completed no-op before an extension hook, candidate claim, load, tree setter,
history rebuild, extension-input clear, redraw, or lease release. Both direct
aliases and picker selection use the picker's existing "already on the selected
native session" diagnostic and the standard footer. A different target retains the
existing `session_before_switch` gate and its bounded denial diagnostic. The
native coordinator then claims the canonical candidate before reading it,
strict-loads it, and requires its stored absolute cwd to resolve to the fixed
terminal workspace. Terminal adoption does not select a new workspace or
provider and does not reconstruct provider/model settings from the target.

Successful adoption orders effects as follows: terminal resolution; extension
gate; candidate claim; strict load and workspace validation while claimed;
guarded `RunControlState.session_tree` publication; slot publication and old
claim release; one `CodingProductSessionCoordinator` history rebuild; extension
input clear; one terminal custom-entry redraw; one sanitized resumed-session
diagnostic; then the existing standard footer. Tree publication remains
state-first and does not add `session_start`, `session_shutdown`, provider/tool
execution, workflow-archive output, or a second lifecycle generation.

Expected failure before pointer publication is recoverable at this terminal
boundary. An extension veto or candidate lease conflict returns a bounded
diagnostic; strict-load failure, malformed or unknown records, and non-absolute
or different-workspace headers become one sanitized target-load diagnostic.
They release only the candidate claim and retain the old tree, lease, history,
extension inputs, and usable terminal lifetime, followed by the standard
footer. Raw target content and exception text are not printed. An unexpected
tree-publication error still propagates and retires the lifetime. Once the tree
setter and lease handoff publish the candidate, rollback is not promised: a
history-rebuild or extension-input-clear failure propagates the typed published
target failure, suppresses redraw/success diagnostic/footer, and controller
teardown releases the adopted target once.

The guarded inventory stays closed. `_LEASE_LOCK` guards the canonical registry;
the slot lock guards only current-claim read, prepare, publish and finish;
`RunControlState.session_tree` and its mutation binding remain under the one
coding-effects lock; the product coordinator remains the history rebuild owner;
and `CodingInputQueue` remains the extension-input owner. The terminal command
adapter receives a typed transition operation, not a lease or mutable tree
handoff. No lock spans an extension hook, filesystem I/O, redraw, diagnostic or
footer write.

Coverage proves initial claim conflict and cleanup, exact current/adopted lease
release on normal and fatal exits, strict claim-before-load, same-canonical-path
no-op for direct and picker targets, old-session usability after expected
prepublication refusal, and the success/fatal order above. It retains picker
cancel/current, list/named/rename/delete and reference-resolution behavior.
`/tree`, `/import`, `/new`, `/fork`, `/clone`, startup `-r`, SDK/RPC behavior,
cross-process locking, provider reconstruction and compatibility retirement are
non-goals.

### D6c4/D6c5 terminal `/new` adoption contract

D6c4 fixes the contract and D6c5 implements it. `SessionCommandEffects` keeps
classifying `/new`, emitting terminal diagnostics and applying the standard
footer. It receives one typed terminal-new operation from composition; it does
not receive the lease slot, create or publish a tree, or rebuild history itself.
The terminal operation uses the existing detailed `session_before_switch`
presentation callback exactly once rather than also running the coordinator's
silent public/RPC gate.

For a persistent source, successful replacement orders the switch gate, fresh
tree creation beside the source file, canonical candidate claim, guarded
`RunControlState.session_tree` publication, slot publication and old-claim
release, one history rebuild, one extension-input clear, the existing sanitized
started-session diagnostic, and one footer. It does not redraw custom entries.
The slot must hold the canonical source before the operation starts. Extension
veto occurs before creation. Candidate lease conflict is recoverable after
creation: the old tree, claim, history and extension input remain usable, while
the newly created durable artifact may remain. Create failure and unexpected
pointer-publication failure are fatal. Rebuild or clear failure after
publication is state-first and fatal, emits no success diagnostic or footer,
and controller teardown releases the adopted candidate exactly once.

An ephemeral terminal source retains the existing Pi-aligned behavior: the slot
must be empty, the gate runs before `NativeSessionTree.create(...,
persist=False)`, and guarded publication is followed by one history rebuild and
one extension-input clear. No durable file or canonical claim is created, and
the slot stays empty through success and teardown. Create, publication, rebuild
and clear failures keep the same fatal timing as the persistent path. The public
`ProductSession.new_session()` and RPC operation continue to refuse ephemeral
sources; terminal preservation does not broaden those contracts.

Coverage pins persistent initial/adopted release, create-before-claim ordering,
old-session usability after lease conflict, veto-before-create, one fresh
history rebuild/input clear without redraw, sanitized success/footer ordering,
published-failure candidate release, and the empty-slot/no-file ephemeral case.
Structural coverage proves terminal `/new` no longer creates or assigns a tree
directly. Fork/clone, import, startup selection, SDK/RPC behavior, provider or
workspace changes, lifecycle hooks, workflow capture and cross-process locking
are non-goals.

### D6c6/D6c7 terminal `/fork` and `/clone` adoption contract

D6c6 fixes the shared terminal contract and D6c7 implements it. The command
adapter retains `/fork` argument parsing, current-filter any-entry reference
resolution, clone/bare-fork current-leaf selection, refusal and success
diagnostics, and the standard footer. It receives one typed terminal fork
operation from composition and no lease or mutable tree handoff. A resolved
entry ID and `fork` or `clone` operation enter the coordinator with the existing
detailed `session_before_fork` callback; the public/RPC silent gate does not run
a second time.

The coordinator requires a persistent active tree whose canonical path matches
the terminal slot. An explicit unknown reference refuses before the gate. Bare
fork and clone refuse an absent current leaf, aligning the terminal with the
public product contract and Pi's empty-clone behavior. Once selected, success
orders the fork gate, `NativeSessionTree.fork_from_snapshot` of the guarded
active in-memory tree, child canonical claim, guarded tree publication, slot
publication and source-claim release, one history rebuild, one extension-input
clear, the existing command-specific sanitized diagnostic, and one footer.
There is no custom-entry redraw or provider/tool call.

Recoverable terminal presentation is explicit. An ephemeral source refuses
before reference resolution with the existing `pipy: /fork requires a
persistent native session.` or `/clone` equivalent. An unresolved explicit
reference keeps `pipy: no tree entry matched {argument!r}.`; an absent current leaf
emits `pipy: nothing to fork yet.` or `pipy: nothing to clone yet.`. The
extension hook emits its existing detailed denial, without a second generic
message. A child lease conflict emits `pipy: new native session is already
active.`. Each recoverable outcome retains the old tree, slot, history and
extension input and is followed by one standard footer. The gate runs before
child creation; a lease conflict occurs after creation and may leave the child
artifact. Creation or unexpected pointer-
publication failure is fatal; publication failure aborts the prepared child
claim and retains the source slot. Rebuild or clear failure after publication
is state-first and fatal, emits no success diagnostic or footer, and controller
teardown releases the adopted child once. No durable source reopen participates
in the transition.

Coverage pins exact resolution/gate/snapshot/claim/publication/handoff/rebuild/
clear/diagnostic/footer order, source release before rebuild, child exclusivity
during the remaining lifetime, adopted-child release on normal and fatal exits,
old-session usability after recoverable refusal, and a later `/new` or `/resume`
after fork/clone. Each explicit refusal mapping and one-footer behavior has a
focused witness. Structural coverage proves the terminal handler no longer
calls `fork_from` or assigns the tree directly. Import/export, picker/tree
management, startup selection, SDK/RPC behavior, lifecycle hooks, workspace and
provider selection, workflow capture and cross-process locking remain non-goals.

### D6c8/D6c9 terminal `/import` adoption contract

D6c8 fixed this contract and D6c9 implements it. The terminal adapter keeps the
exact quoted/unquoted, tilde and cwd-relative path parsing; the case-sensitive
standalone `--yes` token; the raw first confirmation; the raw missing-workspace
confirmation; controlled diagnostics; and the standard footer. The first
confirmation happens before the transition. Its displayed source and detailed
`session_before_switch(operation="switch")` target are the expanded and
cwd-joined path spelling without canonical resolution. The coordinator validates
that a persistent active source matches the terminal lease slot, or that an
ephemeral source has an empty slot, before running that detailed gate once.
Mismatch is fatal under the terminal transition convention: no hook, staging,
candidate claim, tree/history/input mutation, command diagnostic or footer runs.

An allowed operation calls a presentation-owned staging callback outside the
lease and coding-effects locks. Staging preserves the established recovery
boundary: permissively validate the imported header and entries; ask whether to
replace a missing recorded cwd with the fixed terminal workspace; select the
active persistent file's directory or the default native-session directory for
an ephemeral source; choose a collision-free name; copy and chmod the file;
rewrite only the copy's header when accepted; permissively open the durable
copy; and select its last imported entry. The source is never changed. Import
does not adopt the strict product reopen loader or infer a different runtime
workspace, provider or model from the imported header.

The staged candidate has a durable path. The coordinator claims that path before
guarded `RunControlState.session_tree` publication, publishes the slot and
releases the old source claim, rebuilds history once, and clears extension input
once. Success returns to terminal presentation for the existing sanitized
imported-session diagnostic and one footer. It performs no custom-entry redraw,
provider/tool call, lifecycle restart or workflow capture.

Usage, either confirmation cancellation, an extension veto, and a controlled
`NativeExportError` keep their current bounded diagnostic and footer behavior.
The veto precedes source validation and copying. Candidate lease conflict occurs
after copy/open, emits `pipy: imported native session is already active.`, and
retains the old usable tree, slot, history and extension input; the copied
artifact may remain. Unexpected copy, copied-header rewrite or open errors stay
fatal with their current partial-artifact effects and no command footer.
Unexpected pointer-publication failure aborts the candidate claim, retains the
source slot and may leave the copy. Rebuild or clear failure after publication
is state-first and fatal, emits no success diagnostic/footer, and terminal
teardown releases the adopted candidate exactly once. A later diagnostic or
footer failure likewise does not roll back successful adoption.

Coverage pins both confirmations, the exact noncanonical gate target, veto
before source validation/copy, permissive import, collision and copied-only cwd
rewrite, persistent and ephemeral destination/slot behavior, claim-before-
publication, source release before rebuild, old-session usability after claim
conflict, candidate release after publication failure, state-first postpublish
failure and terminal teardown. Structural coverage proves transfer composition
does not assign the controller tree or rebuild terminal history directly.
Separate persistent-source and ephemeral-source mismatch witnesses prove the
fatal pre-hook cutoff. Hook and staging instrumentation proves the
coding-effects lock is unowned there and only guarded pointer publication takes
it; the lease slot lock likewise does not span either callback.
Public/RPC and one-shot CLI runtime behavior, export/share, strict reopen,
cross-process locking and provider/workspace selection remain non-goals.

`fork(entry_id=None)` requires a persistent active tree. Its default is the
current leaf; an explicit `entry_id` is any exact known native entry, including
model, compaction, label, branch-summary, custom, or message entries. It copies
only that root-to-entry branch from the guarded active in-memory tree; public
fork does not permissively reopen its own source file. `clone()` is the same
operation at the current leaf, uses the existing `operation="fork"` extension
gate vocabulary, and rejects an empty tree. The child uses the
active source file's parent directory, has a fresh ID and entry IDs, records
the source file in `parentSession`, reattaches labels to their mapped IDs,
remaps branch-summary references, and inherits the source session name. It never rewrites the source
file and accepts no caller-selected destination.

`new_session()` requires a persistent active tree and creates a fresh empty
tree in that active file's parent directory for the existing facade workspace.
`switch_session(path)` canonicalizes one exact path and detects a same-path
no-op without reading it. For another target, it runs the extension gate, claims
the candidate through D6a's neutral native canonical lease registry, then
strict-loads it and validates its stored absolute cwd against the facade
workspace while claimed. Neither operation performs lookup by ID/recency,
changes workspace, or adopts provider/model metadata. This is still not a
cross-process file lock.

Persistent creation is deliberately prior to adoption, not a transaction. After
an allowed fork/clone/new, a child/fresh path may remain after a later creation,
claim, publication, or rebuild failure; a failed creation write is not promised
to contain a valid header or complete content. Strict-load failure creates no
new artifact. The product coordinator first publishes the selected tree under
its coding-effects/tree lock and then rebuilds the provider-visible history from
that selected tree. A rebuild failure cannot promise atomic restoration: the
public facade fails closed and retires before it raises a typed published-target
failure. Candidate/open/load failure before pointer publication releases the
candidate lease and leaves the old tree, lease, and usable facade untouched.
An immutable target uses `session_path=None` when that retained tree is
ephemeral.

The full interactive workflow runs in the tool-loop product TUI — pipy's
single Pi-like daily-driver shell. The non-TTY captured-stream fallback uses the
same native product session store for ordinary user/assistant conversation
persistence and native-session resume where it can do so without a full selector
UI; selector-only commands may print captured-stream diagnostics instead of
falling through to a provider prompt.

The matching command family is:

- `/session`: show current native session file, id, current leaf, message and
  token/cost counters when known.
- `/tree`: navigate the current session tree in place.
- `/fork`: create a new session file from a previous user message.
- `/clone`: duplicate the current active branch into a new session file.
- `/resume`: select another session file and switch to it.
- `/new`: start a new native product session.
- `/name <name>`: store a human-readable session name.
- `/export [file]`: Pi's HTML/JSONL export command; shipped through
  [export-distribution.md](export-distribution.md).
- `/share`: Pi's private gist/share command; shipped through
  [export-distribution.md](export-distribution.md).

Startup/session CLI parity maps Pi's surfaces semantically (all shipped):

- `pi -c`: continue the most recent native session for the workspace.
- `pi -r`: open the native session picker at startup on a real TTY; on a
  non-TTY (captured) stream it deterministically continues the most recent
  native session.
- `pi --no-session`: ephemeral mode; do not create or write a native session
  tree, and suppress the `pipy-session` metadata-archive lifecycle record too
  so the run is fully ephemeral like Pi.
- `pi --session <path|id>`: open a specific native session file or partial id.
- `pi --session-id <id>`: open the native session with this exact id for the
  current workspace, or create a fresh one carrying it. The id becomes part of
  the session filename, so it is validated as a safe filename component
  (`[A-Za-z0-9_-]`, 1-128 chars); path separators, `..`, and control bytes are
  rejected so a hand-picked id cannot escape the session store.
- `pi --session-dir <dir>`: use `<dir>` as the native session store root
  instead of the default state directory (per-project encoded-cwd subdirs live
  under it). `$PIPY_SESSION_DIR` is the separate metadata-archive root and is
  deliberately not honored here; only `--session-dir`/`$PI_SESSION_DIR` override
  the native store.
- `pi -n`/`--name <name>`: name the native session for the run (applied after
  it is created/opened/forked).
- `pi --fork <path|id>`: fork a native session file or partial id into a new
  session.

Startup flag constraints to match Pi:

- `--fork` is mutually exclusive with `--session`, `-c`/`--continue`,
  `-r`/`--resume-session`, and `--no-session`; combining them is a hard error
  (`packages/coding-agent/src/main.ts:189-201`).
- `--session-id` is mutually exclusive with the same set; combining them is a
  hard error (`packages/coding-agent/src/main.ts:216-238`).
- `--session <partial-id>` that resolves to a session in a *different* project
  does not open it directly. pipy reports the other project and prompts the user
  to fork the session into the current directory, aborting cleanly if declined
  (`packages/coding-agent/src/main.ts:231-247`).

Pipy command names may differ where the existing CLI requires it, but the
product behavior should be equivalent and must use the native session store, not
`pipy-session resume-info`.

The implementation may land in reviewed milestones, but the objective goal for
this track is the full Pi-style product workflow through `/session`, `/name`,
`/new`, `/tree`, `/resume`, `/fork`, `/clone`, `/compact`, native-session
continue/open/fork startup flags, and branch summaries. HTML export and
share/upload remain known Pi-feature deferrals unless a later slice explicitly
includes them.

## Product Storage Model

Add a pipy-owned native session tree store separate from the existing
metadata-first `pipy-session` archive, and make it the product-session store for
pipy-native.

Recommended root:

```text
~/.local/state/pipy/native-sessions/--<encoded-cwd>--/<timestamp>_<uuid>.jsonl
```

The native session JSONL is a private product transcript, like Pi's session
files. It intentionally contains raw user prompts, assistant messages, tool
call/result content, bash command/output records where applicable, compaction
summaries, branch summaries, labels, model changes, custom/custom-message
entries, and session naming entries because `/tree` and product resume need
them. It must live outside git by default, use owner-only permissions where
practical, and never be synced by the existing metadata archive recipes unless a
future explicit sync policy says so.

The existing `pipy-session` archive remains the summary-safe learning/catalog
surface:

- `pipy-session list/search/inspect/export/resume-info` continue to default to
  metadata-only records.
- Native tree files are not searched or exported by those commands unless a new
  explicit native-session command opts in and warns that it reads transcripts.
- Harness lifecycle events may record only safe native-session metadata, such as
  native session id, file stem/path label, current leaf id, branch count,
  message counts, and relationship labels. They must not copy prompt/model/tool
  bodies into the metadata archive.

This split is the required redesign and bug fix: pipy gets Pi-compatible full
session history for the product runtime, while the existing archive remains a
summary-safe learning/catalog surface for day-to-day reflection and sync.
Product resume and tree workflows must read the native session store, not
`pipy-session resume-info`.

## Why The Metadata Archive Still Exists

Pi has one product session store. Pipy will have two local stores by deliberate
choice: the Pi-like native product session tree for interactive product state,
and the existing `pipy-session` metadata archive for summary-safe learning,
review handoffs, cross-agent capture, and sync-friendly catalog/search surfaces.
The archive survives because it records workflow lessons and external-agent
metadata without making raw transcripts the default searchable/synced artifact.
It must not drive product resume or `/tree`; it may only reference native
sessions through safe labels and counters unless a future explicit transcript
command opts into reading raw native session files.

## JSONL Shape

Use an append-only JSONL file with a header followed by tree entries.

Header:

```json
{"type":"session","version":1,"id":"uuid","timestamp":"2026-06-02T12:00:00Z","cwd":"/path/to/project","parentSession":"/optional/source.jsonl"}
```

The `version` field above is a pipy-owned native-tree format version, not a
claim of matching Pi's session-format number. Pi's current session format is
version 3 (`packages/coding-agent/src/core/session-manager.ts:28`;
`packages/coding-agent/docs/session-format.md:25`). pipy keeps its native tree
as its own pipy-owned format and versions it independently.

Every non-header entry has:

- `type`
- `id`: short stable id, unique within the file
- `parentId`: parent entry id or `null`
- `timestamp`: ISO timestamp

Minimum entry types:

- `message`: provider-visible messages, including user, assistant, tool result,
  and pipy tool/batch records needed to rebuild context.
- `model_change`: provider/model selection changes.
- `thinking_level_change`: reasoning/thinking-level selection changes, using
  Pi's entry type name.
- `compaction`: in-place context compaction summary with `firstKeptEntryId`,
  `tokensBefore`, and optional `retainedUserEntryId` for a noncontiguous
  retained user plus suffix.
- `branch_summary`: summary created while leaving a branch through `/tree`.
- `label`: user label for any entry, with undefined/empty label clearing it.
- `session_info`: display name.
- `custom` and `custom_message`: active Pi entry types used by extensions
  (`packages/coding-agent/src/core/session-manager.ts:129`;
  `packages/coding-agent/src/core/agent-session.ts:511-516`). pipy should
  support them once its extension API lands.

The schema and parser support `model_change`, but the current product `/model`
and model-cycle composition path does not append that entry. This pre-existing
implementation/specification gap is tracked as a dedicated compatibility
correction in `docs/backlog.md`; the Phase 3.1d command-ownership extraction
preserves it rather than silently changing persisted JSONL or resume behavior.

The in-memory session manager keeps:

- all entries in append order,
- an `id -> entry` map,
- resolved labels and label timestamps,
- a current `leafId`, initially the latest entry when loading an existing file.

Appending any ordinary entry uses the current leaf as `parentId` and advances
the leaf to the new entry. Existing entries are not modified or deleted. Moving
around the tree changes only the in-memory leaf or appends new metadata/summary
entries.

## Context Reconstruction

Provider-visible context is rebuilt by walking from the active leaf to root,
then reversing that path. Only entries on the active branch are sent to the
provider.

Rules to match Pi:

- `message` entries contribute their message bodies.
- `custom_message` contributes as a user/custom message regardless of its
  display flag; display controls TUI rendering only.
- `branch_summary` contributes a branch-summary message.
- `model_change` and `thinking_level_change` entries affect current runtime
  settings but are not user/assistant text.
- `compaction` contributes its summary first, then keeps only messages from
  `firstKeptEntryId` through the compaction boundary and all later active-branch
  messages.

When `retainedUserEntryId` is present, reconstruction instead retains that exact
actual user message once plus the suffix beginning at `firstKeptEntryId`.
Compactions are then applied chronologically to the effective retained sequence,
so a later ordinary cut cannot resurrect cycles removed by an anchored cut.
Both references must still exist in that sequence at their historical cut and
must describe the canonical safe tool-cycle selection. Invalid anchor-aware
records fail load/reconstruction. Legacy-only branches retain their existing
latest-boundary behavior. Model and thinking settings always reconstruct from
the full raw ancestor path, including entries excluded from message context.
Once a branch contains an anchored cut, later ordinary compaction boundaries
are checked against the effective sequence before append; a rejected boundary
does not advance the tree. Malformed anchor-aware JSON fails with `ValueError`
so session-list readers can omit the invalid file without exposing decoder
implementation errors.

The coding product uses `NativeSessionTree.build_coding_context()` to project the
same retained entries with parallel structural entry IDs and a separate optional
compaction summary. Its summary becomes the coding system suffix, so resumed
summaries do not count as user groups in later cuts. Ordinary `build_context()`
continues to return its synthetic summary message; the JSONL format is unchanged.
Custom-message and branch-summary groups keep their existing projection semantics,
including duplicate text and hidden custom messages. Origin lookup uses message
identity or the immutable last-loaded provenance, never a text search.

A compaction action resolves its suffix boundary and optional retained-user
reference before live acceptance against the effective coding projection. The
tree rejects synthetic custom/branch users and validates the canonical cycle
cut. Failure to map a durable cut refuses without changing live context.
Accepted transitions remain state-first:
live history/summary/counters advance before synchronous persistence, and an
append failure propagates without rollback. Every destination rebuild replaces
the branch summary while preserving current-run counters; a new run restores
summary context with fresh counters.

Before that acceptance, semantic compaction summarizes the previous summary and
exact removed messages through the run's canonical provider executor. Tree
mutation/navigation and active-pointer epochs join the guarded state/generation
snapshot: equal writes, restored leaves and refused publication windows invalidate
pending summaries. Failed, cancelled or stale generation appends no compaction
entry. The accepted summary remains full-content private product data; native
JSONL and other tree projections retain their existing format.

The selected [D3b contract](harness-spec.md#within-run-compaction-contract-d3b)
now has its durable retained-user format and reconstruction prerequisite. Forks
require an explicit map and strictly remap both anchored references, while
shared JSON/export/extension views
carry the optional field. Known-limit automatic selection can now create this
anchored cut after a retained whole-group candidate remains oversized; manual
and unknown-limit selection remain whole-group. Files containing the new field
require an anchor-aware reader.

Canonical tool results require both the provider correlation id and tool name.
The stable JSON format does not add a `tool_name` field: reload resolves it from
the matching assistant tool call on that result's own parent chain. A historical
record with a null or unmatched correlation id remains a private storage-only
message so descendants and forks keep their ancestry, but it is omitted from
provider context rather than fabricating tool identity or dropping the entry.

### Cross-provider tool-correlation replay (D8a)

The durable tree keeps each provider correlation ID exactly as returned. Codex
Responses needs this raw value to preserve its compound `call_id|item_id` and
reconstruct both fields during same-provider replay. Reopen with a different
explicit provider therefore does not rewrite, migrate or annotate stored
messages.

Target adapters compile that canonical identity only while building an outbound
request. Every non-Codex shared wire maps the assistant tool-call ID and its
paired tool-result ID with the same pure function. Values matching
`^[A-Za-z0-9_-]{1,64}$` pass through byte-for-byte. Other values become `tc_`
plus the unpadded URL-safe Base64 encoding of the full SHA-256 digest of the raw
UTF-8 value. The result is deterministic, idempotent, within the shared target
alphabet and length, and collision-resistant without a mutable mapping table.
Canonical in-memory values and JSONL stay unchanged.

The mapping applies to the shared Anthropic Messages wire (including Bedrock),
Google generate-content wire (including Vertex), OpenAI Responses wire
(including Azure), and Chat Completions wire (including compatible providers).
Google also emits the mapped ID on both `functionCall` and `functionResponse` and
retains a returned nonempty `functionCall.id`; responses without an ID retain
the existing provider-prefix/index fallback. The dedicated Codex wire continues
to split its raw compound value and does not use the portable mapper.

Tests must prove safe passthrough, deterministic distinct unsafe mappings,
call/result pairing through each shared wire, Google response-ID retention,
unchanged Codex replay, and one strict durable reopen into a different real
adapter with fake transport. No provider provenance, rich-content union,
history repair or session-schema migration is introduced. Live-provider
acceptance and semantic answer quality remain unverified.

Not every `message` entry is provider-visible exactly as stored. The
leaf->root context build (`buildSessionContext`) collects the active branch,
but some entries are filtered later during LLM message conversion: for example
a bash execution marked `excludeFromContext` is dropped in `convertToLlm`
(`packages/coding-agent/src/core/messages.ts:141-170`), not inside the
leaf->root walk. The walk/reverse/compaction description above is otherwise
correct.

The current in-memory compaction implementation can remain as the first
provider-history reducer, but once native tree storage lands, `/compact` must
append a real `compaction` entry so resumed/tree-navigation sessions rebuild the
same context.

## `/resume` Picker Behavior

Status: **shipped.** `/resume` opens an interactive session picker overlay
(`TerminalUi.run_session_picker`) over native product session files. It
runs no provider turn and no model-visible tool call while the picker is open,
renders inline (no alternate screen), repaints coherently on resize, and
sanitizes user-controlled names/paths so they cannot inject terminal escape
sequences. On a non-TTY (captured) stream `/resume` recognizes the command
locally and prints the deterministic listing plus the `named`/`rename`/`delete
--yes` subcommands; it never falls through as a provider prompt. The `-r`
startup picker shares the same engine through `run_startup_session_picker`.

Pi controls, as shipped (pipy keybindings noted where they differ from Pi):

- typing searches sessions (name, id, and workspace path);
- Up/Down move selection; Enter opens the selected native session;
- Esc/Ctrl-C/Ctrl-D cancel (pipy keeps Ctrl-D as cancel, matching its other
  selectors, so delete is bound to Ctrl-X — see below);
- Tab toggles current-project / all-projects scope
  (`packages/coding-agent/src/modes/interactive/components/session-selector.ts:170,792`);
- Ctrl+P toggles the file-path column;
- Ctrl+S cycles the sort mode (recent / name);
- Ctrl+N filters to named sessions;
- Ctrl+R renames the selected session (in-overlay edit; persisted through the
  native store, never the metadata archive);
- Ctrl+X deletes the selected session after an in-overlay `[y/N]` confirmation
  (Pi uses Ctrl+D, which pipy reserves for cancel). The currently active
  session cannot be deleted.

Deletion matches Pi's safety posture: use the `trash` CLI when available, and
otherwise remove the native session file after explicit confirmation. Deleting
native product session files never deletes `pipy-session` metadata archive
records.

## `/tree` Behavior

`/tree` opens an interactive in-frame selector in the product TUI. It runs no
provider turn and no model-visible tool call while the selector is open.

The selector shows the current session tree, not other session files. It should
include:

- ASCII tree connectors and indentation.
- active-path marker for entries on the current leaf path.
- current selection highlight.
- search by typing.
- filters: `default`, `no-tools`, `user-only`, `labeled-only`, and `all`.
- label editing on the selected entry.
- optional label timestamp display.
- fold/unfold or branch-segment jumps.
- empty-tree diagnostic.

Pi controls to preserve semantically:

- Up/Down move visible selection.
- Left/Right page.
- Ctrl/Alt Left and Ctrl/Alt Right fold/unfold or jump branch segments.
- Shift+L sets or clears a label.
- Shift+T toggles label timestamps.
- Ctrl+O cycles filter mode.
- Enter selects.
- Escape/Ctrl-C cancels.

Captured-stream fallback may initially print a clear diagnostic that `/tree`
requires a TTY, but the command must still be recognized locally and must not
fall through as a provider prompt.

## Selection Semantics

Selecting the current leaf is a no-op.

Selecting a user message:

1. Set the leaf to the selected message's parent, or `null` for the root user
   message.
2. Put the selected user text back into the editor.
3. Leave the editor editable; submitting it appends a new user entry from that
   parent, creating an alternative branch.

Selecting a custom message follows the same parent-plus-editor behavior for
every `custom_message`. When the message has no text blocks, Pi seeds the
editor with empty text rather than skipping the parent+editor path
(`packages/coding-agent/src/core/agent-session.ts:2777-2785`).

Selecting an assistant message, tool result, compaction, branch summary,
model-change, label, or other non-user entry:

1. Set the leaf to the selected entry.
2. Leave the editor empty.
3. The next prompt continues from that point.

This is the behavior that makes `/tree` different from `/fork`: `/tree` stays in
the same file and edits the active leaf; `/fork` creates a new file.

## Branch Summaries

When selection moves away from a different active branch, pipy should offer the
same choices as Pi:

1. no summary
2. summarize with default instructions
3. summarize with custom focus instructions

If the user chooses a summary, collect the abandoned path from the old leaf back
to the common ancestor with the target path. Generate a bounded summary through
the active provider, cancellable with Escape. On success, append a
`branch_summary` at the target position and advance the leaf to that summary.

Attachment position:

- For a selected user/custom message, attach the summary to that entry's parent
  because the selected text goes back into the editor.
- For a selected non-user entry, attach the summary to the selected entry.
- For a root user message, attach at root (`parentId: null`).

The summary is appended at the new (target) leaf via Pi's `branchWithSummary`
(`packages/coding-agent/src/core/agent-session.ts:2797`;
`packages/coding-agent/src/core/session-manager.ts:1188`). The
`branch_summary.fromId` field stores the attachment id/root (the target the
summary is branched from, `branchFromId ?? "root"`), not the abandoned old
leaf. Do not read `fromId` as a pointer at the abandoned branch.

If summarization is cancelled or fails, leave the session tree and leaf
unchanged.

The metadata archive may record only summary-safe counters and labels for this
operation. The native session tree stores the summary text because it is needed
to rebuild provider context.

### Branch Summary Ownership Contract (D4b2)

This contract is implemented through D4b3a for guarded publication. Canonical
branch-summary retry and cancellation are implemented through D4b3b.
Selection, attachment position, `fromId`, and editor-prefill semantics above stay
unchanged. The generic tree command helper must not append generated text after
a summarizer callback returns without a retained conditional-acceptance witness.

Existing session command/collaborator composition owns the branch operation;
`NativeSessionTree` owns entry/leaf mutation and durable append, and
`CodingProductSessionCoordinator` owns loaded-context provenance and coding
history. Add only narrow prepare/accept/persist operations to those owners.
Operation-local immutable values carry work across generation; they are not a
new session owner or shared mutable registry. Ordinary tree API readers/writers
retain their existing guards and behavior.

Capture and validate the selected target against the current tree under the
outer coding-effect/tree mutation lock, then the inner generation/session mutex.
The retained work names the exact tree and pointer/mutation epochs, old leaf,
selected target and attachment parent, abandoned messages, coding binding/history
witness, generation identity/id, publication epoch and terminal/pending-publication
state. Loaded-context provenance currently publishes alongside a history-epoch
advance under the same inner mutex. New acceptance must preserve that coupling;
do not add a provenance-only writer that could escape the captured history witness.
Changes remain stale even after an ABA restoration. Header capture and
provider work run unlocked; check the original work after callback-bearing
preparation and again at final acceptance. A fresh snapshot cannot authorize an
old summary. A refused or stale operation does not undo another accepted writer.

After generation, acquire the outer tree lock and retain it through preparation,
acceptance and persistence. First check the original work under the inner mutex
and return a bounded stale refusal if it no longer matches; release the inner
mutex again. Allocate the proposed entry id/timestamp and build its coding-context
projection under the retained outer lock, outside the inner mutex and before mutation.
Validate the candidate, including exact entry origins, against that guarded tree;
reuse existing branch/compaction projection rules rather than inventing another
transcript representation. Do not recompute the candidate after acceptance; a
failed final witness check discards it. Under outer then inner guards,
revalidate the original work and accept the branch entry/leaf and corresponding
coding history/loaded provenance together. The product acceptance operation must
not call an arbitrary history-load/rebuild callback while holding the inner mutex.
It accepts an already prepared context through the existing product owner.

Release the inner mutex before clearing the existing branch-bound extension
input families and persisting the accepted entry. Keep the outer tree/input lock
through these ordered phases: R5a intentionally holds it through tree memory and
durable append, and this contract does not remove that ordering. Preserve unrelated
seed/local-command/RPC reservations. Persistence receives the exact accepted entry
once. Append failure leaves the accepted tree, leaf and coding context in place,
with branch-bound inputs cleared; it is distinct from generation failure and
must not be disguised as a cancelled summary or rolled back. Propagate the
persistence exception through existing command failure/cleanup after unlocking;
do not catch it in the summary-generation failure handler. Emit no success notice
or editor prefill for that failure. Diagnostics and editor/UI callbacks run after
unlocking.

D4b3a established that conditional-publication seam while preserving the
then-current direct provider completion path. Failed, empty, tool-bearing or
stale generation accepts nothing, leaves editor prefill untouched, and emits
only content-free diagnostics.

D4b3b uses the existing canonical executor, one frozen tool-free request,
settings-derived immutable retry policy, private summary event sink and disabled
delta channels. Check capability on the original provider before abort wrapping;
reissue admission revalidates the same original branch work. Use D4a/D4b1 retry,
progress, accounting and cancellation rules. Failure, cancellation or staleness
before acceptance cannot append or switch the leaf, even if a late worker succeeds.
The successful operation accepts/persists once. Public retry events and ordinary
product usage must not include this private summary.

Reuse the caller's session-thread lifetime and canonical settlement. Do not hold
an exclusive thread-owned coding-effect lease across a provider worker: a retained
extension control invoked there may need that same lease. No new lifecycle or queue
owner is introduced. Manual cancellation/steering/local-command outcomes use the
existing pending-input settlement operations, as manual compaction does; editor
prefill is applied only after successful branch acceptance and persistence.
Thread-confined close/reentry rules remain unchanged, and callback-based abort
must work through the existing start gate. With no terminal or external signal,
provider execution remains synchronous and retry delay only bounded.

D4b3a tests pin stale tree/pointer/generation/history and refused-publication
windows, callback mutation, coherent acceptance and reopen, append-failure state
and error propagation, unchanged standalone tree behavior, and projection/persistence
outside the inner mutex. D4b3b adds private event/accounting and actual provider,
backoff and callback-abort cancellation tests, including retained controls invoked
from a provider worker without a new lease deadlock. Exact helper names and event
choreography belong in those implementing tests; this contract fixes ownership,
freshness, ordering and observable outcomes.

## Settings

Add native settings, backed by the same non-secret local settings store used for
existing REPL controls:

- `treeFilterMode`: one of `default`, `no-tools`, `user-only`,
  `labeled-only`, `all`.
- `branchSummary.skipPrompt`: when true, default to no summary.
- `branchSummary.reserveTokens`: provider budget reserved while summarizing
  (Pi default: `16384`).

Expose `treeFilterMode` and summary prompt behavior through `/settings` once the
interactive command exists.

## Implementation Milestones

The track may land in reviewed milestones, but the objective implementation goal
is the full Pi-style native product session workflow. Work is complete only when
the conformance gate below passes.

1. Native tree session core: value objects, JSONL parser/writer, append-only
   file lifecycle, leaf pointer, labels, `get_branch`, `get_tree`,
   `build_context`, malformed-file handling, and tests.
2. Product persistence wiring: record user, assistant, tool, model-change,
   `thinking_level_change`, compaction, branch-summary, custom, custom-message,
   and session-info entries; rebuild provider history from the active branch on
   startup/resume.
3. Product-session source switch: replace metadata-only product resume with
   Pi-like native-session open/continue/resume, including startup equivalents
   for `-c`, `-r`, `--no-session`, `--session <path|id>`, `--session-id <id>`,
   `--session-dir <dir>`, `-n`/`--name <name>`, and `--fork <path|id>`, with the
   Pi mutual-exclusion errors and the cross-project `--session` fork prompt. The
   old metadata-only `--resume RECORD` / `--branch LABEL` repl flags are retired;
   `pipy-session resume-info` stays an archive utility, not the product context
   source.
4. `/session`, `/name`, `/new`, and native `/resume`: show safe current
   native-session status, persist session names, start a new session, and
   browse/switch/open previous native product sessions. The interactive picker
   overlay (type-to-search, Tab scope, Ctrl+P path, Ctrl+S sort, Ctrl+N
   named-only, Ctrl+R rename, Ctrl+X delete-with-confirmation,
   Esc/Ctrl-C/Ctrl-D cancel) ships for both the in-session `/resume` and the
   `-r` startup picker (shared engine). On a non-TTY stream `/resume` keeps the
   deterministic listing plus the `named`, `rename <ref> <name>`, and
   `delete <ref> --yes` subcommands, and `-r` continues the most recent session.
5. `/tree` selector UI: product-TUI overlay, filters/search/labels/folding,
   selection semantics, captured-stream local diagnostic, and real-PTY tests.
6. Branch summary: abandoned-branch collection, provider summarizer,
   cancellation handling, summary-entry placement, and tests.
7. `/fork` and `/clone`: create new native session files from a selected user
   point or the current active branch, with `parentSession` metadata.
8. Durable compaction replay: `/compact` appends real `compaction` entries, and
   reload/context reconstruction honors them.
9. Export/share follow-on: `/export [file]` HTML/JSONL export, `/import`, and
   `/share` are implemented in the export/distribution track and gated
   separately by `scripts/parity_checks/export_distribution_conformance.py`.

## Verification Plan

Add one top-level deterministic conformance gate and make it the implementation
source of truth:

```sh
uv run python scripts/parity_checks/session_tree_conformance.py --json
```

The conformance script should drive pipy with the deterministic fake provider in
a temporary workspace and fail unless the full product workflow works. It must
verify that:

1. a native raw session tree file is created under the native product session
   store;
2. the file contains raw conversation entries needed for Pi-style product
   resume;
3. a root branch and alternate sibling branch can be created through `/tree`;
4. provider-visible context follows only the active branch;
5. `/session` reports safe current native-session status;
6. `/name` persists a session name;
7. `/new` starts a fresh native product session;
8. `/resume` can switch/open a previous native product session, list named-only
   sessions, rename a session, and delete one with confirmation (the
   captured-stream subcommands), and the interactive picker builds the expected
   rows and performs rename/delete through the product session files
   (`resume_picker_product_rows_and_actions`);
9. startup equivalents for Pi's `-c`, `-r`, `--no-session`, `--session`,
   `--session-id`, `--session-dir`, `--name`, and `--fork` behave semantically
   correctly — including `--no-session` suppressing both native session tree
   writes and `pipy-session` metadata records, the `--fork`/`--session-id`
   mutual-exclusion errors, the retired `--resume`/`--branch` repl flags, the
   cross-project `--session` fork prompt, and the `-r` non-TTY continue-recent
   fallback (the `cli_*` checks);
10. `/fork` creates a new native session from an earlier user message;
11. `/clone` duplicates the current active branch into a new native session;
12. `/compact` appends a durable compaction entry and context rebuild honors it;
13. branch summary entries are created and used when switching branches;
14. reloading pipy from the native session file reconstructs tree, active
    branch, labels, name, compaction, and context;
15. existing `pipy-session` archive commands still work as metadata/catalog
    utilities, but are not used as product session state.

Canonical deterministic scenario:

```text
/name conformance-tree

User: ROOT
Assistant(fake): SEEN:ROOT

User: MAIN
Assistant(fake): SEEN:ROOT,MAIN

/tree
  select MAIN user message
  edit MAIN -> ALT
  submit

Assistant(fake): SEEN:ROOT,ALT
```

Assertions:

```text
native tree contains:
ROOT -> SEEN:ROOT -> MAIN -> SEEN:ROOT,MAIN
ROOT -> SEEN:ROOT -> ALT  -> SEEN:ROOT,ALT

ALT provider request contains ROOT and ALT
ALT provider request does not contain MAIN

navigating back to MAIN and continuing contains ROOT and MAIN
navigating back to MAIN and continuing does not contain ALT
```

Focused tests should cover:

- append creates correct parent chains and advances leaf;
- branch navigation creates sibling branches without rewriting entries;
- loading an existing file rebuilds ids, labels, leaf, and tree order;
- context reconstruction follows only the active branch;
- model-change and `thinking_level_change` entries update runtime settings
  without becoming prompt text;
- custom-message entries participate in provider context even when hidden from
  the TUI;
- compaction and branch-summary entries affect context correctly;
- selecting root/non-root user messages sets editor text and parent leaf;
- selecting non-user entries sets leaf to the selected entry with empty editor;
- no-op current-leaf selection;
- summary cancellation leaves entries and leaf unchanged;
- label set/clear and labeled-only filter;
- filter changes choose the nearest visible ancestor of the current leaf;
- product-TUI `/tree` real-PTY flows (movement, user-message rehydration,
  non-user selection, Escape cancel, label toggle, filter cycle);
- interactive `/resume` picker flows: the state machine (search, scope/sort/
  named toggles, rename, delete, current-session protection, cancel) and a
  real-PTY navigate/resize/select/cancel pass over the live overlay;
- `/new` and startup session flags (`--session-id`, `--session-dir`, `--name`,
  mutual exclusion, cross-project fork prompt, `-r` non-TTY fallback)
  create/open/fork/suppress native sessions as expected;
- metadata archive privacy: no prompts, assistant text, tool payloads, file
  contents, command output, branch summaries, or native transcript bodies reach
  `pipy-session` archive records by default.

Before treating implementation as complete, run:

```sh
uv run python scripts/parity_checks/session_tree_conformance.py --json
uv run python scripts/parity_checks/session_tree_pi_comparison.py --json
uv run pytest tests/test_native_session_tree*.py tests/test_native_session_resolution.py
uv run pytest tests/test_native_session_picker*.py tests/test_native_startup_session_cli.py
uv run pytest tests/test_native_coding_session_tree*.py
uv run pytest tests/test_native_coding_session_terminal_pty.py -k tree
just check
```

`session_tree_pi_comparison.py` drives the SAME canonical tree workflow
(root → MAIN → branch → ALT → name → fork) against Pi's real `SessionManager`
(via the Pi checkout's own `tsx`) and pipy's native tree, normalizes volatile
ids/timestamps/paths, and asserts the two agree on session name, sibling branch
chains, active ALT/MAIN leaf chains, fork-parent + active-branch carry, and
durable reopen reconstruction. The pipy leg is a hard gate (it asserts the
on-disk product files); the Pi leg skips with a reason when Pi cannot be driven.

Update `docs/session-storage.md`, `docs/harness-spec.md`, `docs/pi-parity.md`,
`README.md`, and this spec to match shipped behavior, and get an independent
review pass for storage or TUI implementation slices.
