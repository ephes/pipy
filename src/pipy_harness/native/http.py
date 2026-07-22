"""Shared HTTP transport boundary for the native provider adapters.

This module owns request execution, timeouts, and cancellation for every
provider transport: the cancellable ``urlopen`` machinery that lets an
Escape / Ctrl-C during a turn shut the in-flight socket down, the SSE framing
that splits a streaming response into event payloads (:func:`iter_sse_event_payloads`),
the transport-exception retry classifier (:func:`transport_exception_retryable`),
the small JSON response boundary (:class:`JsonResponse` / :class:`JsonHTTPClient`),
the shared :class:`UrllibJsonHTTPClient` that every plain-JSON provider adapter
reuses, the shared :class:`ProviderHTTPError` base with its declarative
HTTP-status/api-error normalization (:meth:`ProviderHTTPError.from_http_error`),
the response-body decoder, the HTTP-error metadata builder, and the safe
usage-field extractors. Provider modules import these primitives rather than
re-implementing transport concerns.
"""

from __future__ import annotations

import errno
import http.client
import json
import socket
import ssl
import urllib.error
import urllib.request
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Any, ClassVar, Protocol, Self

from pipy_harness.capture import sanitize_text
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


# Transport ``errno`` codes that name a recoverable network condition (reset,
# refused, unreachable, broken pipe, timed out). A retry may succeed once the
# transient condition clears; other ``OSError`` codes are not retried.
RETRYABLE_TRANSPORT_ERRNOS: frozenset[int] = frozenset(
    {
        errno.ECONNABORTED,
        errno.ECONNREFUSED,
        errno.ECONNRESET,
        errno.EHOSTUNREACH,
        errno.ENETRESET,
        errno.ENETUNREACH,
        errno.EPIPE,
        errno.ETIMEDOUT,
    }
)


def transport_exception_retryable(exc: BaseException) -> bool | None:
    """Classify only recognized network exceptions; ``None`` means unrelated.

    Returns ``True`` for transient transport failures (timeouts, DNS failures,
    connection resets, truncated/incomplete reads, retryable ``OSError`` errnos),
    ``False`` for permanent transport failures (certificate verification, an
    invalid status line), and ``None`` for exceptions that are not recognized
    transport failures at all — the caller decides what to do with those.
    ``URLError`` is unwrapped so the underlying reason is classified.
    """

    if isinstance(exc, urllib.error.ContentTooShortError):
        return True
    if isinstance(exc, urllib.error.URLError):
        reason = getattr(exc, "reason", None)
        if isinstance(reason, BaseException) and reason is not exc:
            return transport_exception_retryable(reason)
        return None
    if isinstance(exc, ssl.SSLCertVerificationError):
        return False
    if isinstance(exc, TimeoutError | socket.gaierror | ConnectionError):
        return True
    if isinstance(exc, ssl.SSLError):
        return True
    if isinstance(exc, http.client.IncompleteRead | http.client.RemoteDisconnected):
        return True
    if isinstance(exc, http.client.BadStatusLine):
        return False
    if isinstance(exc, OSError) and exc.errno in RETRYABLE_TRANSPORT_ERRNOS:
        return True
    return None


def iter_sse_event_payloads(
    response: Any,
    collected_body: list[str],
) -> Iterator[str]:
    """Yield raw SSE event payload strings line-by-line from a urllib response.

    SSE events are separated by blank lines. Each line either starts with
    ``data:`` (the payload) or is a comment/keepalive starting with ``:``
    (skipped). This accumulates ``data:`` lines per event, joins them, and
    yields the stripped payload string for each non-empty, non-``[DONE]`` event
    as it arrives over the wire. The raw decoded lines are appended to
    ``collected_body`` so callers retain an after-the-fact transcript. Callers
    decode each yielded payload into their own event/error taxonomy.
    """

    data_lines: list[str] = []
    for raw in response:
        if isinstance(raw, bytes):
            line = raw.decode("utf-8", errors="replace")
        else:
            line = str(raw)
        collected_body.append(line)
        # Strip a single trailing newline so the SSE separator check works on
        # stripped lines.
        stripped_line = line.rstrip("\r\n")
        if stripped_line == "":
            payload = "\n".join(data_lines).strip()
            data_lines.clear()
            if not payload or payload == "[DONE]":
                continue
            yield payload
            continue
        if stripped_line.startswith("data:"):
            data_lines.append(stripped_line.removeprefix("data:").strip())
    # Flush any trailing event that did not end with a blank line.
    if data_lines:
        payload = "\n".join(data_lines).strip()
        if payload and payload != "[DONE]":
            yield payload


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


# Anthropic reports cache counters under distinct keys; both the Anthropic
# Messages adapter and the Bedrock InvokeModel adapter (Claude on Bedrock speaks
# the same body/response shape) map them onto the normalized cache keys.
_ANTHROPIC_CACHE_USAGE_FIELDS: tuple[tuple[str, str], ...] = (
    ("cache_creation_input_tokens", "cache_write_tokens"),
    ("cache_read_input_tokens", "cached_tokens"),
)


def _usage_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def extract_anthropic_usage(value: Any) -> dict[str, int | float]:
    """Extract Anthropic Messages usage, shared by the anthropic + bedrock adapters.

    Reads the normalized usage keys directly, maps Anthropic's
    ``cache_creation_input_tokens``/``cache_read_input_tokens`` counters onto the
    normalized cache-write/cached keys, and synthesizes ``total_tokens`` from
    input + output + cache reads + cache writes when the provider omits it (both
    Claude on the Anthropic API and on Bedrock report cache reads/writes
    separately from fresh input tokens).
    """

    from pipy_harness.native.usage import (
        NORMALIZED_PROVIDER_USAGE_KEYS,
        normalize_provider_usage,
    )

    if not isinstance(value, Mapping):
        return {}
    usage: dict[str, Any] = {}
    for key in NORMALIZED_PROVIDER_USAGE_KEYS:
        usage[key] = value.get(key)
    for provider_key, normalized_key in _ANTHROPIC_CACHE_USAGE_FIELDS:
        item = value.get(provider_key)
        if item is not None and usage.get(normalized_key) is None:
            usage[normalized_key] = item
    if usage.get("total_tokens") is None:
        input_tokens = _usage_int(usage.get("input_tokens"))
        output_tokens = _usage_int(usage.get("output_tokens"))
        if input_tokens is not None and output_tokens is not None:
            usage["total_tokens"] = (
                input_tokens
                + output_tokens
                + (_usage_int(usage.get("cached_tokens")) or 0)
                + (_usage_int(usage.get("cache_write_tokens")) or 0)
            )
    return normalize_provider_usage(usage)


def extract_responses_usage(
    value: Any,
    nested_fields: tuple[tuple[str, str], ...],
) -> dict[str, int | float]:
    """Extract Responses-style usage: identity-mapped normalized keys plus nested details.

    The Responses/``generateContent`` usage payloads carry the normalized usage
    keys directly and stash cached/reasoning counters in nested detail objects
    (``(details_key, usage_key)`` in ``nested_fields``). Flat Chat-Completions
    usage uses :func:`extract_usage_from_fields` instead.
    """

    from pipy_harness.native.usage import (
        NORMALIZED_PROVIDER_USAGE_KEYS,
        normalize_provider_usage,
    )

    if not isinstance(value, Mapping):
        return {}
    usage: dict[str, Any] = {}
    for key in NORMALIZED_PROVIDER_USAGE_KEYS:
        usage[key] = value.get(key)
    for details_key, usage_key in nested_fields:
        details = value.get(details_key)
        if isinstance(details, Mapping) and usage_key in details:
            usage[usage_key] = details[usage_key]
    return normalize_provider_usage(usage)


@dataclass(frozen=True, slots=True)
class ApiErrorField:
    """Declarative spec for lifting one api-error field into error metadata.

    ``source_key`` is read from the response body's ``error`` object and stored
    under ``metadata_key``. ``allow_int`` widens the accepted value type from
    ``str`` to ``str | int``; ``sanitize`` runs the value through
    :func:`sanitize_text` (as ``str``) before storing it.
    """

    source_key: str
    metadata_key: str
    sanitize: bool
    allow_int: bool


class ProviderHTTPError(Exception):
    """Shared base for sanitized provider HTTP-adapter errors.

    Carries a sanitized message and a metadata dict. Subclasses set the
    ``provider_label`` and ``api_error_fields`` class attributes so the shared
    :meth:`from_http_error` normalizes an HTTP-status failure into that
    provider's exact error metadata while returning the concrete subclass.
    """

    provider_label: ClassVar[str] = ""
    api_error_fields: ClassVar[tuple[ApiErrorField, ...]] = ()

    def __init__(
        self, message: str, *, metadata: Mapping[str, Any] | None = None
    ) -> None:
        super().__init__(sanitize_text(message))
        self.metadata: dict[str, Any] = dict(metadata or {})

    @classmethod
    def from_http_error(cls, exc: urllib.error.HTTPError) -> Self:
        """Normalize a urllib ``HTTPError`` into this provider's status error.

        Builds ``{"http_status": code}`` metadata, best-effort decodes the error
        body and lifts each :class:`ApiErrorField` in ``api_error_fields`` into
        the metadata, and returns ``cls`` with the provider-labelled message. A
        non-JSON error body is swallowed (metadata keeps only the status).
        """

        metadata: dict[str, Any] = {"http_status": exc.code}
        try:
            body = decode_json_object(
                exc.read(), error_class=cls, provider_label=cls.provider_label
            )
        except ProviderHTTPError:
            body = {}
        error = body.get("error")
        if isinstance(error, Mapping):
            for spec in cls.api_error_fields:
                value = error.get(spec.source_key)
                allowed: tuple[type, ...] = (str, int) if spec.allow_int else (str,)
                if isinstance(value, allowed):
                    metadata[spec.metadata_key] = (
                        sanitize_text(str(value)) if spec.sanitize else value
                    )
        return cls(
            f"{cls.provider_label} request failed with HTTP status {exc.code}.",
            metadata=metadata,
        )


@dataclass(frozen=True, slots=True)
class UrllibJsonHTTPClient:
    """Standard-library JSON client shared by the plain-JSON provider adapters.

    ``provider_label`` names the provider in the transport (``URLError``) and
    response-parse messages. The three error classes let the client raise each
    provider's own named subclasses — status failures via
    :meth:`ProviderHTTPError.from_http_error` on ``status_error_class``, network
    failures via ``transport_error_class``, and body-decode failures via
    ``parse_error_class`` — so existing provider error handling and tests keep
    asserting on those concrete types.
    """

    provider_label: str
    status_error_class: type[ProviderHTTPError]
    transport_error_class: type[ProviderHTTPError]
    parse_error_class: type[ProviderHTTPError]

    def post_json(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        body: Mapping[str, Any],
        timeout_seconds: float,
        cancel_token: CancelToken | None = None,
    ) -> JsonResponse:
        encoded = json.dumps(body).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=encoded,
            headers=dict(headers),
            method="POST",
        )
        try:
            status_code, payload = urlopen_read_cancellable(
                request,
                timeout_seconds=timeout_seconds,
                cancel_token=cancel_token,
            )
        except urllib.error.HTTPError as exc:
            raise self.status_error_class.from_http_error(exc) from exc
        except urllib.error.URLError as exc:
            reason = (
                sanitize_text(str(exc.reason))
                if getattr(exc, "reason", None)
                else "request failed"
            )
            raise self.transport_error_class(
                f"{self.provider_label} request failed: {reason}"
            ) from exc

        return JsonResponse(
            status_code=status_code,
            body=decode_json_object(
                payload,
                error_class=self.parse_error_class,
                provider_label=self.provider_label,
            ),
        )
