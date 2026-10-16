"""``api.register_tool_renderer`` (MCP parity plan S35, row EXT-15).

A resolver chooses the renderers of the calls to any tool name, also of a
tool that is not registered. Pi ``core/extensions/loader.ts``
``registerToolRenderer``, ``runner.ts`` ``resolveToolRenderers`` and the cases
"resolves tool renderers in extension load order, each able to defer to the
next" (``extensions-runner.test.ts``) and "renders calls to MCP tools that
are not registered" (``10285-mcp-tool-renderers.test.ts``).
"""

from __future__ import annotations

import dataclasses
import io
from pathlib import Path
from typing import Any

import pytest
from test_native_extension_activation import (
    _activate,
    _by_name,
    _make_workspace,
    _write_single_file,
)
from test_native_extension_tool_metadata import _result
from test_native_mcp_session import _extension, _Provider, _run
from test_native_session_history_render import _assistant, _box, _call, _Terminal
from test_native_session_history_render import _result as _stored_result
from test_native_tool_rows import _Live, _plain

from pipy_harness.extensions import (
    ExtensionTool,
    ToolRenderContext,
    ToolRenderers,
    lines_component,
)
from pipy_harness.models import HarnessStatus
from pipy_harness.native.agent import AgentToolCall, ProductContent
from pipy_harness.native.extensions.collectors import extension_tool_renderers
from pipy_harness.native.models import ProviderToolCall
from pipy_harness.native.tool_renderer_lookup import (
    BuiltinToolRows,
    McpToolRows,
    ToolRendererLookup,
)
from pipy_harness.native.tool_renderers import _ToolLoopRenderer


def _renderers(marker: str) -> ToolRenderers:
    return ToolRenderers(
        render_call=lambda ctx: lines_component(
            [f"{marker} call {ctx.tool_name} {dict(ctx.args)}"]
        ),
        render_result=lambda ctx: lines_component(
            [f"{marker} result {ctx.content} error={ctx.is_error}"]
        ),
    )


def _tool(name: str) -> ExtensionTool:
    return ExtensionTool(
        name=name,
        description="d",
        input_schema={"type": "object"},
        handler=lambda ctx, params: "",
        render_call=lambda ctx: lines_component([f"OWN {name}"]),
    )


# -- the chain (Pi ExtensionRunner.resolveToolRenderers) -------------------------


def test_resolvers_run_in_order_and_each_can_defer_to_the_next() -> None:
    # Pi: "resolves tool renderers in extension load order, each able to
    # defer to the next".
    first, second = _renderers("first"), _renderers("second")
    resolvers = (
        lambda name, following: first if name == "a" else following(),
        lambda name, following: following() or second,
    )
    own = _tool("b")

    assert ToolRendererLookup(resolvers=resolvers).resolve("a") is first
    assert ToolRendererLookup(resolvers=resolvers).resolve("b") is second
    assert ToolRendererLookup(tools={"b": own}, resolvers=resolvers).resolve("b") is own


def test_a_resolver_that_only_fills_in_keeps_pipys_own_rows() -> None:
    # Pi 10285: "Registered tools keep their own renderers", and the MCP
    # extension's resolver is the last one, so `next()` has its renderers.
    mine = _renderers("mine")
    seen: list[tuple[str, object | None]] = []

    def fill_in(name: str, following: Any) -> object | None:
        seen.append((name, following()))
        return following() or mine

    lookup = ToolRendererLookup(
        mcp_labels={"mcp__my_docs__search": "my-docs/search"}, resolvers=(fill_in,)
    )

    assert lookup.resolve("mcp__my_docs__search") == McpToolRows("my-docs/search")
    assert lookup.resolve("mcp__my_docs__gone") == McpToolRows("my_docs/gone")
    assert lookup.resolve("read", builtin=True) == BuiltinToolRows()
    assert lookup.resolve("not_registered") is mine
    assert [name for name, _ in seen] == [
        "mcp__my_docs__search",
        "mcp__my_docs__gone",
        "read",
        "not_registered",
    ]
    # What `next()` hands out has the two renderers, which can be called
    # (goal-check findings E6, M23; see the wrapping tests).
    for _name, following in seen[:3]:
        assert following is not None
        assert callable(getattr(following, "render_call", None))
        assert callable(getattr(following, "render_result", None))


def test_a_resolver_can_replace_any_tools_rows() -> None:
    mine = _renderers("mine")
    lookup = ToolRendererLookup(
        tools={"kv": _tool("kv")}, resolvers=(lambda name, following: mine,)
    )

    for name, builtin in (("kv", False), ("mcp__docs__search", False), ("ls", True)):
        assert lookup.resolve(name, builtin=builtin) is mine


def test_next_is_resolved_once_per_resolver() -> None:
    calls: list[str] = []

    def last(name: str, following: Any) -> object | None:
        calls.append(name)
        return following()

    def first(name: str, following: Any) -> object | None:
        return following() or following()

    assert ToolRendererLookup(resolvers=(first, last)).resolve("x") is None
    assert calls == ["x"]


def _raises(name: str, following: Any) -> object | None:
    raise RuntimeError("broken resolver")


@pytest.mark.parametrize(
    "bad",
    [
        _raises,
        lambda name, following: "not renderers",
        lambda name, following: {"render_call": None},
        # Both attributes, but one is neither a callable nor None.
        lambda name, following: ToolRenderers(render_call="x"),  # type: ignore[arg-type]
        lambda name: None,
    ],
)
def test_a_bad_resolver_is_left_out(bad: Any) -> None:
    mine = _renderers("mine")
    lookup = ToolRendererLookup(
        tools={"kv": (own := _tool("kv"))},
        resolvers=(bad, lambda name, following: following() or mine),
    )

    assert lookup.resolve("kv") is own
    assert lookup.resolve("ghost") is mine
    assert lookup.resolve("mcp__docs__search") == McpToolRows("docs/search")


class _RaisingAttribute:
    render_result = None

    @property
    def render_call(self) -> object:
        raise RuntimeError("broken attribute")


def test_renderers_whose_attributes_raise_leave_the_resolver_out() -> None:
    lookup = ToolRendererLookup(
        tools={"kv": (own := _tool("kv"))},
        resolvers=(lambda name, following: _RaisingAttribute(),),
    )

    assert lookup.resolve("kv") is own
    assert lookup.resolve("read", builtin=True) == BuiltinToolRows()


def test_a_resolver_may_return_renderers_without_callables() -> None:
    # Pi: a resolver's `{}` is drawn with the generic rows and stops the
    # resolvers after it.
    empty = ToolRenderers()
    lookup = ToolRendererLookup(resolvers=(lambda name, following: empty,))

    assert lookup.resolve("mcp__docs__search") is empty


# -- registration -------------------------------------------------------------


def test_activate_registers_tool_renderer_resolvers_in_order(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    for name in ("a_first", "b_second"):
        _write_single_file(
            workspace,
            name,
            "from pipy_harness.extensions import ToolRenderers\n"
            "def activate(api):\n"
            f"    api.register_tool_renderer(lambda name, nxt: '{name}-1')\n"
            f"    api.register_tool_renderer(lambda name, nxt: '{name}-2')\n",
        )
    _write_single_file(workspace, "c_disabled", "def activate(api):\n    raise 1\n")

    activated = _activate(workspace)

    assert len(_by_name(activated, "a_first").tool_renderers) == 2
    resolvers = extension_tool_renderers(activated)
    assert [resolver("x", lambda: None) for resolver in resolvers] == [
        "a_first-1",
        "a_first-2",
        "b_second-1",
        "b_second-2",
    ]


@pytest.mark.parametrize("resolver", ["None", "'read'", "42"])
def test_a_resolver_that_is_not_callable_disables_the_extension(
    tmp_path: Path, resolver: str
) -> None:
    workspace = _make_workspace(tmp_path)
    _write_single_file(
        workspace,
        "badresolver",
        "def activate(api):\n"
        "    try:\n"
        f"        api.register_tool_renderer({resolver})\n"
        "    except Exception:\n"
        "        pass\n"
        "    api.register_tool_renderer(lambda name, nxt: None)\n",
    )

    activated = _by_name(_activate(workspace), "badresolver")

    assert activated.status == "disabled"
    assert activated.reason == "invalid_tool_renderer"
    assert activated.tool_renderers == ()
    assert extension_tool_renderers([activated]) == ()


def test_a_resolver_cannot_be_registered_after_activation(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    _write_single_file(
        workspace,
        "late",
        "from pipy_harness.extensions import ExtensionCapabilityError\n"
        "def activate(api):\n"
        "    def late(ctx, args):\n"
        "        try:\n"
        "            api.register_tool_renderer(lambda name, nxt: None)\n"
        "        except ExtensionCapabilityError:\n"
        "            return 'refused'\n"
        "    api.register_command('late', 'd', late)\n",
    )

    activated = _by_name(_activate(workspace), "late")

    assert activated.commands[0].handler(None, "") == "refused"
    assert activated.tool_renderers == ()


# -- rows ---------------------------------------------------------------------


def test_a_resolver_draws_the_calls_of_a_tool_that_is_not_registered(
    tmp_path: Path,
) -> None:
    contexts: list[ToolRenderContext] = []

    def render_call(ctx: ToolRenderContext) -> object:
        contexts.append(ctx)
        return lines_component([f"GHOST {dict(ctx.args)} {ctx.expanded}"])

    def resolve(name: str, following: Any) -> object | None:
        if name != "ghost":
            return following()
        return ToolRenderers(
            render_call=render_call,
            render_result=lambda ctx: lines_component([f"BOO {ctx.content}"]),
        )

    live = _Live(tmp_path)
    live.width = 100
    live.renderer.refresh_tool_renderers(ToolRendererLookup(resolvers=(resolve,)))
    live.call("ghost", {"n": 1})
    live.renderer.render_tool_result(output_text="unknown tool: ghost", is_error=True)
    live.call("other", {"n": 1})
    live.renderer.render_tool_result(output_text="plain", is_error=False)

    assert live.rows() == [
        ["GHOST {'n': 1} False", "BOO unknown tool: ghost"],
        ["other n=1", "plain"],
    ]
    assert contexts[0].tool_name == "ghost" and contexts[0].width == 98
    # Ctrl+O draws the row again with the pinned renderers.
    live.renderer.refresh_tool_renderers(ToolRendererLookup())
    live.transcript.set_tools_expanded(True)
    assert live.rows()[0] == ["GHOST {'n': 1} True", "BOO unknown tool: ghost"]


def test_a_resolver_can_draw_part_of_a_built_in_tools_row(tmp_path: Path) -> None:
    only_call = ToolRenderers(render_call=lambda ctx: lines_component(["MY LS"]))
    live = _Live(tmp_path)
    live.renderer.refresh_tool_renderers(
        ToolRendererLookup(
            resolvers=(
                lambda name, following: only_call if name == "ls" else following(),
            )
        )
    )
    live.call("ls", {"path": "src"})
    live.renderer.render_tool_result(output_text="a.py", is_error=False)
    live.call("find", {"pattern": "*.py"})
    live.renderer.render_tool_result(output_text="a.py", is_error=False)

    # The part without a renderer is the generic one, as in Pi.
    assert live.rows() == [["MY LS", "a.py"], ["find *.py in .", "a.py"]]


def test_a_resolver_that_chooses_nothing_for_a_built_in_tool_gets_the_generic_row(
    tmp_path: Path,
) -> None:
    # Pi: a resolver that returns undefined without `next()` leaves the call
    # without renderers, a built-in tool's too.
    (tmp_path / "a.py").write_text("x = 1\n")
    chosen: list[ToolRenderers | None] = []

    def resolve(name: str, following: Any) -> object | None:
        return chosen[0]

    for choice in (None, ToolRenderers()):
        chosen[:] = [choice]
        live = _Live(tmp_path)
        live.width = 100
        live.renderer.refresh_tool_renderers(ToolRendererLookup(resolvers=(resolve,)))
        live.call("read", {"path": "a.py"})
        live.renderer.render_tool_result(output_text="x = 1", is_error=False)
        live.call("edit", {"path": "a.py", "edits": [{"oldText": "1", "newText": "2"}]})
        # No preview of the edit was started for a row that is not pipy's.
        assert live.renderer._preview_thread is None
        live.renderer.render_tool_result(output_text="edited", is_error=False)

        assert live.rows() == [
            ['read path="a.py"', "x = 1"],
            ['edit path="a.py" edits=[{"oldText":"1","newText":"2"}]', "edited"],
        ]


def test_a_resolver_that_returns_no_callables_gets_the_generic_row(
    tmp_path: Path,
) -> None:
    live = _Live(tmp_path)
    live.width = 100
    live.renderer.refresh_tool_renderers(
        ToolRendererLookup(resolvers=(lambda name, following: ToolRenderers(),))
    )
    live.call("mcp__docs__search", {"query": "pi"})
    live.renderer.render_tool_result(
        output_text="\n".join(f"hit {n}" for n in range(7)), is_error=False
    )

    assert live.rows() == [
        ['mcp__docs__search query="pi"', *[f"hit {n}" for n in range(7)]]
    ]


def test_the_line_renderer_asks_the_resolvers_too() -> None:
    stream = io.StringIO()
    renderer = _ToolLoopRenderer(output_stream=io.StringIO(), error_stream=stream)
    mine = _renderers("mine")
    renderer.refresh_tool_renderers(
        ToolRendererLookup(
            resolvers=(
                lambda name, following: mine if name == "ghost" else following(),
            )
        )
    )

    renderer.render_tool_call(AgentToolCall("c1", "ghost", ProductContent('{"n": 1}')))
    renderer.render_tool_result(output_text="boo", is_error=True)
    renderer.render_tool_call(AgentToolCall("c2", "ls", ProductContent("{}")))
    renderer.render_tool_result(output_text="a.py", is_error=False)

    lines = [line.strip() for line in stream.getvalue().splitlines() if line.strip()]
    assert lines == [
        "mine call ghost {'n': 1}",
        "mine result boo error=True",
        "ls .",
        "a.py",
    ]


def _line_rows(resolver: Any, calls: list[tuple[str, str, str, Any]]) -> list[str]:
    stream = io.StringIO()
    renderer = _ToolLoopRenderer(output_stream=io.StringIO(), error_stream=stream)
    renderer.refresh_tool_renderers(ToolRendererLookup(resolvers=(resolver,)))
    for index, (name, arguments, output, details) in enumerate(calls):
        renderer.render_tool_call(
            AgentToolCall(f"c{index}", name, ProductContent(arguments))
        )
        renderer.render_tool_result(output_text=output, is_error=False, details=details)
    return [line.strip() for line in stream.getvalue().splitlines() if line.strip()]


_WRITE = ("write", '{"path": "a.py", "content": "SECRET BODY"}', "wrote", None)
_EDIT = ("edit", '{"path": "a.py"}', "edited", {"diff": "-1 old\n+1 new"})
_LS = ("ls", '{"path": "src"}', "a.py", None)


def test_the_line_renderer_keeps_nothing_built_in_for_a_replaced_tool() -> None:
    result_only = ToolRenderers(render_result=lambda ctx: lines_component(["MINE"]))
    call_only = ToolRenderers(render_call=lambda ctx: lines_component(["MINE"]))

    # Untouched: pipy's header, and the write content and edit diff.
    assert _line_rows(lambda name, following: following(), [_LS, _WRITE, _EDIT]) == [
        "ls src",
        "a.py",
        "write a.py",
        "SECRET BODY wrote",
        "edit a.py",
        "-1 old",
        "+1 new edited",
    ]
    # Only the result is the resolver's: the header is the generic one.
    assert _line_rows(lambda name, following: result_only, [_LS]) == [
        'ls(path="src")',
        "MINE",
    ]
    # Only the call is the resolver's: no content or diff before the result.
    assert _line_rows(lambda name, following: call_only, [_WRITE, _EDIT]) == [
        "MINE",
        "wrote",
        "MINE",
        "edited",
    ]
    # The resolver chose nothing.
    assert _line_rows(lambda name, following: None, [_LS, _WRITE]) == [
        'ls(path="src")',
        "a.py",
        'write(path="a.py", content="SECRET BODY")',
        "wrote",
    ]


def test_a_redrawn_transcript_asks_the_published_resolvers(tmp_path: Path) -> None:
    """A resumed session is drawn before any provider turn: the stored calls
    get the renderers of the extensions that are loaded now."""

    terminal = _Terminal(tmp_path)
    mine = _renderers("mine")
    terminal.tool_resolvers.append(
        lambda name, following: mine if name == "ghost" else following()
    )
    terminal.tool_renderers["kv"] = _tool("kv")
    terminal.tree.append_message(
        _assistant(
            "",
            _call("c1", "ghost", {"n": 1}),
            _call("c2", "kv", {}),
            _call("c3", "mcp__docs__search", {}),
        )
    )
    terminal.tree.append_message(_stored_result("c1", "ghost", "boo"))
    terminal.history.render_active_branch()

    boxes = [_box(lines) for kind, lines in terminal.rows() if kind == "tool_box"]
    assert boxes == [
        [
            ("success", "mine call ghost {'n': 1}"),
            ("success", "mine result boo error=False"),
        ],
        [("pending", "OWN kv")],
        [("pending", "docs/search")],
    ]

    # A reload that removes the resolver changes the next redraw.
    terminal.tool_resolvers.clear()
    terminal.history.render_active_branch()
    boxes = [_box(lines) for kind, lines in terminal.rows() if kind == "tool_box"]
    assert boxes[0] == [("success", "ghost n=1"), ("success", "boo")]


# -- a session ----------------------------------------------------------------

_RESOLVER = """from pipy_harness.extensions import ToolRenderers, lines_component


def activate(api):
    mine = ToolRenderers(
        render_call=lambda ctx: lines_component(['GHOST CALL ' + ctx.tool_name]),
        render_result=lambda ctx: lines_component(['GHOST RESULT ' + ctx.content]))

    @api.register_tool_renderer
    def resolve(name, following):
        return following() or (mine if name.startswith('ghost') else None)
"""


def test_a_session_draws_calls_with_a_registered_resolver(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "config"))
    workspace = tmp_path / "work"
    _extension(workspace, "resolver", _RESOLVER)
    provider = _Provider(
        [
            _result(
                tool_calls=(
                    ProviderToolCall("c1", "ghost_tool", "{}"),
                    ProviderToolCall("c2", "mcp__docs__search", '{"q": 1}'),
                    ProviderToolCall("c3", "ls", '{"path": "."}'),
                )
            ),
            _result(final_text="done"),
        ]
    )

    status, errors = _run(workspace, provider)

    assert status is HarnessStatus.SUCCEEDED, errors
    lines = [line.strip() for line in errors.splitlines()]
    assert "GHOST CALL ghost_tool" in lines
    assert "GHOST RESULT unknown tool: ghost_tool" in lines
    # `following()` had pipy's own rows for these.
    assert "docs/search q=1" in lines
    assert "ls ." in lines


_LATE_RESOLVER = """from pipy_harness.extensions import ToolRenderers, lines_component


def activate(api):
    mine = ToolRenderers(
        render_call=lambda ctx: lines_component(['LATE CALL ' + ctx.tool_name]),
        render_result=lambda ctx: lines_component(['LATE RESULT ' + ctx.content]))

    def resolve(name, following):
        return following() or (mine if name.startswith('ghost') else None)

    @api.on('session_start')
    def register(event, ctx):
        # Pi allows `registerToolRenderer` after loading.
        api.register_tool_renderer(resolve)
"""


def test_a_resolver_registered_from_session_start_draws_later_calls(
    tmp_path: Path, monkeypatch
) -> None:
    # Goal check r2, S27: registering a resolver while the session runs the
    # extension neither raises nor disables it, and the resolver is asked.
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "config"))
    workspace = tmp_path / "work"
    _extension(workspace, "late", _LATE_RESOLVER)
    provider = _Provider(
        [
            _result(tool_calls=(ProviderToolCall("c1", "ghost_tool", "{}"),)),
            _result(final_text="done"),
        ]
    )

    status, errors = _run(workspace, provider)

    assert status is HarnessStatus.SUCCEEDED, errors
    lines = [line.strip() for line in errors.splitlines()]
    assert "LATE CALL ghost_tool" in lines
    assert "LATE RESULT unknown tool: ghost_tool" in lines


# -- wrapping what `next()` gives (goal-check findings E6, M23) ---------------


def _wrapping(name: str, following: Any) -> object | None:
    """Pi: a resolver calls `next()`'s renderers and adds a line."""

    inner = following()
    if inner is None:
        return None

    def wrap(render: Any, tag: str) -> Any:
        def draw(ctx: Any) -> object:
            component = render(ctx)
            lines = [] if component is None else list(component.render(ctx.width))
            return lines_component([f"[{tag}]", *lines])

        return draw

    return ToolRenderers(
        render_call=wrap(inner.render_call, "call"),
        render_result=wrap(inner.render_result, "result"),
    )


def test_a_resolver_wraps_a_built_in_tools_rows(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("x = 1\n")
    live = _Live(tmp_path)
    live.width = 100
    live.renderer.refresh_tool_renderers(ToolRendererLookup(resolvers=(_wrapping,)))

    live.call("ls", {"path": "src"})
    live.renderer.render_tool_result(output_text="a.py", is_error=False)
    live.call("edit", {"path": "a.py", "edits": [{"oldText": "1", "newText": "2"}]})
    live.renderer.render_tool_result(
        output_text="edited",
        is_error=False,
        details={"diff": "-1 x = 1\n+1 x = 2", "firstChangedLine": 1},
    )

    ls_row, edit_row = live.rows()
    assert ls_row == ["[call]", "ls src", "[result]", "a.py"]
    # The edit's box (title and diff) is its call; its result adds nothing.
    assert edit_row[:4] == ["[call]", "edit a.py", "-1 x = 1", "+1 x = 2"]
    assert edit_row[4:] == ["[result]"]


def test_a_wrapped_edit_row_shows_the_live_preview(tmp_path: Path) -> None:
    # Pi's edit renderer computes the preview for a wrapper that calls it.
    (tmp_path / "a.py").write_text("a = 1\n")
    live = _Live(tmp_path)
    live.width = 100
    live.renderer.refresh_tool_renderers(ToolRendererLookup(resolvers=(_wrapping,)))

    live.call(
        "edit", {"path": "a.py", "edits": [{"oldText": "a = 1", "newText": "a = 2"}]}
    )
    thread = live.renderer._preview_thread
    assert thread is not None
    thread.join(2)

    assert _plain(live.transcript.pending_tool_lines) == [
        "[call]",
        "edit a.py",
        "-1 a = 1",
        "+1 a = 2",
    ]


def test_a_resolver_wraps_an_mcp_tools_rows(tmp_path: Path) -> None:
    live = _Live(tmp_path)
    live.width = 100
    live.renderer.refresh_tool_renderers(
        ToolRendererLookup(
            mcp_labels={"mcp__docs__search": "docs/search"}, resolvers=(_wrapping,)
        )
    )

    live.call("mcp__docs__search", {"query": "pi"})
    live.renderer.render_tool_result(
        output_text="\n".join(f"hit {n}" for n in range(7)), is_error=False
    )

    (row,) = live.rows()
    assert row[:3] == ["[call]", 'docs/search query="pi"', "[result]"]
    # Pi's MCP renderer: the first five lines collapsed.
    assert row[3:8] == [f"hit {n}" for n in range(5)]
    assert "hit 6" not in row


def test_the_line_renderer_lets_a_resolver_wrap_its_own_rows() -> None:
    # The write content and the edit diff are part of the wrapped result.
    assert _line_rows(_wrapping, [_LS, _WRITE, _EDIT]) == [
        "[call]",
        "ls src",
        "[result]",
        "a.py",
        "[call]",
        "write a.py",
        "[result]",
        "SECRET BODY",
        "wrote",
        "[call]",
        "edit a.py",
        "[result]",
        "-1 old",
        "+1 new",
        "edited",
    ]


def test_own_rows_outside_a_row_draw_nothing() -> None:
    from pipy_harness.native.extension_types import ToolRenderContext

    ctx = ToolRenderContext("ls", {}, False, False, None, None, False, 40, None, {})

    assert BuiltinToolRows().render_call(ctx) is None
    assert McpToolRows("docs/search").render_result(ctx) is None


# -- codemode's own rows (goal check r4, D3; Pi codemodeRenderers) -------------

_SCRIPT = "Script completed\nWall time 0.4 seconds\nOutput:\n"


def test_next_gives_codemodes_own_rows() -> None:
    seen: list[object | None] = []

    def fill_in(name: str, following: Any) -> object | None:
        seen.append(following())
        return following()

    lookup = ToolRendererLookup(resolvers=(fill_in,))

    assert lookup.resolve("codemode", builtin=True) == BuiltinToolRows()
    assert seen == [BuiltinToolRows()]
    # An extension that replaces `codemode` with renderers of its own keeps
    # them, before and after the resolvers.
    own = _tool("codemode")
    assert (
        ToolRendererLookup(tools={"codemode": own}).resolve("codemode", builtin=True)
        is own
    )
    assert (
        ToolRendererLookup(tools={"codemode": own}, resolvers=(fill_in,)).resolve(
            "codemode", builtin=True
        )
        is own
    )


def _rendererless(name: str) -> ExtensionTool:
    return ExtensionTool(
        name=name,
        description="d",
        input_schema={"type": "object"},
        handler=lambda ctx, params: "",
    )


def test_a_codemode_replacement_without_renderers_gets_the_generic_rows(
    tmp_path: Path,
) -> None:
    # Goal check r5, S1. Pi: `getRegisteredToolDefinition` resolves the
    # registered definition, which has no renderers, and only the built-in
    # codemode tool brings `codemodeRenderers`; `ToolExecutionComponent`
    # then draws the generic rows, partial results included.
    from pipy_harness.native.extension_types import RegisteredTool
    from pipy_harness.native.extensions.live_tools import (
        LiveToolRegistry,
        LiveToolRenderers,
    )

    replacement = _rendererless("codemode")
    seen: list[object | None] = []

    def fill_in(name: str, following: Any) -> object | None:
        seen.append(following())
        return following() or _renderers("mine")

    lookup = ToolRendererLookup(
        tools=LiveToolRenderers(
            LiveToolRegistry([RegisteredTool(replacement, "mine")])
        ),
        resolvers=(fill_in,),
    )

    # `next()` is the registered tool, so `next() or mine` keeps it, as in Pi.
    assert lookup.resolve("codemode", builtin=True) is replacement
    assert seen == [replacement]

    for tools, partial_shown in (({}, False), (lookup.tools, True)):
        live = _Live(tmp_path)
        live.width = 100
        live.renderer.refresh_tool_renderers(ToolRendererLookup(tools=tools))
        live.call("codemode", {"code": "print(1)"})
        live.transcript.append_tool_output("step 1\n")
        # The built-in codemode's rows show no partial result; the generic
        # rows of its replacement do.
        assert ("step 1" in _plain(live.transcript.pending_tool_lines)) is (
            partial_shown
        )
        live.renderer.render_tool_result(output_text=_SCRIPT + "1", is_error=False)
        (row,) = live.rows()
        assert row[0] == 'codemode code="print(1)"'
        if partial_shown:
            # The generic result: the text as it is, header included.
            assert row[1:] == [
                "Script completed",
                "Wall time 0.4 seconds",
                "Output:",
                "1",
            ]
        else:
            assert row[1:] == ["1"]


def test_the_line_renderer_draws_a_rendererless_codemode_replacement_generically() -> (
    None
):
    stream = io.StringIO()
    renderer = _ToolLoopRenderer(output_stream=io.StringIO(), error_stream=stream)
    renderer.refresh_tool_renderers(
        ToolRendererLookup(tools={"codemode": _rendererless("codemode")})
    )

    renderer.render_tool_call(
        AgentToolCall("c1", "codemode", ProductContent('{"code": "x"}'))
    )
    renderer.render_tool_result(output_text=_SCRIPT + "done", is_error=False)

    lines = [line.strip() for line in stream.getvalue().splitlines() if line.strip()]
    assert lines[0] == 'codemode(code="x")'
    assert "Script completed" in lines and "done" in lines


def test_a_resolver_wraps_codemodes_rows(tmp_path: Path) -> None:
    live = _Live(tmp_path)
    live.width = 100
    live.renderer.refresh_tool_renderers(ToolRendererLookup(resolvers=(_wrapping,)))

    live.call("codemode", {"code": "print(1)"})
    live.renderer.render_tool_result(output_text=_SCRIPT + "1\n2\n", is_error=False)

    # codemode's result rows: the output without the script header.
    assert live.rows() == [["[call]", 'codemode code="print(1)"', "[result]", "1", "2"]]


def test_a_resolver_that_chooses_nothing_for_codemode_gets_the_generic_row(
    tmp_path: Path,
) -> None:
    live = _Live(tmp_path)
    live.width = 100
    live.renderer.refresh_tool_renderers(
        ToolRendererLookup(resolvers=(lambda name, following: None,))
    )

    live.call("codemode", {"code": "print(1)"})
    live.renderer.render_tool_result(output_text=_SCRIPT + "1", is_error=False)

    # Pi's generic result: the text as it is, header included.
    (row,) = live.rows()
    assert row[0] == 'codemode code="print(1)"'
    assert row[1:] == ["Script completed", "Wall time 0.4 seconds", "Output:", "1"]


def test_the_line_renderer_lets_a_resolver_wrap_codemodes_rows() -> None:
    stream = io.StringIO()
    renderer = _ToolLoopRenderer(output_stream=io.StringIO(), error_stream=stream)
    renderer.refresh_tool_renderers(ToolRendererLookup(resolvers=(_wrapping,)))

    renderer.render_tool_call(
        AgentToolCall("c1", "codemode", ProductContent('{"code": "x"}'))
    )
    renderer.render_tool_result(output_text="done", is_error=False)

    lines = [line.strip() for line in stream.getvalue().splitlines() if line.strip()]
    assert lines[:2] == ["[call]", 'codemode(code="x")']
    assert "[result]" in lines and "done" in lines


# -- what a wrapper passes is drawn (goal check r6, D1) --------------------------


def _passing(**changes: Any) -> Any:
    """A resolver that calls `next()`'s renderers with a changed context, as
    a Pi wrapper calls the MCP renderers with other arguments or result."""

    def resolve(name: str, following: Any) -> object | None:
        inner = following()
        if inner is None:
            return None

        def call(ctx: ToolRenderContext) -> object:
            return inner.render_call(dataclasses.replace(ctx, **changes))

        def result(ctx: ToolRenderContext) -> object:
            return inner.render_result(dataclasses.replace(ctx, **changes))

        return ToolRenderers(render_call=call, render_result=result)

    return resolve


def test_a_wrapper_redacts_an_mcp_rows_arguments_and_result(tmp_path: Path) -> None:
    live = _Live(tmp_path)
    live.width = 100
    live.renderer.refresh_tool_renderers(
        ToolRendererLookup(
            mcp_labels={"mcp__docs__search": "docs/search"},
            resolvers=(_passing(args={"query": "REDACTED"}, content="REDACTED"),),
        )
    )

    live.call("mcp__docs__search", {"query": "secret question"})
    live.renderer.render_tool_result(output_text="secret answer", is_error=False)

    (row,) = live.rows()
    assert row == ['docs/search query="REDACTED"', "REDACTED"]
    assert "secret" not in "\n".join(row)


def test_a_wrapper_chooses_the_expansion_of_own_rows(tmp_path: Path) -> None:
    hits = "\n".join(f"hit {n}" for n in range(7))
    for changes, shown in (({}, 5), ({"expanded": True}, 7)):
        live = _Live(tmp_path)
        live.width = 100
        live.renderer.refresh_tool_renderers(
            ToolRendererLookup(
                mcp_labels={"mcp__docs__search": "docs/search"},
                resolvers=(_passing(**changes),),
            )
        )

        live.call("mcp__docs__search", {"query": "pi"})
        live.renderer.render_tool_result(output_text=hits, is_error=False)

        # The rows are collapsed; a wrapper that passes `expanded=True`
        # gets every line, as Pi's MCP renderer reads `options.expanded`.
        assert live.transcript.tools_expanded is False
        (row,) = live.rows()
        assert [line for line in row if line.startswith("hit")] == [
            f"hit {n}" for n in range(shown)
        ]


def test_a_wrapper_changes_a_built_in_tools_rows(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("a = 1\n")
    live = _Live(tmp_path)
    live.width = 100
    live.renderer.refresh_tool_renderers(
        ToolRendererLookup(
            resolvers=(_passing(args={"path": "elsewhere"}, content="b.py"),)
        )
    )

    live.call("ls", {"path": "src"})
    live.renderer.render_tool_result(output_text="a.py", is_error=False)
    assert live.rows() == [["ls elsewhere", "b.py"]]

    # The edit preview was computed from the call's own arguments, so it is
    # not shown under others.
    live.call(
        "edit", {"path": "a.py", "edits": [{"oldText": "a = 1", "newText": "a = 2"}]}
    )
    thread = live.renderer._preview_thread
    assert thread is not None
    thread.join(2)
    assert _plain(live.transcript.pending_tool_lines) == ["edit elsewhere"]


def test_a_wrapper_redacts_codemodes_rows(tmp_path: Path) -> None:
    live = _Live(tmp_path)
    live.width = 100
    live.renderer.refresh_tool_renderers(
        ToolRendererLookup(
            resolvers=(
                _passing(args={"code": "REDACTED"}, content=_SCRIPT + "REDACTED"),
            )
        )
    )

    live.call("codemode", {"code": "secret()"})
    live.renderer.render_tool_result(output_text=_SCRIPT + "secret", is_error=False)

    assert live.rows() == [['codemode code="REDACTED"', "REDACTED"]]


def test_the_line_renderer_draws_what_a_wrapper_passes() -> None:
    passing = _passing(
        args={"path": "b.py", "content": "OTHER BODY"},
        content="redacted",
        is_error=True,
    )

    assert _line_rows(passing, [_WRITE]) == [
        "write b.py",
        "redacted",
        "[error] tool reported a failure",
    ]
    assert _line_rows(
        _passing(args={"path": "b.py", "content": "OTHER BODY"}), [_WRITE]
    ) == ["write b.py", "OTHER BODY", "wrote"]
    # A withheld result is no output, not the call's own.
    assert _line_rows(_passing(content=None), [_LS]) == ["ls src"]
    # A partial codemode result has no output, as Pi's codemode renderer.
    codemode = ("codemode", '{"code": "x"}', "secret", None)
    assert _line_rows(_passing(is_partial=True), [codemode]) == ['codemode(code="x")']


# -- codemode's nested calls belong to its result (goal check r6, D2) ------------


def _nested_codemode(live: _Live, *, settle: bool) -> None:
    """A live codemode call whose script made one call to `read`."""

    from test_native_nested_lifecycle import _child, _end

    from pipy_harness.native.agent import NestedToolCallStarted

    live.call("codemode", {"code": "x"})
    child = _child("c-codemode", args='{"path": "nested.txt"}')
    live.renderer.render_nested_tool_call(NestedToolCallStarted(0, "c-codemode", child))
    live.renderer.render_nested_tool_call(_end(child))
    if settle:
        live.renderer.render_tool_result(output_text=_SCRIPT + "1", is_error=False)


def test_renderers_that_replace_codemodes_draw_no_nested_calls(
    tmp_path: Path,
) -> None:
    # Pi draws the nested calls in `codemodeRenderers.renderResult` only;
    # the row itself has none.
    for resolver in (
        lambda name, following: _renderers("mine"),
        lambda name, following: None,
    ):
        live = _Live(tmp_path)
        live.width = 100
        live.renderer.refresh_tool_renderers(ToolRendererLookup(resolvers=(resolver,)))
        _nested_codemode(live, settle=False)
        assert "nested.txt" not in "\n".join(_plain(live.transcript.pending_tool_lines))
        live.renderer.render_tool_result(output_text=_SCRIPT + "1", is_error=False)
        (row,) = live.rows()
        assert "nested.txt" not in "\n".join(row)


def test_codemodes_own_rows_draw_the_nested_calls_in_their_result(
    tmp_path: Path,
) -> None:
    for resolvers, header in (((), []), ((_wrapping,), ["[call]"])):
        live = _Live(tmp_path)
        live.width = 100
        live.renderer.refresh_tool_renderers(ToolRendererLookup(resolvers=resolvers))
        _nested_codemode(live, settle=False)
        # While the script runs, the result part is the nested calls (Pi's
        # codemode tool sends them as partial results).
        nested = '✓ read {"path": "nested.txt"} 125ms'
        result = ["[result]"] if resolvers else []
        assert _plain(live.transcript.pending_tool_lines) == [
            *header,
            'codemode code="x"',
            *result,
            nested,
        ]
        live.renderer.render_tool_result(output_text=_SCRIPT + "1", is_error=False)
        assert live.rows() == [[*header, 'codemode code="x"', *result, nested, "1"]]


def test_the_line_renderer_writes_nested_calls_only_for_codemodes_own_rows() -> None:
    from test_native_nested_lifecycle import _child, _end

    from pipy_harness.native.agent import NestedToolCallStarted

    details = {
        "nestedCalls": {
            "calls": [
                {
                    "id": "c0/1",
                    "name": "read",
                    "status": "ok",
                    "durationMs": 125,
                    "arguments": {"path": "nested.txt"},
                }
            ],
            "complete": True,
        }
    }
    nested = '✓ read {"path":"nested.txt"} 125ms'

    def run(resolver: Any) -> list[str]:
        stream = io.StringIO()
        renderer = _ToolLoopRenderer(output_stream=io.StringIO(), error_stream=stream)
        renderer.refresh_tool_renderers(ToolRendererLookup(resolvers=(resolver,)))
        renderer.render_tool_call(
            AgentToolCall("c0", "codemode", ProductContent('{"code": "x"}'))
        )
        child = _child("c0", args='{"path":"nested.txt"}')
        renderer.render_nested_tool_call(NestedToolCallStarted(0, "c0", child))
        renderer.render_nested_tool_call(_end(child))
        renderer.render_tool_result(output_text="1", is_error=False, details=details)
        return [line.strip() for line in stream.getvalue().splitlines() if line.strip()]

    # Own rows write them as they run.
    assert nested in run(lambda name, following: following())
    # A wrapper gets them with the result, from its record.
    assert run(_wrapping) == ["[call]", 'codemode(code="x")', "[result]", nested, "1"]
    # Renderers that replace codemode's draw their own.
    assert run(lambda name, following: _renderers("mine")) == [
        "mine call codemode {'code': 'x'}",
        "mine result 1 error=False",
    ]
