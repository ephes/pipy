# Pipy/Pi TUI Hello-World Comparison After CRLF Fix

Commands under test:

```sh
tmux new-session -d -s pipy-hello-crlf-check -x 100 -y 30 -c "$PWD" \
  'env TERM=xterm-256color uv run pipy repl --native-provider openai-codex --native-model gpt-5.5'
tmux new-session -d -s pi-hello-crlf-check -x 100 -y 30 -c "$PWD" \
  'env TERM=xterm-256color PI_OFFLINE=1 pi --no-session'
python3 /Users/jochen/projects/agent-stuff/codex/skills/tmux-transient-ui-verify/scripts/sample_tmux_frames.py \
  --out docs/audit/2026-05-27/compare-hello-crlf-final \
  --target pipy=pipy-hello-crlf-check \
  --target pi=pi-hello-crlf-check \
  --send pipy='hello world' \
  --send pi='hello world' \
  --needle 'hello world' \
  --needle 'Working...' \
  --needle 'Hello' \
  --needle 'pipy v' \
  --needle 'pi v' \
  --needle '$0.000 (sub) 0.0%/272k' \
  --transient 'Working...' \
  --frames 80 \
  --interval 0.1
```

Evidence:

- `raw-typing-after-crlf.ansi` captures a live prompt with only `hel` typed, without `capture-pane -J`; it confirms raw-mode repaints no longer drift horizontally.
- `anomalies.tsv` is header-only: no duplicate `Working...` transient rows.
- `summary.tsv` shows active-frame row parity for the core interaction: `hello world` at row 16 and `Working...` at row 19 for both `pipy` and `pi`.
- Settled frames show both CLIs rendering assistant text at row 19 with the submitted prompt still at row 16.
- Product-specific labels differ (`pipy` vs `pi`, `~/.pipy` vs `~/.pi/agent`), and provider token accounting can differ slightly between runs.
