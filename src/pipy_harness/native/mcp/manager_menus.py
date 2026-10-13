"""The menus of the ``/mcp`` manager (Pi ``serversMenu``, ``serverMenu``,
``showTools`` and ``chooseExposure`` in ``ext/index.ts``).

A menu is a value: a title, text above the list, the items and what the
confirm and the cancel key do. The functions here build one from the servers
as they are at that moment and touch no user interface; the manager view
(``native/ui/components/mcp_manager_view.py``) draws a menu and the flow
(``manager_flow.py``) decides what a choice does. A view that is open builds
its menu again while servers connect and shows it when it differs.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from pipy_harness.native.mcp.config import (
    DEFAULT_MCP_EXPOSURE,
    McpServerEntry,
    get_mcp_tool_exposure,
    global_mcp_config_path,
)
from pipy_harness.native.mcp.manager import McpServerStatus, describe_state
from pipy_harness.native.tools.collation import locale_compare_key

# What each exposure a user can choose means (Pi ``EXPOSURE_DESCRIPTIONS``).
# pipy's codemode is Python, so the search function is ``search_tools()``.
EXPOSURE_DESCRIPTIONS: Final[dict[str, str]] = {
    "codemode": "called from codemode scripts, which find them with search_tools()",
    "deferred": (
        "not declared until tool_search loads them, then called directly; "
        "no codemode needed"
    ),
    "direct": "declared to the model like built-in tools",
}
# The values of a server menu's items.
ACTION_SIGN_IN: Final = "signin"
ACTION_TOOLS: Final = "tools"
ACTION_RECONNECT: Final = "reconnect"
ACTION_SIGN_OUT: Final = "signout"
ACTION_EXPOSURE: Final = "exposure"
ACTION_ENABLE: Final = "enable"
ACTION_DISABLE: Final = "disable"
ACTION_ENABLE_IN_PROJECT: Final = "enable-project"
ACTION_DISABLE_IN_PROJECT: Final = "disable-project"
_SAVED_TO_PROJECT: Final = "saved to the project mcp.json"
_RECONNECTABLE: Final = ("failed", "disconnected", "connected", "needs-auth")


@dataclass(frozen=True, slots=True)
class McpMenuItem:
    """One row of a menu (pi-tui ``SelectItem``)."""

    value: str
    label: str
    description: str | None = None


@dataclass(frozen=True, slots=True)
class McpMenu:
    """One screen of the manager (Pi ``McpMenu``).

    ``details`` is shown below the title and ``error`` below that, in the
    error colour. ``empty`` is shown when there are no items. ``selected`` is
    the value of the item that is selected when the menu opens.
    ``confirm_label`` and ``cancel_label`` say what the two keys do, for the
    key hints.
    """

    title: str
    items: tuple[McpMenuItem, ...]
    confirm_label: str
    cancel_label: str
    details: str | None = None
    error: str | None = None
    empty: str | None = None
    selected: str | None = None


def _exposure(entry: McpServerEntry) -> str:
    exposure = entry.config.get("exposure")
    return DEFAULT_MCP_EXPOSURE if exposure is None else str(exposure)


def _state(status: McpServerStatus) -> str | None:
    return None if status.connection is None else status.connection.state


def attention_rank(status: McpServerStatus) -> int:
    """Servers that need the user come first (Pi ``attentionRank``): one
    that needs a sign-in, then failed, disconnected, starting or connecting,
    connected, disabled."""

    if not status.enabled:
        return 5
    return {"needs-auth": 0, "failed": 1, "disconnected": 2, "connected": 4}.get(
        _state(status) or "", 3
    )


def _name_order(name: str) -> tuple[object, ...]:
    # As the servers section sorts names (Pi ``localeCompare``).
    return (locale_compare_key(name.lower()), tuple(ch.isupper() for ch in name))


def describe_transport(entry: McpServerEntry) -> str:
    """The server's URL, or its command line (Pi ``describeTransport``)."""

    config = entry.config
    if "url" in config:
        return str(config["url"])
    args = config.get("args") or ()
    return " ".join([str(config.get("command", "")), *(str(arg) for arg in args)])


def _source(entry: McpServerEntry) -> str:
    """Where a server comes from, for the list: ``global``, ``project``,
    ``extension`` or ``global, project override``."""

    if entry.override:
        return "global, project override"
    return entry.source if entry.scope is None else entry.scope


def servers_menu(
    servers: Sequence[McpServerStatus],
    config_errors: Sequence[str],
    config_home: Path | str,
    overridden: Sequence[str] = (),
) -> McpMenu:
    """Every configured server with ``<state> · <exposure> · <source>``,
    those that need the user first and then by name (Pi ``serversMenu``).
    The errors of the configuration files and the registered servers that
    ``mcp.json`` overrides are shown above the list (Pi ``notices``)."""

    ordered = sorted(
        servers,
        key=lambda status: (attention_rank(status), _name_order(status.entry.name)),
    )
    return McpMenu(
        title="MCP servers",
        error="\n".join(
            [
                *(f"config: {error}" for error in config_errors),
                *(f"overridden: {line}" for line in overridden),
            ]
        )
        or None,
        items=tuple(
            McpMenuItem(
                value=status.entry.name,
                label=status.entry.name,
                description=(
                    f"{describe_state(status)} · {_exposure(status.entry)}"
                    f" · {_source(status.entry)}"
                ),
            )
            for status in ordered
        ),
        empty=(
            "No MCP servers configured. Add them to "
            f"{global_mcp_config_path(config_home)} or .pipy/mcp.json."
        ),
        confirm_label="manage",
        cancel_label="close",
    )


def _find(servers: Sequence[McpServerStatus], name: str) -> McpServerStatus | None:
    return next((status for status in servers if status.entry.name == name), None)


def _saved(entry: McpServerEntry) -> str:
    """Where enabling or disabling the server is saved."""

    if entry.scope == "extension":
        return "for this session"
    if entry.override:
        return _SAVED_TO_PROJECT
    if entry.scope:
        return f"saved to the {entry.scope} mcp.json"
    return "saved to mcp.json"


def _server_actions(
    status: McpServerStatus, project_config: str | None
) -> tuple[McpMenuItem, ...]:
    entry = status.entry
    saved = _saved(entry)
    # A user-level server without an override can be turned on or off for
    # the trusted project alone.
    in_project = (
        entry.scope == "global" and not entry.override and project_config is not None
    )
    items: list[McpMenuItem] = []
    if not status.enabled:
        items.append(McpMenuItem(ACTION_ENABLE, "Enable", saved))
        if in_project:
            items.append(
                McpMenuItem(
                    ACTION_ENABLE_IN_PROJECT,
                    "Enable in this project",
                    _SAVED_TO_PROJECT,
                )
            )
        return tuple(items)
    view = status.connection
    state = None if view is None else view.state
    if state == "needs-auth":
        items.append(McpMenuItem(ACTION_SIGN_IN, "Sign in", "opens the browser"))
    if view is not None and state == "connected":
        items.append(McpMenuItem(ACTION_TOOLS, "Tools", f"{len(view.tools)} offered"))
    if state in _RECONNECTABLE:
        items.append(McpMenuItem(ACTION_RECONNECT, "Reconnect"))
    if view is not None and state == "connected" and view.oauth_url is not None:
        items.append(
            McpMenuItem(ACTION_SIGN_OUT, "Sign out", "deletes the stored credentials")
        )
    items.append(McpMenuItem(ACTION_EXPOSURE, "Exposure", _exposure(entry)))
    items.append(McpMenuItem(ACTION_DISABLE, "Disable", saved))
    if in_project:
        items.append(
            McpMenuItem(
                ACTION_DISABLE_IN_PROJECT,
                "Disable in this project",
                _SAVED_TO_PROJECT,
            )
        )
    return tuple(items)


def server_menu(
    servers: Sequence[McpServerStatus], name: str, project_config: str | None
) -> McpMenu:
    """One server: its transport, source and state, what its last action
    said, the error of a server that is not connected, and the actions its
    state allows (Pi ``serverMenu``). ``project_config`` is the trusted
    project's ``mcp.json``, or ``None`` without one."""

    status = _find(servers, name)
    if status is None:
        return McpMenu(
            title=name,
            items=(),
            empty="This server is no longer configured.",
            confirm_label="",
            cancel_label="back",
        )
    entry = status.entry
    view = status.connection
    items = _server_actions(status, project_config)
    details = [
        describe_transport(entry),
        f"{entry.scope or 'config'}: {entry.source}",
        *([f"project override: {entry.override}"] if entry.override else []),
        f"State: {describe_state(status, with_error=False)}",
    ]
    errors = [
        status.message,
        None if view is None or view.state == "connected" else view.error,
    ]
    return McpMenu(
        title=f"MCP server {name}",
        details="\n".join(details),
        error="\n".join(line for line in errors if line is not None) or None,
        items=items,
        selected=items[0].value if items else None,
        confirm_label="select",
        cancel_label="back",
    )


def tools_menu(status: McpServerStatus) -> McpMenu:
    """The server's tools with the first line of their description, and
    ``[<exposure>]`` for a tool whose exposure is not the server's (Pi
    ``showTools``). Both keys go back."""

    entry = status.entry
    exposure = _exposure(entry)
    meaning = EXPOSURE_DESCRIPTIONS.get(exposure, "unreachable")
    overridden = (
        "\nSome tools override it with toolExposure."
        if entry.config.get("toolExposure")
        else ""
    )
    tools = () if status.connection is None else status.connection.tools
    items: list[McpMenuItem] = []
    for tool in tools:
        tool_name = str(tool.get("name", ""))
        tool_exposure = get_mcp_tool_exposure(entry.config, tool_name)
        text = tool.get("description")
        description = text.split("\n", 1)[0] if isinstance(text, str) else ""
        items.append(
            McpMenuItem(
                value=tool_name,
                label=tool_name,
                description=description
                if tool_exposure == exposure
                else f"[{tool_exposure}] {description}",
            )
        )
    return McpMenu(
        title=f"Tools of {entry.name}",
        details=f"Exposure {exposure}: {meaning}{overridden}",
        items=tuple(items),
        empty="The server offers no tools.",
        confirm_label="back",
        cancel_label="back",
    )


def exposure_menu(status: McpServerStatus) -> McpMenu:
    """``codemode``, ``deferred`` and ``direct`` with what they mean, the
    server's own marked and selected (Pi ``chooseExposure``)."""

    entry = status.entry
    current = _exposure(entry)
    if entry.scope == "extension":
        details = (
            f"Applies to this session; the server is registered by {entry.source}."
        )
    else:
        details = f"Saved to {entry.override or entry.source}."
    return McpMenu(
        title=f"Exposure of {entry.name}",
        details=details,
        items=tuple(
            McpMenuItem(
                value=exposure,
                label=f"{'✓ ' if exposure == current else '  '}{exposure}",
                description=description,
            )
            for exposure, description in EXPOSURE_DESCRIPTIONS.items()
        ),
        selected=current,
        confirm_label="save",
        cancel_label="back",
    )
