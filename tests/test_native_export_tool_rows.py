"""Tool rows in the HTML export (MCP parity plan S35, the export part of
RND-03).

Pi ``core/export-html/tool-renderer.ts`` and ``ansi-to-html.ts``, and the
regression case "renders them in HTML exports too"
(``10285-mcp-tool-renderers.test.ts``): a call to an MCP tool is drawn with
the MCP renderers in an export too, whether its tool is registered or not.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from test_native_export_distribution import _decode_html_payload, _run_commands

from pipy_harness.extensions import (
    ExtensionTool,
    ToolRenderContext,
    ToolRenderers,
    lines_component,
)
from pipy_harness.native.agent import (
    AgentAssistantMessage,
    AgentToolCall,
    AgentToolResultMessage,
    AgentUserMessage,
    ProductContent,
)
from pipy_harness.native.chrome import ChromeStyle
from pipy_harness.native.export_distribution import (
    export_from_file,
    export_native_session_to_html,
    redact_export_value,
    session_export_payload,
)
from pipy_harness.native.export_tool_rows import (
    ansi_lines_to_html,
    export_chrome_style,
    render_export_tools,
)
from pipy_harness.native.session_tree import NativeSessionTree
from pipy_harness.native.tool_renderer_lookup import ToolRendererLookup

_TAG = re.compile(r"<[^>]+>")
_PLAIN = ChromeStyle(enabled=False)


def _text(fragment: str) -> list[str]:
    """The lines of a rendered fragment, without markup."""

    lines = re.findall(r'<div class="ansi-line">(.*?)</div>', fragment)
    return [
        _TAG.sub("", line)
        .replace("&nbsp;", "")
        .replace("&quot;", '"')
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&amp;", "&")
        for line in lines
    ]


def _call(corr: str, name: str, args: str) -> AgentToolCall:
    return AgentToolCall(corr, name, ProductContent(args))


def _result(
    corr: str, name: str, text: str, *, is_error: bool = False, details: Any = None
) -> AgentToolResultMessage:
    return AgentToolResultMessage(
        tool_request_id=f"pipy-tool-{corr}",
        tool_name=name,
        content=ProductContent(text),
        provider_correlation_id=corr,
        is_error=is_error,
        details=details,
    )


def _tree(tmp_path: Path, *messages: Any) -> NativeSessionTree:
    cwd = tmp_path / "workspace"
    cwd.mkdir(parents=True, exist_ok=True)
    tree = NativeSessionTree.create(cwd, session_dir=tmp_path / "sessions")
    tree.append_message(AgentUserMessage(content=ProductContent("search")))
    for message in messages:
        tree.append_message(message)
    return tree


def _rendered(
    tree: NativeSessionTree, lookup: ToolRendererLookup | None = None
) -> dict[str, dict[str, str]]:
    return render_export_tools(
        tree.get_entries(),
        lookup or ToolRendererLookup(),
        cwd=Path(tree.get_header().cwd),
        redact=redact_export_value,
        style=_PLAIN,
    )


_HITS = "\n".join(f"hit {n}" for n in range(8))


# -- terminal text to HTML (Pi ansi-to-html.ts) ----------------------------------


def test_colours_and_weights_become_inline_styles() -> None:
    line = (
        "\x1b[38;2;255;0;16mred\x1b[39m plain "
        "\x1b[1mbold\x1b[22m \x1b[38;5;196mindexed\x1b[0m "
        "\x1b[1;3;4;32mall\x1b[0m \x1b[91mbright\x1b[39m"
    )

    assert ansi_lines_to_html([line]) == (
        '<div class="ansi-line">'
        '<span style="color:#ff0010">red</span> plain '
        '<span style="font-weight:bold">bold</span> '
        '<span style="color:#ff0000">indexed</span> '
        '<span style="color:#0dbc79;font-weight:bold;font-style:italic;'
        'text-decoration:underline">all</span> '
        '<span style="color:#f14c4c">bright</span>'
        "</div>"
    )


def test_text_is_escaped_and_everything_else_a_terminal_acts_on_is_dropped() -> None:
    hostile = (
        '<script>alert("x")</script> & '
        "\x1b]52;c;aGk=\x07osc \x1b[2Jclear \x1b[48;2;1;2;3mbg\x1b[49m "
        "\x1b]8;;https://example.com\x1b\\link\x1b]8;;\x1b\\ bell\x07 \x9b31m"
    )

    fragment = ansi_lines_to_html([hostile])

    assert fragment == (
        '<div class="ansi-line">'
        "&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt; &amp; "
        "osc clear bg link bell 31m"
        "</div>"
    )


def test_a_parameter_that_is_no_code_is_ignored() -> None:
    # Python refuses to convert a number of more than 4300 digits.
    huge = "9" * 5000
    line = f"\x1b[{huge}mA\x1b[1;{huge};31mB\x1b[38;5;{huge}mC\x1b[38;2;1;2;{huge}mD"

    assert ansi_lines_to_html([line]) == (
        '<div class="ansi-line">A'
        '<span style="color:#cd3131;font-weight:bold">B</span>'
        '<span style="color:#cd3131;font-weight:bold">C</span>'
        '<span style="color:#010200;font-weight:bold">D</span>'
        "</div>"
    )


def test_leading_zeros_of_a_parameter_do_not_count() -> None:
    zeros = "0" * 5000
    line = (
        "\x1b[0031mred\x1b[0001mbold\x1b[0000mplain"
        f"\x1b[38;2;0255;0000;{zeros}16mrgb\x1b[;mplain\x1b[38;05;0196mindexed"
    )

    assert ansi_lines_to_html([line]) == (
        '<div class="ansi-line">'
        '<span style="color:#cd3131">red</span>'
        '<span style="color:#cd3131;font-weight:bold">bold</span>plain'
        '<span style="color:#ff0010">rgb</span>plain'
        '<span style="color:#ff0000">indexed</span>'
        "</div>"
    )


def test_an_empty_line_keeps_its_height_and_each_line_starts_plain() -> None:
    assert ansi_lines_to_html(["\x1b[1mopen", "", "next"]) == (
        '<div class="ansi-line"><span style="font-weight:bold">open</span></div>'
        '<div class="ansi-line">&nbsp;</div>'
        '<div class="ansi-line">next</div>'
    )
    assert ansi_lines_to_html([]) == ""


# -- which calls are drawn, and how ------------------------------------------------


def test_an_mcp_call_without_its_tool_is_drawn_like_in_the_transcript(
    tmp_path: Path,
) -> None:
    # Pi: "renders them in HTML exports too".
    tree = _tree(
        tmp_path,
        AgentAssistantMessage(
            content=ProductContent(""),
            tool_calls=(_call("call-1", "mcp__my_docs__search", '{"query": "pi"}'),),
        ),
        _result(
            "call-1",
            "mcp__my_docs__search",
            _HITS,
            details={"server": "my-docs", "tool": "search", "fullOutputPath": "/t/o"},
        ),
    )

    rendered = _rendered(tree)

    assert list(rendered) == ["call-1"]
    assert _text(rendered["call-1"]["callHtml"]) == ['my_docs/search query="pi"']
    assert _text(rendered["call-1"]["resultHtmlCollapsed"]) == [
        *[f"hit {n}" for n in range(5)],
        "... (3 more lines, ctrl+o to expand)",
        "Full output: /t/o",
    ]
    assert _text(rendered["call-1"]["resultHtmlExpanded"]) == [
        f"hit {n}" for n in range(8)
    ]


def test_a_registered_tools_title_and_the_error_colour(tmp_path: Path) -> None:
    tree = _tree(
        tmp_path,
        AgentAssistantMessage(
            content=ProductContent(""),
            tool_calls=(_call("c1", "mcp__my_docs__search", "{}"),),
        ),
        _result("c1", "mcp__my_docs__search", "no such page", is_error=True),
    )
    lookup = ToolRendererLookup(mcp_labels={"mcp__my_docs__search": "my-docs/search"})

    rendered = render_export_tools(
        tree.get_entries(),
        lookup,
        cwd=tmp_path,
        redact=redact_export_value,
        style=export_chrome_style(),
    )["c1"]

    assert _text(rendered["callHtml"]) == ["my-docs/search"]
    assert 'font-weight:bold">my-docs/search' in rendered["callHtml"]
    # Collapsed and expanded are the same: only the expanded one is kept.
    assert "resultHtmlCollapsed" not in rendered
    assert re.fullmatch(
        r'<div class="ansi-line"><span style="color:#[0-9a-f]{6}">'
        r"no such page</span></div>",
        rendered["resultHtmlExpanded"],
    )


def test_calls_without_renderers_are_left_to_the_page(tmp_path: Path) -> None:
    tree = _tree(
        tmp_path,
        AgentAssistantMessage(
            content=ProductContent(""),
            tool_calls=(
                _call("c1", "read", '{"path": "a"}'),
                _call("c2", "unknown_tool", "{}"),
                _call("c3", "list_mcp_resources", "{}"),
                _call("c4", "mcp__docs__pending", "{}"),
            ),
        ),
        _result("c1", "read", "body"),
        _result("c2", "unknown_tool", "unknown tool"),
    )
    # A resolver's renderers for a built-in tool are not used either: the
    # page draws the built-in tools (Pi `TEMPLATE_RENDERED_TOOLS`).
    mine = ToolRenderers(render_call=lambda ctx: lines_component(["MINE"]))
    lookup = ToolRendererLookup(
        resolvers=(lambda name, following: mine if name == "read" else following(),)
    )

    rendered = _rendered(tree, lookup)

    # A call without a result has only its call.
    assert rendered == {"c4": {"callHtml": '<div class="ansi-line">docs/pending</div>'}}
    payload = session_export_payload(tree, tool_renderers=lookup)
    assert list(payload["renderedTools"]) == ["c4"]
    assert "renderedTools" not in session_export_payload(_tree(tmp_path / "other"))


def test_extension_renderers_and_resolvers_draw_their_calls(tmp_path: Path) -> None:
    seen: list[ToolRenderContext] = []

    def render_result(ctx: ToolRenderContext) -> object:
        seen.append(ctx)
        return lines_component(
            ["", f"KV {ctx.content} expanded={ctx.expanded}", ""]
            if ctx.expanded
            else ["KV short"]
        )

    kv = ExtensionTool(
        name="kv",
        description="d",
        input_schema={"type": "object"},
        handler=lambda ctx, params: "",
        render_result=render_result,
    )
    ghost = ToolRenderers(
        render_call=lambda ctx: lines_component([f"GHOST <{ctx.tool_name}>"])
    )
    tree = _tree(
        tmp_path,
        AgentAssistantMessage(
            content=ProductContent(""),
            tool_calls=(
                _call("c1", "kv", '{"key": "a"}'),
                _call("c2", "ghost", "{}"),
                _call("c3", "bare", "{}"),
            ),
        ),
        _result("c1", "kv", "value", details={"n": 1}),
        _result("c2", "ghost", "boo"),
    )
    lookup = ToolRendererLookup(
        tools={"kv": kv},
        resolvers=(
            lambda name, following: (
                ghost
                if name == "ghost"
                # Renderers without a callable draw nothing.
                else ToolRenderers()
                if name == "bare"
                else following()
            ),
        ),
    )

    rendered = _rendered(tree, lookup)

    assert set(rendered) == {"c1", "c2"}
    # The part without a renderer is the generic one.
    assert _text(rendered["c1"]["callHtml"]) == ['kv key="a"']
    assert _text(rendered["c1"]["resultHtmlCollapsed"]) == ["KV short"]
    # Blank lines at either end of a result are cut (Pi).
    assert _text(rendered["c1"]["resultHtmlExpanded"]) == ["KV value expanded=True"]
    assert rendered["c2"]["callHtml"] == (
        '<div class="ansi-line">GHOST &lt;ghost&gt;</div>'
    )
    assert _text(rendered["c2"]["resultHtmlExpanded"]) == ["boo"]
    # Drawn at Pi's export width, less the box's two columns.
    assert {ctx.width for ctx in seen} == {98}
    assert seen[0].details == {"n": 1}


def test_credentials_are_withheld_before_anything_is_drawn(tmp_path: Path) -> None:
    seen: list[ToolRenderContext] = []

    def render(ctx: ToolRenderContext) -> object:
        seen.append(ctx)
        return lines_component([f"{dict(ctx.args)} {ctx.content} {ctx.details}"])

    tree = _tree(
        tmp_path,
        AgentAssistantMessage(
            content=ProductContent(""),
            tool_calls=(
                _call("c1", "mcp__docs__login", '{"api_key": "ARG-SECRET", "q": 1}'),
                _call("c2", "vault", '{"password": "ARG-SECRET-2"}'),
            ),
        ),
        _result(
            "c1",
            "mcp__docs__login",
            "ok token=ghp_RESULTSECRET123456789",
            details={"fullOutputPath": "/t/o", "secret": "DETAIL-SECRET"},
        ),
        _result(
            "c2",
            "vault",
            "Bearer RESULTSECRET2abcdef",
            details={"token": "DETAIL-SECRET-2"},
        ),
    )
    lookup = ToolRendererLookup(
        resolvers=(
            lambda name, following: (
                ToolRenderers(render_call=render, render_result=render)
                if name == "vault"
                else following()
            ),
        )
    )

    payload = session_export_payload(tree, tool_renderers=lookup)

    serialized = repr(payload) + repr(seen)
    for secret in (
        "ARG-SECRET",
        "RESULTSECRET",
        "DETAIL-SECRET",
    ):
        assert secret not in serialized
    assert _text(payload["renderedTools"]["c1"]["callHtml"]) == [
        'docs/login api_key="[REDACTED]" q=1'
    ]
    assert "[REDACTED]" in payload["renderedTools"]["c2"]["resultHtmlExpanded"]


def test_a_call_that_cannot_be_drawn_is_left_to_the_page(
    tmp_path: Path, monkeypatch
) -> None:
    # Pi `createToolHtmlRenderer`: an error leaves that part to the page.
    import pipy_harness.native.export_tool_rows as module

    real = module.ansi_lines_to_html

    def fragile(lines: Any) -> str:
        if any("BOOM" in line for line in lines):
            raise ValueError("cannot convert")
        return real(lines)

    monkeypatch.setattr(module, "ansi_lines_to_html", fragile)
    tree = _tree(
        tmp_path,
        AgentAssistantMessage(
            content=ProductContent(""),
            tool_calls=(
                _call("c1", "mcp__docs__search", '{"q": "BOOM"}'),
                _call("c2", "mcp__docs__fetch", "{}"),
                _call("c3", "mcp__docs__other", "{}"),
            ),
        ),
        _result("c1", "mcp__docs__search", "fine"),
        _result("c2", "mcp__docs__fetch", "BOOM"),
        _result("c3", "mcp__docs__other", "fine"),
    )

    rendered = _rendered(tree)

    # The call that failed is not drawn at all; the result that failed is
    # missing, so the page shows the stored one.
    assert set(rendered) == {"c2", "c3"}
    assert set(rendered["c2"]) == {"callHtml"}
    assert set(rendered["c3"]) == {"callHtml", "resultHtmlExpanded"}


def test_a_call_id_used_again_is_drawn_from_its_last_call(tmp_path: Path) -> None:
    tree = _tree(
        tmp_path,
        AgentAssistantMessage(
            content=ProductContent(""),
            tool_calls=(_call("c1", "mcp__docs__first", "{}"),),
        ),
        _result("c1", "mcp__docs__first", "one"),
        AgentAssistantMessage(
            content=ProductContent(""), tool_calls=(_call("c1", "read", "{}"),)
        ),
        _result("c1", "read", "two"),
        AgentAssistantMessage(
            content=ProductContent(""),
            tool_calls=(_call("c2", "mcp__docs__second", "{}"),),
        ),
        # A result that names no drawn call changes nothing.
        _result("c9", "mcp__docs__second", "stray"),
    )

    assert _rendered(tree) == {
        "c2": {"callHtml": '<div class="ansi-line">docs/second</div>'}
    }


# -- the exported file ---------------------------------------------------------------


def test_an_exported_file_has_the_rows_and_the_page_shows_them(tmp_path: Path) -> None:
    tree = _tree(
        tmp_path,
        AgentAssistantMessage(
            content=ProductContent(""),
            tool_calls=(_call("call-1", "mcp__my_docs__search", '{"query": "pi"}'),),
        ),
        _result("call-1", "mcp__my_docs__search", _HITS),
    )
    assert tree.path is not None

    for output in (
        export_native_session_to_html(tree, tmp_path / "live.html"),
        # `pipy --export <file>`: no session, so no extension; the name rule.
        export_from_file(tree.path, tmp_path / "file.html"),
    ):
        payload = _decode_html_payload(output)
        rows = payload["renderedTools"]["call-1"]
        assert "my_docs/search" in _TAG.sub("", rows["callHtml"])
        assert "hit 7" in rows["resultHtmlExpanded"]
        assert "hit 7" not in rows["resultHtmlCollapsed"]
        # The stored call and result are still in the export, as before.
        assert len(payload["entries"]) == 3
        page = output.read_text(encoding="utf-8")
        assert "data.renderedTools" in page and "tool-rendered" in page


_RESOLVER = """from pipy_harness.extensions import ToolRenderers, lines_component


def activate(api):
    mine = ToolRenderers(
        render_call=lambda ctx: lines_component(['GHOST CALL ' + ctx.tool_name]))
    api.register_tool_renderer(
        lambda name, following: following() or (mine if name == 'ghost' else None))
"""


def test_the_export_command_asks_the_loaded_extensions(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "config"))
    tree = _tree(
        tmp_path,
        AgentAssistantMessage(
            content=ProductContent(""),
            tool_calls=(
                _call("c1", "ghost", "{}"),
                _call("c2", "mcp__docs__search", "{}"),
            ),
        ),
    )
    cwd = Path(tree.get_header().cwd)
    extensions = cwd / ".pipy" / "extensions"
    extensions.mkdir(parents=True)
    (extensions / "resolver.py").write_text(_RESOLVER, encoding="utf-8")

    err = _run_commands(tree, cwd, "/export out.html\n/exit\n")

    assert "exported native session HTML" in err, err
    rendered = _decode_html_payload(cwd / "out.html")["renderedTools"]
    assert _text(rendered["c1"]["callHtml"]) == ["GHOST CALL ghost"]
    assert _text(rendered["c2"]["callHtml"]) == ["docs/search"]


def test_export_renderers_are_told_each_calls_id(tmp_path: Path) -> None:
    # Pi `ToolRenderContext.toolCallId`; the export draws each call once.
    seen: list[ToolRenderContext] = []

    def draw(ctx: ToolRenderContext) -> object:
        seen.append(ctx)
        return lines_component([ctx.tool_call_id])

    tree = _tree(
        tmp_path,
        AgentAssistantMessage(
            content=ProductContent(""),
            tool_calls=(_call("c1", "jira", "{}"), _call("c2", "jira", "{}")),
        ),
        _result("c1", "jira", "one"),
        _result("c2", "jira", "two"),
    )
    lookup = ToolRendererLookup(
        resolvers=(lambda name, _next: ToolRenderers(draw, draw),)
    )

    _rendered(tree, lookup)

    assert {ctx.tool_call_id for ctx in seen} == {"c1", "c2"}
    assert all(ctx.execution_started is False for ctx in seen)


def test_a_resolver_wraps_codemodes_rows_in_the_export(tmp_path: Path) -> None:
    # Goal check r4, D3: `next()` gives codemode's own rows (Pi
    # `codemodeRenderers`), which draw the output without the script header.
    def wrapping(name: str, following: Any) -> object | None:
        inner = following()
        if inner is None:
            return None

        def result(ctx: ToolRenderContext) -> object:
            component = inner.render_result(ctx)
            lines = [] if component is None else list(component.render(ctx.width))
            return lines_component(["[wrapped]", *lines])

        return ToolRenderers(render_call=inner.render_call, render_result=result)

    tree = _tree(
        tmp_path,
        AgentAssistantMessage(
            content=ProductContent(""),
            tool_calls=(_call("c1", "codemode", '{"code": "print(1)"}'),),
        ),
        _result(
            "c1", "codemode", "Script completed\nWall time 0.1 seconds\nOutput:\n1"
        ),
    )

    rendered = _rendered(tree, ToolRendererLookup(resolvers=(wrapping,)))

    assert _text(rendered["c1"]["callHtml"]) == ['codemode code="print(1)"']
    # Pi's codemode result starts with a spacer.
    assert _text(rendered["c1"]["resultHtmlExpanded"]) == ["[wrapped]", "", "1"]
    # Without a resolver the page draws codemode's call itself, as before.
    assert _rendered(tree) == {}


def test_an_exported_call_is_drawn_before_its_result(tmp_path: Path) -> None:
    # Pi `createToolHtmlRenderer.renderCall`: the call's context is partial
    # and not an error, also for a call that failed; the result's says so.
    seen: list[ToolRenderContext] = []

    def draw(ctx: ToolRenderContext) -> object:
        seen.append(ctx)
        return lines_component([f"{ctx.is_result} error={ctx.is_error}"])

    tree = _tree(
        tmp_path,
        AgentAssistantMessage(
            content=ProductContent(""), tool_calls=(_call("c1", "deploy", "{}"),)
        ),
        _result("c1", "deploy", "boom", is_error=True),
    )
    lookup = ToolRendererLookup(
        resolvers=(
            lambda name, following: ToolRenderers(render_call=draw, render_result=draw),
        )
    )

    rendered = _rendered(tree, lookup)

    assert _text(rendered["c1"]["callHtml"]) == ["False error=False"]
    assert _text(rendered["c1"]["resultHtmlExpanded"]) == ["True error=True"]
    assert seen[0].is_partial is True
