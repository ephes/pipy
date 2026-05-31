"""Shared safe command-execution substrate for pipy-native.

This module owns the single boundary through which pipy-native runs local
processes. It is reused by the model-visible ``bash`` tool
(:mod:`pipy_harness.native.tools.bash`) and by the allowlisted verification
boundary (:mod:`pipy_harness.native.verification`). Nothing else should call
``subprocess`` directly to execute a feature command.

The substrate owns, in one place:

- **cwd resolution** — the working directory is resolved and confined to the
  workspace (or a workspace-relative subdirectory); escapes are refused.
- **environment policy** — the child process receives a minimal, scrubbed
  environment so credentials in the parent environment never reach the
  subprocess or its output.
- **string preflight** — shell metacharacters (command substitution, pipes,
  redirects, globbing, brace/tilde expansion, chaining) are refused before a
  process is spawned, and the command is parsed with :func:`shlex.split` and
  executed with ``shell=False`` so a shell never interprets it.
- **executable policy** — ``argv[0]`` must be a bare name in an allowlist of
  safe, non-interpreter, non-network, read-only inspection commands.
  Interpreters (``python``, ``sh`` ...), network tools, and directory listers
  (``ls``) are intentionally excluded. The program is then resolved against a
  PATH that excludes the workspace and reference roots, and the resolved binary
  must live outside them, so a model-planted binary cannot be executed even if
  PATH is poisoned to include a workspace directory.
- **owner/workspace path policy** — every path-shaped argument is resolved and
  refused if it traverses out of the workspace, points through a symlink that
  escapes the workspace, or lands under ``.git``/generated directories. This
  is the ``.git`` default-deny enforced at execution-resolution time, not just
  by the string blocklist. Directory operands (including ``.``) are refused so
  an allowed command cannot list/recurse the tree (and reach ``.git``) without
  naming a vettable file. The shell stays read-only because the allowlist holds
  only commands that read named files (or stdin) and write to stdout — none can
  write a file or spawn a helper through a flag, so mutation/spawn-capable
  commands (``sort``, ``uniq``, interpreters) are excluded outright rather than
  flag-filtered. Mutation is the edit/write tools' surface.
- **timeout/kill behavior** — long-running commands are killed at a deadline.
- **bounded stdout/stderr capture** — output is capped and a stable truncation
  marker is appended.
- **safe diagnostic shaping** — secret-shaped output is redacted, and the only
  metadata exposed is the executable basename (never the full argument vector).

Standard library only; no new runtime dependencies.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Any

from pipy_harness.native.read_only_tool import (
    _is_ignored_or_generated,
    _is_relative_to,
    has_secret_shaped_content,
    resolve_tool_path,
)

TRUNCATION_MARKER = "... (truncated)"

_SECRET_REDACTION_MARKER = "[redacted: secret-shaped content]"

# Characters that imply shell behavior pipy never wants a feature command to
# trigger: command substitution, expansion, globbing, redirection, chaining.
# The command is executed with ``shell=False`` regardless, so even a quoted
# metacharacter cannot reach a shell; refusing them at the string level keeps
# the surface honest and the failure mode obvious to the model.
_SHELL_METACHARACTERS = frozenset(";&|<>$`(){}*?[]!~\\")

# Conservative, non-interpreter, non-network, read-only inspection commands.
# File operands for every entry are explicit argv tokens that the path policy
# vets, and none traverse the tree implicitly (directory operands and recursive
# search are refused at runtime). Interpreters, network tools, and directory
# listers (`ls`) are deliberately absent — directory listing/search go through
# the dedicated `ls`/`grep`/`find` tools, which enforce their own containment.
# The standalone allowlist keeps this a bounded inspection shell, not a
# permissive wrapper.
_DEFAULT_ALLOWED_EXECUTABLES = frozenset(
    {
        "basename",
        "cat",
        "cmp",
        "comm",
        "cut",
        "date",
        "diff",
        "dirname",
        "echo",
        "false",
        "fold",
        "head",
        "nl",
        "od",
        "pwd",
        "rev",
        "stat",
        "tail",
        "tr",
        "true",
    }
)
# Deliberately excluded from the read-only inspection set even though they look
# harmless. Each can escape the inspection contract through its own options, in
# ways argv inspection cannot robustly contain without an ever-growing
# per-option denylist:
#   - `grep`/`rg` recurse the cwd (`-r`/`-R`/`-rn`/`-d recurse`/`--recursive`,
#     or no path at all);
#   - `ls` lists directories;
#   - `uniq` writes its optional second positional operand;
#   - `sort` writes (`-o`/`--output`/`--temporary-directory`) and even spawns a
#     helper program (`--compress-program=sh`);
#   - `sha1sum`/`sha256sum`/`cksum` in check mode (`-c`/`--check`) read a
#     manifest and open every path it names;
#   - `wc --files0-from=F` (GNU/coreutils) reads its input paths from inside
#     `F`, so a vetted manifest operand smuggles unvetted paths.
# The last three share one failure mode: a path-shaped operand that argv
# inspection vets, whose *contents* then name further paths (for example
# `.git/config`) the policy never sees. Recursive/literal search and listing
# live in the dedicated `grep`/`ls`/`find` tools; mutation lives in the
# edit/write tools. Every command that remains in the allowlist reads only the
# named file operands the path policy vets (or stdin = DEVNULL) and writes only
# to stdout — none writes a file, spawns a helper, or opens a path named in
# another file's contents — so the allowlist itself is the boundary, not a
# per-flag denylist.

# Environment variables forwarded to the child. Everything else (tokens, keys,
# cloud credentials, etc.) is dropped so it can neither influence the command
# nor leak into captured output.
_ALLOWED_ENV_PASSTHROUGH = ("PATH", "LANG", "TZ", "TERM")

_DEFAULT_PATH = "/usr/local/bin:/usr/bin:/bin"

_HARD_MAX_OUTPUT_BYTES = 256 * 1024
_HARD_MAX_TIMEOUT_SECONDS = 600.0


class CommandStatus(StrEnum):
    """Terminal status for one substrate execution attempt."""

    COMPLETED = "completed"
    REJECTED = "rejected"
    TIMED_OUT = "timed-out"
    SPAWN_FAILED = "spawn-failed"


class CommandRejectionReason(StrEnum):
    """Closed safe labels for why a command was refused before/at execution."""

    EMPTY_COMMAND = "empty_command"
    CONTROL_CHARACTERS = "control_characters"
    SHELL_METACHARACTERS = "shell_metacharacters"
    UNPARSEABLE_COMMAND = "unparseable_command"
    DISALLOWED_EXECUTABLE = "disallowed_executable"
    UNSAFE_PATH_ARGUMENT = "unsafe_path_argument"
    DIRECTORY_OPERAND = "directory_operand"
    UNSAFE_CWD = "unsafe_cwd"


@dataclass(frozen=True, slots=True)
class CommandPolicy:
    """Execution policy for one substrate invocation.

    ``workspace_root`` must be an absolute directory. ``reference_roots`` are
    additional absolute read roots (for example a sibling repo added with
    ``--read-root``) against which absolute path arguments may resolve.
    """

    workspace_root: Path
    reference_roots: tuple[Path, ...] = ()
    timeout_seconds: float = 30.0
    max_output_bytes: int = 32 * 1024
    allowed_executables: frozenset[str] = _DEFAULT_ALLOWED_EXECUTABLES

    def __post_init__(self) -> None:
        if not isinstance(self.workspace_root, Path):
            raise ValueError("CommandPolicy.workspace_root must be a Path")
        if not self.workspace_root.is_absolute():
            raise ValueError("CommandPolicy.workspace_root must be absolute")
        if not isinstance(self.reference_roots, tuple):
            raise ValueError("CommandPolicy.reference_roots must be a tuple")
        for root in self.reference_roots:
            if not isinstance(root, Path) or not root.is_absolute():
                raise ValueError(
                    "CommandPolicy.reference_roots entries must be absolute Paths"
                )
        if (
            isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, (int, float))
            or self.timeout_seconds <= 0
            or self.timeout_seconds > _HARD_MAX_TIMEOUT_SECONDS
        ):
            raise ValueError(
                f"CommandPolicy.timeout_seconds must be in (0, {_HARD_MAX_TIMEOUT_SECONDS}]"
            )
        if (
            isinstance(self.max_output_bytes, bool)
            or not isinstance(self.max_output_bytes, int)
            or self.max_output_bytes < 1
            or self.max_output_bytes > _HARD_MAX_OUTPUT_BYTES
        ):
            raise ValueError(
                f"CommandPolicy.max_output_bytes must be in [1, {_HARD_MAX_OUTPUT_BYTES}]"
            )
        if not isinstance(self.allowed_executables, frozenset):
            raise ValueError("CommandPolicy.allowed_executables must be a frozenset")


@dataclass(frozen=True, slots=True)
class CommandResult:
    """Provider-visible result of one substrate execution.

    ``stdout``/``stderr`` are bounded and secret-redacted and are intended to
    be returned to the model by the calling tool. They are never archived; the
    archive boundary remains metadata-only. ``argv_program`` is the executable
    basename only — a safe label suitable for metadata — never the full args.
    """

    status: CommandStatus
    reason: CommandRejectionReason | None = None
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    truncated: bool = False
    duration_seconds: float = 0.0
    argv_program: str | None = None


def execute_allowlisted_argv(
    argv: tuple[str, ...],
    *,
    cwd: Path,
    runner: Callable[..., subprocess.CompletedProcess[Any]] = subprocess.run,
    timeout_seconds: float | None = None,
) -> subprocess.CompletedProcess[Any]:
    """Run a fixed, caller-allowlisted ``argv`` with no captured output.

    This is the shared executor for the verification boundary
    (``/verify just-check``). It owns cwd resolution and the
    stdin/stdout/stderr ``DEVNULL`` discipline so command output never reaches
    an archive, while leaving the exact allowlist (the hardcoded argv) to the
    caller. ``shell=False`` is implied because ``argv`` is a sequence.

    A ``timeout`` keyword is forwarded only when ``timeout_seconds`` is given,
    so callers that want the previous unbounded behavior get a byte-identical
    call shape.
    """

    kwargs: dict[str, Any] = {
        "cwd": cwd.resolve(),
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "check": False,
    }
    if timeout_seconds is not None:
        kwargs["timeout"] = timeout_seconds
    return runner(argv, **kwargs)


def run_command(
    command: str,
    policy: CommandPolicy,
    *,
    cwd_relative: str | None = None,
    runner: Callable[..., subprocess.CompletedProcess[Any]] = subprocess.run,
) -> CommandResult:
    """Execute a single model-supplied command string through the sandbox.

    Returns a :class:`CommandResult`. A rejection (preflight or path policy)
    never spawns a process. A spawned process is confined to ``cwd_relative``
    (resolved under the workspace), runs with a scrubbed environment, is killed
    at the policy deadline, and has its output bounded and secret-redacted.
    """

    workspace = policy.workspace_root.resolve()

    # Resolve and confine the working directory.
    try:
        cwd = _resolve_cwd(
            cwd_relative, workspace=workspace, refs=policy.reference_roots
        )
    except ValueError:
        return CommandResult(
            status=CommandStatus.REJECTED,
            reason=CommandRejectionReason.UNSAFE_CWD,
        )

    # String preflight.
    reason = _preflight_reason(command)
    if reason is not None:
        return CommandResult(status=CommandStatus.REJECTED, reason=reason)

    try:
        argv = shlex.split(command, posix=True)
    except ValueError:
        return CommandResult(
            status=CommandStatus.REJECTED,
            reason=CommandRejectionReason.UNPARSEABLE_COMMAND,
        )
    if not argv:
        return CommandResult(
            status=CommandStatus.REJECTED,
            reason=CommandRejectionReason.EMPTY_COMMAND,
        )

    program = argv[0]
    if "/" in program or program not in policy.allowed_executables:
        return CommandResult(
            status=CommandStatus.REJECTED,
            reason=CommandRejectionReason.DISALLOWED_EXECUTABLE,
        )

    # Resolve the executable against a workspace-filtered PATH and require it to
    # live outside the workspace / reference roots, so a model-planted binary
    # cannot be selected even when PATH is poisoned to include a workspace dir.
    safe_path = _safe_path(workspace, policy.reference_roots)
    resolved_exe = _resolve_executable(
        program,
        safe_path=safe_path,
        workspace=workspace,
        refs=policy.reference_roots,
    )
    if resolved_exe is None:
        return CommandResult(
            status=CommandStatus.REJECTED,
            reason=CommandRejectionReason.DISALLOWED_EXECUTABLE,
        )

    # Per-token policy: command-specific recursion/write flags, .git/traversal/
    # symlink escapes, and directory operands (which enable listing/recursion).
    for token in argv[1:]:
        path_reason = _path_token_reason(
            token, cwd=cwd, refs=policy.reference_roots
        )
        if path_reason is not None:
            return CommandResult(status=CommandStatus.REJECTED, reason=path_reason)

    started = time.perf_counter()
    try:
        completed = runner(
            [resolved_exe, *argv[1:]],  # noqa: S603 - resolved + allowlisted, shell=False
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=policy.timeout_seconds,
            check=False,
            shell=False,
            env=_sandbox_env(cwd, safe_path),
        )
    except subprocess.TimeoutExpired:
        return CommandResult(
            status=CommandStatus.TIMED_OUT,
            duration_seconds=time.perf_counter() - started,
            argv_program=program,
        )
    except OSError:
        return CommandResult(
            status=CommandStatus.SPAWN_FAILED,
            duration_seconds=time.perf_counter() - started,
            argv_program=program,
        )

    duration = time.perf_counter() - started
    stdout, out_trunc = _shape_output(completed.stdout, policy.max_output_bytes)
    stderr, err_trunc = _shape_output(completed.stderr, policy.max_output_bytes)
    return CommandResult(
        status=CommandStatus.COMPLETED,
        exit_code=int(completed.returncode),
        stdout=stdout,
        stderr=stderr,
        truncated=out_trunc or err_trunc,
        duration_seconds=duration,
        argv_program=program,
    )


def _resolve_cwd(
    cwd_relative: str | None, *, workspace: Path, refs: tuple[Path, ...]
) -> Path:
    if cwd_relative is None or cwd_relative in {"", "."}:
        return workspace
    resolved = resolve_tool_path(
        cwd_relative, workspace_root=workspace, reference_roots=refs
    )
    if not resolved.resolved.is_dir():
        raise ValueError("cwd must be an existing directory")
    if _is_ignored_or_generated(resolved.relative_label, resolved.root):
        raise ValueError("cwd must not be under .git or a generated directory")
    return resolved.resolved


def _preflight_reason(command: str) -> CommandRejectionReason | None:
    if not isinstance(command, str):
        return CommandRejectionReason.EMPTY_COMMAND
    if not command.strip():
        return CommandRejectionReason.EMPTY_COMMAND
    if "\x00" in command:
        return CommandRejectionReason.CONTROL_CHARACTERS
    if any(ord(char) < 32 for char in command):
        return CommandRejectionReason.CONTROL_CHARACTERS
    if any(char in _SHELL_METACHARACTERS for char in command):
        return CommandRejectionReason.SHELL_METACHARACTERS
    return None


def _path_token_reason(
    token: str, *, cwd: Path, refs: tuple[Path, ...]
) -> CommandRejectionReason | None:
    """Vet one argv token that might name a filesystem path.

    Pure flags (``-n``, ``-la``) are skipped. A flag that glues a path
    (``--file=PATH`` or anything starting with ``-`` that contains ``/``) is
    inspected so it cannot smuggle a target past the path policy. Commands that
    can write a file or spawn a helper through a flag are excluded from the
    allowlist entirely, so no per-command flag handling is needed here.
    """

    if token in {"-", "--"}:
        return None
    if token.startswith("-"):
        if "/" in token or "~" in token:
            # A glued flag+path (for example ``-f.git/config``) must not bypass
            # the policy; refuse it outright.
            return CommandRejectionReason.UNSAFE_PATH_ARGUMENT
        if token.startswith("--") and "=" in token:
            return _check_path_value(token.split("=", 1)[1], cwd=cwd, refs=refs)
        return None
    return _check_path_value(token, cwd=cwd, refs=refs)


def _check_path_value(
    value: str, *, cwd: Path, refs: tuple[Path, ...]
) -> CommandRejectionReason | None:
    if value == "":
        return None
    looks_pathish = (
        "/" in value
        or "~" in value
        or value in {".", ".."}
        or ".." in PurePosixPath(value).parts
    )
    if not looks_pathish:
        candidate = cwd / value
        if not (candidate.exists() or candidate.is_symlink()):
            return None
    try:
        resolved = resolve_tool_path(value, workspace_root=cwd, reference_roots=refs)
    except ValueError:
        # Includes `.`/`..`/traversal/shellish — all unsafe operands.
        return CommandRejectionReason.UNSAFE_PATH_ARGUMENT
    if _is_ignored_or_generated(resolved.relative_label, resolved.root):
        return CommandRejectionReason.UNSAFE_PATH_ARGUMENT
    # A directory operand lets an allowed command list/recurse the tree (and
    # reach `.git`) without naming a file we can vet; only regular files are
    # legal operands. Non-existent operands are harmless (the command errors).
    if resolved.resolved.is_dir():
        return CommandRejectionReason.DIRECTORY_OPERAND
    return None


def _safe_path(workspace: Path, refs: tuple[Path, ...]) -> str:
    """Return a PATH string excluding the workspace, reference roots, and any
    relative entries, so a model-planted binary cannot be resolved."""

    blocked = [workspace.resolve(), *(root.resolve() for root in refs)]
    entries: list[str] = []
    for raw in os.environ.get("PATH", _DEFAULT_PATH).split(os.pathsep):
        if not raw:
            continue
        candidate = Path(raw)
        if not candidate.is_absolute():
            continue
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if any(_is_relative_to(resolved, root) for root in blocked):
            continue
        entries.append(str(resolved))
    if not entries:
        entries = _DEFAULT_PATH.split(os.pathsep)
    return os.pathsep.join(entries)


def _resolve_executable(
    program: str,
    *,
    safe_path: str,
    workspace: Path,
    refs: tuple[Path, ...],
) -> str | None:
    """Resolve ``program`` to an absolute binary on ``safe_path`` that lives
    outside the workspace and reference roots, or return None to refuse it."""

    located = shutil.which(program, path=safe_path)
    if located is None:
        return None
    try:
        resolved = Path(located).resolve()
    except OSError:
        return None
    blocked = [workspace.resolve(), *(root.resolve() for root in refs)]
    if any(_is_relative_to(resolved, root) for root in blocked):
        return None
    return str(resolved)


def _sandbox_env(cwd: Path, path: str) -> dict[str, str]:
    env: dict[str, str] = {
        "PATH": path or _DEFAULT_PATH,
        "HOME": str(cwd),
        "PWD": str(cwd),
        "TMPDIR": str(cwd),
        "LC_ALL": "C",
    }
    for name in _ALLOWED_ENV_PASSTHROUGH:
        value = os.environ.get(name)
        if value is not None and name not in env:
            env[name] = value
    return env


def _shape_output(text: str | None, max_bytes: int) -> tuple[str, bool]:
    if not text:
        return "", False
    redacted = _redact_secret_lines(text)
    encoded = redacted.encode("utf-8")
    if len(encoded) <= max_bytes:
        return redacted, False
    clipped = encoded[:max_bytes].decode("utf-8", "ignore")
    return clipped + "\n" + TRUNCATION_MARKER, True


def _redact_secret_lines(text: str) -> str:
    lines = text.split("\n")
    out: list[str] = []
    for line in lines:
        if line and has_secret_shaped_content(line):
            out.append(_SECRET_REDACTION_MARKER)
        else:
            out.append(line)
    return "\n".join(out)


__all__ = [
    "TRUNCATION_MARKER",
    "CommandPolicy",
    "CommandRejectionReason",
    "CommandResult",
    "CommandStatus",
    "execute_allowlisted_argv",
    "run_command",
]
