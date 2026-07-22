"""Shared HTTP transport boundary for the native provider adapters.

This module owns request execution, timeouts, and cancellation for every
provider transport: the cancellable ``urlopen`` machinery that lets an
Escape / Ctrl-C during a turn shut the in-flight socket down, the small JSON
response boundary (:class:`JsonResponse` / :class:`JsonHTTPClient`) that each
provider's ``UrllibJsonHTTPClient`` copy satisfies, the response-body decoder,
the HTTP-error metadata builder, and the safe usage-field extractor. Provider
modules import these primitives rather than re-implementing transport concerns.
"""

from __future__ import annotations

import http.client
import json
import socket
import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from pipy_harness.native.cancellation import CancelToken, ProviderCancelledError, _safe_close

# Exceptions that a concurrent :meth:`CancelToken.cancel` can surface from a
# blocked ``http.client`` read once it shuts the socket down. The expected
# symptom is ``OSError`` (the shut-down ``recv`` returns/raises), but the same
# shutdown also races ``http.client.HTTPResponse._close_conn``: one path sets
# ``self.fp = None`` while another calls ``fp.close()``, yielding
# ``AttributeError: 'NoneType' object has no attribute 'close'``. ``ValueError``
# / ``HTTPException`` cover partially-read or torn-down responses. All are benign
# artifacts of the deliberate cancellation, so callers map them to
# :class:`ProviderCancelledError` *only* when the token is actually cancelled;
# otherwise they re-raise so a genuine bug still surfaces.
CANCELLED_READ_ERRORS: tuple[type[BaseException], ...] = (
    OSError,
    ValueError,
    http.client.HTTPException,
    AttributeError,
)


@dataclass(frozen=True, slots=True)
class JsonResponse:
    """Small JSON response boundary used by provider HTTP adapters."""

    status_code: int
    body: Mapping[str, Any]


class JsonHTTPClient(Protocol):
    """Minimal injectable JSON HTTP client used by every provider adapter."""

    def post_json(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        body: Mapping[str, Any],
        timeout_seconds: float,
        cancel_token: CancelToken | None = None,
    ) -> JsonResponse:
        """POST JSON and return parsed JSON metadata.

        ``cancel_token`` is threaded down from the native tool loop's
        active-turn cancellation path. When supplied, the client registers its
        in-flight connection on the token so an Escape / Ctrl-C during the turn
        shuts the socket down — during the header wait *or* the body read — and
        raises :class:`ProviderCancelledError` instead of letting the request
        finish.
        """


class _ConnectionCloser:
    """Closeable that force-unblocks a blocked socket read on an HTTP connection.

    ``socket.shutdown(SHUT_RDWR)`` is used rather than ``close()`` because a
    blocked ``recv`` in the worker thread returns immediately on shutdown
    regardless of outstanding ``makefile()`` io-refs — whereas ``socket.close()``
    defers the real fd close while a response's ``makefile`` reader is open, so
    it would not interrupt a blocked body read. Shutdown therefore unblocks both
    the header wait (``getresponse``) and a later body/stream read.

    The socket is captured at ``connect()`` time, not read from the connection at
    cancel time: for ``Connection: close`` / close-delimited responses
    ``http.client.getresponse()`` hands the socket to the ``HTTPResponse`` and
    sets ``conn.sock = None`` while ``HTTPResponse.fp`` keeps the fd open, so a
    cancel-time ``conn.sock`` lookup would find ``None`` and fail to interrupt
    the blocked body/stream read. The captured socket's fd is still open (the
    response's ``makefile`` holds an io-ref), so ``shutdown`` still unblocks it.
    """

    __slots__ = ("_conn", "_sock")

    def __init__(self, conn: Any) -> None:
        self._conn = conn
        self._sock = getattr(conn, "sock", None)

    def close(self) -> None:
        sock = self._sock if self._sock is not None else getattr(self._conn, "sock", None)
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        try:
            self._conn.close()
        except OSError:
            pass


def _registering_connection(base: type, cancel_token: CancelToken) -> type:
    """Subclass an ``http.client`` connection that registers itself on connect.

    Registering the *connection* (not just the post-``urlopen`` response) is
    what makes cancellation reach a non-streaming JSON request: such a request
    blocks inside ``getresponse()`` waiting for response headers for the entire
    time the model generates, long before any response object exists. By
    registering as soon as the socket is established, :meth:`CancelToken.cancel`
    can shut it down during that header wait — the same shutdown also unblocks a
    later body/stream ``read()`` on the same connection.
    """

    class _RegisteringConnection(base):  # type: ignore[valid-type, misc]
        def connect(self) -> None:
            super().connect()  # type: ignore[misc]
            # register() closes + raises immediately if cancel() already fired,
            # so a worker that lost the race never blocks on a doomed request.
            cancel_token.register(_ConnectionCloser(self))

    return _RegisteringConnection


def _build_cancellable_opener(
    cancel_token: CancelToken,
) -> urllib.request.OpenerDirector:
    """Build a urllib opener whose connections register on ``cancel_token``.

    Subclassing the default HTTP/HTTPS handlers keeps urllib's proxy, redirect,
    and HTTP-error handling intact; only the connection class is swapped for one
    that registers its socket on the token at ``connect()`` time.
    """

    class _CancelHTTPHandler(urllib.request.HTTPHandler):
        def http_open(self, req: urllib.request.Request) -> Any:
            return self.do_open(
                _registering_connection(http.client.HTTPConnection, cancel_token),  # type: ignore[arg-type]
                req,
            )

    class _CancelHTTPSHandler(urllib.request.HTTPSHandler):
        def https_open(self, req: urllib.request.Request) -> Any:
            # Mirror the stdlib's https_open connection kwargs across Python
            # versions: 3.14 folds ``check_hostname`` into ``context`` and keeps
            # only ``self._context`` (no ``self._check_hostname``), while older
            # versions also expose ``self._check_hostname``. Pass each only when
            # present so this works on both without an AttributeError.
            kwargs: dict[str, Any] = {}
            context = getattr(self, "_context", None)
            if context is not None:
                kwargs["context"] = context
            check_hostname = getattr(self, "_check_hostname", None)
            if check_hostname is not None:
                kwargs["check_hostname"] = check_hostname
            return self.do_open(
                _registering_connection(http.client.HTTPSConnection, cancel_token),  # type: ignore[arg-type]
                req,
                **kwargs,
            )

    return urllib.request.build_opener(_CancelHTTPSHandler(), _CancelHTTPHandler())


def open_url_cancellable(
    request: urllib.request.Request,
    *,
    timeout_seconds: float | None,
    cancel_token: CancelToken | None = None,
) -> Any:
    """``urlopen`` that registers the underlying connection on the token.

    Returns the open response. With a token, a concurrent
    :meth:`CancelToken.cancel` closes the connection during the header wait or
    while the body/stream is read, surfacing :class:`ProviderCancelledError`.
    ``HTTPError`` propagates unchanged so providers keep their status handling.
    """

    if cancel_token is None:
        return urllib.request.urlopen(  # noqa: S310 - URL fixed at provider construction
            request, timeout=timeout_seconds
        )
    cancel_token.raise_if_cancelled()
    opener = _build_cancellable_opener(cancel_token)
    try:
        return opener.open(request, timeout=timeout_seconds)
    except urllib.error.HTTPError:
        raise
    except CANCELLED_READ_ERRORS as exc:
        if cancel_token.cancelled:
            raise ProviderCancelledError("native provider turn cancelled") from exc
        raise


def urlopen_read_cancellable(
    request: urllib.request.Request,
    *,
    timeout_seconds: float | None,
    cancel_token: CancelToken | None = None,
) -> tuple[int, bytes]:
    """``urlopen`` + ``read`` that honors a :class:`CancelToken`.

    The underlying connection is registered on the token (see
    :func:`open_url_cancellable`) so a concurrent :meth:`CancelToken.cancel`
    closes the socket during the header wait *or* the body read, unblocking the
    worker and surfacing :class:`ProviderCancelledError`. ``HTTPError`` /
    ``URLError`` propagate unchanged so each provider keeps its existing
    status/transport error handling.
    """

    response = open_url_cancellable(
        request, timeout_seconds=timeout_seconds, cancel_token=cancel_token
    )
    if cancel_token is None:
        try:
            return response.getcode(), response.read()
        finally:
            _safe_close(response)
    try:
        status_code = response.getcode()
        payload = response.read()
    except urllib.error.HTTPError:
        raise
    except CANCELLED_READ_ERRORS as exc:
        if cancel_token.cancelled:
            raise ProviderCancelledError(
                "native provider turn cancelled"
            ) from exc
        raise
    finally:
        _safe_close(response)
    cancel_token.raise_if_cancelled()
    return status_code, payload


def safe_http_status_metadata(status_code: int) -> dict[str, Any]:
    """Build the metadata dict carried on every HTTP-error `ProviderResult`."""

    return {"http_status": status_code}


def decode_json_object(
    payload: bytes,
    *,
    error_class: type[Exception],
    provider_label: str,
) -> Mapping[str, Any]:
    """Decode an HTTP response body as a JSON object; raise ``error_class`` on failure."""

    try:
        decoded = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise error_class(f"{provider_label} returned non-JSON response metadata.") from exc
    if not isinstance(decoded, Mapping):
        raise error_class(f"{provider_label} returned unsupported JSON response metadata.")
    return decoded


def extract_usage_from_fields(
    value: Any,
    fields: tuple[tuple[str, str], ...],
) -> dict[str, int | float]:
    """Extract usage counters from a response using a ``(provider_key, normalized_key)`` map."""

    from pipy_harness.native.usage import normalize_provider_usage

    if not isinstance(value, Mapping):
        return {}
    usage: dict[str, Any] = {}
    for provider_key, normalized_key in fields:
        usage[normalized_key] = value.get(provider_key)
    return normalize_provider_usage(usage)
