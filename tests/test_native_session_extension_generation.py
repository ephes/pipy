from __future__ import annotations

from dataclasses import fields
from pathlib import Path

from pipy_harness.native.extension_hooks import _activate_workspace_extensions
from pipy_harness.native.extension_runtime import (
    ActivatedExtension,
    ExtensionActivationBatch,
    QueuedCustomMessage,
    QueuedUserMessage,
    _ExtensionRuntime,
    activate_extensions,
    dispatch_extension_command,
)
from pipy_harness.native.extensions import discover_extensions
from pipy_harness.native.package_resources import PackageResourceRoots
from pipy_harness.native.resources import WorkspaceResources
from pipy_harness.native.session_generation import (
    SessionExtensionGeneration,
    SessionGenerationRef,
)
from pipy_harness.native.session_tree import NativeSessionTree
from pipy_harness.native.tool_loop_session import (
    _ExtensionCustomEntryRunState,
    _RunControlState,
)


def _empty_resources() -> WorkspaceResources:
    return WorkspaceResources((), (), (), False, False, False)


def _runtime_from_batch(
    tmp_path: Path,
    *,
    message_outbox: list[QueuedUserMessage],
    custom_message_outbox: list[QueuedCustomMessage],
    activated: tuple[ActivatedExtension, ...] = (),
) -> _ExtensionRuntime:
    return _activate_workspace_extensions(
        tmp_path,
        _empty_resources(),
        activation_batch=ExtensionActivationBatch(
            activated=activated,
            message_outbox=message_outbox,
            custom_message_outbox=custom_message_outbox,
        ),
    )


def test_run_control_holds_one_generation_reference_and_no_mirrors() -> None:
    """Extension state has exactly one owner, reached through the session ref."""

    field_names = {field.name for field in fields(_RunControlState)}

    assert "_ext_runtime" not in field_names
    # The generation itself is no longer a bare field: it is reached through
    # the reference that owns the session mutex.
    assert "extension_generation" not in field_names
    assert "generation_ref" in field_names
    assert {name for name in field_names if name.startswith("extension_")} == {
        "extension_in_agent_turn",
    }


def test_generation_preserves_outbox_identity_and_ui_adapter_late_binding(
    tmp_path: Path,
) -> None:
    extension_dir = tmp_path / ".pipy" / "extensions"
    extension_dir.mkdir(parents=True)
    (extension_dir / "sender.py").write_text(
        "def activate(api):\n"
        "    def send(ctx, args):\n"
        "        api.send_user_message(args)\n"
        "    api.register_command('send', 'send', send)\n",
        encoding="utf-8",
    )
    first_outbox: list[QueuedUserMessage] = []
    first_custom_outbox: list[QueuedCustomMessage] = []
    activated = tuple(
        activate_extensions(
            discover_extensions(
                tmp_path,
                config_home_env={},
                home_dir=tmp_path,
                include_workspace_defaults=True,
            ),
            message_outbox=first_outbox,
            custom_message_outbox=first_custom_outbox,
        )
    )
    first_runtime = _runtime_from_batch(
        tmp_path,
        activated=activated,
        message_outbox=first_outbox,
        custom_message_outbox=first_custom_outbox,
    )
    first_flags: dict[str, object] = {"mode": "first"}
    first_generation = SessionExtensionGeneration(first_runtime, first_flags)
    ctl = _RunControlState(
        session_tree=NativeSessionTree.create(tmp_path, persist=False),
        tree_filter_mode="default",
        pending_prefill=None,
        package_roots=PackageResourceRoots.empty(),
        workspace_resources=_empty_resources(),
        generation_ref=SessionGenerationRef(first_generation),
        agent_settled_pending=False,
        extension_in_agent_turn=False,
    )
    adapter = _ExtensionCustomEntryRunState(ctl=ctl)

    assert first_generation.runtime.outbox is first_outbox
    assert first_generation.runtime.custom_outbox is first_custom_outbox
    assert first_generation.flag_values is first_flags
    assert adapter.extension_message_outbox is first_outbox
    assert adapter.extension_custom_message_outbox is first_custom_outbox

    dispatched = dispatch_extension_command(
        "/send queued-late",
        first_generation.runtime.commands,
        cwd=str(tmp_path),
        has_ui=False,
        flags=first_generation.flag_values,
    )
    assert dispatched is not None and dispatched.ran
    assert [message.content for message in adapter.extension_message_outbox] == [
        "queued-late"
    ]

    second_outbox: list[QueuedUserMessage] = []
    second_custom_outbox: list[QueuedCustomMessage] = []
    second_runtime = _runtime_from_batch(
        tmp_path,
        message_outbox=second_outbox,
        custom_message_outbox=second_custom_outbox,
    )
    ctl.extension_generation = SessionExtensionGeneration(second_runtime, {})

    assert adapter.extension_message_outbox is second_outbox
    assert adapter.extension_custom_message_outbox is second_custom_outbox

    stale_dispatch = dispatch_extension_command(
        "/send old-after-swap",
        first_generation.runtime.commands,
        cwd=str(tmp_path),
        has_ui=False,
        flags=first_generation.flag_values,
    )
    assert stale_dispatch is not None and stale_dispatch.ran
    assert adapter.extension_message_outbox == []
    assert [message.content for message in first_outbox] == [
        "queued-late",
        "old-after-swap",
    ]


def _generation(tmp_path: Path, label: str) -> SessionExtensionGeneration:
    return SessionExtensionGeneration(
        _runtime_from_batch(tmp_path, message_outbox=[], custom_message_outbox=[]),
        {"mode": label},
    )


def test_generation_ref_publishes_a_new_identity_and_returns_the_retired_value(
    tmp_path: Path,
) -> None:
    first = _generation(tmp_path, "first")
    second = _generation(tmp_path, "second")
    ref = SessionGenerationRef(first)

    before = ref.snapshot()
    assert before.generation is first

    retired = ref.publish(second)

    after = ref.snapshot()
    assert retired is first
    assert after.generation is second
    assert after.generation_id != before.generation_id


def test_a_snapshot_does_not_follow_a_later_publication(tmp_path: Path) -> None:
    """An operation reads one generation for its whole duration."""

    first = _generation(tmp_path, "first")
    ref = SessionGenerationRef(first)
    held = ref.snapshot()

    ref.publish(_generation(tmp_path, "second"))

    assert held.generation is first
    assert held.generation.flag_values == {"mode": "first"}
    assert ref.snapshot().generation is not first


def test_capabilities_and_the_generation_share_one_session_mutex(
    tmp_path: Path,
) -> None:
    """Two locks would not serialize a reload against a worker's mutation."""

    from pipy_harness.native.tool_capabilities import (
        NativeToolCapabilities,
        ToolFilterOptions,
    )

    ref = SessionGenerationRef(_generation(tmp_path, "first"))
    capabilities = NativeToolCapabilities(
        {},
        {},
        workspace_root=tmp_path,
        reference_roots=(),
        stderr_sink=lambda _text: None,
        filter_options=ToolFilterOptions.empty(),
        cancel_join_timeout_seconds=1.0,
        state_lock=ref.lock,
    )

    assert capabilities._state_lock is ref.lock
