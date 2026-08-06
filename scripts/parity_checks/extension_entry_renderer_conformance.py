"""Durable extension entry-renderer conformance gate.

Proves the Pi-shaped full-entry/context dispatch, omission semantics, active-
branch redraw metadata, and retained product-TUI expansion rerender. The
captured/headless persistence boundary is covered by
``extension_dispatch_conformance.py``.

Run: uv run python scripts/parity_checks/extension_entry_renderer_conformance.py --json
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from io import StringIO
from pathlib import Path
from typing import TextIO, cast

from pipy_harness.extensions import lines_component
from pipy_harness.native.extensions.contracts import (
    RegisteredEntryRenderer,
)
from pipy_harness.native.extensions.custom_payloads import (
    _custom_entry_redraw_rows,
    render_extension_entry,
)
from pipy_harness.native.session_tree import CustomEntry
from pipy_harness.native.tui import TerminalUi


@dataclass
class Check:
    name: str
    passed: bool
    detail: str


class _Theme:
    def fg(self, color, text):
        return f"\x1b[1m{text}\x1b[0m"


class _Tty:
    def write(self, text):
        return len(text)

    def flush(self):
        return None

    def isatty(self):
        return True


def _payload() -> dict[str, object]:
    return {
        "type": "custom",
        "id": "entry-1",
        "parentId": "parent-1",
        "timestamp": "2026-07-17T09:00:00+00:00",
        "customType": "card",
        "data": {"title": "CARD"},
    }


def run_checks(tmp_path: Path) -> list[Check]:
    checks: list[Check] = []
    seen: list[dict[str, object]] = []

    def render(entry, ctx):
        seen.append(dict(entry))
        return lines_component(
            [ctx.theme.fg("accent", f"{entry['id']}:{ctx.expanded}:{ctx.width}")]
        )

    renderers = {"card": RegisteredEntryRenderer("card", render, "gate")}
    payload = _payload()
    rendered = render_extension_entry(
        renderers, payload, width=64, expanded=True, theme=_Theme()
    )
    checks.append(
        Check(
            "full_entry_context",
            rendered is not None
            and rendered.styled
            and rendered.lines == ("\x1b[1mentry-1:True:64\x1b[0m",)
            and seen == [payload],
            "full stored entry and current expanded/width/theme reach component",
        )
    )
    checks.append(
        Check(
            "omission_semantics",
            render_extension_entry({}, payload) is None
            and render_extension_entry(
                {
                    "card": RegisteredEntryRenderer(
                        "card", lambda entry, ctx: None, "gate"
                    )
                },
                payload,
            )
            is None,
            "missing renderer and None component omit the live row",
        )
    )

    stored = CustomEntry(
        "entry-1",
        "parent-1",
        "2026-07-17T09:00:00+00:00",
        "card",
        {"title": "CARD"},
    )
    rows = _custom_entry_redraw_rows(
        (stored,),
        lambda entry: render_extension_entry(renderers, _payload(), theme=_Theme()),
        entry_render_metadata=renderers,
    )
    checks.append(
        Check(
            "active_branch_row",
            len(rows) == 1
            and rows[0][0] == "entry"
            and rows[0][1] == "card"
            and rows[0][3] == payload
            and rows[0][4] is renderers,
            "active-branch redraw retains full entry and entry registry",
        )
    )

    ui = TerminalUi(
        input_stream=cast(TextIO, StringIO()),
        terminal_stream=cast(TextIO, _Tty()),
        cwd=tmp_path,
    )
    first = render_extension_entry(renderers, payload, expanded=False, theme=_Theme())
    assert first is not None
    ui._transcript.add_entry_renderer_component(
        first.lines,
        custom_type="card",
        entry=payload,
        renderers=renderers,
    )
    ui.set_tools_expanded(True)
    block_text = "\n".join(
        line for _kind, lines in ui._transcript.custom_entry_blocks() for line in lines
    )
    checks.append(
        Check(
            "retained_expansion_rerender",
            "entry-1:True" in block_text and "entry-1:False" not in block_text,
            "retained TUI component rerenders with the current expanded flag",
        )
    )
    return checks


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    import tempfile

    with tempfile.TemporaryDirectory(prefix="pipy-entry-renderer-") as raw:
        checks = run_checks(Path(raw))
    passed = all(check.passed for check in checks)
    if args.json:
        print(
            json.dumps(
                {
                    "status": "pass" if passed else "fail",
                    "checks": [check.__dict__ for check in checks],
                },
                indent=2,
            )
        )
    else:
        for check in checks:
            print(f"{'PASS' if check.passed else 'FAIL'} {check.name}: {check.detail}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
