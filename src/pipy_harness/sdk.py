"""Public product embedding: existing sync SDK and opt-in async durable API.

``create_product_session`` constructs the full-content native coding lifetime
with an explicitly supplied tool-capable provider. ``open_product_session``
reopens one exact durable native-session file under an explicit workspace. Their
construction-thread APIs support repeated submissions, canonical observation,
idle immutable snapshots, cross-thread cancellation and explicit disposal
without a workflow archive. The new durable factories use one native SQLite
Session/scheduler, admission-only submit and explicit asynchronous resume.
"""

from __future__ import annotations

from pipy_harness.durable_product_api import (
    DurableProductSession,
    create_durable_product_session,
    open_durable_product_session,
)
from pipy_harness.native.agent import AgentEvent, AgentEventSink
from pipy_harness.native.agent.nested_record import NestedCallEvidence, NestedCallRecord
from pipy_harness.native.agent.tool_details import (
    LEGACY_TOOL_DETAILS_NONE,
    NO_TOOL_DETAILS,
    UNCHANGED_TOOL_DETAILS,
)
from pipy_harness.native.coding.state import CodingSessionResultSnapshot
from pipy_harness.native.durable.product_values import (
    DurableAdmission,
    DurableProductError,
    DurableProviderProfile,
    DurableRunResult,
    DurableRunSelection,
    UnsupportedDurableCapability,
)
from pipy_harness.native.mcp.options import McpOptions
from pipy_harness.native.provider import ProviderPort
from pipy_harness.native.session_builtins import CodemodeOptions
from pipy_harness.product_api import (
    ProductSession,
    ProductSessionTarget,
    ProductSessionTransitionError,
    ProductSessionTransitionFailure,
    ProductSessionTransitionResult,
    create_product_session,
    open_product_session,
)

__all__ = [
    "NO_TOOL_DETAILS",
    "LEGACY_TOOL_DETAILS_NONE",
    "UNCHANGED_TOOL_DETAILS",
    "NestedCallEvidence",
    "NestedCallRecord",
    "DurableProductSession",
    "DurableAdmission",
    "DurableProductError",
    "DurableProviderProfile",
    "DurableRunResult",
    "DurableRunSelection",
    "UnsupportedDurableCapability",
    "create_durable_product_session",
    "open_durable_product_session",
    "AgentEvent",
    "AgentEventSink",
    "CodemodeOptions",
    "CodingSessionResultSnapshot",
    "McpOptions",
    "ProductSession",
    "ProductSessionTarget",
    "ProductSessionTransitionError",
    "ProductSessionTransitionFailure",
    "ProductSessionTransitionResult",
    "ProviderPort",
    "create_product_session",
    "open_product_session",
]
