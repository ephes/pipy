"""Make ``tmp_path_factory.mktemp`` cost the same for every test of a run.

Pytest names temporary directories ``<prefix><N>`` and finds ``N`` by listing
the whole base temporary directory and taking the highest number in use for the
prefix (``_pytest.pathlib.make_numbered_dir``). Every ``tmp_path`` and every
``mktemp`` call adds entries there (the autouse fixtures in ``tests/conftest.py``
call ``mktemp`` twice per test), so a test late in a run lists thousands of
entries three times: the cost grows quadratically with the number of tests in
one process. ``tmp_path`` uses the test's name as the prefix, so a cache per
prefix does not help.

This plugin, loaded through ``addopts`` in ``pyproject.toml``, replaces the
numbering that ``TempPathFactory.mktemp`` uses. It lists a base directory once,
indexes the highest number of every prefix that a name there can have, and then
keeps the index up to date with the directories it creates. A name that turns
out to be taken (a test made it by hand) falls back to pytest's own scan.
Directory names, modes and the ``<prefix>current`` symlink stay as pytest makes
them, and ``mkdir`` without ``exist_ok`` still guarantees a fresh directory; the
base temporary directory itself is still created by pytest's code.

It lives outside ``tests/conftest.py`` because that file is hash-pinned by the
durable comparison manifests. If a pytest release moves these internals, the
plugin leaves pytest alone and ``tests/test_pytest_tmp_numbering.py`` fails.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

import _pytest.pathlib as _pytest_pathlib
import _pytest.tmpdir as _pytest_tmpdir

_ORIGINAL: Callable[..., Path] | None = getattr(
    _pytest_tmpdir, "make_numbered_dir", None
)
_FORCE_SYMLINK: Callable[[Path, str, Path], object] | None = getattr(
    _pytest_pathlib, "_force_symlink", None
)
_PARSE_NUM: Callable[[str], int] | None = getattr(_pytest_pathlib, "parse_num", None)

# Highest number in use, by base directory and lower-cased prefix (pytest
# matches prefixes case-insensitively).
_index: dict[str, dict[str, int]] = {}


def _register(index: dict[str, int], name: str) -> None:
    """Record ``name`` under every prefix that leaves a number behind.

    A suffix is a number when pytest's ``parse_num`` (``int()``) accepts it,
    which includes forms such as ``1_27``; it starts with a digit or ``+``.
    """

    assert _PARSE_NUM is not None
    lowered = name.lower()
    for cut, char in enumerate(lowered):
        if char.isdigit() or char == "+":
            number = _PARSE_NUM(lowered[cut:])
            prefix = lowered[:cut]
            if number > index.get(prefix, -1):
                index[prefix] = number


def _root_index(root: Path) -> dict[str, int]:
    key = str(root)
    index = _index.get(key)
    if index is None:
        index = {}
        with os.scandir(root) as entries:
            for entry in entries:
                _register(index, entry.name)
        _index[key] = index
    return index


def make_numbered_dir(root: Path, prefix: str, mode: int = 0o700) -> Path:
    """``_pytest.pathlib.make_numbered_dir`` without a listing per call."""

    assert _ORIGINAL is not None and _FORCE_SYMLINK is not None
    index = _root_index(root)
    new_path = root.joinpath(f"{prefix}{index.get(prefix.lower(), -1) + 1}")
    try:
        new_path.mkdir(mode=mode)
    except FileExistsError:
        # Made outside this numbering: use pytest's scan, which also links.
        new_path = _ORIGINAL(root, prefix, mode)
    else:
        _FORCE_SYMLINK(root, prefix + "current", new_path)
    _register(index, new_path.name)
    return new_path


def installed() -> bool:
    """Whether ``TempPathFactory.mktemp`` uses this plugin's numbering."""

    return getattr(_pytest_tmpdir, "make_numbered_dir", None) is make_numbered_dir


if None not in (_ORIGINAL, _FORCE_SYMLINK, _PARSE_NUM):
    _pytest_tmpdir.make_numbered_dir = make_numbered_dir  # type: ignore[assignment]
