---
name: pipy-parity-loop
description: Use when driving one pi-mono parity gap in this repo end to end (select gap → plan → different-family plan review → impl plan → implement → docs → code-review loop until CLEAN → commit). Triggers: "parity loop", "next parity gap", "close a pi-mono gap".
---

# Pipy Parity Loop (Claude Code)

Follow the canonical workflow in `docs/parity-loop/skill-body.md` (resolve it
against the repo root and read it now). Drive exactly one parity gap end to end.

Claude-specific notes:
- Run phases directly in this Claude Code session. Do not delegate parity-loop
  work to subagents, and never delegate the different-family review gate
  (`pi-review-loop`) to an `Agent`/Task-style worker.
- Honor the hard rules in the body: never self-grade, gates re-run after every
  fix, operator override is a stop — not a pass.
