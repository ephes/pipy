---
name: pipy-parity-loop
description: Use when driving one pi-mono parity gap in this repo end to end (select gap → plan → different-family plan review → impl plan → implement → docs → code-review loop until CLEAN → commit). Triggers: "parity loop", "next parity gap", "close a pi-mono gap".
---

# Pipy Parity Loop (Claude Code)

Follow the canonical workflow in `docs/parity-loop/skill-body.md` (resolve it
against the repo root and read it now). Drive exactly one parity gap end to end.

Claude-specific notes:
- You may delegate phases (plan, implement, docs) to subagents; keep the
  different-family review gate (`pi-review-loop`) as a separate fresh context.
- Honor the hard rules in the body: never self-grade, gates re-run after every
  fix, operator override is a stop — not a pass.
