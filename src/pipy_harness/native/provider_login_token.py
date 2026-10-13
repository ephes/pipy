"""The current token of a provider, for something other than a model request.

An MCP server whose entry has ``auth.provider`` is sent the token of that
provider (Pi ``ModelRegistry.getApiKeyForProvider``, which the MCP extension
passes as ``providerToken``). :func:`provider_login_token` answers with what
``/login`` or ``pipy auth`` stored for the provider in ``auth.json``, and
with the provider's API-key environment variable when nothing is stored.

It is called on the thread that sends the MCP request, so it does not use the
session's :class:`~pipy_harness.native.auth_store.AuthStore`, which belongs
to the session thread. It reads ``auth.json`` itself at every call, which is
also how a login or a logout made meanwhile applies to the next request. A
file that is missing stores nothing; one that cannot be read or is not a
JSON object is a failure, and a failure is no token: the environment is not
asked then, so a damaged file never sends another credential than the
stored one.

The answer is the key a model request to that provider would carry from
the same sources:

- A stored API key, resolved as usual: the name of a set environment
  variable is that variable's value and ``!command`` is the command's output.
- A stored OAuth credential's access token. For a provider ``refreshes``
  names, which are the ones pipy refreshes before every model request, it
  is refreshed first when it expires within five minutes, through the
  session's ``OAuthCredentialCache``, so model requests and MCP requests
  share one refresh. Other OAuth credentials are sent as stored.
- A stored entry owns the provider: the environment is asked only when
  nothing is stored (Pi ``resolveProviderAuth``).

Narrower than Pi, because pipy has no single place that resolves a
provider's auth off the session thread: a key configured in ``models.json``,
``--api-key``, the ``openai-codex`` login (kept in its own file) and ambient
cloud credentials are not answered; the server then gets no token.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from pathlib import Path

from pipy_harness.native.auth_store import find_env_keys, resolve_config_value
from pipy_harness.native.oauth_providers import OAuthCredentialCache


def provider_login_token(
    provider: str,
    *,
    auth_path: Path,
    env: Mapping[str, str],
    oauth_credentials: OAuthCredentialCache,
    refreshes: Callable[[str], bool],
) -> str | None:
    """The token ``provider`` has now, or ``None`` when it has none.

    ``refreshes`` says whether a stored OAuth login of a provider is
    refreshed through ``oauth_credentials`` when it is about to expire.

    Never raises: a credential that cannot be read, refreshed or resolved is
    no token (Pi's ``getApiKeyForProvider`` catches the same way).
    """

    try:
        stored = _stored_entry(auth_path, provider)
        if stored is None:
            names = find_env_keys(provider, env)
            return env.get(names[0]) if names else None
        return _stored_token(provider, stored, env, oauth_credentials, refreshes)
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
    oauth_credentials: OAuthCredentialCache,
    refreshes: Callable[[str], bool],
) -> str | None:
    kind = stored.get("type")
    if kind == "api_key":
        key = stored.get("key")
        return resolve_config_value(key, env=env) if isinstance(key, str) else None
    if kind != "oauth":
        return None
    if refreshes(provider):
        stored = oauth_credentials.fresh(provider, stored)
    access = stored.get("access")
    return access if isinstance(access, str) and access else None
