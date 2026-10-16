"""MCP server configuration: validation, loading and editing of ``mcp.json``.

Servers are read from ``mcp.json`` in the config home and, for trusted
projects, from ``<project>/.pipy/mcp.json``. Both use the ``mcpServers`` shape
shared by other MCP clients, so existing configurations can be copied over.
Project entries replace user-level entries with the same name.

A project entry without ``command``, ``url`` and ``type`` overrides only
``enabled``, ``exposure`` and ``toolExposure`` of the user-level server with
the same name, for example to turn it off in one project. The rest of the
user-level entry is kept, including credentials the project could not set
itself.

The top-level ``autoEnableCodemode`` (default true) activates the codemode
tool when a server with ``codemode`` exposure connects. A project value
overrides the user-level one.

A server config is the JSON object of its entry, with Pi's key names
(``toolExposure``, ``callbackUrl``). A key that is present with JSON ``null``
counts as present, as in Pi, so ``"exposure": null`` is an error and not the
default.

This module decides nothing about trust: the caller says whether the project
is trusted. Values such as ``${NAME}`` references are not resolved here; that
happens when a server connects.

Where this differs from Pi:

- URLs are read by ``parse_http_url``, which follows Node's WHATWG URL
  parser for ``http`` and ``https`` URLs (slashes and backslashes after the
  scheme, percent-decoded hosts, IDNA, IPv4 addresses such as ``127.1`` or
  ``0x7f.0.0.1``). A host with non-ASCII characters goes through UTS #46
  ToASCII with the ``idna`` package's tables and Python's ``unicodedata``,
  whose Unicode versions are not Node's, so a recent character may be
  judged differently; such a host longer than 1024 characters is refused.
  ``HttpUrl.path`` keeps characters a WHATWG parser percent-encodes.
- ``oauth.clientMetadataBaseUrl`` exists only in pipy and is required with
  ``oauth.clientRegistration: "cimd"``.
- The JSON parser messages are Python's.
- Entries are read in JavaScript's order, in which keys that look like array
  indexes (``"1"``, ``"20"``) come first, but a rewritten file keeps the
  order of its keys where ``JSON.stringify`` would move those keys up.
- A whole-number ``oauth.callbackPort`` written as ``8080.0`` is an ``int`` in
  the validated config.
- Rewritten files print numbers the way Python does (``1.0`` stays ``1.0``).
"""

from __future__ import annotations

import copy
import ipaddress
import json
import re
import unicodedata
import urllib.parse
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final, Literal

import idna

from pipy_harness.native.tools.name_patterns import (
    MCP_TOOL_NAME_PREFIX,
    create_tool_name_matcher,
)

MCP_CONFIG_FILENAME: Final = "mcp.json"
PROJECT_CONFIG_DIR_NAME: Final = ".pipy"

McpExposure = Literal["codemode", "deferred", "direct", "hidden"]
McpServerConfig = dict[str, Any]
McpConfigScope = Literal["global", "project", "extension"]

MCP_EXPOSURES: Final[tuple[str, ...]] = ("codemode", "deferred", "direct", "hidden")
DEFAULT_MCP_EXPOSURE: Final = "codemode"
# Older exposure names, accepted in configs and replaced by their current name
# when validated.
MCP_EXPOSURE_ALIASES: Final[Mapping[str, str]] = {"codemode-deferred": "codemode"}

OVERRIDE_KEYS: Final[tuple[str, ...]] = ("enabled", "exposure", "toolExposure")
LOOPBACK_HOSTS: Final[tuple[str, ...]] = ("localhost", "127.0.0.1", "[::1]")

_SERVER_NAME: Final = re.compile(r"[A-Za-z0-9_-]+")
_ARRAY_INDEX: Final = re.compile(r"0|[1-9][0-9]{0,9}")
_STRUCTURE_ERROR: Final = 'expected an object with an "mcpServers" object'
# What JavaScript's ``String.prototype.trim`` removes.
_JS_WHITESPACE: Final = (
    "\t\n\v\f\r \u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007"
    "\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000\ufeff"
)
# JSON.stringify uses at most this many characters of an indent string.
_MAX_INDENT_CHARS: Final = 10


class McpConfigError(Exception):
    """An ``mcp.json`` could not be edited: it is not valid, or lacks the server."""


@dataclass(frozen=True, slots=True)
class McpServerEntry:
    """One configured server."""

    name: str
    config: McpServerConfig
    # Config file that defined the entry, or the path of the extension that
    # registered it.
    source: str
    scope: McpConfigScope | None = None
    # Project ``mcp.json`` with an override of this user-level server's
    # ``enabled``, ``exposure`` or ``toolExposure``.
    override: str | None = None


def configured_exposures(entry: McpServerEntry) -> frozenset[str]:
    """Exposures the server's tools can have (Pi ``configuredExposures``).

    Known from the configuration before the server connects: the server's
    exposure and every ``toolExposure`` value.
    """

    config = entry.config
    exposure = config.get("exposure")
    exposures = {DEFAULT_MCP_EXPOSURE if exposure is None else str(exposure)}
    overrides = config.get("toolExposure")
    if isinstance(overrides, Mapping):
        exposures.update(str(value) for value in overrides.values())
    return frozenset(exposures)


@dataclass(frozen=True, slots=True)
class LoadedMcpConfig:
    """The servers of the user-level and (when trusted) the project file."""

    servers: list[McpServerEntry]
    errors: list[str]
    # None when no file sets it; the default is true.
    auto_enable_codemode: bool | None = None
    # The project ``mcp.json`` when the project is trusted, whether or not the
    # file exists. Project overrides are saved there.
    project_config: str | None = None


def _is_array_index(key: str) -> bool:
    return _ARRAY_INDEX.fullmatch(key) is not None and int(key) < 2**32 - 1


def _js_key_order(keys: Iterable[str]) -> list[str]:
    """Keys in the order JavaScript enumerates them.

    Keys that are array indexes (``"0"``, ``"12"``) come first, in ascending
    order, and the others follow in the order they were written. Which entry
    is looked at first decides which error is reported.
    """

    listed = list(keys)
    indexes = sorted(filter(_is_array_index, listed), key=int)
    return indexes + [key for key in listed if not _is_array_index(key)]


def mcp_namespace(server: str) -> str:
    """Namespace of a server's tools: ``mcp__<server>`` with ``-`` as ``_``."""

    return MCP_TOOL_NAME_PREFIX + server.replace("-", "_")


def get_mcp_tool_exposure(config: Mapping[str, Any], tool_name: str) -> str:
    """Exposure of one tool: its ``toolExposure`` entry, else the server's.

    An exact name wins over patterns; among patterns the first match in the
    object wins.
    """

    overrides = config.get("toolExposure")
    if isinstance(overrides, dict):
        exact = overrides.get(tool_name)
        if exact is not None:
            return str(exact)
        for pattern, exposure in overrides.items():
            if "*" in pattern and create_tool_name_matcher((pattern,))(tool_name):
                return str(exposure)
    exposure = config.get("exposure")
    return DEFAULT_MCP_EXPOSURE if exposure is None else str(exposure)


@dataclass(frozen=True, slots=True)
class HttpUrl:
    """The parts of an ``http`` or ``https`` URL, as ``parse_http_url`` reads it."""

    scheme: str
    # ASCII and lower case (punycode for a non-ASCII domain); an IPv6
    # address is compressed and in brackets.
    hostname: str
    # None without a port and for the scheme's default port.
    port: int | None
    path: str
    query: str
    fragment: str
    # Whether the URL names a user or a password. They are not kept.
    has_user_info: bool = False

    @property
    def origin(self) -> str:
        port = "" if self.port is None else f":{self.port}"
        return f"{self.scheme}://{self.hostname}{port}"

    @property
    def pathname(self) -> str:
        """The path without dot segments, which is the path a request asks for.

        As in a WHATWG URL parser, ``%2e`` in either letter case is a dot.
        """

        segments = self.path.split("/")[1:]
        kept: list[str] = []
        for index, segment in enumerate(segments):
            dots = segment.lower().replace("%2e", ".")
            if dots not in (".", ".."):
                kept.append(segment)
                continue
            if dots == ".." and kept:
                kept.pop()
            if index == len(segments) - 1:
                kept.append("")
        return "/" + "/".join(kept)

    @property
    def href(self) -> str:
        """Origin, ``pathname`` and query: the URL without user info and fragment."""

        query = f"?{self.query}" if self.query else ""
        return f"{self.origin}{self.pathname}{query}"


# re.ASCII keeps the case-insensitive match to ASCII letters.
_SCHEME: Final = re.compile(r"(https?):", re.IGNORECASE | re.ASCII)
# What ends the authority of an http or https URL.
_AUTHORITY_END: Final = re.compile(r"[/\\?#]")
_PORT: Final = re.compile(r"0*([0-9]{0,5})")
_FORBIDDEN_IN_HOST: Final = re.compile(r"[\x00-\x20\x7f#%/:<>?@\[\\\]^|]")
_IPV4_DIGITS: Final[Mapping[int, str]] = {
    8: "01234567",
    10: "0123456789",
    16: "0123456789abcdefABCDEF",
}
_DEFAULT_PORTS: Final[Mapping[str, int]] = {"http": 80, "https": 443}


def _parse_ipv4_number(text: str) -> int | None:
    """One part of an IPv4 host: decimal, ``0x`` hex or ``0`` octal."""

    if not text:
        return None
    radix = 10
    if text[:2] in ("0x", "0X"):
        text, radix = text[2:], 16
    elif len(text) > 1 and text.startswith("0"):
        text, radix = text[1:], 8
    if any(char not in _IPV4_DIGITS[radix] for char in text):
        return None
    digits = text.lstrip("0")
    # Too large for any part; also keeps int() away from very long input.
    if len(digits) > 12:
        return 2**64
    return int(digits or "0", radix)


def _ends_in_a_number(host: str) -> bool:
    labels = host.split(".")
    if labels[-1] == "":
        if len(labels) == 1:
            return False
        labels.pop()
    last = labels[-1]
    if last.isascii() and last.isdigit():
        return True
    return _parse_ipv4_number(last) is not None


def _parse_ipv4(host: str) -> str | None:
    """A host that ends in a number, as the IPv4 address it spells.

    The WHATWG URL parser's rules: one to four parts, each decimal, hex or
    octal, the last one filling the remaining bytes, so ``127.1`` and
    ``2130706433`` are both ``127.0.0.1``.
    """

    parts = host.split(".")
    if parts[-1] == "" and len(parts) > 1:
        parts.pop()
    if len(parts) > 4:
        return None
    numbers = [_parse_ipv4_number(part) for part in parts]
    if None in numbers:
        return None
    *leading, last = [number for number in numbers if number is not None]
    if any(number > 255 for number in leading) or last >= 256 ** (4 - len(leading)):
        return None
    address = last + sum(
        number * 256 ** (3 - index) for index, number in enumerate(leading)
    )
    return str(ipaddress.IPv4Address(address))


def _serialize_ipv6(address: ipaddress.IPv6Address) -> str:
    """An IPv6 address as a URL writes it: hex pieces, the first longest run
    of two or more zero pieces as ``::``, never a dotted IPv4 tail."""

    value = int(address)
    pieces = [(value >> (16 * (7 - index))) & 0xFFFF for index in range(8)]
    start, length = -1, 1
    index = 0
    while index < 8:
        end = index
        while end < 8 and pieces[end] == 0:
            end += 1
        if end - index > length:
            start, length = index, end - index
        index = max(end, index + 1)
    if start < 0:
        return ":".join(f"{piece:x}" for piece in pieces)
    head = ":".join(f"{piece:x}" for piece in pieces[:start])
    tail = ":".join(f"{piece:x}" for piece in pieces[start + length :])
    return f"{head}::{tail}"


_LTR_BIDI_CLASSES: Final = frozenset({"L", "EN", "ES", "CS", "ET", "ON", "BN", "NSM"})
_RTL_BIDI_CLASSES: Final = frozenset({"R", "AL", "AN", *_LTR_BIDI_CLASSES} - {"L"})


# Node's URL parser (ada 4.0.0, in Node 26) has its own tables of viramas
# and of joining types for the joiner rule. They are copied here, as code
# point ranges, because Unicode's newer and fuller tables accept labels Node
# refuses.
_VIRAMAS: Final[tuple[tuple[int, int], ...]] = (
    (0x094D, 0x094D),
    (0x09CD, 0x09CD),
    (0x0A4D, 0x0A4D),
    (0x0ACD, 0x0ACD),
    (0x0B4D, 0x0B4D),
    (0x0BCD, 0x0BCD),
    (0x0C4D, 0x0C4D),
    (0x0CCD, 0x0CCD),
    (0x0D3B, 0x0D3C),
    (0x0D4D, 0x0D4D),
    (0x0DCA, 0x0DCA),
    (0x0E3A, 0x0E3A),
    (0x0EBA, 0x0EBA),
    (0x0F84, 0x0F84),
    (0x1039, 0x103A),
    (0x1714, 0x1714),
    (0x1734, 0x1734),
    (0x17D2, 0x17D2),
    (0x1A60, 0x1A60),
    (0x1B44, 0x1B44),
    (0x1BAA, 0x1BAB),
    (0x1BF2, 0x1BF3),
    (0x2D7F, 0x2D7F),
    (0xA806, 0xA806),
    (0xA82C, 0xA82C),
    (0xA8C4, 0xA8C4),
    (0xA953, 0xA953),
    (0xA9C0, 0xA9C0),
    (0xAAF6, 0xAAF6),
    (0xABED, 0xABED),
    (0x10A3F, 0x10A3F),
    (0x11046, 0x11046),
    (0x1107F, 0x1107F),
    (0x110B9, 0x110B9),
    (0x11133, 0x11134),
    (0x111C0, 0x111C0),
    (0x11235, 0x11235),
    (0x112EA, 0x112EA),
    (0x1134D, 0x1134D),
    (0x11442, 0x11442),
    (0x114C2, 0x114C2),
    (0x115BF, 0x115BF),
    (0x1163F, 0x1163F),
    (0x116B6, 0x116B6),
    (0x1172B, 0x1172B),
    (0x11839, 0x11839),
    (0x1193D, 0x1193E),
    (0x119E0, 0x119E0),
    (0x11A34, 0x11A34),
    (0x11A47, 0x11A47),
    (0x11A99, 0x11A99),
    (0x11C3F, 0x11C3F),
    (0x11D44, 0x11D45),
    (0x11D97, 0x11D97),
)
_JOINING_TYPE_L_OR_D: Final[tuple[tuple[int, int], ...]] = (
    (0x0620, 0x0620),
    (0x0626, 0x0626),
    (0x0628, 0x0628),
    (0x062A, 0x062E),
    (0x0633, 0x063F),
    (0x0641, 0x0647),
    (0x0649, 0x064A),
    (0x066E, 0x066F),
    (0x0678, 0x0687),
    (0x069A, 0x06BF),
    (0x06C1, 0x06C2),
    (0x06CC, 0x06CC),
    (0x06CE, 0x06CE),
    (0x06D0, 0x06D1),
    (0x06FA, 0x06FC),
    (0x06FF, 0x06FF),
    (0x0712, 0x0714),
    (0x071A, 0x071D),
    (0x071F, 0x0727),
    (0x0729, 0x0729),
    (0x072B, 0x072B),
    (0x072D, 0x072E),
    (0x074E, 0x0758),
    (0x075C, 0x0766),
    (0x0850, 0x0853),
    (0x0855, 0x0855),
    (0x08A0, 0x08A0),
    (0x08A2, 0x08A9),
    (0x1807, 0x1807),
    (0x1820, 0x1877),
    (0x1887, 0x18A8),
    (0x18AA, 0x18AA),
    (0xA840, 0xA872),
)
_JOINING_TYPE_R_OR_D: Final[tuple[tuple[int, int], ...]] = (
    (0x0620, 0x0620),
    (0x0622, 0x063F),
    (0x0641, 0x064A),
    (0x066E, 0x066F),
    (0x0671, 0x0673),
    (0x0675, 0x06D3),
    (0x06D5, 0x06D5),
    (0x06EE, 0x06EF),
    (0x06FA, 0x06FC),
    (0x06FF, 0x06FF),
    (0x0710, 0x0710),
    (0x0712, 0x072F),
    (0x074D, 0x0766),
    (0x0850, 0x0855),
    (0x08A0, 0x08A0),
    (0x08A2, 0x08AC),
    (0x1807, 0x1807),
    (0x1820, 0x1877),
    (0x1887, 0x18A8),
    (0x18AA, 0x18AA),
    (0xA840, 0xA871),
)


def _in_ranges(char: str, ranges: tuple[tuple[int, int], ...]) -> bool:
    code_point = ord(char)
    return any(start <= code_point <= end for start, end in ranges)


def _satisfies_bidi_rule(label: str) -> bool:
    """RFC 5893's Bidi Rule, checked as Node's URL parser (ada) checks it:
    only for a label with an ``R``, ``AL`` or ``AN`` character, and up to
    the last character that is not ``NSM``. A character Python's Unicode
    data does not know (no class) fails, since its class is unknown."""

    classes = [unicodedata.bidirectional(char) for char in label]
    if "" in classes:
        return False
    last = next(
        (index for index in reversed(range(len(classes))) if classes[index] != "NSM"),
        None,
    )
    if last is None:
        return False
    if not {"R", "AL", "AN"} & set(classes):
        return True
    classes = classes[: last + 1]
    if classes[0] == "L":
        return set(classes) <= _LTR_BIDI_CLASSES and classes[-1] in ("L", "EN")
    return (
        classes[0] in ("R", "AL")
        and set(classes) <= _RTL_BIDI_CLASSES
        and not {"EN", "AN"} <= set(classes)
        and classes[-1] in ("R", "AL", "AN", "EN")
    )


def _is_valid_label(label: str) -> bool:
    """UTS #46 validity criteria for one non-ASCII label, as Node checks them.

    Node's URL parser (ada) has ``CheckHyphens`` and ``UseSTD3ASCIIRules``
    off. Where it departs from UTS #46, its behaviour is kept: the first
    joiner (U+200C, U+200D) decides the label, so one after a virama makes
    it valid without the Bidi Rule, and U+200C otherwise needs a left- or
    dual-joining character anywhere before it and a right- or dual-joining
    one anywhere after it.
    """

    if not label:
        return True
    if unicodedata.category(label[0]).startswith("M"):
        return False
    for index, char in enumerate(label):
        if char not in "\u200c\u200d":
            continue
        if index > 0 and _in_ranges(label[index - 1], _VIRAMAS):
            return True
        if char == "\u200d" or index in (0, len(label) - 1):
            return False
        return any(_in_ranges(c, _JOINING_TYPE_L_OR_D) for c in label[:index]) and any(
            _in_ranges(c, _JOINING_TYPE_R_OR_D) for c in label[index + 1 :]
        )
    return _satisfies_bidi_rule(label)


def _uts46_remap(text: str) -> str | None:
    """UTS #46 mapping without the STD3 rules, in NFC; None for a
    disallowed character."""

    try:
        return idna.uts46_remap(text, std3_rules=False)
    except idna.IDNAError:
        return None


def _domain_to_ascii(domain: str) -> str | None:
    """A host's domain in ASCII, or None where Node's URL parser refuses it.

    UTS #46 ToASCII with nontransitional processing, as the WHATWG URL
    parser runs it: ``faß.de`` is ``xn--fa-hia.de``, not ``fass.de``.
    Like Node, a domain that is all ASCII is only lower-cased, so its
    ``xn--`` labels are not checked; in any other domain an ``xn--`` label
    must decode to a valid label and is kept as written.
    """

    if domain.isascii():
        return domain.lower()
    mapped = _uts46_remap(domain)
    if mapped is None:
        return None
    labels: list[str] = []
    for label in mapped.split("."):
        if label.startswith("xn--"):
            try:
                decoded = label[4:].encode("ascii").decode("punycode")
            except (UnicodeError, ValueError):
                return None
            # The decoded label must be one the mapping and NFC leave alone.
            if (
                decoded.isascii()
                or decoded.startswith("xn--")
                or unicodedata.normalize("NFC", decoded) != decoded
                or _uts46_remap(decoded) != decoded
                or not _is_valid_label(decoded)
            ):
                return None
            labels.append(label)
        elif label.isascii():
            labels.append(label)
        elif _is_valid_label(label):
            labels.append("xn--" + label.encode("punycode").decode("ascii"))
        else:
            return None
    return ".".join(labels)


def _parse_host(text: str) -> str | None:
    if text.startswith("["):
        # A zone id ("%eth0") is not part of a URL host.
        if not text.endswith("]") or "%" in text:
            return None
        try:
            return f"[{_serialize_ipv6(ipaddress.IPv6Address(text[1:-1]))}]"
        except ValueError:
            return None
    if not text:
        return None
    host = _domain_to_ascii(
        urllib.parse.unquote_to_bytes(text).decode("utf-8", "replace")
    )
    if not host or _FORBIDDEN_IN_HOST.search(host):
        return None
    if _ends_in_a_number(host):
        return _parse_ipv4(host)
    return host


def parse_http_url(value: object) -> HttpUrl | None:
    """The parts of an ``http`` or ``https`` URL, or None for anything else.

    Read as a WHATWG URL parser reads a URL with a special scheme: any
    number of ``/`` and ``\\`` may follow the scheme, ``\\`` ends the
    authority and is a ``/`` in the path, the host is percent-decoded and
    written in ASCII (``faß.de`` is ``xn--fa-hia.de``), and a host that
    ends in a number is an IPv4 address in any of its spellings.
    """

    if not isinstance(value, str):
        return None
    # The parser reads a USVString: a lone surrogate is U+FFFD.
    text = value.encode("utf-16-le", "surrogatepass").decode("utf-16-le", "replace")
    text = re.sub(r"[\t\n\r]", "", text.strip("".join(map(chr, range(0x21)))))
    match = _SCHEME.match(text)
    if match is None:
        return None
    scheme = match[1].lower()
    rest = text[match.end() :].lstrip("/\\")
    end = _AUTHORITY_END.search(rest)
    authority, rest = (rest[: end.start()], rest[end.start() :]) if end else (rest, "")
    user_info, at_sign, host_port = authority.rpartition("@")
    # The port follows the first ":" outside brackets.
    split, inside_brackets = len(host_port), False
    for index, char in enumerate(host_port):
        if char == ":" and not inside_brackets:
            split = index
            break
        if char in "[]":
            inside_brackets = char == "["
    host, port_text = host_port[:split], host_port[split + 1 :]
    port: int | None = None
    if port_text:
        digits = _PORT.fullmatch(port_text)
        port = int(digits[1] or "0") if digits else None
        if port is None or port > 65535:
            return None
    hostname = _parse_host(host)
    if hostname is None:
        return None
    if port == _DEFAULT_PORTS[scheme]:
        port = None
    rest, _, fragment = rest.partition("#")
    path, _, query = rest.partition("?")
    return HttpUrl(
        scheme,
        hostname,
        port,
        path.replace("\\", "/") or "/",
        query,
        fragment,
        bool(at_sign) and user_info not in ("", ":"),
    )


def is_loopback_redirect_uri(value: object) -> bool:
    """Whether a redirect URI can be served by the loopback callback server."""

    url = parse_http_url(value)
    return (
        url is not None
        and url.scheme == "http"
        and url.hostname in LOOPBACK_HOSTS
        and url.query == ""
        and url.fragment == ""
    )


def _is_string(value: object) -> bool:
    return isinstance(value, str)


def _is_boolean(value: object) -> bool:
    return isinstance(value, bool)


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_positive_number(value: Any) -> bool:
    return _is_number(value) and bool(value > 0)


def _is_port_number(value: Any) -> bool:
    if not _is_number(value) or not 1 <= value <= 65535:
        return False
    return isinstance(value, int) or value.is_integer()


def _is_string_list(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _is_string_record(value: object) -> bool:
    return isinstance(value, dict) and all(
        isinstance(entry, str) for entry in value.values()
    )


def _is_non_blank_string(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip(_JS_WHITESPACE))


def _is_document_base_url(value: object) -> bool:
    """Whether Client ID Metadata Document URLs can be built under ``value``:
    such a URL is a client ID, which names no user, query or fragment."""

    url = parse_http_url(value)
    return (
        url is not None
        and url.scheme == "https"
        and not url.has_user_info
        and url.query == ""
        and url.fragment == ""
    )


def _is_secure_or_loopback(url: HttpUrl | None) -> bool:
    return url is not None and (url.scheme == "https" or url.hostname in LOOPBACK_HOSTS)


def _is_secure_or_loopback_url(value: object) -> bool:
    return _is_secure_or_loopback(parse_http_url(value))


# A check looks at one object and returns what is wrong with it, or None. The
# checks of a list run in order and the first error is the one reported, so
# the order is part of the behaviour.
_Check = Callable[[dict[str, Any]], str | None]


def _typed(key: str, accepts: Callable[[Any], bool], message: str) -> _Check:
    """A check that the value of ``key``, when the key is present, is accepted."""

    def check(value: dict[str, Any]) -> str | None:
        return None if key not in value or accepts(value[key]) else message

    return check


def _first_error(value: dict[str, Any], checks: Iterable[_Check]) -> str | None:
    for check in checks:
        error = check(value)
        if error is not None:
            return error
    return None


def _check_callback_port(oauth: dict[str, Any]) -> str | None:
    if "callbackPort" not in oauth:
        return None
    if not _is_port_number(oauth["callbackPort"]):
        return "oauth.callbackPort must be a port number"
    oauth["callbackPort"] = int(oauth["callbackPort"])
    return None


def _check_callback_url(oauth: dict[str, Any]) -> str | None:
    if "callbackUrl" not in oauth:
        return None
    callback = parse_http_url(oauth["callbackUrl"])
    if callback is None or not is_loopback_redirect_uri(oauth["callbackUrl"]):
        return (
            "oauth.callbackUrl must be an http URI on localhost, 127.0.0.1, "
            "or [::1] without query or fragment"
        )
    # Runs after the port check, so a port that is present is an int.
    if callback.port is not None and oauth.get("callbackPort", callback.port) != (
        callback.port
    ):
        return "oauth.callbackUrl and oauth.callbackPort name different ports"
    return None


def _check_client_registration(oauth: dict[str, Any]) -> str | None:
    registration = oauth.get("clientRegistration", "dcr")
    if registration == "dcr":
        return None
    if registration != "cimd":
        return 'oauth.clientRegistration must be "dcr" or "cimd"'
    if "clientId" in oauth or "clientName" in oauth:
        return (
            'oauth.clientRegistration "cimd" cannot be combined with '
            "oauth.clientId or oauth.clientName"
        )
    # Runs after the callback URL check, so a URL that is present parses.
    callback = parse_http_url(oauth.get("callbackUrl"))
    if callback is not None and (
        callback.hostname == "[::1]" or callback.pathname != "/callback"
    ):
        return (
            'oauth.clientRegistration "cimd" requires oauth.callbackUrl on '
            "localhost or 127.0.0.1 with path /callback"
        )
    if "clientMetadataBaseUrl" not in oauth:
        return 'oauth.clientRegistration "cimd" requires oauth.clientMetadataBaseUrl'
    return None


_OAUTH_CHECKS: Final[tuple[_Check, ...]] = (
    _typed("clientId", _is_string, "oauth.clientId must be a string"),
    _typed("clientSecret", _is_string, "oauth.clientSecret must be a string"),
    _check_callback_port,
    _check_callback_url,
    _typed("scope", _is_string, "oauth.scope must be a string"),
    _typed(
        "clientName",
        _is_non_blank_string,
        "oauth.clientName must be a non-empty string",
    ),
    _typed(
        "clientMetadataBaseUrl",
        _is_document_base_url,
        "oauth.clientMetadataBaseUrl must be an https URL without query or fragment",
    ),
    _check_client_registration,
    _typed(
        "authServerMetadataUrl",
        _is_secure_or_loopback_url,
        "oauth.authServerMetadataUrl must be an https URL, or http on localhost, "
        "127.0.0.1, or [::1]",
    ),
)

_EXPOSURE_LIST: Final = ", ".join(f'"{exposure}"' for exposure in MCP_EXPOSURES)


def _resolve_exposure_alias(value: object) -> object:
    return MCP_EXPOSURE_ALIASES.get(value, value) if isinstance(value, str) else value


def _is_exposure(value: object) -> bool:
    return isinstance(value, str) and value in MCP_EXPOSURES


def _check_exposure(config: dict[str, Any]) -> str | None:
    if "exposure" not in config:
        return None
    config["exposure"] = _resolve_exposure_alias(config["exposure"])
    if not _is_exposure(config["exposure"]):
        return f"exposure must be one of {_EXPOSURE_LIST}"
    return None


def _check_tool_exposure(config: dict[str, Any]) -> str | None:
    if "toolExposure" not in config:
        return None
    tool_exposure = config["toolExposure"]
    if not isinstance(tool_exposure, dict):
        return "toolExposure must map tool names to exposures"
    for tool in _js_key_order(tool_exposure):
        tool_exposure[tool] = _resolve_exposure_alias(tool_exposure[tool])
        if not _is_exposure(tool_exposure[tool]):
            return f'toolExposure "{tool}" must be one of {_EXPOSURE_LIST}'
    return None


def _check_not_sse(config: dict[str, Any]) -> str | None:
    if config.get("type") == "sse":
        return "legacy SSE transport is not supported; use the streamable HTTP URL"
    return None


def _check_url(config: dict[str, Any]) -> str | None:
    if parse_http_url(config["url"]) is None:
        return "url must be an http or https URL"
    return None


def _check_oauth(config: dict[str, Any]) -> str | None:
    if "oauth" not in config:
        return None
    oauth = config["oauth"]
    if not isinstance(oauth, dict):
        return "oauth must be an object"
    return _first_error(oauth, _OAUTH_CHECKS)


def _check_auth(config: dict[str, Any]) -> str | None:
    if "auth" not in config:
        return None
    auth = config["auth"]
    provider = auth.get("provider") if isinstance(auth, dict) else None
    if not isinstance(provider, str) or not provider:
        return "auth.provider must be a provider name"
    if not _is_secure_or_loopback_url(config["url"]):
        return "auth requires an https URL, or http on localhost, 127.0.0.1, or [::1]"
    return None


_COMMON_CHECKS: Final[tuple[_Check, ...]] = (
    _check_exposure,
    _check_tool_exposure,
    _typed("enabled", _is_boolean, "enabled must be a boolean"),
    _typed("description", _is_string, "description must be a string"),
    _typed(
        "timeout",
        _is_positive_number,
        "timeout must be a positive number of seconds",
    ),
    _check_not_sse,
)
_HTTP_CHECKS: Final[tuple[_Check, ...]] = (
    _check_url,
    _typed("headers", _is_string_record, "headers must map names to strings"),
    _check_oauth,
    _check_auth,
)
_STDIO_CHECKS: Final[tuple[_Check, ...]] = (
    _typed("args", _is_string_list, "args must be an array of strings"),
    _typed("env", _is_string_record, "env must map names to strings"),
    _typed("cwd", _is_string, "cwd must be a string"),
)


def _transport_checks(config: Mapping[str, Any]) -> tuple[_Check, ...] | None:
    """The checks of the transport an entry selects, or None when it selects none.

    A string ``url`` selects streamable HTTP and a string ``command`` stdio,
    each unless ``type`` names something else; ``url`` is looked at first.
    """

    server_type = config.get("type")
    untyped = "type" not in config
    if isinstance(config.get("url"), str) and (
        untyped or server_type in ("http", "streamable-http")
    ):
        return _HTTP_CHECKS
    if isinstance(config.get("command"), str) and (untyped or server_type == "stdio"):
        return _STDIO_CHECKS
    return None


def validate_mcp_server_config(name: str, raw: object) -> McpServerConfig | str:
    """Validate one server entry of the ``mcpServers`` shape.

    Returns a deep copy of the entry with exposure aliases replaced by their
    current names, or the error message.
    """

    if not _SERVER_NAME.fullmatch(name):
        return f'invalid server name "{name}" (use letters, digits, "_" and "-")'
    if not isinstance(raw, dict):
        return f'server "{name}" must be an object'
    try:
        config: McpServerConfig = copy.deepcopy(raw)
    except RecursionError:
        return f'server "{name}" is nested too deeply'
    error = _first_error(config, _COMMON_CHECKS)
    if error is None:
        checks = _transport_checks(config)
        if checks is None:
            return (
                f'server "{name}" needs either "command" (stdio) or "url" '
                "(streamable HTTP)"
            )
        error = _first_error(config, checks)
    return config if error is None else f'server "{name}": {error}'


def global_mcp_config_path(config_home: Path | str) -> Path:
    return Path(config_home) / MCP_CONFIG_FILENAME


def project_mcp_config_path(cwd: Path | str) -> Path:
    return Path(cwd) / PROJECT_CONFIG_DIR_NAME / MCP_CONFIG_FILENAME


def _is_override(value: Mapping[str, Any]) -> bool:
    """Whether an entry overrides a server defined elsewhere, not defines one."""

    return "command" not in value and "url" not in value and "type" not in value


def _reject_constant(name: str) -> object:
    raise ValueError(f"{name} is not valid JSON")


def _read_text(path: Path) -> str | None:
    """The file's text, or None when there is no such file.

    Bytes that are not UTF-8 become replacement characters, as with Node's
    ``readFileSync(path, "utf8")``, so the rest of the file still loads.
    """

    try:
        data = path.read_bytes()
    except (FileNotFoundError, NotADirectoryError):
        return None
    return data.decode("utf-8", "replace")


def _parse_json(text: str) -> object:
    try:
        return json.loads(text, parse_constant=_reject_constant)
    except RecursionError:
        raise ValueError("the JSON is nested too deeply") from None


def _has_mcp_servers_shape(parsed: object) -> bool:
    return isinstance(parsed, dict) and (
        "mcpServers" not in parsed or isinstance(parsed["mcpServers"], dict)
    )


@dataclass(slots=True)
class _ConfigState:
    servers: dict[str, McpServerEntry] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    auto_enable_codemode: bool | None = None


def _is_js_truthy(value: object) -> bool:
    if value is None or isinstance(value, bool):
        return value is True
    return not (isinstance(value, (int, float, str)) and not value)


def _read_document(path: Path, errors: list[str]) -> dict[str, Any] | None:
    """The file's JSON object, or None when it is missing or was reported."""

    try:
        text = _read_text(path)
        if text is None:
            return None
        parsed = _parse_json(text)
    except (OSError, ValueError) as exc:
        errors.append(f"{path}: {exc}")
        return None
    if not isinstance(parsed, dict) or not _has_mcp_servers_shape(parsed):
        errors.append(f"{path}: {_STRUCTURE_ERROR}")
        return None
    return parsed


def _override_server(
    path: Path, name: str, value: dict[str, Any], state: _ConfigState
) -> str | None:
    """Merge a project override over its user-level server; returns the error."""

    base = state.servers.get(name)
    if base is None:
        return (
            f'server "{name}" needs "command" or "url", or a global server to override'
        )
    if any(key not in OVERRIDE_KEYS for key in value):
        return f'server "{name}": an override can only set {", ".join(OVERRIDE_KEYS)}'
    config = validate_mcp_server_config(name, {**base.config, **value})
    if isinstance(config, str):
        return config
    state.servers[name] = McpServerEntry(
        name, config, base.source, base.scope, str(path)
    )
    return None


def _define_server(
    path: Path,
    scope: Literal["global", "project"],
    name: str,
    value: object,
    state: _ConfigState,
) -> str | None:
    """Add or replace the server an entry defines; returns the error."""

    config = validate_mcp_server_config(name, value)
    if isinstance(config, str):
        return config
    # Names that differ only in "-" and "_" would share a namespace.
    namespace = mcp_namespace(name)
    for other in state.servers:
        if other != name and mcp_namespace(other) == namespace:
            return f'server "{name}" conflicts with "{other}"'
    if scope == "project" and "url" in config and _is_js_truthy(config.get("auth")):
        return f'server "{name}": auth is only allowed in the global mcp.json'
    state.servers[name] = McpServerEntry(name, config, str(path), scope)
    return None


def _read_config_file(
    path: Path, scope: Literal["global", "project"], state: _ConfigState
) -> None:
    parsed = _read_document(path, state.errors)
    if parsed is None:
        return
    if "autoEnableCodemode" in parsed:
        if isinstance(parsed["autoEnableCodemode"], bool):
            state.auto_enable_codemode = parsed["autoEnableCodemode"]
        else:
            state.errors.append(f"{path}: autoEnableCodemode must be a boolean")
    entries = parsed.get("mcpServers", {})
    for name in _js_key_order(entries):
        value = entries[name]
        if scope == "project" and isinstance(value, dict) and _is_override(value):
            error = _override_server(path, name, value, state)
        else:
            error = _define_server(path, scope, name, value, state)
        if error is not None:
            state.errors.append(f"{path}: {error}")


def load_mcp_config(
    *, config_home: Path | str, cwd: Path | str, project_trusted: bool
) -> LoadedMcpConfig:
    """Load the user-level and, when trusted, the project MCP configuration.

    Disabled servers are included with ``enabled: false``, so they can be
    enabled again. Nothing raises: what is wrong with a file or an entry is
    reported in ``errors`` as ``<path>: <reason>`` and the rest still loads.
    """

    state = _ConfigState()
    _read_config_file(global_mcp_config_path(config_home), "global", state)
    project_config = project_mcp_config_path(cwd) if project_trusted else None
    if project_config is not None:
        _read_config_file(project_config, "project", state)
    return LoadedMcpConfig(
        servers=list(state.servers.values()),
        errors=state.errors,
        auto_enable_codemode=state.auto_enable_codemode,
        project_config=None if project_config is None else str(project_config),
    )


_INDENT: Final = re.compile(r"(?:\A|[\n\r\u2028\u2029])([ \t]+)\S")
_SURROGATE: Final = re.compile("[\ud800-\udfff]")


def _edit_mcp_servers(
    path: Path, edit: Callable[[dict[str, Any] | None, dict[str, Any]], bool]
) -> None:
    """Rewrite an ``mcp.json`` after ``edit`` changed it.

    ``edit`` gets the ``mcpServers`` object (None when the file has none) and
    the whole document (empty when there is no file), and returns whether to
    write. The file is written with its own indentation, two spaces when it
    has none, and other content is kept.
    """

    try:
        text = _read_text(path)
        parsed = {} if text is None else _parse_json(text)
    except ValueError as exc:
        raise McpConfigError(str(exc)) from exc
    if not isinstance(parsed, dict) or not _has_mcp_servers_shape(parsed):
        raise McpConfigError(f"{path}: {_STRUCTURE_ERROR}")
    if not edit(parsed.get("mcpServers"), parsed):
        return
    found = _INDENT.search(text) if text else None
    indent = found[1][:_MAX_INDENT_CHARS] if found else "  "
    try:
        output = json.dumps(parsed, indent=indent, ensure_ascii=False, allow_nan=False)
    except ValueError as exc:
        # A number such as 1e999 is read as infinity, which JSON cannot hold.
        raise McpConfigError(f"{path}: {exc}") from exc
    # A lone surrogate cannot be encoded; JSON.stringify escapes it as well.
    output = _SURROGATE.sub(lambda match: f"\\u{ord(match[0]):04x}", output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(f"{output}\n".encode())


def update_mcp_server_config(
    path: Path | str,
    name: str,
    *,
    enabled: bool | None = None,
    exposure: McpExposure | None = None,
    override: bool = False,
) -> None:
    """Change one server's ``enabled`` or ``exposure`` in an ``mcp.json``.

    ``path`` is the file that defines or overrides the server. In a defining
    entry the defaults (``enabled: true``, ``exposure: "codemode"``) remove
    the key. An override entry keeps them, since they replace the user-level
    server's values. With ``override``, a missing entry is added as an
    override. Raises ``McpConfigError`` when the file is not a valid
    ``mcp.json`` or does not define the server, and ``OSError`` when it cannot
    be read or written.
    """

    if exposure is not None and exposure not in MCP_EXPOSURES:
        raise ValueError(f"unknown MCP exposure {exposure!r}")
    file = Path(path)

    def edit(servers: dict[str, Any] | None, parsed: dict[str, Any]) -> bool:
        if (servers is None or name not in servers) and override:
            servers = parsed["mcpServers"] = {**(servers or {}), name: {}}
        server = None if servers is None else servers.get(name)
        if not isinstance(server, dict):
            raise McpConfigError(f'{file} does not define MCP server "{name}"')
        keep_defaults = _is_override(server)
        if enabled is not None:
            if enabled and not keep_defaults:
                server.pop("enabled", None)
            else:
                server["enabled"] = enabled
        if exposure is not None:
            if exposure == DEFAULT_MCP_EXPOSURE and not keep_defaults:
                server.pop("exposure", None)
            else:
                server["exposure"] = exposure
        return True

    _edit_mcp_servers(file, edit)


def add_mcp_server_config(
    path: Path | str, name: str, config: Mapping[str, Any]
) -> bool:
    """Add a server to an ``mcp.json``, creating the file when missing.

    An entry with the same name is replaced; returns whether one was. The
    config is written as given: the caller validates it. Raises as
    ``update_mcp_server_config`` does.
    """

    replaced = False

    def edit(servers: dict[str, Any] | None, parsed: dict[str, Any]) -> bool:
        nonlocal replaced
        target = {} if servers is None else servers
        replaced = name in target
        target[name] = copy.deepcopy(dict(config))
        parsed["mcpServers"] = target
        return True

    _edit_mcp_servers(Path(path), edit)
    return replaced


def remove_mcp_server_config(path: Path | str, name: str) -> bool:
    """Remove a server from an ``mcp.json``.

    Returns false, and leaves the file alone, when there is no file or it
    does not have the entry. Raises as ``update_mcp_server_config`` does.
    """

    removed = False

    def edit(servers: dict[str, Any] | None, parsed: dict[str, Any]) -> bool:
        nonlocal removed
        if servers is None or name not in servers:
            return False
        del servers[name]
        removed = True
        return True

    _edit_mcp_servers(Path(path), edit)
    return removed
