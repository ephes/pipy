"""What an embedder may replace of a session's MCP support
(Pi ``McpExtensionOptions``).

Every field left at ``None`` is what the ``pipy`` command uses.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from pipy_harness.native.mcp.config import LoadedMcpConfig
from pipy_harness.native.mcp.connection import McpTransportFactory
from pipy_harness.native.mcp.credentials import McpOAuthCredentialStore
from pipy_harness.native.mcp.manager import UpdateMcpConfig


class McpConfigLoader(Protocol):
    """Returns the servers of a session (``load_mcp_config``)."""

    def __call__(
        self, *, config_home: Path, cwd: Path, project_trusted: bool
    ) -> LoadedMcpConfig: ...


@dataclass(frozen=True, slots=True)
class McpOptions:
    """The MCP support of one session."""

    # Called at the start of the session and by every reload. Default:
    # ``mcp.json`` of the config home and of the trusted project.
    load_config: McpConfigLoader | None = None
    # Default: stdio and streamable HTTP transports built from the server's
    # configuration.
    create_transport: McpTransportFactory | None = None
    # Default: ``mcp-auth.json`` in the config home.
    credentials: McpOAuthCredentialStore | None = None
    # The file server log messages are appended to. Default: ``mcp.log`` in
    # the config home.
    log_path: Path | None = None
    # Opens the authorization URL of a sign-in. Default: the system's
    # browser in a terminal session, nothing otherwise.
    open_url: Callable[[str], None] | None = None
    # Saves a change ``/mcp`` made to a server. Default: editing the
    # ``mcp.json`` that defines or overrides the server.
    update_config: UpdateMcpConfig | None = None
    # How long the first prompt waits for servers with ``direct`` tools that
    # are still connecting. Default: 10 seconds.
    startup_wait_seconds: float | None = None

    def __post_init__(self) -> None:
        wait = self.startup_wait_seconds
        if wait is None:
            return
        if isinstance(wait, bool) or not isinstance(wait, (int, float)):
            raise TypeError("startup_wait_seconds must be a number")
        if not (math.isfinite(wait) and wait >= 0):
            raise ValueError("startup_wait_seconds must be finite and not negative")
