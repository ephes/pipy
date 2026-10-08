"""Pi's system prompt and its tagged sections (``coding-agent/src/core/system-prompt.ts``).

Pi ``buildSystemPromptSections`` returns an ordered record: ``preamble`` is
untagged text, every other section is wrapped ``<name>\\n...\\n</name>`` so a
later system message can patch it by name. The transcript records the
sections; the prompt text is their rendering (``getSystemMessageText``).

The default prompt is Pi's (with ``pipy`` for ``pi``): the preamble, then the
``tools``, ``rules`` and ``docs`` sections, which a custom prompt
(``--system-prompt``/``SYSTEM.md``) replaces. ``addendum``,
``project_context``, ``skills`` and ``cwd`` follow, then the pipy-only
``resume`` block as a trailing custom section. The ``tools``/``rules`` and
``skills`` sections depend on the active tools, so a session keeps a
:class:`SystemPromptTemplate` and renders it for each run's tools (Pi
rebuilds them from ``getActiveToolNames()`` before a request).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType

from pipy_harness.native.skills import SkillFile, skills_section_body

SystemPromptSections = tuple[tuple[str, str], ...]
"""Ordered ``(name, rendered text)`` pairs; the text includes the tag wrapper."""

PRODUCT_NAME = "pipy"

DEFAULT_PREAMBLE_TEMPLATE = (
    "You are an expert coding assistant operating inside {product}, a coding "
    "agent harness. You help users by reading files, executing commands, "
    "editing code, and writing new files."
)
DEFAULT_PREAMBLE = DEFAULT_PREAMBLE_TEMPLATE.format(product=PRODUCT_NAME)
"""Pi's default preamble with pipy's name (``system-prompt.ts``)."""

TOOLS_SECTION_FOOTER = (
    "In addition to the tools above, you may have access to other custom tools "
    "depending on the project."
)
DEFAULT_RULES = (
    "Be concise in your responses",
    "Show file paths clearly when working with files",
)


@dataclass(frozen=True, slots=True)
class ToolPrompt:
    """A tool's Pi ``promptSnippet`` and ``promptGuidelines``."""

    snippet: str
    guidelines: tuple[str, ...] = ()


BUILTIN_TOOL_PROMPTS: Mapping[str, ToolPrompt] = MappingProxyType(
    {
        # Pi `core/tools/*.ts` `*ToolSystemPromptContribution`. Pi's bash
        # guideline names the PI_* session environment variables, which it
        # sets only with `exposeSessionEnvironment`; pipy's bash tool sets
        # none, so it carries no guideline (docs/backlog.md).
        "read": ToolPrompt(
            "Read file contents",
            ("Use read to examine files instead of cat or sed.",),
        ),
        "bash": ToolPrompt("Execute bash commands (ls, grep, find, etc.)"),
        "edit": ToolPrompt(
            "Make precise file edits with exact text replacement, including "
            "multiple disjoint edits in one call",
            (
                "Use edit for precise changes (edits[].oldText must match exactly)",
                "When changing multiple separate locations in one file, use one "
                "edit call with multiple entries in edits[] instead of multiple "
                "edit calls",
                "Each edits[].oldText is matched against the original file, not "
                "after earlier edits are applied. Do not emit overlapping or "
                "nested edits. Merge nearby changes into one edit.",
                "Keep edits[].oldText as small as possible while still being "
                "unique in the file. Do not pad with large unchanged regions.",
            ),
        ),
        "write": ToolPrompt(
            "Create or overwrite files",
            ("Use write only for new files or complete rewrites.",),
        ),
        "grep": ToolPrompt("Search file contents for patterns (respects .gitignore)"),
        "find": ToolPrompt("Find files by glob pattern (respects .gitignore)"),
        "ls": ToolPrompt("List directory contents"),
        "codemode": ToolPrompt(
            "Run Python that calls other tools",
            (
                "Use codemode to chain several tool calls or filter large output in one step instead of many separate calls.",
            ),
        ),
    }
)


def tagged_section(name: str, content: str) -> str:
    """Pi's wrapper for every section except ``preamble``."""

    return f"<{name}>\n{content}\n</{name}>"


def tools_section(selected_tools: Sequence[str], snippets: Mapping[str, str]) -> str:
    """Pi's ``tools`` body: one ``- name: snippet`` per selected tool with a snippet."""

    visible = [name for name in selected_tools if snippets.get(name)]
    tools = (
        "\n".join(f"- {name}: {snippets[name]}" for name in visible)
        if visible
        else "(none)"
    )
    return f"{tools}\n\n{TOOLS_SECTION_FOOTER}"


def build_rules(
    selected_tools: Sequence[str],
    tool_guidelines: Mapping[str, Sequence[str]],
    prompt_guidelines: Sequence[str] = (),
) -> str:
    """Pi ``buildRules``: trimmed, deduplicated ``- rule`` bullets.

    pipy has no ``powershell`` tool, so only Pi's bash variant of the
    file-operations rule applies.
    """

    rules: list[str] = []

    def add(rule: str) -> None:
        normalized = rule.strip()
        if normalized and normalized not in rules:
            rules.append(normalized)

    selected = set(selected_tools)
    if "bash" in selected and not selected & {"grep", "find", "ls"}:
        add("Use bash for file operations like ls, rg, find")
    for name in selected_tools:
        for rule in tool_guidelines.get(name, ()):
            add(rule)
    for rule in prompt_guidelines:
        add(rule)
    for rule in DEFAULT_RULES:
        add(rule)
    return "\n".join(f"- {rule}" for rule in rules)


@dataclass(frozen=True, slots=True)
class DocsInfo:
    """What Pi's ``docs`` section names: the product, its docs and topics."""

    product: str
    title: str
    readme: str
    docs: str
    examples: str
    # (topic, doc references) pairs, in Pi's order.
    topics: tuple[tuple[str, str], ...]
    # The example in the last bullet (Pi: ``tui.md for TUI API details``).
    link_example: str


PIPY_DOC_TOPICS: tuple[tuple[str, str], ...] = (
    ("extensions", "docs/extension-api.md, examples/extensions/"),
    ("themes", "docs/customization.md"),
    ("skills", "docs/customization.md"),
    ("prompt templates", "docs/customization.md"),
    ("TUI components", "docs/tui-workflow.md"),
    ("keybindings", "docs/keybindings.md"),
    ("SDK integrations", "docs/sdk.md"),
    ("custom providers", "docs/providers.md"),
    ("adding models", "docs/provider-catalog.md"),
    ("pipy packages", "docs/packages.md"),
)


def docs_section(info: DocsInfo) -> str:
    """Pi's ``docs`` body for ``info`` (Pi's own text for Pi's values)."""

    product = info.product
    topics = ", ".join(f"{topic} ({refs})" for topic, refs in info.topics)
    return "\n".join(
        (
            f"{info.title} documentation (read only when the user asks about "
            f"{product} itself, its SDK, extensions, themes, skills, or TUI):",
            f"- Main documentation: {info.readme}",
            f"- Additional docs: {info.docs}",
            f"- Examples: {info.examples} (extensions, custom tools, SDK)",
            f"- When reading {product} docs or examples, resolve docs/... under "
            "Additional docs and examples/... under Examples, not the current "
            "working directory",
            f"- When asked about: {topics}",
            f"- When working on {product} topics, read the docs and examples, and "
            "follow .md cross-references before implementing",
            f"- Always read {product} .md files completely and follow links to "
            f"related docs (e.g., {info.link_example})",
        )
    )


def _product_root() -> Path:
    """The directory holding pipy's ``README.md`` and ``docs/``.

    Walks up from this module like ``default_changelog_path``; in a checkout
    that is the repository root. The wheel does not ship the docs yet, so an
    installed copy names the package directory (docs/backlog.md).
    """

    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "README.md").is_file() and (parent / "docs").is_dir():
            return parent
    return here.parent.parent


def pipy_docs_info() -> DocsInfo:
    root = _product_root()
    return DocsInfo(
        product=PRODUCT_NAME,
        title="Pipy",
        readme=str(root / "README.md"),
        docs=str(root / "docs"),
        examples=str(root / "docs" / "examples"),
        topics=PIPY_DOC_TOPICS,
        link_example="tui-workflow.md for TUI details",
    )


def build_system_prompt_sections(
    *,
    preamble: str,
    cwd: str,
    tools: str = "",
    rules: str = "",
    docs: str = "",
    addendum: str = "",
    project_context: str = "",
    skills: str = "",
    resume: str = "",
) -> SystemPromptSections:
    """Pi ``buildSystemPromptSections`` order: empty optional sections are omitted.

    Every argument but ``preamble`` is a section body (without its tag);
    ``cwd`` always appears, with backslashes as forward slashes.
    """

    sections: list[tuple[str, str]] = [("preamble", preamble)]
    for name, content in (
        ("tools", tools),
        ("rules", rules),
        ("docs", docs),
        ("addendum", addendum),
        ("project_context", project_context),
        ("skills", skills),
        ("cwd", cwd.replace("\\", "/")),
        ("resume", resume),
    ):
        if content:
            sections.append((name, tagged_section(name, content)))
    return tuple(sections)


@dataclass(frozen=True, slots=True)
class SystemPromptTemplate:
    """The tool-independent inputs of a session's prompt (Pi's base options).

    ``custom_preamble`` is a non-empty ``--system-prompt``/``SYSTEM.md`` text;
    ``None`` (or ``""``, as in Pi) is the default prompt. ``sections`` is Pi's
    ``buildSystemPromptSections`` for one run's selected (active) tools.
    """

    cwd: str
    custom_preamble: str | None = None
    addendum: str = ""
    project_context: str = ""
    skills: tuple[SkillFile, ...] = ()
    resume: str = ""
    tool_snippets: Mapping[str, str] = field(
        default_factory=lambda: {
            name: prompt.snippet for name, prompt in BUILTIN_TOOL_PROMPTS.items()
        }
    )
    tool_guidelines: Mapping[str, tuple[str, ...]] = field(
        default_factory=lambda: {
            name: prompt.guidelines
            for name, prompt in BUILTIN_TOOL_PROMPTS.items()
            if prompt.guidelines
        }
    )
    docs: DocsInfo | None = None
    product: str = PRODUCT_NAME

    def sections(self, selected_tools: Sequence[str]) -> SystemPromptSections:
        selected = tuple(selected_tools)
        tools = rules = docs = ""
        if self.custom_preamble:
            preamble = self.custom_preamble
        else:
            preamble = DEFAULT_PREAMBLE_TEMPLATE.format(product=self.product)
            tools = tools_section(selected, self.tool_snippets)
            rules = build_rules(selected, self.tool_guidelines)
            docs = docs_section(
                self.docs if self.docs is not None else pipy_docs_info()
            )
        loader = next((name for name in ("read", "bash") if name in selected), None)
        skills = skills_section_body(self.skills, loader) if loader else ""
        return build_system_prompt_sections(
            preamble=preamble,
            tools=tools,
            rules=rules,
            docs=docs,
            addendum=self.addendum,
            project_context=self.project_context,
            skills=skills,
            cwd=self.cwd,
            resume=self.resume,
        )


SystemPromptSource = SystemPromptSections | SystemPromptTemplate
"""What a session is given: fixed sections, or a template rendered per run."""


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
