"""Exhaustive DE/P index validation; source/reference evidence never closes parity."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/durable_pi_comparison.py"
spec = importlib.util.spec_from_file_location("coverage_oracle_tests", SCRIPT)
assert spec and spec.loader
o = importlib.util.module_from_spec(spec)
spec.loader.exec_module(o)
a = o.acceptance


def test_every_delivery_and_parity_requirement_retains_explicit_future_ownership():
    value = o.load(o.FIXTURES / "coverage.json")
    a.coverage(value, o.ROOT, o.contract)
    assert len(value["rows"]) == 95
    assert all(
        row["status"] == "OPEN" for row in value["rows"] if row["id"].startswith("P")
    )


@pytest.mark.parametrize(
    "mutation",
    [
        lambda v: v.update(schema_version=1),
        lambda v: v.update(schema_version=True),
        lambda v: v.update(schema_version=2.0),
        lambda v: v["rows"].pop(),
        lambda v: v["rows"].append(v["rows"][0]),
        lambda v: v["rows"][0].update(id="DE99"),
        lambda v: v["rows"][0].update(owners=["DE99"]),
        lambda v: v["rows"][0].update(future="later"),
        lambda v: v["rows"][-1].update(status="PASS"),
        lambda v: v["rows"][0]["current"].update(cases=["fake"]),
        lambda v: v["rows"][0]["current"].update(workloads=["fake"]),
        lambda v: v["rows"][0]["current"].update(oracles=["fake"]),
        lambda v: v["rows"][0].update(evidence=["docs/absent.md"]),
        lambda v: v["rows"][0].update(evidence=["docs/durable-reference.md#absent"]),
        lambda v: v["rows"][0]["current"].update(kind="native-parity"),
        lambda v: v.update(future_registry={"fake": {}}),
    ],
)
def test_missing_duplicate_typo_future_link_or_native_claim_is_rejected(mutation):
    value = o.load(o.FIXTURES / "coverage.json")
    mutation(value)
    with pytest.raises(o.EvidenceError):
        a.coverage(value, o.ROOT, o.contract)


def test_valid_but_wrong_owner_cannot_replace_original_ownership():
    value = o.load(o.FIXTURES / "coverage.json")
    value["rows"][0]["owners"] = ["DE02"]
    with pytest.raises(o.EvidenceError, match="ownership"):
        a.coverage(value, o.ROOT, o.contract)


@pytest.mark.parametrize(
    "change",
    [
        "status",
        "p-source",
        "de-source",
        "b3b-current",
        "inherited-owner",
        "de-future-case",
        "de-link",
    ],
)
def test_status_source_and_owned_versus_inherited_evidence_are_checked(change):
    value = o.load(o.FIXTURES / "coverage.json")
    rows = {row["id"]: row for row in value["rows"]}
    if change == "status":
        rows["DE01b3a"]["status"] = "OPEN"
    elif change == "p-source":
        rows["P01"]["source_anchor"] = "different actual source"
    elif change == "de-source":
        rows["DE03"]["source_anchor"] = rows["DE04"]["source_anchor"]
    elif change == "b3b-current":
        rows["DE01b3b"]["current"].update(
            kind="reference-only", cases=list(o.contract.CASES)
        )
    elif change == "inherited-owner":
        rows["DE01b3b"]["inherited_reference"]["owners"] = ["DE01b3b"]
    elif change == "de-future-case":
        rows["DE03"]["current"].update(
            kind="reference-only", cases=["kernel-memory-v1"]
        )
    else:
        rows["DE03"]["evidence"] = ["docs/durable-reference.md"]
    with pytest.raises(o.EvidenceError):
        a.coverage(value, o.ROOT, o.contract)


@pytest.mark.parametrize(
    "bad",
    [
        "| P01 |",
        "| DE03 |",
        "| P01 | contract | source |",
        "| DE03 | contract | acceptance | extra |",
    ],
)
def test_malformed_plan_rows_are_actual_cli_invalid2(tmp_path, bad):
    """Invoke actual CLI mapping around plan parsing; no reference runtime claim."""
    root = tmp_path / "root"
    path = root / a.PLAN
    path.parent.mkdir(parents=True)
    key = bad.split("|")[1].strip()
    canonical = (o.ROOT / a.PLAN).read_text().splitlines()
    matches = [i for i, line in enumerate(canonical) if line.startswith(f"| {key} ")]
    assert len(matches) == 1
    canonical[matches[0]] = bad
    path.write_text("\n".join(canonical) + "\n")
    program = """import importlib.util,sys
from pathlib import Path
spec=importlib.util.spec_from_file_location('o',sys.argv[1]);o=importlib.util.module_from_spec(spec);spec.loader.exec_module(o)
def reference(*args):
 o.acceptance.plan_rows(Path(sys.argv[2]),o.contract)
 return 0
o.reference=reference
raise SystemExit(o.main(['reference','--pi-source',sys.argv[2],'--output',sys.argv[3]]))
"""
    import subprocess
    import sys

    p = subprocess.run(
        [
            sys.executable,
            "-c",
            program,
            str(SCRIPT),
            str(root),
            str(tmp_path / "unused"),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert (
        p.returncode == 2
        and p.stderr.strip() == "REFERENCE EVIDENCE INVALID: Malformed plan row"
        and "Traceback" not in p.stderr
    )


def test_b3b_uses_inherited_families_without_claiming_hosted_success():
    row = next(
        row
        for row in o.load(o.FIXTURES / "coverage.json")["rows"]
        if row["id"] == "DE01b3b"
    )
    assert row["status"] == "IN_PROGRESS"
    assert (
        row["current"]["cases"]
        == row["current"]["workloads"]
        == row["current"]["oracles"]
        == []
    )
    assert row["inherited_reference"]["owners"] == ["DE01b1", "DE01b2", "DE01b3a"]
    assert row["inherited_reference"]["cases"] == list(o.contract.CASES)
    assert "hosted execution/upload proof remains OPEN" in row["current"]["observables"]
