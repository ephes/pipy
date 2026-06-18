# Pi-Style Export, Import, Share, and Distribution

Status: baseline shipped 2026-06-17. The target specification was researched
from local Pi reference on 2026-06-02 and reselected after extension/package
slice-12 closeout on 2026-06-17.

This document defines the pipy target for real feature parity with Pi's
export / import / share / distribution / self-update surfaces. It is based on
the local reference checkout at `/Users/jochen/src/pi-mono`. It is a companion
to [session-tree.md](session-tree.md): the native session tree is the product
source of truth that these surfaces export, import, and share. It is not a
TypeScript port; pipy reaches Pi-shaped behavior through pipy-owned Python
boundaries, stdlib-only, with no new runtime dependencies.

The single most important parity correction in this spec: **full-session
HTML/JSONL export must export the full native session transcript, matching
Pi.** The existing metadata-only `pipy-session export` is a pipy-specific
divergence. It is superseded for product parity and is flagged as such below.
It may remain as a separate summary-safe catalog utility, but it is not the
product export surface.

## Shipped Baseline

The shipped Python boundary is `pipy_harness.native.export_distribution` and is
gated by:

```sh
uv run python scripts/parity_checks/export_distribution_conformance.py --json
```

Shipped user-facing surfaces:

- `/export`, `/export <path.html>`, and `/export <path.jsonl>` in the native
  tool-loop REPL. HTML carries the full native session tree; JSONL carries the
  active branch with a linear parent chain.
- `/import <path.jsonl>` in the native tool-loop REPL, with confirmation in the
  REPL layer, `--yes` accepted for noninteractive command scripts, and
  collision-safe copy-into-store behavior.
- `/share` in the native tool-loop REPL, using `GITHUB_TOKEN`/`GH_TOKEN` or
  `gh auth token`, stdlib `urllib`, and a fakeable HTTP boundary in tests.
- `pipy --export <session.jsonl> [output.html]` for non-interactive HTML export.
- `pipy update self|pipy [--force] [--dry-run]` self-update planning for
  `uv tool`, `pipx`, `pip`, and user `pip`, with fail-safe behavior for
  development/unknown installs and for unconfigured package names.
- Startup "newer version available" notices remain future polish; explicit
  `pipy update` version checks and opt-out handling are shipped.

The first HTML template is an inline stdlib template in the Python module rather
than a separate packaged data directory. It is still a single self-contained
artifact with inlined CSS/JS and base64 session-data embedding; splitting it
into `importlib.resources` files remains optional polish, not a parity blocker.
Extension/package source updates are owned by `extension-api.md`; managed git
package updates now ship there, while PyPI/npm sources remain deferred pending
broader supply-chain policy.

## Sources

Pi reference (read for exact behavior):

- `packages/coding-agent/src/core/export-html/index.ts` — `exportSessionToHtml`
  (TUI `/export`), `exportFromFile` (CLI `--export`), `generateHtml`,
  template/CSS/JS string templating, base64 session-data embedding,
  `TEMPLATE_RENDERED_TOOLS`, `preRenderCustomTools`, default output filename
  `${APP_NAME}-session-<basename>.html`.
- `packages/coding-agent/src/core/export-html/template.html`,
  `template.css`, `template.js`, `ansi-to-html.ts`, `tool-renderer.ts` — the
  self-contained HTML artifact and its rendering pipeline.
- `packages/coding-agent/src/core/agent-session.ts` — `exportToHtml`,
  `exportToJsonl` (header + active-branch entries re-chained linearly into a
  new JSONL).
- `packages/coding-agent/src/core/agent-session-runtime.ts` — `importFromJsonl`
  (copy file into the session dir, open as a `SessionManager`, resume with a
  `session_start { reason: "resume" }` event, `MissingSessionCwdError` prompt).
- `packages/coding-agent/src/modes/interactive/interactive-mode.ts` —
  `handleExportCommand`, `getPathCommandArgument` (`.html`/`.jsonl` routing and
  quoted-path parsing), `handleImportCommand` (confirm before replace),
  `handleShareCommand` (gh-CLI gist flow + cancellable loader),
  `handleChangelogCommand`, `handleSessionCommand`.
- `packages/coding-agent/src/main.ts` — `--export <file> [output]` non-interactive
  path that exports and exits.
- `packages/coding-agent/src/cli/args.ts` — `--export` parsing and `--help` text.
- `packages/coding-agent/src/config.ts` — `getShareViewerUrl` (default
  `https://pi.dev/session/`, `PI_SHARE_VIEWER_URL` override), `detectInstallMethod`.
- `packages/coding-agent/src/package-manager-cli.ts` — `install`, `remove`/
  `uninstall`, `update [source|self|pi] [--self|--extensions|--extension|--force]`,
  `list`, self-update plan and execution, `detectInstallMethod` gating.
- `packages/coding-agent/src/utils/version-check.ts` — `getLatestPiRelease`
  (`https://pi.dev/api/latest-version`, `PI_SKIP_VERSION_CHECK`/`PI_OFFLINE`
  short-circuit, version comparison helpers), `checkForNewPiVersion`.
- `packages/coding-agent/src/utils/changelog.ts` — `parseChangelog`,
  `getNewEntries`, `getChangelogPath`.
- `packages/coding-agent/src/core/slash-commands.ts` — built-in descriptions
  for `export`, `import`, `share`, `changelog`.
- `README.md` and `packages/coding-agent/README.md` — documented installs
  (`npm install -g --ignore-scripts @earendil-works/pi-coding-agent`,
  `curl -fsSL https://pi.dev/install.sh | sh`), `pi update` docs.

Pipy current state (read for the gap):

- `src/pipy_session/export.py` — `export_session(..., include_transcript=False)`,
  metadata-only by construction, optional sensitive transcript sidecar.
- `src/pipy_harness/native/session_tree.py` — `NativeSessionTree` with
  `SessionHeader` (incl. `parentSession`), entry types, `append_*`,
  `get_branch`, `get_tree`, `build_context`, `open`, `fork`.
- `docs/session-tree.md` — native session tree spec (source for full export).
- `docs/session-storage.md` — `--archive-transcript` sidecar, metadata-only
  archive `export`, deferred raw-transcript import policy.
- `docs/backlog.md` — export / import / share / distribution is now a shipped
  baseline; the next selected slice has moved on.

## Target Outcome / Goal

When running `pipy repl --agent pipy-native --repl-mode tool-loop`, the user can:

- `/export` — write a self-contained HTML file of the current native session
  (the full session tree, with the leaf marking the active path), defaulting to
  `pipy-session-<stem>.html` in the cwd.
- `/export <path.html>` — export HTML to a chosen path.
- `/export <path.jsonl>` — export the active branch as a portable JSONL session
  file (header + linearly re-chained entries), matching Pi's `exportToJsonl`.
- `/import <path.jsonl>` — after a replace confirmation, import a JSONL session
  file into the native store (copy-into-store + open + resume), matching Pi's
  `importFromJsonl`. The destination uses the source basename and may overwrite
  an existing same-named session in the store.
- `/share` — export the current session to HTML and upload it as a secret
  GitHub gist, then print a share URL. Pi gates this only with a cancellable
  loader; an explicit confirmation is an optional pipy choice.
- `/changelog` — render pipy changelog entries.

Non-interactively:

- `pipy ... --export <session.jsonl> [output]` — export an existing native
  session file (or active branch of one) to HTML and exit, matching Pi's
  `--export`.

For distribution and self-update, a stdlib-only `uv`/pip/pipx-installed pipy
can:

- check for a newer published version (opt-out via env),
- print install-method-aware upgrade instructions, and
- where the install method is safely detectable, run the upgrade.

These surfaces operate on the **full native session tree**, not on the
metadata-only `pipy-session` archive.

## Full-Session HTML Export

### Behavior

`/export` (no `.jsonl` argument) and `--export` produce one self-contained HTML
file. It must include the **full session tree** of the native session, not just
the active branch: user prompts, assistant messages (including
thinking/reasoning where captured), tool calls and tool results, bash
command/output records, `write`/`edit` diffs, compaction summaries, branch
summaries, labels, model changes, and the session name. This matches Pi: Pi
base64-encodes the session header, **all** entries (`sm.getEntries()`), the leaf
id, the system prompt, and the tool list into the HTML, so the viewer has the
full tree. The embedded `leafId` selects the active path for rendering; the
active branch is identified by the leaf, not by trimming the exported entries.

Pi does not redact secrets during HTML export; it serializes the session entries
as-is. Pipy makes a deliberate, pipy-specific choice to withhold only auth
tokens, API keys, and provider credentials from the export, reusing the existing
pipy secret-redaction boundary so credentials that appear inside tool output or
prompts are masked. This token withholding is a pipy choice, not Pi behavior. No
other transcript content is dropped: privacy is not a reason to reduce exported
content; the artifact is the user's own session and is exported in full minus
auth tokens.

### Artifact shape

Pipy builds the HTML through stdlib string templating (`str.replace` /
`string.Template` over a checked-in template), mirroring Pi's
`generateHtml`:

- A single HTML file with inlined CSS and JS (no external network fetches at
  view time, no CDN dependency).
- Session data embedded as base64-encoded JSON (`{ header, entries, leafId,
  systemPrompt, tools, renderedTools }`) to avoid escaping issues, decoded and
  rendered client-side, matching Pi's embedding. `renderedTools` is an optional
  map (keyed by tool-call id) of pre-rendered HTML for custom tools (see below);
  it is omitted when nothing is pre-rendered.
- A pipy-owned theme-variable block derived from the active chrome theme, so the
  export visually matches the running theme. Pi derives export background colors
  from the theme; pipy should produce equivalent light/dark-aware backgrounds
  from its own theme colors.
- Tool rendering split. In Pi, the built-in tools in `TEMPLATE_RENDERED_TOOLS`
  (bash/read/write/edit/ls) are **rendered client-side by `template.js` in the
  browser** from the embedded entries, not pre-rendered server-side. Only
  **custom** (non-built-in) tools are pre-rendered server-side into HTML via the
  TUI renderers plus an ANSI-to-HTML pass (`tool-renderer.ts`, `ansi-to-html.ts`)
  and supplied through `renderedTools`. Pipy's first target renders all native
  tool calls/results client-side from the embedded JSON in the template (since
  pipy controls its own tool set), with an ANSI->HTML conversion for any captured
  terminal/bash output so colorized output is preserved. A `renderedTools`-style
  custom-tool pre-render hook is a later extension-API concern, not a first-slice
  blocker.

Templates, CSS, and JS live as packaged data files under the pipy package (for
example `src/pipy_harness/native/export_html/`) and are read at runtime via
`importlib.resources`. No build step and no bundler. Vendored client libraries
(such as a Markdown renderer or syntax highlighter) are optional; if absent, the
template degrades to `<pre>` rendering. No new Python runtime dependency is
added for either the renderer or the vendored assets.

### Output path resolution

- `/export` with no path -> `pipy-session-<session-file-stem>.html` in cwd.
- `/export <path.html>` -> that path (`.html` suffix or no recognized session
  suffix routes to HTML).
- Argument parsing matches Pi's `getPathCommandArgument`: trim leading
  whitespace, support a single- or double-quoted path, otherwise take the first
  whitespace-delimited token.
- An in-memory/ephemeral session (`--no-session`) cannot be HTML-exported;
  surface a clear error like Pi's "Cannot export in-memory session to HTML".
- Exporting before any conversation exists surfaces a clear "nothing to export
  yet" message, matching Pi.

## `.jsonl` Export

`/export <path.jsonl>` writes a portable JSONL session file from the current
native session, matching Pi's `exportToJsonl`:

1. Write the session header line first (`type: "session"`, version, id,
   timestamp, cwd). `parentSession` is omitted for a fresh export.
2. Append the **active-branch** entries (from `NativeSessionTree.get_branch()`),
   re-chaining `parentId` into a linear sequence: the first entry has
   `parentId: null`, each subsequent entry points at the previous one.
3. Default filename when no path is given:
   `session-<iso-timestamp-with-colons-and-dots-replaced>.jsonl` in cwd, matching
   Pi.

This produces a single linear branch suitable for sharing or re-import. It is
the same JSONL shape the native store uses (see session-tree.md "JSONL Shape"),
so a `.jsonl` export round-trips through `/import`. Pi serializes entries as-is
and does not redact on export; pipy applies its own auth-token/secret
withholding on the way out exactly as for HTML export (a pipy choice, not Pi
behavior). All other transcript content is preserved.

## `/import` Import-and-Resume

`/import <path.jsonl>` imports an external JSONL session file and resumes it as
a new native session, matching Pi's `importFromJsonl`:

1. Resolve the input path; error clearly if it does not exist
   (Pi: `SessionImportFileNotFoundError`).
2. Show a confirmation prompt before replacing the current session, e.g.
   "Replace current session with `<path>`?" Cancelling aborts with a status
   message and leaves the running session untouched. In Pi this confirmation
   lives in the **interactive UI** (`handleImportCommand` calls
   `showExtensionConfirm`), not in the runtime. The runtime `importFromJsonl`
   itself only copies, opens, and emits `session_start { reason: "resume" }`; it
   does not prompt. Pipy should keep the same split: confirm in the product TUI
   layer, copy/open/resume in the session runtime.
3. Copy the JSONL into the native session store directory using the source
   basename when available. Pi copies into `getSessionDir()` keyed by basename
   and may overwrite a same-named session. Pipy deliberately diverges here to
   avoid data loss: if the basename already exists in the store, it appends a
   numeric suffix (`name-1.jsonl`, `name-2.jsonl`, ...). The import never mutates
   the source file.
4. Open the copied file as a `NativeSessionTree`, set the active leaf to the
   latest entry, rebuild provider-visible context from the active branch, and
   continue from there.
5. If the imported session header records a `cwd` that no longer exists, prompt
   the user to choose a working directory before opening, matching Pi's
   `MissingSessionCwdError` flow. In non-interactive contexts, fail with a clear
   typed error rather than blocking.
6. Emit the same session-lifecycle transition pipy uses for `/resume` (a
   resume-reason session start), so extension hooks and metadata observe a
   session switch.

Import is the product inverse of `.jsonl` export and is in scope here. This is
distinct from the deferred "raw transcript import" policy in
`docs/session-storage.md`, which concerns importing third-party agent
transcripts into the metadata archive; `/import` here imports pipy's own native
JSONL into the native product store.

Usage error (no path) prints `Usage: /import <path.jsonl>`, matching Pi.

## `/share` Secret Gist Upload

`/share` exports the current session to HTML and uploads it as a **secret**
(non-public) GitHub gist, then prints a share URL.

### Pi reference behavior

Pi's `handleShareCommand`:

1. Requires the `gh` CLI. It runs `spawnSync("gh", ["auth", "status"])` and
   treats any non-zero `status` as a failure, surfacing a **single**
   not-logged-in message: "GitHub CLI is not logged in. Run 'gh auth login'
   first." Because a missing binary makes `spawnSync` return `status !== 0` (with
   an ENOENT `error`), Pi does **not** distinguish "gh not installed" from "gh
   not logged in" — both fall through the same not-logged-in/auth-failure path.
   Pi does **not** use the GitHub REST API and does **not** read token env vars;
   auth is entirely delegated to `gh`.
2. Exports the session to a temp HTML file.
3. Shows a **cancellable loader** (the only user-interaction gate) and shells out
   to `gh gist create --public=false <html>`. There is **no explicit privacy
   confirmation prompt**; the user can only abort the in-flight loader.
4. Parses the gist id from the `gh` output URL and builds the share URL as
   `${PI_SHARE_VIEWER_URL || "https://pi.dev/session/"}#<gistId>`
   (`getShareViewerUrl`), printing both that share URL and the raw gist URL.
5. Cleans up the temp file; on `gh` failure surfaces the stderr message.

### Pipy stdlib adaptation

Pipy is stdlib-only and does not depend on the `gh` binary, so it deliberately
diverges on transport while matching the user-visible outcome (secret gist
created, share URL printed):

1. **Auth resolution.** Resolve a GitHub token without ever exporting or logging
   it:
   - `GITHUB_TOKEN` / `GH_TOKEN` environment variables first;
   - then, as an optional convenience, a token obtained at runtime from
     `gh auth token` if `gh` happens to be installed, used only to obtain a
     token, never bundled into the artifact;
   - otherwise fail with a clear, actionable message (e.g. "No GitHub token
     found. Set GITHUB_TOKEN or run `gh auth login`."). Because pipy resolves the
     token itself rather than delegating to `gh auth status`, it can give a
     clearer diagnostic than Pi — distinguishing "no token configured" from a
     rejected/expired token — which is a pipy improvement over Pi's single
     not-logged-in path.
2. **Privacy confirmation (pipy choice).** Pi has no privacy confirmation. Pipy
   may add one as a deliberate, pipy-specific UX choice (e.g. "Upload this
   session as a secret GitHub gist? The full transcript will be uploaded."),
   since uploading shifts a local artifact to a remote account. This is a pipy
   addition, not Pi parity, and it does not reduce exported content: the gist
   contains the same full HTML the `/export` path produces (minus auth tokens).
3. **Export to a temp HTML file** (owner-only permissions), then read it.
4. **Create the gist** via `urllib` `POST https://api.github.com/gists` with
   `{"public": false, "files": {"<name>.html": {"content": "<html>"}}}`,
   `Authorization: Bearer <token>`, `Accept: application/vnd.github+json`, and a
   pipy user-agent. The request must be cancellable (matching Pi's cancellable
   loader); on abort, print "Share cancelled" and clean up the temp file.
5. **On success**, parse the gist id/`html_url` from the response and print a
   share URL. Pipy's share viewer base URL is pipy-owned and configurable via env
   (analogous to Pi's `PI_SHARE_VIEWER_URL` / `getShareViewerUrl`, which builds
   `<base>#<gistId>`); default to printing the raw gist `html_url` when no pipy
   viewer is configured, and print it alongside any viewer URL.
6. **On failure** (non-2xx, network error, rate limit), surface the GitHub error
   message clearly and clean up the temp file.

The token is never written to the exported HTML, the JSONL, the gist content,
the metadata archive, or any log line.

## `--export <file>` Non-Interactive

`pipy ... --export <session.jsonl> [output]` exports an existing native session
file to HTML and exits, matching Pi's `main.ts` `--export` branch and
`exportFromFile`:

1. Resolve `<session.jsonl>`; error and exit non-zero if missing.
2. Open it as a `NativeSessionTree` (standalone, without a live runtime/system
   prompt), build the HTML from its header + entries + leaf.
3. Write to the optional second positional `output` argument, else default to
   `pipy-session-<basename>.html`.
4. Print `Exported to: <path>` and exit 0; on error print the message to stderr
   and exit 1.

The shipped pipy CLI surface is top-level `--export`, matching Pi's
export-and-exit shape on a session file with an optional output path:

```text
--export <file>   Export a native session file to HTML and exit
```

## `/changelog`

`/changelog` renders pipy changelog entries, matching Pi's
`handleChangelogCommand`:

- Read a packaged/repo `CHANGELOG.md` via a `get_changelog_path()` helper.
- Parse version sections (`parseChangelog` equivalent: split on `##
  [x.y.z]`/`## x.y.z` headers into ordered entries).
- `/changelog` prints all entries **oldest-first** under a "What's New" header:
  Pi parses the newest-first CHANGELOG and `handleChangelogCommand` reverses the
  list before rendering (`modes/interactive/interactive-mode.ts`), so the
  explicit command shows oldest→newest. (Distinct from the startup banner, which
  shows only the newest unseen entries — see below.)
- On startup, show only entries newer than the last-seen version (Pi's
  `getNewEntries` against a stored `lastChangelogVersion` in the non-secret
  settings store), skip the banner for resumed/continued sessions, and record
  the current version on a fresh install so the banner does not repeat. A
  collapse-changelog setting can condense the startup banner to a one-line
  "Updated to vX. Use /changelog to view full changelog." like Pi.

Changelog content is local UI text and is not written to the metadata archive.

## Self-Update / Distribution

Pipy is installed as a Python package, not via npm. The Pi distribution model
maps onto Python install methods.

### Pi reference surface

Pi's `package-manager-cli.ts` exposes a full extension/package + self management
CLI:

- `pi install <source> [-l]` — install an extension/package (npm, git, https,
  ssh, or local path; `-l` for project-local).
- `pi remove <source> [-l]` (alias `pi uninstall`) — remove a package.
- `pi list` — list installed packages from user and project settings.
- `pi update [source|self|pi] [--self] [--extensions] [--extension <source>]
  [--force]` — by default **bare `pi update` updates BOTH installed extensions
  AND pi itself**. `pi update self` / `pi update pi` updates pi only,
  `pi update --extensions` updates packages only, `pi update <source>` /
  `--extension <source>` updates one package, and `--force` reinstalls pi even
  when current.
- `pi config` — package/provider config flows.

Pi self-update detects the install method via `detectInstallMethod()`, which
returns one of `bun-binary | npm | pnpm | yarn | bun | unknown`, and runs the
matching package-manager command with `--ignore-scripts` (for example
`npm install -g --ignore-scripts <pkg>`, `pnpm install -g --ignore-scripts`,
`yarn global add --ignore-scripts`, `bun install -g --ignore-scripts`). For
`unknown`/unsupported methods (and Windows non-npm/pnpm), it prints manual
instructions and the executable location instead of running anything.

The pipy extension/package install/remove/list/config flows (the Python-only,
Pi-shaped equivalents of `pi install`/`remove`/`list`/`config` plus extension
updates) are owned by [extension-api.md](extension-api.md) and specified there,
not here. **This spec covers only pipy's self-update and version-check
surfaces.** The bare-`pipy update` "update both extensions and self" behavior is
a cross-cutting concern: this spec defines the self half, and the extension half
is defined in `docs/extension-api.md`.

### Version check

Add a stdlib-only version check analogous to `version-check.ts`:

- Fetch the latest published version via `urllib` from a pipy-owned endpoint
  (for example the PyPI JSON API `https://pypi.org/pypi/pipy/json`, reading
  `info.version`), with a short timeout and graceful failure.
- Short-circuit when `PIPY_SKIP_VERSION_CHECK` or `PIPY_OFFLINE` is set
  (mirroring `PI_SKIP_VERSION_CHECK` / `PI_OFFLINE`).
- Compare versions with a small semver-style comparator (stdlib only; do not add
  `packaging` as a runtime dependency unless it is already vendored/available).
- On startup, if a newer version exists, show a non-blocking notice with the
  install-method-aware upgrade command. Never block the session on the network.

### Install-method detection

Add a `detect_install_method()` helper analogous to Pi's `detectInstallMethod`
(which returns `bun-binary | npm | pnpm | yarn | bun | unknown`). The pipy
equivalent returns one of `uv-tool`, `pipx`, `pip`, `pip-user`, or `unknown`,
derived from the resolved executable/module path and environment markers:

- `uv tool` installs (path under the `uv` tools dir or `UV_TOOL_DIR`),
- `pipx` installs (path under the pipx venvs dir or `PIPX_HOME`),
- a system/user `pip` install,
- a development checkout / editable install (treated as `unknown` for
  self-update: print a clear "this is a development checkout, update via git"
  message),
- `unknown` otherwise.

### `pipy update` family

Provide a Pi-shaped update CLI, mapped to Python tooling:

- `pipy update self` / `pipy update pipy` — update pipy itself. Pick the upgrade
  command from `detect_install_method()`. Where the underlying tool supports it,
  pass an ignore-scripts equivalent (Pi runs every self-update command with
  `--ignore-scripts`):
  - `uv-tool` -> `uv tool upgrade pipy`
  - `pipx` -> `pipx upgrade pipy`
  - `pip` -> `pip install --upgrade pipy`
  - `pip-user` -> `pip install --user --upgrade pipy`
  - `unknown`/dev -> print actionable manual instructions and the executable
    location, and exit without running anything (matching Pi's
    `printSelfUpdateUnavailable`).
- `--force` reinstalls even when already current (matching Pi's `--force`).
- When the version check reports pipy is already current and `--force` is not
  set, print "pipy is already up to date (vX)" and do nothing, matching Pi.
- If the detected method is unsafe to run automatically, print the exact command
  the user can run themselves (matching Pi's `printSelfUpdateFallback`).
- If `PIPY_SELF_UPDATE_PACKAGE` is unset, do not guess the PyPI distribution
  name. Print a fail-safe manual message instead. This prevents accidentally
  installing an unrelated package that happens to use the development project
  name.
- A bare `pipy update` now composes both halves: it updates installed extension
  packages through [extension-api.md](extension-api.md)'s managed package update
  path, then runs this self-update half. `--extensions` / `--extension <source>`
  select package updates only; `self` / `pipy` selects self-update only.

`pipy update self` / `pipy update pipy` is the in-scope self-update parity
surface. The broader
`install`/`remove`/`uninstall`/`list`/`config` extension/package management and
extension-update flows are the extension-platform concern owned by
[extension-api.md](extension-api.md); this spec only requires self-update and
the version-check notice. Document those extension/package commands there, not
here.

### Install documentation

Add install docs to `README.md` and a quickstart, covering stdlib-only,
dependency-light installs:

```sh
# Recommended from a local checkout during development
uv tool install .

# Published package (replace with the real owned distribution name)
uv tool install <published-pipy-distribution>

# pipx
pipx install <published-pipy-distribution>

# pip (user)
pip install --user <published-pipy-distribution>
```

Document the self-update commands (`pipy update self`), the version-check
opt-out env vars (`PIPY_SKIP_VERSION_CHECK`, `PIPY_OFFLINE`), the explicit
`PIPY_SELF_UPDATE_PACKAGE=<published-pipy-distribution>` requirement for
automatic package-manager updates, and that a curl-style one-line installer is
optional future polish (Pi's
`curl -fsSL https://pi.dev/install.sh | sh`) and must remain stdlib/uv-friendly
if added. Mark the existing "local `uv`-driven project" framing in
`docs/backlog.md`'s selected export/distribution slice as addressed once these
land.

## Invariants

- **Pipy-owned Python boundaries.** Not a TypeScript port; match user-facing
  behavior through native Python code and pipy storage.
- **Stdlib-only, no new runtime dependencies.** HTML export uses stdlib string
  templating and `importlib.resources`; gist upload uses `urllib` against the
  GitHub REST API; version check uses `urllib`. No PyGithub, no `requests`, no
  bundler, no mandatory `gh` binary (the `gh` token is an optional convenience
  source only).
- **Full-session export.** HTML export carries the full native session tree
  (all entries, with the leaf selecting the active path), matching Pi. JSONL
  export carries the active branch re-chained linearly, matching Pi's
  `exportToJsonl`. Privacy-first metadata stripping is NOT a parity constraint
  for these surfaces.
- **Pi does not redact on export; auth-token withholding is a pipy choice.** Pi
  serializes session entries as-is. Pipy additionally withholds only auth
  tokens, API keys, and provider credentials from HTML/JSONL/gist content, the
  metadata archive, and logs, reusing the existing pipy secret-redaction
  boundary. No other transcript content is dropped.
- **The metadata-only `pipy-session export` is superseded for product parity.**
  It may persist as a separate summary-safe catalog/learning utility (with its
  default metadata-only behavior and opt-in sidecar unchanged), but it is not
  the product export surface and must not be presented as the parity answer for
  `/export`. The product `/export`, `/import`, `/share`, and `--export` operate
  on the native session tree.
- **`/share` matches Pi's user-visible outcome, not its transport.** Pi shells
  out to `gh gist create --public=false` and gates only with a cancellable
  loader (no privacy confirmation); pipy uploads via `urllib` to the GitHub gists
  API with `public:false`. Any pipy confirmation prompt is an optional pipy
  addition and does not reduce content; the uploaded artifact is the full HTML
  minus auth tokens.
- **Export/import scope.** HTML export carries the full tree; `.jsonl` export
  linearizes the active branch; `/import` copies the file into the native store
  under its basename or a collision-safe suffixed name and opens/resumes it.
- **Self-update is install-method aware and fails safe.** Unknown/dev installs
  print manual instructions and do not run package managers automatically.
- **Network calls are optional and non-blocking.** Version check, gist upload,
  and self-update degrade gracefully offline and honor the opt-out env vars.
- **No archive leakage.** Changelog text, export paths beyond safe labels, gist
  URLs, and tokens are not written into `pipy-session` metadata records. The
  metadata archive may record only safe labels such as "exported html",
  "exported jsonl", "imported session", "shared gist" with counts, never bodies,
  URLs containing secrets, or transcript content.

## Implementation Milestones

The baseline below has shipped. Future polish should keep the conformance gate
passing and update this section when behavior changes.

1. **HTML export core.** Add a packaged `export_html` template/CSS/JS, a
   stdlib `generate_html(session_data, theme)` that base64-embeds
   `{header, entries, leafId, systemPrompt, tools}`, and
   `export_native_session_to_html(tree, *, output_path, theme)` plus
   `export_from_file(path, output_path)`. ANSI->HTML for captured terminal
   output. Secret redaction on embedded content. Tests over a fake-provider
   session tree.
2. **JSONL export.** `export_native_branch_to_jsonl(tree, output_path)`:
   header + linearly re-chained active-branch entries; default timestamped
   filename. Tests asserting linear `parentId` chain and round-trip shape.
3. **`/export` command.** Wire the tool-loop product TUI `/export [path]` with
   Pi-style argument parsing (`.jsonl` vs HTML routing, quoted paths),
   in-memory/empty-session errors, and captured-stream diagnostics in
   non-interactive mode.
4. **`--export` CLI.** Non-interactive export-and-exit on a native session file
   with optional output path, `Exported to:`/exit-code behavior, and `--help`
   text.
5. **`/import` command.** Collision-safe copy-into-store + open + resume, with
   the replace confirmation in the
   product TUI layer (not the runtime), missing-cwd prompt, file-not-found error,
   usage error, and lifecycle event parity. Tests asserting a session file is
   created/opened in the store, the source is untouched, and provider context is
   rebuilt from the imported branch.
6. **`/share` command.** Token resolution
   (`GITHUB_TOKEN`/`GH_TOKEN`/`gh auth token`), temp HTML export, cancellable
   `urllib` gist creation (`public:false`), share-URL + `html_url` output, and
   error/cleanup handling. Tests use a fake HTTP boundary; no real network.
7. **`/changelog`.** Changelog parser, full-list command, and startup
   new-entries banner with last-seen-version tracking and collapse setting.
8. **Version check + self-update.** `detect_install_method`, stdlib version
   lookup with opt-out env vars, and `pipy update self|pipy [--force]` mapped
   to `uv tool`/`pipx`/`pip`, with fail-safe behavior for unknown/dev installs.
   A non-blocking startup notice remains future polish.
9. **Install docs.** Update `README.md`, quickstart, `docs/session-storage.md`,
   `docs/pi-parity.md`, `docs/backlog.md`, and this spec to match shipped
   behavior. Note the metadata-only `pipy-session export` as superseded for
   product parity but retained as a catalog utility.

## Verification Plan

The conformance gate is the implementation source of truth, in the established
`scripts/parity_checks/` style (a `main()` that prints a machine-readable
`--json` result and exits non-zero on failure, matching the other behavior
checks):

```sh
uv run python scripts/parity_checks/export_distribution_conformance.py --json
```

The script drives deterministic native-session fixtures in a temporary workspace
with fake/stubbed network boundaries and fails unless the export/import/share
round-trip works. It verifies that:

1. a native session tree is seeded with the fake provider (user/assistant
   messages plus at least one tool call/result and a `write`/`edit` diff so the
   transcript is non-trivial);
2. `/export` (HTML) writes a single self-contained file under the default name,
   the file decodes to a session-data JSON that contains the full session tree
   (header, **all** entries, leaf id), and the rendered transcript content (user
   text, assistant text, tool call, tool result) is present; if the seeded tree
   has more than one branch, the export includes the off-branch entries too;
3. `/export <path.jsonl>` writes a header line followed by linearly re-chained
   active-branch entries (`parentId` chain is linear; first entry `parentId`
   is null);
4. `/import <that.jsonl>` opens a session file in the store (using a suffixed
   name on basename collision), leaves the source file unchanged, rebuilds
   provider context from the imported branch, and the imported session's
   active-branch entries match the exported ones (round-trip equality of message
   content and order);
5. the HTML and JSONL exports contain no auth tokens/API keys even when the
   seeded session includes a fake secret in tool output (secret is redacted,
   all other content preserved);
6. `--export <session.jsonl>` produces an HTML file and exits 0; a missing input
   exits non-zero;
7. `/share` resolves a fake token, posts to a stubbed GitHub gist endpoint with
   `public:false`, never includes the token in the gist body, and prints a
   share URL on success; cancellation aborts cleanly;
8. the metadata-only `pipy-session export` remains metadata-only and is NOT used
   as the product export source (assert the product HTML/JSONL came from the
   native tree, and that no transcript body leaked into `pipy-session`
   metadata records);
9. `detect_install_method` returns a known value for a simulated
   uv-tool/pipx/pip layout and `unknown` for a dev checkout, and
   `pipy update self` selects the matching upgrade command without executing it
   under test (dry-run/command-plan assertion);
10. the version check honors `PIPY_SKIP_VERSION_CHECK`/`PIPY_OFFLINE` and
    degrades gracefully when the network is unavailable (stubbed).

Canonical deterministic scenario:

```text
/name export-conformance

User: HELLO
Assistant(fake): SEEN:HELLO  (+ one fake tool call/result, + one write diff)

/export                 -> pipy-session-<stem>.html      (full session tree)
/export out.jsonl       -> header + linear active branch
/import out.jsonl       -> session opened in store, context == exported branch
/share                  -> stub gist POST public:false, share URL printed
--export <session.jsonl> [out.html]  -> Exported to: out.html; exit 0
```

Assertions:

```text
HTML embeds full-tree session data (header, all entries, leaf, system prompt, tools, renderedTools?)
HTML and JSONL contain no token/secret; all other transcript content preserved
out.jsonl: first entry parentId == null; entries form a linear chain
import opens a session file in the store (basename keyed); source out.jsonl unchanged
imported active branch == exported active branch (content + order)
/share posts public:false; token absent from gist body; share URL printed
pipy-session metadata records contain no transcript bodies, URLs, or tokens
```

Focused tests should cover:

- HTML generation: template substitution, base64 session-data round-trip,
  ANSI->HTML for captured output, theme-variable derivation, default filename,
  in-memory/empty-session errors;
- JSONL export: linear re-chaining, default timestamped name, active-branch only;
- import: copy-into-store, source immutability, missing-cwd prompt path,
  file-not-found error, usage error, context rebuild, lifecycle event;
- share: token source precedence, `public:false` request body, token never in
  body/log/artifact, cancellation cleanup, GitHub error surfacing (all against a
  stubbed HTTP boundary);
- changelog: parse/order, full list, startup new-entries filtering, last-version
  tracking, collapse setting;
- version check: opt-out env vars, semver comparison, offline graceful failure;
- self-update: install-method detection per layout, command selection per
  method, fail-safe behavior for unknown/dev, `--force` and already-current
  paths;
- archive privacy: no transcript bodies, gist URLs, or tokens reach
  `pipy-session` metadata records.

Before treating implementation as complete, run:

```sh
uv run python scripts/parity_checks/export_distribution_conformance.py --json
uv run pytest tests/test_native_export_html.py
uv run pytest tests/test_native_export_jsonl.py
uv run pytest tests/test_native_session_import.py
uv run pytest tests/test_native_share_gist.py
uv run pytest tests/test_changelog.py
uv run pytest tests/test_self_update.py
just check
```

Optionally add a Pi comparison smoke (for example
`scripts/tmux_export_compare.sh <out-dir>`) that exports the same canonical
scenario from both pi and pipy and compares user-visible behavior: HTML opens
and shows the full transcript, `.jsonl` export round-trips through `/import`,
and `/share` produces a secret gist with a share URL. Exact Pi HTML/CSS or
gist-payload byte matching is not the hard gate; deterministic pipy conformance
is. Pi uses the `gh` CLI for `/share`; pipy uses `urllib` against the GitHub
API, so only user-visible parity (secret gist created, share URL printed) is
compared, not the transport.

Update `docs/session-storage.md`, `docs/session-tree.md`, `docs/pi-parity.md`,
`docs/backlog.md`, `README.md`, and this spec to match shipped behavior, and get
an independent review pass for the gist-upload and self-update slices.
