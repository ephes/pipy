# Pi-Style Session Tree Workflow

Status: target specification researched from local Pi reference on 2026-06-02.

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
conversation tree. A metadata-only archive cannot implement Pi-style `/tree` by
itself.

## Target Outcome

`pipy repl --agent pipy-native --repl-mode tool-loop` opens and maintains a
durable native session tree. In a live session, `/tree` opens an interactive
selector over the current session's full history. Selecting a prior point moves
the active leaf inside the same session file, optionally writes a branch summary,
and lets the user continue from that point without creating a new session file.

The matching command family is:

- `/session`: show current native session file, id, current leaf, message and
  token/cost counters when known.
- `/tree`: navigate the current session tree in place.
- `/fork`: create a new session file from a previous user message.
- `/clone`: duplicate the current active branch into a new session file.
- `/resume`: select another session file and switch to it.
- `/name <name>`: store a human-readable session name.

The first implementation can stage `/session` and `/tree` before `/fork`,
`/clone`, rich resume selection, HTML export, and share/upload, but the storage
model must be capable of all of them.

## Product Storage Model

Add a pipy-owned native session tree store separate from the existing
metadata-first `pipy-session` archive.

Recommended root:

```text
~/.local/state/pipy/native-sessions/--<encoded-cwd>--/<timestamp>_<uuid>.jsonl
```

The native session JSONL is a private product transcript, like Pi's session
files. It contains raw user prompts, assistant messages, tool call/result
content, compaction summaries, branch summaries, labels, model changes, and
session naming entries because `/tree` needs them. It must live outside git by
default, use owner-only permissions where practical, and never be synced by the
existing metadata archive recipes unless a future explicit sync policy says so.

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

This split is the required redesign: pipy gets Pi-compatible full session
history for the product runtime while preserving the existing archive privacy
contract for day-to-day reflection and sync.

## JSONL Shape

Use an append-only JSONL file with a header followed by tree entries.

Header:

```json
{"type":"session","version":1,"id":"uuid","timestamp":"2026-06-02T12:00:00Z","cwd":"/path/to/project","parentSession":"/optional/source.jsonl"}
```

Every non-header entry has:

- `type`
- `id`: short stable id, unique within the file
- `parentId`: parent entry id or `null`
- `timestamp`: ISO timestamp

Minimum entry types:

- `message`: provider-visible messages, including user, assistant, tool result,
  and pipy tool/batch records needed to rebuild context.
- `model_change`: provider/model selection changes.
- `thinking_level_change`: reasoning/thinking-level selection changes, or the
  pipy-native equivalent if the shipped naming differs.
- `compaction`: in-place context compaction summary with `firstKeptEntryId` and
  `tokensBefore`.
- `branch_summary`: summary created while leaving a branch through `/tree`.
- `label`: user label for any entry, with undefined/empty label clearing it.
- `session_info`: display name.
- `custom` and `custom_message`: reserved for future extension parity.

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
- `model_change` and `thinking_level_change`/reasoning-level entries affect
  current runtime settings but are not user/assistant text.
- `compaction` contributes its summary first, then keeps only messages from
  `firstKeptEntryId` through the compaction boundary and all later active-branch
  messages.

The current in-memory compaction implementation can remain as the first
provider-history reducer, but once native tree storage lands, `/compact` must
append a real `compaction` entry so resumed/tree-navigation sessions rebuild the
same context.

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

Selecting a custom message follows the same parent-plus-editor behavior when it
has text content.

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
- `branchSummary.reserveTokens`: provider budget reserved while summarizing.

Expose `treeFilterMode` and summary prompt behavior through `/settings` once the
interactive command exists.

## Implementation Slices

1. Native tree session core: value objects, JSONL parser/writer, append-only
   file lifecycle, leaf pointer, labels, `get_branch`, `get_tree`,
   `build_context`, malformed-file handling, and tests.
2. Tool-loop persistence wiring: record user, assistant, tool, model-change,
   thinking/reasoning-level-change, compaction, custom-message, and session-info
   entries; rebuild provider history from the active branch on startup/resume;
   keep metadata archive events body-free.
3. `/session` and native session open/resume: show safe current native-session
   status and support opening the latest/current native tree for the workspace.
4. `/tree` selector UI: product-TUI overlay, filters/search/labels/folding,
   selection semantics, captured-stream local diagnostic, and real-PTY tests.
5. Branch summary: abandoned-branch collection, provider summarizer, cancellation
   handling, summary-entry placement, archive-safe operation metadata, and tests.
6. `/fork` and `/clone`: create new native session files from a selected user
   point or the current active branch, with `parentSession` metadata.
7. Rich session selection/export/share: `/resume` selector, rename/delete,
   HTML export, and private share/upload. These are follow-on parity surfaces,
   not prerequisites for the first working `/tree`.

## Verification Plan

Focused tests should cover:

- append creates correct parent chains and advances leaf;
- branch navigation creates sibling branches without rewriting entries;
- loading an existing file rebuilds ids, labels, leaf, and tree order;
- context reconstruction follows only the active branch;
- model-change and thinking/reasoning-level entries update runtime settings
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
- product-TUI `/tree` real-PTY flow at small and larger terminal sizes;
- metadata archive privacy: no prompts, assistant text, tool payloads, file
  contents, command output, branch summaries, or native transcript bodies reach
  `pipy-session` archive records by default.

Before treating implementation as complete, run `just check`, update
`docs/session-storage.md`, `docs/harness-spec.md`, `docs/pi-parity.md`,
`README.md`, and this spec to match the shipped slice, and get an independent
review pass for any storage or TUI implementation slice.
