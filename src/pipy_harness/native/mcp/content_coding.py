"""Undoing the ``Content-Encoding`` of an HTTP answer, as ``fetch`` does.

The OAuth fetch (``oauth/fetch.py``) decodes a whole body, the HTTP
transport's default fetch (``http_fetch.py``) a body as it arrives. Both ask
for ``Accept-Encoding: gzip, deflate`` unless the caller set that header,
and both decode by these rules, which are those of Node's ``fetch``:

- The codings of the answer's ``Content-Encoding`` are undone, the last one
  first: ``gzip`` and ``x-gzip``, and ``deflate`` with or without the zlib
  wrapper (told apart by the first byte, as undici does).
- A list with another coding (``br``, ``zstd``, ``identity``, an empty one)
  leaves the body as it arrived. More than ``MAX_CONTENT_ENCODINGS``
  codings are a network failure.
- The answer of a HEAD or CONNECT request and of the statuses 101, 204, 205
  and 304 is not decoded.
- A compressed body that ends early gives what it held up to there. After a
  gzip member comes another one, or a zero byte that ends the body as
  padding; a deflate stream ignores what follows it. Data that is not what
  the coding says raises ``zlib.error``, which the fetches report as a
  network failure.
"""

from __future__ import annotations

import http.client
import zlib
from collections.abc import Iterable, Iterator, Mapping
from typing import Final

ACCEPT_ENCODING: Final = "gzip, deflate"
MAX_CONTENT_ENCODINGS: Final = 5
# The most bytes one decoding step hands on at once, so that a small
# compressed body cannot make a large piece.
PIECE_BYTES: Final = 64 * 1024
# Statuses whose answer has no body to decode (the fetch standard's null
# body statuses).
_NULL_BODY_STATUSES: Final = frozenset({101, 204, 205, 304})
_DECODED: Final = frozenset({"gzip", "x-gzip", "deflate"})
# `wbits` of zlib: a gzip member, a zlib stream, a raw deflate stream.
_GZIP_WBITS: Final = 16 + zlib.MAX_WBITS
_ZLIB_WBITS: Final = zlib.MAX_WBITS
_RAW_WBITS: Final = -zlib.MAX_WBITS


def asking_for_codings(headers: Mapping[str, str]) -> dict[str, str]:
    """``headers`` with ``Accept-Encoding: gzip, deflate`` unless they name
    that header already, in any letter case."""

    if any(name.lower() == "accept-encoding" for name in headers):
        return dict(headers)
    return {**headers, "Accept-Encoding": ACCEPT_ENCODING}


def content_codings(
    encoding: str | None, method: str, status: int, url: str
) -> tuple[str, ...] | None:
    """The codings to undo, in the order they were applied; ``None`` when
    the body is left as it arrived. Raises ``http.client.HTTPException``
    for more than ``MAX_CONTENT_ENCODINGS`` codings."""

    if (
        not encoding
        or method.upper() in ("HEAD", "CONNECT")
        or status in _NULL_BODY_STATUSES
    ):
        return None
    codings = tuple(coding.strip() for coding in encoding.lower().split(","))
    if len(codings) > MAX_CONTENT_ENCODINGS:
        raise http.client.HTTPException(
            f"Too many content encodings in the response of {url}: {len(codings)}"
        )
    if any(coding not in _DECODED for coding in codings):
        return None
    return codings


class Inflater:
    """Undoes one coding of a body handed over in pieces."""

    __slots__ = ("_decoder", "_ended", "_gzip", "_members")

    def __init__(self, coding: str) -> None:
        self._gzip = coding != "deflate"
        self._decoder: zlib._Decompress | None = None
        # The body ended: padding after a gzip member, or the end of the
        # deflate stream. What follows is ignored.
        self._ended = False
        self._members = 0

    def feed(self, data: bytes) -> Iterator[bytes]:
        """The decoded bytes ``data`` gives, in pieces of at most
        ``PIECE_BYTES``. Raises ``zlib.error`` for data not in the coding."""

        while not self._ended:
            if self._decoder is None:
                if not data:
                    return
                # As in Node's zlib, data after a gzip member is the next
                # member unless it starts with a zero byte.
                if self._members and data[0] == 0:
                    self._ended = True
                    return
                self._decoder = zlib.decompressobj(self._wbits(data))
            out = self._decoder.decompress(data, PIECE_BYTES)
            if out:
                yield out
            if self._decoder.eof:
                data = self._decoder.unused_data
                self._decoder = None
                self._members += 1
                self._ended = not self._gzip
                continue
            data = self._decoder.unconsumed_tail
            # A full piece may leave output in the decoder with no input
            # left; anything less means it waits for more input.
            if not data and len(out) < PIECE_BYTES:
                return

    def _wbits(self, data: bytes) -> int:
        if self._gzip:
            return _GZIP_WBITS
        return _ZLIB_WBITS if data[0] & 0x0F == 0x08 else _RAW_WBITS


class ContentDecoder:
    """Undoes all codings of a body handed over in pieces."""

    __slots__ = ("_stages",)

    def __init__(self, codings: Iterable[str]) -> None:
        self._stages = tuple(Inflater(coding) for coding in reversed(list(codings)))

    def feed(self, data: bytes) -> Iterator[bytes]:
        """As ``Inflater.feed``, through every coding. Each piece must be
        taken before the next ``feed``."""

        pieces: Iterator[bytes] = iter((data,))
        for stage in self._stages:
            pieces = _through(stage, pieces)
        return pieces


def _through(stage: Inflater, pieces: Iterator[bytes]) -> Iterator[bytes]:
    for piece in pieces:
        yield from stage.feed(piece)
