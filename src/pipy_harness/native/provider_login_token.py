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
- The session's ``--api-key``, which a model request uses for any provider.
- What ``/login`` or ``pipy auth`` stored for the provider in ``auth.json``:
  an API key, resolved as usual (the name of a set environment variable is
  that variable's value and ``!command`` is the command's output), or an
  OAuth login's access token. A login of a provider in
  ``PER_REQUEST_OAUTH_PROVIDERS`` is refreshed first when it expires within
  five minutes, through the session's ``OAuthCredentialCache``, so model
  requests and MCP requests share one refresh. Other OAuth logins are sent
  as stored. A stored entry owns the provider: the sources below are asked
  only when nothing is stored (Pi ``resolveProviderAuth``).
- The provider's API-key environment variable.
- The ``apiKey`` that ``models.json`` configures for the provider, resolved
  like a stored key.

For a provider an extension registered, pipy's model requests leave the
credentials to the extension. Its token is what ``auth.json`` stores for it
(a login sent as stored), else its API-key environment variable; the Codex
login, ``--api-key`` and ``models.json`` are not asked.

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

import json
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING

from pipy_harness.native.auth_store import find_env_keys, resolve_config_value
from pipy_harness.native.oauth_providers import (
    PER_REQUEST_OAUTH_PROVIDERS,
    OAuthCredentialCache,
)

if TYPE_CHECKING:
    from pipy_harness.native.openai_codex_provider import OpenAICodexCredentials

OPENAI_CODEX = "openai-codex"


def provider_login_token(
    provider: str,
    *,
    auth_path: Path,
    env: Mapping[str, str],
    oauth_credentials: OAuthCredentialCache,
    replaced: Callable[[str], bool],
    runtime_api_key: Callable[[], str | None],
    models_json_key: Callable[[str], str | None],
    codex_login: Callable[[], OpenAICodexCredentials | None],
) -> str | None:
    """The token ``provider`` has now, or ``None`` when it has none.

    ``replaced`` says whether an extension registered the provider,
    ``runtime_api_key`` is the session's ``--api-key``, ``models_json_key``
    the unresolved ``apiKey`` that ``models.json`` configures for a
    provider, and ``codex_login`` the Codex login, refreshed and saved when it
    is about to expire, or ``None`` without one. Each is asked at the call, so a reload's changes
    apply.

    Never raises: a credential that cannot be read, refreshed or resolved is
    no token (Pi's ``getApiKeyForProvider`` catches the same way).
    """

    try:
        own = not replaced(provider)
        if own and provider == OPENAI_CODEX:
            login = codex_login()
            if login is not None:
                return login.access_token
        if own:
            runtime = runtime_api_key()
            if runtime:
                return runtime
        stored = _stored_entry(auth_path, provider)
        if stored is not None:
            refreshing = own and provider in PER_REQUEST_OAUTH_PROVIDERS
            return _stored_token(
                provider, stored, env, oauth_credentials if refreshing else None
            )
        names = find_env_keys(provider, env)
        if names:
            return env.get(names[0])
        key = models_json_key(provider) if own else None
        return resolve_config_value(key, env=env) if key else None
    except Exception:  # noqa: BLE001 - any failure means there is no token
        return None


def _stored_entry(auth_path: Path, provider: str) -> Mapping[str, object] | None:
    """What ``auth.json`` stores for ``provider``. Raises when the file
    exists and cannot be read as a JSON object."""

    try:
        body = json.loads(auth_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    if not isinstance(body, dict):
        raise ValueError("auth.json is not a JSON object")
    entry = body.get(provider)
    return entry if isinstance(entry, dict) else None


def _stored_token(
    provider: str,
    stored: Mapping[str, object],
    env: Mapping[str, str],
    refreshing: OAuthCredentialCache | None,
) -> str | None:
    """The token of a stored entry. An OAuth login is refreshed through
    ``refreshing`` first when one is given, and sent as stored otherwise."""

    kind = stored.get("type")
    if kind == "api_key":
        key = stored.get("key")
        return resolve_config_value(key, env=env) if isinstance(key, str) else None
    if kind != "oauth":
        return None
    if refreshing is not None:
        stored = refreshing.fresh(provider, stored)
    access = stored.get("access")
    return access if isinstance(access, str) and access else None
