# Parity Runner (unattended)

`scripts/parity_runner.py` runs a bounded batch of parity-loop gaps unattended,
fresh context per gap, with hard caps and a lesson gate. It never pushes;
commits stay local on `main` for review. See the design at
`docs/superpowers/specs/2026-06-22-parity-runner-design.md`.

## Run it

    just parity-run
    just parity-run my-label
    just parity-run-codex-dry
    just parity-run-codex
    just parity-run-codex-report
    just parity-run-claude
    just parity-run-claude-report
    just parity-improve-pipy-dry
    just parity-report-last
    just parity-report parity-20260623T102740Z
    uv run python scripts/parity_runner.py --agent codex --max-gaps 1
    uv run python scripts/parity_runner.py --report-slice
    uv run python scripts/parity_runner.py --max-gaps 2 --time-budget 3600

`--agent codex` uses `codex exec --dangerously-bypass-approvals-and-sandbox`.
`--agent claude` uses
`claude -p --model opus --dangerously-skip-permissions`. The default `opus`
adapter invokes the fish `claude-yolo` function with
`fish -lc 'set args $argv; ...; claude-yolo -p --model opus -- $args'`.

For every spawned gap or lesson-improve child, the runner appends the prompt
after an explicit `--` delimiter and closes child stdin with `/dev/null`. This
prevents noninteractive Claude Code from waiting on an inherited terminal and
keeps the prompt out of adapter option parsing. For the fish-backed `opus`
adapter, the wrapper strips the runner-level delimiter if present, then inserts
a fresh `--` at the `claude-yolo` command boundary. The related `--tools ""`
variadic parsing hazard applies to read-only Claude review commands and is
covered in the parity-loop CLI hygiene notes.

The normal `just parity-run`, `just parity-run-codex`, and
`just parity-run-claude` recipes write a slice report after a clean run. The
`just parity-run-codex-dry` recipe validates startup preconditions without
spawning a gap. The Codex and Claude recipes run one gap with the conservative
one-hour budget used for manual unattended batches; Claude uses Claude Code's
unattended permission bypass adapter. The `*-report` recipe names are retained
as explicit aliases for the same report-writing behavior.
`just parity-report-last` refreshes the latest completed run report, and
`just parity-report <label>` refreshes a named run.

`just parity-improve-pipy-dry` is the current pipy-native dogfood health check:
it asks pipy itself to run the `parity-improve` skill and stop without inventing
work when there are no safely applicable open lessons. This is intentionally
improve-only, not a full unattended `parity-run-pipy`; it exercises skill
advertisement, skill-body loading through the `read` tool, repo checks, and the
lesson ledger before pipy-native is trusted with full parity-loop slices.

## Run artifacts

Each run writes `run.jsonl` plus child logs under `docs/parity-loop/runs/run-<label>/`
by default. `run.started` records the starting `HEAD` and remote-tracking refs,
and `run.finished` records the same pre-run remote-tracking refs alongside the
post-run remote-tracking refs and a `remote_tracking_changed` boolean. This makes
the no-push audit explicit even if a child process changed `origin/main` through
fetching or other ref updates.

Successful `gap.completed` events record the child-reported commit plus
`head_before` and `head_after` for that gap. Older run logs may only contain the
commit field; report generation still works from `run.started.head_before` and
the last recorded gap commit.

When the lesson safety net spawns `parity-improve`, child output stays in
`improve-<phase>.log`. If that log contains warning, skipped, incomplete, blocked,
or failed-work caveats, the runner also emits a `safety_net_child_caveats` event
with the phase, log filename, and bounded caveat lines so post-loop caveats are
visible from `run.jsonl` without opening the child log first.

## Slice reports

`--report-slice [label]` creates or refreshes a Markdown report under
`docs/parity-loop/reports/`. Without a label it uses the newest
`docs/parity-loop/runs/run-*` directory. Report generation requires a
`run.finished` event with exit code `0`; failed or incomplete runs stay in the
raw run artifacts until a human decides what to do with them.

The generated report deliberately separates facts from interpretation. The
runner updates only the block between:

    <!-- BEGIN GENERATED:facts -->
    <!-- END GENERATED:facts -->

That block contains the run label, agent, stop reason, pinned recorded commit
range, commit subjects, changed-file stats, and recorded caveats. For current
logs the range is `run.started.head_before..last gap.completed.head_after`; for
older logs without `head_after`, it falls back to the last recorded
`gap.completed.sha`. It is not live `HEAD`, so refreshing an old report does not
accidentally absorb later commits. It is also not an authoritative semantic
commit list: review fixes or manual follow-ups may land after `run.finished`,
and an already-dirty commit range would still need human curation.

Everything outside the generated block is for the person or agent explaining the
slice: `What Changed`, `Visualization`, `Boundaries`, and optional
`Comprehension Check`. Add slice-specific diagrams and questions only when they
clarify the actual behavior shipped. Generic runner diagrams and trivia-style
quizzes are noise; prefer questions that test the reader's understanding of the
new behavior and the remaining parity boundary.

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
