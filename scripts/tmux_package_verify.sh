#!/usr/bin/env bash
# Live tmux verification of installed-package runtime composition.
#
# Proves that an installed local-path package contributes a working skill,
# prompt, theme, and extension to a real pipy session — the slice-12 closeout
# goal. The harness:
#   1. installs the example `demo-pack` package into a fresh workspace with the
#      real `pipy install <path> -l` CLI,
#   2. launches a real NativeToolReplSession (scripted provider — no
#      network/auth) in a tmux pane via scripts/package_demo.py,
#   3. asserts via capture-pane that:
#        - the startup `[Skills]` chrome lists the package skill `greet`,
#        - `/theme` lists the package theme `midnight` and `/theme midnight`
#          selects it,
#        - `/skill` lists `greet` and `/template` lists `plan`,
#        - the package extension command `/demo-hello` runs.
#
# It polls for each stable expected state (no fixed-sleep races). Usage:
# scripts/tmux_package_verify.sh — exits 0 when every assertion passes.

set -u
REPO="$(cd "$(dirname "$0")/.." && pwd)"
SESSION="pipy-package-verify-$$"
ROOT="$(mktemp -d)"
WS="$ROOT/ws"
PKG="$REPO/docs/examples/packages/demo-pack"
FAILED=0

cleanup() {
  tmux kill-session -t "$SESSION" 2>/dev/null
  rm -rf "$ROOT" 2>/dev/null
}
trap cleanup EXIT

wait_for() {
  local label="$1" needle="$2" timeout="${3:-30}"
  local deadline=$(( SECONDS + timeout ))
  while [ "$SECONDS" -lt "$deadline" ]; do
    if tmux capture-pane -t "$SESSION" -p -S -400 | grep -qF -- "$needle"; then
      echo "PASS: $label"
      return 0
    fi
    sleep 0.3
  done
  echo "FAIL: $label (missing after ${timeout}s: $needle)"
  echo "----- pane -----"
  tmux capture-pane -t "$SESSION" -p -S -400
  echo "----------------"
  FAILED=1
  return 1
}

mkdir -p "$WS/.pipy"
export PIPY_CONFIG_HOME="$ROOT/cfg"

# 1. Install the example package into the workspace (project scope) with the
#    real CLI.
if ! (cd "$REPO" && uv run pipy install "$PKG" -l --cwd "$WS" >/dev/null 2>&1); then
  echo "FAIL: pipy install demo-pack"
  exit 1
fi
echo "PASS: installed demo-pack into workspace"

# 2. Launch the demo session in tmux.
tmux new-session -d -s "$SESSION" -x 110 -y 40 \
  "cd \"$REPO\" && PIPY_CONFIG_HOME=\"$PIPY_CONFIG_HOME\" exec uv run python scripts/package_demo.py \"$WS\""

# 3a. Startup chrome lists the package skill (cold `uv run` can be slow).
wait_for "startup [Skills] lists package skill" "greet" 60

# 3b. Theme: list shows the package theme, then select it.
tmux send-keys -t "$SESSION" "/theme" C-m
wait_for "package theme listed" "midnight" 20
tmux send-keys -t "$SESSION" "/theme midnight" C-m
wait_for "package theme selected" "selected theme midnight" 20

# 3c. Skill + prompt listings include the package resources.
tmux send-keys -t "$SESSION" "/skill" C-m
wait_for "package skill listed" "greet:" 20
tmux send-keys -t "$SESSION" "/template" C-m
wait_for "package prompt listed" "plan:" 20

# 3d. Package extension command runs.
tmux send-keys -t "$SESSION" "/demo-hello pipy" C-m
wait_for "package extension command runs" "from demo-pack" 20

tmux send-keys -t "$SESSION" "exit" C-m

if [ "$FAILED" -eq 0 ]; then
  echo "ALL PASS"
else
  echo "FAILURES PRESENT"
fi
exit "$FAILED"
