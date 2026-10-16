"""Native product export/import/share and self-update helpers.

These helpers are the product-session counterpart to the metadata-only
``pipy-session export`` catalog utility. They operate on
``NativeSessionTree`` files and intentionally preserve full transcript content
except for auth-token/credential-shaped values.
"""

from __future__ import annotations

import base64
import copy
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, BinaryIO, TypeVar

from pipy_harness.native.cancellation import CancelToken, ProviderCancelledError
from pipy_harness.native.http import urlopen_read_cancellable
from pipy_harness.native.session_tree import (
    NativeSessionTree,
    _entry_to_json,
    _load_file_entries,
)

if TYPE_CHECKING:
    from pipy_harness.native.tool_renderer_lookup import ToolRendererLookup

# Text keys containing a marker are candidates; ``_key_is_sensitive`` then
# decides, so token-count fields such as ``totalTokens`` survive.
_SENSITIVE_KEY_MARKERS = r"api[_-]?key|token|secret|password|passwd|credential"
_SENSITIVE_KEY_CANDIDATE_RE = (
    rf"[A-Za-z0-9_.-]*?(?:{_SENSITIVE_KEY_MARKERS})[A-Za-z0-9_.-]*"
)
# A key prefix is matched first; its value is consumed only when the key is
# sensitive, so a skipped key's value is still scanned for nested secrets.
_JSON_SECRET_KEY_RE = re.compile(
    rf"(?i)(?P<quote>['\"])(?P<key>{_SENSITIVE_KEY_CANDIDATE_RE})(?P=quote)\s*:\s*"
)
# A quoted value that text cut short leaves open (a nested call's argument
# preview, an error cut to its bound) is a value to the end of the text, so
# no part of it survives.
_JSON_SECRET_VALUE_RE = re.compile(
    r"\"(?:\\.|[^\"\\])*(?:\"|\\?\Z)|'(?:\\.|[^'\\])*(?:'|\\?\Z)|[^\s,}\]]+"
)
_SECRET_ASSIGN_KEY_RE = re.compile(
    rf"(?i)(?<![A-Za-z0-9_.-])(?P<key>{_SENSITIVE_KEY_CANDIDATE_RE})"
    r"\s*(?P<sep>[:=])\s*"
)
_SECRET_ASSIGN_VALUE_RE = re.compile(r"\"[^\"]*(?:\"|\Z)|'[^']*(?:'|\Z)|[^\s<>'\"]+")
_SECRET_KEY_MARKER_RE = re.compile(r"api_?key|secret|password|passwd|credential")
# ``token`` marks a credential unless it is token accounting: the plural
# (``totalTokens``, ``tokensBefore``) or a count-shaped suffix
# (``totalTokenCount``, ``promptTokenDetails``).
_TOKEN_KEY_MARKER_RE = re.compile(
    r"token(?!s|_?(?:count|details|start|end|limit|usage|budget))"
)
_BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}")
_COMMON_TOKEN_RE = re.compile(
    r"\b(?:sk-[A-Za-z0-9._-]{8,}|ghp_[A-Za-z0-9._-]{8,}|"
    r"github_pat_[A-Za-z0-9._-]{8,}|ya29\.[A-Za-z0-9._-]{8,}|"
    r"xox[bp]-[A-Za-z0-9._-]{8,})\b"
)
SELF_UPDATE_PACKAGE_ENV = "PIPY_SELF_UPDATE_PACKAGE"
_MappingKeyT = TypeVar("_MappingKeyT")


class NativeExportError(RuntimeError):
    """Raised for user-facing native export/import/share failures."""


class InvalidSessionCwdError(NativeExportError):
    """An imported cwd requires explicit directory replacement."""


class ShareCancelled(NativeExportError):
    """Raised when a share request is cancelled before upload."""


@dataclass(frozen=True, slots=True)
class ShareResult:
    gist_id: str
    gist_url: str
    viewer_url: str | None


@dataclass(frozen=True, slots=True)
class UpdatePlan:
    method: str
    command: tuple[str, ...]
    executable: str
    automatic: bool
    reason: str | None = None


def redact_export_value(value: Any) -> Any:
    """Return ``value`` with auth-token/credential-shaped strings redacted."""

    if isinstance(value, Mapping):
        return _redact_export_mapping(value)
    if isinstance(value, list):
        return [redact_export_value(item) for item in value]
    if isinstance(value, tuple):
        return [redact_export_value(item) for item in value]
    if isinstance(value, str):
        return _redact_text(value)
    return value


def _redact_export_mapping(
    value: Mapping[_MappingKeyT, object],
) -> dict[str, Any]:
    redacted: dict[str, Any] = {}
    image_block = _is_image_block(value)
    for key, item in value.items():
        key_text = str(key)
        if image_block and key_text == "data":
            # Base64 image data is not text: a pattern that matched inside it
            # would only break the image.
            redacted[key_text] = item
        elif _key_is_sensitive(key_text):
            redacted[key_text] = "[REDACTED]"
        else:
            redacted[key_text] = redact_export_value(item)
    return redacted


_BASE64_DATA_RE = re.compile(r"[A-Za-z0-9+/]+={0,2}")


def _is_image_block(value: Mapping[Any, object]) -> bool:
    """A stored tool result image: ``{"type":"image","data","mimeType"}``."""

    data = value.get("data")
    return (
        value.get("type") == "image"
        and isinstance(value.get("mimeType"), str)
        and isinstance(data, str)
        and _BASE64_DATA_RE.fullmatch(data) is not None
    )


def _key_is_sensitive(key: str) -> bool:
    """Return whether ``key`` names a credential rather than a token count.

    ``OPENAI_API_KEY``, ``accessToken``, ``GITHUB_TOKEN`` and ``client_secret``
    are sensitive; ``totalTokens``, ``tokensBefore`` and ``totalTokenCount``
    are not.
    """

    normalized = key.lower().replace("-", "_").replace(".", "_")
    return bool(
        _SECRET_KEY_MARKER_RE.search(normalized)
        or _TOKEN_KEY_MARKER_RE.search(normalized)
    )


def _redact_text(value: str) -> str:
    value = _redact_keyed_values(
        value, _JSON_SECRET_KEY_RE, _JSON_SECRET_VALUE_RE, _redact_json_secret_field
    )
    value = _redact_keyed_values(
        value,
        _SECRET_ASSIGN_KEY_RE,
        _SECRET_ASSIGN_VALUE_RE,
        _redact_secret_assignment,
    )
    value = _BEARER_RE.sub("Bearer [REDACTED]", value)
    return _COMMON_TOKEN_RE.sub("[REDACTED]", value)


def _redact_keyed_values(
    text: str,
    key_re: re.Pattern[str],
    value_re: re.Pattern[str],
    render: Callable[[re.Match[str], str], str],
) -> str:
    """Replace the value after each sensitive key matched by ``key_re``.

    A non-sensitive key such as ``tokenUsage`` or ``max_tokens`` is skipped
    without consuming its value, so secrets nested in or following that value
    are still found.
    """

    parts: list[str] = []
    position = 0
    while (key_match := key_re.search(text, position)) is not None:
        value_end = (
            _secret_value_end(text, key_match.end(), value_re)
            if _key_is_sensitive(key_match.group("key"))
            else None
        )
        if value_end is None:
            parts.append(text[position : key_match.end()])
            position = key_match.end()
            continue
        parts.append(text[position : key_match.start()])
        parts.append(render(key_match, text[key_match.end() : value_end]))
        position = value_end
    parts.append(text[position:])
    return "".join(parts)


def _secret_value_end(text: str, start: int, value_re: re.Pattern[str]) -> int | None:
    """Return where the value starting at ``start`` ends, or ``None``.

    An object or array value is consumed through its matching bracket
    (quote-aware), or to the end of the text when it is unbalanced, so no part
    of a structured secret survives.
    """

    if text[start : start + 1] not in _CONTAINER_CLOSERS:
        value_match = value_re.match(text, start)
        return None if value_match is None else value_match.end()
    return _container_end(text, start)


_CONTAINER_CLOSERS = {"{": "}", "[": "]"}


def _container_end(text: str, start: int) -> int:
    """End of the bracketed value at ``start``; end of text if mismatched."""

    closers: list[str] = []
    index = start
    while index < len(text):
        char = text[index]
        if char in {'"', "'"}:
            index = _quoted_end(text, index)
            continue
        if char in _CONTAINER_CLOSERS:
            closers.append(_CONTAINER_CLOSERS[char])
        elif char in {"}", "]"}:
            if char != closers.pop():
                return len(text)
            if not closers:
                return index + 1
        index += 1
    return len(text)


def _quoted_end(text: str, start: int) -> int:
    """Index after the quoted string opening at ``start`` (or end of text)."""

    quote = text[start]
    index = start + 1
    while index < len(text):
        if text[index] == "\\":
            index += 2
        elif text[index] == quote:
            return index + 1
        else:
            index += 1
    return len(text)


def _redact_json_secret_field(key_match: re.Match[str], raw_value: str) -> str:
    quote = raw_value[0] if raw_value[:1] in {"'", '"'} else ""
    redacted = f"{quote}[REDACTED]{quote}" if quote else "[REDACTED]"
    return f"{key_match.group(0)}{redacted}"


def _redact_secret_assignment(key_match: re.Match[str], raw_value: str) -> str:
    del raw_value
    return f"{key_match.group('key')}{key_match.group('sep')}[REDACTED]"


def parse_command_path_argument(argument: str) -> str:
    """Parse the first Pi-style path argument, including simple quotes."""

    text = argument.lstrip()
    if not text:
        return ""
    quote = text[0] if text[0] in {"'", '"'} else ""
    if quote:
        escaped = False
        chars: list[str] = []
        for ch in text[1:]:
            if escaped:
                chars.append(ch)
                escaped = False
                continue
            if ch == "\\":
                escaped = True
                continue
            if ch == quote:
                return "".join(chars)
            chars.append(ch)
        return "".join(chars)
    return text.split(maxsplit=1)[0]


def default_html_export_path(tree: NativeSessionTree, *, cwd: Path) -> Path:
    stem = tree.path.stem if tree.path is not None else tree.session_id
    return cwd / f"pipy-session-{stem}.html"


def default_jsonl_export_path(*, cwd: Path) -> Path:
    stamp = datetime.now(UTC).isoformat().replace(":", "-").replace(".", "-")
    return cwd / f"session-{stamp}.jsonl"


def session_export_payload(
    tree: NativeSessionTree,
    *,
    system_prompt: str = "",
    tools: list[dict[str, Any]] | None = None,
    tool_renderers: ToolRendererLookup | None = None,
) -> dict[str, Any]:
    """The data an exported page shows.

    ``renderedTools`` (Pi's name) has the rows of the tool calls that
    renderers draw, by call id; it is left out when there are none.
    ``tool_renderers`` chooses the renderers. Without it only calls to MCP
    server tools are drawn, and no extension code runs.
    """

    payload = {
        "header": tree.get_header().to_json_dict(),
        "entries": [_entry_to_json(entry) for entry in tree.get_entries()],
        "leafId": tree.get_leaf_id(),
        "systemPrompt": system_prompt,
        "tools": tools or [],
    }
    redacted = _redact_export_mapping(payload)
    rendered = _rendered_tools(tree, tool_renderers)
    if rendered:
        redacted["renderedTools"] = rendered
    return redacted


def _rendered_tools(
    tree: NativeSessionTree, tool_renderers: ToolRendererLookup | None
) -> dict[str, dict[str, str]]:
    # Imported here: the rows need the chrome and tool-row modules, which
    # nothing else in this module does.
    from pipy_harness.native.export_tool_rows import render_export_tools
    from pipy_harness.native.tool_renderer_lookup import ToolRendererLookup

    return render_export_tools(
        tree.get_entries(),
        tool_renderers if tool_renderers is not None else ToolRendererLookup(),
        cwd=Path(tree.get_header().cwd),
        redact=redact_export_value,
    )


def encoded_session_payload(payload: Mapping[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return base64.b64encode(raw).decode("ascii")


def generate_session_html(payload: Mapping[str, Any]) -> str:
    encoded = encoded_session_payload(payload)
    return _HTML_TEMPLATE.replace("__PIPY_SESSION_DATA_BASE64__", encoded)


def export_native_session_to_html(
    tree: NativeSessionTree,
    output_path: Path,
    *,
    system_prompt: str = "",
    tools: list[dict[str, Any]] | None = None,
    tool_renderers: ToolRendererLookup | None = None,
) -> Path:
    if tree.path is None:
        raise NativeExportError("Cannot export in-memory session to HTML.")
    if not tree.get_entries():
        raise NativeExportError("Nothing to export yet.")
    output = Path(output_path).expanduser()
    html = generate_session_html(
        session_export_payload(
            tree,
            system_prompt=system_prompt,
            tools=tools,
            tool_renderers=tool_renderers,
        )
    )
    _write_export(tree.path, output, html)
    return output


def _write_export(source: Path | None, output: Path, text: str) -> None:
    if source is not None and (
        source.resolve() == output.resolve()
        or (source.exists() and output.exists() and source.samefile(output))
    ):
        raise NativeExportError("Export destination identifies the backing session.")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=output.parent,
            prefix=".pipy-export-",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, output)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _linearized_entry_dicts(tree: NativeSessionTree) -> list[dict[str, Any]]:
    previous_id: str | None = None
    result: list[dict[str, Any]] = []
    for entry in tree.get_branch():
        body = copy.deepcopy(_entry_to_json(entry))
        body["parentId"] = previous_id
        previous_id = str(body["id"])
        result.append(redact_export_value(body))
    return result


def export_native_branch_to_jsonl(tree: NativeSessionTree, output_path: Path) -> Path:
    if not tree.get_entries():
        raise NativeExportError("Nothing to export yet.")
    output = Path(output_path).expanduser()
    header = redact_export_value(
        {
            "type": "session",
            "version": tree.get_header().version,
            "id": tree.get_header().id,
            "timestamp": tree.get_header().timestamp,
            "cwd": tree.get_header().cwd,
        }
    )
    lines = [json.dumps(header, sort_keys=True)]
    lines.extend(
        json.dumps(entry, sort_keys=True) for entry in _linearized_entry_dicts(tree)
    )
    _write_export(tree.path, output, "\n".join(lines) + "\n")
    return output


def export_from_file(session_path: Path, output_path: Path | None = None) -> Path:
    source = Path(session_path).expanduser()
    if not source.is_file():
        raise NativeExportError(f"native session file not found: {source}")
    tree = NativeSessionTree.open(source, persist=False)
    output = (
        Path(output_path).expanduser()
        if output_path is not None
        else source.with_name(f"pipy-session-{source.stem}.html")
    )
    # Standalone CLI export is allowed from a file opened persist=False.
    if not tree.get_entries():
        raise NativeExportError("Nothing to export yet.")
    _write_export(source, output, generate_session_html(session_export_payload(tree)))
    return output


def import_native_session_jsonl(
    source_path: Path, *, session_dir: Path, missing_cwd: Path | None = None
) -> NativeSessionTree:
    source = Path(source_path).expanduser()
    if not source.is_file():
        raise NativeExportError(f"import file not found: {source}")
    header, entries = _load_file_entries(source)
    if header is None:
        raise NativeExportError(f"not a valid native session file: {source}")
    cwd = Path(header.cwd).expanduser()
    replacement_cwd = Path(missing_cwd).expanduser().resolve() if missing_cwd else None
    if header.cwd and not cwd.is_dir() and replacement_cwd is None:
        reason = "does not exist" if not cwd.exists() else "is not a directory"
        raise InvalidSessionCwdError(
            f"imported session cwd {reason}: {_redact_text(header.cwd)}"
        )
    if replacement_cwd is not None and not replacement_cwd.is_dir():
        raise NativeExportError(
            f"replacement cwd is not a directory: {replacement_cwd}"
        )
    destination_dir = Path(session_dir).expanduser()
    destination_dir.mkdir(parents=True, exist_ok=True)
    try:
        destination_dir.chmod(0o700)
    except OSError:
        pass
    destination, handle = _reserve_import_destination(destination_dir / source.name)
    try:
        with handle, source.open("rb") as source_handle:
            shutil.copyfileobj(source_handle, handle)
            if header.cwd and not cwd.is_dir() and replacement_cwd is not None:
                handle.seek(0)
                rewritten = _rewrite_import_cwd(handle.read(), replacement_cwd)
                handle.seek(0)
                handle.write(rewritten)
                handle.truncate()
            handle.flush()
            os.fsync(handle.fileno())
        imported = NativeSessionTree.open(destination)
        if entries:
            imported.set_leaf(entries[-1].id)
        return imported
    except BaseException:
        destination.unlink(missing_ok=True)
        raise


def _rewrite_import_cwd(content: bytes, cwd: Path) -> bytes:
    lines = content.splitlines(keepends=True)
    for index, line in enumerate(lines):
        try:
            body = json.loads(line)
        except json.JSONDecodeError:
            continue
        if (
            isinstance(body, dict)
            and body.get("type") == "session"
            and isinstance(body.get("id"), str)
        ):
            body["cwd"] = str(cwd)
            ending = (
                b"\r\n"
                if line.endswith(b"\r\n")
                else b"\n"
                if line.endswith(b"\n")
                else b""
            )
            lines[index] = json.dumps(body, sort_keys=True).encode("utf-8") + ending
            break
    return b"".join(lines)


def _reserve_import_destination(path: Path) -> tuple[Path, BinaryIO]:
    for index in range(10_000):
        candidate = (
            path if index == 0 else path.with_name(f"{path.stem}-{index}{path.suffix}")
        )
        try:
            fd = os.open(candidate, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
        except FileExistsError:
            continue
        return candidate, os.fdopen(fd, "w+b")
    raise NativeExportError(f"could not choose a unique import path for {path}")


def resolve_github_token(
    env: Mapping[str, str] | None = None,
    *,
    run_gh_token: Callable[[], str | None] | None = None,
) -> str | None:
    source = env if env is not None else os.environ
    for key in ("GITHUB_TOKEN", "GH_TOKEN"):
        token = source.get(key, "").strip()
        if token:
            return token
    if run_gh_token is not None:
        gh_token = run_gh_token()
        return gh_token.strip() if gh_token else None
    try:
        completed = subprocess.run(
            ["gh", "auth", "token"],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode == 0:
        return completed.stdout.strip() or None
    return None


def create_secret_gist(
    *,
    html: str,
    filename: str,
    token: str,
    opener: Callable[[urllib.request.Request, float], Any] | None = None,
    cancelled: Callable[[], bool] | None = None,
    cancel_token: CancelToken | None = None,
    api_url: str = "https://api.github.com/gists",
    timeout: float = 15.0,
) -> ShareResult:
    if cancelled is not None and cancelled():
        raise ShareCancelled("Share cancelled.")
    if cancel_token is not None:
        try:
            cancel_token.raise_if_cancelled()
        except ProviderCancelledError as exc:
            raise ShareCancelled("Share cancelled.") from exc
    payload = json.dumps(
        {"public": False, "files": {filename: {"content": html}}},
        ensure_ascii=False,
    ).encode("utf-8")
    request = urllib.request.Request(
        api_url,
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "User-Agent": "pipy-native-export",
        },
    )
    try:
        if opener is not None:
            response = opener(request, timeout)
            status = int(getattr(response, "status", 200))
            body_bytes = response.read()
        else:
            status, body_bytes = urlopen_read_cancellable(
                request,
                timeout_seconds=timeout,
                cancel_token=cancel_token,
            )
    except urllib.error.HTTPError as exc:
        detail = _github_error_message(exc.read().decode("utf-8", errors="replace"))
        raise NativeExportError(f"GitHub gist upload failed: {detail}") from exc
    except urllib.error.URLError as exc:
        raise NativeExportError(f"GitHub gist upload failed: {exc.reason}") from exc
    except ProviderCancelledError as exc:
        raise ShareCancelled("Share cancelled.") from exc
    if status < 200 or status >= 300:
        detail = _github_error_message(body_bytes.decode("utf-8", errors="replace"))
        raise NativeExportError(f"GitHub gist upload failed: {detail}") from None
    data = json.loads(body_bytes.decode("utf-8"))
    gist_id = str(data.get("id") or "")
    gist_url = str(data.get("html_url") or "")
    if not gist_id or not gist_url:
        raise NativeExportError("GitHub gist upload returned an incomplete response.")
    viewer_base = os.environ.get("PIPY_SHARE_VIEWER_URL", "").strip()
    viewer_url = f"{viewer_base.rstrip('/')}#{gist_id}" if viewer_base else None
    return ShareResult(gist_id=gist_id, gist_url=gist_url, viewer_url=viewer_url)


def share_session_html(
    tree: NativeSessionTree,
    *,
    system_prompt: str = "",
    tool_renderers: ToolRendererLookup | None = None,
) -> str:
    """The page ``/share`` uploads: the HTML export of ``tree``, with the
    session's system prompt and the renderers ``tool_renderers`` chooses,
    as Pi's ``/share`` uploads ``exportToHtml``'s page.

    The renderers are extension code: call it on the session's thread, not
    on the thread that uploads.
    """

    if tree.path is None:
        raise NativeExportError("Cannot share an in-memory session.")
    if not tree.get_entries():
        raise NativeExportError("Nothing to share yet.")
    return generate_session_html(
        session_export_payload(
            tree, system_prompt=system_prompt, tool_renderers=tool_renderers
        )
    )


def share_native_session(
    tree: NativeSessionTree,
    html: str,
    *,
    token: str,
    opener: Callable[[urllib.request.Request, float], Any] | None = None,
    cancelled: Callable[[], bool] | None = None,
    cancel_token: CancelToken | None = None,
) -> ShareResult:
    """Upload ``html`` (``share_session_html``'s page of ``tree``) as a
    secret gist. Runs no extension code."""

    if tree.path is None:
        raise NativeExportError("Cannot share an in-memory session.")
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", suffix=".html", prefix="pipy-share-", delete=False
    ) as handle:
        temp_path = Path(handle.name)
        handle.write(html)
    try:
        try:
            temp_path.chmod(stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass
        html = temp_path.read_text(encoding="utf-8")
        return create_secret_gist(
            html=html,
            filename=f"pipy-session-{tree.path.stem}.html",
            token=token,
            opener=opener,
            cancelled=cancelled,
            cancel_token=cancel_token,
        )
    finally:
        try:
            temp_path.unlink()
        except OSError:
            pass


def _github_error_message(text: str) -> str:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return _redact_text(text.strip() or "request failed")
    message = data.get("message") if isinstance(data, dict) else None
    return _redact_text(str(message or "request failed"))


def compare_versions(current: str, latest: str) -> int:
    """Return -1/0/1 for current older/equal/newer than latest."""

    cur = _version_parts(current)
    new = _version_parts(latest)
    width = max(len(cur), len(new))
    cur += (0,) * (width - len(cur))
    new += (0,) * (width - len(new))
    return (cur > new) - (cur < new)


def _version_parts(value: str) -> tuple[int, ...]:
    cleaned = value.strip().lstrip("v")
    parts: list[int] = []
    for raw in re.split(r"[.+-]", cleaned):
        if raw.isdigit():
            parts.append(int(raw))
        else:
            break
    return tuple(parts or [0])


def fetch_latest_pipy_version(
    *,
    env: Mapping[str, str] | None = None,
    opener: Callable[[urllib.request.Request, float], Any] | None = None,
    timeout: float = 3.0,
) -> str | None:
    source = env if env is not None else os.environ
    if source.get("PIPY_SKIP_VERSION_CHECK") or source.get("PIPY_OFFLINE"):
        return None
    package_name = self_update_package_name(env=source)
    if package_name is None:
        return None
    request = urllib.request.Request(
        f"https://pypi.org/pypi/{urllib.parse.quote(package_name, safe='')}/json",
        headers={"Accept": "application/json", "User-Agent": "pipy-version-check"},
    )
    try:
        response = (
            opener(request, timeout)
            if opener is not None
            else urllib.request.urlopen(request, timeout=timeout)
        )
        data = json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, json.JSONDecodeError):
        return None
    info = data.get("info") if isinstance(data, dict) else None
    version = info.get("version") if isinstance(info, dict) else None
    return str(version) if version else None


def detect_install_method(
    *,
    executable: str | None = None,
    prefix: str | None = None,
    env: Mapping[str, str] | None = None,
) -> str:
    source = env if env is not None else os.environ
    exe = Path(executable or source.get("_", "")).expanduser()
    prefix_text = prefix or source.get("VIRTUAL_ENV", "") or sys.prefix
    uv_dir = Path(
        source.get("UV_TOOL_DIR", Path.home() / ".local" / "share" / "uv" / "tools")
    )
    pipx_home = Path(source.get("PIPX_HOME", Path.home() / ".local" / "pipx"))
    try:
        if (
            exe
            and exe.is_absolute()
            and exe.resolve().is_relative_to(uv_dir.expanduser().resolve())
        ):
            return "uv-tool"
    except OSError:
        pass
    try:
        if (
            exe
            and exe.is_absolute()
            and exe.resolve().is_relative_to(pipx_home.expanduser().resolve())
        ):
            return "pipx"
    except OSError:
        pass
    lowered = str(prefix_text).lower()
    exe_text = str(exe).lower()
    if (Path.cwd() / "pyproject.toml").is_file() and not (
        "pipx" in lowered
        or "pipx" in exe_text
        or "uv/tools" in lowered
        or "uv\\tools" in lowered
    ):
        return "unknown"
    if "pipx" in lowered or "pipx" in exe_text:
        return "pipx"
    if "uv/tools" in lowered or "uv\\tools" in lowered:
        return "uv-tool"
    if "site-packages" in lowered or "python" in lowered:
        return "pip-user" if "--user" in source.get("PIPY_INSTALL_FLAGS", "") else "pip"
    return "unknown"


def self_update_package_name(*, env: Mapping[str, str] | None = None) -> str | None:
    source = env if env is not None else os.environ
    value = source.get(SELF_UPDATE_PACKAGE_ENV, "").strip()
    if not value:
        return None
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", value):
        return None
    return value


def self_update_plan(
    *,
    method: str | None = None,
    executable: str | None = None,
    force: bool = False,
    distribution_name: str | None = None,
    env: Mapping[str, str] | None = None,
) -> UpdatePlan:
    del force
    resolved_method = method or detect_install_method(executable=executable)
    exe = executable or "pipy"
    package_name = distribution_name or self_update_package_name(env=env)
    if package_name is None:
        return UpdatePlan(
            method=resolved_method,
            command=(),
            executable=exe,
            automatic=False,
            reason=(
                f"{SELF_UPDATE_PACKAGE_ENV} is not set; refusing to guess a "
                "published package name for self-update"
            ),
        )
    commands = {
        "uv-tool": ("uv", "tool", "upgrade", package_name),
        "pipx": ("pipx", "upgrade", package_name),
        "pip": (sys.executable, "-m", "pip", "install", "--upgrade", package_name),
        "pip-user": (
            sys.executable,
            "-m",
            "pip",
            "install",
            "--user",
            "--upgrade",
            package_name,
        ),
    }
    command = commands.get(resolved_method)
    if command is None:
        return UpdatePlan(
            method=resolved_method,
            command=(),
            executable=exe,
            automatic=False,
            reason="development or unknown install; update manually from the source used to install pipy",
        )
    return UpdatePlan(
        method=resolved_method,
        command=command,
        executable=exe,
        automatic=True,
    )


_HTML_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>pipy session export</title>
<style>
:root { color-scheme: light dark; --bg: #f7f7f4; --fg: #181a1f; --muted: #626875; --line: #d8dadd; --accent: #0b6bcb; }
@media (prefers-color-scheme: dark) { :root { --bg: #151617; --fg: #eceff3; --muted: #aab0ba; --line: #383c43; --accent: #6db1ff; } }
body { margin: 0; font: 14px/1.45 ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: var(--bg); color: var(--fg); }
main { max-width: 980px; margin: 0 auto; padding: 24px; }
h1 { font-size: 20px; margin: 0 0 4px; }
.meta { color: var(--muted); margin-bottom: 20px; }
.entry { border-top: 1px solid var(--line); padding: 14px 0; }
.role { font-weight: 700; color: var(--accent); margin-bottom: 6px; }
pre { white-space: pre-wrap; overflow-wrap: anywhere; margin: 0; font: 13px/1.45 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
.tool { color: var(--muted); }
.tool-rendered { background: #1f2023; color: #d7dae0; border-radius: 6px; padding: 10px 12px; font: 13px/1.45 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; white-space: pre-wrap; overflow-wrap: anywhere; }
.tool-rendered .tool-output { margin-top: 1.45em; }
.tool-rendered .expandable { cursor: pointer; }
.tool-rendered .expandable .output-full, .tool-rendered .expandable.expanded .output-preview { display: none; }
.tool-rendered .expandable.expanded .output-full { display: block; }
.tool-image { display: block; max-width: 100%; margin-top: 8px; border-radius: 6px; }
.nested-calls { margin-top: 8px; cursor: pointer; color: var(--muted); }
.nested-calls .output-full, .nested-calls.expanded .output-preview { display: none; }
.nested-calls.expanded .output-full { display: block; }
</style>
</head>
<body>
<main>
<h1>pipy session export</h1>
<div class="meta" id="meta">Loading...</div>
<section id="entries"></section>
</main>
<script id="pipy-session-data" type="application/pipy-session+base64">__PIPY_SESSION_DATA_BASE64__</script>
<script>
(function () {
  const raw = document.getElementById("pipy-session-data").textContent.trim();
  const data = JSON.parse(new TextDecoder().decode(Uint8Array.from(atob(raw), c => c.charCodeAt(0))));
  const entries = document.getElementById("entries");
  const meta = document.getElementById("meta");
  meta.textContent = `${data.header.id || "session"} · ${data.entries.length} entries · active ${data.leafId || "root"}`;
  function append(role, text, cls) {
    const wrap = document.createElement("article");
    wrap.className = "entry";
    const label = document.createElement("div");
    label.className = "role";
    label.textContent = role;
    const pre = document.createElement("pre");
    if (cls) pre.className = cls;
    pre.textContent = text || "";
    wrap.append(label, pre);
    entries.append(wrap);
    return wrap;
  }
  // Rows drawn ahead by the tool's renderers, by call id. The HTML in them
  // was escaped when it was made.
  const renderedTools = data.renderedTools || {};
  function rendered(id) {
    const rows = Object.prototype.hasOwnProperty.call(renderedTools, id) ? renderedTools[id] : null;
    return rows && typeof rows === "object" ? rows : null;
  }
  function part(cls, html) {
    const node = document.createElement("div");
    node.className = cls;
    node.innerHTML = html;
    return node;
  }
  // The images of a tool result, after its text or its drawn row.
  function appendImages(blocks) {
    const images = (Array.isArray(blocks) ? blocks : []).filter(block =>
      block && block.type === "image" && typeof block.data === "string" &&
      /^image\\/[A-Za-z0-9.+-]+$/.test(block.mimeType || "") && /^[A-Za-z0-9+\\/=\\s]+$/.test(block.data));
    if (!images.length) return;
    const wrap = document.createElement("article");
    wrap.className = "entry";
    const label = document.createElement("div");
    label.className = "role";
    label.textContent = "Tool result images";
    wrap.append(label);
    for (const block of images) {
      const image = document.createElement("img");
      image.className = "tool-image";
      image.alt = block.mimeType;
      image.src = `data:${block.mimeType};base64,${block.data}`;
      wrap.append(image);
    }
    entries.append(wrap);
  }
  // The calls a tool made to other tools (a codemode script's), from the
  // nestedCalls record of its result, without their results (Pi
  // renderNestedCalls): the count, and one line per call on a click.
  function nestedCallsText(details) {
    const record = details && typeof details === "object" ? details.nestedCalls : null;
    // An imported file may hold anything: a call without a string name and
    // status is left out, and only plain values are shown.
    const calls = record && Array.isArray(record.calls)
      ? record.calls.filter(call => call && typeof call === "object" &&
        typeof call.name === "string" && typeof call.status === "string") : [];
    if (!calls.length) return "";
    const icons = new Map([["ok", "\u2713"], ["error", "\u2717"], ["unfinished", "\u2026"], ["cancelled", "\u2298"]]);
    // The start of the arguments the record left out, by call id (Pi's
    // codemode row shows them from details.calls).
    const previews = details.argumentPreviews && typeof details.argumentPreviews === "object"
      ? details.argumentPreviews : {};
    const lines = calls.map(call => {
      const bytes = typeof call.argumentsBytes === "number" ? call.argumentsBytes : "?";
      const preview = typeof call.id === "string" &&
        Object.prototype.hasOwnProperty.call(previews, call.id) &&
        typeof previews[call.id] === "string" ? `${previews[call.id].slice(0, 200)} ` : "";
      const args = call.arguments !== undefined
        ? JSON.stringify(call.arguments)
        : `${preview}[arguments omitted, ${bytes} bytes]`;
      const duration = typeof call.durationMs === "number" ? ` ${Math.round(call.durationMs)}ms` : "";
      const error = typeof call.error === "string" && call.error
        ? `\\n    ${call.error.split("\\n").join("\\n    ")}` : "";
      return `${icons.get(call.status) || "?"} ${call.name} ${args}${duration}${error}`;
    });
    const title = `Nested calls: ${calls.length}${record.complete ? "" : " (incomplete record)"}`;
    return [title, ...lines].join("\\n");
  }
  function appendNestedCalls(target, details) {
    const text = nestedCallsText(details);
    if (!text) return;
    const lines = text.split("\\n");
    const output = document.createElement("div");
    output.className = "nested-calls";
    const preview = document.createElement("pre");
    preview.className = "output-preview";
    preview.textContent = `${lines[0]}\\n... (${lines.length - 1} more lines)`;
    const full = document.createElement("pre");
    full.className = "output-full";
    full.textContent = text;
    output.append(preview, full);
    output.addEventListener("click", function () {
      if (!window.getSelection().toString()) output.classList.toggle("expanded");
    });
    target.append(output);
  }
  // The box of each call drawn ahead, by call id, for its nested calls.
  const renderedBoxes = new Map();
  function appendRenderedTool(rows, id) {
    const wrap = document.createElement("article");
    wrap.className = "entry";
    const label = document.createElement("div");
    label.className = "role";
    label.textContent = "Tool call";
    const box = part("tool-rendered", rows.callHtml || "");
    if (rows.resultHtmlCollapsed && rows.resultHtmlExpanded) {
      const output = document.createElement("div");
      output.className = "tool-output expandable";
      output.append(part("output-preview", rows.resultHtmlCollapsed), part("output-full", rows.resultHtmlExpanded));
      output.addEventListener("click", function () {
        if (!window.getSelection().toString()) output.classList.toggle("expanded");
      });
      box.append(output);
    } else if (rows.resultHtmlExpanded) box.append(part("tool-output", rows.resultHtmlExpanded));
    wrap.append(label, box);
    entries.append(wrap);
    renderedBoxes.set(id, box);
  }
  for (const entry of data.entries || []) {
    if (entry.type === "message") {
      const msg = entry.message || {};
      if (msg.role === "user") append("User", msg.content || "");
      else if (msg.role === "assistant") {
        const calls = msg.tool_calls || [];
        const plain = calls.filter(call => !rendered(call.provider_correlation_id));
        if (msg.content) append("Assistant", msg.content);
        else if (plain.length || !calls.length) append("Assistant", JSON.stringify(plain, null, 2));
        for (const call of calls) {
          const rows = rendered(call.provider_correlation_id);
          if (rows) appendRenderedTool(rows, call.provider_correlation_id);
        }
      }
      else if (msg.role === "tool") {
        // A result drawn ahead is shown in its call's box.
        const id = msg.provider_correlation_id;
        const rows = rendered(id);
        const box = rows && "resultHtmlExpanded" in rows ? renderedBoxes.get(id) : undefined;
        const target = box || append("Tool result", msg.output_text || "", "tool");
        appendNestedCalls(target, msg.details);
        appendImages(msg.blocks);
      }
      else append("Message", JSON.stringify(msg, null, 2));
    } else if (entry.type === "session_info") append("Session", entry.name || "");
    else if (entry.type === "compaction") append("Compaction", entry.summary || "");
    else if (entry.type === "branch_summary") append("Branch summary", entry.summary || "");
    else if (entry.type === "custom_message") append(entry.customType || "Custom", entry.content || "");
    else append(entry.type || "Entry", JSON.stringify(entry, null, 2), "tool");
  }
}());
</script>
</body>
</html>
"""
