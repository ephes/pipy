"""A full TUI session with an MCP server that never finishes connecting.

Run on a tty by ``test_native_mcp_readiness_pty.py``. Prompts and Escape are
read by the session's normal input owner; the provider is the offline fake.

``--phase prompt``: the server has ``direct`` tools, so the first prompt waits
for it (with a limit far beyond the test's patience). ``--phase tool``: the
server has ``deferred`` tools and the provider's first turn calls
``tool_search``, which waits for it.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from pipy_harness.native.coding.session import CodingSession
from pipy_harness.native.fake import FakeNativeProvider
from pipy_harness.native.mcp import readiness
from pipy_harness.native.models import ProviderToolCall
from pipy_harness.native.settings import SettingsManager

_FAKE_SERVER = Path(__file__).with_name("fixtures") / "mcp_fake_server.py"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--phase", choices=("prompt", "tool"), required=True)
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
    scenario = root / "scenario.json"
    scenario.write_text(
        json.dumps(
            {
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
                        "exposure": "direct" if args.phase == "prompt" else "deferred",
                    }
                }
            }
        )
    )
    # Escape, not the limit, has to end the first prompt's wait.
    readiness.STARTUP_WAIT_SECONDS = 600.0
    search = ProviderToolCall("search", "tool_search", '{"query": "docs"}')
    CodingSession(
        provider=FakeNativeProvider(
            supports_tool_calls=True,
            final_text="S18_DONE",
            programmable_tool_calls=() if args.phase == "prompt" else ((search,), ()),
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


if __name__ == "__main__":
    raise SystemExit(main())
