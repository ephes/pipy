"""Error types of the OAuth client library."""

from __future__ import annotations

import json


class OAuthFlowError(Exception):
    """The flow cannot go on: a document or an answer is not what it needs.

    Pi raises a plain ``Error`` in these places.
    """


class OAuthError(Exception):
    """An error an authorization server reported (RFC 6749, section 5.2)."""

    def __init__(self, code: str, message: str, error_uri: str | None = None) -> None:
        super().__init__(message or code)
        self.code = code
        self.error_uri = error_uri


def _format_issuer(issuer: str | None) -> str:
    return "none" if issuer is None else json.dumps(issuer, ensure_ascii=False)


class OAuthIssuerMismatchError(Exception):
    """Metadata or an authorization response names another issuer than expected.

    ``expected`` is ``None`` when no authorization server metadata was
    discovered, ``received`` when an authorization response lacks the ``iss``
    parameter its server promised (RFC 9207).
    """

    def __init__(self, expected: str | None, received: str | None) -> None:
        super().__init__(
            f"OAuth issuer mismatch: expected {_format_issuer(expected)}, "
            f"received {_format_issuer(received)}"
        )
        self.expected = expected
        self.received = received


class OAuthInsecureEndpointError(Exception):
    """Credentials would go to an endpoint that is neither https nor loopback."""

    def __init__(self, endpoint: str) -> None:
        super().__init__(
            f"Refusing to send OAuth credentials to non-HTTPS endpoint {endpoint}"
        )
        self.endpoint = endpoint


class OAuthRegistrationError(Exception):
    """Dynamic client registration was refused."""

    def __init__(self, status: int, body: str) -> None:
        super().__init__(
            f"OAuth dynamic client registration failed with status {status}: {body}"
        )
        self.status = status
        self.body = body


class McpOAuthAuthorizationRequiredError(Exception):
    """The user has to authorize; no stored credential can be used."""

    def __init__(self) -> None:
        super().__init__("MCP OAuth authorization requires user interaction")
