"""Test tiers: select test files before collection and mark test categories.

Loaded through ``addopts`` in ``pyproject.toml``. Without ``--pipy-tier`` the
plugin only adds the ``durable``, ``pty``, ``node`` and ``subprocess`` markers,
so plain ``pytest`` still runs everything. With ``--pipy-tier`` it decides which
test files to collect (see ``pipy_test_routing``) and skips the rest in
``pytest_ignore_collect``, so unselected modules are never imported:

* ``default`` (``just test``): smoke tests plus the tests the change affects.
  When a change reaches too far it escalates to the durable area or the full
  tier and says so in the header and the summary; it never drops tests to stay
  fast.
* ``durable`` (``just test-durable``) and ``core`` (``just test-core``): areas.
* ``full`` (``just test-full``): every test.

The few expensive parametrized groups in ``routing.FULL_TIER_MATRICES`` run
their full matrix only when every test file runs or when the change itself
selects their file; other runs keep the listed representative cases and say so
in the header and the summary.

Tier runs use bounded parallelism (pytest-xdist, ``--dist=loadfile``) as
``pipy_test_routing.plan_workers`` decides, unless ``-n`` or
``$PIPY_TEST_WORKERS`` says otherwise; ``0`` runs serially. The selection is
computed once, in the controlling pytest process, and handed to the workers.
"""

from __future__ import annotations

import os
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pipy_test_routing as routing
import pytest

_SELECTION = pytest.StashKey[routing.Selection]()
_REPO = pytest.StashKey[routing.Repo]()
_WORKERS = pytest.StashKey[str]()
# workerinput key that carries the controller's selection to xdist workers.
_WORKER_SELECTION = "pipy_tier_selection"

MARKERS = {
    "durable": "durable-area test (tests/durable/ or a test file that mentions durable)",
    "pty": "drives a real pseudo-terminal or tmux",
    "node": "runs Node.js",
    "subprocess": "starts child processes",
    "smoke": "member of the always-run smoke set of the default tier",
}


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("pipy tiers")
    group.addoption(
        "--pipy-tier",
        choices=routing.TIERS,
        default=None,
        help="select test files by tier before collection (see justfile test recipes)",
    )
    group.addoption(
        "--pipy-base",
        default=None,
        help=f"git ref the default tier diffs against (default: ${routing.BASE_ENV} "
        f"or {routing.DEFAULT_BASE})",
    )


def _repo(config: pytest.Config) -> routing.Repo:
    if _REPO not in config.stash:
        config.stash[_REPO] = routing.Repo(Path(str(config.rootpath)))
    return config.stash[_REPO]


def pytest_configure(config: pytest.Config) -> None:
    for name, description in MARKERS.items():
        config.addinivalue_line("markers", f"{name}: {description}")


@pytest.hookimpl(wrapper=True)
def pytest_cmdline_main(config: pytest.Config) -> Generator[None, object, object]:
    """Select the tier's test files and plan the workers.

    A hook wrapper, so it runs before pytest-xdist's own (``tryfirst``)
    ``pytest_cmdline_main`` turns ``-n`` into worker specs. An xdist worker
    takes the selection its controller computed, so every worker collects the
    same files even if the checkout changes during the run.
    """

    _select(config)
    return (yield)


def _select(config: pytest.Config) -> None:
    tier = config.getoption("--pipy-tier")
    if tier is None:
        return
    workerinput: dict[str, Any] | None = getattr(config, "workerinput", None)
    if workerinput is not None:
        payload = workerinput.get(_WORKER_SELECTION)
        if payload is None:
            raise pytest.UsageError(
                "--pipy-tier: this xdist worker got no selection from its controller"
            )
        config.stash[_SELECTION] = routing.Selection.from_payload(payload)
        return
    if config.args_source != pytest.Config.ArgsSource.TESTPATHS:
        raise pytest.UsageError(
            "--pipy-tier selects test files itself; drop the path arguments "
            "(use -k/-m to narrow a tier) or run pytest on the paths without a tier"
        )
    selection = routing.select(
        tier, _repo(config), base=config.getoption("--pipy-base")
    )
    config.stash[_SELECTION] = selection
    config.stash[_WORKERS] = _plan_workers(config, selection)


def _cpus() -> int:
    try:
        return len(os.sched_getaffinity(0))
    except AttributeError:  # Not on Linux.
        return os.cpu_count() or 1


def _plan_workers(config: pytest.Config, selection: routing.Selection) -> str:
    """Set xdist's ``-n``/``--dist`` for a tier run; return the header line."""

    if not config.pluginmanager.hasplugin("xdist"):
        return "workers: none (pytest-xdist is not loaded)"
    option = config.option
    serial_hint = f"-n 0 or {routing.WORKERS_ENV}=0 runs serially"
    if option.numprocesses is not None:
        source = f"-n {option.numprocesses}"
    elif os.environ.get(routing.WORKERS_ENV, "").strip():
        raw = os.environ[routing.WORKERS_ENV].strip()
        if not raw.isdigit():
            raise pytest.UsageError(
                f"{routing.WORKERS_ENV} must be a number of workers (0 = serial), "
                f"not {raw!r}"
            )
        option.numprocesses = int(raw)
        source = f"{routing.WORKERS_ENV}={raw}"
    elif option.collectonly or option.usepdb:
        option.numprocesses = 0
        source = "--collect-only" if option.collectonly else "--pdb"
    else:
        option.numprocesses, source = routing.plan_workers(selection, _cpus())
    if option.numprocesses == 0:
        return f"workers: none, serial ({source})"
    if option.dist == "no":
        # Whole files per worker: module-scoped fixtures are set up once.
        option.dist = "loadfile"
    return (
        f"workers: {option.numprocesses} (--dist={option.dist}; {source}); "
        f"{serial_hint}"
    )


@pytest.hookimpl(optionalhook=True)
def pytest_configure_node(node: Any) -> None:
    """Hand the controller's selection to an xdist worker."""

    selection = node.config.stash.get(_SELECTION, None)
    if selection is not None:
        node.workerinput[_WORKER_SELECTION] = selection.to_payload()


def _rel(config: pytest.Config, path: Path) -> str | None:
    try:
        return path.resolve().relative_to(config.rootpath.resolve()).as_posix()
    except ValueError:
        return None


def pytest_ignore_collect(collection_path: Path, config: pytest.Config) -> bool | None:
    selection = config.stash.get(_SELECTION, None)
    if selection is None or selection.files is None:
        return None
    rel = _rel(config, collection_path)
    if rel is None:
        return None
    if collection_path.is_dir():
        prefix = rel + "/"
        if any(name.startswith(prefix) for name in selection.files):
            return None
        return True
    if collection_path.name.startswith("test_") and collection_path.suffix == ".py":
        return True if rel not in selection.files else None
    return None


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(
    session: pytest.Session, config: pytest.Config, items: list[pytest.Item]
) -> None:
    # tryfirst: cut matrices before -k/-m deselect, so a kept case that -k drops
    # is not mistaken for a stale FULL_TIER_MATRICES entry.
    repo = _repo(config)
    smoke = set(routing.SMOKE_TESTS)
    selection = config.stash.get(_SELECTION, None)
    partial = selection.partial if selection is not None else {}
    markers_by_file: dict[str, set[str]] = {}
    keep: list[pytest.Item] = []
    dropped: list[pytest.Item] = []
    for item in items:
        rel = _rel(config, item.path)
        if rel is None:
            keep.append(item)
            continue
        if rel not in markers_by_file:
            markers_by_file[rel] = repo.markers(rel)
        for name in sorted(markers_by_file[rel]):
            item.add_marker(name)
        ids = _node_ids(rel, item)
        if not ids.isdisjoint(smoke):
            item.add_marker("smoke")
        if rel in partial and ids.isdisjoint(partial[rel]):
            dropped.append(item)
        else:
            keep.append(item)
    if selection is not None and selection.matrices:
        keep = _cut_matrices(config, keep, selection.matrices, dropped)
    if dropped:
        config.hook.pytest_deselected(items=dropped)
        items[:] = keep


def _cut_matrices(
    config: pytest.Config,
    items: list[pytest.Item],
    matrices: dict[str, frozenset[str]],
    dropped: list[pytest.Item],
) -> list[pytest.Item]:
    """Keep only the representative cases of the cut matrices.

    Appends the other cases to ``dropped``. A representative case that a
    collected group no longer has is a stale ``FULL_TIER_MATRICES`` entry.
    """

    keep: list[pytest.Item] = []
    seen: dict[str, set[str]] = {}
    for item in items:
        rel = _rel(config, item.path)
        group = None if rel is None else _matrix_group(rel, item)
        if group is None or group not in matrices:
            keep.append(item)
            continue
        node = f"{rel}::{item.name}"
        seen.setdefault(group, set()).add(node)
        (keep if node in matrices[group] else dropped).append(item)
    for group, nodes in seen.items():
        missing = matrices[group] - nodes
        if missing:
            raise pytest.UsageError(
                "FULL_TIER_MATRICES in tests/pytest_plugins/pipy_test_routing.py "
                f"names cases {group} no longer has: {', '.join(sorted(missing))}"
            )
    return keep


def _matrix_group(rel: str, item: pytest.Item) -> str:
    return f"{rel}::{getattr(item, 'originalname', None) or item.name}"


def _node_ids(rel: str, item: pytest.Item) -> set[str]:
    """IDs a smoke or guard entry may use for ``item``."""

    ids = {item.nodeid, f"{rel}::{item.name}"}
    original = getattr(item, "originalname", None)
    if original:
        ids.add(f"{rel}::{original}")
    return ids


def pytest_report_header(config: pytest.Config) -> list[str] | None:
    selection = config.stash.get(_SELECTION, None)
    if selection is None:
        return None
    lines = selection.summary_lines()
    workers = config.stash.get(_WORKERS, None)
    if workers is not None:
        lines.append(f"  {workers}")
    return lines


def pytest_terminal_summary(
    terminalreporter: pytest.TerminalReporter, exitstatus: int, config: pytest.Config
) -> None:
    selection = config.stash.get(_SELECTION, None)
    if selection is None:
        return
    if selection.matrices:
        terminalreporter.write_line(
            f"pipy {selection.tier} tier: {len(selection.matrices)} parametrized "
            "group(s) ran representative cases only; `just test-full` runs "
            "their full matrices"
        )
    if selection.tier != routing.TIER_DEFAULT:
        return
    if selection.escalation is not None:
        terminalreporter.write_sep(
            "=", f"pipy default tier ESCALATED to {selection.escalation}", yellow=True
        )
        for reason in selection.reasons:
            terminalreporter.write_line(f"  {reason}")
    else:
        affected = len((selection.files or frozenset()) - selection.smoke_only)
        terminalreporter.write_line(
            f"pipy default tier: smoke set plus {affected} affected test file(s) "
            f"and {len(selection.partial)} partly selected; "
            "run `just test-full` before durable deliveries"
        )
