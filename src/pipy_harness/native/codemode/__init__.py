"""Python codemode sandbox: CPython-on-WASI under wasmtime (CM1).

This package is standalone: it does not import the agent loop, tools or UI.
The host side never imports ``wasmtime``; only :mod:`.worker`, run as a
separate process, does. :func:`run_script` (:mod:`.host`) runs one script in a
fresh worker and serves its tool calls through an injected callback. See ``docs/specs/2026-10-06-python-codemode-spike.md``.
"""

from pipy_harness.native.codemode.host import ToolCallback, run_script
from pipy_harness.native.codemode.outcome import (
    CallResult,
    CallStatus,
    ErrorKind,
    ScriptError,
    ScriptLimits,
    ScriptOutcome,
    ToolCallRecord,
)
from pipy_harness.native.codemode.result import ResultText, format_result
from pipy_harness.native.codemode.runtime import (
    RUNTIME_PIN,
    CodemodePaths,
    CodemodeRuntimeError,
    InstallResult,
    RuntimePin,
    default_paths,
    install_runtime,
    verify_runtime,
)
from pipy_harness.native.codemode.selftest import Availability, availability

__all__ = [
    "RUNTIME_PIN",
    "Availability",
    "CallResult",
    "CallStatus",
    "CodemodePaths",
    "CodemodeRuntimeError",
    "ErrorKind",
    "InstallResult",
    "ResultText",
    "RuntimePin",
    "ScriptError",
    "ScriptLimits",
    "ScriptOutcome",
    "ToolCallRecord",
    "ToolCallback",
    "availability",
    "default_paths",
    "format_result",
    "install_runtime",
    "run_script",
    "verify_runtime",
]
