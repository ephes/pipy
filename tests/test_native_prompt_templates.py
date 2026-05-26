"""Focused tests for the workspace prompt-template discovery loader.

These tests pin the discovery rules listed in
`pipy_harness.native.prompt_templates`. They never wire the loader
into a provider, the REPL, or the session archive; the integrator
wires the `/template <name>` slash command separately.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from pipy_harness.native._resource_files import (
    PIPY_CONFIG_DIR_NAME,
    PIPY_CONFIG_HOME_ENV,
    XDG_CONFIG_HOME_ENV,
)
from pipy_harness.native.prompt_templates import (
    PromptTemplate,
    discover_workspace_prompt_templates,
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
        description="Code review template",
        body="please review\n",
    )
    _write_template(
        templates_dir,
        filename="bugfix.md",
        name="bugfix",
        description="Bug fix template",
        body="please fix\n",
    )

    templates, cap_reached = _discover(workspace)

    assert cap_reached is False
    names = {template.name for template in templates}
    assert names == {"review", "bugfix"}
    review = next(template for template in templates if template.name == "review")
    assert review.description == "Code review template"
    assert "please review" in review.body
    assert review.path_label == ".pipy/templates/review.md"
    assert review.truncated is False


def test_discovers_global_templates_via_pipy_config_home(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    pipy_home = tmp_path / "pipy-home"
    global_templates_dir = pipy_home / "templates"
    _write_template(
        global_templates_dir,
        filename="brief.md",
        name="brief",
        description="Short summary",
        body="please summarize\n",
    )

    env = {PIPY_CONFIG_HOME_ENV: str(pipy_home)}
    templates, cap_reached = _discover(workspace, env=env)

    assert cap_reached is False
    assert len(templates) == 1
    only = templates[0]
    assert only.name == "brief"
    assert only.path_label == "<global>/templates/brief.md"
    assert "please summarize" in only.body


def test_global_root_precedence_uses_pipy_config_home_first(
    tmp_path: Path,
) -> None:
    workspace = _make_workspace(tmp_path)
    pipy_home = tmp_path / "pipy-home"
    _write_template(
        pipy_home / "templates",
        filename="review.md",
        name="review-from-pipy-home",
        description="winning",
    )
    xdg_root = tmp_path / "xdg"
    xdg_pipy = xdg_root / PIPY_CONFIG_DIR_NAME / "templates"
    _write_template(
        xdg_pipy,
        filename="review.md",
        name="review-from-xdg",
        description="losing",
    )
    home = tmp_path / "home"
    home_pipy = home / ".config" / PIPY_CONFIG_DIR_NAME / "templates"
    _write_template(
        home_pipy,
        filename="review.md",
        name="review-from-home",
        description="losing",
    )

    env = {
        PIPY_CONFIG_HOME_ENV: str(pipy_home),
        XDG_CONFIG_HOME_ENV: str(xdg_root),
    }
    templates, _ = _discover(workspace, env=env, home_dir=home)

    assert len(templates) == 1
    assert templates[0].name == "review-from-pipy-home"
    assert templates[0].path_label == "<global>/templates/review.md"


def test_dedupes_by_canonical_path(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    templates_dir = workspace / ".pipy" / "templates"
    shared = _write_template(
        templates_dir,
        filename="shared.md",
        name="shared",
        description="shared",
    )
    (templates_dir / "alias.md").symlink_to(shared)

    templates, _ = _discover(workspace)

    assert len(templates) == 1
    assert templates[0].name == "shared"


def test_refuses_symlink_outside_workspace(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    secret = outside_dir / "secret.md"
    secret.write_text("never load me\n", encoding="utf-8")

    templates_dir = workspace / ".pipy" / "templates"
    templates_dir.mkdir(parents=True)
    (templates_dir / "leak.md").symlink_to(secret)

    _write_template(
        templates_dir,
        filename="legitimate.md",
        name="legitimate",
        description="real",
        body="real body\n",
    )

    templates, _ = _discover(workspace)

    assert all(template.name != "leak" for template in templates)
    assert all("never load me" not in template.body for template in templates)
    assert any(template.name == "legitimate" for template in templates)


def test_per_file_byte_cap_truncates_with_marker(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    templates_dir = workspace / ".pipy" / "templates"
    payload = "x" * 4096
    _write_template(
        templates_dir,
        filename="big.md",
        name="big",
        description="big",
        body=payload,
    )

    templates, cap_reached = _discover(workspace, per_file_byte_cap=1024)
    assert cap_reached is False
    big = next(template for template in templates if template.name == "big")
    assert big.truncated is True
    on_disk = (templates_dir / "big.md").read_bytes()
    assert big.byte_length == len(on_disk)
    assert big.sha256 == hashlib.sha256(on_disk).hexdigest()
    assert "[pipy: resource file truncated at 1024 bytes]" in big.body


def test_total_byte_cap_reached_flag_set(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    templates_dir = workspace / ".pipy" / "templates"
    payload = "a" * 800
    for index, letter in enumerate("abc"):
        _write_template(
            templates_dir,
            filename=f"{letter}.md",
            name=letter,
            description=f"file {index}",
            body=payload,
        )

    templates, cap_reached = _discover(
        workspace,
        per_file_byte_cap=4096,
        total_byte_cap=900,
    )

    assert cap_reached is True
    # Only one file fits under the 900-byte total cap.
    assert len(templates) == 1


def test_frontmatter_parsed_with_name_and_description(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    templates_dir = workspace / ".pipy" / "templates"
    _write_template(
        templates_dir,
        filename="review.md",
        name="review-pr",
        description="Review the pending PR",
        body="please review\n",
    )
    plain = templates_dir / "plain.md"
    templates_dir.mkdir(parents=True, exist_ok=True)
    plain.write_text("just a body\n", encoding="utf-8")

    templates, _ = _discover(workspace)
    by_name = {template.name: template for template in templates}
    assert "review-pr" in by_name
    review = by_name["review-pr"]
    assert review.description == "Review the pending PR"
    assert "please review" in review.body
    assert "---" not in review.body
    assert "plain" in by_name
    plain_template = by_name["plain"]
    assert plain_template.description == ""
    assert "just a body" in plain_template.body


def test_find_template_by_name_returns_match(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    templates_dir = workspace / ".pipy" / "templates"
    _write_template(
        templates_dir,
        filename="review.md",
        name="review",
        description="Code review template",
        body="please review\n",
    )
    _write_template(
        templates_dir,
        filename="brief.md",
        name="brief",
        description="Short summary",
        body="please summarize\n",
    )

    templates, _ = _discover(workspace)

    review = find_template_by_name(templates, "review")
    assert review is not None
    assert review.name == "review"
    assert "please review" in review.body

    # Case-sensitive miss.
    assert find_template_by_name(templates, "Review") is None
    assert find_template_by_name(templates, "missing") is None


def test_no_template_body_in_returned_metadata_archive_function(
    tmp_path: Path,
) -> None:
    workspace = _make_workspace(tmp_path)
    templates_dir = workspace / ".pipy" / "templates"
    _write_template(
        templates_dir,
        filename="review.md",
        name="review",
        description="Code review template",
        body="sensitive template body that must never reach the archive\n",
    )

    templates, _ = _discover(workspace)
    safe = safe_prompt_template_metadata(templates)

    assert len(safe) == 1
    entry = safe[0]
    assert set(entry.keys()) == {"path_label", "sha256", "byte_length", "truncated"}
    assert entry["path_label"] == ".pipy/templates/review.md"
    assert "sensitive template body" not in str(entry)
    assert "name" not in entry
    assert "description" not in entry
    assert "body" not in entry
