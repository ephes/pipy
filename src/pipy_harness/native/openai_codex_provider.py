"""OpenAI Codex subscription provider for the native pipy runtime."""

from __future__ import annotations

import base64
import errno
import hashlib
import http.client
import json
import math
import os
import random
import re
import secrets
import socket
import ssl
import stat
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import webbrowser
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Protocol, TextIO

from pipy_harness.capture import sanitize_text
from pipy_harness.native._provider_helpers import (
    decode_json_object,
    failed_provider_result,
    open_url_cancellable,
    serialize_tool_for_responses,
    utc_now,
)
from pipy_harness.native.cancellation import CancelToken, ProviderCancelledError, _safe_close
from pipy_harness.models import HarnessStatus
from pipy_harness.native.models import ProviderRequest, ProviderResult, ProviderToolCall
from pipy_harness.native.provider import StreamChunkSink, apply_provider_headers
from pipy_harness.native.retry import (
    DEFAULT_RETRIABLE_STATUSES,
    RetryPolicy,
    retry_with_backoff,
)
from pipy_harness.native.settings import (
    DEFAULT_HTTP_IDLE_TIMEOUT_MS,
    DEFAULT_WEBSOCKET_CONNECT_TIMEOUT_MS,
)
from pipy_harness.native.tools.messages import (
    AssistantMessage,
    ToolResultMessage,
    UserMessage,
)
from pipy_harness.native.usage import NORMALIZED_PROVIDER_USAGE_KEYS, normalize_provider_usage

OPENAI_CODEX_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
OPENAI_CODEX_AUTHORIZE_URL = "https://auth.openai.com/oauth/authorize"
OPENAI_CODEX_TOKEN_URL = "https://auth.openai.com/oauth/token"
OPENAI_CODEX_REDIRECT_URI = "http://localhost:1455/auth/callback"
OPENAI_CODEX_SCOPE = "openid profile email offline_access"
OPENAI_CODEX_RESPONSES_URL = "https://chatgpt.com/backend-api/codex/responses"
OPENAI_CODEX_RESPONSES_WS_URL = "wss://chatgpt.com/backend-api/codex/responses"
OPENAI_CODEX_JWT_AUTH_CLAIM = "https://api.openai.com/auth"
OPENAI_CODEX_MIN_TOKEN_TTL_SECONDS = 60
OPENAI_CODEX_API_LABEL_MAX_LENGTH = 64
OPENAI_CODEX_API_LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")
OPENAI_CODEX_API_LABEL_ALLOWLIST = frozenset(
    {
        "authentication_error",
        "billing_hard_limit_reached",
        "insufficient_quota",
        "invalid_request_error",
        "permission_error",
        "quota_exceeded",
        "rate_limit_error",
        "server_error",
        "usage_limit_reached",
        "websocket_connection_limit_reached",
    }
)
OPENAI_CODEX_RESPONSE_STATUS_ALLOWLIST = frozenset(
    {"cancelled", "completed", "failed", "incomplete"}
)
OPENAI_CODEX_TERMINAL_API_LABELS = frozenset(
    {
        "authentication_error",
        "billing_hard_limit_reached",
        "insufficient_quota",
        "invalid_request_error",
        "permission_error",
        "quota_exceeded",
        "usage_limit_reached",
    }
)
MAX_RETRY_AFTER_SECONDS = 120.0
_RETRYABLE_TRANSPORT_ERRNOS = frozenset(
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
OPENAI_CODEX_NESTED_USAGE_FIELDS: tuple[tuple[str, str], ...] = (
    ("input_tokens_details", "cached_tokens"),
    ("output_tokens_details", "reasoning_tokens"),
)


@dataclass(frozen=True, slots=True)
class SseResponse:
    """Small SSE response boundary used by the Codex subscription endpoint.

    ``body`` holds the complete decoded SSE stream (used by hermetic
    test stubs). ``event_stream``, when present, is an iterator of
    pre-parsed event dicts that yields each event as it arrives over
    the network. Real adapters set ``event_stream`` so stream/reasoning
    sinks fire live; test stubs leave it as ``None`` and the parser
    falls back to splitting ``body`` after the fact.
    """

    status_code: int
    body: str
    event_stream: Iterator[Mapping[str, Any]] | None = None


@dataclass(frozen=True, slots=True)
class OAuthTokenResponse:
    """OAuth token endpoint response with raw auth material."""

    access_token: str
    refresh_token: str
    expires_in: int


@dataclass(frozen=True, slots=True)
class OpenAICodexCredentials:
    """Pipy-owned OAuth credentials for the Codex subscription endpoint."""

    access_token: str
    refresh_token: str
    expires_at: int
    account_id: str

    def is_expiring(self, *, now: int | None = None) -> bool:
        current = int(time.time()) if now is None else now
        return self.expires_at <= current + OPENAI_CODEX_MIN_TOKEN_TTL_SECONDS


class WebSocketClient(Protocol):
    """Minimal injectable WebSocket client for Codex Responses calls."""

    def post_events(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        body: Mapping[str, Any],
        connect_timeout_seconds: float | None,
        idle_timeout_seconds: float | None,
        cancel_token: CancelToken | None = None,
    ) -> Iterator[Mapping[str, Any]]:
        """Send a Responses create request and yield decoded events."""


class SseHTTPClient(Protocol):
    """Minimal injectable HTTP client for Codex Responses calls."""

    def post_sse(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        body: Mapping[str, Any],
        timeout_seconds: float | None,
        cancel_token: CancelToken | None = None,
    ) -> SseResponse:
        """POST JSON and return the SSE response body.

        When ``cancel_token`` is supplied the in-flight streaming response is
        registered on it, so an active-turn Escape / Ctrl-C closes the
        connection and surfaces :class:`ProviderCancelledError` instead of
        draining the stream to completion.
        """


class OAuthHTTPClient(Protocol):
    """Minimal injectable OAuth form HTTP client."""

    def post_form(
        self,
        url: str,
        *,
        fields: Mapping[str, str],
        timeout_seconds: float,
    ) -> OAuthTokenResponse:
        """POST form data and return parsed OAuth tokens."""


class OpenAICodexCredentialStore(Protocol):
    """Storage boundary for pipy-owned OpenAI Codex credentials."""

    def load(self) -> OpenAICodexCredentials | None:
        """Load stored credentials, if present."""

    def save(self, credentials: OpenAICodexCredentials) -> None:
        """Save credentials with private file permissions when file-backed."""

    def delete(self) -> bool:
        """Delete stored credentials, if present."""


@dataclass(frozen=True, slots=True)
class WebsocketsSyncClient:
    """Synchronous adapter over the maintained ``websockets`` client."""

    def post_events(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        body: Mapping[str, Any],
        connect_timeout_seconds: float | None,
        idle_timeout_seconds: float | None,
        cancel_token: CancelToken | None = None,
    ) -> Iterator[Mapping[str, Any]]:
        if cancel_token is not None:
            cancel_token.raise_if_cancelled()
        try:
            from websockets.exceptions import (
                ConnectionClosed,
                ConnectionClosedOK,
                InvalidHandshake,
                WebSocketException,
            )
            from websockets.sync.client import connect
        except Exception as exc:  # noqa: BLE001
            raise OpenAICodexTransportError(
                "OpenAI Codex transport failed while waiting for response headers.",
                metadata={"phase": "headers", "retryable": False, "transport": "websocket"},
            ) from exc
        try:
            websocket = connect(
                url,
                additional_headers=dict(headers),
                user_agent_header=None,
                open_timeout=connect_timeout_seconds,
                ping_interval=None,
                max_size=4 * 1024 * 1024,
                max_queue=16,
            )
        except Exception as exc:  # noqa: BLE001
            if cancel_token is not None:
                cancel_token.raise_if_cancelled()
            if isinstance(exc, WebSocketException | InvalidHandshake):
                raise OpenAICodexTransportError(
                    "OpenAI Codex transport failed while waiting for response headers.",
                    metadata={
                        "phase": "headers",
                        "retryable": True,
                        "transport": "websocket",
                    },
                ) from exc
            normalized = _normalize_transport_exception(
                exc, transport="websocket", phase="headers", stream=False
            )
            if normalized is not None:
                raise normalized from exc
            raise

        def _events() -> Iterator[Mapping[str, Any]]:
            registered = False
            try:
                if cancel_token is not None:
                    cancel_token.register(websocket)
                    registered = True
                    cancel_token.raise_if_cancelled()
                try:
                    websocket.send(json.dumps({"type": "response.create", **body}))
                except Exception as exc:  # noqa: BLE001
                    if cancel_token is not None:
                        cancel_token.raise_if_cancelled()
                    normalized = _normalize_transport_exception(
                        exc, transport="websocket", phase="headers", stream=False
                    )
                    if normalized is not None:
                        raise normalized from exc
                    raise
                while True:
                    if cancel_token is not None:
                        cancel_token.raise_if_cancelled()
                    try:
                        message = websocket.recv(timeout=idle_timeout_seconds)
                    except StopIteration:
                        return
                    except TimeoutError as exc:
                        if cancel_token is not None:
                            cancel_token.raise_if_cancelled()
                        raise OpenAICodexStreamInterruptedError(
                            "OpenAI Codex stream was interrupted before completion.",
                            metadata={"phase": "stream", "retryable": True, "transport": "websocket"},
                        ) from exc
                    except ConnectionClosedOK:
                        return
                    except ConnectionClosed as exc:
                        if cancel_token is not None:
                            cancel_token.raise_if_cancelled()
                        raise OpenAICodexStreamInterruptedError(
                            "OpenAI Codex stream was interrupted before completion.",
                            metadata={"phase": "stream", "retryable": True, "transport": "websocket"},
                        ) from exc
                    except Exception as exc:  # noqa: BLE001
                        if cancel_token is not None:
                            cancel_token.raise_if_cancelled()
                        normalized = _normalize_transport_exception(
                            exc, transport="websocket", phase="stream", stream=True
                        )
                        if normalized is not None:
                            raise normalized from exc
                        raise
                    if not isinstance(message, str):
                        raise OpenAICodexResponseParseError(
                            "OpenAI Codex stream included a non-text WebSocket message."
                        )
                    yield _decode_websocket_event(message)
            finally:
                if cancel_token is not None and registered:
                    cancel_token.unregister(websocket)
                try:
                    websocket.close()
                except Exception:  # noqa: BLE001
                    pass

        return _events()


@dataclass(frozen=True, slots=True)
class UrllibSseHTTPClient:
    """Standard-library SSE client for Codex Responses calls.

    The client streams events line-by-line from the underlying HTTP
    response so that the caller's `stream_sink` and `reasoning_sink`
    fire as deltas arrive over the wire, not after the full body is
    buffered. The optional `body` field on the returned `SseResponse`
    is filled progressively for compatibility with tests that read
    the full string; production code should consume `event_stream`.
    """

    retry_clock: Callable[[], float] = time.time

    def post_sse(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        body: Mapping[str, Any],
        timeout_seconds: float | None,
        cancel_token: CancelToken | None = None,
    ) -> SseResponse:
        if cancel_token is not None:
            cancel_token.raise_if_cancelled()
        encoded = json.dumps(body).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=encoded,
            headers=dict(headers),
            method="POST",
        )
        try:
            # open_url_cancellable registers the underlying connection on the
            # token, so an active-turn cancel closes the socket during the
            # header wait or while the stream is read; the streaming loop below
            # converts the resulting read failure into ProviderCancelledError.
            response = open_url_cancellable(
                request,
                timeout_seconds=timeout_seconds,
                cancel_token=cancel_token,
            )
        except urllib.error.HTTPError as exc:
            raise OpenAICodexHTTPStatusError.from_http_error(
                exc,
                cancel_token=cancel_token,
                now_seconds=self.retry_clock(),
            ) from exc
        except ProviderCancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - recognized transport failures only
            if cancel_token is not None:
                cancel_token.raise_if_cancelled()
            normalized = _normalize_transport_exception(
                exc,
                transport="sse",
                phase="headers",
                stream=False,
            )
            if normalized is None:
                raise
            raise normalized from exc

        status_code = response.getcode()
        # Build a streaming iterator that yields parsed events as they
        # arrive. The iterator also captures the raw decoded body into
        # `collected_body` so the SseResponse retains an after-the-fact
        # transcript for tests and diagnostics.
        collected_body: list[str] = []

        def _events() -> Iterator[Mapping[str, Any]]:
            try:
                for event in _iter_sse_stream(response, collected_body):
                    if cancel_token is not None:
                        cancel_token.raise_if_cancelled()
                    yield event
            except ProviderCancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - recognized transport failures only
                if cancel_token is not None and cancel_token.cancelled:
                    raise ProviderCancelledError(
                        "native provider turn cancelled"
                    ) from exc
                normalized = _normalize_transport_exception(
                    exc,
                    transport="sse",
                    phase="stream",
                    stream=True,
                )
                if normalized is None:
                    raise
                raise normalized from exc
            finally:
                # The connection (registered by open_url_cancellable) is closed
                # here; the per-turn token is discarded after the turn, so the
                # closed connection it still references is a harmless no-op for
                # any later cancel().
                _safe_close(response)

        return SseResponse(
            status_code=status_code,
            body="",  # populated lazily as the stream is consumed
            event_stream=_events(),
        )


def _iter_sse_stream(
    response: Any,
    collected_body: list[str],
) -> Iterator[Mapping[str, Any]]:
    """Yield parsed SSE events line-by-line from a urllib response.

    SSE events are separated by blank lines. Each event line either
    starts with ``data:`` (the payload) or is a comment/keepalive
    starting with ``:`` (skipped). The function accumulates ``data:``
    lines per event, joins them, parses JSON, and yields the parsed
    mapping. Malformed JSON raises ``OpenAICodexResponseParseError``.
    """

    data_lines: list[str] = []
    for raw in response:
        if isinstance(raw, bytes):
            line = raw.decode("utf-8", errors="replace")
        else:
            line = str(raw)
        collected_body.append(line)
        # Strip a single trailing newline so the SSE separator check
        # works on stripped lines.
        stripped_line = line.rstrip("\r\n")
        if stripped_line == "":
            payload = "\n".join(data_lines).strip()
            data_lines.clear()
            if not payload or payload == "[DONE]":
                continue
            yield _decode_sse_event(payload)
            continue
        if stripped_line.startswith("data:"):
            data_lines.append(stripped_line.removeprefix("data:").strip())
    # Flush any trailing event that did not end with a blank line.
    if data_lines:
        payload = "\n".join(data_lines).strip()
        if payload and payload != "[DONE]":
            yield _decode_sse_event(payload)


@dataclass(frozen=True, slots=True)
class UrllibOAuthHTTPClient:
    """Standard-library form client for OpenAI OAuth token calls."""

    def post_form(
        self,
        url: str,
        *,
        fields: Mapping[str, str],
        timeout_seconds: float,
    ) -> OAuthTokenResponse:
        encoded = urllib.parse.urlencode(dict(fields)).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=encoded,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                payload = response.read()
        except urllib.error.HTTPError as exc:
            raise OpenAICodexOAuthError(
                f"OpenAI Codex OAuth request failed with HTTP status {exc.code}.",
                metadata={"http_status": exc.code},
            ) from exc
        except urllib.error.URLError as exc:
            reason = sanitize_text(str(exc.reason)) if getattr(exc, "reason", None) else "request failed"
            raise OpenAICodexOAuthError(f"OpenAI Codex OAuth request failed: {reason}") from exc

        body = decode_json_object(payload, error_class=OpenAICodexResponseParseError, provider_label="OpenAI Codex")
        access_token = body.get("access_token")
        refresh_token = body.get("refresh_token")
        expires_in = body.get("expires_in")
        if not isinstance(access_token, str) or not access_token:
            raise OpenAICodexOAuthError("OpenAI Codex OAuth response omitted access token.")
        if not isinstance(refresh_token, str) or not refresh_token:
            raise OpenAICodexOAuthError("OpenAI Codex OAuth response omitted refresh token.")
        if not isinstance(expires_in, int) or isinstance(expires_in, bool) or expires_in <= 0:
            raise OpenAICodexOAuthError("OpenAI Codex OAuth response omitted token expiry.")
        return OAuthTokenResponse(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_in=expires_in,
        )


@dataclass(frozen=True, slots=True)
class FileOpenAICodexCredentialStore:
    """Pipy-owned file store for OpenAI Codex OAuth credentials."""

    path: Path = field(default_factory=lambda: default_openai_codex_auth_path())

    def load(self) -> OpenAICodexCredentials | None:
        if not self.path.exists():
            return None
        try:
            body = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise OpenAICodexAuthError("OpenAI Codex auth state could not be read.") from exc
        if not isinstance(body, Mapping):
            raise OpenAICodexAuthError("OpenAI Codex auth state has unsupported shape.")
        if body.get("provider") != "openai-codex" or body.get("type") != "oauth":
            raise OpenAICodexAuthError("OpenAI Codex auth state has unsupported provider data.")
        access_token = body.get("access_token")
        refresh_token = body.get("refresh_token")
        account_id = body.get("account_id")
        expires_at = body.get("expires_at")
        if (
            not isinstance(access_token, str)
            or not access_token
            or not isinstance(refresh_token, str)
            or not refresh_token
            or not isinstance(account_id, str)
            or not account_id
            or not isinstance(expires_at, int)
            or isinstance(expires_at, bool)
        ):
            raise OpenAICodexAuthError("OpenAI Codex auth state is missing required fields.")
        return OpenAICodexCredentials(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at=expires_at,
            account_id=account_id,
        )

    def save(self, credentials: OpenAICodexCredentials) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.path.parent.chmod(0o700)
        except OSError:
            pass
        payload = {
            "provider": "openai-codex",
            "type": "oauth",
            "access_token": credentials.access_token,
            "refresh_token": credentials.refresh_token,
            "expires_at": credentials.expires_at,
            "account_id": credentials.account_id,
        }
        temporary_path = self.path.with_name(f"{self.path.name}.partial")
        with temporary_path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
        temporary_path.chmod(stat.S_IRUSR | stat.S_IWUSR)
        temporary_path.replace(self.path)
        self.path.chmod(stat.S_IRUSR | stat.S_IWUSR)

    def delete(self) -> bool:
        try:
            self.path.unlink()
        except FileNotFoundError:
            return False
        return True


@dataclass(slots=True)
class OpenAICodexAuthManager:
    """OAuth auth boundary for OpenAI Codex subscription access."""

    store: OpenAICodexCredentialStore = field(default_factory=FileOpenAICodexCredentialStore)
    oauth_client: OAuthHTTPClient = field(default_factory=UrllibOAuthHTTPClient)
    token_url: str = OPENAI_CODEX_TOKEN_URL
    timeout_seconds: float = 60.0

    def get_credentials(self) -> OpenAICodexCredentials | None:
        credentials = self.store.load()
        if credentials is None:
            return None
        if not credentials.is_expiring():
            return credentials
        refreshed = self.refresh(credentials.refresh_token)
        self.store.save(refreshed)
        return refreshed

    def refresh(self, refresh_token: str) -> OpenAICodexCredentials:
        token_response = self.oauth_client.post_form(
            self.token_url,
            fields={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": OPENAI_CODEX_CLIENT_ID,
            },
            timeout_seconds=self.timeout_seconds,
        )
        return _credentials_from_token_response(token_response)

    def exchange_authorization_code(self, code: str, verifier: str) -> OpenAICodexCredentials:
        token_response = self.oauth_client.post_form(
            self.token_url,
            fields={
                "grant_type": "authorization_code",
                "client_id": OPENAI_CODEX_CLIENT_ID,
                "code": code,
                "code_verifier": verifier,
                "redirect_uri": OPENAI_CODEX_REDIRECT_URI,
            },
            timeout_seconds=self.timeout_seconds,
        )
        return _credentials_from_token_response(token_response)

    def login_interactive(
        self,
        *,
        input_stream: TextIO,
        output_stream: TextIO,
        open_browser: bool = True,
    ) -> OpenAICodexCredentials:
        flow = create_authorization_flow()
        server = _LocalOAuthCallbackServer.start(flow.state)
        print("OpenAI Codex OAuth login:", file=output_stream)
        print(flow.url, file=output_stream)
        if open_browser:
            webbrowser.open(flow.url)
        print(
            "Complete login in the browser, then press Enter. "
            "If browser callback is unavailable, paste the redirect URL or code instead.",
            file=output_stream,
        )
        manual_input = input_stream.readline().strip()
        try:
            parsed = parse_authorization_input(manual_input) if manual_input else server.wait_for_code()
            # Bare-code paste is intentionally accepted for manual fallback and
            # relies on the PKCE verifier to bind the token exchange.
            if parsed.state and parsed.state != flow.state:
                raise OpenAICodexOAuthError("OpenAI Codex OAuth state mismatch.")
            if not parsed.code:
                raise OpenAICodexOAuthError("OpenAI Codex OAuth authorization code was missing.")
            credentials = self.exchange_authorization_code(parsed.code, flow.verifier)
            self.store.save(credentials)
            return credentials
        finally:
            server.close()

    def logout(self) -> bool:
        return self.store.delete()


class CodexTransportState:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._auto_fallback_to_sse = False

    def should_skip_websocket(self) -> bool:
        with self._lock:
            return self._auto_fallback_to_sse

    def remember_sse_fallback(self) -> None:
        with self._lock:
            self._auto_fallback_to_sse = True


@dataclass(frozen=True, slots=True)
class OpenAICodexResponsesProvider:
    """OpenAI Codex Responses streaming provider behind ProviderPort.

    Real adapter with `supports_tool_calls=True`. When
    `ProviderRequest.messages` is non-empty the provider serializes them
    into the Responses-API streaming `input` list (with `function_call`
    and `function_call_output` items) and declares `tools` from
    `available_tools`; the SSE assembler then accumulates function-call
    items across `response.output_item.added`,
    `response.function_call_arguments.delta`,
    `response.function_call_arguments.done`, and
    `response.output_item.done` events and surfaces them on
    `ProviderResult.tool_calls`. Legacy single-turn callers leave
    `messages` empty and keep the previous list `input` body builder.
    """

    model_id: str
    auth_manager: OpenAICodexAuthManager = field(default_factory=OpenAICodexAuthManager)
    http_client: SseHTTPClient = field(default_factory=UrllibSseHTTPClient)
    websocket_client: WebSocketClient = field(default_factory=WebsocketsSyncClient)
    endpoint: str = OPENAI_CODEX_RESPONSES_URL
    websocket_endpoint: str = OPENAI_CODEX_RESPONSES_WS_URL
    timeout_seconds: float | None = DEFAULT_HTTP_IDLE_TIMEOUT_MS / 1000.0
    # CLI/settings construction passes Pi's resolved default (auto); direct
    # construction keeps the historic SSE-safe default unless a transport is
    # explicitly selected.
    transport: str = "sse"
    websocket_connect_timeout_seconds: float | None = (
        DEFAULT_WEBSOCKET_CONNECT_TIMEOUT_MS / 1000.0
    )
    transport_state: CodexTransportState = field(default_factory=CodexTransportState)
    request_id_factory: Callable[[], str] = field(default_factory=lambda: lambda: uuid.uuid4().hex)
    supports_tool_calls: bool = True
    # Mapped reasoning effort for the request's ``reasoning.effort`` field. Set by
    # the REPL provider boundary from the current model + thinking level (clamped
    # and mapped); ``None`` omits ``effort`` and keeps the Pi-forced summary only.
    reasoning_effort: str | None = None
    retry_policy: "RetryPolicy" = field(
        default_factory=lambda: RetryPolicy(
            max_attempts=4,
            initial_delay_seconds=2.0,
            max_delay_seconds=60.0,
        )
    )
    retry_sleep: Callable[[float], None] | None = None
    retry_jitter: Callable[[], float] = random.random
    retry_clock: Callable[[], float] = time.time

    @property
    def name(self) -> str:
        return "openai-codex"

    def complete(
        self,
        request: ProviderRequest,
        *,
        stream_sink: StreamChunkSink | None = None,
        reasoning_sink: StreamChunkSink | None = None,
        cancel_token: CancelToken | None = None,
    ) -> ProviderResult:
        if cancel_token is not None:
            cancel_token.raise_if_cancelled()
        started_at = utc_now()
        if not self.model_id or not self.model_id.strip():
            return failed_provider_result(
                request,
                provider_name=self.name,
                started_at=started_at,
                error_type="OpenAICodexConfigurationError",
                error_message="--native-model is required for native provider openai-codex.",
            )

        try:
            credentials = self.auth_manager.get_credentials()
        except OpenAICodexProviderError as exc:
            return failed_provider_result(
                request,
                provider_name=self.name,
                started_at=started_at,
                error_type=type(exc).__name__,
                error_message=str(exc),
                metadata=exc.metadata,
            )
        if credentials is None:
            return failed_provider_result(
                request,
                provider_name=self.name,
                started_at=started_at,
                error_type="OpenAICodexAuthError",
                error_message=(
                    "OpenAI Codex login is required. "
                    "Run `pipy auth openai-codex login` before using native provider openai-codex."
                ),
            )

        body: dict[str, Any] = {
            "model": self.model_id,
            "instructions": request.system_prompt,
            "input": _responses_input_messages(request),
            "store": False,
            "stream": True,
            "text": {"verbosity": "low"},
            "include": ["reasoning.encrypted_content"],
            "reasoning": (
                {"summary": "auto", "effort": self.reasoning_effort}
                if self.reasoning_effort is not None
                else {"summary": "auto"}
            ),
            "tool_choice": "auto",
            "parallel_tool_calls": True,
        }
        if request.available_tools:
            body["tools"] = [
                serialize_tool_for_responses(tool)
                for tool in request.available_tools
            ]
        # Pi fires the extension hook once against request-scoped additional
        # headers, then each transport derives its required auth/protocol fields.
        # Keep this snapshot outside the retry/fallback loop so every attempt
        # reuses it without re-running extension code.
        extension_headers = apply_provider_headers(request, {})
        base_headers = {
            **extension_headers,
            "Authorization": f"Bearer {credentials.access_token}",
            "chatgpt-account-id": credentials.account_id,
            "originator": "pipy",
            "User-Agent": "pipy",
        }
        headers = {
            **base_headers,
            "Accept": "text/event-stream",
            "Content-Type": "application/json",
            "OpenAI-Beta": "responses=experimental",
        }
        http_client: SseHTTPClient = self.http_client
        if type(http_client) is UrllibSseHTTPClient:
            http_client = UrllibSseHTTPClient(retry_clock=self.retry_clock)

        attempt = 0
        progress = StreamProgress()

        def _sse_attempt() -> ParsedOpenAICodexResponse:
            try:
                resp = http_client.post_sse(
                    self.endpoint,
                    headers=headers,
                    body=body,
                    timeout_seconds=self.timeout_seconds,
                    cancel_token=cancel_token,
                )
            except urllib.error.HTTPError as exc:
                raise OpenAICodexHTTPStatusError.from_http_error(
                    exc,
                    cancel_token=cancel_token,
                    now_seconds=self.retry_clock(),
                ) from exc
            if resp.status_code < 200 or resp.status_code >= 300:
                raise OpenAICodexHTTPStatusError(
                    f"OpenAI Codex request failed with HTTP status {resp.status_code}.",
                    metadata={"http_status": resp.status_code},
                )
            return _parse_sse_response(
                resp.body,
                stream_sink=stream_sink,
                reasoning_sink=reasoning_sink,
                event_stream=resp.event_stream,
                cancel_token=cancel_token,
                progress=progress,
            )

        def _websocket_attempt() -> ParsedOpenAICodexResponse:
            request_id = self.request_id_factory()
            websocket_headers = {
                **base_headers,
                "OpenAI-Beta": "responses_websockets=2026-02-06",
                "session-id": request_id,
                "x-client-request-id": request_id,
            }
            events = self.websocket_client.post_events(
                self.websocket_endpoint,
                headers=websocket_headers,
                body=body,
                connect_timeout_seconds=self.websocket_connect_timeout_seconds,
                idle_timeout_seconds=self.timeout_seconds,
                cancel_token=cancel_token,
            )
            return _parse_response_events(
                events,
                transport="websocket",
                stream_sink=stream_sink,
                reasoning_sink=reasoning_sink,
                cancel_token=cancel_token,
                progress=progress,
            )

        def _attempt() -> ParsedOpenAICodexResponse:
            nonlocal attempt, progress
            attempt += 1
            progress = StreamProgress()
            if cancel_token is not None:
                cancel_token.raise_if_cancelled()
            use_websocket = self.transport in {"auto", "websocket"} and not (
                self.transport == "auto"
                and self.transport_state.should_skip_websocket()
            )
            connection_limit_retried = False
            if not use_websocket:
                return _sse_attempt()
            while True:
                try:
                    return _websocket_attempt()
                except OpenAICodexResponseParseError as exc:
                    if (
                        not progress.observed
                        and exc.metadata.get("api_error_code")
                        == "websocket_connection_limit_reached"
                    ):
                        if not connection_limit_retried:
                            connection_limit_retried = True
                            continue
                        if self.transport == "auto":
                            self.transport_state.remember_sse_fallback()
                        return _sse_attempt()
                    raise
                except OpenAICodexTransportError:
                    if progress.observed:
                        raise
                    if self.transport == "auto":
                        self.transport_state.remember_sse_fallback()
                    return _sse_attempt()

        def _should_retry(exc: BaseException) -> bool:
            if cancel_token is not None:
                cancel_token.raise_if_cancelled()
            return not progress.observed and _codex_failure_retryable(exc)

        def _retry_after_seconds(exc: BaseException) -> float | None:
            metadata = getattr(exc, "metadata", None)
            if not isinstance(metadata, dict):
                return None
            value = metadata.get("retry_after_seconds")
            if isinstance(value, bool) or not isinstance(value, int | float):
                return None
            capped = min(self.retry_policy.max_delay_seconds, max(0.0, float(value)))
            metadata["retry_after_seconds"] = capped
            return capped

        def _sleep_before_retry(delay: float) -> None:
            if cancel_token is not None:
                cancel_token.raise_if_cancelled()
            if self.retry_sleep is not None:
                self.retry_sleep(delay)
            elif cancel_token is not None:
                cancel_token.event.wait(delay)
            else:
                time.sleep(delay)
            if cancel_token is not None:
                cancel_token.raise_if_cancelled()

        try:
            result = retry_with_backoff(
                _attempt,
                policy=self.retry_policy,
                sleep=_sleep_before_retry,
                jitter=self.retry_jitter,
                should_retry=_should_retry,
                retry_after_seconds=_retry_after_seconds,
            )
        except ProviderCancelledError:
            raise
        except OpenAICodexProviderError as exc:
            metadata = dict(exc.metadata)
            retry_after = metadata.get("retry_after_seconds")
            if (
                isinstance(retry_after, int | float)
                and not isinstance(retry_after, bool)
            ):
                metadata["retry_after_seconds"] = min(
                    self.retry_policy.max_delay_seconds,
                    max(0.0, float(retry_after)),
                )
            intrinsically_retryable = _codex_failure_retryable(exc)
            metadata.update(
                {
                    "attempt": attempt,
                    "exhausted": (
                        intrinsically_retryable
                        and not progress.observed
                        and attempt >= self.retry_policy.max_attempts
                    ),
                    "max_attempts": self.retry_policy.max_attempts,
                    "progress": progress.value,
                    "retryable": intrinsically_retryable,
                }
            )
            return failed_provider_result(
                request,
                provider_name=self.name,
                started_at=started_at,
                error_type=type(exc).__name__,
                error_message=str(exc),
                metadata=metadata,
            )

        return ProviderResult(
            status=HarnessStatus.SUCCEEDED,
            provider_name=self.name,
            model_id=self.model_id,
            started_at=started_at,
            ended_at=utc_now(),
            final_text=result.final_text,
            usage=result.usage,
            metadata={
                "provider_response_store_requested": False,
                "response_status": result.response_status,
            },
            tool_calls=result.tool_calls,
        )


def _responses_input_messages(request: ProviderRequest) -> list[dict[str, object]]:
    if request.messages:
        items: list[dict[str, object]] = []
        for envelope in request.messages:
            items.extend(_envelope_to_input_items(envelope))
        return items
    messages: list[dict[str, object]] = []
    if request.no_tool_repl_context is not None:
        for exchange in request.no_tool_repl_context.exchanges:
            messages.append(
                {
                    "role": "user",
                    "content": [{"type": "input_text", "text": exchange.user_prompt}],
                }
            )
            messages.append(
                {
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": exchange.provider_final_text}],
                }
            )
    messages.append(
        {
            "role": "user",
            "content": [{"type": "input_text", "text": request.user_prompt}],
        }
    )
    return messages


def _envelope_to_input_items(envelope: Any) -> list[dict[str, object]]:
    """Translate one LoopMessage into one or more Responses streaming items.

    Mirrors the Pi OpenAI Codex Responses serialization: user/assistant text
    rides as `{"role": ..., "content": [{"type": "input_text" |
    "output_text", ...}]}` items, assistant tool intents preserve both
    Responses IDs (`call_id` and `id`) when available, and tool results ride as
    `function_call_output` items keyed by `call_id`.
    """

    if isinstance(envelope, UserMessage):
        return [
            {
                "role": "user",
                "content": [{"type": "input_text", "text": envelope.content}],
            }
        ]
    if isinstance(envelope, AssistantMessage):
        items: list[dict[str, object]] = []
        if envelope.content:
            items.append(
                {
                    "role": "assistant",
                    "content": [
                        {"type": "output_text", "text": envelope.content}
                    ],
                }
            )
        for call in envelope.tool_calls:
            call_id, item_id = _split_responses_tool_correlation(
                call.provider_correlation_id
            )
            item: dict[str, object] = {
                "type": "function_call",
                "call_id": call_id,
                "name": call.tool_name,
                "arguments": call.arguments_json,
            }
            if item_id is not None:
                item["id"] = item_id
            items.append(item)
        return items
    if isinstance(envelope, ToolResultMessage):
        return [
            {
                "type": "function_call_output",
                "call_id": _require_responses_tool_call_id(envelope),
                "output": envelope.output_text,
            }
        ]
    raise OpenAICodexResponseParseError(
        f"unsupported message envelope: {type(envelope).__name__}"
    )


def _require_provider_correlation_id(envelope: ToolResultMessage) -> str:
    if envelope.provider_correlation_id:
        return envelope.provider_correlation_id
    raise OpenAICodexProviderError(
        "ToolResultMessage is missing provider_correlation_id."
    )


def _require_responses_tool_call_id(envelope: ToolResultMessage) -> str:
    return _split_responses_tool_correlation(
        _require_provider_correlation_id(envelope)
    )[0]


def _split_responses_tool_correlation(correlation: str) -> tuple[str, str | None]:
    call_id, sep, item_id = correlation.partition("|")
    if sep and item_id:
        return call_id, item_id
    return correlation, None


def _join_responses_tool_correlation(
    call_id: str | None, item_id: str | None
) -> str | None:
    if call_id and item_id:
        return f"{call_id}|{item_id}"
    return call_id or item_id


@dataclass(frozen=True, slots=True)
class AuthorizationFlow:
    verifier: str
    state: str
    url: str


@dataclass(frozen=True, slots=True)
class ParsedAuthorizationInput:
    code: str | None = None
    state: str | None = None


@dataclass(frozen=True, slots=True)
class ParsedOpenAICodexResponse:
    final_text: str | None
    usage: dict[str, int | float]
    response_status: str
    tool_calls: tuple[ProviderToolCall, ...] = ()


class OpenAICodexProviderError(Exception):
    """Base class for sanitized OpenAI Codex provider errors."""

    def __init__(self, message: str, *, metadata: Mapping[str, Any] | None = None) -> None:
        super().__init__(sanitize_text(message))
        self.metadata = dict(metadata or {})


class OpenAICodexAuthError(OpenAICodexProviderError):
    """Raised when stored OpenAI Codex auth state is missing or invalid."""


class OpenAICodexOAuthError(OpenAICodexProviderError):
    """Raised when OpenAI Codex OAuth login or refresh fails."""


class OpenAICodexHTTPStatusError(OpenAICodexProviderError):
    """Raised when the Codex endpoint returns a non-success HTTP status."""

    @classmethod
    def from_http_error(
        cls,
        exc: urllib.error.HTTPError,
        *,
        cancel_token: CancelToken | None = None,
        now_seconds: float | None = None,
    ) -> OpenAICodexHTTPStatusError:
        metadata: dict[str, Any] = {"http_status": exc.code}
        retry_after = _parse_retry_after_seconds(
            exc.headers,
            now_seconds=time.time() if now_seconds is None else now_seconds,
        )
        if retry_after is not None:
            metadata["retry_after_seconds"] = retry_after
        body: Mapping[str, Any]
        if cancel_token is not None:
            cancel_token.raise_if_cancelled()
        try:
            raw_body = exc.read()
        except ProviderCancelledError:
            raise
        except Exception as read_exc:  # noqa: BLE001 - recognized transport failures only
            if cancel_token is not None:
                cancel_token.raise_if_cancelled()
            if _transport_exception_retryable(read_exc) is None:
                raise
            # The HTTP response status is already sufficient for the stable
            # provider-domain failure. A transport interruption while reading
            # its optional diagnostic body must not leak a raw socket error.
            body = {}
        else:
            if cancel_token is not None:
                cancel_token.raise_if_cancelled()
            try:
                body = decode_json_object(
                    raw_body,
                    error_class=OpenAICodexResponseParseError,
                    provider_label="OpenAI Codex",
                )
            except OpenAICodexResponseParseError:
                body = {}
        error = body.get("error")
        if isinstance(error, Mapping):
            error_type = _safe_codex_api_label(error.get("type"))
            error_code = _safe_codex_api_label(error.get("code"))
            if error_type is not None:
                metadata["api_error_type"] = error_type
            if error_code is not None:
                metadata["api_error_code"] = error_code
        return cls(f"OpenAI Codex request failed with HTTP status {exc.code}.", metadata=metadata)


class OpenAICodexTransportError(OpenAICodexProviderError):
    """Raised when the HTTP request cannot reach OpenAI Codex."""


class OpenAICodexStreamInterruptedError(OpenAICodexTransportError):
    """Raised when an opened Codex response stream ends unexpectedly."""


class OpenAICodexResponseParseError(OpenAICodexProviderError):
    """Raised when the Codex response shape is unsupported."""


@dataclass(slots=True)
class StreamProgress:
    """Attempt-local monotonic provider-event progress marker."""

    observed: bool = False

    @property
    def value(self) -> str:
        return "event" if self.observed else "none"

    def mark_event(self) -> None:
        self.observed = True


def _safe_codex_api_label(value: Any) -> str | None:
    """Return a known bounded API label, never arbitrary server text."""

    if not isinstance(value, str):
        return None
    if len(value) > OPENAI_CODEX_API_LABEL_MAX_LENGTH:
        return None
    if OPENAI_CODEX_API_LABEL_RE.fullmatch(value) is None:
        return None
    return value if value in OPENAI_CODEX_API_LABEL_ALLOWLIST else None


def _safe_codex_response_status(value: Any) -> str:
    if not isinstance(value, str):
        return "unknown"
    return value if value in OPENAI_CODEX_RESPONSE_STATUS_ALLOWLIST else "unknown"


def _parse_retry_after_seconds(
    headers: Any,
    *,
    now_seconds: float,
) -> float | None:
    """Parse Pi-shaped retry headers into one bounded numeric duration."""

    if headers is None:
        return None
    retry_after_ms = headers.get("retry-after-ms")
    if retry_after_ms is not None:
        try:
            milliseconds = float(str(retry_after_ms).strip())
        except ValueError:
            milliseconds = -1.0
        if math.isfinite(milliseconds) and milliseconds >= 0:
            return min(MAX_RETRY_AFTER_SECONDS, milliseconds / 1000.0)

    retry_after = headers.get("Retry-After")
    if retry_after is None:
        retry_after = headers.get("retry-after")
    if retry_after is None:
        return None
    text = str(retry_after).strip()
    try:
        seconds = float(text)
    except ValueError:
        try:
            parsed_date = parsedate_to_datetime(text)
        except (TypeError, ValueError, OverflowError):
            return None
        if parsed_date.tzinfo is None:
            return None
        seconds = parsed_date.timestamp() - now_seconds
    if not math.isfinite(seconds) or seconds < 0:
        return None
    return min(MAX_RETRY_AFTER_SECONDS, seconds)


def _codex_failure_retryable(exc: BaseException) -> bool:
    """Classify sanitized Codex failures without consulting server prose."""

    if isinstance(exc, ProviderCancelledError):
        return False
    if isinstance(exc, OpenAICodexHTTPStatusError):
        metadata = exc.metadata
        if any(
            metadata.get(key) in OPENAI_CODEX_TERMINAL_API_LABELS
            for key in ("api_error_type", "api_error_code")
        ):
            return False
        status = metadata.get("http_status")
        return (
            isinstance(status, int)
            and not isinstance(status, bool)
            and status in DEFAULT_RETRIABLE_STATUSES
        )
    if isinstance(exc, OpenAICodexTransportError):
        return exc.metadata.get("retryable") is True
    return False


def _transport_exception_retryable(exc: BaseException) -> bool | None:
    """Classify only recognized network exceptions; ``None`` means unrelated."""

    if isinstance(exc, urllib.error.ContentTooShortError):
        return True
    if isinstance(exc, urllib.error.URLError):
        reason = getattr(exc, "reason", None)
        if isinstance(reason, BaseException) and reason is not exc:
            return _transport_exception_retryable(reason)
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
    if isinstance(exc, OSError) and exc.errno in _RETRYABLE_TRANSPORT_ERRNOS:
        return True
    return None


def _normalize_transport_exception(
    exc: BaseException,
    *,
    transport: str,
    phase: str,
    stream: bool,
) -> OpenAICodexTransportError | None:
    """Create a stable provider error for a narrowly recognized exception."""

    retryable = _transport_exception_retryable(exc)
    if retryable is None:
        return None
    metadata = {
        "phase": phase,
        "retryable": retryable,
        "transport": transport,
    }
    if stream:
        return OpenAICodexStreamInterruptedError(
            "OpenAI Codex stream was interrupted before completion.",
            metadata=metadata,
        )
    return OpenAICodexTransportError(
        "OpenAI Codex transport failed while waiting for response headers.",
        metadata=metadata,
    )


def default_openai_codex_auth_path() -> Path:
    configured = os.environ.get("PIPY_AUTH_DIR")
    auth_dir = Path(configured).expanduser() if configured else Path.home() / ".local" / "state" / "pipy" / "auth"
    return auth_dir / "openai-codex.json"


def create_authorization_flow() -> AuthorizationFlow:
    verifier = _base64url(secrets.token_bytes(32))
    challenge = _base64url(hashlib.sha256(verifier.encode("ascii")).digest())
    state = secrets.token_hex(16)
    params = {
        "response_type": "code",
        "client_id": OPENAI_CODEX_CLIENT_ID,
        "redirect_uri": OPENAI_CODEX_REDIRECT_URI,
        "scope": OPENAI_CODEX_SCOPE,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
        "id_token_add_organizations": "true",
        "codex_cli_simplified_flow": "true",
        "originator": "pipy",
    }
    return AuthorizationFlow(
        verifier=verifier,
        state=state,
        url=f"{OPENAI_CODEX_AUTHORIZE_URL}?{urllib.parse.urlencode(params)}",
    )


def parse_authorization_input(value: str) -> ParsedAuthorizationInput:
    cleaned = value.strip()
    if not cleaned:
        return ParsedAuthorizationInput()
    try:
        parsed_url = urllib.parse.urlparse(cleaned)
        if parsed_url.scheme and parsed_url.netloc:
            params = urllib.parse.parse_qs(parsed_url.query)
            return ParsedAuthorizationInput(
                code=_single_query_value(params, "code"),
                state=_single_query_value(params, "state"),
            )
    except ValueError:
        pass
    if "#" in cleaned:
        code, state = cleaned.split("#", 1)
        return ParsedAuthorizationInput(code=code or None, state=state or None)
    if "code=" in cleaned:
        params = urllib.parse.parse_qs(cleaned)
        return ParsedAuthorizationInput(
            code=_single_query_value(params, "code"),
            state=_single_query_value(params, "state"),
        )
    return ParsedAuthorizationInput(code=cleaned)


def _credentials_from_token_response(response: OAuthTokenResponse) -> OpenAICodexCredentials:
    account_id = _extract_account_id(response.access_token)
    if account_id is None:
        raise OpenAICodexOAuthError("OpenAI Codex OAuth token omitted account id.")
    return OpenAICodexCredentials(
        access_token=response.access_token,
        refresh_token=response.refresh_token,
        expires_at=int(time.time()) + response.expires_in,
        account_id=account_id,
    )


def _extract_account_id(access_token: str) -> str | None:
    try:
        parts = access_token.split(".")
        if len(parts) != 3:
            return None
        payload = parts[1] + "=" * (-len(parts[1]) % 4)
        decoded = json.loads(base64.urlsafe_b64decode(payload.encode("ascii")).decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(decoded, Mapping):
        return None
    auth = decoded.get(OPENAI_CODEX_JWT_AUTH_CLAIM)
    if not isinstance(auth, Mapping):
        return None
    account_id = auth.get("chatgpt_account_id")
    return account_id if isinstance(account_id, str) and account_id else None


@dataclass(slots=True)
class _StreamingFunctionCall:
    """Mutable accumulator for one streamed function-call output item."""

    item_id: str
    call_id: str | None = None
    name: str | None = None
    argument_deltas: list[str] = field(default_factory=list)
    final_arguments: str | None = None
    order_index: int = 0

    def arguments_json(self) -> str:
        if self.final_arguments is not None:
            return self.final_arguments
        return "".join(self.argument_deltas)


def _parse_sse_response(
    body: str,
    *,
    transport: str = "sse",
    stream_sink: StreamChunkSink | None = None,
    reasoning_sink: StreamChunkSink | None = None,
    event_stream: Iterator[Mapping[str, Any]] | None = None,
    cancel_token: CancelToken | None = None,
    progress: StreamProgress | None = None,
) -> ParsedOpenAICodexResponse:
    events: Iterator[Mapping[str, Any]] = (
        event_stream if event_stream is not None else _iter_sse_events(body)
    )
    return _parse_response_events(
        events,
        transport=transport,
        stream_sink=stream_sink,
        reasoning_sink=reasoning_sink,
        cancel_token=cancel_token,
        progress=progress,
    )


def _parse_response_events(
    events: Iterator[Mapping[str, Any]],
    *,
    transport: str = "unknown",
    stream_sink: StreamChunkSink | None = None,
    reasoning_sink: StreamChunkSink | None = None,
    cancel_token: CancelToken | None = None,
    progress: StreamProgress | None = None,
) -> ParsedOpenAICodexResponse:
    """Assemble a transport-neutral stream of accepted Responses events."""

    text_chunks: list[str] = []
    fallback_text_chunks: list[str] = []
    terminal_response: Mapping[str, Any] | None = None
    terminal_event_type: str | None = None
    function_call_items: dict[str, _StreamingFunctionCall] = {}
    next_call_order = 0

    attempt_progress = progress if progress is not None else StreamProgress()
    for event in events:
        if cancel_token is not None:
            cancel_token.raise_if_cancelled()
        # Progress changes before any event-specific processing, including
        # metadata, explicit errors, reasoning, text, and tool assembly.
        event_type = event.get("type")
        if event_type == "error" and event.get("code") == "websocket_connection_limit_reached":
            safe_code = _safe_codex_api_label(event.get("code"))
            pre_progress_metadata: dict[str, Any] = {
                "provider_response_store_requested": False,
                "response_status": "unknown",
            }
            if safe_code is not None:
                pre_progress_metadata["api_error_code"] = safe_code
            raise OpenAICodexResponseParseError(
                "OpenAI Codex stream returned an error event.",
                metadata=pre_progress_metadata,
            )
        attempt_progress.mark_event()
        if event_type == "response.reasoning_summary_text.delta":
            delta = event.get("delta")
            if reasoning_sink is not None and isinstance(delta, str) and delta:
                reasoning_sink(delta)
            continue
        if event_type == "response.reasoning_summary_part.added":
            # Part boundaries inside a reasoning summary; emit a paragraph
            # break so the rendered text retains Pi's section-like cadence.
            if reasoning_sink is not None:
                reasoning_sink("\n\n")
            continue
        if event_type == "error":
            safe_code = _safe_codex_api_label(event.get("code"))
            metadata: dict[str, Any] = {
                "provider_response_store_requested": False,
                "response_status": "unknown",
            }
            if safe_code is not None:
                metadata["api_error_code"] = safe_code
            raise OpenAICodexResponseParseError(
                "OpenAI Codex stream returned an error event.",
                metadata=metadata,
            )
        if event_type == "response.output_text.delta":
            delta = event.get("delta")
            if isinstance(delta, str):
                text_chunks.append(delta)
                if stream_sink is not None and delta:
                    stream_sink(delta)
            continue
        if event_type == "response.output_item.added":
            item = event.get("item")
            if isinstance(item, Mapping) and item.get("type") == "function_call":
                item_id = _safe_str(item.get("id"))
                if item_id is not None:
                    call = function_call_items.get(item_id)
                    if call is None:
                        call = _StreamingFunctionCall(
                            item_id=item_id,
                            call_id=_safe_str(item.get("call_id")),
                            name=_safe_str(item.get("name")),
                            order_index=next_call_order,
                        )
                        function_call_items[item_id] = call
                        next_call_order += 1
                    else:
                        # A delta or done event may have already created a
                        # placeholder for this item_id. Merge the metadata
                        # from `added` into that placeholder so finalization
                        # does not drop a partially-described call.
                        if call.call_id is None:
                            call.call_id = _safe_str(item.get("call_id"))
                        if call.name is None:
                            call.name = _safe_str(item.get("name"))
                    initial_arguments = item.get("arguments")
                    if isinstance(initial_arguments, str) and initial_arguments:
                        call.argument_deltas.append(initial_arguments)
            continue
        if event_type == "response.function_call_arguments.delta":
            item_id = _safe_str(event.get("item_id"))
            delta = event.get("delta")
            if item_id is None or not isinstance(delta, str):
                continue
            call = function_call_items.get(item_id)
            if call is None:
                call = _StreamingFunctionCall(
                    item_id=item_id, order_index=next_call_order
                )
                function_call_items[item_id] = call
                next_call_order += 1
            call.argument_deltas.append(delta)
            continue
        if event_type == "response.function_call_arguments.done":
            item_id = _safe_str(event.get("item_id"))
            if item_id is None:
                continue
            arguments = event.get("arguments")
            call = function_call_items.get(item_id)
            if call is None:
                call = _StreamingFunctionCall(
                    item_id=item_id, order_index=next_call_order
                )
                function_call_items[item_id] = call
                next_call_order += 1
            if isinstance(arguments, str):
                call.final_arguments = arguments
            continue
        if event_type == "response.output_item.done":
            item = event.get("item")
            if isinstance(item, Mapping):
                if item.get("type") == "function_call":
                    item_id = _safe_str(item.get("id"))
                    if item_id is not None:
                        call = function_call_items.get(item_id)
                        if call is None:
                            call = _StreamingFunctionCall(
                                item_id=item_id, order_index=next_call_order
                            )
                            function_call_items[item_id] = call
                            next_call_order += 1
                        if call.call_id is None:
                            call.call_id = _safe_str(item.get("call_id"))
                        if call.name is None:
                            call.name = _safe_str(item.get("name"))
                        final_arguments = item.get("arguments")
                        if isinstance(final_arguments, str):
                            call.final_arguments = final_arguments
                else:
                    fallback_text_chunks.extend(_extract_output_text_chunks(item))
            continue
        if event_type in {
            "response.cancelled",
            "response.completed",
            "response.done",
            "response.failed",
            "response.incomplete",
        }:
            terminal_event_type = event_type
            terminal_response = _event_response(event)
            # The first terminal event is authoritative. Closing a real SSE
            # generator runs its ``finally`` immediately and closes the HTTP
            # response, while preventing later bytes/events or a late socket
            # error from changing an already terminal outcome.
            _close_event_iterator(events)
            break

    # A cancel that shuts the socket down makes the stream end at EOF (a clean
    # iterator stop, not an exception), so re-check after the loop: a cancelled
    # turn must raise rather than return a partial "successful" result.
    if cancel_token is not None:
        cancel_token.raise_if_cancelled()

    if terminal_event_type is None:
        raise OpenAICodexStreamInterruptedError(
            "OpenAI Codex stream was interrupted before completion.",
            metadata={
                "phase": "stream",
                "provider_response_store_requested": False,
                "response_status": "unknown",
                "retryable": True,
                "transport": transport,
            },
        )

    terminal_failure_status = {
        "response.cancelled": "cancelled",
        "response.failed": "failed",
        "response.incomplete": "incomplete",
    }.get(terminal_event_type)
    if terminal_failure_status is not None:
        response_status = terminal_failure_status
        if terminal_response is None:
            terminal_response = {}
    elif terminal_response is None:
        response_status = "unknown"
        terminal_response = {}
    else:
        response_status = _safe_codex_response_status(terminal_response.get("status"))
    if response_status != "completed":
        raise OpenAICodexResponseParseError(
            "OpenAI Codex response did not complete successfully.",
            metadata={
                "provider_response_store_requested": False,
                "response_status": response_status,
            },
        )

    final_text_assembled = "".join(text_chunks) if text_chunks else "".join(fallback_text_chunks)
    final_text: str | None = final_text_assembled if final_text_assembled else None
    tool_calls = _finalize_streaming_function_calls(function_call_items)
    if final_text is None and not tool_calls:
        raise OpenAICodexResponseParseError(
            "OpenAI Codex response did not include final output text or tool calls.",
            metadata={
                "provider_response_store_requested": False,
                "response_status": response_status,
            },
        )

    return ParsedOpenAICodexResponse(
        final_text=final_text,
        usage=_extract_usage(terminal_response.get("usage")),
        response_status=response_status,
        tool_calls=tool_calls,
    )


def _finalize_streaming_function_calls(
    items: Mapping[str, _StreamingFunctionCall],
) -> tuple[ProviderToolCall, ...]:
    calls: list[ProviderToolCall] = []
    for call in sorted(items.values(), key=lambda entry: entry.order_index):
        if not call.name:
            continue
        correlation = _join_responses_tool_correlation(call.call_id, call.item_id)
        if not correlation:
            continue
        arguments_json = call.arguments_json()
        try:
            calls.append(
                ProviderToolCall(
                    provider_correlation_id=correlation[
                        : ProviderToolCall.PROVIDER_CORRELATION_ID_MAX_LENGTH
                    ],
                    tool_name=call.name[: ProviderToolCall.TOOL_NAME_MAX_LENGTH],
                    arguments_json=arguments_json[
                        : ProviderToolCall.ARGUMENTS_JSON_MAX_LENGTH
                    ],
                )
            )
        except ValueError:
            continue
    return tuple(calls)


def _safe_str(value: Any) -> str | None:
    if isinstance(value, str) and value:
        return value
    return None


def _iter_sse_events(body: str) -> Iterator[Mapping[str, Any]]:
    """Yield body-fixture SSE events lazily so terminals stop later parsing."""

    for block in body.replace("\r\n", "\n").split("\n\n"):
        data_lines = [
            line.removeprefix("data:").strip()
            for line in block.split("\n")
            if line.startswith("data:")
        ]
        if not data_lines:
            continue
        data = "\n".join(data_lines).strip()
        if not data or data == "[DONE]":
            continue
        yield _decode_sse_event(data)


def _decode_websocket_event(payload: str) -> Mapping[str, Any]:
    """Decode exactly one WebSocket JSON event without retaining server text."""

    return _decode_json_event(
        payload,
        malformed_message="OpenAI Codex WebSocket stream included malformed event JSON.",
        non_object_message="OpenAI Codex WebSocket stream included a non-object event.",
    )


def _decode_sse_event(payload: str) -> Mapping[str, Any]:
    """Decode exactly one SSE event without retaining server-controlled data."""

    return _decode_json_event(
        payload,
        malformed_message="OpenAI Codex stream included malformed event JSON.",
        non_object_message="OpenAI Codex stream included a non-object event.",
    )


def _decode_json_event(
    payload: str,
    *,
    malformed_message: str,
    non_object_message: str,
) -> Mapping[str, Any]:

    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise OpenAICodexResponseParseError(malformed_message) from exc
    if not isinstance(parsed, Mapping):
        raise OpenAICodexResponseParseError(non_object_message)
    return parsed


def _event_response(event: Mapping[str, Any]) -> Mapping[str, Any] | None:
    response = event.get("response")
    return response if isinstance(response, Mapping) else None


def _close_event_iterator(events: Iterator[Mapping[str, Any]]) -> None:
    close = getattr(events, "close", None)
    if callable(close):
        try:
            close()
        except Exception:  # noqa: BLE001 - best-effort terminal stream close
            pass


def _extract_output_text_chunks(item: Mapping[str, Any]) -> list[str]:
    content = item.get("content")
    if not isinstance(content, list):
        return []
    chunks: list[str] = []
    for content_item in content:
        if not isinstance(content_item, Mapping):
            continue
        if content_item.get("type") == "output_text" and isinstance(content_item.get("text"), str):
            chunks.append(content_item["text"])
    return chunks


def _extract_usage(value: Any) -> dict[str, int | float]:
    if not isinstance(value, Mapping):
        return {}
    usage: dict[str, Any] = {}
    for key in NORMALIZED_PROVIDER_USAGE_KEYS:
        usage[key] = value.get(key)

    for details_key, usage_key in OPENAI_CODEX_NESTED_USAGE_FIELDS:
        details = value.get(details_key)
        if isinstance(details, Mapping) and usage_key in details:
            usage[usage_key] = details[usage_key]

    return normalize_provider_usage(usage)


def _base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _single_query_value(params: Mapping[str, list[str]], key: str) -> str | None:
    values = params.get(key)
    if not values:
        return None
    return values[0] or None


@dataclass(slots=True)
class _LocalOAuthCallbackServer:
    server: HTTPServer | None
    thread: threading.Thread | None
    code_event: threading.Event
    parsed_input: ParsedAuthorizationInput | None = None

    @classmethod
    def start(cls, state: str) -> _LocalOAuthCallbackServer:
        code_event = threading.Event()
        holder = cls(server=None, thread=None, code_event=code_event)

        class CallbackHandler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                parsed = urllib.parse.urlparse(self.path)
                params = urllib.parse.parse_qs(parsed.query)
                if parsed.path != "/auth/callback":
                    self.send_response(404)
                    self.end_headers()
                    self.wfile.write(b"OpenAI Codex callback route not found.")
                    return
                callback_state = _single_query_value(params, "state")
                if callback_state != state:
                    self.send_response(400)
                    self.end_headers()
                    self.wfile.write(b"OpenAI Codex OAuth state mismatch.")
                    return
                code = _single_query_value(params, "code")
                if not code:
                    self.send_response(400)
                    self.end_headers()
                    self.wfile.write(b"OpenAI Codex OAuth code missing.")
                    return
                holder.parsed_input = ParsedAuthorizationInput(code=code, state=callback_state)
                holder.code_event.set()
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"OpenAI Codex authentication completed. You can close this window.")

            def log_message(self, format: str, *args: object) -> None:
                return

        try:
            server = HTTPServer(("127.0.0.1", 1455), CallbackHandler)
        except OSError:
            return holder
        thread = threading.Thread(target=server.handle_request, daemon=True)
        holder.server = server
        holder.thread = thread
        thread.start()
        return holder

    def wait_for_code(self, timeout_seconds: float = 300.0) -> ParsedAuthorizationInput:
        if self.server is None:
            return ParsedAuthorizationInput()
        self.code_event.wait(timeout_seconds)
        return self.parsed_input or ParsedAuthorizationInput()

    def close(self) -> None:
        if self.server is not None:
            try:
                self.server.server_close()
            except OSError:
                pass
