# MCP client conformance

Runs the official [MCP conformance suite](https://github.com/modelcontextprotocol/conformance)
against pipy's MCP client and compares the result with a committed baseline.

```bash
just mcp-conformance
```

This is a manual check. It is not part of `just check` or CI, because it needs
Node (`npx`) and, the first time, network access to fetch the pinned suite
(`@modelcontextprotocol/conformance@0.2.0-alpha.11`). Install scripts are
disabled. It needs a POSIX shell. A full run takes about a minute.

Pi runs the same suite in its CI (`packages/coding-agent/test/mcp-conformance`).
`run.py` and `client.py` are ports of Pi's `run.ts` and `client.ts`.

## How it works

For every protocol version pipy negotiates (`2025-03-26`, `2025-06-18`,
`2025-11-25`), `run.py` lists the suite's client scenarios and runs them one at
a time. The suite starts a scenario server and runs `client.py` against it.

`client.py` uses the code pipy runs for an HTTP server in `mcp.json`:
`McpServerConnection` connects and calls tools, and `sign_in_mcp_server` runs
the OAuth sign-in that `/mcp` starts. A simulated browser fetches the
authorization URL and delivers the redirect to pipy's loopback callback server.
When the server asks for sign-in again (for example for more scope), the
simulated user signs in again, up to three times. Credentials are kept in a
temporary directory; the run reads and writes nothing under your pipy config
home.

Every check the suite reports is compared with `baseline.json`. The extra
`pipy-client` check records whether `client.py` completed the scenario. Some
scenarios expect the client to give up (`auth/scope-retry-limit`), so it is
baselined like the others.

The run fails when:

- a check that passes in the baseline fails or is missing,
- a check fails that the baseline does not list as failing,
- a baselined scenario did not run.

Checks that started passing are reported; update the baseline to lock them in.

## Options

```bash
just mcp-conformance --mode 2025-11-25 --scenario auth/scope-step-up --verbose
just mcp-conformance --update-baseline
```

- `--mode`, `--scenario`: run a subset (repeatable).
- `--verbose`: print the suite's output, including each check and the client's
  log.
- `--keep-results`: keep the suite's `checks.json` and client output.
- `--update-baseline`: write the results of a full run to `baseline.json`.
  Review the diff; do not regenerate it to hide a regression.

## Known failures

- `elicitation-sep1034-client-defaults`: pipy does not support elicitation.
- `auth/basic-cimd`: the scenario's client has no `oauth.clientRegistration`
  setting, so pipy registers it dynamically instead of using a Client ID
  Metadata Document. pipy uses such a document only when `mcp.json` asks for
  one and names where it is hosted (see `docs/mcp.md`).
- `auth/scope-retry-limit`: `pipy-client` fails by design; the server never
  accepts the granted scope.

These are the failures of Pi's baseline at the commit the MCP parity plan
names. `tests/test_mcp_conformance_recipe.py` pins the list, so a baseline
with another failing check does not pass `just check`.

2026-07-28 is not covered: it is a stateless protocol pipy does not implement.
