# Pi/Pipy TUI Parity Verification

Date: 2026-05-27.

Prompt: `Reply exactly: Hello!`

Purpose: verify that a simple prompt renders the same in `pipy` and `pi`
through the live tmux TUI path, including submitted prompt placement,
assistant output placement, transient `Working...` placement, footer/status
placement, color replay, and screenshot output.

## Sessions

- pipy: `TERM=xterm-256color uv run pipy repl --native-provider openai-codex --native-model gpt-5.5`
- pi: `TERM=xterm-256color pi`
- tmux size: 100 columns by 30 rows
- cwd: `/Users/jochen/projects/pipy`

## Sampling

Sampler:

```sh
python3 /Users/jochen/projects/agent-stuff/codex/skills/tmux-transient-ui-verify/scripts/sample_tmux_frames.py \
  --out docs/audit/2026-05-27/reply-exactly-final-v2/sample \
  --target pipy=pipy-exact-final-v2 \
  --target pi=pi-exact-final-v2 \
  --send pipy='Reply exactly: Hello!' \
  --send pi='Reply exactly: Hello!' \
  --needle 'Reply exactly: Hello!' \
  --needle 'Working...' \
  --needle 'Hello!' \
  --needle '$0.000 (sub) 0.0%/272k' \
  --transient 'Working...' \
  --frames 100 \
  --interval 0.1
```

Result: `sample/anomalies.tsv` is header-only. No frame contained duplicate
`Working...` rows.

Inspected active frames:

- `sample/frames/pipy-01.log` and `sample/frames/pi-01.log`: submitted prompt
  on line 17, one `Working...` row on line 20, footer/status on lines 25-26.
- `sample/frames/pipy-17.log` and `sample/frames/pi-10.log`: assistant output
  `Hello!` on line 20, one `Working...` row on line 22 while streaming,
  footer/status on lines 27-28.
- `pipy-final.ansi` and `pi-final.ansi`: submitted prompt on line 17,
  assistant output `Hello!` on line 20, no stale `Working...`, footer/status
  on lines 25-26.

Screenshots:

- `screenshots/pipy-final.png`
- `screenshots/pi-final.png`

Known acceptable product-specific differences: product label/version,
global context path (`~/.pipy/AGENTS.md` vs `~/.pi/agent/AGENTS.md`), and token
cost accounting.
