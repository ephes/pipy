"""Stdlib OAuth subscription provider registry (M7).

Pipy analogue of Pi's built-in OAuth registry (packages/ai/src/auth/oauth/):
Anthropic (Claude Pro/Max, PKCE + callback server), GitHub Copilot (device-code
login, token exchange, model list + policy enable, per-request ``proxy-ep`` base
URL, ``availableModelIds`` filtering), and OpenAI Codex (ChatGPT, PKCE — the
existing pipy provider).

Each provider implements ``refresh_token``/``get_api_key``; Copilot adds
``login``, ``request_base_url`` and ``filter_models``. A provider may also
implement ``modify_models`` (no built-in one does today). HTTP goes through an
injectable transport so the flows are testable against fakes; the default
transport is stdlib ``urllib``. :class:`OAuthCredentialCache` holds refreshed
per-request credentials without touching the auth store.

Credential dicts hold only token material (``access``/``refresh``/``expires``,
plus Copilot's ``enterpriseUrl`` and ``availableModelIds``).
Secrets, refresh tokens, PKCE verifiers, authorization URLs, and ``Authorization``
headers are never archived.
"""

from __future__ import annotations

import base64
import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from typing import Protocol, runtime_checkable

from pipy_harness.native.catalog import NativeModelSpec

Clock = Callable[[], int]


class Transport(Protocol):
    """Keyword-aware HTTP transport used by the built-in OAuth providers."""

    def __call__(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        data: str | None = None,
    ) -> tuple[int, str]: ...


def _now_ms() -> int:
    return int(time.time() * 1000)


def _default_transport(
    method: str,
    url: str,
    *,
    headers: Mapping[str, str] | None = None,
    data: str | None = None,
) -> tuple[int, str]:
    request = urllib.request.Request(
        url,
        method=method,
        data=data.encode("utf-8") if data is not None else None,
        headers=dict(headers or {}),
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
            return response.status, response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")
    except (urllib.error.URLError, OSError):
        return 0, ""


def _decode_b64(value: str) -> str:
    return base64.b64decode(value).decode("ascii")


def _json_object(body: str) -> dict[str, object]:
    parsed: object = json.loads(body)
    if not isinstance(parsed, dict) or not all(isinstance(key, str) for key in parsed):
        raise TypeError("OAuth response must be a JSON object")
    return {key: value for key, value in parsed.items() if isinstance(key, str)}


def _json_int(value: object) -> int:
    if not isinstance(value, (str, int, float)):
        raise TypeError("OAuth response value must be numeric")
    return int(value)


class OAuthProvider(Protocol):
    id: str

    def refresh_token(self, credentials: Mapping[str, object]) -> dict[str, object]: ...
    def get_api_key(self, credentials: Mapping[str, object]) -> str: ...


@runtime_checkable
class _OAuthModelModifierProvider(Protocol):
    def modify_models(
        self,
        rows: list[NativeModelSpec],
        credentials: Mapping[str, object],
    ) -> list[NativeModelSpec]: ...


# --------------------------------------------------------------------------- #
# Anthropic (Claude Pro/Max)
# --------------------------------------------------------------------------- #

_ANTHROPIC_TOKEN_URL = "https://platform.claude.com/v1/oauth/token"
_ANTHROPIC_CLIENT_ID = _decode_b64("OWQxYzI1MGEtZTYxYi00NGQ5LTg4ZWQtNTk0NGQxOTYyZjVl")
_ANTHROPIC_CALLBACK_PORT = 53692
_ANTHROPIC_CALLBACK_PATH = "/callback"
_FIVE_MINUTES_MS = 5 * 60 * 1000


class AnthropicOAuthProvider:
    id = "anthropic"
    token_url = _ANTHROPIC_TOKEN_URL
    client_id = _ANTHROPIC_CLIENT_ID
    callback_port = _ANTHROPIC_CALLBACK_PORT
    callback_path = _ANTHROPIC_CALLBACK_PATH

    def __init__(
        self, *, transport: Transport | None = None, now_ms: Clock | None = None
    ) -> None:
        self._transport = transport or _default_transport
        self._now_ms = now_ms or _now_ms

    def _token_request(self, payload: Mapping[str, object]) -> dict[str, object]:
        status, body = self._transport(
            "POST",
            self.token_url,
            headers={"Content-Type": "application/json"},
            data=json.dumps(payload),
        )
        if status != 200:
            raise OAuthError(f"anthropic token request failed ({status})")
        response = _json_object(body)
        return {
            "type": "oauth",
            "access": response["access_token"],
            "refresh": response["refresh_token"],
            # 5-minute safety margin (anthropic.ts).
            "expires": (
                self._now_ms()
                + _json_int(response["expires_in"]) * 1000
                - _FIVE_MINUTES_MS
            ),
        }

    def exchange_code(
        self, code: str, verifier: str, redirect_uri: str
    ) -> dict[str, object]:
        return self._token_request(
            {
                "grant_type": "authorization_code",
                "client_id": self.client_id,
                "code": code,
                "code_verifier": verifier,
                "redirect_uri": redirect_uri,
            }
        )

    def refresh_token(self, credentials: Mapping[str, object]) -> dict[str, object]:
        return self._token_request(
            {
                "grant_type": "refresh_token",
                "client_id": self.client_id,
                "refresh_token": credentials["refresh"],
            }
        )

    def get_api_key(self, credentials: Mapping[str, object]) -> str:
        return str(credentials["access"])


# --------------------------------------------------------------------------- #
# GitHub Copilot
# --------------------------------------------------------------------------- #

_COPILOT_CLIENT_ID = _decode_b64("SXYxLmI1MDdhMDhjODdlY2ZlOTg=")
_PROXY_EP = re.compile(r"proxy-ep=([^;]+)")
# Copilot editor headers required by the Copilot token, model and policy
# endpoints (github-copilot.ts ``COPILOT_HEADERS``).
_COPILOT_HEADERS = {
    "User-Agent": "GitHubCopilotChat/0.35.0",
    "Editor-Version": "vscode/1.107.0",
    "Editor-Plugin-Version": "copilot-chat/0.35.0",
    "Copilot-Integration-Id": "vscode-chat",
}
_COPILOT_API_VERSION = "2026-06-01"
COPILOT_INDIVIDUAL_BASE_URL = "https://api.individual.githubcopilot.com"
_DEVICE_GRANT_TYPE = "urn:ietf:params:oauth:grant-type:device_code"
# device-code.ts polling constants (RFC 8628).
_DEVICE_MIN_INTERVAL_S = 1.0
_DEVICE_DEFAULT_INTERVAL_S = 5.0
_DEVICE_SLOW_DOWN_INCREMENT_S = 5.0
_DEVICE_TIMEOUT_MESSAGE = "Device flow timed out"
_DEVICE_SLOW_DOWN_TIMEOUT_MESSAGE = (
    "Device flow timed out after one or more slow_down responses. This is often "
    "caused by clock drift in WSL or VM environments. Please sync or restart the "
    "VM clock and try again."
)
# Pi's ``fetchWithRateLimitRetry`` budgets: login retries a 429 twice within
# five seconds; refresh does not retry.
_LOGIN_RETRY = (2, 5.0)
_NO_RETRY = (0, 0.0)

Sleep = Callable[[float], None]
MonotonicClock = Callable[[], float]
PromptCallback = Callable[[Mapping[str, object]], str]
NotifyCallback = Callable[[Mapping[str, object]], None]


def copilot_base_url_from_token(token: str) -> str | None:
    """Extract the API base URL from a Copilot token's ``proxy-ep`` claim.

    Pi converts the ``proxy.`` host prefix to ``api.`` (github-copilot.ts).
    """

    match = _PROXY_EP.search(token)
    if not match:
        return None
    api_host = re.sub(r"^proxy\.", "api.", match.group(1))
    return f"https://{api_host}"


def copilot_base_url(token: str | None, enterprise_domain: str | None) -> str:
    """Pi ``getGitHubCopilotBaseUrl``: token proxy, enterprise, then default."""

    if token:
        from_token = copilot_base_url_from_token(token)
        if from_token:
            return from_token
    if enterprise_domain:
        return f"https://copilot-api.{enterprise_domain}"
    return COPILOT_INDIVIDUAL_BASE_URL


def normalize_copilot_domain(value: str) -> str | None:
    """Pi ``normalizeDomain``: a hostname from a bare domain or a URL."""

    trimmed = value.strip()
    if not trimmed:
        return None
    candidate = trimmed if "://" in trimmed else f"https://{trimmed}"
    try:
        hostname = urllib.parse.urlsplit(candidate).hostname
    except ValueError:
        return None
    return hostname or None


def _copilot_enterprise_domain(credentials: Mapping[str, object]) -> str | None:
    enterprise = credentials.get("enterpriseUrl")
    if not isinstance(enterprise, str) or not enterprise:
        return None
    return normalize_copilot_domain(enterprise)


def _known_copilot_model_ids() -> frozenset[str]:
    from pipy_harness.native.catalog_data import BUILTIN_MODEL_ROWS

    return frozenset(
        row.model_id
        for row in BUILTIN_MODEL_ROWS
        if row.provider_name == "github-copilot"
    )


def parse_copilot_model_catalog(
    raw: object, *, allow_policy_fallback: bool
) -> tuple[list[str], list[str]]:
    """Pi ``parseGitHubCopilotModelCatalog``: ``(available ids, policy ids)``."""

    data = raw.get("data") if isinstance(raw, dict) else None
    if not isinstance(data, list):
        raise OAuthError("Invalid Copilot models response")
    account: list[tuple[str, bool, object]] = []
    for item in data:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            continue
        capabilities = item.get("capabilities")
        supports = (
            capabilities.get("supports") if isinstance(capabilities, dict) else None
        )
        if isinstance(supports, dict) and supports.get("tool_calls") is False:
            continue
        policy = item.get("policy")
        state = policy.get("state") if isinstance(policy, dict) else None
        account.append((item["id"], item.get("model_picker_enabled") is True, state))
    picker = [
        model_id
        for model_id, enabled, state in account
        if enabled and state != "disabled"
    ]
    use_policy_fallback = allow_policy_fallback and not picker
    if picker or not allow_policy_fallback:
        available = picker
    else:
        available = [model_id for model_id, _, state in account if state == "enabled"]
    known = _known_copilot_model_ids()
    policy_ids = [
        model_id
        for model_id, enabled, state in account
        if state == "unconfigured"
        and model_id in known
        and (enabled or use_policy_fallback)
    ]
    return available, policy_ids


class GitHubCopilotOAuthProvider:
    """GitHub Copilot device-code OAuth (Pi ``auth/oauth/github-copilot.ts``).

    ``refresh`` holds the durable GitHub token; ``access`` is the short-lived
    Copilot token whose ``proxy-ep`` claim names the request base URL. Login
    and refresh record ``availableModelIds`` for :meth:`filter_models`.
    """

    id = "github-copilot"
    client_id = _COPILOT_CLIENT_ID

    def __init__(
        self,
        *,
        transport: Transport | None = None,
        now_ms: Clock | None = None,
        sleep: Sleep | None = None,
        monotonic: MonotonicClock | None = None,
    ) -> None:
        self._transport = transport or _default_transport
        self._now_ms = now_ms or _now_ms
        self._sleep = sleep or time.sleep
        self._monotonic = monotonic or time.monotonic

    # -- endpoints -----------------------------------------------------------

    @staticmethod
    def _urls(domain: str) -> dict[str, str]:
        return {
            "device": f"https://{domain}/login/device/code",
            "access": f"https://{domain}/login/oauth/access_token",
            "copilot": f"https://api.{domain}/copilot_internal/v2/token",
        }

    def _json_request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        data: str | None = None,
    ) -> object:
        status, body = self._transport(method, url, headers=headers, data=data)
        if not 200 <= status < 300:
            raise OAuthError(f"copilot request failed ({status})")
        try:
            return json.loads(body)
        except ValueError as exc:
            raise OAuthError("copilot response was not JSON") from exc

    def _rate_limited(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        data: str | None = None,
        retry: tuple[int, float],
    ) -> tuple[int, str]:
        """Pi ``fetchWithRateLimitRetry`` without ``Retry-After`` (no headers)."""

        max_retries, budget_s = retry
        deadline = self._monotonic() + budget_s
        attempt = 0
        while True:
            status, body = self._transport(method, url, headers=headers, data=data)
            if status != 429 or attempt >= max_retries:
                return status, body
            delay = 0.5 * 2**attempt
            if delay >= deadline - self._monotonic():
                return status, body
            self._sleep(delay)
            attempt += 1

    # -- token + model catalog -------------------------------------------------

    def exchange_token(
        self, github_token: str, enterprise_domain: str | None
    ) -> dict[str, object]:
        """Pi ``refreshGitHubCopilotAccessToken``: GitHub token -> Copilot token."""

        domain = enterprise_domain or "github.com"
        raw = self._json_request(
            "GET",
            self._urls(domain)["copilot"],
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {github_token}",
                **_COPILOT_HEADERS,
            },
        )
        token = raw.get("token") if isinstance(raw, dict) else None
        expires_at = raw.get("expires_at") if isinstance(raw, dict) else None
        expires_at_s = _number(expires_at)
        if not isinstance(token, str) or expires_at_s is None:
            raise OAuthError("Invalid Copilot token response fields")
        credentials: dict[str, object] = {
            "type": "oauth",
            "refresh": github_token,
            "access": token,
            "expires": int(expires_at_s * 1000) - _FIVE_MINUTES_MS,
        }
        if enterprise_domain:
            credentials["enterpriseUrl"] = enterprise_domain
        return credentials

    def fetch_models(
        self,
        copilot_token: str,
        enterprise_domain: str | None,
        *,
        retry: tuple[int, float] = _NO_RETRY,
    ) -> tuple[list[str], list[str]]:
        """Pi ``fetchGitHubCopilotModels``: ``(available ids, policy ids)``."""

        base_url = copilot_base_url(copilot_token, enterprise_domain)
        status, body = self._rate_limited(
            "GET",
            f"{base_url}/models",
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {copilot_token}",
                **_COPILOT_HEADERS,
                "X-GitHub-Api-Version": _COPILOT_API_VERSION,
            },
            retry=retry,
        )
        if not 200 <= status < 300:
            raise OAuthError(f"copilot models request failed ({status})")
        try:
            raw = json.loads(body)
        except ValueError as exc:
            raise OAuthError("Invalid Copilot models response") from exc
        # Some Individual accounts report no picker models despite enabled
        # policies; Pi limits the policy fallback to that endpoint.
        return parse_copilot_model_catalog(
            raw, allow_policy_fallback=base_url == COPILOT_INDIVIDUAL_BASE_URL
        )

    def enable_model(
        self, copilot_token: str, model_id: str, enterprise_domain: str | None = None
    ) -> bool:
        """Pi ``enableGitHubCopilotModel``; raises after an exhausted 429."""

        base_url = copilot_base_url(copilot_token, enterprise_domain)
        status, _ = self._rate_limited(
            "POST",
            f"{base_url}/models/{model_id}/policy",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {copilot_token}",
                **_COPILOT_HEADERS,
                "openai-intent": "chat-policy",
                "x-interaction-type": "chat-policy",
            },
            data=json.dumps({"state": "enabled"}),
            retry=_LOGIN_RETRY,
        )
        if status == 429:
            raise OAuthError("copilot policy request rate limited (429)")
        return 200 <= status < 300

    def enable_models(
        self,
        copilot_token: str,
        model_ids: list[str],
        enterprise_domain: str | None,
    ) -> list[str]:
        """Pi ``enableGitHubCopilotModels``: best effort, a 429 stops the batch."""

        enabled: list[str] = []
        for model_id in model_ids:
            try:
                if self.enable_model(copilot_token, model_id, enterprise_domain):
                    enabled.append(model_id)
            except OAuthError:
                break
        return enabled

    # -- device-code login -------------------------------------------------------

    def _start_device_flow(self, domain: str) -> dict[str, object]:
        raw = self._json_request(
            "POST",
            self._urls(domain)["device"],
            headers={
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": _COPILOT_HEADERS["User-Agent"],
            },
            data=urllib.parse.urlencode(
                {"client_id": self.client_id, "scope": "read:user"}
            ),
        )
        if not isinstance(raw, dict):
            raise OAuthError("Invalid device code response")
        interval = raw.get("interval")
        expires_in = raw.get("expires_in")
        if (
            not isinstance(raw.get("device_code"), str)
            or not isinstance(raw.get("user_code"), str)
            or not isinstance(raw.get("verification_uri"), str)
            or (interval is not None and _number(interval) is None)
            or _number(expires_in) is None
        ):
            raise OAuthError("Invalid device code response fields")
        # The URI is shown to the user; accept only http(s) URLs (Pi).
        uri = urllib.parse.urlsplit(str(raw["verification_uri"]))
        if uri.scheme not in ("http", "https") or not uri.netloc:
            raise OAuthError("Untrusted verification_uri in device code response")
        return raw

    def _poll_access_token(self, domain: str, device: Mapping[str, object]) -> str:
        """Pi ``pollOAuthDeviceCodeFlow`` with ``waitBeforeFirstPoll``."""

        deadline = self._monotonic() + (_number(device.get("expires_in")) or 0.0)
        interval = max(
            _DEVICE_MIN_INTERVAL_S,
            _number(device.get("interval")) or _DEVICE_DEFAULT_INTERVAL_S,
        )
        slow_downs = 0
        remaining = deadline - self._monotonic()
        if remaining > 0:
            self._sleep(min(interval, remaining))
        while self._monotonic() < deadline:
            raw = self._json_request(
                "POST",
                self._urls(domain)["access"],
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/x-www-form-urlencoded",
                    "User-Agent": _COPILOT_HEADERS["User-Agent"],
                },
                data=urllib.parse.urlencode(
                    {
                        "client_id": self.client_id,
                        "device_code": str(device["device_code"]),
                        "grant_type": _DEVICE_GRANT_TYPE,
                    }
                ),
            )
            if isinstance(raw, dict) and isinstance(raw.get("access_token"), str):
                return str(raw["access_token"])
            if not isinstance(raw, dict) or not isinstance(raw.get("error"), str):
                raise OAuthError("Invalid device token response")
            error = str(raw["error"])
            if error == "slow_down":
                slow_downs += 1
                server_interval = _number(raw.get("interval"))
                if server_interval is not None and server_interval > 0:
                    interval = max(_DEVICE_MIN_INTERVAL_S, server_interval)
                else:
                    interval = max(
                        _DEVICE_MIN_INTERVAL_S, interval + _DEVICE_SLOW_DOWN_INCREMENT_S
                    )
            elif error != "authorization_pending":
                description = raw.get("error_description")
                suffix = f": {description}" if description else ""
                raise OAuthError(f"Device flow failed: {error}{suffix}")
            remaining = deadline - self._monotonic()
            if remaining <= 0:
                break
            self._sleep(min(interval, remaining))
        raise OAuthError(
            _DEVICE_SLOW_DOWN_TIMEOUT_MESSAGE if slow_downs else _DEVICE_TIMEOUT_MESSAGE
        )

    def login(
        self, *, prompt: PromptCallback, notify: NotifyCallback
    ) -> dict[str, object]:
        """Pi ``loginGitHubCopilot``: enterprise prompt, device flow, models."""

        answer = prompt(
            {
                "type": "text",
                "message": "GitHub Enterprise URL/domain (blank for github.com)",
                "placeholder": "company.ghe.com",
            }
        )
        enterprise_domain = normalize_copilot_domain(answer)
        if answer.strip() and not enterprise_domain:
            raise OAuthError("Invalid GitHub Enterprise URL/domain")
        domain = enterprise_domain or "github.com"
        device = self._start_device_flow(domain)
        notify(
            {
                "type": "device_code",
                "userCode": device["user_code"],
                "verificationUri": device["verification_uri"],
                "intervalSeconds": device.get("interval"),
                "expiresInSeconds": device["expires_in"],
            }
        )
        github_token = self._poll_access_token(domain, device)
        credentials = self.exchange_token(github_token, enterprise_domain)
        access = str(credentials["access"])
        available, policy_ids = self.fetch_models(
            access, enterprise_domain, retry=_LOGIN_RETRY
        )
        enabled: list[str] = []
        if policy_ids:
            notify({"type": "progress", "message": "Enabling models..."})
            enabled = self.enable_models(access, policy_ids, enterprise_domain)
        credentials["availableModelIds"] = list(dict.fromkeys([*available, *enabled]))
        return credentials

    # -- refresh + per-request auth -------------------------------------------

    def refresh_token(self, credentials: Mapping[str, object]) -> dict[str, object]:
        """Pi ``refreshGitHubCopilotToken``: re-exchange, then re-list models."""

        refresh = credentials.get("refresh")
        if not isinstance(refresh, str) or not refresh:
            raise OAuthError("copilot credential has no refresh token")
        enterprise_domain = _copilot_enterprise_domain(credentials)
        refreshed = self.exchange_token(refresh, enterprise_domain)
        available, _policy = self.fetch_models(
            str(refreshed["access"]), enterprise_domain
        )
        refreshed["availableModelIds"] = available
        return refreshed

    def get_api_key(self, credentials: Mapping[str, object]) -> str:
        return str(credentials["access"])

    def request_base_url(self, credentials: Mapping[str, object]) -> str:
        """Pi ``toAuth`` base URL for this credential."""

        access = credentials.get("access")
        return copilot_base_url(
            access if isinstance(access, str) else None,
            _copilot_enterprise_domain(credentials),
        )

    def filter_models(
        self, rows: list[NativeModelSpec], credentials: Mapping[str, object] | None
    ) -> list[NativeModelSpec]:
        """Pi ``filterModels``: keep the credential's ``availableModelIds``."""

        if credentials is None or credentials.get("type") != "oauth":
            return rows
        available = credentials.get("availableModelIds")
        if not isinstance(available, list) or not all(
            isinstance(model_id, str) for model_id in available
        ):
            return rows
        keep = set(available)
        return [
            row for row in rows if row.provider_name != self.id or row.model_id in keep
        ]


def _number(value: object) -> float | None:
    """A JSON number as ``float`` (``bool`` excluded), else ``None``."""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


# --------------------------------------------------------------------------- #
# OpenAI Codex (ChatGPT)
# --------------------------------------------------------------------------- #

_CODEX_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
_CODEX_TOKEN_URL = "https://auth.openai.com/oauth/token"


class OpenAICodexOAuthProvider:
    id = "openai-codex"
    client_id = _CODEX_CLIENT_ID
    token_url = _CODEX_TOKEN_URL

    def __init__(
        self, *, transport: Transport | None = None, now_ms: Clock | None = None
    ) -> None:
        self._transport = transport or _default_transport
        self._now_ms = now_ms or _now_ms

    def refresh_token(self, credentials: Mapping[str, object]) -> dict[str, object]:
        status, body = self._transport(
            "POST",
            self.token_url,
            headers={"Content-Type": "application/json"},
            data=json.dumps(
                {
                    "grant_type": "refresh_token",
                    "client_id": self.client_id,
                    "refresh_token": credentials["refresh"],
                }
            ),
        )
        if status != 200:
            raise OAuthError(f"openai-codex token request failed ({status})")
        response = _json_object(body)
        return {
            "type": "oauth",
            "access": response["access_token"],
            "refresh": response["refresh_token"],
            # No safety margin for Codex (openai-codex.ts).
            "expires": self._now_ms() + _json_int(response["expires_in"]) * 1000,
        }

    def get_api_key(self, credentials: Mapping[str, object]) -> str:
        return str(credentials["access"])


class OAuthError(Exception):
    pass


# --------------------------------------------------------------------------- #
# Registry
# --------------------------------------------------------------------------- #

_OAuthProviderFactory = Callable[[], OAuthProvider]


_BUILTIN_OAUTH_PROVIDERS: dict[str, _OAuthProviderFactory] = {
    "anthropic": AnthropicOAuthProvider,
    "github-copilot": GitHubCopilotOAuthProvider,
    "openai-codex": OpenAICodexOAuthProvider,
}


def get_oauth_provider_ids() -> list[str]:
    return list(_BUILTIN_OAUTH_PROVIDERS)


def get_oauth_provider(provider_id: str) -> OAuthProvider | None:
    cls = _BUILTIN_OAUTH_PROVIDERS.get(provider_id)
    return cls() if cls is not None else None


# Built-in OAuth providers whose request auth pipy resolves per request. Their
# refresh material does not rotate on refresh (Copilot's GitHub token), so a
# per-process in-memory refresh never invalidates the stored credential.
PER_REQUEST_OAUTH_PROVIDERS = frozenset({"github-copilot"})
_MIN_VALIDITY_MS = _FIVE_MINUTES_MS


class OAuthCredentialCache:
    """Freshest refreshed OAuth credential per provider, shared across threads.

    Pi refreshes a stored OAuth credential that expires within five minutes
    (``auth/resolve.ts``) and persists it under a store lock. Pipy keeps the
    refreshed credential here instead: this object owns its own lock and holds
    no ``AuthStore`` reference, so a worker thread can refresh without touching
    the single-thread auth store. Callers pass a snapshot of the stored
    credential taken on the owner thread.

    Two locks: ``_refresh_lock`` serializes refreshes (one network refresh per
    expiry, the re-check happens under it), and ``_lock`` guards only the
    dictionary, so :meth:`current` never waits on a refresh's network call.
    """

    def __init__(
        self,
        *,
        providers: Callable[[str], OAuthProvider | None] | None = None,
        now_ms: Clock | None = None,
    ) -> None:
        self._providers = providers or get_oauth_provider
        self._now_ms = now_ms or _now_ms
        self._lock = threading.Lock()
        self._refresh_lock = threading.Lock()
        self._fresh: dict[str, dict[str, object]] = {}

    def _newest(
        self, provider_id: str, snapshot: Mapping[str, object]
    ) -> dict[str, object]:
        cached = self._fresh.get(provider_id)
        if (
            cached is not None
            and cached.get("refresh") == snapshot.get("refresh")
            and _expires(cached) >= _expires(snapshot)
        ):
            return dict(cached)
        return dict(snapshot)

    def current(
        self, provider_id: str, snapshot: Mapping[str, object]
    ) -> dict[str, object]:
        """The newest known credential for ``snapshot``, without refreshing."""

        with self._lock:
            return self._newest(provider_id, snapshot)

    def fresh(
        self, provider_id: str, snapshot: Mapping[str, object]
    ) -> dict[str, object]:
        """A credential valid for at least five minutes; refreshes if needed.

        Raises :class:`OAuthError` when the refresh fails.
        """

        with self._refresh_lock:
            credential = self.current(provider_id, snapshot)
            if self._now_ms() + _MIN_VALIDITY_MS < _expires(credential):
                return credential
            provider = self._providers(provider_id)
            if provider is None:
                raise OAuthError(f"no OAuth provider for {provider_id}")
            try:
                refreshed = dict(provider.refresh_token(credential))
            except OAuthError:
                raise
            except Exception as exc:  # noqa: BLE001 - transport/parse failures
                raise OAuthError(f"OAuth refresh failed for {provider_id}") from exc
            with self._lock:
                self._fresh[provider_id] = refreshed
            return dict(refreshed)


def _expires(credentials: Mapping[str, object]) -> int:
    value = credentials.get("expires")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return int(value)
