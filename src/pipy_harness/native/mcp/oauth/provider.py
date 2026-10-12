"""The provider that keeps one MCP server's OAuth state in a store.

``McpOAuthProvider`` is the ``OAuthClientProvider`` the flow is run with. It
holds nothing itself: the client registration, the tokens, the PKCE verifier,
the ``state`` parameter and the discovery result are one ``McpOAuthState``
document in a ``McpOAuthStateStore``, which the application supplies to keep
them (``MemoryOAuthStateStore`` forgets them with the process).

A state document belongs to one MCP server URL. A provider that finds the
document of another URL in its store treats the store as empty, and its first
write replaces that document, so credentials never reach another server.

Every method reads the store, and every change is one read and one write of
the whole document under the provider's lock, so the threads using one
provider do not lose each other's changes. Two providers on one store, or two
processes, are not kept apart here; whoever shares a store has to lock it.

Where this differs from Pi:

- The server URL is compared in the form ``config.parse_http_url`` gives it:
  without its fragment, where Pi keeps the fragment. A URL that parser does
  not accept, or one with user info, is refused with ``ValueError``.
- ``state`` creates and stores a new value in one step under the lock; in Pi
  two concurrent first calls can return different values.
- Pi's plain ``Error`` for a missing code verifier is ``OAuthFlowError``.
"""

from __future__ import annotations

import copy
import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Final, Protocol

from pipy_harness.native.mcp.config import parse_http_url
from pipy_harness.native.mcp.oauth.errors import OAuthFlowError
from pipy_harness.native.mcp.oauth.flow import (
    InvalidationKind,
    OAuthClientMetadataDocument,
    OAuthDiscoveryState,
)
from pipy_harness.native.mcp.oauth.types import (
    AuthorizationServerMetadata,
    OAuthClientInformation,
    OAuthClientMetadata,
    OAuthTokens,
)
from pipy_harness.native.mcp.protocol import is_object

McpOAuthState = dict[str, Any]
"""The stored OAuth state of one MCP server.

The keys are Pi's: ``serverUrl``, and when there is a value
``clientInformation``, ``tokens``, ``tokensExpireAt`` (when the access token
expires, in milliseconds since the epoch, from ``expires_in`` at the time the
tokens were saved), ``codeVerifier``, ``oauthState`` and ``discovery``.
"""

ClientMetadataDocumentChooser = Callable[
    [AuthorizationServerMetadata | None], OAuthClientMetadataDocument | None
]

# What `invalidate_credentials` removes from the state, by kind.
_INVALIDATED_KEYS: Final[dict[str, tuple[str, ...]]] = {
    "client": ("clientInformation",),
    "tokens": ("tokens", "tokensExpireAt"),
    "verifier": ("codeVerifier",),
    "discovery": ("discovery",),
    "all": (
        "clientInformation",
        "tokens",
        "tokensExpireAt",
        "codeVerifier",
        "discovery",
        "oauthState",
    ),
}


class McpOAuthStateStore(Protocol):
    """Keeps one state document. ``load`` returns ``None`` when it has none."""

    def load(self) -> McpOAuthState | None: ...

    def save(self, state: McpOAuthState) -> None: ...


class MemoryOAuthStateStore:
    """A store that lives as long as the object."""

    def __init__(self) -> None:
        self._value: McpOAuthState | None = None

    def load(self) -> McpOAuthState | None:
        return copy.deepcopy(self._value)

    def save(self, state: McpOAuthState) -> None:
        self._value = copy.deepcopy(state)


@dataclass(frozen=True, slots=True)
class McpOAuthProviderOptions:
    """What a provider is made of.

    ``client_metadata`` is what a dynamic registration sends. Its
    ``redirect_uris``, ``grant_types``, ``response_types`` and
    ``token_endpoint_auth_method`` have defaults: the redirect URL, the
    authorization code flow with refresh tokens, and ``client_secret_post``
    with a ``client_secret``, else ``none``.

    ``client_id`` names a client registered beforehand, with ``client_secret``
    when it has one. Such a provider never registers and never stores a
    client.

    ``client_metadata_document`` chooses a Client ID Metadata Document; see
    the flow.

    ``on_redirect`` is given the authorization URL the user has to open.
    """

    server_url: str
    redirect_url: str
    client_metadata: OAuthClientMetadata
    on_redirect: Callable[[str], None]
    client_metadata_document: ClientMetadataDocumentChooser | None = None
    client_id: str | None = None
    client_secret: str | None = None
    store: McpOAuthStateStore | None = None


def _server_url(value: str) -> str:
    url = parse_http_url(value)
    if url is None or url.has_user_info:
        raise ValueError(f"Invalid MCP server URL: {value}")
    return url.href


class McpOAuthProvider:
    """The stateful provider for one exact MCP server URL."""

    def __init__(self, options: McpOAuthProviderOptions) -> None:
        self._server_url = _server_url(options.server_url)
        self.redirect_url = options.redirect_url
        metadata = dict(options.client_metadata)
        defaults = {
            "redirect_uris": [self.redirect_url],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "client_secret_post"
            if options.client_secret
            else "none",
        }
        for key, value in defaults.items():
            if metadata.get(key) is None:
                metadata[key] = value
        self.client_metadata: OAuthClientMetadata = metadata
        self._choose_document = options.client_metadata_document
        self._configured_client: OAuthClientInformation | None = None
        if options.client_id:
            self._configured_client = {"client_id": options.client_id}
            if options.client_secret:
                self._configured_client["client_secret"] = options.client_secret
        self._store: McpOAuthStateStore = (
            MemoryOAuthStateStore() if options.store is None else options.store
        )
        self._on_redirect = options.on_redirect
        # Held for every read, and for the read and the write of a change.
        self._lock = threading.RLock()

    def client_metadata_document(
        self, metadata: AuthorizationServerMetadata | None
    ) -> OAuthClientMetadataDocument | None:
        return (
            None if self._choose_document is None else self._choose_document(metadata)
        )

    def state(self) -> str:
        """The ``state`` parameter: 32 random bytes in hex.

        It stays the same until the state is forgotten, so the authorization
        response can be matched after the flow returned.
        """

        with self._lock:
            existing = self._load().get("oauthState")
            if isinstance(existing, str) and existing:
                return existing
            state = secrets.token_hex(32)
            self._set("oauthState", state)
            return state

    def client_information(self) -> OAuthClientInformation | None:
        if self._configured_client is not None:
            return dict(self._configured_client)
        information: OAuthClientInformation | None = self._load().get(
            "clientInformation"
        )
        return information

    def save_client_information(self, information: OAuthClientInformation) -> None:
        if self._configured_client is None:
            self._set("clientInformation", information)

    def tokens(self) -> OAuthTokens | None:
        tokens: OAuthTokens | None = self._load().get("tokens")
        return tokens

    def save_tokens(self, tokens: OAuthTokens) -> None:
        expires_in = tokens.get("expires_in")
        expires_at = (
            None if expires_in is None else int((time.time() + expires_in) * 1000)
        )

        def change(state: McpOAuthState) -> None:
            state["tokens"] = tokens
            if expires_at is None:
                state.pop("tokensExpireAt", None)
            else:
                state["tokensExpireAt"] = expires_at

        self._update(change)

    def redirect_to_authorization(self, url: str) -> None:
        self._on_redirect(url)

    def save_code_verifier(self, verifier: str) -> None:
        self._set("codeVerifier", verifier)

    def code_verifier(self) -> str:
        verifier = self._load().get("codeVerifier")
        if not isinstance(verifier, str) or not verifier:
            raise OAuthFlowError("No OAuth PKCE code verifier is stored")
        return verifier

    def invalidate_credentials(self, kind: InvalidationKind) -> None:
        def change(state: McpOAuthState) -> None:
            for key in _INVALIDATED_KEYS[kind]:
                state.pop(key, None)

        self._update(change)

    def save_discovery_state(self, discovery: OAuthDiscoveryState) -> None:
        self._set("discovery", discovery)

    def discovery_state(self) -> OAuthDiscoveryState | None:
        discovery: OAuthDiscoveryState | None = self._load().get("discovery")
        return discovery

    def _load(self) -> McpOAuthState:
        with self._lock:
            return self._own(self._store.load())

    def _update(self, change: Callable[[McpOAuthState], None]) -> None:
        with self._lock:
            state = self._own(self._store.load())
            change(state)
            self._store.save(state)

    def _set(self, key: str, value: object) -> None:
        def change(state: McpOAuthState) -> None:
            state[key] = value

        self._update(change)

    def _own(self, state: McpOAuthState | None) -> McpOAuthState:
        """``state`` if it is this server's, else a new one.

        Stored state for another server URL is ignored, so credentials never
        leak across servers.
        """

        if is_object(state) and state.get("serverUrl") == self._server_url:
            return dict(state)
        return {"serverUrl": self._server_url}
