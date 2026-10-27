"""Actual native CLI interrupted producer and semantic/source corruption controls."""

import copy
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "interrupted_comparison", ROOT / "scripts/durable_interrupted_comparison.py"
)
assert spec and spec.loader
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


@pytest.fixture(scope="module")
def native_run(tmp_path_factory):
    root = tmp_path_factory.mktemp("interrupted-source")
    (root / "processes").mkdir()
    m.d.dump(root / "input.json", m.recipe())
    stdout, stderr, code = m.proc.run(
        m.d,
        [sys.executable, "-I", str(m.NATIVE), str(root / "input.json"), str(root)],
        m.ROOT,
        root / "processes",
        "native-interrupted",
        m.o.child_env(root),
        120,
    )
    assert code == 0 and not stdout and not stderr, stderr
    raw = m.d.load(root / "native-raw.json")
    return m.projection(raw, True), raw, root


def test_actual_sdk_crash_cold_cli_interrupted_next_answer_and_reopen(native_run):
    actual, raw, _ = native_run
    m.successful(actual)
    assert all(
        case["cut"] == "SIGKILL-after-progress-COMMIT-before-details-ACK"
        for case in raw
    )


@pytest.mark.parametrize("name", m.values.MUTANTS)
def test_schema_valid_interrupted_semantic_controls(native_run, name):
    actual, _, _ = native_run
    changed = m.values.mutate(actual, name, m.v)
    m.trace(changed)
    assert m.compare(actual, changed) == 1


@pytest.mark.parametrize(
    "seam", ["missing_receipt", "corrupt_receiver", "ack_identity", "wire_content"]
)
def test_actual_raw_boundary_corruption_is_not_normalized_away(native_run, seam):
    _, raw, _ = native_run
    changed = copy.deepcopy(raw)
    case = changed[1]
    entry = next(
        row for row in case["completed"]["entries"] if row["kind"] == "pi.tool-result"
    )
    if seam == "missing_receipt":
        entry["data"] = {}
    elif seam == "corrupt_receiver":
        entry["data"]["nativeToolReceipt"]["nativeToolOutput"]["outcome"]["nodes"][0][
            "tag"
        ] = "unknown"
    elif seam == "ack_identity":
        case["prefix"]["documents"]["pi.tool.progress"][-1]["key"] = "not-this-call"
    else:
        next(row for row in case["wire"][-1]["messages"] if row["role"] == "tool")[
            "content"
        ] = "different actual wire"
    with pytest.raises((ValueError, KeyError, AssertionError)):
        m.projection(changed, True)


def test_protocol_source_and_lifecycle_corruption_refuse(native_run, tmp_path):
    actual, _, root = native_run
    bad = copy.deepcopy(actual)
    bad["unknown"] = True
    with pytest.raises(m.o.EvidenceError):
        m.compare(actual, bad)
    m.d.dump(tmp_path / "native-modules.json", {"src/unlisted.py": "0" * 64})
    with pytest.raises(m.o.EvidenceError, match="unlisted"):
        m.native_modules(tmp_path)
    receipt = m.d.load(root / "processes/native-interrupted.json")
    receipt["leader_reaped"] = False
    with pytest.raises(m.o.EvidenceError):
        m.proc.attest(m.d, receipt, root / "processes", "native-interrupted")


def test_root_relative_artifact_exclusions(tmp_path):
    root = tmp_path / "ancestor/stores/output"
    (root / "nested").mkdir(parents=True)
    (root / "stores").mkdir()
    for path in (root / "result.json", root / "nested/result.json", root / "stores/db"):
        path.write_text("{}")
    assert set(m.artifacts(root)) == {"nested/result.json"}


@pytest.mark.parametrize("mutation", ["leader", "ack", "source"])
def test_actual_physical_child_evidence_cannot_be_promoted(native_run, mutation):
    _, raw, root = native_run
    m.native_actors(root, raw)
    path = root / "native-records/unsafe_same/cut-process.json"
    original = path.read_bytes()
    saved = m.d.load(path)
    if mutation == "leader":
        saved["process"]["leader_reaped"] = False
    elif mutation == "ack":
        saved["events"][-1]["callback_acknowledged"] = True
        saved["process"]["events"] = copy.deepcopy(saved["events"])
    else:
        next(iter(saved["events"][-1]["native_origins"].values()))["sha256"] = "0" * 64
        saved["process"]["events"] = copy.deepcopy(saved["events"])
    try:
        m.d.dump(path, saved)
        with pytest.raises(m.o.EvidenceError):
            m.native_actors(root, raw)
    finally:
        path.write_bytes(original)
