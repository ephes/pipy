"""Tool search core (MCP parity S06, TSR-06 to TSR-08).

The first two groups mirror the ``tokenize`` and ``Bm25Ranker`` groups of
Pi's ``packages/coding-agent/test/tool-search.test.ts`` with the same inputs
and expected orders; each test names its Pi case.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from types import MappingProxyType
from typing import Any

import pytest

from pipy_harness.native.tool_search import (
    Bm25Ranker,
    ToolRanker,
    ToolSearchDocument,
    ToolSearchMatch,
    create_tool_search_document,
    stem,
    tokenize,
)
from pipy_harness.native.tools.base import ToolDefinition, ToolInfo, ToolNamespace


def _tool(
    name: str,
    description: str,
    properties: Mapping[str, Any] | None = None,
    schema_description: str | None = None,
) -> ToolInfo:
    schema: dict[str, Any] = {"type": "object", "properties": properties or {}}
    if schema_description is not None:
        schema["description"] = schema_description
    return ToolInfo(
        name=name,
        description=description,
        input_schema=schema,
        exposure="deferred",
    )


def _names(matches: list[ToolSearchMatch]) -> list[str]:
    return [match.name for match in matches]


# --- Pi: describe("tokenize") ----------------------------------------------


def test_tokenize_splits_names_drops_stop_words_and_folds_plurals() -> None:
    # Pi: "splits camelCase and snake_case, drops stop words, and folds plurals"
    assert tokenize("listIssues for the GitHub_repo") == [
        "list",
        "issue",
        "git",
        "hub",
        "repo",
    ]
    assert tokenize("searches queries HTTPServer") == [
        "search",
        "query",
        "http",
        "server",
    ]


# --- Pi: describe("Bm25Ranker") --------------------------------------------

_TOOLS = [
    _tool(
        "mcp__github__list_issues",
        "List issues in a repository.",
        {"state": {"type": "string", "description": "open or closed"}},
    ),
    _tool("mcp__github__create_pull_request", "Open a pull request."),
    _tool("mcp__linear__search_issues", "Search Linear issues by text."),
    _tool("mcp__docs__search", "Search the documentation."),
]
_DOCUMENTS = [create_tool_search_document(entry) for entry in _TOOLS]


def test_ranks_by_term_relevance_and_respects_the_limit() -> None:
    # Pi: "ranks by term relevance and respects the limit"
    ranker = Bm25Ranker()
    assert _names(ranker.rank("issue", _DOCUMENTS, 8)) == [
        "mcp__linear__search_issues",
        "mcp__github__list_issues",
    ]
    assert (
        ranker.rank("pull requests", _DOCUMENTS, 8)[0].name
        == "mcp__github__create_pull_request"
    )
    assert len(ranker.rank("search", _DOCUMENTS, 1)) == 1
    # Property names and descriptions are searchable.
    assert _names(ranker.rank("closed", _DOCUMENTS, 8)) == ["mcp__github__list_issues"]
    assert _names(ranker.rank("state", _DOCUMENTS, 8)) == ["mcp__github__list_issues"]


def test_returns_nothing_for_unknown_or_empty_queries() -> None:
    # Pi: "returns nothing for unknown or empty queries"
    ranker = Bm25Ranker()
    assert ranker.rank("kubernetes", _DOCUMENTS, 8) == []
    assert ranker.rank("the", _DOCUMENTS, 8) == []
    # Known limit: no synonyms, so "tickets" does not find "issues".
    assert ranker.rank("tickets", _DOCUMENTS, 8) == []


def test_includes_the_namespace_in_the_search_text() -> None:
    # Pi: "includes the namespace in the search text"
    document = create_tool_search_document(
        _tool("mcp__x__run", "Run it."),
        ToolNamespace(name="mcp__x", description="Kubernetes cluster tools"),
    )
    matches = Bm25Ranker().rank("kubernetes", [document], 8)
    assert _names(matches) == ["mcp__x__run"]
    assert matches[0].score > 0


# --- TSR-06: tokenizer ------------------------------------------------------


@pytest.mark.parametrize(
    ("term", "expected"),
    [
        ("queries", "query"),
        ("ties", "tie"),  # too short for the "ies" rule; loses its "s"
        ("matches", "match"),
        ("dishes", "dish"),
        ("classes", "class"),
        ("boxes", "box"),
        ("sizes", "siz"),
        ("axes", "axe"),  # four letters: only the final "s" goes
        ("issues", "issue"),
        ("class", "class"),
        ("bus", "bus"),
        ("gas", "gas"),
        ("https", "http"),
        ("s", "s"),
        ("", ""),
    ],
)
def test_stem_folds_plurals(term: str, expected: str) -> None:
    assert stem(term) == expected


def test_tokenize_drops_each_of_the_21_stop_words() -> None:
    stop_words = (
        "a an and are as at be by for from in is it of on or that the this to with"
    )
    assert len(stop_words.split()) == 21
    assert tokenize(stop_words) == []
    assert tokenize(stop_words.upper()) == []
    # A stop word is dropped as a whole term only, and before plurals fold.
    assert tokenize("ass its bye fors") == ["ass", "its", "bye", "for"]


def test_tokenize_splits_at_case_boundaries_and_non_alphanumerics() -> None:
    assert tokenize("getHTTPResponseCode") == ["get", "http", "response", "code"]
    assert tokenize("v2Beta item2Go") == ["v2", "beta", "item2", "go"]
    assert tokenize("mcp__github__list_issues") == ["mcp", "github", "list", "issue"]
    assert tokenize("a/b.c-d:e  f\ng") == ["b", "c", "d", "e", "f", "g"]
    assert tokenize("ABC") == ["abc"]
    assert tokenize("") == []
    assert tokenize(" \t--__ ") == []


def test_tokenize_treats_non_ascii_letters_as_separators() -> None:
    assert tokenize("Übergröße naïveCafé") == ["bergr", "e", "na", "ve", "caf"]
    # Lower-casing runs before the split, so the Kelvin sign becomes "k".
    assert tokenize("K9") == ["k9"]


# --- TSR-07: ranking --------------------------------------------------------


def _doc(name: str, text: str) -> ToolSearchDocument:
    return ToolSearchDocument(name=name, text=text)


def test_bm25_score_of_a_single_document() -> None:
    # One document: idf is ln(1 + 0.5 / 1.5) and the length norm is k1.
    [match] = Bm25Ranker().rank("alpha", [_doc("t", "alpha alpha beta")], 8)
    expected = math.log(4 / 3) * (2 * 2.2 / (2 + 1.2))
    assert match.name == "t"
    assert match.score == pytest.approx(expected)


def test_bm25_scores_follow_the_okapi_formula() -> None:
    documents = [
        _doc("short", "alpha beta"),
        _doc("long", "alpha gamma gamma gamma delta delta"),
        _doc("none", "gamma"),
    ]
    matches = Bm25Ranker().rank("alpha delta", documents, 8)

    average = (2 + 6 + 1) / 3
    idf_alpha = math.log(1 + (3 - 2 + 0.5) / (2 + 0.5))
    idf_delta = math.log(1 + (3 - 1 + 0.5) / (1 + 0.5))

    def term(count: int, length: int) -> float:
        norm = 1.2 * (1 - 0.75 + 0.75 * length / average)
        return count * 2.2 / (count + norm)

    assert _names(matches) == ["long", "short"]
    assert [match.score for match in matches] == pytest.approx(
        [idf_alpha * term(1, 6) + idf_delta * term(2, 6), idf_alpha * term(1, 2)]
    )


def test_bm25_parameters_can_be_set() -> None:
    documents = [_doc("a", "alpha"), _doc("b", "alpha alpha alpha beta beta beta")]
    assert _names(Bm25Ranker().rank("alpha", documents, 8)) == ["a", "b"]
    # Without length normalization the higher term count wins.
    assert _names(Bm25Ranker(b=0).rank("alpha", documents, 8)) == ["b", "a"]
    # With k1 0 the term count does not matter: a tie, in document order.
    scores = Bm25Ranker(k1=0).rank("alpha", documents, 8)
    assert _names(scores) == ["a", "b"]
    assert scores[0].score == scores[1].score


def test_ties_keep_registration_order() -> None:
    documents = [_doc(name, "run the job") for name in ("c", "a", "b")]
    assert _names(Bm25Ranker().rank("job", documents, 8)) == ["c", "a", "b"]
    assert _names(Bm25Ranker().rank("job", documents, 2)) == ["c", "a"]


def test_a_repeated_query_term_counts_once() -> None:
    documents = [_doc("a", "issue tracker"), _doc("b", "search engine")]
    once = Bm25Ranker().rank("issue", documents, 8)
    assert Bm25Ranker().rank("issue issues Issue", documents, 8) == once


def test_only_matching_documents_are_returned() -> None:
    documents = [_doc("a", "alpha"), _doc("b", "beta"), _doc("c", "")]
    assert _names(Bm25Ranker().rank("alpha beta", documents, 8)) == ["a", "b"]
    # A term every document contains still scores above zero.
    everywhere = [_doc("a", "alpha"), _doc("b", "alpha")]
    assert _names(Bm25Ranker().rank("alpha", everywhere, 8)) == ["a", "b"]


def test_no_documents_or_no_room_gives_nothing() -> None:
    ranker = Bm25Ranker()
    assert ranker.rank("alpha", [], 8) == []
    assert ranker.rank("alpha", [_doc("a", "alpha")], 0) == []
    assert ranker.rank("alpha", [_doc("a", "alpha")], -1) == []
    assert ranker.rank("", [_doc("a", "alpha")], 8) == []
    # Documents without any term: the average length falls back to 1.
    assert ranker.rank("alpha", [_doc("a", ""), _doc("b", "the")], 8) == []


def test_bm25_ranker_is_a_tool_ranker() -> None:
    ranker: ToolRanker = Bm25Ranker()
    assert ranker.rank("alpha", (_doc("a", "alpha"),), 8)[0].name == "a"


# --- TSR-08: search text ----------------------------------------------------


def test_document_text_lists_name_description_and_schema_in_order() -> None:
    document = create_tool_search_document(
        _tool(
            "list_issues",
            "List issues.",
            {
                "state": {"type": "string", "description": "open or closed"},
                "labels": {"type": "array", "items": {"description": "A label."}},
            },
            schema_description="Arguments.",
        )
    )
    assert document == ToolSearchDocument(
        name="list_issues",
        text=(
            "list_issues list issues List issues. Arguments. "
            "state open or closed labels A label."
        ),
    )


def test_document_text_follows_items_and_schema_combinators() -> None:
    schema = {
        "type": "object",
        "properties": {
            "target": {
                "anyOf": [
                    {"description": "first variant"},
                    {"properties": {"inner": {"description": "nested name"}}},
                ],
                "oneOf": [{"description": "second variant"}],
                "allOf": [{"items": {"description": "third variant"}}],
            }
        },
    }
    tool = ToolInfo(name="t", description="d", input_schema=schema, exposure="codemode")
    assert create_tool_search_document(tool).text == (
        "t t d target first variant inner nested name second variant third variant"
    )


def test_document_text_ignores_other_keywords_and_malformed_schema_parts() -> None:
    schema = {
        "type": "object",
        "title": "not searched",
        "description": 7,
        "properties": {
            "a": "not a schema",
            "b": {"description": ["not", "text"], "enum": ["not searched"]},
            "c": {"items": [{"description": "positional items are skipped"}]},
            "d": {"anyOf": {"description": "not a list"}},
        },
        "additionalProperties": {"description": "not searched"},
        "$defs": {"x": {"description": "not searched"}},
        "items": "not a schema",
    }
    tool = ToolInfo(name="t", description="d", input_schema=schema, exposure="deferred")
    assert create_tool_search_document(tool).text == "t t d a b c d"


def test_document_text_reads_frozen_schemas() -> None:
    schema = MappingProxyType(
        {
            "type": "object",
            "properties": MappingProxyType(
                {"q": MappingProxyType({"anyOf": ({"description": "query text"},)})}
            ),
        }
    )
    tool = ToolInfo(name="t", description="d", input_schema=schema, exposure="deferred")
    assert create_tool_search_document(tool).text == "t t d q query text"


def test_document_text_of_a_very_deep_schema() -> None:
    schema: dict[str, Any] = {"description": "bottom"}
    for _ in range(5000):
        schema = {"items": schema}
    tool = ToolInfo(
        name="t",
        description="d",
        input_schema={"type": "object", "properties": {"deep": schema}},
        exposure="deferred",
    )
    assert create_tool_search_document(tool).text == "t t d deep bottom"


def test_document_text_includes_namespace_description_and_instructions() -> None:
    namespace = ToolNamespace(
        name="mcp__k8s",
        aliases=("cluster",),
        description="Kubernetes cluster tools",
        instructions="Call get_pods before delete_pod.",
    )
    document = create_tool_search_document(_tool("mcp__k8s__run", "Run it."), namespace)
    assert document.text == (
        "mcp__k8s__run mcp  k8s  run Run it. mcp__k8s "
        "Kubernetes cluster tools Call get_pods before delete_pod."
    )
    # Aliases are not part of the text.
    assert Bm25Ranker().rank("cluster pods", [document], 8)[0].name == "mcp__k8s__run"
    bare = create_tool_search_document(
        _tool("mcp__k8s__run", "Run it."), ToolNamespace(name="mcp__k8s")
    )
    assert bare.text == "mcp__k8s__run mcp  k8s  run Run it. mcp__k8s"


def test_a_server_description_ranks_that_servers_tools() -> None:
    github = ToolNamespace(name="mcp__gh", description="GitHub issue tracker")
    documents = [
        create_tool_search_document(_tool("mcp__gh__list", "List entries."), github),
        create_tool_search_document(_tool("mcp__fs__list", "List entries.")),
    ]
    assert _names(Bm25Ranker().rank("tracker", documents, 8)) == ["mcp__gh__list"]
    # camelCase splits the text, not only names: "GitHub" is "git" and "hub".
    assert Bm25Ranker().rank("github", documents, 8) == []
    assert _names(Bm25Ranker().rank("gitHub", documents, 8)) == ["mcp__gh__list"]


def test_blank_parts_are_left_out_of_the_text() -> None:
    document = create_tool_search_document(
        _tool("run", " \n\t", {"﻿": {"description": " "}}),
        ToolNamespace(name="ns", description="", instructions="  "),
    )
    assert document.text == "run run ns"
    # A part is kept as it is, not trimmed.
    assert create_tool_search_document(_tool("run", " go ")).text == "run run  go "


def test_a_tool_definition_is_searchable() -> None:
    definition = ToolDefinition(
        name="mcp__docs__search",
        description="Search the documentation.",
        input_schema={
            "type": "object",
            "properties": {"query": {"type": "string", "description": "Words."}},
            "required": ["query"],
        },
        schema_mode="foreign",
        namespace=ToolNamespace(name="mcp__docs"),
    )
    document = create_tool_search_document(definition, definition.namespace)
    assert document.text == (
        "mcp__docs__search mcp  docs  search Search the documentation. "
        "query Words. mcp__docs"
    )
