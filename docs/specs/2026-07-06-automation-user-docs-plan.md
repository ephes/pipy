# Automation User Docs Plan

Gap: User documentation parity — automation docs split. Pi has user-facing `packages/coding-agent/docs/json.md` plus RPC type/client sources; pipy currently has a maintainer-heavy `docs/automation-rpc.md` and SDK overview, but lacks user-facing JSON and RPC pages named in `docs/user-documentation.md`.

## Pi reference

- `/Users/jochen/src/pi-mono/packages/coding-agent/docs/json.md` documents `pi --mode json`, JSONL framing, full-content event streams, event types, and stdout/stderr expectations for users.
- `/Users/jochen/src/pi-mono/packages/coding-agent/src/modes/rpc/rpc-types.ts`, `rpc-mode.ts`, `rpc-client.ts`, and `jsonl.ts` define Pi's RPC command vocabulary, async events, JSONL framing, request correlation, and extension-UI bridge.

## Scope

Add one small documentation slice: create user-facing `docs/json.md` and `docs/rpc.md`, link them from `docs/index.md`, `docs/sdk.md`, and `docs/user-documentation.md`, and update parity/backlog/audit notes so the automation-docs gap is marked shipped. Do not change runtime behavior.

## Pipy design

- `docs/json.md` describes shipped `pipy repl --mode json "<prompt>"`: LF-only JSONL on stdout, diagnostics on stderr, first session header, full-content event stream, privacy implications, examples, and pointers to the maintainer contract for exact event tables.
- `docs/rpc.md` describes shipped `pipy repl --mode rpc`: stdin/stdout JSONL, request ids, responses vs async session events, core command families, examples, current limits, and privacy implications.
- `docs/sdk.md` should keep SDK as Python in-process and cross-link JSON/RPC for out-of-process automation.
- Navigation should expose the pages as product user docs without duplicating the full maintainer spec.

## Done when

- `docs/json.md` and `docs/rpc.md` exist and are linked from docs navigation.
- `docs/user-documentation.md`, `docs/backlog.md`, and `docs/pi-mono-gap-audit.md` no longer list automation user docs as an open documentation gap.
- `just docs-build` and `just check` pass.
