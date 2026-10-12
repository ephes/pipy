"""Tool discovery: a BM25 ranker over tool metadata.

The port of the ranking half of Pi's
``packages/coding-agent/src/extensions/tool-search/tool.ts``: the tokenizer,
the search text of one tool and the ranker. Codemode's ``search_tools`` and
the ``tool_search`` tool share it.

Standard library only. A schema handed to `create_tool_search_document` is a
JSON value as a tool definition holds it: mappings and sequences without
cycles.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from pipy_harness.native.tools.base import ToolNamespace

__all__ = [
    "Bm25Ranker",
    "SearchableTool",
    "ToolRanker",
    "ToolSearchDocument",
    "ToolSearchMatch",
    "create_tool_search_document",
    "stem",
    "tokenize",
]

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
_JS_BLANK = re.compile("[\t\n\v\f\r    -     　﻿]*")


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
