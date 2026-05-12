# pipy documentation

Pipy is a local-first coding-agent harness experiment. The product direction is
`pipy-native`: a Python runtime that owns provider access, tool boundaries,
session semantics, and privacy-conscious archive metadata.

Read these documents in order to learn the project from the outside in:

1. [Architecture](architecture.md): the current runtime, diagrams, codebase
   map, and the isolation boundary between domain logic and adapters.
2. [Pi Parity](pi-parity.md): what has already been slopforked from Pi, what
   remains, and how pipy's architecture differs from Pi's.
3. [Harness Spec](harness-spec.md): detailed design rationale, event
   vocabulary, native runtime direction, adapter boundaries, and deferred
   design.
4. [Session Storage](session-storage.md): session archive layout,
   metadata-only capture rules, sync behavior, and privacy policy.
5. [Backlog](backlog.md): current product planning, completed slices,
   near-term priorities, and deferred boundaries.

The short version: pipy is no longer just a session recorder. The repository now
contains a line-oriented native shell, direct provider ports, bounded read,
proposal, apply, and verification boundaries, conservative archive tooling, and
a subprocess capture path kept for reference workflows rather than the product
runtime.
