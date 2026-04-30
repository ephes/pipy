# pipy

Python slop fork experiments for a coding-agent harness inspired by Pi and clean architecture.

The repository currently contains the first project infrastructure slices:
durable session-storage policy, a small local session-recorder CLI, and
explicit sync between the `studio` and `atlas` development machines.

## Development Setup

Install the Python tooling with `uv`:

```sh
uv sync
```

## Session Sync Setup

Copy the direnv example and approve it:

```sh
cp .envrc.example .envrc
direnv allow
just sessions-init
```

Then sync finalized session records with the paired Tailscale machine:

```sh
just sessions-sync
```

On `studio`, the default remote is `atlas.tailde2ec.ts.net`.
On `atlas`, the default remote is `studio.tailde2ec.ts.net`.

Raw session records live outside git by default:

```text
~/.local/state/pipy/sessions/
```

Active records should be written under:

```text
~/.local/state/pipy/sessions/.in-progress/pipy/
```

Finalized immutable records should be moved to:

```text
~/.local/state/pipy/sessions/pipy/YYYY/MM/
```

See `docs/session-storage.md` for the full lifecycle.

## Session Recorder CLI

Use `pipy-session` to create active JSONL records, append structured events,
and finalize immutable records into the syncable archive:

```sh
active="$(uv run pipy-session init --agent codex --slug session-storage-work)"
uv run pipy-session append "$active" --type decision.recorded --summary "Use finalized immutable JSONL files for sync."
uv run pipy-session finalize "$active" --summary "# Summary

Implemented the local recorder foundation."
```

The recorder resolves its root from `PIPY_SESSION_DIR`, or defaults to:

```text
~/.local/state/pipy/sessions/
```

It writes active records under `.in-progress/pipy/` and finalizes them under
`pipy/YYYY/MM/` with filenames shaped like:

```text
YYYY-MM-DDTHHMMSSZ-<machine>-<agent>-<slug>.jsonl
YYYY-MM-DDTHHMMSSZ-<machine>-<agent>-<slug>.md
```

For partial reconstructions, use `--partial` when initializing the record:

```sh
uv run pipy-session init --agent codex --slug manual-reconstruction --partial
```

## Automatic Capture Status

Automatic session capture from Codex, Claude, or Pi is not implemented yet.

For now, session records are created with the generic `pipy-session` recorder,
manually, or by future wrappers/hooks. Some agent platforms do not expose a
complete raw transcript to the running agent, so records may need to be partial
reconstructions until we add platform-specific capture adapters.
