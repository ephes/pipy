"""Code-free, allowlisted inspection on the existing committed Session line."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import replace
from typing import TYPE_CHECKING, Any, TypeVar, cast

from .checkpoint_codec import decode_checkpoint
from .generation_tasks import (
    GENERATION_NAME,
    GENERATION_VERSION,
    TOOL_NAME,
    TOOL_VERSION,
)
from .generation_transport import owned_call
from .inspection_read import document_exists, round_facts, tasks, terminate_index
from .inspection_values import (
    PHASES,
    InspectionError,
    InspectionPage,
    InspectionSupport,
    TaskInspection,
)
from .records import RecordError, TaskId, safe_integer
from .restart_policy import RestartPolicy
from .session import SessionClosedError, SessionFailed
from .session_core import Session
from .storage import Cursor, Storage, TaskQuery
from .tool_execution import RECEIPT_DOC
from .tool_values import checkpoint

if TYPE_CHECKING:
    from .scheduler import TaskScheduler

Tree = dict[str, Any]
T = TypeVar("T")


def _family(row: Tree) -> str:
    return {GENERATION_NAME: "generation", TOOL_NAME: "model_tool"}.get(
        row["kind"], "custom"
    )


def _known_frame(row: Tree, phase: object) -> None:
    if (row["kind"], row["version"]) == (TOOL_NAME, TOOL_VERSION):
        checkpoint(row["state"]["checkpoint"])
    elif (row["kind"], row["version"]) == (GENERATION_NAME, GENERATION_VERSION):
        role = {
            "prepare": "prepare",
            "request": "request",
            "settle": "provider_outcome",
            "tools": "provider_outcome",
            "blocked": "provider_outcome",
        }.get(cast(str, phase))
        if role is None:
            raise ValueError("Unsupported frame")
        decode_checkpoint(
            row["state"]["checkpoint"],
            name=GENERATION_NAME,
            version=GENERATION_VERSION,
            phase=cast(str, phase),
            role=role,
        )


def _compatibility(
    row: Tree,
    raw_phase: object,
    support: InspectionSupport | None,
    migration_failed: bool,
) -> tuple[str, str | None]:
    if support is None:
        return "unknown", None
    definition = support.definition(row["kind"])
    if definition is None:
        return "unsupported", "definition_missing"
    if definition["version"] != row["version"]:
        if definition["version"] < row["version"]:
            return "unsupported", "stored_version_newer"
        if row["kind"] in (GENERATION_NAME, TOOL_NAME):
            return "unsupported", "definition_version_mismatch"
        if migration_failed or not definition["migrate"]:
            return "unsupported", "migration_failed"
        return "unknown", "migration_required"
    if type(raw_phase) is not str:
        return "unsupported", "unsupported_phase"
    # Blocked is a deliberately nondispatchable frame, not a missing callback.
    if raw_phase != "blocked" and raw_phase not in definition["phases"]:
        return "unsupported", "unsupported_phase"
    return "supported", None


def _hold(storage: Storage, row: Tree) -> str | None:
    rows = tasks(storage, row["conversationId"])
    parent = row
    if row["kind"] == TOOL_NAME:
        parent = next((r for r in rows if r["id"] == row.get("owner")), {})
    if not parent or parent["state"]["status"] not in ("pending", "running", "waiting"):
        return None
    frame = parent["state"].get("checkpoint")
    if type(frame) is not dict or frame.get("phase") not in ("tools", "blocked"):
        return None
    doc, assistant, _, entries = round_facts(storage, parent, rows)
    index = terminate_index(parent, doc["value"], assistant, rows, entries)
    if index is None:
        return None
    if row["id"] == parent["id"]:
        return "retained_terminate"
    for slot_index, slot in enumerate(doc["value"]["slots"]):
        if slot.get("taskId") == row["id"] and slot_index > index:
            return "retained_terminate"
    return None


def _capture(
    storage: Storage,
    row: Tree,
    support: InspectionSupport | None,
    policy: RestartPolicy,
    *,
    migration_failed: bool = False,
) -> TaskInspection:
    state = row["state"]
    dependencies = tuple(state.get("on", ()))
    result = TaskInspection(
        row["id"],
        row["conversationId"],
        row.get("owner"),
        _family(row),
        row["version"],
        state["status"],
        None,
        None,
        None,
        "unknown",
        dependencies[:128],
        len(dependencies),
        restart_count=row.get("restarts"),
        restart_limit=policy.limit,
        restart_parked=policy.parked(row),
    )
    if state["status"] in ("terminal", "completing"):
        # Do not decode an arbitrary outcome result or consult current code.
        return replace(
            result,
            outcome=state["outcome"]["status"],
            compatibility="terminal_readonly"
            if state["status"] == "terminal"
            else "unknown",
        )
    raw = state.get("checkpoint")
    phase = raw.get("phase") if type(raw) is dict else None
    shown = phase if type(phase) is str and phase in PHASES else "unknown"
    compatibility, reason = _compatibility(row, phase, support, migration_failed)
    result = replace(result, phase=shown, compatibility=compatibility, reason=reason)
    result = _native_details(storage, row, raw, phase, result, support)
    return (
        replace(result, reason="restart_limit")
        if result.restart_parked and result.reason is None
        else result
    )


def _supported_format(row: Tree, raw: object, phase: object) -> bool:
    if row["kind"] == GENERATION_NAME:
        field = "envelope_version"
    elif row["kind"] == TOOL_NAME and phase != "call":
        field = "version"
    else:
        return True
    return type(raw) is dict and type(raw.get(field)) is int and raw[field] == 1


def _native_details(
    storage: Storage,
    row: Tree,
    raw: Any,
    phase: object,
    result: TaskInspection,
    support: InspectionSupport | None,
) -> TaskInspection:
    # Unknown/newer frames remain outer metadata, before payload decoding.
    versions = {TOOL_NAME: TOOL_VERSION, GENERATION_NAME: GENERATION_VERSION}
    if (
        (row["kind"], row["version"]) == (TOOL_NAME, TOOL_VERSION)
        and row["state"]["status"] == "pending"
        and phase == "blocked"
        and document_exists(storage, RECEIPT_DOC, row["conversationId"], row["id"])
    ):
        # Existence holds even an unknown/newer frame. No receipt/frame payload
        # decoder or current resource is needed to establish this refusal.
        return replace(
            result,
            reason="receipt_held_needs_repair",
            compatibility=result.compatibility
            if _supported_format(row, raw, phase)
            else "unsupported",
        )
    if row["abortRequested"]:
        return replace(result, reason="abort_requested")
    if row["kind"] not in versions:
        return result
    if row["version"] != versions[row["kind"]] or not _supported_format(
        row, raw, phase
    ):
        return replace(
            result,
            reason=result.reason or "unsupported_format",
            compatibility="unsupported",
        )
    try:
        _known_frame(row, phase)
    except (ValueError, TypeError, KeyError, IndexError, RecursionError):
        return replace(result, reason="unsupported_format", compatibility="unsupported")
    if row["kind"] == TOOL_NAME and phase == "blocked":
        result = replace(result, reason=raw["reason"])
    try:
        from .inspection_support import matches

        compatible = matches(storage, row, support)
        if compatible is False:
            result = replace(result, compatibility="unsupported", reason="incompatible")
    except (ValueError, TypeError, KeyError, IndexError, RecursionError):
        result = replace(result, compatibility="unsupported", reason="incompatible")
    try:
        held = _hold(storage, row)
    except (ValueError, TypeError, KeyError, IndexError, RecursionError):
        return replace(result, reason="unsupported_graph", compatibility="unsupported")
    return result if held is None else replace(result, reason=held)


def _inputs(session: Session, support: InspectionSupport | None) -> None:
    if (
        type(session) is not Session
        or support is not None
        and type(support) is not InspectionSupport
    ):
        raise InspectionError("invalid_request")


def _id(value: object) -> None:
    try:
        safe_integer(value)
    except RecordError:
        raise InspectionError("invalid_request") from None


def inspect_task(
    session: Session,
    task_id: int,
    *,
    support: InspectionSupport | None = None,
    restart_limit: object = 5,
) -> TaskInspection | None:
    _inputs(session, support)
    policy = RestartPolicy(restart_limit)
    _id(task_id)

    def read() -> TaskInspection | None:
        storage = session._owner.storage
        found = storage.task(TaskId(task_id))
        return (
            None
            if found is None
            else _capture(storage, cast(Tree, found.to_tree()), support, policy)
        )

    try:
        return session.read_on_line(read)
    except (SessionClosedError, SessionFailed):
        raise InspectionError("closed") from None
    except InspectionError:
        raise
    except Exception:  # noqa: BLE001 - raw private failures never enter operator output
        raise InspectionError() from None


def _inspect_owned_task(
    scheduler: TaskScheduler, task_id: int
) -> TaskInspection | None:
    """Private owner context: acquire the Session line before its metadata guard."""
    _id(task_id)
    session = scheduler._session

    def read() -> TaskInspection | None:
        storage = session._owner.storage
        found = storage.task(TaskId(task_id))
        if found is None:
            return None
        row = cast(Tree, found.to_tree())
        if row["state"]["status"] in ("terminal", "completing"):
            with scheduler._guard:
                policy = scheduler._restart_policy
            return _capture(storage, row, None, policy)
        from .task_values import identity

        key = identity(row["kind"])
        with scheduler._guard:
            policy = scheduler._restart_policy
            generation = scheduler._definition_generation
            binding = generation.bindings.get(key)
            failed = (
                binding is not None
                and scheduler._migration_failures.get(task_id) is binding
            )
        return _capture(
            storage,
            row,
            generation.support,
            policy,
            migration_failed=failed,
        )

    try:
        return session.read_on_line(read)
    except (SessionClosedError, SessionFailed):
        raise InspectionError("closed") from None
    except InspectionError:
        raise
    except Exception:  # noqa: BLE001 - same finite boundary as ordinary inspection
        raise InspectionError() from None


def inspect_live(
    session: Session,
    *,
    conversation_id: int | None = None,
    limit: int = 128,
    cursor: Cursor | None = None,
    support: InspectionSupport | None = None,
    restart_limit: object = 5,
) -> InspectionPage:
    _inputs(session, support)
    policy = RestartPolicy(restart_limit)
    if conversation_id is not None:
        _id(conversation_id)
    if (
        type(limit) is not int
        or not 1 <= limit <= 128
        or cursor is not None
        and type(cursor) is not Cursor
    ):
        raise InspectionError("invalid_request")

    def read() -> InspectionPage:
        storage = session._owner.storage
        page = storage.scan_tasks(
            TaskQuery(
                {} if conversation_id is None else {"conversationId": conversation_id}
            ),
            limit,
            cursor,
        )
        return InspectionPage(
            tuple(
                _capture(storage, cast(Tree, row.to_tree()), support, policy)
                for row in page.items
                if cast(Tree, row.to_tree())["state"]["status"] != "terminal"
            ),
            page.next,
        )

    try:
        return session.read_on_line(read)
    except (SessionClosedError, SessionFailed):
        raise InspectionError("closed") from None
    except InspectionError:
        raise
    except Exception:  # noqa: BLE001 - raw private failures never enter operator output
        raise InspectionError() from None


class _Read:
    """Loop-owned strong task, while the real worker owns its Session ticket."""

    def __init__(self, job: Callable[[], Any]) -> None:
        self.work = asyncio.create_task(owned_call(job))
        self.work.add_done_callback(self.finished)

    def finished(self, work: asyncio.Task[Any]) -> None:
        if not work.cancelled():
            work.exception()


async def ainspect_task(
    session: Session,
    task_id: int,
    *,
    support: InspectionSupport | None = None,
    restart_limit: object = 5,
) -> TaskInspection | None:
    _inputs(session, support)
    policy = RestartPolicy(restart_limit)
    session._refuse_reentry("ainspect_task")
    read = _Read(
        lambda: inspect_task(session, task_id, support=support, restart_limit=policy)
    )
    return cast(TaskInspection | None, await asyncio.shield(read.work))


async def ainspect_live(
    session: Session,
    *,
    conversation_id: int | None = None,
    limit: int = 128,
    cursor: Cursor | None = None,
    support: InspectionSupport | None = None,
    restart_limit: object = 5,
) -> InspectionPage:
    _inputs(session, support)
    policy = RestartPolicy(restart_limit)
    session._refuse_reentry("ainspect_live")
    read = _Read(
        lambda: inspect_live(
            session,
            conversation_id=conversation_id,
            limit=limit,
            cursor=cursor,
            support=support,
            restart_limit=policy,
        )
    )
    return cast(InspectionPage, await asyncio.shield(read.work))


def inspect_store(
    path: object,
    *,
    conversation_id: int | None = None,
    limit: int = 128,
    cursor: Cursor | None = None,
    restart_limit: object = 5,
) -> InspectionPage:
    """Existing-store only, logical read-only; normal close may maintain WAL."""
    if conversation_id is not None:
        _id(conversation_id)
    if (
        type(limit) is not int
        or not 1 <= limit <= 128
        or cursor is not None
        and type(cursor) is not Cursor
    ):
        raise InspectionError("invalid_request")
    return _store_read(
        path,
        lambda session: inspect_live(
            session,
            conversation_id=conversation_id,
            limit=limit,
            cursor=cursor,
            restart_limit=restart_limit,
        ),
    )


def inspect_store_task(
    path: object, task_id: int, *, restart_limit: object = 5
) -> TaskInspection | None:
    """Terminal/unsupported metadata needs neither current handlers nor auth."""
    _id(task_id)
    return _store_read(
        path,
        lambda session: inspect_task(session, task_id, restart_limit=restart_limit),
    )


def _store_read(path: object, read: Callable[[Session], T]) -> T:
    from .ownership import StorageNotFoundError, StorageOwnershipError
    from .sqlite import open_sqlite_storage
    from .sqlite_connection import UnsupportedInspectionVersion
    from .sqlite_schema import NewerStoreError

    try:
        storage = open_sqlite_storage(
            path, mode="open", schema_policy="validate_current"
        )
    except StorageNotFoundError:
        raise InspectionError("missing_store") from None
    except StorageOwnershipError:
        raise InspectionError("busy") from None
    except (NewerStoreError, UnsupportedInspectionVersion):
        raise InspectionError("unsupported_store_version") from None
    except Exception:  # noqa: BLE001 - raw private failures never enter operator output
        raise InspectionError("invalid_store") from None
    session = Session(storage)
    try:
        return read(session)
    finally:
        try:
            session.close()
        except Exception:  # noqa: BLE001 - raw private failures never enter operator output
            raise InspectionError("unavailable") from None
