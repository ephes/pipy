"""Coverage gate negatives and actual parent/child pytest behavior."""

from __future__ import annotations

import copy
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def api():
    spec = importlib.util.spec_from_file_location(
        "ci_aggregate_under_test", ROOT / "scripts/ci_shard_aggregate.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_json(path, value):
    path.write_text(json.dumps(value) + "\n")


@pytest.fixture
def artifacts(tmp_path, api):
    root = tmp_path / "artifacts"
    full = ["test_alpha.py::test_ok", "test_beta.py::test_ok"]
    source = {
        "sha": "a" * 40,
        "tree": "b" * 40,
        "fingerprint": "c" * 64,
        "tracked_files": 2,
    }
    for index, node in enumerate(full):
        directory = root / str(index)
        directory.mkdir(parents=True)
        inventory = {
            "schema": 1,
            "count": 2,
            "index": index,
            "python": "3.14",
            "run_id": "7",
            "run_attempt": "1",
            "source": source,
            "pytest_config": {
                "rootpath": str(tmp_path / "repo"),
                "inipath": str(tmp_path / "repo/pytest.ini"),
                "sha256": "d" * 64,
            },
            "inventory": full,
            "selected": [node],
        }
        write_json(directory / "inventory.json", inventory)
        suite = ET.Element("testsuite")
        case = ET.SubElement(suite, "testcase", name="test_ok")
        props = ET.SubElement(case, "properties")
        ET.SubElement(props, "property", name="pipy_ci_nodeid", value=node)
        ET.ElementTree(suite).write(directory / "junit.xml")
        receipt = {
            "schema": 1,
            "complete": True,
            "exitstatus": 0,
            "inventory_sha256": api.digest(directory / "inventory.json"),
            "junit_sha256": api.digest(directory / "junit.xml"),
            "source_after": source,
            "started": [node],
            "reports": [
                {
                    "nodeid": node,
                    "when": phase,
                    "outcome": "passed",
                    "wasxfail": None,
                    "reason": None,
                }
                for phase in ("setup", "call", "teardown")
            ],
            "problems": [],
        }
        write_json(directory / "receipt.json", receipt)
    return root, source


def validate(api, artifacts):
    root, source = artifacts
    return api.validate(
        root, count=2, python="3.14", run_id="7", run_attempt="1", source=source
    )


def test_exact_once_gate_accepts_complete_source_bound_results(api, artifacts):
    assert validate(api, artifacts)["tests"] == 2


@pytest.mark.parametrize(
    "fault",
    [
        "missing",
        "duplicate-index",
        "run",
        "attempt",
        "version",
        "source",
        "inventory-order",
        "selection",
        "duplicate-selected",
        "unstarted",
        "unfinished",
        "teardown-error",
        "exit",
        "cancelled",
        "junit-identity",
        "junit-duplicate",
        "junit-status",
        "junit-malformed",
        "empty",
        "origin",
        "inventory-digest",
        "junit-digest",
    ],
)
def test_gate_rejects_incomplete_or_contradictory_actual_coverage(
    api, artifacts, fault
):
    root, _source = artifacts
    directory = root / "1"
    inv = json.loads((directory / "inventory.json").read_text())
    receipt = json.loads((directory / "receipt.json").read_text())
    if fault == "missing":
        (directory / "receipt.json").unlink()
    elif fault == "duplicate-index":
        inv["index"] = 0
    elif fault in {"run", "attempt", "version"}:
        inv[{"run": "run_id", "attempt": "run_attempt", "version": "python"}[fault]] = (
            "wrong"
        )
    else:
        corrupt(fault, inv, receipt, directory)
    if fault != "missing":
        write_json(directory / "inventory.json", inv)
        receipt["inventory_sha256"] = api.digest(directory / "inventory.json")
        receipt["junit_sha256"] = api.digest(directory / "junit.xml")
        if fault == "inventory-digest":
            receipt["inventory_sha256"] = "wrong"
        if fault == "junit-digest":
            receipt["junit_sha256"] = "wrong"
        write_json(directory / "receipt.json", receipt)
    with pytest.raises((ValueError, ET.ParseError)):
        validate(api, artifacts)


@pytest.mark.parametrize("fault", ["drift", "missing", "malformed", "foreign"])
def test_gate_rejects_configuration_drift_or_malformed_identity(api, artifacts, fault):
    root, _source = artifacts
    directory = root / "1"
    inv = json.loads((directory / "inventory.json").read_text())
    if fault == "drift":
        inv["pytest_config"]["sha256"] = "e" * 64
    elif fault == "missing":
        del inv["pytest_config"]
    elif fault == "malformed":
        inv["pytest_config"]["sha256"] = "not-a-digest"
    else:
        inv["pytest_config"]["inipath"] = str(root / "foreign.ini")
    write_json(directory / "inventory.json", inv)
    receipt = json.loads((directory / "receipt.json").read_text())
    receipt["inventory_sha256"] = api.digest(directory / "inventory.json")
    write_json(directory / "receipt.json", receipt)
    with pytest.raises(ValueError, match="[Pp]ytest configuration"):
        validate(api, artifacts)


def corrupt(fault, inv, receipt, directory):
    changes = {
        "source": lambda: receipt["source_after"].update(sha="wrong"),
        "inventory-order": lambda: inv["inventory"].reverse(),
        "selection": lambda: inv.update(selected=[inv["inventory"][0]]),
        "duplicate-selected": lambda: inv["selected"].extend(inv["selected"]),
        "unstarted": lambda: receipt.update(started=[]),
        "unfinished": lambda: receipt["reports"].pop(),
        "teardown-error": lambda: receipt["reports"][-1].update(outcome="failed"),
        "exit": lambda: receipt.update(exitstatus=1),
        "cancelled": lambda: receipt.update(complete=False),
        "empty": lambda: inv.update(selected=[]),
        "origin": lambda: receipt.update(problems=["Foreign native module"]),
    }
    if fault in changes:
        changes[fault]()
    elif fault.startswith("junit-") and fault != "junit-digest":
        corrupt_xml(directory / "junit.xml", fault)


def corrupt_xml(path, fault):
    tree = ET.parse(path)
    case = tree.getroot().find("testcase")
    assert case is not None
    if fault == "junit-identity":
        prop = case.find("./properties/property")
        assert prop is not None
        prop.set("value", "foreign")
    elif fault == "junit-duplicate":
        tree.getroot().append(copy.deepcopy(case))
    elif fault == "junit-status":
        ET.SubElement(case, "skipped")
    if fault == "junit-malformed":
        path.write_text("<broken")
    else:
        tree.write(path)


def mini_repo(tmp_path, problem=None):
    root = tmp_path / "mini"
    (root / "scripts").mkdir(parents=True)
    for name in ("ci_shard.py", "ci_shard_aggregate.py"):
        shutil.copy2(ROOT / "scripts" / name, root / "scripts" / name)
    (root / "pytest.ini").write_text("[pytest]\ntestpaths = .\n")
    (root / "conftest.py").write_text("""from pathlib import Path

ROOT = Path(__file__).resolve().parent

def pytest_configure(config):
    assert config.rootpath.resolve() == ROOT
    assert config.inipath is not None
    assert config.inipath.resolve() == ROOT / 'pytest.ini'
""")
    (root / "test_alpha.py").write_text("""import pytest, subprocess, sys

def test_ok(): assert True

def test_skip(): pytest.skip('deliberate call skip')

@pytest.fixture
def unavailable(): pytest.skip('deliberate setup skip')

def test_setup_skip(unavailable): pass

@pytest.mark.xfail(reason='expected failure')
def test_xfail(): assert False

@pytest.mark.xfail(reason='non-strict pass', strict=False)
def test_xpass(): assert True

def test_nested_pytest_is_not_sharded():
    child = subprocess.run([sys.executable, '-m', 'pytest', 'child_only.py', '-q'], capture_output=True, text=True, timeout=30)
    assert child.returncode == 0, child.stdout + child.stderr
    assert '1 passed' in child.stdout
""")
    beta = {
        None: "def test_other(): assert True\n",
        "strict-xpass": "import pytest\n@pytest.mark.xfail(strict=True)\ndef test_other(): assert True\n",
        "teardown-error": "import pytest\n@pytest.fixture\ndef cleanup():\n    yield\n    raise RuntimeError('actual teardown failure')\ndef test_other(cleanup): pass\n",
        "collection-error": "def broken(\n",
    }[problem]
    (root / "test_beta.py").write_text(beta)
    (root / "child_only.py").write_text("""def test_child(request):
    assert not request.config.pluginmanager.hasplugin('pipy-ci-shard-owner')
    assert not any(arg.startswith('--ci-shard') for arg in request.config.invocation_params.args)
""")
    env = dict(os.environ)
    for name in (
        "PYTEST_ADDOPTS",
        "PYTEST_PLUGINS",
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_INDEX_FILE",
    ):
        env.pop(name, None)
    env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
    git(root, env, "init", "-q")
    git(root, env, "add", ".")
    git(
        root,
        env,
        "-c",
        "user.name=CI fixture",
        "-c",
        "user.email=ci@example.invalid",
        "-c",
        "core.hooksPath=/dev/null",
        "commit",
        "-qm",
        "fixture",
    )
    sha = git(root, env, "rev-parse", "HEAD").strip()
    return root, env, sha


def git(root, env, *args):
    return subprocess.check_output(
        ["git", "--no-optional-locks", *args], cwd=root, env=env, text=True
    )


def run_mini(tmp_path, root, env, sha):
    receipts, results = tmp_path / "receipts", []
    for index in range(2):
        output = receipts / str(index)
        output.mkdir(parents=True)
        assert output.is_dir() and not output.is_relative_to(root)
        command = [
            sys.executable,
            "-B",
            "-m",
            "pytest",
            "-c",
            "pytest.ini",
            "--rootdir",
            ".",
            "-p",
            "scripts.ci_shard",
            "--ci-shard-count",
            "2",
            "--ci-shard-index",
            str(index),
            "--ci-shard-output",
            str(output),
            "--ci-shard-run-id",
            "7",
            "--ci-shard-run-attempt",
            "1",
            "--ci-shard-source-sha",
            sha,
            "--junitxml=" + str(output / "junit.xml"),
            "-q",
            "-p",
            "no:cacheprovider",
        ]
        results.append(
            subprocess.run(
                command, cwd=root, env=env, capture_output=True, text=True, timeout=60
            )
        )
    return receipts, results


def test_real_cli_keeps_skip_xfail_xpass_properties_and_nested_child(tmp_path, api):
    root, env, sha = mini_repo(tmp_path)
    receipts, results = run_mini(tmp_path, root, env, sha)
    assert [r.returncode for r in results] == [0, 0], [
        (r.stdout, r.stderr) for r in results
    ]
    inventories = [
        json.loads((receipts / str(index) / "inventory.json").read_text())
        for index in range(2)
    ]
    assert inventories[0]["inventory"] == inventories[1]["inventory"]
    for inventory in inventories:
        assert inventory["pytest_config"]["rootpath"] == str(root.resolve())
        assert inventory["pytest_config"]["inipath"] == str(
            (root / "pytest.ini").resolve()
        )
        assert inventory["pytest_config"]["sha256"] == api.digest(root / "pytest.ini")
    assert {node.split("::", 1)[0] for node in inventories[0]["inventory"]} == {
        "test_alpha.py",
        "test_beta.py",
    }
    assert {node.split("::", 1)[0] for node in inventories[0]["selected"]} == {
        "test_alpha.py"
    }
    assert {node.split("::", 1)[0] for node in inventories[1]["selected"]} == {
        "test_beta.py"
    }
    summary = api.validate(
        receipts,
        count=2,
        python=f"{sys.version_info.major}.{sys.version_info.minor}",
        run_id="7",
        run_attempt="1",
        source=api.source_identity(root, sha),
    )
    assert summary["tests"] == 7
    gate = gate_cli(root, env, sha, receipts)
    assert gate.returncode == 0, gate.stdout + gate.stderr
    assert json.loads(gate.stdout)["tests"] == 7
    reports = [
        report
        for f in receipts.glob("*/receipt.json")
        for report in json.loads(f.read_text())["reports"]
    ]
    assert sum(bool(p["wasxfail"]) for p in reports) == 2
    assert any("deliberate" in (p["reason"] or "") for p in reports)


@pytest.mark.parametrize(
    "problem", ["strict-xpass", "teardown-error", "collection-error"]
)
def test_real_pytest_failures_and_collection_errors_never_aggregate(
    tmp_path, api, problem
):
    root, env, sha = mini_repo(tmp_path, problem)
    receipts, results = run_mini(tmp_path, root, env, sha)
    assert any(r.returncode != 0 for r in results)
    combined = "\n".join(r.stdout + r.stderr for r in results)
    assert {
        "strict-xpass": "XPASS(strict)",
        "teardown-error": "actual teardown failure",
        "collection-error": "SyntaxError",
    }[problem] in combined
    gate = gate_cli(root, env, sha, receipts)
    assert gate.returncode == 1 and "CI coverage rejected" in gate.stderr
    with pytest.raises(ValueError):
        api.validate(
            receipts,
            count=2,
            python=f"{sys.version_info.major}.{sys.version_info.minor}",
            run_id="7",
            run_attempt="1",
            source=api.source_identity(root, sha),
        )


def test_parent_plugin_rejects_empty_partition_and_partial_collection(tmp_path):
    root, env, sha = mini_repo(tmp_path)
    base = [
        sys.executable,
        "-B",
        "-m",
        "pytest",
        "-c",
        "pytest.ini",
        "--rootdir",
        ".",
        "-p",
        "scripts.ci_shard",
        "--ci-shard-count",
        "16",
        "--ci-shard-index",
        "15",
        "--ci-shard-run-id",
        "7",
        "--ci-shard-run-attempt",
        "1",
        "--ci-shard-source-sha",
        sha,
    ]
    for name, extra in [
        ("empty", []),
        ("partial", ["test_alpha.py"]),
        ("filter", ["-k", "test_ok"]),
    ]:
        out = tmp_path / name
        command = base + [
            "--ci-shard-output",
            str(out),
            "--junitxml=" + str(out / "junit.xml"),
            *extra,
        ]
        result = subprocess.run(
            command, cwd=root, env=env, capture_output=True, text=True, timeout=60
        )
        assert result.returncode == 4, result.stdout + result.stderr
        assert {
            "empty": "Empty shard",
            "partial": "complete configured",
            "filter": "filtering",
        }[name] in result.stdout + result.stderr


def gate_cli(root, env, sha, receipts):
    command = [
        sys.executable,
        "-B",
        "scripts/ci_shard_aggregate.py",
        str(receipts),
        "--count",
        "2",
        "--python-version",
        f"{sys.version_info.major}.{sys.version_info.minor}",
        "--run-id",
        "7",
        "--run-attempt",
        "1",
        "--source-sha",
        sha,
    ]
    return subprocess.run(
        command, cwd=root, env=env, capture_output=True, text=True, timeout=30
    )


def root_command(root, output, sha, *, pinned=True):
    command = [sys.executable, "-B", "-m", "pytest"]
    if pinned:
        command.extend(["-c", "pytest.ini", "--rootdir", "."])
    return command + [
        "-p",
        "scripts.ci_shard",
        "--ci-shard-count",
        "2",
        "--ci-shard-index",
        "0",
        "--ci-shard-output",
        str(output),
        "--ci-shard-run-id",
        "7",
        "--ci-shard-run-attempt",
        "1",
        "--ci-shard-source-sha",
        sha,
        "--junitxml=" + str(output / "junit.xml"),
        "-q",
        "-p",
        "no:cacheprovider",
    ]


def test_existing_external_output_cannot_displace_explicit_repo_config(tmp_path, api):
    root, env, sha = mini_repo(tmp_path)
    # A real ancestor config makes discovery of the unpinned command observable.
    # This is an unfixed-command control against the new guard, not old CI proof.
    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    external = tmp_path / "external-existing"
    before, after = external / "before", external / "after"
    before.mkdir(parents=True)
    after.mkdir()
    unpinned = subprocess.run(
        root_command(root, before, sha, pinned=False),
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert unpinned.returncode == 4
    assert "CI root must match repository cwd" in unpinned.stderr
    pinned = subprocess.run(
        root_command(root, after, sha),
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert pinned.returncode == 0, pinned.stdout + pinned.stderr
    inventory = json.loads((after / "inventory.json").read_text())
    assert inventory["pytest_config"]["rootpath"] == str(root.resolve())
    assert inventory["pytest_config"]["inipath"] == str((root / "pytest.ini").resolve())
    assert inventory["pytest_config"]["sha256"] == api.digest(root / "pytest.ini")
    assert {node.split("::", 1)[0] for node in inventory["inventory"]} == {
        "test_alpha.py",
        "test_beta.py",
    }
    assert {node.split("::", 1)[0] for node in inventory["selected"]} == {
        "test_alpha.py"
    }


@pytest.mark.parametrize("wrong", ["root", "config"])
def test_parent_refuses_foreign_root_or_config_before_test_execution(tmp_path, wrong):
    root, env, sha = mini_repo(tmp_path)
    outside = tmp_path / "outside.ini"
    outside.write_text("[pytest]\n")
    output = tmp_path / "external-existing"
    output.mkdir()
    extra = ["--rootdir", str(tmp_path)] if wrong == "root" else ["-c", str(outside)]
    result = subprocess.run(
        root_command(root, output, sha) + extra,
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 4
    assert {"root": "CI root must match", "config": "CI configuration must belong"}[
        wrong
    ] in result.stderr
    assert not (output / "inventory.json").exists()
