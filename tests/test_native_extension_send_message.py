from pipy_harness.extensions import coerce_custom_message
from pipy_harness.native.extension_runtime import (
    ExtensionCapabilityError,
    RegisteredCommand,
    dispatch_extension_command,
)


def test_coerce_custom_message_accepts_pi_shape_and_aliases():
    msg = coerce_custom_message(
        {"customType": "note", "content": 123, "display": False, "details": {"x": 1}},
        {"triggerTurn": True, "deliverAs": "nextTurn"},
    )
    assert msg.custom_type == "note"
    assert msg.content == "123"
    assert msg.display is False
    assert msg.details == {"x": 1}
    assert msg.options == {"triggerTurn": True, "deliverAs": "nextTurn"}

    snake = coerce_custom_message({"custom_type": "note-2", "content": "ok"})
    assert snake.custom_type == "note-2"
    assert snake.display is True


def test_coerce_custom_message_rejects_invalid_type():
    try:
        coerce_custom_message({"customType": "../bad", "content": "x"})
    except ValueError as exc:
        assert "invalid" in str(exc)
    else:  # pragma: no cover - assertion path
        raise AssertionError("expected ValueError")


def test_command_context_send_message_aliases_append_through_bound_writer(tmp_path):
    calls = []

    def handler(ctx, args):
        assert ctx.send_message({"customType": "note", "content": args}) == "e1"
        assert ctx.sendMessage({"customType": "note", "content": "hidden", "display": False}) == "e2"

    result = dispatch_extension_command(
        "/emit hello",
        {"emit": RegisteredCommand("emit", "emit", handler, "ext")},
        cwd=str(tmp_path),
        has_ui=False,
        send_message_fn=lambda custom_type, content, display, options, details: calls.append(
            (custom_type, content, display, dict(options), details)
        )
        or f"e{len(calls)}",
    )

    assert result is not None and result.ran
    assert calls == [
        ("note", "hello", True, {}, None),
        ("note", "hidden", False, {}, None),
    ]


def test_command_context_send_message_unavailable_is_bounded(tmp_path):
    def handler(ctx, args):
        try:
            ctx.send_message({"customType": "note", "content": "x"})
        except ExtensionCapabilityError:
            raise

    result = dispatch_extension_command(
        "/emit",
        {"emit": RegisteredCommand("emit", "emit", handler, "ext")},
        cwd=str(tmp_path),
        has_ui=False,
    )

    assert result is not None
    assert result.ran is False
    assert result.error == "ExtensionCapabilityError"


def test_activation_api_stages_custom_messages_only_on_success(tmp_path):
    from pipy_harness.native.extension_runtime import activate_extensions
    from pipy_harness.native.extensions import ExtensionDescriptor

    good = tmp_path / "good.py"
    good.write_text(
        "def activate(api):\n"
        "    api.send_message({'customType': 'note', 'content': 'boot'})\n"
        "    api.sendMessage({'customType': 'note', 'content': 'hidden', 'display': False})\n"
    )
    bad = tmp_path / "bad.py"
    bad.write_text(
        "def activate(api):\n"
        "    api.send_message({'customType': 'note', 'content': 'leak'})\n"
        "    raise RuntimeError('boom')\n"
    )

    activated = activate_extensions(
        [
            ExtensionDescriptor("good", "", "0.1", "", "workspace", "single_file", "good.py", "good", "activate", "good.py", {}, False, "loadable", None, "", 0, entry_path=str(good)),
            ExtensionDescriptor("bad", "", "0.1", "", "workspace", "single_file", "bad.py", "bad", "activate", "bad.py", {}, False, "loadable", None, "", 0, entry_path=str(bad)),
        ]
    )

    assert activated[0].status == "activated"
    assert [(m.custom_type, m.content, m.display) for m in activated[0].custom_messages] == [
        ("note", "boot", True),
        ("note", "hidden", False),
    ]
    assert activated[1].status == "disabled"
    assert activated[1].custom_messages == ()
