"""The ``mcp_servers`` prompt section (MCP parity plan S20, rows SEC-01 to
SEC-05 and SEC-07).

Mirrors the "MCP servers section" cases of Pi's ``mcp-extension.test.ts``.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from test_native_mcp_connection import _Server
from test_native_mcp_manager import _Session, _tool

from pipy_harness.native.mcp.config import McpServerEntry
from pipy_harness.native.mcp.servers_section import (
    MAX_SERVERS_SECTION_CHARS,
    McpServerListing,
    has_indirect_tools,
    render_servers_section,
)

_INTRO = "MCP servers whose tools are not declared to you."
_CODEMODE = " Call the tools of `codemode` servers from codemode scripts."
_TOOL_SEARCH = " Load the tools of `tool_search` servers with `tool_search`."


def _entry(name: str, **config: Any) -> McpServerEntry:
    return McpServerEntry(name=name, config={"command": "x", **config}, source="test")


def _listing(
    name: str, instructions: str | None = None, **config: Any
) -> McpServerListing:
    return McpServerListing(_entry(name, **config), instructions)


def _utf16(text: str) -> int:
    return len(text.encode("utf-16-le"))


def test_lists_servers_with_their_reach_and_the_first_line_of_their_description() -> (
    None
):
    # Pi: "lists servers with how their tools are reached and the first line
    # of their description".
    section = render_servers_section(
        [
            _listing("docs", description="Docs search.\nMore."),
            _listing("later", exposure="deferred"),
            _listing("direct", description="Declared.", exposure="direct"),
            _listing("plain", "From instructions."),
        ]
    )

    assert section is not None
    assert section.split("\n") == [
        _INTRO + _CODEMODE + _TOOL_SEARCH,
        "- mcp__docs (codemode): Docs search.",
        "- mcp__later (tool_search)",
        "- mcp__plain (codemode): From instructions.",
    ]
    # SEC-01, SEC-07: a server with only direct tools is not listed.
    assert (
        render_servers_section(
            [_listing("direct", description="Declared.", exposure="direct")]
        )
        is None
    )
    assert render_servers_section([]) is None


def test_the_first_line_names_only_the_ways_the_listed_servers_use() -> None:
    # SEC-02.
    codemode = render_servers_section([_listing("a")])
    deferred = render_servers_section([_listing("a", exposure="deferred")])

    assert codemode == f"{_INTRO}{_CODEMODE}\n- mcp__a (codemode)"
    assert deferred == f"{_INTRO}{_TOOL_SEARCH}\n- mcp__a (tool_search)"


def test_reach_is_codemode_when_any_configured_exposure_is_codemode() -> None:
    # SEC-03: the server's exposure and every toolExposure value count.
    section = render_servers_section(
        [
            _listing("mixed", exposure="deferred", toolExposure={"x": "codemode"}),
            _listing("one-tool", exposure="direct", toolExposure={"x": "deferred"}),
            _listing("hidden", exposure="hidden"),
            _listing("off", enabled=False),
        ]
    )

    assert section is not None
    assert section.split("\n")[1:] == [
        "- mcp__mixed (codemode)",
        "- mcp__one_tool (tool_search)",
    ]
    assert not has_indirect_tools(_entry("hidden", exposure="hidden"))
    assert has_indirect_tools(_entry("default"))


def test_servers_are_sorted_by_name_like_locale_compare() -> None:
    # SEC-03: Pi sorts with `localeCompare`, where case and `-`/`_` do not
    # order as their code points do.
    names = ["b", "B", "a", "a_b", "a-b", "a2", "A1"]

    section = render_servers_section([_listing(name) for name in names])

    assert section is not None
    assert [line.split(" ")[1] for line in section.split("\n")[1:]] == [
        "mcp__a",
        "mcp__a_b",
        "mcp__a_b",
        "mcp__A1",
        "mcp__a2",
        "mcp__b",
        "mcp__B",
    ]


def test_the_summary_prefers_the_description_and_is_one_trimmed_line() -> None:
    # SEC-04.
    def summary(listing: McpServerListing) -> str:
        section = render_servers_section([listing])
        assert section is not None
        return section.split("\n", 1)[1]

    assert summary(_listing("a", "Instructions.", description="  Configured. \nX")) == (
        "- mcp__a (codemode): Configured."
    )
    # A blank description counts as none.
    assert summary(_listing("a", "  First. \nSecond.", description=" \n ")) == (
        "- mcp__a (codemode): First."
    )
    # Instructions that start with an empty line give no summary.
    assert summary(_listing("a", "\nSecond.")) == "- mcp__a (codemode)"
    long = summary(_listing("a", description="word " * 100))
    text = long.removeprefix("- mcp__a (codemode): ")
    assert len(text) <= 250 and text.endswith("word…")
    assert summary(_listing("a", description="x" * 250)).endswith(": " + "x" * 250)
    assert summary(_listing("a", description="x" * 251)).endswith(
        ": " + "x" * 249 + "…"
    )


def test_shortens_descriptions_to_fit_the_size_limit() -> None:
    # Pi: "shortens descriptions to fit the size limit".
    servers = [_listing(f"server{index}", description="x" * 400) for index in range(40)]

    section = render_servers_section(servers) or ""

    assert len(section) <= MAX_SERVERS_SECTION_CHARS
    lines = section.split("\n")
    assert len(lines) == 41
    assert "- mcp__server39 (codemode): x" in section
    # They shrink equally.
    assert len({len(line.split(": ", 1)[1]) for line in lines[1:]}) == 1


def test_leaves_out_the_last_servers_when_their_names_alone_do_not_fit() -> None:
    # Pi: "leaves out the last servers when their names alone do not fit".
    servers = [
        _listing(f"server-with-a-long-name-{index}", description="desc")
        for index in range(200)
    ]

    section = render_servers_section(servers) or ""

    assert len(section) <= MAX_SERVERS_SECTION_CHARS
    lines = section.split("\n")
    match = re.fullmatch(
        r"- … (\d+) more servers; find their tools with search_tools\(\)", lines[-1]
    )
    assert match is not None
    assert len(lines) - 2 + int(match.group(1)) == 200


def test_one_server_left_out_is_counted_in_the_singular() -> None:
    # SEC-05. The second name alone is longer than the section may be.
    section = render_servers_section([_listing("a"), _listing("b" * 4096)]) or ""

    assert section.split("\n")[1:] == [
        "- mcp__a (codemode)",
        "- … 1 more server; find their tools with search_tools()",
    ]
    # When not even the first server fits, only the count is left.
    assert (render_servers_section([_listing("b" * 4096)]) or "").split("\n")[1:] == [
        "- … 1 more server; find their tools with search_tools()"
    ]


def test_sizes_are_counted_in_utf16_units_and_no_character_is_cut() -> None:
    # Pi counts `length`; a character outside the BMP is two units.
    servers = [_listing(f"s{index}", description="😀" * 300) for index in range(40)]

    section = render_servers_section(servers) or ""

    assert _utf16(section) // 2 <= MAX_SERVERS_SECTION_CHARS
    section.encode("utf-8")  # no lone surrogate
    one = render_servers_section([_listing("a", description="😀" * 126)]) or ""
    assert one.endswith(": " + "😀" * 124 + "…")


def test_the_manager_lists_a_server_before_and_after_it_connects(
    tmp_path: Path,
) -> None:
    # SEC-04, SEC-07: listed from the configuration; the instructions become
    # the summary once the server connected.
    session = _Session(
        tmp_path,
        {
            "docs": _Server(tools=[_tool("search")], instructions="Docs.\nMore."),
            "direct": _Server(tools=[_tool("x")], instructions="Declared."),
            "off": _Server(tools=[_tool("x")]),
        },
        [
            _entry("docs"),
            _entry("direct", exposure="direct"),
            _entry("off", enabled=False),
        ],
    )

    before = session.manager.servers_section()
    session.start()
    try:
        after = session.manager.servers_section()
    finally:
        session.manager.close()

    assert before == f"{_INTRO}{_CODEMODE}\n- mcp__docs (codemode)"
    assert after == f"{_INTRO}{_CODEMODE}\n- mcp__docs (codemode): Docs."
