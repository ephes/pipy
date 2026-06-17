from __future__ import annotations

import base64
import io
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from pipy_harness.cli import main
from pipy_harness.models import HarnessStatus
from pipy_harness.native.export_distribution import (
    ShareCancelled,
    compare_versions,
    create_secret_gist,
    default_html_export_path,
    detect_install_method,
    export_native_branch_to_jsonl,
    export_native_session_to_html,
    fetch_latest_pipy_version,
    import_native_session_jsonl,
    parse_command_path_argument,
    redact_export_value,
    self_update_plan,
)
from pipy_harness.native.cancellation import CancelToken
from pipy_harness.native.models import ProviderRequest, ProviderResult, ProviderToolCall
from pipy_harness.native.provider import ProviderPort
from pipy_harness.native.tool_loop_session import NativeToolReplSession
from pipy_harness.native.session_tree import NativeSessionTree
from pipy_harness.native.tools.messages import (
    AssistantMessage,
    ToolResultMessage,
    UserMessage,
)


class _NoTurnProvider(ProviderPort):
    name = "fake"
    model_id = "fake-native-bootstrap"
    supports_tool_calls = True

    def complete(self, request: ProviderRequest, **kwargs: object) -> ProviderResult:
        del request, kwargs
        now = datetime.now(UTC)
        return ProviderResult(
            status=HarnessStatus.SUCCEEDED,
            provider_name=self.name,
            model_id=self.model_id,
            started_at=now,
            ended_at=now,
            final_text="unused",
            tool_calls=(),
        )


def _tree(tmp_path: Path) -> NativeSessionTree:
    cwd = tmp_path / "workspace"
    cwd.mkdir()
    tree = NativeSessionTree.create(cwd, session_dir=tmp_path / "sessions")
    tree.append_message(UserMessage(content="ROOT prompt"))
    first_reply = tree.append_message(AssistantMessage(content="ROOT answer"))
    tree.append_message(UserMessage(content="MAIN prompt"))
    tree.append_message(
        AssistantMessage(
            content=(
                "MAIN answer ghp_BARETOKENSHOULDNOTLEAK123456789 "
                "github_pat_BARETOKENSHOULDNOTLEAK123456789 "
                "ya29.BARETOKENSHOULDNOTLEAK123456789 "
                'password="quoted-secret-value" '
                '{"api_key":"json-secret-value","password": "json-password-value",'
                '"token":"json-token-value"}'
            ),
            tool_calls=(
                ProviderToolCall(
                    provider_correlation_id="call-1",
                    tool_name="write",
                    arguments_json=(
                        '{"path":"out.txt","content":"hello",'
                        '"api_key":"tool-json-secret-value"}'
                    ),
                ),
            ),
        )
    )
    tree.append_message(
        ToolResultMessage(
            tool_request_id="pipy-tool-1",
            output_text="wrote file token=ghp_SHOULD_NOT_LEAK_123456789",
            provider_correlation_id="call-1",
        )
    )
    main_leaf = tree.get_leaf_id()
    tree.branch(first_reply.id)
    tree.append_message(UserMessage(content="ALT prompt"))
    tree.append_message(AssistantMessage(content="ALT answer"))
    assert main_leaf is not None
    tree.branch(main_leaf)
    return tree


def _run_commands(tree: NativeSessionTree, cwd: Path, commands: str) -> str:
    err = io.StringIO()
    NativeToolReplSession(provider=_NoTurnProvider(), native_session=tree).run(
        workspace_root=cwd,
        input_stream=io.StringIO(commands),
        output_stream=io.StringIO(),
        error_stream=err,
    )
    return err.getvalue()


def _decode_html_payload(path: Path) -> dict[str, Any]:
    html = path.read_text(encoding="utf-8")
    marker = '<script id="pipy-session-data" type="application/pipy-session+base64">'
    encoded = html.split(marker, 1)[1].split("</script>", 1)[0]
    return json.loads(base64.b64decode(encoded).decode("utf-8"))


def test_redaction_covers_json_quoted_secret_assignments() -> None:
    redacted = redact_export_value(
        '{"api_key":"plainsecret", "password": "pw", "token":"tok", "safe":"ok"}'
    )

    assert "plainsecret" not in redacted
    assert '"api_key":"[REDACTED]"' in redacted
    assert '"password": "[REDACTED]"' in redacted
    assert '"token":"[REDACTED]"' in redacted
    assert '"safe":"ok"' in redacted


def test_tool_loop_export_command_writes_jsonl_and_html_with_quoted_path(tmp_path: Path) -> None:
    tree = _tree(tmp_path)
    cwd = Path(tree.get_header().cwd)
    jsonl = cwd / "quoted export.jsonl"
    html = cwd / "quoted export.html"

    err = _run_commands(
        tree,
        cwd,
        f'/export "{jsonl.name}"\n/export "{html.name}"\n/exit\n',
    )

    assert "exported native session JSONL" in err
    assert "exported native session HTML" in err
    assert jsonl.is_file()
    assert html.is_file()
    assert _decode_html_payload(html)["header"]["id"] == tree.session_id


def test_tool_loop_import_command_reports_usage_and_imports_with_yes(tmp_path: Path) -> None:
    source_tree = _tree(tmp_path)
    source = tmp_path / "portable import.jsonl"
    export_native_branch_to_jsonl(source_tree, source)
    cwd = Path(source_tree.get_header().cwd)
    target_tree = NativeSessionTree.create(cwd, session_dir=tmp_path / "target-store")
    assert target_tree.path is not None

    err = _run_commands(
        target_tree,
        cwd,
        f"/import\n/import \"{source}\" --yes\n/exit\n",
    )

    assert "Usage: /import <path.jsonl>" in err
    assert "imported native session" in err
    assert (target_tree.path.parent / source.name).is_file()


def test_tool_loop_share_command_uses_token_and_share_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pipy_harness.native.export_distribution import ShareResult

    tree = _tree(tmp_path)
    cwd = Path(tree.get_header().cwd)
    calls: list[str] = []

    def fake_share(session_tree: NativeSessionTree, **kwargs: object) -> ShareResult:
        assert session_tree.session_id == tree.session_id
        assert kwargs["token"] == "ghp_UPLOAD_TOKEN_123456789"
        calls.append("share")
        return ShareResult(
            gist_id="gist123",
            gist_url="https://gist.github.com/u/gist123",
            viewer_url=None,
        )

    monkeypatch.setenv("GITHUB_TOKEN", "ghp_UPLOAD_TOKEN_123456789")
    monkeypatch.setattr("pipy_harness.native.tool_loop_session.share_native_session", fake_share)

    err = _run_commands(tree, cwd, "/share\n/exit\n")

    assert calls == ["share"]
    assert "gist URL: https://gist.github.com/u/gist123" in err


def test_html_export_embeds_full_tree_and_redacts_auth_tokens(tmp_path: Path) -> None:
    tree = _tree(tmp_path)
    output = default_html_export_path(tree, cwd=tmp_path)

    export_native_session_to_html(tree, output)

    payload = _decode_html_payload(output)
    serialized = json.dumps(payload)
    assert payload["header"]["id"] == tree.session_id
    assert len(payload["entries"]) == len(tree.get_entries())
    assert payload["leafId"] == tree.get_leaf_id()
    assert "MAIN prompt" in serialized
    assert "ALT prompt" in serialized
    assert "write" in serialized
    assert "ghp_SHOULD_NOT_LEAK" not in serialized
    assert "ghp_BARETOKENSHOULDNOTLEAK" not in serialized
    assert "github_pat_BARETOKENSHOULDNOTLEAK" not in serialized
    assert "ya29.BARETOKENSHOULDNOTLEAK" not in serialized
    assert "quoted-secret-value" not in serialized
    assert "json-secret-value" not in serialized
    assert "json-password-value" not in serialized
    assert "json-token-value" not in serialized
    assert "tool-json-secret-value" not in serialized
    assert "[REDACTED]" in serialized
    html = output.read_text(encoding="utf-8")
    assert "https://" not in html


def test_jsonl_export_linearizes_active_branch_and_import_round_trips(tmp_path: Path) -> None:
    tree = _tree(tmp_path)
    output = tmp_path / "portable.jsonl"

    export_native_branch_to_jsonl(tree, output)

    lines = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    assert lines[0]["type"] == "session"
    entries = lines[1:]
    assert entries[0]["parentId"] is None
    for previous, current in zip(entries, entries[1:]):
        assert current["parentId"] == previous["id"]
    serialized = json.dumps(lines)
    assert "MAIN prompt" in serialized
    assert "ALT prompt" not in serialized
    assert "ghp_SHOULD_NOT_LEAK" not in serialized
    assert "ghp_BARETOKENSHOULDNOTLEAK" not in serialized
    assert "json-secret-value" not in serialized
    assert "tool-json-secret-value" not in serialized

    before_import = output.read_text(encoding="utf-8")
    imported = import_native_session_jsonl(output, session_dir=tmp_path / "store")
    texts = [
        getattr(message, "content", getattr(message, "output_text", ""))
        for message in imported.build_context().messages
    ]
    assert "ROOT prompt" in texts
    assert "MAIN prompt" in texts
    assert "ALT prompt" not in texts
    assert output.read_text(encoding="utf-8") == before_import


def test_import_missing_cwd_rewrites_only_imported_copy(tmp_path: Path) -> None:
    tree = _tree(tmp_path)
    output = tmp_path / "portable-missing-cwd.jsonl"
    export_native_branch_to_jsonl(tree, output)
    missing = tmp_path / "does-not-exist"
    lines = output.read_text(encoding="utf-8").splitlines()
    header = json.loads(lines[0])
    header["cwd"] = str(missing)
    lines[0] = json.dumps(header, sort_keys=True)
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    before = output.read_text(encoding="utf-8")

    imported = import_native_session_jsonl(
        output, session_dir=tmp_path / "store", missing_cwd=tmp_path
    )

    assert imported.get_header().cwd == str(tmp_path.resolve())
    assert output.read_text(encoding="utf-8") == before


def test_import_collision_chooses_unique_destination(tmp_path: Path) -> None:
    tree = _tree(tmp_path)
    output = tmp_path / "portable-collision.jsonl"
    export_native_branch_to_jsonl(tree, output)
    store = tmp_path / "store"
    store.mkdir()
    existing = store / output.name
    existing.write_text("keep me\n", encoding="utf-8")

    imported = import_native_session_jsonl(output, session_dir=store)

    assert imported.path == store / "portable-collision-1.jsonl"
    assert existing.read_text(encoding="utf-8") == "keep me\n"


def test_share_gist_uses_secret_gist_and_never_sends_token_in_body() -> None:
    captured: dict[str, Any] = {}

    class Response:
        status = 201

        def read(self) -> bytes:
            return json.dumps(
                {"id": "gist123", "html_url": "https://gist.github.com/u/gist123"}
            ).encode("utf-8")

    def opener(request, timeout: float):  # noqa: ANN001
        captured["headers"] = dict(request.header_items())
        captured["body"] = request.data.decode("utf-8")
        captured["timeout"] = timeout
        return Response()

    result = create_secret_gist(
        html="<html>session</html>",
        filename="session.html",
        token="ghp_SECRET_TOKEN_123456789",
        opener=opener,
    )

    body = json.loads(captured["body"])
    assert body["public"] is False
    assert body["files"]["session.html"]["content"] == "<html>session</html>"
    assert "ghp_SECRET_TOKEN" not in captured["body"]
    assert captured["headers"]["Authorization"] == "Bearer ghp_SECRET_TOKEN_123456789"
    assert result.gist_id == "gist123"
    assert result.gist_url.endswith("gist123")


def test_share_gist_cancelled_before_network_post() -> None:
    called = False

    def opener(request, timeout: float):  # noqa: ANN001
        nonlocal called
        called = True
        raise AssertionError("network should not be called")

    try:
        create_secret_gist(
            html="<html></html>",
            filename="session.html",
            token="ghp_TOKEN",
            opener=opener,
            cancelled=lambda: True,
        )
    except ShareCancelled:
        pass
    else:  # pragma: no cover - failure branch keeps assertion readable
        raise AssertionError("expected ShareCancelled")
    assert called is False


def test_share_native_session_redacts_bare_tokens_in_gist_body(tmp_path: Path) -> None:
    from pipy_harness.native.export_distribution import share_native_session

    tree = _tree(tmp_path)
    captured: dict[str, Any] = {}

    class Response:
        status = 201

        def read(self) -> bytes:
            return json.dumps(
                {"id": "gist123", "html_url": "https://gist.github.com/u/gist123"}
            ).encode("utf-8")

    def opener(request, timeout: float):  # noqa: ANN001
        captured["body"] = request.data.decode("utf-8")
        return Response()

    share_native_session(tree, token="ghp_UPLOAD_TOKEN_123456789", opener=opener)

    assert "ghp_BARETOKENSHOULDNOTLEAK" not in captured["body"]
    assert "github_pat_BARETOKENSHOULDNOTLEAK" not in captured["body"]
    assert "ya29.BARETOKENSHOULDNOTLEAK" not in captured["body"]
    assert "quoted-secret-value" not in captured["body"]
    assert "json-secret-value" not in captured["body"]
    assert "tool-json-secret-value" not in captured["body"]
    assert "ghp_UPLOAD_TOKEN" not in captured["body"]


def test_tool_loop_tui_share_command_cancels_worker_with_cancel_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pipy_harness.native.export_distribution import ShareResult
    from pipy_harness.native.tool_loop_session import TURN_ABORTED

    class CancelUi:
        def __init__(self) -> None:
            self.notices: list[str] = []

        def add_notice(self, message: str) -> None:
            self.notices.append(message)

        def wait_for_active_turn_interrupt(
            self, done_event: Any, abort_event: Any, *, accept_queue: bool
        ) -> str:
            del done_event
            assert accept_queue is False
            abort_event.set()
            return TURN_ABORTED

    tree = _tree(tmp_path)
    ui = CancelUi()
    observed: list[str] = []

    def fake_share(session_tree: NativeSessionTree, **kwargs: object) -> ShareResult:
        assert session_tree.session_id == tree.session_id
        assert kwargs["token"] == "ghp_UPLOAD_TOKEN_123456789"
        cancel_token = kwargs["cancel_token"]
        assert isinstance(cancel_token, CancelToken)
        assert callable(kwargs["cancelled"])
        cancel_token.event.wait(timeout=2.0)
        assert cancel_token.event.is_set()
        observed.append("cancelled")
        raise ShareCancelled("Share cancelled.")

    monkeypatch.setattr("pipy_harness.native.tool_loop_session.share_native_session", fake_share)
    result = NativeToolReplSession(provider=_NoTurnProvider())._share_native_session_command(
        session_tree=tree,
        token="ghp_UPLOAD_TOKEN_123456789",
        terminal_ui=ui,  # type: ignore[arg-type]
        error_stream=io.StringIO(),
    )

    assert result is None
    assert observed == ["cancelled"]
    assert any("Share cancelled" in notice for notice in ui.notices)


def test_top_level_export_cli_writes_html(tmp_path: Path, capfd) -> None:
    tree = _tree(tmp_path)
    assert tree.path is not None
    output = tmp_path / "out.html"

    exit_code = main(["--export", str(tree.path), str(output)])

    captured = capfd.readouterr()
    assert exit_code == 0
    assert "Exported to:" in captured.out
    assert output.is_file()
    assert _decode_html_payload(output)["header"]["id"] == tree.session_id


def test_parse_path_argument_supports_quotes() -> None:
    assert parse_command_path_argument(' "dir/my export.html" trailing') == "dir/my export.html"
    assert parse_command_path_argument(" 'dir/my export.jsonl' --yes") == "dir/my export.jsonl"
    assert parse_command_path_argument(" plain.jsonl more") == "plain.jsonl"


def test_update_helpers_plan_known_install_methods(tmp_path: Path) -> None:
    uv_root = tmp_path / "uv-tools"
    exe = uv_root / "pipy" / "bin" / "pipy"
    exe.parent.mkdir(parents=True)
    exe.write_text("", encoding="utf-8")

    method = detect_install_method(executable=str(exe), env={"UV_TOOL_DIR": str(uv_root)})
    plan = self_update_plan(method=method, distribution_name="pipy-test-package")

    assert method == "uv-tool"
    assert plan.automatic is True
    assert plan.command == ("uv", "tool", "upgrade", "pipy-test-package")
    assert compare_versions("0.1.0", "0.2.0") < 0

    pip_plan = self_update_plan(method="pip", distribution_name="pipy-test-package")
    assert pip_plan.command[:4] == (
        sys.executable,
        "-m",
        "pip",
        "install",
    )


def test_update_plan_fails_safe_without_configured_distribution() -> None:
    plan = self_update_plan(method="pip", env={})

    assert plan.automatic is False
    assert plan.command == ()
    assert "PIPY_SELF_UPDATE_PACKAGE" in (plan.reason or "")


def test_version_check_honors_offline_and_skip_env() -> None:
    def opener(request, timeout: float):  # noqa: ANN001
        raise AssertionError("version check should not open network")

    assert (
        fetch_latest_pipy_version(
            env={"PIPY_OFFLINE": "1", "PIPY_SELF_UPDATE_PACKAGE": "pipy-test"},
            opener=opener,
        )
        is None
    )
    assert (
        fetch_latest_pipy_version(
            env={"PIPY_SKIP_VERSION_CHECK": "1", "PIPY_SELF_UPDATE_PACKAGE": "pipy-test"},
            opener=opener,
        )
        is None
    )
