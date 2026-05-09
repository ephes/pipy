"""OpenAI Codex subscription provider for the native pipy runtime."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import stat
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Protocol, TextIO

from pipy_harness.capture import sanitize_text
from pipy_harness.models import HarnessStatus
from pipy_harness.native.models import ProviderRequest, ProviderResult
from pipy_harness.native.usage import NORMALIZED_PROVIDER_USAGE_KEYS, normalize_provider_usage

OPENAI_CODEX_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
OPENAI_CODEX_AUTHORIZE_URL = "https://auth.openai.com/oauth/authorize"
OPENAI_CODEX_TOKEN_URL = "https://auth.openai.com/oauth/token"
OPENAI_CODEX_REDIRECT_URI = "http://localhost:1455/auth/callback"
OPENAI_CODEX_SCOPE = "openid profile email offline_access"
OPENAI_CODEX_RESPONSES_URL = "https://chatgpt.com/backend-api/codex/responses"
OPENAI_CODEX_JWT_AUTH_CLAIM = "https://api.openai.com/auth"
OPENAI_CODEX_MIN_TOKEN_TTL_SECONDS = 60
OPENAI_CODEX_NESTED_USAGE_FIELDS: tuple[tuple[str, str], ...] = (
    ("input_tokens_details", "cached_tokens"),
    ("output_tokens_details", "reasoning_tokens"),
)


@dataclass(frozen=True, slots=True)
class JsonResponse:
    """Small JSON response boundary used by provider tests."""

    status_code: int
    body: Mapping[str, Any]


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


class JsonHTTPClient(Protocol):
    """Minimal injectable JSON HTTP client."""

    def post_json(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        body: Mapping[str, Any],
        timeout_seconds: float,
    ) -> JsonResponse:
        """POST JSON and return parsed JSON metadata."""


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
class UrllibJsonHTTPClient:
    """Standard-library JSON client for Codex Responses calls."""

    def post_json(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        body: Mapping[str, Any],
        timeout_seconds: float,
    ) -> JsonResponse:
        encoded = json.dumps(body).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=encoded,
            headers=dict(headers),
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                payload = response.read()
                status_code = response.getcode()
        except urllib.error.HTTPError as exc:
            raise OpenAICodexHTTPStatusError.from_http_error(exc) from exc
        except urllib.error.URLError as exc:
            reason = sanitize_text(str(exc.reason)) if getattr(exc, "reason", None) else "request failed"
            raise OpenAICodexTransportError(f"OpenAI Codex request failed: {reason}") from exc

        return JsonResponse(status_code=status_code, body=_decode_json_object(payload))


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

        body = _decode_json_object(payload)
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


@dataclass(frozen=True, slots=True)
class OpenAICodexResponsesProvider:
    """One-turn OpenAI Codex Responses provider behind ProviderPort."""

    model_id: str
    auth_manager: OpenAICodexAuthManager = field(default_factory=OpenAICodexAuthManager)
    http_client: JsonHTTPClient = field(default_factory=UrllibJsonHTTPClient)
    endpoint: str = OPENAI_CODEX_RESPONSES_URL
    timeout_seconds: float = 60.0

    @property
    def name(self) -> str:
        return "openai-codex"

    def complete(self, request: ProviderRequest) -> ProviderResult:
        started_at = _utc_now()
        if not self.model_id or not self.model_id.strip():
            return _failed_result(
                request,
                provider_name=self.name,
                started_at=started_at,
                error_type="OpenAICodexConfigurationError",
                error_message="--native-model is required for native provider openai-codex.",
            )

        try:
            credentials = self.auth_manager.get_credentials()
        except OpenAICodexProviderError as exc:
            return _failed_result(
                request,
                provider_name=self.name,
                started_at=started_at,
                error_type=type(exc).__name__,
                error_message=str(exc),
                metadata=exc.metadata,
            )
        if credentials is None:
            return _failed_result(
                request,
                provider_name=self.name,
                started_at=started_at,
                error_type="OpenAICodexAuthError",
                error_message=(
                    "OpenAI Codex login is required. "
                    "Run `pipy auth openai-codex login` before using native provider openai-codex."
                ),
            )

        body = {
            "model": self.model_id,
            "instructions": request.system_prompt,
            "input": request.user_prompt,
            "store": False,
            "stream": False,
        }
        headers = {
            "Authorization": f"Bearer {credentials.access_token}",
            "Content-Type": "application/json",
            "OpenAI-Beta": "responses=experimental",
            "chatgpt-account-id": credentials.account_id,
            "originator": "pipy",
            "User-Agent": "pipy",
        }

        try:
            response = self.http_client.post_json(
                self.endpoint,
                headers=headers,
                body=body,
                timeout_seconds=self.timeout_seconds,
            )
            if response.status_code < 200 or response.status_code >= 300:
                raise OpenAICodexHTTPStatusError(
                    f"OpenAI Codex request failed with HTTP status {response.status_code}.",
                    metadata={"http_status": response.status_code},
                )
            result = _parse_response(response.body)
        except OpenAICodexProviderError as exc:
            return _failed_result(
                request,
                provider_name=self.name,
                started_at=started_at,
                error_type=type(exc).__name__,
                error_message=str(exc),
                metadata=exc.metadata,
            )

        return ProviderResult(
            status=HarnessStatus.SUCCEEDED,
            provider_name=self.name,
            model_id=self.model_id,
            started_at=started_at,
            ended_at=_utc_now(),
            final_text=result.final_text,
            usage=result.usage,
            metadata={
                "provider_response_store_requested": False,
                "response_status": result.response_status,
            },
        )


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
    final_text: str
    usage: dict[str, int | float]
    response_status: str


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
    def from_http_error(cls, exc: urllib.error.HTTPError) -> OpenAICodexHTTPStatusError:
        metadata: dict[str, Any] = {"http_status": exc.code}
        try:
            body = _decode_json_object(exc.read())
        except OpenAICodexResponseParseError:
            body = {}
        error = body.get("error")
        if isinstance(error, Mapping):
            error_type = error.get("type")
            error_code = error.get("code")
            if isinstance(error_type, str):
                metadata["api_error_type"] = sanitize_text(error_type)
            if isinstance(error_code, str | int):
                metadata["api_error_code"] = sanitize_text(str(error_code))
        return cls(f"OpenAI Codex request failed with HTTP status {exc.code}.", metadata=metadata)


class OpenAICodexTransportError(OpenAICodexProviderError):
    """Raised when the HTTP request cannot reach OpenAI Codex."""


class OpenAICodexResponseParseError(OpenAICodexProviderError):
    """Raised when the Codex response shape is unsupported."""


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


def _parse_response(body: Mapping[str, Any]) -> ParsedOpenAICodexResponse:
    status = body.get("status")
    response_status = _safe_response_label(status, default="unknown")
    if response_status and response_status != "completed":
        raise OpenAICodexResponseParseError(
            f"OpenAI Codex response status was {response_status}.",
            metadata={
                "provider_response_store_requested": False,
                "response_status": response_status,
            },
        )

    final_text = _extract_final_text(body)
    if not final_text:
        raise OpenAICodexResponseParseError(
            "OpenAI Codex response did not include final output text.",
            metadata={
                "provider_response_store_requested": False,
                "response_status": response_status,
            },
        )

    return ParsedOpenAICodexResponse(
        final_text=final_text,
        usage=_extract_usage(body.get("usage")),
        response_status=response_status,
    )


def _extract_final_text(body: Mapping[str, Any]) -> str | None:
    output_text = body.get("output_text")
    if isinstance(output_text, str):
        return output_text

    output = body.get("output")
    if not isinstance(output, list):
        return None

    chunks: list[str] = []
    for item in output:
        if not isinstance(item, Mapping):
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for content_item in content:
            if not isinstance(content_item, Mapping):
                continue
            if content_item.get("type") == "output_text" and isinstance(content_item.get("text"), str):
                chunks.append(content_item["text"])
    if not chunks:
        return None
    return "".join(chunks)


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


def _decode_json_object(payload: bytes) -> Mapping[str, Any]:
    try:
        decoded = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OpenAICodexResponseParseError("OpenAI Codex returned non-JSON response metadata.") from exc
    if not isinstance(decoded, Mapping):
        raise OpenAICodexResponseParseError("OpenAI Codex returned unsupported JSON response metadata.")
    return decoded


def _safe_response_label(value: Any, *, default: str) -> str:
    if not isinstance(value, str) or not value:
        return default
    sanitized = sanitize_text(value)
    return sanitized if sanitized != "[REDACTED]" else default


def _failed_result(
    request: ProviderRequest,
    *,
    provider_name: str,
    started_at: datetime,
    error_type: str,
    error_message: str,
    metadata: Mapping[str, Any] | None = None,
) -> ProviderResult:
    return ProviderResult(
        status=HarnessStatus.FAILED,
        provider_name=provider_name,
        model_id=request.model_id,
        started_at=started_at,
        ended_at=_utc_now(),
        metadata=dict(metadata or {}),
        error_type=sanitize_text(error_type),
        error_message=sanitize_text(error_message),
    )


def _base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _single_query_value(params: Mapping[str, list[str]], key: str) -> str | None:
    values = params.get(key)
    if not values:
        return None
    return values[0] or None


def _utc_now() -> datetime:
    return datetime.now(UTC)


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
