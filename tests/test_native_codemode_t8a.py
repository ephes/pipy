"""CM1 T8a definition, management and host-only full-output delivery."""

from __future__ import annotations

import stat
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from pipy_harness import cli
from pipy_harness.native.codemode import cli as management
from pipy_harness.native.codemode.outcome import ErrorKind, ScriptError, ScriptOutcome
from pipy_harness.native.codemode.result import format_result
from pipy_harness.native.codemode.selftest import Availability
from pipy_harness.native.coding import codemode_runner
from pipy_harness.native.tools.base import (
    ToolContext,
    ToolRequest,
    make_tool_request_id,
)
from pipy_harness.native.tools.codemode import CodemodeTool
from pipy_harness.native.tools.registry import production_tool_registry


def test_definition_literal_contract_and_fallback(tmp_path: Path) -> None:
    definition = CodemodeTool().definition
    assert definition.name == "codemode"
    # Byte-pinned: the registered description names no tool, so it is the same
    # whatever is registered (MCP parity plan CMD-18, CMD-21).
    assert definition.description == (
        "Run a Python script that calls other tools. The script runs in an isolated "
        "Python 3.14 interpreter with no file system, network or process access of "
        "its own. All effects go through `tools.<name>(args)`, which runs the named "
        "tool exactly as a direct call would, with the same JSON arguments.\n"
        "- `tools.<name>(args)` returns a string, or a structured value if the "
        "tool's declaration says so, and raises `ToolError` on failure. A tool whose "
        "declaration returns a structured value returns it on failure too when the "
        "failed result carries one; check its fields. `tools.<name>.submit(args)` "
        "starts the call and returns at once; its `.result()` returns or raises as "
        "the call would, so calls submitted together run at the same time. Calls "
        "still running when the script ends are cancelled.\n"
        '- Optional first line: `// @options: {"max_output_tokens": 10000, '
        '"timeout_ms": 60000}`\n'
        "- Unavailable modules: subprocess, socket networking, ssl, threading, "
        "ctypes, zlib/gzip/bz2/lzma, sqlite3. Limits: 256 MiB memory, about 10k "
        "tokens of output; no wall time limit unless `timeout_ms` sets one. Tool "
        "calls already made are not undone when the script fails.\n"
        "\n"
        "Globals:\n"
        "- `text(value)` emits output: strings as-is, anything else as JSON. "
        "`print()` output is included as well. With several `text()` items, each "
        "starts with a `==> text N/M <==` line, and `print()` output follows the "
        "other output in one `<console_output>` block.\n"
        "- `image(value)` adds an image to the output: a base64 `data:` URL, a dict "
        'with `image_url`, or an image block `{"type": "image", "data", '
        '"mimeType"}` as MCP tools return it. PNG, JPEG, GIF and WebP are accepted; '
        "remote URLs are not. The image is also saved to a temp file and the result "
        "names its path.\n"
        "- `store(key, value)` and `load(key)` keep JSON values across codemode "
        "calls; `store(key, None)` deletes a key.\n"
        "- `ALL_TOOLS`, `search_tools(query, limit=8, namespace=None)`, "
        "`describe_tool(name)`, `describe_namespace(name)`: find unlisted tools, "
        "such as MCP tools.\n"
        "\n"
        "A string result is the tool's text, not a result object. For a text file, "
        "tools.read returns file text as str (possibly with paging/truncation "
        "notices); use splitlines() to process lines. JSON-decode only content that "
        "is actually JSON. Follow read's offset/limit continuation notices when the "
        "file is incomplete. For read/filter tasks, keep intermediate results in "
        "Python, filter there, and text() only the final compact answer. Do not "
        "print types, dump raw reads, or deliberately raise with their contents just "
        "to probe this documented format. Combine the required reads and processing "
        "in one script when practical. Example for two small text files:\n"
        "\n"
        "```python\n"
        "matches = []\n"
        'for path in ("alpha.txt", "beta.txt"):\n'
        '    content = tools.read({"path": path})\n'
        "    for line in content.splitlines():\n"
        '        if line.startswith("KEEP "):\n'
        '            matches.append({"file": path, "value": line[5:]})\n'
        'text({"matches": matches, "count": len(matches)})\n'
        "```"
    )
    assert definition.input_schema == {
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "Python source run as a script."}
        },
        "required": ["code"],
        "additionalProperties": False,
    }
    request = ToolRequest(
        make_tool_request_id(), "codemode", {"code": "raise RuntimeError()"}, "parent"
    )
    result = CodemodeTool().invoke(request, ToolContext(workspace_root=tmp_path))
    assert result.is_error and "canonical composite service" in result.output_text
    assert result.tool_request_id == request.tool_request_id
    assert result.provider_correlation_id == "parent"
    assert "codemode" not in production_tool_registry()


@pytest.mark.parametrize("error", [None, *ErrorKind])
def test_full_body_exact_owner_only_and_error_retained(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: ErrorKind | None
) -> None:
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    formatted = format_result(
        ScriptOutcome(
            ScriptError(error, "primary error") if error else None,
            output=("é🙂" * 25_000, "last"),
        )
    )
    text, details = codemode_runner._spill_result(formatted)
    path = Path(str(details["fullOutputPath"]))
    assert path.read_bytes() == formatted.full_output.encode()  # type: ignore[union-attr]
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert str(path) in text and text.startswith(formatted.text)
    assert len(text) < 41_000
    assert details["truncation"]["totalBytes"] == len(path.read_bytes())  # type: ignore[index]
    if error:
        assert "primary error" in text


@pytest.mark.parametrize("error", [False, True])
@pytest.mark.parametrize("stage", ["open", "write", "short_write", "close"])
def test_spill_failure_is_honest_and_preserves_formatted_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage: str, error: bool
) -> None:
    original = tempfile.NamedTemporaryFile
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))

    class FailingFile:
        def __init__(self, **kwargs):
            if stage == "open":
                raise OSError("open failed")
            self.handle = original(**kwargs)
            self.name = self.handle.name

        def __enter__(self):
            return self

        def write(self, data):
            if stage == "write":
                self.handle.write(data[:10])
                raise OSError("write failed")
            if stage == "short_write":
                return self.handle.write(data[:10])
            return self.handle.write(data)

        def __exit__(self, *args):
            self.handle.close()
            if stage == "close":
                raise OSError("close failed")

    monkeypatch.setattr(tempfile, "NamedTemporaryFile", FailingFile)
    formatted = format_result(
        ScriptOutcome(
            ScriptError(ErrorKind.SCRIPT, "primary error") if error else None,
            output=("x" * 50_000,),
        )
    )
    text, details = codemode_runner._spill_result(formatted)
    assert text.startswith(formatted.text)
    assert formatted.is_error is error
    if error:
        assert "primary error" in text
    assert "could not be saved" in text
    assert "fullOutputPath" not in details and not list(tmp_path.iterdir())


def test_root_status_never_installs_or_starts_provider(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def forbidden(*args, **kwargs):
        pytest.fail("status must not install or start product services")

    monkeypatch.setattr(management, "install_runtime", forbidden)
    monkeypatch.setattr(cli, "_build_catalog_state", forbidden)
    monkeypatch.setattr(cli, "_resolve_runtime_project_trust_startup", forbidden)
    monkeypatch.setattr(
        management,
        "availability",
        lambda paths: Availability(False, reason="missing runtime"),
    )
    assert cli.route_argv(["codemode", "status"], cli.KNOWN_SUBCOMMANDS) == [
        "codemode",
        "status",
    ]
    assert cli.main(["codemode", "status"]) == 1
    assert "missing runtime" in capsys.readouterr().err
    monkeypatch.setattr(
        management,
        "availability",
        lambda paths: Availability(True, wasmtime_version="49.0.0", cache="hit"),
    )
    assert cli.main(["codemode", "status"]) == 0


def test_root_install_forwarding_and_developer_entrypoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []

    def install(paths, *, source, force):
        calls.append((source, force))
        return SimpleNamespace(status="installed", runtime_dir=Path("runtime"))

    monkeypatch.setattr(management, "install_runtime", install)
    monkeypatch.setattr(management, "availability", lambda paths: Availability(True))
    assert (
        cli.main(["codemode", "install", "--from-file", "archive.zip", "--force"]) == 0
    )
    assert management.main(["install"]) == 0
    assert calls == [(Path("archive.zip"), True), (None, False)]

    def failed(*args, **kwargs):
        raise management.CodemodeRuntimeError("bad archive")

    monkeypatch.setattr(management, "install_runtime", failed)
    assert cli.main(["codemode", "install"]) == 1


@pytest.mark.parametrize(
    "args",
    [
        ["codemode"],
        ["codemode", "unknown"],
        ["codemode", "status", "--force"],
        ["codemode", "install", "--from-file"],
    ],
)
def test_root_parser_errors(args: list[str]) -> None:
    with pytest.raises(SystemExit) as error:
        cli.main(args)
    assert error.value.code == 2


def test_root_help_exposes_management(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as error:
        cli.main(["--help"])
    assert error.value.code == 0
    assert "codemode" in capsys.readouterr().out


def test_actual_missing_runtime_status_never_fetches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pipy_harness.native.codemode import runtime

    def forbidden(*args, **kwargs):
        pytest.fail("status fetched the runtime")

    monkeypatch.setattr(
        management, "default_paths", lambda: runtime.CodemodePaths(tmp_path)
    )
    monkeypatch.setattr(runtime, "_default_fetch", forbidden)
    assert cli.main(["codemode", "status"]) == 1
    assert not list(tmp_path.iterdir())


def test_injected_definition_identity_guard_excludes_overrides(tmp_path: Path) -> None:
    from pipy_harness.native.tool_capabilities import (
        NativeToolCapabilities,
        ToolFilterOptions,
    )

    builtin = CodemodeTool()
    capabilities = NativeToolCapabilities(
        {"codemode": builtin},
        {},
        workspace_root=tmp_path,
        filter_options=ToolFilterOptions.empty(),
        cancel_join_timeout_seconds=0.05,
    )
    frozen = capabilities.snapshot_for_projection(capabilities.state)
    assert frozen.composite_enabled()
    capabilities.publish(capabilities.prepare_extensions({"codemode": CodemodeTool()}))
    replaced = capabilities.snapshot_for_projection(capabilities.state)
    assert not replaced.composite_enabled()
    assert "codemode" not in replaced.callable_names()
    assert frozen.composite_enabled()
