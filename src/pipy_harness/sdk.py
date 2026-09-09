"""Public synchronous product-session embedding.

``create_product_session`` constructs the full-content native coding lifetime
with an explicitly supplied tool-capable provider. ``open_product_session``
reopens one exact durable native-session file under an explicit workspace. Their
construction-thread APIs support repeated submissions, canonical observation,
idle immutable snapshots, cross-thread cancellation and explicit disposal
without a workflow archive.
"""

from __future__ import annotations

from pipy_harness.native.agent import AgentEvent, AgentEventSink
from pipy_harness.native.coding.state import CodingSessionResultSnapshot
from pipy_harness.native.provider import ProviderPort
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
    "AgentEvent",
    "AgentEventSink",
    "CodingSessionResultSnapshot",
    "ProductSession",
    "ProductSessionTarget",
    "ProductSessionTransitionError",
    "ProductSessionTransitionFailure",
    "ProductSessionTransitionResult",
    "ProviderPort",
    "create_product_session",
    "open_product_session",
]
