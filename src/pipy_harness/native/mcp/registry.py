"""The MCP servers extensions register (Pi ``McpServerRegistry`` in
``core/mcp-servers.ts`` and the checks of ``registerMcpServer`` in
``core/extensions/loader.ts``).

A registry holds the registrations of the extensions of one extension
generation: the extensions that were loaded together, at the start of a
session or by one ``/reload``. It validates and stores; it connects nothing.
The session's ``McpManager`` reads it when it starts and after every change
(``McpManager.sync_registered``) and connects the servers next to those of
``mcp.json``.
Another session in the same run (``/new``, ``/resume``, ``/fork``) takes
the registry back to what was registered while the extensions loaded
(``reset``), as Pi's new runtime starts with a new registry.

Threads. Every method may be called from any thread. The check whether a
name is free and the registration are one step under the registry's lock, so
two extensions that register one name at the same time cannot both succeed.
The lock is never held while the change listener runs or while anything
else is called, and nothing here takes another lock. That includes copying
a configuration, which can run code of the extension that supplied it: the
registry stores its own copy, never changes a stored registration, and
copies again, outside the lock, for whoever reads.

Ownership. Pi identifies the owner of a registration by the extension's
path. pipy shows a path *label*, which two extensions can share (two
``--extension`` directories of the same name), so a registration also
carries an ``owner`` token that only its extension has; the label is for
display. Without a token the label is the owner.

Different from Pi: the owner check is the registry's own step (Pi's loader
checks and then registers, on one thread). A registry that was closed, which
the session does with the registry of the extensions a reload replaced,
refuses every change.
"""

from __future__ import annotations

import copy
import threading
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field

from pipy_harness.native.mcp.config import (
    McpServerConfig,
    mcp_namespace,
    validate_mcp_server_config,
)


class McpServerRegistrationError(ValueError):
    """A server cannot be registered; the message says why."""


@dataclass(frozen=True, slots=True)
class RegisteredMcpServer:
    """A server an extension registered (Pi ``RegisteredMcpServer``)."""

    name: str
    # The validated ``mcpServers`` entry.
    config: McpServerConfig
    # Path label of the extension that registered the server, for display.
    extension_path: str
    # What identifies that extension; ``None`` when the label does.
    owner: object = field(default=None, compare=False, repr=False)


def _owner(server: RegisteredMcpServer) -> object:
    return server.extension_path if server.owner is None else server.owner


def validated_registration(
    name: object, config: object, extension_path: str, owner: object = None
) -> RegisteredMcpServer:
    """The registration of ``name`` with a validated copy of ``config``.

    Raises ``McpServerRegistrationError`` for a name or a configuration that
    an ``mcpServers`` entry could not have.
    """

    reason = (
        validate_mcp_server_config(name, config)
        if isinstance(name, str)
        else "the server name must be a string"
    )
    if isinstance(reason, str):
        raise McpServerRegistrationError(
            f'Invalid MCP server registered by extension "{extension_path}": {reason}'
        )
    assert isinstance(name, str)
    return RegisteredMcpServer(name, reason, extension_path, owner)


def registration_conflict(
    server: RegisteredMcpServer, registered: Iterable[RegisteredMcpServer]
) -> str | None:
    """Why ``server`` cannot be registered next to ``registered``: another
    extension has its name, or a server has its namespace under another name
    (names that differ only in ``-`` and ``_``). ``None`` when it can."""

    namespace = mcp_namespace(server.name)
    clash: RegisteredMcpServer | None = None
    for other in registered:
        if other.name == server.name:
            if _owner(other) != _owner(server):
                return (
                    f'MCP server "{server.name}" is already registered by '
                    f'extension "{other.extension_path}"'
                )
        elif clash is None and mcp_namespace(other.name) == namespace:
            clash = other
    if clash is not None:
        return (
            f'MCP server "{server.name}" conflicts with registered server '
            f'"{clash.name}"'
        )
    return None


def _copy(server: RegisteredMcpServer) -> RegisteredMcpServer:
    return RegisteredMcpServer(
        server.name, copy.deepcopy(server.config), server.extension_path, server.owner
    )


class McpServerRegistry:
    """The servers registered by the extensions of one generation."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # By name, in registration order; a name that is registered again
        # keeps its place.
        self._servers: dict[str, RegisteredMcpServer] = {}
        # What the extensions registered while they loaded (``load``).
        self._loaded: dict[str, RegisteredMcpServer] = {}
        self._listener: Callable[[], None] | None = None
        # The registrations after each change that nobody took yet.
        self._changes: list[tuple[RegisteredMcpServer, ...]] = []
        self._closed = False

    def register(self, server: RegisteredMcpServer) -> None:
        """Register ``server``, or replace the registration its extension
        made under the same name. Raises ``McpServerRegistrationError`` when
        another extension has the name or a registered server has the
        namespace, and when the registry is closed."""

        stored = _copy(server)
        with self._lock:
            self._check_open()
            conflict = registration_conflict(stored, self._servers.values())
            if conflict is not None:
                raise McpServerRegistrationError(conflict)
            self._servers[stored.name] = stored
            listener = self._changed()
        if listener is not None:
            listener()

    def load_conflict(self, servers: Sequence[RegisteredMcpServer]) -> str | None:
        """Why ``load`` would refuse ``servers``; ``None`` when it would
        take them."""

        with self._lock:
            return self._load_conflict(servers)

    def load(self, servers: Sequence[RegisteredMcpServer]) -> None:
        """Register what one extension registered while it loaded: all of
        ``servers`` or, with ``McpServerRegistrationError``, none. The
        listener is not called: nothing follows the registry of extensions
        that are still loading."""

        stored = [_copy(server) for server in servers]
        with self._lock:
            self._check_open()
            conflict = self._load_conflict(stored)
            if conflict is not None:
                raise McpServerRegistrationError(conflict)
            for server in stored:
                self._servers[server.name] = server
                self._loaded[server.name] = server

    def _load_conflict(self, servers: Sequence[RegisteredMcpServer]) -> str | None:
        taken = dict(self._servers)
        for server in servers:
            conflict = registration_conflict(server, taken.values())
            if conflict is not None:
                return conflict
            taken[server.name] = server
        return None

    def unregister(self, name: str, owner: object) -> None:
        """Remove the server ``owner`` registered as ``name``: its token, or
        its path label when it registered without one. A server of another
        extension, and a name that is not registered, are left alone. Raises
        ``McpServerRegistrationError`` when the registry is closed."""

        with self._lock:
            self._check_open()
            server = self._servers.get(name)
            if server is None or _owner(server) != owner:
                return
            del self._servers[name]
            listener = self._changed()
        if listener is not None:
            listener()

    def get(self, name: str) -> RegisteredMcpServer | None:
        """A copy of the registration of ``name``, if there is one."""

        with self._lock:
            server = self._servers.get(name)
        return None if server is None else _copy(server)

    def list(self) -> list[RegisteredMcpServer]:
        """Copies of the registered servers, in registration order."""

        with self._lock:
            servers = list(self._servers.values())
        return [_copy(server) for server in servers]

    def set_change_listener(self, listener: Callable[[], None] | None) -> None:
        """Call ``listener`` after every change from now on, on the thread
        that made the change. It reads the registry itself, so it finds the
        state after the latest change whatever order two calls arrive in."""

        with self._lock:
            self._listener = listener

    def take_changes(self) -> tuple[tuple[RegisteredMcpServer, ...], ...]:
        """The registrations after each change since the last call, oldest
        first, as copies: one entry per ``register`` and per ``unregister``
        that removed a server, whether or not anybody listened. Each was
        taken in the step that made the change, so the entries are in the
        order of the changes whatever threads made them. What ``load``
        registered is not a change."""

        with self._lock:
            changes, self._changes = self._changes, []
        # Copied here, not under the lock: copying a configuration can run
        # an extension's code, which may call the registry.
        return tuple(tuple(_copy(server) for server in change) for change in changes)

    def _changed(self) -> Callable[[], None] | None:
        """Note the change that was just made and return the listener to
        call for it. The lock is held. The note has the stored
        registrations themselves, which are never changed, only replaced."""

        self._changes.append(tuple(self._servers.values()))
        return self._listener

    def reset(self) -> None:
        """Go back to what the extensions registered while they loaded and
        forget the changes nobody took: another session replaced this one
        in the run, and Pi's new runtime loads the extensions again, so a
        server registered later is there only when the new session's
        handlers register it again. The listener is not called: the
        session starts its servers again from the registry. Does nothing
        once the registry is closed."""

        with self._lock:
            if self._closed:
                return
            self._servers = dict(self._loaded)
            self._changes = []

    def close(self) -> None:
        """Refuse every change from now on, call the listener no more and
        forget the changes nobody took. The registrations can still be
        read."""

        with self._lock:
            self._closed = True
            self._listener = None
            self._changes = []

    def _check_open(self) -> None:
        if self._closed:
            raise McpServerRegistrationError(
                "MCP servers can no longer be changed here: "
                "the extensions were loaded again"
            )
