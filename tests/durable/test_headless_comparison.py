"""Actual isolated native CLI source records and pure semantic discriminators."""

import copy
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "headless_comparison", ROOT / "scripts/durable_headless_comparison.py"
)
assert spec and spec.loader
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


@pytest.fixture(scope="module")
def native_run(tmp_path_factory):
    root = tmp_path_factory.mktemp("headless-source")
    (root / "processes").mkdir()
    m.d.dump(root / "input.json", m.recipe())
    actual = m.native(root, m.o.child_env(root))
    m.d.dump(root / "native.json", actual)
    m.witness.native(SimpleNamespace(**vars(m)), root)
    return actual, root


def test_actual_cli_output_receipts_reopen_and_source_projection(native_run):
    actual, root = native_run
    for row in actual["cases"]:
        value = m.v.decode(row["observed"])
        assert value["completed"] and value["usage_once"]
        assert value["reconnect"] and value["repeat_unchanged"]
        assert value["user_text"] == "input"
        assert value["provider_calls"] == (1 if row["id"] == "plain" else 2)
        assert value["applications"] == (0 if row["id"] == "plain" else 2)
    profiles = m.native_profile_records(root)
    for case in profiles:
        view = m.profiles.native_view(case, m.parent)
        assert view["committed"] and view["before_requests"] == 0
        assert view["input"] == "input" and view["session_consistent"]
        assert view["model"] == ["openai", "gpt-test", "openai-completions"]
        prepared = case["id"] == "prepared-current"
        assert case["resumed"]["code"] == int(prepared)
        assert len(case["resumed"]["requests"]) == int(not prepared)
        assert case["resumed"]["output"]["inspection"]["outcome"] == (
            "faulted" if prepared else "completed"
        )


@pytest.mark.parametrize("name", m.values.MUTANTS)
def test_schema_valid_semantic_common_mutants(native_run, name):
    actual, _ = native_run
    changed = m.values.mutate(actual, name, m.v)
    m.trace(changed)
    assert m.compare(actual, changed) == 1


def test_terminal_fault_is_not_a_completed_output(native_run):
    _, root = native_run
    raw = m.d.load(root / "native-common-raw.json")[1]["raw"]
    row = next(t for t in raw["tasks"] if t["kind"] == "pi.generation")
    row["state"]["outcome"] = {"status": "faulted", "error": "synthetic"}
    assert not m.values.project(raw, m.parent)["completed"]


def test_loaded_native_unlisted_and_module_hash_fail_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(m.profiles, "IDS", ())
    m.d.dump(tmp_path / "native-common-modules.json", {"src/unlisted.py": "0" * 64})
    with pytest.raises(m.o.EvidenceError, match="unlisted"):
        m.native_modules(tmp_path)
    source = "src/pipy_harness/cli.py"
    m.d.dump(tmp_path / "native-common-modules.json", {source: "0" * 64})
    with pytest.raises(m.o.EvidenceError, match="drifted"):
        m.native_modules(tmp_path)


def test_retained_raw_projection_change_is_rejected(native_run):
    _, root = native_run
    path = root / "native.json"
    original = m.d.load(path)
    m.d.dump(path, m.values.mutate(original, "wrong_input", m.v))
    try:
        with pytest.raises(m.o.EvidenceError, match="projection"):
            m.witness.native(SimpleNamespace(**vars(m)), root)
    finally:
        m.d.dump(path, original)


def test_artifact_exclusions_are_run_relative_only(tmp_path):
    root = tmp_path / "ancestor/stores/output"
    (root / "nested").mkdir(parents=True)
    (root / "stores").mkdir()
    for path in (root / "result.json", root / "nested/result.json", root / "stores/db"):
        path.write_text("{}")
    assert set(m.artifact_hashes(root)) == {"nested/result.json"}


def test_malformed_is_not_semantic_mismatch(native_run):
    actual, _ = native_run
    changed = copy.deepcopy(actual)
    changed["unknown"] = True
    with pytest.raises(m.o.EvidenceError):
        m.compare(actual, changed)
    with pytest.raises(m.o.EvidenceError):
        m.profile_check({})


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value.update(completed=1),
        lambda value: value.update(provider_calls=True),
        lambda value: value["usage"].update(input=True),
        lambda value: value["usage"].update(unknown=0),
        lambda value: value["usage"]["cost"].update(input=False),
    ],
)
def test_bool_numeric_and_unknown_usage_protocol_is_rejected(native_run, mutation):
    actual, _ = native_run
    changed = copy.deepcopy(actual)
    value = m.v.decode(changed["cases"][0]["observed"])
    mutation(value)
    changed["cases"][0]["observed"] = m.v.encode(value)
    with pytest.raises(m.o.EvidenceError):
        m.trace(changed)
