"""Where a session keeps what codemode scripts ``store()`` (Pi
``readCodemodeStore`` and the ``codemode-store`` custom entries).

A script that succeeds and changed the store appends one custom entry,
``{"set": {key: value}, "delete": [key]}``, to the session branch. ``load()``
reads the entries on the current branch, applied from the root, so each
branch sees the values written on its own path, also after resume, ``/tree``
and ``/fork``. The session's own store is
``repl/codemode_session_store.py``.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any, Protocol

from pipy_harness.native.codemode.outcome import StoreWrites

CODEMODE_STORE_ENTRY_TYPE = "codemode-store"


class CodemodeStore(Protocol):
    """The store of one session, as the codemode runner uses it."""

    def values(self) -> Mapping[str, Any]:
        """What ``load()`` reads now, by key."""
        ...

    def append(self, writes: StoreWrites) -> None:
        """Keep a successful script's writes for later scripts."""
        ...


def _is_store_entry_data(data: object) -> bool:
    if not isinstance(data, dict):
        return False
    values, deleted = data.get("set"), data.get("delete")
    return (
        isinstance(values, dict)
        and isinstance(deleted, list)
        and all(isinstance(key, str) for key in deleted)
    )


def read_codemode_store(entries: Iterable[object]) -> dict[str, Any]:
    """The values of ``load()``: the ``codemode-store`` entries among
    ``entries`` (a session branch, root first), applied in order. An entry
    is a custom entry by its ``type`` (``custom``) and ``custom_type``; one
    whose data is malformed is skipped."""

    store: dict[str, Any] = {}
    for entry in entries:
        data = getattr(entry, "data", None)
        if (
            getattr(entry, "type", None) != "custom"
            or getattr(entry, "custom_type", None) != CODEMODE_STORE_ENTRY_TYPE
            or not _is_store_entry_data(data)
        ):
            continue
        assert isinstance(data, dict)
        for key in data["delete"]:
            store.pop(key, None)
        store.update(data["set"])
    return store


def store_entry_data(writes: StoreWrites) -> dict[str, Any]:
    """The data of the custom entry that keeps ``writes``."""

    return {"set": dict(writes.values), "delete": list(writes.deleted)}
