"""Command-line interface for the pipy product harness."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pipy_harness.adapters import (
    PipyNativeAdapter,
    PipyNativeReplAdapter,
    PipyNativeToolReplAdapter,
    SubprocessAdapter,
)
from pipy_harness.capture import CapturePolicy
from pipy_harness.models import RunRequest, RunResult
from pipy_harness.native import (
    DEFAULT_NATIVE_MODELS,
    SUPPORTED_NATIVE_PROVIDERS,
    NativeDefaultsStore,
    NativeModelSelection,
    NativeReplProviderState,
    OpenAICodexAuthManager,
    OpenAICodexProviderError,
    OpenAICodexResponsesProvider,
    FakeNativeProvider,
    OpenAIResponsesProvider,
    OpenRouterChatCompletionsProvider,
    ProviderPort,
    ReplInputUnavailableError,
    SUPPORTED_REPL_INPUT_RUNTIMES,
    default_native_defaults_path,
    default_openai_codex_auth_path,
    default_selection_for,
    validate_native_repl_input_runtime,
)
from pipy_harness.native.provider import StreamChunkSink
from pipy_harness.native.workspace_context import default_workspace_instruction_loader
from pipy_harness.runner import HarnessRunner


STREAMING_NATIVE_PROVIDERS = frozenset({"openai-codex", "fake"})
"""Native providers that advertise streaming text deltas through
`ProviderPort.complete(..., stream_sink=...)`. `--stream` fails closed
with a stderr diagnostic when the active provider is not in this set."""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pipy",
        description="Run coding-agent tasks through the pipy harness.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    auth_parser = subparsers.add_parser("auth", help="Manage provider authentication.")
    auth_subparsers = auth_parser.add_subparsers(dest="auth_provider", required=True)
    openai_codex_auth = auth_subparsers.add_parser(
        "openai-codex",
        help="Manage OpenAI Codex subscription OAuth credentials.",
    )
    openai_codex_auth_subparsers = openai_codex_auth.add_subparsers(dest="auth_action", required=True)
    openai_codex_login = openai_codex_auth_subparsers.add_parser(
        "login",
        help="Run OpenAI Codex OAuth login and store pipy-owned credentials.",
    )
    openai_codex_login.add_argument(
        "--no-browser",
        action="store_true",
        help="Print the OAuth URL without attempting to open a browser.",
    )

    run_parser = subparsers.add_parser("run", help="Run one agent command with partial capture.")
    run_parser.add_argument("--agent", required=True, help="Logical agent name, for example codex.")
    run_parser.add_argument("--slug", required=True, help="Short run label for the session filename.")
    run_parser.add_argument(
        "--cwd",
        type=Path,
        default=Path.cwd(),
        help="Working directory for the child process. Defaults to the current directory.",
    )
    run_parser.add_argument("--goal", help="Optional short goal for the run record.")
    run_parser.add_argument(
        "--native-provider",
        choices=[
            "fake",
            "openai",
            "openai-completions",
            "openai-codex",
            "openrouter",
            "anthropic",
            "google",
            "google-vertex",
            "mistral",
            "amazon-bedrock",
            "azure-openai",
            "cloudflare",
        ],
        help=(
            "Native provider for --agent pipy-native. Defaults to the deterministic fake provider."
        ),
    )
    run_parser.add_argument(
        "--native-model",
        help=(
            "Native model identifier for --agent pipy-native. Defaults to fake-native-bootstrap "
            "for --native-provider fake and is required for real native providers."
        ),
    )
    run_parser.add_argument(
        "--native-output",
        choices=["json"],
        help="Native stdout mode for --agent pipy-native. Only json is supported.",
    )
    run_parser.add_argument(
        "--stream",
        action="store_true",
        help=(
            "Stream provider-emitted assistant text deltas to stdout in plain "
            "text mode or stderr in --native-output json mode. Requires "
            "--agent pipy-native and a streaming-capable native provider "
            "(openai-codex or fake). Off by default; the final buffered "
            "result is still emitted on completion."
        ),
    )
    run_parser.add_argument(
        "--record-files",
        action="store_true",
        help="Record changed git file paths only, not diffs or contents.",
    )
    run_parser.add_argument(
        "--root",
        type=Path,
        help="Session root. Defaults to PIPY_SESSION_DIR or ~/.local/state/pipy/sessions.",
    )
    run_parser.add_argument("native_command", nargs=argparse.REMAINDER, help="Command to run after --.")

    repl_parser = subparsers.add_parser(
        "repl",
        help="Run the bounded pipy-native REPL.",
    )
    repl_parser.add_argument(
        "--agent",
        default="pipy-native",
        help="Logical agent name. Only pipy-native is supported. Defaults to pipy-native.",
    )
    repl_parser.add_argument(
        "--slug",
        default="native-repl",
        help="Short run label for the session filename. Defaults to native-repl.",
    )
    repl_parser.add_argument(
        "--cwd",
        type=Path,
        default=Path.cwd(),
        help="Working directory for the native provider. Defaults to the current directory.",
    )
    repl_parser.add_argument(
        "--goal",
        help="Optional short goal for the REPL run record. Conversation turns are not archived.",
    )
    repl_parser.add_argument(
        "--native-provider",
        choices=[
            "fake",
            "openai",
            "openai-completions",
            "openai-codex",
            "openrouter",
            "anthropic",
            "google",
            "google-vertex",
            "mistral",
            "amazon-bedrock",
            "azure-openai",
            "cloudflare",
        ],
        help=(
            "Native provider for --agent pipy-native. Defaults to the deterministic fake provider."
        ),
    )
    repl_parser.add_argument(
        "--native-model",
        help=(
            "Native model identifier for --agent pipy-native. Defaults to fake-native-bootstrap "
            "for --native-provider fake and is required for real native providers."
        ),
    )
    repl_parser.add_argument(
        "--root",
        type=Path,
        help="Session root. Defaults to PIPY_SESSION_DIR or ~/.local/state/pipy/sessions.",
    )
    repl_parser.add_argument(
        "--input-runtime",
        choices=SUPPORTED_REPL_INPUT_RUNTIMES,
        default="auto",
        help=(
            "Native REPL input runtime. auto prefers prompt-toolkit when it is "
            "available on real TTY streams, then falls back to the stdlib "
            "readline editor (Tab completion, no external deps), and finally "
            "to plain stdin/stderr."
        ),
    )
    repl_parser.add_argument(
        "--repl-mode",
        choices=["auto", "no-tool", "tool-loop"],
        default="auto",
        help=(
            "Native REPL mode. auto (the default) launches the bounded "
            "model-driven tool loop when the selected provider advertises "
            "supports_tool_calls=True and falls back to the existing "
            "line-oriented REPL otherwise. no-tool forces the existing REPL "
            "with /read, /ask-file, /propose-file, /apply-proposal, and "
            "/verify just-check. tool-loop forces the bounded tool loop and "
            "errors out when the provider is not tool-capable."
        ),
    )
    repl_parser.add_argument(
        "--tool-budget",
        type=int,
        default=10,
        help=(
            "Per-user-turn tool invocation budget for --repl-mode tool-loop. "
            "Default 10, capped at 25."
        ),
    )
    repl_parser.add_argument(
        "--archive-transcript",
        action="store_true",
        help=(
            "Write raw loop turns to "
            "~/.local/state/pipy/transcripts/<id>.jsonl as an opt-in sidecar. "
            "The sidecar is sensitive content and is excluded from "
            "pipy-session list/search/inspect. Off by default."
        ),
    )
    repl_parser.add_argument(
        "--read-root",
        action="append",
        default=[],
        metavar="PATH",
        help=(
            "Additional read-only directory the model-driven read/ls/grep/find "
            "tools may resolve absolute paths against. May be repeated. The "
            "PIPY_READ_ROOTS environment variable (':'-separated) acts as the "
            "default. Mutation tools always stay inside the workspace."
        ),
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    raw_argv = sys.argv[1:] if argv is None else argv
    if not raw_argv:
        raw_argv = ["repl"]
    args = parser.parse_args(raw_argv)

    try:
        if args.command == "auth":
            if args.auth_provider == "openai-codex" and args.auth_action == "login":
                OpenAICodexAuthManager().login_interactive(
                    input_stream=sys.stdin,
                    output_stream=sys.stderr,
                    open_browser=not args.no_browser,
                )
                print(
                    "pipy: openai-codex OAuth login stored.",
                    file=sys.stderr,
                )
                return 0
        if args.command == "run":
            _validate_native_output(args.agent, args.native_output)
            _validate_stream(
                args.agent,
                args.stream,
                native_provider=args.native_provider,
            )
            command = _native_command(args.native_command, agent=args.agent)
            if args.agent == "pipy-native" and not args.goal:
                raise ValueError("pipy-native runs require --goal")
            stream_sink: StreamChunkSink | None = None
            if args.stream:
                stream_sink = _build_stream_sink(
                    native_output=args.native_output
                )
            adapter = _adapter_for(
                args.agent,
                args.native_provider,
                args.native_model,
                stream_sink=stream_sink,
            )
            request = RunRequest(
                agent=args.agent,
                slug=args.slug,
                command=command,
                cwd=args.cwd,
                goal=args.goal,
                root=args.root,
                capture_policy=CapturePolicy(record_file_paths=args.record_files),
                native_provider=args.native_provider,
                native_model=args.native_model,
                native_output=args.native_output,
            )
            result = HarnessRunner(adapter=adapter).run(request)
            if result.error_type is not None:
                detail = f": {result.error_message}" if result.error_message else ""
                print(
                    f"pipy: run ended with {result.error_type}{detail}; session finalized at "
                    f"{result.record.jsonl_path}",
                    file=sys.stderr,
                )
            else:
                print(
                    f"pipy: session finalized at {result.record.jsonl_path}",
                    file=sys.stderr,
                )
            if args.native_output == "json":
                print(json.dumps(_native_json_output(request, result), sort_keys=True))
            return result.exit_code
        if args.command == "repl":
            if args.agent != "pipy-native":
                raise ValueError("pipy repl currently requires --agent pipy-native")
            validate_native_repl_input_runtime(
                input_stream=sys.stdin,
                error_stream=sys.stderr,
                input_runtime=args.input_runtime,
                workspace=args.cwd,
            )
            repl_adapter: PipyNativeReplAdapter | PipyNativeToolReplAdapter
            resolved_repl_mode = _resolve_repl_mode(
                args.repl_mode,
                native_provider=args.native_provider,
                native_model=args.native_model,
            )
            if resolved_repl_mode == "tool-loop":
                if args.tool_budget < 1 or args.tool_budget > 25:
                    raise ValueError(
                        "--tool-budget must be in [1, 25]; got "
                        f"{args.tool_budget}"
                    )
                reference_roots = _resolve_reference_roots(
                    args.read_root,
                    cwd=args.cwd.expanduser().resolve(),
                )
                repl_adapter = _tool_repl_adapter_for(
                    args.native_provider,
                    args.native_model,
                    tool_budget=args.tool_budget,
                    archive_transcript=args.archive_transcript,
                    input_runtime=args.input_runtime,
                    reference_roots=reference_roots,
                )
            else:
                repl_adapter = _repl_adapter_for(
                    args.native_provider,
                    args.native_model,
                    input_runtime=args.input_runtime,
                )
            request = RunRequest(
                agent=args.agent,
                slug=args.slug,
                command=[],
                cwd=args.cwd,
                goal=args.goal or "Native REPL",
                root=args.root,
                capture_policy=CapturePolicy(),
                native_provider=args.native_provider,
                native_model=args.native_model,
            )
            result = HarnessRunner(adapter=repl_adapter).run(request)
            if result.error_type is not None:
                detail = f": {result.error_message}" if result.error_message else ""
                print(
                    f"pipy: repl ended with {result.error_type}{detail}; session finalized at "
                    f"{result.record.jsonl_path}",
                    file=sys.stderr,
                )
            else:
                print(
                    f"pipy: session finalized at {result.record.jsonl_path}",
                    file=sys.stderr,
                )
            return result.exit_code
    except ValueError as exc:
        print(f"pipy: {exc}", file=sys.stderr)
        return 2
    except ReplInputUnavailableError as exc:
        print(f"pipy: {exc}", file=sys.stderr)
        return 2
    except OpenAICodexProviderError as exc:
        print(
            f"pipy: openai-codex auth failed with {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 1
    except OSError as exc:
        print(f"pipy: {exc}", file=sys.stderr)
        return 1

    parser.error("unknown command")
    return 2


def _validate_native_output(agent: str, native_output: str | None) -> None:
    if native_output is not None and agent != "pipy-native":
        raise ValueError("--native-output requires --agent pipy-native")


def _validate_stream(
    agent: str,
    stream: bool,
    *,
    native_provider: str | None,
) -> None:
    if not stream:
        return
    if agent != "pipy-native":
        raise ValueError("--stream requires --agent pipy-native")
    selected = native_provider or "fake"
    if selected not in STREAMING_NATIVE_PROVIDERS:
        raise ValueError(
            "--stream requires a streaming-capable native provider "
            f"(one of: {', '.join(sorted(STREAMING_NATIVE_PROVIDERS))}); "
            f"got {selected}"
        )


def _build_stream_sink(*, native_output: str | None) -> StreamChunkSink:
    target = sys.stderr if native_output == "json" else sys.stdout

    def _sink(chunk: str) -> None:
        target.write(chunk)
        target.flush()

    return _sink


def _native_command(command: list[str], *, agent: str) -> list[str]:
    if command and command[0] == "--":
        command = command[1:]
    if agent == "pipy-native":
        if command:
            raise ValueError("pipy-native runs do not accept a command after --")
        return []
    if not command:
        raise ValueError("run requires a command after --")
    return command


def _native_json_output(request: RunRequest, result: RunResult) -> dict[str, Any]:
    metadata = result.metadata or {}
    output: dict[str, Any] = {
        "schema": "pipy.native_output",
        "schema_version": 1,
        "run_id": result.run_id,
        "status": result.status.value,
        "exit_code": result.exit_code,
        "agent": request.agent,
        "adapter": _metadata_text(metadata, "adapter") or "pipy-native",
        "record": {
            "jsonl_path": str(result.record.jsonl_path),
            "markdown_path": str(result.record.markdown_path)
            if result.record.markdown_path is not None
            else None,
        },
        "capture": {
            "partial": True,
            "stdout_stored": request.capture_policy.record_stdout,
            "stderr_stored": request.capture_policy.record_stderr,
            "prompt_stored": False,
            "model_output_stored": False,
            "tool_payloads_stored": False,
            "raw_transcript_imported": request.capture_policy.import_raw_transcript,
        },
    }
    provider = _metadata_text(metadata, "provider") or request.native_provider or "fake"
    model_id = (
        _metadata_text(metadata, "model_id")
        or request.native_model
        or ("fake-native-bootstrap" if provider == "fake" else None)
    )
    if provider is not None:
        output["provider"] = provider
    if model_id is not None:
        output["model_id"] = model_id
    if result.duration_seconds is not None:
        output["duration_seconds"] = result.duration_seconds
    usage = metadata.get("usage")
    if isinstance(usage, Mapping) and usage:
        output["usage"] = dict(usage)
    return output


def _metadata_text(metadata: Mapping[str, Any], key: str) -> str | None:
    value = metadata.get(key)
    return value if isinstance(value, str) and value else None


def _adapter_for(
    agent: str,
    native_provider: str | None,
    native_model: str | None,
    *,
    stream_sink: StreamChunkSink | None = None,
) -> SubprocessAdapter | PipyNativeAdapter:
    if agent == "pipy-native":
        if native_provider not in (None, *SUPPORTED_NATIVE_PROVIDERS):
            raise ValueError(f"unsupported native provider: {native_provider}")
        if native_provider in {
            "openai",
            "openai-completions",
            "openai-codex",
            "openrouter",
            "anthropic",
            "google",
            "google-vertex",
            "mistral",
            "amazon-bedrock",
            "azure-openai",
            "cloudflare",
        }:
            if not native_model:
                raise ValueError(
                    f"--native-model is required for --native-provider {native_provider}"
                )
            return PipyNativeAdapter(
                provider=_native_provider_for_selection(
                    NativeModelSelection(
                        provider_name=native_provider, model_id=native_model
                    )
                ),
                instruction_loader=default_workspace_instruction_loader,
                stream_sink=stream_sink,
            )
        return PipyNativeAdapter(
            provider=FakeNativeProvider(model_id=native_model or "fake-native-bootstrap"),
            instruction_loader=default_workspace_instruction_loader,
            stream_sink=stream_sink,
        )
    if native_provider is not None or native_model is not None:
        raise ValueError("--native-provider and --native-model require --agent pipy-native")
    if stream_sink is not None:
        raise ValueError("--stream requires --agent pipy-native")
    return SubprocessAdapter()


def _repl_adapter_for(
    native_provider: str | None,
    native_model: str | None,
    *,
    input_runtime: str = "auto",
) -> PipyNativeReplAdapter:
    if native_provider not in (None, *SUPPORTED_NATIVE_PROVIDERS):
        raise ValueError(f"unsupported native provider: {native_provider}")
    defaults_store = NativeDefaultsStore(default_native_defaults_path())
    selection = default_selection_for(
        native_provider=native_provider,
        native_model=native_model,
        defaults_store=defaults_store if native_provider is None and native_model is None else None,
    )
    using_stored_default = native_provider is None and native_model is None
    provider_state = NativeReplProviderState(
        selection=selection,
        provider_factory=_native_provider_for_selection,
        defaults_store=defaults_store,
        auth_manager_factory=OpenAICodexAuthManager,
        openai_codex_auth_path=default_openai_codex_auth_path(),
    )
    if using_stored_default and not provider_state.provider_available(selection.provider_name):
        provider_state.selection = _fallback_default_selection(provider_state)
    return PipyNativeReplAdapter(
        provider_state=provider_state,
        input_runtime=input_runtime,
        instruction_loader=default_workspace_instruction_loader,
    )


def _fallback_default_selection(
    provider_state: NativeReplProviderState,
) -> NativeModelSelection:
    """Pick a real provider when the saved/initial selection is unavailable.

    Mirrors Pi's behavior of surfacing a real provider/model in the footer
    when one is configured, rather than showing the deterministic fake
    bootstrap. Falls back to fake only when no real provider is reachable.
    """

    from pipy_harness.native.repl_state import AUTO_DEFAULT_PROVIDER_PRIORITY

    for provider_name in AUTO_DEFAULT_PROVIDER_PRIORITY:
        if provider_state.provider_available(provider_name):
            return NativeModelSelection(
                provider_name=provider_name,
                model_id=DEFAULT_NATIVE_MODELS[provider_name],
            )
    return NativeModelSelection("fake", DEFAULT_NATIVE_MODELS["fake"])


def _resolve_repl_mode(
    requested: str,
    *,
    native_provider: str | None,
    native_model: str | None,
) -> str:
    """Resolve the effective REPL mode for slice 12 of the parity track.

    `auto` (the default) routes to `tool-loop` when the selected provider
    advertises `supports_tool_calls=True` and falls back to `no-tool`
    otherwise. Explicit `no-tool` and `tool-loop` are returned unchanged.
    """

    if requested != "auto":
        return requested
    if native_provider not in (None, *SUPPORTED_NATIVE_PROVIDERS):
        return "no-tool"
    selection = default_selection_for(
        native_provider=native_provider,
        native_model=native_model,
        defaults_store=None,
    )
    try:
        provider = _native_provider_for_selection(selection)
    except Exception:
        return "no-tool"
    if getattr(provider, "supports_tool_calls", False):
        return "tool-loop"
    return "no-tool"


def _tool_repl_adapter_for(
    native_provider: str | None,
    native_model: str | None,
    *,
    tool_budget: int,
    archive_transcript: bool = False,
    input_runtime: str = "auto",
    reference_roots: tuple[Path, ...] = (),
) -> PipyNativeToolReplAdapter:
    if native_provider not in (None, *SUPPORTED_NATIVE_PROVIDERS):
        raise ValueError(f"unsupported native provider: {native_provider}")
    defaults_store = NativeDefaultsStore(default_native_defaults_path())
    selection = default_selection_for(
        native_provider=native_provider,
        native_model=native_model,
        defaults_store=defaults_store
        if native_provider is None and native_model is None
        else None,
    )
    using_stored_default = native_provider is None and native_model is None
    provider_state = NativeReplProviderState(
        selection=selection,
        provider_factory=_native_provider_for_selection,
        defaults_store=defaults_store,
        auth_manager_factory=OpenAICodexAuthManager,
        openai_codex_auth_path=default_openai_codex_auth_path(),
    )
    if using_stored_default and not provider_state.provider_available(
        selection.provider_name
    ):
        provider_state.selection = _fallback_default_selection(provider_state)
    transcript_sink = None
    if archive_transcript:
        from pipy_harness.native.transcripts import TranscriptSink

        transcript_sink = TranscriptSink()
    return PipyNativeToolReplAdapter(
        provider_state=provider_state,
        tool_budget=tool_budget,
        transcript_sink=transcript_sink,
        instruction_loader=default_workspace_instruction_loader,
        input_runtime=input_runtime,
        reference_roots=reference_roots,
    )


PIPY_READ_ROOTS_ENV = "PIPY_READ_ROOTS"
_AUTO_REFERENCE_ROOT_DOCS = (
    Path("docs") / "parity-criterion.md",
    Path("docs") / "pi-parity.md",
    Path("AGENTS.md"),
)


def _resolve_reference_roots(
    cli_roots: list[str] | None,
    *,
    cwd: Path | None = None,
) -> tuple[Path, ...]:
    """Resolve the configured read-only reference roots for the tool loop.

    Combines repeated ``--read-root`` CLI values with the ``PIPY_READ_ROOTS``
    environment variable (``:``-separated). When no roots are configured,
    scans a small fixed list of workspace docs (``AGENTS.md``,
    ``docs/parity-criterion.md``, ``docs/pi-parity.md``) for ``~``-prefixed
    absolute reference paths and auto-discovers the matching directories;
    auto-discovery only fires when the user has not configured any explicit
    root. Each entry is expanded (``~`` → home), resolved to an absolute
    path, and de-duplicated while preserving order. Entries that do not
    exist or are not directories are silently skipped so a stale env value
    cannot break the REPL boot.
    """

    raw_entries: list[str] = []
    if cli_roots:
        raw_entries.extend(cli_roots)
    env_value = os.environ.get(PIPY_READ_ROOTS_ENV, "")
    if env_value:
        raw_entries.extend(part for part in env_value.split(":") if part)

    explicit_resolved = _resolve_root_entries(raw_entries)
    if explicit_resolved:
        return explicit_resolved
    if cwd is None:
        return ()
    auto_entries = _scan_workspace_reference_roots(cwd)
    return _resolve_root_entries(auto_entries)


def _resolve_root_entries(entries: list[str]) -> tuple[Path, ...]:
    seen: set[Path] = set()
    resolved: list[Path] = []
    for entry in entries:
        candidate = Path(entry).expanduser()
        try:
            candidate = candidate.resolve(strict=False)
        except OSError:
            continue
        if not candidate.is_absolute():
            continue
        if not candidate.exists() or not candidate.is_dir():
            continue
        if candidate in seen:
            continue
        seen.add(candidate)
        resolved.append(candidate)
    return tuple(resolved)


def _scan_workspace_reference_roots(cwd: Path) -> list[str]:
    """Extract ``~/`` reference paths from a small fixed list of workspace docs.

    Returns the deepest-existing root for each top-level directory
    referenced by ``~/<dir>/...`` occurrences in those files. The
    candidates are emitted at two depths (``~/<top>/<second>`` and
    ``~/<top>``) and the existence check picks the deepest one that
    resolves to a real directory. Only ``~``-prefixed paths are
    considered so the scan never leaks an arbitrary absolute path from
    a doc. Patterns under ``~/.``-prefixed config directories
    (``~/.claude``, ``~/.codex``, ``~/.local``, ``~/.config``,
    ``~/.pipy``) are skipped because those are pipy/agents config
    locations, not reference repos.
    """

    import re

    pattern = re.compile(
        r"~/([A-Za-z0-9_.][A-Za-z0-9_.\-]*(?:/[A-Za-z0-9_.][A-Za-z0-9_.\-]*)*)"
    )
    skip_top_levels = {".claude", ".codex", ".config", ".local", ".pipy"}
    top_to_candidates: dict[str, list[str]] = {}
    for relative in _AUTO_REFERENCE_ROOT_DOCS:
        candidate = cwd / relative
        try:
            text = candidate.read_text(encoding="utf-8")
        except OSError:
            continue
        for match in pattern.finditer(text):
            segments = match.group(1).split("/")
            top = segments[0]
            if top in skip_top_levels:
                continue
            depths: list[str] = []
            if len(segments) >= 2:
                depths.append(f"~/{segments[0]}/{segments[1]}")
            depths.append(f"~/{segments[0]}")
            top_to_candidates.setdefault(top, [])
            for entry in depths:
                if entry not in top_to_candidates[top]:
                    top_to_candidates[top].append(entry)

    references: list[str] = []
    for top, candidates in top_to_candidates.items():
        del top
        for entry in candidates:
            resolved = Path(entry).expanduser()
            try:
                resolved = resolved.resolve(strict=False)
            except OSError:
                continue
            if resolved.exists() and resolved.is_dir():
                references.append(entry)
                break
    return references


def _native_provider_for_selection(selection: NativeModelSelection) -> ProviderPort:
    if selection.provider_name == "openai":
        return OpenAIResponsesProvider(model_id=selection.model_id)
    if selection.provider_name == "openai-completions":
        from pipy_harness.native.openai_completions_provider import (
            OpenAIChatCompletionsProvider,
        )

        return OpenAIChatCompletionsProvider(model_id=selection.model_id)
    if selection.provider_name == "openai-codex":
        return OpenAICodexResponsesProvider(model_id=selection.model_id)
    if selection.provider_name == "openrouter":
        return OpenRouterChatCompletionsProvider(model_id=selection.model_id)
    if selection.provider_name == "anthropic":
        from pipy_harness.native.anthropic_provider import AnthropicProvider

        return AnthropicProvider(model_id=selection.model_id)
    if selection.provider_name == "google":
        from pipy_harness.native.google_provider import GoogleGenerativeAIProvider

        return GoogleGenerativeAIProvider(model_id=selection.model_id)
    if selection.provider_name == "google-vertex":
        from pipy_harness.native.google_vertex_provider import GoogleVertexProvider

        return GoogleVertexProvider(model_id=selection.model_id)
    if selection.provider_name == "mistral":
        from pipy_harness.native.mistral_provider import MistralProvider

        return MistralProvider(model_id=selection.model_id)
    if selection.provider_name == "amazon-bedrock":
        from pipy_harness.native.bedrock_provider import AmazonBedrockProvider

        return AmazonBedrockProvider(model_id=selection.model_id)
    if selection.provider_name == "azure-openai":
        from pipy_harness.native.azure_openai_provider import (
            AzureOpenAIResponsesProvider,
        )

        return AzureOpenAIResponsesProvider(model_id=selection.model_id)
    if selection.provider_name == "cloudflare":
        from pipy_harness.native.cloudflare_provider import (
            CloudflareWorkersAIProvider,
        )

        return CloudflareWorkersAIProvider(model_id=selection.model_id)
    if selection.provider_name == "fake":
        return FakeNativeProvider(model_id=selection.model_id or DEFAULT_NATIVE_MODELS["fake"])
    raise ValueError(f"unsupported native provider: {selection.provider_name}")


if __name__ == "__main__":
    raise SystemExit(main())
