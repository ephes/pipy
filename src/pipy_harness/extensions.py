"""Public Pipy extension API surface.

This is the stable import path for Python extensions (the path the
extension examples in `docs/extension-api.md` import from). It re-exports
the activation-time API and the discovery/activation value objects from
the pipy-owned native runtime, so extension authors depend on
`pipy_harness.extensions` rather than internal module layout.

    from pipy_harness.extensions import PipyExtensionAPI

    def activate(api: PipyExtensionAPI) -> None:
        api.register_command("hello", "Print a greeting", _hello)

The surface grows by slice: slice 2 supports `register_command` only.
Tool / hook / provider / UI registration land in later slices.
"""

from __future__ import annotations

from pipy_harness.native.extension_runtime import (
    ActivatedExtension,
    PipyExtensionAPI,
    RegisteredCommand,
    activate_extensions,
    safe_activation_metadata,
)
from pipy_harness.native.extensions import (
    ExtensionDescriptor,
    discover_extensions,
    safe_extension_metadata,
)

__all__ = [
    "PipyExtensionAPI",
    "RegisteredCommand",
    "ActivatedExtension",
    "activate_extensions",
    "safe_activation_metadata",
    "ExtensionDescriptor",
    "discover_extensions",
    "safe_extension_metadata",
]
