"""Finite compatibility checks without execution material or credential access."""

from __future__ import annotations

from typing import Any

from pipy_harness.native.agent.request import AgentProviderRequestSnapshot

from .canonical_codec import decode_canonical
from .checkpoint_codec import decode_checkpoint
from .codec import OwnedJson
from .generation_tasks import GENERATION_NAME, TOOL_NAME
from .inspection_read import round_facts, tasks
from .inspection_values import InspectionSupport
from .resolution_policy import _registration
from .storage import Storage

Tree = dict[str, Any]


def matches(
    storage: Storage, row: Tree, support: InspectionSupport | None
) -> bool | None:
    """None means no admitted finite descriptor, never proven executable."""
    if support is None:
        return None
    definition = support.definition(row["kind"])
    current = None if definition is None else definition["support"]
    if current is None:
        return None
    if row["kind"] == GENERATION_NAME:
        return _generation(row, current)
    if row["kind"] == TOOL_NAME:
        if row["state"]["checkpoint"]["phase"] in ("execute", "settle"):
            from .tool_recovery import committed

            committed(storage, row, current)
            return True
        rows = tasks(storage, row["conversationId"])
        parent = next((r for r in rows if r["id"] == row.get("owner")), None)
        if parent is None:
            return False
        _, assistant, snapshot, _ = round_facts(storage, parent, rows)
        call = next(
            (
                c
                for c in assistant.tool_calls
                if c.provider_correlation_id == row["input"]["callId"]
            ),
            None,
        )
        if call is None:
            return False
        _registration(row, call, snapshot, support)
        return True
    return None


def _generation(row: Tree, current: Tree) -> bool:
    phase = row["state"]["checkpoint"]["phase"]
    role = {"prepare": "prepare", "request": "request"}.get(phase, "provider_outcome")
    frame = decode_checkpoint(
        row["state"]["checkpoint"],
        name=row["kind"],
        version=row["version"],
        phase=phase,
        role=role,
    )
    seed = decode_canonical(row["input"])
    if type(seed) is not AgentProviderRequestSnapshot or (
        seed.request.provider_name,
        seed.request.model_id,
    ) != (current["provider"], current["model"]):
        return False
    if frame.settings is None:
        return True
    saved = frame.settings.to_tree()
    return all(
        OwnedJson(saved[key]) == OwnedJson(current[key])
        for key in ("provider", "model", "api")
    )
