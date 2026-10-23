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
    assert len(value["rows"]) == len(a.ALL_IDS)
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


def test_delivered_b3b_keeps_inherited_family_ownership_separate():
    row = next(
        row
        for row in o.load(o.FIXTURES / "coverage.json")["rows"]
        if row["id"] == "DE01b3b"
    )
    assert row["status"] == "LANDED"
    assert (
        row["current"]["cases"]
        == row["current"]["workloads"]
        == row["current"]["oracles"]
        == []
    )
    assert row["inherited_reference"]["owners"] == ["DE01b1", "DE01b2", "DE01b3a"]
    assert row["inherited_reference"]["cases"] == list(o.contract.CASES)
    assert "CI39370449156" in row["current"]["observables"]
    assert "inherited explicitly" in row["current"]["observables"]


@pytest.mark.parametrize("parent", ["DE03", "DE03d", "DE04", "DE06", "DE32"])
def test_new_parent_status_cannot_land_before_all_children(tmp_path, parent):
    root = tmp_path / "root"
    path = root / a.LEDGER
    path.parent.mkdir(parents=True)
    source = (o.ROOT / a.LEDGER).read_text()
    rows = source.splitlines()
    index = next(
        i for i, line in enumerate(rows) if line.startswith("| " + parent + " |")
    )
    import re

    rows[index] = re.sub(
        r"\| (?:OPEN|IN_PROGRESS|LANDED) \|", "| LANDED |", rows[index]
    )
    child = {
        "DE03": "DE03d",
        "DE03d": "DE03d2",
        "DE04": "DE04b",
        "DE06": "DE06b",
        "DE32": "DE32b",
    }[parent]
    child_index = next(
        i for i, line in enumerate(rows) if line.startswith("| " + child + " |")
    )
    rows[child_index] = re.sub(
        r"\| (?:OPEN|IN_PROGRESS|LANDED) \|", "| OPEN |", rows[child_index]
    )
    path.write_text("\n".join(rows) + "\n")
    with pytest.raises(o.EvidenceError, match="Cumulative parent status drift"):
        a.ledger_status(root, o.contract)


@pytest.mark.parametrize(
    "parent", ["DE02a2", "DE02a", "DE02b2", "DE02b", "DE02", "DE03c", "DE03d", "DE03"]
)
def test_delivered_parent_cannot_reopen_while_children_are_landed(tmp_path, parent):
    root = tmp_path / "root"
    path = root / a.LEDGER
    path.parent.mkdir(parents=True)
    rows = (o.ROOT / a.LEDGER).read_text().splitlines()
    index = next(
        i for i, line in enumerate(rows) if line.startswith("| " + parent + " |")
    )
    assert "| LANDED |" in rows[index]
    rows[index] = rows[index].replace("| LANDED |", "| IN_PROGRESS |")
    path.write_text("\n".join(rows) + "\n")
    with pytest.raises(o.EvidenceError, match="Cumulative parent status drift"):
        a.ledger_status(root, o.contract)


def test_session_split_children_keep_original_parent_contract():
    value = o.load(o.FIXTURES / "coverage.json")
    a.coverage(value, o.ROOT, o.contract)
    rows = {row["id"]: row for row in value["rows"]}
    assert rows["DE02b"]["status"] == "LANDED"
    assert rows["DE02b"]["future"].startswith(
        "Retain original DE02 commit/adoption acceptance"
    )
    assert "b1a+b1b+b2+b3" in rows["DE02b"]["future"]
    for child in ("DE02b1a", "DE02b1b", "DE02b2", "DE02b2a", "DE02b2b", "DE02b3"):
        assert rows[child]["status"] == "LANDED"
    for child in ("DE02b1a", "DE02b2", "DE02b2a", "DE02b2b"):
        current = rows[child]["current"]
        assert current["kind"] == "source-only"
        assert current["cases"] == current["workloads"] == current["oracles"] == []
    for child, case in (
        ("DE02b1b", "session-guard-b1b-v1"),
        ("DE02b3", "session-commit-b3-v1"),
    ):
        current = rows[child]["current"]
        assert current["kind"] == "bounded-native"
        assert current["cases"] == [case]
        assert current["workloads"] == current["oracles"] == []
    assert rows["DE02a2b"]["status"] == rows["DE02a2"]["status"] == "LANDED"
    assert rows["DE02a"]["status"] == rows["DE02"]["status"] == "LANDED"


def test_bounded_native_evidence_cannot_be_claimed_by_cumulative_parent():
    value = o.load(o.FIXTURES / "coverage.json")
    row = next(row for row in value["rows"] if row["id"] == "DE02a2")
    row["current"].update(kind="bounded-native", cases=["values-records-a1"])
    with pytest.raises(o.EvidenceError):
        a.coverage(value, o.ROOT, o.contract)


@pytest.mark.parametrize(
    "child", ["DE02a2b", "DE02b", "DE02b1a", "DE02b1b", "DE02a", "DE02"]
)
def test_table_evidence_is_owned_only_by_actual_internal_child(child):
    value = o.load(o.FIXTURES / "coverage.json")
    a.coverage(value, o.ROOT, o.contract)
    rows = {row["id"]: row for row in value["rows"]}
    assert rows["DE02a2a"]["current"]["cases"] == ["memory-tables-a2a-v1"]
    assert rows["DE02a2a"]["current"]["kind"] == "bounded-native"
    assert rows["DE02a2b"]["current"]["kind"] == "bounded-native"
    assert rows["DE02a2b"]["current"]["cases"] == ["memory-current-base-a2b-v1"]
    rows[child]["current"].update(kind="bounded-native", cases=["memory-tables-a2a-v1"])
    with pytest.raises(
        o.EvidenceError, match="Native parity claim|Unknown current|ownership drift"
    ):
        a.coverage(value, o.ROOT, o.contract)


@pytest.mark.parametrize("bad", ["DE02a2c", "DE02a3", "DE02a2aa"])
def test_only_exact_new_child_ids_are_accepted(bad):
    value = o.load(o.FIXTURES / "coverage.json")
    row = next(row for row in value["rows"] if row["id"] == "DE02a2b")
    row["id"] = bad
    with pytest.raises(o.EvidenceError, match="Unknown/duplicate coverage ID"):
        a.coverage(value, o.ROOT, o.contract)


@pytest.mark.parametrize("bad", ["DE02b4", "DE02b1c", "DE02b2c", "DE02b3a", "DE02b11"])
def test_only_exact_session_child_ids_are_accepted(bad):
    value = o.load(o.FIXTURES / "coverage.json")
    row = next(row for row in value["rows"] if row["id"] == "DE02b1a")
    row["id"] = bad
    with pytest.raises(o.EvidenceError, match="Unknown/duplicate coverage ID"):
        a.coverage(value, o.ROOT, o.contract)


@pytest.mark.parametrize(
    "child",
    ["DE02b", "DE02b1a", "DE02b2", "DE02b2a", "DE02b2b", "DE02b3", "DE02", "DE03"],
)
def test_guard_oracle_evidence_is_owned_only_by_b1b(child):
    value = o.load(o.FIXTURES / "coverage.json")
    a.coverage(value, o.ROOT, o.contract)
    rows = {row["id"]: row for row in value["rows"]}
    assert rows["DE02b1b"]["current"]["cases"] == ["session-guard-b1b-v1"]
    rows[child]["current"].update(kind="bounded-native", cases=["session-guard-b1b-v1"])
    with pytest.raises(
        o.EvidenceError, match="Native parity claim|Unknown current|ownership drift"
    ):
        a.coverage(value, o.ROOT, o.contract)


@pytest.mark.parametrize("change", ["case", "kind", "extra-case"])
def test_b1b_cannot_claim_other_or_broader_evidence(change):
    value = o.load(o.FIXTURES / "coverage.json")
    row = next(row for row in value["rows"] if row["id"] == "DE02b1b")
    if change == "case":
        row["current"]["cases"] = ["memory-current-base-a2b-v1"]
    elif change == "kind":
        row["current"]["kind"] = "source-only"
    else:
        row["current"]["cases"] = ["session-guard-b1b-v1", "kernel-memory-v1"]
    with pytest.raises(o.EvidenceError):
        a.coverage(value, o.ROOT, o.contract)


def test_tx_core_split_keeps_original_b2_contract():
    value = o.load(o.FIXTURES / "coverage.json")
    a.coverage(value, o.ROOT, o.contract)
    rows = {row["id"]: row for row in value["rows"]}
    assert rows["DE02b2"]["future"].startswith("Callback API decision recorded")
    assert "DE02b2 completes only after both" in rows["DE02b2"]["future"]
    assert "no commit entrypoint" in rows["DE02b2a"]["contract"]
    assert "Session/line/async driver" not in rows["DE02b2a"]["contract"]
    assert "none moves to b3" in rows["DE02b2b"]["future"]
    assert "No reference oracle (b3)" in rows["DE02b2b"]["current"]["observables"]
    assert rows["DE02b2b"]["evidence"][0].endswith(
        "#de02b2a-delivery-and-de02b2b-admission"
    )
    for key in ("DE04", "DE27"):
        assert "session-documents.test.ts 190" in rows[key]["current"]["observables"]


@pytest.mark.parametrize(
    "child",
    ["DE02b", "DE02b1a", "DE02b1b", "DE02b2", "DE02b2b", "DE02", "DE03"],
)
def test_session_commit_oracle_evidence_is_owned_only_by_b3(child):
    value = o.load(o.FIXTURES / "coverage.json")
    a.coverage(value, o.ROOT, o.contract)
    rows = {row["id"]: row for row in value["rows"]}
    assert rows["DE02b3"]["current"]["cases"] == ["session-commit-b3-v1"]
    rows[child]["current"].update(kind="bounded-native", cases=["session-commit-b3-v1"])
    with pytest.raises(
        o.EvidenceError, match="Native parity claim|Unknown current|ownership drift"
    ):
        a.coverage(value, o.ROOT, o.contract)


@pytest.mark.parametrize("change", ["case", "kind", "extra-case", "reopened-parent"])
def test_b3_cannot_claim_other_or_broader_evidence(change):
    value = o.load(o.FIXTURES / "coverage.json")
    rows = {row["id"]: row for row in value["rows"]}
    row = rows["DE02b3"]
    if change == "case":
        row["current"]["cases"] = ["session-guard-b1b-v1"]
    elif change == "kind":
        row["current"]["kind"] = "source-only"
    elif change == "extra-case":
        row["current"]["cases"] = ["session-commit-b3-v1", "kernel-memory-v1"]
    else:
        rows["DE02b"]["status"] = "IN_PROGRESS"
    with pytest.raises(o.EvidenceError):
        a.coverage(value, o.ROOT, o.contract)


def test_sqlite_split_children_keep_original_parent_contract():
    value = o.load(o.FIXTURES / "coverage.json")
    a.coverage(value, o.ROOT, o.contract)
    rows = {row["id"]: row for row in value["rows"]}
    parent = rows["DE03"]
    assert parent["status"] == "LANDED"
    assert parent["contract"].startswith("Implement local SQLite backend/schema")
    assert parent["future"].startswith("Two processes/aliases cannot own one DB;")
    assert "DE03 completes only after its a+b+c+d children" in parent["future"]
    assert rows["DE03a"]["status"] == "LANDED"
    assert "delivered at 0d37f64a" in rows["DE03a"]["current"]["observables"]
    assert rows["DE03b"]["status"] == "LANDED"
    assert "delivered at f6024e56" in rows["DE03b"]["current"]["observables"]
    assert rows["DE03c"]["status"] == "LANDED"
    assert rows["DE03d"]["status"] == "LANDED"
    for key in ("DE03", "DE03a", "DE03b", "DE03c", "DE03d"):
        current = rows[key]["current"]
        assert current["kind"] == "source-only"
        assert current["cases"] == current["workloads"] == current["oracles"] == []
        split = "de03b-delivery-and-de03c-split" if key == "DE03c" else None
        assert rows[key]["evidence"][0].endswith(
            "#" + (split or "de02b3-delivery-and-de03-split")
        )
    assert "ownership.py" in rows["DE03a"]["contract"]
    assert "sqlite_schema.py" in rows["DE03b"]["contract"]
    assert "SqliteStorage" in rows["DE03c"]["contract"]
    assert "openNodeSqliteStorage" in rows["DE03d"]["future"]
    assert rows["DE04"]["status"] == "LANDED"
    assert "Carried from DE02b3" in rows["DE04"]["current"]["observables"]


@pytest.mark.parametrize("child", ["DE03a", "DE03b", "DE03c", "DE03d"])
def test_sqlite_parent_cannot_land_with_any_open_child(tmp_path, child):
    root = tmp_path / "root"
    path = root / a.LEDGER
    path.parent.mkdir(parents=True)
    rows = (o.ROOT / a.LEDGER).read_text().splitlines()
    # An open DE03c keeps its own children open, so only DE03's rule can fail.
    opened = (
        {child, "DE03c1", "DE03c2"}
        if child == "DE03c"
        else ({child, "DE03d1", "DE03d2"} if child == "DE03d" else {child})
    )
    for key in (
        "DE03a",
        "DE03b",
        "DE03c",
        "DE03c1",
        "DE03c2",
        "DE03d",
        "DE03d1",
        "DE03d2",
        "DE03",
    ):
        index = next(
            i for i, line in enumerate(rows) if line.startswith("| " + key + " |")
        )
        state = "OPEN" if key in opened else "LANDED"
        rows[index] = (
            rows[index]
            .replace("| IN_PROGRESS |", f"| {state} |")
            .replace("| OPEN |", f"| {state} |")
            .replace("| LANDED |", f"| {state} |")
        )
    path.write_text("\n".join(rows) + "\n")
    with pytest.raises(o.EvidenceError, match="Cumulative parent status drift"):
        a.ledger_status(root, o.contract)


@pytest.mark.parametrize(
    "bad",
    ["DE03e", "DE03a1", "DE03aa", "DE03b1", "DE04c", "DE03", "DE03c3", "DE03d3"],
)
def test_only_exact_sqlite_child_ids_are_accepted(bad):
    value = o.load(o.FIXTURES / "coverage.json")
    row = next(row for row in value["rows"] if row["id"] == "DE03a")
    row["id"] = bad
    with pytest.raises(o.EvidenceError, match="Unknown/duplicate coverage ID"):
        a.coverage(value, o.ROOT, o.contract)


@pytest.mark.parametrize(
    "child", ["DE03", "DE03a", "DE03b", "DE03c", "DE03c1", "DE03c2", "DE03d"]
)
@pytest.mark.parametrize(
    "case", ["kernel-memory-v1", "session-commit-b3-v1", "memory-current-base-a2b-v1"]
)
def test_sqlite_children_cannot_claim_reference_or_native_case_evidence(child, case):
    value = o.load(o.FIXTURES / "coverage.json")
    row = next(row for row in value["rows"] if row["id"] == child)
    row["current"].update(kind="bounded-native", cases=[case])
    with pytest.raises(
        o.EvidenceError, match="Native parity claim|Unknown current|ownership drift"
    ):
        a.coverage(value, o.ROOT, o.contract)


def test_sqlite_storage_split_children_keep_original_parent_contract():
    value = o.load(o.FIXTURES / "coverage.json")
    a.coverage(value, o.ROOT, o.contract)
    rows = {row["id"]: row for row in value["rows"]}
    parent = rows["DE03c"]
    assert parent["status"] == "LANDED"
    assert parent["contract"].startswith(
        "`SqliteStorage` in `native/durable/sqlite.py`"
    )
    assert parent["future"].startswith("Contract cases over Memory, SQLite and SQLite")
    assert "DE03c completes only after its c1+c2 children" in parent["future"]
    assert rows["DE03c1"]["status"] == "LANDED"
    assert rows["DE03c2"]["status"] == "LANDED"
    for key in ("DE03c", "DE03c1", "DE03c2"):
        current = rows[key]["current"]
        assert current["kind"] == "source-only"
        assert current["cases"] == current["workloads"] == current["oracles"] == []
        assert rows[key]["evidence"][0].endswith("#de03b-delivery-and-de03c-split")
    assert "storage_contract_cases.py" in rows["DE03c1"]["contract"]
    assert "DE03c2" in rows["DE03c1"]["contract"]
    assert "retention rows" in rows["DE03c2"]["contract"]
    assert "SIGKILL before and after COMMIT" in rows["DE03c2"]["future"]


@pytest.mark.parametrize("child", ["DE03c1", "DE03c2"])
def test_sqlite_storage_parent_cannot_land_with_an_open_child(tmp_path, child):
    root = tmp_path / "root"
    path = root / a.LEDGER
    path.parent.mkdir(parents=True)
    rows = (o.ROOT / a.LEDGER).read_text().splitlines()
    for key in ("DE03c1", "DE03c2", "DE03c"):
        index = next(
            i for i, line in enumerate(rows) if line.startswith("| " + key + " |")
        )
        state = "OPEN" if key == child else "LANDED"
        rows[index] = (
            rows[index]
            .replace("| IN_PROGRESS |", f"| {state} |")
            .replace("| OPEN |", f"| {state} |")
            .replace("| LANDED |", f"| {state} |")
        )
    path.write_text("\n".join(rows) + "\n")
    with pytest.raises(o.EvidenceError, match="Cumulative parent status drift"):
        a.ledger_status(root, o.contract)


def test_canonical_codec_split_retains_cumulative_acceptance():
    rows = {row["id"]: row for row in o.load(o.FIXTURES / "coverage.json")["rows"]}
    assert rows["DE06"]["status"] == "LANDED"
    assert rows["DE06a"]["status"] == "LANDED"
    assert "delivery still pending" not in rows["DE06a"]["current"]["observables"]
    assert rows["DE06a"]["plan_cells"][0] == "DE06a / DE02a1"
    assert rows["DE06b"]["plan_cells"][0] == "DE06b / DE05 + DE06a"
    assert "compare=False" in rows["DE06"]["future"]
    assert all(rows[key]["status"] == "OPEN" for key in ("P15", "P16", "P19"))


def test_current_sqlite_summaries_match_delivered_children():
    rows = {row["id"]: row for row in o.load(o.FIXTURES / "coverage.json")["rows"]}
    for key in ("DE03", "DE03c", "DE03c2"):
        current = rows[key]["current"]["observables"]
        assert "a2c4c6d" in current
        assert "final gates pending" not in current
        assert "review and delivery gates pending" not in current
    assert rows["DE03"]["status"] == "LANDED"
    assert rows["DE03c"]["status"] == rows["DE03c2"]["status"] == "LANDED"


def test_current_delivery_gate_names_current_reviewer_and_lanes():
    text = (o.ROOT / a.LEDGER).read_text()
    current = text.split("## Next delivery gate\n", 1)[1].split("\n## ", 1)[0]
    assert "DE04 and all its children are delivered" in current
    assert "DE05a" in current and "DE05b" in current
    assert "Delivered DE06a" in current and "DE07 generation" in current
    assert "claude-opus-5-5" in current and "HIGH" in current
    assert "Codex gpt-6.1-sol HIGH CLEAN" not in current
    assert "All43 P rows remain OPEN" in current


@pytest.mark.parametrize("child", ["DE03d1", "DE03d2"])
def test_sqlite_final_parent_requires_both_children(tmp_path, child):
    root = tmp_path / "root"
    path = root / a.LEDGER
    path.parent.mkdir(parents=True)
    lines = (o.ROOT / a.LEDGER).read_text().splitlines()
    for key in ("DE03d1", "DE03d2", "DE03d"):
        i = next(
            i for i, line in enumerate(lines) if line.startswith("| " + key + " |")
        )
        state = "OPEN" if key == child else "LANDED"
        import re

        lines[i] = re.sub(
            r"\| (?:OPEN|IN_PROGRESS|LANDED) \|", f"| {state} |", lines[i]
        )
    path.write_text("\n".join(lines) + "\n")
    with pytest.raises(o.EvidenceError, match="Cumulative parent status drift"):
        a.ledger_status(root, o.contract)


def test_sqlite_d_split_retains_original_acceptance_and_exact_ids():
    value = o.load(o.FIXTURES / "coverage.json")
    a.coverage(value, o.ROOT, o.contract)
    rows = {row["id"]: row for row in value["rows"]}
    assert rows["DE03d1"]["status"] == "LANDED"
    assert rows["DE03d2"]["status"] == "LANDED"
    assert "DE03d completes only after its d1+d2 children" in rows["DE03d"]["future"]
    assert rows["DE04"]["plan_cells"][0] == "DE04 / DE03"
    for child in ("DE03d1", "DE03d2"):
        assert rows[child]["current"]["kind"] == (
            "source-only" if child == "DE03d1" else "bounded-native"
        )
        assert rows[child]["current"]["cases"] == (
            [] if child == "DE03d1" else ["storage-sqlite-d2-v1"]
        )


def test_scanner_split_retains_full_environment_join():
    rows = {r["id"]: r for r in o.load(o.FIXTURES / "coverage.json")["rows"]}
    assert rows["DE32a"]["plan_cells"][0] == "DE32a / DE00"
    assert rows["DE32b"]["plan_cells"][0] == "DE32b / DE31 + DE32a"
    assert "every original DE32 acceptance" in rows["DE32b"]["future"]
    assert rows["DE33"]["plan_cells"][0] == "DE33 / DE32"


def test_typed_document_split_preserves_checkpoint_rollback_and_parent_dependency():
    value = o.load(o.FIXTURES / "coverage.json")
    a.coverage(value, o.ROOT, o.contract)
    rows = {row["id"]: row for row in value["rows"]}
    assert len(rows) == len(a.ALL_IDS)
    assert rows["DE04"]["status"] == "LANDED"
    assert rows["DE04a"]["status"] == "LANDED"
    assert rows["DE04b"]["status"] == "LANDED"
    assert rows["DE04a"]["plan_cells"][0] == "DE04a / DE03"
    assert rows["DE04b"]["plan_cells"][0] == "DE04b / DE04a"
    assert rows["DE05"]["plan_cells"][0] == "DE05 / DE04"
    assert "checkpoint exceptions rollback" in rows["DE04"]["future"]
    assert (
        "second-predicate exception rolling back all coupled candidates"
        in rows["DE04b"]["future"]
    )
    assert "no always-base callback substitute" in rows["DE04b"]["future"]
    assert len([row for row in value["rows"] if row["id"].startswith("P")]) == 43


def test_operation_prerequisite_split_preserves_full_document_join():
    rows = {r["id"]: r for r in o.load(o.FIXTURES / "coverage.json")["rows"]}
    assert rows["DE04a"]["plan_cells"][0] == "DE04a / DE03"
    assert rows["DE04b"]["plan_cells"][0] == "DE04b / DE04a"
    assert rows["DE04b1a"]["plan_cells"][0] == "DE04b1a / DE02a1"
    assert rows["DE04b1b"]["plan_cells"][0] == "DE04b1b / DE04a + DE04b1a"
    assert "second-predicate exception rolling back" in rows["DE04b"]["future"]
    assert "semantic schema2" in rows["DE04b1b"]["contract"]
    assert rows["DE05"]["plan_cells"][0] == "DE05 / DE04"
    assert all(rows[f"P{i:02}"]["status"] == "OPEN" for i in range(1, 44))


@pytest.mark.parametrize("parent", ["DE04", "DE04b", "DE04b1b", "DE04b1b3"])
def test_document_join_cannot_land_before_prerequisites(tmp_path, parent):
    import re

    root = tmp_path / "root"
    path = root / a.LEDGER
    path.parent.mkdir(parents=True)
    lines = (o.ROOT / a.LEDGER).read_text().splitlines()
    for i, line in enumerate(lines):
        key = line.split(" |", 1)[0].removeprefix("| ")
        if key == "DE04b1b3b":
            lines[i] = re.sub(r"\| (?:OPEN|IN_PROGRESS|LANDED) \|", "| OPEN |", line)
        elif key in {"DE04", "DE04b", "DE04b1b", "DE04b1b3"}:
            lines[i] = re.sub(
                r"\| (?:OPEN|IN_PROGRESS|LANDED) \|", "| IN_PROGRESS |", line
            )
    index = next(
        i for i, line in enumerate(lines) if line.startswith("| " + parent + " |")
    )
    lines[index] = re.sub(
        r"\| (?:OPEN|IN_PROGRESS|LANDED) \|", "| LANDED |", lines[index]
    )
    path.write_text("\n".join(lines) + "\n")
    with pytest.raises(o.EvidenceError, match="Cumulative parent status drift"):
        a.ledger_status(root, o.contract)


def test_tracker_storage_join_dependency_map_and_current_storage_evidence():
    value = o.load(o.FIXTURES / "coverage.json")
    a.coverage(value, o.ROOT, o.contract)
    rows = {row["id"]: row for row in value["rows"]}
    assert len(rows) == len(a.ALL_IDS)
    assert rows["DE04b1a"]["status"] == "LANDED"
    assert rows["DE04b1b"]["status"] == "LANDED"
    dependencies = {
        "DE04b1b1": "DE04b1b1 / DE04a + DE04b1a",
        "DE04b1b2": "DE04b1b2 / DE03d + DE04b1a",
        "DE04b1b3": "DE04b1b3 / DE04b1b1 + DE04b1b2 + DE04a + owner policy266",
    }
    for key, dependency in dependencies.items():
        row = rows[key]
        assert row["plan_cells"][0] == dependency
        if key in {"DE04b1b1", "DE04b1b2"}:
            assert row["current"]["kind"] == "bounded-native"
            case, topic = {
                "DE04b1b1": ("draft-tracker-v1", "docs/durable-draft-tracker.md"),
                "DE04b1b2": (
                    "storage-current-deltas-v1",
                    "docs/durable-current-deltas.md",
                ),
            }[key]
            assert row["current"]["cases"] == [case]
            assert topic in row["evidence"]
        else:
            assert row["current"]["kind"] == "source-only"
            assert all(
                row["current"][field] == []
                for field in ("cases", "workloads", "oracles")
            )
            assert "No new source" in row["current"]["observables"]
        assert (
            row["status"]
            == {
                "DE04b1b1": "LANDED",
                "DE04b1b2": "LANDED",
                "DE04b1b3": "LANDED",
            }[key]
        )
    assert "no dependency on T" in rows["DE04b1b2"]["contract"]
    assert (
        "BEFORE authoritative candidate/operation selection"
        in rows["DE04b1b3"]["future"]
    )
    assert "every original acceptance cell" in rows["DE04b1b"]["future"]
    assert "use the persisted/replayed value" in rows["DE04b1b3"]["future"]
    assert rows["DE04b1b3a"]["status"] == "LANDED"
    assert rows["DE04b1b3a"]["current"]["kind"] == "bounded-native"
    assert rows["DE04b1b3a"]["current"]["cases"] == [
        "session-commit-b3-v1",
        "session-documents-a-v1",
    ]
    assert "docs/durable-session-deltas.md" in rows["DE04b1b3a"]["evidence"]
    completion = a.LEDGER + "#de04b1b3b-cumulative-acceptance"
    for key in ("DE04", "DE04b", "DE04b1b", "DE04b1b3", "DE04b1b3b"):
        assert rows[key]["evidence"][0] == completion
    assert rows["DE04b1b3b"]["status"] == "LANDED"
    assert rows["DE04b1b3b"]["current"]["kind"] == "bounded-native"
    assert rows["DE04b1b3b"]["current"]["cases"] == ["session-checkpoints-j-v1"]
    assert "docs/durable-session-checkpoints.md" in rows["DE04b1b3b"]["evidence"]
    assert rows["DE04b1b3b"]["plan_cells"][0] == "DE04b1b3b / DE04b1b3a"
    assert "actual unload" in rows["DE04b1b3b"]["future"].lower()
    assert "second-predicate exception" in rows["DE04b1b3b"]["future"]
    assert rows["DE05"]["plan_cells"][0] == "DE05 / DE04"
    assert all(rows[f"P{i:02}"]["status"] == "OPEN" for i in range(1, 44))


@pytest.mark.parametrize(
    "bad", ["DE04b1b0", "DE04b1b4", "DE04b1ba", "DE04b1b1a", "DE04b1b11"]
)
def test_only_registered_immediate_join_children_are_accepted(bad):
    value = o.load(o.FIXTURES / "coverage.json")
    next(row for row in value["rows"] if row["id"] == "DE04b1b1")["id"] = bad
    with pytest.raises(o.EvidenceError, match="Unknown/duplicate coverage ID"):
        a.coverage(value, o.ROOT, o.contract)


def test_de05_children_preserve_full_execution_acceptance():
    value = o.load(o.FIXTURES / "coverage.json")
    a.coverage(value, o.ROOT, o.contract)
    rows = {row["id"]: row for row in value["rows"]}
    assert rows["DE05"]["status"] == "LANDED"
    assert "DE05a" in rows["DE05"]["current"]["observables"]
    assert "DE05b" in rows["DE05"]["current"]["observables"]
    assert "no own executed" not in rows["DE05"]["current"]["observables"]
    assert rows["DE05a"]["current"]["cases"] == ["task-creation-a-v1"]
    assert rows["DE05a"]["status"] == "LANDED"
    assert rows["DE05a"]["evidence"][0] == a.LEDGER + "#de05a-typed-creation-acceptance"
    assert rows["DE05b"]["status"] == "LANDED"
    assert rows["DE05b"]["current"]["cases"] == ["task-runtime-b-v1"]
    assert (
        rows["DE05b"]["evidence"][0]
        == a.LEDGER + "#de05b-runtime-reservation-recovery-acceptance"
    )
    assert rows["DE05"]["evidence"][0] == rows["DE05b"]["evidence"][0]
    future = rows["DE05b"]["future"]
    for acceptance in (
        "SIGKILL",
        "no dispatch on open",
        "repeated resume",
        "intent/effect/receipt",
        "pre/post-end",
        "terminal cleanup",
        "close seal/join",
        "before backend release",
        "ack loss",
        "same owner",
    ):
        assert acceptance in future
    assert all(
        row["status"] == "OPEN" for key, row in rows.items() if key.startswith("P")
    )


def test_canonical_runtime_completion_owns_exact_acceptance_without_p_closure():
    rows = {row["id"]: row for row in o.load(o.FIXTURES / "coverage.json")["rows"]}
    assert rows["DE06b"]["status"] == rows["DE06"]["status"] == "LANDED"
    assert rows["DE06b"]["current"]["cases"] == ["canonical-checkpoint-b-v1"]
    assert (
        rows["DE06b"]["evidence"][0]
        == a.LEDGER + "#de06b-canonical-checkpoint-acceptance"
    )
    assert rows["DE06"]["evidence"][0] == rows["DE06b"]["evidence"][0]
    for text in (
        "compare=False",
        "raw whitespace",
        "absent/null",
        "thinking/redaction/signatures",
        "usage/cache/cost",
    ):
        assert text in rows["DE06"]["future"]
    assert all(
        row["status"] == "OPEN" for key, row in rows.items() if key.startswith("P")
    )


def test_generation_owns_bounded_actual_evidence_and_retains_successor_boundaries():
    rows = {row["id"]: row for row in o.load(o.FIXTURES / "coverage.json")["rows"]}
    row = rows["DE07"]
    assert row["current"]["kind"] == "bounded-native"
    assert row["current"]["cases"] == ["generation-b-v1"]
    assert row["evidence"][0] == a.LEDGER + "#de07-generation-acceptance"
    assert "Assistant+tool admissions atomic" in row["future"]
    assert "No claim of durable partials" in row["future"]
    assert "all real generation-owned tool admissions" in row["current"]["observables"]
    assert all(
        rows[key]["status"] == "OPEN"
        for key in ("DE08", "DE13", "DE14", "DE15", "DE17", "DE26")
    )
    assert all(
        row["status"] == "OPEN" for key, row in rows.items() if key.startswith("P")
    )
