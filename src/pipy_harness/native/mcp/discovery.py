"""Keeping the tools that reach undeclared MCP tools active (Pi
``ensureDiscoveryActive``).

Tools that are not declared to the model are reached through the ``codemode``
tool (scripts call them) or the ``tool_search`` tool (it declares them).
Either reaches every such tool. ``McpDiscovery`` activates the one the
configured exposures ask for: ``codemode`` for ``codemode`` unless
``autoEnableCodemode`` is false, ``tool_search`` for ``deferred``. It decides
from the configuration, so the tool is active before the servers connect.
When undeclared tools exist and neither tool is active afterwards, it warns
once.

Different from Pi: pipy registers its built-in ``codemode`` only on demand,
because registering it probes an optional runtime. ``McpDiscovery`` asks for
the registration when it wants to activate the tool. With
``autoEnableCodemode`` false it does not probe, so the warning names that
setting whenever the tool filters would keep ``codemode``.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Sequence
from typing import Final, Protocol

from pipy_harness.native.tools.base import ToolInfo

CODEMODE_TOOL_NAME: Final = "codemode"
TOOL_SEARCH_TOOL_NAME: Final = "tool_search"


def unreachable_warning(*, auto_enable_codemode_is_cause: bool) -> str:
    reason = " (autoEnableCodemode is false)" if auto_enable_codemode_is_cause else ""
    return (
        "MCP tools are only reachable from the codemode or tool_search tool, "
        f"but neither is active{reason}; they cannot be called."
    )


class DiscoveryTools(Protocol):
    """What the session's tool capabilities offer (``NativeToolCapabilities``)."""

    def all_tools(self) -> Sequence[ToolInfo]: ...

    def declared_tool_names(self) -> Collection[str]: ...

    def activate_tools(self, names: Sequence[str], /) -> None: ...

    def codemode_selectable(self) -> bool: ...

    def register_codemode(self) -> bool: ...


class McpDiscovery:
    """Activates ``codemode`` and ``tool_search`` for one session's servers.

    ``configured_exposures`` gives the exposures of the enabled servers
    (``McpManager.configured_exposures``) and ``notify(level, message)``
    takes the warning. Call ``ensure_active`` on the session thread: at the
    start of the session and after whatever changes the servers' exposures.
    """

    def __init__(
        self,
        *,
        configured_exposures: Callable[[], Collection[str]],
        auto_enable_codemode: bool,
        tools: DiscoveryTools,
        notify: Callable[[str, str], None],
    ) -> None:
        self._configured_exposures = configured_exposures
        self._auto_enable_codemode = auto_enable_codemode
        self._tools = tools
        self._notify = notify
        # Whether the "tools unreachable" warning was shown in this session.
        self._warned = False

    def ensure_active(self) -> None:
        exposures = self._configured_exposures()
        needs_codemode = "codemode" in exposures
        needs_tool_search = "deferred" in exposures
        if not needs_codemode and not needs_tool_search:
            return
        auto = self._auto_enable_codemode
        if needs_codemode and auto:
            self._tools.register_codemode()
        # Only a tool of these names that declares ``reaches_tools`` can reach
        # MCP tools; an extension's tool of the same name without it is never
        # activated. A ``hidden`` one is never declared, so it reaches nothing
        # (Pi would activate it and stay silent).
        reaching = {
            tool.name
            for tool in self._tools.all_tools()
            if tool.reaches_tools and tool.exposure != "hidden"
        }
        has_codemode = CODEMODE_TOOL_NAME in reaching
        has_tool_search = TOOL_SEARCH_TOOL_NAME in reaching
        active = set(self._tools.declared_tool_names())
        activate: list[str] = []
        if (
            needs_codemode
            and has_codemode
            and auto
            and CODEMODE_TOOL_NAME not in active
        ):
            activate.append(CODEMODE_TOOL_NAME)
        if (
            needs_tool_search
            and has_tool_search
            and TOOL_SEARCH_TOOL_NAME not in active
        ):
            activate.append(TOOL_SEARCH_TOOL_NAME)
        if activate:
            self._tools.activate_tools(activate)
        reachable = active.union(activate)
        if has_codemode and CODEMODE_TOOL_NAME in reachable:
            return
        if has_tool_search and TOOL_SEARCH_TOOL_NAME in reachable:
            return
        if self._warned:
            return
        self._warned = True
        # Pi has ``codemode`` registered whenever the filters keep it.
        could_have_codemode = has_codemode or self._tools.codemode_selectable()
        self._notify(
            "warning",
            unreachable_warning(
                auto_enable_codemode_is_cause=needs_codemode
                and could_have_codemode
                and not auto
            ),
        )
