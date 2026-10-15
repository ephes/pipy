"""A full TUI session with an MCP server that never finishes connecting, or
with one that wants an OAuth sign-in.

Run on a tty by ``test_native_mcp_readiness_pty.py``. Prompts and Escape are
read by the session's normal input owner; the provider is the offline fake.

``--phase prompt``: the server has ``direct`` tools, so the first prompt waits
for it (with a limit far beyond the test's patience). ``--phase tool``: the
server has ``deferred`` tools and the provider's first turn calls
``tool_search``, which waits for it. ``--phase command``: the same server,
and no prompt is sent; the test types ``/mcp``.

``--phase manager``: a server with one ``direct`` tool that connects at
once; the test types ``/mcp`` and uses the manager.

``--phase rows``: a server with one ``direct`` tool that connects at once and
answers with eight lines; the provider's first turn calls the tool.
``--phase image``: the same, but the tool answers with a line of text and a
PNG image.

``--phase login`` and ``--phase login-cancel``: the server is the fake OAuth
MCP server, running in this process, and the test types ``/mcp login``. The
browser is a double: for ``login`` it asks the authorization URL and follows
the redirect to the loopback port, for ``login-cancel`` it does nothing, as
a user who never approves.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import struct
import sys
import webbrowser
import zlib
from pathlib import Path

from pipy_harness.native.coding.session import CodingSession
from pipy_harness.native.fake import FakeNativeProvider
from pipy_harness.native.mcp import readiness
from pipy_harness.native.models import ProviderToolCall
from pipy_harness.native.settings import SettingsManager

sys.path.insert(0, str(Path(__file__).with_name("fixtures")))

from mcp_fake_oauth import FakeBrowser, FakeOAuthMcpServer  # noqa: E402

_FAKE_SERVER = Path(__file__).with_name("fixtures") / "mcp_fake_server.py"
_PHASES = (
    "prompt",
    "tool",
    "command",
    "manager",
    "rows",
    "image",
    "login",
    "login-cancel",
)
_SEARCH_TOOL = {"name": "search", "inputSchema": {"type": "object"}}
_EIGHT_LINES = {
    "content": [{"type": "text", "text": "\n".join(f"hit {n}" for n in range(8))}]
}


def _png(width: int, height: int) -> str:
    """A complete PNG (a grey gradient) a terminal can display."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    header = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)
    pixels = b"".join(
        b"\x00" + bytes((x * 255 // width) for x in range(width)) for _ in range(height)
    )
    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(pixels))
        + chunk(b"IEND", b"")
    )
    return base64.b64encode(png).decode()


_PNG = _png(90, 36)
_SHOT = {
    "content": [
        {"type": "text", "text": "screenshot taken"},
        {"type": "image", "data": _PNG, "mimeType": "image/png"},
    ]
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--phase", choices=_PHASES, required=True)
    args = parser.parse_args()
    root = args.workspace.resolve()
    root.mkdir(parents=True)
    for name, leaf in (
        ("PIPY_CONFIG_HOME", "config"),
        ("PIPY_NATIVE_THEME_PATH", "theme.json"),
        ("PIPY_NATIVE_DEFAULTS_PATH", "defaults.json"),
        ("PIPY_AUTH_DIR", "auth"),
        ("PIPY_PROMPT_HISTORY_PATH", "prompt-history.json"),
        ("PIPY_NATIVE_SESSIONS_ROOT", "sessions"),
        ("XDG_STATE_HOME", "state"),
    ):
        os.environ[name] = str(root / leaf)
    os.environ["PIPY_OFFLINE"] = "1"
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        parser.error("a real tty is required")
    if args.phase.startswith("login"):
        return _login_session(root, follow=args.phase == "login")
    scenario = root / "scenario.json"
    scenario.write_text(
        json.dumps(
            {
                "tools": [_SEARCH_TOOL],
                "toolResults": {
                    "search": _SHOT if args.phase == "image" else _EIGHT_LINES
                },
            }
            if args.phase in ("manager", "rows", "image")
            else {
                "record": str(root / "record.jsonl"),
                "methods": {"initialize": [{"record": "initialize"}, {"sleep": 600}]},
                "onStdinClose": [],
            }
        )
    )
    config = root / "config"
    config.mkdir()
    (config / "mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "slow": {
                        "command": sys.executable,
                        "args": ["-I", str(_FAKE_SERVER), "--scenario", str(scenario)],
                        "exposure": "deferred"
                        if args.phase in ("tool", "command")
                        else "direct",
                    }
                }
            }
        )
    )
    # Escape, not the limit, has to end the first prompt's wait.
    readiness.STARTUP_WAIT_SECONDS = 600.0
    search = ProviderToolCall("search", "tool_search", '{"query": "docs"}')
    if args.phase in ("rows", "image"):
        search = ProviderToolCall("search", "mcp__slow__search", '{"query": "pi"}')
    CodingSession(
        provider=FakeNativeProvider(
            supports_tool_calls=True,
            final_text="S18_DONE",
            programmable_tool_calls=((search,), ())
            if args.phase in ("tool", "rows", "image")
            else (),
        ),
        settings_manager=SettingsManager(
            global_path=config / "settings.json",
            project_path=root / ".pipy" / "settings.json",
            overrides={
                "defaultTools": ["+tool_search"],
                "theme": "pi",
                "quietStartup": True,
            },
        ),
    ).run(
        workspace_root=root,
        input_stream=sys.stdin,
        output_stream=sys.stdout,
        error_stream=sys.stderr,
    )
    (root / "finished").write_text("ok")
    return 0


def _login_session(root: Path, *, follow: bool) -> int:
    """A session whose one server needs a sign-in the test makes with
    ``/mcp login``."""

    def browser(url: str, new: int = 0, autoraise: bool = True) -> bool:
        if follow:
            FakeBrowser().open(url)
        return True

    # No real browser: the session opens URLs with this instead.
    webbrowser.open = browser
    config = root / "config"
    config.mkdir()
    with FakeOAuthMcpServer() as server:
        (config / "mcp.json").write_text(
            json.dumps(
                {"mcpServers": {"issues": {"url": server.url, "exposure": "direct"}}}
            )
        )
        CodingSession(
            provider=FakeNativeProvider(supports_tool_calls=True, final_text="DONE"),
            settings_manager=SettingsManager(
                global_path=config / "settings.json",
                project_path=root / ".pipy" / "settings.json",
                overrides={"theme": "pi", "quietStartup": True},
            ),
        ).run(
            workspace_root=root,
            input_stream=sys.stdin,
            output_stream=sys.stdout,
            error_stream=sys.stderr,
        )
        (root / "log.json").write_text(json.dumps(server.log))
    (root / "finished").write_text("ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
