#!/usr/bin/env bash
# Verify real-TTY product input states that do not require a provider turn.
#
# Usage:
#   scripts/tmux_tui_input_verify.sh <out-dir>
#
# The script launches the product command:
#   uv run pipy repl --native-provider openai-codex --native-model gpt-5.5
# then captures startup, Escape-on-empty-input, slash-menu-open, slash-menu
# navigation, and Escape-to-close frames. It fails from parsed screen-cell
# metrics, not screenshots.

set -euo pipefail

OUT_DIR="${1:-}"
if [[ -z "$OUT_DIR" ]]; then
    echo "usage: $0 <out-dir>" >&2
    exit 2
fi

SESSION="pipy-tui-input-verify-$$"
PANE_COLUMNS="${PANE_COLUMNS:-100}"
PANE_ROWS="${PANE_ROWS:-30}"
RAW_DIR="$OUT_DIR/raw-frames"
SCREEN_DIR="$OUT_DIR/screenshots"
SUMMARY="$OUT_DIR/summary.tsv"
CURSOR_METRICS="$OUT_DIR/cursor-metrics.tsv"
SCREEN_METRICS="$OUT_DIR/screen-metrics.jsonl"
SCREEN_REPORT="$OUT_DIR/terminal-report.json"
ANOMALIES="$OUT_DIR/anomalies.tsv"

mkdir -p "$RAW_DIR" "$SCREEN_DIR"
rm -f "$SUMMARY" "$CURSOR_METRICS" "$SCREEN_METRICS" "$SCREEN_REPORT" "$ANOMALIES"
rm -f "$RAW_DIR"/frame-*.ansi "$SCREEN_DIR"/final.png
printf 'key\tvalue\n' > "$SUMMARY"
printf 'frame\tphase\tcursor_x\tcursor_y\tpane_active\n' > "$CURSOR_METRICS"

cleanup() {
    tmux kill-session -t "$SESSION" >/dev/null 2>&1 || true
}
trap cleanup EXIT

sample_frame() {
    local index="$1"
    local phase="$2"
    local file="$RAW_DIR/frame-$(printf '%03d' "$index")-${phase}.ansi"
    tmux capture-pane -t "$SESSION" -p -e -J > "$file"
    local cursor_x cursor_y pane_active
    IFS=$'\t' read -r cursor_x cursor_y pane_active < <(
        tmux display-message -p -t "$SESSION" \
            '#{cursor_x}	#{cursor_y}	#{pane_active}'
    )
    printf '%s\t%s\t%s\t%s\t%s\n' \
        "$index" "$phase" "${cursor_x:-}" "${cursor_y:-}" "${pane_active:-}" \
        >> "$CURSOR_METRICS"
}

tmux new-session -d -s "$SESSION" -x "$PANE_COLUMNS" -y "$PANE_ROWS" -c "$PWD" \
    'uv run pipy repl --native-provider openai-codex --native-model gpt-5.5'

sleep 1.2
sample_frame 0 startup
tmux send-keys -t "$SESSION" Escape
sleep 0.2
sample_frame 1 escape-empty
tmux send-keys -t "$SESSION" /
sleep 0.2
sample_frame 2 slash-open
tmux send-keys -t "$SESSION" Down
sleep 0.2
sample_frame 3 slash-down
tmux send-keys -t "$SESSION" Escape
sleep 0.2
sample_frame 4 slash-closed

uv run python -m pipy_harness.native.terminal_screen \
    "$RAW_DIR" \
    --cursor-metrics "$CURSOR_METRICS" \
    --columns "$PANE_COLUMNS" \
    --rows "$PANE_ROWS" \
    --out-jsonl "$SCREEN_METRICS" \
    --report "$SCREEN_REPORT" \
    --anomalies "$ANOMALIES"

uv run python - "$SCREEN_METRICS" "$ANOMALIES" <<'PY'
from __future__ import annotations

import json
import sys
from pathlib import Path

metrics_path = Path(sys.argv[1])
anomalies_path = Path(sys.argv[2])
records = [json.loads(line) for line in metrics_path.read_text(encoding="utf-8").splitlines() if line.strip()]
by_phase = {record["phase"]: record for record in records}
errors: list[str] = []

def viewport(record: dict) -> str:
    return "\n".join(str(row) for row in record.get("viewport", []))

def regions(record: dict, key: str) -> list[dict]:
    value = record.get("visual_regions", {}).get(key, [])
    return value if isinstance(value, list) else []

for phase in ("startup", "escape-empty", "slash-open", "slash-down", "slash-closed"):
    if phase not in by_phase:
        errors.append(f"missing captured phase: {phase}")

if "escape-empty" in by_phase:
    record = by_phase["escape-empty"]
    if regions(record, "slash_menu") or regions(record, "slash_menu_selection"):
        errors.append("Escape on empty input opened or left a slash menu")
    if record.get("inferred_input_row") is None:
        errors.append("Escape on empty input lost the framed input row")

if "slash-open" in by_phase:
    record = by_phase["slash-open"]
    text = viewport(record)
    if "→ help" not in text or "  exit" not in text:
        errors.append("slash-open frame does not show the command list")
    if not regions(record, "slash_menu_selection"):
        errors.append("slash-open frame has no styled selected slash-menu row")
    if not regions(record, "slash_menu"):
        errors.append("slash-open frame has no non-selected slash-menu rows")
    if record.get("inferred_input_row") is None:
        errors.append("slash-open frame lost the framed input row")

if "slash-down" in by_phase:
    selected = regions(by_phase["slash-down"], "slash_menu_selection")
    if not selected or "→ exit" not in str(selected[0].get("text", "")):
        errors.append("Down arrow did not move slash-menu selection to /exit")

if "slash-closed" in by_phase:
    record = by_phase["slash-closed"]
    if regions(record, "slash_menu") or regions(record, "slash_menu_selection"):
        errors.append("Escape did not close the slash menu")
    input_row = record.get("inferred_input_row")
    if not isinstance(input_row, int):
        errors.append("slash-closed frame lost the framed input row")
    elif "/" not in str(record.get("viewport", [""])[input_row]):
        errors.append("Escape close did not preserve typed slash in input row")

if errors:
    with anomalies_path.open("a", encoding="utf-8") as handle:
        for error in errors:
            handle.write(f"input\tERROR\t{error}\n")
    for error in errors:
        print(error, file=sys.stderr)
    raise SystemExit(1)
PY

scripts/tmux_screenshot.sh "$SESSION" "$SCREEN_DIR/final.png" >/dev/null 2>&1 || true
tmux send-keys -t "$SESSION" C-c
sleep 0.2

anomaly_count="$(( $(wc -l < "$ANOMALIES" | tr -d ' ') - 1 ))"
printf 'session\t%s\n' "$SESSION" >> "$SUMMARY"
printf 'pane_columns\t%s\n' "$PANE_COLUMNS" >> "$SUMMARY"
printf 'pane_rows\t%s\n' "$PANE_ROWS" >> "$SUMMARY"
printf 'command\tuv run pipy repl --native-provider openai-codex --native-model gpt-5.5\n' >> "$SUMMARY"
printf 'screen_metrics\t%s\n' "$SCREEN_METRICS" >> "$SUMMARY"
printf 'terminal_report\t%s\n' "$SCREEN_REPORT" >> "$SUMMARY"
printf 'anomaly_count\t%s\n' "$anomaly_count" >> "$SUMMARY"

echo "wrote $OUT_DIR"
