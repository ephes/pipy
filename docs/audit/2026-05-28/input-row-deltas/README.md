# Input Row Parity Follow-Up

Date: 2026-05-28.

Prompt: `hello`

Purpose: investigate the reported screenshot mismatch where pipy's submitted
prompt/input region appeared lower than Pi's.

## Why The Previous Check Missed It

The previous follow-up verified that pipy's live terminal cursor no longer
split from the drawn cursor cell and no longer moved within pipy after submit.
It did not assert cross-product row deltas for the submitted prompt, separators,
footer, or live cursor. That let a vertical placement difference be treated as
acceptable as long as pipy was internally stable.

The tmux transient UI skill now writes `row-deltas.tsv/json`, comparing the
first target against each later target for tracked needles and `cursor_y`.
For Pi-vs-pipy runs, include needles for the submitted prompt, separators, and
footer path so the row delta is visible instead of inferred from screenshots.

## Current Evidence

Run:

```sh
python3 /Users/jochen/projects/agent-stuff/codex/skills/tmux-transient-ui-verify/scripts/sample_tmux_frames.py \
  --out docs/audit/2026-05-28/input-row-deltas/sample \
  --target pipy=pipy-input-row-deltas \
  --target pi=pi-input-row-deltas \
  --send pipy='hello' \
  --send pi='hello' \
  --needle 'hello' \
  --needle 'Hello' \
  --needle 'Working...' \
  --needle '~/projects/pipy' \
  --needle '────────────────' \
  --transient 'Working...' \
  --frames 40 \
  --interval 0.1
```

Result:

- `sample/anomalies.tsv` is header-only.
- Submitted prompt row: pipy row 16, Pi row 16 from frame 0 onward.
- Live cursor row: pipy row 22, Pi row 22 in stable frames.
- Footer row: pipy row 24, Pi row 24 in stable frames.
- Brief nonzero deltas occur only while one side has a temporary extra
  streaming/working line and settle back to zero.

The reported screenshot mismatch is therefore explained by the earlier
verification gap plus stale/failing visual state. With the current TUI code and
the improved skill, the same-prompt tmux run does not reproduce a persistent
pipy-lower-than-Pi submitted prompt or input row.
