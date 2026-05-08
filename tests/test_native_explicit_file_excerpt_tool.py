from __future__ import annotations

import json
from dataclasses import fields
from pathlib import Path

import pytest

from pipy_harness.native import (
    NativeExplicitFileExcerptReason,
    NativeExplicitFileExcerptResult,
    NativeExplicitFileExcerptTarget,
    NativeExplicitFileExcerptTool,
    NativeReadOnlyApprovalDecision,
    NativeReadOnlyGateDecision,
    NativeReadOnlyToolLimits,
    NativeReadOnlyToolRequest,
    NativeReadOnlyToolRequestKind,
    NativeToolApprovalMode,
    NativeToolApprovalPolicy,
    NativeToolRequestIdentity,
    NativeToolSandboxMode,
    NativeToolSandboxPolicy,
    NativeToolStatus,
)


ROOT = Path(__file__).parents[1]


def current_request(
    *,
    request_kind: NativeReadOnlyToolRequestKind = NativeReadOnlyToolRequestKind.EXPLICIT_FILE_EXCERPT,
    limits: NativeReadOnlyToolLimits | None = None,
) -> NativeReadOnlyToolRequest:
    identity = NativeToolRequestIdentity.current_noop()
    return NativeReadOnlyToolRequest(
        tool_request_id=identity.request_id,
        turn_index=identity.turn_index,
        request_kind=request_kind,
        limits=limits or NativeReadOnlyToolLimits(),
    )


def allowed_gate() -> NativeReadOnlyGateDecision:
    return NativeReadOnlyGateDecision(approval_decision=NativeReadOnlyApprovalDecision.ALLOWED)


def invoke_success(tmp_path: Path, relative_path: str = "src/example.py"):
    target_path = tmp_path / relative_path
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text("line one\nline two\n", encoding="utf-8")

    return NativeExplicitFileExcerptTool(tmp_path).invoke(
        current_request(),
        allowed_gate(),
        NativeExplicitFileExcerptTarget(relative_path),
    )


def unsafe_request(
    *,
    approval_policy: NativeToolApprovalPolicy | None = None,
    sandbox_policy: NativeToolSandboxPolicy | None = None,
) -> NativeReadOnlyToolRequest:
    request = object.__new__(NativeReadOnlyToolRequest)
    identity = NativeToolRequestIdentity.current_noop()
    object.__setattr__(request, "tool_request_id", identity.request_id)
    object.__setattr__(request, "turn_index", identity.turn_index)
    object.__setattr__(request, "request_kind", NativeReadOnlyToolRequestKind.EXPLICIT_FILE_EXCERPT)
    object.__setattr__(request, "tool_name", "read_only_repo_inspection")
    object.__setattr__(request, "tool_kind", "read_only_workspace")
    object.__setattr__(
        request,
        "approval_policy",
        approval_policy or NativeToolApprovalPolicy(mode=NativeToolApprovalMode.REQUIRED),
    )
    object.__setattr__(
        request,
        "sandbox_policy",
        sandbox_policy
        or NativeToolSandboxPolicy(
            mode=NativeToolSandboxMode.READ_ONLY_WORKSPACE,
            workspace_read_allowed=True,
        ),
    )
    object.__setattr__(request, "limits", NativeReadOnlyToolLimits())
    object.__setattr__(request, "scope_label", None)
    for field_name in (
        "tool_payloads_stored",
        "stdout_stored",
        "stderr_stored",
        "diffs_stored",
        "file_contents_stored",
        "prompt_stored",
        "model_output_stored",
        "provider_responses_stored",
        "raw_transcript_imported",
    ):
        object.__setattr__(request, field_name, False)
    return request


def test_explicit_file_excerpt_success_is_bounded_and_in_memory(tmp_path):
    result = invoke_success(tmp_path)

    assert result.status == NativeToolStatus.SUCCEEDED
    assert result.reason_label == NativeExplicitFileExcerptReason.READ_SUCCEEDED
    assert result.tool_request_id == "native-tool-0001"
    assert result.turn_index == 0
    assert result.excerpt is not None
    assert result.excerpt.text == "line one\nline two\n"
    assert result.byte_count == len("line one\nline two\n".encode("utf-8"))
    assert result.line_count == 2
    assert result.excerpt.byte_count == result.byte_count
    assert result.excerpt.line_count == result.line_count

    metadata = result.archive_metadata()
    assert metadata["status"] == "succeeded"
    assert metadata["byte_count"] == result.byte_count
    assert metadata["line_count"] == 2
    assert metadata["excerpt_count"] == 1
    assert metadata["distinct_source_file_count"] == 1
    assert metadata["workspace_read_allowed"] is True
    assert "line one" not in json.dumps(metadata)


def test_explicit_file_excerpt_requires_pipy_owned_identity_and_gate_data(tmp_path):
    with pytest.raises(ValueError, match="tool_request_id"):
        NativeReadOnlyToolRequest(
            tool_request_id="provider-owned-id",
            turn_index=0,
            request_kind=NativeReadOnlyToolRequestKind.EXPLICIT_FILE_EXCERPT,
        )
    with pytest.raises(ValueError, match="pipy-owned"):
        NativeReadOnlyGateDecision(
            approval_decision=NativeReadOnlyApprovalDecision.ALLOWED,
            decision_authority="provider-selected",
        )
    denied = NativeExplicitFileExcerptTool(tmp_path).invoke(
        current_request(),
        NativeReadOnlyGateDecision(approval_decision=NativeReadOnlyApprovalDecision.DENIED),
        NativeExplicitFileExcerptTarget("missing.txt"),
    )

    assert denied.status == NativeToolStatus.SKIPPED
    assert denied.reason_label == NativeExplicitFileExcerptReason.APPROVAL_NOT_ALLOWED
    assert denied.archive_metadata()["approval_decision"] == "denied"


@pytest.mark.parametrize(
    ("sandbox", "reason"),
    [
        (
            NativeToolSandboxPolicy(mode=NativeToolSandboxMode.NO_WORKSPACE_ACCESS),
            NativeExplicitFileExcerptReason.UNSAFE_SANDBOX,
        ),
        (
            NativeToolSandboxPolicy(mode=NativeToolSandboxMode.READ_ONLY_WORKSPACE),
            NativeExplicitFileExcerptReason.UNSAFE_SANDBOX,
        ),
        (
            NativeToolSandboxPolicy(
                mode=NativeToolSandboxMode.READ_ONLY_WORKSPACE,
                workspace_read_allowed=True,
                filesystem_mutation_allowed=True,
            ),
            NativeExplicitFileExcerptReason.UNSAFE_SANDBOX,
        ),
        (
            NativeToolSandboxPolicy(
                mode=NativeToolSandboxMode.READ_ONLY_WORKSPACE,
                workspace_read_allowed=True,
                shell_execution_allowed=True,
            ),
            NativeExplicitFileExcerptReason.UNSAFE_SANDBOX,
        ),
        (
            NativeToolSandboxPolicy(
                mode=NativeToolSandboxMode.READ_ONLY_WORKSPACE,
                workspace_read_allowed=True,
                network_access_allowed=True,
            ),
            NativeExplicitFileExcerptReason.UNSAFE_SANDBOX,
        ),
    ],
)
def test_explicit_file_excerpt_enforces_sandbox_and_capability_posture(
    tmp_path,
    sandbox: NativeToolSandboxPolicy,
    reason: NativeExplicitFileExcerptReason,
):
    result = NativeExplicitFileExcerptTool(tmp_path).invoke(
        unsafe_request(sandbox_policy=sandbox),
        allowed_gate(),
        NativeExplicitFileExcerptTarget("safe.txt"),
    )

    assert result.status == NativeToolStatus.SKIPPED
    assert result.reason_label == reason
    metadata = result.archive_metadata()
    assert metadata["sandbox_policy"] == sandbox.label
    assert metadata["workspace_read_allowed"] is sandbox.workspace_read_allowed


def test_explicit_file_excerpt_allows_not_required_approval_policy(tmp_path):
    result = NativeExplicitFileExcerptTool(tmp_path).invoke(
        unsafe_request(approval_policy=NativeToolApprovalPolicy(mode=NativeToolApprovalMode.NOT_REQUIRED)),
        allowed_gate(),
        NativeExplicitFileExcerptTarget("safe.txt"),
    )

    assert result.status == NativeToolStatus.SKIPPED
    assert result.reason_label == NativeExplicitFileExcerptReason.MISSING_FILE
    assert result.archive_metadata()["approval_required"] is False
    metadata = result.archive_metadata()
    assert metadata["approval_policy"] == "not-required"
    assert metadata["approval_required"] is False


def test_explicit_file_excerpt_skips_search_requests(tmp_path):
    result = NativeExplicitFileExcerptTool(tmp_path).invoke(
        current_request(request_kind=NativeReadOnlyToolRequestKind.SEARCH_EXCERPT),
        allowed_gate(),
        NativeExplicitFileExcerptTarget("safe.txt"),
    )

    assert result.status == NativeToolStatus.SKIPPED
    assert result.reason_label == NativeExplicitFileExcerptReason.UNSUPPORTED_REQUEST_KIND


@pytest.mark.parametrize(
    "target_value",
    [
        "/tmp/outside.txt",
        "../outside.txt",
        "src/../outside.txt",
        "~/safe.txt",
        "$HOME/safe.txt",
        "src/*.py",
        "C:/Users/name/file.txt",
        "src\\safe.txt",
        "secret_config.py",
    ],
)
def test_explicit_file_excerpt_target_validation_rejects_unsafe_targets(target_value: str):
    with pytest.raises(ValueError):
        NativeExplicitFileExcerptTarget(target_value)


def test_explicit_file_excerpt_rejects_provider_or_model_authority():
    with pytest.raises(ValueError, match="pipy-owned"):
        NativeExplicitFileExcerptTarget("src/example.py", target_authority="model-selected")


def test_explicit_file_excerpt_resolved_path_must_stay_in_workspace(tmp_path):
    outside = tmp_path.parent / "outside-safe.txt"
    outside.write_text("outside\n", encoding="utf-8")
    link = tmp_path / "link.txt"
    link.symlink_to(outside)

    result = NativeExplicitFileExcerptTool(tmp_path).invoke(
        current_request(),
        allowed_gate(),
        NativeExplicitFileExcerptTarget("link.txt"),
    )

    assert result.status == NativeToolStatus.SKIPPED
    assert result.reason_label == NativeExplicitFileExcerptReason.UNSAFE_TARGET


def test_explicit_file_excerpt_missing_directories_and_unreadable_files_fail_closed(tmp_path):
    directory = tmp_path / "folder"
    directory.mkdir()
    unreadable = tmp_path / "unreadable.txt"
    unreadable.write_text("safe\n", encoding="utf-8")
    unreadable.chmod(0)

    tool = NativeExplicitFileExcerptTool(tmp_path)
    request = current_request()
    assert tool.invoke(request, allowed_gate(), NativeExplicitFileExcerptTarget("missing.txt")).reason_label == (
        NativeExplicitFileExcerptReason.MISSING_FILE
    )
    assert tool.invoke(request, allowed_gate(), NativeExplicitFileExcerptTarget("folder")).reason_label == (
        NativeExplicitFileExcerptReason.DIRECTORY_TARGET
    )
    assert tool.invoke(request, allowed_gate(), NativeExplicitFileExcerptTarget("unreadable.txt")).reason_label == (
        NativeExplicitFileExcerptReason.UNREADABLE_FILE
    )


def test_explicit_file_excerpt_binary_unsupported_secret_and_oversized_content_fail_closed(tmp_path):
    (tmp_path / "binary.txt").write_bytes(b"abc\x00def")
    (tmp_path / "latin1.txt").write_bytes("caf\xe9".encode("latin-1"))
    (tmp_path / "config.txt").write_text("api_key = raw-value\n", encoding="utf-8")
    (tmp_path / "large.txt").write_text("abcdef", encoding="utf-8")

    tool = NativeExplicitFileExcerptTool(tmp_path)
    request = current_request()
    small_limit_request = current_request(
        limits=NativeReadOnlyToolLimits(
            per_excerpt_bytes=5,
            per_excerpt_lines=80,
            per_source_file_bytes=5,
            per_source_file_lines=160,
            total_context_bytes=5,
            total_context_lines=480,
            max_excerpts=12,
            max_distinct_source_files=6,
        )
    )

    assert tool.invoke(request, allowed_gate(), NativeExplicitFileExcerptTarget("binary.txt")).reason_label == (
        NativeExplicitFileExcerptReason.BINARY_FILE
    )
    assert tool.invoke(request, allowed_gate(), NativeExplicitFileExcerptTarget("latin1.txt")).reason_label == (
        NativeExplicitFileExcerptReason.UNSUPPORTED_ENCODING
    )
    assert tool.invoke(request, allowed_gate(), NativeExplicitFileExcerptTarget("config.txt")).reason_label == (
        NativeExplicitFileExcerptReason.SECRET_LOOKING_CONTENT
    )
    assert tool.invoke(small_limit_request, allowed_gate(), NativeExplicitFileExcerptTarget("large.txt")).reason_label == (
        NativeExplicitFileExcerptReason.OVERSIZED_FILE
    )


def test_explicit_file_excerpt_line_and_count_limits_fail_closed(tmp_path):
    (tmp_path / "too-many-lines.txt").write_text("one\ntwo\n", encoding="utf-8")
    line_limit_request = current_request(
        limits=NativeReadOnlyToolLimits(
            per_excerpt_bytes=4096,
            per_excerpt_lines=1,
            per_source_file_bytes=8192,
            per_source_file_lines=1,
            total_context_bytes=24576,
            total_context_lines=1,
            max_excerpts=12,
            max_distinct_source_files=6,
        )
    )
    zero_count_request = current_request(
        limits=NativeReadOnlyToolLimits(
            per_excerpt_bytes=4096,
            per_excerpt_lines=80,
            per_source_file_bytes=8192,
            per_source_file_lines=160,
            total_context_bytes=24576,
            total_context_lines=480,
            max_excerpts=0,
            max_distinct_source_files=6,
        )
    )

    tool = NativeExplicitFileExcerptTool(tmp_path)

    assert tool.invoke(
        line_limit_request,
        allowed_gate(),
        NativeExplicitFileExcerptTarget("too-many-lines.txt"),
    ).reason_label == NativeExplicitFileExcerptReason.LIMIT_EXCEEDED
    assert tool.invoke(
        zero_count_request,
        allowed_gate(),
        NativeExplicitFileExcerptTarget("too-many-lines.txt"),
    ).reason_label == NativeExplicitFileExcerptReason.LIMIT_EXCEEDED


def test_explicit_file_excerpt_ignored_and_generated_files_fail_closed(tmp_path):
    (tmp_path / ".gitignore").write_text("ignored.txt\nignored-dir/\n", encoding="utf-8")
    (tmp_path / "ignored.txt").write_text("safe\n", encoding="utf-8")
    ignored_dir = tmp_path / "ignored-dir"
    ignored_dir.mkdir()
    (ignored_dir / "safe.txt").write_text("safe\n", encoding="utf-8")
    generated_dir = tmp_path / "__pycache__"
    generated_dir.mkdir()
    (generated_dir / "module.pyc").write_bytes(b"safe")

    tool = NativeExplicitFileExcerptTool(tmp_path)
    request = current_request()

    assert tool.invoke(request, allowed_gate(), NativeExplicitFileExcerptTarget("ignored.txt")).reason_label == (
        NativeExplicitFileExcerptReason.IGNORED_OR_GENERATED_FILE
    )
    assert tool.invoke(request, allowed_gate(), NativeExplicitFileExcerptTarget("ignored-dir/safe.txt")).reason_label == (
        NativeExplicitFileExcerptReason.IGNORED_OR_GENERATED_FILE
    )
    assert tool.invoke(request, allowed_gate(), NativeExplicitFileExcerptTarget("__pycache__/module.pyc")).reason_label == (
        NativeExplicitFileExcerptReason.IGNORED_OR_GENERATED_FILE
    )


def test_explicit_file_excerpt_archive_metadata_is_closed_and_metadata_only(tmp_path):
    result = invoke_success(tmp_path, "nested/safe.txt")
    metadata = result.archive_metadata()

    assert set(metadata) == NativeExplicitFileExcerptTool.SAFE_METADATA_KEYS
    assert metadata["source_label"] == "safe.txt"
    assert metadata["source_sha256"] != "nested/safe.txt"
    assert metadata["tool_payloads_stored"] is False
    assert metadata["stdout_stored"] is False
    assert metadata["stderr_stored"] is False
    assert metadata["diffs_stored"] is False
    assert metadata["file_contents_stored"] is False
    assert metadata["prompt_stored"] is False
    assert metadata["model_output_stored"] is False
    assert metadata["provider_responses_stored"] is False
    assert metadata["raw_transcript_imported"] is False

    forbidden_fields = {
        "args",
        "arguments",
        "command",
        "credentials",
        "diff",
        "file_content",
        "file_contents",
        "model_output",
        "patch",
        "path",
        "paths",
        "payload",
        "private_key",
        "prompt",
        "provider_response",
        "query",
        "raw_args",
        "raw_payload",
        "secret",
        "stderr",
        "stdout",
        "token",
    }
    assert forbidden_fields.isdisjoint(metadata)
    assert "line one" not in json.dumps(metadata)


def test_explicit_file_excerpt_result_separates_in_memory_text_from_metadata():
    result_fields = {field.name for field in fields(NativeExplicitFileExcerptResult)}

    assert "excerpt" in result_fields
    assert "excerpt_text" not in result_fields
    assert "file_content" not in result_fields
    assert "file_contents" not in result_fields


def test_explicit_file_excerpt_tool_boundary_is_documented():
    spec = (ROOT / "docs/harness-spec.md").read_text(encoding="utf-8")
    backlog = (ROOT / "docs/backlog.md").read_text(encoding="utf-8")
    storage = (ROOT / "docs/session-storage.md").read_text(encoding="utf-8")
    compact_spec = " ".join(spec.split())
    compact_backlog = " ".join(backlog.split())
    compact_storage = " ".join(storage.split())

    assert "### Native Explicit File Excerpt Tool" in spec
    assert "`NativeExplicitFileExcerptTool`" in spec
    assert "`NativeReadOnlyGateDecision`" in spec
    assert "`NativeExplicitFileExcerptTarget`" in spec
    assert "`workspace_read_allowed`" in spec
    assert "wired into `NativeAgentSession` only through the bounded fixture-gated" in compact_spec
    assert "Oversized files fail closed" in compact_spec
    assert "fuller ignore semantics remain deferred" in spec
    assert "metadata helper" in spec
    assert "excludes raw excerpt text" in spec

    done = backlog[: backlog.index("## Next Slice")]
    assert "Native explicit file excerpt read-only tool implementation" in done
    assert "bounded post-tool provider turn against synthetic sanitized observations" in compact_backlog
    assert "Native bounded read-only tool observation into follow-up provider turn" in done

    assert "explicit file excerpt tool keeps successful excerpt text in memory only" in compact_storage
