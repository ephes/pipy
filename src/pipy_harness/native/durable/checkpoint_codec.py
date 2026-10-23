"""Opt-in canonical phase envelopes on the existing Task/TaskRuntime authority."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, NoReturn

from pipy_harness.native.agent.provider_turn import ProviderTurnOutcome
from pipy_harness.native.agent.request import AgentProviderRequestSnapshot
from pipy_harness.native.agent.tools import ToolExecutionOutcome

from .canonical_codec import decode_canonical, encode_canonical
from .codec import OwnedJson
from .provider_checkpoint import PreparedProviderSettings, decode_provider_settings
from .scheduler import TaskContext, TaskRuntime, TaskTx
from .tasks import Task, define_task

ROOTS = {
    "prepare": type(None),
    "request": AgentProviderRequestSnapshot,
    "provider_outcome": ProviderTurnOutcome,
    "tool_outcome": ToolExecutionOutcome,
}


class CheckpointCodecError(ValueError):
    """Fixed structural errors; no payload in persistent fault diagnostics."""


def _fail() -> NoReturn:
    raise CheckpointCodecError("Canonical task checkpoint is incompatible") from None


@dataclass(frozen=True, slots=True, repr=False)
class CanonicalCheckpoint:
    """Owned serialized value; payload and identity detach on every read."""

    _owned: OwnedJson

    def to_tree(self) -> dict[str, Any]:
        if type(self._owned) is not OwnedJson:
            _fail()
        value = self._owned.thaw()
        assert type(value) is dict
        return value

    @property
    def payload(self) -> object:
        return decode_canonical(self.to_tree()["payload"])

    @property
    def settings(self) -> PreparedProviderSettings | None:
        value = self.to_tree()["settings"]
        return None if value is None else decode_provider_settings(value)

    @property
    def identity(self) -> dict[str, Any]:
        return dict(self.to_tree()["identity"])


def decode_checkpoint(
    value: object, *, name: str, version: int | float, phase: str, role: str
) -> CanonicalCheckpoint:
    try:
        if any(type(text) is not str for text in (name, phase, role)) or type(
            version
        ) not in (int, float):
            _fail()
        OwnedJson(version)
        tree = OwnedJson(value).thaw()
        if type(tree) is not dict or set(tree) != {
            "envelope_version",
            "definition",
            "definition_version",
            "phase",
            "role",
            "identity",
            "payload",
            "settings",
        }:
            _fail()
        if (
            type(tree["envelope_version"]) is not int
            or tree["envelope_version"] != 1
            or type(tree["definition"]) is not str
            or tree["definition"] != name
            or type(tree["definition_version"]) not in (int, float)
            or tree["definition_version"] != version
            or type(tree["phase"]) is not str
            or tree["phase"] != phase
            or type(tree["role"]) is not str
            or tree["role"] != role
            or role not in ROOTS
        ):
            _fail()
        identity = tree["identity"]
        if type(identity) is not dict or set(identity) != {
            "attempt",
            "request",
            "cutoff",
        }:
            _fail()
        if any(
            type(identity[key]) is not str or not identity[key]
            for key in ("attempt", "request")
        ):
            _fail()
        if (
            type(identity["cutoff"]) is not int
            or not 0 <= identity["cutoff"] <= 2**53 - 1
        ):
            _fail()
        payload = decode_canonical(tree["payload"])
        if type(payload) is not ROOTS[role]:
            _fail()
        _validate_payload_settings(tree, payload, role)
        return CanonicalCheckpoint(OwnedJson(tree))
    except (TypeError, ValueError, RecursionError):
        _fail()


def _validate_payload_settings(
    tree: dict[str, Any], payload: object, role: str
) -> None:
    settings = tree["settings"]
    if role in ("request", "provider_outcome"):
        saved = decode_provider_settings(settings).to_tree()
        if role == "provider_outcome":
            assert type(payload) is ProviderTurnOutcome
            result = payload.result
            if result is not None and (result.provider_name, result.model_id) != (
                saved["provider"],
                saved["model"],
            ):
                _fail()
        if role == "request":
            assert type(payload) is AgentProviderRequestSnapshot
            if (payload.request.provider_name, payload.request.model_id) != (
                saved["provider"],
                saved["model"],
            ):
                _fail()
    elif settings is not None:
        _fail()


def encode_checkpoint(
    *,
    name: str,
    version: int | float,
    phase: str,
    role: str,
    identity: object,
    payload: object,
    settings: PreparedProviderSettings | None = None,
) -> CanonicalCheckpoint:
    """Synchronous ownership boundary, before any coroutine or queue transfer."""
    try:
        if settings is not None and type(settings) is not PreparedProviderSettings:
            _fail()
        tree = {
            "envelope_version": 1,
            "definition": name,
            "definition_version": version,
            "phase": phase,
            "role": role,
            "identity": identity,
            "payload": encode_canonical(payload),
            "settings": None if settings is None else settings.to_tree(),
        }
        return decode_checkpoint(
            tree, name=name, version=version, phase=phase, role=role
        )
    except (TypeError, ValueError, RecursionError):
        _fail()


CanonicalHandler = Callable[
    [CanonicalCheckpoint, TaskRuntime, TaskContext], Awaitable[None]
]


def _validate_phases(phases: object, initial_phase: object) -> None:
    if type(phases) is not dict or type(initial_phase) is not str:
        _fail()
    for key, value in phases.items():
        if type(key) is not str or type(value) is not tuple or len(value) != 2:
            _fail()
        if type(value[0]) is not str or value[0] not in ROOTS or not callable(value[1]):
            _fail()


def define_canonical_task(
    name: str,
    version: int | float,
    *,
    initial_phase: str,
    phases: dict[str, tuple[str, CanonicalHandler]],
    abort: CanonicalHandler,
) -> Task[Any, Any, Any]:
    """Wrap actual phase handlers; raw finite-JSON tasks are unaffected."""
    _validate_phases(phases, initial_phase)
    captured = dict(phases)
    if initial_phase not in captured:
        _fail()

    def read(value: object, phase: str) -> CanonicalCheckpoint:
        return decode_checkpoint(
            value, name=name, version=version, phase=phase, role=captured[phase][0]
        )

    def initial(value: object) -> object:
        return read(value, initial_phase).to_tree()

    def wrap(
        phase: str, handler: CanonicalHandler
    ) -> Callable[[dict[str, Any], TaskRuntime, TaskContext], Awaitable[None]]:
        async def invoke(
            record: dict[str, Any], runtime: TaskRuntime, context: TaskContext
        ) -> None:
            frame = read(record["state"]["checkpoint"], phase)
            await handler(frame, runtime, context)

        return invoke

    async def abort_phase(
        record: dict[str, Any], runtime: TaskRuntime, context: TaskContext
    ) -> None:
        phase = record["state"]["checkpoint"]["phase"]
        if phase not in captured:
            _fail()
        await abort(read(record["state"]["checkpoint"], phase), runtime, context)

    return define_task(
        name,
        version,
        initial,
        {p: wrap(p, handler) for p, (_, handler) in captured.items()},
        abort_phase,
    )


def commit_checkpoint(
    runtime: TaskRuntime,
    checkpoint: CanonicalCheckpoint,
    *,
    change: Callable[[TaskTx], object | Awaitable[object]] | None = None,
    terminal: bool = False,
) -> Awaitable[None]:
    """Own before suspension, then couple entries/docs with the state commit."""
    if type(checkpoint) is not CanonicalCheckpoint or type(terminal) is not bool:
        _fail()
    frame = checkpoint.to_tree()
    decode_checkpoint(
        frame,
        name=frame["definition"],
        version=frame["definition_version"],
        phase=frame["phase"],
        role=frame["role"],
    )
    state = (
        {"status": "terminal", "outcome": {"status": "completed", "result": frame}}
        if terminal
        else {"status": "running", "checkpoint": frame}
    )

    async def commit() -> None:
        async def apply(tx: TaskTx, current: dict[str, Any]) -> object:
            if (
                current["kind"] != frame["definition"]
                or current["version"] != frame["definition_version"]
            ):
                _fail()
            if change is not None:
                import inspect

                value = change(tx)
                while inspect.isawaitable(value):
                    value = await value
            return state

        await runtime.commit(apply)

    return commit()
