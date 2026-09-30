"""TOOLS2 helpers: the file mutation queue, tool-row headers, `!` output.

Each follows a Pi source file at pi-mono ``1b347794e``:
``core/tools/file-mutation-queue.ts``, ``core/tools/renderers/*.ts`` with
``render-utils.ts``, and ``core/bash-executor.ts`` with ``utils/ansi.ts``.
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from pipy_harness.native.tool_headers import pi_tool_call_header
from pipy_harness.native.tools import file_mutation_queue as queue_module
from pipy_harness.native.tools.bash_executor import (
    ExecutorOutput,
    sanitize_binary_output,
    strip_ansi,
    utf16_length,
)
from pipy_harness.native.tools.file_mutation_queue import (
    file_mutation_queue,
    mutation_queue_key,
)

# -- file mutation queue -------------------------------------------------------


def test_queue_key_is_the_realpath_or_the_missing_path(tmp_path: Path) -> None:
    real = tmp_path / "real.txt"
    real.write_text("")
    (tmp_path / "link.txt").symlink_to(real)

    assert mutation_queue_key(tmp_path / "link.txt") == os.path.realpath(real)
    missing = tmp_path / "nope" / "x.txt"
    assert mutation_queue_key(missing) == str(missing)
    (tmp_path / "file").write_text("")
    # ENOTDIR (a file in the parent chain) also keys on the path itself.
    assert mutation_queue_key(tmp_path / "file" / "x") == str(tmp_path / "file" / "x")


def test_a_symlink_loop_is_an_error(tmp_path: Path) -> None:
    (tmp_path / "loop").symlink_to(tmp_path / "loop")

    with pytest.raises(OSError):
        mutation_queue_key(tmp_path / "loop")


def test_mutations_of_one_file_run_one_after_another(tmp_path: Path) -> None:
    key = mutation_queue_key(tmp_path / "a.txt")
    events: list[str] = []
    first_inside = threading.Event()

    def first() -> None:
        with file_mutation_queue(key):
            events.append("first-start")
            first_inside.set()
            time.sleep(0.2)
            events.append("first-end")

    def second() -> None:
        first_inside.wait()
        with file_mutation_queue(key):
            events.append("second")

    threads = [threading.Thread(target=first), threading.Thread(target=second)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert events == ["first-start", "first-end", "second"]
    # The entry is dropped once its last holder releases it.
    assert key not in queue_module._entries


def test_different_files_do_not_wait_for_each_other(tmp_path: Path) -> None:
    inside = threading.Event()
    release = threading.Event()

    def hold() -> None:
        with file_mutation_queue(str(tmp_path / "a")):
            inside.set()
            release.wait(5)

    holder = threading.Thread(target=hold)
    holder.start()
    inside.wait()
    with file_mutation_queue(str(tmp_path / "b")):
        pass
    release.set()
    holder.join()


# -- tool-row headers ------------------------------------------------------------


@pytest.mark.parametrize(
    ("tool", "args", "expected"),
    [
        ("grep", {"pattern": "p"}, ("grep", "/p/ in .")),
        (
            "grep",
            {"pattern": "p", "path": "src", "glob": "*.py", "limit": 5},
            ("grep", "/p/ in src (*.py) limit 5"),
        ),
        ("grep", {"pattern": "", "path": "", "glob": ""}, ("grep", "// in .")),
        ("grep", {"pattern": 3, "path": 4}, ("grep", "[invalid arg] in [invalid arg]")),
        ("grep", {"pattern": "p", "limit": None}, ("grep", "/p/ in . limit null")),
        ("find", {"pattern": "*.py"}, ("find", "*.py in .")),
        (
            "find",
            {"pattern": "*.py", "path": "a", "limit": 2},
            ("find", "*.py in a (limit 2)"),
        ),
        ("ls", {}, ("ls", ".")),
        ("ls", {"path": ""}, ("ls", ".")),
        ("ls", {"path": "src", "limit": 10}, ("ls", "src (limit 10)")),
        ("ls", {"path": 1}, ("ls", "[invalid arg]")),
        ("write", {"path": "a.txt", "content": "x"}, ("write", "a.txt")),
        ("write", {}, ("write", "...")),
        ("edit", {"file_path": "b.txt", "path": "a.txt"}, ("edit", "b.txt")),
        ("edit", {"path": None}, ("edit", "...")),
        ("edit", {"path": []}, ("edit", "[invalid arg]")),
    ],
)
def test_headers_match_pis_renderers(
    tool: str, args: dict[str, object], expected: tuple[str, str]
) -> None:
    assert pi_tool_call_header(tool, args) == expected


def test_headers_shorten_the_home_directory(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", "/home/me")

    assert pi_tool_call_header("ls", {"path": "/home/me/src"}) == ("ls", "~/src")
    assert pi_tool_call_header("grep", {"pattern": "x", "path": "/home/me"}) == (
        "grep",
        "/x/ in ~",
    )
    # A plain string prefix, as in Pi.
    assert pi_tool_call_header("write", {"path": "/home/meta"}) == ("write", "~ta")
    assert pi_tool_call_header("read", {"path": "x"}) is None


# -- `!` output (bash-executor) --------------------------------------------------


def test_strip_ansi_and_sanitize_follow_pi() -> None:
    assert strip_ansi("a\x1b[31mred\x1b[0m") == "ared"
    assert strip_ansi("\x1b]0;title\x07x") == "x"
    assert strip_ansi("\x1b]8;;url\x1b\\link") == "link"
    assert strip_ansi("\x9b1;2mz") == "z"
    assert sanitize_binary_output("a\x00b\tc\nd\re\x7f") == "ab\tc\nd\re\x7f"
    assert sanitize_binary_output("x" + chr(0xFFF9) + "y") == "xy"
    assert utf16_length("a\U0001f600") == 3


def test_executor_output_sanitizes_and_keeps_everything_small() -> None:
    output = ExecutorOutput()
    output.append(b"\x1b[1mbold\x1b[0m\r\nline\x00\n")

    result = output.finish()

    assert (result.output, result.truncated, result.full_output_path) == (
        "bold\nline\n",
        False,
        None,
    )


def test_executor_output_rolls_whole_chunks_by_utf16_units() -> None:
    output = ExecutorOutput()
    # 60 000 emoji are 120 000 UTF-16 units: over the 102 400-unit buffer as
    # soon as the next chunk arrives, so the emoji chunk is dropped.
    output.append(("\U0001f600" * 60_000).encode())
    output.append(b"tail\n")
    result = output.finish()

    assert result.output == "tail\n"
    assert result.truncated is False
    assert result.full_output_path is not None
    full = Path(result.full_output_path)
    assert full.read_text() == "\U0001f600" * 60_000 + "tail\n"
    full.unlink()


def test_executor_output_truncates_the_tail_and_names_a_temp_file() -> None:
    output = ExecutorOutput()
    for number in range(1, 3001):
        output.append(f"{number}\n".encode())
    result = output.finish()

    assert result.truncated is True
    assert result.output.splitlines()[0] == "1001"
    assert result.full_output_path is not None
    full = Path(result.full_output_path)
    assert full.stat().st_mode & 0o777 == 0o600
    assert full.read_text() == "".join(f"{n}\n" for n in range(1, 3001))
    full.unlink()


# -- mutation-tool edge inputs through the executor ----------------------------


def _execute(tmp_path: Path, tool_name: str, arguments_json: str) -> Any:
    from pipy_harness.native.agent import AgentToolCall
    from pipy_harness.native.agent.content import ProductContent
    from pipy_harness.native.agent.tools import ToolExecutor
    from pipy_harness.native.tools import ToolContext
    from pipy_harness.native.tools.edit import EditTool
    from pipy_harness.native.tools.write import WriteTool

    executor = ToolExecutor({"write": WriteTool(), "edit": EditTool()})
    call = AgentToolCall("c1", tool_name, ProductContent(arguments_json))
    return executor.execute(call, ToolContext(workspace_root=tmp_path)).result


def test_a_lone_surrogate_is_written_as_the_replacement_character(
    tmp_path: Path,
) -> None:
    (tmp_path / "keep.txt").write_text("old\n")
    replacement = chr(0xFFFD).encode()

    write = _execute(tmp_path, "write", '{"path": "keep.txt", "content": "x\\ud800y"}')
    assert write.is_error is False
    assert (tmp_path / "keep.txt").read_bytes() == b"x" + replacement + b"y"

    edit = _execute(
        tmp_path,
        "edit",
        '{"path": "keep.txt", "edits": [{"oldText": "y", "newText": "\\udc00z"}]}',
    )
    assert edit.is_error is False
    assert (tmp_path / "keep.txt").read_bytes() == (
        b"x" + replacement + replacement + b"z"
    )


@pytest.mark.parametrize(
    ("tool_name", "arguments"),
    [
        ("write", '{"path": "nul\\u0000.txt", "content": "x"}'),
        (
            "edit",
            '{"path": "nul\\u0000.txt", "edits": [{"oldText": "a", "newText": "b"}]}',
        ),
    ],
)
def test_a_nul_byte_path_is_an_error_result(
    tmp_path: Path, tool_name: str, arguments: str
) -> None:
    result = _execute(tmp_path, tool_name, arguments)

    from pipy_harness.native.tools.fs_errors import node_null_byte_message

    assert result.is_error is True
    # The inspected path is cut at 128 units, so compare the whole message.
    assert result.content.value == node_null_byte_message(f"{tmp_path}/nul{chr(0)}.txt")
    assert result.content.value.startswith(
        "The argument 'path' must be a string, Uint8Array, or URL without null "
        f"bytes. Received '{str(tmp_path)[:20]}"
    )


def test_adjacent_surrogates_from_two_edits_form_one_character(
    tmp_path: Path,
) -> None:
    # Pi's strings are UTF-16: a high and a low surrogate side by side are one
    # character.
    (tmp_path / "e.txt").write_text("ab")

    result = _execute(
        tmp_path,
        "edit",
        '{"path": "e.txt", "edits": [{"oldText": "a", "newText": "\ud83d"}, '
        '{"oldText": "b", "newText": "\ude00"}]}',
    )

    assert result.is_error is False
    assert (tmp_path / "e.txt").read_bytes() == chr(0x1F600).encode()


def test_nul_byte_message_quotes_like_node() -> None:
    # Measured with Node's realpathSync (util.inspect quoting and escapes).
    from pipy_harness.native.tools.fs_errors import node_null_byte_message

    nul, prefix = chr(0), "The argument 'path' must be a string, Uint8Array, or URL"
    assert node_null_byte_message("/a/it's" + nul) == (
        prefix + ' without null bytes. Received "/a/it' + "'" + 's\\x00"'
    )
    assert node_null_byte_message("/a/it's " + '"q"' + nul) == (
        prefix + " without null bytes. Received `/a/it's " + '"q"' + "\\x00`"
    )
    assert node_null_byte_message(
        "/a/l" + chr(10) + "b" + nul + chr(9) + chr(0x7F)
    ) == (prefix + " without null bytes. Received '/a/l\\nb\\x00\\t\\x7F'")


def test_nul_byte_message_escapes_and_cuts_like_node() -> None:
    # Measured with Node's realpathSync: C1 controls as uppercase hex, a lone
    # surrogate as lowercase \\u, and the inspected value cut to 128 units.
    from pipy_harness.native.tools.fs_errors import node_null_byte_message

    nul = chr(0)
    received = "without null bytes. Received "
    assert node_null_byte_message("/a/" + chr(0x85) + chr(0xD800) + nul).endswith(
        received + "'/a/\\x85\\ud800\\x00'"
    )
    long = node_null_byte_message("/a/" + "x" * 200 + nul)
    assert long.endswith(received + "'/a/" + "x" * 124 + "...")
    # A surrogate pair cut in half is written as U+FFFD (Node keeps the half,
    # which no UTF-8 writer can encode).
    split = node_null_byte_message("/a/" + "x" * 123 + chr(0x1F600) + nul)
    assert split.endswith("x" + chr(0xFFFD) + "...")
