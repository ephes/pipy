"""Host-independent DE02b3 Session recipe/schema/CLI tests, not Node evidence."""

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


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def module():
    return _load(
        "session_commit_tests", ROOT / "scripts/durable_session_commit_comparison.py"
    )


def b2b():
    return _load("b2b_for_b3", ROOT / "tests/durable/test_session_commit.py")


def as_node(traces):
    """The async native trace (every scenario) relabelled as a reference trace."""
    value = json.loads(json.dumps(traces["async"]))
    value["backend"] = "node"
    del value["mode"]
    return value


def rows(trace):
    return {s["id"]: s for s in trace["scenarios"]}


def step(m, row, op, **match):
    return m._trace_step(row, op, **match)


@pytest.fixture(scope="module")
def traces():
    m = module()
    return m.native(m.load(m.INPUT))[0]


@pytest.fixture(scope="module")
def reference(traces):
    return as_node(traces)


def test_native_recipe_is_complete_meaningful_and_deterministic(traces):
    m = module()
    again, witness = m.native(m.load(m.INPUT))
    assert again == traces
    assert witness["threads_started"] == witness["threads_reaped"] > 0
    assert not [t for t in threading.enumerate() if t.name.startswith("de02b3-")]
    assert [len(t["scenarios"]) for t in traces.values()] == [35, 41]
    assert sum(len(s["steps"]) for s in traces["async"]["scenarios"]) == 405
    # Both modes agree on every scenario they share.
    assert m.projection(traces["sync"], "sync") == m.projection(traces["async"], "sync")
    r = rows(traces["sync"])
    cancelled = step(m, r["queued-view-cancel"], "settle", job="j2")["outcome"]
    assert cancelled == {"status": "rejected", "error": {"kind": "commit-cancelled"}}
    marks = [e["mark"] for e in r["queued-view-cancel"]["effects"] if e["e"] == "mark"]
    assert marks == ["a-in", "c-in"]
    identities = [
        s for s in r["d405-assembly-rollback"]["steps"] if s["op"] == "identity"
    ]
    assert [s["outcome"]["value"] for s in identities] == [["bool", True]] * 2
    poisoned = [e["e"] for e in r["h328-internal-listener-poisons"]["effects"]][6:10]
    assert poisoned == ["publish", "close-listener", "report", "publish"]
    window = [
        s for s in r["busy-line-guard-window"]["steps"] if s["op"] == "guard_read"
    ]
    assert [s["outcome"]["status"] for s in window] == ["fulfilled", "rejected"]
    applied = step(
        m, r["after-ack-applied"], "raw_read", call={"op": "entry", "args": {"id": 3}}
    )
    assert applied["outcome"]["status"] == "fulfilled"
    late = rows(traces["async"])["d311-no-late-init"]["effects"]
    assert {e["e"] for e in late} == {"call", "return", "close-enter", "close-exit"}
    nested = step(
        m, rows(traces["async"])["ingress-pending-nested-copy"], "call", job="j1"
    )
    data = m.v.decode(nested["outcome"]["value"])["data"]
    assert data == {"nested": {"value": 2}}


def cli(m, tmp_path, expected, actual, wanted):
    left, right = tmp_path / "expected.json", tmp_path / "actual.json"
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


def test_reference_equals_both_native_modes_through_the_cli(
    tmp_path, traces, reference
):
    m = module()
    for mode in m.MODES:
        cli(m, tmp_path, reference, traces[mode], 0)


@pytest.mark.parametrize("name", module().MUTANTS)
def test_schema_valid_session_mutants_are_cli1_never_invalid2(
    tmp_path, traces, reference, name
):
    m = module()
    mutant = m._mutation(reference, name)
    assert mutant != reference
    modes = m.mutant_modes(name)
    for mode in modes:
        assert m.comparison(mutant, traces[mode]) == 1
    cli(m, tmp_path, mutant, traces[modes[0]], 1)


def _corruptions(m):
    def at(row, op, **match):
        return lambda r: step(m, r[row], op, **match)

    late = at("late-read-discard", "settle", job="j1")
    return {
        "outcome-null": lambda r: late(r).update(outcome=None),
        "unknown-ref-kind": lambda r: late(r).update(
            outcome={"status": "rejected", "error": {"kind": "error-name-map"}}
        ),
        "unknown-scripted-id": lambda r: r["fail-under-held-line"]["end"].update(
            error={"kind": "scripted", "id": "E9"}
        ),
        "node-unsettled-outcome": lambda r: late(r).update(
            outcome={"status": "unsettled"}
        ),
        "node-unsettled-end": lambda r: r["fail-under-held-line"].update(
            end={"reason": "unsettled"}
        ),
        "node-unreached-barrier": lambda r: at("late-read-discard", "await_entered")(
            r
        ).update(reached=False),
        "node-pending-outside-blockers": lambda r: at(
            "d284-pending-acquisition", "observe"
        )(r)["state"].update(pending=["closer"]),
        "unknown-effect": lambda r: r["fail-under-held-line"]["effects"].append(
            {"e": "close-twice"}
        ),
        "unknown-gate": lambda r: next(
            e for e in r["late-read-discard"]["effects"] if e["e"] == "call"
        ).update(gate="g9"),
        "gate-consumed-twice": lambda r: r["late-read-discard"]["effects"].append(
            dict(
                next(
                    e
                    for e in r["late-read-discard"]["effects"]
                    if e.get("gate") == "g1"
                )
            )
        ),
        "unknown-listener": lambda r: next(
            e
            for e in r["h328-internal-listener-poisons"]["effects"]
            if e["e"] == "publish"
        ).update(listener="L9"),
        "bad-value": lambda r: at("tb132-minted-ids", "call", job="j1")(r).update(
            outcome={"status": "fulfilled", "value": ["string", "0"]}
        ),
        "step-identity": lambda r: r["fail-under-held-line"]["steps"][0].update(id="x"),
        "report-type": lambda r: next(
            e for e in r["fail-under-held-line"]["effects"] if e["e"] == "report"
        ).update(type="host_error"),
        "unknown-read-method": lambda r: next(
            e for e in r["tb82-121-read-after-write"]["effects"] if e["e"] == "caught"
        )["error"].update(method="scanConversations"),
    }


TOP_LEVEL = {
    "version": ("schema_version", True),
    "phase": ("phase", "session-guard-b1b-v1"),
    "empty": ("scenarios", []),
}


@pytest.mark.parametrize("corrupt", sorted(TOP_LEVEL) + sorted(_corruptions(module())))
def test_invalid_trace_is_cli2_not_a_valid_mismatch(tmp_path, reference, corrupt):
    m = module()
    bad = json.loads(json.dumps(reference))
    if corrupt in TOP_LEVEL:
        key, value = TOP_LEVEL[corrupt]
        bad[key] = value
    else:
        _corruptions(m)[corrupt](rows(bad))
    cli(m, tmp_path, reference, reference, 0)
    assert "Invalid session evidence" in cli(m, tmp_path, reference, bad, 2).stderr


def test_python_only_unsettled_is_a_valid_mismatch_and_modes_never_mix(
    tmp_path, traces, reference
):
    m = module()
    native = json.loads(json.dumps(traces["sync"]))
    step(m, rows(native)["late-read-discard"], "settle", job="j1")["outcome"] = {
        "status": "unsettled"
    }
    rows(native)["fail-under-held-line"]["end"] = {"reason": "unsettled"}
    cli(m, tmp_path, reference, native, 1)
    assert (
        "Native mode drift"
        in cli(m, tmp_path, traces["sync"], traces["async"], 2).stderr
    )


def _recipe_change(m, value, change):
    s = {item["id"]: item for item in value["scenarios"]}
    steps = s["late-read-discard"]["steps"]
    programs = value["programs"]
    changes = {
        "scenario-drift": lambda: value["scenarios"].pop(),
        "unknown-step": lambda: steps.insert(0, {"id": "x", "op": "sleep"}),
        "await-end-not-last": lambda: steps.append(dict(steps[0], id="late")),
        "duplicate-step": lambda: steps[1].update(id=steps[0]["id"]),
        "release-before-entry": lambda: steps.remove(
            next(x for x in steps if x["op"] == "await_entered")
        ),
        "unjoined-effect": lambda: s["queued-view-cancel"]["steps"].remove(
            next(
                x for x in s["queued-view-cancel"]["steps"] if x["op"] == "await_queued"
            )
        ),
        "idle-window-guard": lambda: s["idle-close-quiesce"]["steps"].remove(
            next(x for x in s["idle-close-quiesce"]["steps"] if x["op"] == "quiesce")
        ),
        "ambiguous-bind": lambda: next(
            x
            for x in s["d405-assembly-rollback"]["steps"]
            if x.get("bind") == "live-before"
        ).update(bind="live"),
        "unknown-program-op": lambda: programs["note"].insert(0, {"sleep": 1}),
        "sync-start-tx": lambda: programs["note"].insert(
            0, {"start_tx": "conversation", "id": 2, "handle": "h"}
        ),
        "abort-without-cancellable": lambda: next(
            x
            for x in s["cancelled-tx-read-exempt"]["steps"]
            if x.get("job") == "j1" and x["op"] == "call"
        ).pop("context"),
        "held-unreleased": lambda: s["queued-view-cancel"]["steps"].remove(
            next(x for x in s["queued-view-cancel"]["steps"] if x["op"] == "release")
        ),
        "dangling-cell": lambda: value["cells"]["DE02:immutable-ids"]["paired"].append(
            "ghost"
        ),
        "unmapped-scenario": lambda: value["cells"]["DE02:immutable-ids"][
            "paired"
        ].remove("tb132-minted-ids"),
        "unknown-error": lambda: steps[2].update(outcome={"throw_before": "E9"}),
        "reject-in-sync": lambda: s["h466-host-close-listeners-contained"]["listeners"][
            0
        ].update(action="reject"),
    }
    changes[change]()


RECIPE_CHANGES = [
    "scenario-drift",
    "unknown-step",
    "await-end-not-last",
    "duplicate-step",
    "release-before-entry",
    "unjoined-effect",
    "idle-window-guard",
    "ambiguous-bind",
    "unknown-program-op",
    "sync-start-tx",
    "abort-without-cancellable",
    "held-unreleased",
    "dangling-cell",
    "unmapped-scenario",
    "unknown-error",
    "reject-in-sync",
]


@pytest.mark.parametrize("change", RECIPE_CHANGES)
def test_closed_recipe_rules_reject_unsafe_or_unpaired_recipes(change):
    m = module()
    value = m.load(m.INPUT)
    _recipe_change(m, value, change)
    with pytest.raises(m.o.EvidenceError):
        m.recipe(value)


def test_cell_map_cites_existing_native_evidence_and_docs():
    m = module()
    value = m.load(m.INPUT)
    m.cell_evidence(value)
    names = m.native_names()
    assert (
        "cancel-view-queued-never-enters-fifo-kept" in names["test_session_commit.py"]
    )
    assert (
        "tables82-reads-only-before-first-table-write"
        in names["test_session_tx_core.py"]
    )
    value["cells"]["DE02b:reentry"]["native"].append("test_session_commit.py::ghost")
    with pytest.raises(m.o.EvidenceError, match="Dangling native evidence"):
        m.cell_evidence(value)


@pytest.mark.parametrize(
    "change", ["phase", "ref", "closure", "hash", "b1b", "scope", "tests", "sources"]
)
def test_closed_session_manifest_fails_before_runtime_or_output(
    tmp_path, monkeypatch, change, capsys
):
    m = module()
    value = m.load(m.MANIFEST)
    core = "src/pipy_harness/native/durable/session_core.py"
    edits = {
        "phase": lambda: value.update(phase="session-guard-b1b-v1"),
        "ref": lambda: value.update(reference_commit="0" * 40),
        "closure": lambda: value["closure"].pop(core),
        "hash": lambda: value["closure"].update({core: "0" * 64}),
        "b1b": lambda: value.update(b1b_manifest_sha256="0" * 64),
        "scope": lambda: value.update(scope="Full Session parity."),
        "tests": lambda: value["upstream_tests"].pop(
            "packages/durable/test/session-support.ts"
        ),
        "sources": lambda: value["reference_sources"].pop(
            "packages/durable/src/session/transaction.ts"
        ),
    }
    edits[change]()
    path = tmp_path / "manifest.json"
    m.dump(path, value)
    monkeypatch.setattr(m, "MANIFEST", path)
    args = [
        "run",
        "--acceptance",
        str(tmp_path / "absent"),
        "--output",
        str(tmp_path / "out"),
    ]
    assert m.main(args) == 2
    assert "Invalid session evidence" in capsys.readouterr().err
    assert not (tmp_path / "out").exists()


def test_current_session_manifest_is_closed_and_cascades():
    m = module()
    config = m.configuration()
    assert set(config["closure"]) == m.CLOSURE
    assert set(config["reference_sources"]) == m.SOURCES
    assert set(config["upstream_tests"]) == m.UPSTREAM_TESTS
    assert {k: config[k] for k in m.PARENT_KEYS} == m.parents()


UNIT_DIAGNOSTICS = {
    "invalid": "outcome",
    "context-drift": "Session reference context drift",
    "raw-drift": "raw session error",
    "node-backend": "Reference trace backend drift",
}


def _unit_bootstrap(m, monkeypatch, parent, real_mutants=False):
    snapshot = parent / "reference/snapshot"
    snapshot.mkdir(parents=True)
    acceptance = {
        "status": "acceptance-verified",
        "complete": True,
        "execution_id": "unit",
    }
    m.dump(parent / "acceptance.json", acceptance)
    for name in ("run.json", "source-sha256.json", "dependency-sha256.json"):
        m.dump(snapshot.parent / name, {})
    monkeypatch.setattr(
        m, "configuration", lambda: {"reference_sources": {}, "upstream_tests": {}}
    )
    runtime = {"runtime": {"node": "unit-node", "npm": "unit-npm"}}
    monkeypatch.setattr(m.o, "manifest", lambda: runtime)
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
    if not real_mutants:
        monkeypatch.setattr(m, "execute_mutants", lambda *_: [])
    return attestations


def _unit_child(m, observed, products, calls, mutation):
    def child(command, cwd, folder, identity, env, timeout):
        calls.append(identity)
        if identity.endswith("-version"):
            return ("unit-" + identity.removesuffix("-version")).encode()
        if identity == "native-session":
            for mode, product in products.items():
                m.dump(folder / f"native-{mode}.json", product)
            witness = {"schema_version": 1, "phase": m.PHASE, "session_module": "unit"}
            witness |= {
                "modes": list(m.MODES),
                "threads_started": 1,
                "threads_reaped": 1,
            }
            m.dump(folder / "native-context.json", witness | {"executor_workers": 8})
            m.dump(folder / "native-session.json", {"exit_code": 0})
            return b""
        native = {"backend": "python", "mode": "async"}
        m.dump(
            folder / "observed.json",
            observed | (native if mutation == "node-backend" else {}),
        )
        m.dump(folder / "resolutions.json", [])
        m.dump(folder / "pi-session-commit.json", {"exit_code": 0})
        changes = m.document_changes(observed)
        driver = dict(m.CONTEXT_DRIVER, base_document_changes=changes)
        if mutation == "context-drift":
            driver["sweeps"] = 1
        context = {"schema_version": 1, "phase": m.PHASE, "scenarios": len(m.SCENARIOS)}
        m.dump(folder / "reference-context.json", context | {"driver": driver})
        errors = [{"scenario": "ghost"}] if mutation == "raw-drift" else []
        m.dump(folder / "session-raw.json", {"schema_version": 1, "errors": errors})
        return b""

    return child


@pytest.mark.parametrize(
    "mutation",
    ["none", "late-read-leak", "invalid", "context-drift", "raw-drift", "node-backend"],
)
def test_run_controller_classifies_and_attests_0_1_2_without_host_node(
    tmp_path, monkeypatch, capsys, traces, reference, mutation
):
    # Unit-only bootstrap/child replacements, NOT an actual pinned execution.
    m = module()
    parent = tmp_path / "parent"
    attestations = _unit_bootstrap(m, monkeypatch, parent)
    products = json.loads(json.dumps(traces))
    if mutation == "invalid":
        products["async"]["scenarios"][0]["steps"][0]["outcome"] = None
    elif mutation in m.MUTANTS:
        products["async"] = m._mutation(products["async"], mutation)
    calls = []
    monkeypatch.setattr(
        m.o, "run", _unit_child(m, reference, products, calls, mutation)
    )
    output = tmp_path / "output"
    wanted = 0 if mutation == "none" else 1 if mutation in m.MUTANTS else 2
    assert (
        m.main(["run", "--acceptance", str(parent), "--output", str(output)]) == wanted
    )
    early = mutation in {"context-drift", "raw-drift", "node-backend"}
    assert calls == ["node-version", "npm-version", "pi-session-commit"] + (
        [] if early else ["native-session"]
    )
    if wanted == 2:
        diagnostic = capsys.readouterr().err
        assert "Invalid session evidence" in diagnostic
        assert UNIT_DIAGNOSTICS[mutation] in diagnostic
        assert not (output / "result.json").exists()
        assert not (output / "mismatch-report.json").exists()
        return
    assert attestations == ["bundle", "reference", "reference", "bundle"]
    result = m.load(output / "result.json")
    assert result["status"] == ("PASS" if wanted == 0 else "mismatch")
    assert result["comparisons"] == {"sync": 0, "async": wanted}
    assert (
        result["fresh_reference_bootstrap"] is False
        and result["p_rows_closed"] is False
    )
    for name, digest in result["artifacts_sha256"].items():
        assert m.o.sha(output / name) == digest
    assert (output / "mismatch-report.json").exists() == bool(wanted)


def test_valid_native_mismatch_still_runs_real_mutant_controls_and_exits1(
    tmp_path, monkeypatch, capsys, traces, reference
):
    # Unit Node/native children through the existing ``o.run`` seam; the real
    # execute_mutants() runs every control as an actual CLI compare child.
    m = module()
    parent = tmp_path / "parent"
    attestations = _unit_bootstrap(m, monkeypatch, parent, real_mutants=True)
    products = json.loads(json.dumps(traces))
    products["async"] = m._mutation(products["async"], "late-read-leak")
    calls, controls = [], []
    unit = _unit_child(m, reference, products, calls, "late-read-leak")
    real = m.o.run

    def child(command, cwd, folder, identity, env, timeout):
        if identity.endswith(("-positive", "-negative")):
            controls.append(identity)
            return real(command, cwd, folder, identity, env, timeout)
        return unit(command, cwd, folder, identity, env, timeout)

    monkeypatch.setattr(m.o, "run", child)
    output = tmp_path / "output"
    code = m.main(["run", "--acceptance", str(parent), "--output", str(output)])
    assert "Invalid session evidence" not in capsys.readouterr().err
    assert code == 1
    assert calls == [
        "node-version",
        "npm-version",
        "pi-session-commit",
        "native-session",
    ]
    assert attestations == ["bundle", "reference", "reference", "bundle"]
    result = m.load(output / "result.json")
    assert result["status"] == "mismatch"
    assert result["comparisons"] == {"sync": 0, "async": 1}
    assert controls == [
        f"{name}-{label}" for name in m.MUTANTS for label in ("positive", "negative")
    ]
    for identity in controls:
        wanted = 0 if identity.endswith("positive") else 1
        assert m.load(output / (identity + ".json"))["exit_code"] == wanted
    assert [x["id"] for x in result["mutants"]] == list(m.MUTANTS)
    for item in result["mutants"]:
        assert (item["positive_exit"], item["negative_exit"]) == (0, 1)
        assert item["sha256"] == m.o.sha(output / (item["id"] + "-mutant.json"))
    digests = result["artifacts_sha256"]
    assert "mismatch-report.json" in digests
    for name, digest in digests.items():
        assert m.o.sha(output / name) == digest
    report = m.load(output / "mismatch-report.json")
    assert {mode: x["code"] for mode, x in report["modes"].items()} == {
        "sync": 0,
        "async": 1,
    }
    for mode, item in report["modes"].items():
        assert item["actual_sha256"] == digests[f"native-{mode}.json"]
        assert item["expected_sha256"] == digests["observed.json"]


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
    env = {"HOME": str(tmp_path), "PATH": os.defpath}
    with pytest.raises(m.o.EvidenceError, match="valid mismatch1, never invalid2"):
        m.execute_mutants(tmp_path, reference, env)
    first = m.MUTANTS[0]
    assert calls == [(f"{first}-positive", 0), (f"{first}-negative", 2)]


# b2b's private source mutants through the paired recipes: 1 = the native trace
# differs in that mode, 0 = Python-only/native-only behaviour no paired recipe
# reaches, 2 = the build fails closed (a backend close run on the loop blocks a
# recipe-held close, so the driver reports a fault).
NEGATIVE_CONTROLS = {
    "line-hold-during-drain": (0, 1),
    "pending-check": (0, 1),
    "queued-cancel-cut": (1, 1),
    "post-callback-health-check": (1, 1),
    "internal-host-routing": (1, 1),
    "line-drain-close-predicate": (1, 1),
    "close-listener-ordering": (1, 1),
    "adopt-fail-to-fail": (0, 0),
    "shield": (0, 0),
    "txop-shield": (0, 0),
    "bulk-reject": (0, 0),
    "in-flight-memo": (0, 0),
    "line-hand-off-atomic": (0, 0),
    "close-hook-done-gate": (0, 0),
    "wait-closed-reentry": (0, 0),
    "detach-outside-lock": (0, 0),
    "nested-close-returns": (0, 0),
    "background-close-outcome-reported": (0, 0),
    "close-off-loop": (2, 2),
}


def test_negative_control_inventory_matches_b2b_mutants():
    assert set(NEGATIVE_CONTROLS) == set(b2b().MUTANTS)


@pytest.mark.parametrize("name", sorted(NEGATIVE_CONTROLS))
def test_b2b_source_mutants_through_the_native_runner(traces, name):
    m = module()
    expected = dict(zip(m.MODES, NEGATIVE_CONTROLS[name], strict=True))
    if expected["sync"] == 2:
        with pytest.raises(m.o.EvidenceError, match="never released"):
            m.native(m.load(m.INPUT), b2b().mutant(name), wait=1.0)
    else:
        variant, witness = m.native(m.load(m.INPUT), b2b().mutant(name), wait=1.0)
        assert witness["threads_started"] == witness["threads_reaped"]
        assert {
            mode: m.comparison(traces[mode], variant[mode]) for mode in m.MODES
        } == expected
    assert not [t for t in threading.enumerate() if t.name.startswith("de02b3-")]


def test_unreaped_native_thread_is_invalid_evidence():
    m = module()
    value = m.load(m.INPUT)
    run = m._Run(m.REAL, value, value["scenarios"][0], "sync", 0.05)
    gate = threading.Event()
    run.start("stuck", gate.wait)
    try:
        with pytest.raises(m.o.EvidenceError, match="Unreaped native session thread"):
            run.execute()
    finally:
        gate.set()
        for thread in run.threads:
            thread.join(5)
    assert not [t for t in run.threads if t.is_alive()]


STUCK_CHILD = """import importlib.util, sys, threading
spec = importlib.util.spec_from_file_location("s", sys.argv[1])
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
m.WAIT = 0.05
execute = m._Run.execute
def stuck(run):
    if not run.threads:
        run.start("stuck", threading.Event().wait)  # never released
    return execute(run)
m._Run.execute = stuck
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
        timeout=120,
    )
    assert p.returncode == 2, p.stderr
    assert "Unreaped native session thread" in p.stderr
    assert "Traceback" not in p.stderr
    assert not (output / "native-sync.json").exists()


HANGING_NATIVE = """import os, pathlib, sys, threading
pathlib.Path(sys.argv[1]).write_text(str(os.getpid()))
threading.Thread(target=threading.Event().wait, daemon=False).start()
"""


def test_run_kills_and_reaps_a_hanging_native_child_and_exits2(
    tmp_path, monkeypatch, capsys, traces, reference
):
    # Unit bootstrap, but the native child is a REAL process that never exits.
    m = module()
    _unit_bootstrap(m, monkeypatch, tmp_path / "parent")
    real_run = m.o.run
    calls = []
    fake = _unit_child(m, reference, traces, calls, "none")
    pid_file = tmp_path / "native.pid"

    def child(command, cwd, folder, identity, env, timeout):
        if identity != "native-session":
            return fake(command, cwd, folder, identity, env, timeout)
        calls.append(identity)
        return real_run(command, cwd, folder, identity, env, timeout)

    monkeypatch.setattr(m.o, "run", child)
    monkeypatch.setattr(m, "NATIVE_TIMEOUT", 3)
    command = [sys.executable, "-c", HANGING_NATIVE, str(pid_file)]
    monkeypatch.setattr(m, "_native_command", lambda output: command)
    output = tmp_path / "output"
    started = time.monotonic()
    args = ["run", "--acceptance", str(tmp_path / "parent"), "--output", str(output)]
    assert m.main(args) == 2 and time.monotonic() - started < 30
    assert "Child deadline/interruption: native-session" in capsys.readouterr().err
    assert m.load(output / "native-session.json")["timed_out"] is True
    with pytest.raises(ProcessLookupError):
        os.kill(int(pid_file.read_text()), 0)
    for name in ("result.json", "mismatch-report.json", "native-sync.json"):
        assert not (output / name).exists()
