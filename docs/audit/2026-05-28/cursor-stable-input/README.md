# Cursor Verification Follow-Up

Date: 2026-05-28.

Prompt: `Reply exactly: Hello!`

Purpose: verify the active-pane cursor behavior that the previous
Pi/pipy TUI parity run missed.

## Why The Previous Check Missed It

The old tmux sampler used `capture-pane` text/ANSI output and row summaries.
That sees the drawn reverse-video cursor cell, but it does not report tmux's
live terminal cursor position. In pipy, those were different: the drawn input
cell was on the input row, while the real cursor was placed on the footer/status
row. The second cursor only becomes visible when the pane is active, so the
HTML/ANSI screenshot replay also did not prove the live cursor location.

The tmux transient UI skill now writes `cursor-metrics.tsv/json` from:

```sh
tmux display-message -p -t <target> '#{cursor_x}\t#{cursor_y}\t#{pane_active}'
```

The repo-local verifier helper also records cursor metrics.

## Baseline Failure

Baseline evidence is in `../cursor-baseline/`.

- startup: pipy live cursor was row 19, while Pi was row 22
- after submit: pipy live cursor was row 25 while the drawn input row was 22/23
- this mismatch explains the active-pane second blinking cursor and the cursor
  appearing to move down into the footer/status area

## Fixed Run

Sessions:

- pipy: `TERM=xterm-256color uv run pipy repl --native-provider openai-codex --native-model gpt-5.5`
- pi: `TERM=xterm-256color pi`
- tmux size: 100 columns by 30 rows
- cwd: `/Users/jochen/projects/pipy`

Sampler:

```sh
python3 /Users/jochen/projects/agent-stuff/codex/skills/tmux-transient-ui-verify/scripts/sample_tmux_frames.py \
  --out docs/audit/2026-05-28/cursor-stable-input/sample \
  --target pipy=pipy-cursor-stable-input \
  --target pi=pi-cursor-stable-input \
  --send pipy='Reply exactly: Hello!' \
  --send pi='Reply exactly: Hello!' \
  --needle 'Reply exactly: Hello!' \
  --needle 'Working...' \
  --needle 'Hello!' \
  --needle '$0.000 (sub) 0.0%/272k' \
  --transient 'Working...' \
  --frames 50 \
  --interval 0.1
```

Result:

- `sample/anomalies.tsv` is header-only
- pipy startup cursor row: 22
- pipy sampled cursor row after submit: 22 in selected frames 0, 1, 10, 20,
  30, 40, and 49
- pipy frame rows show the input row at line 23, so tmux zero-based cursor row
  22 is now on the drawn input cell
- final screenshot: `screenshots/pipy-final.png`

Pi reported an update notice during this run, so Pi rows include that extra
notice block and are not used as exact row-for-row visual parity evidence here.
