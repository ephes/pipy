#!/usr/bin/env bash
# Sample a tmux-backed pipy REPL turn and write transient UI evidence.
#
# Usage:
#   scripts/tmux_transient_ui_verify.sh <out-dir> [prompt] [expected-output]
#
# The verifier intentionally samples active frames while generation is running,
# not just the final screen. It writes:
#   summary.tsv
#   frame-metrics.tsv
#   screen-metrics.jsonl
#   terminal-report.json
#   anomalies.tsv
#   raw-frames/frame-*.ansi
#   screenshots/final.png

set -euo pipefail

OUT_DIR="${1:-}"
PROMPT="${2:-hello world!}"
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

SESSION="pipy-tui-verify-$$"
PANE_COLUMNS=100
PANE_ROWS=30
SAMPLE_INTERVAL="${SAMPLE_INTERVAL:-0.1}"
MAX_SAMPLES="${MAX_SAMPLES:-450}"
RAW_DIR="$OUT_DIR/raw-frames"
SCREEN_DIR="$OUT_DIR/screenshots"
SUMMARY="$OUT_DIR/summary.tsv"
METRICS="$OUT_DIR/frame-metrics.tsv"
CURSOR_METRICS="$OUT_DIR/cursor-metrics.tsv"
ROW_DELTAS="$OUT_DIR/row-deltas.tsv"
ANOMALIES="$OUT_DIR/anomalies.tsv"
SCREEN_METRICS="$OUT_DIR/screen-metrics.jsonl"
SCREEN_REPORT="$OUT_DIR/terminal-report.json"
SCREEN_ANOMALIES="$OUT_DIR/screen-anomalies.tsv"

mkdir -p "$RAW_DIR" "$SCREEN_DIR"
rm -f "$SUMMARY" "$METRICS" "$CURSOR_METRICS" "$ROW_DELTAS" "$ANOMALIES" \
    "$SCREEN_METRICS" "$SCREEN_REPORT" "$SCREEN_ANOMALIES"
rm -f "$RAW_DIR"/frame-*.ansi "$SCREEN_DIR"/final.png
printf 'key\tvalue\n' > "$SUMMARY"
printf 'frame\tphase\tworking_count\tprompt_count\tassistant_nonempty\tfooter_count\tstatus_count\tdiagnostic_count\n' > "$METRICS"
printf 'frame\tphase\tcursor_x\tcursor_y\tpane_active\n' > "$CURSOR_METRICS"
printf 'frame\tphase\tmetric\tbaseline\tdelta\n' > "$ROW_DELTAS"
printf 'frame\tseverity\tmessage\n' > "$ANOMALIES"

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

    local plain
    plain="$(perl -pe 's/\e\[[0-9;?]*[ -\/]*[@-~]//g' "$file")"
    local working_count prompt_count assistant_nonempty footer_count status_count diagnostic_count
    working_count="$(printf '%s\n' "$plain" | grep -cF 'Working...' || true)"
    prompt_count="$(printf '%s\n' "$plain" | awk -v prompt="$PROMPT" '
        {
            line = $0
            sub(/^[[:space:]]+/, "", line)
            sub(/[[:space:]]+$/, "", line)
            if (line == prompt || line == ("user  " prompt)) {
                n++
            }
        }
        END { print n + 0 }
    ')"
    assistant_nonempty="$(printf '%s\n' "$plain" | awk -v prompt="$PROMPT" '
        BEGIN { n=0 }
        {
            trimmed = $0
            sub(/^[[:space:]]+/, "", trimmed)
            sub(/[[:space:]]+$/, "", trimmed)
        }
        /[[:alnum:]]/ &&
        trimmed != prompt &&
        trimmed != ("user  " prompt) &&
        $0 !~ /pipy v/ &&
        $0 !~ /escape interrupt/ &&
        $0 !~ /Press ctrl\+o/ &&
        $0 !~ /\[Context\]/ &&
        $0 !~ /AGENTS\.md/ &&
        $0 !~ /\[Skills\]/ &&
        $0 !~ /commit-/ &&
        $0 !~ /Working\.\.\./ &&
        $0 !~ /openai-codex/ &&
        $0 !~ /projects\/pipy/ { n++ }
        END { print n }
    ')"
    footer_count="$(printf '%s\n' "$plain" | grep -c '/pipy' || true)"
    status_count="$(printf '%s\n' "$plain" | grep -c 'openai-codex.*gpt-5.5' || true)"
    diagnostic_count="$(printf '%s\n' "$plain" | grep -c '^pipy  pipy:' || true)"
    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
        "$index" "$phase" "$working_count" "$prompt_count" "$assistant_nonempty" \
        "$footer_count" "$status_count" "$diagnostic_count" >> "$METRICS"

    local baseline_cursor_y baseline_prompt_count baseline_footer_count
    if [[ "$index" == "0" && "$phase" == "startup" ]]; then
        baseline_cursor_y="${cursor_y:-}"
        baseline_prompt_count="$prompt_count"
        baseline_footer_count="$footer_count"
    else
        baseline_cursor_y="$(awk -F'\t' 'NR==2 {print $4}' "$CURSOR_METRICS")"
        baseline_prompt_count="$(awk -F'\t' 'NR==2 {print $4}' "$METRICS")"
        baseline_footer_count="$(awk -F'\t' 'NR==2 {print $6}' "$METRICS")"
    fi
    if [[ -n "${cursor_y:-}" && -n "$baseline_cursor_y" ]]; then
        printf '%s\t%s\tcursor_y\t%s\t%s\n' \
            "$index" "$phase" "$baseline_cursor_y" \
            "$(( cursor_y - baseline_cursor_y ))" >> "$ROW_DELTAS"
    fi
    printf '%s\t%s\tprompt_count\t%s\t%s\n' \
        "$index" "$phase" "$baseline_prompt_count" \
        "$(( prompt_count - baseline_prompt_count ))" >> "$ROW_DELTAS"
    printf '%s\t%s\tfooter_count\t%s\t%s\n' \
        "$index" "$phase" "$baseline_footer_count" \
        "$(( footer_count - baseline_footer_count ))" >> "$ROW_DELTAS"

    if [[ "$phase" == active* ]]; then
        if (( working_count > 1 )); then
            printf '%s\terror\tduplicate Working lines: %s\n' "$index" "$working_count" >> "$ANOMALIES"
        fi
        if (( prompt_count > 1 )); then
            printf '%s\terror\tduplicate submitted prompt blocks: %s\n' "$index" "$prompt_count" >> "$ANOMALIES"
        fi
    fi
}

tmux new-session -d -s "$SESSION" -x "$PANE_COLUMNS" -y "$PANE_ROWS" -c "$PWD" \
    'uv run pipy repl --native-provider openai-codex --native-model gpt-5.5'

sleep 1.2
sample_frame 0 startup
tmux send-keys -t "$SESSION" "$PROMPT" Enter

settled="no"
final_index=0
for index in $(seq 1 "$MAX_SAMPLES"); do
    sleep "$SAMPLE_INTERVAL"
    sample_frame "$index" active
    final_index="$index"
    last_metrics="$(tail -n 1 "$METRICS")"
    working_count="$(printf '%s\n' "$last_metrics" | cut -f3)"
    prompt_count="$(printf '%s\n' "$last_metrics" | cut -f4)"
    assistant_nonempty="$(printf '%s\n' "$last_metrics" | cut -f5)"
    diagnostic_count="$(printf '%s\n' "$last_metrics" | cut -f8)"
    if (( working_count == 0 && prompt_count == 1 && (assistant_nonempty > 0 || diagnostic_count > 0) )); then
        settled="yes"
        break
    fi
done

sleep 0.5
sample_frame "$(( final_index + 1 ))" final

screen_args=(
    "$RAW_DIR"
    --cursor-metrics "$CURSOR_METRICS"
    --prompt "$PROMPT"
    --columns "$PANE_COLUMNS"
    --rows "$PANE_ROWS"
    --out-jsonl "$SCREEN_METRICS"
    --report "$SCREEN_REPORT"
    --anomalies "$SCREEN_ANOMALIES"
)
if [[ -n "$EXPECTED_OUTPUT" ]]; then
    screen_args+=(--expected-output "$EXPECTED_OUTPUT")
fi
uv run python -m pipy_harness.native.terminal_screen "${screen_args[@]}"
if [[ -s "$SCREEN_ANOMALIES" ]]; then
    awk -F'\t' 'NR > 1 {print $1 "\t" $2 "\t" "screen-cell: " $3}' \
        "$SCREEN_ANOMALIES" >> "$ANOMALIES"
fi

if command -v scripts/tmux_screenshot.sh >/dev/null 2>&1; then
    scripts/tmux_screenshot.sh "$SESSION" "$SCREEN_DIR/final.png" >/dev/null 2>&1 || true
elif [[ -x scripts/tmux_screenshot.sh ]]; then
    scripts/tmux_screenshot.sh "$SESSION" "$SCREEN_DIR/final.png" >/dev/null 2>&1 || true
fi

tmux send-keys -t "$SESSION" C-c
sleep 0.2

total_frames="$(find "$RAW_DIR" -type f -name 'frame-*.ansi' | wc -l | tr -d ' ')"
active_frames="$(find "$RAW_DIR" -type f -name '*-active.ansi' | wc -l | tr -d ' ')"
anomaly_count="$(( $(wc -l < "$ANOMALIES" | tr -d ' ') - 1 ))"
if [[ "$settled" != "yes" ]]; then
    printf '%s\terror\tno settled assistant or diagnostic frame observed\n' "$(( final_index + 1 ))" >> "$ANOMALIES"
    anomaly_count="$(( anomaly_count + 1 ))"
fi
printf 'session\t%s\n' "$SESSION" >> "$SUMMARY"
printf 'prompt\t%s\n' "$PROMPT" >> "$SUMMARY"
printf 'expected_output\t%s\n' "$EXPECTED_OUTPUT" >> "$SUMMARY"
printf 'command\tuv run pipy repl --native-provider openai-codex --native-model gpt-5.5\n' >> "$SUMMARY"
printf 'total_frames\t%s\n' "$total_frames" >> "$SUMMARY"
printf 'active_frames\t%s\n' "$active_frames" >> "$SUMMARY"
printf 'settled\t%s\n' "$settled" >> "$SUMMARY"
printf 'anomaly_count\t%s\n' "$anomaly_count" >> "$SUMMARY"
printf 'screen_metrics\t%s\n' "$SCREEN_METRICS" >> "$SUMMARY"
printf 'terminal_report\t%s\n' "$SCREEN_REPORT" >> "$SUMMARY"

echo "wrote $OUT_DIR"
