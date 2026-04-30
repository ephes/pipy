# pipy

Python slop fork experiments for a coding-agent harness inspired by Pi and clean architecture.

The repository currently contains the first project infrastructure slice: durable session-storage policy and explicit sync between the `studio` and `atlas` development machines.

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

## Automatic Capture Status

Automatic session capture from Codex, Claude, or Pi is not implemented yet.

For now, session records are created manually or by future wrappers/hooks. Some agent platforms do not expose a complete raw transcript to the running agent, so records may need to be partial reconstructions until we add platform-specific capture adapters.
