"""Target-safe tool correlation identifiers for cross-provider replay."""

from __future__ import annotations

import base64
import hashlib
import re

_PORTABLE_TOOL_CORRELATION_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def portable_tool_correlation_id(value: str) -> str:
    """Compile one canonical correlation ID for a shared provider wire.

    Safe values preserve their exact bytes. Unsafe provider-specific values use
    a stateless digest encoding, so paired call/result IDs always compile to the
    same target-safe value without changing canonical history.
    """

    if _PORTABLE_TOOL_CORRELATION_ID.fullmatch(value):
        return value
    digest = hashlib.sha256(value.encode("utf-8")).digest()
    encoded = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return f"tc_{encoded}"
