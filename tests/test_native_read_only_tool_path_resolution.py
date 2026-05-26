"""Tests for the new `resolve_tool_path` helper and the secret-content check.

These tests pin the boundaries that let the model-driven `read`/`ls`/
`grep`/`find` tools accept absolute paths under a workspace or a
configured reference root while keeping `.git`/secret-content defenses
intact.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pipy_harness.native.read_only_tool import (
    has_secret_shaped_content,
    resolve_tool_path,
)


# ----------------------------- path resolver ------------------------------


def test_resolve_tool_path_accepts_workspace_relative(tmp_path: Path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    target = workspace / "src" / "code.py"
    target.parent.mkdir()
    target.write_text("x = 1\n", encoding="utf-8")

    resolved = resolve_tool_path(
        "src/code.py",
        workspace_root=workspace,
    )

    assert resolved.resolved == target
    assert resolved.root == workspace.resolve()
    assert resolved.relative_label == "src/code.py"
    assert resolved.is_workspace is True


def test_resolve_tool_path_accepts_absolute_under_workspace(tmp_path: Path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    target = workspace / "doc.md"
    target.write_text("# doc\n", encoding="utf-8")

    resolved = resolve_tool_path(
        str(target),
        workspace_root=workspace,
    )

    assert resolved.is_workspace is True
    assert resolved.relative_label == "doc.md"


def test_resolve_tool_path_accepts_absolute_under_reference_root(tmp_path: Path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    ref = tmp_path / "ref-project"
    ref.mkdir()
    target = ref / "README.md"
    target.write_text("# ref\n", encoding="utf-8")

    resolved = resolve_tool_path(
        str(target),
        workspace_root=workspace,
        reference_roots=(ref,),
    )

    assert resolved.is_workspace is False
    assert resolved.root == ref.resolve()
    assert resolved.relative_label == "README.md"
    assert resolved.display_label == "ref-project/README.md"


def test_resolve_tool_path_rejects_path_outside_any_root(tmp_path: Path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "file.md").write_text("nothing\n", encoding="utf-8")

    with pytest.raises(ValueError, match="outside the workspace"):
        resolve_tool_path(
            str(elsewhere / "file.md"),
            workspace_root=workspace,
        )


def test_resolve_tool_path_refuses_shell_expansion(tmp_path: Path):
    workspace = tmp_path / "ws"
    workspace.mkdir()

    with pytest.raises(ValueError, match="shell expansion"):
        resolve_tool_path(
            "$HOME/anything",
            workspace_root=workspace,
        )


def test_resolve_tool_path_refuses_parent_traversal(tmp_path: Path):
    workspace = tmp_path / "ws"
    workspace.mkdir()

    with pytest.raises(ValueError):
        resolve_tool_path(
            "../escape.md",
            workspace_root=workspace,
        )


def test_resolve_tool_path_refuses_control_chars(tmp_path: Path):
    workspace = tmp_path / "ws"
    workspace.mkdir()

    with pytest.raises(ValueError, match="control characters"):
        resolve_tool_path(
            "src/code.\x07py",
            workspace_root=workspace,
        )


def test_resolve_tool_path_refuses_mid_path_tilde(tmp_path: Path):
    workspace = tmp_path / "ws"
    workspace.mkdir()

    with pytest.raises(ValueError, match="leading home marker"):
        resolve_tool_path(
            "src/foo~bar.py",
            workspace_root=workspace,
        )


def test_resolve_tool_path_follows_symlink_then_rejects_when_target_escapes(
    tmp_path: Path,
):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    ref = tmp_path / "ref"
    ref.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "secret.txt").write_text("nothing\n", encoding="utf-8")
    sneaky = ref / "sneaky"
    sneaky.symlink_to(elsewhere / "secret.txt")

    with pytest.raises(ValueError, match="outside the workspace"):
        resolve_tool_path(
            str(sneaky),
            workspace_root=workspace,
            reference_roots=(ref,),
        )


# --------------------------- secret content check -------------------------


def test_has_secret_shaped_content_passes_prose_with_keyword():
    text = (
        "The deployment uses an API token; never store the token in git.\n"
        "If your password expires, regenerate the credential through the\n"
        "config panel and update the secret manager.\n"
    )

    assert has_secret_shaped_content(text) is False


def test_has_secret_shaped_content_detects_assigned_value():
    text = "api_key=ABCDEF0123456789ZYX987654321"

    assert has_secret_shaped_content(text) is True


def test_has_secret_shaped_content_detects_aws_access_key_id():
    text = "AKIAIOSFODNN7EXAMPLE"

    assert has_secret_shaped_content(text) is True


def test_has_secret_shaped_content_detects_openai_key_prefix():
    text = "sk-test_ABCDEFGHIJKLMNOPQRSTUVWXYZ012345"

    assert has_secret_shaped_content(text) is True


def test_has_secret_shaped_content_detects_pem_block():
    text = "-----BEGIN RSA PRIVATE KEY-----\nMIIB...\n-----END RSA PRIVATE KEY-----\n"

    assert has_secret_shaped_content(text) is True


def test_has_secret_shaped_content_passes_quoted_short_value():
    # Short value below the 16-character threshold is treated as a sample.
    text = 'api_key = "abc"\n'

    assert has_secret_shaped_content(text) is False
