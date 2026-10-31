"""Explicit parent CLI plugin. Never enable through environment or conftest."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest

from scripts.ci_shard_aggregate import (
    PROPERTY,
    SCHEMA,
    assignment,
    digest,
    source_identity,
)


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("pipy CI coverage")
    group.addoption("--ci-shard-count", type=int)
    group.addoption("--ci-shard-index", type=int)
    group.addoption("--ci-shard-output", type=Path)
    group.addoption("--ci-shard-run-id")
    group.addoption("--ci-shard-run-attempt")
    group.addoption("--ci-shard-source-sha")


@pytest.hookimpl(tryfirst=True)
def pytest_configure(config: pytest.Config) -> None:
    root = Path.cwd().resolve()
    if config.rootpath.resolve() != root:
        raise pytest.UsageError("CI root must match repository cwd")
    if config.inipath is None or config.inipath.resolve().parent != root:
        raise pytest.UsageError("CI configuration must belong to repository cwd")
    count, index = (
        config.getoption("ci_shard_count"),
        config.getoption("ci_shard_index"),
    )
    output = config.getoption("ci_shard_output")
    required = [
        config.getoption(name)
        for name in ("ci_shard_run_id", "ci_shard_run_attempt", "ci_shard_source_sha")
    ]
    if not (
        count and index is not None and 0 <= index < count and output and all(required)
    ):
        raise pytest.UsageError(
            "CI parent requires count/index/output/run/attempt/source arguments"
        )
    filters = (
        "keyword",
        "markexpr",
        "deselect",
        "ignore",
        "ignore_glob",
        "pyargs",
        "lf",
        "failedfirst",
        "newfirst",
        "stepwise",
    )
    if any(config.getoption(name, default=None) for name in filters):
        raise pytest.UsageError("CI collection cannot use filtering/reordering options")
    expected = config.getini("testpaths") or [str(Path.cwd())]
    if [Path(a).resolve() for a in config.args] != [
        Path(a).resolve() for a in expected
    ]:
        raise pytest.UsageError("CI must collect the complete configured testpaths")
    if config.option.collectonly or config.option.xmlpath is None:
        raise pytest.UsageError("CI shard needs actual tests and --junitxml")
    output = output.resolve()
    if Path(config.option.xmlpath).resolve() != output / "junit.xml":
        raise pytest.UsageError("JUnit must use the shard output directory")
    output.mkdir(parents=True, exist_ok=True)
    if any(
        (output / name).exists()
        for name in ("receipt.json", "inventory.json", "junit.xml")
    ):
        raise pytest.UsageError("CI shard output already contains evidence")
    config.pluginmanager.register(
        Shard(config, count, index, output), "pipy-ci-shard-owner"
    )


class Shard:
    def __init__(
        self, config: pytest.Config, count: int, index: int, output: Path
    ) -> None:
        self.config, self.count, self.index, self.output = config, count, index, output
        self.root = Path.cwd().resolve()
        self.sha = config.getoption("ci_shard_source_sha")
        self.before = source_identity(self.root, self.sha)
        self.started: list[str] = []
        self.reports: list[dict[str, Any]] = []
        self.inventory: dict[str, Any] | None = None

    @pytest.hookimpl(trylast=True)
    def pytest_collection_modifyitems(
        self, session: pytest.Session, items: list[pytest.Item]
    ) -> None:
        full = [item.nodeid for item in items]
        if session.testsfailed or len(full) != len(set(full)):
            raise pytest.UsageError(
                "CI full collection failed or has duplicate node IDs"
            )
        selected, other = [], []
        for item in items:
            (
                selected if assignment(item.nodeid, self.count) == self.index else other
            ).append(item)
        self.inventory = {
            "schema": SCHEMA,
            "count": self.count,
            "index": self.index,
            "python": f"{sys.version_info.major}.{sys.version_info.minor}",
            "run_id": self.config.getoption("ci_shard_run_id"),
            "run_attempt": self.config.getoption("ci_shard_run_attempt"),
            "source": self.before,
            "pytest_config": {
                "rootpath": str(self.config.rootpath.resolve()),
                "inipath": str(self.config.inipath.resolve()),
                "sha256": digest(self.config.inipath),
            },
            "inventory": full,
            "selected": [item.nodeid for item in selected],
        }
        self._put("inventory.json", self.inventory)
        if not full or not selected:
            raise pytest.UsageError("Empty shard is a configuration error")
        for item in selected:
            item.user_properties.append((PROPERTY, item.nodeid))
        self.config.hook.pytest_deselected(items=other)
        items[:] = selected

    def pytest_runtest_logstart(self, nodeid: str) -> None:
        self.started.append(nodeid)

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        self.reports.append(
            {
                "nodeid": report.nodeid,
                "when": report.when,
                "outcome": report.outcome,
                "wasxfail": getattr(report, "wasxfail", None),
                "reason": str(report.longrepr)
                if report.skipped or report.failed
                else None,
            }
        )

    def _origins(self) -> tuple[list[dict[str, str]], list[str]]:
        origins, problems = [], []
        for name, module in sorted(tuple(sys.modules.items())):
            if not (
                name == "pipy_harness"
                or name.startswith("pipy_harness.")
                or name == "pipy_session"
                or name.startswith("pipy_session.")
            ):
                continue
            file = getattr(module, "__file__", None)
            if file is None:
                continue
            path = Path(file).resolve()
            if not path.is_relative_to(self.root / "src"):
                problems.append(f"Foreign native module: {name}")
            origins.append({"module": name, "path": str(path), "sha256": digest(path)})
        return origins, problems

    def _put(self, name: str, value: Any) -> None:
        path = self.output / name
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(value, ensure_ascii=True, indent=2) + "\n")
        temporary.replace(path)

    @pytest.hookimpl(trylast=True)
    def pytest_sessionfinish(self, session: pytest.Session, exitstatus: int) -> None:
        if self.inventory is None:
            return
        after = source_identity(self.root, self.sha)
        origins, problems = self._origins()
        if after != self.before:
            problems.append("Source changed while tests ran")
        junit = self.output / "junit.xml"
        if not junit.is_file():
            problems.append("JUnit sessionfinish is absent")
        self._put(
            "receipt.json",
            {
                "schema": SCHEMA,
                "complete": True,
                "exitstatus": int(exitstatus),
                "inventory_sha256": digest(self.output / "inventory.json"),
                "started": self.started,
                "reports": self.reports,
                "source_after": after,
                "junit_sha256": digest(junit) if junit.is_file() else None,
                "native_origins": origins,
                "problems": problems,
                "parent": {
                    "pid": os.getpid(),
                    "pgid": os.getpgrp(),
                    "executable": sys.executable,
                    "version": sys.version,
                },
            },
        )
        if problems and session.exitstatus == 0:
            session.exitstatus = pytest.ExitCode.INTERNAL_ERROR
