"""The codemode store of a REPL session: ``codemode-store`` custom entries
on its session tree (Pi ``readCodemodeStore`` and ``appendEntry``)."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
from typing import Any

from pipy_harness.native.codemode.outcome import StoreWrites
from pipy_harness.native.coding.codemode_store import (
    CODEMODE_STORE_ENTRY_TYPE,
    read_codemode_store,
    store_entry_data,
)
from pipy_harness.native.session_tree import NativeSessionTree


class SessionCodemodeStore:
    """The store of a session tree: read from and appended to the branch
    that is current when a script starts and ends.

    ``section`` yields the live tree under the session's tree lock, so a
    ``/resume``, ``/fork`` or ``/new`` that replaces the tree is seen. The
    entry is appended directly, not through the extension ``append_entry``,
    which truncates data over 64 KiB.
    """

    __slots__ = ("_section",)

    def __init__(
        self, section: Callable[[], AbstractContextManager[NativeSessionTree]]
    ) -> None:
        self._section = section

    def values(self) -> Mapping[str, Any]:
        with self._section() as tree:
            return read_codemode_store(tree.get_branch())

    def append(self, writes: StoreWrites) -> None:
        with self._section() as tree:
            tree.append_custom(CODEMODE_STORE_ENTRY_TYPE, store_entry_data(writes))
