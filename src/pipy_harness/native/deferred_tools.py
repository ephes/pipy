"""Deterministic ids for provider-native tool loads.

Tool additions are declared by transcript system messages (Pi ``9e05370b2``)
and anchored per adapter (``providers/transcript.py``); Pi removed the older
tool-result load markers.
"""

from __future__ import annotations


def short_hash(value: str) -> str:
    """Port Pi's deterministic two-accumulator JavaScript ``shortHash``."""

    mask = 0xFFFFFFFF
    h1 = 0xDEADBEEF
    h2 = 0x41C6CE57
    encoded = value.encode("utf-16-le", errors="surrogatepass")
    for index in range(0, len(encoded), 2):
        code_unit = encoded[index] | (encoded[index + 1] << 8)
        h1 = ((h1 ^ code_unit) * 2654435761) & mask
        h2 = ((h2 ^ code_unit) * 1597334677) & mask
    h1 = (((h1 ^ (h1 >> 16)) * 2246822507) ^ ((h2 ^ (h2 >> 13)) * 3266489909)) & mask
    h2 = (((h2 ^ (h2 >> 16)) * 2246822507) ^ ((h1 ^ (h1 >> 13)) * 3266489909)) & mask
    return _base36(h2) + _base36(h1)


def _base36(value: int) -> str:
    alphabet = "0123456789abcdefghijklmnopqrstuvwxyz"
    if value == 0:
        return "0"
    digits: list[str] = []
    while value:
        value, remainder = divmod(value, 36)
        digits.append(alphabet[remainder])
    return "".join(reversed(digits))
