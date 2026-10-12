"""The MCP client: handshake, requests, notifications and server requests.

``McpClient`` is synchronous. ``request`` blocks its caller until the response
arrives, the request times out, the caller's cancel event is set or the
connection closes. Request messages and cancellation notices are written by
one writer thread per client, in the order queued, so a server that stopped
reading cannot hold a caller past its timeout or its cancel event. Responses,
notifications and server requests are dispatched on whichever thread reads the
transport; a handler for a server request runs on a thread of its own, so the
reading thread never waits for one.

One lock guards the client's state, its pending requests and its listener
tables. It is never held while the client sends a message or calls a listener
or a handler.

A listener runs on the reading thread, so it must not call ``request``: the
response could only be delivered by the thread that is waiting for it. Hand
such work to another thread.
"""

from __future__ import annotations

import math
import threading
import time
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Final, Literal, TypeVar

from pipy_harness.native.mcp.protocol import (
    INTERNAL_ERROR,
    INVALID_REQUEST,
    LATEST_PROTOCOL_VERSION,
    METHOD_NOT_FOUND,
    SUPPORTED_PROTOCOL_VERSIONS,
    JsonObject,
    JsonRpcId,
    JsonRpcMessage,
    McpAbortError,
    McpConnectionClosedError,
    McpError,
    McpProtocolError,
    McpTimeoutError,
    format_id,
    is_json_rpc_id,
    is_json_rpc_notification,
    is_json_rpc_request,
    is_json_rpc_response,
    is_number,
    is_object,
)
from pipy_harness.native.mcp.transport import Disposer, McpTransport

DEFAULT_REQUEST_TIMEOUT_MS: Final = 30_000
# A list that is still handing out cursors after this many pages is given up.
MAX_LIST_PAGES = 1_000
# How often a waiting request looks at its caller's cancel event.
_CANCEL_POLL_SECONDS: Final = 0.05

ClientState = Literal["idle", "connecting", "connected", "closed"]
NotificationListener = Callable[[object], None]
ErrorListener = Callable[[Exception], None]
CloseListener = Callable[[], None]
ProgressListener = Callable[[JsonObject], None]
Roots = Sequence[Mapping[str, object]]
_Listener = TypeVar("_Listener")


@dataclass(frozen=True, slots=True)
class McpRequestContext:
    """What a server-request handler gets besides the params.

    ``cancel_event`` is set when the server cancels the request and when the
    connection closes.
    """

    cancel_event: threading.Event


RequestHandler = Callable[[object, McpRequestContext], object]


@dataclass(frozen=True, slots=True)
class McpClientOptions:
    """Identity and defaults of one client.

    ``roots`` is the list answered to ``roots/list``, or a callable returning
    it; ``None`` means the client does not announce roots. A
    ``request_timeout_ms`` that is not a positive finite number disables the
    default timeout.
    """

    name: str
    version: str
    title: str | None = None
    capabilities: Mapping[str, object] | None = None
    protocol_version: str | None = None
    request_timeout_ms: float | None = None
    roots: Roots | Callable[[], Roots] | None = None


@dataclass(frozen=True, slots=True)
class McpRequestOptions:
    """Per-request overrides.

    Setting ``cancel_event`` aborts the request. ``on_progress`` is called on
    the reading thread with the params of each progress notification for this
    request; it may still run while the request is being settled.
    """

    cancel_event: threading.Event | None = None
    timeout_ms: float | None = None
    on_progress: ProgressListener | None = None


@dataclass(frozen=True, slots=True)
class _Outgoing:
    """A message waiting for the writer: a request, or the cancellation of one."""

    message: JsonRpcMessage
    request_id: JsonRpcId
    entry: _PendingRequest
    is_request: bool


class _PendingRequest:
    """One request awaiting its response. Fields are guarded by the client lock."""

    __slots__ = (
        "cancel_event",
        "cancellable",
        "deadline",
        "done",
        "error",
        "on_progress",
        "progress_token",
        "result",
        "timeout_ms",
        "timeout_seconds",
        "write_failed",
        "written",
    )

    def __init__(
        self,
        *,
        timeout_ms: float,
        cancel_event: threading.Event | None,
        cancellable: bool,
        on_progress: ProgressListener | None,
        progress_token: JsonRpcId | None,
    ) -> None:
        self.done = threading.Event()
        # `written` is set when the writer takes the request message off the
        # outbox to write it, `write_failed` when that write raised.
        self.written = False
        self.write_failed = False
        self.result: object = None
        self.error: Exception | None = None
        self.timeout_ms = timeout_ms
        self.timeout_seconds = (
            timeout_ms / 1000 if math.isfinite(timeout_ms) and timeout_ms > 0 else None
        )
        self.deadline: float | None = None
        self.cancel_event = cancel_event
        self.cancellable = cancellable
        self.on_progress = on_progress
        self.progress_token = progress_token
        self.arm_timeout()

    def arm_timeout(self) -> None:
        if self.timeout_seconds is not None:
            self.deadline = time.monotonic() + self.timeout_seconds

    def settle(self, *, result: object = None, error: Exception | None = None) -> None:
        self.result = result
        self.error = error
        self.done.set()


def validate_initialize_result(value: object) -> JsonObject:
    """Return ``value`` when it is a well-formed ``initialize`` result."""

    if is_object(value):
        server_info = value.get("serverInfo")
        if (
            isinstance(value.get("protocolVersion"), str)
            and is_object(value.get("capabilities"))
            and is_object(server_info)
            and isinstance(server_info.get("name"), str)
            and isinstance(server_info.get("version"), str)
            and isinstance(value.get("instructions", ""), str)
        ):
            return value
    raise McpError(INVALID_REQUEST, "Invalid MCP initialize result")


def _invalid(message: str) -> McpError:
    return McpError(INVALID_REQUEST, message)


def _validate_list_page(
    method: str, key: str, value: object, is_item: Callable[[JsonObject], bool]
) -> tuple[list[JsonObject], str | None]:
    """Return the items under ``key`` of one list page, and its next cursor.

    The cursor is ``None`` on the last page. Some servers end pagination with
    ``null`` or ``""`` instead of omitting the cursor.
    """

    items = value.get(key) if is_object(value) else None
    if not is_object(value) or not isinstance(items, list):
        raise _invalid(f"Invalid MCP {method} result")
    for item in items:
        if not is_object(item) or not is_item(item):
            raise _invalid(f"Invalid entry in MCP {method} result")
    next_cursor = value.get("nextCursor")
    if next_cursor is None or next_cursor == "":
        return items, None
    if not isinstance(next_cursor, str):
        raise _invalid(f"Invalid MCP {method} cursor")
    return items, next_cursor


def _has_optional_name(item: JsonObject) -> bool:
    return "name" not in item or isinstance(item["name"], str)


def _is_tool(tool: JsonObject) -> bool:
    return isinstance(tool.get("name"), str) and is_object(tool.get("inputSchema"))


# `name` is required by the spec, but some servers omit it; the URI stands in.
def _is_resource(resource: JsonObject) -> bool:
    return isinstance(resource.get("uri"), str) and _has_optional_name(resource)


def _is_resource_template(template: JsonObject) -> bool:
    return isinstance(template.get("uriTemplate"), str) and _has_optional_name(template)


def _to_resource(item: JsonObject) -> JsonObject:
    return {**item, "name": item.get("name", item["uri"])}


def _to_resource_template(item: JsonObject) -> JsonObject:
    return {**item, "name": item.get("name", item["uriTemplate"])}


def _page_result(key: str, items: list[JsonObject], cursor: str | None) -> JsonObject:
    page: JsonObject = {key: items}
    if cursor is not None:
        page["nextCursor"] = cursor
    return page


def validate_read_resource_result(value: object) -> JsonObject:
    """Return ``value`` when it is a well-formed ``resources/read`` result."""

    contents = value.get("contents") if is_object(value) else None
    if not is_object(value) or not isinstance(contents, list):
        raise _invalid("Invalid MCP resources/read result")
    for entry in contents:
        if (
            not is_object(entry)
            or not isinstance(entry.get("uri"), str)
            or not (
                isinstance(entry.get("text"), str) or isinstance(entry.get("blob"), str)
            )
        ):
            raise _invalid("Invalid contents in MCP resources/read result")
    return value


def validate_call_tool_result(value: object) -> JsonObject:
    """Return a well-formed ``tools/call`` result, with ``content`` present.

    ``content`` is required by the spec, but servers that only return
    ``structuredContent`` omit it; it then becomes an empty list.
    """

    if not is_object(value) or (
        "content" in value and not isinstance(value["content"], list)
    ):
        raise _invalid("Invalid MCP tools/call result")
    if "structuredContent" in value and not is_object(value["structuredContent"]):
        raise _invalid("Invalid MCP tools/call structured content")
    return value if "content" in value else {**value, "content": []}


class McpClient:
    """A client for one MCP server connection. A client connects once."""

    def __init__(self, options: McpClientOptions) -> None:
        self.options = options
        self._lock = threading.Lock()
        self._outbox: deque[_Outgoing] = deque()
        self._outbox_changed = threading.Condition(self._lock)
        self._writer: threading.Thread | None = None
        self._state: ClientState = "idle"
        self._transport: McpTransport | None = None
        self._disposers: list[Disposer] = []
        self._next_request_id = 1
        self._server_info: JsonObject | None = None
        self._server_capabilities: JsonObject | None = None
        self._instructions: str | None = None
        self._protocol_version: str | None = None
        self._pending: dict[JsonRpcId, _PendingRequest] = {}
        self._progress_requests: dict[JsonRpcId, JsonRpcId] = {}
        self._incoming: dict[JsonRpcId, threading.Event] = {}
        self._request_handlers: dict[str, RequestHandler] = {"ping": _answer_ping}
        self._notification_listeners: dict[str, dict[NotificationListener, None]] = {}
        self._error_listeners: dict[ErrorListener, None] = {}
        self._close_listeners: dict[CloseListener, None] = {}
        if options.roots is not None:
            self._request_handlers["roots/list"] = self._answer_roots

    @property
    def connection_state(self) -> ClientState:
        with self._lock:
            return self._state

    @property
    def server_info(self) -> JsonObject | None:
        with self._lock:
            return self._server_info

    @property
    def server_capabilities(self) -> JsonObject | None:
        with self._lock:
            return self._server_capabilities

    @property
    def instructions(self) -> str | None:
        with self._lock:
            return self._instructions

    @property
    def protocol_version(self) -> str | None:
        with self._lock:
            return self._protocol_version

    def connect(self, transport: McpTransport) -> JsonObject:
        """Start ``transport`` and run the handshake; return the server's answer.

        The client is usable once this returns. When it raises, the client and
        the transport are closed.
        """

        with self._lock:
            if self._state != "idle":
                raise RuntimeError(f"Cannot connect MCP client in {self._state} state")
            self._state = "connecting"
            self._transport = transport
            self._disposers = [
                transport.on_message(self._handle_message),
                # Transport errors are reported only. Pending requests fail
                # when the transport closes.
                transport.on_error(self._emit_error),
                transport.on_close(self._mark_closed),
            ]
        try:
            transport.start()
            result = validate_initialize_result(
                self._request(
                    "initialize",
                    self._initialize_params(),
                    McpRequestOptions(),
                    allow_connecting=True,
                )
            )
            version = result["protocolVersion"]
            if version not in SUPPORTED_PROTOCOL_VERSIONS:
                raise McpProtocolError(
                    f"MCP server selected unsupported protocol version {version}"
                )
            assert isinstance(version, str)
            instructions = result.get("instructions")
            with self._lock:
                self._protocol_version = version
                self._server_info = _as_object(result["serverInfo"])
                self._server_capabilities = _as_object(result["capabilities"])
                self._instructions = (
                    instructions if isinstance(instructions, str) else None
                )
            transport.set_protocol_version(version)
            self._notify("notifications/initialized", None, allow_connecting=True)
            with self._lock:
                # The transport may have dropped while the handshake finished.
                if self._state != "connecting":
                    raise McpConnectionClosedError(f"MCP client is {self._state}")
                self._state = "connected"
            return result
        except BaseException:
            try:
                self.close()
            except Exception:  # noqa: BLE001 - a failing close must not mask the connect error
                pass
            raise

    def request(
        self,
        method: str,
        params: Mapping[str, object] | None = None,
        options: McpRequestOptions | None = None,
    ) -> object:
        """Send a request and block until its result; raise what ends it otherwise."""

        return self._request(
            method, params, options or McpRequestOptions(), allow_connecting=False
        )

    def notify(self, method: str, params: Mapping[str, object] | None = None) -> None:
        self._notify(method, params, allow_connecting=False)

    def ping(self, options: McpRequestOptions | None = None) -> None:
        self.request("ping", None, options)

    def list_tools(self, options: McpRequestOptions | None = None) -> list[JsonObject]:
        """Every tool, as the server defined it, following ``nextCursor``."""

        return self._list_all("tools/list", "tools", _is_tool, options)

    def list_resources(
        self, options: McpRequestOptions | None = None
    ) -> list[JsonObject]:
        """Every resource, following ``nextCursor`` through all pages."""

        resources = self._list_all("resources/list", "resources", _is_resource, options)
        return [_to_resource(item) for item in resources]

    def list_resources_page(
        self, cursor: str | None = None, options: McpRequestOptions | None = None
    ) -> JsonObject:
        """One page of resources, starting at ``cursor``."""

        items, next_cursor = self._list_page(
            "resources/list", "resources", _is_resource, cursor, options
        )
        return _page_result(
            "resources", [_to_resource(item) for item in items], next_cursor
        )

    def list_resource_templates(
        self, options: McpRequestOptions | None = None
    ) -> list[JsonObject]:
        """Every resource template, following ``nextCursor`` through all pages."""

        templates = self._list_all(
            "resources/templates/list",
            "resourceTemplates",
            _is_resource_template,
            options,
        )
        return [_to_resource_template(item) for item in templates]

    def list_resource_templates_page(
        self, cursor: str | None = None, options: McpRequestOptions | None = None
    ) -> JsonObject:
        """One page of resource templates, starting at ``cursor``."""

        items, next_cursor = self._list_page(
            "resources/templates/list",
            "resourceTemplates",
            _is_resource_template,
            cursor,
            options,
        )
        return _page_result(
            "resourceTemplates",
            [_to_resource_template(item) for item in items],
            next_cursor,
        )

    def read_resource(
        self, uri: str, options: McpRequestOptions | None = None
    ) -> JsonObject:
        return validate_read_resource_result(
            self.request("resources/read", {"uri": uri}, options)
        )

    def call_tool(
        self,
        name: str,
        arguments: Mapping[str, object] | None = None,
        options: McpRequestOptions | None = None,
    ) -> JsonObject:
        """Call a tool; the result always has a ``content`` list."""

        params: JsonObject = {"name": name}
        if arguments is not None:
            params["arguments"] = dict(arguments)
        return validate_call_tool_result(self.request("tools/call", params, options))

    def _list_page(
        self,
        method: str,
        key: str,
        is_item: Callable[[JsonObject], bool],
        cursor: str | None,
        options: McpRequestOptions | None,
    ) -> tuple[list[JsonObject], str | None]:
        params = None if cursor is None else {"cursor": cursor}
        return _validate_list_page(
            method, key, self.request(method, params, options), is_item
        )

    def _list_all(
        self,
        method: str,
        key: str,
        is_item: Callable[[JsonObject], bool],
        options: McpRequestOptions | None,
    ) -> list[JsonObject]:
        """Every item of a paginated list method. ``options`` apply to each page."""

        items: list[JsonObject] = []
        cursors: set[str] = set()
        cursor: str | None = None
        for _ in range(MAX_LIST_PAGES):
            page, cursor = self._list_page(method, key, is_item, cursor, options)
            items.extend(page)
            if cursor is None:
                return items
            if cursor in cursors:
                raise McpProtocolError(
                    f"MCP {method} returned duplicate cursor: {cursor}"
                )
            cursors.add(cursor)
        raise McpProtocolError(f"MCP {method} exceeded {MAX_LIST_PAGES} pages")

    def set_request_handler(self, method: str, handler: RequestHandler) -> Disposer:
        """Answer the server's ``method`` requests with ``handler``'s return value."""

        with self._lock:
            self._request_handlers[method] = handler

        def dispose() -> None:
            with self._lock:
                if self._request_handlers.get(method) is handler:
                    del self._request_handlers[method]

        return dispose

    def on_notification(self, method: str, listener: NotificationListener) -> Disposer:
        with self._lock:
            self._notification_listeners.setdefault(method, {})[listener] = None

        def dispose() -> None:
            with self._lock:
                listeners = self._notification_listeners.get(method)
                if listeners is None:
                    return
                listeners.pop(listener, None)
                if not listeners:
                    del self._notification_listeners[method]

        return dispose

    def on_error(self, listener: ErrorListener) -> Disposer:
        with self._lock:
            self._error_listeners[listener] = None
        return lambda: self._remove_listener(self._error_listeners, listener)

    def on_close(self, listener: CloseListener) -> Disposer:
        """Call ``listener`` once when the connection closes, however it closes."""

        with self._lock:
            self._close_listeners[listener] = None
        return lambda: self._remove_listener(self._close_listeners, listener)

    def close(self) -> None:
        """Close the connection. Safe to call more than once and from any thread."""

        with self._lock:
            transport = self._transport
            self._transport = None
            disposers = self._disposers
            self._disposers = []
        for dispose in disposers:
            dispose()
        self._mark_closed()
        if transport is not None:
            transport.close()

    # -- outgoing ---------------------------------------------------------

    def _initialize_params(self) -> JsonObject:
        capabilities: JsonObject = dict(self.options.capabilities or {})
        if self.options.roots is not None and "roots" not in capabilities:
            capabilities["roots"] = {}
        client_info: JsonObject = {
            "name": self.options.name,
            "version": self.options.version,
        }
        if self.options.title is not None:
            client_info["title"] = self.options.title
        return {
            "protocolVersion": self.options.protocol_version or LATEST_PROTOCOL_VERSION,
            "capabilities": capabilities,
            "clientInfo": client_info,
        }

    def _request(
        self,
        method: str,
        params: Mapping[str, object] | None,
        options: McpRequestOptions,
        *,
        allow_connecting: bool,
    ) -> object:
        timeout_ms = options.timeout_ms
        if timeout_ms is None:
            timeout_ms = self.options.request_timeout_ms
        if timeout_ms is None:
            timeout_ms = DEFAULT_REQUEST_TIMEOUT_MS
        with self._lock:
            transport = self._require_transport(allow_connecting)
            if options.cancel_event is not None and options.cancel_event.is_set():
                raise McpAbortError()
            request_id = self._next_request_id
            self._next_request_id += 1
            entry = _PendingRequest(
                timeout_ms=timeout_ms,
                cancel_event=options.cancel_event,
                # The spec forbids cancelling `initialize`.
                cancellable=method != "initialize",
                on_progress=options.on_progress,
                progress_token=request_id if options.on_progress is not None else None,
            )
            self._pending[request_id] = entry
            if entry.progress_token is not None:
                self._progress_requests[entry.progress_token] = request_id
            message: JsonRpcMessage = {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
            }
            if entry.progress_token is not None:
                meta = params.get("_meta") if params is not None else None
                message["params"] = {
                    **(params or {}),
                    "_meta": {
                        **(meta if is_object(meta) else {}),
                        "progressToken": entry.progress_token,
                    },
                }
            elif params is not None:
                message["params"] = dict(params)
            # A write blocks for as long as the server does not read, so the
            # writer does it while this thread waits with the deadline and
            # the cancel event.
            self._outbox.append(_Outgoing(message, request_id, entry, is_request=True))
            self._outbox_changed.notify()
            if self._writer is None:
                writer = threading.Thread(
                    target=self._write_outbox,
                    args=(transport,),
                    name="mcp-client-writer",
                    daemon=True,
                )
                # The first request is `initialize`: when the thread cannot be
                # started, `connect` fails and closes the client, which ends
                # this request.
                writer.start()
                self._writer = writer
        return self._await_response(request_id, entry)

    def _write_outbox(self, transport: McpTransport) -> None:
        """Write queued requests and cancellation notices until the client closes."""

        while True:
            with self._outbox_changed:
                while not self._outbox and self._state != "closed":
                    self._outbox_changed.wait()
                if self._state == "closed":
                    self._outbox.clear()
                    return
                outgoing = self._outbox.popleft()
                if outgoing.is_request:
                    outgoing.entry.written = True
                elif outgoing.entry.write_failed:
                    # The request never reached the server.
                    continue
            try:
                transport.send(outgoing.message)
            except Exception as error:  # noqa: BLE001 - ends the request, or is reported
                if not outgoing.is_request:
                    self._emit_error(error)
                    continue
                with self._lock:
                    outgoing.entry.write_failed = True
                self._cancel_pending(outgoing.request_id, error, notify_server=False)

    def _await_response(self, request_id: int, entry: _PendingRequest) -> object:
        cancel_event = entry.cancel_event
        while not entry.done.is_set():
            if cancel_event is not None and cancel_event.is_set():
                self._cancel_pending(
                    request_id,
                    McpAbortError(),
                    notify_server=entry.cancellable,
                    reason="Aborted",
                )
                continue
            wait = self._seconds_left(request_id, entry)
            if cancel_event is not None:
                wait = (
                    _CANCEL_POLL_SECONDS
                    if wait is None
                    else min(wait, _CANCEL_POLL_SECONDS)
                )
            entry.done.wait(wait)
        if entry.error is not None:
            raise entry.error
        return entry.result

    def _seconds_left(self, request_id: int, entry: _PendingRequest) -> float | None:
        """Seconds until ``entry`` times out, ``None`` when it has no deadline.

        An entry whose deadline has passed is settled with a timeout here, in
        the same critical section as the check, so a progress notification
        either renews the deadline before it or arrives too late.
        """

        with self._lock:
            if entry.deadline is None or entry.done.is_set():
                return None
            remaining = entry.deadline - time.monotonic()
            if remaining > 0:
                return remaining
            self._remove_pending(request_id, entry)
            entry.settle(error=McpTimeoutError(entry.timeout_ms))
            if entry.cancellable:
                self._queue_cancelled(request_id, entry, "Request timed out")
        return None

    def _notify(
        self,
        method: str,
        params: Mapping[str, object] | None,
        *,
        allow_connecting: bool,
    ) -> None:
        with self._lock:
            transport = self._require_transport(allow_connecting)
        message: JsonRpcMessage = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = dict(params)
        transport.send(message)

    def _require_transport(self, allow_connecting: bool) -> McpTransport:
        if self._transport is not None and (
            self._state == "connected"
            or (allow_connecting and self._state == "connecting")
        ):
            return self._transport
        raise McpConnectionClosedError(f"MCP client is {self._state}")

    def _cancel_pending(
        self,
        request_id: JsonRpcId,
        error: Exception,
        *,
        notify_server: bool,
        reason: str | None = None,
    ) -> None:
        """End a request that is still pending; do nothing when it was settled."""

        with self._lock:
            entry = self._pending.get(request_id)
            if entry is None:
                return
            self._remove_pending(request_id, entry)
            entry.settle(error=error)
            if notify_server:
                self._queue_cancelled(request_id, entry, reason)

    def _queue_cancelled(
        self, request_id: JsonRpcId, entry: _PendingRequest, reason: str | None
    ) -> None:
        """Queue the notice that a request was given up. Caller holds the lock.

        The writer sends it after the request itself. A request the writer
        never took, or failed to write, did not reach the server and gets
        none; neither does anything once ``close()`` dropped the transport.
        """

        if self._transport is None or not entry.written or entry.write_failed:
            return
        params: JsonObject = {"requestId": request_id}
        if reason:
            params["reason"] = reason
        message: JsonRpcMessage = {
            "jsonrpc": "2.0",
            "method": "notifications/cancelled",
            "params": params,
        }
        self._outbox.append(_Outgoing(message, request_id, entry, is_request=False))
        self._outbox_changed.notify()

    def _send_reported(self, transport: McpTransport, message: JsonRpcMessage) -> None:
        try:
            transport.send(message)
        except Exception as error:  # noqa: BLE001 - reported to the error listeners instead
            self._emit_error(error)

    def _remove_pending(self, request_id: JsonRpcId, entry: _PendingRequest) -> None:
        # Caller holds the lock.
        del self._pending[request_id]
        if not entry.written:
            # Still queued: a request that ended is not written any more.
            self._outbox = deque(
                outgoing for outgoing in self._outbox if outgoing.entry is not entry
            )
        if entry.progress_token is not None:
            self._progress_requests.pop(entry.progress_token, None)

    # -- incoming ---------------------------------------------------------

    def _handle_message(self, message: JsonRpcMessage) -> None:
        if is_json_rpc_response(message):
            self._handle_response(message)
        elif is_json_rpc_request(message):
            self._handle_request(message)
        elif is_json_rpc_notification(message):
            self._handle_notification(str(message["method"]), message.get("params"))
        else:
            self._emit_error(
                McpError(INVALID_REQUEST, "Received invalid JSON-RPC message")
            )

    def _handle_response(self, message: JsonRpcMessage) -> None:
        response_id = message["id"]
        assert is_json_rpc_id(response_id)
        with self._lock:
            entry = self._pending.get(response_id)
            if entry is not None:
                self._remove_pending(response_id, entry)
                if "result" in message:
                    entry.settle(result=message["result"])
                else:
                    error = _as_object(message["error"])
                    code = error["code"]
                    assert is_number(code)
                    entry.settle(
                        error=McpError(code, str(error["message"]), error.get("data"))
                    )
        if entry is None:
            self._emit_error(
                McpProtocolError(
                    f"Received response for unknown MCP request {format_id(response_id)}"
                )
            )

    def _handle_request(self, message: JsonRpcMessage) -> None:
        request_id = message["id"]
        assert is_json_rpc_id(request_id)
        method = str(message["method"])
        with self._lock:
            transport = self._transport
            # A request dispatched while the connection closes is dropped:
            # nothing would cancel its handler any more.
            if transport is None or self._state == "closed":
                return
            handler = self._request_handlers.get(method)
            cancel_event = threading.Event()
            if handler is not None:
                self._incoming[request_id] = cancel_event
        # The reading thread must stay free to deliver a cancellation of this
        # request, and must never block on a write to the server.
        threading.Thread(
            target=self._serve_request,
            args=(
                transport,
                request_id,
                method,
                message.get("params"),
                handler,
                cancel_event,
            ),
            name="mcp-server-request",
            daemon=True,
        ).start()

    def _serve_request(
        self,
        transport: McpTransport,
        request_id: JsonRpcId,
        method: str,
        params: object,
        handler: RequestHandler | None,
        cancel_event: threading.Event,
    ) -> None:
        if handler is None:
            self._send_reported(
                transport,
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {
                        "code": METHOD_NOT_FOUND,
                        "message": f"Method not found: {method}",
                    },
                },
            )
            return
        try:
            result = handler(params, McpRequestContext(cancel_event))
            transport.send(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {} if result is None else result,
                }
            )
        except Exception as error:  # noqa: BLE001 - a failing handler is answered with a JSON-RPC error
            response_error: JsonObject
            if isinstance(error, McpError):
                response_error = {"code": error.code, "message": error.message}
                if error.data is not None:
                    response_error["data"] = error.data
            else:
                response_error = {"code": INTERNAL_ERROR, "message": str(error)}
            self._send_reported(
                transport, {"jsonrpc": "2.0", "id": request_id, "error": response_error}
            )
        finally:
            with self._lock:
                if self._incoming.get(request_id) is cancel_event:
                    del self._incoming[request_id]

    def _handle_notification(self, method: str, params: object) -> None:
        if method == "notifications/progress":
            self._handle_progress(params)
        elif method == "notifications/cancelled":
            self._handle_cancelled(params)
        with self._lock:
            listeners = list(self._notification_listeners.get(method, ()))
        for listener in listeners:
            try:
                listener(params)
            except Exception as error:  # noqa: BLE001 - one listener must not affect the others
                self._emit_error(error)

    def _handle_progress(self, params: object) -> None:
        if not is_object(params) or not is_number(params.get("progress")):
            return
        token = params.get("progressToken")
        if not is_json_rpc_id(token):
            return
        with self._lock:
            request_id = self._progress_requests.get(token)
            entry = None if request_id is None else self._pending.get(request_id)
            if entry is None:
                return
            entry.arm_timeout()
            on_progress = entry.on_progress
        if on_progress is None:
            return
        try:
            on_progress(params)
        except Exception as error:  # noqa: BLE001 - a failing progress handler is reported only
            self._emit_error(error)

    def _handle_cancelled(self, params: object) -> None:
        if not is_object(params):
            return
        request_id = params.get("requestId")
        if not is_json_rpc_id(request_id):
            return
        with self._lock:
            cancel_event = self._incoming.get(request_id)
        if cancel_event is not None:
            cancel_event.set()

    # -- closing and reporting -------------------------------------------

    def _mark_closed(self) -> None:
        """End everything in flight and flip the state. Idempotent.

        Called for a transport that closed on its own and from ``close()``.
        """

        with self._lock:
            was_closed = self._state == "closed"
            self._state = "closed"
            self._outbox.clear()
            self._outbox_changed.notify_all()
            for request_id, entry in list(self._pending.items()):
                self._remove_pending(request_id, entry)
                entry.settle(error=McpConnectionClosedError())
            incoming = list(self._incoming.values())
            self._incoming.clear()
            listeners = [] if was_closed else list(self._close_listeners)
        for cancel_event in incoming:
            cancel_event.set()
        for listener in listeners:
            try:
                listener()
            except Exception as error:  # noqa: BLE001 - every close listener is still called
                self._emit_error(error)

    def _emit_error(self, error: Exception) -> None:
        with self._lock:
            listeners = list(self._error_listeners)
        for listener in listeners:
            try:
                listener(error)
            except Exception:  # noqa: BLE001 - an error listener must not break the reading thread
                pass

    def _remove_listener(
        self, listeners: dict[_Listener, None], listener: _Listener
    ) -> None:
        with self._lock:
            listeners.pop(listener, None)

    def _answer_roots(self, params: object, context: McpRequestContext) -> object:
        roots = self.options.roots
        assert roots is not None
        return {
            "roots": [dict(root) for root in (roots() if callable(roots) else roots)]
        }


def _answer_ping(params: object, context: McpRequestContext) -> object:
    return {}


def _as_object(value: object) -> JsonObject:
    assert is_object(value)
    return value
