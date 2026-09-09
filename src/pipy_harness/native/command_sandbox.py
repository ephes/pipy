"""Shared safe command-execution substrate for pipy-native.

This module owns the bounded no-shell execution helper used by the legacy
allowlisted verification boundary. The model-visible ``bash`` tool intentionally
uses a real shell for Pi parity and does not call this helper.

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
import selectors
import shlex
import shutil
import signal
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from dataclasses import field as dataclass_field
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
_OVERLONG_UPDATE_MARKER = "[output update suppressed: overlong line]"

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
_TERMINATION_GRACE_SECONDS = 0.1


class CommandStatus(StrEnum):
    """Terminal status for one substrate execution attempt."""

    COMPLETED = "completed"
    CANCELLED = "cancelled"
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

    This is the shared executor for fixed, caller-allowlisted commands. It owns cwd resolution and the
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


def _resolve_invocation(
    command: str,
    *,
    policy: CommandPolicy,
    workspace: Path,
    cwd: Path,
    safe_path: str,
) -> tuple[str, str, list[str]] | CommandRejectionReason:
    """Parse and confine a command string into an executable + arguments.

    Returns ``(program, resolved_exe, rest_args)`` when the command tokenizes to
    an allow-listed program that resolves to a binary outside the workspace and
    whose path operands all pass per-token policy; otherwise returns the first
    :class:`CommandRejectionReason` encountered. Pure preflight — never spawns.
    """

    try:
        argv = shlex.split(command, posix=True)
    except ValueError:
        return CommandRejectionReason.UNPARSEABLE_COMMAND
    if not argv:
        return CommandRejectionReason.EMPTY_COMMAND

    program = argv[0]
    if "/" in program or program not in policy.allowed_executables:
        return CommandRejectionReason.DISALLOWED_EXECUTABLE

    resolved_exe = _resolve_executable(
        program,
        safe_path=safe_path,
        workspace=workspace,
        refs=policy.reference_roots,
    )
    if resolved_exe is None:
        return CommandRejectionReason.DISALLOWED_EXECUTABLE

    # Per-token policy: command-specific recursion/write flags, .git/traversal/
    # symlink escapes, and directory operands (which enable listing/recursion).
    for token in argv[1:]:
        path_reason = _path_token_reason(token, cwd=cwd, refs=policy.reference_roots)
        if path_reason is not None:
            return path_reason

    return program, resolved_exe, argv[1:]


def run_command(
    command: str,
    policy: CommandPolicy,
    *,
    cwd_relative: str | None = None,
    runner: Callable[..., subprocess.CompletedProcess[Any]] = subprocess.run,
    cancel_event: threading.Event | None = None,
    output_callback: Callable[[str], None] | None = None,
) -> CommandResult:
    """Execute a single model-supplied command string through the sandbox.

    Returns a :class:`CommandResult`. A rejection (preflight or path policy)
    never spawns a process. A spawned process is confined to ``cwd_relative``
    and runs with a scrubbed environment. When ``cancel_event`` is supplied,
    cancellation and timeout terminate its complete process group, drain the
    available separate streams, and reap the direct child.
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

    # Resolve the executable against a workspace-filtered PATH and require it to
    # live outside the workspace / reference roots, so a model-planted binary
    # cannot be selected even when PATH is poisoned to include a workspace dir.
    safe_path = _safe_path(workspace, policy.reference_roots)
    resolution = _resolve_invocation(
        command, policy=policy, workspace=workspace, cwd=cwd, safe_path=safe_path
    )
    if isinstance(resolution, CommandRejectionReason):
        return CommandResult(status=CommandStatus.REJECTED, reason=resolution)
    program, resolved_exe, rest_args = resolution

    # The gate deliberately follows preflight: rejection remains a normal
    # policy result, while an abort that wins before Popen starts no child.
    if cancel_event is not None and cancel_event.is_set():
        return CommandResult(
            status=CommandStatus.CANCELLED,
            argv_program=program,
        )

    started = time.perf_counter()
    # ``runner`` remains a small compatibility/test seam for the older
    # synchronous callers. The cancellable RPC path always uses Popen below so
    # it can own group lifetime and observe its fresh event promptly.
    if (
        cancel_event is None
        and output_callback is None
        and runner is not subprocess.run
    ):
        return _run_with_runner(
            runner,
            resolved_exe=resolved_exe,
            rest_args=rest_args,
            cwd=cwd,
            safe_path=safe_path,
            policy=policy,
            program=program,
            started=started,
        )

    try:
        proc = subprocess.Popen(  # noqa: S603 - resolved + allowlisted, shell=False
            [resolved_exe, *rest_args],  # noqa: S603 - resolved + allowlisted, shell=False
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            env=_sandbox_env(cwd, safe_path),
            start_new_session=True,
        )
    except OSError:
        return CommandResult(
            status=CommandStatus.SPAWN_FAILED,
            duration_seconds=time.perf_counter() - started,
            argv_program=program,
        )

    stdout, stderr, out_truncated, err_truncated, outcome = _collect_process_output(
        proc,
        cancel_event=cancel_event,
        timeout_seconds=policy.timeout_seconds,
        max_output_bytes=policy.max_output_bytes,
        output_callback=output_callback,
    )
    duration = time.perf_counter() - started
    return CommandResult(
        status=outcome,
        exit_code=(
            int(proc.returncode) if outcome is CommandStatus.COMPLETED else None
        ),
        stdout=stdout,
        stderr=stderr,
        truncated=out_truncated or err_truncated,
        duration_seconds=duration,
        argv_program=program,
    )


def _run_with_runner(
    runner: Callable[..., subprocess.CompletedProcess[Any]],
    *,
    resolved_exe: str,
    rest_args: list[str],
    cwd: Path,
    safe_path: str,
    policy: CommandPolicy,
    program: str,
    started: float,
) -> CommandResult:
    """Preserve the legacy injectable synchronous runner contract."""

    try:
        completed = runner(
            [resolved_exe, *rest_args],  # noqa: S603 - resolved + allowlisted
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
    stdout, out_trunc = _shape_output(completed.stdout, policy.max_output_bytes)
    stderr, err_trunc = _shape_output(completed.stderr, policy.max_output_bytes)
    return CommandResult(
        status=CommandStatus.COMPLETED,
        exit_code=int(completed.returncode),
        stdout=stdout,
        stderr=stderr,
        truncated=out_trunc or err_trunc,
        duration_seconds=time.perf_counter() - started,
        argv_program=program,
    )


def _collect_process_output(
    proc: subprocess.Popen[bytes],
    *,
    cancel_event: threading.Event | None,
    timeout_seconds: float,
    max_output_bytes: int,
    output_callback: Callable[[str], None] | None,
) -> tuple[str, str, bool, bool, CommandStatus]:
    """Drain two pipes while enforcing cancellation/timeout process lifetime."""

    assert proc.stdout is not None
    assert proc.stderr is not None
    streams = (proc.stdout, proc.stderr)
    buffers = [_CapturedBytes(), _CapturedBytes()]
    gates = (
        _IncrementalOutputGate(max_output_bytes, output_callback),
        _IncrementalOutputGate(max_output_bytes, output_callback),
    )
    selector = selectors.DefaultSelector()
    for index, stream in enumerate(streams):
        selector.register(stream, selectors.EVENT_READ, index)

    deadline = time.monotonic() + timeout_seconds
    outcome = CommandStatus.COMPLETED
    stopped = False
    kill_deadline: float | None = None
    try:
        while True:
            # Poll every cycle rather than only after both pipes close: a
            # descendant can retain inherited descriptors after the direct
            # child exits, and the direct child must still be reaped promptly.
            direct_child_running = proc.poll() is None
            if not selector.get_map() and not direct_child_running:
                break
            if not stopped:
                requested = _requested_outcome(cancel_event, deadline)
                if requested is not None:
                    outcome = requested
                    _terminate_process_group(proc)
                    stopped = True
                    kill_deadline = time.monotonic() + _TERMINATION_GRACE_SECONDS

            _drain_selector(selector, buffers, gates)

            if kill_deadline is not None and time.monotonic() >= kill_deadline:
                # The direct child can exit while a same-group descendant keeps
                # its inherited pipes open. The grace is therefore independent
                # of ``proc.poll()``: pipe/group work still requires escalation.
                _kill_process_group(proc)
                kill_deadline = None
    finally:
        selector.close()
        # The direct child must be reaped even if a read descriptor faults.
        if proc.poll() is None:
            _kill_process_group(proc)
        proc.wait()

    stdout, stdout_truncated = _shape_captured_bytes(buffers[0], max_output_bytes)
    stderr, stderr_truncated = _shape_captured_bytes(buffers[1], max_output_bytes)
    return stdout, stderr, stdout_truncated, stderr_truncated, outcome


def _requested_outcome(
    cancel_event: threading.Event | None, deadline: float
) -> CommandStatus | None:
    if cancel_event is not None and cancel_event.is_set():
        return CommandStatus.CANCELLED
    if time.monotonic() >= deadline:
        return CommandStatus.TIMED_OUT
    return None


def _drain_selector(
    selector: selectors.BaseSelector,
    buffers: list["_CapturedBytes"],
    gates: tuple["_IncrementalOutputGate", "_IncrementalOutputGate"],
) -> None:
    for key, _ in selector.select(timeout=0.02):
        try:
            data = os.read(key.fd, 64 * 1024)
        except OSError:
            # Read faults are not an EOF proof for an unterminated prefix.
            selector.unregister(key.fileobj)
            continue
        if data:
            buffers[key.data].append(data)
            gates[key.data].feed(data)
        else:
            # Only a real zero-byte ``os.read`` is EOF for this exact stream.
            # A direct-child exit, a short read, or termination request does not
            # make an unterminated prefix eligible for publication.
            gates[key.data].finish_eof()
            selector.unregister(key.fileobj)


@dataclass(slots=True)
class _CapturedBytes:
    data: bytearray = dataclass_field(default_factory=bytearray)

    def append(self, chunk: bytes) -> None:
        self.data.extend(chunk)


@dataclass(slots=True)
class _IncrementalOutputGate:
    """Line-gate safe callback output for one captured byte stream.

    Terminal output intentionally keeps its whole-stream redaction semantics.
    This stricter projection never exposes a byte until a complete LF record
    (or the exact stream's EOF fragment) has been classified.  The gate is
    deliberately synchronous and tiny: its callback is required to be a
    nonblocking admission seam owned by the caller.
    """

    max_output_bytes: int
    callback: Callable[[str], None] | None
    _pending: bytearray = dataclass_field(default_factory=bytearray)
    _emitted_bytes: int = 0
    _exhausted: bool = False

    def feed(self, data: bytes) -> None:
        """Accept one raw read without exposing a partial/unclassified prefix."""

        if self._exhausted:
            return
        for byte in data:
            self._pending.append(byte)
            if byte == ord("\n"):
                record = bytes(self._pending)
                self._pending.clear()
                self._commit(record)
                if self._exhausted:
                    return
            elif len(self._pending) > self.max_output_bytes:
                # This is mutually exclusive with the ordinary truncation
                # marker.  Do not decode or expose even a safe-looking prefix.
                self._publish(_OVERLONG_UPDATE_MARKER)
                self._exhausted = True
                self._pending.clear()
                return

    def finish_eof(self) -> None:
        """Classify the final unterminated fragment after this stream's EOF."""

        if self._exhausted or not self._pending:
            return
        record = bytes(self._pending)
        self._pending.clear()
        self._commit(record)

    def _commit(self, record: bytes) -> None:
        if self._exhausted:
            return
        text = record.decode("utf-8", "replace")
        if has_secret_shaped_content(text):
            self._commit_secret(record.endswith(b"\n"))
            return
        self._commit_safe(text)

    def _commit_secret(self, source_has_lf: bool) -> None:
        marker_bytes = len(_SECRET_REDACTION_MARKER.encode("utf-8"))
        remaining = self.max_output_bytes - self._emitted_bytes
        if marker_bytes > remaining:
            self._exhausted = True
            return
        delta = _SECRET_REDACTION_MARKER
        if source_has_lf and marker_bytes + 1 <= remaining:
            delta += "\n"
        self._publish(delta)
        self._emitted_bytes += len(delta.encode("utf-8"))

    def _commit_safe(self, text: str) -> None:
        remaining = self.max_output_bytes - self._emitted_bytes
        payload_bytes = text.encode("utf-8")
        if len(payload_bytes) <= remaining:
            self._publish(text)
            self._emitted_bytes += len(payload_bytes)
            return
        # The text has already passed complete-record classification.  Trim
        # only at UTF-8 code-point boundaries, then append the one permitted
        # over-budget exhaustion marker.
        clipped = payload_bytes[:remaining].decode("utf-8", "ignore")
        self._publish(clipped + TRUNCATION_MARKER)
        self._emitted_bytes += len(clipped.encode("utf-8"))
        self._exhausted = True

    def _publish(self, delta: str) -> None:
        if self.callback is not None:
            self.callback(delta)


def _shape_captured_bytes(
    buffer: _CapturedBytes, max_output_bytes: int
) -> tuple[str, bool]:
    # Redact the complete stream before applying the returned-result cap. A
    # secret's decisive suffix can arrive in a later read chunk or after the
    # visible prefix, and a partial value must never escape that boundary.
    return _shape_output(
        bytes(buffer.data).decode("utf-8", "replace"), max_output_bytes
    )


def _terminate_process_group(proc: subprocess.Popen[bytes]) -> None:
    """Ask every member of the new child session to terminate."""

    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass


def _kill_process_group(proc: subprocess.Popen[bytes]) -> None:
    """Reap a stubborn direct child and any descendants in its process group."""

    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


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
