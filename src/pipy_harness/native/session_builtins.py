"""The optional built-ins of an embedded session: ``codemode``,
``tool_search`` and MCP.

The ``pipy`` command has all three, as its settings and flags say. Pi loads
them as built-in extensions there, and an SDK session has none of them until
the embedder adds their factories (Pi ``createCodemodeExtension``,
``createToolSearchExtension``, ``createMcpExtension``). ``SessionBuiltins`` is
that choice for one session; a session without one is the command's.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Protocol

from pipy_harness.native.coding.codemode_description import (
    CodemodeLoadout,
    CodemodeMode,
)
from pipy_harness.native.mcp.options import McpOptions

_CODEMODE_MODES = ("on", "only")


@dataclass(frozen=True, slots=True)
class CodemodeOptions:
    """Pi ``CodemodeExtensionOptions``: what replaces the ``codemode``
    settings of one session. ``None`` leaves the setting in force."""

    # Replaces the ``codemode.mode`` setting.
    mode: CodemodeMode | None = None
    # Replaces the ``codemode.inlineBudget`` setting.
    inline_budget: float | None = None

    def __post_init__(self) -> None:
        if self.mode is not None and self.mode not in _CODEMODE_MODES:
            raise ValueError('mode must be "on" or "only"')
        budget = self.inline_budget
        if budget is None:
            return
        if isinstance(budget, bool) or not isinstance(budget, (int, float)):
            raise TypeError("inline_budget must be a number")
        if not (math.isfinite(budget) and budget >= 0):
            raise ValueError("inline_budget must be finite and not negative")


@dataclass(frozen=True, slots=True)
class SessionBuiltins:
    """Which optional built-ins one embedded session has. The default is
    none of them."""

    # ``codemode`` can be activated: by the ``defaultTools`` setting, or by
    # MCP for a server with ``codemode`` tools.
    codemode: CodemodeOptions | None = None
    # ``tool_search`` can be activated the same way, for ``deferred`` tools.
    tool_search: bool = False
    # The session has MCP servers.
    mcp: McpOptions | None = None

    @property
    def tool_names(self) -> frozenset[str]:
        """The optional built-in tools the session may register."""

        names = set()
        if self.codemode is not None:
            names.add("codemode")
        if self.tool_search:
            names.add("tool_search")
        return frozenset(names)


class _CodemodeSettings(Protocol):
    def get_codemode_mode(self) -> CodemodeMode: ...

    def get_codemode_inline_budget(self) -> float: ...


def codemode_loadout(
    settings: _CodemodeSettings, builtins: SessionBuiltins | None
) -> CodemodeLoadout:
    """How ``codemode`` presents the tools, as the settings say now and the
    embedder's options replace them."""

    options = None if builtins is None else builtins.codemode
    mode = None if options is None else options.mode
    budget = None if options is None else options.inline_budget
    return CodemodeLoadout(
        mode=settings.get_codemode_mode() if mode is None else mode,
        inline_budget=(
            settings.get_codemode_inline_budget() if budget is None else budget
        ),
    )
