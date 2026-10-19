"""The real canonical loop replayed over durable experimental I/O receipts."""

from __future__ import annotations

import importlib
import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

DIRECTORY = Path(__file__).resolve().parents[1] / "scripts" / "durable_prototype"
_spec = importlib.util.spec_from_file_location(
    "loop_experiments", DIRECTORY / "loop_experiments.py"
)
assert _spec and _spec.loader
experiments = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(experiments)


@pytest.fixture
def runtime(monkeypatch):
    monkeypatch.syspath_prepend(str(DIRECTORY))
    return importlib.import_module("loop_integration")


@pytest.mark.parametrize("scenario", ["safe", "unsafe"])
@pytest.mark.parametrize("point", experiments.POINTS)
def test_real_loop_hard_crashes(tmp_path, scenario, point):
    experiments.case(tmp_path, scenario, point)


@pytest.mark.parametrize("scenario", ["safe", "unsafe"])
@pytest.mark.parametrize("failure", experiments.FAILURES)
def test_real_loop_storage_failure(tmp_path, scenario, failure):
    experiments.case(tmp_path, scenario, failure=failure)


@pytest.mark.parametrize("scenario", ["safe", "unsafe"])
def test_real_loop_sigkill(tmp_path, scenario):
    experiments.case(tmp_path, scenario, "t0.effect", sigkill=True)


def messages(runtime, directory):
    with sqlite3.connect(directory / "run.sqlite") as db:
        return {
            key: runtime.loads(body)
            for key, body in db.execute("SELECT task_id,body FROM entries")
        }


@pytest.mark.parametrize("scenario", ["safe", "unsafe"])
def test_exact_rich_fields_and_native_result_reused(runtime, tmp_path, scenario):
    initial = experiments.invoke(
        tmp_path, "start", scenario, "--crash-at", "t1.message"
    )
    assert initial.returncode == 86
    before = messages(runtime, tmp_path)
    first = before["g0"]
    assert (
        first.provider == runtime.IDENTITY["provider"]
        and first.model == runtime.IDENTITY["model"]
    )
    assert (
        first.api == runtime.IDENTITY["api"] and first.provider_thinking_level == "high"
    )
    assert [type(block).__name__ for block in first.blocks] == [
        "ThinkingContent",
        "ThinkingContent",
        "TextContent",
        "AgentToolCall",
        "AgentToolCall",
        "TextContent",
    ]
    assert first.blocks[0].signature == "thinking-signature"
    assert first.blocks[1].signature == "opaque-redacted" and first.blocks[1].redacted
    assert first.blocks[2].signature == "text-signature"
    assert first.blocks[-1].signature == "trailing-signature"
    # Canonical message usage separates cached/written input from the raw total.
    assert first.usage.input == 60 and first.usage.output == 20
    assert first.usage.reasoning == 7 and first.usage.cache_write_1h == 4
    assert first.usage.cache_read == 30 and first.usage.cache_write == 10
    assert first.usage.total_tokens == 127 and first.usage.cost.total == 0.0001305
    for index, call in enumerate(first.tool_calls):
        result = before[f"t{index}"]
        assert call.thought_signature == f"call-signature-{index}"
        assert "\n" in call.arguments_json.value
        assert result.provider_correlation_id == call.provider_correlation_id
        assert result.tool_request_id.startswith("pipy-tool-")
        assert result.tool_name == call.tool_name and not result.is_error
        assert result.details == {"fixture": {"ordered": [1, "é", None]}}
        assert result.structured_content == {
            "nested": [False, {"receipt": call.tool_name}]
        }
        assert result.blocks[0].text == result.content.value
        assert result.blocks[1].mime_type == "image/png"
        assert result.terminate is False
    # A changed live file must not replace the committed read observation.
    (tmp_path / "effect.txt").write_text("changed after commit\n", encoding="utf-8")
    resumed = experiments.invoke(tmp_path, "resume")
    assert resumed.returncode == 0, resumed.stderr
    after = messages(runtime, tmp_path)
    for key, message in before.items():
        assert runtime.dumps(after[key]) == runtime.dumps(
            message
        )  # includes compare=False fields
    with sqlite3.connect(tmp_path / "run.sqlite") as db:
        payload = json.loads(
            db.execute("SELECT payload FROM tasks WHERE id='g1'").fetchone()[0]
        )
        context = runtime.loads(payload["fingerprint"])
        assert runtime.dumps(context["messages"][-1]) == runtime.dumps(before["t1"])
        assert runtime.dumps(context["target_views"][0]) == runtime.dumps(
            first.ordered_content()
        )
        raw = json.loads(
            db.execute("SELECT payload FROM tasks WHERE id='t1'").fetchone()[0]
        )["result"]
        assert runtime.dumps(runtime.loads(raw)) == raw
    foreign = runtime.transform_assistant_blocks(
        first, runtime.ReplayTarget(first.provider, first.api, "other-model")
    )
    assert not any(isinstance(block, runtime.ThinkingContent) for block in foreign)
    assert all(getattr(block, "signature", None) is None for block in foreign)
    assert all(getattr(block, "thought_signature", None) is None for block in foreign)
    assert (tmp_path / "effect.txt").read_text(
        encoding="utf-8"
    ) == "changed after commit\n"


def test_terminate_checkpoint_controls_real_loop(runtime, tmp_path):
    initial = experiments.invoke(
        tmp_path, "start", "safe", "--terminate", "--crash-at", "t1.receipt"
    )
    assert initial.returncode == 86
    resumed = experiments.invoke(tmp_path, "resume")
    assert resumed.returncode == 0, resumed.stderr
    summary = json.loads(resumed.stdout)
    assert {task["id"]: task["attempts"] for task in summary["tasks"]} == {
        "g0": 1,
        "t0": 1,
        "t1": 1,
    }
    assert summary["run"]["messages"] == 4 and summary["observations"] == 5
    assert summary["run"]["usage"]["input_tokens"] == 100
    result = messages(runtime, tmp_path)
    assert result["t0"].terminate and result["t1"].terminate
    assert "g1" not in result


def test_publication_checks_a_separate_connection(runtime, tmp_path):
    store = runtime.LoopStore(tmp_path / "run.sqlite", runtime.Faults())
    try:
        store.admit("request", runtime.configuration(tmp_path, "safe"))
        body = runtime.dumps(
            runtime.AgentUserMessage(runtime.ProductContent("uncommitted"))
        )
        with store.transaction("uncommitted") as tx:
            tx.execute("INSERT INTO entries(task_id,body) VALUES('input',?)", (body,))
            with pytest.raises(RuntimeError, match="publication preceded"):
                store.publish("input", body)
        assert store.db.execute("SELECT count(*) FROM observations").fetchone()[0] == 0
        store.publish("input", body)
        assert store.db.execute("SELECT count(*) FROM observations").fetchone()[0] == 1
    finally:
        store.close()


@pytest.mark.parametrize("mutation", ["fingerprint", "result", "provider", "tool"])
def test_corrupt_receipts_block_before_new_external_work(tmp_path, mutation):
    initial = experiments.invoke(
        tmp_path, "start", "unsafe", "--crash-at", "t0.message"
    )
    assert initial.returncode == 86
    with sqlite3.connect(tmp_path / "run.sqlite") as db:
        if mutation in ("provider", "tool"):
            db.execute(
                "DELETE FROM tasks WHERE id=?",
                ("g0" if mutation == "provider" else "t0",),
            )
        else:
            payload = json.loads(
                db.execute("SELECT payload FROM tasks WHERE id='t0'").fetchone()[0]
            )
            if mutation == "fingerprint":
                payload["fingerprint"] = "changed"
            else:
                del payload["result"]
            db.execute(
                "UPDATE tasks SET payload=? WHERE id='t0'", (json.dumps(payload),)
            )
    result = experiments.invoke(tmp_path, "resume")
    assert result.returncode == 3, result.stderr
    assert json.loads(result.stdout)["phase"] == "blocked"
    assert experiments.lines(tmp_path) == 1
    with sqlite3.connect(tmp_path / "run.sqlite") as db:
        assert not db.execute("SELECT 1 FROM tasks WHERE id='g1'").fetchone()


@pytest.mark.parametrize("change", ["provider", "code", "workspace"])
def test_pinned_configuration_refuses_drift(tmp_path, change):
    initial = experiments.invoke(
        tmp_path, "start", "unsafe", "--crash-at", "g0.message"
    )
    assert initial.returncode == 86
    with sqlite3.connect(tmp_path / "run.sqlite") as db:
        config = json.loads(db.execute("SELECT config FROM admission").fetchone()[0])
        if change == "provider":
            config["identity"]["model"] = "changed"
        elif change == "code":
            config["code"] = "changed"
        else:
            config["workspace_identity"][1] += 1
        db.execute("UPDATE admission SET config=?", (json.dumps(config),))
    result = experiments.invoke(tmp_path, "resume")
    assert result.returncode == 3, result.stderr
    assert experiments.lines(tmp_path) == 0


def test_failed_receipt_poisons_open_loop_store(runtime, tmp_path):
    store = runtime.LoopStore(
        tmp_path / "run.sqlite", runtime.Faults(fail_write_at="t0.receipt.after_commit")
    )
    try:
        store.admit("request", runtime.configuration(tmp_path, "unsafe"))
        with pytest.raises(sqlite3.OperationalError, match="acknowledgement"):
            runtime.run(store)
        with pytest.raises(RuntimeError, match="reopen"):
            runtime.run(store)
        assert experiments.lines(tmp_path) == 1
    finally:
        store.close()


def test_admission_dedup_and_invalid_cli_paths(tmp_path):
    for action in ("resume", "inspect"):
        result = experiments.invoke(tmp_path, action)
        assert result.returncode == 2 and list(tmp_path.iterdir()) == []
    invalid = experiments.invoke(
        tmp_path, "start", "safe", "--workspace", str(tmp_path / "absent")
    )
    assert invalid.returncode == 2 and list(tmp_path.iterdir()) == []
    first = experiments.invoke(tmp_path, "start", "unsafe")
    assert first.returncode == 0, first.stderr
    duplicate = experiments.invoke(tmp_path, "start", "unsafe")
    assert duplicate.returncode == 0 and duplicate.stdout == first.stdout
    conflict = experiments.invoke(tmp_path, "start", "safe")
    assert conflict.returncode == 1 and experiments.lines(tmp_path) == 1


def test_prepared_arguments_cannot_diverge_on_replay(runtime, tmp_path, monkeypatch):
    initial = experiments.invoke(
        tmp_path, "start", "unsafe", "--crash-at", "g0.message"
    )
    assert initial.returncode == 86
    original = runtime.ReceiptTools.validated_arguments

    def changed(self, call):
        arguments = original(self, call)
        arguments["command"] = "printf changed > other.txt"
        return arguments

    monkeypatch.setattr(runtime.ReceiptTools, "validated_arguments", changed)
    store = runtime.LoopStore(tmp_path / "run.sqlite", runtime.Faults())
    try:
        assert runtime.run(store)["phase"] == "blocked"
        assert not (tmp_path / "effect.txt").exists()
        assert not (tmp_path / "other.txt").exists()
    finally:
        store.close()


def test_provider_request_cannot_diverge_on_replay(runtime, tmp_path, monkeypatch):
    initial = experiments.invoke(
        tmp_path, "start", "unsafe", "--crash-at", "g0.message"
    )
    assert initial.returncode == 86
    original = runtime.LoopAdapters.prepare

    def changed(self, history, active_input, turn_index, available_tools):
        prepared = original(self, history, active_input, turn_index, available_tools)
        request = runtime.replace(prepared.snapshot.request, system_prompt="changed")
        return runtime.AgentLoopRequestPreparation(
            history, runtime.snapshot_provider_request(request)
        )

    monkeypatch.setattr(runtime.LoopAdapters, "prepare", changed)
    store = runtime.LoopStore(tmp_path / "run.sqlite", runtime.Faults())
    try:
        assert runtime.run(store)["phase"] == "blocked"
        assert not (tmp_path / "effect.txt").exists()
    finally:
        store.close()


@pytest.mark.parametrize("cut", [None, "settled.committed", "lost-ack"])
def test_terminal_done_survives_code_drift_and_drains_observations(tmp_path, cut):
    flags = (
        []
        if cut is None
        else (
            ["--fail-write-at", "loop.settled.after_commit"]
            if cut == "lost-ack"
            else ["--crash-at", cut]
        )
    )
    initial = experiments.invoke(tmp_path, "start", "unsafe", *flags)
    assert initial.returncode == (
        0 if cut is None else (1 if cut == "lost-ack" else 86)
    )
    with sqlite3.connect(tmp_path / "run.sqlite") as db:
        phase, encoded = db.execute("SELECT phase,config FROM admission").fetchone()
        assert phase == "done"
        stored_summary = db.execute(
            "SELECT body FROM loop_meta WHERE key='settled'"
        ).fetchone()[0]
        config = json.loads(encoded)
        config["code"] = "different-code"
        db.execute("UPDATE admission SET config=?", (json.dumps(config),))
        assert db.execute("SELECT count(*) FROM observations").fetchone()[0] == (
            6 if cut is None else 5
        )
    conflict = experiments.invoke(tmp_path, "start", "unsafe")
    assert conflict.returncode == 1  # exact admission/config identity remains enforced
    for action in ("resume", "inspect", "resume"):
        resumed = experiments.invoke(tmp_path, action)
        assert resumed.returncode == 0, resumed.stderr
        summary = json.loads(resumed.stdout)
        assert summary["phase"] == "done" and summary["blocked_reason"] is None
        assert summary["run"] == json.loads(stored_summary)
        assert summary["observations"] == 6
        assert {row["id"]: row["attempts"] for row in summary["tasks"]} == {
            "g0": 1,
            "g1": 1,
            "t0": 1,
            "t1": 1,
        }
    assert experiments.lines(tmp_path) == 1


def test_terminal_blocked_reason_is_immutable_after_drift(tmp_path):
    assert (
        experiments.invoke(
            tmp_path, "start", "unsafe", "--crash-at", "t0.effect"
        ).returncode
        == 86
    )
    first = experiments.invoke(tmp_path, "resume")
    assert first.returncode == 3
    with sqlite3.connect(tmp_path / "run.sqlite") as db:
        config = json.loads(db.execute("SELECT config FROM admission").fetchone()[0])
        config["code"] = "different-code"
        db.execute("UPDATE admission SET config=?", (json.dumps(config),))
    for action in ("resume", "inspect"):
        resumed = experiments.invoke(tmp_path, action)
        assert resumed.returncode == 3 and resumed.stdout == first.stdout
    assert experiments.lines(tmp_path) == 1


def test_blocked_transition_cannot_overwrite_a_completed_run(runtime, tmp_path):
    initial = experiments.invoke(tmp_path, "start")
    assert initial.returncode == 0
    store = runtime.LoopStore(tmp_path / "run.sqlite", runtime.Faults())
    try:
        before = store.summary()
        with pytest.raises(runtime.ReplayBlocked):
            store.blocked("late refusal")
        assert store.summary() == before
    finally:
        store.close()
