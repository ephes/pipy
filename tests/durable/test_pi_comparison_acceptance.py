"""Host-independent acceptance contracts; actual pinned execution is a separate gate."""

from __future__ import annotations

import copy
import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/durable_pi_comparison.py"
spec = importlib.util.spec_from_file_location("acceptance_oracle_tests", SCRIPT)
assert spec and spec.loader
o = importlib.util.module_from_spec(spec)
spec.loader.exec_module(o)
a = o.acceptance
c = o.contract


def write_row(name, expected, ident, start):
    batch = []
    back = []
    seq = 3
    for index in range(100 if expected == 100 else 1):
        entry = {
            "id": 101 + index,
            "conversationId": 1,
            "kind": "benchmark.write",
            "data": {
                "text": "x" * 128,
                **({"index": index} if expected == 100 else {}),
            },
        }
        if expected == 4:
            entry = {"id": 101, "conversationId": 1, "kind": "benchmark.mixed"}
        batch.append({"type": "entry", "value": entry})
        back.append({"type": "entry", "value": {"entry": entry, "commitSeq": seq}})
    if expected == 4:
        task = {
            "id": 102,
            "conversationId": 1,
            "kind": "benchmark.other",
            "version": 1,
            "input": {"index": 102},
            "background": False,
            "abortRequested": False,
            "state": {
                "status": "pending",
                "checkpoint": {"index": 102, "payload": "x" * 64},
            },
        }
        submission = {
            "id": 103,
            "conversationId": 1,
            "requestId": "benchmark-103",
            "type": "write",
            "status": "done",
            "entry": 101,
        }
        record = {
            "id": 104,
            "kind": "benchmark.mixed",
            "key": "104",
            "scope": {"kind": "session"},
        }
        doc = {"entryId": 101, "taskId": 102}
        stored = {
            "record": {**record, "createdAt": seq},
            "version": 1,
            "value": doc,
            "deltasSinceBase": 0,
        }
        batch.extend(
            [
                {"type": "task", "value": task},
                {"type": "submission", "value": submission},
                {
                    "type": "document.create",
                    "record": record,
                    "content": {"kind": "base", "version": 1, "value": doc},
                },
            ]
        )
        back.extend(
            [
                {"type": "task", "value": task},
                {"type": "submission", "value": submission, "byRequest": submission},
                {
                    "type": "document.create",
                    "value": stored,
                    "byAddress": stored["record"],
                },
            ]
        )
    return {
        "id": ident,
        "name": name,
        "expected": expected,
        "actual": expected,
        "seed": {"start": start, "end": start + 1},
        "run": {"start": start + 2, "end": start + 3},
        "checked": start + 4,
        "before": 100,
        "after": 100 + (100 if expected == 100 else 1),
        "commit": {"seq": seq, "writes": batch},
        "readbacks": back,
        "closed": True,
    }


def report():
    dataset = {
        "firstEntryId": 1,
        "filteredTaskCount": 5,
        "exactDocumentId": 1600,
        "exactDocumentKey": "key-299",
        "replayDocumentIds": {"0": 1601, "16": 1602, "128": 1603, "1024": 1604},
        "historicalDocumentId": 1605,
        "ancientAt": 1180,
        "recentAt": 1309,
        "deepestConversationId": 1837,
        "ancestorHeadEntryId": 1,
    }
    expected = [1, 100, 5, 1600, 0, 16, 128, 1024, 128, 256, 100, 1]
    data = {
        "schema_version": 1,
        "backend": "memory",
        "scale": {
            "name": "timing",
            "entryCount": 1000,
            "taskCount": 300,
            "documentCount": 300,
        },
        "primary_records": 1870,
        "dataset": dataset,
        "hardware": {
            "platform": "synthetic-test",
            "release": "test",
            "arch": "test",
            "cpu_model": "test",
            "cpu_count": 1,
            "total_ram_bytes": 1024,
            "node": "v26.12.0",
        },
        "limits": {
            "memory_retained": "uncollected",
            "protocol": "bounded-local-v1",
            "warmups": 0,
            "write_span": "test observer included",
            "performance_parity": False,
        },
        "spans": {
            "started": 0,
            "seed": {"start": 0, "end": 1},
            "correctness_end": 20,
            "finished": 200,
        },
        "reads": {
            "correctness": [
                {"name": name, "actual": result, "expected": result}
                for name, result in zip(a.READS, expected, strict=True)
            ],
            "samples": [
                {
                    "name": name,
                    "round": round,
                    "start": 21 + round,
                    "end": 21.5 + round,
                    "actual": result,
                }
                for round in range(10)
                for name, result in zip(a.READS, expected, strict=True)
            ],
        },
        "writes": {
            "correctness": [
                write_row(name, result, i + 1, 2 + i * 5)
                for i, (name, result) in enumerate(
                    zip(a.WRITES, [1, 100, 4], strict=True)
                )
            ],
            "samples": [
                write_row(name, result, 4 + i * 3 + j, 40 + i * 30 + j * 5)
                for i, (name, result) in enumerate(
                    zip(a.WRITES, [1, 100, 4], strict=True)
                )
                for j in range(3)
            ],
        },
        "closed": True,
    }
    return {
        "schema_version": 1,
        "id": a.WORKLOAD,
        "codec": "tagged-js-v1",
        "value": c.encode(data),
    }


def alter(value, fn):
    data = c.decode(value["value"])
    fn(data)
    value["value"] = c.encode(data)
    return value


def test_workload_validates_actual_results_counts_and_persisted_records():
    data = a.workload(report(), c)
    assert len(data["reads"]["samples"]) == 120 and len(data["writes"]["samples"]) == 9


@pytest.mark.parametrize(
    "mutation",
    [
        lambda d: d.update(primary_records=1000),
        lambda d: d.update(closed=False),
        lambda d: d["reads"]["correctness"].pop(),
        lambda d: d["reads"]["samples"].pop(),
        lambda d: d["reads"]["correctness"][2].update(actual=50),
        lambda d: d["reads"]["samples"][0].update(actual=99),
        lambda d: d["reads"]["samples"][0].update(start=0),
        lambda d: d["writes"]["samples"].pop(),
        lambda d: d["writes"]["samples"][1].update(id=4),
        lambda d: d["writes"]["correctness"][0].update(after=100),
        lambda d: d["writes"]["correctness"][0]["readbacks"].clear(),
        lambda d: d["writes"]["correctness"][0]["readbacks"][0]["value"].update(
            entry={}
        ),
        lambda d: d["writes"]["correctness"][2]["readbacks"][2].update(byRequest={}),
        lambda d: d["writes"]["correctness"][2]["readbacks"][3]["value"].update(
            value={}
        ),
        lambda d: d["hardware"].update(cpu_count=True),
        lambda d: d["hardware"].update(node="v22"),
        lambda d: d["limits"].update(performance_parity=True),
    ],
)
def test_workload_rejects_wrong_result_lying_return_readback_count_and_timing(mutation):
    with pytest.raises(o.EvidenceError):
        a.workload(alter(report(), mutation), c)


@pytest.mark.parametrize("name", a.ORACLES)
def test_actual_compare_cli_requires_four_valid_semantic_mutants(tmp_path, name):
    case = a.ORACLES[name]
    raw = o.load(o.FIXTURES / (case + ".json"))
    value = a.mutate(name, raw, c)
    c.project(value)
    assert not o.compare(raw, value)
    o.dump(tmp_path / "raw.json", raw)
    o.dump(tmp_path / "mutant.json", value)
    import subprocess

    p = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "compare",
            str(tmp_path / "raw.json"),
            str(tmp_path / "mutant.json"),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert p.returncode == 1 and "TRACE MISMATCH" in p.stdout


def test_mutation_refuses_noop_and_wrong_case():
    raw = o.load(o.FIXTURES / "harness-round-v1.json")
    changed = a.mutate("lost-field", raw, c)
    with pytest.raises(o.EvidenceError):
        a.mutate("lost-field", changed, c)
    with pytest.raises(o.EvidenceError):
        a.mutate("unknown", raw, c)


def test_acceptance_requires_fresh_exclusive_reference_once(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(
        o, "manifest", lambda *args: o.load(o.FIXTURES / "manifest.json")
    )

    def failure(source, output, fixtures):
        calls.append(output)
        return 1

    monkeypatch.setattr(o, "reference", failure)
    from types import SimpleNamespace

    api = SimpleNamespace(**vars(o))
    assert a.execute(api, tmp_path / "source", tmp_path / "fresh", o.FIXTURES) == 1
    assert (
        calls == [tmp_path / "fresh/reference"]
        and not (tmp_path / "fresh/acceptance.json").exists()
    )
    with pytest.raises(FileExistsError):
        a.execute(api, tmp_path / "source", tmp_path / "fresh", o.FIXTURES)
    assert len(calls) == 1


def test_acceptance_has_no_historical_reference_input(tmp_path, capsys):
    with pytest.raises(SystemExit) as e:
        o.main(
            [
                "acceptance",
                "--pi-source",
                str(tmp_path / "source"),
                "--output",
                str(tmp_path / "new"),
                "--reference",
                str(tmp_path / "historical"),
            ]
        )
    assert e.value.code == 2
    assert "unrecognized arguments: --reference" in capsys.readouterr().err


@pytest.mark.parametrize(
    "mutation",
    [
        lambda d: d["reads"]["correctness"][2].update(actual=50, expected=50),
        lambda d: d["reads"]["samples"][0].update(end=float("inf")),
        lambda d: d["writes"]["correctness"][0].update(expected=True),
    ],
)
def test_source_expected_and_finite_timing_cannot_be_replaced(mutation):
    with pytest.raises(o.EvidenceError):
        a.workload(alter(report(), mutation), c)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda v: v.update(schema_version=True),
        lambda v: v.update(schema_version=3.0),
        lambda v: v["workload"].update(id="other"),
        lambda v: v["workload"]["read_names"].pop(),
        lambda v: v["workload"]["write_names"].append("fake"),
        lambda v: v["oracles"].pop(),
        lambda v: v["oracles"].append(v["oracles"][0]),
        lambda v: v["oracles"][0].update(mutation_version=True),
        lambda v: v["verification"].pop("tests/durable/test_reference_codec.mts"),
        lambda v: v["verification"].update(
            {"tests/durable/test_reference_codec.mts": "0" * 64}
        ),
        lambda v: v["coverage"].update(sha256="0" * 64),
    ],
)
def test_manifest3_acceptance_contract_is_closed(tmp_path, mutation):
    import shutil

    fixtures = tmp_path / "fixtures"
    shutil.copytree(o.FIXTURES, fixtures)
    config = o.load(fixtures / "manifest.json")
    mutation(config)
    o.dump(fixtures / "manifest.json", config)
    with pytest.raises(o.EvidenceError):
        o.manifest(fixtures)


@pytest.mark.parametrize(
    "failure",
    ["noop", "invalid", "workload-readback", "artifact-drift", "manifest-drift"],
)
def test_acceptance_rejects_oracle_false_pass_and_postexecution_drift(
    tmp_path, monkeypatch, failure
):
    """Synthetic execution wiring only; this does not claim a reference runtime pass."""
    from types import SimpleNamespace

    config = o.load(o.FIXTURES / "manifest.json")
    config["adapters"] = {}
    source = tmp_path / "source"
    output = tmp_path / "acceptance"
    real_run = o.run
    real_mutate = a.mutate

    def reference(source, folder, fixtures):
        folder.mkdir()
        (folder / "snapshot").mkdir()
        for case in c.CASES:
            path = folder / case
            path.mkdir()
            o.dump(path / "trace.json", o.load(o.FIXTURES / (case + ".json")))
        for name in ("source-sha256.json", "dependency-sha256.json"):
            o.dump(folder / name, {})
        o.dump(folder / "run.json", {"installed_dependencies": {}})
        return 0

    def execute(command, cwd, folder, label, env, timeout=120):
        if label == a.WORKLOAD:
            work = output / a.WORKLOAD
            raw = report()
            if failure == "workload-readback":
                alter(raw, lambda d: d["writes"]["correctness"][0]["readbacks"].clear())
            o.dump(work / "workload.json", raw)
            o.dump(work / "resolutions.json", [])
            o.dump(folder / (label + ".json"), {"exit_code": 0})
            return b""
        result = real_run(command, cwd, folder, label, env, timeout)
        if (
            failure == "artifact-drift"
            and folder.name == "stale-head"
            and label == "positive"
        ):
            path = output / a.WORKLOAD / "workload.json"
            path.write_text(path.read_text() + " ")
        if (
            failure == "manifest-drift"
            and folder.name == "stale-head"
            and label == "positive"
        ):
            changed[0] = True
        return result

    mutate = mutation_injection(failure, real_mutate)

    changed = [False]
    real_sha = o.sha
    monkeypatch.setattr(
        o,
        "sha",
        lambda path: (
            "0" * 64
            if changed[0] and path == o.FIXTURES / "manifest.json"
            else real_sha(path)
        ),
    )
    monkeypatch.setattr(o, "manifest", lambda *args: config)
    monkeypatch.setattr(o, "reference", reference)
    monkeypatch.setattr(o, "run", execute)
    monkeypatch.setattr(o, "verify_resolutions", lambda *args: {})
    monkeypatch.setattr(o, "module_dependencies", lambda *args: None)
    monkeypatch.setattr(
        a, "attest_reference", lambda *args: {"installed_dependencies": {}}
    )
    monkeypatch.setattr(a, "mutate", mutate)
    api = SimpleNamespace(**vars(o))
    with pytest.raises(o.EvidenceError):
        a.execute(api, source, output, o.FIXTURES)
    assert not (output / "acceptance.json").exists()
    if failure == "invalid":
        assert o.load(output / "lost-field/positive.json")["exit_code"] == 0
        assert o.load(output / "lost-field/negative.json")["exit_code"] == 2


def mutation_injection(failure, real_mutate):
    def mutate(name, raw, contract):
        if name == "lost-field" and failure in {"noop", "invalid"}:
            if failure == "invalid":
                bad = copy.deepcopy(raw)
                bad["schema_version"] = 99
                return bad
            return raw
        return real_mutate(name, raw, contract)

    return mutate


@pytest.mark.parametrize(
    "failure,diagnostic",
    [
        ("missing-tool-result", "Missing/ambiguous tool result for mutation"),
        ("empty-model", "Missing tool result model for mutation"),
        ("missing-late-commit", "Missing/ambiguous late commit for mutation"),
        ("empty-variants", "Incomplete recovery policy matrix"),
    ],
)
def test_malformed_mutation_preconditions_fail_actual_cli2_without_traceback(
    tmp_path, failure, diagnostic
):
    """Actual CLI mapping: empty variants hits the prior schema, others mutate guards.

    This is malformed scripted input; no Node/reference runtime is executed.
    """
    case = (
        "harness-round-v1"
        if failure in {"missing-tool-result", "empty-model"}
        else (
            "durable-abort-v1"
            if failure == "missing-late-commit"
            else "tool-recovery-v1"
        )
    )
    raw = o.load(o.FIXTURES / (case + ".json"))
    value = c.decode(raw["steps"][0]["value"])
    if failure == "missing-tool-result":
        value["history"] = [
            row for row in value["history"] if row["kind"] != "pi.tool-result"
        ]
    elif failure == "empty-model":
        next(row for row in value["history"] if row["kind"] == "pi.tool-result")[
            "model"
        ] = []
    elif failure == "missing-late-commit":
        value["late"] = [row for row in value["late"] if row["op"] != "commit"]
    else:
        value["variants"] = []
    raw["steps"][0]["value"] = c.encode(value)
    o.dump(tmp_path / "raw.json", raw)
    name = {
        "harness-round-v1": "lost-field",
        "durable-abort-v1": "post-abort-write",
        "tool-recovery-v1": "duplicate-effect",
    }[case]
    program = """import importlib.util,sys
from pathlib import Path
spec=importlib.util.spec_from_file_location('o',sys.argv[1]);o=importlib.util.module_from_spec(spec);spec.loader.exec_module(o)
def reference(*args):return o.acceptance.mutate(sys.argv[3],o.load(Path(sys.argv[2])),o.contract)
o.reference=reference
raise SystemExit(o.main(['reference','--pi-source',sys.argv[4],'--output',sys.argv[5]]))
"""
    import subprocess

    p = subprocess.run(
        [
            sys.executable,
            "-c",
            program,
            str(SCRIPT),
            str(tmp_path / "raw.json"),
            name,
            str(tmp_path / "source"),
            str(tmp_path / "unused"),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert (
        p.returncode == 2
        and p.stderr.strip() == f"REFERENCE EVIDENCE INVALID: {diagnostic}"
        and "Traceback" not in p.stderr
    )


def test_verification_closure_includes_exact_ci_collector_and_tests():
    expected = {
        "tests/durable/test_pi_comparison.py",
        "tests/durable/test_pi_comparison_cases.py",
        "tests/durable/test_pi_comparison_acceptance.py",
        "tests/durable/test_pi_comparison_coverage.py",
        "tests/durable/test_reference_codec.mts",
        "scripts/durable_reference_artifacts.py",
        "tests/durable/test_reference_artifacts.py",
        ".github/workflows/ci.yml",
    }
    assert a.VERIFICATION == expected
    assert set(o.manifest()["verification"]) == expected


@pytest.mark.parametrize(
    "name",
    [
        "scripts/durable_reference_artifacts.py",
        "tests/durable/test_reference_artifacts.py",
        ".github/workflows/ci.yml",
    ],
)
def test_new_verification_identity_drift_is_rejected(monkeypatch, name):
    real_sha = o.sha
    monkeypatch.setattr(
        o, "sha", lambda path: "0" * 64 if path == o.ROOT / name else real_sha(path)
    )
    with pytest.raises(o.EvidenceError, match="Verification tool drift"):
        o.manifest()
