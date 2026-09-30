"""Pi ``shortHash`` parity for provider-native tool-load ids.

Tool loads are transcript system messages since SYS1b; their per-adapter
serialization is covered by ``tests/test_native_provider_system_messages.py``.
"""

from pipy_harness.native.deferred_tools import short_hash


def test_short_hash_matches_pi_utf16_vectors() -> None:
    assert short_hash("call_abc:late_tool") == "1o0l89w1i7wxtx"
    assert short_hash("call_abc|fc_abc:late_tool") == "xvuydyik9a48"
    assert short_hash("call_loader:late_tool,later_tool") == "dulyo1k6qd28"
    assert short_hash("call_😀:late_tool") == "1ee5wtp1l226u7"
