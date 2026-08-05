"""Named synchronization boundary shared by one native REPL session."""

from __future__ import annotations

import threading
from typing import NewType

SessionStateLock = NewType("SessionStateLock", threading.RLock)
"""The one reentrant state lock composed for a production REPL run.

The newtype has no default construction path: callers must explicitly provide
an existing ``threading.RLock``. At runtime it preserves that lock's identity.
"""
