# MCP parity gaps, goal check r8: notes for the shared files

Slices K2 and K3 of `handoff/triage-r8.md` run on the parallel track T9B
(branch `feat/mcp-parity-gaps-r8`), which does not edit `CHANGELOG.md`,
`docs/backlog.md`, the plan (`docs/specs/2026-10-12-mcp-parity-plan.md`),
the acceptance note (`docs/acceptance/2026-10-13-mcp-parity.md`) or
`tests/test_mcp_parity_audit.py`. This file holds the text meant for them;
the merge INT9 folds it in.

Links in this file are relative to `docs/specs`. When a changelog line is
copied into `CHANGELOG.md`, its links must start with `docs/` instead
(`../mcp.md#x` becomes `docs/mcp.md#x`). No DEV numbers are allocated
here; a new deviation would be written `DEV-new` for the merge to number.

## K2: OAuth discovery bodies (C) and config inherit (A)

### C: discovery does not wait for the body of a miss, an error or a redirect

**Changelog (Unreleased, Fixed):**

- OAuth discovery no longer waits for the body of an answer it does not
  use, as Pi's discovery cancels it: a 404, 502 or other failure status of
  a metadata URL is acted on at its headers, so a server whose error body
  stalls no longer holds the sign-in up until the request's time limit (and
  then fails it). The OAuth fetch also follows a redirect without waiting
  for the redirect's body, as Node's `fetch` does, also for registration and
  token requests. A fetch passed to the OAuth library
  (`adapt_oauth_provider(provider, fetch=...)`) takes the new
  `discard_unless_ok` argument. See
  [the OAuth library](../mcp.md#the-oauth-library).

**Plan:**

- OAU-02 row: after "A network failure fails the flow; any other failure
  means no metadata." add "The body of a miss, a failure status and a
  redirect is not waited for (Pi's `discard`; since goal check r8, C)." In
  its slice column add "goal check r8 (K2)".
- OAU-03 row: after "4xx and 502 try the next URL; another failure status
  is an error;" add "neither body is waited for (goal check r8, C);". In its
  slice column add "goal check r8 (K2)".
- DEV-72: add "A body that is not read is not limited: the body of a
  redirect that is followed, and in discovery the body of an answer that is
  not 2xx, are not read since goal check r8 (C), as `fetch` does not wait
  for the first and Pi's discovery cancels the second."
- Section 2.x text of S27 or L2 where the OAuth fetch is described as
  "returns the whole response" (`mcp/oauth/fetch.py`), if the plan has such
  a sentence: add "With `discard_unless_ok=True` (discovery's requests) a
  response that is not 2xx is returned at its headers with an empty body
  and its connection closed; a redirect that is followed is never read
  (goal check r8, C)."

**Acceptance:**

- OAU-02 row: add tests `test_native_mcp_oauth_discovery.py`:
  `test_a_stalled_body_of_a_miss_does_not_hold_discovery_up`,
  `test_a_stalled_body_of_a_failure_status_does_not_hold_discovery_up`;
  `test_native_mcp_oauth_fetch.py`:
  `test_with_discard_unless_ok_the_body_of_a_failure_is_not_waited_for`,
  `test_with_discard_unless_ok_a_2xx_body_is_read`,
  `test_the_body_of_a_redirect_that_is_followed_is_not_waited_for` (r8 C;
  `test_every_request_goes_through_the_given_fetch` now also asserts the
  argument).
- OAU-03 row: add tests `test_native_mcp_oauth_discovery.py`:
  `test_a_stalled_body_of_a_miss_does_not_hold_discovery_up`,
  `test_a_stalled_body_of_a_failure_status_does_not_hold_discovery_up`
  (r8 C).

**Audit test references:** none changed.

### A: `pipy config inherit builtin <name>`

**Changelog (Unreleased, Added):**

- `pipy config inherit builtin <name>` puts a project's setting for a
  built-in extension (`mcp`, `codemode`, `tool-search`) back to inherit, as
  the inherit state of Pi's config list does: it removes the `+`, `-` and
  `!` entries of `.pipy/settings.json` that name `builtin:<name>`, and a
  bare `builtin:<name>`, so the user settings decide again. A `!` glob that
  only matches it, such as `!builtin:*`, stays. `inherit` always writes the
  project settings (`--scope global` is refused) and needs a trusted
  project. Before, a project's entry could only be flipped between `+` and
  `-`. See [turning MCP off](../mcp.md#turning-mcp-off).

**Plan:**

- DEV-32: replace "the toggle is `pipy config enable|disable builtin
  <mcp|codemode|tool-search>`" with "the toggle is `pipy config
  enable|disable builtin <mcp|codemode|tool-search>`, and `pipy config
  inherit builtin <name>` puts a project's setting back to inherit, as the
  inherit state of Pi's config list (since goal check r8, A). `pipy config
  list` shows the effective state, not Pi's `project load` / `project
  unload` / `inherited global` labels. Inherit drops a project entry
  written `+./builtin:<name>` (or with `-` or `!`) too, since pipy's
  matching reads it as naming the built-in; Pi's selector compares the
  entry as written and keeps it."
- EXP-12 and EXP-13 rows: in the slice column add "goal check r8 (K2)". The
  row text needs no change.

**Acceptance:**

- EXP-12 row: add tests `test_native_mcp_discovery.py`:
  `test_config_command_puts_a_project_s_builtin_entry_back_to_inherit`,
  `test_config_command_inherit_writes_no_settings_it_cannot` (r8 A). In the
  limits column replace "`pipy config enable\|disable builtin mcp`" with
  "`pipy config enable\|disable\|inherit builtin mcp`".
- EXP-13 row: add test `test_native_mcp_discovery.py`:
  `test_config_command_puts_a_project_s_builtin_entry_back_to_inherit` (r8
  A, `tool-search`). In the limits column replace "`pipy config
  enable\|disable builtin <name>`" with "`pipy config
  enable\|disable\|inherit builtin <name>`".

**Audit test references:** none changed.
