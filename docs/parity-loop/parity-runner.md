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
    just parity-run-pipy-dry
    just parity-run-pipy
    just parity-run-pipy-report
    just parity-improve-pipy-dry
    just parity-report-last
    just parity-report parity-20260623T102740Z
    uv run python scripts/parity_runner.py --agent codex --max-gaps 1
    uv run python scripts/parity_runner.py --report-slice --curate-report
    uv run python scripts/parity_runner.py --max-gaps 2 --time-budget 3600

The child parity-loop skill honors reviewer-selection environment variables for
the plan and code review gates:

    time env REVIEWER_AGENT=pi just parity-run-pipy
    time env REVIEWER_AGENT=opus just parity-run-pipy
    time env REVIEWER_AGENT=pi REVIEWER_MODEL=openai-codex/gpt-5.5 just parity-run-pipy

`REVIEWER_AGENT=pi` selects the Pi review harness and `REVIEWER_AGENT=opus`
selects the Opus review harness. The parity-loop hard rule still applies: the
selected reviewer must be a different model family from the implementer. If the
runner exits `3` during preflight, it stopped on open lessons requiring human
sign-off before any gap or reviewer was selected.

`--agent codex` uses `codex exec --dangerously-bypass-approvals-and-sandbox`.
`--agent claude` uses
`claude -p --model opus --dangerously-skip-permissions`. The default `opus`
adapter invokes the fish `claude-yolo` function with
`fish -lc 'set args $argv; ...; claude-yolo -p --model opus -- $args'`.
`--agent pipy` uses `uv run pipy --tool-budget 200 -p`, so the runner exercises
the repo-local pipy-native one-shot product path as a first-class parity-loop
agent with the current maximum per-turn tool budget. The larger budget is
intentional for parity-loop dogfooding: one slice includes planning, review,
implementation, docs, checks, and review follow-up, and the default interactive
budget of 50 has repeatedly stopped useful runs after partial progress.

For every spawned gap or lesson-improve child, the runner appends the prompt
after an explicit `--` delimiter and closes child stdin with `/dev/null`. This
prevents noninteractive Claude Code from waiting on an inherited terminal and
keeps the prompt out of adapter option parsing. For the fish-backed `opus`
adapter, the wrapper strips the runner-level delimiter if present, then inserts
a fresh `--` at the `claude-yolo` command boundary. The related `--tools ""`
variadic parsing hazard applies to read-only Claude review commands and is
covered in the parity-loop CLI hygiene notes.

The normal `just parity-run`, `just parity-run-codex`, `just parity-run-claude`,
and `just parity-run-pipy` recipes write and curate a slice report after a clean
run. The `*-dry` recipes validate startup preconditions without spawning a gap.
The Codex, Claude, and pipy single-gap recipes give the child gap an explicit
one-hour `--per-gap-timeout` inside a 70-minute runner budget, leaving a small
post-gap margin for runner bookkeeping and lesson-gate checks. Claude uses
Claude Code's unattended permission bypass adapter, while pipy dogfoods the
native one-shot product path. The `*-report` recipes do not start a new run;
they refresh the latest slice report, or a named run's report when passed a
label. These report recipes pass `--curate-report`, so an agent replaces the
generated-facts-only scaffold with a human-readable explanation; the
agent-specific recipes pass their matching `--agent`, while the generic report
recipes use the CLI default unless `--agent` is supplied directly.
`just parity-report-last` refreshes the latest completed run report, and
`just parity-report <label>` refreshes a named run.

`just parity-improve-pipy-dry` is the current pipy-native dogfood health check:
it asks pipy itself to run the `parity-improve` skill and stop without checks or
invented work when there are no safely applicable open lessons. This remains a
focused improve-path smoke test alongside the full `parity-run-pipy` agent path;
it exercises skill advertisement, skill-body loading through the `read` tool,
repo checks when work exists, and the lesson ledger.

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

When the lesson safety net has open lessons and enough budget to spawn
`parity-improve`, child output stays in `improve-<phase>.log`. After that child
returns and the runner verifies the repo and lesson ledger state, the runner
records a structured `safety_net_completed` event with the phase, log filename,
exit code, before/after HEADs, open-lesson counts, and any commits created by the
safety-net child. If the child has caveats that should be promoted into the
report, it must write explicit `Caveat: ...`, `Blocked: ...`, `Failed: ...`, or
`Incomplete: ...` lines. Those bounded explicit lines become
`safety_net_child_caveats` events. Generic stderr, tool diagnostics, test output,
or prompt text is intentionally not keyword-scanned into caveats.

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
range, commit subjects, changed-file stats, safety-net improvement commits, and
recorded caveats. For current logs the main parity range is
`first gap.completed.head_before..last gap.completed.head_after`; for older logs
without `head_after`, it falls back to the last recorded `gap.completed.sha`, and
without a first-gap `head_before` it falls back to `run.started.head_before`. It
is not live `HEAD`, so refreshing an old report does not accidentally absorb
later commits. Preflight and post-loop lesson commits are shown separately from
the main parity range through the safety-net table when the run log contains
structured safety-net events. The recorded range is also not an authoritative
semantic commit list: review fixes or manual follow-ups may land after
`run.finished`, and an already-dirty commit range would still need human
curation.

Everything outside the generated block is for the person or agent explaining the
slice: `What Changed`, `Visualization`, `Boundaries`, and optional
`Comprehension Check`. New reports mark `What Changed` as generated-facts-only
until that paragraph is replaced. Passing `--curate-report` asks the selected
agent to edit only the report, preserve the generated facts block byte-for-byte,
and replace the placeholder with semantic slice notes. The runner fails curation
if the agent exits nonzero, changes the generated facts block, or leaves the
generated-facts-only marker in place. Add slice-specific diagrams and questions
only when they clarify the actual behavior shipped. Generic runner diagrams and
trivia-style quizzes are noise; prefer questions that test the reader's
understanding of the new behavior and the remaining parity boundary.

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
