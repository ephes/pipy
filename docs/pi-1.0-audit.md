# Pi 1.0 selected-surface audit

Inspected 2026-10-02 from pipy `749c602a`, comparing the September Pi baseline
`4df157433` with release `a13d35a742c6ef8462812a28fbe1d8c8b7431c32`
(1.0.0, 2026-10-01) and local upstream HEAD `b271b0a52`. This closes UP1's
selected established-agent audit; it is not full Pi feature coverage.

Evidence: the coding-agent, AI and TUI changelogs; the commit range; pipy's
settings, startup renderers, provider selection, retry and Anthropic transcript
code. Current priorities remain in [backlog.md](backlog.md).

## Checks

All ran against the unmodified upstream checkout at `b271b0a52`:

- `just catalog-drift`: CLEAN, 97 shared rows, zero unallowlisted drift,
  zero stale entries and zero pending refreshes. There are 47 allowlisted
  field deviations, 13 pipy-only rows and 718 Pi rows not carried.
- `uv run python scripts/parity_checks/session_tree_pi_comparison.py --json`:
  14 checks passed, no skips.
- `uv run python scripts/parity_checks/automation_pi_comparison.py --json`:
  8 checks passed, no skips.

These are deterministic offline comparison gates. They cover their named
scenarios, not live provider quality or every newly released feature. The
September historical baseline remains recorded; these results establish a
new endpoint for the selected comparison gates.

## Adopted

- Pi `f29ea3deb`: `quietStartup: "header"` keeps version and key hints while
  hiding startup details/resources. Both terminal history and stream chrome
  support it; reload honors it and `--verbose` restores full chrome.
- Post-release Pi `3874b3e98`: capacity-error text enters pipy's existing
  bounded retry policy. Quota/billing and context-overflow exclusions retain
  precedence. This fix is not part of the 1.0.0 tag.

The Pi system-prompt fixture was also refreshed for 1.0's new codemode docs
topic. The fixture and Pi-only test inputs name upstream features; pipy's own
documentation topics remain limited to its supported surfaces.

## Remaining selected-surface candidates

- Anthropic copy-code login (`7a11fe1c7`): useful for remote browser setups;
  defer until a concrete Anthropic authentication need warrants the slice.
- Anthropic inline tool definitions (post-release `b271b0a52`): pipy still
  uses deferred top-level declarations and falls back on same-name
  redefinitions. A later provider slice should adopt the inline beta and
  prove replay/cache behavior, including tool removals and redefinitions.
- Provider-only CLI (`0c453048b`): pipy's catalog resolver explicitly selects
  the requested provider's default; the upstream silent-ignore failure is
  not reproduced by this path. Retain this useful difference; one-shot
  built-in providers may separately require an explicit model.
- Invalid Retry-After (`2bbfcca43`): pipy's parser already falls back to the
  retry policy on unparseable dates, with regression coverage.
- Leading-whitespace slash completion: pipy's completer already strips
  leading whitespace before matching; no port needed.
- Rendering memory and model-lookup performance: measure pipy before
  selecting optimizations; Pi's concrete caches/catalog structures differ.
- Strict Anthropic schemas remain deferred: pipy does not enable strict
  tool use, so Pi's strict-schema fallback fix does not apply directly.
- Z.AI overflow wording, extension validation and defaultTools reload
  semantics remain follow-up audit candidates; these comparison scenarios
  do not establish parity for those surfaces.

## Existing feature boundaries

GPT-6.1 Sol and OpenAI ChatGPT sign-in already landed in pipy 0.3.0.
Fullscreen default and terminal-palette themes do not replace pipy's inline
scrollback and `pi` theme choices. Selection highlight/cache fixes need
Python-specific evidence rather than a mechanical TypeScript port.

MCP/codemode/tool search remain deferred to the existing feature-scope
decision. If selected, include server-name-and-URL credential identity,
RFC 9207 issuer validation, metadata overrides, cumulative step-up scopes,
empty optional OAuth fields, normalized-name collision handling, background
connection/discovery and restoration of loaded tools on resume/reload in
acceptance criteria. Lean prompts and actionable script errors are valuable
design inputs for that future work. Radius setup depends on that scope.

Image generation, virtual models/classifiers and durable execution retain
their independent scope decisions. Pi 1.0 does not authorize a new runtime,
parallel tool execution or a task-storage migration.
