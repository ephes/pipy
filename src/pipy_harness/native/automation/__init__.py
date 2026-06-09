"""Pi-compatible headless automation transports for pipy-native.

This package implements the `--mode json` full-event stream, the `--print`/`-p`
one-shot text path, and the `--mode rpc` stdin/stdout JSONL protocol described
in ``docs/automation-rpc.md``. The transports reuse the real native runtime
(the tool loop and the native session tree) and serialize its observed events
onto Pi's `AgentSessionEvent` JSON vocabulary; they do not fork the runtime or
maintain a parallel session model.

These are full-content automation surfaces: assistant text, tool arguments,
tool results, and bash output are emitted like Pi. Only auth secrets/tokens are
ever withheld. The metadata-first ``pipy-session`` archive contract is
unaffected.
"""

from __future__ import annotations

from pipy_harness.native.automation.events import (
    AutomationEmitter,
    AutomationEventSink,
)
from pipy_harness.native.automation.jsonl import (
    JsonlLineBuffer,
    JsonlWriter,
    serialize_json_line,
)
from pipy_harness.native.automation.serialize import serialize_message

__all__ = [
    "AutomationEmitter",
    "AutomationEventSink",
    "JsonlLineBuffer",
    "JsonlWriter",
    "serialize_json_line",
    "serialize_message",
]
