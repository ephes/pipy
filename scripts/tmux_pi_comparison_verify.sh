#!/usr/bin/env bash
# Compare pipy and Pi TUI behavior through controlled tmux captures.
#
# Usage:
#   scripts/tmux_pi_comparison_verify.sh <out-dir> [prompt] [expected-output]
#
# The script writes pipy and pi subdirectories with raw frames, screenshots,
# screen-metrics.jsonl, and terminal reports, then writes comparison artifacts:
#   comparison/row-column-deltas.{json,tsv}
#   comparison/comparison-report.json
#   comparison/comparison-anomalies.tsv

set -euo pipefail

OUT_DIR="${1:-}"
PROMPT="${2:-Reply exactly: Hello!}"
EXPECTED_OUTPUT="${3:-}"
if [[ -z "$OUT_DIR" ]]; then
    echo "usage: $0 <out-dir> [prompt] [expected-output]" >&2
    exit 2
fi
if [[ -z "$EXPECTED_OUTPUT" && "$PROMPT" == "Reply exactly: "* ]]; then
    EXPECTED_OUTPUT="${PROMPT#Reply exactly: }"
fi
if [[ -z "$EXPECTED_OUTPUT" ]]; then
    echo "expected-output is required unless prompt starts with 'Reply exactly: '" >&2
    exit 2
fi

PANE_COLUMNS=100
PANE_ROWS=30
SAMPLE_INTERVAL="${SAMPLE_INTERVAL:-0.1}"
MAX_SAMPLES="${MAX_SAMPLES:-450}"
PI_COMMAND="${PI_COMMAND:-pi --provider openai-codex --model gpt-5.5}"
PIPY_DIR="$OUT_DIR/pipy"
PI_DIR="$OUT_DIR/pi"
COMPARE_DIR="$OUT_DIR/comparison"

mkdir -p "$OUT_DIR" "$COMPARE_DIR"
rm -rf "$PIPY_DIR" "$PI_DIR"
mkdir -p "$PI_DIR/raw-frames" "$PI_DIR/screenshots"

scripts/tmux_transient_ui_verify.sh "$PIPY_DIR" "$PROMPT" "$EXPECTED_OUTPUT"

SESSION="pi-tui-verify-$$"
SUMMARY="$PI_DIR/summary.tsv"
CURSOR_METRICS="$PI_DIR/cursor-metrics.tsv"
SCREEN_METRICS="$PI_DIR/screen-metrics.jsonl"
SCREEN_REPORT="$PI_DIR/terminal-report.json"
SCREEN_ANOMALIES="$PI_DIR/screen-anomalies.tsv"

cleanup() {
    tmux kill-session -t "$SESSION" >/dev/null 2>&1 || true
}
trap cleanup EXIT

printf 'key\tvalue\n' > "$SUMMARY"
printf 'frame\tphase\tcursor_x\tcursor_y\tpane_active\n' > "$CURSOR_METRICS"

sample_pi_frame() {
    local index="$1"
    local phase="$2"
    local file="$PI_DIR/raw-frames/frame-$(printf '%03d' "$index")-${phase}.ansi"
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
    "$PI_COMMAND"

sleep 1.2
sample_pi_frame 0 startup
tmux send-keys -t "$SESSION" "$PROMPT" Enter

settled="no"
final_index=0
for index in $(seq 1 "$MAX_SAMPLES"); do
    sleep "$SAMPLE_INTERVAL"
    sample_pi_frame "$index" active
    final_index="$index"
    plain="$(perl -pe 's/\e\[[0-9;?]*[ -\/]*[@-~]//g' "$PI_DIR/raw-frames/frame-$(printf '%03d' "$index")-active.ansi")"
    working_count="$(printf '%s\n' "$plain" | grep -cF 'Working...' || true)"
    prompt_count="$(printf '%s\n' "$plain" | awk -v prompt="$PROMPT" '
        {
            line = $0
            sub(/^[[:space:]]+/, "", line)
            sub(/[[:space:]]+$/, "", line)
            if (line == prompt || line == ("user  " prompt)) n++
        }
        END { print n + 0 }
    ')"
    output_count=0
    if [[ -n "$EXPECTED_OUTPUT" ]]; then
        output_count="$(printf '%s\n' "$plain" | awk -v output="$EXPECTED_OUTPUT" -v prompt="$PROMPT" '
            {
                line = $0
                sub(/^[[:space:]]+/, "", line)
                sub(/[[:space:]]+$/, "", line)
                if (index(line, output) && index(line, prompt) == 0) n++
            }
            END { print n + 0 }
        ')"
    fi
    if (( working_count == 0 && prompt_count == 1 && output_count > 0 )); then
        settled="yes"
        break
    fi
done

sleep 0.5
sample_pi_frame "$(( final_index + 1 ))" final

pi_screen_args=(
    "$PI_DIR/raw-frames"
    --cursor-metrics "$CURSOR_METRICS"
    --prompt "$PROMPT"
    --columns "$PANE_COLUMNS"
    --rows "$PANE_ROWS"
    --out-jsonl "$SCREEN_METRICS"
    --report "$SCREEN_REPORT"
    --anomalies "$SCREEN_ANOMALIES"
)
if [[ -n "$EXPECTED_OUTPUT" ]]; then
    pi_screen_args+=(--expected-output "$EXPECTED_OUTPUT")
fi
uv run python -m pipy_harness.native.terminal_screen "${pi_screen_args[@]}"
pi_anomaly_count="$(awk 'NR > 1 { n++ } END { print n + 0 }' "$SCREEN_ANOMALIES")"

scripts/tmux_screenshot.sh "$SESSION" "$PI_DIR/screenshots/final.png" >/dev/null 2>&1 || true
tmux send-keys -t "$SESSION" C-c
sleep 0.2

printf 'session\t%s\n' "$SESSION" >> "$SUMMARY"
printf 'prompt\t%s\n' "$PROMPT" >> "$SUMMARY"
printf 'expected_output\t%s\n' "$EXPECTED_OUTPUT" >> "$SUMMARY"
printf 'command\t%s\n' "$PI_COMMAND" >> "$SUMMARY"
printf 'settled\t%s\n' "$settled" >> "$SUMMARY"
printf 'anomaly_count\t%s\n' "$pi_anomaly_count" >> "$SUMMARY"
printf 'screen_metrics\t%s\n' "$SCREEN_METRICS" >> "$SUMMARY"
printf 'terminal_report\t%s\n' "$SCREEN_REPORT" >> "$SUMMARY"

uv run python -m pipy_harness.native.terminal_compare \
    --reference "$PIPY_DIR/screen-metrics.jsonl" \
    --target "$PI_DIR/screen-metrics.jsonl" \
    --reference-label pipy \
    --target-label pi \
    --out-json "$COMPARE_DIR/row-column-deltas.json" \
    --out-tsv "$COMPARE_DIR/row-column-deltas.tsv" \
    --anomalies "$COMPARE_DIR/comparison-anomalies.tsv" \
    --report "$COMPARE_DIR/comparison-report.json"

echo "wrote $OUT_DIR"
