"""Demo: persistent chrome widgets (slice B).

Copy to `<workspace>/.pipy/extensions/chrome-widgets-demo.py`. On session start
it pins a header, an above-editor widget, and a footer showing the git branch,
and sets the terminal title.
"""
from __future__ import annotations

from pipy_harness.extensions import lines_component


def activate(api):
    @api.on("session_start")
    def _start(event, ctx):
        ctx.ui.set_title("pipy · chrome demo")
        ctx.ui.set_header(lambda theme: lines_component([theme.fg("accent", "── chrome demo ──")]))
        ctx.ui.set_widget("hint", ["tip: this widget sits just above the input"])
        ctx.ui.set_footer(
            lambda theme, fd: lines_component(
                [theme.dim(f"branch: {fd.git_branch or 'n/a'}")]
            )
        )
