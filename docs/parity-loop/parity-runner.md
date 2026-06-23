# Parity Runner (unattended)

`scripts/parity_runner.py` runs a bounded batch of parity-loop gaps unattended,
fresh context per gap, with hard caps and a lesson gate. It never pushes;
commits stay local on `main` for review. See the design at
`docs/superpowers/specs/2026-06-22-parity-runner-design.md`.

## Run it

    just parity-run
    just parity-run my-label
    uv run python scripts/parity_runner.py --agent codex --max-gaps 1
    uv run python scripts/parity_runner.py --max-gaps 2 --time-budget 3600

`--agent codex` uses `codex exec --dangerously-bypass-approvals-and-sandbox`.
`--agent claude` uses `claude -p --model opus`. The default `opus` adapter uses
`claude-yolo -p --model opus`.

## Exit codes

- `0` — clean stop, no open lessons remaining.
- `1` — something failed mid-run: a blocked or unverified gap, dirty tree,
  off-`main`, invalid ledger, or safety-net dirtied state.
- `2` — startup precondition failed, so nothing ran: dirty tree or off `main` at
  start, unsafe `--run-dir`, busy lock, duplicate label, or invalid label.
- `3` — work completed but open lessons still need human sign-off; drain them
  with `parity-improve`, then the next run can proceed.

## Schedule it

Example launchd plist, not auto-installed. Save as
`~/Library/LaunchAgents/de.wersdoerfer.parity-run.plist`, adjust paths, then load
it with `launchctl`.

    <?xml version="1.0" encoding="UTF-8"?>
    <!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
      "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
    <plist version="1.0"><dict>
      <key>Label</key><string>de.wersdoerfer.parity-run</string>
      <key>WorkingDirectory</key><string>/Users/jochen/projects/pipy</string>
      <key>ProgramArguments</key><array>
        <string>/bin/sh</string><string>-lc</string>
        <string>just parity-run "$(date -u +%Y-%m-%dT%H%M%SZ)"</string>
      </array>
      <key>StartCalendarInterval</key><dict>
        <key>Hour</key><integer>2</integer><key>Minute</key><integer>30</integer>
      </dict>
      <key>StandardOutPath</key><string>/tmp/parity-run.out.log</string>
      <key>StandardErrorPath</key><string>/tmp/parity-run.err.log</string>
    </dict></plist>

Prefer small nightly batches over long continuous runs.
