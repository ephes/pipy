"""The current token of a provider, for something other than a model request.

An MCP server whose entry has ``auth.provider`` is sent the token of that
provider (Pi ``ModelRegistry.getApiKeyForProvider``, which the MCP extension
passes as ``providerToken``). :func:`provider_login_token` answers with the
key a model request to that provider would carry, from the same sources in
the same order:

- For ``openai-codex``, the Codex login, which is kept in its own file and is
  what a model request to that provider's Codex models uses. It is refreshed
  first when it is about to expire, as before a model request, and the
  refreshed login is saved, under the lock model requests take too. Without
  a login the sources below are asked, as for a model ``models.json``
  defines for that provider with another ``api``; a login that cannot be
  read or refreshed is no token.
- The session's ``--api-key``, only for the provider it belongs to: the
  provider of the model the session started with (Pi sets it as the runtime
  key of that provider alone). No other provider is ever sent it.
- What ``/login`` or ``pipy auth`` stored for the provider in ``auth.json``:
  an API key, resolved as usual (the name of a set environment variable is
  that variable's value and ``!command`` is the command's output), or an
  OAuth login. A login is refreshed first when it expires within five
  minutes, as Pi refreshes every stored login before network access: one of
  ``PER_REQUEST_OAUTH_PROVIDERS`` through the session's
  ``OAuthCredentialCache``, so model requests and MCP requests share one
  refresh; a login of any other built-in OAuth provider under the auth-file
  lock, saving the refreshed login in ``auth.json``. The token is the
  login's access token when it is a non-empty string (Pi ``toAuth``). A login of a provider without an
  OAuth provider is no token (Pi: no ``oauth`` auth). A stored entry owns the
  provider: the sources below are asked only when nothing is stored (Pi
  ``resolveProviderAuth``).
- The provider's API-key environment variable.
- The ``apiKey`` that ``models.json`` configures for the provider, resolved
  like a stored key.

For a provider an extension registered, pipy's model requests leave the
credentials to the extension. Its token is what ``auth.json`` stores for it,
else its API-key environment variable; the Codex login, ``--api-key`` and
``models.json`` are not asked. A stored login is refreshed through the
extension's ``oauth.refresh_token`` when it expires within five minutes and
saved under the auth-file lock, and the token is the extension's
``oauth.get_api_key`` of it (Pi ``adaptOAuth``); the extension's callbacks
run on the thread that sends the MCP request then. The login of an
extension provider without ``oauth`` is no token.

The token belongs to the provider, not to one of its models: where a
``models.json`` model of ``openai-codex`` with another ``api`` uses a stored
key for its model requests, an MCP server is still sent the Codex login when
there is one.

It is called on the thread that sends the MCP request, so it does not use the
session's :class:`~pipy_harness.native.auth_store.AuthStore`, which belongs
to the session thread. It reads ``auth.json`` itself at every call, which is
also how a login or a logout made meanwhile applies to the next request. A
file that is missing stores nothing; one that cannot be read or is not a
JSON object is a failure, and a failure is no token: the sources after it
are not asked then, so a damaged file never sends another credential than
the stored one.

Narrower than Pi: ambient cloud credentials (an AWS profile, Google
application default credentials) are no token.
"""

from __future__ import annotations

import inspect
import json
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING

from pipy_harness.native.auth_store import find_env_keys, resolve_config_value
from pipy_harness.native.oauth_providers import (
    PER_REQUEST_OAUTH_PROVIDERS,
    OAuthCredentialCache,
    OAuthError,
    OAuthProvider,
)

if TYPE_CHECKING:
    from pipy_harness.native.extension_types import (
        ExtensionOAuthConfig,
        ExtensionProvider,
    )
    from pipy_harness.native.openai_codex_provider import OpenAICodexCredentials

OPENAI_CODEX = "openai-codex"


def provider_login_token(
    provider: str,
    *,
    auth_path: Path,
    env: Mapping[str, str],
    oauth_credentials: OAuthCredentialCache,
    extension_provider: Callable[[str], ExtensionProvider | None],
    runtime_api_key: Callable[[str], str | None],
    models_json_key: Callable[[str], str | None],
    codex_login: Callable[[], OpenAICodexCredentials | None],
) -> str | None:
    """The token ``provider`` has now, or ``None`` when it has none.

    ``extension_provider`` is the provider an extension registered under the
    name, or ``None``; ``runtime_api_key`` the session's ``--api-key`` when
    it belongs to the provider asked about, else ``None``;
    ``models_json_key`` the unresolved ``apiKey`` that ``models.json``
    configures for a provider, and ``codex_login`` the Codex login, refreshed
    and saved when it is about to expire, or ``None`` without one. Each is
    asked at the call, so a reload's changes apply.

    Never raises: a credential that cannot be read, refreshed or resolved is
    no token (Pi's ``getApiKeyForProvider`` catches the same way).
    """

    try:
        extension = extension_provider(provider)
        own = extension is None
        if own and provider == OPENAI_CODEX:
            login = codex_login()
            if login is not None:
                return login.access_token
        if own:
            runtime = runtime_api_key(provider)
            if runtime:
                return runtime
        stored = _stored_entry(auth_path, provider)
        if stored is not None:
            return _stored_token(
                provider, stored, env, extension, oauth_credentials, auth_path
            )
        names = find_env_keys(provider, env)
        if names:
            return env.get(names[0])
        key = models_json_key(provider) if own else None
        return resolve_config_value(key, env=env) if key else None
    except Exception:  # noqa: BLE001 - any failure means there is no token
        return None


def _stored_token(
    provider: str,
    stored: Mapping[str, object],
    env: Mapping[str, str],
    extension: ExtensionProvider | None,
    oauth_credentials: OAuthCredentialCache,
    auth_path: Path,
) -> str | None:
    """The token of what ``auth.json`` stores for ``provider``: an API key
    resolved as usual, or the token of an OAuth login, refreshed first when
    it expires soon. Raises when a login cannot be refreshed."""

    kind = stored.get("type")
    if kind == "api_key":
        key = stored.get("key")
        return resolve_config_value(key, env=env) if isinstance(key, str) else None
    if kind != "oauth":
        return None
    login_provider: OAuthProvider | None
    if extension is not None:
        oauth = extension.oauth
        login_provider = _ExtensionLogin(provider, oauth) if oauth is not None else None
    else:
        login_provider = oauth_credentials.oauth_provider(provider)
    if login_provider is None:
        return None  # Pi: a provider without `oauth` auth
    fresh: Mapping[str, object] | None
    if extension is None and provider in PER_REQUEST_OAUTH_PROVIDERS:
        fresh = oauth_credentials.fresh(provider, stored)
    else:
        fresh = oauth_credentials.fresh_stored(
            auth_path, provider, stored, login_provider
        )
    if fresh is None:
        return None
    # Pi `toAuth`: the access token of a built-in login, the extension's
    # `getApiKey` of an extension's.
    token = (
        login_provider.get_api_key(fresh)
        if extension is not None
        else fresh.get("access")
    )
    return token if isinstance(token, str) and token else None


class _ExtensionLogin:
    """An extension's ``oauth`` as an :class:`OAuthProvider` (Pi
    ``adaptOAuth``): its ``refresh_token`` result is the stored login, typed
    ``oauth``, and its ``get_api_key`` the token."""

    def __init__(self, provider: str, oauth: ExtensionOAuthConfig) -> None:
        self.id = provider
        self._oauth = oauth

    def refresh_token(self, credentials: Mapping[str, object]) -> dict[str, object]:
        refreshed = _synchronous(self._oauth.refresh_token(dict(credentials)))
        if not isinstance(refreshed, Mapping):
            raise OAuthError(f"OAuth refresh failed for {self.id}")
        return {**refreshed, "type": "oauth"}

    def get_api_key(self, credentials: Mapping[str, object]) -> str:
        token = _synchronous(self._oauth.get_api_key(dict(credentials)))
        if not isinstance(token, str):
            raise OAuthError(f"OAuth auth derivation failed for {self.id}")
        return token


def _synchronous(value: object) -> object:
    """``value``, which an extension callback returned; an awaitable is
    closed and refused, as for an extension's ``login``."""

    if inspect.isawaitable(value):
        close = getattr(value, "close", None)
        if callable(close):
            close()
        raise OAuthError("extension OAuth callback returned an awaitable")
    return value


def _stored_entry(auth_path: Path, provider: str) -> Mapping[str, object] | None:
    """What ``auth.json`` stores for ``provider``. Raises when the file
    exists and cannot be read as a JSON object."""

    try:
        # Pi ``stripBom``: a leading byte order mark is not part of the JSON.
        body = json.loads(auth_path.read_text(encoding="utf-8").removeprefix("\ufeff"))
    except FileNotFoundError:
        return None
    if not isinstance(body, dict):
        raise ValueError("auth.json is not a JSON object")
    entry = body.get(provider)
    return entry if isinstance(entry, dict) else None
