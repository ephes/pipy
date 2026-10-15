# IDNA for URL hosts

## Status

Accepted for MCP parity slice H2 (round-2 goal check finding F1 and Q4),
whose brief allows a small, well-known dependency for UTS #46 processing.
Code review is the slice's review gate.

## Context

Pi reads an MCP server URL with Node's WHATWG URL parser. A host with
non-ASCII characters goes through UTS #46 ToASCII with nontransitional
processing, so `faß.de` becomes `xn--fa-hia.de` and a host the parser
refuses (a misplaced joiner, a disallowed character, a broken `xn--` label)
makes the URL invalid. pipy's `config.parse_http_url` kept such a host as
written, and Python's socket and TLS code later encoded it with the
standard library's `idna` codec, which is IDNA 2003 with transitional
processing and maps `faß.de` to `fass.de`, a different domain. Configured
headers or tokens could reach that other host. OAuth resource checks,
credential keys and callback ids also compared a different spelling of
the host than Pi does.

UTS #46 needs the Unicode IDNA mapping table, which Python's
`unicodedata` does not have.

## Decision

Use the pure-Python `idna` package (`>=3.10,<4`, locked by uv; no
dependencies of its own, typed) for its UTS #46 mapping table
(`idna.uts46_remap(std3_rules=False)`). The label
checks follow Node's URL parser (ada), which Pi runs on, where it departs
from the letter of UTS #46:

- an all-ASCII host is only lower-cased, so its `xn--` labels are not
  checked;
- in any other host an `xn--` label must decode to a non-ASCII label that
  the mapping and NFC leave alone, does not itself start with `xn--`, and
  passes the label checks; it is then kept as written, not re-encoded;
- a label must not start with a combining mark; the first joiner decides
  the label: after a virama it is valid (the Bidi Rule is skipped), U+200D
  otherwise fails, and U+200C needs a left- or dual-joining character
  anywhere before it and a right- or dual-joining one anywhere after it,
  with ada's own virama and joining-type tables (copied into `config.py`
  as about 60 code point ranges), which lack letters Unicode's tables have;
- the Bidi Rule applies only to a label with an `R`, `AL` or `AN`
  character, up to its last character that is not `NSM`.

Other non-ASCII labels are Punycode-encoded with the standard library's
`punycode` codec. The forbidden-character and IPv4 checks then run on the
ASCII result. Every consumer of the parser (HTTP transport, OAuth,
credential keys, callback ids) sees that host.

`idna.encode` is not used: it applies IDNA 2008's code point rules and
hyphen checks, which refuse hosts Node accepts, such as `☃.net`, and
`idna.check_bidi` and `idna.valid_contextj` follow RFC 5893 and RFC 5892,
which refuse labels Node accepts, such as Arabic beh, U+200C, a digit and
beh again.

## Alternatives

The standard library's `idna` codec is IDNA 2003 and transitional, the
behaviour this decision removes. A hand-written UTS #46 would need the
mapping table (thousands of ranges) copied into pipy and kept current. Refusing every non-ASCII host would fail closed but
reject URLs Pi accepts.

## Consequences

One small runtime dependency is added. The Unicode tables are the `idna`
package's and Python's, not Node's (ICU/ada), so a character from a recent
Unicode version (ada's mark and Bidi tables lack Unicode 14 additions
such as U+061D and U+11070) may be judged differently; a character Python's
`unicodedata` does not know fails the Bidi Rule. The package caps its
input at 1024 characters, so a non-ASCII host longer than that is refused,
where Node accepts it; no such host can be resolved.

## Verification/Reversal

Parser tests pin Node's results for mapped, deviation, joiner, Bidi,
`xn--` and refused hosts; transport, discovery, credential and callback id
tests check the ASCII host. During the slice about 22 900 hosts (random
ones and ones built around joiners, viramas and right-to-left characters)
were compared with Node 26's `new URL`; the only differences came from
characters added in Unicode 14, which ada's tables lack. Reversal removes
the dependency and the mapping in `config._domain_to_ascii`, returning
non-ASCII hosts to the old behaviour.
