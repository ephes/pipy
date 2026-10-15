"""Tools and tool renderer resolvers that extensions register after they
loaded (Pi ``registerTool`` outside the extension factory, followed by
``refreshTools``, and ``registerToolRenderer``).

One registry belongs to one extension generation. It knows the extensions'
load order and which extension registered each tool name while the
generation loaded, keeps the latest registration of every tool registered
since, and tells the session after each change, which then publishes the
generation's tools again. An extension may register a new name or one of
its own names again (with ``exposure="hidden"`` to withdraw a tool). Of
two extensions with the same tool name the one that loaded first has it,
as in Pi's ``getAllRegisteredTools``: the other's registration is left out
silently, also when it came first.

The registry also holds the generation's tool renderer resolvers, in Pi's
order: by extension in load order, and in the order each extension
registered them, so one registered late joins its extension's others.
"""

from __future__ import annotations

import bisect
import threading
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from typing import overload

from pipy_harness.native.extension_types import (
    ExtensionTool,
    RegisteredTool,
    ToolRendererResolver,
)
from pipy_harness.native.extensions.command_context import ExtensionCapabilityError

_CLOSED = (
    "tools can no longer be registered here: "
    "the extensions were loaded again or their session ended"
)


class LiveToolRegistry:
    """The tool registrations and tool renderer resolvers of one extension
    generation after it loaded.

    ``renderer_groups`` names every extension of the generation in load
    order, each with the resolvers it registered while loading. An
    extension it does not name counts as loaded after all of them.
    """

    def __init__(
        self,
        registered: Iterable[RegisteredTool] = (),
        *,
        renderer_groups: Iterable[tuple[str, Iterable[ToolRendererResolver]]] = (),
    ) -> None:
        self._lock = threading.Lock()
        self._loaded = tuple(registered)
        # Which extension has each name: the earliest-loaded one that
        # registered it.
        self._owners: dict[str, str] = {
            item.tool.name: item.extension for item in self._loaded
        }
        self._rank: dict[str, int] = {}
        # The resolvers in Pi's order, each with its extension's rank, which
        # keeps the list sorted by rank.
        self._renderers: list[tuple[int, ToolRendererResolver]] = []
        for name, resolvers in renderer_groups:
            rank = self._rank.setdefault(name, len(self._rank))
            self._renderers.extend((rank, resolver) for resolver in resolvers)
        # The latest registration of each name registered after loading, in
        # the order the names were first registered.
        self._live: dict[str, RegisteredTool] = {}
        self._listener: Callable[[], None] | None = None
        self._closed = False

    def _rank_of(self, extension: str) -> int:
        return self._rank.get(extension, len(self._rank))

    def register(self, registered: RegisteredTool) -> None:
        """Take ``registered`` and tell the listener, unless an extension
        that loaded earlier has the name (or one that did not load earlier
        registered it first): then nothing changes. Raises
        ``ExtensionCapabilityError`` once the registry is closed; the
        listener may raise it too."""

        name = registered.tool.name
        extension = registered.extension
        with self._lock:
            if self._closed:
                raise ExtensionCapabilityError(_CLOSED)
            owner = self._owners.get(name)
            if (
                owner is not None
                and owner != extension
                and self._rank_of(owner) <= self._rank_of(extension)
            ):
                return
            self._owners[name] = extension
            self._live[name] = registered
            listener = self._listener
        if listener is not None:
            listener()

    def register_tool_renderer(
        self, extension: str, resolver: ToolRendererResolver
    ) -> None:
        """Add ``resolver`` after the other resolvers of ``extension`` (Pi
        ``registerToolRenderer``). Raises ``ExtensionCapabilityError`` once
        the registry is closed."""

        with self._lock:
            if self._closed:
                raise ExtensionCapabilityError(_CLOSED)
            rank = self._rank_of(extension)
            index = bisect.bisect_right(
                self._renderers, rank, key=lambda entry: entry[0]
            )
            self._renderers.insert(index, (rank, resolver))

    def tool_renderers(self) -> tuple[ToolRendererResolver, ...]:
        """The generation's tool renderer resolvers now, in Pi's order."""

        with self._lock:
            return tuple(resolver for _, resolver in self._renderers)

    def registrations(self) -> tuple[RegisteredTool, ...]:
        """What was registered after loading, the latest per name."""

        with self._lock:
            return tuple(self._live.values())

    def tools(self) -> tuple[RegisteredTool, ...]:
        """Every tool of the generation now: the loaded ones, each replaced
        by its latest registration, then the names registered later."""

        with self._lock:
            live = dict(self._live)
        loaded = [live.pop(item.tool.name, item) for item in self._loaded]
        return (*loaded, *live.values())

    def close(self) -> None:
        """Refuse every registration from now on: the generation was
        replaced (``/reload``)."""

        with self._lock:
            self._closed = True
            self._listener = None

    def set_change_listener(self, listener: Callable[[], None] | None) -> None:
        """Call ``listener`` after every registration from now on, on the
        thread that registered. It reads the registry itself, so it finds
        the latest registrations whatever order two calls arrive in."""

        with self._lock:
            self._listener = listener


class LiveToolRenderers(Mapping[str, ExtensionTool]):
    """The tools of a generation that have a renderer, by name, as they are
    registered now: a re-registered tool draws with its latest renderers."""

    def __init__(self, registry: LiveToolRegistry) -> None:
        self._registry = registry

    def _current(self) -> dict[str, ExtensionTool]:
        return {
            item.tool.name: item.tool
            for item in self._registry.tools()
            if item.tool.render_call is not None or item.tool.render_result is not None
        }

    def __getitem__(self, name: str) -> ExtensionTool:
        return self._current()[name]

    def __iter__(self) -> Iterator[str]:
        return iter(self._current())

    def __len__(self) -> int:
        return len(self._current())


class LiveToolRendererResolvers(Sequence[ToolRendererResolver]):
    """The tool renderer resolvers of a generation as they are registered
    now: one an extension registers while the session runs is asked from
    then on. Read it once per lookup (``tuple(view)``); every reading may
    see a later registration."""

    def __init__(self, registry: LiveToolRegistry) -> None:
        self._registry = registry

    @overload
    def __getitem__(self, index: int) -> ToolRendererResolver: ...

    @overload
    def __getitem__(self, index: slice) -> Sequence[ToolRendererResolver]: ...

    def __getitem__(
        self, index: int | slice
    ) -> ToolRendererResolver | Sequence[ToolRendererResolver]:
        return self._registry.tool_renderers()[index]

    def __len__(self) -> int:
        return len(self._registry.tool_renderers())

    def __iter__(self) -> Iterator[ToolRendererResolver]:
        return iter(self._registry.tool_renderers())


__all__ = ["LiveToolRegistry", "LiveToolRendererResolvers", "LiveToolRenderers"]
