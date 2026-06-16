#!/usr/bin/env bash
# Live tmux verification of the example `answer` extension (port of answer.ts).
#
# Launches the deterministic answer-demo TUI (scripts/answer_demo.py, scripted
# provider — no network/auth) in a real tmux pane, drives the /answer flow with
# send-keys, and asserts via capture-pane that the Q&A overlay renders and the
# whole flow behaves like the TypeScript original:
#   1. a seed turn yields an assistant message that poses questions,
#   2. /answer extracts them and opens the bordered Q&A overlay (title,
#      progress dots, question + context, answer editor, footer),
#   3. typing an answer + Enter advances to the next question,
#   4. Enter on the last question shows the submit confirmation,
#   5. confirming submits the compiled Q/>/A answers and triggers a turn,
#   6. Esc on a fresh overlay cancels.
#
# It polls for each stable expected state (no fixed-sleep races) and never
# asserts transient notices. Usage: scripts/tmux_answer_verify.sh
# Exits 0 when every assertion passes, non-zero otherwise.

set -u
REPO="$(cd "$(dirname "$0")/.." && pwd)"
SESSION="pipy-answer-verify-$$"
WS="$(mktemp -d)/answer-demo"
FAILED=0

cleanup() {
  tmux kill-session -t "$SESSION" 2>/dev/null
  rm -rf "$(dirname "$WS")" 2>/dev/null
}
trap cleanup EXIT

# Poll the pane (with scrollback) until `needle` appears or `timeout` seconds
# elapse. Asserting a stable state this way tolerates a slow cold `uv run`
# startup and avoids racing transient output.
wait_for() {
  local label="$1" needle="$2" timeout="${3:-30}"
  local deadline=$(( SECONDS + timeout ))
  while [ "$SECONDS" -lt "$deadline" ]; do
    if tmux capture-pane -t "$SESSION" -p -S -200 | grep -qF -- "$needle"; then
      echo "PASS: $label"
      return 0
    fi
    sleep 0.3
  done
  echo "FAIL: $label (missing after ${timeout}s: $needle)"
  echo "----- pane -----"
  tmux capture-pane -t "$SESSION" -p -S -200
  echo "----------------"
  FAILED=1
  return 1
}

mkdir -p "$WS/.pipy/extensions"
cp "$REPO/docs/examples/extensions/answer.py" "$WS/.pipy/extensions/answer.py"

tmux new-session -d -s "$SESSION" -x 110 -y 36 \
  "cd \"$REPO\" && PIPY_CONFIG_HOME=\"$WS/cfg\" exec uv run python scripts/answer_demo.py \"$WS\""

# 0. Startup (cold `uv run` can be slow).
wait_for "startup TUI renders" "demo-model" 60

# 1. Seed turn -> assistant poses questions.
tmux send-keys -t "$SESSION" "set up a new project" C-m
wait_for "seed assistant message" "Which database?" 30

# 2. /answer -> extraction + Q&A overlay (assert the stable overlay, not the
#    transient "Extracting…" notice).
tmux send-keys -t "$SESSION" "/answer" C-m
wait_for "overlay title" "Questions (1/2)" 30
wait_for "overlay question" "Q: Which database?" 5
wait_for "overlay context" "We can configure MySQL or PostgreSQL." 5
wait_for "overlay footer" "Esc cancel" 5

# 3. Answer Q1 + advance.
tmux send-keys -t "$SESSION" "postgres"
tmux send-keys -t "$SESSION" C-m
wait_for "advanced to Q2" "Questions (2/2)" 15
wait_for "second question" "Q: TypeScript or JavaScript?" 5

# 4. Answer Q2 + confirm prompt.
tmux send-keys -t "$SESSION" "typescript"
tmux send-keys -t "$SESSION" C-m
wait_for "confirmation dialog" "Submit all answers?" 15

# 5. Confirm -> submit compiled answers -> turn.
tmux send-keys -t "$SESSION" C-m
wait_for "submitted answers block" "I answered your questions" 20
wait_for "compiled answer 1" "A: postgres" 5
wait_for "compiled answer 2" "A: typescript" 5
wait_for "answers triggered a turn" "recorded your answers" 20

# 6. Esc cancels a fresh overlay. Clear the scrollback first so detecting the
#    reopened overlay cannot match the earlier (now-submitted) one.
tmux clear-history -t "$SESSION"
tmux send-keys -t "$SESSION" "/answer" C-m
wait_for "overlay reopened" "Questions (1/2)" 20
tmux send-keys -t "$SESSION" Escape
wait_for "esc cancels" "Cancelled" 20

if [ "$FAILED" -eq 0 ]; then
  echo "ALL PASS"
else
  echo "FAILURES PRESENT"
fi
exit "$FAILED"
