"""Actual native CLI reporting producer and semantic/source corruption controls."""

import copy
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "reporting_comparison", ROOT / "scripts/durable_reporting_comparison.py"
)
assert spec and spec.loader
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


@pytest.fixture(scope="module")
def native_run(tmp_path_factory):
    root = tmp_path_factory.mktemp("reporting-source")
    (root / "processes").mkdir()
    m.d.dump(root / "input.json", m.recipe())
    stdout, stderr, code = m.proc.run(
        m.d,
        [sys.executable, "-I", str(m.NATIVE), str(root / "input.json"), str(root)],
        m.ROOT,
        root / "processes",
        "native-reporting",
        m.o.child_env(root),
        120,
    )
    assert code == 0 and not stdout and not stderr, stderr
    raw = m.d.load(root / "native-raw.json")
    return m.projection(raw, True), raw, root


def test_actual_cli_reporting_failed_child_next_answer_and_reopen(native_run):
    actual, raw, _ = native_run
    m.successful(actual)
    for case in raw:
        assert case["raw"]["native_return_shape"] == (
            "throws" if case["id"].startswith("throw_") else "explicit-output-text"
        )


@pytest.mark.parametrize("name", m.values.MUTANTS)
def test_schema_valid_reporting_semantic_controls(native_run, name):
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
    case = changed[0]["raw"]
    entry = next(row for row in case["entries"] if row["kind"] == "pi.tool-result")
    if seam == "missing_receipt":
        entry["data"] = {}
    elif seam == "corrupt_receiver":
        entry["data"]["nativeToolReceipt"]["nativeToolOutput"]["outcome"]["nodes"][0][
            "tag"
        ] = "unknown"
    elif seam == "ack_identity":
        case["reports"][0]["committed"]["key"] = "not-this-call"
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
    receipt = m.d.load(root / "processes/native-reporting.json")
    receipt["leader_reaped"] = False
    with pytest.raises(m.o.EvidenceError):
        m.proc.attest(m.d, receipt, root / "processes", "native-reporting")


def test_root_relative_artifact_exclusions(tmp_path):
    root = tmp_path / "ancestor/stores/output"
    (root / "nested").mkdir(parents=True)
    (root / "stores").mkdir()
    for path in (root / "result.json", root / "nested/result.json", root / "stores/db"):
        path.write_text("{}")
    assert set(m.artifacts(root)) == {"nested/result.json"}
