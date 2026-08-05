"""Focused contracts for repository-wide formatting gate ownership."""

from __future__ import annotations

import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
FORMAT_CHECK_COMMAND = "uv run ruff format --check ."
FORMAT_WRITE_COMMAND = "uv run ruff format ."


def _read(path: str) -> str:
    return (REPO_ROOT / path).read_text(encoding="utf-8")


def test_local_and_ci_gates_share_the_format_check_recipe() -> None:
    justfile = _read("justfile")
    workflow = _read(".github/workflows/ci.yml")

    assert (
        "# Check repository-wide Python formatting without changing files.\n"
        "format-check:\n"
        f"    {FORMAT_CHECK_COMMAND}\n"
    ) in justfile
    assert "check: lint format-check typecheck test" in justfile
    assert justfile.count(FORMAT_CHECK_COMMAND) == 1

    assert workflow.count("run: just format-check") == 1
    assert FORMAT_CHECK_COMMAND not in workflow


def test_contributor_docs_name_format_check_and_write_commands() -> None:
    readme = _read("README.md")

    assert "just format-check  # ruff format --check across the repository" in readme
    assert f"`{FORMAT_CHECK_COMMAND}`" in readme
    assert f"`{FORMAT_WRITE_COMMAND}`" in readme
    assert "(or `just format`)" in readme


def test_formatter_gate_has_no_custom_repository_exclusions() -> None:
    config = tomllib.loads(_read("pyproject.toml"))
    ruff = config["tool"]["ruff"]

    assert "exclude" not in ruff
    assert "extend-exclude" not in ruff
    assert not (REPO_ROOT / ".ruff.toml").exists()
    assert not (REPO_ROOT / "ruff.toml").exists()


def test_ruff_quality_gates_select_only_the_exact_ratchet_rules() -> None:
    config = tomllib.loads(_read("pyproject.toml"))
    lint = config["tool"]["ruff"]["lint"]
    extend_select = lint["extend-select"]
    configured_selectors = lint.get("select", []) + extend_select
    ignored_selectors = lint.get("ignore", []) + lint.get("extend-ignore", [])
    for selectors in lint.get("per-file-ignores", {}).values():
        ignored_selectors.extend(selectors)

    assert extend_select == ["C901", "I001", "UP035", "B008", "B905", "BLE001"]
    assert configured_selectors == extend_select
    for category, exact_selectors in (
        ("C", {"C901"}),
        ("I", {"I001"}),
        ("UP", {"UP035"}),
        ("B", {"B008", "B905", "BLE001"}),
        ("BLE", {"BLE001"}),
    ):
        assert {
            selector
            for selector in configured_selectors
            if selector == "ALL" or selector.startswith(category)
        } == exact_selectors

    protected_selectors = ("I001", "UP035", "B008", "B905", "BLE001")
    assert all(
        ignored_selector != "ALL"
        and all(
            not protected_selector.startswith(ignored_selector)
            for protected_selector in protected_selectors
        )
        for ignored_selector in ignored_selectors
    )


def test_readme_names_the_codex_websocket_dependency() -> None:
    readme = _read("README.md")

    assert "Codex WebSocket transport uses" in readme
    assert "declared `websockets` dependency" in readme


# --- decomposition size ratchet ---------------------------------------------
#
# The previous architecture program shrank these two files and they grew ~14%
# straight back, because every gate in this repository constrains *direction*
# (no new complex function, no new boundary violation) and none constrains
# *mass*. The concrete coding session in particular is protected as the apex
# of the import DAG, which means nothing gates what it accretes.
#
# These bounds are the mass gate for the god-file decomposition
# (docs/plans/2026-08-03-god-file-decomposition-plan.md). Lower them in any
# slice that shrinks a file; never raise one. A slice that needs a bound raised
# is a slice that put code back.
_SIZE_RATCHET = {
    "src/pipy_harness/native/tui.py": 1466,
    "src/pipy_harness/native/coding/session.py": 336,
}

# Nothing else under `native/` may quietly become the next god file while the
# named two are being burned down. Set at today's largest non-ratcheted module
# (`session.py`).
_NATIVE_FILE_CEILING = 2488


def _line_count(path: Path) -> int:
    return len(path.read_text(encoding="utf-8").splitlines())


def test_coding_session_has_no_composition_c901_pin() -> None:
    config = tomllib.loads(_read("pyproject.toml"))
    per_file_ignores = config["tool"]["ruff"]["lint"]["per-file-ignores"]

    assert "src/pipy_harness/native/coding/session.py" not in per_file_ignores
    retired_path = "src/pipy_harness/native/tool_" + "loop_session.py"
    assert retired_path not in per_file_ignores


def test_slice_46_retires_activation_module_and_keeps_new_owner_below_ceiling() -> None:
    config = tomllib.loads(_read("pyproject.toml"))
    per_file_ignores = config["tool"]["ruff"]["lint"]["per-file-ignores"]
    retired_relative = "src/pipy_harness/native/extension_" + "runtime.py"
    retired_module = "pipy_harness.native.extension_" + "runtime"
    activation_relative = "src/pipy_harness/native/extensions/activation.py"
    scanned = [
        *REPO_ROOT.joinpath("src").rglob("*.py"),
        *REPO_ROOT.joinpath("scripts").rglob("*.py"),
        *REPO_ROOT.joinpath("tests").rglob("*.py"),
    ]

    assert not (REPO_ROOT / retired_relative).exists()
    assert not {
        str(path.relative_to(REPO_ROOT)): retired_module
        for path in scanned
        if retired_module in path.read_text(encoding="utf-8")
    }
    assert _line_count(REPO_ROOT / activation_relative) <= _NATIVE_FILE_CEILING
    assert activation_relative not in per_file_ignores
    assert retired_relative not in per_file_ignores


def test_slice_45_owns_the_session_and_command_router_exactly_once() -> None:
    retired_path = "src/pipy_harness/native/tool_" + "loop_session.py"
    old_session = REPO_ROOT / retired_path
    session = _read("src/pipy_harness/native/coding/session.py")
    router = _read("src/pipy_harness/native/repl/command_router.py")
    collaborators = _read("src/pipy_harness/native/repl/collaborators.py")
    wiring = _read("src/pipy_harness/native/repl/wiring.py")

    assert not old_session.exists()
    assert session.count("class CodingSession:") == 1
    assert "class BuiltinCommandInterpreter:" in router
    assert "class BuiltinCommandInterpreter:" not in collaborators
    assert "from pipy_harness.native.repl.command_router import " in wiring
    assert "from pipy_harness.native.coding.session import " not in wiring


def test_slice_45_retires_old_session_surfaces_and_test_filenames() -> None:
    forbidden = (
        "NativeTool" + "ReplSession",
        "NativeTool" + "ReplResult",
        "PipyNativeTool" + "ReplAdapter",
        "build_" + "repl_result",
        "pipy_harness.native.tool_" + "loop_session",
        "native/tool_" + "loop_session.py",
    )
    scanned = [
        *REPO_ROOT.joinpath("src").rglob("*.py"),
        *REPO_ROOT.joinpath("scripts").rglob("*.py"),
        *REPO_ROOT.joinpath("tests").rglob("*.py"),
    ]

    assert not {
        str(path.relative_to(REPO_ROOT)): retired
        for path in scanned
        for retired in forbidden
        if retired in path.read_text(encoding="utf-8")
    }
    assert not [
        path.name
        for path in REPO_ROOT.joinpath("tests").glob("*.py")
        if "tool_" + "loop" in path.name
        or "pipy_native_tool_" + "repl_adapter" in path.name
    ]


def test_decomposition_size_ratchet_never_grows() -> None:
    for relative, bound in _SIZE_RATCHET.items():
        actual = _line_count(REPO_ROOT / relative)
        assert actual <= bound, (
            f"{relative} grew to {actual} lines, above its ratchet of {bound}. "
            "Move code out rather than raising the bound."
        )


def test_no_other_native_module_becomes_the_next_god_file() -> None:
    offenders = {
        str(path.relative_to(REPO_ROOT)): _line_count(path)
        for path in (REPO_ROOT / "src/pipy_harness/native").rglob("*.py")
        if "__pycache__" not in path.parts
        and str(path.relative_to(REPO_ROOT)) not in _SIZE_RATCHET
        and _line_count(path) > _NATIVE_FILE_CEILING
    }

    assert not offenders, (
        f"native modules above the {_NATIVE_FILE_CEILING}-line ceiling: {offenders}"
    )
