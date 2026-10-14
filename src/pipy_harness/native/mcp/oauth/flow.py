"""The OAuth authorization flow for an MCP server.

``authorize_mcp`` runs one pass of the flow for a provider, the object that
keeps the client registration, the tokens and the PKCE verifier:

1. discovery, or the provider's stored discovery state;
2. the client: the stored one, a Client ID Metadata Document, or a dynamic
   registration;
3. with an authorization code: the code is exchanged for tokens;
4. else, with a stored refresh token: the tokens are refreshed;
5. else the provider is handed the authorization URL to send the user to.

It returns ``"AUTHORIZED"`` when the provider now holds usable tokens and
``"REDIRECT"`` when the user has to authorize. The caller then waits for the
authorization response and calls ``authorize_mcp`` again with its code, and
with the same scope and URLs as before.

``adapt_oauth_provider`` makes the auth provider of an HTTP transport from
any provider (Pi ``adaptOAuthProvider``): with a ``McpOAuthProvider`` and a
``MemoryOAuthStateStore`` or a store of the application's own, a program
connects to a server that wants OAuth (see ``OAuthTransportAuth``).

A provider is any object with the members of ``OAuthClientProvider``. Further
methods are used when the provider has them:

- ``state() -> str | None``: the ``state`` parameter of the authorization
  request.
- ``save_client_information(information)``: stores a registered client. A
  provider without it cannot register one.
- ``client_metadata_document(metadata) -> OAuthClientMetadataDocument | None``:
  a Client ID Metadata Document to identify with instead of registering. It
  is asked when no client is stored, and is given the authorization server's
  metadata, ``None`` when the server has none.
- ``add_client_authentication(headers, params, url, metadata)``: adds the
  client's credentials to a token request itself, by changing ``headers`` and
  ``params``.
- ``invalidate_credentials(kind)``: forgets ``"all"``, ``"client"``,
  ``"tokens"``, ``"verifier"`` or ``"discovery"``.
- ``discovery_state() -> OAuthDiscoveryState | None`` and
  ``save_discovery_state(state)``: keep what discovery found.

Where this differs from Pi:

- URLs are read with the config parser (``config.parse_http_url``) and not
  with a WHATWG URL parser, and they are strings. An endpoint is requested as
  the server wrote it. An endpoint that parser does not accept is refused as
  insecure where credentials would be sent to it.
- The authorization URL is the authorization endpoint with its query
  rewritten by ``urllib.parse.urlencode``, which leaves ``~`` as it is and
  writes ``*`` as ``%2A``; the rest of the endpoint is kept as written.
- Whether the scope includes ``offline_access`` is decided with
  ``str.split``, whose white space is not exactly JavaScript's ``\\s``.
- A request that was cancelled always ends the flow, whether or not the
  cancel event is still set when its ``McpAbortError`` is seen.
- A registration answer that is not JSON is an ``OAuthFlowError``; client
  information without a usable ``client_id`` is one too.
- Pi's plain ``Error`` is ``OAuthFlowError``.
- The auth provider ``adapt_oauth_provider`` makes sends the requests of the
  flow with its own fetch, the library's default unless it is given one; Pi
  uses the fetch of the transport whose request was rejected, which the
  transport does not hand an auth provider here.
"""

from __future__ import annotations

import base64
import hashlib
import http.client
import json
import secrets
import threading
import urllib.parse
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final, Literal, Protocol

from pipy_harness.native.mcp.config import LOOPBACK_HOSTS, parse_http_url
from pipy_harness.native.mcp.http import UnauthorizedContext
from pipy_harness.native.mcp.oauth.discovery import (
    discover_authorization_server_metadata,
    discover_oauth_server_info,
    parse_www_authenticate,
    select_resource,
)
from pipy_harness.native.mcp.oauth.errors import (
    McpOAuthAuthorizationRequiredError,
    OAuthError,
    OAuthFlowError,
    OAuthInsecureEndpointError,
    OAuthIssuerMismatchError,
    OAuthRegistrationError,
)
from pipy_harness.native.mcp.oauth.fetch import OAuthFetch, oauth_fetch
from pipy_harness.native.mcp.oauth.types import (
    AuthorizationServerMetadata,
    OAuthChallenge,
    OAuthClientInformation,
    OAuthClientMetadata,
    OAuthServerInfo,
    OAuthTokens,
    can_parse_url,
    parse_client_information,
    parse_oauth_tokens,
)
from pipy_harness.native.mcp.protocol import McpAbortError, is_object

OAuthFlowResult = Literal["AUTHORIZED", "REDIRECT"]
InvalidationKind = Literal["all", "client", "tokens", "verifier", "discovery"]
ClientAuthMethod = Literal["client_secret_basic", "client_secret_post", "none"]

OAuthDiscoveryState = dict[str, Any]
"""What discovery found, in the form a provider stores.

The keys are Pi's: ``authorizationServerUrl``, and when there is a value
``authorizationServerMetadata``, ``resourceMetadata`` and
``resourceMetadataUrl``.
"""

AddClientAuthentication = Callable[
    [dict[str, str], dict[str, str], str, AuthorizationServerMetadata | None], None
]
"""Adds client credentials to a token request's headers and form fields.

It is given the token endpoint's URL and the authorization server's metadata.
"""

_CLIENT_AUTH_METHODS: Final[tuple[ClientAuthMethod, ...]] = (
    "client_secret_basic",
    "client_secret_post",
    "none",
)
# Errors of a token request after which the flow starts over without the
# credentials the server refused.
_STALE_CLIENT_ERRORS: Final = ("invalid_client", "unauthorized_client")
_STALE_GRANT_ERROR: Final = "invalid_grant"
_JSON_HEADERS: Final[Mapping[str, str]] = {
    "Accept": "application/json",
    "Content-Type": "application/json",
}
_FORM_HEADERS: Final[Mapping[str, str]] = {
    "Accept": "application/json",
    "Content-Type": "application/x-www-form-urlencoded",
}


@dataclass(frozen=True, slots=True)
class OAuthClientMetadataDocument:
    """A Client ID Metadata Document: an https URL used as ``client_id``.

    ``redirect_url`` is a redirect URI the document lists.
    """

    url: str
    redirect_url: str


class OAuthClientProvider(Protocol):
    """Keeps the state of one MCP server's authorization; see the module text."""

    @property
    def redirect_url(self) -> str: ...

    @property
    def client_metadata(self) -> OAuthClientMetadata: ...

    def client_information(self) -> OAuthClientInformation | None: ...

    def tokens(self) -> OAuthTokens | None: ...

    def save_tokens(self, tokens: OAuthTokens) -> None: ...

    def redirect_to_authorization(self, url: str) -> None: ...

    def save_code_verifier(self, verifier: str) -> None: ...

    def code_verifier(self) -> str: ...


@dataclass(frozen=True, slots=True)
class OAuthFlowOptions:
    """What one pass of the flow is run with.

    ``iss`` is the ``iss`` parameter of the authorization response that
    delivered ``authorization_code`` (RFC 9207).

    ``authorization_server_metadata_url`` names a metadata document to use
    instead of discovery, for servers that advertise a wrong authorization
    server or none. It is trusted as configured, and must use https, except on
    loopback.

    ``cancel_event`` ends every request of the flow. Requests have no time
    limit unless ``fetch`` sets one.

    ``skip_refresh`` goes straight to the authorization redirect instead of
    refreshing stored tokens, for example when the server asks for scopes the
    current grant lacks (a refresh keeps the old scope).
    """

    server_url: str
    authorization_code: str | None = None
    iss: str | None = None
    scope: str | None = None
    resource_metadata_url: str | None = None
    authorization_server_metadata_url: str | None = None
    fetch: OAuthFetch | None = None
    cancel_event: threading.Event | None = None
    skip_issuer_validation: bool = False
    skip_refresh: bool = False


@dataclass(frozen=True, slots=True)
class TokenRequestOptions:
    """What a request to the token endpoint needs besides its grant."""

    client_information: OAuthClientInformation
    metadata: AuthorizationServerMetadata | None = None
    resource: str | None = None
    add_client_authentication: AddClientAuthentication | None = None
    fetch: OAuthFetch | None = None
    cancel_event: threading.Event | None = None


@dataclass(frozen=True, slots=True)
class AuthorizationRequest:
    """The URL to send the user to, and the PKCE verifier that belongs to it."""

    authorization_url: str
    code_verifier: str


def _is_loopback(hostname: str) -> bool:
    return hostname in LOOPBACK_HOSTS


def _application_type(redirect_uris: Sequence[str]) -> Literal["native", "web"]:
    """The OpenID Connect ``application_type`` for ``redirect_uris`` (MCP SEP-837).

    Without one, OpenID Connect servers assume ``web``, which rejects http
    loopback redirect URIs. Loopback hosts and custom schemes are native
    (RFC 8252).
    """

    for uri in redirect_uris:
        if not isinstance(uri, str) or not can_parse_url(uri):
            continue
        url = parse_http_url(uri)
        if url is None or _is_loopback(url.hostname):
            return "native"
    return "web"


def _secure_endpoint(value: str) -> str:
    """``value``, if credentials may be sent there: https, or a loopback host."""

    url = parse_http_url(value)
    if url is None:
        raise OAuthInsecureEndpointError(value)
    if url.scheme != "https" and not _is_loopback(url.hostname):
        raise OAuthInsecureEndpointError(url.href)
    return value


def _default_endpoint(authorization_server_url: str, path: str) -> str:
    """Where a server without metadata has the endpoint ``path``."""

    url = parse_http_url(authorization_server_url)
    if url is None:
        raise http.client.InvalidURL(f"Cannot request URL {authorization_server_url}")
    return f"{url.origin}{path}"


def _client_id(information: OAuthClientInformation) -> str:
    client_id = information.get("client_id")
    if not isinstance(client_id, str) or not client_id:
        raise OAuthFlowError("Invalid client_id")
    return client_id


def _select_client_auth_method(
    information: OAuthClientInformation, supported: Sequence[str]
) -> ClientAuthMethod:
    """How the client proves itself to the token endpoint.

    The method the client was registered with, when the server supports it;
    else the first of basic, post and none that fits client and server.
    """

    hinted = information.get("token_endpoint_auth_method")
    secret = bool(information.get("client_secret"))
    for method in _CLIENT_AUTH_METHODS:
        if method == hinted and (not supported or method in supported):
            return method
    if not supported:
        return "client_secret_basic" if secret else "none"
    if secret and "client_secret_basic" in supported:
        return "client_secret_basic"
    if secret and "client_secret_post" in supported:
        return "client_secret_post"
    if "none" in supported:
        return "none"
    return "client_secret_post" if secret else "none"


def _apply_client_authentication(
    method: ClientAuthMethod,
    information: OAuthClientInformation,
    headers: dict[str, str],
    params: dict[str, str],
) -> None:
    client_id = _client_id(information)
    secret = information.get("client_secret")
    if method == "client_secret_basic":
        if not secret:
            raise OAuthFlowError("client_secret_basic requires a client secret")
        credentials = base64.b64encode(f"{client_id}:{secret}".encode()).decode("ascii")
        headers["Authorization"] = f"Basic {credentials}"
        return
    params["client_id"] = client_id
    if method == "client_secret_post" and secret:
        params["client_secret"] = secret


def _base64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _pkce() -> tuple[str, str]:
    """A new PKCE code verifier and its S256 challenge (RFC 7636)."""

    verifier = _base64url(secrets.token_bytes(32))
    return verifier, _base64url(hashlib.sha256(verifier.encode("ascii")).digest())


def _with_query_parameters(url: str, parameters: Mapping[str, str]) -> str:
    """``url`` with each of ``parameters`` set in its query.

    A parameter the query already has is replaced where it first stands.
    """

    base, hash_mark, fragment = url.partition("#")
    address, _, query = base.partition("?")
    pairs = urllib.parse.parse_qsl(query, keep_blank_values=True)
    for name, value in parameters.items():
        others = [pair for pair in pairs if pair[0] != name]
        position = next(
            (index for index, pair in enumerate(pairs) if pair[0] == name), len(others)
        )
        pairs = [*others[:position], (name, value), *others[position:]]
    return f"{address}?{urllib.parse.urlencode(pairs)}{hash_mark}{fragment}"


def start_authorization(
    authorization_server_url: str,
    *,
    client_information: OAuthClientInformation,
    redirect_url: str,
    metadata: AuthorizationServerMetadata | None = None,
    scope: str | None = None,
    state: str | None = None,
    resource: str | None = None,
) -> AuthorizationRequest:
    """Build the authorization request of the code flow with PKCE.

    Raises ``OAuthFlowError`` when the server's metadata says it supports
    neither authorization codes nor the S256 challenge method.
    """

    if metadata is not None and "code" not in (
        metadata.get("response_types_supported") or []
    ):
        raise OAuthFlowError(
            "Authorization server does not support authorization codes"
        )
    challenge_methods = (metadata or {}).get("code_challenge_methods_supported")
    if challenge_methods is not None and "S256" not in challenge_methods:
        raise OAuthFlowError("Authorization server does not support PKCE S256")
    endpoint: str = (
        metadata["authorization_endpoint"]
        if metadata is not None
        else _default_endpoint(authorization_server_url, "/authorize")
    )
    verifier, challenge = _pkce()
    parameters = {
        "response_type": "code",
        "client_id": _client_id(client_information),
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "redirect_uri": redirect_url,
    }
    if state:
        parameters["state"] = state
    if scope:
        parameters["scope"] = scope
        if "offline_access" in scope.split():
            parameters["prompt"] = "consent"
    if resource:
        parameters["resource"] = resource
    return AuthorizationRequest(_with_query_parameters(endpoint, parameters), verifier)


def _token_request(
    authorization_server_url: str, options: TokenRequestOptions, params: dict[str, str]
) -> OAuthTokens:
    metadata = options.metadata
    url = _secure_endpoint(
        metadata["token_endpoint"]
        if metadata is not None
        else _default_endpoint(authorization_server_url, "/token")
    )
    headers = dict(_FORM_HEADERS)
    if options.resource:
        params["resource"] = options.resource
    if options.add_client_authentication is not None:
        options.add_client_authentication(headers, params, url, metadata)
    else:
        supported = (metadata or {}).get("token_endpoint_auth_methods_supported")
        _apply_client_authentication(
            _select_client_auth_method(options.client_information, supported or []),
            options.client_information,
            headers,
            params,
        )
    response = (options.fetch or oauth_fetch)(
        url,
        method="POST",
        headers=headers,
        body=urllib.parse.urlencode(params).encode("ascii"),
        cancel_event=options.cancel_event,
    )
    text = response.text()
    value: object = None
    try:
        value = response.json()
    except ValueError:
        pass  # Not JSON: the status decides, and then the token parser.
    # Servers may report OAuth errors with any status, so the body is checked
    # before the status.
    code = value.get("error") if is_object(value) else None
    if is_object(value) and isinstance(code, str):
        description = value.get("error_description")
        error_uri = value.get("error_uri")
        raise OAuthError(
            code,
            description if isinstance(description, str) else code,
            error_uri if isinstance(error_uri, str) else None,
        )
    if not response.ok:
        raise OAuthError("server_error", f"HTTP {response.status}: {text}")
    return parse_oauth_tokens(value)


def register_client(
    authorization_server_url: str,
    *,
    client_metadata: OAuthClientMetadata,
    metadata: AuthorizationServerMetadata | None = None,
    scope: str | None = None,
    fetch: OAuthFetch | None = None,
    cancel_event: threading.Event | None = None,
) -> OAuthClientInformation:
    """Register a client dynamically (RFC 7591) and return what the server answered.

    Raises ``OAuthFlowError`` when the server's metadata names no registration
    endpoint and ``OAuthRegistrationError`` when the server refuses.
    """

    endpoint = (metadata or {}).get("registration_endpoint")
    if metadata is not None and not endpoint:
        raise OAuthFlowError(
            "Authorization server does not support dynamic client registration"
        )
    document = dict(client_metadata)
    if document.get("application_type") is None:
        document["application_type"] = _application_type(
            document.get("redirect_uris") or []
        )
    if scope:
        document["scope"] = scope
    response = (fetch or oauth_fetch)(
        endpoint or _default_endpoint(authorization_server_url, "/register"),
        method="POST",
        headers=_JSON_HEADERS,
        body=json.dumps(document, ensure_ascii=False, allow_nan=False).encode("utf-8"),
        cancel_event=cancel_event,
    )
    if not response.ok:
        raise OAuthRegistrationError(response.status, response.text())
    try:
        answer = response.json()
    except ValueError:
        raise OAuthFlowError("Invalid OAuth client registration response") from None
    return parse_client_information(answer)


def exchange_authorization_code(
    authorization_server_url: str,
    options: TokenRequestOptions,
    *,
    code: str,
    code_verifier: str,
    redirect_url: str,
) -> OAuthTokens:
    """Exchange an authorization code for tokens."""

    return _token_request(
        authorization_server_url,
        options,
        {
            "grant_type": "authorization_code",
            "code": code,
            "code_verifier": code_verifier,
            "redirect_uri": redirect_url,
        },
    )


def refresh_authorization(
    authorization_server_url: str, options: TokenRequestOptions, *, refresh_token: str
) -> OAuthTokens:
    """Get new tokens for a refresh token.

    An answer without a new refresh token keeps ``refresh_token``.
    """

    tokens = _token_request(
        authorization_server_url,
        options,
        {"grant_type": "refresh_token", "refresh_token": refresh_token},
    )
    return {"refresh_token": refresh_token, **tokens}


def step_up_scope(granted: str | None, challenged: str | None) -> str | None:
    """The scope of an authorization after an ``insufficient_scope``
    challenge: the scopes granted so far and the challenged ones, each once
    (SEP-2350), since a challenge may list only the missing scopes.

    Without challenged scopes it is ``None``, which lets the flow choose.
    """

    if not challenged:
        return None
    scopes = (name for scope in (granted, challenged) for name in (scope or "").split())
    return " ".join(dict.fromkeys(scopes))


def _with_scope(tokens: OAuthTokens, scope: str | None) -> OAuthTokens:
    return {**tokens, "scope": scope} if "scope" not in tokens and scope else tokens


def _discovery_state(
    info: OAuthServerInfo, resource_metadata_url: str | None
) -> OAuthDiscoveryState:
    state: OAuthDiscoveryState = {
        "authorizationServerUrl": info.authorization_server_url
    }
    if info.authorization_server_metadata is not None:
        state["authorizationServerMetadata"] = info.authorization_server_metadata
    if info.resource_metadata is not None:
        state["resourceMetadata"] = info.resource_metadata
    if resource_metadata_url:
        state["resourceMetadataUrl"] = resource_metadata_url
    return state


def _discover(
    provider: OAuthClientProvider, options: OAuthFlowOptions
) -> OAuthServerInfo:
    """What the flow knows about the authorization server.

    With a configured metadata URL nothing is read from or written to the
    provider's discovery state, so a changed URL applies at once.
    """

    metadata_url = options.authorization_server_metadata_url
    if metadata_url:
        return discover_oauth_server_info(
            options.server_url,
            resource_metadata_url=options.resource_metadata_url,
            authorization_server_metadata_url=_secure_endpoint(metadata_url),
            fetch=options.fetch,
            skip_issuer_validation=options.skip_issuer_validation,
            cancel_event=options.cancel_event,
        )
    load = getattr(provider, "discovery_state", None)
    cached: OAuthDiscoveryState | None = load() if load is not None else None
    if cached and cached.get("authorizationServerUrl"):
        server_url: str = cached["authorizationServerUrl"]
        metadata = cached.get("authorizationServerMetadata")
        if metadata is None:
            metadata = discover_authorization_server_metadata(
                server_url,
                fetch=options.fetch,
                skip_issuer_validation=options.skip_issuer_validation,
                cancel_event=options.cancel_event,
            )
        info = OAuthServerInfo(server_url, metadata, cached.get("resourceMetadata"))
    else:
        info = discover_oauth_server_info(
            options.server_url,
            resource_metadata_url=options.resource_metadata_url,
            fetch=options.fetch,
            skip_issuer_validation=options.skip_issuer_validation,
            cancel_event=options.cancel_event,
        )
    save = getattr(provider, "save_discovery_state", None)
    if save is not None:
        save(_discovery_state(info, options.resource_metadata_url))
    return info


def _client_metadata_document(
    provider: OAuthClientProvider, metadata: AuthorizationServerMetadata | None
) -> OAuthClientMetadataDocument | None:
    choose = getattr(provider, "client_metadata_document", None)
    document: OAuthClientMetadataDocument | None = (
        choose(metadata) if choose is not None else None
    )
    if document is None:
        return None
    url = parse_http_url(document.url)
    if url is None or url.scheme != "https" or url.pathname == "/":
        raise OAuthFlowError("Invalid OAuth client metadata URL")
    return document


def _register(
    provider: OAuthClientProvider,
    options: OAuthFlowOptions,
    discovered: OAuthServerInfo,
    scope: str | None,
) -> OAuthClientInformation:
    """Register a client for a provider that has none, and store it."""

    if options.authorization_code:
        raise OAuthFlowError("OAuth client information is missing during code exchange")
    save = getattr(provider, "save_client_information", None)
    if save is None:
        raise OAuthFlowError("OAuth client information cannot be persisted")
    client = register_client(
        discovered.authorization_server_url,
        client_metadata=provider.client_metadata,
        metadata=discovered.authorization_server_metadata,
        scope=scope,
        fetch=options.fetch,
        cancel_event=options.cancel_event,
    )
    save(client)
    return client


def _check_issuer(
    metadata: AuthorizationServerMetadata | None, iss: str | None
) -> None:
    """Refuse an authorization response that is not from this server (RFC 9207).

    A code from another authorization server must never be sent to this one.
    Without metadata there is no issuer to compare with, so any ``iss`` is
    rejected.
    """

    issuer: str | None = (metadata or {}).get("issuer")
    promised = (metadata or {}).get("authorization_response_iss_parameter_supported")
    if (iss is not None or promised) and iss != issuer:
        raise OAuthIssuerMismatchError(issuer, iss)


def _refresh(
    provider: OAuthClientProvider,
    options: OAuthFlowOptions,
    authorization_server_url: str,
    token_options: TokenRequestOptions,
) -> bool:
    """Refresh the provider's tokens; False when the user has to authorize.

    A refresh the server did not answer, or answered with ``server_error`` or
    with something that is no token response, is no reason to give up: the
    flow goes on to a new authorization. A cancelled flow, an insecure token
    endpoint and any other OAuth error are raised.
    """

    existing = None if options.skip_refresh else provider.tokens()
    refresh_token = (existing or {}).get("refresh_token")
    if not existing or not refresh_token:
        return False
    try:
        tokens = refresh_authorization(
            authorization_server_url, token_options, refresh_token=refresh_token
        )
        # A refresh without `scope` keeps the scope of the grant (RFC 6749,
        # section 6).
        provider.save_tokens(_with_scope(tokens, existing.get("scope")))
    except Exception as error:  # noqa: BLE001 - as Pi: a refresh that failed for a reason not named here falls through to a new authorization
        cancel = options.cancel_event
        if (cancel is not None and cancel.is_set()) or isinstance(
            error, (McpAbortError, OAuthInsecureEndpointError)
        ):
            raise
        if isinstance(error, OAuthError) and error.code != "server_error":
            raise
        return False
    return True


def _run_flow(
    provider: OAuthClientProvider, options: OAuthFlowOptions
) -> OAuthFlowResult:
    discovered = _discover(provider, options)
    server_url = discovered.authorization_server_url
    metadata = discovered.authorization_server_metadata
    resource_metadata = discovered.resource_metadata
    resource = select_resource(options.server_url, resource_metadata)
    # An empty scope, for example from `scopes_supported: []`, falls through
    # to the next source.
    scope: str | None = (
        options.scope
        or " ".join((resource_metadata or {}).get("scopes_supported") or [])
        or provider.client_metadata.get("scope")
        or None
    )
    client = provider.client_information()
    document = _client_metadata_document(provider, metadata) if client is None else None
    if document is not None:
        client = {"client_id": document.url}
    if client is None:
        client = _register(provider, options, discovered, scope)
    # The document's redirect URI may differ from the provider's, for example
    # by a server-specific path.
    redirect_url = document.redirect_url if document else provider.redirect_url
    token_options = TokenRequestOptions(
        client_information=client,
        metadata=metadata,
        resource=resource,
        add_client_authentication=getattr(provider, "add_client_authentication", None),
        fetch=options.fetch,
        cancel_event=options.cancel_event,
    )
    if options.authorization_code:
        _check_issuer(metadata, options.iss)
        tokens = exchange_authorization_code(
            server_url,
            token_options,
            code=options.authorization_code,
            code_verifier=provider.code_verifier(),
            redirect_url=redirect_url,
        )
        # A response without `scope` grants the requested scope (RFC 6749,
        # section 5.1). It is recorded so a step-up can keep it. Callers pass
        # the options of the authorization request, so `scope` is what was
        # requested.
        provider.save_tokens(_with_scope(tokens, scope))
        return "AUTHORIZED"
    if _refresh(provider, options, server_url, token_options):
        return "AUTHORIZED"
    new_state = getattr(provider, "state", None)
    authorization = start_authorization(
        server_url,
        client_information=client,
        redirect_url=redirect_url,
        metadata=metadata,
        scope=scope,
        state=new_state() if new_state is not None else None,
        resource=resource,
    )
    provider.save_code_verifier(authorization.code_verifier)
    provider.redirect_to_authorization(authorization.authorization_url)
    return "REDIRECT"


def authorize_mcp(
    provider: OAuthClientProvider, options: OAuthFlowOptions
) -> OAuthFlowResult:
    """Run the flow once; see the module text.

    When the authorization server refuses the client or the grant, the
    provider forgets what was refused and the flow runs once more, which
    registers a new client or asks the user to authorize again.
    """

    try:
        return _run_flow(provider, options)
    except OAuthError as error:
        if error.code in _STALE_CLIENT_ERRORS:
            kind: InvalidationKind = "all"
        elif error.code == _STALE_GRANT_ERROR:
            kind = "tokens"
        else:
            raise
    invalidate = getattr(provider, "invalidate_credentials", None)
    if invalidate is not None:
        invalidate(kind)
    return _run_flow(provider, options)


class _Authorization:
    """One run of the flow, which every rejected request that arrives
    meanwhile waits for."""

    __slots__ = ("done", "error")

    def __init__(self) -> None:
        self.done = threading.Event()
        self.error: Exception | None = None


def _access_token(tokens: OAuthTokens | None) -> str | None:
    token = (tokens or {}).get("access_token")
    return token if isinstance(token, str) else None


class OAuthTransportAuth:
    """The auth provider of a ``StreamableHttpTransport`` for an
    ``OAuthClientProvider``; ``adapt_oauth_provider`` makes it.

    ``token`` gives the provider's access token. ``on_unauthorized`` runs the
    flow after the server rejected a request: it refreshes the tokens, or
    raises ``McpOAuthAuthorizationRequiredError`` when the user has to
    authorize (again); the provider has been handed the authorization URL
    then. A challenge that reports ``insufficient_scope`` goes to the
    authorization at once, for the granted scopes and the challenged ones.

    Rejected requests share one run of the flow: one that arrives while it
    runs waits for it and gets its outcome. A request whose token was
    replaced already is just sent again, since with rotating refresh tokens
    a second refresh with the old one would fail and lose the new grant. The
    flow runs on the thread of the request that started it, an I/O thread of
    the transport; it has no time limit unless ``fetch`` sets one.
    """

    def __init__(
        self, provider: OAuthClientProvider, *, fetch: OAuthFetch | None = None
    ) -> None:
        self._provider = provider
        self._fetch = fetch
        # Guards `_running`. Never held while the flow runs.
        self._lock = threading.Lock()
        self._running: _Authorization | None = None

    def token(self) -> str | None:
        return _access_token(self._provider.tokens())

    def on_unauthorized(self, context: UnauthorizedContext) -> None:
        challenge = parse_www_authenticate(context.www_authenticate)
        insufficient_scope = challenge.error == "insufficient_scope"
        with self._lock:
            running = self._running
            started = running is None
            if running is None:
                running = self._running = _Authorization()
        if not started:
            running.done.wait()
            if running.error is not None:
                raise running.error
            return
        try:
            # Pi looks at the token before it starts a run; here it is done
            # in the run, so no run can end between the look and the start.
            if insufficient_scope or not self._replaced(context.token):
                self._authorize(context, challenge, insufficient_scope)
        except Exception as error:
            running.error = error
            raise
        finally:
            with self._lock:
                self._running = None
            running.done.set()

    def _replaced(self, rejected: str | None) -> bool:
        """Whether the provider holds another token than ``rejected`` now:
        another request renewed it, and the request is just sent again."""

        if rejected is None:
            return False
        current = self.token()
        return current is not None and current != rejected

    def _authorize(
        self, context: UnauthorizedContext, challenge: OAuthChallenge, step_up: bool
    ) -> None:
        scope = challenge.scope
        if step_up:
            granted = self._provider.tokens()
            scope = step_up_scope((granted or {}).get("scope"), challenge.scope)
        result = authorize_mcp(
            self._provider,
            OAuthFlowOptions(
                server_url=context.server_url,
                resource_metadata_url=challenge.resource_metadata_url,
                scope=scope,
                fetch=self._fetch,
                skip_refresh=step_up,
            ),
        )
        if result == "REDIRECT":
            raise McpOAuthAuthorizationRequiredError()


def adapt_oauth_provider(
    provider: OAuthClientProvider, *, fetch: OAuthFetch | None = None
) -> OAuthTransportAuth:
    """The auth provider of an HTTP transport for ``provider`` (Pi
    ``adaptOAuthProvider``); see ``OAuthTransportAuth``. ``fetch`` sends the
    requests of the flow; the library's default when it is ``None``."""

    return OAuthTransportAuth(provider, fetch=fetch)
