"""Ownership characterization for the headless extension UI bridge.

Slice 6.4c relocated `_CollectingUi`, `_safe_ui_key`, and the
`coerce_tool_render_lines` / `_LinesComponent` / `lines_component` chrome
helpers into `pipy_harness.native.extension_ui`. `extension_runtime` and the
public `pipy_harness.extensions` surface re-export the same objects, and the
new module never reaches the concrete product session or terminal UI.
"""

from __future__ import annotations

import pipy_harness.native.extension_ui as extension_ui


def test_collecting_ui_owned_by_extension_ui() -> None:
    from pipy_harness.native.extension_runtime import _CollectingUi

    assert _CollectingUi is extension_ui._CollectingUi


def test_render_helpers_reexport_same_objects() -> None:
    from pipy_harness.extensions import coerce_tool_render_lines, lines_component
    from pipy_harness.native.extension_runtime import (
        coerce_tool_render_lines as rt_coerce,
    )
    from pipy_harness.native.extension_runtime import lines_component as rt_lines

    assert coerce_tool_render_lines is extension_ui.coerce_tool_render_lines
    assert rt_coerce is extension_ui.coerce_tool_render_lines
    assert lines_component is extension_ui.lines_component
    assert rt_lines is extension_ui.lines_component
