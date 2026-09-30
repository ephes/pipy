"""Tool path resolution, following Pi's ``path-utils.ts``.

Mirrors ``packages/coding-agent/src/core/tools/path-utils.ts`` and the parts
of ``src/utils/paths.ts`` it uses (pi-mono ``1b347794e``). A tool path
resolves against the session's working directory with no deny list and no
root containment, exactly like Pi:

1. Unicode space variants (no-break, narrow no-break, en/em spaces, …)
   become a plain space.
2. One leading ``@`` is stripped.
3. ``~`` and ``~/…`` expand to the home directory (``~user`` does not).
4. A ``file://`` URL becomes its path.
5. An absolute path is normalized, a relative one is joined to the working
   directory and normalized. Normalization is lexical, like Node's
   ``path.resolve``: ``..`` collapses and symlinks are not resolved.

:func:`resolve_read_path` (Pi ``resolveReadPath``, used by ``read`` and the
``@file``/``@image:`` references) also tries the file names macOS uses for
screenshots when the plain path does not exist: a narrow no-break space
before ``AM.``/``PM.``, the NFD form, a curly apostrophe, and NFD plus curly
apostrophe.
"""

from __future__ import annotations

import os
import posixpath
import re
import unicodedata
from collections.abc import Callable
from pathlib import Path
from urllib.parse import unquote, urlparse

_UNICODE_SPACES = re.compile("[  -   　]")
_NARROW_NO_BREAK_SPACE = " "
_AM_PM = re.compile(r" (AM|PM)\.", re.IGNORECASE)


def normalize_path(value: str) -> str:
    """Pi ``normalizePath`` with ``normalizeUnicodeSpaces`` and ``stripAtPrefix``.

    Raises ``ValueError`` for a ``file://`` URL Node's ``fileURLToPath``
    rejects (unparsable, or a host other than ``localhost``); the tools
    report it as an error result, as Pi's do.
    """

    normalized = _UNICODE_SPACES.sub(" ", value)
    if normalized.startswith("@"):
        normalized = normalized[1:]
    home = os.path.expanduser("~")
    if normalized == "~":
        return home
    if normalized.startswith("~/"):
        # Node's path.join keeps the home prefix for "~//x"; posixpath.join
        # would restart at the absolute suffix.
        return posixpath.normpath(f"{home}/{normalized[2:]}")
    if normalized.startswith("file://"):
        try:
            parsed = urlparse(normalized)
        except ValueError:
            raise ValueError(f"Invalid URL: {value}") from None
        # Node accepts only an empty or `localhost` host (any case), with no
        # credentials or port.
        if parsed.netloc.lower() not in ("", "localhost"):
            raise ValueError(f'File URL host must be "localhost" or empty: {value}')
        return unquote(parsed.path)
    return normalized


def resolve_to_cwd(value: str, cwd: Path) -> Path:
    """Pi ``resolveToCwd``: resolve ``value`` against ``cwd``."""

    normalized = normalize_path(value)
    joined = (
        normalized
        if normalized.startswith("/")
        else posixpath.join(str(cwd), normalized)
    )
    resolved = posixpath.normpath(joined)
    # Node's path.resolve collapses a leading "//"; posixpath keeps it.
    if resolved.startswith("//"):
        resolved = "/" + resolved.lstrip("/")
    return Path(resolved)


def resolve_read_path(
    value: str,
    cwd: Path,
    *,
    exists: Callable[[str], bool] = os.path.exists,
) -> Path:
    """Pi ``resolveReadPath``: :func:`resolve_to_cwd` plus macOS name variants."""

    resolved = str(resolve_to_cwd(value, cwd))
    if exists(resolved):
        return Path(resolved)
    am_pm = _AM_PM.sub(lambda match: f"{_NARROW_NO_BREAK_SPACE}{match[1]}.", resolved)
    if am_pm != resolved and exists(am_pm):
        return Path(am_pm)
    nfd = unicodedata.normalize("NFD", resolved)
    if nfd != resolved and exists(nfd):
        return Path(nfd)
    curly = resolved.replace("'", "’")
    if curly != resolved and exists(curly):
        return Path(curly)
    nfd_curly = nfd.replace("'", "’")
    if nfd_curly != resolved and exists(nfd_curly):
        return Path(nfd_curly)
    return Path(resolved)


__all__ = ["normalize_path", "resolve_read_path", "resolve_to_cwd"]
