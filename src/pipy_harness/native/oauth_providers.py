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
import hashlib
import html
import json
import math
import os
import re
import secrets
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Protocol, runtime_checkable

from pipy_harness.capture import sanitize_text
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


def generate_pkce() -> tuple[str, str]:
    """Pi ``auth/oauth/pkce.ts``: ``(verifier, S256 challenge)``, base64url."""

    verifier = _base64url(secrets.token_bytes(32))
    challenge = _base64url(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge


def _base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


# --------------------------------------------------------------------------- #
# OpenAI (Sign in with ChatGPT)
# --------------------------------------------------------------------------- #

# Pi ``auth/oauth/openai-chatgpt.ts``: every login registers a new client with
# this ID; OpenAI returns the issued client ID in the callback.
_CHATGPT_DYNAMIC_CLIENT_ID = "dynamic_agent_client"
# Pi sends "Pi"; the consent screen names the app the user runs (pipy's Codex
# flow sends ``originator: "pipy"`` for the same reason).
_CHATGPT_AGENT_NAME_HINT = "pipy"
_UUID_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE
)
CHATGPT_AUTHORIZE_URL = "https://auth.openai.com/api/accounts/authorize"
CHATGPT_TOKEN_URL = "https://auth.openai.com/api/accounts/oauth/token"
_CHATGPT_RESOURCE = "https://api.openai.com/v1"
_CHATGPT_CALLBACK_PORT = 1455
_CHATGPT_CALLBACK_PATH = "/auth/callback"
_CHATGPT_REDIRECT_URI = (
    f"http://127.0.0.1:{_CHATGPT_CALLBACK_PORT}{_CHATGPT_CALLBACK_PATH}"
)
_CHATGPT_DIRECT_TOKEN_SCOPE = "chatgpt.tokens.use.direct"
_CHATGPT_SCOPE = (
    f"openid profile email offline_access resource.invoke {_CHATGPT_DIRECT_TOKEN_SCOPE}"
)
# Refresh this long before the real expiry (Pi ``EXPIRY_MARGIN_MS``).
_CHATGPT_EXPIRY_MARGIN_MS = 3 * 60 * 1000
_CALLBACK_POLL_SECONDS = 0.2


def _random_value() -> str:
    return _base64url(secrets.token_bytes(32))


def chatgpt_agent_host_id(device_id: object) -> str:
    """Pi ``agentHostId``: ``urn:uuid:<lower-cased device UUID>``."""

    if not isinstance(device_id, str) or not _UUID_PATTERN.match(device_id):
        raise OAuthError(
            "Sign in with ChatGPT requires a device ID (UUID) for this installation"
        )
    return f"urn:uuid:{device_id.lower()}"


def _query_value(params: Mapping[str, list[str]], key: str) -> str | None:
    values = params.get(key)
    return values[0] if values else None


def _chatgpt_authorization_from_callback(
    query: str, expected_state: str
) -> tuple[str, str]:
    """Pi ``authorizationResultFromCallback``: ``(code, issued client id)``."""

    params = urllib.parse.parse_qs(query, keep_blank_values=True)
    code = _query_value(params, "code")
    if not code:
        raise OAuthError("Missing authorization code")
    state = _query_value(params, "state")
    if not state:
        raise OAuthError("Missing OAuth state")
    if state != expected_state:
        raise OAuthError("OAuth state mismatch")
    client_id = (_query_value(params, "client_id") or "").strip()
    if not client_id:
        raise OAuthError(
            "OpenAI OAuth registration callback did not contain an issued client ID"
        )
    return code, client_id


def _chatgpt_authorization_from_manual_input(
    value: str, expected_state: str, redirect_uri: str
) -> tuple[str, str]:
    """Pi ``authorizationResultFromManualInput``: the pasted callback URL."""

    try:
        url = urllib.parse.urlsplit(value.strip())
        port = url.port
    except ValueError:
        url = None
        port = None
    if url is None or not url.scheme or not url.netloc:
        raise OAuthError("Paste the full callback URL from the browser")
    expected = urllib.parse.urlsplit(redirect_uri)
    same_origin = (url.scheme.lower(), (url.hostname or ""), port) == (
        expected.scheme.lower(),
        expected.hostname or "",
        expected.port,
    )
    if not same_origin or (url.path or "/") != expected.path:
        raise OAuthError(f"The pasted callback URL must start with {redirect_uri}")
    params = urllib.parse.parse_qs(url.query, keep_blank_values=True)
    error = _query_value(params, "error")
    if error:
        raise _authorization_failed(error)
    return _chatgpt_authorization_from_callback(url.query, expected_state)


def _authorization_failed(error: str) -> OAuthError:
    """Pi's ``ChatGPT authorization failed: <error>``.

    ``error`` comes from the callback URL, so it is redacted when it looks
    secret-bearing; the flow's other messages are fixed text.
    """

    return OAuthError(f"ChatGPT authorization failed: {sanitize_text(error)}")


_OAUTH_PAGE = (
    '<!doctype html><html><head><meta charset="utf-8"><title>pipy</title>'
    "</head><body><p>{message}</p></body></html>"
)


class _ChatGPTCallbackServer:
    """Pi ``startCallbackServer``: the local ``/auth/callback`` listener.

    An invalid callback answers 400 and keeps waiting; an ``error`` callback
    answers 400 and fails the login; a valid one answers 200 and completes it.
    """

    def __init__(self, host: str, port: int, expected_state: str) -> None:
        self._done = threading.Event()
        self._finish_lock = threading.Lock()
        self.result: tuple[str, str] | None = None
        self.error: OAuthError | None = None
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 - http.server API
                try:
                    url = urllib.parse.urlsplit(self.path)
                    if url.path != _CHATGPT_CALLBACK_PATH:
                        self._send(404, "Callback route not found.")
                        return
                    params = urllib.parse.parse_qs(url.query, keep_blank_values=True)
                    error = _query_value(params, "error")
                    if error:
                        self._send(400, f"ChatGPT was not connected. Error: {error}")
                        owner._finish(error=_authorization_failed(error))
                        return
                    try:
                        result = _chatgpt_authorization_from_callback(
                            url.query, expected_state
                        )
                    except OAuthError as exc:
                        self._send(400, str(exc))
                        return
                    self._send(
                        200,
                        "ChatGPT authentication completed. You can close this window.",
                    )
                    owner._finish(result=result)
                except Exception:  # noqa: BLE001 - Pi answers 500 and keeps waiting
                    self._send(500, "Internal error while processing the callback.")

            def _send(self, status: int, message: str) -> None:
                body = _OAUTH_PAGE.format(message=html.escape(message)).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format: str, *args: object) -> None:  # noqa: A002
                return

        self._server = _CallbackHTTPServer((host, port), Handler)
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            kwargs={"poll_interval": _CALLBACK_POLL_SECONDS},
            name="pipy-chatgpt-oauth-callback",
            daemon=True,
        )
        self._thread.start()

    @property
    def port(self) -> int:
        return int(self._server.server_address[1])

    def _finish(
        self, *, result: tuple[str, str] | None = None, error: OAuthError | None = None
    ) -> None:
        # Handler threads run concurrently: the first outcome wins.
        with self._finish_lock:
            if self._done.is_set():
                return
            self.result = result
            self.error = error
            self._done.set()

    def wait(self) -> tuple[str, str]:
        while not self._done.wait(_CALLBACK_POLL_SECONDS):
            pass
        if self.error is not None:
            raise self.error
        assert self.result is not None
        return self.result

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        # Pi ``closeAllConnections``: a browser's spare pre-opened connection
        # would otherwise stay attached to this closed listener.
        self._server.close_all_connections()
        self._thread.join(timeout=5)


class _CallbackHTTPServer(ThreadingHTTPServer):
    """One thread per connection, so an idle browser pre-connection cannot
    block the callback; open connections are tracked so ``close`` can drop
    them (Pi ``closeAllConnections``)."""

    daemon_threads = True
    block_on_close = False

    def __init__(
        self,
        address: tuple[str, int],
        handler: type[BaseHTTPRequestHandler],
    ) -> None:
        super().__init__(address, handler)
        self._connections: set[socket.socket] = set()
        self._connections_lock = threading.Lock()

    def process_request(self, request: object, client_address: object) -> None:
        if isinstance(request, socket.socket):
            with self._connections_lock:
                self._connections.add(request)
        super().process_request(request, client_address)  # type: ignore[arg-type]

    def shutdown_request(self, request: object) -> None:
        if isinstance(request, socket.socket):
            with self._connections_lock:
                self._connections.discard(request)
        super().shutdown_request(request)  # type: ignore[arg-type]

    def close_all_connections(self) -> None:
        with self._connections_lock:
            connections = list(self._connections)
            self._connections.clear()
        for connection in connections:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                connection.close()
            except OSError:
                pass


class OpenAIChatGPTOAuthProvider:
    """Sign in with ChatGPT for the ``openai`` provider (Pi ``openai-chatgpt.ts``).

    A public-client PKCE flow that registers a user-owned client (the issued
    ``client_id`` arrives in the callback) and sends the resulting user access
    token directly to ``api.openai.com``. The credential holds ``access``,
    ``refresh``, ``expires`` (three-minute margin), ``clientId`` and the granted
    ``scopes``. Refresh rotates the refresh token.
    """

    id = "openai"
    name = "OpenAI (ChatGPT subscription)"
    login_label = "Sign in with ChatGPT"
    is_subscription = True

    def __init__(
        self,
        *,
        transport: Transport | None = None,
        now_ms: Clock | None = None,
        authorize_url: str = CHATGPT_AUTHORIZE_URL,
        token_url: str = CHATGPT_TOKEN_URL,
        callback_host: str | None = None,
        callback_port: int = _CHATGPT_CALLBACK_PORT,
    ) -> None:
        self._transport = transport or _default_transport
        self._now_ms = now_ms or _now_ms
        self._authorize_url = authorize_url
        self._token_url = token_url
        self._callback_host = callback_host
        self._callback_port = callback_port

    # -- token endpoint --------------------------------------------------------

    def _request_token(self, fields: Mapping[str, str]) -> dict[str, object]:
        status, body = self._transport(
            "POST",
            self._token_url,
            headers={
                "accept": "application/json",
                "content-type": "application/x-www-form-urlencoded",
            },
            data=urllib.parse.urlencode(dict(fields)),
        )
        if not 200 <= status < 300:
            # The response body is redacted when it looks secret-bearing.
            detail = sanitize_text(body) or "request failed"
            raise OAuthError(f"OpenAI OAuth token request failed ({status}): {detail}")
        try:
            parsed: object = json.loads(body)
        except ValueError as exc:
            raise OAuthError("OpenAI OAuth token response was not JSON") from exc
        if not isinstance(parsed, dict):
            raise OAuthError("OpenAI OAuth token response must be an object")
        return {str(key): value for key, value in parsed.items()}

    def _credential_from_token(
        self, token: Mapping[str, object], client_id: str
    ) -> dict[str, object]:
        """Pi ``credentialFromTokenResponse``."""

        access = _require_token_string(token.get("access_token"), "access_token")
        refresh = _require_token_string(token.get("refresh_token"), "refresh_token")
        scope = _require_token_string(token.get("scope"), "scope")
        expires_in = token.get("expires_in")
        if (
            isinstance(expires_in, bool)
            or not isinstance(expires_in, (int, float))
            or not math.isfinite(expires_in)
            or expires_in <= 0
        ):
            raise OAuthError("OpenAI OAuth token response has invalid expires_in")
        scopes = scope.split()
        if _CHATGPT_DIRECT_TOKEN_SCOPE not in scopes:
            raise OAuthError(
                f"OpenAI OAuth grant did not include {_CHATGPT_DIRECT_TOKEN_SCOPE}"
            )
        return {
            "type": "oauth",
            "access": access,
            "refresh": refresh,
            "expires": int(
                self._now_ms() + expires_in * 1000 - _CHATGPT_EXPIRY_MARGIN_MS
            ),
            "clientId": client_id,
            "scopes": scopes,
        }

    def exchange_code(
        self, code: str, verifier: str, client_id: str
    ) -> dict[str, object]:
        """Pi ``exchangeAuthorizationCode`` (requires an ``id_token``)."""

        token = self._request_token(
            {
                "grant_type": "authorization_code",
                "client_id": client_id,
                "code": code,
                "code_verifier": verifier,
                "redirect_uri": _CHATGPT_REDIRECT_URI,
                "resource": _CHATGPT_RESOURCE,
            }
        )
        # Pi keeps the presence check as part of the token-response contract;
        # the ID token is neither stored nor decoded.
        id_token = token.get("id_token")
        if not isinstance(id_token, str) or not id_token.strip():
            raise OAuthError("OpenAI OAuth token response did not contain an ID token")
        return self._credential_from_token(token, client_id)

    def refresh_token(self, credentials: Mapping[str, object]) -> dict[str, object]:
        """Pi ``refreshAccessToken``: the issued client ID is required."""

        client_id = credentials.get("clientId")
        if not isinstance(client_id, str) or not client_id.strip():
            raise OAuthError(
                "Stored OpenAI OAuth credential does not contain an issued client "
                "ID; reconnect ChatGPT"
            )
        token = self._request_token(
            {
                "grant_type": "refresh_token",
                "client_id": client_id,
                "refresh_token": str(credentials.get("refresh", "")),
                "resource": _CHATGPT_RESOURCE,
            }
        )
        return self._credential_from_token(token, client_id)

    def get_api_key(self, credentials: Mapping[str, object]) -> str:
        return str(credentials["access"])

    # -- login -----------------------------------------------------------------

    def authorization_url(
        self, host_id: str, state: str, challenge: str, nonce: str
    ) -> str:
        params = {
            "client_id": _CHATGPT_DYNAMIC_CLIENT_ID,
            "agent_name_hint": _CHATGPT_AGENT_NAME_HINT,
            "ext_agent_host_id": host_id,
            "response_type": "code",
            "redirect_uri": _CHATGPT_REDIRECT_URI,
            "resource": _CHATGPT_RESOURCE,
            "scope": _CHATGPT_SCOPE,
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "nonce": nonce,
        }
        return f"{self._authorize_url}?{urllib.parse.urlencode(params)}"

    def login(
        self,
        *,
        prompt: PromptCallback,
        notify: NotifyCallback,
        get_device_id: Callable[[], object] | None,
        env: Mapping[str, str] | None = None,
    ) -> dict[str, object]:
        """Pi ``loginOpenAIChatGPT`` over pipy's line-based callbacks.

        The manual prompt cannot race the callback in a line-based terminal:
        an empty answer waits for the browser callback, a pasted redirect URL
        is validated with Pi's rules. Ctrl-C cancels (``Login cancelled``).
        """

        host_id = chatgpt_agent_host_id(
            get_device_id() if get_device_id is not None else None
        )
        verifier, challenge = generate_pkce()
        state = _random_value()
        nonce = _random_value()
        environ = os.environ if env is None else env
        host = (
            self._callback_host or environ.get("PI_OAUTH_CALLBACK_HOST") or "127.0.0.1"
        )
        server: _ChatGPTCallbackServer | None = None
        try:
            server = _ChatGPTCallbackServer(host, self._callback_port, state)
        except OSError as exc:
            notify(
                {
                    "type": "info",
                    "message": (
                        f"Could not listen on {_CHATGPT_REDIRECT_URI}; paste the final "
                        f"redirect URL to continue. {exc}"
                    ),
                }
            )
        try:
            notify(
                {
                    "type": "auth_url",
                    "url": self.authorization_url(host_id, state, challenge, nonce),
                    "instructions": (
                        "Complete sign-in in your browser. If the callback does not "
                        "complete, paste the final redirect URL here."
                    ),
                }
            )
            answer = prompt(
                {
                    "type": "manual_code",
                    "message": (
                        "Complete login in your browser, or paste the final redirect "
                        "URL here:"
                    ),
                    "placeholder": _CHATGPT_REDIRECT_URI,
                }
            )
            if answer.strip() or server is None:
                code, client_id = _chatgpt_authorization_from_manual_input(
                    answer, state, _CHATGPT_REDIRECT_URI
                )
            else:
                code, client_id = server.wait()
            notify(
                {
                    "type": "progress",
                    "message": "Exchanging authorization code for tokens...",
                }
            )
            return self.exchange_code(code, verifier, client_id)
        except KeyboardInterrupt as exc:
            raise OAuthError("Login cancelled") from exc
        finally:
            if server is not None:
                server.close()


def _require_token_string(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise OAuthError(f"OpenAI OAuth token response has invalid {field_name}")
    return value


# --------------------------------------------------------------------------- #
# Registry
# --------------------------------------------------------------------------- #

_OAuthProviderFactory = Callable[[], OAuthProvider]


_BUILTIN_OAUTH_PROVIDERS: dict[str, _OAuthProviderFactory] = {
    "anthropic": AnthropicOAuthProvider,
    "github-copilot": GitHubCopilotOAuthProvider,
    "openai": OpenAIChatGPTOAuthProvider,
    "openai-codex": OpenAICodexOAuthProvider,
}


def get_oauth_provider_ids() -> list[str]:
    return list(_BUILTIN_OAUTH_PROVIDERS)


def get_oauth_provider(provider_id: str) -> OAuthProvider | None:
    cls = _BUILTIN_OAUTH_PROVIDERS.get(provider_id)
    return cls() if cls is not None else None


# Built-in OAuth providers whose request auth pipy resolves per request.
PER_REQUEST_OAUTH_PROVIDERS = frozenset({"github-copilot", "openai"})
# Of those, the providers whose refresh rotates the refresh token (Sign in with
# ChatGPT). Their refreshed credential is persisted to ``auth.json`` under the
# auth-file lock, as Pi does for every provider; Copilot's refresh material (the
# GitHub token) does not rotate, so its refresh stays in memory.
PERSISTED_OAUTH_REFRESH_PROVIDERS = frozenset({"openai"})
_MIN_VALIDITY_MS = _FIVE_MINUTES_MS


class OAuthCredentialCache:
    """Freshest refreshed OAuth credential per provider, shared across threads.

    Pi refreshes a stored OAuth credential that expires within five minutes
    (``auth/resolve.ts``) and persists it under a store lock. Pipy keeps the
    refreshed credential here: this object owns its own lock and holds no
    ``AuthStore`` reference, so a worker thread can refresh without touching
    the single-thread auth store. Callers pass a snapshot of the stored
    credential taken on the owner thread. For a provider in
    :data:`PERSISTED_OAUTH_REFRESH_PROVIDERS` (with ``auth_path`` set) the
    refresh runs Pi's ``resolveStoredOAuth`` shape through
    :func:`~pipy_harness.native.auth_store.modify_stored_credential`: re-read
    the stored entry under the auth-file lock, reuse it when another process
    already refreshed it, else refresh and persist the rotated credential.

    Two locks: ``_refresh_lock`` serializes refreshes (one network refresh per
    expiry, the re-check happens under it), and ``_lock`` guards only the
    dictionaries, so :meth:`current` never waits on a refresh's network call.
    """

    def __init__(
        self,
        *,
        providers: Callable[[str], OAuthProvider | None] | None = None,
        now_ms: Clock | None = None,
        auth_path: Path | None = None,
    ) -> None:
        self._providers = providers or get_oauth_provider
        self._now_ms = now_ms or _now_ms
        self._auth_path = auth_path
        self._lock = threading.Lock()
        self._refresh_lock = threading.Lock()
        self._fresh: dict[str, dict[str, object]] = {}
        # The snapshot refresh token a rotated credential replaced, so a stale
        # owner snapshot still maps to the newest credential.
        self._rotated_from: dict[str, object] = {}

    def _newest(
        self, provider_id: str, snapshot: Mapping[str, object]
    ) -> dict[str, object]:
        cached = self._fresh.get(provider_id)
        if cached is None:
            return dict(snapshot)
        snapshot_refresh = snapshot.get("refresh")
        if cached.get("refresh") == snapshot_refresh:
            if _expires(cached) >= _expires(snapshot):
                return dict(cached)
            return dict(snapshot)
        if self._rotated_from.get(provider_id) == snapshot_refresh:
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
            if (
                provider_id in PERSISTED_OAUTH_REFRESH_PROVIDERS
                and self._auth_path is not None
            ):
                refreshed = self._persisted_refresh(provider_id, provider)
            else:
                refreshed = _refresh_with(provider_id, provider, credential)
            with self._lock:
                self._fresh[provider_id] = refreshed
                if refreshed.get("refresh") != snapshot.get("refresh"):
                    self._rotated_from[provider_id] = snapshot.get("refresh")
            return dict(refreshed)

    def _persisted_refresh(
        self, provider_id: str, provider: OAuthProvider
    ) -> dict[str, object]:
        """Pi ``resolveStoredOAuth``: refresh once under the auth-file lock."""

        from pipy_harness.native.auth_store import modify_stored_credential

        assert self._auth_path is not None
        logged_out = False

        def modify(current: dict[str, object] | None) -> dict[str, object] | None:
            nonlocal logged_out
            if current is None or current.get("type") != "oauth":
                logged_out = True
                return None
            if self._now_ms() + _MIN_VALIDITY_MS < _expires(current):
                return None  # another process or request refreshed it
            return _refresh_with(provider_id, provider, current)

        try:
            stored = modify_stored_credential(self._auth_path, provider_id, modify)
        except OAuthError:
            raise
        except OSError as exc:
            raise OAuthError(
                f"Credential store modify failed for {provider_id}"
            ) from exc
        if logged_out or stored is None:
            raise OAuthError(f"No OAuth credential stored for {provider_id}")
        return stored


def _refresh_with(
    provider_id: str, provider: OAuthProvider, credential: Mapping[str, object]
) -> dict[str, object]:
    try:
        return dict(provider.refresh_token(credential))
    except OAuthError:
        raise
    except Exception as exc:  # noqa: BLE001 - transport/parse failures
        raise OAuthError(f"OAuth refresh failed for {provider_id}") from exc


def _expires(credentials: Mapping[str, object]) -> int:
    value = credentials.get("expires")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return int(value)
