"""Owned provider identity, adapted from Pi 1.4's utils/identity.ts.

Identity is request data, never credentials or provider construction authority.
Native user agents describe the actual pipy/Python installation.
"""

from __future__ import annotations

import platform
import sys
from dataclasses import dataclass
from importlib import metadata
from typing import Literal
from urllib.parse import urlsplit


class _Unset:
    __slots__ = ()


_UNSET = _Unset()


@dataclass(frozen=True, slots=True)
class CustomUserAgent:
    custom: str

    def __post_init__(self) -> None:
        if type(self.custom) is not str:
            raise TypeError("CustomUserAgent.custom must be an exact string")


@dataclass(frozen=True, slots=True, init=False)
class ClientAttribution:
    name: str
    url: str
    categories: tuple[str, ...] | _Unset

    def __init__(
        self,
        name: str,
        url: str,
        categories: tuple[str, ...] | list[str] | _Unset = _UNSET,
    ) -> None:
        if type(name) is not str or type(url) is not str:
            raise TypeError("ClientAttribution name/url must be exact strings")
        if categories is not _UNSET:
            if type(categories) is not tuple and type(categories) is not list:
                raise TypeError("categories must be an exact tuple or list")
            if any(type(child) is not str for child in categories):
                raise TypeError("categories must contain exact strings")
            categories = tuple(categories)
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "url", url)
        object.__setattr__(self, "categories", categories)


@dataclass(frozen=True, slots=True)
class ClientIdentity:
    user_agent: Literal["short", "detailed"] | CustomUserAgent | _Unset = _UNSET
    attribution: ClientAttribution | None | _Unset = _UNSET
    version: str | _Unset = _UNSET

    def __post_init__(self) -> None:
        validate_client_identity(self)


def validate_client_identity(identity: ClientIdentity | None) -> None:
    """Validate exact immutable carriers, including forged slot contents."""
    if identity is None:
        return
    if type(identity) is not ClientIdentity:
        raise TypeError("identity must be an exact ClientIdentity or None")
    _validate_user_agent(identity.user_agent)
    _validate_attribution(identity.attribution)
    if identity.version is not _UNSET and type(identity.version) is not str:
        raise TypeError("identity version must be an exact string")


def _validate_user_agent(mode: object) -> None:
    if mode is _UNSET:
        return
    if type(mode) is CustomUserAgent:
        if type(mode.custom) is not str:
            raise TypeError("custom user agent must be an exact string")
    elif type(mode) is not str or mode not in ("short", "detailed"):
        raise TypeError("user_agent must be short, detailed or CustomUserAgent")


def _validate_attribution(attribution: object) -> None:
    if attribution is _UNSET or attribution is None:
        return
    if type(attribution) is not ClientAttribution:
        raise TypeError("attribution must be an exact ClientAttribution or None")
    if type(attribution.name) is not str or type(attribution.url) is not str:
        raise TypeError("attribution name/url must be exact strings")
    categories = attribution.categories
    if categories is not _UNSET and (
        type(categories) is not tuple
        or any(type(child) is not str for child in categories)
    ):
        raise TypeError("captured categories must be an exact tuple of strings")


def merge_client_identity(*sources: ClientIdentity | None) -> ClientIdentity | None:
    """Fieldwise last-present wins; explicit null clears attribution."""
    user_agent: Literal["short", "detailed"] | CustomUserAgent | _Unset = _UNSET
    attribution: ClientAttribution | None | _Unset = _UNSET
    version: str | _Unset = _UNSET
    present = False
    for source in sources:
        validate_client_identity(source)
        if source is None:
            continue
        present = True
        if source.user_agent is not _UNSET:
            user_agent = source.user_agent
        if source.attribution is not _UNSET:
            attribution = source.attribution
        if source.version is not _UNSET:
            version = source.version
    return ClientIdentity(user_agent, attribution, version) if present else None


HARNESS_IDENTITY = ClientIdentity(
    attribution=ClientAttribution("pi", "https://pi.dev", ("cli-agent",))
)


def _installed_version() -> str | None:
    try:
        return metadata.version("pipy")
    except metadata.PackageNotFoundError:
        return None


def format_user_agent(identity: ClientIdentity | None = None) -> str:
    validate_client_identity(identity)
    mode = _UNSET if identity is None else identity.user_agent
    if type(mode) is CustomUserAgent:
        return mode.custom
    version = _UNSET if identity is None else identity.version
    effective = version if type(version) is str else _installed_version()
    product = "pipy" if effective is None else "pipy/" + effective
    if mode == "short":
        return product
    return (
        f"{product} ({sys.platform}; python/{platform.python_version()}; "
        f"{platform.machine()})"
    )


def _host_matches(target_url: str | None, host: str) -> bool:
    if target_url is None:
        return False
    try:
        parsed = urlsplit(target_url)
        # urlsplit defers invalid/non-numeric/out-of-range port rejection until
        # this property is read; Pi's new URL() rejects these actual targets.
        _ = parsed.port
        return bool(parsed.scheme and parsed.netloc) and parsed.hostname == host
    except ValueError:
        return False


def _open_code(provider: str, target_url: str | None) -> bool:
    return provider in ("opencode", "opencode-go") or _host_matches(
        target_url, "opencode.ai"
    )


def _nvidia_name(name: str) -> str:
    # JS charAt(0) is one UTF-16 code unit. An astral first character must not
    # undergo Python's whole-codepoint uppercase mapping.
    if not name or ord(name[0]) > 0xFFFF:
        return name
    return name[0].upper() + name[1:]


def identity_headers(
    identity: ClientIdentity | None,
    provider: str,
    *,
    target_url: str | None = None,
    session_id: str | None = None,
) -> dict[str, str]:
    """Low-precedence defaults; the caller owns actual target resolution."""
    validate_client_identity(identity)
    if type(provider) is not str or (
        target_url is not None and type(target_url) is not str
    ):
        raise TypeError("identity target must use exact strings")
    if session_id is not None and type(session_id) is not str:
        raise TypeError("identity session_id must be an exact string or None")
    headers = {"User-Agent": format_user_agent(identity)}
    attribution = _UNSET if identity is None else identity.attribution
    if type(attribution) is ClientAttribution:
        if provider == "openrouter" or (
            target_url is not None and "openrouter.ai" in target_url
        ):
            headers.update(
                {
                    "HTTP-Referer": attribution.url,
                    "X-OpenRouter-Title": attribution.name,
                }
            )
            if type(attribution.categories) is tuple and attribution.categories:
                headers["X-OpenRouter-Categories"] = ",".join(attribution.categories)
        elif provider == "nvidia" or _host_matches(
            target_url, "integrate.api.nvidia.com"
        ):
            headers["X-BILLING-INVOKE-ORIGIN"] = _nvidia_name(attribution.name)
        elif _open_code(provider, target_url):
            headers["x-opencode-client"] = attribution.name
    if session_id and _open_code(provider, target_url):
        headers["x-opencode-session"] = session_id
    return headers
