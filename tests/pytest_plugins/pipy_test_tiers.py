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
"""

from __future__ import annotations

from pathlib import Path

import pipy_test_routing as routing
import pytest

_SELECTION = pytest.StashKey[routing.Selection]()
_REPO = pytest.StashKey[routing.Repo]()

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
    tier = config.getoption("--pipy-tier")
    if tier is None:
        return
    if config.args_source != pytest.Config.ArgsSource.TESTPATHS:
        raise pytest.UsageError(
            "--pipy-tier selects test files itself; drop the path arguments "
            "(use -k/-m to narrow a tier) or run pytest on the paths without a tier"
        )
    config.stash[_SELECTION] = routing.select(
        tier, _repo(config), base=config.getoption("--pipy-base")
    )


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


def pytest_collection_modifyitems(
    session: pytest.Session, config: pytest.Config, items: list[pytest.Item]
) -> None:
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
    if dropped:
        config.hook.pytest_deselected(items=dropped)
        items[:] = keep


def _node_ids(rel: str, item: pytest.Item) -> set[str]:
    """IDs a smoke or guard entry may use for ``item``."""

    ids = {item.nodeid, f"{rel}::{item.name}"}
    original = getattr(item, "originalname", None)
    if original:
        ids.add(f"{rel}::{original}")
    return ids


def pytest_report_header(config: pytest.Config) -> list[str] | None:
    selection = config.stash.get(_SELECTION, None)
    return selection.summary_lines() if selection is not None else None


def pytest_terminal_summary(
    terminalreporter: pytest.TerminalReporter, exitstatus: int, config: pytest.Config
) -> None:
    selection = config.stash.get(_SELECTION, None)
    if selection is None or selection.tier != routing.TIER_DEFAULT:
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
