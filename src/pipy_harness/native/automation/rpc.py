"""Pi-compatible `--mode rpc` stdin/stdout JSONL protocol (`docs/automation-rpc.md`).

A long-lived headless process: it reads one JSON command per LF-delimited line
on stdin and writes JSON responses plus asynchronous session events on stdout,
mirroring Pi's ``runRpcMode`` (`packages/coding-agent/src/modes/rpc/rpc-mode.ts`).

Design (stdlib only, composes — does not fork — the runtime):

- The same ``NativeToolReplSession.run`` loop the CLI/TUI use runs on a worker
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

import queue
import threading
import time
from pathlib import Path
from typing import Any, BinaryIO, TextIO

from pipy_harness.capture import CapturePolicy
from pipy_harness.models import RunRequest
from pipy_harness.native.automation.jsonl import (
    JsonlLineBuffer,
    JsonlWriter,
    loads_strict,
)
from pipy_harness.native.automation.serialize import serialize_message
from pipy_harness.native.command_sandbox import (
    CommandPolicy,
    CommandStatus,
    run_command,
)

# Sentinel distinguishing "omit the response data field" from an explicit
# ``data: null`` (Pi's `... | null` data contract, e.g. cycle_model).
_OMIT: Any = object()

# The full Pi RPC command vocabulary (29 types). Every type is accepted; the
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
    }
)


class _PromptChannel:
    """Blocking LF line stream feeding prompts to the worker ``run`` loop.

    ``readline`` blocks until a prompt is pushed or EOF is signalled (returns
    ``""`` so the loop terminates). It quacks like a text stream so the non-TTY
    REPL input reads it via ``readline``.
    """

    def __init__(self) -> None:
        self._q: "queue.Queue[str | None]" = queue.Queue()
        self._eof = False

    def push(self, text: str) -> None:
        self._q.put(text if text.endswith("\n") else text + "\n")

    def signal_eof(self) -> None:
        if not self._eof:
            self._eof = True
            self._q.put(None)

    def readline(self, *_args: Any) -> str:
        item = self._q.get()
        if item is None:
            self._q.put(None)  # re-arm EOF for any later readline
            return ""
        return item

    def read(self, *_args: Any) -> str:
        return self.readline()

    def isatty(self) -> bool:
        return False

    def readable(self) -> bool:
        return True

    def writable(self) -> bool:
        return False

    def seekable(self) -> bool:
        return False

    def close(self) -> None:
        return None

    @property
    def closed(self) -> bool:
        return False

    def fileno(self) -> int:
        raise OSError("prompt channel has no fileno")


class _NullEventSink:
    def emit(self, event_type: str, *, summary: str, payload: Any | None = None) -> None:
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

        self._channel = _PromptChannel()
        self._abort = threading.Event()
        self._lock = threading.Lock()
        self._turn_active = False
        self._steering_mode = "all"
        self._follow_up_mode = "all"
        self._steering: list[str] = []
        self._follow_up: list[str] = []
        self._last_assistant_text: str | None = None
        self._auto_compaction = True
        self._auto_retry = True
        self._thinking_level = "off"
        self._bash_in_flight = 0
        self._bash_threads: list[threading.Thread] = []
        self._worker: threading.Thread | None = None

    _THINKING_LEVELS = ("off", "minimal", "low", "medium", "high", "xhigh")

    # -- event tap (called from the worker thread) -----------------------
    def emit(self, event: dict[str, Any]) -> None:
        event_type = event.get("type")
        if event_type == "agent_start":
            with self._lock:
                self._turn_active = True
        elif event_type == "message_end":
            message = event.get("message") or {}
            if message.get("role") == "assistant":
                text = "".join(
                    block.get("text", "")
                    for block in message.get("content", [])
                    if block.get("type") == "text"
                )
                if text:
                    self._last_assistant_text = text
        # On the run boundary, settle state and reserve the next queued message
        # BEFORE the agent_end line hits the wire, so a client that observes
        # agent_end and immediately calls get_state sees the settled boundary
        # (isStreaming false when idle, or true when the next queued run is
        # already reserved) — never a stale in-flight state. The actual delivery
        # (queue_update + channel push) happens AFTER agent_end so agent_end stays
        # the clean boundary that precedes the next run's events.
        reserved: str | None = None
        if event_type == "agent_end":
            self._abort.clear()
            reserved = self._reserve_next_message(settled=True)
        # Async session events are fire-and-forget through the single writer.
        self._writer.write_line(event)
        if reserved is not None:
            self._deliver(reserved)

    def _reserve_next_message(self, *, settled: bool) -> str | None:
        """Atomically settle the run (if any) and reserve the next queued message.

        pipy promotes a queued ``steer``/``follow_up`` message to run after the
        current run settles, **one message per turn boundary, steering first**
        (follow-up only once steering is empty). Settling (``_turn_active`` ->
        False on ``settled=True``) and reserving the next message (pop one +
        ``_turn_active`` -> True) happen under a single lock, so there is never a
        window where the run is marked idle while messages are still queued — a
        `prompt` racing the boundary either sees the active/reserved run (and is
        routed to the queue) or the post-reservation state, never jumps ahead of
        queued steering. The queues stay the single truthful source for
        ``pendingMessageCount``/``queue_update``; nothing is bulk-pushed behind
        the queue's back, and ``abort`` can still discard steering that has not
        been started yet. ``steeringMode``/``followUpMode`` are accepted and
        reported in state, but delivery is uniformly one-per-boundary (a
        documented simplification of Pi's in-turn injection).

        Returns the reserved message (caller then calls ``_deliver``) or ``None``.
        ``settled=True`` is the just-ended run's ``agent_end`` boundary;
        ``settled=False`` is an enqueue while no run is in flight.
        """

        with self._lock:
            if settled:
                self._turn_active = False
            if self._turn_active:
                return None
            if self._steering:
                message = self._steering.pop(0)
            elif self._follow_up:
                message = self._follow_up.pop(0)
            else:
                return None
            # The reserved run is active from this moment (accept time), not only
            # once the worker later emits agent_start.
            self._turn_active = True
            return message

    def _deliver(self, message: str) -> None:
        self._emit_queue_update()
        self._channel.push(message)

    # -- lifecycle -------------------------------------------------------
    def run(self) -> int:
        self._adapter.native_session = self._tree
        self._adapter.automation_observer = self
        self._adapter.abort_event = self._abort
        self._adapter.input_stream = self._channel
        import io as _io

        self._adapter.output_stream = _io.StringIO()
        self._adapter.error_stream = self._error

        self._worker = threading.Thread(target=self._run_worker, daemon=True)
        self._worker.start()
        try:
            self._read_loop()
        finally:
            # Drain the active turn and any queued steer/follow-up BEFORE
            # signalling channel EOF. The channel is FIFO, so signalling EOF
            # while work is still queued/in-flight would let the worker read the
            # EOF sentinel ahead of a reserved queued message and drop it. Batch
            # clients (submit commands, then close stdin) therefore still get
            # their queued steering/follow-up runs delivered.
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
                idle = (
                    not self._turn_active
                    and not self._steering
                    and not self._follow_up
                )
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
        prepared = self._adapter.prepare(request)
        try:
            self._adapter.run(
                prepared, event_sink=_NullEventSink(), capture_policy=CapturePolicy()
            )
        except Exception as exc:  # pragma: no cover - surface, never crash stdout
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
        except Exception as exc:  # never crash the loop
            self._respond_error(cid, str(ctype), f"{type(exc).__name__}: {exc}")

    def _dispatch(self, ctype: str, cid: str | None, command: dict[str, Any]) -> None:
        handler = getattr(self, f"_cmd_{ctype}", None)
        if handler is None:
            self._respond_error(
                cid, ctype, f"{ctype} is not yet implemented over RPC"
            )
            return
        handler(cid, command)

    # -- response helpers ------------------------------------------------
    def _respond(
        self, cid: str | None, command: str, data: Any = _OMIT
    ) -> None:
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

    def _emit_queue_update(self) -> None:
        with self._lock:
            steering = list(self._steering)
            follow_up = list(self._follow_up)
        self._writer.write_line(
            {"type": "queue_update", "steering": steering, "followUp": follow_up}
        )

    # -- prompting / run control ----------------------------------------
    def _cmd_prompt(self, cid: str | None, command: dict[str, Any]) -> None:
        message = command.get("message")
        if not isinstance(message, str) or not message:
            self._respond_error(cid, "prompt", "prompt requires a non-empty message")
            return
        behavior = command.get("streamingBehavior")
        with self._lock:
            active = self._turn_active
        if active:
            # A prompt sent during an active run is routed through the observable
            # queue (Pi's streamingBehavior: steer -> steering, otherwise
            # follow-up) rather than silently deferred: it shows up in
            # queue_update / pendingMessageCount and drains after the current run
            # settles. Without this, a mid-run prompt would be an invisible
            # queued turn.
            with self._lock:
                if behavior == "steer":
                    self._steering.append(message)
                else:
                    self._follow_up.append(message)
            self._respond(cid, "prompt")
            self._emit_queue_update()
            return
        # Idle: preflight succeeded; emit the authoritative success, then events.
        # Mark the run active synchronously (accept time) so an immediately
        # following abort/steer/follow_up/get_state sees the in-flight run rather
        # than racing the worker's later agent_start.
        with self._lock:
            self._turn_active = True
        self._respond(cid, "prompt")
        self._channel.push(message)

    def _cmd_steer(self, cid: str | None, command: dict[str, Any]) -> None:
        message = command.get("message")
        if not isinstance(message, str) or not message:
            self._respond_error(cid, "steer", "steer requires a non-empty message")
            return
        with self._lock:
            self._steering.append(message)
            active = self._turn_active
        self._respond(cid, "steer")
        self._emit_queue_update()
        if not active:
            # No run in flight: deliver immediately rather than leaving it queued.
            reserved = self._reserve_next_message(settled=False)
            if reserved is not None:
                self._deliver(reserved)

    def _cmd_follow_up(self, cid: str | None, command: dict[str, Any]) -> None:
        message = command.get("message")
        if not isinstance(message, str) or not message:
            self._respond_error(cid, "follow_up", "follow_up requires a non-empty message")
            return
        with self._lock:
            self._follow_up.append(message)
            active = self._turn_active
        self._respond(cid, "follow_up")
        self._emit_queue_update()
        if not active:
            reserved = self._reserve_next_message(settled=False)
            if reserved is not None:
                self._deliver(reserved)

    def _cmd_abort(self, cid: str | None, command: dict[str, Any]) -> None:
        # Only signal an abort when a turn is actually in flight. The abort event
        # is cleared on agent_end, so setting it while idle would poison the next
        # prompt (its turn would cancel immediately). Idle abort is a no-op.
        # Queued steering targeted the run being aborted, so it is discarded
        # (follow-ups, which are meant to run after, are kept); the change is
        # observable via queue_update.
        with self._lock:
            active = self._turn_active
            had_steering = bool(self._steering)
            self._steering = []
        if active:
            self._abort.set()
        self._respond(cid, "abort")
        if had_steering:
            self._emit_queue_update()

    def _cmd_abort_bash(self, cid: str | None, command: dict[str, Any]) -> None:
        # RPC `bash` runs on a worker thread through the bounded, secret-scrubbing
        # sandbox, which is not externally cancellable (it completes or hits its
        # timeout). If a bash is in flight we surface a well-formed error rather
        # than falsely claiming a cancellation that did not happen; otherwise
        # there is nothing to abort (a valid no-op success).
        with self._lock:
            running = self._bash_in_flight > 0
        if running:
            self._respond_error(
                cid,
                "abort_bash",
                "a running sandboxed bash command is not externally cancellable; "
                "it completes or hits its timeout",
            )
        else:
            self._respond(cid, "abort_bash")

    def _cmd_abort_retry(self, cid: str | None, command: dict[str, Any]) -> None:
        # No auto-retry loop runs in this transport, so there is nothing to abort
        # — a valid no-op success (Pi parity).
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

    def _cmd_set_auto_compaction(self, cid: str | None, command: dict[str, Any]) -> None:
        self._auto_compaction = bool(command.get("enabled"))
        self._respond(cid, "set_auto_compaction")

    def _cmd_set_auto_retry(self, cid: str | None, command: dict[str, Any]) -> None:
        self._auto_retry = bool(command.get("enabled"))
        self._respond(cid, "set_auto_retry")

    def _cmd_set_thinking_level(self, cid: str | None, command: dict[str, Any]) -> None:
        level = command.get("level")
        if level not in self._THINKING_LEVELS:
            self._respond_error(
                cid, "set_thinking_level", f"unknown thinking level: {level}"
            )
            return
        # The requested level is recorded and surfaced in get_state.thinkingLevel
        # plus a thinking_level_changed event, but it is not yet threaded into the
        # running session's provider requests — a documented follow-on
        # (docs/automation-rpc.md "Model / thinking controls").
        self._thinking_level = level
        self._respond(cid, "set_thinking_level")
        self._writer.write_line({"type": "thinking_level_changed", "level": level})

    def _cmd_cycle_thinking_level(self, cid: str | None, command: dict[str, Any]) -> None:
        index = self._THINKING_LEVELS.index(self._thinking_level)
        level = self._THINKING_LEVELS[(index + 1) % len(self._THINKING_LEVELS)]
        self._thinking_level = level
        self._respond(cid, "cycle_thinking_level", {"level": level})
        self._writer.write_line({"type": "thinking_level_changed", "level": level})

    def _cmd_set_model(self, cid: str | None, command: dict[str, Any]) -> None:
        provider, model_id = self._selection()
        if command.get("provider") == provider and command.get("modelId") == model_id:
            self._respond(cid, "set_model", {"provider": provider, "id": model_id})
            return
        # Single-provider automation build: any other provider/model is unknown.
        self._respond_error(
            cid,
            "set_model",
            f"unknown provider/model: {command.get('provider')}/{command.get('modelId')}",
        )

    def _cmd_cycle_model(self, cid: str | None, command: dict[str, Any]) -> None:
        # Single configured model in this build: nothing to cycle to (Pi returns
        # null `data` for "no other model"), not a misleading success payload.
        self._respond(cid, "cycle_model", None)

    # -- introspection ---------------------------------------------------
    def _selection(self) -> tuple[str, str]:
        try:
            sel = self._adapter._current_selection()
            return sel.provider_name, sel.model_id
        except Exception:
            return "fake", "fake-tools"

    def _messages(self) -> list[Any]:
        try:
            return list(self._tree.build_context().messages)
        except Exception:
            return []

    def _cmd_get_state(self, cid: str | None, command: dict[str, Any]) -> None:
        provider, model_id = self._selection()
        messages = self._messages()
        with self._lock:
            streaming = self._turn_active
            pending = len(self._steering) + len(self._follow_up)
            steering_mode = self._steering_mode
            follow_up_mode = self._follow_up_mode
        tree_path = getattr(self._tree, "path", None)
        self._respond(
            cid,
            "get_state",
            {
                "model": {"provider": provider, "id": model_id},
                "thinkingLevel": self._thinking_level,
                "isStreaming": streaming,
                "isCompacting": False,
                "steeringMode": steering_mode,
                "followUpMode": follow_up_mode,
                "sessionFile": str(tree_path) if tree_path else None,
                "sessionId": self._tree.session_id,
                "sessionName": self._tree.name,
                "autoCompactionEnabled": self._auto_compaction,
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

    def _cmd_get_last_assistant_text(self, cid: str | None, command: dict[str, Any]) -> None:
        # Source of truth is the native session tree, so a resumed/preloaded
        # session returns its last assistant text even with no live cache yet;
        # fall back to the live-event cache otherwise.
        text: str | None = None
        from pipy_harness.native.tools.messages import AssistantMessage

        for message in reversed(self._messages()):
            if isinstance(message, AssistantMessage) and message.content:
                text = message.content
                break
        if text is None:
            text = self._last_assistant_text
        self._respond(cid, "get_last_assistant_text", {"text": text})

    def _cmd_get_fork_messages(self, cid: str | None, command: dict[str, Any]) -> None:
        entries = []
        try:
            for entry in self._tree.get_branch():
                message = getattr(entry, "message", None)
                if message is not None and getattr(message, "content", None) is not None and type(message).__name__ == "UserMessage":
                    entries.append({"entryId": entry.id, "text": message.content})
        except Exception:
            entries = []
        self._respond(cid, "get_fork_messages", {"messages": entries})

    def _cmd_get_commands(self, cid: str | None, command: dict[str, Any]) -> None:
        self._respond(cid, "get_commands", {"commands": []})

    def _cmd_get_available_models(self, cid: str | None, command: dict[str, Any]) -> None:
        provider, model_id = self._selection()
        self._respond(
            cid,
            "get_available_models",
            {"models": [{"provider": provider, "id": model_id}]},
        )

    def _cmd_set_session_name(self, cid: str | None, command: dict[str, Any]) -> None:
        name = command.get("name")
        if not isinstance(name, str) or not name.strip():
            self._respond_error(cid, "set_session_name", "session name must be non-empty")
            return
        try:
            self._tree.append_session_info(name.strip())
        except Exception as exc:
            self._respond_error(cid, "set_session_name", f"{type(exc).__name__}: {exc}")
            return
        self._respond(cid, "set_session_name")
        self._writer.write_line(
            {"type": "session_info_changed", "name": name.strip()}
        )

    # -- bash ------------------------------------------------------------
    def _cmd_bash(self, cid: str | None, command: dict[str, Any]) -> None:
        cmd = command.get("command")
        if not isinstance(cmd, str) or not cmd:
            self._respond_error(cid, "bash", "bash requires a non-empty command")
            return
        # Run on a worker thread so the dispatch loop stays responsive to other
        # commands (steer/abort/abort_bash) while a (bounded) bash runs. The
        # response is written from the worker when the command settles. Output is
        # secret-scrubbed and bounded by the sandbox.
        with self._lock:
            self._bash_in_flight += 1

        def _run_bash() -> None:
            try:
                policy = CommandPolicy(workspace_root=self._cwd)
                result = run_command(cmd, policy)
                output = result.stdout
                if result.stderr:
                    output = output + result.stderr
                self._respond(
                    cid,
                    "bash",
                    {
                        "output": output,
                        "exitCode": result.exit_code,
                        "cancelled": result.status == CommandStatus.TIMED_OUT,
                        "truncated": result.truncated,
                    },
                )
            except Exception as exc:  # never crash the loop
                self._respond_error(cid, "bash", f"{type(exc).__name__}: {exc}")
            finally:
                with self._lock:
                    self._bash_in_flight -= 1

        thread = threading.Thread(target=_run_bash, name="pipy-rpc-bash", daemon=True)
        with self._lock:
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
