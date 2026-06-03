"""Tests for system-prompt replace/append inputs (`pipy_harness.native.system_prompt_inputs`).

Mirrors Pi's resolvePromptInput + buildSystemPrompt customPrompt/append behavior:
- a value is a file path when it names an existing file (read unbounded),
  otherwise literal text; an unreadable existing path warns and falls back to
  the literal input string (not fail-closed);
- --system-prompt replaces the default base prompt; --append-system-prompt
  (repeatable) appends after the base/custom prompt and before context files;
- auto-discovery of SYSTEM.md (replace) and APPEND_SYSTEM.md (append) under
  project .pipy/ then global <config>/, with the explicit flag winning;
- only safe metadata (source label, sha256, byte length) is exposed, never the
  prompt body.
"""

from __future__ import annotations

from pathlib import Path

from pipy_harness.native.system_prompt_inputs import (
    resolve_prompt_input,
    resolve_system_prompt,
)

DEFAULT = "DEFAULT BASE PROMPT"


def _warns() -> tuple[list[str], object]:
    captured: list[str] = []
    return captured, captured.append


# --- resolve_prompt_input ---------------------------------------------------


def test_literal_text_when_not_a_file(tmp_path: Path) -> None:
    text, was_file = resolve_prompt_input("just some text", cwd=tmp_path)
    assert text == "just some text"
    assert was_file is False


def test_reads_existing_file_unbounded(tmp_path: Path) -> None:
    body = "FILE BODY\n" * 100000  # large, to prove there is no byte cap
    path = tmp_path / "prompt.md"
    path.write_text(body, encoding="utf-8")
    text, was_file = resolve_prompt_input(str(path), cwd=tmp_path)
    assert text == body
    assert was_file is True


def test_relative_file_resolved_against_cwd(tmp_path: Path) -> None:
    (tmp_path / "p.md").write_text("REL BODY", encoding="utf-8")
    text, was_file = resolve_prompt_input("p.md", cwd=tmp_path)
    assert text == "REL BODY"
    assert was_file is True


def test_unreadable_existing_path_warns_and_falls_back_to_literal(tmp_path: Path) -> None:
    # A directory exists but cannot be read as a file: Pi warns and treats the
    # literal input string as the prompt text (not fail-closed).
    a_dir = tmp_path / "adir"
    a_dir.mkdir()
    warnings, warn = _warns()
    text, was_file = resolve_prompt_input(str(a_dir), cwd=tmp_path, warn=warn)
    assert text == str(a_dir)
    assert was_file is False
    assert warnings  # a warning was emitted


# --- resolve_system_prompt: replace -----------------------------------------


def test_replace_via_flag_overrides_default(tmp_path: Path) -> None:
    result = resolve_system_prompt(
        DEFAULT, cwd=tmp_path, config_home=tmp_path / "cfg", system_prompt_source="CUSTOM"
    )
    assert result.base_prompt == "CUSTOM"
    assert result.replaced is True
    assert result.replace_input is not None
    assert result.replace_input.source_label == "--system-prompt"


def test_default_used_when_no_replace(tmp_path: Path) -> None:
    result = resolve_system_prompt(DEFAULT, cwd=tmp_path, config_home=tmp_path / "cfg")
    assert result.base_prompt == DEFAULT
    assert result.replaced is False
    assert result.replace_input is None


def test_replace_via_project_system_md_when_no_flag(tmp_path: Path) -> None:
    (tmp_path / ".pipy").mkdir()
    (tmp_path / ".pipy" / "SYSTEM.md").write_text("PROJECT SYSTEM", encoding="utf-8")
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    (cfg / "SYSTEM.md").write_text("GLOBAL SYSTEM", encoding="utf-8")
    result = resolve_system_prompt(DEFAULT, cwd=tmp_path, config_home=cfg)
    # Project .pipy/SYSTEM.md wins over global <config>/SYSTEM.md.
    assert result.base_prompt == "PROJECT SYSTEM"
    assert result.replace_input is not None
    assert ".pipy/SYSTEM.md" in result.replace_input.source_label


def test_replace_via_global_system_md_when_no_project(tmp_path: Path) -> None:
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    (cfg / "SYSTEM.md").write_text("GLOBAL SYSTEM", encoding="utf-8")
    result = resolve_system_prompt(DEFAULT, cwd=tmp_path, config_home=cfg)
    assert result.base_prompt == "GLOBAL SYSTEM"


def test_flag_wins_over_system_md_file(tmp_path: Path) -> None:
    (tmp_path / ".pipy").mkdir()
    (tmp_path / ".pipy" / "SYSTEM.md").write_text("PROJECT SYSTEM", encoding="utf-8")
    result = resolve_system_prompt(
        DEFAULT, cwd=tmp_path, config_home=tmp_path / "cfg", system_prompt_source="FLAG"
    )
    assert result.base_prompt == "FLAG"


# --- resolve_system_prompt: append ------------------------------------------


def test_append_via_flag_repeatable(tmp_path: Path) -> None:
    result = resolve_system_prompt(
        DEFAULT,
        cwd=tmp_path,
        config_home=tmp_path / "cfg",
        append_sources=["EXTRA ONE", "EXTRA TWO"],
    )
    assert result.base_prompt == f"{DEFAULT}\n\nEXTRA ONE\n\nEXTRA TWO"
    assert len(result.append_inputs) == 2


def test_append_via_file(tmp_path: Path) -> None:
    (tmp_path / "extra.md").write_text("FROM FILE", encoding="utf-8")
    result = resolve_system_prompt(
        DEFAULT,
        cwd=tmp_path,
        config_home=tmp_path / "cfg",
        append_sources=[str(tmp_path / "extra.md")],
    )
    assert result.base_prompt == f"{DEFAULT}\n\nFROM FILE"


def test_append_via_append_system_md_when_no_flag(tmp_path: Path) -> None:
    (tmp_path / ".pipy").mkdir()
    (tmp_path / ".pipy" / "APPEND_SYSTEM.md").write_text("PROJECT APPEND", encoding="utf-8")
    result = resolve_system_prompt(DEFAULT, cwd=tmp_path, config_home=tmp_path / "cfg")
    assert result.base_prompt == f"{DEFAULT}\n\nPROJECT APPEND"


def test_append_flag_wins_over_append_system_md(tmp_path: Path) -> None:
    (tmp_path / ".pipy").mkdir()
    (tmp_path / ".pipy" / "APPEND_SYSTEM.md").write_text("FILE APPEND", encoding="utf-8")
    result = resolve_system_prompt(
        DEFAULT,
        cwd=tmp_path,
        config_home=tmp_path / "cfg",
        append_sources=["FLAG APPEND"],
    )
    assert result.base_prompt == f"{DEFAULT}\n\nFLAG APPEND"


def test_replace_and_append_compose(tmp_path: Path) -> None:
    result = resolve_system_prompt(
        DEFAULT,
        cwd=tmp_path,
        config_home=tmp_path / "cfg",
        system_prompt_source="CUSTOM BASE",
        append_sources=["APPENDED"],
    )
    assert result.base_prompt == "CUSTOM BASE\n\nAPPENDED"


# --- safe metadata ----------------------------------------------------------


def test_safe_metadata_has_no_body(tmp_path: Path) -> None:
    result = resolve_system_prompt(
        DEFAULT,
        cwd=tmp_path,
        config_home=tmp_path / "cfg",
        system_prompt_source="SECRET CUSTOM PROMPT BODY",
        append_sources=["SECRET APPEND BODY"],
    )
    meta = result.safe_metadata()
    blob = repr(meta)
    assert "SECRET CUSTOM PROMPT BODY" not in blob
    assert "SECRET APPEND BODY" not in blob
    # Metadata carries source label, sha256, byte length.
    assert meta["system_prompt_replaced"] is True
    assert meta["system_prompt_replace"]["byte_length"] == len("SECRET CUSTOM PROMPT BODY")
    assert len(meta["system_prompt_replace"]["sha256"]) == 64
    assert meta["system_prompt_append"][0]["byte_length"] == len("SECRET APPEND BODY")
