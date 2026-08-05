from __future__ import annotations

import threading
from collections.abc import Mapping

from pipy_harness.native.extension_chrome_state import ExtensionChromeSink
from pipy_harness.native.extension_runtime import (
    RegisteredTool,
    _ExtensionRuntime,
)
from pipy_harness.native.extensions.tool_port import _ExtensionToolPort
from pipy_harness.native.session_generation import (
    ExtensionChromeHandle,
    ExtensionProjection,
    ProjectionStepObserver,
    build_extension_projection,
)
from pipy_harness.native.tool_capabilities import ToolCapabilityState, ToolFilterOptions
from pipy_harness.native.tools import ToolPort


def build_test_projection(
    runtime: _ExtensionRuntime,
    flag_values: Mapping[str, object],
    *,
    queue_mutex: threading.RLock,
    reference_mutex: threading.RLock | None = None,
    chrome: ExtensionChromeSink | None = None,
    step_observer: ProjectionStepObserver | None = None,
) -> ExtensionProjection:
    """Build a detached projection with focused, effect-free test adapters."""

    def build_tool_port(
        registered: RegisteredTool, flags: Mapping[str, object]
    ) -> ToolPort:
        return _ExtensionToolPort(
            registered,
            has_ui=False,
            notify_sink=lambda *_args: None,
            set_active_tools_fn=lambda _generation_id, _names: True,
            flags=flags,
            render_details_sink={},
            project_trusted=True,
        )

    def build_capability(ports: Mapping[str, ToolPort]) -> ToolCapabilityState:
        return ToolCapabilityState.build(
            {},
            ports,
            filter_options=ToolFilterOptions.empty(),
            cancel_join_timeout_seconds=1.0,
        )

    return build_extension_projection(
        runtime,
        flag_values,
        queue_mutex=queue_mutex,
        reference_mutex=(queue_mutex if reference_mutex is None else reference_mutex),
        build_tool_port=build_tool_port,
        build_tool_capability=build_capability,
        chrome=(ExtensionChromeHandle(chrome) if chrome is not None else None),
        step_observer=step_observer,
    )
