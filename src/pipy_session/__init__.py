"""Session recording helpers for pipy."""

from pipy_session.auto_capture import (
    AutoCaptureState,
    HookResult,
    append_auto_event,
    handle_claude_hook,
    start_auto_capture,
    state_dir,
    stop_auto_capture,
)
from pipy_session.recorder import (
    PROJECT_NAME,
    FinalizedRecordError,
    SessionRecord,
    append_event,
    finalize_session,
    init_session,
    resolve_active_path,
    resolve_session_root,
)

__all__ = [
    "PROJECT_NAME",
    "AutoCaptureState",
    "FinalizedRecordError",
    "HookResult",
    "SessionRecord",
    "append_auto_event",
    "append_event",
    "finalize_session",
    "handle_claude_hook",
    "init_session",
    "resolve_active_path",
    "resolve_session_root",
    "start_auto_capture",
    "state_dir",
    "stop_auto_capture",
]
