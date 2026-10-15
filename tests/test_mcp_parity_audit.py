"""The row audit of the MCP parity plan (plan slice S40).

``docs/acceptance/2026-10-13-mcp-parity.md`` lists every row of the plan's
checklist with the tests that pin it. These tests keep that table in step
with the plan and the test suite: every row is there once, every test it
names exists, every deviation it names is in the plan's section 3, and the
plan states the same result. They do not prove that a test covers its row;
the note says how that was read.

The last tests are the pins of the documentation rows DOC-01 to DOC-05: the
user documents name what those rows ask for.
"""

from __future__ import annotations

import re
from collections import Counter
from functools import cache
from pathlib import Path

from pipy_harness.native.mcp.shell_cli import HELP

_ROOT = Path(__file__).resolve().parents[1]
_PLAN = _ROOT / "docs" / "specs" / "2026-10-12-mcp-parity-plan.md"
_NOTE = _ROOT / "docs" / "acceptance" / "2026-10-13-mcp-parity.md"
_DOCS = _ROOT / "docs"

_ROW_ID = r"[A-Z]{3,4}-\d{2}"
_STATES = ("closed", "closed, with limits", "part open", "open")


@cache
def _plan_rows() -> tuple[str, ...]:
    """The row ids of the checklist, plan section 2, in order."""

    checklist = _PLAN.read_text().split("## 3. Proposed deviations")[0]
    return tuple(re.findall(rf"^\| ({_ROW_ID}) \|", checklist, flags=re.MULTILINE))


@cache
def _audit() -> tuple[tuple[str, str, str], ...]:
    """``(row, state, tests cell)`` for every line of the note's audit tables."""

    audit = _NOTE.read_text().split("## Row audit")[1]
    audit = audit.split("## What this note does not show")[0]
    lines = re.findall(
        rf"^\| ({_ROW_ID}) \| [^|]* \| ([^|]*) \| ([^|]*) \|", audit, flags=re.MULTILINE
    )
    return tuple((row, state.strip(), tests.strip()) for row, state, tests in lines)


def _named_tests(cell: str) -> list[tuple[str, str]]:
    """``(file, test)`` pairs of a tests cell: `` `file.py`: `test_a`, `test_b`; ... ``."""

    pairs: list[tuple[str, str]] = []
    for part in cell.split(";"):
        names = re.findall(r"`([^`]+)`", part)
        if not names:
            continue
        assert names[0].endswith(".py"), cell
        assert len(names) > 1, cell
        pairs.extend((names[0], name) for name in names[1:])
    return pairs


@cache
def _tests_of(file: str) -> frozenset[str]:
    source = (_ROOT / "tests" / file).read_text()
    return frozenset(
        re.findall(r"^\s*(?:async )?def (test_\w+)", source, flags=re.MULTILINE)
    )


def _section(page: str, heading: str) -> str:
    """The text of a page from ``heading`` to the next heading of its level."""

    text = (_DOCS / page).read_text()
    level = heading.split(" ", 1)[0]
    start = text.index(f"\n{heading}\n")
    end = re.search(rf"^{level} ", text[start + 1 + len(heading) :], flags=re.MULTILINE)
    return (
        text[start:]
        if end is None
        else text[start : start + len(heading) + end.start()]
    )


# --- the audit tables -------------------------------------------------------


def test_the_plan_states_the_number_of_its_rows() -> None:
    rows = _plan_rows()
    counts = Counter(row.split("-")[0] for row in rows)

    assert len(rows) == len(set(rows)) == 292
    stated = re.search(r"Row count: (\d+) \(([^;]+);", _PLAN.read_text())
    assert stated is not None
    assert int(stated.group(1)) == len(rows)
    per_section = dict(
        (name, int(number))
        for name, number in re.findall(r"([A-Z]+) (\d+)", stated.group(2))
    )
    assert per_section == dict(counts)


def test_every_row_of_the_plan_is_audited_once() -> None:
    assert [row for row, _state, _tests in _audit()] == list(_plan_rows())


def test_every_test_the_audit_names_exists() -> None:
    missing = [
        f"{row}: {file}::{name}"
        for row, _state, cell in _audit()
        for file, name in _named_tests(cell)
        if not (_ROOT / "tests" / file).is_file() or name not in _tests_of(file)
    ]

    assert missing == []


def test_every_row_names_a_test_and_none_is_open() -> None:
    # SLH-13 and the open part of MGR-09 were built after the audit (N2, N3).
    states = {row: state for row, state, _tests in _audit()}
    without = [row for row, _state, cell in _audit() if not _named_tests(cell)]

    assert set(states.values()) <= set(_STATES)
    assert without == []
    assert [row for row, state in states.items() if state == "open"] == []
    assert [row for row, state in states.items() if state == "part open"] == []


def test_the_note_lists_no_row_that_is_not_closed() -> None:
    not_closed = _NOTE.read_text().split("## Not closed")[1].split("\n## ")[0]

    assert re.findall(rf"^\| ({_ROW_ID})", not_closed, flags=re.MULTILINE) == []
    assert "No row is open." in not_closed


def test_the_summary_counts_are_those_of_the_tables() -> None:
    states = Counter(state for _row, state, _tests in _audit())
    summary = _NOTE.read_text().split("## Reference")[0]

    assert f"{states['closed']} are\nclosed with a test" in summary
    assert (
        f"{states['closed, with limits']} are closed with a recorded limit" in summary
    )
    assert sum(states.values()) == 292


def _deviations() -> list[str]:
    """The DEV ids of plan section 3, in table order."""

    section = _PLAN.read_text().split("## 3. Proposed deviations")[1]
    section = section.split("## 4. pipy design")[0]
    return re.findall(r"^\| (DEV-\d{2})[ |]", section, flags=re.MULTILINE)


def test_the_deviations_are_numbered_without_a_gap() -> None:
    deviations = _deviations()

    assert deviations == [f"DEV-{number:02d}" for number in range(1, 86)]
    # A closed deviation keeps its number and says so.
    section = _PLAN.read_text().split("## 3. Proposed deviations")[1]
    closed = re.findall(r"^\| (DEV-\d{2}) \(closed by ", section, flags=re.MULTILINE)
    assert closed == ["DEV-08", "DEV-39", "DEV-42", "DEV-65"]


def test_every_deviation_the_note_names_is_in_the_plan() -> None:
    known = set(_deviations())
    named = set(re.findall(r"DEV-\d{2}", _NOTE.read_text()))

    assert named <= known, sorted(named - known)
    # Every difference the slices recorded (plan 3.1) names its rows, and
    # each of those rows is a row of the checklist.
    recorded = _PLAN.read_text().split("### 3.1 Differences the slices recorded")[1]
    recorded = recorded.split("### 3.2 Audit result")[0]
    cells = re.findall(r"^\| DEV-\d{2} \| ([^|]*) \|", recorded, flags=re.MULTILINE)
    # DEV-15 to DEV-85 without the closed DEV-39, DEV-42 and DEV-65.
    assert len(cells) == 68
    rows = set(_plan_rows())
    for cell in cells:
        assert set(re.findall(_ROW_ID, cell)) <= rows | {"DEV-12"}, cell


def _decision(question: str) -> str:
    """The row of plan section 7 with the owner's decision ``question``."""

    match = re.search(rf"^\| {question} \|.*$", _PLAN.read_text(), flags=re.MULTILINE)
    assert match is not None
    return match.group(0)


def _q11() -> str:
    """The row of plan section 7 with the owner's triage decision."""

    return _decision("Q11")


def test_q11_accepts_the_rows_of_the_triage() -> None:
    accepted = set(re.findall(r"DEV-\d{2}", _q11()))

    assert {f"DEV-{number}" for number in range(69, 80)} <= accepted
    # Added 2026-10-15 under the same triage decision (R7): DEV-79 and the
    # `duration_ms` part of DEV-48.
    assert "DEV-79 (EXT-01" in _q11()
    assert "DEV-48 (EXT-11, the outcome's `duration_ms`" in _q11()
    recorded = _PLAN.read_text().split("### 3.1 Differences the slices recorded")[1]
    recorded = recorded.split("### 3.2 Audit result")[0]
    for deviation, source in re.findall(
        r"^\| (DEV-\d{2}) \|.*\| ([^|]*) \|$", recorded, flags=re.MULTILINE
    ):
        if "accepted in Q11" in source:
            assert deviation in accepted, deviation
        if deviation in accepted:
            assert "proposal" not in source, deviation
    # The acceptance note calls none of them a proposal either.
    assert "proposal DEV-79" not in _NOTE.read_text()
    assert "DEV-79 (INT4) are still proposals" not in _NOTE.read_text()


def test_q12_accepts_the_rows_of_the_third_triage() -> None:
    q12 = _decision("Q12")
    accepted = set(re.findall(r"DEV-\d{2}", q12))

    assert {f"DEV-{number}" for number in range(80, 86)} <= accepted
    assert {"DEV-36", "DEV-40", "DEV-48", "DEV-54", "DEV-59", "DEV-66"} <= accepted
    assert {"DEV-67", "DEV-68"} <= accepted
    # Moved from accepted to fixed by the coordinator: no row for them, and
    # P1 corrects Q11's refutation of round 2's P4.
    fixed = q12.split("Fixed by the slices T1 to T6: ")[1].split(".")[0]
    assert {"P1", "D1", "S3"} <= set(fixed.split(", "))
    recorded = _PLAN.read_text().split("### 3.1 Differences the slices recorded")[1]
    recorded = recorded.split("### 3.2 Audit result")[0]
    for deviation, source in re.findall(
        r"^\| (DEV-\d{2}) \|.*\| ([^|]*) \|$", recorded, flags=re.MULTILINE
    ):
        if "accepted in Q12" in source:
            assert deviation in accepted, deviation
        if deviation in accepted:
            assert "proposal" not in source, deviation


def _deviation(number: int) -> str:
    """The line of plan section 3.1 with ``DEV-<number>``."""

    match = re.search(
        rf"^\| DEV-{number:02d} \|.*$", _PLAN.read_text(), flags=re.MULTILINE
    )
    assert match is not None
    return match.group(0)


def test_the_fixes_of_the_third_triage_left_the_limits() -> None:
    # INT5 folded T3, T4 and T6 (docs/specs/2026-10-15-mcp-gaps-r3-notes.md).
    assert "8.0" not in _deviation(27)
    assert "Since goal check r3 (U2)" in _deviation(31)
    assert "`!` glob" in _deviation(32) and "last matching" not in _deviation(32)
    assert "no live preview" not in _deviation(45)
    assert "`read` uses the same processor" in _deviation(68)
    ext15 = next(
        line for line in _NOTE.read_text().splitlines() if "| EXT-15 |" in line
    )
    assert ext15.endswith("| `renderShell` is not ported. DEV-45. |")
    assert "no live preview" not in _NOTE.read_text()
    assert "test_without_waitid" not in _PLAN.read_text()
    notes = (_DOCS / "specs" / "2026-10-15-mcp-gaps-r3-notes.md").read_text()
    assert "**Folded in INT5 (2026-10-15).**" in notes


def _rows_of(cell: str) -> list[str]:
    """The rows a "Rows" cell names: ``A-01, B-02 to B-04``."""

    rows: list[str] = []
    for part in cell.split(","):
        span = re.fullmatch(r"([A-Z]+)-(\d{2}) to \1-(\d{2})", part.strip())
        if span is not None:
            first, last = int(span.group(2)), int(span.group(3))
            rows.extend(f"{span.group(1)}-{n:02d}" for n in range(first, last + 1))
        elif re.fullmatch(_ROW_ID, part.strip()):
            rows.append(part.strip())
    return rows


def test_a_row_a_recorded_difference_changes_is_closed_with_that_limit() -> None:
    recorded = _PLAN.read_text().split("### 3.1 Differences the slices recorded")[1]
    recorded = recorded.split("### 3.2 Audit result")[0]
    note = _NOTE.read_text().split("## Row audit")[1]
    lines = {
        line.split(" | ")[0][2:]: line
        for line in note.splitlines()
        if re.match(rf"\| {_ROW_ID} \|", line)
    }
    states = {row: state for row, state, _tests in _audit()}

    for deviation, cell in re.findall(
        r"^\| (DEV-\d{2}) \| ([^|]*) \|", recorded, flags=re.MULTILINE
    ):
        for row in _rows_of(cell):
            assert deviation in lines[row], (deviation, row)
            assert states[row] != "closed", (deviation, row)


def test_the_plan_states_the_result_of_the_audit() -> None:
    states = Counter(state for _row, state, _tests in _audit())
    plan = _PLAN.read_text()
    result = plan.split("### 3.2 Audit result")[1].split("## 4. pipy design")[0]

    assert f"| Closed with a test | {states['closed']} |" in result
    assert (
        f"| Closed with a limit or deviation | {states['closed, with limits']} |"
        in result
    )
    assert "| Closed in part | 0 |" in result
    assert "| Open | 0 |" in result
    closed = states["closed"] + states["closed, with limits"]
    assert f"Of the 292 rows of section 2, {closed} are closed\n" in plan
    assert f"({states['closed, with limits']} of them with a recorded limit" in plan


# --- DOC-01 to DOC-05 -------------------------------------------------------


def test_the_user_page_has_a_section_for_every_topic_of_doc_01() -> None:
    # DOC-01: quick setup, configuration and its rules, inspecting and
    # diagnosing servers, migrating from other clients, OAuth with every
    # option, exposure, resources, permissions, and extensions and SDK.
    page = (_DOCS / "mcp.md").read_text()
    headings = re.findall(r"^#{2,4} (.+)$", page, flags=re.MULTILINE)

    for heading in (
        "Quick setup",
        "Configure servers",
        "Configuration rules",
        "Migrate configuration from another client",
        "Manage servers in a session",
        "The `/mcp` command",
        "The `/mcp` manager",
        "Diagnose connection problems",
        "The `pipy mcp` commands",
        "Authenticate with OAuth",
        "OAuth settings",
        "Control tool exposure",
        "Use resources",
        "Permissions",
        "Extensions and SDK",
        "Servers from extensions",
        "Replace the built-in MCP support",
        "MCP in an embedded session",
    ):
        assert heading in headings, heading

    oauth = _section("mcp.md", "## Authenticate with OAuth")
    for option in (
        "clientId",
        "clientSecret",
        "callbackPort",
        "callbackUrl",
        "scope",
        "clientName",
        "clientRegistration",
        "clientMetadataBaseUrl",
        "authServerMetadataUrl",
    ):
        assert f"`{option}`" in oauth, option
    for exposure in ("codemode", "deferred", "direct", "hidden"):
        assert f"| `{exposure}`" in _section("mcp.md", "## Control tool exposure")
    migrate = _section("mcp.md", "## Migrate configuration from another client")
    for client in ("Claude", "Cursor", "VS Code", "Codex", "OpenCode", "Pi"):
        assert client in migrate, client


def test_the_cli_reference_covers_what_doc_02_names() -> None:
    # DOC-02: `--no-mcp`, tool patterns, the MCP rules of `--tools`,
    # `tool_search`, and the `mcp` commands.
    usage = (_DOCS / "usage.md").read_text()

    assert "| `--no-mcp` |" in usage
    commands = _section("usage.md", "### MCP commands")
    for command in ("add", "remove", "list", "login", "logout"):
        assert f"pipy mcp {command} " in commands, command
    tools = _section("usage.md", "#### MCP tools")
    assert "mcp__radius__*" in tools and "--exclude-tools 'mcp__*'" in tools
    assert "`*` matches any characters" in usage
    assert "`tool_search`" in usage and "(mcp.md#tool-search)" in usage
    # Every option the commands print in their usage text is in the MCP page.
    page = _section("mcp.md", "## The `pipy mcp` commands")
    for option in sorted(set(re.findall(r"--[a-z][a-z-]+", HELP))):
        assert f"`{option}" in page, option


def test_the_codemode_page_covers_what_doc_03_names() -> None:
    # DOC-03: the discovery helpers, result shapes, listing rules, settings.
    page = (_DOCS / "codemode.md").read_text()

    for helper in (
        "`ALL_TOOLS`",
        "`search_tools(query, limit=8, namespace=None)`",
        "`describe_tool(name)`",
        "`describe_namespace(name)`",
    ):
        assert helper in _section("codemode.md", "## Globals"), helper
    assert "structured content" in page and "`ToolError`" in page
    assert "`image(value)`" in page
    told = _section("codemode.md", "## What the model is told")
    assert "Nested tools:" in told
    assert "`codemode.mode`" in page and "`codemode.inlineBudget`" in page


def test_the_extension_api_reference_covers_what_doc_04_names() -> None:
    # DOC-04: exposure, namespaces, annotations, output schemas,
    # `reaches_tools`, nested execution and MCP server registration.
    page = (_DOCS / "extension-api.md").read_text()
    headings = re.findall(r"^#{2,4} (.+)$", page, flags=re.MULTILINE)

    for heading in (
        "Extension tool metadata",
        "Running other tools",
        "Presenting the loadout",
        "Replacing built-in tools",
        "Tool rendering",
        "MCP servers",
    ):
        assert heading in headings, heading
    metadata = _section("extension-api.md", "### Extension tool metadata")
    for name in (
        "exposure",
        "namespace",
        "annotations",
        "output_schema",
        "reaches_tools",
    ):
        assert f"`{name}`" in metadata, name
    assert "ctx.execute_tool" in _section("extension-api.md", "### Running other tools")
    servers = _section("extension-api.md", "### MCP servers")
    for name in (
        "register_mcp_server",
        "unregister_mcp_server",
        "get_mcp_servers",
        "mcp_servers_change",
    ):
        assert name in servers, name


def test_the_changelog_names_the_user_visible_parts() -> None:
    # DOC-05, by key terms: every part of the feature a user can see has an
    # entry under Unreleased.
    changelog = (_ROOT / "CHANGELOG.md").read_text()
    unreleased = changelog.split("## [Unreleased]")[1].split("\n## [")[0]

    for term in (
        "`mcp.json`",
        "`pipy mcp add`",
        "`pipy mcp login",
        "`/mcp` manager",
        "`/mcp login",
        "`tool_search`",
        "`--no-mcp`",
        "`mcp_servers`",
        "MCP resources",
        "HTTP MCP servers",
        "`oauth.clientRegistration",
        "`auth`",
        "`search_tools",
        "`codemode.mode`",
        "`api.register_mcp_server",
        "`ctx.execute_tool",
        "prepare_loadout=",
        "`api.register_tool_renderer",
        "`McpOptions`",
        "Images in tool results",
        "`image(",
        "`just mcp-conformance`",
    ):
        assert term in unreleased, term
