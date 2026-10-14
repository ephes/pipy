"""String formats as TypeBox's ``Format`` registry checks them (goal check M11).

The cases follow ``typebox/build/format`` at the version pi-mono locks.
"""

from __future__ import annotations

import pytest

from pipy_harness.native.tools.json_formats import UNCHECKED_FORMATS, format_matches


@pytest.mark.parametrize(
    ("name", "value", "expected"),
    [
        ("date", "2024-02-29", True),
        ("date", "2023-02-29", False),
        ("date", "2000-02-29", True),
        ("date", "1900-02-29", False),
        ("date", "2024-13-01", False),
        ("date", "2024-1-01", False),
        ("time", "20:20:40Z", True),
        ("time", "20:20:40.5+01:00", True),
        ("time", "20:20:40", False),  # a time zone is required
        ("time", "23:59:60Z", True),  # a leap second at 23:59:60 UTC
        ("time", "22:59:60-01:00", True),
        ("time", "12:00:60Z", False),
        ("time", "24:00:00Z", False),
        ("date-time", "2020-12-12T20:20:40+00:00", True),
        ("date-time", "2020-12-12t20:20:40z", True),
        ("date-time", "2020-12-12 20:20:40Z", False),
        ("duration", "P1Y2M3DT4H5M6S", True),
        ("duration", "P1W", True),
        ("duration", "PT", False),
        ("email", "first.last@example.com", True),
        ("email", "a@[127.0.0.1]", True),
        ("email", "no-at-sign", False),
        ("ipv4", "192.168.0.1", True),
        ("ipv4", "192.168.0.01", False),
        ("ipv6", "::1", True),
        ("ipv6", "fe80::1:2:3:4", True),
        ("ipv6", "1:2:3:4:5:6:7:8:9", False),
        ("uuid", "123e4567-E89B-12d3-a456-426614174000", True),
        ("uuid", "123e4567e89b12d3a456426614174000", False),
        ("uri", "https://example.com/a?b#c", True),
        ("uri", "/relative", False),
        ("uri-reference", "/relative", True),
        ("uri-reference", "a b", False),
        ("json-pointer", "/a/b~1c", True),
        ("json-pointer", "a", False),
        ("relative-json-pointer", "1/a", True),
        ("json-pointer-uri-fragment", "#/a", True),
        ("uri-template", "/users/{id}", True),
        ("uri-template", "/users/{id", False),
        # ASCII digits only, as JavaScript's \\d.
        ("date", "２０２４-02-29", False),
        # The whole string, not a prefix or a line.
        ("ipv4", "10.0.0.1\n", False),
    ],
)
def test_formats_are_checked_as_typebox_checks_them(
    name: str, value: str, expected: bool
) -> None:
    assert format_matches(name, value) is expected


@pytest.mark.parametrize("name", sorted(UNCHECKED_FORMATS) + ["x-unknown"])
def test_an_unchecked_or_unknown_format_matches(name: str) -> None:
    assert format_matches(name, "anything at all") is True
