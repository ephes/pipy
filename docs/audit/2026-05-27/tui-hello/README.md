# Tool-Loop TUI Hello Smoke

Command under test:

```sh
uv run pipy repl --native-provider openai-codex --native-model gpt-5.5
```

Prompt: `hello world!`

Verifier:

```sh
scripts/tmux_transient_ui_verify.sh docs/audit/2026-05-27/tui-hello 'hello world!'
```

Evidence:

- `summary.tsv`: 8 sampled frames, 6 active frames, settled `yes`, anomaly count `0`.
- `frame-metrics.tsv`: active frames 1-5 each have one submitted prompt block, one `Working...` line, stable footer/status, and no assistant text yet; active frame 6 and final frame have no `Working...` line and one assistant text region.
- `anomalies.tsv`: header only; no duplicate `Working...`, duplicate prompt, or unsettled-state anomalies.
- `raw-frames/`: ANSI tmux captures of startup, active, and final visible frames.
- `screenshots/final.png`: final screenshot rendered from the tmux pane.

This run exercises the real `openai-codex` provider path with the requested
`gpt-5.5` model. The active-frame evidence shows a single submitted
`hello world!` user-message block, a single live transient working indicator
during generation, stable footer/status, and a clean settled assistant response.
