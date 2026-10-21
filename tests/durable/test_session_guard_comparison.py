"""Host-independent DE02b1b guard recipe/schema/CLI tests, not Node evidence."""

import importlib.util
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def module():
    path = ROOT / "scripts/durable_session_guard_comparison.py"
    spec = importlib.util.spec_from_file_location("guard_tests", path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def guard_tests():
    path = ROOT / "tests/durable/test_session_storage_guard.py"
    spec = importlib.util.spec_from_file_location("b1a_guard_for_b1b", path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def rows(trace):
    return {s["id"]: s for s in trace["scenarios"]}


def step(m, row, op, handle=None):
    return m._step_of(row, op, handle)


def as_node(trace):
    value = json.loads(json.dumps(trace))
    value["backend"] = "node"
    return value


SESSION_FAILED = "Session failed after a storage error; close and reopen it"


def failed(cause):
    return {
        "kind": "session-failed",
        "message": SESSION_FAILED,
        "cause": {"kind": "scripted", "id": cause},
    }


def test_native_recipe_is_complete_meaningful_and_deterministic_20x():
    m = module()
    first, witness = m.native(m.load(m.INPUT))
    m.trace(first)
    assert [s["id"] for s in first["scenarios"]] == list(m.SCENARIOS)
    assert sum(len(s["steps"]) for s in first["scenarios"]) == 307
    assert witness["threads_started"] == witness["threads_reaped"] > 0
    for _ in range(19):
        again, _ = m.native(m.load(m.INPUT))
        assert again == first
    assert not [t for t in threading.enumerate() if t.name.startswith("de02b1b-")]
    r = rows(first)
    for name in ("roles-request-error", "roles-cancelled-error"):
        assert not [e for e in r[name]["effects"] if e["e"] == "report"]
        assert r[name]["end"] == {"reason": "closed"}
    rejected = [
        s["outcome"]["error"]
        for s in r["roles-request-error"]["steps"]
        if s["op"] == "call" and s["outcome"]["status"] == "rejected"
    ]
    assert rejected == [{"kind": "scripted", "id": "R1"}] * 14
    fulfilled = [s for s in r["roles-cancelled-success"]["steps"] if s["op"] == "call"]
    assert all(s["outcome"]["status"] == "fulfilled" for s in fulfilled)
    for name in (
        "commit-request-error-fatal",
        "mint-request-error-fatal",
        "commit-cancelled-fatal",
        "mint-cancelled-fatal",
        "read-error-fatal-task",
        "mint-error-fatal",
        "sync-throw",
    ):
        reports = [e for e in r[name]["effects"] if e["e"] == "report"]
        assert len(reports) == 1 and reports[0]["failure_visible"] is True, name
        assert r[name]["end"]["reason"] == "failed", name
    late = r["pi132-late-read-success"]
    assert step(m, late, "settle", "h1")["outcome"]["error"] == failed("E1")
    observed = step(m, late, "observe")["state"]
    assert observed["pending"] == ["h1"] and observed["close_entered"] == 0
    assert step(m, r["pi132-late-read-error"], "settle", "h1")["outcome"][
        "error"
    ] == failed("E1")
    paged = r["pi156-paged-close-drain"]
    assert step(m, paged, "probe", "page2")["outcome"]["error"] == {
        "kind": "session-closed",
        "message": "Session is closed",
    }
    assert [e["e"] for e in paged["effects"]][-3:] == [
        "return",
        "close-enter",
        "close-exit",
    ]
    closers = r["concurrent-closers-cancel-one"]
    assert step(m, closers, "settle", "second")["outcome"]["error"] == {
        "kind": "wait-cancelled"
    }
    assert [e["e"] for e in closers["effects"]].count("close-enter") == 1
    assert step(m, r["admission-after-fail-vs-closed"], "probe", "later")["outcome"][
        "error"
    ] == failed("E1")
    assert step(m, r["reentrant-report"], "settle", "reporter")["outcome"][
        "error"
    ] == failed("E1")
    healthy = r["pi192-healthy-close-failure"]
    assert healthy["end"] == {
        "reason": "failed",
        "error": {"kind": "scripted", "id": "C1"},
    }
    assert [e["e"] for e in healthy["effects"]][-3:] == [
        "close-enter",
        "close-exit",
        "report",
    ]
    refused = r["refused-commit-not-applied"]
    assert [s["outcome"]["value"] for s in refused["steps"] if s["op"] == "raw_read"][
        0
    ] == ["undefined"]


def cli(m, tmp_path, expected, actual, wanted):
    left = tmp_path / "expected.json"
    right = tmp_path / "actual.json"
    m.dump(left, expected)
    m.dump(right, actual)
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    p = subprocess.run(
        [sys.executable, m.__file__, "compare", str(left), str(right)],
        env={"PATH": os.defpath, "HOME": str(home)},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert p.returncode == wanted, p.stderr
    assert "Traceback" not in p.stderr
    return p


@pytest.fixture(scope="module")
def reference():
    m = module()
    raw, _ = m.native(m.load(m.INPUT))
    return as_node(raw)


@pytest.mark.parametrize("name", module().MUTANTS)
def test_schema_valid_guard_mutants_are_cli1_never_invalid2(tmp_path, reference, name):
    m = module()
    mutant = m._mutation(reference, name)
    assert mutant != reference
    m.trace(mutant)
    cli(m, tmp_path, reference, reference, 0)
    cli(m, tmp_path, reference, mutant, 1)


def _set_outcome(m, scenario, op, handle, outcome):
    def mutate(r):
        step(m, r[scenario], op, handle)["outcome"] = outcome

    return mutate


def _corruptions(m):
    late = ("pi132-late-read-success", "settle", "h1")
    return {
        "outcome-null": _set_outcome(m, *late, None),
        "unknown-ref-kind": _set_outcome(
            m, *late, {"status": "rejected", "error": {"kind": "error-name-map"}}
        ),
        "unknown-scripted-id": lambda r: r["fail-direct"]["end"].update(
            error={"kind": "scripted", "id": "E9"}
        ),
        "node-unsettled-outcome": _set_outcome(m, *late, {"status": "unsettled"}),
        "node-unsettled-end": lambda r: r["fail-direct"].update(
            end={"reason": "unsettled"}
        ),
        "node-pending-outside-blockers": lambda r: step(m, r["fail-direct"], "observe")[
            "state"
        ].update(pending=["later"]),
        "node-unreached-barrier": lambda r: step(
            m, r["pi132-late-read-success"], "await_entered", "h1"
        ).update(reached=False),
        "effect-unknown-handle": lambda r: r["fail-direct"]["effects"][0].update(
            h="ghost"
        ),
        "effect-bad-args": lambda r: r["fail-direct"]["effects"][0].update(
            args=["null"]
        ),
        "unknown-effect": lambda r: r["fail-direct"]["effects"].append(
            {"e": "close-twice"}
        ),
        "step-identity": lambda r: r["fail-direct"]["steps"][0].update(id="renamed"),
        "bad-value": lambda r: step(m, r["healthy-delegates"], "call", "mint")[
            "outcome"
        ].update(value=["string", "00"]),
        "observed-counter": lambda r: step(m, r["fail-direct"], "observe")[
            "state"
        ].update(reports=-1),
    }


TOP_LEVEL = {
    "version": ("schema_version", True),
    "phase": ("phase", "memory-current-base-a2b-v1"),
    "empty": ("scenarios", []),
}


def _corrupt(m, raw, corrupt):
    bad = json.loads(json.dumps(raw))
    if corrupt in TOP_LEVEL:
        key, value = TOP_LEVEL[corrupt]
        bad[key] = value
    else:
        _corruptions(m)[corrupt](rows(bad))
    return bad


INVALID = [
    "version",
    "phase",
    "empty",
    "outcome-null",
    "unknown-ref-kind",
    "unknown-scripted-id",
    "node-unsettled-outcome",
    "node-unsettled-end",
    "node-pending-outside-blockers",
    "node-unreached-barrier",
    "effect-unknown-handle",
    "effect-bad-args",
    "unknown-effect",
    "step-identity",
    "bad-value",
    "observed-counter",
]


@pytest.mark.parametrize("corrupt", INVALID)
def test_invalid_trace_is_cli2_not_a_valid_mismatch(tmp_path, reference, corrupt):
    m = module()
    bad = _corrupt(m, reference, corrupt)
    cli(m, tmp_path, reference, reference, 0)
    p = cli(m, tmp_path, reference, bad, 2)
    assert "Invalid guard evidence" in p.stderr


@pytest.mark.parametrize(
    "corrupt", ["node-unsettled-outcome", "node-unsettled-end", "node-pending"]
)
def test_python_only_unsettled_and_pending_are_valid_mismatch1(
    tmp_path, reference, corrupt
):
    m = module()
    native = json.loads(json.dumps(reference))
    native["backend"] = "python"
    r = rows(native)
    if corrupt == "node-unsettled-outcome":
        step(m, r["pi132-late-read-success"], "settle", "h1")["outcome"] = {
            "status": "unsettled"
        }
    elif corrupt == "node-unsettled-end":
        r["fail-direct"]["end"] = {"reason": "unsettled"}
    else:
        step(m, r["fail-direct"], "observe")["state"]["pending"] = ["later"]
    m.trace(native)
    cli(m, tmp_path, reference, native, 1)


def _first(steps, op, **exclude):
    """First step of ``op`` whose fields differ from every ``exclude`` value."""
    return next(
        s
        for s in steps
        if s["op"] == op and all(s.get(k) != v for k, v in exclude.items())
    )


def _raw_read_cursor(scenarios):
    raw = _first(scenarios["refused-commit-not-applied"]["steps"], "raw_read")
    raw["call"]["args"] = module().v.encode({"id": 30, "cursor_ref": "seed"})


def _recipe_change(value, change):
    scenarios = {s["id"]: s for s in value["scenarios"]}
    steps = scenarios["pi132-late-read-success"]["steps"]
    arms = [s for s in steps if s["op"] == "arm"]
    closed = scenarios["closed-then-probe"]["steps"]
    changes = {
        "call-in-seal-window": lambda: closed.remove(_first(closed, "quiesce")),
        "release-before-entry": lambda: steps.remove(_first(steps, "await_entered")),
        "await-end-not-last": lambda: steps.append(dict(steps[1], id="late")),
        "abort-without-cancellable": lambda: _first(
            scenarios["roles-cancelled-error"]["steps"], "call", handle="seed"
        ).update(facade="default"),
        "held-inline-call": lambda: arms[1].update(hold=True),
        "unsettled-handle": lambda: steps.remove(_first(steps, "settle")),
        "unknown-error": lambda: arms[1].update(outcome={"throw": "E9"}),
        "close-error-role": lambda: arms[1].update(outcome={"throw": "C1"}),
        "duplicate-step": lambda: steps[2].update(id=steps[1]["id"]),
        "raw-read-cursor-ref": lambda: _raw_read_cursor(scenarios),
        "scenario-drift": lambda: value["scenarios"].pop(),
        "unknown-step": lambda: steps.insert(0, {"id": "x", "op": "sleep"}),
    }
    changes[change]()


@pytest.mark.parametrize(
    "change",
    [
        "call-in-seal-window",
        "release-before-entry",
        "await-end-not-last",
        "abort-without-cancellable",
        "held-inline-call",
        "unsettled-handle",
        "unknown-error",
        "close-error-role",
        "duplicate-step",
        "raw-read-cursor-ref",
        "scenario-drift",
        "unknown-step",
    ],
)
def test_closed_recipe_rules_reject_unsafe_or_unpaired_recipes(change):
    m = module()
    value = m.load(m.INPUT)
    _recipe_change(value, change)
    with pytest.raises(m.o.EvidenceError):
        m.recipe(value)


@pytest.mark.parametrize(
    "change", ["phase", "ref", "closure", "hash", "parent", "a2b", "scope", "tests"]
)
def test_closed_guard_manifest_fails_before_runtime_or_output(
    tmp_path, monkeypatch, change, capsys
):
    m = module()
    value = m.load(m.MANIFEST)
    if change == "phase":
        value["phase"] = "memory-current-base-a2b-v1"
    elif change == "ref":
        value["reference_commit"] = "0" * 40
    elif change == "closure":
        value["closure"].pop("src/pipy_harness/native/durable/session.py")
    elif change == "hash":
        value["closure"]["src/pipy_harness/native/durable/session.py"] = "0" * 64
    elif change == "parent":
        value["baseline_manifest_sha256"] = "0" * 64
    elif change == "a2b":
        value["a2b_manifest_sha256"] = "0" * 64
    elif change == "scope":
        value["scope"] = "Full Session parity."
    else:
        value["upstream_tests"].pop("packages/durable/test/session-support.ts")
    path = tmp_path / "manifest.json"
    m.dump(path, value)
    monkeypatch.setattr(m, "MANIFEST", path)
    assert (
        m.main(
            [
                "run",
                "--acceptance",
                str(tmp_path / "absent"),
                "--output",
                str(tmp_path / "output"),
            ]
        )
        == 2
    )
    assert "Invalid guard evidence" in capsys.readouterr().err
    assert not (tmp_path / "output").exists()


def test_current_guard_manifest_is_closed_and_cascades():
    m = module()
    config = m.configuration()
    assert set(config["closure"]) == m.CLOSURE
    assert set(config["reference_sources"]) == m.SOURCES
    assert set(config["upstream_tests"]) == m.UPSTREAM_TESTS
    assert config["a2b_manifest_sha256"] == m.o.sha(m.m.MANIFEST)


@pytest.mark.parametrize(
    "mutation",
    [
        "none",
        "late-read-success-leak",
        "double-close",
        "invalid",
        "context-drift",
        "raw-drift",
        "node-backend",
    ],
)
def test_run_controller_classifies_and_attests_0_1_2_without_host_node(
    tmp_path, monkeypatch, capsys, mutation
):
    # Unit-only bootstrap/child replacements, NOT an actual pinned execution.
    m = module()
    parent = tmp_path / "parent"
    attestations = _unit_bootstrap(m, monkeypatch, parent)
    original = m.native
    observed = as_node(original(m.load(m.INPUT))[0])
    calls = []

    def native(value):
        raw, witness = original(value)
        if mutation == "invalid":
            raw["scenarios"][0]["steps"][0]["outcome"] = None
        elif mutation in m.MUTANTS:
            raw = m._mutation(raw, mutation)
        return raw, witness

    monkeypatch.setattr(m.o, "run", _unit_child(m, observed, calls, mutation))
    monkeypatch.setattr(m, "native", native)
    output = tmp_path / "output"
    wanted = 0 if mutation == "none" else 1 if mutation in m.MUTANTS else 2
    code = m.main(["run", "--acceptance", str(parent), "--output", str(output)])
    assert code == wanted
    # Reference-side drift is refused before the native child is started.
    early = mutation in {"context-drift", "raw-drift", "node-backend"}
    assert calls == ["node-version", "npm-version", "pi-session-guard"] + (
        [] if early else ["native-guard"]
    )
    if wanted == 2:
        diagnostic = capsys.readouterr().err
        assert "Invalid guard evidence" in diagnostic
        assert UNIT_DIAGNOSTICS[mutation] in diagnostic
        assert not (output / "result.json").exists()
        assert not (output / "mismatch-report.json").exists()
        return
    assert attestations == ["bundle", "reference", "reference", "bundle"]
    _assert_unit_result(m, output, wanted)


UNIT_DIAGNOSTICS = {
    "invalid": "Malformed outcome",
    "context-drift": "Guard reference context drift",
    "raw-drift": "raw guard error",
    "node-backend": "Reference trace backend drift",
}


def _unit_bootstrap(m, monkeypatch, parent):
    snapshot = parent / "reference/snapshot"
    snapshot.mkdir(parents=True)
    m.dump(
        parent / "acceptance.json",
        {"status": "acceptance-verified", "complete": True, "execution_id": "unit"},
    )
    for name in ("run.json", "source-sha256.json", "dependency-sha256.json"):
        m.dump(snapshot.parent / name, {})
    monkeypatch.setattr(
        m, "configuration", lambda: {"reference_sources": {}, "upstream_tests": {}}
    )
    monkeypatch.setattr(
        m.o, "manifest", lambda: {"runtime": {"node": "unit-node", "npm": "unit-npm"}}
    )
    attestations = []
    monkeypatch.setattr(
        m.v.artifacts, "attest", lambda *_: attestations.append("bundle")
    )
    monkeypatch.setattr(
        m.o.acceptance,
        "attest_reference",
        lambda *_: attestations.append("reference") or {"installed_dependencies": []},
    )
    monkeypatch.setattr(m.o, "verify_sources", lambda *_: None)
    monkeypatch.setattr(m.o, "verify_resolutions", lambda *_: {})
    monkeypatch.setattr(m.o, "module_dependencies", lambda *_: None)
    monkeypatch.setattr(m, "execute_mutants", lambda *_: [])
    return attestations


def _unit_child(m, observed, calls, mutation):
    def child(command, cwd, folder, identity, env, timeout):
        calls.append(identity)
        if identity.endswith("-version"):
            return ("unit-" + identity.removesuffix("-version")).encode()
        if identity == "native-guard":
            product, witness = m.native(m.load(m.INPUT))
            m.dump(folder / "native.json", product)
            m.dump(folder / "native-context.json", witness)
            m.dump(folder / "native-guard.json", {"exit_code": 0})
            return b""
        backend = "python" if mutation == "node-backend" else "node"
        m.dump(folder / "observed.json", dict(observed, backend=backend))
        m.dump(folder / "resolutions.json", [])
        m.dump(folder / "pi-session-guard.json", {"exit_code": 0})
        driver = dict(m.CONTEXT_DRIVER)
        if mutation == "context-drift":
            driver["own_members"] = ["constructor", "guarded", "report", "x"]
        context = {
            "schema_version": 1,
            "phase": m.PHASE,
            "scenarios": len(m.SCENARIOS),
            "driver": driver,
        }
        m.dump(folder / "reference-context.json", context)
        errors = [{"scenario": "ghost"}] if mutation == "raw-drift" else []
        m.dump(folder / "guard-raw.json", {"schema_version": 1, "errors": errors})
        return b""

    return child


def _assert_unit_result(m, output, wanted):
    result = m.load(output / "result.json")
    assert result["status"] == ("PASS" if wanted == 0 else "mismatch")
    assert result["fresh_reference_bootstrap"] is False
    assert result["session_line"] is result["tx"] is result["full_session_parity"]
    assert result["full_session_parity"] is False
    for name, digest in result["artifacts_sha256"].items():
        assert m.o.sha(output / name) == digest
    if not wanted:
        assert not (output / "mismatch-report.json").exists()
        return
    report = m.load(output / "mismatch-report.json")
    assert m.o.sha(output / report["expected"]) == report["expected_sha256"]
    assert m.o.sha(output / report["actual"]) == report["actual_sha256"]
    expected = m.load(output / report["expected"])
    assert m.comparison(expected, m.load(output / report["actual"])) == 1


def test_mutant_driver_rejects_invalid2_even_after_positive0(
    tmp_path, monkeypatch, reference
):
    m = module()
    m.dump(tmp_path / "observed.json", reference)
    calls = []

    def child(command, cwd, folder, identity, env, timeout):
        code = 0 if identity.endswith("positive") else 2
        calls.append((identity, code))
        m.dump(folder / (identity + ".json"), {"exit_code": code})
        if code:
            raise m.o.EvidenceError("unit invalid child")
        return b""

    monkeypatch.setattr(m.o, "run", child)
    with pytest.raises(m.o.EvidenceError, match="valid mismatch1, never invalid2"):
        m.execute_mutants(
            tmp_path, reference, {"HOME": str(tmp_path), "PATH": os.defpath}
        )
    assert calls == [
        ("role-exemption-flip-positive", 0),
        ("role-exemption-flip-negative", 2),
    ]


# b1a's private source mutants that paired recipes reach, and the three that only
# b1a's native race/BaseException scenarios reach (no paired Pi analogue).
NEGATIVE_CONTROLS = {
    "read-role-exemption": 1,
    "post-call-health-check": 1,
    "late-error-health-check": 1,
    "admission-seal": 1,
    "registration": 1,
    "drain-wait": 1,
    "first-cause-only": 1,
    "close-once": 1,
    "split-success-settle": 0,
    "split-error-settle": 0,
    "close-error-replaces-inflight": 0,
}


def test_negative_control_inventory_matches_b1a_mutants():
    assert set(NEGATIVE_CONTROLS) == set(guard_tests().MUTANTS)
    assert sum(NEGATIVE_CONTROLS.values()) == 8


@pytest.mark.parametrize("name", sorted(NEGATIVE_CONTROLS))
def test_native_negative_controls_through_actual_paired_recipes(name):
    m = module()
    unmodified, _ = m.native(m.load(m.INPUT))
    variant, witness = m.native(m.load(m.INPUT), guard_tests().mutant(name), wait=1.0)
    m.trace(variant)
    assert witness["threads_started"] == witness["threads_reaped"]
    assert m.comparison(unmodified, variant) == NEGATIVE_CONTROLS[name]
    assert not [t for t in threading.enumerate() if t.name.startswith("de02b1b-")]


def test_unreaped_native_thread_is_invalid_evidence():
    m = module()
    value = m.load(m.INPUT)
    scenario = value["scenarios"][0]
    run = m._Run(m.real_session, value, scenario, 0.05)
    gate = threading.Event()
    run.start("stuck", gate.wait)
    try:
        with pytest.raises(m.o.EvidenceError, match="Unreaped native guard thread"):
            run.cleanup()
    finally:
        gate.set()
        run.threads["stuck"].join(5)
    assert not run.threads["stuck"].is_alive()


STUCK_CHILD = """import importlib.util, sys, threading
spec = importlib.util.spec_from_file_location("g", sys.argv[1])
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
m.WAIT = 0.05
cleanup = m._Run.cleanup
def stuck_cleanup(run):
    if not run.threads.get("stuck"):
        run.start("stuck", threading.Event().wait)  # never released
    cleanup(run)
m._Run.cleanup = stuck_cleanup
m.main(["native", sys.argv[2]])
"""


def test_native_child_exits2_promptly_with_a_never_returning_worker(tmp_path):
    m = module()
    output = tmp_path / "output"
    output.mkdir()
    p = subprocess.run(
        [sys.executable, "-c", STUCK_CHILD, m.__file__, str(output)],
        env={"PATH": os.defpath, "HOME": str(tmp_path)},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert p.returncode == 2, p.stderr
    assert "Unreaped native guard thread" in p.stderr
    assert "Traceback" not in p.stderr
    assert not (output / "native.json").exists()


HANGING_NATIVE = """import os, pathlib, sys, threading
pathlib.Path(sys.argv[1]).write_text(str(os.getpid()))
threading.Thread(target=threading.Event().wait, daemon=False).start()
"""


def test_run_kills_and_reaps_a_hanging_native_child_and_exits2(
    tmp_path, monkeypatch, capsys
):
    # Unit bootstrap, but the native child is a REAL process that never exits.
    m = module()
    parent = tmp_path / "parent"
    _unit_bootstrap(m, monkeypatch, parent)
    real_run = m.o.run
    observed = as_node(m.native(m.load(m.INPUT))[0])
    calls = []
    fake = _unit_child(m, observed, calls, "none")
    pid_file = tmp_path / "native.pid"

    def child(command, cwd, folder, identity, env, timeout):
        if identity != "native-guard":
            return fake(command, cwd, folder, identity, env, timeout)
        calls.append(identity)
        return real_run(command, cwd, folder, identity, env, timeout)

    monkeypatch.setattr(m.o, "run", child)
    monkeypatch.setattr(m, "NATIVE_TIMEOUT", 3)
    monkeypatch.setattr(
        m,
        "_native_command",
        lambda output: [sys.executable, "-c", HANGING_NATIVE, str(pid_file)],
    )
    output = tmp_path / "output"
    started = time.monotonic()
    code = m.main(["run", "--acceptance", str(parent), "--output", str(output)])
    assert code == 2 and time.monotonic() - started < 30
    assert "Child deadline/interruption: native-guard" in capsys.readouterr().err
    assert calls[-1] == "native-guard"
    assert m.load(output / "native-guard.json")["timed_out"] is True
    pid = int(pid_file.read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
    for name in ("result.json", "mismatch-report.json", "native.json"):
        assert not (output / name).exists()
