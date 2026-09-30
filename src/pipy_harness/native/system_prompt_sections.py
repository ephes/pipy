"""Pi's tagged system-prompt sections (``coding-agent/src/core/system-prompt.ts``).

Pi ``buildSystemPromptSections`` returns an ordered record: ``preamble`` is
untagged text, every other section is wrapped ``<name>\\n...\\n</name>`` so a
later system message can patch it by name. The transcript records the
sections; the prompt text is their rendering (``getSystemMessageText``).

pipy builds the sections it has: ``preamble`` (the default or custom prompt,
plus pipy's reference-roots lines), ``addendum`` (appended prompts),
``project_context``, ``skills``, ``cwd``, and the pipy-only ``resume`` block
as a trailing custom section. Pi's ``tools``/``rules``/``docs`` sections are
a follow-on (``docs/backlog.md``).
"""

from __future__ import annotations

from collections.abc import Sequence

SystemPromptSections = tuple[tuple[str, str], ...]
"""Ordered ``(name, rendered text)`` pairs; the text includes the tag wrapper."""


def tagged_section(name: str, content: str) -> str:
    """Pi's wrapper for every section except ``preamble``."""

    return f"<{name}>\n{content}\n</{name}>"


def build_system_prompt_sections(
    *,
    preamble: str,
    cwd: str,
    addendum: str = "",
    project_context: str = "",
    skills: str = "",
    resume: str = "",
) -> SystemPromptSections:
    """Pi ``buildSystemPromptSections`` order: empty optional sections are omitted.

    ``project_context`` and ``skills`` are section bodies (without their tag);
    ``cwd`` always appears, with backslashes as forward slashes.
    """

    sections: list[tuple[str, str]] = [("preamble", preamble)]
    for name, content in (
        ("addendum", addendum),
        ("project_context", project_context),
        ("skills", skills),
        ("cwd", cwd.replace("\\", "/")),
        ("resume", resume),
    ):
        if content:
            sections.append((name, tagged_section(name, content)))
    return tuple(sections)


def render_system_prompt(sections: Sequence[tuple[str, str]]) -> str:
    """Pi ``getSystemMessageText`` over sections: non-empty parts joined by a blank line."""

    return "\n\n".join(text for _, text in sections if text)


def sections_for_prompt(
    system_prompt: str, sections: Sequence[tuple[str, str]]
) -> SystemPromptSections:
    """The sections a session records for ``system_prompt``.

    Callers that pass only a prompt string (or a string that is not the
    rendering of ``sections``) get one untagged ``preamble``.
    """

    if sections and render_system_prompt(sections) == system_prompt:
        return tuple(sections)
    return (("preamble", system_prompt),)
