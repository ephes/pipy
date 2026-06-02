"""Stdlib OAuth subscription provider registry (M7).

Pipy analogue of Pi's built-in OAuth registry (packages/ai/src/utils/oauth/):
Anthropic (Claude Pro/Max, PKCE + callback server), GitHub Copilot (device-code
+ per-model policy enable + ``proxy-ep`` base-URL rewrite), and OpenAI Codex
(ChatGPT, PKCE — the existing pipy provider).

Each provider implements ``login``/``refresh_token``/``get_api_key`` and,
optionally, ``modify_models``. HTTP goes through an injectable transport so the
flows are testable against fakes; the default transport is stdlib ``urllib``.

Credential dicts hold only token material (``access``/``refresh``/``expires``).
Secrets, refresh tokens, PKCE verifiers, authorization URLs, and ``Authorization``
headers are never archived.
"""

from __future__ import annotations

import base64
import json
import re
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import replace
from typing import Protocol

from pipy_harness.native.catalog import NativeModelSpec


# (method, url, headers, data) -> (status_code, body_text)
Transport = Callable[..., tuple[int, str]]
Clock = Callable[[], int]


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


class OAuthProvider(Protocol):
    id: str

    def refresh_token(self, credentials: Mapping[str, object]) -> dict: ...
    def get_api_key(self, credentials: Mapping[str, object]) -> str: ...


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

    def _token_request(self, payload: dict) -> dict:
        status, body = self._transport(
            "POST",
            self.token_url,
            headers={"Content-Type": "application/json"},
            data=json.dumps(payload),
        )
        if status != 200:
            raise OAuthError(f"anthropic token request failed ({status})")
        data = json.loads(body)
        return {
            "type": "oauth",
            "access": data["access_token"],
            "refresh": data["refresh_token"],
            # 5-minute safety margin (anthropic.ts).
            "expires": self._now_ms() + int(data["expires_in"]) * 1000 - _FIVE_MINUTES_MS,
        }

    def exchange_code(self, code: str, verifier: str, redirect_uri: str) -> dict:
        return self._token_request(
            {
                "grant_type": "authorization_code",
                "client_id": self.client_id,
                "code": code,
                "code_verifier": verifier,
                "redirect_uri": redirect_uri,
            }
        )

    def refresh_token(self, credentials: Mapping[str, object]) -> dict:
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
# Copilot editor headers required by the Copilot token + policy endpoints.
_COPILOT_HEADERS = {
    "User-Agent": "GitHubCopilotChat/0.35.0",
    "Editor-Version": "vscode/1.107.0",
    "Editor-Plugin-Version": "copilot-chat/0.35.0",
    "Copilot-Integration-Id": "vscode-chat",
}


def copilot_base_url_from_token(token: str) -> str | None:
    """Extract the API base URL from a Copilot token's ``proxy-ep`` claim.

    Pi converts the ``proxy.`` host prefix to ``api.`` (github-copilot.ts).
    """

    match = _PROXY_EP.search(token)
    if not match:
        return None
    api_host = re.sub(r"^proxy\.", "api.", match.group(1))
    return f"https://{api_host}"


class GitHubCopilotOAuthProvider:
    id = "github-copilot"
    client_id = _COPILOT_CLIENT_ID

    def __init__(
        self,
        *,
        transport: Transport | None = None,
        now_ms: Clock | None = None,
        domain: str = "github.com",
    ) -> None:
        self._transport = transport or _default_transport
        self._now_ms = now_ms or _now_ms
        self.domain = domain

    def _urls(self) -> dict[str, str]:
        return {
            "device": f"https://{self.domain}/login/device/code",
            "access": f"https://{self.domain}/login/oauth/access_token",
            "copilot": f"https://api.{self.domain}/copilot_internal/v2/token",
        }

    def get_api_key(self, credentials: Mapping[str, object]) -> str:
        return str(credentials["access"])

    def refresh_token(self, credentials: Mapping[str, object]) -> dict:
        # Copilot exchanges the stored GitHub token for a short-lived Copilot
        # token. The GitHub token is the durable refresh material. Pi sends
        # ``Authorization: Bearer <github token>`` + Copilot headers and reads
        # ``expires_at`` (seconds), storing it with a 5-minute margin.
        github_token = str(credentials.get("refresh") or credentials.get("access"))
        status, body = self._transport(
            "GET",
            self._urls()["copilot"],
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {github_token}",
                **_COPILOT_HEADERS,
            },
        )
        if status != 200:
            raise OAuthError(f"copilot token request failed ({status})")
        data = json.loads(body)
        return {
            "type": "oauth",
            "access": data["token"],
            "refresh": github_token,
            "expires": int(data["expires_at"]) * 1000 - _FIVE_MINUTES_MS,
        }

    def enable_model(self, copilot_token: str, model_id: str) -> bool:
        base_url = copilot_base_url_from_token(copilot_token)
        if base_url is None:
            return False
        status, _ = self._transport(
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
        )
        return 200 <= status < 300

    def modify_models(
        self, rows: list[NativeModelSpec], credentials: Mapping[str, object]
    ) -> list[NativeModelSpec]:
        base_url = copilot_base_url_from_token(str(credentials.get("access", "")))
        if base_url is None:
            return rows
        return [
            replace(row, base_url=base_url) if row.provider_name == "github-copilot" else row
            for row in rows
        ]


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

    def refresh_token(self, credentials: Mapping[str, object]) -> dict:
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
        data = json.loads(body)
        return {
            "type": "oauth",
            "access": data["access_token"],
            "refresh": data["refresh_token"],
            # No safety margin for Codex (openai-codex.ts).
            "expires": self._now_ms() + int(data["expires_in"]) * 1000,
        }

    def get_api_key(self, credentials: Mapping[str, object]) -> str:
        return str(credentials["access"])


class OAuthError(Exception):
    pass


# --------------------------------------------------------------------------- #
# Registry
# --------------------------------------------------------------------------- #

_BUILTIN_OAUTH_PROVIDERS: dict[str, type] = {
    "anthropic": AnthropicOAuthProvider,
    "github-copilot": GitHubCopilotOAuthProvider,
    "openai-codex": OpenAICodexOAuthProvider,
}


def get_oauth_provider_ids() -> list[str]:
    return list(_BUILTIN_OAUTH_PROVIDERS)


def get_oauth_provider(provider_id: str) -> OAuthProvider | None:
    cls = _BUILTIN_OAUTH_PROVIDERS.get(provider_id)
    return cls() if cls is not None else None
