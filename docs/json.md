# JSON Mode

`pipy repl --mode json "<prompt>"` runs one non-interactive pipy session and
writes a Pi-style full event stream as newline-delimited JSON. Use it when a
script wants the same runtime as the interactive product but needs structured
stdout instead of terminal UI text.

```sh
uv run pipy repl --mode json "Summarize this repository"
```

## Stream format

- stdout contains only LF-delimited JSON objects. Split on `\n`; payload strings
  may contain escaped newlines.
- stderr carries diagnostics, warnings, and provider/setup errors that are not
  session events.
- The first stdout object is the native session header:

  ```json
  {"type":"session","version":1,"id":"...","timestamp":"...","cwd":"..."}
  ```

- Later objects are session events from the real native tool loop: agent and turn
  lifecycle, message start/update/end, tool calls and results, queue updates,
  compaction/retry events, and related session events.

The detailed event vocabulary and conformance contract live in
[Automation & RPC](automation-rpc.md). This page is the user-facing quick
reference.

## Reading the stream

A shell pipeline can process one object per line:

```sh
uv run pipy repl --mode json "List the main docs" |
  python3 -c 'import json,sys; [print(json.loads(line)["type"]) for line in sys.stdin]'
```

For one-shot automation that only needs final assistant text, prefer
`uv run pipy repl --print "<prompt>"` instead of filtering the JSON event stream.
For long-lived bidirectional control, use [RPC Mode](rpc.md).

## Content and privacy

JSON mode is a full-content automation surface. Event payloads can include user
messages, assistant text, tool-call arguments, tool results, and bash output.
Do not pipe it to logs unless that destination is allowed to hold transcript and
tool-output content. Auth secrets and credential tokens should still never be
emitted.

This differs from the separate `pipy-session` metadata/catalog utility, which is
summary-safe by default.
