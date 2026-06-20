"""Focused tests for the workspace prompt-template discovery loader.

These tests pin the discovery rules and argument expansion listed in
`pipy_harness.native.prompt_templates`. They never wire the loader
into a provider, the REPL, or the session archive; the integrator
wires `find_template_by_name` + `expand_template_body` into each
template's own `/<name>` slash command separately.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from pipy_harness.native._resource_files import PIPY_CONFIG_HOME_ENV
from pipy_harness.native.prompt_templates import (
    PromptTemplate,
    discover_workspace_prompt_templates,
    expand_template_body,
    find_template_by_name,
    safe_prompt_template_metadata,
)


def _empty_env() -> dict[str, str]:
    return {}


def _discover(
    workspace: Path,
    *,
    env: dict[str, str] | None = None,
    home_dir: Path | None = None,
    per_file_byte_cap: int = 64 * 1024,
    total_byte_cap: int = 256 * 1024,
) -> tuple[list[PromptTemplate], bool]:
    return discover_workspace_prompt_templates(
        workspace,
        config_home_env=env if env is not None else _empty_env(),
        home_dir=home_dir if home_dir is not None else workspace,
        per_file_byte_cap=per_file_byte_cap,
        total_byte_cap=total_byte_cap,
    )


def _make_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    return workspace


def _write_template(
    directory: Path,
    *,
    filename: str,
    name: str | None = None,
    description: str | None = None,
    body: str = "template body\n",
) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    parts: list[str] = []
    if name is not None or description is not None:
        parts.append("---")
        if name is not None:
            parts.append(f"name: {name}")
        if description is not None:
            parts.append(f"description: {description}")
        parts.append("---")
        parts.append("")
    parts.append(body)
    path = directory / filename
    path.write_text("\n".join(parts), encoding="utf-8")
    return path


def test_no_templates_dir_returns_empty_list(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    templates, cap_reached = _discover(workspace)
    assert templates == []
    assert cap_reached is False


def test_discovers_workspace_templates(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    templates_dir = workspace / ".pipy" / "templates"
    _write_template(
        templates_dir,
        filename="review.md",
        name="review",
        description="Review the diff",
        body="please review $ARGUMENTS\n",
    )

    templates, cap_reached = _discover(workspace)

    assert cap_reached is False
    assert len(templates) == 1
    review = templates[0]
    assert review.name == "review"
    assert review.description == "Review the diff"
    assert review.path_label == ".pipy/templates/review.md"
    assert "please review $ARGUMENTS" in review.body


def test_discovers_global_templates_via_pipy_config_home(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    pipy_home = tmp_path / "pipy-home"
    _write_template(
        pipy_home / "templates",
        filename="explain.md",
        name="explain",
        description="Explain code",
        body="explain $1\n",
    )

    env = {PIPY_CONFIG_HOME_ENV: str(pipy_home)}
    templates, _ = _discover(workspace, env=env)

    assert len(templates) == 1
    assert templates[0].name == "explain"
    assert templates[0].path_label == "<global>/templates/explain.md"


def test_find_template_by_name_case_sensitive(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    templates_dir = workspace / ".pipy" / "templates"
    _write_template(
        templates_dir,
        filename="review.md",
        name="Review",
        description="capitalized",
        body="body\n",
    )

    templates, _ = _discover(workspace)
    assert find_template_by_name(templates, "Review") is not None
    assert find_template_by_name(templates, "review") is None
    assert find_template_by_name(templates, "missing") is None


def test_per_file_byte_cap_truncates_with_marker(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    templates_dir = workspace / ".pipy" / "templates"
    _write_template(
        templates_dir,
        filename="big.md",
        name="big",
        description="big",
        body="x" * 4096,
    )

    templates, cap_reached = _discover(workspace, per_file_byte_cap=1024)
    assert cap_reached is False
    big = templates[0]
    assert big.truncated is True
    on_disk = (templates_dir / "big.md").read_bytes()
    assert big.byte_length == len(on_disk)
    assert big.sha256 == hashlib.sha256(on_disk).hexdigest()
    assert "[pipy: resource file truncated at 1024 bytes]" in big.body


def test_safe_metadata_excludes_body(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    templates_dir = workspace / ".pipy" / "templates"
    _write_template(
        templates_dir,
        filename="review.md",
        name="review",
        description="Review the diff",
        body="sensitive template that must never reach the archive\n",
    )

    templates, _ = _discover(workspace)
    safe = safe_prompt_template_metadata(templates)

    assert len(safe) == 1
    entry = safe[0]
    assert set(entry.keys()) == {"path_label", "sha256", "byte_length", "truncated"}
    assert "sensitive template" not in str(entry)
    assert "name" not in entry
    assert "description" not in entry
    assert "body" not in entry


# --- expand_template_body ---------------------------------------------------


def test_expand_replaces_arguments_token() -> None:
    out = expand_template_body("please review $ARGUMENTS now", "the staged diff")
    assert out == "please review the staged diff now"


def test_expand_replaces_braced_arguments_token() -> None:
    out = expand_template_body("review ${ARGUMENTS}", "diff")
    assert out == "review diff"


def test_expand_replaces_positional_tokens() -> None:
    out = expand_template_body("compare $1 with $2", "alpha beta gamma")
    assert out == "compare alpha with beta"


def test_expand_out_of_range_positional_is_empty() -> None:
    out = expand_template_body("only $1 and $2 here", "solo")
    assert out == "only solo and  here"


def test_expand_appends_arguments_when_no_placeholder() -> None:
    out = expand_template_body("Summarize the file.\n", "src/main.py")
    assert out == "Summarize the file.\n\nsrc/main.py"


def test_expand_no_placeholder_no_arguments_returns_body() -> None:
    out = expand_template_body("Summarize the file.\n", "")
    assert out == "Summarize the file.\n"


def test_expand_empty_body_with_arguments_returns_arguments() -> None:
    out = expand_template_body("", "just this")
    assert out == "just this"


def test_expand_does_not_resolve_shell_metacharacters() -> None:
    out = expand_template_body("run $ARGUMENTS", "$(rm -rf /) `id`")
    # The expansion is purely textual; metacharacters survive verbatim and
    # are never executed by the loader.
    assert out == "run $(rm -rf /) `id`"
