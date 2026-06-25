# Compaction

Compaction reduces the provider-visible context for long sessions while keeping
the native session tree durable and navigable.

## What compaction does

Pipy's current compaction is a safe, deterministic reduction:

1. It cuts history only at user-turn boundaries, so tool results are not orphaned
   from the assistant tool calls that produced them.
2. It keeps the most recent user-turn groups verbatim for the next provider
   request.
3. It drops older groups from the in-memory provider context and adds a
   metadata/count summary block to the system prompt.
4. When enough durable session history exists, it appends a `compaction` entry to
   the native session JSONL file.

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

Only the bare `/compact` command is currently accepted. A trailing prompt such
as `/compact summarize decisions` is rejected as an unhandled command rather
than used as custom compaction instructions. The current implementation uses
pipy's deterministic summary behavior rather than a model-authored custom
summary.

## Automatic compaction

Automatic compaction is enabled by default. The tool-loop session checks history
between turns and compacts when message count or byte thresholds are exceeded.
This prevents the next provider request from growing without bound.

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
summary and the first retained entry ID. On resume, `/tree` navigation, fork, and
clone, pipy rebuilds the active branch with that compaction boundary honored.

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
safe diagnostic such as `compact blocked by extension` and leaves the active
context unchanged.

## Limitations and follow-ons

- The current summary is metadata/count based. It is safe and deterministic, but
  less semantically rich than a model-authored long-context summary.
- `/compact <custom instructions>` is not accepted yet; use bare `/compact`.
  Custom instructions are not yet used to produce a model-authored summary.
- Compaction is lossy for future provider requests: older details may no longer
  be in context unless you navigate or resume from a branch point that includes
  them before the compaction boundary.

For the broader session model, see [Sessions](sessions.md) and the maintainer
spec [Session Tree](session-tree.md).
