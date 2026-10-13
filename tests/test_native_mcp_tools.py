"""Tests for the adaptation of MCP tools (MCP parity plan S16).

The first six tests mirror the "MCP tools" cases of Pi's
``mcp-extension.test.ts``; each names its case. The rest pins the naming
across tool lists, the definition, and a call through a tool port.
"""

from __future__ import annotations

import base64
import json
import os
import re
import stat
import threading
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import pytest

from pipy_harness.native.agent import AgentToolCall, ProductContent
from pipy_harness.native.agent.tools import ToolExecutor
from pipy_harness.native.codemode.declarations import (
    render_tool_output_type,
    render_tool_signature,
)
from pipy_harness.native.coding.codemode_description import describe_script_call
from pipy_harness.native.mcp.client import McpRequestOptions
from pipy_harness.native.mcp.config import McpServerEntry
from pipy_harness.native.mcp.connection import McpServerConnection
from pipy_harness.native.mcp.protocol import (
    LATEST_PROTOCOL_VERSION,
    JsonRpcMessage,
    McpError,
)
from pipy_harness.native.mcp.tools import (
    MCP_OUTPUT_MAX_BYTES,
    McpToolNames,
    McpToolPort,
    convert_mcp_result,
    create_mcp_result_schema,
    create_mcp_tool_definition,
    create_mcp_tool_name,
    limit_mcp_content,
    mcp_tool_namespace,
    render_model_text,
    save_to_temp_file,
    to_tool_exposure,
)
from pipy_harness.native.mcp.transport import create_in_memory_transport_pair
from pipy_harness.native.tools.base import (
    ToolAnnotations,
    ToolContext,
    ToolExecutionResult,
    ToolNamespace,
    ToolRequest,
    make_tool_request_id,
)
from pipy_harness.native.tools.truncate import truncate_middle

_NAMESPACE = ToolNamespace(name="mcp__docs", aliases=("docs",))
_CONTENT_TYPE = (
    '"content": list[{"data"?: str, "mimeType"?: str, "text"?: str, '
    '"type": "text" | "image" | "audio" | "resource_link" | "resource", '
    'str: Any}], "isError"?: bool,'
)


def _saver(
    saved: list[tuple[str | bytes, str]], path: str = "/tmp/saved"
) -> Callable[[str | bytes, str], str]:
    def save(data: str | bytes, extension: str) -> str:
        saved.append((data, extension))
        return f"{path}{extension}"

    return save


def _failing_saver(data: str | bytes, extension: str) -> str:
    raise OSError("disk full")


# --- Pi "MCP tools" cases ---------------------------------------------------


def test_creates_provider_safe_tool_names() -> None:
    # Pi: "creates provider-safe tool names"
    assert create_mcp_tool_name("docs", "search") == "mcp__docs__search"
    assert (
        create_mcp_tool_name("my-server", "get.item/v2")
        == "mcp__my_server__get_item_v2"
    )
    long = create_mcp_tool_name("server", "x" * 100)
    assert len(long) == 64
    assert re.fullmatch(r"mcp__server__x+_[0-9a-f]{8}", long)
    assert create_mcp_tool_name("server", "x" * 100 + "y") != long
    # Names that sanitize to one already taken by another tool get a hash suffix.
    taken = create_mcp_tool_name("s", "a_b")
    second = create_mcp_tool_name("s", "a-b", lambda name: name == taken)
    assert re.fullmatch(r"mcp__s__a_b_[0-9a-f]{8}", second)


def test_declares_the_call_tool_result_scripts_receive() -> None:
    # Pi: "declares the CallToolResult scripts receive, keeping references in
    # the server's output schema"
    assert render_tool_output_type(create_mcp_result_schema(None)) == (
        f'{{{_CONTENT_TYPE} "structuredContent"?: dict[str, Any], str: Any}}'
    )

    hit = {"type": "object", "properties": {"id": {"type": "string"}}}
    hit["required"] = ["id"]
    schema = create_mcp_result_schema(
        {
            "type": "object",
            "properties": {
                "hits": {"type": "array", "items": {"$ref": "#/$defs/Hit~1v1"}},
                "again": {"$ref": "#/properties/hits"},
                "children": {"type": "array", "items": {"$ref": "#"}},
                # A property named like a keyword whose value is data.
                "default": {"$ref": "#/definitions/Hit"},
                "remote": {"$ref": "https://example.com/schema.json"},
            },
            "required": ["hits"],
            "$defs": {"Hit/v1": {**hit, "default": {"$ref": "#/stays"}}},
            "definitions": {"Hit": hit},
        }
    )
    structured: Any = schema["properties"]
    structured = structured["structuredContent"]
    # Data that looks like a reference is kept as is.
    assert structured["$defs"]["Hit/v1"]["default"] == {"$ref": "#/stays"}
    assert structured["properties"]["again"] == {
        "$ref": "#/properties/structuredContent/properties/hits"
    }
    assert structured["properties"]["children"]["items"] == {
        "$ref": "#/properties/structuredContent"
    }
    assert structured["properties"]["remote"] == {
        "$ref": "https://example.com/schema.json"
    }
    hits = 'list[{"id": str}]'
    inner = (
        f'{{"again"?: {hits}, "children"?: list[Any], "default"?: {{"id": str}}, '
        f'"hits": {hits}, "remote"?: Any}}'
    )
    assert render_tool_output_type(schema) == (
        f'{{{_CONTENT_TYPE} "structuredContent"?: {{"again"?: {hits}, '
        f'"children"?: list[{inner}], "default"?: {{"id": str}}, "hits": {hits}, '
        f'"remote"?: Any}}, str: Any}}'
    )


def test_converts_results_passing_the_call_tool_result_to_scripts() -> None:
    # Pi: "converts results, passing the CallToolResult to scripts and
    # flagging errors"
    blocks = [
        {"type": "resource_link", "uri": "file:///a", "name": "a"},
        {"type": "resource", "resource": {"uri": "file:///b", "text": "b text"}},
        {"type": "audio", "data": "", "mimeType": "audio/wav"},
    ]
    converted = convert_mcp_result(
        "docs",
        "t",
        {"content": blocks, "structuredContent": {"ok": True}, "_meta": {"t": "x"}},
    )
    assert converted.content == [
        {"type": "text", "text": '[Resource file:///a "a"]'},
        {"type": "text", "text": "b text"},
        {"type": "text", "text": "[audio audio/wav omitted]"},
    ]
    assert converted.details == {"server": "docs", "tool": "t"}
    # Scripts get the server's blocks as sent, without `_meta`.
    assert converted.structured_content == {
        "content": blocks,
        "structuredContent": {"ok": True},
    }
    assert converted.is_error is False

    structured_only = convert_mcp_result(
        "docs", "t", {"content": [], "structuredContent": {"n": 1}}
    )
    assert structured_only.content == [{"type": "text", "text": '{\n  "n": 1\n}'}]

    failed = convert_mcp_result(
        "docs", "t", {"content": [{"type": "text", "text": "nope"}], "isError": True}
    )
    assert failed.content == [{"type": "text", "text": "nope"}]
    assert failed.details == {"server": "docs", "tool": "t"}
    assert failed.structured_content == {
        "content": [{"type": "text", "text": "nope"}],
        "isError": True,
    }
    assert failed.is_error is True

    silent = convert_mcp_result("docs", "t", {"content": [], "isError": True})
    assert silent.content == [
        {"type": "text", "text": "MCP tool docs/t returned an error"}
    ]


def test_points_resource_links_to_read_mcp_resource_and_saves_binary_resources() -> (
    None
):
    # Pi: "points resource links to read_mcp_resource and saves binary resources"
    saved: list[tuple[str | bytes, str]] = []
    converted = convert_mcp_result(
        "docs",
        "t",
        {
            "content": [
                {
                    "type": "resource_link",
                    "uri": "docs://guide",
                    "name": "guide",
                    "title": "The Guide",
                    "mimeType": "text/markdown",
                    "size": 2048,
                    "description": "How to use it",
                },
                {
                    "type": "resource",
                    "resource": {
                        "uri": "file:///r/report.pdf",
                        "mimeType": "application/pdf",
                        "blob": "JVBERg==",
                    },
                },
                {
                    "type": "resource",
                    "resource": {
                        "uri": "docs://logo",
                        "mimeType": "image/png",
                        "blob": "AAAA",
                    },
                },
            ]
        },
        save_output=_saver(saved),
        readable_resources=True,
    )
    assert converted.content == [
        {
            "type": "text",
            "text": '[Resource docs://guide "The Guide" (text/markdown, 2.0KB): '
            'How to use it. Read it with read_mcp_resource (server "docs")]',
        },
        {
            "type": "text",
            "text": "[Binary resource file:///r/report.pdf (application/pdf, 4B) "
            "saved to /tmp/saved.pdf]",
        },
        {"type": "image", "data": "AAAA", "mimeType": "image/png"},
    ]
    assert saved == [(b"%PDF", ".pdf")]


def test_cuts_the_middle_of_model_facing_text_over_20kb() -> None:
    # Pi: "cuts the middle of model-facing text over 20KB and keeps the full
    # result for scripts"
    saved: list[tuple[str | bytes, str]] = []
    save = _saver(saved, "/tmp/full")
    full = "\n".join(f"line {index + 1}" for index in range(3000))
    image = {"type": "image", "data": "AAAA", "mimeType": "image/png"}
    result = {"content": [{"type": "text", "text": full}, image]}
    converted = convert_mcp_result("docs", "snapshot", result, save_output=save)
    assert len(converted.content) == 2
    text = converted.content[0]["text"]
    # Codex's format: a header, the start and end of the text, then the file
    # with the full text.
    tokens = -(-len(full) // 4)
    assert text.startswith(
        f"Warning: truncated output (original token count: {tokens})\n"
        "Total output lines: 3000\n\nline 1\nline 2\n"
    )
    assert re.search(r"…\d+ chars truncated…", text)
    assert text.endswith(
        "line 3000\n\n[Full output: /tmp/full.txt (read it with offset/limit)]"
    )
    assert len(text.encode()) < 21 * 1024
    assert converted.content[1] == image
    assert converted.details == {
        "server": "docs",
        "tool": "snapshot",
        "fullOutputPath": "/tmp/full.txt",
    }
    assert saved == [(full, ".txt")]
    assert converted.structured_content == result

    # Text within the limit is not saved.
    convert_mcp_result(
        "docs", "small", {"content": [{"type": "text", "text": "ok"}]}, save_output=save
    )
    assert len(saved) == 1


def test_cuts_multi_byte_text_only_at_character_boundaries() -> None:
    # Pi: "cuts multi-byte text only at character boundaries"
    text = "é" * 20_000 + "end"
    result = truncate_middle(text, 1001)
    assert result.truncated is True
    assert "�" not in result.content
    assert result.content.endswith("end")
    head, tail = re.split(r"…\d+ chars truncated…", result.content)
    assert len(head.encode()) <= 500
    assert len(tail.encode()) <= 501
    assert len(head) + len(tail) + result.removed_chars == len(text)


# --- truncation and spill ---------------------------------------------------


def test_truncate_middle_leaves_text_within_the_limit_alone() -> None:
    result = truncate_middle("a\nb\n", 4)
    assert (result.content, result.truncated, result.removed_chars) == (
        "a\nb\n",
        False,
        0,
    )
    assert (result.total_bytes, result.total_lines) == (4, 2)
    cut = truncate_middle("abcdefghij", 5)
    assert cut.content == "ab…5 chars truncated…hij"
    assert (cut.total_bytes, cut.total_lines, cut.removed_chars) == (10, 1, 5)


def test_truncate_middle_counts_a_lone_surrogate_as_node_writes_it() -> None:
    # JSON allows "\ud800"; Node writes U+FFFD (3 bytes) for it.
    assert truncate_middle("\ud800", 10).total_bytes == 3
    cut = truncate_middle("\ud800" * 10, 6)
    assert cut.content == "�…8 chars truncated…�"


def test_text_at_the_limit_is_kept_and_one_byte_more_is_cut() -> None:
    saved: list[tuple[str | bytes, str]] = []
    exact = [{"type": "text", "text": "x" * MCP_OUTPUT_MAX_BYTES}]
    assert limit_mcp_content(exact, _saver(saved)) == (exact, None)
    # The limit counts all text blocks joined by newlines, not images.
    parts = [
        {"type": "text", "text": "x" * (MCP_OUTPUT_MAX_BYTES // 2)},
        {"type": "image", "data": "A" * 50_000, "mimeType": "image/png"},
        {"type": "text", "text": "y" * (MCP_OUTPUT_MAX_BYTES // 2)},
    ]
    content, path = limit_mcp_content(parts, _saver(saved))
    assert path == "/tmp/saved.txt"
    assert [block["type"] for block in content] == ["text", "image"]
    assert content[0]["text"].startswith(
        f"Warning: truncated output (original token count: {MCP_OUTPUT_MAX_BYTES // 4 + 1})\n"
        "Total output lines: 2\n\n"
    )
    assert "…1 chars truncated…" in content[0]["text"]
    assert saved == [(parts[0]["text"] + "\n" + parts[2]["text"], ".txt")]


def test_a_failed_save_is_reported_and_publishes_no_path() -> None:
    converted = convert_mcp_result(
        "docs",
        "t",
        {"content": [{"type": "text", "text": "x" * 30_000}]},
        save_output=_failing_saver,
    )
    assert converted.content[0]["text"].endswith(
        "\n\n[Could not save the full output: disk full]"
    )
    assert converted.details == {"server": "docs", "tool": "t"}


def test_the_spill_file_is_new_and_readable_only_by_the_user(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path))
    full = "ü" * 30_000
    converted = convert_mcp_result(
        "docs", "t", {"content": [{"type": "text", "text": full}]}
    )
    path = Path(converted.details["fullOutputPath"])
    assert path.parent == tmp_path
    assert re.fullmatch(r"pipy-mcp-[0-9a-f]{16}\.txt", path.name)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert path.read_text(encoding="utf-8") == full
    assert (
        f"[Full output: {path} (read it with offset/limit)]"
        in (converted.content[0]["text"])
    )

    binary = Path(save_to_temp_file(b"\x00\x01", ".bin"))
    assert binary.read_bytes() == b"\x00\x01"
    assert stat.S_IMODE(binary.stat().st_mode) == 0o600
    assert binary != path


def test_save_never_writes_through_an_existing_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path))
    monkeypatch.setattr("secrets.token_hex", lambda size: "ab" * size)
    target = tmp_path / "elsewhere"
    (tmp_path / "pipy-mcp-abababababababab.txt").symlink_to(target)
    with pytest.raises(FileExistsError):
        save_to_temp_file("secret", ".txt")
    assert not target.exists()


# --- content blocks ---------------------------------------------------------


def _blob(data: bytes) -> str:
    return base64.b64encode(data).decode()


def _resource(**fields: object) -> dict[str, object]:
    return {"type": "resource", "resource": fields}


def test_resource_link_parts_appear_only_when_present() -> None:
    def link(readable: bool = False, **fields: object) -> str:
        block = {"type": "resource_link", "uri": "docs://a", "name": "a", **fields}
        converted = convert_mcp_result(
            "my docs", "t", {"content": [block]}, readable_resources=readable
        )
        return converted.content[0]["text"]

    assert link() == '[Resource docs://a "a"]'
    assert link(size=0) == '[Resource docs://a "a" (0B)]'
    assert link(size=1280.0, mimeType="") == '[Resource docs://a "a" (1.3KB)]'
    assert link(description="") == '[Resource docs://a "a"]'
    assert link(readable=True) == (
        '[Resource docs://a "a". Read it with read_mcp_resource (server "my docs")]'
    )
    # Without the fields the block type requires it is not a link pipy can show.
    broken = convert_mcp_result(
        "docs", "t", {"content": [{"type": "resource_link", "uri": "docs://a"}]}
    )
    assert broken.content == [
        {"type": "text", "text": "[unsupported MCP content resource_link]"}
    ]


@pytest.mark.parametrize(
    "mime_type",
    ["text/csv", "TEXT/Plain; charset=utf-8", "application/json", " image/svg+xml "],
)
def test_a_blob_with_a_text_mime_type_is_decoded_as_text(mime_type: str) -> None:
    saved: list[tuple[str | bytes, str]] = []
    block = _resource(uri="docs://a", mimeType=mime_type, blob=_blob("zäh".encode()))
    if mime_type.strip().startswith("image/"):
        block = _resource(
            uri="docs://a", mimeType="application/x+xml", blob=_blob("zäh".encode())
        )
    converted = convert_mcp_result(
        "docs", "t", {"content": [block]}, save_output=_saver(saved)
    )
    assert converted.content == [{"type": "text", "text": "zäh"}]
    assert saved == []


@pytest.mark.parametrize(
    ("uri", "extension"),
    [
        ("file:///r/report.pdf", ".pdf"),
        ("https://example.com/a/data.tar.gz?download=1#top", ".gz"),
        ("docs://items/archive", ".bin"),
        ("relative/name.verylongextension", ".bin"),
        ("relative/name.JSON5", ".JSON5"),
        ("file:///r/odd.p-f", ".bin"),
    ],
)
def test_a_binary_blob_is_saved_with_the_extension_of_its_uri(
    uri: str, extension: str
) -> None:
    saved: list[tuple[str | bytes, str]] = []
    converted = convert_mcp_result(
        "docs",
        "t",
        {"content": [_resource(uri=uri, blob=_blob(b"\x00" * 2048))]},
        save_output=_saver(saved),
    )
    assert converted.content == [
        {
            "type": "text",
            "text": f"[Binary resource {uri} (unknown type, 2.0KB) "
            f"saved to /tmp/saved{extension}]",
        }
    ]
    assert saved == [(b"\x00" * 2048, extension)]


def test_a_binary_blob_that_cannot_be_saved_says_why() -> None:
    block = _resource(uri="docs://a", mimeType="application/zip", blob="UEs=")
    converted = convert_mcp_result(
        "docs", "t", {"content": [block]}, save_output=_failing_saver
    )
    assert converted.content == [
        {
            "type": "text",
            "text": "[Binary resource docs://a (application/zip, 2B) "
            "could not be saved: disk full]",
        }
    ]


def test_base64_is_decoded_as_leniently_as_node_does() -> None:
    saved: list[tuple[str | bytes, str]] = []
    blocks = [
        _resource(uri="a", blob="JV BE\nRg"),  # whitespace, no padding
        _resource(uri="b", blob="-_-_"),  # the URL-safe alphabet
        _resource(uri="c", blob="JVBERg==ignored"),
    ]
    convert_mcp_result("docs", "t", {"content": blocks}, save_output=_saver(saved))
    assert [data for data, _ in saved] == [b"%PDF", b"\xfb\xff\xbf", b"%PDF"]


def test_text_from_a_server_is_made_encodable() -> None:
    converted = convert_mcp_result(
        "docs", "t", {"content": [{"type": "text", "text": "a\ud800b"}]}
    )
    assert converted.content == [{"type": "text", "text": "a�b"}]
    # A script still gets what the server sent.
    assert converted.structured_content["content"] == [
        {"type": "text", "text": "a\ud800b"}
    ]


@pytest.mark.parametrize(
    ("value", "is_error"),
    [(True, True), ("yes", True), (1, True), ({}, True), (False, False), (0, False)],
)
def test_is_error_follows_javascript_truthiness(value: object, is_error: bool) -> None:
    converted = convert_mcp_result("docs", "t", {"content": [], "isError": value})
    assert converted.is_error is is_error
    assert converted.structured_content == {"content": [], "isError": value}


def test_images_are_placeholders_in_the_text_of_a_result() -> None:
    content = [
        {"type": "text", "text": "before"},
        {"type": "image", "data": "AAAA", "mimeType": "image/png"},
        {"type": "text", "text": "after"},
    ]
    assert render_model_text(content) == "before\n[image image/png omitted]\nafter"
    many = [{"type": "image", "data": "", "mimeType": "image/png"}] * 5000
    text = render_model_text(many)
    assert len(text) <= ToolExecutionResult.OUTPUT_TEXT_MAX_LENGTH
    assert text.endswith("\n[pipy: MCP tool output truncated]")


# --- names across tool lists ------------------------------------------------


def test_colliding_tools_all_get_the_hash_suffix_in_any_order() -> None:
    forward = McpToolNames().assign("s", ["a-b", "a_b", "plain"])
    backward = McpToolNames().assign("s", ["plain", "a_b", "a-b"])
    assert forward[2] == backward[0] == "mcp__s__plain"
    assert forward[0] == backward[2] != forward[1] == backward[1]
    assert re.fullmatch(r"mcp__s__a_b_[0-9a-f]{8}", forward[0])
    assert re.fullmatch(r"mcp__s__a_b_[0-9a-f]{8}", forward[1])


def test_a_refresh_that_adds_a_colliding_tool_renames_both() -> None:
    names = McpToolNames()
    assert names.assign("s", ["a_b"]) == ["mcp__s__a_b"]
    both = names.assign("s", ["a_b", "a-b"])
    assert "mcp__s__a_b" not in both
    assert len(set(both)) == 2
    # When the collision is gone the remaining tool has its plain name again.
    assert names.assign("s", ["a_b"]) == ["mcp__s__a_b"]
    assert names.owner("mcp__s__a_b") == ("s", "a_b")
    assert names.owner(both[1]) == ("s", "a-b")
    assert names.owner("mcp__s__other") is None


def test_a_name_once_owned_by_another_tool_is_not_reused() -> None:
    names = McpToolNames()
    assert names.assign("s", ["a_b"]) == ["mcp__s__a_b"]
    # The first tool is gone; the other one would now have the name to itself.
    (renamed,) = names.assign("s", ["a-b"])
    assert re.fullmatch(r"mcp__s__a_b_[0-9a-f]{8}", renamed)
    # Also across servers: `a` + `b__c` and `a__b` + `c` sanitize alike.
    assert names.assign("a", ["b__c"]) == ["mcp__a__b__c"]
    (other,) = names.assign("a__b", ["c"])
    assert re.fullmatch(r"mcp__a__b__c_[0-9a-f]{8}", other)
    assert names.assign("a", ["b__c"]) == ["mcp__a__b__c"]


def test_a_tool_listed_twice_gets_two_names() -> None:
    # As in Pi: the second entry finds the plain name in use by this list.
    first, second = McpToolNames().assign("s", ["t", "t"])
    assert first == "mcp__s__t"
    assert re.fullmatch(r"mcp__s__t_[0-9a-f]{8}", second)


def test_a_suffixed_name_in_use_is_not_handed_over() -> None:
    # A tool may be called just what another tool's suffixed name would be.
    names = McpToolNames()
    assert names.assign("s", ["a_b_ed3867d6"]) == ["mcp__s__a_b_ed3867d6"]
    dashed, plain = names.assign("s", ["a-b", "a_b"])
    assert names.owner("mcp__s__a_b_ed3867d6") == ("s", "a_b_ed3867d6")
    assert re.fullmatch(r"mcp__s__a_b_[0-9a-f]{8}", dashed)
    assert len({dashed, plain, "mcp__s__a_b_ed3867d6"}) == 3
    # The replacement is stable across lists.
    assert names.assign("s", ["a_b", "a-b"]) == [plain, dashed]

    together = McpToolNames().assign("s", ["a_b_ed3867d6", "a-b", "a_b"])
    assert together[0] == "mcp__s__a_b_ed3867d6"
    assert len(set(together)) == 3
    reverse = McpToolNames().assign("s", ["a_b", "a-b", "a_b_ed3867d6"])
    assert len(set(reverse)) == 3

    # A tool listed three times gets three names.
    thrice = McpToolNames().assign("s", ["t", "t", "t"])
    assert len(set(thrice)) == 3
    assert all(len(name) <= 64 for name in thrice)

    # Long names are suffixed without a look at the table in Pi; here too
    # the name stays with its first owner.
    long = McpToolNames()
    (first,) = long.assign("s", ["x" * 100])
    assert long.assign("s", [first.removeprefix("mcp__s__")]) != [first]
    assert long.owner(first) == ("s", "x" * 100)


def test_names_count_characters_as_pi_counts_utf16_units() -> None:
    # Pi replaces each UTF-16 unit, so an emoji is two underscores.
    assert create_mcp_tool_name("s", "a😀b") == "mcp__s__a__b"
    assert create_mcp_tool_name("s", "ünï") == "mcp__s___n_"
    # The hash is over the UTF-8 bytes of `<server>\0<tool>`.
    assert create_mcp_tool_name("s", "x" * 100).endswith("_244cf40d")
    # A lone surrogate is hashed as Node encodes it, as U+FFFD.
    assert create_mcp_tool_name("s", "\ud800" * 100).endswith("_8ca955c3")
    assert len(create_mcp_tool_name("s", "\ud800" * 100)) == 64


# --- the definition ---------------------------------------------------------


def _definition(tool: Mapping[str, object], exposure: str = "codemode") -> Any:
    return create_mcp_tool_definition(
        server="docs",
        tool={"inputSchema": {"type": "object"}, **tool},
        name=create_mcp_tool_name("docs", str(tool["name"])),
        exposure=exposure,
        namespace=_NAMESPACE,
    )


def test_description_falls_back_to_the_title_then_to_a_generic_text() -> None:
    assert _definition({"name": "t", "description": "  Find.\n"}).description == "Find."
    titled = {"name": "t", "description": " \n", "title": "Finder"}
    assert _definition(titled).description == "Finder"
    annotated = {"name": "t", "annotations": {"title": "Annotated"}}
    assert _definition(annotated).description == "Annotated"
    assert _definition({"name": "t"}).description == "MCP tool t from server docs"
    # An empty title is a title: the annotation title is not consulted.
    empty = {"name": "t", "title": "", "annotations": {"title": "Annotated"}}
    assert _definition(empty).description == "MCP tool t from server docs"
    # No length cap, unlike pipy's own tools.
    assert len(_definition({"name": "t", "description": "d" * 10_000}).description) == (
        10_000
    )


def test_input_schema_gets_type_and_properties_when_missing() -> None:
    assert _definition({"name": "t", "inputSchema": {}}).input_schema == {
        "type": "object",
        "properties": {},
    }
    schema = {
        "type": "object",
        "properties": {"q": {"anyOf": [{"type": "string"}, {"type": "null"}]}},
        "required": ["q"],
        "$defs": {"X": {"type": "number"}},
        "x-vendor": [1.5],
    }
    definition = _definition({"name": "t", "inputSchema": schema})
    assert definition.input_schema == schema
    assert definition.schema_mode == "foreign"
    assert _definition(
        {"name": "t", "inputSchema": {"type": None, "required": ["a"]}}
    ).input_schema == {"type": "object", "required": ["a"], "properties": {}}
    with pytest.raises(ValueError, match="top-level schema must be an object"):
        _definition({"name": "t", "inputSchema": {"type": "string"}})


def test_output_schema_is_the_result_with_the_tools_schema_inside() -> None:
    plain = _definition({"name": "t"})
    assert plain.output_schema == create_mcp_result_schema(None)
    own = {"type": "object", "properties": {"hits": {"type": "array"}}}
    typed = _definition({"name": "t", "outputSchema": own})
    assert typed.output_schema["properties"]["structuredContent"] == own
    assert typed.output_schema["required"] == ["content"]
    assert typed.output_schema["additionalProperties"] is True
    # CMD-16: the declaration spells the result out, with the schema inline.
    assert render_tool_signature(
        typed.name, typed.input_schema, typed.output_schema
    ) == (
        f"mcp__docs__t(args: dict[str, Any]) -> {{{_CONTENT_TYPE} "
        '"structuredContent"?: {"hits"?: list[Any]}, str: Any}'
    )


def test_a_direct_tool_tells_scripts_what_a_call_resolves_to() -> None:
    # Pi: "tells scripts what directly exposed MCP tools resolve to" (CMD-23)
    hits = {"type": "object", "properties": {"hits": {"type": "array"}}}
    hits["required"] = ["hits"]
    search = _definition({"name": "search", "outputSchema": hits}, "direct")
    assert describe_script_call(search, "tools.mcp__docs__search").endswith(
        "Codemode: `tools.mcp__docs__search(args)` resolves to "
        "`{ content, structuredContent?: { hits }, isError? }`."
    )
    fail = _definition({"name": "fail"}, "direct")
    assert describe_script_call(fail, "tools.mcp__docs__fail").endswith(
        "Codemode: `tools.mcp__docs__fail(args)` resolves to "
        "`{ content, structuredContent?, isError? }`."
    )


def test_only_the_boolean_hints_become_annotations() -> None:
    annotations = {
        "title": "T",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": "yes",
        "openWorldHint": None,
        "vendorHint": True,
    }
    assert _definition({"name": "t", "annotations": annotations}).annotations == (
        ToolAnnotations(read_only_hint=True, destructive_hint=False)
    )
    assert _definition({"name": "t", "annotations": {"title": "T"}}).annotations is None
    assert _definition({"name": "t"}).annotations is None


@pytest.mark.parametrize(
    ("exposure", "tool_exposure"),
    [
        ("codemode", "deferred"),
        ("deferred", "deferred"),
        ("direct", "direct"),
        ("hidden", "hidden"),
    ],
)
def test_mcp_exposure_maps_to_tool_exposure(exposure: str, tool_exposure: str) -> None:
    assert to_tool_exposure(exposure) == tool_exposure
    assert _definition({"name": "t"}, exposure).exposure == tool_exposure


def test_an_unknown_exposure_is_refused() -> None:
    with pytest.raises(ValueError, match="Unknown MCP exposure"):
        to_tool_exposure("model-only")


def test_namespace_carries_alias_description_and_instructions() -> None:
    assert mcp_tool_namespace(
        "dev-radius", description="  Radius docs \n", instructions="Use search first."
    ) == ToolNamespace(
        name="mcp__dev_radius",
        aliases=("dev-radius",),
        description="Radius docs",
        instructions="Use search first.",
    )
    bare = mcp_tool_namespace("docs", description="  ", instructions=None)
    assert bare == ToolNamespace(name="mcp__docs", aliases=("docs",))
    definition = _definition({"name": "t"})
    assert definition.namespace is _NAMESPACE


# --- a call through the port ------------------------------------------------


class _Caller:
    """Stands in for a connection: records calls and answers from a function."""

    def __init__(self, answer: Callable[[McpRequestOptions], dict[str, Any]]) -> None:
        self.calls: list[tuple[str, dict[str, Any], McpRequestOptions]] = []
        self._answer = answer

    def call_tool(
        self,
        name: str,
        arguments: Mapping[str, object] | None = None,
        options: McpRequestOptions | None = None,
    ) -> dict[str, Any]:
        assert options is not None
        self.calls.append((name, dict(arguments or {}), options))
        return self._answer(options)


def _port(caller: Any, **overrides: Any) -> McpToolPort:
    options: dict[str, Any] = {
        "server": "docs",
        "tool": {
            "name": "get.item",
            "description": "Get one item.",
            "inputSchema": {
                "type": "object",
                "properties": {"id": {"type": "number"}},
                "required": ["id"],
            },
        },
        "name": "mcp__docs__get_item",
        "exposure": "codemode",
        "namespace": _NAMESPACE,
        "timeout_ms": 4000.0,
        "get_caller": lambda: caller,
    }
    options.update(overrides)
    return McpToolPort(**options)


def test_a_port_has_the_title_of_its_calls() -> None:
    # Pi `createMcpToolDefinition`: label `<server>/<tool>`, the tool's name
    # as its server lists it.
    assert _port(object()).label == "docs/get.item"
    assert _port(object(), server="my-docs").label == "my-docs/get.item"


def _invoke(
    port: McpToolPort,
    arguments: dict[str, Any],
    *,
    sink: Callable[[str], None] | None = None,
    cancel_event: threading.Event | None = None,
) -> ToolExecutionResult:
    return port.invoke(
        ToolRequest(
            tool_request_id=make_tool_request_id(),
            tool_name=port.definition.name,
            arguments=arguments,
            provider_correlation_id="call-1",
        ),
        ToolContext(
            workspace_root=Path.cwd(), output_sink=sink, cancel_event=cancel_event
        ),
    )


def test_a_call_sends_arguments_timeout_and_cancellation() -> None:
    caller = _Caller(lambda options: {"content": [{"type": "text", "text": "item 7"}]})
    port = _port(caller)
    cancel_event = threading.Event()
    result = _invoke(port, {"id": 7}, cancel_event=cancel_event)
    ((name, arguments, options),) = caller.calls
    # The server's own tool name, not the pipy name.
    assert (name, arguments) == ("get.item", {"id": 7})
    assert options.timeout_ms == 4000.0
    assert options.cancel_event is cancel_event
    assert result.output_text == "item 7"
    assert result.is_error is False
    assert result.provider_correlation_id == "call-1"
    assert result.details == {"server": "docs", "tool": "get.item"}
    assert result.structured_content == {
        "content": [{"type": "text", "text": "item 7"}]
    }
    assert (port.server, port.tool) == ("docs", "get.item")


def test_progress_notifications_become_live_updates() -> None:
    def answer(options: McpRequestOptions) -> dict[str, Any]:
        assert options.on_progress is not None
        options.on_progress({"progressToken": 1, "progress": 1})
        options.on_progress({"progressToken": 1, "progress": 2.0, "total": 4})
        options.on_progress({"progressToken": 1, "progress": 0.5, "total": 1.5})
        options.on_progress(
            {"progressToken": 1, "progress": 3, "total": 4, "message": "indexing"}
        )
        return {"content": []}

    updates: list[str] = []
    _invoke(_port(_Caller(answer)), {"id": 1}, sink=updates.append)
    assert updates == [
        "Progress 1\n",
        "Progress 2/4\n",
        "Progress 0.5/1.5\n",
        "indexing\n",
    ]
    # Without a place to show them, updates are dropped.
    assert _invoke(_port(_Caller(answer)), {"id": 1}).output_text == ""


def test_an_error_result_is_an_error_that_keeps_the_result_for_scripts() -> None:
    answer = {"content": [], "structuredContent": {"code": 404}, "isError": True}
    result = _invoke(_port(_Caller(lambda options: answer)), {"id": 1})
    assert result.is_error is True
    assert result.output_text == '{\n  "code": 404\n}'
    # CMD-05: with the declared output schema this is what a script call
    # resolves to, error or not.
    assert result.structured_content == answer


def test_what_a_call_raises_is_the_error_result() -> None:
    def refuse() -> Any:
        raise RuntimeError('MCP server "docs" is disabled.')

    result = _invoke(_port(None, get_caller=refuse), {"id": 1})
    assert result.is_error is True
    assert result.output_text == 'MCP server "docs" is disabled.'
    assert result.structured_content is None
    assert result.details == {"server": "docs", "tool": "get.item"}

    def fail(options: McpRequestOptions) -> dict[str, Any]:
        raise McpError(-32000, "x" * 100_000)

    long = _invoke(_port(_Caller(fail)), {"id": 1})
    assert long.is_error is True
    assert "chars truncated…" in long.output_text
    assert len(long.output_text.encode()) < 21 * 1024

    def nameless(options: McpRequestOptions) -> dict[str, Any]:
        raise TimeoutError

    assert _invoke(_port(_Caller(nameless)), {"id": 1}).output_text == "TimeoutError"


def test_resource_links_name_the_read_tool_when_resources_are_reachable() -> None:
    link = {"type": "resource_link", "uri": "docs://a", "name": "a"}
    caller = _Caller(lambda options: {"content": [link]})
    readable = [False]
    port = _port(caller, readable_resources=lambda: readable[0])
    assert _invoke(port, {"id": 1}).output_text == '[Resource docs://a "a"]'
    # Asked at the time of the call.
    readable[0] = True
    assert _invoke(port, {"id": 1}).output_text == (
        '[Resource docs://a "a". Read it with read_mcp_resource (server "docs")]'
    )


def test_a_long_result_is_cut_for_the_model_and_whole_for_scripts() -> None:
    saved: list[tuple[str | bytes, str]] = []
    full = "\n".join(f"line {index + 1}" for index in range(3000))
    image = {"type": "image", "data": "AAAA", "mimeType": "image/png"}
    answer = {"content": [{"type": "text", "text": full}, image]}
    port = _port(_Caller(lambda options: answer), save_output=_saver(saved))
    result = _invoke(port, {"id": 1})
    assert result.output_text.startswith("Warning: truncated output")
    assert result.output_text.endswith(
        "[Full output: /tmp/saved.txt (read it with offset/limit)]\n"
        "[image image/png omitted]"
    )
    assert result.details == {
        "server": "docs",
        "tool": "get.item",
        "fullOutputPath": "/tmp/saved.txt",
    }
    assert result.structured_content == answer


def test_the_executor_validates_arguments_against_the_servers_schema() -> None:
    caller = _Caller(lambda options: {"content": [{"type": "text", "text": "ok"}]})
    port = _port(caller)
    executor = ToolExecutor({port.definition.name: port})
    context = ToolContext(workspace_root=Path.cwd())

    def call(arguments: dict[str, Any]) -> Any:
        return executor.execute(
            AgentToolCall(
                tool_name=port.definition.name,
                arguments_json=ProductContent(json.dumps(arguments)),
                provider_correlation_id="call-1",
            ),
            context,
        ).result

    refused = call({"id": "seven"})
    assert refused.is_error is True
    assert caller.calls == []
    accepted = call({"id": 7.5, "extra": True})
    assert accepted.is_error is False
    assert accepted.structured_content == {"content": [{"type": "text", "text": "ok"}]}
    # Undeclared arguments pass through, as the server's schema allows.
    assert caller.calls[0][1] == {"id": 7.5, "extra": True}


# --- a call to a connected server --------------------------------------------


class _Server:
    """An in-memory server with one tool that reports progress before it answers."""

    def __init__(self) -> None:
        self.client_end, self.end = create_in_memory_transport_pair()
        self.calls: list[JsonRpcMessage] = []
        self.cancelled = threading.Event()
        self.hold = False
        self.end.on_message(self._on_message)
        self.end.start()

    def _answer(self, message: JsonRpcMessage, result: object) -> None:
        self.end.send({"jsonrpc": "2.0", "id": message["id"], "result": result})

    def _on_message(self, message: JsonRpcMessage) -> None:
        method = message.get("method")
        if method == "initialize":
            self._answer(
                message,
                {
                    "protocolVersion": LATEST_PROTOCOL_VERSION,
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "fake", "version": "1.0.0"},
                },
            )
        elif method == "tools/list":
            self._answer(message, {"tools": [_TOOL]})
        elif method == "notifications/cancelled":
            self.cancelled.set()
        elif method == "tools/call":
            self.calls.append(message)
            params: Any = message["params"]
            self.end.send(
                {
                    "jsonrpc": "2.0",
                    "method": "notifications/progress",
                    "params": {
                        "progressToken": params["_meta"]["progressToken"],
                        "progress": 1,
                        "total": 2,
                    },
                }
            )
            if not self.hold:
                self._answer(
                    message,
                    {
                        "content": [{"type": "text", "text": "found"}],
                        "structuredContent": {"hits": [params["arguments"]["q"]]},
                        "_meta": {"trace": "x"},
                    },
                )


_TOOL: dict[str, Any] = {
    "name": "search",
    "description": "Search the docs.",
    "inputSchema": {"type": "object", "properties": {"q": {"type": "string"}}},
    "outputSchema": {"type": "object", "properties": {"hits": {"type": "array"}}},
    "annotations": {"readOnlyHint": True},
}


def _connected_port(server: _Server) -> tuple[McpServerConnection, McpToolPort]:
    connection = McpServerConnection(
        entry=McpServerEntry(
            name="docs", config={"command": "unused", "timeout": 30}, source="test"
        ),
        cwd=os.getcwd(),
        client_version="1.2.3",
        create_transport=lambda entry, cwd, auth: server.client_end,
        on_tools=lambda connection: None,
    )
    connection.get_client()
    (tool,) = connection.tools
    (name,) = McpToolNames().assign("docs", [str(tool["name"])])
    port = McpToolPort(
        server="docs",
        tool=tool,
        name=name,
        exposure="codemode",
        namespace=mcp_tool_namespace("docs", instructions=connection.instructions),
        timeout_ms=connection.timeout_ms,
        get_caller=lambda: connection,
    )
    return connection, port


def test_a_tool_of_a_connected_server_is_called_through_its_port() -> None:
    server = _Server()
    connection, port = _connected_port(server)
    try:
        definition = port.definition
        assert definition.name == "mcp__docs__search"
        assert definition.exposure == "deferred"
        assert definition.annotations == ToolAnnotations(read_only_hint=True)
        updates: list[str] = []
        result = _invoke(port, {"q": "radius"}, sink=updates.append)
        assert result.output_text == "found"
        assert result.structured_content == {
            "content": [{"type": "text", "text": "found"}],
            "structuredContent": {"hits": ["radius"]},
        }
        assert updates == ["Progress 1/2\n"]
        params: Any = server.calls[0]["params"]
        assert (params["name"], params["arguments"]) == ("search", {"q": "radius"})
    finally:
        connection.close()


def test_cancelling_a_call_ends_it_and_tells_the_server() -> None:
    server = _Server()
    server.hold = True
    connection, port = _connected_port(server)
    try:
        cancel_event = threading.Event()
        updates: list[str] = []

        def sink(chunk: str) -> None:
            updates.append(chunk)
            cancel_event.set()

        result = _invoke(port, {"q": "radius"}, sink=sink, cancel_event=cancel_event)
        assert result.is_error is True
        assert result.output_text == "MCP request aborted"
        assert server.cancelled.wait(10)
        assert updates == ["Progress 1/2\n"]
    finally:
        connection.close()
