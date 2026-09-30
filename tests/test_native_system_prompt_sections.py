"""Pi's tagged system-prompt sections (SYS1b; ``system-prompt.ts``)."""

from __future__ import annotations

from pipy_harness.native.system_prompt_sections import (
    build_system_prompt_sections,
    render_system_prompt,
    sections_for_prompt,
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
