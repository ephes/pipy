"""Pi's tagged system-prompt sections (SYS1b; ``system-prompt.ts``)."""

from __future__ import annotations

import pytest

from pipy_harness.native.system_prompt_sections import (
    build_system_prompt_sections,
    is_custom_section_name,
    render_system_prompt,
    sections_for_prompt,
    split_custom_sections,
    with_custom_sections,
)


def test_sections_follow_pi_order_and_wrap_every_section_but_preamble() -> None:
    sections = build_system_prompt_sections(
        preamble="You are pipy.",
        addendum="Extra rules.",
        project_context="Project-specific instructions and guidelines:",
        skills="skill list",
        cwd="C:\\work\\repo",
        resume="Resumed from session s1.",
    )

    assert sections == (
        ("preamble", "You are pipy."),
        ("addendum", "<addendum>\nExtra rules.\n</addendum>"),
        (
            "project_context",
            "<project_context>\nProject-specific instructions and guidelines:\n"
            "</project_context>",
        ),
        ("skills", "<skills>\nskill list\n</skills>"),
        ("cwd", "<cwd>\nC:/work/repo\n</cwd>"),
        ("resume", "<resume>\nResumed from session s1.\n</resume>"),
    )
    assert render_system_prompt(sections) == "\n\n".join(text for _, text in sections)


def test_empty_optional_sections_are_omitted_and_cwd_always_present() -> None:
    sections = build_system_prompt_sections(preamble="P", cwd="/w")

    assert sections == (("preamble", "P"), ("cwd", "<cwd>\n/w\n</cwd>"))
    assert render_system_prompt(sections) == "P\n\n<cwd>\n/w\n</cwd>"


def test_render_drops_empty_parts_like_pi_get_system_message_text() -> None:
    assert render_system_prompt((("preamble", ""), ("cwd", "<cwd>\n/w\n</cwd>"))) == (
        "<cwd>\n/w\n</cwd>"
    )


def test_a_prompt_that_is_not_the_sections_rendering_is_one_preamble() -> None:
    sections = build_system_prompt_sections(preamble="P", cwd="/w")

    assert sections_for_prompt(render_system_prompt(sections), sections) == sections
    assert sections_for_prompt("custom prompt", sections) == (
        ("preamble", "custom prompt"),
    )
    assert sections_for_prompt("only a string", ()) == (("preamble", "only a string"),)


def test_custom_sections_replace_in_place_or_follow_the_last_section() -> None:
    # Pi `buildSystemPromptSections` with the `sections` option.
    sections = build_system_prompt_sections(
        preamble="P", cwd="/work", tools="- read", resume="resumed"
    )

    result = with_custom_sections(
        sections, {"mcp_servers": "servers", "cwd": "/other", "notes": "n", "gone": ""}
    )

    assert result == (
        ("preamble", "P"),
        ("tools", "<tools>\n- read\n</tools>"),
        ("cwd", "<cwd>\n/other\n</cwd>"),
        ("resume", "<resume>\nresumed\n</resume>"),
        ("mcp_servers", "<mcp_servers>\nservers\n</mcp_servers>"),
        ("notes", "<notes>\nn\n</notes>"),
    )
    assert with_custom_sections(sections, {}) == sections
    # Sections a caller passed as they are (no template) take them as well.
    assert with_custom_sections((("preamble", "only"),), {"a-b_1": "x"}) == (
        ("preamble", "only"),
        ("a-b_1", "<a-b_1>\nx\n</a-b_1>"),
    )


@pytest.mark.parametrize("name", ["preamble", "Notes", "1st", "a b", "", "a\n", "é"])
def test_an_invalid_custom_section_name_is_refused(name: str) -> None:
    # Pi: `Invalid system prompt section name: <name>`.
    assert not is_custom_section_name(name)
    with pytest.raises(ValueError, match="Invalid system prompt section name: "):
        with_custom_sections((("preamble", "P"),), {name: "x"})


def test_split_custom_sections_separates_what_cannot_be_a_section() -> None:
    valid, invalid = split_custom_sections(
        {"notes": "n", "empty": "", "Bad": "x", "preamble": "x", "count": 3, 7: "x"}
    )

    assert valid == {"notes": "n", "empty": ""}
    assert invalid == ("Bad", "preamble", "count", "7")
