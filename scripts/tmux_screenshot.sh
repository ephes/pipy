#!/usr/bin/env bash
# Render a tmux pane capture (with ANSI escapes) as a PNG.
#
# Pipeline: tmux capture-pane -e  →  aha (ANSI→HTML)  →  Chrome --headless (HTML→PNG)
# Usage:    scripts/tmux_screenshot.sh <session-name> <out.png>
#
# Requires Homebrew packages `aha` and a `Google Chrome.app` install on macOS.
# Strips wrapping, restores newlines, and pads tool-panel rows so the
# dark-green background renders to a usable width on the screenshot
# (Chrome cannot replay `\x1b[K` clear-to-EOL the way a real terminal
# does, so we fill rows in HTML directly to mirror the live look).

set -euo pipefail

SESSION="${1:-}"
OUTPUT="${2:-}"
if [[ -z "$SESSION" || -z "$OUTPUT" ]]; then
    echo "usage: $0 <session> <out.png>" >&2
    exit 2
fi

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT
ANSI="$TMP_DIR/pane.ansi"
HTML="$TMP_DIR/pane.html"
WRAPPED="$TMP_DIR/pane-wrapped.html"

tmux capture-pane -t "$SESSION" -p -e -S -200 > "$ANSI"
aha --black --no-header < "$ANSI" > "$HTML"

python3 - "$HTML" "$WRAPPED" <<'PY'
import re, sys
from pathlib import Path

body = Path(sys.argv[1]).read_text()
match = re.search(r"<body[^>]*>(?P<inner>.*?)</body>", body, re.DOTALL)
inner = match.group("inner") if match else body

# Restore the tool-panel "fill to end of row" effect that real
# terminals implement via `\x1b[K`. aha emits each colored span as
# its own element; we hand each green-backgrounded row a 100%-width
# block so the panel reads as a contiguous strip.
inner = re.sub(
    r"<span([^>]*background-color:[^>]*green[^>]*)>([^<]*)</span>",
    r'<span\1 style="display:inline-block;width:100%">\2</span>',
    inner,
)

html = f"""<!doctype html>
<html><head><meta charset="utf-8"><style>
body {{ margin: 0; padding: 12px; background: #0e1116;
        font-family: 'JetBrains Mono', 'Menlo', monospace;
        font-size: 14px; line-height: 1.45; color: #d6d6d6; }}
pre  {{ margin: 0; white-space: pre; }}
</style></head><body><pre>{inner}</pre></body></html>
"""
Path(sys.argv[2]).write_text(html)
PY

"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
    --headless --disable-gpu --no-sandbox \
    --window-size=2000,1200 \
    --screenshot="$OUTPUT" \
    "file://$WRAPPED" >/dev/null 2>&1

echo "wrote $OUTPUT"
