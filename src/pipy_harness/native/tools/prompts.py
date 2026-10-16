"""What tools contribute to the system prompt (Pi ``promptSnippet`` and
``promptGuidelines``).

A tool's definition carries a one-line snippet for the prompt's ``tools``
list and guideline bullets for its ``rules``; the built-in tools carry Pi's
texts from :data:`BUILTIN_TOOL_PROMPTS`. A script that reaches a tool sees its
guidelines with its declaration, since the prompt has them only for declared
tools.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Protocol


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
                "Use codemode to batch independent tool calls (`tools.<name>.submit()`), chain them, or filter large output in one step instead of many separate calls.",
                "Keep intermediate tool results in Python and emit only the task-relevant answer; parse documented text output directly instead of adding format-probe calls.",
            ),
        ),
        # Pi `extensions/tool-search/tool.ts`.
        "tool_search": ToolPrompt(
            "Search for tools that are not loaded yet and load the matches"
        ),
    }
)


_WHITESPACE = re.compile(r"\s+")


def normalized_prompt_snippet(text: str | None) -> str | None:
    """Pi ``_normalizePromptSnippet``: one line with single spaces, or
    ``None`` when nothing is left."""

    if not text:
        return None
    one_line = _WHITESPACE.sub(" ", text.replace("\r", " ").replace("\n", " "))
    one_line = one_line.strip()
    return one_line or None


def normalized_prompt_guidelines(guidelines: Sequence[str]) -> tuple[str, ...]:
    """Pi ``_normalizePromptGuidelines``: trimmed, without empty or repeated
    entries, in their order."""

    unique: dict[str, None] = {}
    for guideline in guidelines:
        normalized = guideline.strip()
        if normalized:
            unique.setdefault(normalized)
    return tuple(unique)


class _HasPrompts(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def prompt_snippet(self) -> str | None: ...

    @property
    def prompt_guidelines(self) -> tuple[str, ...]: ...


def prompt_snippets(definitions: Iterable[_HasPrompts]) -> dict[str, str]:
    """The normalized snippet of each definition that has one, by name."""

    return {
        definition.name: snippet
        for definition in definitions
        if (snippet := normalized_prompt_snippet(definition.prompt_snippet))
    }


def prompt_guidelines(
    definitions: Iterable[_HasPrompts],
) -> dict[str, tuple[str, ...]]:
    """The normalized guidelines of each definition that has some, by name."""

    return {
        definition.name: guidelines
        for definition in definitions
        if (guidelines := normalized_prompt_guidelines(definition.prompt_guidelines))
    }
