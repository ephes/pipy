"""Headless fake-host coverage for the extension coding-session port (6.3c).

An extension command and an extension hook are exercised end to end against
fake ``ExtensionCodingSessionControl`` (coding-session) and
``ExtensionModelRuntimeControl`` (model-runtime) ports and a plain in-memory
conversation snapshot — with no real terminal, no ``ToolLoopTerminalUi``, and no
concrete ``NativeToolReplSession`` product session. This proves the host ports
are sufficient to drive extension dispatch: everything the handler reaches
(``complete`` / ``append_entry`` / session-name / label / ``send_message`` /
``set_active_tools`` / the conversation view / the read-only session-manager
view) is satisfied by the two frozen ports plus an optional detached session
tree.

Deliberately imports neither ``tool_loop_session`` nor ``tui``.
"""

from __future__ import annotations

from pipy_harness.native.agent import (
    AgentAssistantMessage,
    AgentUserMessage,
    ProductContent,
)
from pipy_harness.native.extension_hooks import dispatch_input_hooks
from pipy_harness.native.extension_runtime import RegisteredCommand
from pipy_harness.native.extension_types import (
    ExtensionCodingSessionControl,
    ExtensionModelRuntimeControl,
    InputEvent,
    InputTransform,
)
from pipy_harness.native.extensions.command_context import ExtensionCapabilityError
from pipy_harness.native.extensions.dispatch import dispatch_extension_command
from pipy_harness.native.session_tree import NativeSessionTree


def test_command_runs_against_fake_coding_session_and_model_runtime_ports(
    tmp_path,
) -> None:
    """A command reaches every coding-session collaborator through the port."""

    calls: list[tuple[str, object]] = []

    tree = NativeSessionTree.create(tmp_path, persist=False)
    tree.append_session_info("headless-demo")

    def fake_append_entry(name: str, data: object | None) -> object:
        calls.append(("append", (name, data)))
        return "entry-1"

    def fake_set_name(name: str | None) -> object:
        calls.append(("set_name", name))
        return "name-1"

    def fake_set_label(entry_id: str, label: str | None) -> object:
        calls.append(("label", (entry_id, label)))
        return "label-1"

    def fake_send_message(ct, content, display, options, details) -> object:
        calls.append(("send", (ct, content, display)))
        return "msg-1"

    def fake_set_active_tools(names) -> bool:
        calls.append(("tools", tuple(names)))
        return True

    coding_session = ExtensionCodingSessionControl(
        complete_fn=lambda system, user: f"completed:{system}|{user}",
        append_entry_fn=fake_append_entry,
        set_session_name_fn=fake_set_name,
        get_session_name_fn=lambda: "headless-demo",
        set_label_fn=fake_set_label,
        send_message_fn=fake_send_message,
        session_tree=tree,
        messages=(
            AgentUserMessage(content=ProductContent("do the thing")),
            AgentAssistantMessage(content=ProductContent("Which database?")),
        ),
    )
    model_runtime = ExtensionModelRuntimeControl(
        set_active_tools_fn=fake_set_active_tools
    )

    seen: dict[str, object] = {}

    def handler(ctx, args):
        # Conversation snapshot backed by the port's ``messages``.
        message = ctx.conversation.last_assistant_message()
        seen["assistant"] = None if message is None else message.text
        # Read-only session-manager view backed by the port's ``session_tree``.
        seen["session_name_view"] = ctx.session_manager.get_session_name()
        # Coding-session capability callables.
        seen["completion"] = ctx.complete("SYS", args)
        seen["get_name"] = ctx.get_session_name()
        ctx.append_entry("note", {"body": args})
        ctx.set_session_name("renamed")
        ctx.set_label("e5", "starred")
        ctx.send_message({"customType": "note", "content": "hi"})
        # Model-runtime capability callable.
        seen["set_tools"] = ctx.set_active_tools(["read", "bash"])

    dispatch = dispatch_extension_command(
        "/probe payload",
        {"probe": RegisteredCommand("probe", "probe", handler, "headless")},
        cwd=str(tmp_path),
        has_ui=False,
        coding_session=coding_session,
        model_runtime=model_runtime,
    )

    assert dispatch is not None
    assert dispatch.ran is True
    assert dispatch.error is None
    assert seen["assistant"] == "Which database?"
    assert seen["session_name_view"] == "headless-demo"
    assert seen["completion"] == "completed:SYS|payload"
    assert seen["get_name"] == "headless-demo"
    assert seen["set_tools"] is True
    assert calls == [
        ("append", ("note", {"body": "payload"})),
        ("set_name", "renamed"),
        ("label", ("e5", "starred")),
        ("send", ("note", "hi", True)),
        ("tools", ("read", "bash")),
    ]


def test_command_capability_is_bounded_when_port_field_is_absent(tmp_path) -> None:
    """An unwired coding-session field fails closed, not with a crash."""

    def handler(ctx, args):
        ctx.complete("SYS", "x")

    dispatch = dispatch_extension_command(
        "/probe",
        {"probe": RegisteredCommand("probe", "probe", handler, "headless")},
        cwd=str(tmp_path),
        has_ui=False,
        coding_session=ExtensionCodingSessionControl(),
    )

    assert dispatch is not None
    assert dispatch.ran is False
    assert dispatch.error == ExtensionCapabilityError.__name__


def test_input_hook_runs_against_fake_model_runtime_port(tmp_path) -> None:
    """An input hook mutates text and reaches the model-runtime port headless."""

    tool_calls: list[tuple[str, ...]] = []

    def hook(event: InputEvent, ctx):
        ctx.set_active_tools(["read"])
        return InputTransform(text=event.text.upper())

    def fake_set_active_tools(names) -> bool:
        tool_calls.append(tuple(names))
        return True

    result = dispatch_input_hooks(
        [hook],
        "hello",
        cwd=str(tmp_path),
        has_ui=False,
        model_runtime=ExtensionModelRuntimeControl(
            set_active_tools_fn=fake_set_active_tools
        ),
    )

    assert result == "HELLO"
    assert tool_calls == [("read",)]
