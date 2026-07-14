# RPC `get_entries` / `get_tree` — design + implementation plan

Status: parity-loop slice (single gap). Reference: Pi main `b084d2fb`
(`packages/coding-agent/src/modes/rpc/rpc-mode.ts`,
`packages/coding-agent/src/modes/rpc/rpc-types.ts`,
`packages/coding-agent/src/core/session-manager.ts`).

## Gap

Pi's RPC command union grew from 29 to 31 with two **read-only** commands,
`get_entries` (optional `since`) and `get_tree`. pipy ships the green
29-command baseline but returns an unknown-command / not-implemented response
for these two. `agent_settled` (the third Pi RPC addition) is a separate,
lifecycle-coupled follow-on and is **out of scope** here.

This slice adds `get_entries` and `get_tree` to pipy's RPC surface, matching
Pi's request/response shapes exactly, using pipy's already-present session-tree
read methods. No new session-tree behavior is introduced.

## Pi reference — pinned behavior

### `get_entries` (`rpc-mode.ts:612-623`)

```ts
case "get_entries": {
  const sessionManager = session.sessionManager;
  let entries = sessionManager.getEntries();
  if (command.since !== undefined) {
    const sinceIndex = entries.findIndex((e) => e.id === command.since);
    if (sinceIndex === -1) {
      return error(id, "get_entries", `Entry not found: ${command.since}`);
    }
    entries = entries.slice(sinceIndex + 1);
  }
  return success(id, "get_entries", { entries, leafId: sessionManager.getLeafId() });
}
```

- Request field: `since?: string` (optional).
- `getEntries()` returns entries in **file/append order** (all entries except
  the `session` header), a shallow copy (`session-manager.ts:1230`).
- `since` semantics: when present, find the entry whose `id === since` in that
  file-order list. If **not found**, return an **error** response with message
  exactly `Entry not found: <since>`. If found, return only the entries
  **after** it (`slice(sinceIndex + 1)`) — the matched entry itself is
  excluded, and an exact match on the **last** entry yields an empty list.
- Response data: `{ entries: SessionEntry[], leafId: string | null }`. `leafId`
  is `getLeafId()` (the current leaf pointer, or `null`).

### `get_tree` (`rpc-mode.ts:625-628`)

```ts
case "get_tree": {
  const sessionManager = session.sessionManager;
  return success(id, "get_tree", { tree: sessionManager.getTree(), leafId: sessionManager.getLeafId() });
}
```

- No request fields.
- Response data: `{ tree: SessionTreeNode[], leafId: string | null }`.

### Wire shapes

`SessionEntry` (`session-manager.ts:46-149`) — `type`, `id`,
`parentId: string | null`, `timestamp`, plus type-specific fields (`message`,
`provider`/`modelId`, `thinkingLevel`, `summary`/`firstKeptEntryId`/…, etc.).
JSON serialization of an entry over the wire is a direct `JSON.stringify` of the
entry object.

`SessionTreeNode` (`session-manager.ts:154-162`) — **exactly four** fields:

```ts
interface SessionTreeNode {
  entry: SessionEntry;
  children: SessionTreeNode[];
  label?: string;          // resolved label, omitted when undefined
  labelTimestamp?: string; // latest label-change timestamp, omitted when undefined
}
```

`JSON.stringify` **omits** `label` / `labelTimestamp` when they are `undefined`.
So the pipy serialization must include those keys only when a resolved label
exists, not emit `null`.

Pi's `getTree()` (`session-manager.ts:1239-1287`): builds nodes from
file-order entries; a node is a **root** when `parentId === null`,
`parentId === entry.id` (self-parent), or its parent id is absent (orphan);
children are **sorted by timestamp ascending**.

## pipy mapping — pipy-owned boundaries

pipy already has every building block; this slice is pure RPC wiring.

- `NativeSessionTree.get_entries()` (`session_tree.py:1037`) → file-order
  `list[SessionEntry]` (shallow copy). Matches Pi `getEntries()`.
- `NativeSessionTree.get_tree()` (`session_tree.py:1074`) → `list[SessionTreeNode]`
  with root rule (`parent_id is None or parent_id == entry.id` → root; missing
  parent → orphan root) and per-node label/label_timestamp resolution. The same
  builder is used by the `/tree` command and the extension API
  (`SessionTreeNodeView`). **Parity fix required:** Pi's `getTree()` explicitly
  sorts each node's children by timestamp ascending
  (`session-manager.ts:1281-1286`), whereas pipy's builder relies on
  append/file order and does not sort. Append order equals timestamp order for
  pipy-written single-file sessions, but that is not an enforced invariant
  (non-monotonic wall-clock at append time; loaded/edited files), so to match Pi
  exactly this slice ports the sort **into the shared builder**: after building
  the node map, sort every node's `children` by `entry.timestamp` ascending
  (iterative walk over the roots, mirroring Pi's stack approach to avoid deep
  recursion). This is a Pi-faithful hardening that is a no-op for already-ordered
  sessions; `/tree` and the extension `get_tree` inherit the same faithful
  ordering. Update the builder's "no re-sort needed" comment accordingly.
  **Refactor** the body into a pure module function `build_tree_nodes(entries)`
  so it can be driven from a captured snapshot (below);
  `NativeSessionTree.get_tree()` delegates to it via
  `build_tree_nodes(self.entries)`. The function snapshots
  `entries = list(entries)` at the top (its two passes would otherwise `KeyError`
  on a concurrent append), **derives the resolved labels from the entries
  themselves** — replaying `LabelEntry` resolution in order (truthy `label` →
  set `label`+`timestamp` for `target_id`, else clear), mirroring `_load_entries`
  / `append_label_change` (`session_tree.py:791-797,940-945`) — and applies the
  timestamp sort. Deriving labels from entries (rather than reading the live
  `labels_by_id`/`label_timestamps_by_id` maps) yields identical output — those
  maps are themselves a running fold of the same `LabelEntry` stream — and makes
  the node labels **exactly consistent with the captured entries** with no
  label-map copy or label-mutation locking. `/tree` and the extension `get_tree`
  inherit this unchanged output.
- `NativeSessionTree.leaf_id` (`session_tree.py:` field) → Pi `getLeafId()`.
- `_entry_to_json(entry)` (`session_tree.py:316`) → the entry wire form already
  used by export (`export_distribution.py:163`) and the extension entry view
  (`extension_runtime.py:1789`). This is pipy's canonical `SessionEntry` wire
  shape (`type/id/parentId/timestamp` + type-specific fields). Reuse it so
  get_entries returns the same shape clients already see from export/extensions.

### Concurrency: atomic `(entries, leaf)` snapshot

Pi reads `getEntries()`/`getTree()` and `getLeafId()` back-to-back on its single
event loop with no yield, so the entries/tree and the leaf pointer are always a
**coherent pair** (`leafId` names an entry present in the returned set, and the
set is never ahead of the leaf for a plain append). pipy runs the provider turn
on a **worker thread** that appends to the session tree concurrently with RPC
command dispatch, and `_append_entry` (`session_tree.py:823-826`) is a two-step
mutation — append to `entries`, *then* set `leaf_id`. No read-side ordering or
retry can recover a coherent pair from a non-atomic mutation: reading leaf-first
can return entries ahead of the leaf; leaf-last can return a leaf absent from the
snapshot; and the intermediate "entry appended, leaf not yet advanced" state is
observable either way. So the snapshot must share a lock with the mutator.

- Add a dedicated `threading.Lock` to `NativeSessionTree` (e.g. `_write_lock`)
  guarding the entry/leaf mutation. **All** live-session appends funnel through
  `_append_entry` (the `entries.append` + `leaf_id =` pair), and the only other
  concurrent-relevant leaf mutation is `branch()`; acquire the lock around the
  mutation body of both so the two-step append is atomic w.r.t. the snapshot.
  Label-map writes need **no** locking because labels are derived from entries,
  not read from the live maps (above). `_load_entries`, `create`, and
  `fork_from` run single-threaded at construction/load and need no locking. The
  lock is leaf-level (no nested acquisition, no callbacks under it) → no
  deadlock/perf concern (a handful of appends per turn).
- Add one method `snapshot_entries_and_leaf() -> tuple[list[SessionEntry],
  str | None]` that, under `_write_lock`, returns `(list(self.entries),
  self.leaf_id)` — a coherent pair (the entries copy always contains the leaf,
  and is never ahead of it). Hold time is just the shallow list copy; the tree
  build/label-derivation and iterative encode run **outside** the lock on the
  captured entries (`build_tree_nodes` is pure). get_entries and get_tree both
  use this single snapshot.

### New RPC handlers (`native/automation/rpc.py`)

1. Add `"get_entries"` and `"get_tree"` to `_KNOWN_COMMANDS` (baseline grows
   29 → 31). Dispatch is by method name (`_cmd_<type>`), so adding the methods
   plus the vocabulary entries fully wires them.

2. `_cmd_get_entries(cid, command)`:
   - `entries, leaf = self._tree.snapshot_entries_and_leaf()` — a coherent pair
     captured under the tree's `_write_lock` (see Concurrency above). Use this
     `entries` copy and captured `leaf` throughout; do not re-read live state.
   - Gate the `since` branch on **key presence** — `if "since" in command:` —
     mirroring Pi's `command.since !== undefined`. This is the faithful test:
     an explicit JSON `null` `since` is present (`!== undefined`), so Pi enters
     the branch, matches no entry id, and returns the not-found error; an
     **absent** `since` returns the full list. A Python `is not None` gate would
     wrongly treat an explicit `null` like an absent key and return all entries,
     diverging from Pi — so it must gate on membership, not on `None`.
   - Inside the branch: `since = command.get("since")`; locate the index where
     `entry.id == since`. If no entry matches (including the `null`/non-string
     case, since ids are always strings), respond with the not-found error
     (message construction below) and return. Otherwise keep `entries[idx + 1:]`
     (the matched entry excluded; an exact match on the last entry yields `[]`).
   - Serialize the kept entries with `_entry_to_json` and respond
     `{"entries": [...], "leafId": leaf}` (the leaf captured above).
   - **Message parity — in-contract domain only.** Pi builds the message as
     `` `Entry not found: ${command.since}` ``. `since` is typed `since?: string`,
     so the well-formed inputs are **absent** or a **string id** (renders as
     itself). The one realistic out-of-type value a JSON client can send is
     explicit `null`, which JS renders as `null`. Special-case exactly that:
     when `since is None`, use the literal `"null"`; otherwise `str(since)`
     (correct and exact for a string id). Build
     `f"Entry not found: {rendered}"`. Do **not** attempt to reproduce V8's full
     number/boolean/`Object` stringification (e.g. JS `1.0`→`"1"`, large-integer
     rounding, `[object Object]`): those `since` values are outside Pi's typed
     contract, and byte-reproducing V8 numeric coercion is out of scope. The
     parity guarantee is scoped to the in-contract inputs (absent / valid string
     id) plus the explicit-`null` case; a non-string, non-null `since` is a
     malformed request rendered best-effort via `str()` and is not claimed
     byte-identical to Pi.

3. `_cmd_get_tree(cid, command)`:
   - `entries, leaf = self._tree.snapshot_entries_and_leaf()` — the same coherent
     snapshot. Build the nodes **outside the lock** with
     `build_tree_nodes(entries)` (labels derived from `entries`); `leaf` names a
     node in that tree, matching Pi's atomic read.
   - **Serialize the tree with a single iterative string encoder — no
     recursion, no recursion-limit bump.** A linear conversation nests one level
     per entry, so a ~1000-entry session yields a ~1000-deep tree. Both a
     recursive builder *and* the final `json.dumps` would `RecursionError` on
     such a tree: the response is ultimately encoded by `serialize_json_line`
     (`automation/jsonl.py:50`) = `json.dumps(..., ensure_ascii=False,
     separators=(",",":"), allow_nan=False)`, whose CPython C encoder recurses
     once per nesting level (each level being *two* containers — the node object
     and its `children` array). A temporary `sys.setrecursionlimit` bump is
     rejected: it is **process-global and unsynchronized** (`serialize_json_line`
     runs per caller thread *outside* the writer's stdout lock —
     `jsonl.py:104-108` — and events can be emitted from the worker thread
     concurrently with a get_tree encode), so concurrent encodes could restore
     the limit out of order, and raising it high enough still does not guarantee
     C-stack safety.
   - Add `_encode_session_tree(roots) -> str`: it walks the node spine with an
     **explicit stack** (never recursing on children) and emits the `tree` JSON
     array text directly. Per `SessionTreeNode`, emit fields in **Pi's object
     literal order** (`session-manager.ts:1244`,
     `{ entry, children, label?, labelTimestamp? }`): `{"entry":<E>` then
     `,"children":[` … push children … `]`, then — *after* the children array
     closes — optional `,"label":<L>` and `,"labelTimestamp":<T>` only when the
     value is not None (mirrors Pi's `JSON.stringify` omission of `undefined`),
     then `}`. `<E>`/`<L>`/`<T>` come from `json.dumps(..., ensure_ascii=False,
     separators=(",",":"), allow_nan=False)` on the **shallow** entry dict /
     scalar (bounded depth — safe from `RecursionError`); the stack walk emits
     the deep array/object delimiters and comma separators. Only the deep spine
     is handled by the stack; the per-node scalar/entry encodings reuse
     `json.dumps` with the exact `serialize_json_line` byte options, so escaping
     and separators are identical to the normal path (the field-order guarantee
     is Pi-literal order, stated here, not a comparison to any dict path).
   - Emit via a raw-line path: add `JsonlWriter.write_raw_line(str)` (writes an
     already-encoded JSON line under the same `_lock`, preserving strict LF
     framing and serialized stdout), and have `_cmd_get_tree` assemble the full
     response line — the shallow envelope (`id?`, `type`, `command`, `success`,
     `data` with `leafId`) encoded with the same `json.dumps` options and the
     pre-encoded `tree` array string spliced into `data` — then `write_raw_line`
     it. This keeps stdout serialization intact, introduces no global state, and
     is `RecursionError`-free at any depth. (`get_entries` returns a **flat**
     entry list — shallow — so it stays on the normal `_respond` path unchanged;
     only get_tree needs the iterative path.)

4. Concurrency: match the existing read handlers (`_cmd_get_messages`,
   `_cmd_get_fork_messages`, `_cmd_get_last_assistant_text`), which read the
   tree without taking `self._lock`. No new lock — staying consistent with the
   sibling read handlers, not introducing a divergent locking convention in this
   slice.

## Tests (`tests/test_native_automation_rpc.py`)

TDD — add focused tests before wiring:

1. `get_tree` on a session with a couple of appended entries returns
   `command: "get_tree"`, `data.tree` is a list whose first root node has an
   `entry` dict with `id`/`type`/`parentId`/`timestamp`, nested `children`, and
   `data.leafId` equals the last appended entry id.
2. A labelled entry surfaces `label` (and `labelTimestamp`) on its node; an
   unlabelled node omits both keys entirely (assert `"label" not in node`).
3. `get_entries` with no `since` returns all appended entries in file order and
   the correct `leafId`.
4. `get_entries` with `since` = an existing entry id returns only the entries
   after it (and empty list when `since` is the last entry id).
5. `get_entries` with an unknown `since` returns an **error** response
   (`success: false`) whose message is `Entry not found: <id>`.
6. `get_entries` with an explicit `{"since": null}` returns an **error**
   response (present-but-unmatched → not-found), **not** the full entry list,
   and its message is exactly `Entry not found: null` — guarding both the
   key-presence gate against a `None`-based regression and the null rendering
   against a `None`-string regression.
7. `get_tree` children are ordered by timestamp ascending: build a session whose
   sibling entries are appended with **out-of-order timestamps** (a later append
   carrying an earlier timestamp) and assert the serialized `children` come back
   timestamp-sorted, proving the ported sort rather than raw append order. A
   sibling test at the shared-builder level (`test_native_session_tree_core.py`)
   covers `/tree` + extension inheritance.
8. **Deep-tree encode:** build a linear session well past Python's default
   recursion limit (e.g. ~2000 chained entries → ~2000-deep tree), issue
   `get_tree`, and assert the **server** emits a single well-formed get_tree
   success line without raising `RecursionError` (no error record, one LF-framed
   line). Verify content with depth-safe **string** assertions rather than a
   full `json.loads` — because CPython's `json.loads` C scanner *also* recurses
   per level and would itself `RecursionError` on a ~2000-deep line, so parsing
   the whole payload in-test would fail even when the encoder is correct. Assert
   the line begins with the get_tree success envelope, contains
   `"leafId":"<last entry id>"`, contains the first/last/deepest entry ids, and
   that the count of `"children":[` opens equals the node count (spine intact).
   If a full structural parse is wanted, it must be done under a locally raised
   `sys.setrecursionlimit` *inside the isolated test process only* — never rely
   on the default limit to parse the response.
9. **Atomic snapshot:** a focused tree-level test asserting
   `snapshot_entries_and_leaf()` returns a coherent pair — the captured `leaf`
   id is always present in the captured entries and the entries are never ahead
   of the leaf — under a concurrent appender thread hammering `append_message`
   **and `append_label_change`** while the test repeatedly snapshots and builds
   the tree. Assert every snapshot's `leaf` is in its entries, and that every
   node label in the built tree matches the label resolution of that same
   captured entries list (labels consistent with entries). This proves the
   `_write_lock` coupling and the entries-derived labels rather than incidental
   timing.

## Docs

- `docs/automation-rpc.md`: move `get_entries`/`get_tree` from the
  "two additional read-only commands pipy does not yet implement" follow-on
  table into the shipped baseline; update the count language from
  **29-command baseline** to **31-command baseline**; keep `agent_settled` as the
  remaining follow-on.
- `scripts/parity_checks/automation_rpc_conformance.py`: add both commands to
  `_ALL_COMMANDS`, update the `29` literals/label to `31`, and update the
  module docstring that currently calls them explicit follow-ons.
- `docs/pi-mono-gap-audit.md` / `docs/backlog.md`: strike the two read-only RPC
  commands from the remaining gap; note `get_entries`/`get_tree` shipped and
  that `agent_settled` (and broader live provider switching over RPC) remain
  follow-ons.

## Acceptance criteria

- `get_entries` (with/without/unknown `since`) and `get_tree` return Pi-shaped
  responses; unknown `since` errors with the exact Pi message.
- Node label/labelTimestamp keys are present only when a label exists.
- The RPC conformance gate accepts all **31** commands; `just check` green.
- Docs + gap sources reflect the shipped state; only `agent_settled` remains a
  named RPC follow-on.

## Out of scope

- `agent_settled` event and its idle-lifecycle coupling.
- Live provider switching / broader RPC session mutation.
- Any change to `NativeSessionTree.get_entries()` behavior. The only
  `get_tree()` change is porting Pi's child-timestamp sort (above); the root
  rule, label resolution, and node shape are unchanged.
- Byte-reproducing V8 numeric/boolean/object stringification for a malformed
  non-string, non-null `since`.
