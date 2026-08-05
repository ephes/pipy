from __future__ import annotations

import gc
import warnings

import pytest

from pipy_harness.extensions import lines_component
from pipy_harness.native.extensions.contracts import (
    RegisteredEntryRenderer,
)
from pipy_harness.native.extensions.custom_payloads import render_extension_entry


def _renderers(custom_type, fn):
    return {custom_type: RegisteredEntryRenderer(custom_type, fn, "ext")}


def _entry(data=None):
    return {
        "type": "custom",
        "id": "entry-1",
        "parentId": "parent-1",
        "timestamp": "2026-07-17T09:00:00+00:00",
        "customType": "card",
        "data": {"title": "original"} if data is None else data,
    }


def test_entry_renderer_receives_full_detached_entry_and_context():
    seen = {}

    def renderer(entry, ctx):
        seen["entry"] = entry
        seen["context"] = ctx
        entry["data"]["title"] = "mutated"
        return lines_component(
            [f"{entry['id']}:{ctx.expanded}:{ctx.width}:{ctx.theme is not None}"]
        )

    original = _entry()
    theme = object()
    rendered = render_extension_entry(
        _renderers("card", renderer),
        original,
        width=72,
        expanded=True,
        theme=theme,
    )

    assert rendered is not None
    assert rendered.styled is True
    assert rendered.lines == ("entry-1:True:72:True",)
    assert seen["entry"] == {
        **_entry(),
        "data": {"title": "mutated"},
    }
    assert seen["context"].expanded is True
    assert seen["context"].width == 72
    assert seen["context"].theme is theme
    assert original["data"] == {"title": "original"}


def test_entry_renderer_missing_none_bad_output_and_failures_are_omitted():
    assert render_extension_entry({}, _entry()) is None
    assert (
        render_extension_entry(_renderers("card", lambda entry, ctx: None), _entry())
        is None
    )
    assert (
        render_extension_entry(
            _renderers("card", lambda entry, ctx: "not a component"), _entry()
        )
        is None
    )

    def raises(entry, ctx):
        raise RuntimeError("secret entry data")

    assert render_extension_entry(_renderers("card", raises), _entry()) is None

    class BadComponent:
        def render(self, width):
            raise RuntimeError("secret render data")

    assert (
        render_extension_entry(
            _renderers("card", lambda entry, ctx: BadComponent()), _entry()
        )
        is None
    )


def test_entry_renderer_interrupts_propagate():
    def interrupted_renderer(entry, ctx):
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        render_extension_entry(_renderers("card", interrupted_renderer), _entry())

    class _ExitingComponent:
        def render(self, width):
            raise SystemExit(7)

    with pytest.raises(SystemExit, match="7"):
        render_extension_entry(
            _renderers("card", lambda entry, ctx: _ExitingComponent()),
            _entry(),
        )


def test_async_entry_renderer_is_closed_and_omitted():
    async def renderer(entry, ctx):
        return lines_component(["async"])

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        assert render_extension_entry(_renderers("card", renderer), _entry()) is None
        gc.collect()

    assert not [
        warning for warning in caught if "never awaited" in str(warning.message)
    ]
