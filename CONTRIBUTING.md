# Contributing to pipy

This repository uses a small, trunk-based contribution workflow. Read
[`AGENTS.md`](AGENTS.md) before starting; it is the authoritative repository
process policy. Product architecture is documented in
[`docs/architecture.md`](docs/architecture.md), with broader runtime and privacy
invariants in [`docs/harness-spec.md`](docs/harness-spec.md).

## Set up a clean checkout

You need Git, Python 3.11–3.15, [`uv`](https://docs.astral.sh/uv/), and
[`just`](https://just.systems/). From the root of a clean checkout, install the
locked project and development environment, then inspect the available recipes:

```sh
uv sync --python 3.15
just --list
```

`uv sync` installs the development tools declared in `pyproject.toml`, including
Pytest, Ruff, Mypy, and Zensical. Run all commands below from the repository
root. Full-suite CI covers Python 3.11, 3.14, and 3.15; real-PTY jobs cover
Linux and macOS on 3.14 and 3.15, and runtime-required codemode probes cover Linux
on both versions. Lint, formatting, types, and docs run on Python 3.14.

To check a second interpreter without replacing the normal `.venv`:

```sh
UV_PROJECT_ENVIRONMENT=.venv-py315 UV_PYTHON=3.15 uv sync --frozen --all-extras
UV_PROJECT_ENVIRONMENT=.venv-py315 UV_PYTHON=3.15 just check
```

The host version does not change codemode's pinned CPython 3.14.7 WASI guest.

## Work directly on `main`

Pipy uses trunk-based development. Make routine changes directly on `main` in
small, coherent, reviewable increments, and keep every commit green. Do not
create routine feature, topic, or other side branches. If a clean worktree is
on another branch, return to `main` before editing; if that branch already holds
completed work, merge it back promptly, validate `main`, and continue there.

Keep a slice bounded. Do not combine an architecture change with unrelated
cleanup, and update documentation in the same change whenever behavior,
workflow, or user-facing usage changes. Update release notes when they apply;
do not invent a release or publication process.

## Preserve the architecture boundaries

Use the ownership map and dependency directions in
[`docs/architecture.md`](docs/architecture.md); do not reproduce them in a new
module or document. They are executable contracts, not naming conventions.
Before adding or moving an import, inspect and run the relevant gates, especially
`tests/test_architecture_import_boundaries.py`, the focused
`tests/test_architecture_agent_*_boundaries.py` modules, and
`tests/test_architecture_archive_sdk_contracts.py`. The gates cover static
imports, selected recursive/fresh-process dependency closures, exact direct
dependencies, and the separation between the two session stores.

## Develop with focused tests

Run the smallest existing test modules that exercise the code you are changing,
then widen coverage as the slice stabilizes. For example, an import-boundary
change should start with:

```sh
uv run pytest tests/test_architecture_import_boundaries.py
uv run pytest tests/test_architecture_agent_loop_boundaries.py \
  tests/test_architecture_agent_runtime_ports_boundaries.py
```

Add focused characterization when an ownership, precedence, failure-order, or
privacy invariant is not already executable. Do not weaken an architecture,
trust, path, privacy, lint, or type gate merely to make a change pass.

Tests isolate `HOME` to avoid reading or changing developer resources. The legacy
score test runs its real probes with the already installed test interpreter:

```sh
PIPY_PARITY_PYTHON="$PWD/.venv/bin/python" bash scripts/parity_score.sh
```

This explicit mode never invokes `uv`, resolves an environment, or installs
Python. It checks the selected virtual-environment interpreter, Pytest, and both
installed console scripts before scoring any row; invalid configuration fails
immediately. The test also traps accidental `uv` calls and verifies interpreter
links and `pyvenv.cfg` stay unchanged. Keep home isolation in place rather than
restoring the real `HOME` to run nested checks. Ordinary `just parity-score`
without this variable retains its existing `uv run` dispatch.

Before treating any contribution as complete, run the repository-wide gate and
build the documentation:

```sh
just check
just docs-build
git diff --check
git status --short
```

`just check` runs lint, repository-wide format checking, Mypy over `src` and
`tests`, and the full Pytest suite. `just docs-build` builds the Zensical site
through `scripts/docs.py build`, with strict link and anchor validation.
The docs build validates the site locally. Read the Docs uses
[`.readthedocs.yaml`](.readthedocs.yaml) to install only the locked `docs`
dependency group and build the same Zensical site on Python 3.14. This requires
neither the product package nor the optional codemode runtime. Hosted commands
use `python -m uv`, so they work even when pip's executable directory is absent
from RTD's `PATH`.

Inspect the final status and diff so code, tests, docs, and generated-artifact
expectations agree and the change contains only its intended paths.

## Documentation hosting

The Read the Docs project name is `pipy-agent` (the name `pipy` belongs to
another repository). Documentation is published at
<https://pipy-agent.readthedocs.io/en/latest/>. The owner imported the project on
2026-10-17; [build 35212674](https://app.readthedocs.org/projects/pipy-agent/builds/35212674/)
succeeded, and the homepage, MCP, and codemode guides returned HTTP 200.
Build status is available at <https://app.readthedocs.org/projects/pipy-agent/builds/>.
For reference, the one-time setup is:

1. Open <https://app.readthedocs.org/dashboard/import/> in the owner's RTD account.
2. Import `https://github.com/ephes/pipy`, using project name `pipy-agent`, branch
   `main`, and configuration file `.readthedocs.yaml`.
3. Confirm that the GitHub integration created the webhook, then trigger the
   `latest` build. Check that it succeeds before advertising the hosted URL.

Pushes to `main` rebuild `latest` through the RTD integration. The RTD `latest` version tracks
the checkout, including Unreleased changes. Old release tags without the hosting
configuration are not expected to build. Project creation/authentication belongs
to Read the Docs; no API tokens or webhook credentials belong in this repository.

To reproduce the hosted build without changing the normal `.venv`:

```sh
UV_PROJECT_ENVIRONMENT=.venv-docs uv sync --frozen --only-group docs --python 3.14
UV_PROJECT_ENVIRONMENT=.venv-docs uv run --frozen --only-group docs python scripts/docs.py build
mkdir -p _readthedocs/html
cp -a site/. _readthedocs/html/
```

The last two commands reproduce RTD's artifact handoff. `site/` and
`_readthedocs/` are generated output and remain ignored.

Always use `just docs-build` or `python scripts/docs.py build` with the docs
dependencies installed. The wrapper stages current `docs/` files, including
untracked contributions, and excludes the reserved `docs/audit/` and
`docs/parity-loop/runs/` scratch directories before reading their contents.
The exclusions live in `[tool.pipy_docs]` in `zensical.toml`; Zensical 0.0.41
does not implement MkDocs-style `exclude_docs`. Documentation symlinks are
refused so they cannot bypass this boundary. A successful build replaces the
generated `site/` directory completely, removing stale artifacts from older
builds. Failed or interrupted builds preserve the last successful output. Source scratch
files remain untouched.

`just docs-serve` uses the same filtered inputs in an isolated preview. It
mirrors document additions, edits and deletions every quarter second, restarts
Zensical from fresh generated state on deletions or configuration changes, and
stops its child process on Ctrl-C. Preview reports link warnings; Zensical's
strict mode is supported for builds only, so run `just docs-build` before shipping.
Preview output stays temporary; use `just docs-build` to publish local `site/`.
Direct `zensical build` or `zensical serve` bypasses the exclusion boundary.

### Rendered navigation checks

After changing navigation or documentation assets, inspect the built site at
both mobile and desktop widths. An optional behavioral check uses an **already
installed** Node.js, Playwright module, and Chromium executable; it installs
nothing and is separate from the Python test suite. Serve `site/` locally after
`just docs-build`, then in another terminal run:

```sh
PIPY_PLAYWRIGHT_MODULE=/path/to/playwright-core/index.mjs \
PIPY_BROWSER_EXECUTABLE=/path/to/chromium \
node scripts/docs_browser_check.mjs http://127.0.0.1:8000/ /tmp/pipy-docs-browser
```

For example, `python -m http.server 8000 --bind 127.0.0.1 --directory site`
serves the generated output. Stop the server after checking. The command tests
real drawer focus, keyboard/search/history/resize behavior and contained code
blocks at 390 and 1440 pixels, and saves screenshots plus a JSON report outside
the source tree. Inspect the diagrams and screenshots too; automated bounds
checks do not establish legibility. The normal docs build needs no browser.

## Independent review and review budget

Follow the diminishing-returns review rule in [`AGENTS.md`](AGENTS.md). There
is no fixed round cap: stop on a clean result, when further feedback is no longer
material, or when the documented churn signals apply. Fix material findings and
verify the repair; do not chase prose precision. Correctness defects in shared
mutable state are exempt from stopping rules and still block a commit. Record
unresolved findings and distinguish advisory closure from CLEAN. The parity-loop
workflow retains its mandatory different-family CLEAN gate.

## Session and workflow-data privacy

Read [`docs/session-storage.md`](docs/session-storage.md) before changing
capture, archive layout, catalog commands, automatic capture, synchronization,
or privacy policy. Keep raw coding-agent records outside Git by default, never
place secrets or transcript/tool/file bodies in metadata-first workflow fields,
and commit only intentionally promoted, summary-safe artifacts. The full-content
native product session tree and the summary-safe `pipy-session` archive have
different trust boundaries; changes must preserve both rather than copying one
store's policy onto the other.
