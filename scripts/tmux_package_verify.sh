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
#   3. proves via the package runtime APIs that the package theme is selectable,
#   4. asserts via capture-pane that:
#        - the startup `[Skills]` chrome lists the package skill `greet`,
#        - `/skill greet` and the package prompt command `/plan` reach the
#          provider turn,
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

# 2. Prove the package theme is contributed to the active theme registry. Theme
#    selection is now in /settings, not a /theme slash command.
if ! (cd "$REPO" && PIPY_CONFIG_HOME="$PIPY_CONFIG_HOME" WS="$WS" uv run python - <<'PY'
import os
from pathlib import Path

from pipy_harness.native import themes
from pipy_harness.native.package_runtime import compose_package_runtime
from pipy_harness.native.resources import WorkspaceResources
from pipy_harness.native.settings import SettingsManager

ws = Path(os.environ["WS"])
settings = SettingsManager.for_workspace(ws)
roots = compose_package_runtime(settings, ws)
resources = WorkspaceResources.discover(ws, package_roots=roots)
if "greet" not in resources.skill_names():
    raise SystemExit("package skill missing")
if "plan" not in resources.template_names():
    raise SystemExit("package prompt missing")
if not themes.is_known_theme("midnight"):
    raise SystemExit("package theme missing")
PY
); then
  echo "FAIL: package runtime contributed skill/prompt/theme"
  exit 1
fi
echo "PASS: package runtime contributed skill/prompt/theme"

# 3. Launch the demo session in tmux.
tmux new-session -d -s "$SESSION" -x 110 -y 40 \
  "cd \"$REPO\" && PIPY_CONFIG_HOME=\"$PIPY_CONFIG_HOME\" exec uv run python scripts/package_demo.py \"$WS\""

# 4a. Startup chrome lists the package skill (cold `uv run` can be slow).
wait_for "startup [Skills] lists package skill" "greet" 60

# 4b. Skill + prompt template commands both reach the provider.
tmux send-keys -t "$SESSION" "/skill greet" C-m
wait_for "package skill reaches provider" "demo-pack provider turn 1 acknowledged." 20
tmux send-keys -t "$SESSION" "/plan" C-m
wait_for "package prompt reaches provider" "demo-pack provider turn 2 acknowledged." 20

# 4c. Package extension command runs.
tmux send-keys -t "$SESSION" "/demo-hello pipy" C-m
wait_for "package extension command runs" "from demo-pack" 20

tmux send-keys -t "$SESSION" "exit" C-m

if [ "$FAILED" -eq 0 ]; then
  echo "ALL PASS"
else
  echo "FAILURES PRESENT"
fi
exit "$FAILED"
