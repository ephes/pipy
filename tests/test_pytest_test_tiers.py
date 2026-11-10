"""Tests for the test-tier routing (tests/pytest_plugins/pipy_test_*.py)."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import textwrap
from pathlib import Path

import pipy_test_routing as routing
import pytest

PLUGIN_DIR = Path(__file__).parent / "pytest_plugins"


def _write(root: Path, rel: str, text: str = "") -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture(autouse=True)
def _not_in_ci(monkeypatch: pytest.MonkeyPatch) -> None:
    # The default tier runs everything under CI; these tests check routing.
    monkeypatch.delenv("CI", raising=False)


@pytest.fixture
def repo(tmp_path: Path) -> routing.Repo:
    """A miniature repository with each kind of path the router knows."""

    _write(tmp_path, "pyproject.toml", "[tool.pytest.ini_options]\n")
    _write(tmp_path, "src/pkg/core.py", "VALUE = 1\n")
    _write(tmp_path, "src/pkg/native/durable/store.py", "VALUE = 2\n")
    _write(tmp_path, "tests/conftest.py")
    _write(tmp_path, "tests/helper_mod.py", "HELP = 1\n")
    _write(tmp_path, "tests/test_a.py", "import helper_mod\n\ndef test_a(): pass\n")
    _write(tmp_path, "tests/chain_helper.py", "import tool_x\n")
    _write(tmp_path, "tests/test_b.py", "import chain_helper\n\ndef test_b(): pass\n")
    _write(tmp_path, "tests/test_docs_build.py", "def test_docs(): pass\n")
    _write(tmp_path, "tests/test_plain.py", "def test_plain(): pass\n")
    _write(tmp_path, "tests/unused_helper.py", "X = 1\n")
    _write(tmp_path, "tests/durable/test_store.py", "def test_store(): pass\n")
    _write(
        tmp_path,
        "tests/test_mentions.py",
        "# exercises the durable product surface\ndef test_m(): pass\n",
    )
    _write(tmp_path, "tests/fixtures/family_one/case.json", "{}")
    _write(
        tmp_path,
        "tests/test_family.py",
        'FIXTURES = "family_one"\n\ndef test_f(): pass\n',
    )
    _write(tmp_path, "scripts/tool_x.py", "Y = 1\n")
    _write(tmp_path, "tests/test_architecture_quality_gates.py", "def test_g(): pass\n")
    _write(tmp_path, "docs/guide.md", "guide\n")
    _write(tmp_path, "docs/pinned.md", "pinned\n")
    _write(
        tmp_path,
        "tests/fixtures/durable/thing-v1.manifest.json",
        json.dumps({"inputs": {"closure": {"docs/pinned.md": "0" * 64}}}),
    )
    return routing.Repo(tmp_path)


def _route(repo: routing.Repo, *changed: str) -> routing.Selection:
    return routing.select(routing.TIER_DEFAULT, repo, changed=changed, base="base")


def test_areas_partition_and_detect_durable_mentions(repo: routing.Repo) -> None:
    assert repo.durable_files == {
        "tests/durable/test_store.py",
        "tests/test_mentions.py",
    }
    core = routing.select(routing.TIER_CORE, repo).files
    assert core is not None and "tests/durable/test_store.py" not in core
    assert core | repo.durable_files == set(repo.test_files)
    assert routing.select(routing.TIER_FULL, repo).files is None


def test_no_changes_selects_only_the_smoke_set(repo: routing.Repo) -> None:
    selection = _route(repo)
    smoke_files = {node.split("::")[0] for node in routing.SMOKE_TESTS}
    assert selection.escalation is None
    assert selection.files == smoke_files
    assert selection.smoke_only == smoke_files


def test_changed_test_file_selects_itself(repo: routing.Repo) -> None:
    selection = _route(repo, "tests/test_plain.py")
    assert selection.escalation is None
    assert selection.files is not None and "tests/test_plain.py" in selection.files
    assert "tests/test_architecture_quality_gates.py" in selection.files
    assert "tests/test_a.py" not in selection.files


def test_deleted_test_file_selects_only_repository_guards(
    repo: routing.Repo,
) -> None:
    selection = _route(repo, "tests/test_gone.py")
    assert selection.escalation is None
    assert selection.files is not None
    assert selection.files - selection.smoke_only == {
        "tests/test_architecture_quality_gates.py"
    }


@pytest.mark.parametrize(
    ("changed", "expected"),
    [
        # Repository-wide guards scan tests/ and scripts/ for every change there.
        (
            "tests/helper_mod.py",
            {"tests/test_a.py", "tests/test_architecture_quality_gates.py"},
        ),
        # scripts/tool_x.py is reached through tests/chain_helper.py.
        (
            "scripts/tool_x.py",
            {"tests/test_b.py", "tests/test_architecture_quality_gates.py"},
        ),
        (
            "tests/fixtures/family_one/case.json",
            {"tests/test_family.py", "tests/test_architecture_quality_gates.py"},
        ),
        ("docs/guide.md", {"tests/test_docs_build.py"}),
    ],
)
def test_helpers_fixtures_and_docs_select_the_tests_that_mention_them(
    repo: routing.Repo, changed: str, expected: set[str]
) -> None:
    selection = _route(repo, changed)
    assert selection.escalation is None
    assert selection.files is not None
    assert selection.files - selection.smoke_only == expected


@pytest.mark.parametrize(
    "changed",
    [
        "src/pkg/core.py",
        "pyproject.toml",
        "uv.lock",
        "tests/conftest.py",
        "tests/pytest_plugins/anything.py",
        "tests/unused_helper.py",
        "somewhere/unknown.bin",
    ],
)
def test_far_reaching_or_unknown_paths_escalate_to_full(
    repo: routing.Repo, changed: str
) -> None:
    selection = _route(repo, changed, "tests/test_plain.py")
    assert selection.escalation == routing.TIER_FULL
    assert selection.files is None
    assert any(changed in reason for reason in selection.reasons)
    assert "ESCALATED to full" in "\n".join(selection.summary_lines())


@pytest.mark.parametrize(
    "changed", ["src/pkg/native/durable/store.py", "docs/pinned.md"]
)
def test_durable_source_and_pinned_files_escalate_to_the_durable_area(
    repo: routing.Repo, changed: str
) -> None:
    selection = _route(repo, changed)
    assert selection.escalation == routing.TIER_DURABLE
    assert selection.files is not None
    assert repo.durable_files <= selection.files


def test_single_test_guards_select_only_that_test(
    repo: routing.Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    node = "tests/test_plain.py::test_plain"
    monkeypatch.setitem(routing.REPO_GUARDS, node, ("docs/",))

    guarded = _route(repo, "docs/guide.md")
    assert guarded.partial["tests/test_plain.py"] == {node}

    # A file that is selected as a whole is not narrowed by a guard entry.
    whole = _route(repo, "docs/guide.md", "tests/test_plain.py")
    assert "tests/test_plain.py" not in whole.partial
    assert whole.files is not None and "tests/test_plain.py" in whole.files


def test_full_escalation_wins_over_durable(repo: routing.Repo) -> None:
    selection = _route(repo, "docs/pinned.md", "src/pkg/core.py")
    assert selection.escalation == routing.TIER_FULL
    assert selection.files is None


def _git(root: Path, *args: str) -> str:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(
        GIT_AUTHOR_NAME="t",
        GIT_AUTHOR_EMAIL="t@example.invalid",
        GIT_COMMITTER_NAME="t",
        GIT_COMMITTER_EMAIL="t@example.invalid",
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_CONFIG_NOSYSTEM="1",
    )
    return subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    ).stdout


def _init_git(root: Path) -> None:
    _git(root, "init", "-q", "-b", "main")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "base")
    _git(root, "branch", "base")


def test_git_changes_cover_commits_index_worktree_and_untracked_files(
    repo: routing.Repo,
) -> None:
    root = repo.root
    _init_git(root)
    _write(root, "tests/test_plain.py", "def test_plain(): assert True\n")
    _git(root, "commit", "-qam", "committed change")
    _write(root, "tests/test_a.py", "import helper_mod\n\ndef test_a(): assert 1\n")
    _write(root, "tests/test_b.py", "import chain_helper\n\ndef test_b(): assert 1\n")
    _git(root, "add", "tests/test_b.py")
    _write(root, "tests/test_new.py", "def test_new(): pass\n")

    changed = routing.git_changed_paths(root, "base")

    assert changed == [
        "tests/test_a.py",
        "tests/test_b.py",
        "tests/test_new.py",
        "tests/test_plain.py",
    ]


@pytest.mark.parametrize("value", ["true", "1"])
def test_ci_runs_the_full_tier(
    repo: routing.Repo, monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("CI", value)
    selection = routing.select(routing.TIER_DEFAULT, repo, base="base")
    assert selection.escalation == routing.TIER_FULL
    assert selection.files is None
    assert "CI" in selection.reasons[0]


def test_hash_pinned_verification_files_escalate_to_the_durable_area(
    repo: routing.Repo,
) -> None:
    _write(repo.root, ".github/workflows/ci.yml", "jobs: {}\n")
    _write(
        repo.root,
        "tests/fixtures/durable/manifest.json",
        json.dumps({"verification": {".github/workflows/ci.yml": "1" * 64}}),
    )
    selection = _route(repo, ".github/workflows/ci.yml")
    assert selection.escalation == routing.TIER_DURABLE
    assert repo.closure_files[".github/workflows/ci.yml"] == 1

    # Deleting or renaming a pinned file must not drop its pin.
    (repo.root / ".github/workflows/ci.yml").unlink()
    deleted = routing.select(
        routing.TIER_DEFAULT,
        routing.Repo(repo.root),
        changed=[".github/workflows/ci.yml"],
        base="base",
    )
    assert deleted.escalation == routing.TIER_DURABLE


def test_missing_base_ref_escalates_to_full(repo: routing.Repo) -> None:
    _init_git(repo.root)
    selection = routing.select(routing.TIER_DEFAULT, repo, base="no-such-ref")
    assert selection.escalation == routing.TIER_FULL
    assert selection.files is None
    assert "no-such-ref" in selection.reasons[0]


def _run_pytest(
    root: Path, *args: str, env_extra: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"PYTEST_ADDOPTS", "CI", routing.BASE_ENV, routing.WORKERS_ENV}
        and not key.startswith("PYTEST_XDIST_")
    }
    env["PYTHONPATH"] = str(PLUGIN_DIR)
    env.update(env_extra or {})
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", *args],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def _plugin_project(root: Path) -> None:
    _write(root, "pytest.ini", "[pytest]\ntestpaths = tests\n")
    _write(root, "tests/test_kept.py", "def test_kept(): pass\n")
    # Importing this module would fail collection: proves it is never imported.
    _write(
        root,
        "tests/test_unselected.py",
        "raise RuntimeError('unselected module was imported')\n",
    )
    _write(
        root,
        "tests/test_pty_thing.py",
        "import subprocess, pty\n\ndef test_marked(): pass\n",
    )
    # Starts processes only through a helper module.
    _write(root, "tests/spawn_helper.py", "import subprocess\n")
    _write(
        root,
        "tests/test_via_helper.py",
        "import spawn_helper\n\ndef test_via_helper(): pass\n",
    )
    _init_git(root)


def test_default_tier_skips_unselected_modules_before_import(tmp_path: Path) -> None:
    _plugin_project(tmp_path)
    _write(tmp_path, "tests/test_kept.py", "def test_kept(): assert True\n")

    result = _run_pytest(
        tmp_path, "-p", "pipy_test_tiers", "--pipy-tier=default", "--pipy-base=base"
    )

    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "1 passed" in output
    assert "pipy test tier: default" in output
    assert "tests/test_kept.py: changed test file (+0 that mention it" in output


def test_default_tier_reports_escalation_and_runs_everything(tmp_path: Path) -> None:
    _plugin_project(tmp_path)
    _write(tmp_path, "src/pkg/core.py", "X = 2\n")

    result = _run_pytest(
        tmp_path,
        "-p",
        "pipy_test_tiers",
        "--pipy-tier=default",
        "--pipy-base=base",
        "--co",
        "-q",
    )

    output = result.stdout + result.stderr
    # Everything is collected, so the poisoned module errors during collection.
    assert "unselected module was imported" in output
    assert "ESCALATED to full" in output
    assert "src/pkg/core.py is runtime source" in output


def test_markers_are_applied_without_a_tier(tmp_path: Path) -> None:
    _plugin_project(tmp_path)

    result = _run_pytest(
        tmp_path,
        "-p",
        "pipy_test_tiers",
        "-m",
        "pty and subprocess",
        "-q",
        "tests/test_pty_thing.py",
        "tests/test_kept.py",
    )

    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "1 passed, 1 deselected" in output


def test_markers_follow_helpers_the_test_uses(tmp_path: Path) -> None:
    _plugin_project(tmp_path)

    result = _run_pytest(
        tmp_path,
        "-p",
        "pipy_test_tiers",
        "-m",
        "subprocess",
        "-q",
        "tests/test_via_helper.py",
        "tests/test_kept.py",
    )

    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "1 passed, 1 deselected" in output


def test_tier_rejects_explicit_paths(tmp_path: Path) -> None:
    _plugin_project(tmp_path)

    result = _run_pytest(
        tmp_path, "-p", "pipy_test_tiers", "--pipy-tier=full", "tests/test_kept.py"
    )

    assert result.returncode == pytest.ExitCode.USAGE_ERROR
    assert "--pipy-tier selects test files itself" in result.stderr


def test_small_default_selection_runs_serially(tmp_path: Path) -> None:
    _plugin_project(tmp_path)
    _write(tmp_path, "tests/test_kept.py", "def test_kept(): assert True\n")

    result = _run_pytest(
        tmp_path, "-p", "pipy_test_tiers", "--pipy-tier=default", "--pipy-base=base"
    )

    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "workers: none, serial (1 whole test file(s) selected)" in output
    assert "created:" not in output


def test_workers_collect_the_selection_their_controller_made(tmp_path: Path) -> None:
    _plugin_project(tmp_path)
    _write(tmp_path, "tests/test_kept.py", "def test_kept(): assert True\n")
    _write(tmp_path, "tests/test_kept_too.py", "def test_kept_too(): assert True\n")

    result = _run_pytest(
        tmp_path,
        "-p",
        "pipy_test_tiers",
        "--pipy-tier=default",
        "--pipy-base=base",
        env_extra={routing.WORKERS_ENV: "2"},
    )

    output = result.stdout + result.stderr
    # The workers never import the poisoned unselected module.
    assert result.returncode == 0, output
    assert "2 passed" in output
    assert f"workers: 2 (--dist=loadfile; {routing.WORKERS_ENV}=2)" in output
    assert "created: 2/2 workers" in output


def test_area_tiers_run_on_bounded_workers(tmp_path: Path) -> None:
    _plugin_project(tmp_path)
    (tmp_path / "tests/test_unselected.py").unlink()

    result = _run_pytest(tmp_path, "-p", "pipy_test_tiers", "--pipy-tier=full")

    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "3 passed" in output
    # The child inherits this process's CPU affinity, so it sees the same CPUs.
    cpus = (
        len(os.sched_getaffinity(0))
        if hasattr(os, "sched_getaffinity")
        else (os.cpu_count() or 1)
    )
    expected = routing.plan_workers(routing.Selection(tier="full", files=None), cpus)[0]
    assert f"workers: {expected} (--dist=loadfile; full tier)" in output


def test_command_line_and_environment_choose_serial_runs(tmp_path: Path) -> None:
    _plugin_project(tmp_path)
    (tmp_path / "tests/test_unselected.py").unlink()

    by_option = _run_pytest(
        tmp_path, "-p", "pipy_test_tiers", "--pipy-tier=full", "-n", "0"
    )
    by_env = _run_pytest(
        tmp_path,
        "-p",
        "pipy_test_tiers",
        "--pipy-tier=full",
        env_extra={routing.WORKERS_ENV: "0"},
    )
    bad = _run_pytest(
        tmp_path,
        "-p",
        "pipy_test_tiers",
        "--pipy-tier=full",
        env_extra={routing.WORKERS_ENV: "many"},
    )

    assert "workers: none, serial (-n 0)" in by_option.stdout, by_option.stderr
    assert "3 passed" in by_option.stdout
    assert f"workers: none, serial ({routing.WORKERS_ENV}=0)" in by_env.stdout
    assert "3 passed" in by_env.stdout
    assert bad.returncode == pytest.ExitCode.USAGE_ERROR
    assert f"{routing.WORKERS_ENV} must be a number of workers" in bad.stderr


@pytest.mark.parametrize(
    ("cpus", "workers"),
    [(1, 1), (2, 2), (4, 4), (6, 4), (8, 4), (10, 5), (12, 6), (16, 8), (64, 8)],
)
def test_default_workers_use_half_the_cpus_within_bounds(
    cpus: int, workers: int
) -> None:
    assert routing.default_workers(cpus) == workers


def test_plan_workers_bounds_parallel_tiers_and_keeps_small_gates_serial() -> None:
    full = routing.Selection(tier="full", files=None)
    assert routing.plan_workers(full, 64) == (routing.MAX_WORKERS, "full tier")
    assert routing.plan_workers(full, 16) == (8, "full tier")
    assert routing.plan_workers(full, 4) == (4, "full tier")
    assert routing.plan_workers(full, 2) == (2, "full tier")
    assert routing.plan_workers(full, 1) == (1, "full tier")
    escalated = routing.Selection(tier="default", files=None, escalation="full")
    assert routing.plan_workers(escalated, 64)[0] == routing.MAX_WORKERS
    smoke = {"tests/test_s.py": frozenset({"tests/test_s.py::test_x"})}
    small = routing.Selection(
        tier="default",
        files=frozenset({"tests/test_s.py", "tests/test_a.py"}),
        partial=smoke,
    )
    assert routing.plan_workers(small, 64) == (0, "1 whole test file(s) selected")
    many = frozenset(f"tests/test_{n}.py" for n in range(routing.SERIAL_MAX_FILES + 1))
    large = routing.Selection(tier="default", files=many)
    assert routing.plan_workers(large, 64)[0] == routing.MAX_WORKERS


def test_selection_payload_round_trips() -> None:
    selection = routing.Selection(
        tier="default",
        files=frozenset({"tests/test_a.py", "tests/test_s.py"}),
        partial={"tests/test_s.py": frozenset({"tests/test_s.py::test_x"})},
        matrices={"tests/test_a.py::test_m": frozenset({"tests/test_a.py::test_m[1]"})},
        escalation="durable",
        reasons=["why"],
        changed=("a", "b"),
        base="base",
    )
    payload = json.loads(json.dumps(selection.to_payload()))
    assert routing.Selection.from_payload(payload) == selection
    everything = routing.Selection(tier="full", files=None)
    assert routing.Selection.from_payload(everything.to_payload()) == everything


def test_real_smoke_set_exists_and_includes_durable_tests() -> None:
    repo = routing.Repo()
    smoke_files = {node.split("::")[0] for node in routing.SMOKE_TESTS}
    assert smoke_files <= set(repo.test_files)
    assert any(name.startswith("tests/durable/") for name in smoke_files)

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "-q",
            "-p",
            "no:cacheprovider",
            *routing.SMOKE_TESTS,
        ],
        cwd=routing.REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"{len(routing.SMOKE_TESTS)} tests collected" in result.stdout


def test_real_repository_areas_cover_every_test_file() -> None:
    repo = routing.Repo()
    durable = routing.select(routing.TIER_DURABLE, repo).files
    core = routing.select(routing.TIER_CORE, repo).files
    assert durable is not None and core is not None
    assert {f for f in repo.test_files if f.startswith("tests/durable/")} <= durable
    assert durable | core == set(repo.test_files)
    assert all(
        (routing.REPO_ROOT / guard.split("::")[0]).is_file()
        for guard in routing.REPO_GUARDS
    )
    # Durable-manifest closures pin this suite's shared conftest; touching it
    # must never stay inside the default gate's narrow selection.
    assert repo.closure_files.get("tests/conftest.py", 0) > 0
    assert (
        routing.select(
            routing.TIER_DEFAULT, repo, changed=["tests/conftest.py"]
        ).escalation
        == routing.TIER_FULL
    )


def test_routing_cli_prints_the_selection(capsys: pytest.CaptureFixture[str]) -> None:
    assert routing.main(["--tier", "durable"]) == 0
    out = capsys.readouterr().out
    assert textwrap.dedent(out).startswith("pipy test tier: durable")


def test_recipes_forward_arguments_with_their_quoting() -> None:
    justfile = (routing.REPO_ROOT / "justfile").read_text(encoding="utf-8")
    for tier in routing.TIERS:
        assert f'uv run pytest --pipy-tier={tier} "$@"' in justfile
    assert justfile.count("[positional-arguments]") >= len(routing.TIERS)


# Test files that look like tree scanners to the audit below but only walk temp
# directories or name the repository files they read (mentions cover those).
_NOT_TREE_SCANNERS = {
    "tests/durable/test_tool_interrupted_sdk.py": "walks temp copies",
    "tests/test_durable_loop_integration.py": "names scripts/durable_prototype",
    "tests/test_durable_prototype.py": "names scripts/durable_prototype",
    "tests/test_native_extension_activation_sealing.py": "scans src only",
    "tests/test_native_extension_conformance.py": "names its docs example",
    "tests/test_native_mcp_shell_cli.py": "walks temp homes",
    "tests/test_native_tool_search_tool.py": "walks temp trees",
    "tests/test_sdk_codemode_mcp_example.py": "names its docs example",
}


def test_every_repository_tree_scanner_is_a_registered_guard() -> None:
    """A test that walks tests/, scripts/, docs/ or typings/ must be in
    REPO_GUARDS, or the default gate misses violations in files it never names."""

    scans = re.compile(r"rglob\(|os\.walk\(|\.glob\(|iterdir\(|ls-files")
    trees = re.compile(r"""["'](tests|scripts|docs|typings)["']""")
    repo = routing.Repo()
    candidates = {
        rel
        for rel in repo.test_files
        if scans.search(repo.texts[rel]) and trees.search(repo.texts[rel])
    }
    candidates.discard("tests/test_pytest_test_tiers.py")
    guard_files = {guard.split("::")[0] for guard in routing.REPO_GUARDS}
    unregistered = candidates - guard_files - set(_NOT_TREE_SCANNERS)
    assert not unregistered
    assert set(_NOT_TREE_SCANNERS) <= candidates


_MATRIX_GROUP = "tests/test_durable_loop_integration.py::test_real_loop_hard_crashes"


def test_full_matrices_run_only_in_full_or_when_the_change_selects_them(
    repo: routing.Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        routing,
        "FULL_TIER_MATRICES",
        {"tests/durable/test_store.py::test_store": ("a", "b")},
    )
    kept = {
        "tests/durable/test_store.py::test_store": frozenset(
            {
                "tests/durable/test_store.py::test_store[a]",
                "tests/durable/test_store.py::test_store[b]",
            }
        )
    }

    assert routing.select(routing.TIER_FULL, repo).matrices == {}
    assert routing.select(routing.TIER_DURABLE, repo).matrices == kept
    assert routing.select(routing.TIER_CORE, repo).matrices == {}
    # Escalated to the durable area by a pinned doc: representative cases.
    escalated = _route(repo, "docs/pinned.md")
    assert escalated.escalation == routing.TIER_DURABLE
    assert escalated.matrices == kept
    assert any("full matrices: 1" in line for line in escalated.summary_lines())
    # The change selects the file itself: its full matrix runs.
    assert _route(repo, "tests/durable/test_store.py").matrices == {}
    assert _route(repo, "tests/durable/test_store.py", "docs/pinned.md").matrices == {}
    # Escalated to full: everything runs.
    assert _route(repo, "src/pkg/core.py").matrices == {}


def _matrix_project(root: Path, cases: list[str]) -> None:
    _write(root, "pytest.ini", "[pytest]\ntestpaths = tests\n")
    _write(
        root,
        "tests/test_durable_loop_integration.py",
        "# exercises the durable loop\nimport pytest\n\n"
        f"@pytest.mark.parametrize('case', {cases!r})\n"
        "def test_real_loop_hard_crashes(case): pass\n\n"
        "def test_other(): pass\n",
    )
    _init_git(root)


def test_tier_runs_cut_full_matrices_and_report_it(tmp_path: Path) -> None:
    representative = list(routing.FULL_TIER_MATRICES[_MATRIX_GROUP])
    _matrix_project(tmp_path, [*representative, "extra-safe", "extra-unsafe"])

    durable = _run_pytest(
        tmp_path, "-p", "pipy_test_tiers", "--pipy-tier=durable", "-n", "0"
    )
    full = _run_pytest(tmp_path, "-p", "pipy_test_tiers", "--pipy-tier=full", "-n", "0")
    keyword = _run_pytest(
        tmp_path,
        "-p",
        "pipy_test_tiers",
        "--pipy-tier=durable",
        "-n",
        "0",
        "-k",
        "safe and not unsafe",
    )

    output = durable.stdout + durable.stderr
    assert durable.returncode == 0, output
    assert f"{len(representative) + 1} passed, 2 deselected" in output
    assert "full matrices: 1 parametrized group(s)" in output
    assert "ran representative cases only" in output
    assert full.returncode == 0, full.stdout + full.stderr
    assert f"{len(representative) + 3} passed" in full.stdout
    assert "full matrices" not in full.stdout
    # -k narrows after the cut; it does not trip the stale-entry check.
    assert keyword.returncode == 0, keyword.stdout + keyword.stderr
    assert f"{len(representative) // 2} passed" in keyword.stdout


def test_stale_full_matrix_entry_fails_the_run(tmp_path: Path) -> None:
    representative = list(routing.FULL_TIER_MATRICES[_MATRIX_GROUP])
    _matrix_project(tmp_path, [*representative[1:], "renamed-safe"])

    result = _run_pytest(
        tmp_path, "-p", "pipy_test_tiers", "--pipy-tier=durable", "-n", "0"
    )

    assert result.returncode == pytest.ExitCode.USAGE_ERROR
    assert representative[0] in result.stderr
    assert "FULL_TIER_MATRICES" in result.stderr


def test_real_full_matrix_cases_exist() -> None:
    files = sorted({group.split("::")[0] for group in routing.FULL_TIER_MATRICES})
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "-q",
            "-p",
            "no:cacheprovider",
            *files,
        ],
        cwd=routing.REPO_ROOT,
        # Inherited -q/-k would hide node IDs and fail the check falsely.
        env={k: v for k, v in os.environ.items() if k != "PYTEST_ADDOPTS"},
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    collected = set(result.stdout.split())
    for group, cases in routing.FULL_TIER_MATRICES.items():
        matrix = {node for node in collected if node.startswith(f"{group}[")}
        kept = {f"{group}[{case}]" for case in cases}
        assert kept < matrix, group
