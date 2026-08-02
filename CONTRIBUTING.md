# Contributing to pipy

This repository uses a small, trunk-based contribution workflow. Read
[`AGENTS.md`](AGENTS.md) before starting; it is the authoritative repository
process policy. Product architecture is documented in
[`docs/architecture.md`](docs/architecture.md), with broader runtime and privacy
invariants in [`docs/harness-spec.md`](docs/harness-spec.md).

## Set up a clean checkout

You need Git, Python 3.11 or newer, [`uv`](https://docs.astral.sh/uv/), and
[`just`](https://just.systems/). From the root of a clean checkout, install the
locked project and development environment, then inspect the available recipes:

```sh
uv sync
just --list
```

`uv sync` installs the development tools declared in `pyproject.toml`, including
Pytest, Ruff, Mypy, and Zensical. Run all commands below from the repository
root.

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

Before treating any contribution as complete, run the repository-wide gate and
build the documentation:

```sh
just check
just docs-build
git diff --check
git status --short
```

`just check` runs lint, repository-wide format checking, Mypy over `src` and
`tests`, and the full Pytest suite. `just docs-build` builds the Zensical site.
Inspect the final status and diff so code, tests, docs, and generated-artifact
expectations agree and the change contains only its intended paths.

## Independent review and review budget

Follow the review budget in [`AGENTS.md`](AGENTS.md): at most two rounds for
docs/specs/plans and three for code unless the operator explicitly authorizes
more material work. Stop on a clean result or when further feedback is no longer
material; do not chase prose precision or reviewer-induced churn. Correctness
defects—especially shared-mutable-state races or lost updates—still block a
commit regardless of the cap. Record any unresolved material finding instead
of silently treating the budget as a pass.

## Session and workflow-data privacy

Read [`docs/session-storage.md`](docs/session-storage.md) before changing
capture, archive layout, catalog commands, automatic capture, synchronization,
or privacy policy. Keep raw coding-agent records outside Git by default, never
place secrets or transcript/tool/file bodies in metadata-first workflow fields,
and commit only intentionally promoted, summary-safe artifacts. The full-content
native product session tree and the summary-safe `pipy-session` archive have
different trust boundaries; changes must preserve both rather than copying one
store's policy onto the other.
