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
    [Architecture Migration](architecture-migration.md) is completed historical
    evidence; the reviewed
    [Architecture Quality Improvement Program](specs/2026-07-24-architecture-quality-improvement-plan.md)
    has complete Slice 16 implementation; independent review complete; landed
    Slice 16 commit pending.
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
