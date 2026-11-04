"""Keep bytecode and assertion-rewrite caching on while pytest collects.

Some helper scripts set ``sys.dont_write_bytecode = True`` when they are
imported, so the child processes they start leave no ``__pycache__`` behind.
When a test module imports such a script during collection, the flag stays set
in the pytest process, and pytest then skips writing assertion-rewrite caches
for every module collected afterwards. Each later run rewrites those modules
again, which made full collection several times slower.

This plugin, loaded through ``addopts`` in ``pyproject.toml``, records the flag
when pytest starts (so ``python -B`` and ``PYTHONDONTWRITEBYTECODE`` are still
honoured) and restores that value before each collector runs and once
collection has finished. It lives outside ``tests/conftest.py`` because that
file is hash-pinned by the durable comparison manifests.
"""

from __future__ import annotations

import sys

import pytest

INITIAL_DONT_WRITE_BYTECODE = sys.dont_write_bytecode


def _restore() -> None:
    sys.dont_write_bytecode = INITIAL_DONT_WRITE_BYTECODE


@pytest.hookimpl(tryfirst=True)
def pytest_collectstart(collector: pytest.Collector) -> None:
    _restore()


@pytest.hookimpl(tryfirst=True)
def pytest_collection_finish(session: pytest.Session) -> None:
    _restore()
