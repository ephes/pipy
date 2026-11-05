"""Tests for the temporary-directory numbering plugin (tests/pytest_plugins)."""

from __future__ import annotations

import os
from pathlib import Path

import pipy_tmp_numbering
import pytest


def test_plugin_is_loaded_and_installed(request: pytest.FixtureRequest) -> None:
    # Fails when a pytest release moves the internals the plugin relies on, so
    # the quadratic numbering cannot come back silently.
    assert request.config.pluginmanager.has_plugin("pipy_tmp_numbering")
    assert pipy_tmp_numbering.installed()


def _count_scans(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    scans: list[str] = []
    original = os.scandir

    def counting(path):  # type: ignore[no-untyped-def]
        scans.append(os.fspath(path))
        return original(path)

    monkeypatch.setattr(pipy_tmp_numbering.os, "scandir", counting)
    return scans


def test_numbers_like_pytest_and_lists_the_directory_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scans = _count_scans(monkeypatch)
    root = tmp_path / "base"
    root.mkdir()
    (root / "Home7").mkdir()  # pytest matches prefixes case-insensitively
    (root / "homeX").mkdir()
    (root / "test_a12").mkdir()
    made = [pipy_tmp_numbering.make_numbered_dir(root, "home") for _ in range(50)]
    # Per-test prefixes (tmp_path uses the test name) need no new listing.
    others = [
        pipy_tmp_numbering.make_numbered_dir(root, prefix)
        for prefix in ("test_a", "test_a1", "test_b", "test_b")
    ]

    assert [path.name for path in made] == [f"home{n}" for n in range(8, 58)]
    assert all(path.is_dir() for path in made)
    assert (os.stat(made[0]).st_mode & 0o777) == 0o700
    assert (root / "homecurrent").resolve() == made[-1]
    # The numbers pytest's scan picks: test_a12 counts as test_a 12 and as
    # test_a1 2, and test_a13 then as test_a1 3.
    assert [path.name for path in others] == [
        "test_a13",
        "test_a14",
        "test_b0",
        "test_b1",
    ]
    assert scans == [str(root)]


def test_taken_name_falls_back_to_pytest_scan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scans = _count_scans(monkeypatch)
    first = pipy_tmp_numbering.make_numbered_dir(tmp_path, "x")
    # A test makes the next two names by hand, bypassing the numbering.
    (tmp_path / "x1").mkdir()
    (tmp_path / "x2").mkdir()
    after = pipy_tmp_numbering.make_numbered_dir(tmp_path, "x")
    following = pipy_tmp_numbering.make_numbered_dir(tmp_path, "x")

    assert (first.name, after.name, following.name) == ("x0", "x3", "x4")
    assert (tmp_path / "xcurrent").resolve() == following
    # The first listing, then pytest's own scan once for the taken name.
    assert scans == [str(tmp_path), str(tmp_path)]


def test_mktemp_uses_the_plugin(tmp_path_factory: pytest.TempPathFactory) -> None:
    first = tmp_path_factory.mktemp("numbering-probe")
    second = tmp_path_factory.mktemp("numbering-probe")
    index = pipy_tmp_numbering._index[str(tmp_path_factory.getbasetemp())]

    number = int(second.name.removeprefix("numbering-probe"))
    assert number == int(first.name.removeprefix("numbering-probe")) + 1
    assert index["numbering-probe"] == number
