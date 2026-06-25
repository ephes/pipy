# pipy documentation

Pipy is a local-first coding-agent harness experiment. The product direction is
`pipy-native`: a Python runtime that owns provider access, tool boundaries,
session semantics, and privacy-conscious archive metadata. Pipy is intended to
work both as a CLI/TUI application and as an embeddable headless Python runtime
for programs that need agentic workflow support.

**Start here for the parity roadmap:** [Parity Plan](parity-plan.md) is the
single clear plan for reaching real feature parity with Pi — the slash-command
and CLI matrices, the list of accidental pipy-only surfaces to remove or
realign, and the index of per-topic specs with their conformance gates. The
latest comparison snapshot against `/Users/jochen/src/pi-mono` is
[Pi-Mono Gap Audit](pi-mono-gap-audit.md).

Read these documents in order to learn the project from the outside in:

1. [Quickstart](quickstart.md): install from a checkout, start a first session,
   configure a provider, and understand where local state is written.
2. [Using pipy](usage.md): interactive mode, slash commands, sessions, context
   files, and the current CLI reference from a user point of view.
3. [Providers and models](providers.md): list models, choose providers, configure
   credentials, and add custom `models.json` rows.
4. User-facing terminal setup: [Terminal Setup](terminal-setup.md) and
   [tmux Setup](tmux.md).
5. [Architecture](architecture.md): the current runtime, diagrams, codebase
   map, and the isolation boundary between domain logic and adapters.
6. [Pi Parity](pi-parity.md): what has already been slopforked from Pi, what
   remains, and how pipy's architecture differs from Pi's.
7. [Parity Plan](parity-plan.md): the clear plan to reach real Pi parity —
   command/flag matrices, accidental-surface cleanup, and big-topic spec index.
8. [Pi-Mono Gap Audit](pi-mono-gap-audit.md): the latest ranked comparison
   against the local Pi checkout, with implementation contracts for the largest
   remaining gaps.
9. [Harness Spec](harness-spec.md): detailed design rationale, event
   vocabulary, native runtime direction, adapter boundaries, and deferred
   design.
10. [Python SDK and Headless Embedding](sdk.md): the current in-process Python
   embedding surface and how it relates to JSON/RPC automation.
11. Big-topic parity specs (target designs, one per large surface):
   [Session Tree](session-tree.md), [Extension API](extension-api.md),
   [Provider Catalog](provider-catalog.md), [Settings & Config](settings-config.md),
   [Automation & RPC](automation-rpc.md), [TUI Workflow](tui-workflow.md),
   [Export & Distribution](export-distribution.md), and
   [User Documentation](user-documentation.md).
12. [Session Storage](session-storage.md): the metadata-only catalog utility.
   Note: this is a pipy-specific layer, **not** the product session store — the
   full-transcript [Session Tree](session-tree.md) is the shipped product
   session source of truth (`pipy_harness.native.session_tree`), proven by
   `scripts/parity_checks/session_tree_conformance.py --json`.
13. [Backlog](backlog.md): current product planning, completed slices,
   near-term priorities, and deferred boundaries.

The short version: pipy is no longer just a session recorder. The repository now
contains a line-oriented native shell, direct provider ports, bounded read,
proposal and apply boundaries, conservative archive tooling, and
a subprocess capture path kept for reference workflows rather than the product
runtime.
