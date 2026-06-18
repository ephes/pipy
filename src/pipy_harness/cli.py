"""Command-line interface for the pipy product harness."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
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
from pipy_harness.models import (
    RunRequest,
    RunResult,
)
from pipy_harness.native import (
    DEFAULT_NATIVE_MODELS,
    NativeDefaultsStore,
    NativeModelSelection,
    NativeReplProviderState,
    OpenAICodexAuthManager,
    OpenAICodexProviderError,
    OpenAICodexResponsesProvider,
    AUTOMATION_FAKE_MODEL_ID,
    AutomationFakeProvider,
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
from pipy_harness.native.provider_registry import (
    native_provider_spec,
)
from pipy_harness.native.auth_store import AuthStore
from pipy_harness.native.catalog_state import ProviderCatalogState, format_list_models
from pipy_harness.native.prompt_history import PromptHistoryStore
from pipy_harness.native.resource_loading import RuntimeResourceOptions
from pipy_harness.native.package_runtime import compose_package_runtime
from pipy_harness.native.session_tree_commands import StartupSessionAborted
from pipy_harness.native.repl_state import NativeProviderFactory
from pipy_harness.native.retry import RetryPolicy
from pipy_harness.native.export_distribution import (
    NativeExportError,
    export_from_file,
)
from pipy_harness.native.version_check import (
    compare_versions,
    fetch_latest_pipy_version,
    pipy_version,
    self_update_plan,
)
from pipy_harness.native.settings import (
    SettingsManager,
    local_state_base_defaults,
    retry_policy_from_settings,
)
from pipy_harness.native.themes import NativeThemeStore, THEME_ENV_VAR, resolve_active_theme_name
from pipy_harness.native.workspace_context import (
    default_workspace_instruction_loader,
    empty_workspace_instruction_loader,
)
from pipy_harness.runner import (
    FileSessionRecorder,
    HarnessRunner,
    NullSessionRecorder,
)


STREAMING_NATIVE_PROVIDERS = frozenset({"openai-codex", "fake"})
"""Native providers that advertise streaming text deltas through
`ProviderPort.complete(..., stream_sink=...)`. `--stream` fails closed
with a stderr diagnostic when the active provider is not in this set."""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pipy",
        description="Run coding-agent tasks through the pipy harness.",
    )
    parser.add_argument(
        "--version",
        "-v",
        action="version",
        version=f"pipy {pipy_version()}",
        help="Print the pipy version and exit.",
    )
    parser.add_argument(
        "--export",
        nargs="+",
        metavar="FILE",
        help="Export a native session JSONL file to HTML and exit; optional second value is output path.",
    )
    subparsers = parser.add_subparsers(dest="command", required=False)

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
        help=(
            "Native provider for --agent pipy-native (built-in or a custom "
            "models.json provider). Defaults to the deterministic fake provider."
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
        help=(
            "DEPRECATED. Emits one final metadata-only object (record paths, "
            "counters) for --agent pipy-native; it is not a Pi-style event "
            "stream and has no Pi equivalent. Use 'pipy repl --mode json "
            "\"<prompt>\"' for the full Pi-shaped session event stream "
            "(see docs/automation-rpc.md)."
        ),
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
        help=(
            "Native provider for --agent pipy-native (built-in or a custom "
            "models.json provider). Defaults to the deterministic fake provider."
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
            "with /read, /ask-file, /propose-file, and /apply-proposal. "
            "tool-loop forces the bounded tool loop and "
            "errors out when the provider is not tool-capable."
        ),
    )
    repl_parser.add_argument(
        "--tool-budget",
        type=int,
        default=50,
        help=(
            "Per-user-turn tool invocation budget for --repl-mode tool-loop. "
            "Default 50, capped at 200."
        ),
    )
    repl_parser.add_argument(
        "--mode",
        choices=["text", "json", "rpc"],
        default="text",
        help=(
            "Headless automation mode (Pi-compatible). text (default) is the "
            "interactive REPL, or with --print/-p a one-shot final-text run "
            "(piped non-TTY stdin stays interactive REPL input — use --print or "
            "--mode json for one-shot). json emits the native session header then "
            "the full Pi-shaped session event stream as LF-only JSONL on stdout. "
            "rpc starts the long-lived stdin/stdout JSONL command protocol. See "
            "docs/automation-rpc.md."
        ),
    )
    repl_parser.add_argument(
        "--print",
        "-p",
        dest="print_mode",
        action="store_true",
        help=(
            "Run one non-interactive turn and print only the final assistant "
            "text to stdout (Pi -p). Consumes the trailing positional prompt."
        ),
    )
    repl_parser.add_argument(
        "prompt",
        nargs="?",
        default=None,
        help=(
            "One-shot prompt for --mode json / --print. Ignored by the "
            "interactive REPL."
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
    repl_parser.add_argument(
        "--extension",
        "-e",
        dest="extensions",
        action="append",
        default=[],
        metavar="PATH",
        help=(
            "Load an extension file, extension directory, or directory of "
            "extensions for this run. May be repeated. Explicit extensions "
            "still load with --no-extensions."
        ),
    )
    repl_parser.add_argument(
        "--no-extensions",
        "-ne",
        action="store_true",
        help=(
            "Disable default workspace/global/package extension discovery for "
            "this run. Explicit --extension paths still load."
        ),
    )
    repl_parser.add_argument(
        "--skill",
        dest="skills",
        action="append",
        default=[],
        metavar="PATH",
        help=(
            "Load a skill Markdown file or directory for this run. May be "
            "repeated. Explicit skills still load with --no-skills."
        ),
    )
    repl_parser.add_argument(
        "--no-skills",
        "-ns",
        action="store_true",
        help=(
            "Disable default workspace/global/package skill discovery for this "
            "run. Explicit --skill paths still load."
        ),
    )
    repl_parser.add_argument(
        "--prompt-template",
        dest="prompt_templates",
        action="append",
        default=[],
        metavar="PATH",
        help=(
            "Load a prompt-template Markdown file or directory for this run. "
            "May be repeated. Explicit templates still load with "
            "--no-prompt-templates."
        ),
    )
    repl_parser.add_argument(
        "--no-prompt-templates",
        "-np",
        action="store_true",
        help=(
            "Disable default workspace/global/package prompt-template "
            "discovery for this run. Explicit --prompt-template paths still "
            "load."
        ),
    )
    repl_parser.add_argument(
        "--theme",
        dest="themes",
        action="append",
        default=[],
        metavar="PATH",
        help=(
            "Load a theme TOML file or directory for this run. This makes the "
            "theme selectable by settings, PIPY_THEME, or /theme; it does not "
            "select the active theme by itself."
        ),
    )
    repl_parser.add_argument(
        "--no-themes",
        action="store_true",
        help=(
            "Disable package theme discovery for this run. Explicit --theme "
            "paths and built-in themes remain available."
        ),
    )
    # Retired metadata-only session flags. They are still *recognized* (hidden)
    # so they fail with a clear retirement message instead of argparse silently
    # abbreviating "--resume" to "--resume-session" (the picker flag) and
    # treating the value as a positional prompt.
    repl_parser.add_argument(
        "--resume",
        dest="retired_resume",
        metavar="RECORD",
        help=argparse.SUPPRESS,
    )
    repl_parser.add_argument(
        "--branch",
        dest="retired_branch",
        metavar="LABEL",
        help=argparse.SUPPRESS,
    )
    # Native product session-tree startup controls (Pi-style). These select
    # the durable native session under the native-session store; ``pipy-session``
    # remains a separate metadata archive and is never the product source.
    repl_parser.add_argument(
        "-c",
        "--continue",
        dest="continue_recent",
        action="store_true",
        help=(
            "Continue the most recent native product session for this workspace "
            "(Pi `-c`). Creates a fresh native session when none exists."
        ),
    )
    repl_parser.add_argument(
        "-r",
        "--resume-session",
        dest="resume_picker",
        action="store_true",
        help=(
            "Open the native product session at startup (Pi `-r`). In a TTY this "
            "is the interactive picker; otherwise it continues the most recent "
            "native session."
        ),
    )
    repl_parser.add_argument(
        "--session",
        dest="session_target",
        metavar="PATH_OR_ID",
        help=(
            "Open a specific native product session file or partial id "
            "(Pi `--session`)."
        ),
    )
    repl_parser.add_argument(
        "--fork",
        dest="fork_target",
        metavar="PATH_OR_ID",
        help=(
            "Fork a native product session file or partial id into a new native "
            "session (Pi `--fork`)."
        ),
    )
    repl_parser.add_argument(
        "--session-id",
        dest="session_id",
        metavar="ID",
        help=(
            "Open the native product session with this exact id for the current "
            "workspace, or create a fresh one carrying it (Pi `--session-id`). "
            "Mutually exclusive with --session/--continue/--resume-session/"
            "--no-session."
        ),
    )
    repl_parser.add_argument(
        "--session-dir",
        dest="session_dir",
        metavar="DIR",
        help=(
            "Use DIR as the native session store root instead of the default "
            "state directory (Pi `--session-dir`). Per-project session files "
            "live in encoded-cwd subdirectories under it."
        ),
    )
    repl_parser.add_argument(
        "-n",
        "--name",
        dest="session_name",
        metavar="NAME",
        help=(
            "Name the native product session for this run (Pi `--name`/`-n`). "
            "Applied after the session is created/opened/forked."
        ),
    )
    repl_parser.add_argument(
        "--no-session",
        dest="no_session",
        action="store_true",
        help=(
            "Ephemeral mode (Pi `--no-session`): do not create or write a native "
            "session tree, and suppress the pipy-session metadata archive record "
            "for this run."
        ),
    )
    repl_parser.add_argument(
        "--system-prompt",
        dest="system_prompt",
        metavar="TEXT_OR_FILE",
        help=(
            "Replace the default system prompt (Pi `--system-prompt`). The value "
            "is literal text, or a file path when it names an existing file "
            "(read unbounded). Also auto-discovered from .pipy/SYSTEM.md then "
            "<config>/SYSTEM.md when omitted."
        ),
    )
    repl_parser.add_argument(
        "--append-system-prompt",
        dest="append_system_prompt",
        metavar="TEXT_OR_FILE",
        action="append",
        help=(
            "Append to the system prompt (Pi `--append-system-prompt`, "
            "repeatable). Each value is literal text or an existing file path. "
            "Also auto-discovered from .pipy/APPEND_SYSTEM.md then "
            "<config>/APPEND_SYSTEM.md when omitted."
        ),
    )
    repl_parser.add_argument(
        "--no-context-files",
        "-nc",
        dest="no_context_files",
        action="store_true",
        help=(
            "Disable AGENTS.md / CLAUDE.md context-file discovery for this run "
            "(Pi `--no-context-files`/`-nc`): no instruction files are read, "
            "injected into the system prompt, or recorded as safe metadata."
        ),
    )

    config_parser = subparsers.add_parser(
        "config",
        help="View or toggle resource enablement (skills/prompts/themes/extensions).",
    )
    config_parser.add_argument(
        "action",
        nargs="?",
        choices=["list", "enable", "disable"],
        default="list",
        help="list (default) shows discovered resources and their enabled state; "
        "enable/disable write a +pattern/-pattern entry to settings.",
    )
    config_parser.add_argument(
        "resource_type",
        nargs="?",
        choices=["skill", "prompt", "theme", "extension"],
        help="Resource type for enable/disable.",
    )
    config_parser.add_argument(
        "name", nargs="?", help="Resource name (or glob) for enable/disable."
    )
    config_parser.add_argument(
        "--scope",
        choices=["global", "project"],
        default="global",
        help="Settings scope to write to (default global).",
    )
    config_parser.add_argument(
        "--cwd",
        type=Path,
        default=Path.cwd(),
        help="Workspace directory for project scope and resource discovery.",
    )
    config_parser.add_argument(
        "--json",
        dest="config_json",
        action="store_true",
        help="Emit machine-readable JSON (for list).",
    )

    # Package manager (local-path sources). `config` above is the resource
    # enable/disable surface; these manage the `packages` settings array.
    for _name, _help in (
        ("install", "Install (record) a local-path extension package source."),
        ("remove", "Remove a configured package source."),
        ("uninstall", "Remove a configured package source (alias of remove)."),
    ):
        _pkg = subparsers.add_parser(_name, help=_help)
        _pkg.add_argument("source", help="Local-path package source.")
        _pkg.add_argument(
            "-l",
            "--local",
            action="store_true",
            help="Write to project settings (.pipy/settings.json) instead of user.",
        )
        _pkg.add_argument("--cwd", type=Path, default=Path.cwd(), help="Workspace root.")
    _list = subparsers.add_parser("list", help="List configured packages.")
    _list.add_argument("--cwd", type=Path, default=Path.cwd(), help="Workspace root.")

    update_parser = subparsers.add_parser(
        "update",
        help="Update pipy itself (and, later, installed packages).",
    )
    update_parser.add_argument(
        "target",
        nargs="?",
        choices=["self", "pipy"],
        default="self",
        help="Update target. Bare `pipy update` currently runs the self-update half.",
    )
    update_parser.add_argument("--force", action="store_true", help="Run even if already current.")
    update_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the planned update command without executing it.",
    )

    _add_catalog_flags(run_parser)
    _add_catalog_flags(repl_parser)

    return parser


def _add_catalog_flags(parser: argparse.ArgumentParser) -> None:
    """Provider/model catalog flags shared by ``run`` and ``repl`` (Pi parity)."""

    parser.add_argument(
        "--list-models",
        dest="list_models",
        nargs="?",
        const="",
        default=None,
        metavar="SEARCH",
        help=(
            "Print the table of available provider/models (optionally fuzzy-"
            "filtered by SEARCH over 'provider id') and exit. Runs no provider "
            "turn."
        ),
    )
    parser.add_argument(
        "--thinking",
        dest="thinking",
        default=None,
        metavar="LEVEL",
        help=(
            "Accepted thinking level: off|minimal|low|medium|high|xhigh. "
            "Stored in local provider-selection state, but provider-request "
            "mapping is not yet wired (see docs/provider-catalog.md). "
            "Invalid values warn and fall back to the default."
        ),
    )
    parser.add_argument(
        "--models",
        dest="scoped_models",
        default=None,
        metavar="PATTERNS",
        help=(
            "Comma-separated model patterns (globs, each with an optional "
            ":level suffix which is ignored for scoping). In `pipy repl` these "
            "apply as a final CLI override of `enabledModels`, constraining the "
            "`/scoped-models` set and Ctrl+P cycling for the session (CLI wins "
            "over settings.json). The per-pattern :level initial preference is "
            "not yet applied (see docs/provider-catalog.md)."
        ),
    )
    parser.add_argument(
        "--api-key",
        dest="api_key",
        default=None,
        help=(
            "Runtime API key override for the selected provider. Accepted and "
            "kept out of archives, but real provider-call auth wiring is not "
            "yet complete (see docs/provider-catalog.md)."
        ),
    )


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    raw_argv = sys.argv[1:] if argv is None else argv
    if not raw_argv:
        raw_argv = ["repl"]
    args = parser.parse_args(raw_argv)

    try:
        if getattr(args, "export", None):
            return _cmd_product_export(args.export)
        if getattr(args, "list_models", None) is not None:
            cwd = getattr(args, "cwd", Path.cwd()).expanduser().resolve()
            settings_manager = _build_runtime_settings(cwd)
            return _handle_list_models(
                args.list_models or None,
                cwd=cwd,
                settings_manager=settings_manager,
                resource_options=_resource_options_from_args(args),
                api_key=getattr(args, "api_key", None),
            )
        if args.command == "config":
            return _cmd_config(args)
        if args.command == "update":
            return _cmd_update(args)
        if args.command in {"install", "remove", "uninstall", "list"}:
            return _cmd_package(args)
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
            cwd = args.cwd.expanduser().resolve()
            run_settings_manager = (
                _build_runtime_settings(
                    cwd,
                    scoped_models=_parse_models_flag(
                        getattr(args, "scoped_models", None)
                    ),
                )
                if args.agent == "pipy-native"
                else None
            )
            run_resource_options = (
                _resource_options_from_args(args)
                if args.agent == "pipy-native"
                else None
            )
            run_catalog_state = (
                _build_catalog_state(
                    runtime_api_key=getattr(args, "api_key", None),
                    cwd=cwd,
                    settings_manager=run_settings_manager,
                    resource_options=run_resource_options,
                )
                if args.agent == "pipy-native"
                else None
            )
            # Resolve the native selection once (catalog-aware) so stream
            # validation and adapter construction agree on the EFFECTIVE provider
            # (a bare --native-model can resolve to a real provider, not fake).
            run_selection = (
                _resolve_run_selection(
                    args.native_provider,
                    args.native_model,
                    api_key=getattr(args, "api_key", None),
                    catalog_state=run_catalog_state,
                )
                if args.agent == "pipy-native"
                else None
            )
            _validate_stream(
                args.agent,
                args.stream,
                native_provider=(
                    run_selection.provider_name
                    if run_selection is not None
                    else args.native_provider
                ),
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
                thinking=getattr(args, "thinking", None),
                api_key=getattr(args, "api_key", None),
                settings_manager=run_settings_manager,
                selection=run_selection,
                catalog_state=run_catalog_state,
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
            # Validate session flags (retired --resume/--branch + mutual
            # exclusion) up front and unconditionally, before any repl-mode
            # branch or native-session resolution, so the rejection never
            # depends on a downstream code path being reached.
            _validate_native_session_flags(args)
            validate_native_repl_input_runtime(
                input_stream=sys.stdin,
                error_stream=sys.stderr,
                input_runtime=args.input_runtime,
                workspace=args.cwd,
            )
            repl_adapter: PipyNativeReplAdapter | PipyNativeToolReplAdapter
            # The native product session tree (Pi-style --session/--fork/-c/-r)
            # is the product session source; the old metadata-only --resume
            # RECORD / --branch LABEL repl flags were retired in favour of it.
            # `pipy-session resume-info` remains the separate archive utility.
            resume_context = None
            resume_lineage = None
            resume_branch_label = None
            # Layered settings: a settings.json defaultProvider/defaultModel/theme
            # is the source of truth over the legacy local-state store (CLI flags
            # still win). The store remains the fallback for selection.
            settings_manager = _build_runtime_settings(
                args.cwd.expanduser().resolve(),
                scoped_models=_parse_models_flag(getattr(args, "scoped_models", None)),
            )
            resource_options = _resource_options_from_args(args)
            file_settings = settings_manager.merged_file_settings()
            _apply_settings_theme_env(file_settings)
            eff_native_provider = args.native_provider or _settings_str(
                file_settings, "defaultProvider"
            )
            eff_native_model = args.native_model or _settings_str(
                file_settings, "defaultModel"
            )
            cwd = args.cwd.expanduser().resolve()
            startup_catalog_state = _build_catalog_state(
                runtime_api_key=args.api_key,
                cwd=cwd,
                settings_manager=settings_manager,
                resource_options=resource_options,
            )
            resolved_repl_mode = _resolve_repl_mode(
                args.repl_mode,
                native_provider=eff_native_provider,
                native_model=eff_native_model,
                catalog_state=startup_catalog_state,
            )
            # Resolve the Pi-style native product session for this run from the
            # startup flags. ``pipy-session`` is a separate metadata archive and
            # is never the product session source.
            native_session = _resolve_native_startup_session(args)
            if resolved_repl_mode == "tool-loop":
                if args.tool_budget < 1 or args.tool_budget > 200:
                    raise ValueError(
                        "--tool-budget must be in [1, 200]; got "
                        f"{args.tool_budget}"
                    )
                reference_roots = _resolve_reference_roots(
                    args.read_root,
                    cwd=cwd,
                )
                repl_adapter = _tool_repl_adapter_for(
                    eff_native_provider,
                    eff_native_model,
                    cwd=cwd,
                    tool_budget=args.tool_budget,
                    archive_transcript=args.archive_transcript,
                    input_runtime=args.input_runtime,
                    reference_roots=reference_roots,
                    resume_context=resume_context,
                    resume_branch_label=resume_branch_label,
                    native_session=native_session,
                    thinking=args.thinking,
                    api_key=args.api_key,
                    settings_manager=settings_manager,
                    system_prompt_source=args.system_prompt,
                    append_system_prompt_sources=args.append_system_prompt,
                    no_context_files=args.no_context_files,
                    resource_options=resource_options,
                    catalog_state=startup_catalog_state,
                )
            else:
                repl_adapter = _repl_adapter_for(
                    eff_native_provider,
                    eff_native_model,
                    cwd=cwd,
                    input_runtime=args.input_runtime,
                    resume_context=resume_context,
                    resume_branch_label=resume_branch_label,
                    thinking=args.thinking,
                    api_key=args.api_key,
                    settings_manager=settings_manager,
                    system_prompt_source=args.system_prompt,
                    append_system_prompt_sources=args.append_system_prompt,
                    no_context_files=args.no_context_files,
                    resource_options=resource_options,
                    catalog_state=startup_catalog_state,
                )
            # Headless automation surfaces (Pi --mode json/rpc, --print). These
            # drive the same tool-loop adapter for a one-shot run (json/print) or
            # the long-lived JSONL protocol (rpc). The interactive REPL — which
            # includes piped (non-TTY) stdin as REPL input — is the default.
            app_mode = _select_repl_app_mode(args)
            if app_mode != "interactive":
                return _run_repl_automation(
                    app_mode,
                    args,
                    repl_adapter=repl_adapter,
                    native_session=native_session,
                )
            if args.prompt is not None:
                # A bare positional prompt is ambiguous in the interactive REPL
                # (which reads its prompts live); one-shot/RPC must be explicit.
                raise ValueError(
                    "a positional prompt requires --print/-p (one-shot text) or "
                    "--mode json|rpc"
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
                resume=resume_lineage,
            )
            repl_recorder = (
                NullSessionRecorder() if args.no_session else FileSessionRecorder()
            )
            result = HarnessRunner(
                adapter=repl_adapter, recorder=repl_recorder
            ).run(request)
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
    except StartupSessionAborted:
        # Declined the cross-project fork prompt (Pi prints "Aborted." / exit 0).
        print("pipy: aborted.", file=sys.stderr)
        return 0
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


def _cmd_product_export(values: list[str]) -> int:
    if len(values) not in (1, 2):
        print(
            "pipy: --export expects <session.jsonl> and optional <output.html>",
            file=sys.stderr,
        )
        return 2
    source = Path(values[0])
    output = Path(values[1]) if len(values) == 2 else None
    try:
        exported = export_from_file(source, output)
    except NativeExportError as exc:
        print(f"pipy: {exc}", file=sys.stderr)
        return 1
    print(f"Exported to: {exported}")
    return 0


def _cmd_update(args: Any) -> int:
    current = pipy_version()
    latest = None if args.force else fetch_latest_pipy_version()
    if latest is not None and compare_versions(current, latest) >= 0:
        print(f"pipy is already up to date (v{current})")
        return 0
    plan = self_update_plan(force=args.force)
    if not plan.automatic:
        print(
            "pipy: automatic self-update is unavailable for this install "
            f"({plan.method}); {plan.reason}. Executable: {plan.executable}",
            file=sys.stderr,
        )
        return 0
    command_text = " ".join(plan.command)
    if args.dry_run:
        print(f"pipy update plan ({plan.method}): {command_text}")
        return 0
    completed = subprocess.run(plan.command, check=False)
    return int(completed.returncode)


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
    thinking: str | None = None,
    api_key: str | None = None,
    settings_manager: SettingsManager | None = None,
    selection: "NativeModelSelection | None" = None,
    catalog_state: ProviderCatalogState | None = None,
) -> SubprocessAdapter | PipyNativeAdapter:
    if agent == "pipy-native":
        if selection is None:
            selection = _resolve_run_selection(
                native_provider,
                native_model,
                api_key=api_key,
                catalog_state=catalog_state,
            )
        return PipyNativeAdapter(
            provider=_run_provider_for_selection(
                selection,
                thinking=thinking,
                api_key=api_key,
                settings_manager=settings_manager,
                catalog_state=catalog_state,
            ),
            instruction_loader=default_workspace_instruction_loader,
            stream_sink=stream_sink,
        )
    if native_provider is not None or native_model is not None:
        raise ValueError("--native-provider and --native-model require --agent pipy-native")
    if stream_sink is not None:
        raise ValueError("--stream requires --agent pipy-native")
    return SubprocessAdapter()


def _resolve_run_selection(
    native_provider: str | None,
    native_model: str | None,
    *,
    api_key: str | None = None,
    catalog_state: ProviderCatalogState | None = None,
) -> NativeModelSelection:
    """Resolve the one-shot ``pipy run`` selection (catalog-aware).

    Both-None keeps the deterministic ``fake`` bootstrap; ``--native-provider
    fake`` passes through; otherwise the selection is catalog-resolved (a custom
    ``models.json`` provider resolves its default, a bare ``--native-model``
    resolves its provider). The run-only "a built-in real provider needs an
    explicit model" rule is enforced against the RESOLVED canonical provider, so
    case variants and inferred providers are validated too.
    """

    if native_provider is None and native_model is None:
        return NativeModelSelection("fake", "fake-native-bootstrap")
    if native_provider == "fake":
        return NativeModelSelection("fake", native_model or "fake-native-bootstrap")
    if catalog_state is None:
        catalog_state = _build_catalog_state(runtime_api_key=api_key)
    selection = default_selection_for(
        native_provider=native_provider,
        native_model=native_model,
        rows=catalog_state.get_all(),
    )
    if native_model is None:
        spec = native_provider_spec(selection.provider_name)
        if spec is not None and spec.requires_model_for_run:
            raise ValueError(
                "--native-model is required for --native-provider "
                f"{selection.provider_name}"
            )
    return selection


def _run_provider_for_selection(
    selection: NativeModelSelection,
    *,
    thinking: str | None = None,
    api_key: str | None = None,
    settings_manager: SettingsManager | None = None,
    catalog_state: ProviderCatalogState | None = None,
) -> ProviderPort:
    """Construct the one-shot ``pipy run`` provider via catalog construction.

    Mirrors the REPL's catalog-first construction
    (:class:`NativeReplProviderState`): catalog-wired families are built from the
    selected ``NativeModelSpec`` plus resolved auth/headers/thinking, so a custom
    ``models.json`` provider, ``--api-key``, base URLs, headers and ``--thinking``
    all reach the one-shot turn the same way they reach a REPL turn. ``fake`` and
    ``openai-codex`` fall back to the legacy factory (codex keeps its
    settings-derived ``RetryPolicy``).
    """

    if catalog_state is None:
        catalog_state = _build_catalog_state(runtime_api_key=api_key)
    provider_state = NativeReplProviderState(
        selection=selection,
        provider_factory=_provider_factory_for(settings_manager),
        auth_manager_factory=OpenAICodexAuthManager,
        openai_codex_auth_path=default_openai_codex_auth_path(),
        catalog_state=catalog_state,
        thinking_level=_validated_thinking_level(thinking),
        persist_defaults=False,
    )
    return provider_state.current_provider()


_CONFIG_RESOURCE_KEYS = {
    "skill": "skills",
    "prompt": "prompts",
    "theme": "themes",
    "extension": "extensions",
}


def _cmd_package(args: Any) -> int:
    """`pipy install/remove/uninstall/list`: manage local-path packages.

    Sources are recorded in the `packages` array of the user settings
    (`<config>/settings.json`) or, with `-l/--local`, the project settings
    (`<cwd>/.pipy/settings.json`). Only local-path sources are supported in
    this slice; `git:`/`http(s):`/`npm:` sources are rejected.
    """

    from pipy_harness.native import package_manager as pkg
    from pipy_harness.native.settings import (
        global_settings_path,
        project_settings_path,
    )

    cwd = args.cwd.expanduser().resolve()
    if args.command == "list":
        listing = pkg.list_packages(
            user_path=global_settings_path(),
            project_path=project_settings_path(cwd),
        )
        print(pkg.format_package_listing(listing))
        return 0

    source = args.source
    settings_path = (
        project_settings_path(cwd) if args.local else global_settings_path()
    )
    try:
        if args.command == "install":
            if not pkg.is_local_path_source(source):
                print(
                    f"pipy: only local-path package sources are supported; "
                    f"got {source!r}",
                    file=sys.stderr,
                )
                return 2
            if pkg.canonical_local_source(source, cwd if args.local else None) is None:
                print(
                    f"pipy: package source not found: {source}",
                    file=sys.stderr,
                )
                return 2
            print(pkg.install_package(source, settings_path))
            return 0
        # remove / uninstall
        message = pkg.remove_package(source, settings_path)
    except pkg.PackageSettingsError as exc:
        print(f"pipy: {exc}", file=sys.stderr)
        return 1
    if message is None:
        print(f"pipy: package source not configured: {source}", file=sys.stderr)
        return 1
    print(message)
    return 0


def _cmd_config(args: Any) -> int:
    """`pipy config`: view or toggle resource enablement via settings patterns.

    `list` reports the discovered skills/prompts and their enabled state under
    the resolved settings; `enable`/`disable` write a `+pattern`/`-pattern` entry
    into the relevant settings array at the chosen scope (Pi's `pi config`
    model — discovered paths are never removed). Runs no provider turn.
    """

    from pipy_harness.native.resource_enablement import (
        disable_entry,
        enable_entry,
        is_resource_enabled,
    )
    from pipy_harness.native.resources import WorkspaceResources
    from pipy_harness.native.settings import SCOPE_GLOBAL, SCOPE_PROJECT

    cwd = args.cwd.expanduser().resolve()
    manager = SettingsManager.for_workspace(cwd)
    action = getattr(args, "action", "list") or "list"

    if action == "list":
        skills_patterns = manager.get_skills_patterns()
        prompts_patterns = manager.get_prompts_patterns()
        enable_skill_commands = manager.get_enable_skill_commands()
        themes_patterns = manager.get_themes_patterns()
        extensions_patterns = manager.get_extensions_patterns()
        # Include installed local-path package resources in the listing so
        # `pipy config` reflects what a session would discover, for all four
        # resource kinds. Resources are discovered UNFILTERED here; the
        # enabled/disabled state is computed per kind below, so a disabled
        # package resource (skill/prompt/theme/extension) still appears with
        # enabled=false rather than vanishing. No global theme registry is
        # installed (listing has no lasting effect on session state).
        from pipy_harness.native.extensions import discover_extensions
        from pipy_harness.native.package_runtime import compose_package_runtime
        from pipy_harness.native.theme_files import build_theme_registry
        from pipy_harness.native.themes import builtin_palettes

        package_roots = compose_package_runtime(manager, cwd, install_theme_registry=False)
        resources = WorkspaceResources.discover(cwd, package_roots=package_roots)
        descriptors = discover_extensions(cwd, package_roots=package_roots.extensions)
        theme_names = build_theme_registry(package_roots.themes).names()
        builtin_theme_names = set(builtin_palettes())
        skills = [
            {
                "name": s.name,
                "enabled": enable_skill_commands
                and is_resource_enabled(s.name, skills_patterns),
            }
            for s in resources.skills
        ]
        prompts = [
            {"name": t.name, "enabled": is_resource_enabled(t.name, prompts_patterns)}
            for t in resources.templates
        ]
        # Built-in themes are always selectable at runtime (filters apply only
        # to package themes), so report them as enabled regardless of filters.
        theme_items = [
            {
                "name": name,
                "enabled": name in builtin_theme_names
                or is_resource_enabled(name, themes_patterns),
            }
            for name in theme_names
        ]
        # A descriptor's `disabled` status (unsafe/duplicate/etc.) and the
        # `+/-pattern` filter both gate whether an extension is active.
        extension_items = [
            {
                "name": d.name,
                "enabled": d.status == "loadable"
                and is_resource_enabled(d.name, extensions_patterns),
            }
            for d in descriptors
        ]
        report = {
            "enableSkillCommands": enable_skill_commands,
            "skills": skills,
            "prompts": prompts,
            "themes": theme_items,
            "extensions": extension_items,
            "skillsPatterns": skills_patterns,
            "promptsPatterns": prompts_patterns,
            "themesPatterns": themes_patterns,
            "extensionsPatterns": extensions_patterns,
        }
        if getattr(args, "config_json", False):
            print(json.dumps(report, sort_keys=True))
        else:
            print("pipy config — resource enablement:")
            print(f"  enableSkillCommands: {enable_skill_commands}")
            for label, items in (
                ("skills", skills),
                ("prompts", prompts),
                ("themes", theme_items),
                ("extensions", extension_items),
            ):
                print(f"  {label}:")
                if not items:
                    print("    (none discovered)")
                for item in items:
                    state = "enabled" if item["enabled"] else "disabled"
                    print(f"    {item['name']} [{state}]")
        return 0

    resource_type = getattr(args, "resource_type", None)
    name = getattr(args, "name", None)
    if resource_type is None or not name:
        print(
            f"pipy: `pipy config {action}` requires a resource type and name, "
            "e.g. `pipy config disable skill review`.",
            file=sys.stderr,
        )
        return 2
    key = _CONFIG_RESOURCE_KEYS[resource_type]
    scope = SCOPE_PROJECT if args.scope == "project" else SCOPE_GLOBAL
    current = manager.get_extensions_patterns() if key == "extensions" else (
        manager.get_skills_patterns()
        if key == "skills"
        else manager.get_prompts_patterns()
        if key == "prompts"
        else manager.get_themes_patterns()
    )
    updated = (
        enable_entry(current, name) if action == "enable" else disable_entry(current, name)
    )
    try:
        manager.set_resource_patterns(key, updated, scope=scope)
    except (RuntimeError, ValueError) as exc:
        print(f"pipy: could not update {key}: {exc}", file=sys.stderr)
        return 1
    print(f"pipy: {action}d {resource_type} {name!r} in {scope} settings ({key}).")
    return 0


_MODELS_THINKING_LEVELS = frozenset(
    {"off", "minimal", "low", "medium", "high", "xhigh"}
)


def _parse_models_flag(value: str | None) -> list[str] | None:
    """Parse ``--models`` into scoped-model patterns.

    Each comma-separated token is a model pattern with an optional trailing
    ``:level`` thinking-level suffix. Model references can themselves contain
    colons (e.g. Bedrock ``...sonnet-...-v1:0``), so only a **trailing** suffix
    after the **last** colon is stripped, and only when it is a known thinking
    level; otherwise the token is kept verbatim (the ``:level`` initial
    preference is not yet applied — see docs/provider-catalog.md).
    """

    if not value:
        return None
    patterns: list[str] = []
    for raw in value.split(","):
        token = raw.strip()
        if not token:
            continue
        head, sep, tail = token.rpartition(":")
        if sep and head.strip() and tail.strip().lower() in _MODELS_THINKING_LEVELS:
            token = head.strip()
        patterns.append(token)
    return patterns or None


def _build_runtime_settings(
    cwd: Path, *, scoped_models: list[str] | None = None
) -> SettingsManager:
    """Build the layered settings manager for a ``pipy repl`` run.

    Imports the existing local-state store values (provider/model/theme/
    prompt-history) as the lowest-precedence ``base_defaults`` layer so they
    surface through settings without rewriting the runtime-state files, while a
    user ``settings.json`` still overrides them. ``--models`` patterns, when
    given, apply as a final CLI override of ``enabledModels`` so they constrain
    the scoped-model / Ctrl+P cycle for the session (CLI wins over the file).
    """

    stored = NativeDefaultsStore(default_native_defaults_path()).load()
    theme = resolve_active_theme_name(store=NativeThemeStore())
    base = local_state_base_defaults(
        provider=stored.provider_name if stored is not None else None,
        model=stored.model_id if stored is not None else None,
        theme=theme,
        prompt_history_enabled=PromptHistoryStore().enabled,
    )
    overrides = {"enabledModels": scoped_models} if scoped_models else None
    return SettingsManager.for_workspace(cwd, base_defaults=base, overrides=overrides)


def _settings_str(file_settings: dict[str, object], key: str) -> str | None:
    value = file_settings.get(key)
    return value if isinstance(value, str) else None


def _apply_settings_theme_env(file_settings: dict[str, object]) -> None:
    """Make a ``settings.json`` ``theme`` take effect via the theme env var.

    Settings is the source of truth over the persisted theme store, but an
    explicit ``PIPY_THEME`` in the environment is a final override and wins. So
    the file theme is injected into ``PIPY_THEME`` only when the env var is unset
    (the rest of the chrome already reads ``PIPY_THEME`` per render).
    """

    file_theme = _settings_str(file_settings, "theme")
    if file_theme and not os.environ.get(THEME_ENV_VAR):
        os.environ[THEME_ENV_VAR] = file_theme


def _repl_adapter_for(
    native_provider: str | None,
    native_model: str | None,
    *,
    cwd: Path,
    input_runtime: str = "auto",
    resume_context: Any = None,
    resume_branch_label: str | None = None,
    thinking: str | None = None,
    api_key: str | None = None,
    settings_manager: SettingsManager | None = None,
    system_prompt_source: str | None = None,
    append_system_prompt_sources: list[str] | None = None,
    no_context_files: bool = False,
    resource_options: RuntimeResourceOptions | None = None,
    catalog_state: ProviderCatalogState | None = None,
) -> PipyNativeReplAdapter:
    defaults_store = NativeDefaultsStore(default_native_defaults_path())
    if catalog_state is None:
        catalog_state = _build_catalog_state(
            runtime_api_key=api_key,
            cwd=cwd,
            settings_manager=settings_manager,
            resource_options=resource_options,
        )
    selection = default_selection_for(
        native_provider=native_provider,
        native_model=native_model,
        defaults_store=defaults_store if native_provider is None and native_model is None else None,
        rows=catalog_state.get_all(),
    )
    using_stored_default = native_provider is None and native_model is None
    provider_state = NativeReplProviderState(
        selection=selection,
        provider_factory=_provider_factory_for(settings_manager),
        defaults_store=defaults_store,
        auth_manager_factory=OpenAICodexAuthManager,
        openai_codex_auth_path=default_openai_codex_auth_path(),
        catalog_state=catalog_state,
        thinking_level=_validated_thinking_level(thinking),
    )
    if using_stored_default and not provider_state.provider_available(selection.provider_name):
        provider_state.selection = _fallback_default_selection(provider_state)
    return PipyNativeReplAdapter(
        provider_state=provider_state,
        input_runtime=input_runtime,
        instruction_loader=(
            empty_workspace_instruction_loader
            if no_context_files
            else default_workspace_instruction_loader
        ),
        resume_context=resume_context,
        resume_branch_label=resume_branch_label,
        settings_manager=settings_manager,
        system_prompt_source=system_prompt_source,
        append_system_prompt_sources=append_system_prompt_sources,
        resource_options=resource_options,
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


def _run_startup_resume_picker(args: Any) -> Path | None:
    """Open the interactive native-session picker for ``-r`` on a real TTY.

    Returns the chosen native session file to open, or ``None`` to fall back to
    continuing the most recent session when no interactive picker runs (non-TTY
    or no sessions to pick). When the picker *does* run and the user cancels it
    (Esc/Ctrl-C/Ctrl-D), this raises :class:`StartupSessionAborted` so the run
    exits cleanly like Pi's "No session selected" rather than silently
    continuing a different session. Shares the in-session ``/resume`` picker.
    """

    if not _startup_stdin_is_tty():
        return None
    cwd = args.cwd.expanduser().resolve()
    sessions_root = _native_sessions_root_override(args)
    from pipy_harness.native.session_tree import (
        default_native_session_dir,
        native_sessions_root,
    )
    from pipy_harness.native.session_tree_commands import (
        list_all_native_sessions,
        list_native_sessions,
    )
    from pipy_harness.native.tui import run_startup_session_picker

    project_dir = default_native_session_dir(cwd, sessions_root=sessions_root)
    root = native_sessions_root(session_dir=sessions_root)
    project_sessions = list_native_sessions(project_dir)
    all_sessions = list_all_native_sessions(root)
    if not project_sessions and not all_sessions:
        return None
    picked = run_startup_session_picker(
        project_sessions=project_sessions,
        all_sessions=all_sessions,
        current_cwd=str(cwd),
    )
    if picked is None:
        # The interactive picker ran and the user cancelled it; abort cleanly
        # (exit 0) instead of falling through to continue-most-recent.
        raise StartupSessionAborted("")
    return picked


def _startup_stdin_is_tty() -> bool:
    try:
        return bool(sys.stdin) and sys.stdin.isatty() and sys.stdout.isatty()
    except (ValueError, OSError):
        return False


def _resolve_repl_mode(
    requested: str,
    *,
    native_provider: str | None,
    native_model: str | None,
    cwd: Path | None = None,
    settings_manager: SettingsManager | None = None,
    resource_options: RuntimeResourceOptions | None = None,
    api_key: str | None = None,
    catalog_state: ProviderCatalogState | None = None,
) -> str:
    """Resolve the effective REPL mode for slice 12 of the parity track.

    `auto` (the default) routes to `tool-loop` when the selected provider
    advertises `supports_tool_calls=True` and falls back to `no-tool`
    otherwise. Explicit `no-tool` and `tool-loop` are returned unchanged.
    """

    if requested != "auto":
        return requested
    if catalog_state is None:
        catalog_state = _build_catalog_state(
            runtime_api_key=api_key,
            cwd=cwd,
            settings_manager=settings_manager,
            resource_options=resource_options,
        )
    try:
        selection = default_selection_for(
            native_provider=native_provider,
            native_model=native_model,
            defaults_store=None,
            rows=catalog_state.get_all(),
        )
    except ValueError:
        # Unknown provider/model — the adapter build will surface the error;
        # default the probe to no-tool.
        return "no-tool"
    # Probe through catalog construction (the same boundary the REPL uses) so a
    # custom models.json provider is recognized as tool-capable, not just the
    # built-in registry.
    provider_state = NativeReplProviderState(
        selection=selection,
        provider_factory=_native_provider_for_selection,
        catalog_state=catalog_state,
        persist_defaults=False,
    )
    try:
        provider = provider_state.current_provider()
    except Exception:
        return "no-tool"
    if getattr(provider, "supports_tool_calls", False):
        return "tool-loop"
    return "no-tool"


def _tool_repl_adapter_for(
    native_provider: str | None,
    native_model: str | None,
    *,
    cwd: Path,
    tool_budget: int,
    archive_transcript: bool = False,
    input_runtime: str = "auto",
    reference_roots: tuple[Path, ...] = (),
    resume_context: Any = None,
    resume_branch_label: str | None = None,
    native_session: Any = None,
    thinking: str | None = None,
    api_key: str | None = None,
    settings_manager: SettingsManager | None = None,
    system_prompt_source: str | None = None,
    append_system_prompt_sources: list[str] | None = None,
    no_context_files: bool = False,
    resource_options: RuntimeResourceOptions | None = None,
    catalog_state: ProviderCatalogState | None = None,
) -> PipyNativeToolReplAdapter:
    defaults_store = NativeDefaultsStore(default_native_defaults_path())
    if catalog_state is None:
        catalog_state = _build_catalog_state(
            runtime_api_key=api_key,
            cwd=cwd,
            settings_manager=settings_manager,
            resource_options=resource_options,
        )
    selection = default_selection_for(
        native_provider=native_provider,
        native_model=native_model,
        defaults_store=defaults_store
        if native_provider is None and native_model is None
        else None,
        rows=catalog_state.get_all(),
    )
    using_stored_default = native_provider is None and native_model is None
    provider_state = NativeReplProviderState(
        selection=selection,
        provider_factory=_provider_factory_for(settings_manager),
        defaults_store=defaults_store,
        auth_manager_factory=OpenAICodexAuthManager,
        openai_codex_auth_path=default_openai_codex_auth_path(),
        catalog_state=catalog_state,
        thinking_level=_validated_thinking_level(thinking),
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
        instruction_loader=(
            empty_workspace_instruction_loader
            if no_context_files
            else default_workspace_instruction_loader
        ),
        input_runtime=input_runtime,
        reference_roots=reference_roots,
        resume_context=resume_context,
        resume_branch_label=resume_branch_label,
        native_session=native_session,
        settings_manager=settings_manager,
        system_prompt_source=system_prompt_source,
        append_system_prompt_sources=append_system_prompt_sources,
        resource_options=resource_options,
    )


def _validate_native_session_flags(args: Any) -> None:
    """Enforce Pi's startup-flag mutual exclusion before resolving a session.

    Matches ``validateForkFlags``/``validateSessionIdFlags`` in Pi's
    ``main.ts``: ``--fork`` and ``--session-id`` each conflict with
    ``--session``, ``--continue``, ``--resume-session``, and ``--no-session``.
    ``--fork`` may be combined with ``--session-id`` (the id names the new
    forked session).
    """

    if (
        getattr(args, "retired_resume", None) is not None
        or getattr(args, "retired_branch", None) is not None
    ):
        raise ValueError(
            "--resume/--branch were retired; the native session tree is the "
            "product session source. Use -c/--continue, -r/--resume-session, "
            "--session, --session-id, or --fork (or /resume in-session). The "
            "metadata-only archive reader remains as 'pipy-session resume-info'."
        )

    # ``is not None`` (not truthiness) so an explicit empty ``--session ""`` /
    # ``--fork ""`` still counts as present for conflict + required-target
    # handling instead of being silently ignored.
    conflict_specs = (
        ("--session", getattr(args, "session_target", None) is not None),
        ("--continue", bool(getattr(args, "continue_recent", False))),
        ("--resume-session", bool(getattr(args, "resume_picker", False))),
        ("--no-session", bool(getattr(args, "no_session", False))),
    )
    if getattr(args, "fork_target", None) is not None:
        clashes = [name for name, present in conflict_specs if present]
        if clashes:
            raise ValueError(
                "--fork cannot be combined with " + ", ".join(clashes)
            )
    # Use ``is not None`` so an explicit empty ``--session-id ""`` is not
    # silently treated as absent (it bypasses validation/mutual exclusion);
    # an empty value is rejected downstream by ``validate_session_id``.
    if getattr(args, "session_id", None) is not None:
        clashes = [name for name, present in conflict_specs if present]
        if clashes:
            raise ValueError(
                "--session-id cannot be combined with " + ", ".join(clashes)
            )


def _native_sessions_root_override(args: Any) -> Path | None:
    """Resolve the Pi ``--session-dir`` native-store root override.

    Only the ``--session-dir`` flag (or Pi's own ``$PI_SESSION_DIR``) overrides
    the native session store root. ``$PIPY_SESSION_DIR`` is deliberately *not*
    honored here: it points at the separate ``pipy-session`` metadata archive,
    and reusing it would write native product transcripts into the archive's
    directory tree. The native store root otherwise comes from
    ``$PIPY_NATIVE_SESSIONS_ROOT`` via ``default_state_root()``.
    """

    flag = getattr(args, "session_dir", None)
    if flag:
        return Path(flag).expanduser()
    env_dir = os.environ.get("PI_SESSION_DIR")
    if env_dir:
        return Path(env_dir).expanduser()
    return None


def _confirm_cross_project_fork(other_cwd: str) -> bool:
    """Prompt to fork a session found in a different project (Pi behavior)."""

    from pipy_harness.native.session_tree_commands import sanitize_label_text

    # other_cwd is the header cwd of another project's session file; sanitize it
    # before printing so it cannot inject terminal escape sequences.
    print(
        f"pipy: session found in different project: "
        f"{sanitize_label_text(other_cwd)}",
        file=sys.stderr,
    )
    print(
        "Fork this session into current directory? [y/N] ",
        end="",
        file=sys.stderr,
        flush=True,
    )
    try:
        answer = sys.stdin.readline()
    except (OSError, ValueError):
        return False
    return answer.strip().lower() in ("y", "yes")


def _resolve_native_startup_session(args: Any) -> Any:
    """Build the native product session tree for a ``pipy repl`` run.

    Maps the Pi-style startup flags to a native session. Mutually exclusive
    flag combinations are rejected first (matching Pi). A ``--session`` partial
    id that resolves only in a *different* project prompts to fork it into the
    current workspace (Pi cross-project behavior). ``--name``/``-n`` is applied
    after the session is resolved, and ``--session-dir`` overrides the native
    session store root.
    """

    from pipy_harness.native.session_tree_commands import resolve_startup_session

    _validate_native_session_flags(args)

    cwd = args.cwd.expanduser().resolve()
    sessions_root = _native_sessions_root_override(args)
    name = getattr(args, "session_name", None)
    session_id = getattr(args, "session_id", None)

    if getattr(args, "no_session", False):
        mode, target = "none", None
    elif getattr(args, "session_target", None) is not None:
        mode, target = "session", args.session_target
    elif getattr(args, "fork_target", None) is not None:
        mode, target = "fork", args.fork_target
    elif session_id is not None:
        # An explicit (even empty) --session-id selects session-id mode; an
        # empty value is then rejected by the resolver / validate_session_id.
        mode, target = "session-id", session_id
    elif getattr(args, "continue_recent", False):
        mode, target = "continue", None
    elif getattr(args, "resume_picker", False):
        mode, target = _resolve_startup_resume_mode(args)
    else:
        mode, target = "new", None

    def confirm(ref: Any) -> bool:
        return _confirm_cross_project_fork(getattr(ref, "cwd", "") or "")

    return resolve_startup_session(
        cwd,
        mode=mode,
        target=target,
        name=name,
        session_id=session_id if mode in ("fork", "session") else None,
        sessions_root=sessions_root,
        confirm_fork=confirm,
    )


def _resolve_startup_resume_mode(args: Any) -> tuple[str, str | None]:
    """Resolve the ``-r``/``--resume-session`` startup mode.

    On a real TTY this opens the interactive native-session picker; otherwise
    (captured/non-TTY) it deterministically continues the most recent native
    session, matching the documented fallback.
    """

    picked = _run_startup_resume_picker(args)
    if picked is not None:
        return "session", str(picked)
    return "continue", None


def _select_repl_app_mode(args: Any) -> str:
    """Select the effective ``pipy repl`` app mode.

    One-shot/RPC modes are selected explicitly: ``--mode rpc`` -> rpc,
    ``--mode json`` -> json full-event stream, ``--print``/``-p`` -> one-shot
    final text; otherwise the interactive REPL. This is a deliberate pipy
    boundary over Pi's ``resolveAppMode`` (encoded/tested as ``resolve_app_mode``
    in ``automation/run_modes.py``): pipy's REPL consumes piped (non-TTY) stdin
    as live prompts, so a bare non-TTY stdin stays interactive rather than
    becoming a one-shot, and a positional prompt alone is not enough to switch
    modes (the caller must pass ``--print`` or ``--mode json|rpc``).
    """

    if args.mode == "rpc":
        return "rpc"
    if args.mode == "json":
        return "json"
    if args.print_mode:
        return "print"
    return "interactive"


def _run_repl_automation(
    app_mode: str,
    args: Any,
    *,
    repl_adapter: Any,
    native_session: Any,
) -> int:
    """Drive a headless automation run for the resolved ``app_mode``."""

    from pipy_harness.native.automation.run_modes import (
        run_json_mode,
        run_print_mode,
    )
    from pipy_harness.native.session_tree import NativeSessionTree

    if not isinstance(repl_adapter, PipyNativeToolReplAdapter):
        raise ValueError(
            "--mode json/rpc and --print require a tool-capable native provider "
            "(the automation event stream is produced by the tool loop)"
        )
    cwd = args.cwd.expanduser().resolve()

    if app_mode == "rpc":
        from pipy_harness.native.automation.rpc import run_rpc_mode

        if args.prompt is not None:
            # RPC reads prompt commands on stdin; a positional prompt would be
            # silently ignored, so reject it rather than lose user input.
            raise ValueError(
                "--mode rpc does not accept a positional prompt; send a "
                '{"type":"prompt","message":"..."} command on stdin'
            )
        if native_session is None:
            native_session = NativeSessionTree.create(cwd, persist=False)
        return run_rpc_mode(
            adapter=repl_adapter,
            cwd=cwd,
            native_session=native_session,
            stdin=sys.stdin,
            stdout_buffer=sys.stdout.buffer,
            error_stream=sys.stderr,
        )

    if app_mode == "json":
        prompt = args.prompt
        if prompt is None and not sys.stdin.isatty():
            # Read stdin as the prompt without stripping its content (a multiline
            # prompt is preserved verbatim as a single turn); only reject when it
            # is blank.
            prompt = sys.stdin.read()
        if not prompt or not prompt.strip():
            raise ValueError("--mode json requires a prompt argument")
        if native_session is None:
            native_session = NativeSessionTree.create(cwd, persist=False)
        return run_json_mode(
            adapter=repl_adapter,
            prompt=prompt,
            cwd=cwd,
            native_session=native_session,
            stdout_buffer=sys.stdout.buffer,
            error_stream=sys.stderr,
        )

    # print
    prompt = args.prompt
    if prompt is None and not sys.stdin.isatty():
        prompt = sys.stdin.read()
    if not prompt or not prompt.strip():
        raise ValueError("--print requires a prompt argument")
    if native_session is not None:
        repl_adapter.native_session = native_session
    return run_print_mode(
        adapter=repl_adapter,
        prompt=prompt,
        cwd=cwd,
        stdout=sys.stdout,
        error_stream=sys.stderr,
    )


PIPY_READ_ROOTS_ENV = "PIPY_READ_ROOTS"
_AUTO_REFERENCE_ROOT_DOCS = (
    Path("docs") / "parity-criterion.md",
    Path("docs") / "pi-parity.md",
    Path("AGENTS.md"),
)


def _resource_options_from_args(args: Any) -> RuntimeResourceOptions:
    """Resolve Pi-shaped source-loading flags for one REPL run."""

    cwd = args.cwd.expanduser().resolve()
    return RuntimeResourceOptions(
        extension_paths=_resolve_cli_resource_paths(
            cwd, getattr(args, "extensions", None)
        ),
        skill_paths=_resolve_cli_resource_paths(cwd, getattr(args, "skills", None)),
        prompt_template_paths=_resolve_cli_resource_paths(
            cwd, getattr(args, "prompt_templates", None)
        ),
        theme_paths=_resolve_cli_resource_paths(cwd, getattr(args, "themes", None)),
        no_extensions=bool(getattr(args, "no_extensions", False)),
        no_skills=bool(getattr(args, "no_skills", False)),
        no_prompt_templates=bool(getattr(args, "no_prompt_templates", False)),
        no_themes=bool(getattr(args, "no_themes", False)),
    )


def _resolve_cli_resource_paths(
    cwd: Path,
    values: list[str] | None,
) -> tuple[Path, ...]:
    """Resolve repeated source-loading path flags relative to ``cwd``."""

    resolved: list[Path] = []
    for value in values or []:
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = cwd / path
        try:
            path = path.resolve(strict=False)
        except OSError:
            continue
        resolved.append(path)
    return tuple(resolved)


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


def _build_catalog_state(
    runtime_api_key: str | None = None,
    *,
    cwd: Path | None = None,
    settings_manager: SettingsManager | None = None,
    resource_options: RuntimeResourceOptions | None = None,
) -> ProviderCatalogState:
    """Build the shared provider/model catalog state for the REPL.

    Backs both ``/model`` selection and availability over the full catalog
    (built-in + models.json + ds4 env shim) with the same matcher and auth gate
    used by ``--list-models``. ``runtime_api_key`` is the ``--api-key`` override
    (highest auth priority; never archived).
    """

    state = ProviderCatalogState(
        auth_store=AuthStore(),
        openai_codex_auth_path=default_openai_codex_auth_path(),
        runtime_api_key=runtime_api_key,
    )
    if cwd is not None and settings_manager is not None:
        providers, unregistered = _extension_provider_contributions(
            cwd,
            settings_manager=settings_manager,
            resource_options=resource_options,
        )
        state.set_extension_provider_contributions(providers, unregistered)
    return state


def _validated_thinking_level(thinking: str | None) -> str | None:
    """Validate a ``--thinking`` value, warning (not failing) on invalid input."""

    if not thinking:
        return None
    from pipy_harness.native.thinking import validate_thinking_level

    level, warning = validate_thinking_level(thinking)
    if warning is not None:
        print(f"pipy: {warning}", file=sys.stderr)
    return level


def _handle_list_models(
    search: str | None,
    *,
    cwd: Path | None = None,
    settings_manager: SettingsManager | None = None,
    resource_options: RuntimeResourceOptions | None = None,
    api_key: str | None = None,
) -> int:
    """Print the available provider/models table and exit (Pi `--list-models`)."""

    state = _build_catalog_state(
        runtime_api_key=api_key,
        cwd=cwd,
        settings_manager=settings_manager,
        resource_options=resource_options,
    )
    output = format_list_models(
        state.get_available(), search=search, load_error=state.error
    )
    print(output)
    return 0


def _extension_provider_contributions(
    cwd: Path,
    *,
    settings_manager: SettingsManager,
    resource_options: RuntimeResourceOptions | None,
):
    options = resource_options or RuntimeResourceOptions.empty()
    package_roots = compose_package_runtime(
        settings_manager,
        cwd,
        install_theme_registry=False,
    )
    from pipy_harness.native.extension_provider_catalog import (
        extension_reserved_command_names,
        extension_reserved_tool_names,
        load_extension_provider_contributions,
    )
    from pipy_harness.native.resources import WorkspaceResources

    workspace_resources = WorkspaceResources.discover(
        cwd,
        package_roots=package_roots,
        explicit_skill_paths=options.skill_paths,
        explicit_prompt_template_paths=options.prompt_template_paths,
        include_skills_defaults=not options.no_skills,
        include_prompt_template_defaults=not options.no_prompt_templates,
    ).with_enablement(
        skills_patterns=settings_manager.get_skills_patterns(),
        prompts_patterns=settings_manager.get_prompts_patterns(),
        enable_skill_commands=settings_manager.get_enable_skill_commands(),
    )

    return load_extension_provider_contributions(
        cwd,
        package_roots=()
        if options.no_extensions
        else package_roots.extensions,
        extension_patterns=settings_manager.get_extensions_patterns(),
        explicit_extension_paths=options.extension_paths,
        include_default_extensions=not options.no_extensions,
        reserved_command_names=extension_reserved_command_names(
            workspace_resources.custom_command_slash_names()
        ),
        reserved_tool_names=extension_reserved_tool_names(),
    )


def _provider_factory_for(
    settings_manager: SettingsManager | None,
) -> NativeProviderFactory:
    """Return the provider factory, binding the settings-derived retry policy.

    The ``retry.*`` settings feed the provider HTTP retry policy. In normal REPL
    startup a settings manager is always present, so retry-aware providers (e.g.
    openai-codex) are built from the settings-derived ``RetryPolicy`` (its
    defaults honor the documented ``baseDelayMs``/``maxRetryDelayMs``). The
    ``None`` branch — provider keeps its built-in field default — is for direct
    or test callers that pass no settings manager.
    """

    if settings_manager is None:
        return _native_provider_for_selection
    policy = retry_policy_from_settings(settings_manager)

    def _factory(selection: NativeModelSelection) -> ProviderPort:
        return _native_provider_for_selection(selection, retry_policy=policy)

    return _factory


def _native_provider_for_selection(
    selection: NativeModelSelection,
    *,
    retry_policy: "RetryPolicy | None" = None,
) -> ProviderPort:
    if selection.provider_name == "openai":
        return OpenAIResponsesProvider(model_id=selection.model_id)
    if selection.provider_name == "openai-completions":
        from pipy_harness.native.openai_completions_provider import (
            OpenAIChatCompletionsProvider,
        )

        return OpenAIChatCompletionsProvider(model_id=selection.model_id)
    if selection.provider_name == "ds4":
        from pipy_harness.native.ds4_provider import Ds4ChatCompletionsProvider

        return Ds4ChatCompletionsProvider(model_id=selection.model_id)
    if selection.provider_name == "openai-codex":
        if retry_policy is not None:
            return OpenAICodexResponsesProvider(
                model_id=selection.model_id, retry_policy=retry_policy
            )
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
        if selection.model_id == AUTOMATION_FAKE_MODEL_ID:
            # Deterministic, tool-capable, streaming fake for the headless
            # automation surfaces and the conformance gate (offline).
            return AutomationFakeProvider(model_id=selection.model_id)
        return FakeNativeProvider(model_id=selection.model_id or DEFAULT_NATIVE_MODELS["fake"])
    raise ValueError(f"unsupported native provider: {selection.provider_name}")


if __name__ == "__main__":
    raise SystemExit(main())
