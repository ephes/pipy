"""Tool discovery: a BM25 ranker over tool metadata, and the `tool_search` tool.

The port of Pi's
``packages/coding-agent/src/extensions/tool-search/tool.ts``: the tokenizer,
the search text of one tool, the ranker and the tool. Codemode's
``search_tools`` and `tool_search` share the ranking.

`tool_search` searches the tools that are not declared to the model
(``codemode`` and ``deferred`` exposure) and loads the matches, so the next
model request declares them. Loading changes the active tool set, so the
transcript records it like any other tool change.

A schema handed to `create_tool_search_document` is a JSON value as a tool
definition holds it: mappings and sequences without cycles.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from pipy_harness.native.agent.tool_details import NO_TOOL_DETAILS
from pipy_harness.native.tools.base import (
    ToolContext,
    ToolDefinition,
    ToolExecutionResult,
    ToolNamespace,
    ToolReach,
    ToolRequest,
)
from pipy_harness.native.tools.prompts import BUILTIN_TOOL_PROMPTS

__all__ = [
    "DEFAULT_TOOL_SEARCH_LIMIT",
    "TOOL_SEARCH_DESCRIPTION",
    "TOOL_SEARCH_TOOL_NAME",
    "Bm25Ranker",
    "LoadableTools",
    "SearchableTool",
    "ToolRanker",
    "ToolSearchDocument",
    "ToolSearchMatch",
    "ToolSearchTool",
    "create_tool_search_document",
    "stem",
    "tokenize",
    "tool_search_definition",
]

TOOL_SEARCH_TOOL_NAME = "tool_search"
DEFAULT_TOOL_SEARCH_LIMIT = 8

# Pi ``TOOL_SEARCH_DESCRIPTION``. It names no searchable tool and no
# namespace, so it stays the same while tools are registered, for example
# when MCP servers connect.
TOOL_SEARCH_DESCRIPTION = (
    "# Tool discovery\n\n"
    "Searches over deferred tool metadata with BM25 and exposes matching tools "
    "for the next model call.\n\n"
    "Some of the tools, such as tools of MCP servers, may not have been "
    "provided to you upfront, and you should use this tool "
    f"(`{TOOL_SEARCH_TOOL_NAME}`) to search for the required tools. For MCP "
    f"tool discovery, always use `{TOOL_SEARCH_TOOL_NAME}`."
)

_STOP_WORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "for",
        "from",
        "in",
        "is",
        "it",
        "of",
        "on",
        "or",
        "that",
        "the",
        "this",
        "to",
        "with",
    }
)

_LOWER_THEN_UPPER = re.compile(r"([a-z0-9])([A-Z])")
_ACRONYM_THEN_WORD = re.compile(r"([A-Z]+)([A-Z][a-z])")
_NOT_A_TERM = re.compile(r"[^a-z0-9]+")
_ES_PLURALS = ("ches", "shes", "sses", "xes", "zes")
_VARIANT_KEYWORDS = ("anyOf", "oneOf", "allOf")

# What JavaScript's ``String.prototype.trim`` removes, so a part is dropped
# exactly when Pi drops it.
_JS_BLANK_CLASS = "[\t\n\v\f\r    -     　﻿]"
_JS_BLANK = re.compile(f"{_JS_BLANK_CLASS}*")
_JS_BLANK_ENDS = re.compile(f"^{_JS_BLANK_CLASS}+|{_JS_BLANK_CLASS}+$")
_LINE_BREAK = re.compile(r"\r?\n")


@dataclass(frozen=True, slots=True)
class ToolSearchDocument:
    """A tool as the ranker sees it: its name and its search text."""

    name: str
    text: str


@dataclass(frozen=True, slots=True)
class ToolSearchMatch:
    """One ranked tool and its score, which is always positive."""

    name: str
    score: float


class ToolRanker(Protocol):
    """Ranks tools for a query, best first, at most `limit` of them."""

    def rank(
        self,
        query: str,
        documents: Sequence[ToolSearchDocument],
        limit: int,
    ) -> list[ToolSearchMatch]: ...


class SearchableTool(Protocol):
    """The part of a tool the search text is built from.

    `ToolInfo` and `ToolDefinition` both fit.
    """

    @property
    def name(self) -> str: ...

    @property
    def description(self) -> str: ...

    @property
    def input_schema(self) -> Mapping[str, Any]: ...


def stem(term: str) -> str:
    """Naive singular form: `issues` is `issue`, `searches` is `search`."""

    if len(term) > 4 and term.endswith("ies"):
        return f"{term[:-3]}y"
    if len(term) > 4 and term.endswith(_ES_PLURALS):
        return term[:-2]
    if len(term) > 3 and term.endswith("s") and not term.endswith("ss"):
        return term[:-1]
    return term


def tokenize(text: str) -> list[str]:
    """Split text into lower-case terms without stop words, plurals folded.

    Terms end at camelCase boundaries and at every character that is not an
    ASCII letter or digit.
    """

    spaced = _LOWER_THEN_UPPER.sub(r"\1 \2", text)
    spaced = _ACRONYM_THEN_WORD.sub(r"\1 \2", spaced)
    return [
        stem(term)
        for term in _NOT_A_TERM.split(spaced.lower())
        if term and term not in _STOP_WORDS
    ]


@dataclass(frozen=True, slots=True)
class _SchemaText:
    """A text found in a schema, told apart from a string in a schema's place."""

    text: str


def _schema_text(schema: object) -> list[str]:
    """Schema descriptions and property names, depth first.

    Walks with its own stack, so the depth of a schema is not limited by the
    interpreter's recursion limit.
    """

    parts: list[str] = []
    pending: list[object] = [schema]
    while pending:
        node = pending.pop()
        if isinstance(node, _SchemaText):
            parts.append(node.text)
        elif isinstance(node, Mapping):
            pending.extend(reversed(_schema_children(node)))
    return parts


def _schema_children(schema: Mapping[Any, Any]) -> list[object]:
    """The texts and subschemas of one schema, in reading order."""

    children: list[object] = []
    description = schema.get("description")
    if isinstance(description, str):
        children.append(_SchemaText(description))
    properties = schema.get("properties")
    if isinstance(properties, Mapping):
        for name, subschema in properties.items():
            if isinstance(name, str):
                children.append(_SchemaText(name))
            children.append(subschema)
    children.append(schema.get("items"))
    for keyword in _VARIANT_KEYWORDS:
        variants = schema.get(keyword)
        if isinstance(variants, (list, tuple)):
            children.extend(variants)
    return children


def create_tool_search_document(
    tool: SearchableTool,
    namespace: ToolNamespace | None = None,
) -> ToolSearchDocument:
    """Build the search text of one tool.

    The text is the name, the name with ``_`` as spaces, the description, the
    descriptions and property names of the input schema, and the namespace
    name with its description and instructions.
    """

    parts = [tool.name, tool.name.replace("_", " "), tool.description]
    parts.extend(_schema_text(tool.input_schema))
    if namespace is not None:
        parts.extend(
            (namespace.name, namespace.description or "", namespace.instructions or "")
        )
    text = " ".join(part for part in parts if not _JS_BLANK.fullmatch(part))
    return ToolSearchDocument(name=tool.name, text=text)


class Bm25Ranker:
    """Okapi BM25 with the usual parameters. Ties keep document order."""

    def __init__(self, *, k1: float = 1.2, b: float = 0.75) -> None:
        self._k1 = k1
        self._b = b

    def rank(
        self,
        query: str,
        documents: Sequence[ToolSearchDocument],
        limit: int,
    ) -> list[ToolSearchMatch]:
        query_terms = list(dict.fromkeys(tokenize(query)))
        if not query_terms or not documents or limit <= 0:
            return []
        term_counts = [Counter(tokenize(document.text)) for document in documents]
        lengths = [sum(counts.values()) for counts in term_counts]
        average_length = sum(lengths) / len(documents) or 1
        idf = {
            term: _inverse_document_frequency(
                len(documents), sum(1 for counts in term_counts if term in counts)
            )
            for term in query_terms
        }
        matches: list[ToolSearchMatch] = []
        for document, counts, length in zip(
            documents, term_counts, lengths, strict=True
        ):
            norm = self._k1 * (1 - self._b + self._b * length / average_length)
            score = 0.0
            for term in query_terms:
                count = counts.get(term, 0)
                if count:
                    score += idf[term] * (count * (self._k1 + 1) / (count + norm))
            if score > 0:
                matches.append(ToolSearchMatch(name=document.name, score=score))
        matches.sort(key=lambda match: match.score, reverse=True)
        return matches[:limit]


def _inverse_document_frequency(document_count: int, frequency: int) -> float:
    return math.log(1 + (document_count - frequency + 0.5) / (frequency + 0.5))


class LoadableTools(Protocol):
    """The session's tools as `tool_search` needs them.

    `NativeToolCapabilities` fits.
    """

    def loadable_tools(self) -> tuple[ToolDefinition, ...]:
        """The tools a search may load, in registration order."""
        ...

    def load_tools(self, names: Sequence[str], /) -> tuple[str, ...]:
        """Activate the named loadable tools and return the names it loaded."""
        ...


def _reaches_all(_arguments: Mapping[str, Any]) -> ToolReach:
    # A query can match any tool that is not declared yet.
    return "all"


def tool_search_definition() -> ToolDefinition:
    """The `tool_search` declaration (Pi ``createToolSearchToolDefinition``).

    The schema is Pi's, with ``limit`` as a JSON number, which the strict
    subset does not have; it is therefore a foreign schema.
    """

    return ToolDefinition(
        name=TOOL_SEARCH_TOOL_NAME,
        description=TOOL_SEARCH_DESCRIPTION,
        prompt_snippet=BUILTIN_TOOL_PROMPTS[TOOL_SEARCH_TOOL_NAME].snippet,
        prompt_guidelines=BUILTIN_TOOL_PROMPTS[TOOL_SEARCH_TOOL_NAME].guidelines,
        input_schema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query for deferred tools.",
                },
                "limit": {
                    "type": "number",
                    "description": (
                        "Maximum number of tools to return. Defaults to "
                        f"{DEFAULT_TOOL_SEARCH_LIMIT}."
                    ),
                },
            },
            "required": ["query"],
        },
        # Searching is not something scripts need; it changes what the model
        # sees.
        exposure="model-only",
        reaches_tools=_reaches_all,
        default_active=False,
        schema_mode="foreign",
    )


def _positive_integer(value: object) -> int | None:
    """JavaScript ``Number.isInteger(value) && value > 0``, as an int."""

    if type(value) is float and value.is_integer():
        value = int(value)
    if type(value) is int and value > 0:
        return value
    return None


def _loaded_text(tools: Sequence[ToolDefinition]) -> str:
    """The result text; lines the result cannot hold are counted, not listed."""

    if not tools:
        return "No matching tools found."
    plural = "" if len(tools) == 1 else "s"
    text = f"Loaded {len(tools)} tool{plural}. They are available from your next call:"
    for listed, tool in enumerate(tools):
        description = _JS_BLANK_ENDS.sub("", tool.description)
        line = f"\n- {tool.name}: {_LINE_BREAK.split(description, maxsplit=1)[0]}"
        rest = f"\n(and {len(tools) - listed} more)"
        if (
            len(text) + len(line) + len(rest)
            > ToolExecutionResult.OUTPUT_TEXT_MAX_LENGTH
        ):
            return text + rest
        text += line
    return text


class ToolSearchTool:
    """The built-in `tool_search` tool.

    It ranks the loadable tools of the session for the query and loads the
    matches. Without `tools` it finds nothing.
    """

    def __init__(
        self,
        tools: LoadableTools | None = None,
        *,
        ranker: ToolRanker | None = None,
    ) -> None:
        self._tools = tools
        self._ranker = ranker if ranker is not None else Bm25Ranker()

    @property
    def definition(self) -> ToolDefinition:
        return tool_search_definition()

    def invoke(self, request: ToolRequest, context: ToolContext) -> ToolExecutionResult:
        query = request.arguments.get("query")
        if not isinstance(query, str) or _JS_BLANK.fullmatch(query):
            return self._result(request, "query must not be empty", is_error=True)
        limit = _positive_integer(
            request.arguments.get("limit", DEFAULT_TOOL_SEARCH_LIMIT)
        )
        if limit is None:
            return self._result(
                request, "limit must be a positive integer", is_error=True
            )
        loaded = self._search_and_load(query, limit)
        return self._result(
            request,
            _loaded_text(loaded),
            details={"loaded": [tool.name for tool in loaded]},
        )

    def _search_and_load(self, query: str, limit: int) -> tuple[ToolDefinition, ...]:
        """Rank the loadable tools and activate the matches (Pi ``searchAndLoad``)."""

        if self._tools is None:
            return ()
        candidates = {tool.name: tool for tool in self._tools.loadable_tools()}
        documents = [
            create_tool_search_document(tool, tool.namespace)
            for tool in candidates.values()
        ]
        matches = self._ranker.rank(query, documents, limit)
        if not matches:
            return ()
        loaded = self._tools.load_tools([match.name for match in matches])
        return tuple(candidates[name] for name in loaded if name in candidates)

    @staticmethod
    def _result(
        request: ToolRequest,
        text: str,
        *,
        is_error: bool = False,
        details: Any = NO_TOOL_DETAILS,
    ) -> ToolExecutionResult:
        return ToolExecutionResult(
            tool_request_id=request.tool_request_id,
            output_text=text,
            is_error=is_error,
            provider_correlation_id=request.provider_correlation_id,
            details=details,
        )
