"""The sign-in to an MCP server that uses OAuth (Pi ``signInMcpServer`` in
``ext/oauth.ts``).

A connection never opens a browser; it only sends and refreshes stored
tokens (``credentials.py``). ``sign_in_mcp_server`` is what the user starts
with ``/mcp login`` or ``pipy mcp login``:

1. It listens for the authorization response on a loopback port: the
   configured one, else the port of the redirect URI the stored client was
   registered with, else a free one. A port that is taken is replaced by a
   free one unless it was configured.
2. It forgets the ``state`` parameter of an earlier sign-in. A stored client
   that was registered for another redirect URI is dropped with its tokens,
   unless the client is configured (``oauth.clientId``). With
   ``oauth.clientRegistration: "cimd"`` every stored client is dropped with
   its tokens: a Client ID Metadata Document is never stored, so a stored
   client was registered before the setting was changed.
3. It runs the flow once. A stored refresh token is tried first, which needs
   no browser, unless the server asked for more scope.
4. Otherwise the prompt is shown the authorization URL, and the sign-in
   waits for the browser to arrive at the loopback port or for the redirect
   URL the user pasted, whichever comes first. The response is accepted
   only on the path of the redirect URI the flow chose, which with a Client
   ID Metadata Document may be one of this MCP server alone
   (``/callback/<id>``, see ``credentials.client_metadata_document``).
5. The code is exchanged for tokens, which the store keeps.

The scope asked for is the configured ``oauth.scope`` together with the
scope of the server's last challenge. After an ``insufficient_scope``
challenge the scopes granted so far are added as well, because such a
challenge may name only the missing ones and a token with just those would
lose the access the old one had.

Cancelling. Setting ``cancel_event`` ends the sign-in at every step with
``McpSignInCancelledError``, also while a request to the authorization
server is pending. Every such request is limited to 15 seconds besides.

Threads. The function blocks its caller. ``show_authorization_url`` is
called on the caller's thread. ``prompt_for_redirect_url`` is called on a
daemon thread of its own (``mcp-sign-in-prompt``) and has to return once
the event it is given is set; what it returns after that is not used. The
sign-in waits a second for it to return and then goes on without it.

Where this differs from Pi:

- The wait for the browser is registered before the prompt is shown the
  URL, so a browser that answers at once is not turned away.
- A pasted text that is a URL of another scheme than ``http`` and ``https``
  is answered as one that does not match the redirect URI.
- Scopes are separated with ``str.split``, whose white space is not exactly
  JavaScript's ``\\s``.
- As in Pi, the refresh a sign-in tries first does not take the refresh
  lock of the server's store.
"""

from __future__ import annotations

import threading
import urllib.parse
import webbrowser
from dataclasses import dataclass, replace
from typing import Final, Protocol

from pipy_harness.native.mcp.config import parse_http_url
from pipy_harness.native.mcp.credentials import (
    CALLBACK_HOST,
    CALLBACK_PATH,
    OAUTH_REQUEST_TIMEOUT_MS,
    McpOAuthSettings,
    callback_id,
    create_oauth_provider,
    fixed_redirect_url,
    registered_redirect_urls,
)
from pipy_harness.native.mcp.oauth.callback import (
    OAuthCallbackServer,
    OAuthCallbackServerOptions,
)
from pipy_harness.native.mcp.oauth.errors import OAuthFlowError
from pipy_harness.native.mcp.oauth.fetch import OAuthFetch, create_oauth_fetch
from pipy_harness.native.mcp.oauth.flow import OAuthFlowOptions, authorize_mcp
from pipy_harness.native.mcp.oauth.page import render_sign_in_page
from pipy_harness.native.mcp.oauth.provider import McpOAuthState, McpOAuthStateStore
from pipy_harness.native.mcp.oauth.types import OAuthChallenge, can_parse_url
from pipy_harness.native.mcp.protocol import is_object

_POLL_SECONDS: Final = 0.05
# How long the end of a sign-in waits for its prompt to return.
_PROMPT_JOIN_SECONDS: Final = 1.0


class McpSignInCancelledError(Exception):
    """The sign-in was cancelled: by its cancel event, or by the user at the
    prompt for the redirect URL."""

    def __init__(self) -> None:
        super().__init__("Sign-in cancelled")


class McpSignInPrompt(Protocol):
    """How a sign-in reaches the user; see the module text for the threads."""

    def show_authorization_url(self, url: str) -> None:
        """Show the authorization URL to the user and open it in a browser."""

    def prompt_for_redirect_url(self, cancel_event: threading.Event) -> str | None:
        """Ask for the redirect URL from the browser's address bar, for when
        the browser cannot reach the loopback port (over SSH, for example).

        Returns ``None`` or an empty text when the user cancels, which
        cancels the sign-in. ``cancel_event`` is set once the answer is no
        longer wanted: the browser arrived, or the sign-in was cancelled.
        A prompt that cannot ask waits for the event and returns ``None``
        (``wait_for_cancel``).
        """


def open_browser(url: str) -> None:
    """Open ``url`` in the system's browser, for ``show_authorization_url``.
    The user is shown the URL as well, so a browser that cannot be started
    is not an error."""

    try:
        webbrowser.open(url)
    except Exception:  # noqa: BLE001 - best effort: the user has the URL
        pass


def wait_for_cancel(cancel_event: threading.Event) -> None:
    """The ``prompt_for_redirect_url`` of a prompt that cannot ask: only the
    browser can finish the sign-in."""

    cancel_event.wait()


@dataclass(frozen=True, slots=True)
class CallbackSettings:
    """Where the loopback callback server listens and the redirect URI it
    serves (Pi ``callbackSettings``)."""

    # The address to listen on.
    host: str
    # The host name in the redirect URI.
    redirect_host: str
    port: int | None
    path: str
    # The exact redirect URI, when the port is fixed.
    fixed_redirect_url: str | None


def callback_settings(settings: McpOAuthSettings) -> CallbackSettings:
    """The callback server of a server's ``oauth`` settings.

    ``oauth.callbackUrl`` gives host and path, and the port when it names
    one; else ``oauth.callbackPort`` is the port. ``localhost`` is served on
    127.0.0.1, where browsers end up when ``::1`` refuses.
    """

    configured = settings.callback_url
    url = parse_http_url(
        f"http://{CALLBACK_HOST}{CALLBACK_PATH}" if configured is None else configured
    )
    if url is None:
        raise ValueError(f"Invalid OAuth callback URL: {configured}")
    address = url.hostname.removeprefix("[").removesuffix("]")
    return CallbackSettings(
        host=CALLBACK_HOST if address == "localhost" else address,
        redirect_host=address,
        port=settings.callback_port if url.port is None else url.port,
        path=url.pathname,
        fixed_redirect_url=fixed_redirect_url(settings),
    )


def merge_scopes(*scopes: str | None) -> str | None:
    """The scopes of all the lists, each once; ``None`` when there is none."""

    merged = dict.fromkeys(name for scope in scopes for name in (scope or "").split())
    return " ".join(merged) or None


def step_up_scope(granted: str | None, challenged: str | None) -> str | None:
    """The scope of a sign-in after an ``insufficient_scope`` challenge: the
    scopes granted so far and the challenged ones (Pi ``stepUpScope``).

    Without challenged scopes it is ``None``, which lets the flow choose.
    """

    if not challenged:
        return None
    return merge_scopes(granted, challenged) or ""


@dataclass(frozen=True, slots=True)
class _AuthorizationResponse:
    code: str
    iss: str | None


def _first(params: dict[str, list[str]], name: str) -> str | None:
    values = params.get(name)
    return values[0] if values else None


def response_from_redirect_url(
    text: str, state: str, redirect_url: str
) -> _AuthorizationResponse:
    """The authorization response in a redirect URL the user pasted.

    Raises ``OAuthFlowError`` for a text that is no URL, a URL that is not
    this sign-in's redirect URI or carries another ``state``, an ``error``
    parameter, and a missing code.
    """

    pasted = text.strip()
    if not can_parse_url(pasted):
        raise OAuthFlowError(
            "Expected the full redirect URL from the browser address bar"
        )
    url = parse_http_url(pasted)
    expected = parse_http_url(redirect_url)
    # A server-specific redirect URI tells authorization servers apart, so
    # it must match exactly.
    if (
        url is None
        or expected is None
        or url.origin != expected.origin
        or url.pathname != expected.pathname
    ):
        raise OAuthFlowError(
            "The redirect URL does not match this sign-in's redirect URI"
        )
    params = urllib.parse.parse_qs(url.query, keep_blank_values=True)
    error = _first(params, "error")
    if error:
        description = _first(params, "error_description")
        raise OAuthFlowError(error if description is None else description)
    if _first(params, "state") != state:
        raise OAuthFlowError("The redirect URL belongs to a different sign-in")
    code = _first(params, "code")
    if not code:
        raise OAuthFlowError("The redirect URL does not contain an authorization code")
    return _AuthorizationResponse(code, _first(params, "iss"))


def _wait_for_authorization_response(
    callback: OAuthCallbackServer,
    state: str,
    redirect_url: str,
    authorization_url: str,
    prompt: McpSignInPrompt,
    cancel_event: threading.Event | None,
) -> _AuthorizationResponse:
    """Show the URL, then wait for the browser's callback or a pasted
    redirect URL, whichever comes first (Pi ``waitForAuthorizationResponse``).
    """

    expected = parse_http_url(redirect_url)
    # Registered before the URL is shown: the browser may answer at once.
    from_browser = callback.wait_for_callback(
        state, None if expected is None else expected.pathname
    )
    prompt.show_authorization_url(authorization_url)
    # Set when the wait is over, so the prompt returns.
    stop = threading.Event()
    answered = threading.Event()
    answer: list[str | None | Exception] = []

    def ask() -> None:
        try:
            answer.append(prompt.prompt_for_redirect_url(stop))
        except Exception as error:  # noqa: BLE001 - cross-thread capture: raised by the waiting caller
            answer.append(error)
        finally:
            answered.set()

    asking = threading.Thread(target=ask, name="mcp-sign-in-prompt", daemon=True)
    asking.start()
    try:
        while True:
            if from_browser.done.is_set():
                received = from_browser.result()
                return _AuthorizationResponse(received.code, received.iss)
            if answered.is_set():
                pasted = answer[0]
                if isinstance(pasted, Exception):
                    raise pasted
                if pasted is None or not pasted.strip():
                    raise McpSignInCancelledError()
                return response_from_redirect_url(pasted, state, redirect_url)
            if cancel_event is not None and cancel_event.is_set():
                raise McpSignInCancelledError()
            from_browser.done.wait(_POLL_SECONDS)
    finally:
        stop.set()
        # A prompt returns at once now; one that does not is left behind.
        asking.join(_PROMPT_JOIN_SECONDS)


def _listen_for_callback(
    settings: CallbackSettings,
    extra_paths: tuple[str, ...],
    port: int | None,
    required: bool,
) -> OAuthCallbackServer:
    """Listen on ``port``, or on a free port when it is taken and not
    ``required`` (Pi ``listenForCallback``)."""

    options = OAuthCallbackServerOptions(
        host=settings.host,
        redirect_host=settings.redirect_host,
        port=port,
        path=settings.path,
        extra_paths=extra_paths,
        render_page=render_sign_in_page,
    )
    try:
        return OAuthCallbackServer(options)
    except (OSError, OverflowError):
        if required or port is None:
            raise
    return OAuthCallbackServer(replace(options, port=None))


def _registered_port(redirect_urls: list[str]) -> int | None:
    """The port of the redirect URI a stored client was registered with."""

    url = parse_http_url(redirect_urls[0]) if redirect_urls else None
    return None if url is None else url.port or None


def _forget_previous_sign_in(
    store: McpOAuthStateStore,
    stored: McpOAuthState,
    settings: McpOAuthSettings,
    redirect_url: str,
) -> None:
    """Prepare the stored state for a sign-in that redirects to
    ``redirect_url``."""

    state = dict(stored)
    # Every sign-in gets a new `state` parameter.
    state.pop("oauthState", None)
    # A registered client cannot use another redirect URI, and its tokens
    # belong to it. A Client ID Metadata Document is not stored, so with
    # one, a stored client was registered before and is replaced.
    if settings.client_id:
        keep_client = True
    elif settings.client_registration == "cimd":
        keep_client = not stored.get("clientInformation")
    else:
        keep_client = redirect_url in registered_redirect_urls(stored)
    if not keep_client:
        for key in ("clientInformation", "tokens", "tokensExpireAt"):
            state.pop(key, None)
    store.save(state)


def _granted_scope(stored: McpOAuthState | None) -> str | None:
    tokens = None if stored is None else stored.get("tokens")
    scope = tokens.get("scope") if is_object(tokens) else None
    return scope if isinstance(scope, str) else None


def _redirect_uri_of(authorization_url: str) -> str | None:
    query = urllib.parse.urlsplit(authorization_url).query
    return _first(urllib.parse.parse_qs(query), "redirect_uri")


def sign_in_mcp_server(
    *,
    server_url: str,
    store: McpOAuthStateStore,
    settings: McpOAuthSettings,
    prompt: McpSignInPrompt,
    challenge: OAuthChallenge | None = None,
    cancel_event: threading.Event | None = None,
    fetch: OAuthFetch | None = None,
) -> None:
    """Sign in to the MCP server at ``server_url``; see the module text.

    The tokens are saved to ``store``. ``challenge`` is the server's last
    challenge, for its resource metadata URL and its scope. ``fetch`` sends
    the requests to the authorization server; the default limits each to
    ``OAUTH_REQUEST_TIMEOUT_MS``.

    Raises ``McpSignInCancelledError`` when ``cancel_event`` is set or the
    user cancelled, and the error of the step that failed otherwise.
    """

    if cancel_event is not None and cancel_event.is_set():
        raise McpSignInCancelledError()
    stored = store.load()
    step_up = challenge is not None and challenge.error == "insufficient_scope"
    listen = callback_settings(settings)
    # The port of the registered redirect URI keeps the registered client
    # valid.
    preferred_port = (
        _registered_port(registered_redirect_urls(stored))
        if listen.port is None
        else listen.port
    )
    cimd = settings.client_registration == "cimd"
    callback = _listen_for_callback(
        listen,
        # The redirect URI of a server-specific Client ID Metadata Document.
        (f"{CALLBACK_PATH}/{callback_id(server_url)}",) if cimd else (),
        preferred_port,
        listen.port is not None,
    )
    redirect_url = listen.fixed_redirect_url or callback.redirect_url
    try:
        if stored is not None:
            _forget_previous_sign_in(store, stored, settings, redirect_url)
        authorization_urls: list[str] = []
        provider = create_oauth_provider(
            server_url, store, settings, redirect_url, authorization_urls.append
        )
        challenged = None if challenge is None else challenge.scope
        flow = OAuthFlowOptions(
            server_url=server_url,
            fetch=create_oauth_fetch(timeout_ms=OAUTH_REQUEST_TIMEOUT_MS)
            if fetch is None
            else fetch,
            cancel_event=cancel_event,
            resource_metadata_url=(
                None if challenge is None else challenge.resource_metadata_url
            ),
            authorization_server_metadata_url=settings.auth_server_metadata_url,
            scope=merge_scopes(
                settings.scope,
                step_up_scope(_granted_scope(stored), challenged)
                if step_up
                else challenged,
            ),
        )
        # A refresh keeps the granted scope; a server that asks for more
        # needs the browser.
        if authorize_mcp(provider, replace(flow, skip_refresh=step_up)) == "AUTHORIZED":
            return
        if not authorization_urls:
            raise OAuthFlowError("OAuth flow did not produce an authorization URL")
        authorization_url = authorization_urls[-1]
        response = _wait_for_authorization_response(
            callback,
            provider.state(),
            # The flow chooses the redirect URI.
            _redirect_uri_of(authorization_url) or redirect_url,
            authorization_url,
            prompt,
            cancel_event,
        )
        authorize_mcp(
            provider,
            replace(flow, authorization_code=response.code, iss=response.iss),
        )
    except McpSignInCancelledError:
        raise
    except Exception as error:
        # A request that was cancelled fails with its own error; report the
        # cancellation it is.
        if cancel_event is not None and cancel_event.is_set():
            raise McpSignInCancelledError() from error
        raise
    finally:
        callback.close()
