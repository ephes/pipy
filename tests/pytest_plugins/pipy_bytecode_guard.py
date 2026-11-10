"""Keep bytecode and assertion-rewrite caching on while pytest collects.

Pytest caches each rewritten test module as a ``__pycache__/*-pytest-*.pyc``
file and only writes it while ``sys.dont_write_bytecode`` is false. Without
the cache every pytest process (each xdist worker too) rewrites every test
module again: full collection then takes about 15 s instead of 3 s.

Two things turn the flag on during collection:

* Some helper scripts set ``sys.dont_write_bytecode = True`` when they are
  imported, so the child processes they start leave no ``__pycache__`` behind.
  When a test module imports such a script, the flag stays set in the pytest
  process for every module collected afterwards.
* ``PYTHONDONTWRITEBYTECODE`` or ``python -B``. Some agent harnesses export
  the variable for every command they run.

This plugin, loaded through ``addopts`` in ``pyproject.toml``, turns bytecode
writing on before each collector runs, so the rewrite caches (and the bytecode
of modules imported during collection) land in the git-ignored
``__pycache__`` directories. Once collection has finished it restores the value
pytest started with, so test bodies, and the child processes they start (which
inherit ``PYTHONDONTWRITEBYTECODE``), see the caller's setting. It lives
outside ``tests/conftest.py`` because that file is hash-pinned by the durable
comparison manifests.
"""

from __future__ import annotations

import sys

import pytest

INITIAL_DONT_WRITE_BYTECODE = sys.dont_write_bytecode


@pytest.hookimpl(tryfirst=True)
def pytest_collectstart(collector: pytest.Collector) -> None:
    sys.dont_write_bytecode = False


@pytest.hookimpl(tryfirst=True)
def pytest_collection_finish(session: pytest.Session) -> None:
    sys.dont_write_bytecode = INITIAL_DONT_WRITE_BYTECODE
