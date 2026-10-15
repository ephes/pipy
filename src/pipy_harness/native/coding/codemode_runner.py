"""CPython-on-WASI runner for the opt-in canonical composite tool."""

from __future__ import annotations

import base64
import json
import tempfile
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from time import monotonic

from pipy_harness.native.agent.content import (
    NO_STRUCTURED_CONTENT,
    ImageContent,
    ProductContent,
    TextContent,
    ToolResultBlock,
    tool_result_text,
)
from pipy_harness.native.agent.messages import AgentToolCall, AgentToolResultMessage
from pipy_harness.native.agent.nested_calls import (
    NestedToolCallOutcome,
    NestedToolCallService,
    tool_error_text,
)
from pipy_harness.native.agent.nested_status import NestedCallStatus
from pipy_harness.native.agent.tools import (
    ToolExecutionInterruption,
    ToolExecutionOutcome,
    ToolInterruptWaiter,
)
from pipy_harness.native.codemode.host import run_script
from pipy_harness.native.codemode.outcome import (
    NO_VALUE,
    CallResult,
    ErrorKind,
    ScriptError,
    ScriptImage,
    ScriptLimits,
    ScriptOutcome,
)
from pipy_harness.native.codemode.result import (
    CHARS_PER_TOKEN,
    DEFAULT_MAX_OUTPUT_TOKENS,
    ResultText,
    format_result,
    join_output,
)
from pipy_harness.native.codemode.runtime import CodemodePaths
from pipy_harness.native.codemode.source import (
    CodemodeSourceError,
    ParsedCodemodeSource,
    parse_codemode_source,
)
from pipy_harness.native.coding.codemode_description import (
    codemode_tool_guidelines,
)
from pipy_harness.native.coding.codemode_discovery import CodemodeDiscovery
from pipy_harness.native.coding.codemode_store import CodemodeStore
from pipy_harness.native.tools.base import (
    NestedToolResult,
    make_tool_request_id,
    validate_arguments,
)
from pipy_harness.native.tools.codemode import codemode_definition
from pipy_harness.native.tools.truncate import format_size

# A result's text is at most 64 KiB (``AgentToolResultMessage``); Pi has no
# such limit. The output budget leaves this much of it to the header, the
# truncation lines and the spill notice, so ``max_output_tokens`` acts as at
# most ``MAX_RESULT_OUTPUT_TOKENS``; the images' path lines take more.
_RESULT_TEXT_ROOM = 1536
MAX_RESULT_OUTPUT_TOKENS = (
    AgentToolResultMessage.CONTENT_MAX_LENGTH - _RESULT_TEXT_ROOM
) // CHARS_PER_TOKEN


def _spill_result(
    formatted: ResultText, max_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS
) -> tuple[str, dict[str, object]]:
    """Host-only optional spill; publish a path only after successful close."""
    body = formatted.full_output
    if body is None:
        return formatted.text, {}
    data = body.encode("utf-8")
    details: dict[str, object] = {
        "truncation": {
            "truncated": True,
            "truncatedBy": "tokens",
            "totalCharacters": len(body),
            "totalBytes": len(data),
            "maxTokens": max_tokens,
            "estimate": "characters/4",
        }
    }
    path: str | None = None
    try:
        # Like OutputAccumulator, keep an owner-only host temp file for reading.
        with tempfile.NamedTemporaryFile(
            prefix="pipy-codemode-", suffix=".log", delete=False
        ) as handle:
            path = handle.name
            if handle.write(data) != len(data):
                raise OSError("incomplete full-output write")
        details["fullOutputPath"] = path
        notice = f"Full output saved to {path} ({len(data)} bytes)."
    except OSError:
        if path is not None:
            try:
                Path(path).unlink(missing_ok=True)
            except OSError:
                pass
        notice = "Full output could not be saved; displayed output is truncated."
    return formatted.text + "\n\n" + notice, details


def _spilled_items(formatted: ResultText, text: str) -> tuple[str | ScriptImage, ...]:
    """The result's items once ``text`` names where its full text is.

    A truncated result is its one text item, then its images; the notice of
    the spill belongs to the text, before the images.
    """

    if formatted.full_output is None:
        return formatted.items
    images = [item for item in formatted.items if isinstance(item, ScriptImage)]
    return (text.removeprefix(formatted.header), *images)


# Pi ``IMAGE_EXTENSIONS``: every type the sandbox's ``image()`` detects.
IMAGE_EXTENSIONS = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
}

ImageSaver = Callable[[bytes, str], str]
"""``(image bytes, file extension) -> path`` of the new file; may raise."""


def save_image_file(data: bytes, extension: str) -> str:
    """Write an image to a new temp file only the user can read."""

    path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix="pipy-codemode-", suffix=extension, delete=False
        ) as handle:
            path = handle.name
            if handle.write(data) != len(data):
                raise OSError("incomplete image write")
    except OSError:
        if path is not None:
            try:
                Path(path).unlink(missing_ok=True)
            except OSError:
                pass
        raise
    return path


def _image_label(image: ScriptImage, save: ImageSaver) -> str:
    """Pi ``saveImages``: where the image went, or why it went nowhere."""

    data = base64.b64decode(image.data)
    kind = f"{image.mime_type}, {format_size(len(data))}"
    # A failed write (disk full, unwritable temp dir) must not discard the
    # result of a script whose tool calls already ran: it is the label.
    try:
        path = save(data, IMAGE_EXTENSIONS[image.mime_type])
    except Exception as error:  # noqa: BLE001 - whatever a save raises is the label
        reason = str(error) or type(error).__name__
        return f"[Image ({kind}) could not be saved: {reason}]"
    return f"[Image saved to {path} ({kind})]"


class SaveOnce:
    """An :data:`ImageSaver` that saves each image once and gives the same
    path (or raises the same error) when asked again, so a result's labels
    can be measured before the result is built."""

    def __init__(self, save: ImageSaver = save_image_file) -> None:
        self._save = save
        self._saved: dict[bytes, str | Exception] = {}

    def __call__(self, data: bytes, extension: str) -> str:
        if data not in self._saved:
            try:
                self._saved[data] = self._save(data, extension)
            except Exception as error:  # noqa: BLE001 - replayed to every caller
                self._saved[data] = error
        saved = self._saved[data]
        if isinstance(saved, Exception):
            raise saved
        return saved


def fitting_budget(outcome: ScriptOutcome, max_tokens: int, save: ImageSaver) -> int:
    """The output budget that keeps the result's text within its 64 KiB:
    ``max_tokens``, less what the images' path lines take."""

    labels = sum(
        len(_image_label(item, save)) + 1
        for item in outcome.output
        if isinstance(item, ScriptImage)
    )
    room = AgentToolResultMessage.CONTENT_MAX_LENGTH - _RESULT_TEXT_ROOM - labels
    return max(0, min(max_tokens, room // CHARS_PER_TOKEN))


def result_content(
    header: str,
    items: Sequence[str | ScriptImage],
    save: ImageSaver = save_image_file,
) -> tuple[str, tuple[ToolResultBlock, ...]]:
    """The text and the ordered blocks of a codemode result.

    Pi ``saveImages``: each image is saved to a temp file and a text line
    with its path precedes it, because the model has no other way to reach
    its bytes. An image shown more than once is saved once. Adjacent texts
    are one block, each part on its own line; the header leads the first.
    """

    labels: dict[str, str] = {}
    blocks: list[ToolResultBlock] = []
    run: list[str] = [header]

    for item in items:
        if isinstance(item, str):
            run.append(item)
            continue
        if item.data not in labels:
            labels[item.data] = _image_label(item, save)
        run.append(labels[item.data])
        blocks.append(TextContent(join_output(run)))
        blocks.append(ImageContent(item.data, item.mime_type))
        run = []
    if run:
        blocks.append(TextContent(join_output(run)))
    return tool_result_text(blocks), tuple(blocks)


_OPERATOR = frozenset(
    {ToolExecutionInterruption.OPERATOR_ABORT, ToolExecutionInterruption.LOCAL_COMMAND}
)


class _RunControl:
    """Fresh per parent; session thread only.

    ``stop`` is the host's ``tool_stop``: set when the script's run ended,
    it ends the calls still running. ``cancel`` ends the run as aborted.
    """

    def __init__(self, waiter: ToolInterruptWaiter | None) -> None:
        self.waiter = waiter
        self.cancel = threading.Event()
        self.stop = threading.Event()
        self.interruption = ToolExecutionInterruption.SETTLED

    def interrupted(self, interruption: ToolExecutionInterruption | None) -> None:
        """An operator interruption ends the run as aborted."""
        if interruption in _OPERATOR:
            assert interruption is not None
            self.interruption = interruption
            self.cancel.set()

    def wait(
        self, done: threading.Event, cancel: threading.Event
    ) -> ToolExecutionInterruption:
        """The session's wait, which Ctrl-C ends as an operator abort."""
        return self._wait(done, cancel)

    def _wait(
        self, done: threading.Event, cancel: threading.Event
    ) -> ToolExecutionInterruption:
        try:
            if self.waiter is None:
                done.wait()
                return ToolExecutionInterruption.SETTLED
            interruption = self.waiter(done, cancel)
            if type(interruption) is not ToolExecutionInterruption:
                raise TypeError(
                    "tool interrupt waiter must return ToolExecutionInterruption"
                )
            return interruption
        except KeyboardInterrupt:
            cancel.set()
            return ToolExecutionInterruption.OPERATOR_ABORT


@dataclass(frozen=True, slots=True)
class CodemodeCompositeRunner:
    """Optional product runner, using the canonical admitted-child service.

    ``store`` is the session's: scripts ``load()`` from it, and a script
    that succeeds keeps its ``store()`` writes there. Without it (Pi without
    a session) the store starts empty and writes last for the one script.
    """

    limits: ScriptLimits = ScriptLimits()
    paths: CodemodePaths | None = None
    store: CodemodeStore | None = None

    def _options(self, source: ParsedCodemodeSource) -> tuple[ScriptLimits, int]:
        """The limits and the output budget of a script with these options:
        ``timeout_ms`` is the deadline, ``max_output_tokens`` the budget
        (which :func:`fitting_budget` may lower)."""

        limits = self.limits
        if source.timeout_ms is not None:
            limits = replace(limits, wall_seconds=source.timeout_ms / 1000)
        if source.max_output_tokens is None:
            return limits, DEFAULT_MAX_OUTPUT_TOKENS
        return limits, source.max_output_tokens

    def execute(
        self,
        call: AgentToolCall,
        service: NestedToolCallService,
        tool_waiter: ToolInterruptWaiter | None,
        /,
    ) -> ToolExecutionOutcome:
        """Run the script; the outcome says how long it ran (Pi times the
        codemode tool's ``execute()``), unless its arguments were invalid."""
        began = monotonic()
        outcome = self._run(call, service, tool_waiter)
        if outcome.malformed_arguments:
            return outcome
        return replace(outcome, execution_seconds=monotonic() - began)

    def _run(
        self,
        call: AgentToolCall,
        service: NestedToolCallService,
        tool_waiter: ToolInterruptWaiter | None,
        /,
    ) -> ToolExecutionOutcome:
        def result(
            text: str,
            *,
            error: bool,
            malformed: bool = False,
            details: dict[str, object] | None = None,
            interruption: ToolExecutionInterruption = ToolExecutionInterruption.SETTLED,
            blocks: tuple[ToolResultBlock, ...] = (),
        ) -> ToolExecutionOutcome:
            return ToolExecutionOutcome(
                AgentToolResultMessage(
                    tool_request_id=make_tool_request_id(),
                    tool_name=call.tool_name,
                    content=ProductContent(text),
                    is_error=error,
                    provider_correlation_id=call.provider_correlation_id,
                    details={
                        **(details or {}),
                        "nestedCalls": service.durable_record(),
                    },
                    blocks=blocks,
                ),
                malformed_arguments=malformed,
                interruption=interruption,
            )

        try:
            arguments = validate_arguments(
                tool_name=call.tool_name,
                schema=codemode_definition().input_schema,
                arguments=json.loads(call.arguments_json.value),
            )
        except ValueError as exc:
            return result(str(exc), error=True, malformed=True)
        # Pi ``parseCodemodeSource``: an optional first line of options.
        try:
            source = parse_codemode_source(arguments["code"])
        except CodemodeSourceError as exc:
            return result(str(exc), error=True)
        limits, max_tokens = self._options(source)
        control = _RunControl(tool_waiter)
        # The script's calls run at the same time; each starts in the order
        # the script made it, and ``stop`` ends those still running.
        batch = service.batch(control.wait, control.stop)

        def start(
            name: str, arguments_json: str, settle: Callable[[CallResult], None]
        ) -> None:
            structured = service.declares_output_schema(name)

            def settled(
                answer: NestedToolResult, outcome: NestedToolCallOutcome | None
            ) -> None:
                if outcome is None:
                    # Refused before it got an id: the parent's calls ended.
                    settle(CallResult(ok=False, text=answer.content))
                    return
                control.interrupted(outcome.interruption)
                result = outcome.result
                # Pi ``toScriptValue``: a tool that declares an output schema
                # resolves to its structured content (``None`` too, which is
                # null), also for an error result that carries one; any other
                # tool resolves to its text, and a failure raises with the
                # tool's error text.
                value = result.structured_content
                settle(
                    CallResult(
                        ok=not result.is_error,
                        text=tool_error_text(name, result.content.value)
                        if result.is_error
                        else result.content.value,
                        cancelled=outcome.interruption is not None
                        or outcome.status is NestedCallStatus.CANCELLED,
                        value=value
                        if structured and value is not NO_STRUCTURED_CONTENT
                        else NO_VALUE,
                    )
                )

            batch.start(name, arguments_json, settled)

        def activity(done: threading.Event, cancel: threading.Event) -> None:
            control.interrupted(batch.pump(done, cancel))

        try:
            callable_tools = service.callable_tools()
            discovery = CodemodeDiscovery(
                callable_tools, guidelines=codemode_tool_guidelines(callable_tools)
            )
            outcome = run_script(
                source.code,
                start_tool=start,
                tools=discovery.script_tools(),
                discover=discovery,
                limits=limits,
                paths=self.paths,
                cancel=control.cancel,
                tool_stop=control.stop,
                activity_waiter=activity,
                store=self.store.values() if self.store is not None else None,
            )
            # Pi: the calls still running when the script ended are cancelled.
            batch.end(ToolExecutionInterruption.SCRIPT_STOP)
            # Pi: only a script that succeeded keeps what it stored.
            if outcome.ok and outcome.store_writes and self.store is not None:
                self.store.append(outcome.store_writes)
            save = SaveOnce()
            budget = fitting_budget(outcome, max_tokens, save)
            formatted = format_result(outcome, max_output_tokens=budget)
            text, details = _spill_result(formatted, budget)
            text, blocks = result_content(
                formatted.header, _spilled_items(formatted, text), save
            )
            return result(
                text,
                blocks=blocks,
                details=details,
                error=formatted.is_error,
                interruption=control.interruption,
            )
        except Exception as exc:  # noqa: BLE001 - canonical failures are retained by the service
            return result(
                format_result(
                    ScriptOutcome(
                        ScriptError(
                            ErrorKind.SANDBOX,
                            f"{type(exc).__name__}: {exc}",
                        )
                    )
                ).text,
                error=True,
                interruption=control.interruption,
            )
        finally:
            service.close()
