# pipy documentation

Pipy is a native Python coding-agent product with a Pi-shaped interactive TUI,
headless JSON/RPC/SDK modes, direct provider transports, model-driven tools,
extensions, and private full-content product sessions. A separate metadata-only
workflow archive supports summary-safe capture and learning; it is not the
product session store. Subprocess wrapping remains a reference/capture facility,
not the primary runtime.

**Start here for current architecture disposition:** the
[2026-07-29 Architecture Quality Assessment](2026-07-29-architecture-quality-assessment.md)
records the exact revisions, final program evidence, residuals, and bounded
reload-contract follow-up. For product parity, the historical
[Parity Plan](parity-plan.md) remains the command/flag policy and topic-spec
index, while the [Pi-Mono Gap Audit](pi-mono-gap-audit.md) is the current
comparison and ranking input.

Read these documents in order to learn the project from the outside in:

1. [Quickstart](quickstart.md): install from a checkout, start a first session,
    configure a provider, and understand where local state is written.
2. [Using pipy](usage.md): interactive mode, slash commands, sessions, context
    files, and the current CLI reference from a user point of view.
3. [Providers and models](providers.md): list models, choose providers, configure
    credentials, and add custom `models.json` rows.
4. [Settings](settings.md) and [Keybindings](keybindings.md): configure global
    and project defaults, resource filters, model cycling, and TUI shortcuts.
5. [Customization](customization.md): add skills, prompt templates, custom slash
    commands, and chrome themes, and where extensions/packages/providers fit.
6. [Packages](packages.md): install, update, filter, and share trusted local or
    managed-git resource packages.
7. [Sessions](sessions.md) and [Compaction](compaction.md): native product
    sessions, resume/fork/clone/tree workflows, and long-context reduction.
8. User-facing terminal setup: [Terminal Setup](terminal-setup.md) and
    [tmux Setup](tmux.md).
9. Automation and embedding: [JSON Mode](json.md) for one-shot full event
    streams, [RPC Mode](rpc.md) for long-lived JSONL control, and
    [Python SDK and Headless Embedding](sdk.md) for in-process Python callers.
10. [Architecture](architecture.md): the current runtime, ownership boundaries,
    executable gates, and measured residual risks. The Phase 0–7
    [Architecture Migration](architecture-migration.md) and reviewed
    [Architecture Quality Improvement Program](specs/2026-07-24-architecture-quality-improvement-plan.md)
    are completed/reconciled historical evidence.

    The integration ledger remains open through committed endpoint
    `b64ceb7db9581bf3ebfab51f5803c513c1fdb549`: 12 program/integration commits
    since `fe474e0e55b3d1e8ae370534acb54a0a5fd9496b`, with 298 changed paths. The
    exhaustive partition union exactly covers all 298 changed paths:

    - A: 29/29, 220,750 bytes/5,384 lines, valid complete CLEAN.
    - B: 22/22, 359,459 bytes/8,776 lines, valid complete CLEAN.
    - C: 14/14, 111,705 bytes/2,418 lines, valid complete CLEAN.
    - D: 103/103, 410,314 bytes/9,494 lines, valid complete CLEAN.
    - E: 150/150, 406,331 bytes/9,333 lines, valid complete CLEAN.
    - Refreshed F: 19/19, 139,365 bytes/1,892 lines, valid complete CLEAN.

    Slice 16 landed as `7deb8d8807f4e7eb52f7c9c8bd9e0ad30cb60727`
    (`docs: close architecture quality program`). The three integration-fix
    commits are the original Bundle F ledger fix
    `ffeb86f0319efd28f6f360174ae640fa358761d0`
    (`docs: reconcile architecture program ledger`), warning-state closure
    `aea52b438713ce04fcad93ae32927ff156574aac`
    (`docs: record integration warning closure`), and README/provider-catalog
    closure `b64ceb7db9581bf3ebfab51f5803c513c1fdb549`
    (`docs: align provider catalog status`). A valid complete exact-schema
    cross-cutting review by Pi `openai-codex/gpt-5.6-sol` at committed endpoint
    `b64ceb7` found only this incomplete-ledger Warning. It found zero Critical or
    Suggestion findings, omissions, forbidden tool uses, skips, truncations, or
    redactions. The incomplete-ledger documentation/test fix is implemented in
    the current worktree; its fresh cross-cutting re-review remains pending, so
    no overall integration CLEAN is claimed.

    Current-worktree verification after this ledger/test fix is strict Mypy
    across 169 source files, combined Mypy across 438 source/test files, and
    `just check` at 4,829 passed / 2 skipped. Ruff formatting covers 480 files.
    Current-worktree metrics are 34 / 18 repository/source C901 findings, 81,738
    / 121,175 source/test physical lines, 43 `ToolLoopTerminalUi` fields, one
    source ignore, and 5,433 / 6,329 lines in `tool_loop_session.py` / `tui.py`.
    Docs are clean, both theme sources are `pi`, and pre-commit is absent. Slice 14 stress evidence
    remains focused 20x, groups 10x, PTY smoke 5x, then the full check; the
    latest PTY smoke is 8/8.

    The completed/reconciled disposition is unchanged. The explicit next
    architecture boundary is bounded transactional-reload contract completion
    or formal reconciliation before ordinary product-parity selection.
11. [Architecture Quality Assessment — 2026-07-29](2026-07-29-architecture-quality-assessment.md):
    exact revisions, before/after evidence, ownership outcomes, residuals,
    comparisons, and the bounded next queue.
12. [Pi Parity](pi-parity.md): what has already been slopforked from Pi, what
    remains, and how pipy's architecture differs from Pi's.
13. [Parity Plan](parity-plan.md): the historical matrix and continuing policy
    index for real Pi parity —
    command/flag matrices, accidental-surface cleanup, and big-topic spec index.
14. [Pi-Mono Gap Audit](pi-mono-gap-audit.md): the latest ranked comparison
    against the local Pi checkout, with implementation contracts for the largest
    remaining gaps.
15. [Harness Spec](harness-spec.md): detailed design rationale, event
    vocabulary, native runtime direction, adapter boundaries, and deferred
    design.
16. Big-topic parity specs (target designs, one per large surface):
    [Session Tree](session-tree.md), [Extension API](extension-api.md),
    [Provider Catalog](provider-catalog.md), [Settings & Config](settings-config.md),
    [Automation & RPC](automation-rpc.md), [TUI Workflow](tui-workflow.md),
    [Export & Distribution](export-distribution.md), and
    [User Documentation](user-documentation.md).
17. [Session Storage](session-storage.md): the metadata-only catalog utility.
    Note: this is a pipy-specific layer, **not** the product session store — the
    full-transcript [Session Tree](session-tree.md) is the shipped product
    session source of truth (`pipy_harness.native.session_tree`), proven by
    `scripts/parity_checks/session_tree_conformance.py --json`.
18. [Backlog](backlog.md): current product planning, completed slices,
    near-term priorities, and deferred boundaries.

The short version: pipy is a native coding agent. Its canonical agent and
coding-session layers drive direct providers, tools, extensions, private product
sessions, automation transports, and an inline-scrollback terminal UI; the
metadata workflow archive and subprocess capture path remain separate auxiliary
facilities.
