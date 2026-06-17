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
from typing import Any

from pipy_harness.native.session_tree import (
    NativeSessionTree,
    _entry_to_json,
    _load_file_entries,
)
from pipy_harness.native._provider_helpers import urlopen_read_cancellable
from pipy_harness.native.cancellation import CancelToken, ProviderCancelledError

_SENSITIVE_KEY_RE = r"api[_-]?key|apikey|secret|token|password|credential"
_JSON_SECRET_FIELD_RE = re.compile(
    rf"(?i)(?P<prefix>(?P<quote>['\"])(?:{_SENSITIVE_KEY_RE})(?P=quote)\s*:\s*)"
    r"(?P<value>\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|[^\s,}\]]+)"
)
_SECRET_ASSIGN_RE = re.compile(
    rf"(?i)\b({_SENSITIVE_KEY_RE})\b"
    r"\s*([:=])\s*(?:\"[^\"]*\"|'[^']*'|[^\s<>'\"]+)"
)
_BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}")
_COMMON_TOKEN_RE = re.compile(
    r"\b(?:sk-[A-Za-z0-9._-]{8,}|ghp_[A-Za-z0-9._-]{8,}|"
    r"github_pat_[A-Za-z0-9._-]{8,}|ya29\.[A-Za-z0-9._-]{8,}|"
    r"xox[bp]-[A-Za-z0-9._-]{8,})\b"
)
SELF_UPDATE_PACKAGE_ENV = "PIPY_SELF_UPDATE_PACKAGE"


class NativeExportError(RuntimeError):
    """Raised for user-facing native export/import/share failures."""


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
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if _key_is_sensitive(key_text):
                redacted[key_text] = "[REDACTED]"
            else:
                redacted[key_text] = redact_export_value(item)
        return redacted
    if isinstance(value, list):
        return [redact_export_value(item) for item in value]
    if isinstance(value, tuple):
        return [redact_export_value(item) for item in value]
    if isinstance(value, str):
        return _redact_text(value)
    return value


def _key_is_sensitive(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    return any(
        marker in normalized
        for marker in ("api_key", "apikey", "secret", "token", "password", "credential")
    )


def _redact_text(value: str) -> str:
    value = _JSON_SECRET_FIELD_RE.sub(_redact_json_secret_field, value)
    value = _SECRET_ASSIGN_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}[REDACTED]", value)
    value = _BEARER_RE.sub("Bearer [REDACTED]", value)
    return _COMMON_TOKEN_RE.sub("[REDACTED]", value)


def _redact_json_secret_field(match: re.Match[str]) -> str:
    raw_value = match.group("value")
    quote = raw_value[0] if raw_value[:1] in {"'", '"'} else ""
    redacted = f"{quote}[REDACTED]{quote}" if quote else "[REDACTED]"
    return f"{match.group('prefix')}{redacted}"


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
    tree: NativeSessionTree, *, system_prompt: str = "", tools: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    payload = {
        "header": tree.get_header().to_json_dict(),
        "entries": [_entry_to_json(entry) for entry in tree.get_entries()],
        "leafId": tree.get_leaf_id(),
        "systemPrompt": system_prompt,
        "tools": tools or [],
    }
    return redact_export_value(payload)


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
) -> Path:
    if tree.path is None:
        raise NativeExportError("Cannot export in-memory session to HTML.")
    if not tree.get_entries():
        raise NativeExportError("Nothing to export yet.")
    output = Path(output_path).expanduser()
    output.parent.mkdir(parents=True, exist_ok=True)
    html = generate_session_html(
        session_export_payload(tree, system_prompt=system_prompt, tools=tools)
    )
    output.write_text(html, encoding="utf-8")
    return output


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
    output.parent.mkdir(parents=True, exist_ok=True)
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
    lines.extend(json.dumps(entry, sort_keys=True) for entry in _linearized_entry_dicts(tree))
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
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
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(generate_session_html(session_export_payload(tree)), encoding="utf-8")
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
    if header.cwd and not cwd.exists() and replacement_cwd is None:
        raise NativeExportError(f"imported session cwd does not exist: {header.cwd}")
    if replacement_cwd is not None and not replacement_cwd.is_dir():
        raise NativeExportError(f"replacement cwd is not a directory: {replacement_cwd}")
    destination_dir = Path(session_dir).expanduser()
    destination_dir.mkdir(parents=True, exist_ok=True)
    try:
        destination_dir.chmod(0o700)
    except OSError:
        pass
    destination = _unique_import_destination(destination_dir / source.name)
    shutil.copyfile(source, destination)
    if header.cwd and not cwd.exists() and replacement_cwd is not None:
        _rewrite_session_header_cwd(destination, replacement_cwd)
    try:
        destination.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass
    imported = NativeSessionTree.open(destination)
    if entries:
        imported.set_leaf(entries[-1].id)
    return imported


def _unique_import_destination(path: Path) -> Path:
    if not path.exists():
        return path
    for index in range(1, 10_000):
        candidate = path.with_name(f"{path.stem}-{index}{path.suffix}")
        if not candidate.exists():
            return candidate
    raise NativeExportError(f"could not choose a unique import path for {path}")


def _rewrite_session_header_cwd(path: Path, cwd: Path) -> None:
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines:
        return
    header = json.loads(lines[0])
    if isinstance(header, dict) and header.get("type") == "session":
        header["cwd"] = str(cwd)
        lines[0] = json.dumps(header, sort_keys=True)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")


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


def share_native_session(
    tree: NativeSessionTree,
    *,
    token: str,
    opener: Callable[[urllib.request.Request, float], Any] | None = None,
    cancelled: Callable[[], bool] | None = None,
    cancel_token: CancelToken | None = None,
) -> ShareResult:
    if tree.path is None:
        raise NativeExportError("Cannot share an in-memory session.")
    if not tree.get_entries():
        raise NativeExportError("Nothing to share yet.")
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", suffix=".html", prefix="pipy-share-", delete=False
    ) as handle:
        temp_path = Path(handle.name)
        handle.write(generate_session_html(session_export_payload(tree)))
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
    *, executable: str | None = None, prefix: str | None = None, env: Mapping[str, str] | None = None
) -> str:
    source = env if env is not None else os.environ
    exe = Path(executable or source.get("_", "")).expanduser()
    prefix_text = prefix or source.get("VIRTUAL_ENV", "") or sys.prefix
    uv_dir = Path(source.get("UV_TOOL_DIR", Path.home() / ".local" / "share" / "uv" / "tools"))
    pipx_home = Path(source.get("PIPX_HOME", Path.home() / ".local" / "pipx"))
    try:
        if exe and exe.is_absolute() and exe.resolve().is_relative_to(uv_dir.expanduser().resolve()):
            return "uv-tool"
    except OSError:
        pass
    try:
        if exe and exe.is_absolute() and exe.resolve().is_relative_to(pipx_home.expanduser().resolve()):
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
  }
  for (const entry of data.entries || []) {
    if (entry.type === "message") {
      const msg = entry.message || {};
      if (msg.role === "user") append("User", msg.content || "");
      else if (msg.role === "assistant") append("Assistant", msg.content || JSON.stringify(msg.tool_calls || [], null, 2));
      else if (msg.role === "tool") append("Tool result", msg.output_text || "", "tool");
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
