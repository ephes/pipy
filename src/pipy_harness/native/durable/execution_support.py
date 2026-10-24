"""Finite private definition metadata, captured before inspection or dispatch.

These descriptors are not persisted credentials or an execution authorization.
They contain no callbacks and never probe material/auth/ports on a read path.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, cast

from .codec import OwnedJson
from .records import shape

Tree = dict[str, Any]


@dataclass(frozen=True, slots=True, init=False, repr=False)
class ExecutionSupport:
    _owned: OwnedJson = field(repr=False)

    def __init__(self, value: object) -> None:
        owned = OwnedJson(value)
        tree = owned.thaw()
        if type(tree) is not dict or type(tree.get("version")) is not int:
            raise ValueError("Execution support is incompatible")
        if tree["version"] != 1:
            raise ValueError("Execution support is incompatible")
        if tree.get("family") == "generation":
            _generation(tree)
        elif tree.get("family") == "model_tool":
            _tool(tree)
        else:
            raise ValueError("Execution support is incompatible")
        object.__setattr__(self, "_owned", owned)

    def to_tree(self) -> Tree:
        if type(self._owned) is not OwnedJson:
            raise ValueError("Execution support is incompatible")
        return cast(Tree, self._owned.thaw())


def _generation(tree: Tree) -> None:
    shape(
        tree,
        {
            "family",
            "version",
            "provider",
            "model",
            "api",
            "thinking",
            "options",
            "pricing",
        },
    )
    if any(type(tree[k]) is not str for k in ("provider", "model", "api")):
        raise ValueError("Execution support is incompatible")
    if tree["thinking"] is not None and type(tree["thinking"]) is not str:
        raise ValueError("Execution support is incompatible")
    if (
        type(tree["options"]) is not dict
        or tree["pricing"] is not None
        and type(tree["pricing"]) is not dict
    ):
        raise ValueError("Execution support is incompatible")


def _tool(tree: Tree) -> None:
    shape(tree, {"family", "version", "workspace", "tools"})
    if type(tree["workspace"]) is not str or type(tree["tools"]) is not dict:
        raise ValueError("Execution support is incompatible")
    for value in tree["tools"].values():
        shape(value, {"descriptor", "advertised", "supported"})
        if type(value["supported"]) is not bool or any(
            type(value[k]) is not dict for k in ("descriptor", "advertised")
        ):
            raise ValueError("Execution support is incompatible")
