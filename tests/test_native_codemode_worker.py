"""CM1 T2: the codemode worker's run mode and the guest prelude.

Host-side unit tests import the prelude's helpers directly. The rest run real
scripts in CPython-on-WASI through the minimal test driver and skip when the
``wasmtime`` wheel or the installed runtime is missing.
"""

from __future__ import annotations

import ast
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, cast

import pytest
from codemode_driver import (
    REAL_RUNTIME,
    ToolFailure,
    WorkerRun,
    group_is_gone,
    real_runtime_skip_reason,
    run_worker,
)

from pipy_harness.native.codemode import prelude
from pipy_harness.native.codemode.selftest import WORKER_PATH

CODEMODE_SRC = WORKER_PATH.parent

# --- host-side unit tests of the prelude helpers -----------------------------------


def test_unknown_tool_suggests_the_closest_name() -> None:
    message = prelude.unknown_tool_message("reed", ["read", "ls", "grep"])
    assert message == (
        "tools.reed does not exist. Did you mean tools.read? ALL_TOOLS lists "
        "every tool; search_tools(query) finds tools by topic. Check for a tool "
        'with "reed" in tools.'
    )


def test_unknown_tool_lists_up_to_twenty_names_and_no_more() -> None:
    few = prelude.unknown_tool_message("zzz", ["read", "ls"])
    assert few.startswith("tools.zzz does not exist. Available: read, ls. ALL_TOOLS")
    many = [f"tool{index:02d}" for index in range(21)]
    assert prelude.unknown_tool_message("zzz", many).startswith(
        "tools.zzz does not exist. ALL_TOOLS lists every tool;"
    )
    assert prelude.unknown_tool_message("zzz", []).endswith("No tools are available.")


def _wire_with_replies(
    *replies: dict[str, Any],
) -> tuple[prelude.Wire, int, list[int]]:
    """A wire whose host side already wrote ``replies``."""

    read_fd, write_fd = os.pipe()
    out_read, out_write = os.pipe()
    for reply in replies:
        os.write(write_fd, (json.dumps({"type": "result", **reply}) + "\n").encode())
    return (
        prelude.Wire(read_fd, out_write),
        out_read,
        [read_fd, write_fd, out_read, out_write],
    )


def _sent(out_read: int) -> list[dict[str, Any]]:
    os.set_blocking(out_read, False)
    try:
        data = os.read(out_read, 1 << 16)
    except BlockingIOError:
        return []
    return [json.loads(line) for line in data.splitlines()]


def test_submitted_calls_are_sent_at_once_and_take_their_own_replies() -> None:
    # Replies come in the order the calls settle, not the order they were made.
    wire, out_read, fds = _wire_with_replies(
        {"id": 2, "ok": True, "text": "second"},
        {"id": 1, "ok": False, "error": "first failed"},
        {"id": 3, "ok": True, "text": "third"},
    )
    try:
        tools = prelude.Tools({"read": "read"}, ["read"], wire.call, wire.submit_call)
        first = tools.read.submit(path="a")
        second = cast(Any, tools["read"]).submit({"path": "b"})
        assert [m["id"] for m in _sent(out_read)] == [1, 2]
        assert repr(first) == "<pending call of tools.read>"
        assert second.result() == "second"
        assert second.result() == "second"
        for _ in range(2):  # every result() raises again
            with pytest.raises(prelude.ToolError, match="first failed"):
                first.result()
        assert repr(first) == "<settled call of tools.read>"
        # A plain call waits for its own reply.
        assert tools.read(path="c") == "third"
    finally:
        for fd in fds:
            os.close(fd)


def test_a_script_past_the_pending_limit_waits_for_a_reply() -> None:
    limit = prelude.MAX_PENDING_REQUESTS
    wire, out_read, fds = _wire_with_replies({"id": 1, "ok": True, "text": "one"})
    try:
        calls = [wire.submit_call("read", {"n": n}) for n in range(limit)]
        assert len(_sent(out_read)) == limit
        # The next request first reads a reply, so no more than the limit
        # are ever waiting for the host.
        calls.append(wire.submit_call("read", {"n": limit}))
        assert [m["id"] for m in _sent(out_read)] == [limit + 1]
        assert calls[0].result() == "one"
    finally:
        for fd in fds:
            os.close(fd)


def test_submit_without_a_submitting_wire_is_a_type_error() -> None:
    tools = prelude.Tools({"read": "read"}, ["read"], lambda name, args: "x")
    with pytest.raises(TypeError, match=r"tools\.read\.submit\(\) is not available"):
        tools.read.submit()
    with pytest.raises(TypeError, match="one dict of arguments or keyword"):
        prelude.Tools(
            {"read": "read"}, ["read"], lambda name, args: "x", lambda n, a: None
        ).read.submit({"a": 1}, b=2)


def test_the_host_and_the_prelude_share_the_pending_limit() -> None:
    from pipy_harness.native.codemode import host

    assert vars(host)["MAX_PENDING_REQUESTS"] is prelude.MAX_PENDING_REQUESTS == 16


def test_call_arguments_accept_one_dict_or_keywords() -> None:
    assert prelude.call_arguments("read", {"path": "a"}, {}) == {"path": "a"}
    no_args = prelude.NO_ARGS
    assert prelude.call_arguments("read", no_args, {"path": "a"}) == {"path": "a"}
    assert prelude.call_arguments("read", no_args, {}) == {}
    with pytest.raises(TypeError, match="not NoneType"):
        prelude.call_arguments("read", None, {})
    with pytest.raises(TypeError, match="not both"):
        prelude.call_arguments("read", None, {"path": "a"})
    with pytest.raises(TypeError, match="not both"):
        prelude.call_arguments("read", {"path": "a"}, {"limit": 1})
    with pytest.raises(TypeError, match="not list"):
        prelude.call_arguments("read", ["a"], {})


def test_text_values_are_strings_or_json_or_repr() -> None:
    assert prelude.text_value("plain") == "plain"
    assert prelude.text_value({"k": [1, "ü"]}) == '{"k": [1, "ü"]}'
    assert prelude.text_value({1, 2}) == "{1, 2}"
    assert prelude.text_value([float("nan")]) == "[nan]"


def test_wire_lines_are_strict_json() -> None:
    assert prelude.encode({"a": 1}) == b'{"a":1}\n'
    for bad in (float("nan"), float("inf"), object()):
        with pytest.raises((TypeError, ValueError)):
            prelude.encode({"a": bad})


def test_tools_proxy_routes_attribute_calls_and_hides_private_names() -> None:
    sent: list[tuple[str, dict[str, Any]]] = []

    def call(name: str, args: dict[str, Any]) -> str:
        sent.append((name, args))
        return "value"

    tools = prelude.Tools({"read": "read", "ls": "ls"}, ["read", "ls"], call)
    assert tools.read(path="a") == "value"
    assert tools.ls({"path": "."}) == "value"
    assert sent == [("read", {"path": "a"}), ("ls", {"path": "."})]
    assert dir(tools) == ["ls", "read"]
    with pytest.raises(AttributeError) as unknown:
        tools.reed  # noqa: B018 - attribute access is the behaviour under test
    assert unknown.value.name is None
    with pytest.raises(AttributeError, match="__deepcopy__"):
        tools.__deepcopy__  # noqa: B018


def test_traceback_keeps_only_script_frames_through_the_chain() -> None:
    namespace: dict[str, Any] = {}
    code = (
        "def inner():\n"
        "    raise KeyError('k')\n"
        "try:\n"
        "    inner()\n"
        "except KeyError as exc:\n"
        "    raise ValueError('wrapped') from exc\n"
    )
    done = prelude.run_script(code, namespace)

    assert done["ok"] is False
    error = done["error"]
    assert error["type"] == "ValueError" and error["message"] == "wrapped"
    trace = error["traceback"]
    assert 'File "<codemode>", line 2, in inner' in trace
    assert "raise ValueError('wrapped') from exc" in trace
    assert "KeyError: 'k'" in trace
    assert "prelude.py" not in trace and "test_native_codemode" not in trace


def test_script_outcomes_for_exit_and_syntax_errors() -> None:
    assert prelude.run_script("import sys\nsys.exit(0)\n", {}) == {
        "type": "done",
        "ok": True,
    }
    failed = prelude.run_script("import sys\nsys.exit(2)\n", {})
    assert failed["ok"] is False and failed["error"]["type"] == "SystemExit"
    syntax = prelude.run_script("def broken(:\n", {})
    assert syntax["error"]["type"] == "SyntaxError"
    assert '"<codemode>", line 1' in syntax["error"]["traceback"]


def test_a_failure_frees_the_script_namespace() -> None:
    namespace: dict[str, Any] = {}
    prelude.run_script("big = [0] * 10\nraise RuntimeError('x')\n", namespace)
    assert namespace == {}


def test_worker_and_prelude_import_only_stdlib_and_wasmtime() -> None:
    for name, allowed in (("worker.py", {"wasmtime"}), ("prelude.py", set())):
        tree = ast.parse((CODEMODE_SRC / name).read_text())
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                assert node.level == 0
                imported.add((node.module or "").split(".")[0])
        non_stdlib = {n for n in imported if n not in sys.stdlib_module_names}
        assert non_stdlib == allowed, name


def test_wire_call_returns_the_text_or_the_structured_value_of_a_reply() -> None:
    # MCP parity plan 4.9: a reply carries ``text`` or ``value`` (any JSON).
    for reply, expected in (
        ({"ok": True, "text": "2 files"}, "2 files"),
        (
            {"ok": True, "value": {"files": 2, "names": ["a"]}},
            {"files": 2, "names": ["a"]},
        ),
        ({"ok": True, "value": "a string value"}, "a string value"),
        ({"ok": True, "value": None}, None),
        ({"ok": True, "value": 0}, 0),
    ):
        read_fd, write_fd = os.pipe()
        out_read, out_write = os.pipe()
        try:
            os.write(
                write_fd,
                (json.dumps({"type": "result", "id": 1, **reply}) + "\n").encode(),
            )
            wire = prelude.Wire(read_fd, out_write)
            assert wire.call("stats", {"key": "a"}) == expected
            assert json.loads(os.read(out_read, 4096)) == {
                "type": "call",
                "id": 1,
                "name": "stats",
                "args": {"key": "a"},
            }
        finally:
            for fd in (read_fd, write_fd, out_read, out_write):
                os.close(fd)


def test_wire_call_raises_tool_error_for_an_error_reply() -> None:
    read_fd, write_fd = os.pipe()
    out_read, out_write = os.pipe()
    try:
        os.write(write_fd, b'{"type":"result","id":1,"ok":false,"error":"denied"}\n')
        with pytest.raises(prelude.ToolError, match="denied"):
            prelude.Wire(read_fd, out_write).call("stats", {})
    finally:
        for fd in (read_fd, write_fd, out_read, out_write):
            os.close(fd)


# --- real runtime -------------------------------------------------------------------

real_runtime = pytest.mark.skipif(
    real_runtime_skip_reason() is not None, reason=str(real_runtime_skip_reason())
)


@pytest.fixture(scope="module")
def cache_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("codemode-cache")


def _run(code: str, cache_dir: Path, **kwargs: Any) -> WorkerRun:
    kwargs.setdefault("runtime_dir", REAL_RUNTIME)
    return run_worker(code, cache_dir=cache_dir, **kwargs)


def _ok(run: WorkerRun) -> None:
    assert run.done == {"type": "done", "ok": True}, (run.done, run.stderr)
    assert run.exit == {"type": "exit", "reason": "ok", "code": 0}
    assert run.returncode == 0


@real_runtime
def test_status_fd_reports_ready_then_exit(cache_dir: Path) -> None:
    run = _run("text('hi')", cache_dir)

    _ok(run)
    ready, exit_ = run.status
    assert ready == {
        "type": "ready",
        "backend": "wasi",
        "wasmtime": ready["wasmtime"],
        "runtime_sha256": json.loads((REAL_RUNTIME / "manifest.json").read_text())[
            "runtime_sha256"
        ],
    }
    assert ready["wasmtime"].startswith("49.")
    assert exit_["type"] == "exit"
    assert run.messages == [
        {"type": "text", "value": "hi"},
        {"type": "done", "ok": True},
    ]


@real_runtime
def test_output_is_one_message_per_write_in_order(cache_dir: Path) -> None:
    code = (
        "import sys\n"
        "print('a', 1, sep='-', end='!')\n"
        "text({'k': 1})\n"
        "sys.stdout.write('w')\n"
        "print('err', file=sys.stderr)\n"
        "print()\n"
        "text({1})\n"
    )
    run = _run(code, cache_dir)

    _ok(run)
    assert run.texts == ["a-1!", '{"k": 1}', "w", "err\n", "\n", "{1}"]


@real_runtime
def test_print_reaches_the_host_before_a_blocking_tool_call(cache_dir: Path) -> None:
    run = _run(
        "print('before')\ntools.read(path='x')\n",
        cache_dir,
        tools={"read": lambda args: "ok"},
    )

    _ok(run)
    assert [m["type"] for m in run.messages] == ["text", "call", "done"]


@real_runtime
def test_tool_calls_return_strings_and_raise_tool_error(cache_dir: Path) -> None:
    def grep(args: dict[str, Any]) -> str:
        raise ToolFailure("no such pattern")

    code = (
        "text(tools.read({'path': 'a.txt'}))\n"
        "text(tools.read(path='b.txt', limit=2))\n"
        "try:\n"
        "    tools.grep(pattern='x')\n"
        "except ToolError as exc:\n"
        "    text('caught: ' + str(exc))\n"
        "tools.grep(pattern='y')\n"
    )
    run = _run(
        code,
        cache_dir,
        tools={"read": lambda args: f"read {json.dumps(args)}", "grep": grep},
    )

    assert run.calls == [
        {"type": "call", "id": 1, "name": "read", "args": {"path": "a.txt"}},
        {
            "type": "call",
            "id": 2,
            "name": "read",
            "args": {"path": "b.txt", "limit": 2},
        },
        {"type": "call", "id": 3, "name": "grep", "args": {"pattern": "x"}},
        {"type": "call", "id": 4, "name": "grep", "args": {"pattern": "y"}},
    ]
    assert run.texts == [
        'read {"path": "a.txt"}',
        'read {"path": "b.txt", "limit": 2}',
        "caught: no such pattern",
    ]
    assert run.done is not None and run.done["ok"] is False
    error = run.done["error"]
    assert error["type"] == "ToolError" and error["message"] == "no such pattern"
    assert error["traceback"] == (
        "Traceback (most recent call last):\n"
        '  File "<codemode>", line 7, in <module>\n'
        "    tools.grep(pattern='y')\n"
        "    ~~~~~~~~~~^^^^^^^^^^^^^\n"
        "ToolError: no such pattern\n"
    )
    assert run.exit == {"type": "exit", "reason": "ok", "code": 0}


@real_runtime
def test_unknown_tool_and_bad_arguments_never_reach_the_host(cache_dir: Path) -> None:
    code = (
        "for attempt in (lambda: tools.reed, lambda: tools.read(['a']),\n"
        "                lambda: tools.read({'a': 1}, b=2),\n"
        "                lambda: tools.read(path=object()),\n"
        "                lambda: tools.read(value=float('nan')),\n"
        "                lambda: tools.read(None, path='x')):\n"
        "    try:\n"
        "        attempt()\n"
        "    except (AttributeError, TypeError) as exc:\n"
        "        text(type(exc).__name__ + ': ' + str(exc))\n"
        "text(repr(dir(tools)))\n"
    )
    run = _run(code, cache_dir, tool_names=["read", "ls"])

    _ok(run)
    assert run.calls == []
    assert run.texts[0].startswith(
        "AttributeError: tools.reed does not exist. Did you mean tools.read? "
    )
    assert run.texts[1] == "TypeError: tools.read() takes a dict of arguments, not list"
    assert "not both" in run.texts[2]
    assert run.texts[3].startswith(
        "TypeError: tools.read() arguments must be JSON serializable"
    )
    assert run.texts[4].startswith(
        "TypeError: tools.read() arguments must be JSON serializable"
    )
    assert "not both" in run.texts[5]
    assert run.texts[6] == "['ls', 'read']"


@real_runtime
def test_script_globals_stdin_and_exit(cache_dir: Path) -> None:
    code = (
        "text(__name__)\n"
        "try:\n"
        "    input()\n"
        "except EOFError:\n"
        "    text('eof')\n"
        "import sys\n"
        "sys.exit(0)\n"
        "text('unreachable')\n"
    )
    run = _run(code, cache_dir)

    _ok(run)
    assert run.texts == ["__main__", "eof"]


@real_runtime
def test_syntax_error_is_a_script_failure(cache_dir: Path) -> None:
    run = _run("print('x'\n", cache_dir)

    assert run.done is not None and run.done["ok"] is False
    assert run.done["error"]["type"] == "SyntaxError"
    assert "<codemode>" in run.done["error"]["traceback"]
    assert run.exit == {"type": "exit", "reason": "ok", "code": 0}


@real_runtime
def test_a_real_guest_receives_structured_values(cache_dir: Path) -> None:
    replies = iter(
        [
            {"ok": True, "value": {"files": 2, "names": ["ä", None, 1.5, True]}},
            {"ok": True, "value": [1, "two"]},
            {"ok": True, "text": "plain"},
        ]
    )
    run = _run(
        "values = [tools.read(), tools.read(), tools.read()]\n"
        "text([[type(value).__name__, value] for value in values])\n",
        cache_dir,
        tool_names=["read"],
        reply=lambda message: {
            "type": "result",
            "id": message["id"],
            **next(replies),
        },
    )

    _ok(run)
    assert json.loads(run.output) == [
        ["dict", {"files": 2, "names": ["ä", None, 1.5, True]}],
        ["list", [1, "two"]],
        ["str", "plain"],
    ]


@real_runtime
@pytest.mark.parametrize(
    "reply",
    [
        {"ok": True},
        {"ok": True, "text": 1},
        {"ok": True, "text": "both", "value": "both"},
        {"ok": False, "value": {"files": 2}},
        {"ok": 1, "value": {"files": 2}},
    ],
)
def test_a_reply_that_is_neither_text_value_nor_error_ends_the_guest(
    cache_dir: Path, reply: dict[str, Any]
) -> None:
    run = _run(
        "try:\n    tools.read()\nexcept BaseException:\n    text('swallowed')\n",
        cache_dir,
        tool_names=["read"],
        reply=lambda message: {"type": "result", "id": message["id"], **reply},
    )

    assert run.done is None and run.texts == []
    assert run.exit == {
        "type": "exit",
        "reason": "error",
        "code": prelude.EXIT_PROTOCOL,
    }


@real_runtime
@pytest.mark.parametrize("reply_id", [2, True, 1.0, "1", None])
def test_a_malformed_host_reply_ends_the_guest_without_an_outcome(
    cache_dir: Path, reply_id: object
) -> None:
    def wrong_id(message: dict[str, Any]) -> dict[str, Any]:
        assert message["id"] == 1
        return {"type": "result", "id": reply_id, "ok": True, "text": ""}

    run = _run(
        "try:\n    tools.read()\nexcept BaseException:\n    text('swallowed')\n",
        cache_dir,
        tool_names=["read"],
        reply=wrong_id,
    )

    assert run.done is None and run.texts == []
    assert run.exit == {
        "type": "exit",
        "reason": "error",
        "code": prelude.EXIT_PROTOCOL,
    }
    assert "unexpected reply" in run.stderr


@real_runtime
def test_a_malformed_run_request_ends_the_guest(cache_dir: Path) -> None:
    run = _run("", cache_dir, first_line=b'{"type": "run", "code": 1}\n')

    assert run.messages == []
    assert run.exit == {
        "type": "exit",
        "reason": "error",
        "code": prelude.EXIT_PROTOCOL,
    }


@real_runtime
def test_setup_failure_reports_exit_without_ready(
    cache_dir: Path, tmp_path: Path
) -> None:
    run = _run("text('never')", cache_dir, runtime_dir=tmp_path / "missing")

    assert [s["type"] for s in run.status] == ["exit"]
    assert run.exit is not None and run.exit["reason"] == "error"
    assert "cannot read installed runtime" in run.exit["detail"]
    assert run.messages == [] and run.returncode == 1


# --- limits -----------------------------------------------------------------------


@real_runtime
def test_cpu_backstop_interrupts_a_busy_loop(cache_dir: Path) -> None:
    started = time.monotonic()
    run = _run("text('start')\nwhile True: pass\n", cache_dir, cpu_seconds=0.5)

    assert run.texts == ["start"] and run.done is None
    assert run.exit is not None and run.exit["reason"] == "trap-interrupt"
    assert not run.timed_out and time.monotonic() - started < 10


@real_runtime
def test_cpu_backstop_counts_time_blocked_on_the_host(cache_dir: Path) -> None:
    def slow(args: dict[str, Any]) -> str:
        time.sleep(1.0)
        return "late"

    run = _run(
        "tools.read()\ntext('after')\n",
        cache_dir,
        tools={"read": slow},
        cpu_seconds=0.5,
    )

    # The epoch is wall time: the guest traps on its first check after the
    # reply, which is why the host's wall deadline (T3) is the primary limit.
    assert run.texts == [] and run.done is None
    assert run.exit is not None and run.exit["reason"] == "trap-interrupt"


@real_runtime
def test_memory_limit_raises_memory_error_and_the_script_continues(
    cache_dir: Path,
) -> None:
    code = (
        "try:\n"
        "    bytearray(200 * 1024 * 1024)\n"
        "except MemoryError:\n"
        "    text('memory error')\n"
        "text('alive')\n"
    )
    run = _run(code, cache_dir, memory_bytes=64 * 1024 * 1024)

    _ok(run)
    assert run.texts == ["memory error", "alive"]


@real_runtime
def test_memory_exhaustion_still_reports_the_outcome(cache_dir: Path) -> None:
    code = (
        "text('start')\nchunks = []\nwhile True:\n    chunks.append(' ' * 1_000_000)\n"
    )
    run = _run(code, cache_dir, memory_bytes=64 * 1024 * 1024)

    assert run.texts == ["start"]
    assert run.done is not None and run.done["ok"] is False
    assert run.done["error"]["type"] == "MemoryError"
    assert run.exit == {"type": "exit", "reason": "ok", "code": 0}


@real_runtime
def test_wasm_stack_exhaustion_traps(cache_dir: Path) -> None:
    code = (
        "import sys\n"
        "sys.setrecursionlimit(10**7)\n"
        "def f(n):\n"
        "    return f(n + 1)\n"
        "f(0)\n"
    )
    run = _run(code, cache_dir)

    assert run.done is None
    assert run.exit is not None and run.exit["reason"] == "trap-stack"


@real_runtime
def test_default_recursion_limit_is_a_catchable_error(cache_dir: Path) -> None:
    run = _run("def f(n):\n    return f(n + 1)\nf(0)\n", cache_dir)

    assert run.done is not None and run.done["error"]["type"] == "RecursionError"
    assert run.exit == {"type": "exit", "reason": "ok", "code": 0}


def test_runtime_is_present_when_ci_requires_it() -> None:
    if os.environ.get("PIPY_CODEMODE_REQUIRE_RUNTIME") != "1":
        pytest.skip("PIPY_CODEMODE_REQUIRE_RUNTIME is not set")
    assert real_runtime_skip_reason() is None


@real_runtime
@pytest.mark.parametrize(
    "request_json",
    [
        "not json",
        "[]",
        '{"runtime_dir": "r", "cache_dir": "c", "memory_bytes": true, "cpu_seconds": 1, '
        '"host_pid": 2}',
        '{"runtime_dir": "r", "cache_dir": "c", "memory_bytes": 1, "cpu_seconds": 1e300, '
        '"host_pid": 2}',
        '{"runtime_dir": "r", "cache_dir": "c", "memory_bytes": 8589934592, '
        '"cpu_seconds": 1, "host_pid": 2}',
        '{"runtime_dir": "r", "cache_dir": "c", "memory_bytes": 1, "cpu_seconds": 1}',
        '{"runtime_dir": "r", "cache_dir": "c", "memory_bytes": 1, "cpu_seconds": 1, '
        '"host_pid": 1}',
        '{"runtime_dir": "r", "cache_dir": "c", "memory_bytes": 1, "cpu_seconds": 1, '
        '"host_pid": "2"}',
    ],
)
def test_malformed_run_requests_report_exit_without_ready(request_json: str) -> None:
    read_fd, write_fd = os.pipe()
    try:
        process = subprocess.run(  # noqa: S603 - fixed argv
            [
                sys.executable,
                "-I",
                str(WORKER_PATH),
                "run",
                str(write_fd),
                request_json,
            ],
            pass_fds=(write_fd,),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=60,
            check=False,
        )
        os.close(write_fd)
        status = os.read(read_fd, 65536).decode()
    finally:
        os.close(read_fd)

    assert process.returncode == 1 and process.stdout == b""
    assert [json.loads(line) for line in status.splitlines()] == [
        {"type": "exit", "reason": "error", "detail": "malformed run request"}
    ]


@real_runtime
def test_a_symlinked_stdlib_is_refused(cache_dir: Path, tmp_path: Path) -> None:
    # Red-team: preopen_dir follows a symlinked lib/, so a swapped-in link
    # would expose any host directory to the guest.
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    for name in ("manifest.json", "python.wasm"):
        (runtime / name).write_bytes((REAL_RUNTIME / name).read_bytes())
    (runtime / "lib").symlink_to(REAL_RUNTIME / "lib", target_is_directory=True)
    run = _run("text('never')", cache_dir, runtime_dir=runtime)

    assert run.status == [
        {
            "type": "exit",
            "reason": "error",
            "detail": "the runtime's lib is not a directory (symlinks are refused)",
        }
    ]
    assert run.messages == []


@real_runtime
def test_a_worker_whose_parent_is_not_the_host_does_not_start(
    cache_dir: Path,
) -> None:
    run = _run("text('never')", cache_dir, host_pid=os.getppid())

    assert run.status == [
        {"type": "exit", "reason": "error", "detail": "the host is gone"}
    ]
    assert run.messages == []


class _SlowEngine:
    """Counts epoch increments; each takes 5 ms, like a loaded machine."""

    def __init__(self) -> None:
        self.epoch = 0

    def increment_epoch(self) -> None:
        time.sleep(0.005)
        self.epoch += 1


def test_the_epoch_follows_the_clock_not_the_tick_count() -> None:
    # Red-team: counting one tick per 10 ms wait stretched the backstop ~24%.
    import pipy_harness.native.codemode.worker as worker

    engine = _SlowEngine()
    with worker._EpochTicker(engine, os.getppid()):  # type: ignore[arg-type]
        time.sleep(1.0)
    # One tick per wait would be about 1.0 / 0.015 = 66.
    assert engine.epoch >= 90


def test_the_ticker_exits_the_worker_when_the_host_is_gone() -> None:
    script = (
        "import sys, time\n"
        f"sys.path.insert(0, {str(WORKER_PATH.parent)!r})\n"
        "import worker\n"
        "class Engine:\n"
        "    def increment_epoch(self): pass\n"
        "worker._EpochTicker(Engine(), 1).__enter__()\n"
        "time.sleep(10)\n"
    )
    started = time.monotonic()
    process = subprocess.run(  # noqa: S603 - fixed argv
        [sys.executable, "-I", "-c", script], timeout=10, check=False
    )
    assert process.returncode == 4
    assert time.monotonic() - started < 5


@real_runtime
@pytest.mark.parametrize(
    "code",
    [
        "text('running')\nwhile True: pass",
        "import time\ntext('running')\ntime.sleep(60)",
    ],
)
def test_a_worker_dies_with_its_sigkilled_host(code: str, tmp_path: Path) -> None:
    # Red-team: a SIGKILLed host left a spinning worker until the backstop.
    root = tmp_path / "codemode"
    (root / "runtime").mkdir(parents=True)
    (root / "runtime" / REAL_RUNTIME.name).symlink_to(REAL_RUNTIME)
    host_script = (
        "import sys\n"
        "from pathlib import Path\n"
        "from pipy_harness.native.codemode import host\n"
        "real = host._Worker\n"
        "class Worker(real):\n"
        "    def __init__(self, *args):\n"
        "        super().__init__(*args)\n"
        "        print(self.process.pid, flush=True)\n"
        "host._Worker = Worker\n"
        "on_text = host._Channel._on_text\n"
        "def report_text(self, message):\n"
        "    on_text(self, message)\n"
        "    print(message['value'], flush=True)\n"
        "host._Channel._on_text = report_text\n"
        "outcome = host.run_script(sys.argv[1], call_tool=print, tools=(),\n"
        "    limits=host.ScriptLimits(wall_seconds=60),\n"
        "    paths=host.CodemodePaths(Path(sys.argv[2])))\n"
        "print(outcome, flush=True)\n"
    )
    fake_host = subprocess.Popen(  # noqa: S603 - fixed argv
        [sys.executable, "-c", host_script, code, str(root)], stdout=subprocess.PIPE
    )
    assert fake_host.stdout is not None
    worker_pid = int(fake_host.stdout.readline())
    try:
        # A one-way text message proves the guest got past setup and into
        # the script, with no pending read that the host's EOF could end.
        # A setup failure prints the outcome instead (wall limit: 60 s).
        assert fake_host.stdout.readline() == b"running\n"
        assert fake_host.poll() is None
        assert not group_is_gone(worker_pid)
        assert os.getpgid(worker_pid) == worker_pid
    finally:
        fake_host.kill()
        fake_host.wait()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and not group_is_gone(worker_pid):
        time.sleep(0.05)
    gone = group_is_gone(worker_pid)
    if not gone:
        os.killpg(worker_pid, signal.SIGKILL)
    assert gone, "the worker outlived its host"


@real_runtime
def test_a_runtime_without_its_stdlib_reports_a_setup_failure(
    cache_dir: Path, tmp_path: Path
) -> None:
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    for name in ("manifest.json", "python.wasm"):
        (runtime / name).write_bytes((REAL_RUNTIME / name).read_bytes())
    run = _run("text('never')", cache_dir, runtime_dir=runtime)

    assert [s["type"] for s in run.status] == ["exit"]
    assert run.exit is not None and run.exit["reason"] == "error"
    assert run.messages == []
