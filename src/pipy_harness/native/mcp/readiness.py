"""Which servers a prompt or a tool call waits for (Pi ``ext/index.ts``).

Servers connect in the background, so their tools may not be registered yet
when the session needs them. Two waits close that gap:

- The first prompt of a session waits, for a bounded time, for the servers
  that have ``direct`` tools, so its first request declares them (Pi
  ``waitForDirectServers``).
- A tool call waits for the servers it may reach before it runs (Pi's
  ``tool_call`` handler): a tool that declares ``reaches_tools`` for the
  servers its reach names, a resource tool for every server, and an MCP tool
  for its own server.

``McpReadiness`` only decides what to wait for. It hands the caller a function
that does the waiting, or ``None`` when nothing is connecting, which is the
usual case and costs one look at the servers. The session runs the function
so that Escape and an abort end it; nothing here knows a user interface.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Final, Literal

from pipy_harness.native.mcp.config import (
    DEFAULT_MCP_EXPOSURE,
    McpServerEntry,
    mcp_namespace,
)
from pipy_harness.native.mcp.manager import McpManager, ServerSelector
from pipy_harness.native.tools.base import ReachesTools, ToolInfo
from pipy_harness.native.tools.name_patterns import MCP_RESOURCE_TOOL_NAMES

# How long the first prompt waits for servers with ``direct`` tools (Pi
# ``DEFAULT_STARTUP_WAIT_MS``).
STARTUP_WAIT_SECONDS: float = 10.0

STILL_CONNECTING_NOTICE: Final = (
    "MCP servers are still connecting; their tools become available once connected."
)

# Waits for servers. Returns whether they all settled; ``False`` after the
# cancel event was set or the wait's time limit passed.
ReadinessWait = Callable[[threading.Event | None], bool]

_Reach = Literal["all"] | tuple[str, ...]


def has_direct_tools(entry: McpServerEntry) -> bool:
    """Whether some of the server's tools are declared to the model.

    Known from the configuration before the server connects: its exposure,
    or any ``toolExposure`` value, is ``direct``.
    """

    config = entry.config
    if (config.get("exposure") or DEFAULT_MCP_EXPOSURE) == "direct":
        return True
    overrides = config.get("toolExposure")
    return isinstance(overrides, Mapping) and "direct" in overrides.values()


def reaches_server(reach: _Reach, server: str) -> bool:
    """Whether a call with this reach needs the server (Pi ``reachesServer``).

    It reaches every tool, or one of its words contains the server's
    namespace.
    """

    if reach == "all":
        return True
    namespace = mcp_namespace(server)
    return any(namespace in word for word in reach)


def _call_reach(reaches_tools: ReachesTools, arguments_json: str) -> _Reach | None:
    """The reach a tool declares for one call, or ``None`` when it has none.

    A reach function that fails, or returns something that is neither
    ``"all"`` nor a list of words, counts as ``"all"``: waiting for every
    server is the over-match the contract allows.
    """

    try:
        arguments: Any = json.loads(arguments_json)
    except ValueError:
        arguments = None
    if not isinstance(arguments, dict):
        arguments = {}
    try:
        reach: Any = reaches_tools(arguments)
    except Exception:  # noqa: BLE001 - an extension's function; wait for every server
        return "all"
    if reach is None:
        return None
    if isinstance(reach, (list, tuple)) and all(
        isinstance(word, str) for word in reach
    ):
        return tuple(reach)
    return "all"


def _every_server(entry: McpServerEntry) -> bool:
    return True


class McpReadiness:
    """The readiness waits of one session. Used by the session thread only."""

    def __init__(
        self,
        manager: McpManager,
        all_tools: Callable[[], Sequence[ToolInfo]],
        *,
        startup_wait_seconds: float | None = None,
    ) -> None:
        """``all_tools`` lists the session's registered tools as they are now
        (``NativeToolCapabilities.all_tools``)."""

        self._manager = manager
        self._all_tools = all_tools
        self._startup_wait_seconds = (
            STARTUP_WAIT_SECONDS
            if startup_wait_seconds is None
            else startup_wait_seconds
        )
        self._waited_for_startup = False

    def first_prompt_wait(self) -> ReadinessWait | None:
        """The wait of the session's first prompt, at most once.

        ``None`` for every later prompt and when no server with ``direct``
        tools is still connecting. The wait gives up after the startup limit
        and then returns ``False``; the caller shows
        ``STILL_CONNECTING_NOTICE``.
        """

        if self._waited_for_startup:
            return None
        self._waited_for_startup = True
        if self._manager.wait_for_servers(has_direct_tools, timeout=0):
            return None
        return lambda cancel_event: self._manager.wait_for_servers(
            has_direct_tools,
            timeout=self._startup_wait_seconds,
            cancel_event=cancel_event,
        )

    def call_wait(self, tool_name: str, arguments_json: str) -> ReadinessWait | None:
        """The wait a call to ``tool_name`` makes before it runs, if any.

        Has no time limit of its own: it ends when the servers settled, when
        the session closes them, or when the caller's cancel event is set.
        """

        if self._manager.wait_for_servers(timeout=0):
            return None
        tool = next(
            (tool for tool in self._all_tools() if tool.name == tool_name), None
        )
        if tool is None:
            return None
        select = self._servers_of(tool, arguments_json)
        if select is None or self._manager.wait_for_servers(select, timeout=0):
            return None
        return lambda cancel_event: self._manager.wait_for_servers(
            select, cancel_event=cancel_event
        )

    def _servers_of(self, tool: ToolInfo, arguments_json: str) -> ServerSelector | None:
        reach = (
            None
            if tool.reaches_tools is None
            else _call_reach(tool.reaches_tools, arguments_json)
        )
        if reach is not None:
            return lambda entry: reaches_server(reach, entry.name)
        if tool.name in MCP_RESOURCE_TOOL_NAMES:
            return _every_server
        owner = self._manager.tool_owner(tool.name)
        if owner is None:
            return None
        return lambda entry: entry.name == owner
