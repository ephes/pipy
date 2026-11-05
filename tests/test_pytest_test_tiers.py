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


def _run_pytest(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"PYTEST_ADDOPTS", "CI", routing.BASE_ENV}
    }
    env["PYTHONPATH"] = str(PLUGIN_DIR)
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
