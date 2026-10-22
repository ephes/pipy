"""Evidence validation and permanent semantic controls for operation provenance."""

import ast
import copy
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = (
    Path(__file__).resolve().parents[2] / "scripts/durable_draft_tracker_comparison.py"
)
spec = importlib.util.spec_from_file_location("tracker_pair_tests", SCRIPT)
assert spec and spec.loader
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def test_closed_manifest_and_all_recipe_shapes():
    assert set(m.manifest()["closure"]) == m.CLOSURE
    inputs = m.recipes()
    assert len(inputs) == 1612
    actual = m.native(inputs)
    assert m.compare(actual, actual) == 0


@pytest.mark.parametrize("index", [0, 1, 2, 3, 4, 5, 6, 7])
def test_schema_valid_semantic_mutants_mismatch(index):
    actual = m.native(m.recipes())
    mutant = copy.deepcopy(actual)
    mutant[index]["replay"] = m.profile("wrong replay")
    assert m.compare(actual, actual) == 0
    assert m.compare(actual, mutant) == 1


@pytest.mark.parametrize(
    "bad",
    [
        None,
        [],
        {},
        ["number", "7ff0000000000000"],
        ["bool", 1],
        ["string", "0"],
        ["object", [["0061", ["null"]], ["0061", ["null"]]]],
    ],
)
def test_invalid_profiles_refuse(bad):
    with pytest.raises(m.o.EvidenceError):
        m.valid_profile(bad)


def test_strict_json_duplicate_and_nonfinite_refusal(tmp_path):
    for index, text in enumerate(('{"x":1,"x":2}', '{"x":NaN}', '{"x":1e999}')):
        path = tmp_path / str(index)
        path.write_text(text)
        with pytest.raises((m.o.EvidenceError, ValueError)):
            m.load(path)


@pytest.mark.parametrize(
    "boundary",
    ["relative", "noncheckout", "subdirectory", "inside", "symlink", "existing"],
)
def test_cli_refusals_use_private_fake_checkout(tmp_path, boundary):
    source = tmp_path / "private-source"
    source.mkdir()
    (source / ".git").mkdir()
    output = tmp_path / "output"
    if boundary == "relative":
        source = Path("private-source")
    elif boundary == "noncheckout":
        (source / ".git").rmdir()
    elif boundary == "subdirectory":
        source = source / "subdirectory"
        source.mkdir()
    elif boundary == "inside":
        output = source / "output"
    elif boundary == "symlink":
        output.symlink_to(tmp_path / "absent", target_is_directory=True)
    elif boundary == "existing":
        output.mkdir()
    process = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "run",
            "--pi-source",
            str(source),
            "--output",
            str(output),
        ],
        cwd=tmp_path,
        env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(m.ROOT / "src")},
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert process.returncode == 2
    expected = (
        b"Output must be outside upstream"
        if boundary == "inside"
        else b"Reference source must be absolute"
        if boundary == "relative"
        else b"Reference source must be a checkout root"
        if boundary in {"noncheckout", "subdirectory"}
        else b"Output entry already exists"
    )
    assert expected in process.stderr
    if boundary not in {"existing", "symlink"}:
        assert not output.exists()
    assert not (output / "result.json").exists()
    assert not (tmp_path / "absent").exists()


def test_compare_cli_zero_one_two(tmp_path):
    expected = m.native(m.recipes())
    paths = [tmp_path / name for name in ("expected.json", "actual.json")]
    paths[0].write_text(json.dumps(expected))
    for code in (0, 1, 2):
        actual = copy.deepcopy(expected)
        if code == 1:
            actual[0]["replay"] = m.profile("wrong")
        if code == 2:
            actual[0]["extra"] = True
        paths[1].write_text(json.dumps(actual))
        process = subprocess.run(
            [sys.executable, str(SCRIPT), "compare", *map(str, paths)],
            env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(m.ROOT / "src")},
            capture_output=True,
            timeout=30,
            check=False,
        )
        assert process.returncode == code, process.stderr


@pytest.fixture
def simulated_node_producer(tmp_path, monkeypatch):
    """Exercise real run/main boundaries with a closed, simulated child response."""
    source = tmp_path / "upstream"
    source.mkdir()
    (source / ".git").mkdir()
    output = tmp_path / "proof"
    inputs = [{"name": "boundary", "base": {}, "actions": []}]
    row = {
        "name": "boundary",
        "ops": m.profile([]),
        "replay": m.profile({}),
        "prepared": m.profile({}),
        "revoked": True,
    }
    response = {"observed": [row], "raw": [], "loaded": []}
    monkeypatch.setattr(m, "manifest", lambda: {"sources": {}})
    monkeypatch.setattr(m, "recipes", lambda: inputs)
    monkeypatch.setattr(m, "SOURCES", set())
    monkeypatch.setattr(m, "MODULES", set())
    monkeypatch.setattr(m.o, "verify_sources", lambda *_: None)

    def execute(_command, _cwd, _output, label, _env, *, timeout):
        assert timeout == 120
        if label == "node-version":
            return b"v26.12.0"
        if label == "npm-version":
            return b"11.20.0"
        assert label == "node-delta"
        return json.dumps(response).encode()

    monkeypatch.setattr(m.o, "run", execute)
    monkeypatch.setattr(
        sys,
        "argv",
        [str(SCRIPT), "run", "--pi-source", str(source), "--output", str(output)],
    )
    return response, output, execute


def test_run_main_requires_node_prepared_before_native(
    simulated_node_producer, monkeypatch, capsys
):
    response, output, _ = simulated_node_producer
    del response["observed"][0]["prepared"]

    def forbidden_native(*_):
        pytest.fail("Missing Node prepared must refuse before native execution")

    monkeypatch.setattr(m, "native", forbidden_native)
    assert m.main() == 2
    assert capsys.readouterr().err == "Node observation requires prepared profile\n"
    assert not (output / "result.json").exists()
    # Native/general observations intentionally have no prepared candidate.
    assert m.observations(response["observed"], m.recipes()) is response["observed"]


@pytest.mark.parametrize("boundary", ["run", "native"])
@pytest.mark.parametrize("error_type", [KeyError, IndexError, AssertionError])
def test_run_main_unexpected_failures_refuse_without_payload(
    simulated_node_producer, monkeypatch, capsys, boundary, error_type
):
    _, output, execute = simulated_node_producer

    def fail(*_args, **_kwargs):
        raise error_type("stored-payload-must-not-escape")

    if boundary == "native":
        monkeypatch.setattr(m, "native", fail)
    else:

        def child(*args, **kwargs):
            if args[3] == "node-delta":
                fail()
            return execute(*args, **kwargs)

        monkeypatch.setattr(m.o, "run", child)
    assert m.main() == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "Unexpected tracker comparison failure\n"
    assert not (output / "result.json").exists()


def test_run_main_does_not_swallow_keyboard_interrupt(
    simulated_node_producer, monkeypatch, capsys
):
    _, output, _ = simulated_node_producer

    def interrupt(*_):
        raise KeyboardInterrupt

    monkeypatch.setattr(m, "native", interrupt)
    with pytest.raises(KeyboardInterrupt):
        m.main()
    assert capsys.readouterr().err == ""
    assert not (output / "result.json").exists()


@pytest.mark.parametrize(
    ("before", "after", "selector"),
    [
        (
            "if _equal(before, after):",
            "if False:",
            "test_restore_cancels_but_structural_identity_survives",
        ),
        (
            "self.pieces = [piece[::-1] for piece in reversed(self.pieces)]\n        self.structural = True",
            "self.pieces = [piece[::-1] for piece in reversed(self.pieces)]\n        self.structural = False",
            "test_structural_history_disables_dense_even_when_canceled",
        ),
        (
            '_RESERVED = frozenset({"__proto__", "constructor", "prototype"})',
            "_RESERVED = frozenset()",
            "test_reserved_restoring_mutation_still_folds",
        ),
        (
            '["m", path, [ranks[index] for index in retained]]',
            '["m", path, list(range(len(retained)))]',
            "test_equal_reverse_and_canceled_reverse",
        ),
        (
            '["p", path, start, 0, inserted]',
            '["p", path, start, 0, []]',
            "test_restore_cancels_but_structural_identity_survives",
        ),
        (
            "if _scalar_equal(old, value):",
            "if old == value:",
            "test_duplicate_utf16_key_admission_and_scalar_number_identity",
        ),
        (
            "if index is None or edge.value is not current:",
            "if index is None:",
            "test_equal_placement_detaches_old_handle_and_retains_baseline_order",
        ),
        ('["t", path, len(a) - overlap]', '["t", path, overlap]', "test_utf16_strings"),
        (
            "self._open = False  # Revoke before validation/replay can fail or escape.",
            "self._open = True  # mutant leaves views active",
            "test_preparation_revokes_every_view_even_when_validation_fails",
        ),
    ],
)
def test_private_source_mutants_defeated_by_designated_tests(
    tmp_path, before, after, selector
):
    # A private minimal package avoids importing or modifying any live checkout.
    package = tmp_path / "src/pipy_harness/native/durable"
    package.mkdir(parents=True)
    for parent in (package, package.parent, package.parent.parent):
        (parent / "__init__.py").write_text("")
    source = (m.ROOT / "src/pipy_harness/native/durable/draft_tracker.py").read_text()
    assert source.count(before) == 1
    (package / "draft_tracker.py").write_text(source.replace(before, after))
    (package / "codec.py").write_bytes(
        (m.ROOT / "src/pipy_harness/native/durable/codec.py").read_bytes()
    )
    (package / "delta_ops.py").write_bytes(
        (m.ROOT / "src/pipy_harness/native/durable/delta_ops.py").read_bytes()
    )
    target = tmp_path / "test_draft_tracker.py"
    target.write_bytes((m.ROOT / "tests/durable/test_draft_tracker.py").read_bytes())
    process = subprocess.run(
        [
            sys.executable,
            "-X",
            "dev",
            "-W",
            "error",
            "-m",
            "pytest",
            "-q",
            "-c",
            "/dev/null",
            "-p",
            "no:cacheprovider",
            "--rootdir",
            str(tmp_path),
            str(target) + "::" + selector,
            "--basetemp",
            str(tmp_path / "pytest"),
        ],
        cwd=tmp_path,
        env={
            "PATH": "/usr/bin:/bin",
            "PYTHONPATH": str(tmp_path / "src"),
            "TMPDIR": str(tmp_path),
        },
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert process.returncode == 1, process.stdout.decode() + process.stderr.decode()
    assert b"failed" in process.stdout


@pytest.mark.parametrize(
    "field",
    [
        "sources",
        "closure",
        "reference_commit",
        "schema_version",
        "extra",
        "digest",
        "bytes",
    ],
)
def test_closed_manifest_drift_refuses(monkeypatch, tmp_path, field):
    value = m.load(m.MANIFEST)
    if field in {"sources", "closure"}:
        value[field]["unexpected.py"] = "0" * 64
    elif field == "extra":
        value[field] = True
    elif field == "digest":
        value["sources"][next(iter(value["sources"]))] = "bad"
    elif field == "bytes":
        value["closure"][next(iter(value["closure"]))] = "0" * 64
    else:
        value[field] = False
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(value))
    monkeypatch.setattr(m, "MANIFEST", manifest)
    with pytest.raises(m.o.EvidenceError):
        m.manifest()


def test_pinned_source_byte_drift_refuses_private_snapshot(monkeypatch, tmp_path):
    source = tmp_path / "private-source"
    source.mkdir()
    (source / ".git").mkdir()
    output = tmp_path / "output"

    def execute(command, cwd, output, label, env, timeout):
        if label == "node-version":
            return b"v26.12.0"
        if label == "npm-version":
            return b"11.20.0"
        assert label.startswith("source-")
        return b"mutated pinned source"

    monkeypatch.setattr(m.o, "run", execute)
    with pytest.raises(m.o.EvidenceError):
        m.run(source, output)
    assert not (output / "result.json").exists()


@pytest.mark.parametrize(
    ("left", "right"),
    [
        (True, 1),
        (False, 0),
        (0.0, -0.0),
        ([0.0], [-0.0]),
        ("\ud800", "\ud801"),
        ("\U0001f600", "\U0001f601"),
        ({"\ud800": 0}, {"\ud801": 0}),
        ({"a": 1, "b": 2}, {"b": 2, "a": 1}),
    ],
)
def test_strict_typed_profile_negative_controls(left, right):
    assert json.dumps(m.profile(left)) != json.dumps(m.profile(right))
    rows = m.native(m.recipes())
    other = copy.deepcopy(rows)
    rows[0]["replay"], other[0]["replay"] = m.profile(left), m.profile(right)
    assert m.compare(rows, other) == 1


@pytest.mark.parametrize(
    ("left", "right"),
    [
        (1, 1.0),
        ("\U0001f600", "\ud83d\ude00"),
        ({"\U0001f600": 0}, {"\ud83d\ude00": 0}),
    ],
)
def test_utf16_and_binary64_equivalent_profiles(left, right):
    assert json.dumps(m.profile(left)) == json.dumps(m.profile(right))


@pytest.mark.parametrize(
    "action",
    [
        [],
        ["unknown", []],
        ["set", []],
        ["set", [True], 1],
        ["set", [-1], 1],
        ["splice", [], 0, -1, []],
        ["splice", [], 0, 0, {}],
        ["hold", [], []],
        ["hset", "missing", ["n"], 1],
        ["pop_reinsert", [], True],
    ],
)
def test_invalid_recipe_actions_fail_closed(action):
    with pytest.raises(m.o.EvidenceError):
        m._action(action, set())


def test_native_import_probe_requires_this_checkout(monkeypatch):
    assert set(m.native_imports()) == {"codec", "delta_ops", "draft_tracker"}
    module = sys.modules[m.Tracker.__module__]
    monkeypatch.setattr(module, "__file__", "/private/tmp/wrong/draft_tracker.py")
    with pytest.raises(m.o.EvidenceError, match="import drift"):
        m.native_imports()


def test_projection_controls_record_acceptance_and_rejections():
    controls = m.projection_controls()
    assert len(controls) == 18
    assert sum(row["equivalent"] for row in controls) == 3
    assert {row["name"] for row in controls} >= {
        "leading-zero",
        "non-index-boundary",
        "unicode-digit",
        "long-numeric-string",
        "ordinary-order",
        "op-order",
        "missing-key",
        "changed-key",
        "changed-value",
        "bool-number",
        "false-zero",
        "signed-zero",
        "utf16",
    }


@pytest.mark.parametrize(
    ("key", "expected"),
    [
        ("0", 0),
        ("1", 1),
        ("2", 2),
        ("4294967294", 4294967294),
        ("4294967295", None),
        ("4294967296", None),
        ("01", None),
        ("00", None),
        ("-0", None),
        ("+1", None),
        ("1.0", None),
        ("1e0", None),
        ("１", None),
        ("١", None),
        ("", None),
        ("9" * 5000, None),
    ],
)
def test_index_key_is_bounded_ascii_canonical_spelling(key, expected):
    assert m.index_key(key.encode("utf-16-be", "surrogatepass").hex()) == expected


@pytest.mark.parametrize("field", ["ops", "replay"])
def test_compare_projects_index_members_but_keeps_raw_profiles(monkeypatch, field):
    inputs = [{"name": "explicit-keys", "base": {}, "actions": []}]
    monkeypatch.setattr(m, "recipes", lambda: inputs)
    rows = m.native(inputs)
    other = copy.deepcopy(rows)
    left, right = {"2": 2, "a": 0, "1": 1}, {"1": 1, "2": 2, "a": 0}
    if field == "ops":
        left, right = [["s", ["target"], left]], [["s", ["target"], right]]
    rows[0][field], other[0][field] = m.profile(left), m.profile(right)
    retained = json.dumps([rows, other])
    assert json.dumps(rows[0][field]) != json.dumps(other[0][field])
    assert m.compare(rows, other) == 0
    assert json.dumps([rows, other]) == retained
    other[0]["revoked"] = False
    assert m.compare(rows, other) == 1


@pytest.mark.parametrize(
    ("before", "after", "control"),
    [
        (" + strings]", " + sorted(strings)]", "leading-zero"),
        ("number < 4294967295", "number <= 4294967295", "non-index-boundary"),
        ('not "1" <= key[0] <= "9" or ', "", "leading-zero"),
        (
            'key = bytes.fromhex(units).decode("utf-16-be", "surrogatepass")',
            'key = bytes.fromhex(units).decode("utf-16-be", "surrogatepass").replace("１", "1")',
            "unicode-digit",
        ),
        (
            "[comparison_profile(child) for child in value[1]]",
            "sorted([comparison_profile(child) for child in value[1]], key=json.dumps)",
            "op-order",
        ),
    ],
)
def test_private_projection_mutants_fail_designated_controls(before, after, control):
    # Execute only the actual comparison functions in an isolated namespace.
    # No checkout or historical source bytes are rewritten.
    tree = ast.parse(SCRIPT.read_text())
    names = {
        "index_key",
        "comparison_profile",
        "profile_equivalent",
        "projection_controls",
    }
    selected = ast.Module(
        body=[
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name in names
        ],
        type_ignores=[],
    )
    source = ast.unparse(selected)
    # AST unparse uses single quotes; both spellings target the same source.
    before, after = before.replace('"', "'"), after.replace('"', "'")
    assert source.count(before) == 1
    namespace = dict(vars(m))
    exec(
        compile(source.replace(before, after), "<private-projection-mutant>", "exec"),
        namespace,
    )
    with pytest.raises(m.o.EvidenceError, match=control):
        namespace["projection_controls"]()
