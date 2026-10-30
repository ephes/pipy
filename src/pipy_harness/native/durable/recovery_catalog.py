"""Run-owned immutable catalog values; the original header stays authoritative."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, cast

from pipy_harness.native.agent.request import AgentProviderRequestSnapshot
from pipy_harness.native.tools.base import ToolDefinition

from .canonical_codec import decode_canonical
from .codec import OwnedJson
from .documents import Doc, define_doc
from .execution_support import ExecutionSupport
from .tool_values import _model_declaration, declaration, refuse

Tree = dict[str, Any]
HEADER: Doc[Tree] = define_doc(
    "pipy.product.binding",
    1,
    "session",
    lambda: {},
    checkpoint_when=lambda _value, _ops, _info: False,
)


def _identity(header: object) -> Tree:
    if type(header) is not dict:
        refuse()
    # submit() fills these admission pointers after an empty session is created.
    # Every other header field remains part of the immutable run binding. The
    # product binding still validates the complete header and initial graph.
    return {
        name: value
        for name, value in header.items()
        if name not in {"initial_entry", "initial_generation"}
    }


@dataclass(frozen=True, slots=True)
class RecoveryCatalog:
    identity: OwnedJson
    current: OwnedJson

    @classmethod
    def capture(cls, header: Tree, current: Tree) -> RecoveryCatalog:
        saved = ExecutionSupport(header["tools"]).to_tree()
        actual = ExecutionSupport(current).to_tree()
        if (
            saved["family"] != "model_tool"
            or actual["family"] != "model_tool"
            or saved["workspace"] != actual["workspace"]
        ):
            refuse()
        for catalog in (saved, actual):
            for name, row in catalog["tools"].items():
                definition = decode_canonical(row["advertised"])
                _model_declaration(definition)
                declaration(row["descriptor"])
                if type(definition) is not ToolDefinition or definition.name != name:
                    refuse()
        return cls(OwnedJson(_identity(header)), OwnedJson(actual))

    @property
    def unchanged(self) -> bool:
        return OwnedJson(cast(Tree, self.identity.thaw())["tools"]) == self.current

    def validate(self, actual: object, cid: int) -> Tree:
        if OwnedJson(_identity(actual)) != self.identity:
            refuse()
        saved = cast(Tree, self.identity.thaw())
        if saved["conversation"] != cid:
            refuse()
        return saved

    def validate_intent(self, final: Tree, name: str) -> None:
        saved = cast(Tree, self.identity.thaw())["tools"]
        registered = saved["tools"].get(name)
        if (
            registered is None
            or not registered["supported"]
            or saved["workspace"] != final["workspace"]
            or OwnedJson(registered["descriptor"]) != OwnedJson(final["definition"])
        ):
            refuse()

    def next_snapshot(
        self, actual: object, cid: int, snapshot: AgentProviderRequestSnapshot
    ) -> AgentProviderRequestSnapshot:
        saved = self.validate(actual, cid)["tools"]
        current = cast(Tree, self.current.thaw())
        if str(snapshot.request.cwd) != saved["workspace"]:
            refuse()
        chosen = []
        seen: set[str] = set()
        for offered in snapshot.request.available_tools:
            original = saved["tools"].get(offered.name)
            if (
                offered.name in seen
                or original is None
                or _model_declaration(offered)
                != _model_declaration(decode_canonical(original["advertised"]))
            ):
                refuse()
            seen.add(offered.name)
            live = current["tools"].get(offered.name)
            if (
                live is not None
                and live["supported"]
                and OwnedJson(live) == OwnedJson(original)
            ):
                chosen.append(offered)
        # The normal path keeps the actual saved snapshot and members. This
        # comparison form never becomes a replacement persisted declaration.
        if self.unchanged:
            return snapshot
        names = tuple(tool.name for tool in chosen)
        hidden = tuple(
            dict.fromkeys(
                (
                    *snapshot.request.hidden_tool_names,
                    *(
                        tool.name
                        for tool in snapshot.request.available_tools
                        if tool.name not in names
                    ),
                )
            )
        )
        return replace(
            snapshot,
            request=replace(
                snapshot.request,
                available_tools=tuple(chosen),
                hidden_tool_names=hidden,
            ),
            advertised_tool_names=names,
        )
