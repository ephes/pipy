"""Codemode ``store(key, value)`` and ``load(key)`` (MCP parity plan G4).

Pi's semantics: both are synchronous and work on JSON values; a script that
succeeds (an early exit included) keeps what it stored as a
``codemode-store`` custom entry on the session branch, a failed one keeps
nothing, and each branch reads the values written on its own path, also
after resume, ``/tree`` and ``/fork``. Mirrors Pi's sandbox tests "store and
load" and "ends the script successfully on exit(), keeping output and store
writes", and the session tests of "codemode options and store".
"""

from __future__ import annotations

import io
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from codemode_driver import REAL_RUNTIME, group_is_gone, real_runtime_skip_reason
from test_native_codemode_delivery import runtime as _runtime  # noqa: F401 - fixture
from test_native_codemode_delivery import settings
from test_native_codemode_host_runtime import _run as _run_real
from test_native_codemode_host_runtime import pids as _pids  # noqa: F401 - fixture
from test_native_coding_session import _CollectingAgentEventSink

from pipy_harness.native.agent import ToolCallCompleted
from pipy_harness.native.agent.messages import AgentUserMessage
from pipy_harness.native.codemode import host, prelude
from pipy_harness.native.codemode.outcome import (
    ErrorKind,
    ScriptLimits,
    ScriptOutcome,
    ScriptTool,
    StoreWrites,
)
from pipy_harness.native.codemode.runtime import RUNTIME_PIN, CodemodePaths, RuntimePin
from pipy_harness.native.coding.codemode_store import (
    CODEMODE_STORE_ENTRY_TYPE,
    read_codemode_store,
)
from pipy_harness.native.coding.session import CodingSession
from pipy_harness.native.fake import FakeNativeProvider
from pipy_harness.native.models import ProviderToolCall
from pipy_harness.native.session_tree import (
    CustomEntry,
    MessageEntry,
    NativeSessionTree,
)

real_runtime = pytest.mark.skipif(
    real_runtime_skip_reason() is not None, reason=str(real_runtime_skip_reason())
)

# --- the prelude's store ---------------------------------------------------------


# Pi: "reads the snapshot and reports writes"
def test_the_store_reads_the_snapshot_and_reports_writes() -> None:
    script = prelude.ScriptStore({"counter": "41", "old": '"x"'})
    seen = script.load("counter")
    script.store("counter", seen + 1)
    script.store("list", [1, {"a": None}])
    script.store("old", None)
    assert [seen, script.load("counter"), script.load("missing")] == [41, 42, None]
    assert script.load("old") is None
    assert script.writes() == [
        ["counter", "42"],
        ["list", '[1,{"a":null}]'],
        ["old"],
    ]


# Pi: "returns copies, so mutating a loaded value does not change the store"
def test_loaded_values_are_copies() -> None:
    script = prelude.ScriptStore({"obj": '{"a":1}'})
    value = script.load("obj")
    value["a"] = 2
    kept = {"b": 1}
    script.store("kept", kept)
    kept["b"] = 2
    assert [script.load("obj")["a"], script.load("kept")["b"]] == [1, 1]
    assert script.writes() == [["kept", '{"b":1}']]


def test_deleting_a_key_the_snapshot_lacks_is_no_write() -> None:
    script = prelude.ScriptStore({})
    script.store("gone")
    script.store("new", "value")
    script.store("new", None)
    assert script.writes() == []
    script.store("text", "ünïcode")
    assert script.writes() == [["text", '"ünïcode"']]


# Pi: "rejects invalid keys, values, and oversized writes inside the script"
def test_invalid_keys_values_and_oversized_writes_raise_in_the_script() -> None:
    script = prelude.ScriptStore({})

    def attempt(action: Any) -> str:
        try:
            action()
        except Exception as exc:  # noqa: BLE001 - the test names what was raised
            return type(exc).__name__
        return "ok"

    big = "x" * (300 * 1024)

    def fill() -> None:
        for index in range(8):
            script.store(f"k{index}", "x" * (200 * 1024))

    assert [
        attempt(lambda: script.store(1, "x")),
        attempt(lambda: script.load({})),
        attempt(lambda: script.store("fn", lambda: 1)),
        attempt(lambda: script.store("nan", float("nan"))),
        attempt(lambda: script.store("big", big)),
        attempt(fill),
    ] == [
        "TypeError",
        "TypeError",
        "TypeError",
        "TypeError",
        "ValueError",
        "ValueError",
    ]
    # The writes that fitted are kept; the one that did not is not.
    assert [key for key, _ in script.writes()] == ["k0", "k1", "k2", "k3", "k4"]


# Pi: "explains oversized writes"
def test_oversized_writes_are_explained() -> None:
    script = prelude.ScriptStore({})
    with pytest.raises(ValueError) as value_error:
        script.store("img", "x" * (300 * 1024))
    assert 'store("img") value has 307202 characters of JSON' in str(value_error.value)
    assert "Show images with image()" in str(value_error.value)
    for index in range(4):
        script.store(f"k{index}", "x" * (250 * 1024))
    with pytest.raises(ValueError, match=r"Delete keys with store\(key, None\)"):
        script.store("more", "x" * 30_000)
    with pytest.raises(TypeError, match="not JSON-serializable"):
        script.store("set", {1, 2})


def test_store_writes_of_a_snapshot_over_the_limit_may_only_shrink() -> None:
    script = prelude.ScriptStore({"big": json.dumps("x" * (1100 * 1024))})
    with pytest.raises(ValueError, match="store is full"):
        script.store("small", 1)
    script.store("big", None)
    script.store("small", 1)
    assert script.writes() == [["big"], ["small", "1"]]


# --- the host's checks (scripted guest) --------------------------------------------

FAKE_WORKER = Path(__file__).with_name("codemode_fake_worker.py")
PIN = RuntimePin(version="0", url="file:///fake", sha256="ab" * 32, size=1)
LIMITS = ScriptLimits(wall_seconds=5.0)


@pytest.fixture
def _spawned(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Run the scripted fake worker; every worker group must be gone after."""

    pids: list[int] = []
    real_worker = host._Worker

    class RecordingWorker(real_worker):  # type: ignore[misc, valid-type]
        def __init__(self, argv: list[str], status_write: int) -> None:
            super().__init__(argv, status_write)
            pids.append(self.process.pid)

    monkeypatch.setattr(host, "_Worker", RecordingWorker)
    yield
    assert all(group_is_gone(pid) for pid in pids)


def _run(
    monkeypatch: pytest.MonkeyPatch,
    scenario: str,
    *,
    limits: ScriptLimits = LIMITS,
    store: dict[Any, Any] | None = None,
) -> ScriptOutcome:
    def fake_argv(python: str, status_fd: int, request: str) -> list[str]:
        return [python, "-I", str(FAKE_WORKER), str(status_fd), PIN.sha256, scenario]

    monkeypatch.setattr(host, "_worker_argv", fake_argv)
    return host.run_script(
        "pass",
        call_tool=lambda name, arguments: AssertionError(name),  # type: ignore[arg-type, return-value]
        tools=[ScriptTool("read")],
        limits=limits,
        paths=CodemodePaths(Path("/nonexistent-codemode-root")),
        pin=PIN,
        store=store,
    )


def _sandbox_error(outcome: ScriptOutcome, fragment: str) -> None:
    assert outcome.error is not None, outcome
    assert outcome.error.kind is ErrorKind.SANDBOX, outcome.error
    assert fragment in outcome.error.message, outcome.error.message


@pytest.mark.usefixtures("_spawned")
def test_the_run_line_carries_the_store_and_a_done_its_writes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = (
        "run = start()\n"
        "send({'type': 'text', 'value': json.dumps(run['store'], sort_keys=True)})\n"
        "send({'type': 'done', 'ok': True, 'writes': [['a', '[1,2]'], ['b'],"
        " ['c', '{\"d\":null}'], ['a', '3']]})\n"
    )
    outcome = _run(
        monkeypatch,
        scenario,
        store={"b": True, "n": {"x": [1.5]}, "bad": float("nan"), 7: "x"},
    )
    assert outcome.ok, outcome.error
    assert outcome.output == ('{"b": "true", "n": "{\\"x\\":[1.5]}"}',)
    # A key written twice keeps its last value.
    assert outcome.store_writes == StoreWrites({"a": 3, "c": {"d": None}}, ("b",))


@pytest.mark.usefixtures("_spawned")
def test_a_failed_script_reports_no_writes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = (
        "start()\n"
        "send({'type': 'done', 'ok': False, 'writes': [['a', '1']],"
        " 'error': {'type': 'ValueError', 'message': 'boom', 'traceback': ''}})\n"
    )
    outcome = _run(monkeypatch, scenario)
    assert outcome.error is not None and outcome.error.kind is ErrorKind.SCRIPT
    assert outcome.store_writes == StoreWrites()
    assert not outcome.store_writes


@pytest.mark.parametrize(
    ("writes", "fragment"),
    [
        ("{}", "malformed store writes"),
        ("[['a', '1', '2']]", "malformed store writes"),
        ("[[1, '1']]", "malformed store writes"),
        ("[['a', 1]]", "malformed store writes"),
        ("[[]]", "malformed store writes"),
        ("[['a', '{']]", "not JSON"),
        ("[['a', 'NaN']]", "not JSON"),
        (
            f"[['a', json.dumps('x' * {prelude.MAX_STORE_VALUE_CHARS})]]",
            "over the store's limit",
        ),
        (
            "[[f'k{i}', json.dumps('x' * 200_000)] for i in range(6)]",
            "exceed the store's limit",
        ),
    ],
)
@pytest.mark.usefixtures("_spawned")
def test_writes_the_prelude_would_refuse_are_violations(
    monkeypatch: pytest.MonkeyPatch,
    writes: str,
    fragment: str,
) -> None:
    done = f"{{'type': 'done', 'ok': True, 'writes': {writes}}}"
    scenario = (
        f"start()\nraw((json.dumps({done}, separators=(',', ':')) + '\\n').encode())\n"
    )
    outcome = _run(monkeypatch, scenario)
    _sandbox_error(outcome, fragment)
    assert outcome.store_writes == StoreWrites()


@pytest.mark.usefixtures("_spawned")
def test_deletions_from_a_snapshot_over_the_limit_are_accepted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    big = {f"k{i}": "x" * 400_000 for i in range(3)}
    # The guest does not read the long run line, which takes the fake long.
    scenario = "ready()\nsend({'type': 'done', 'ok': True, 'writes': [['k0']]})\n"
    outcome = _run(monkeypatch, scenario, store=big)
    assert outcome.ok, outcome.error
    assert outcome.store_writes == StoreWrites({}, ("k0",))


@pytest.mark.usefixtures("_spawned")
def test_deletions_of_keys_the_snapshot_lacks_are_dropped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A forged done: the prelude never reports such a deletion, and keeping
    # it would let a script append an entry of any size that changes nothing.
    head = b'{"type":"done","ok":true,"writes":[["k"],["'
    scenario = f"start()\nraw({head!r} + b'x' * 2_000_000 + b'\"]]}}\\n')\n"
    outcome = _run(monkeypatch, scenario, store={"k": 1})
    assert outcome.ok, outcome.error
    assert outcome.store_writes == StoreWrites({}, ("k",))


@pytest.mark.usefixtures("_spawned")
def test_only_a_done_line_with_writes_may_pass_the_line_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    limits = ScriptLimits(wall_seconds=5.0, line_bytes=4096)
    value = json.dumps("\U0001f600" * 5000, ensure_ascii=False)
    done = (
        json.dumps(
            {"type": "done", "ok": True, "writes": [["k", value]]},
            separators=(",", ":"),
        )
        + "\n"
    ).encode()
    assert len(done) > 50_000
    outcome = _run(monkeypatch, f"start()\nraw({done!r})\n", limits=limits)
    assert outcome.ok, outcome.error
    assert outcome.store_writes.values == {"k": "\U0001f600" * 5000}

    # A text line of that length is still a violation, and so is a done line
    # longer than the store's limits allow.
    text = json.dumps({"type": "text", "value": "x" * 50_000}).encode() + b"\n"
    _sandbox_error(
        _run(monkeypatch, f"start()\nraw({text!r})\nhang()\n", limits=limits),
        "a guest line exceeded 4096 bytes",
    )
    allowance = 4096 + 20 * prelude.MAX_STORE_TOTAL_CHARS
    head = b'{"type":"done","ok":true,"writes":[["k","'
    flood = f"start()\nraw({head!r} + b'x' * {allowance})\nhang()\n"
    _sandbox_error(
        _run(monkeypatch, flood, limits=limits), "a guest line exceeded 4096 bytes"
    )


# --- a real guest ------------------------------------------------------------------


@pytest.fixture(scope="module")
def paths(tmp_path_factory: pytest.TempPathFactory) -> CodemodePaths:
    """The installed runtime under a private root with its own cache."""

    root = tmp_path_factory.mktemp("codemode-store")
    (root / "runtime").mkdir()
    (root / "runtime" / RUNTIME_PIN.dir_name).symlink_to(REAL_RUNTIME)
    return CodemodePaths(root)


@real_runtime
@pytest.mark.usefixtures("_pids")
def test_a_real_script_loads_and_stores(paths: CodemodePaths) -> None:
    code = (
        "count = load('count')\n"
        "store('count', count + 1)\n"
        "store('emoji', '\\U0001f600' * 100_000)\n"
        "store('gone', None)\n"
        "text([count, load('count'), load('missing'), load('gone')])\n"
    )
    outcome = _run_real(paths, code, store={"count": 41, "gone": [1]})
    assert outcome.ok, (outcome.error, outcome.diagnostics)
    assert outcome.output == ("[41, 42, null, null]",)
    # 100k astral characters take 1.2 MB on the wire, over the line limit.
    assert outcome.store_writes == StoreWrites(
        {"count": 42, "emoji": "\U0001f600" * 100_000}, ("gone",)
    )


# Pi: "ends the script successfully on exit(), keeping output and store writes"
@real_runtime
@pytest.mark.usefixtures("_pids")
def test_a_real_script_keeps_its_writes_on_an_early_exit(
    paths: CodemodePaths,
) -> None:
    code = (
        "import sys\n"
        "text('before')\n"
        "store('k', 1)\n"
        "tools.read({})\n"
        "sys.exit()\n"
        "text('after')\n"
    )
    outcome = _run_real(paths, code)
    assert outcome.ok, (outcome.error, outcome.diagnostics)
    assert outcome.output == ("before",)
    assert outcome.store_writes == StoreWrites({"k": 1})


@pytest.mark.parametrize("end", ["raise ValueError('boom')", "import sys; sys.exit(1)"])
@real_runtime
@pytest.mark.usefixtures("_pids")
def test_a_real_script_that_fails_drops_its_writes(
    paths: CodemodePaths,
    end: str,
) -> None:
    outcome = _run_real(paths, f"store('k', 1)\n{end}\n")
    assert outcome.error is not None and outcome.error.kind is ErrorKind.SCRIPT
    assert outcome.store_writes == StoreWrites()


# Pi: "runs without a session, starting from an empty store"
@real_runtime
@pytest.mark.usefixtures("_pids")
def test_a_real_script_without_a_store_starts_empty(
    paths: CodemodePaths,
) -> None:
    outcome = _run_real(paths, "text(load('count') is None)\nstore('count', 1)\n")
    assert outcome.ok, (outcome.error, outcome.diagnostics)
    assert outcome.output == ("true",)
    assert outcome.store_writes == StoreWrites({"count": 1})


# --- the session's store -------------------------------------------------------------


def _entry(data: object, custom_type: str = CODEMODE_STORE_ENTRY_TYPE) -> CustomEntry:
    return CustomEntry(
        id="x", parent_id=None, timestamp="", custom_type=custom_type, data=data
    )


# Pi: "folds store entries from the root, ignoring malformed data"
def test_store_entries_are_folded_from_the_root_ignoring_malformed_data() -> None:
    assert read_codemode_store(
        [
            _entry({"set": {"a": 1, "b": {"c": 2}}, "delete": []}),
            _entry({"set": {"a": 3}, "delete": ["b"]}),
            _entry({"set": {"z": 1}}),
            _entry({"set": [], "delete": []}),
            _entry({"set": {"y": 1}, "delete": [1]}),
            _entry({"set": {"other": 1}, "delete": []}, "other-extension"),
        ]
    ) == {"a": 3}


INCREMENT = "count = (load('count') or 0) + 1\nstore('count', count)\ntext(count)\n"


def _provider(*codes: str) -> FakeNativeProvider:
    turns: list[tuple[ProviderToolCall, ...]] = []
    for index, code in enumerate(codes):
        turns.append(
            (ProviderToolCall(f"call-{index}", "codemode", json.dumps({"code": code})),)
        )
        turns.append(())
    return FakeNativeProvider(
        model_id="fake-tools",
        supports_tool_calls=True,
        final_text="done",
        programmable_tool_calls=tuple(turns),
    )


def _run_session(
    workspace: Path, tree: NativeSessionTree, lines: list[str], *codes: str
) -> list[tuple[bool, str]]:
    """Run the input ``lines``; return each codemode result as (error, output)."""

    sink = _CollectingAgentEventSink()
    CodingSession(
        provider=_provider(*codes),
        settings_manager=settings(workspace),
        agent_event_sink=sink,
        native_session=tree,
    ).run(
        workspace_root=workspace,
        input_stream=io.StringIO("".join(f"{line}\n" for line in lines)),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
    )
    return [
        (event.result.is_error, event.result.content.value.partition("Output:\n")[2])
        for event in sink.events
        if isinstance(event, ToolCallCompleted)
    ]


def _store_entries(tree: NativeSessionTree) -> list[object]:
    return [
        entry.data
        for entry in tree.get_branch()
        if isinstance(entry, CustomEntry)
        and entry.custom_type == CODEMODE_STORE_ENTRY_TYPE
    ]


def _user_entries(tree: NativeSessionTree) -> list[MessageEntry]:
    return [
        entry
        for entry in tree.get_branch()
        if isinstance(entry, MessageEntry)
        and isinstance(entry.message, AgentUserMessage)
    ]


# Pi: "persists store() writes as custom entries for later calls"
@pytest.mark.usefixtures("_runtime")
def test_writes_are_custom_entries_that_later_calls_load(
    tmp_path: Path,
) -> None:
    tree = NativeSessionTree.create(tmp_path, session_dir=tmp_path / "saved")
    results = _run_session(
        tmp_path,
        tree,
        ["one", "two", "three", "four"],
        INCREMENT,
        INCREMENT,
        "store('count', None)\ntext(load('count') is None)\n",
        "import sys\nstore('note', {'id': 7})\nsys.exit()\n",
    )
    assert results == [(False, "1"), (False, "2"), (False, "true"), (False, "")]
    assert _store_entries(tree) == [
        {"set": {"count": 1}, "delete": []},
        {"set": {"count": 2}, "delete": []},
        {"set": {}, "delete": ["count"]},
        {"set": {"note": {"id": 7}}, "delete": []},
    ]
    # The entry is kept before the result of the script that wrote it.
    kinds = [
        "store"
        if isinstance(entry, CustomEntry)
        else type(entry.message).__name__
        if isinstance(entry, MessageEntry)
        else None
        for entry in tree.get_branch()
    ]
    first = kinds.index("store")
    assert kinds[first + 1] == "AgentToolResultMessage"
    assert tree.path is not None
    assert _store_entries(NativeSessionTree.open(tree.path)) == _store_entries(tree)


# Pi: "appends nothing for failed scripts or scripts without writes"
@pytest.mark.usefixtures("_runtime")
def test_failed_scripts_and_scripts_without_writes_append_nothing(
    tmp_path: Path,
) -> None:
    tree = NativeSessionTree.create(tmp_path, session_dir=tmp_path / "saved")
    results = _run_session(
        tmp_path,
        tree,
        ["one", "two", "three"],
        "store('count', 5)\nraise ValueError('boom')\n",
        "import sys\nstore('count', 6)\nsys.exit(2)\n",
        "text(load('count') or 'missing')\n",
    )
    assert [error for error, _ in results] == [True, True, False]
    assert results[2] == (False, "missing")
    assert _store_entries(tree) == []


# Pi: "loads the values written on the current branch"
@pytest.mark.usefixtures("_runtime")
def test_the_store_follows_the_branch_through_resume_tree_and_fork(
    tmp_path: Path,
) -> None:
    saved = tmp_path / "saved"
    tree = NativeSessionTree.create(tmp_path, session_dir=saved)
    assert _run_session(tmp_path, tree, ["one", "two"], INCREMENT, INCREMENT) == [
        (False, "1"),
        (False, "2"),
    ]
    first, second = _user_entries(tree)
    second_write = next(
        entry
        for entry in tree.get_branch()
        if isinstance(entry, CustomEntry) and entry.data["set"] == {"count": 2}
    )
    assert tree.path is not None

    # Resumed: the values written before are there.
    resumed = NativeSessionTree.open(tree.path)
    assert _run_session(tmp_path, resumed, ["three"], INCREMENT) == [(False, "3")]

    # /tree to before the second prompt: only the first write is on the path;
    # to before the first: none is.
    assert _run_session(
        tmp_path,
        NativeSessionTree.open(tree.path),
        [f"/tree select {second.id}", "four", f"/tree select {first.id}", "five"],
        INCREMENT,
        INCREMENT,
    ) == [(False, "2"), (False, "1")]

    # /fork from the second write: the new session has both writes.
    before = set(saved.glob("*.jsonl"))
    assert _run_session(
        tmp_path,
        NativeSessionTree.open(tree.path),
        [f"/fork {second_write.id}", "six"],
        INCREMENT,
    ) == [(False, "3")]
    (forked_path,) = set(saved.glob("*.jsonl")) - before
    forked = NativeSessionTree.open(forked_path)
    assert [data["set"] for data in _store_entries(forked)] == [  # type: ignore[index]
        {"count": 1},
        {"count": 2},
        {"count": 3},
    ]


def test_the_snapshot_is_each_value_as_json_text() -> None:
    assert host._store_snapshot({"a": [1, "ü"], "b": None, "c": {1, 2}}) == {
        "a": '[1,"ü"]',
        "b": "null",
    }
