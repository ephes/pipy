"""Product sessions record Pi system messages (Pi `9e05370b2`, SYS1a).

The first run of a session persists the prompt (pipy's one `preamble`
section) and every tool; later runs persist only changes. Provider requests
never carry a system message: the prompt still travels out of band.
"""

from __future__ import annotations

import io
from datetime import UTC, datetime
from pathlib import Path

import pytest

from pipy_harness.models import HarnessStatus
from pipy_harness.native import ProviderRequest, ProviderResult
from pipy_harness.native.agent import (
    AgentSystemMessage,
    AgentUserMessage,
    ProductContent,
)
from pipy_harness.native.agent.system_messages import (
    current_system_message,
    system_message_text,
)
from pipy_harness.native.coding.session import CodingSession
from pipy_harness.native.session_tree import MessageEntry, NativeSessionTree
from pipy_harness.native.settings import SettingsManager


class _RecordingProvider:
    name = "fake"
    supports_tool_calls = True
    model_id = "fake-native-bootstrap"

    def __init__(self) -> None:
        self.requests: list[ProviderRequest] = []

    def complete(self, request: ProviderRequest, **_kwargs: object) -> ProviderResult:
        self.requests.append(request)
        now = datetime.now(UTC)
        return ProviderResult(
            status=HarnessStatus.SUCCEEDED,
            provider_name=self.name,
            model_id=self.model_id,
            started_at=now,
            ended_at=now,
            final_text="ok",
            tool_calls=(),
        )


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))


def _workspace(tmp_path: Path) -> Path:
    cwd = tmp_path / "workspace"
    cwd.mkdir()
    return cwd


def _run(
    tree: NativeSessionTree,
    cwd: Path,
    inputs: str,
    *,
    system_prompt: str = "You are a test harness.",
) -> _RecordingProvider:
    provider = _RecordingProvider()
    session = CodingSession(
        provider=provider,
        native_session=tree,
        settings_manager=SettingsManager.for_workspace(cwd),
    )
    session.run(
        workspace_root=cwd,
        input_stream=io.StringIO(inputs),
        output_stream=io.StringIO(),
        error_stream=io.StringIO(),
        system_prompt=system_prompt,
    )
    return provider


def _stored_messages(tree: NativeSessionTree) -> list[object]:
    return [
        entry.message for entry in tree.get_entries() if isinstance(entry, MessageEntry)
    ]


def test_first_run_records_the_prompt_and_tools_once(tmp_path: Path) -> None:
    cwd = _workspace(tmp_path)
    tree = NativeSessionTree.create(cwd, session_dir=tmp_path / "sessions")

    provider = _run(tree, cwd, "ONE\nTWO\n/exit\n")

    stored = _stored_messages(tree)
    systems = [m for m in stored if isinstance(m, AgentSystemMessage)]
    assert len(systems) == 1
    assert stored[0] is systems[0]
    assert isinstance(stored[1], AgentUserMessage)
    leading = systems[0]
    assert [name for name, _ in leading.sections] == ["preamble"]
    # The stored prompt is exactly what the provider received out of band.
    assert system_message_text(leading) == provider.requests[0].system_prompt
    assert [tool.name for tool in leading.tools_added] == [
        tool.name for tool in provider.requests[0].available_tools
    ]
    assert leading.tools_added
    for request in provider.requests:
        assert not any(isinstance(m, AgentSystemMessage) for m in request.messages)


def test_a_changed_prompt_records_only_a_preamble_patch(tmp_path: Path) -> None:
    cwd = _workspace(tmp_path)
    tree = NativeSessionTree.create(cwd, session_dir=tmp_path / "sessions")
    _run(tree, cwd, "ONE\n/exit\n")
    assert tree.path is not None

    reopened = NativeSessionTree.open(tree.path)
    provider = _run(
        reopened,
        cwd,
        "TWO\n/exit\n",
        system_prompt="You are a test harness.\n\nAlways answer in haiku.",
    )

    systems = [
        m for m in _stored_messages(reopened) if isinstance(m, AgentSystemMessage)
    ]
    assert len(systems) == 2
    patch = systems[1]
    assert [name for name, _ in patch.sections] == ["preamble"]
    assert patch.tools_added == () and patch.tools_removed == ()
    assert "Always answer in haiku." in (patch.sections[0][1] or "")
    current = current_system_message(reopened.build_context().messages)
    assert current is not None
    assert system_message_text(current) == provider.requests[0].system_prompt


def test_an_unchanged_resumed_session_records_nothing_new(tmp_path: Path) -> None:
    cwd = _workspace(tmp_path)
    tree = NativeSessionTree.create(cwd, session_dir=tmp_path / "sessions")
    _run(tree, cwd, "ONE\n/exit\n")
    assert tree.path is not None

    reopened = NativeSessionTree.open(tree.path)
    _run(reopened, cwd, "TWO\n/exit\n")

    systems = [
        m for m in _stored_messages(reopened) if isinstance(m, AgentSystemMessage)
    ]
    assert len(systems) == 1


def test_a_session_without_system_state_gets_the_whole_state_later(
    tmp_path: Path,
) -> None:
    cwd = _workspace(tmp_path)
    tree = NativeSessionTree.create(cwd, session_dir=tmp_path / "sessions")
    tree.append_message(AgentUserMessage(ProductContent("OLD")))

    _run(tree, cwd, "NEW\n/exit\n")

    stored = _stored_messages(tree)
    assert isinstance(stored[0], AgentUserMessage)
    assert isinstance(stored[1], AgentSystemMessage)
    assert stored[1].tools_added
    assert isinstance(stored[2], AgentUserMessage)


def test_a_narrowing_request_hook_leaves_the_stored_declarations_intact(
    tmp_path: Path,
) -> None:
    cwd = _workspace(tmp_path)
    extension = cwd / ".pipy" / "extensions" / "narrow.py"
    extension.parent.mkdir(parents=True)
    extension.write_text(
        "from pipy_harness.native.extension_types import ProviderRequestTransform\n"
        "def activate(api):\n"
        "    @api.on('before_provider_request')\n"
        "    def narrow(event, ctx):\n"
        "        return ProviderRequestTransform(available_tools=('read',))\n",
        encoding="utf-8",
    )
    tree = NativeSessionTree.create(cwd, persist=False)

    provider = _run(tree, cwd, "ONE\n/exit\n")

    assert [tool.name for tool in provider.requests[0].available_tools] == ["read"]
    leading = next(
        m for m in _stored_messages(tree) if isinstance(m, AgentSystemMessage)
    )
    assert len(leading.tools_added) > 1
