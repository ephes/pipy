"""Narrow verified interface for partial-json-parser (no upstream py.typed)."""

from collections.abc import Callable

def loads(
    json_string: str,
    allow_partial: int = ...,
    parser: Callable[[str], object] | None = ...,
    use_fast_fix: bool = ...,
) -> object: ...
