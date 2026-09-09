"""Pi-compatible `--mode rpc` stdin/stdout JSONL protocol (`docs/automation-rpc.md`).

A long-lived headless process: it reads one JSON command per LF-delimited line
on stdin and writes JSON responses plus asynchronous session events on stdout,
mirroring Pi's ``runRpcMode`` (`packages/coding-agent/src/modes/rpc/rpc-mode.ts`).

Design (stdlib only, composes — does not fork — the runtime):

- The same ``CodingSession.run`` loop the CLI/TUI use runs on a worker
  thread, fed prompts through a blocking line channel. The native session tree
  is the durable source of truth and the introspection source for
  ``get_state``/``get_messages``/``get_session_stats``.
- A single serialized :class:`JsonlWriter` carries every stdout record so
  command responses and async session events never interleave mid-line.
- The reader/dispatch runs on the main thread, so ``steer``/``follow_up``/
  ``abort`` reach the running prompt promptly rather than blocking behind it.

This is a full-content surface: assistant text, tool args/results, and bash
output are emitted like Pi. Only auth secrets/tokens are never emitted.
"""

from __future__ import annotations

import json
import queue
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO, TextIO

from pipy_harness.capture import CapturePolicy
from pipy_harness.models import RunRequest
from pipy_harness.native.agent.content import ProductContent
from pipy_harness.native.agent.results import AgentCancellationReason
from pipy_harness.native.automation.jsonl import (
    JsonlLineBuffer,
    JsonlWriter,
    loads_strict,
)
from pipy_harness.native.automation.serialize import serialize_message
from pipy_harness.native.coding.session_controller import (
    _NativeControlReady,
    _NativeControlSnapshot,
    _NativeSessionControlBridge,
)
from pipy_harness.native.command_sandbox import (
    CommandPolicy,
    CommandStatus,
    run_command,
)

# Sentinel distinguishing "omit the response data field" from an explicit
# ``data: null`` (Pi's `... | null` data contract, e.g. cycle_model).
_OMIT: Any = object()
_MISSING_COMPACTION_CORRELATION: Any = object()


@dataclass(slots=True)
class _BashOperation:
    """One private direct-RPC bash lifetime, owned by ``NativeRpcServer``."""

    cancel_event: threading.Event
    relay: "_BashUpdateRelay"
    abort_requested: bool = False


class _BashUpdateRelay:
    """One bounded, closeable callback-to-writer relay.

    The sandbox's per-stream gate already bounds every accepted operation's
    total safe payload.  Coalescing into one pending delta keeps the relay
    bounded even when ``JsonlWriter`` is blocked, while this condition lock is
    independent of the RPC operation registry lock.
    """

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._pending = ""
        self._closed = False

    def admit(self, delta: str) -> None:
        """Nonblocking safe-delta admission called from the sandbox selector."""

        if not delta:
            return
        with self._condition:
            if self._closed:
                return
            self._pending += delta
            self._condition.notify()

    def close(self) -> None:
        with self._condition:
            self._closed = True
            self._condition.notify_all()

    def take(self) -> str | None:
        with self._condition:
            while not self._pending and not self._closed:
                self._condition.wait()
            if self._pending:
                delta = self._pending
                self._pending = ""
                return delta
            return None


def _dumps(value: Any) -> str:
    """Compact JSON encode with the exact ``serialize_json_line`` byte options."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _js_since(value: Any) -> str:
    """Render a get_entries ``since`` value the way JS template coercion would.

    Pi builds the not-found message as ``Entry not found: ${command.since}``.
    ``since`` is typed ``string``; the in-contract inputs render as themselves,
    and the one realistic out-of-type value a JSON client can send — explicit
    ``null`` — must render as ``null`` (not Python's ``None``). Non-string,
    non-null values are malformed requests rendered best-effort via ``str``.
    """
    if value is None:
        return "null"
    return str(value)


def _encode_session_tree(roots: list[Any]) -> str:
    """Iteratively encode session-tree roots to the ``tree`` JSON array string.

    A linear conversation nests one level per entry, so the tree can be ~1000+
    deep; both a recursive builder and CPython's recursive ``json.dumps`` C
    encoder would ``RecursionError`` on it. Here the deep child spine is walked
    with an explicit stack and only the shallow per-node entry dict / scalars go
    through ``json.dumps`` (identical byte options to ``serialize_json_line``),
    so encoding is ``RecursionError``-free at any depth. Fields are emitted in
    Pi's ``SessionTreeNode`` object order: ``entry``, ``children``, then optional
    ``label`` / ``labelTimestamp`` (omitted when unset, matching ``JSON.stringify``).
    """
    from pipy_harness.native.session_tree import _entry_to_json

    def suffix(node: Any) -> str:
        parts = ["]"]
        if node.label is not None:
            parts.append(',"label":' + _dumps(node.label))
        if node.label_timestamp is not None:
            parts.append(',"labelTimestamp":' + _dumps(node.label_timestamp))
        parts.append("}")
        return "".join(parts)

    out: list[str] = []
    # Stack items: ("lit", text) emits verbatim; ("node", node) expands a node.
    # Processed LIFO, so children/commas are pushed in reverse.
    stack: list[tuple[str, Any]] = [("lit", "]")]
    for i in range(len(roots) - 1, -1, -1):
        stack.append(("node", roots[i]))
        if i > 0:
            stack.append(("lit", ","))
    stack.append(("lit", "["))

    while stack:
        kind, payload = stack.pop()
        if kind == "lit":
            out.append(payload)
            continue
        node = payload
        out.append('{"entry":' + _dumps(_entry_to_json(node.entry)) + ',"children":[')
        stack.append(("lit", suffix(node)))
        children = node.children
        for i in range(len(children) - 1, -1, -1):
            stack.append(("node", children[i]))
            if i > 0:
                stack.append(("lit", ","))
    return "".join(out)


# The full Pi RPC command vocabulary (31 types). Every type is accepted; the
# ones pipy has not fully implemented return a well-formed error response (never
# a crash or an unknown-command response).
_KNOWN_COMMANDS = frozenset(
    {
        "prompt",
        "steer",
        "follow_up",
        "abort",
        "new_session",
        "get_state",
        "set_model",
        "cycle_model",
        "get_available_models",
        "set_thinking_level",
        "cycle_thinking_level",
        "set_steering_mode",
        "set_follow_up_mode",
        "compact",
        "set_auto_compaction",
        "set_auto_retry",
        "abort_retry",
        "bash",
        "abort_bash",
        "get_session_stats",
        "export_html",
        "switch_session",
        "fork",
        "clone",
        "get_fork_messages",
        "get_last_assistant_text",
        "set_session_name",
        "get_messages",
        "get_commands",
        "get_entries",
        "get_tree",
    }
)


class _WakeChannel:
    """Blocking wake/EOF transport for the native control bridge.

    This channel owns no prompt content or queue classification.  A wake is an
    empty line; the bridge resolves the matching native reservation separately.
    """

    def __init__(self) -> None:
        self._q: "queue.Queue[bool]" = queue.Queue()
        self._eof = False

    def wake(self) -> None:
        self._q.put(False)

    def signal_eof(self) -> None:
        if not self._eof:
            self._eof = True
            self._q.put(True)

    def readline(self, *_args: Any) -> str:
        return "" if self._q.get() else ""


class _NullEventSink:
    def emit(
        self, event_type: str, *, summary: str, payload: Any | None = None
    ) -> None:
        return None


class NativeRpcServer:
    """Drives one long-lived RPC session over stdin/stdout."""

    def __init__(
        self,
        *,
        adapter: Any,
        cwd: Path,
        native_session: Any,
        stdin: TextIO,
        stdout_buffer: BinaryIO,
        error_stream: TextIO,
    ) -> None:
        self._adapter = adapter
        self._cwd = cwd
        self._tree = native_session
        self._stdin = stdin
        self._writer = JsonlWriter(stdout_buffer)
        self._error = error_stream

        self._channel = _WakeChannel()
        self._bridge = _NativeSessionControlBridge()
        self._bridge.bind_wake_reader(self._channel.readline)
        self._lock = threading.Lock()
        self._steering_mode = "all"
        self._follow_up_mode = "all"
        self._last_assistant_text: str | None = None
        self._configuration: Any | None = None
        self._compaction: Any | None = None
        self._retry: Any | None = None
        self._compact_ids: dict[object, str | None] = {}
        self._bash_operations: dict[object, _BashOperation] = {}
        self._bash_threads: list[threading.Thread] = []
        self._worker: threading.Thread | None = None

    # -- event tap (called from the worker thread) -----------------------
    def emit(self, event: dict[str, Any]) -> None:
        event_type = event.get("type")
        if event_type == "message_end":
            message = event.get("message") or {}
            if message.get("role") == "assistant":
                text = "".join(
                    block.get("text", "")
                    for block in message.get("content", [])
                    if block.get("type") == "text"
                )
                if text:
                    self._last_assistant_text = text
        if event_type != "agent_end":
            # Async session events are fire-and-forget through the single writer.
            self._writer.write_line(event)
            return

        def publish(snapshot: _NativeControlSnapshot) -> None:
            self._writer.write_line(event)
            if snapshot.reservation is not None:
                self._emit_queue_update(snapshot)

        snapshot = self._bridge.consume_attached_agent_end_and_publish(publish)
        if snapshot.reservation is not None:
            self._channel.wake()

    # -- lifecycle -------------------------------------------------------
    def run(self) -> int:
        self._adapter.native_session = self._tree
        self._adapter.automation_observer = self
        self._adapter.abort_event = self._bridge
        self._adapter.input_stream = self._bridge
        import io as _io

        self._adapter.output_stream = _io.StringIO()
        self._adapter.error_stream = self._error

        self._worker = threading.Thread(target=self._run_worker, daemon=True)
        self._worker.start()
        ready = False
        try:
            outcome = self._bridge.wait_ready(120.0)
            if outcome is None:
                raise RuntimeError("native RPC startup timed out")
            if not isinstance(outcome, _NativeControlReady):
                print(
                    f"pipy: rpc startup failed: {type(outcome.error).__name__}",
                    file=self._error,
                )
                return 1
            outcome.readiness_port.bind(self._publish_true_idle)
            self._configuration = outcome.configuration_port
            self._compaction = outcome.compaction_port
            self._retry = outcome.retry_port
            self._bridge.bind_manual_compaction_handler(self._run_manual_compaction)
            ready = True
            self._read_loop()
        finally:
            # Drain the active turn and any queued steer/follow-up BEFORE
            # signalling channel EOF. The channel is FIFO, so signalling EOF
            # while work is still queued/in-flight would let the worker read the
            # EOF sentinel ahead of a reserved queued message and drop it. Batch
            # clients (submit commands, then close stdin) therefore still get
            # their queued steering/follow-up runs delivered.
            if ready:
                self._await_drain(timeout=120.0)
            self._channel.signal_eof()
            if self._worker is not None:
                self._worker.join(timeout=10.0)
            # Join any in-flight bash workers so their responses are written
            # before the process exits — the JSONL request/response contract
            # holds even when stdin closes mid-bash. Bounded by the sandbox's own
            # timeout; a generous cap avoids hanging on a stuck child.
            with self._lock:
                pending_bash = list(self._bash_threads)
            for thread in pending_bash:
                thread.join(timeout=35.0)
        return 0

    def _await_drain(self, *, timeout: float) -> None:
        """Wait until no run is in flight and the queues are empty (bounded).

        Lets a still-active turn settle and its queued steer/follow-up runs
        deliver and complete before EOF, so closing stdin after submitting
        commands does not drop queued work. Bounded so a stuck provider cannot
        hang shutdown — the worker is a daemon thread joined with a timeout.
        """

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                snapshot = self._bridge.snapshot()
                idle = snapshot.reservation is None and snapshot.pending_count == 0
            if idle:
                return
            time.sleep(0.02)

    def _run_worker(self) -> None:
        request = RunRequest(
            agent="pipy-native",
            slug="automation-rpc",
            command=[],
            cwd=self._cwd,
            capture_policy=CapturePolicy(),
        )
        try:
            prepared = self._adapter.prepare(request)
            self._adapter.run(
                prepared, event_sink=_NullEventSink(), capture_policy=CapturePolicy()
            )
        # never crash stdout
        except BaseException as exc:  # noqa: BLE001 - retain the startup primary
            self._bridge.publish_failure_if_unpublished(exc)
            print(f"pipy: rpc worker ended: {type(exc).__name__}", file=self._error)

    def _read_loop(self) -> None:
        buffer = JsonlLineBuffer()
        while True:
            chunk = self._stdin.readline()
            if chunk == "":
                # Flush any buffered partial line, then EOF → graceful shutdown.
                for line in buffer.flush():
                    self._handle_line(line)
                return
            for line in buffer.feed(chunk):
                self._handle_line(line)

    # -- dispatch --------------------------------------------------------
    def _handle_line(self, line: str) -> None:
        if line.strip() == "":
            return
        try:
            command = loads_strict(line)
        except (ValueError, TypeError) as exc:
            self._respond_error(None, "parse", f"Failed to parse command: {exc}")
            return
        if not isinstance(command, dict):
            self._respond_error(None, "parse", "Failed to parse command: not an object")
            return
        ctype = command.get("type")
        if ctype == "extension_ui_response":
            # No pending extension UI request in this build; ignore safely.
            return
        cid = command.get("id")
        # A non-string (and possibly unhashable, e.g. a list) ``type`` must not
        # crash the membership test or the loop — treat it as an unknown command.
        if not isinstance(ctype, str) or ctype not in _KNOWN_COMMANDS:
            # Pi drops the id for unknown commands (rpc-mode.ts:665-668).
            self._respond_error(None, str(ctype), f"Unknown command: {ctype}")
            return
        try:
            self._dispatch(ctype, cid, command)
        except Exception as exc:  # noqa: BLE001 - becomes an error response, never a dead loop
            self._respond_error(cid, str(ctype), f"{type(exc).__name__}: {exc}")

    def _dispatch(self, ctype: str, cid: str | None, command: dict[str, Any]) -> None:
        handler = getattr(self, f"_cmd_{ctype}", None)
        if handler is None:
            self._respond_error(cid, ctype, f"{ctype} is not yet implemented over RPC")
            return
        handler(cid, command)

    # -- response helpers ------------------------------------------------
    def _respond(self, cid: str | None, command: str, data: Any = _OMIT) -> None:
        record: dict[str, Any] = {
            "type": "response",
            "command": command,
            "success": True,
        }
        if cid is not None:
            record = {"id": cid, **record}
        # ``_OMIT`` -> no ``data`` field (command has no payload); an explicit
        # ``None`` -> ``"data": null`` on the wire (e.g. cycle_model when there is
        # nothing to cycle to), matching Pi's `... | null` data contract.
        if data is not _OMIT:
            record["data"] = data
        self._writer.write_line(record)

    def _respond_error(self, cid: str | None, command: str, message: str) -> None:
        record: dict[str, Any] = {
            "type": "response",
            "command": command,
            "success": False,
            "error": message,
        }
        if cid is not None:
            record = {"id": cid, **record}
        self._writer.write_line(record)

    def _emit_queue_update(
        self, snapshot: _NativeControlSnapshot | None = None
    ) -> None:
        if snapshot is None:
            snapshot = self._bridge.snapshot()
        self._writer.write_line(
            {
                "type": "queue_update",
                "steering": [content.value for content in snapshot.steering],
                "followUp": [content.value for content in snapshot.follow_ups],
            }
        )

    @staticmethod
    def _manual_compaction_active(snapshot: _NativeControlSnapshot) -> bool:
        reservation = snapshot.reservation
        return reservation is not None and reservation.manual_compaction is not None

    def _publish_true_idle(self) -> None:
        self._bridge.publish_if_true_idle(
            lambda: self._writer.write_line({"type": "agent_settled"})
        )

    # -- prompting / run control ----------------------------------------
    def _cmd_prompt(self, cid: str | None, command: dict[str, Any]) -> None:
        message = command.get("message")
        if not isinstance(message, str) or not message:
            self._respond_error(cid, "prompt", "prompt requires a non-empty message")
            return
        snapshot = self._bridge.admit_prompt(
            ProductContent(message),
            steer_active=command.get("streamingBehavior") == "steer",
        )
        self._respond(cid, "prompt")
        if snapshot.pending_count and not self._manual_compaction_active(snapshot):
            self._emit_queue_update(snapshot)
        if snapshot.reservation is not None and not snapshot.reservation.claimed:
            self._channel.wake()

    def _cmd_steer(self, cid: str | None, command: dict[str, Any]) -> None:
        message = command.get("message")
        if not isinstance(message, str) or not message:
            self._respond_error(cid, "steer", "steer requires a non-empty message")
            return
        snapshot = self._bridge.admit_steering(ProductContent(message))
        self._respond(cid, "steer")
        if not self._manual_compaction_active(snapshot):
            self._emit_queue_update(snapshot)
        if snapshot.reservation is not None and not snapshot.reservation.claimed:
            self._channel.wake()

    def _cmd_follow_up(self, cid: str | None, command: dict[str, Any]) -> None:
        message = command.get("message")
        if not isinstance(message, str) or not message:
            self._respond_error(
                cid, "follow_up", "follow_up requires a non-empty message"
            )
            return
        snapshot = self._bridge.admit_follow_up(ProductContent(message))
        self._respond(cid, "follow_up")
        if not self._manual_compaction_active(snapshot):
            self._emit_queue_update(snapshot)
        if snapshot.reservation is not None and not snapshot.reservation.claimed:
            self._channel.wake()

    def _cmd_abort(self, cid: str | None, command: dict[str, Any]) -> None:
        before = self._bridge.snapshot()
        snapshot = self._bridge.request_abort()
        self._respond(cid, "abort")
        if snapshot.steering != before.steering:
            self._emit_queue_update(snapshot)

    def _cmd_abort_bash(self, cid: str | None, command: dict[str, Any]) -> None:
        # Mark and snapshot while the operation registry is stable. Signalling
        # happens after releasing the RPC lock: an Event callback must never be
        # able to block command correlation or terminal fixation.
        with self._lock:
            events = []
            for operation in self._bash_operations.values():
                operation.abort_requested = True
                events.append(operation.cancel_event)
        for event in events:
            event.set()
        self._respond(cid, "abort_bash")

    def _cmd_abort_retry(self, cid: str | None, command: dict[str, Any]) -> None:
        self._retry_port().abort_retry()
        self._respond(cid, "abort_retry")

    # -- queue modes -----------------------------------------------------
    def _cmd_set_steering_mode(self, cid: str | None, command: dict[str, Any]) -> None:
        mode = command.get("mode")
        if mode not in ("all", "one-at-a-time"):
            self._respond_error(cid, "set_steering_mode", "invalid steering mode")
            return
        self._steering_mode = mode
        self._respond(cid, "set_steering_mode")

    def _cmd_set_follow_up_mode(self, cid: str | None, command: dict[str, Any]) -> None:
        mode = command.get("mode")
        if mode not in ("all", "one-at-a-time"):
            self._respond_error(cid, "set_follow_up_mode", "invalid follow-up mode")
            return
        self._follow_up_mode = mode
        self._respond(cid, "set_follow_up_mode")

    def _cmd_set_auto_compaction(
        self, cid: str | None, command: dict[str, Any]
    ) -> None:
        enabled = command.get("enabled")
        if type(enabled) is not bool:
            self._respond_error(cid, "set_auto_compaction", "enabled must be a boolean")
            return
        try:
            port = self._compaction_port()
            if not port.set_auto_compaction_enabled(enabled):
                self._respond_error(
                    cid,
                    "set_auto_compaction",
                    "effective compaction policy is overridden",
                )
                return
        except Exception:  # noqa: BLE001 - settings details can contain paths
            self._respond_error(
                cid, "set_auto_compaction", "could not update compaction policy"
            )
            return
        self._respond(cid, "set_auto_compaction")

    def _compaction_port(self) -> Any:
        if self._compaction is None:
            raise RuntimeError("native RPC compaction port is not ready")
        return self._compaction

    @staticmethod
    def _compaction_result(result: Any) -> dict[str, Any]:
        return {
            "summary": result.summary,
            "firstKeptEntryId": result.first_kept_entry_id,
            "tokensBefore": result.tokens_before,
            "details": {
                "droppedGroupCount": result.dropped_group_count,
                "droppedMessageCount": result.dropped_message_count,
            },
        }

    def _cmd_compact(self, cid: str | None, command: dict[str, Any]) -> None:
        raw = command.get("customInstructions")
        if raw is not None and type(raw) is not str:
            self._respond_error(cid, "compact", "customInstructions must be a string")
            return
        custom = None if raw is None else ProductContent(raw)
        snapshot = self._bridge.admit_manual_compaction(custom)
        if snapshot is None or snapshot.reservation is None:
            self._respond_error(cid, "compact", "session is not idle")
            return
        with self._lock:
            self._compact_ids[snapshot.reservation.token] = cid
        self._channel.wake()

    def _run_manual_compaction(  # noqa: C901 - exact terminal projection matrix
        self, claim: Any, custom: ProductContent | None
    ) -> Any:
        """Run on the native worker; return its under-gate publication callback."""

        with self._lock:
            cid = self._compact_ids.pop(claim.token, _MISSING_COMPACTION_CORRELATION)
        if cid is _MISSING_COMPACTION_CORRELATION:
            raise RuntimeError("native manual compaction correlation is missing")
        self._writer.write_line({"type": "compaction_start", "reason": "manual"})
        if claim.is_aborted:
            # The latch belongs to this accepted reservation.  Do not enter
            # semantic hooks or construct a private provider request when the
            # operator won the race before the worker claimed it.
            from pipy_harness.native.coding.compaction import CodingCompactionOutcome

            outcome = CodingCompactionOutcome(
                "pipy: compaction cancelled.", AgentCancellationReason.OPERATOR_ABORT
            )
        else:
            try:
                outcome = self._compaction_port().compact(custom)
            except Exception:  # noqa: BLE001 - terminal command still propagates; RPC projects it
                outcome = None

        def publish(snapshot: Any) -> None:
            if outcome is None:
                self._writer.write_line(
                    {
                        "type": "compaction_end",
                        "reason": "manual",
                        "result": None,
                        "aborted": False,
                        "willRetry": False,
                        "errorMessage": "Compaction failed",
                    }
                )
                self._respond_error(cid, "compact", "compaction failed")
            elif outcome.cancellation_reason is not None:
                self._writer.write_line(
                    {
                        "type": "compaction_end",
                        "reason": "manual",
                        "result": None,
                        "aborted": True,
                        "willRetry": False,
                    }
                )
                self._respond_error(cid, "compact", "compaction cancelled")
            elif outcome.result is None:
                self._writer.write_line(
                    {
                        "type": "compaction_end",
                        "reason": "manual",
                        "result": None,
                        "aborted": False,
                        "willRetry": False,
                        "errorMessage": "Compaction refused",
                    }
                )
                self._respond_error(cid, "compact", "compaction refused")
            else:
                result = self._compaction_result(outcome.result)
                end: dict[str, Any] = {
                    "type": "compaction_end",
                    "reason": "manual",
                    "result": result,
                    "aborted": False,
                    "willRetry": False,
                }
                if outcome.persistence_failed:
                    end["errorMessage"] = (
                        "Compaction accepted but durable persistence failed"
                    )
                self._writer.write_line(end)
                if outcome.persistence_failed:
                    self._respond_error(cid, "compact", "compaction persistence failed")
                else:
                    self._respond(cid, "compact", result)
            if snapshot.reservation is not None:
                self._emit_queue_update(snapshot)

        return publish

    def _cmd_set_auto_retry(self, cid: str | None, command: dict[str, Any]) -> None:
        enabled = command.get("enabled")
        if type(enabled) is not bool:
            self._respond_error(
                cid,
                "set_auto_retry",
                "set_auto_retry requires an exact boolean enabled",
            )
            return
        try:
            if not self._retry_port().set_enabled(enabled):
                self._respond_error(
                    cid,
                    "set_auto_retry",
                    "retry.enabled is overridden and cannot be changed by RPC",
                )
                return
        except Exception:  # noqa: BLE001 - settings details can contain paths
            self._respond_error(cid, "set_auto_retry", "could not update retry policy")
            return
        self._respond(cid, "set_auto_retry")

    def _retry_port(self) -> Any:
        port = self._retry
        if port is None:
            raise RuntimeError("native RPC retry port is not ready")
        return port

    def _configuration_port(self) -> Any:
        port = self._configuration
        if port is None:
            raise RuntimeError("native RPC configuration port is not ready")
        return port

    @staticmethod
    def _model(selection: Any) -> dict[str, str]:
        return {"provider": selection.provider_name, "id": selection.model_id}

    def _configuration_error(self, cid: str | None, command: str, result: Any) -> None:
        self._respond_error(cid, command, result.diagnostic or "configuration refused")

    def _cmd_set_thinking_level(self, cid: str | None, command: dict[str, Any]) -> None:
        level = command.get("level")
        if not isinstance(level, str):
            self._respond_error(
                cid, "set_thinking_level", f"unknown thinking level: {level}"
            )
            return
        result = self._configuration_port().set_thinking_level(level)
        if not result.success:
            self._configuration_error(cid, "set_thinking_level", result)
            return
        self._respond(cid, "set_thinking_level")
        if result.thinking_changed:
            assert result.snapshot is not None
            self._writer.write_line(
                {
                    "type": "thinking_level_changed",
                    "level": result.snapshot.thinking_level,
                }
            )

    def _cmd_cycle_thinking_level(
        self, cid: str | None, command: dict[str, Any]
    ) -> None:
        result = self._configuration_port().cycle_thinking_level()
        if result is None:
            self._respond(cid, "cycle_thinking_level", None)
            return
        if not result.success:
            self._configuration_error(cid, "cycle_thinking_level", result)
            return
        assert result.snapshot is not None
        self._respond(
            cid, "cycle_thinking_level", {"level": result.snapshot.thinking_level}
        )
        if result.thinking_changed:
            self._writer.write_line(
                {
                    "type": "thinking_level_changed",
                    "level": result.snapshot.thinking_level,
                }
            )

    def _cmd_set_model(self, cid: str | None, command: dict[str, Any]) -> None:
        provider = command.get("provider")
        model_id = command.get("modelId")
        if not isinstance(provider, str) or not isinstance(model_id, str):
            self._respond_error(cid, "set_model", "unknown provider/model")
            return
        from pipy_harness.native.repl_state import NativeModelSelection

        result = self._configuration_port().set_model(
            NativeModelSelection(provider, model_id)
        )
        if not result.success:
            self._configuration_error(cid, "set_model", result)
            return
        assert result.snapshot is not None
        self._respond(cid, "set_model", self._model(result.snapshot.selection))
        if result.thinking_changed:
            self._writer.write_line(
                {
                    "type": "thinking_level_changed",
                    "level": result.snapshot.thinking_level,
                }
            )

    def _cmd_cycle_model(self, cid: str | None, command: dict[str, Any]) -> None:
        cycle = self._configuration_port().cycle_model()
        if cycle is None:
            self._respond(cid, "cycle_model", None)
            return
        result = cycle.result
        if not result.success:
            self._configuration_error(cid, "cycle_model", result)
            return
        assert result.snapshot is not None
        self._respond(
            cid,
            "cycle_model",
            {
                "model": self._model(result.snapshot.selection),
                "thinkingLevel": result.snapshot.thinking_level,
                "isScoped": cycle.is_scoped,
            },
        )
        if result.thinking_changed:
            self._writer.write_line(
                {
                    "type": "thinking_level_changed",
                    "level": result.snapshot.thinking_level,
                }
            )

    # -- introspection ---------------------------------------------------
    def _messages(self) -> list[Any]:
        try:
            return list(self._tree.build_context().messages)
        except Exception:  # noqa: BLE001 - an unreadable tree reports no messages
            return []

    def _cmd_get_state(self, cid: str | None, command: dict[str, Any]) -> None:
        configuration = self._configuration_port().snapshot()
        messages = self._messages()
        snapshot = self._bridge.snapshot()
        # A private compaction owns the native active slot so that it has the
        # same admission and cancellation semantics as a run, but it is not an
        # agent stream.  Keep that distinction visible to RPC consumers.
        streaming = (
            snapshot.reservation is not None
            and snapshot.reservation.manual_compaction is None
        )
        pending = snapshot.pending_count
        steering_mode = self._steering_mode
        follow_up_mode = self._follow_up_mode
        tree_path = getattr(self._tree, "path", None)
        self._respond(
            cid,
            "get_state",
            {
                "model": self._model(configuration.selection),
                "thinkingLevel": configuration.thinking_level,
                "isStreaming": streaming,
                "isCompacting": self._compaction_port().is_compacting()
                or bool(
                    snapshot.reservation is not None
                    and snapshot.reservation.manual_compaction is not None
                ),
                "steeringMode": steering_mode,
                "followUpMode": follow_up_mode,
                "sessionFile": str(tree_path) if tree_path else None,
                "sessionId": self._tree.session_id,
                "sessionName": self._tree.name,
                "autoCompactionEnabled": self._compaction_port().auto_compaction_enabled(),
                "messageCount": len(messages),
                "pendingMessageCount": pending,
            },
        )

    def _cmd_get_messages(self, cid: str | None, command: dict[str, Any]) -> None:
        messages = [serialize_message(m) for m in self._messages()]
        self._respond(cid, "get_messages", {"messages": messages})

    def _cmd_get_session_stats(self, cid: str | None, command: dict[str, Any]) -> None:
        messages = self._messages()
        serialized = [serialize_message(m) for m in messages]
        user = sum(1 for m in serialized if m["role"] == "user")
        assistant = sum(1 for m in serialized if m["role"] == "assistant")
        tool_results = sum(1 for m in serialized if m["role"] == "toolResult")
        tool_calls = sum(
            1
            for m in serialized
            if m["role"] == "assistant"
            for block in m["content"]
            if block.get("type") == "toolCall"
        )
        tree_path = getattr(self._tree, "path", None)
        self._respond(
            cid,
            "get_session_stats",
            {
                "sessionFile": str(tree_path) if tree_path else None,
                "sessionId": self._tree.session_id,
                "userMessages": user,
                "assistantMessages": assistant,
                "toolCalls": tool_calls,
                "toolResults": tool_results,
                "totalMessages": len(serialized),
                "tokens": {
                    "input": 0,
                    "output": 0,
                    "cacheRead": 0,
                    "cacheWrite": 0,
                    "total": 0,
                },
                "cost": 0,
            },
        )

    def _cmd_get_last_assistant_text(
        self, cid: str | None, command: dict[str, Any]
    ) -> None:
        # Source of truth is the native session tree, so a resumed/preloaded
        # session returns its last assistant text even with no live cache yet;
        # fall back to the live-event cache otherwise.
        text: str | None = None
        from pipy_harness.native.agent import AgentAssistantMessage

        for message in reversed(self._messages()):
            if isinstance(message, AgentAssistantMessage) and message.content.value:
                text = message.content.value
                break
        if text is None:
            text = self._last_assistant_text
        self._respond(cid, "get_last_assistant_text", {"text": text})

    def _cmd_get_fork_messages(self, cid: str | None, command: dict[str, Any]) -> None:
        from pipy_harness.native.agent import AgentUserMessage

        entries = []
        try:
            for entry in self._tree.get_branch():
                message = getattr(entry, "message", None)
                if isinstance(message, AgentUserMessage):
                    entries.append({"entryId": entry.id, "text": message.content.value})
        except Exception:  # noqa: BLE001 - an unreadable branch reports no fork points
            entries = []
        self._respond(cid, "get_fork_messages", {"messages": entries})

    def _cmd_get_entries(self, cid: str | None, command: dict[str, Any]) -> None:
        from pipy_harness.native.session_tree import _entry_to_json

        # Coherent (entries, leaf) pair under the tree write lock: leaf is always
        # present in entries and entries are never ahead of it (Pi atomic read).
        entries, leaf = self._tree.snapshot_entries_and_leaf()
        # Gate on key presence to mirror Pi's `command.since !== undefined`: an
        # explicit `null` is present (errors as not-found), an absent key is not.
        if "since" in command:
            since = command.get("since")
            index = next(
                (i for i, entry in enumerate(entries) if entry.id == since), -1
            )
            if index == -1:
                self._respond_error(
                    cid, "get_entries", f"Entry not found: {_js_since(since)}"
                )
                return
            entries = entries[index + 1 :]
        self._respond(
            cid,
            "get_entries",
            {"entries": [_entry_to_json(entry) for entry in entries], "leafId": leaf},
        )

    def _cmd_get_tree(self, cid: str | None, command: dict[str, Any]) -> None:
        from pipy_harness.native.session_tree import build_tree_nodes

        entries, leaf = self._tree.snapshot_entries_and_leaf()
        roots = build_tree_nodes(entries)
        # get_tree is the only deeply-nested RPC payload (a linear history nests
        # one level per entry). Encode the tree iteratively and emit the line
        # raw so a deep session cannot RecursionError in json.dumps.
        tree_json = _encode_session_tree(roots)
        data_str = '{"tree":' + tree_json + ',"leafId":' + _dumps(leaf) + "}"
        head: dict[str, Any] = {
            "type": "response",
            "command": "get_tree",
            "success": True,
        }
        if cid is not None:
            head = {"id": cid, **head}
        # Splice the pre-encoded (possibly very deep) data into the shallow
        # envelope without re-encoding it recursively.
        line = _dumps(head)[:-1] + ',"data":' + data_str + "}"
        self._writer.write_raw_line(line)

    def _cmd_get_commands(self, cid: str | None, command: dict[str, Any]) -> None:
        self._respond(cid, "get_commands", {"commands": []})

    def _cmd_get_available_models(
        self, cid: str | None, command: dict[str, Any]
    ) -> None:
        self._respond(
            cid,
            "get_available_models",
            {
                "models": [
                    self._model(model)
                    for model in self._configuration_port().available_models()
                ]
            },
        )

    def _cmd_set_session_name(self, cid: str | None, command: dict[str, Any]) -> None:
        name = command.get("name")
        if not isinstance(name, str) or not name.strip():
            self._respond_error(
                cid, "set_session_name", "session name must be non-empty"
            )
            return
        try:
            self._tree.append_session_info(name.strip())
        except Exception as exc:  # noqa: BLE001 - reported as a command error response
            self._respond_error(cid, "set_session_name", f"{type(exc).__name__}: {exc}")
            return
        self._respond(cid, "set_session_name")
        self._writer.write_line({"type": "session_info_changed", "name": name.strip()})

    # -- bash ------------------------------------------------------------
    def _cmd_bash(  # noqa: C901 - exact-operation terminal projection branches
        self, cid: str | None, command: dict[str, Any]
    ) -> None:
        cmd = command.get("command")
        if not isinstance(cmd, str) or not cmd:
            self._respond_error(cid, "bash", "bash requires a non-empty command")
            return
        # Each accepted command receives a fresh identity/event pair before its
        # worker can start. A late abort can therefore neither reclassify a
        # fixed result nor spill into a later command.
        identity = object()
        relay = _BashUpdateRelay()
        operation = _BashOperation(cancel_event=threading.Event(), relay=relay)

        def _run_bash() -> None:
            result = None
            error: Exception | None = None

            def emit_updates() -> None:
                while (delta := relay.take()) is not None:
                    update: dict[str, Any] = {
                        "type": "bash_execution_update",
                        "delta": delta,
                    }
                    if cid is not None:
                        update = {"id": cid, **update}
                    self._writer.write_line(update)

            emitter = threading.Thread(
                target=emit_updates,
                name="pipy-rpc-bash-output",
                daemon=True,
            )
            emitter.start()
            try:
                policy = CommandPolicy(workspace_root=self._cwd)
                result = run_command(
                    cmd,
                    policy,
                    cancel_event=operation.cancel_event,
                    output_callback=relay.admit,
                )
            except Exception as exc:  # noqa: BLE001 - becomes one RPC error response
                error = exc
            finally:
                # The sandbox has completed all drain/reap work.  Closing
                # rejects any late callback before the terminal fixation; the
                # join guarantees every accepted update precedes that response.
                relay.close()
                emitter.join()

            # This is the sole terminal fixation point. An abort mark observed
            # here wins over a concurrently settled sandbox outcome; retirement
            # before an abort snapshot prevents later reclassification.
            with self._lock:
                abort_requested = operation.abort_requested
                self._bash_operations.pop(identity, None)

            if abort_requested:
                output = ""
                truncated = False
                if result is not None:
                    output = result.stdout + result.stderr
                    truncated = result.truncated
                self._respond(
                    cid,
                    "bash",
                    {
                        "output": output,
                        "exitCode": None,
                        "cancelled": True,
                        "truncated": truncated,
                    },
                )
                return
            if error is not None:
                self._respond_error(cid, "bash", f"{type(error).__name__}: {error}")
                return
            assert result is not None
            output = result.stdout
            if result.stderr:
                output = output + result.stderr
            self._respond(
                cid,
                "bash",
                {
                    "output": output,
                    "exitCode": result.exit_code,
                    "cancelled": result.status is CommandStatus.CANCELLED,
                    "truncated": result.truncated,
                },
            )

        thread = threading.Thread(target=_run_bash, name="pipy-rpc-bash", daemon=True)
        with self._lock:
            self._bash_operations[identity] = operation
            # Prune finished workers, then track this one so EOF shutdown can join
            # it and guarantee its response is written before the process exits.
            self._bash_threads = [t for t in self._bash_threads if t.is_alive()]
            self._bash_threads.append(thread)
        thread.start()


def run_rpc_mode(
    *,
    adapter: Any,
    cwd: Path,
    native_session: Any,
    stdin: TextIO,
    stdout_buffer: BinaryIO,
    error_stream: TextIO,
) -> int:
    """Entry point for ``pipy repl --mode rpc``."""

    server = NativeRpcServer(
        adapter=adapter,
        cwd=cwd,
        native_session=native_session,
        stdin=stdin,
        stdout_buffer=stdout_buffer,
        error_stream=error_stream,
    )
    return server.run()
