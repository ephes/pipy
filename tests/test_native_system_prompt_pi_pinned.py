"""pipy's system prompt against Pi's own ``buildSystemPromptSections`` output.

``tests/fixtures/pi_system_prompt_sections.json`` was produced by running
``scripts/parity_checks/pi_system_prompt_driver.mts`` (Pi's real
``system-prompt.ts`` and tool prompt contributions at pi-mono ``1b347794e``)
under Node, through ``scripts/parity_checks/pi_system_prompt_fixture.py
--write``. Fed Pi's product name, paths and doc topics, pipy's builder must
reproduce every case byte for byte; pipy's own prompt differs only by those
inputs. ``test_fixture_is_current_with_pi`` reruns the driver when Node and a
pi-mono checkout are present and is skipped otherwise.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from pipy_harness.native.skills import SkillFile
from pipy_harness.native.system_prompt_sections import (
    BUILTIN_TOOL_PROMPTS,
    DEFAULT_PREAMBLE,
    DocsInfo,
    SystemPromptTemplate,
    pipy_docs_info,
)
from pipy_harness.native.workspace_context import PROJECT_CONTEXT_INTRO

REPO = Path(__file__).resolve().parents[1]
FIXTURE = json.loads(
    (REPO / "tests" / "fixtures" / "pi_system_prompt_sections.json").read_text(
        encoding="utf-8"
    )
)

# Pi's `docs` topics (`system-prompt.ts`).
PI_DOC_TOPICS = (
    ("extensions", "docs/extensions.md, examples/extensions/"),
    ("themes", "docs/themes.md"),
    ("skills", "docs/skills.md"),
    ("prompt templates", "docs/prompt-templates.md"),
    ("TUI components", "docs/tui.md"),
    ("keybindings", "docs/keybindings.md"),
    ("SDK integrations", "docs/sdk.md"),
    ("custom providers", "docs/custom-provider.md"),
    ("adding models", "docs/models.md"),
    ("pi packages", "docs/packages.md"),
    ("environment variables", "docs/environment-variables.md"),
    ("MCP servers", "docs/mcp.md"),
    (
        "codemode scripts and non-LLM models such as classifiers and image models",
        "docs/codemode.md",
    ),
)


def _pi_docs() -> DocsInfo:
    paths = FIXTURE["paths"]
    return DocsInfo(
        product="pi",
        title="Pi",
        readme=paths["readme"],
        docs=paths["docs"],
        examples=paths["examples"],
        topics=PI_DOC_TOPICS,
        link_example="tui.md for TUI API details",
    )


def _skill(entry: dict) -> SkillFile:
    return SkillFile(
        path_label=entry["filePath"],
        name=entry["name"],
        description=entry["description"],
        body="",
        sha256="0" * 64,
        byte_length=0,
        truncated=False,
        absolute_path=Path(entry["filePath"]),
        disable_model_invocation=entry["disableModelInvocation"],
    )


def _project_context(files: list[dict]) -> str:
    if not files:
        return ""
    return "\n\n".join(
        [PROJECT_CONTEXT_INTRO]
        + [
            f'<project_instructions path="{f["path"]}">\n{f["content"]}\n'
            "</project_instructions>"
            for f in files
        ]
    )


def _pipy_sections(case: dict) -> list[list[str]]:
    template = SystemPromptTemplate(
        cwd="C:\\w\\proj",
        custom_preamble=case.get("customPrompt"),
        addendum=case.get("appendSystemPrompt", ""),
        project_context=_project_context(case.get("contextFiles", [])),
        skills=tuple(_skill(entry) for entry in case.get("skills", [])),
        tool_snippets=case["toolSnippets"],
        tool_guidelines={
            name: tuple(rules) for name, rules in case["toolGuidelines"].items()
        },
        docs=_pi_docs(),
        product="pi",
    )
    return [list(pair) for pair in template.sections(case["selectedTools"])]


@pytest.mark.parametrize("name", sorted(FIXTURE["cases"]))
def test_sections_match_pi(name: str) -> None:
    case = FIXTURE["cases"][name]
    assert _pipy_sections(case["input"]) == case["sections"]


def test_builtin_tool_prompts_are_pis_contributions() -> None:
    pi = FIXTURE["cases"]["pi_default"]["input"]
    # The pinned driver covers the seven existing tools. Optional codemode
    # adapts Pi's JavaScript contribution to CM1's sequential Python contract.
    assert set(BUILTIN_TOOL_PROMPTS) == set(pi["toolSnippets"]) | {"codemode"}
    assert {
        name: p.snippet
        for name, p in BUILTIN_TOOL_PROMPTS.items()
        if name != "codemode"
    } == pi["toolSnippets"]
    guidelines = {
        name: list(p.guidelines)
        for name, p in BUILTIN_TOOL_PROMPTS.items()
        if p.guidelines and name != "codemode"
    }
    # pipy's bash tool sets no PI_* session variables, so it has no guideline
    # (Pi without `exposeSessionEnvironment`).
    assert guidelines == {k: v for k, v in pi["toolGuidelines"].items() if k != "bash"}
    assert pi["toolGuidelines"]["bash"] == [
        "You can inspect PI_* environment variables for current model and "
        "session details."
    ]


def test_codemode_prompt_matches_sequential_python_contract() -> None:
    contribution = BUILTIN_TOOL_PROMPTS["codemode"]
    assert contribution.snippet == "Run Python that calls other tools"
    assert contribution.guidelines == (
        "Use codemode to chain several tool calls or filter large output "
        "in one step instead of many separate calls.",
    )


def test_pipy_prompt_is_pis_with_pipys_name_and_docs() -> None:
    sections = dict(
        SystemPromptTemplate(cwd="/w").sections(
            ("read", "ls", "grep", "find", "write", "edit", "bash")
        )
    )
    pi = dict(FIXTURE["cases"]["pipy_default"]["sections"])

    assert list(sections) == ["preamble", "tools", "rules", "docs", "cwd"]
    assert (
        sections["preamble"]
        == DEFAULT_PREAMBLE
        == pi["preamble"].replace("inside pi,", "inside pipy,")
    )
    assert sections["tools"] == pi["tools"]
    assert sections["rules"] == pi["rules"]
    docs = pipy_docs_info()
    assert Path(docs.readme) == REPO / "README.md"
    assert Path(docs.docs) == REPO / "docs"
    for _, refs in docs.topics:
        for ref in refs.split(", "):
            root = Path(docs.examples) if ref.startswith("examples/") else REPO
            target = (
                ref.removeprefix("examples/") if ref.startswith("examples/") else ref
            )
            assert (root / target).exists(), ref
    assert sections["docs"].startswith(
        "<docs>\nPipy documentation (read only when the user asks about pipy itself"
    )


def test_fixture_is_current_with_pi() -> None:
    script = REPO / "scripts" / "parity_checks" / "pi_system_prompt_fixture.py"
    proc = subprocess.run(
        [sys.executable, str(script)],
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    if proc.returncode == 2:
        pytest.skip(proc.stderr.strip() or "Pi reference unavailable")
    assert proc.returncode == 0, proc.stdout + proc.stderr
