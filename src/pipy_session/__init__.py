"""Session recording helpers for pipy."""

from pipy_session.auto_capture import (
    AutoCaptureState,
    HookResult,
    PrunedState,
    append_auto_event,
    handle_claude_hook,
    prune_auto_capture_state,
    start_auto_capture,
    state_dir,
    stop_auto_capture,
)
from pipy_session.catalog import (
    FinalizedSessionInspection,
    FinalizedSessionListing,
    inspect_finalized_session,
    list_finalized_sessions,
    resolve_finalized_record,
)
from pipy_session.recorder import (
    FILENAME_RE,
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
    "FILENAME_RE",
    "FinalizedRecordError",
    "FinalizedSessionInspection",
    "FinalizedSessionListing",
    "HookResult",
    "PrunedState",
    "SessionRecord",
    "append_auto_event",
    "append_event",
    "finalize_session",
    "handle_claude_hook",
    "init_session",
    "inspect_finalized_session",
    "list_finalized_sessions",
    "prune_auto_capture_state",
    "resolve_active_path",
    "resolve_finalized_record",
    "resolve_session_root",
    "start_auto_capture",
    "state_dir",
    "stop_auto_capture",
]
