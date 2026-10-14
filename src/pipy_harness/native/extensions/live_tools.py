"""Tools that extensions register after they loaded (Pi ``registerTool``
outside the extension factory, followed by ``refreshTools``).

One registry belongs to one extension generation. It knows which extension
registered each tool name while the generation loaded, keeps the latest
registration of every tool registered since, and tells the session after
each change, which then publishes the generation's tools again. An
extension may register a new name or one of its own names again (with
``exposure="hidden"`` to withdraw a tool); a name another extension
registered is refused, as it is while extensions load.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterable, Iterator, Mapping

from pipy_harness.native.extension_types import ExtensionTool, RegisteredTool
from pipy_harness.native.extensions.command_context import ExtensionCapabilityError

_CLOSED = (
    "tools can no longer be registered here: "
    "the extensions were loaded again or their session ended"
)


class LiveToolRegistry:
    """The tool registrations of one extension generation after it loaded."""

    def __init__(self, registered: Iterable[RegisteredTool] = ()) -> None:
        self._lock = threading.Lock()
        self._loaded = tuple(registered)
        # Which extension has each name; a later registration cannot take
        # another extension's name.
        self._owners: dict[str, str] = {
            item.tool.name: item.extension for item in self._loaded
        }
        # The latest registration of each name registered after loading, in
        # the order the names were first registered.
        self._live: dict[str, RegisteredTool] = {}
        self._listener: Callable[[], None] | None = None
        self._closed = False

    def register(self, registered: RegisteredTool) -> None:
        """Take ``registered`` and tell the listener. Raises
        ``ExtensionCapabilityError`` when another extension has the name and
        once the registry is closed; the listener may raise it too."""

        name = registered.tool.name
        with self._lock:
            if self._closed:
                raise ExtensionCapabilityError(_CLOSED)
            owner = self._owners.get(name)
            if owner is not None and owner != registered.extension:
                raise ExtensionCapabilityError(
                    f'tool "{name}" is already registered by extension "{owner}"'
                )
            self._owners[name] = registered.extension
            self._live[name] = registered
            listener = self._listener
        if listener is not None:
            listener()

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


__all__ = ["LiveToolRegistry", "LiveToolRenderers"]
