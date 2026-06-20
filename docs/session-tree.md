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
- Runtime wiring + `/session`, `/name`, `/new`, `/tree`, `/resume`, `/fork`,
  `/clone`, durable `/compact`, branch summaries:
  `src/pipy_harness/native/tool_loop_session.py`.
- Live-TTY `/tree` selector and the interactive session picker overlay
  (`ToolLoopTerminalUi.run_tree_selector`, `run_session_picker`, and the
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
  ship through `ToolLoopTerminalUi.run_session_picker` /
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
- `compaction`: in-place context compaction summary with `firstKeptEntryId` and
  `tokensBefore`.
- `branch_summary`: summary created while leaving a branch through `/tree`.
- `label`: user label for any entry, with undefined/empty label clearing it.
- `session_info`: display name.
- `custom` and `custom_message`: active Pi entry types used by extensions
  (`packages/coding-agent/src/core/session-manager.ts:129`;
  `packages/coding-agent/src/core/agent-session.ts:511-516`). pipy should
  support them once its extension API lands.

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
(`ToolLoopTerminalUi.run_session_picker`) over native product session files. It
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
uv run pytest tests/test_native_tool_loop_session_tree*.py
uv run pytest tests/test_native_tool_loop_tui_pty.py -k tree
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
