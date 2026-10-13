"""What the MCP integration knows about resources apart from a connection.

Pi ``extensions/mcp/resources.ts``. MCP App resources (``ui://`` URIs and
``profile=mcp-app`` HTML) are user interfaces for hosts that render them, so
they are left out of what pipy lists and counts.
"""

from __future__ import annotations

import re
import threading
from collections.abc import Callable, Mapping, Sequence
from typing import Final, TypeVar

from pipy_harness.native.fuzzy import JS_WHITESPACE

T = TypeVar("T")

_JS_SPACE: Final = "[" + "".join(sorted(re.escape(ch) for ch in JS_WHITESPACE)) + "]*"
_MCP_APP_PROFILE: Final = re.compile(
    f';{_JS_SPACE}profile{_JS_SPACE}={_JS_SPACE}"?mcp-app"?', re.IGNORECASE
)


def is_mcp_app_resource(item: Mapping[str, object]) -> bool:
    """MCP App user interfaces, which only hosts that render them can use."""

    uri = item.get("uri")
    if uri is None:
        uri = item.get("uriTemplate")
    mime_type = item.get("mimeType")
    return (isinstance(uri, str) and uri.startswith("ui://")) or (
        isinstance(mime_type, str) and _MCP_APP_PROFILE.search(mime_type) is not None
    )


def settle_all(calls: Sequence[Callable[[], T]]) -> list[T | Exception]:
    """Run ``calls`` at the same time and wait for all of them.

    Returns, in the order of the calls, what each returned or the exception
    it raised (``Promise.allSettled``). The first call runs on the caller's
    thread, each other one on a daemon thread of its own; a call for which
    no thread can be started runs on the caller's thread as well.
    """

    results: list[T | Exception] = [RuntimeError("not run")] * len(calls)

    def run(index: int) -> None:
        try:
            results[index] = calls[index]()
        except Exception as error:  # noqa: BLE001 - the exception is the call's result
            results[index] = error

    threads: list[threading.Thread] = []
    for index in range(1, len(calls)):
        thread = threading.Thread(
            target=run, args=(index,), name="mcp-request", daemon=True
        )
        try:
            thread.start()
        except RuntimeError:
            run(index)
        else:
            threads.append(thread)
    if calls:
        run(0)
    for thread in threads:
        thread.join()
    return results
