"""Map changed repository paths to the test files the default gate must run.

The default gate (``just test``) runs a small smoke set plus the tests a change
can affect. This module decides which test files those are. It has no pytest
dependency so the rules can be unit-tested directly; ``pipy_test_tiers`` turns
its result into collection-time file selection.

Routing is deliberately conservative. A path either maps to a known set of test
files or the selection escalates, visibly, to a whole area or to the full tier:

* Runtime source under ``src/`` is imported by almost every test (the
  ``pipy_harness.native`` package imports most of the runtime), so a source
  change escalates to the full tier. Durable source escalates to the durable
  area only: tests outside it import durable code at module level, which the
  smoke set covers, but only durable-area tests exercise it.
* Files hash-pinned by a durable manifest (``closure``, ``verification`` and
  other ``path: sha256`` maps) escalate to the durable area, because the
  comparison tests hash them.
* Test files select themselves. Test helpers, fixtures, scripts and drivers
  select every test file that mentions them by path, file name or module name,
  following mentions through other helpers, scripts and test modules. A code
  file nobody mentions escalates to the full tier.
* Tests that scan whole trees (``REPO_GUARDS``) run for any change inside the
  trees they scan.
* Packaging, dependency and pytest configuration (``pyproject.toml``,
  ``uv.lock``, ``conftest.py``, ``tests/pytest_plugins/``) and any path no rule
  knows escalate to the full tier.

Run ``uv run python tests/pytest_plugins/pipy_test_routing.py`` to print the
selection for the current checkout without running tests.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections.abc import Iterable
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BASE = "origin/main"
BASE_ENV = "PIPY_TEST_BASE"

TIER_DEFAULT = "default"
TIER_DURABLE = "durable"
TIER_CORE = "core"
TIER_FULL = "full"
TIERS = (TIER_DEFAULT, TIER_DURABLE, TIER_CORE, TIER_FULL)

# Always-run smoke set (about 8 s serial): cheap tests that import the runtime,
# drive fake-provider sessions, the session recorder, the CLI, the SDK surface
# and a few durable codec, storage and product-session paths. The list lives
# here, not in markers, because many durable test files are hash-pinned.
SMOKE_TESTS = (
    "tests/test_cli_top_level_routing.py::test_route_argv[argv0-expected0]",
    "tests/test_harness_cli.py::test_cli_run_smoke_streams_child_output_and_finalizes_record",
    "tests/test_recorder.py::test_init_creates_active_session_under_in_progress",
    "tests/test_recorder.py::test_append_writes_valid_jsonl_events",
    "tests/test_sdk.py::test_sdk_exports_expected_surface",
    "tests/test_native_session.py::"
    "test_native_session_no_intent_builds_prompt_calls_provider_and_emits_safe_events",
    "tests/test_coding_session_end_to_end.py::"
    "test_openrouter_tool_loop_dispatches_read_and_returns_final_text",
    "tests/test_product_session_api.py::"
    "test_two_turn_real_tools_history_and_lifecycle_without_archive",
    "tests/durable/test_canonical_codec.py::"
    "test_each_registered_dto_field_survives_transport[value0]",
    "tests/durable/test_records.py::"
    "test_all_terminal_outcomes_preserve_fields[completing-outcome0]",
    "tests/durable/test_sqlite_storage.py::test_memory_storage_body"
    "[memory-test_root_global_namespace_mint_seq_empty_and_exhaustion]",
    "tests/durable/test_sqlite_storage.py::test_memory_storage_body"
    "[sqlite-test_root_global_namespace_mint_seq_empty_and_exhaustion]",
    "tests/durable/test_product_session.py::"
    "test_empty_created_session_reopens_exactly_without_dispatch",
)

# Test-side paths whose change can affect any test.
_FULL_TIER_PATHS = frozenset(
    {
        "pyproject.toml",
        "uv.lock",
        ".python-version",
        "tests/__init__.py",
    }
)
_FULL_TIER_PREFIXES = ("tests/pytest_plugins/",)

# Prose and configuration that is not imported. These select the tests that
# mention them; when none does, the smoke set is enough.
_PROSE_SUFFIXES = frozenset({".md", ".txt", ".toml", ".yml", ".yaml", ".example"})
_PROSE_PATHS = frozenset({"LICENSE", "justfile", ".gitignore"})
# Repository-wide guards: test files (or single tests, as ``file::name``) that
# scan whole trees instead of naming files, with the path prefixes they scan
# outside ``src/`` (source changes run the full tier anyway). Register every new test that walks ``tests/``,
# ``scripts/``, ``docs/`` or ``typings/`` here, or the default gate misses it.
REPO_GUARDS: dict[str, tuple[str, ...]] = {
    "tests/test_architecture_quality_gates.py": ("tests/", "scripts/"),
    "tests/test_native_extension_leaf_ownership.py": ("tests/", "scripts/"),
    "tests/test_native_extension_shortcuts.py": (
        "docs/examples/extensions/",
        "scripts/",
    ),
    # Only this test of the (slow) module scans tests/.
    "tests/test_native_session_extension_generation.py::"
    "test_generation_message_routing_cluster_has_one_authoritative_owner": ("tests/",),
    "tests/test_native_extension_ui_import.py": ("tests/", "scripts/"),
    "tests/test_native_startup_selector_ownership.py": ("tests/", "scripts/"),
    "tests/test_parity_loop_skill.py": (
        "docs/",
        "scripts/",
        "AGENTS.md",
        "CHANGELOG.md",
        "README.md",
    ),
    "tests/test_parity_probe_trust.py": ("scripts/parity_checks/",),
    "tests/test_docs_build.py": ("docs/", "zensical.toml", ".readthedocs.yaml"),
    "tests/test_typing_config.py": ("typings/",),
}

# Code-like text that can carry references from one helper to another.
_CODE_SUFFIXES = frozenset({".py", ".pyi", ".mts", ".mjs", ".ts", ".js", ".sh"})
# Directories on sys.path in tests (pyproject pythonpath, rootdir conftest
# insertion, and scripts inserting their own directory).
_IMPORT_ROOTS = frozenset({"tests", "tests/durable", "tests/fixtures", "scripts"})
# Stems too generic to count as a reference on their own.
_GENERIC_STEMS = frozenset(
    {"__init__", "__main__", "conftest", "run", "main", "cli", "base", "util", "docs"}
)

# Substrings that put a file (and every file that uses it) in a category.
_CATEGORY_SIGNALS = {
    "pty": (
        "import pty",
        "from pty",
        "forkpty",
        "openpty",
        "pty.fork",
        "tmux",
        "pexpect",
    ),
    "node": ('"node"', "'node'", ".mts", "npx", '"npm"', "'npm'"),
    "subprocess": ("subprocess", "multiprocessing", "sys.executable"),
}


@dataclass
class Selection:
    """The test files a tier runs and why."""

    tier: str
    #: None means every test file (no file filter).
    files: frozenset[str] | None
    #: Files that run only some of their tests (smoke tests, single guards):
    #: file -> node IDs (``file::name``; a name without parameters covers all
    #: of its parametrizations).
    partial: dict[str, frozenset[str]] = field(default_factory=dict)

    @property
    def smoke_only(self) -> frozenset[str]:
        """Files selected only for some of their tests."""

        return frozenset(self.partial)

    escalation: str | None = None
    reasons: list[str] = field(default_factory=list)
    changed: tuple[str, ...] = ()
    base: str | None = None

    def summary_lines(self) -> list[str]:
        lines = [f"pipy test tier: {self.tier}"]
        if self.base is not None:
            lines.append(f"  changes against {self.base}: {len(self.changed)} path(s)")
        if self.escalation is not None:
            lines.append(f"  ESCALATED to {self.escalation}")
        if self.files is None:
            lines.append("  selection: every test file")
        else:
            lines.append(
                f"  selection: {len(self.files)} test file(s), "
                f"{len(self.partial)} of them for single smoke or guard tests"
            )
        lines.extend(f"  - {reason}" for reason in self.reasons)
        return lines


class Repo:
    """Read-only view of the repository files the router needs."""

    def __init__(self, root: Path = REPO_ROOT) -> None:
        self.root = root

    def _text(self, rel: str) -> str:
        try:
            return (self.root / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""

    @cached_property
    def test_files(self) -> tuple[str, ...]:
        tests = self.root / "tests"
        return tuple(
            sorted(
                path.relative_to(self.root).as_posix()
                for path in tests.rglob("test_*.py")
                if "__pycache__" not in path.parts
            )
        )

    @cached_property
    def helper_files(self) -> tuple[str, ...]:
        """Non-test code under tests/ and scripts/ that can mention other files."""

        found: list[str] = []
        for top in ("tests", "scripts"):
            for path in (self.root / top).rglob("*"):
                if not path.is_file() or "__pycache__" in path.parts:
                    continue
                if path.suffix not in _CODE_SUFFIXES:
                    continue
                rel = path.relative_to(self.root).as_posix()
                # Plugins escalate to the full tier themselves; their rule
                # tables mention many paths without depending on them.
                if is_test_file(rel) or rel.startswith(_FULL_TIER_PREFIXES):
                    continue
                found.append(rel)
        return tuple(sorted(found))

    @cached_property
    def texts(self) -> dict[str, str]:
        return {rel: self._text(rel) for rel in (*self.test_files, *self.helper_files)}

    @cached_property
    def mention_keys(self) -> dict[str, frozenset[str]]:
        """Per file: every string a reference token can equal in its text."""

        return {name: _mention_keys(text) for name, text in self.texts.items()}

    @cached_property
    def _uses(self) -> dict[str, frozenset[str]]:
        """Per file: the helpers, scripts and test modules its text mentions."""

        owners: dict[str, set[str]] = {}
        for name in self.texts:
            for token in reference_tokens(name):
                owners.setdefault(token, set()).add(name)
        uses: dict[str, frozenset[str]] = {}
        for name, keys in self.mention_keys.items():
            found: set[str] = set()
            for token in keys & owners.keys():
                found |= owners[token]
            found.discard(name)
            uses[name] = frozenset(found)
        return uses

    @cached_property
    def categories(self) -> dict[str, frozenset[str]]:
        """Per file: durable/pty/node/subprocess, from its text and every file it
        uses, transitively (a test that starts processes through a helper is a
        subprocess test)."""

        flags: dict[str, set[str]] = {}
        for name, text in self.texts.items():
            own = {
                key
                for key, signals in _CATEGORY_SIGNALS.items()
                if any(signal in text for signal in signals)
            }
            if "pty" in Path(name).stem.split("_"):
                own.add("pty")
            if name.startswith("tests/durable/") or "durable" in text.lower():
                own.add("durable")
            flags[name] = own
        changed = True
        while changed:
            changed = False
            for name, used in self._uses.items():
                before = len(flags[name])
                for other in used:
                    flags[name] |= flags[other]
                changed = changed or len(flags[name]) != before
        return {name: frozenset(value) for name, value in flags.items()}

    @cached_property
    def durable_files(self) -> frozenset[str]:
        """Test files of the durable area, including durable coverage elsewhere.

        A test file belongs to it when it lives in ``tests/durable/`` or when it,
        or a helper, script or test module it uses, mentions durable.
        """

        return frozenset(
            rel for rel in self.test_files if "durable" in self.categories.get(rel, ())
        )

    @cached_property
    def closure_files(self) -> dict[str, int]:
        """Repository paths hash-pinned by durable manifests, with manifest counts.

        That is every ``closure`` map key plus the key of every other
        ``path: sha256`` entry (``verification`` and ``sources`` maps, for
        example, pin ``.github/workflows/ci.yml``), whether or not the file
        still exists.
        """

        counts: dict[str, int] = {}
        manifests = sorted(
            (self.root / "tests/fixtures/durable").glob("*manifest.json")
        )
        for manifest in manifests:
            try:
                data = json.loads(manifest.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            # Keys that are not repository paths (evidence names such as
            # "clean/result.json") never match a changed path, so keeping
            # them is harmless, and deleted pinned files stay pinned.
            pinned = set(_pinned_keys(data))
            for path in pinned:
                counts[path] = counts.get(path, 0) + 1
        return counts

    def markers(self, rel: str) -> set[str]:
        """pytest markers for a collected test file."""

        return set(self.categories.get(rel, ()))


_SHA256 = re.compile(r"[0-9a-f]{64}")


def _pinned_keys(data: object) -> Iterable[str]:
    """Every hash-pinned path in a manifest: closure-map keys and other
    ``path: sha256`` entries."""

    if isinstance(data, dict):
        for key, value in data.items():
            if key == "closure" and isinstance(value, dict):
                yield from (k for k in value if isinstance(k, str))
            elif isinstance(value, str) and _SHA256.fullmatch(value):
                yield key
            else:
                yield from _pinned_keys(value)
    elif isinstance(data, list):
        for item in data:
            yield from _pinned_keys(item)


def is_test_file(rel: str) -> bool:
    return (
        rel.startswith("tests/")
        and Path(rel).name.startswith("test_")
        and (rel.endswith(".py"))
    )


def reference_tokens(rel: str) -> set[str]:
    """Strings whose appearance in a file counts as a reference to ``rel``."""

    path = Path(rel)
    tokens = {rel, path.name}
    # Modules directly in an import root are imported by their bare name. A
    # module inside a package is reached through the package (family) name.
    if (
        path.suffix in {".py", ".pyi"}
        and path.stem not in _GENERIC_STEMS
        and path.parent.as_posix() in _IMPORT_ROOTS
    ):
        tokens.add(path.stem)
    parts = path.parts
    # A file inside a package or fixture family is usually reached through the
    # directory: ``scripts/durable_reference/x.py`` or ``fixtures/<family>/*``.
    if len(parts) >= 3 and parts[0] in {"tests", "scripts"}:
        family = parts[2] if parts[1] == "fixtures" and len(parts) >= 4 else parts[1]
        if family not in {"fixtures", "durable", "pytest_plugins"}:
            tokens.add(family)
    return {token for token in tokens if len(token) >= 4}


# Bytes that can be part of a path-like run; everything else separates runs.
_RUN_BYTES = frozenset(
    b"abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_./-"
)
_SEPARATE = bytes(byte if byte in _RUN_BYTES else 0x20 for byte in range(256))


def _mention_keys(text: str) -> frozenset[str]:
    """Every string a reference token can equal when it appears in ``text``.

    A token counts as mentioned when it is a whole path-like run of
    ``[A-Za-z0-9_./-]``, a trailing ``/``-separated part of one (``x/y.json``
    inside ``../x/y.json``) or a dotted-name part (``helper`` in
    ``tests.helper``).
    """

    data = text.encode("ascii", "replace").translate(_SEPARATE)
    runs = set(data.decode("ascii").split())
    keys = set(runs)
    for run in runs:
        if "/" in run:
            parts = run.split("/")
            keys.update("/".join(parts[index:]) for index in range(1, len(parts)))
        elif "." in run:
            parts = [run]
        else:
            continue
        for part in parts:
            keys.add(part)
            if "." in part:
                keys.update(part.split("."))
    return frozenset(keys)


def referencing_test_files(repo: Repo, rel: str) -> set[str]:
    """Test files that mention ``rel``, directly or through other files.

    A mention found in a helper, script or test file makes that file a new
    source of mentions, so ``test -> helper -> script -> changed file`` chains
    are followed to any depth.
    """

    seen = {rel}
    frontier = {rel}
    selected: set[str] = set()
    while frontier:
        tokens: set[str] = set()
        for item in frontier:
            tokens |= reference_tokens(item)
        if not tokens:
            break
        frontier = set()
        for name, keys in repo.mention_keys.items():
            if name in seen or tokens.isdisjoint(keys):
                continue
            seen.add(name)
            frontier.add(name)
            if is_test_file(name):
                selected.add(name)
    return selected


def _is_durable_path(rel: str) -> bool:
    return any(part.startswith("durable") for part in Path(rel).parts[:-1]) or Path(
        rel
    ).name.startswith("durable")


def _guards_for(repo: Repo, rel: str) -> set[str]:
    """Guard entries (files or ``file::name`` node IDs) that scan ``rel``."""

    return {
        guard
        for guard, prefixes in REPO_GUARDS.items()
        if rel.startswith(prefixes) and (repo.root / guard.split("::")[0]).exists()
    }


def _far_reaching(rel: str) -> tuple[str, str] | None:
    """(escalation, reason) for paths that reach beyond any file selection."""

    if rel.startswith("src/"):
        if _is_durable_path(rel.removeprefix("src/")):
            return TIER_DURABLE, f"{rel} is durable source"
        return TIER_FULL, f"{rel} is runtime source"
    if rel in _FULL_TIER_PATHS or rel.startswith(_FULL_TIER_PREFIXES):
        return TIER_FULL, f"{rel} configures every test"
    if Path(rel).name == "conftest.py":
        return TIER_FULL, f"{rel} is a conftest"
    return None


def _unmentioned(
    rel: str, covered_by_durable: bool, guards: set[str]
) -> tuple[str | None, str]:
    """(escalation, reason) for a non-test path that no test file mentions."""

    path = Path(rel)
    if guards and not rel.startswith(("tests/", "scripts/")):
        return None, f"{rel}: only repository-wide guards cover it"
    is_prose = path.suffix in _PROSE_SUFFIXES or rel in _PROSE_PATHS
    if is_prose and not rel.startswith(("tests/", "scripts/")):
        return None, f"{rel}: prose or config that no test mentions"
    if covered_by_durable:
        return None, f"{rel}: covered by the durable area"
    return TIER_FULL, f"{rel}: no rule or test mention covers this path"


def _route_path(repo: Repo, rel: str) -> tuple[set[str], list[tuple[str | None, str]]]:
    """Route one changed path: (selected test files, [(escalation, reason)])."""

    far = _far_reaching(rel)
    if far is not None:
        return set(), [far]
    notes: list[tuple[str | None, str]] = []
    pins = repo.closure_files.get(rel, 0)
    if pins:
        notes.append(
            (TIER_DURABLE, f"{rel} is hash-pinned by {pins} durable manifest(s)")
        )
    if is_test_file(rel):
        # Test modules sometimes import helpers from each other.
        importers = referencing_test_files(repo, rel)
        guards = _guards_for(repo, rel)
        extra = f"{len(importers)} that mention it, {len(guards)} guard(s)"
        if (repo.root / rel).exists():
            reason = f"{rel}: changed test file (+{extra})"
            return {rel, *importers, *guards}, [*notes, (None, reason)]
        reason = f"{rel}: deleted test file ({extra})"
        return importers | guards, [*notes, (None, reason)]
    durable_support = rel.startswith(("tests/", "scripts/")) and _is_durable_path(
        rel.split("/", 1)[1]
    )
    if durable_support:
        notes.append((TIER_DURABLE, f"{rel} is durable test support"))
    mentioned = referencing_test_files(repo, rel)
    guards = _guards_for(repo, rel)
    if mentioned:
        notes.append((None, f"{rel}: {len(mentioned)} test file(s) mention it"))
    else:
        notes.append(_unmentioned(rel, bool(pins) or durable_support, guards))
    if guards:
        notes.append((None, f"{rel}: {len(guards)} repository-wide guard(s) scan it"))
    return mentioned | guards, notes


def route(repo: Repo, changed: Iterable[str]) -> tuple[set[str], str | None, list[str]]:
    """Return (selected test files, escalation, reasons) for changed paths.

    Escalation is ``None``, ``"durable"`` (add the durable area) or ``"full"``.
    """

    selected: set[str] = set()
    reasons: list[str] = []
    levels: set[str] = set()
    for rel in sorted(set(changed)):
        files, notes = _route_path(repo, rel)
        selected |= files
        for level, reason in notes:
            if level is None:
                reasons.append(reason)
            else:
                levels.add(level)
                reasons.append(f"{level}: {reason}")
    escalation = next(
        (level for level in (TIER_FULL, TIER_DURABLE) if level in levels), None
    )
    return selected, escalation, reasons


def git_changed_paths(root: Path, base: str) -> list[str]:
    """Paths changed between merge-base(HEAD, base) and the working tree."""

    def git(*args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(root), *args],
            check=True,
            capture_output=True,
            text=True,
        ).stdout

    merge_base = git("merge-base", "HEAD", base).strip()
    tracked = git("diff", "--name-only", "--no-renames", "-z", merge_base)
    untracked = git("ls-files", "--others", "--exclude-standard", "-z")
    return sorted({p for p in (tracked + untracked).split("\0") if p})


def _area_selection(tier: str, repo: Repo) -> Selection:
    if tier == TIER_FULL:
        return Selection(tier=tier, files=None)
    if tier == TIER_DURABLE:
        return Selection(
            tier=tier,
            files=repo.durable_files,
            reasons=[
                "durable area: tests/durable/ and test files that use durable code"
                " or mention durable"
            ],
        )
    if tier == TIER_CORE:
        return Selection(
            tier=tier,
            files=frozenset(
                f for f in repo.test_files if not f.startswith("tests/durable/")
            ),
            reasons=["core area: every test file outside tests/durable/"],
        )
    raise ValueError(f"unknown tier {tier!r}")


def select(
    tier: str,
    repo: Repo | None = None,
    *,
    changed: Iterable[str] | None = None,
    base: str | None = None,
) -> Selection:
    """Compute the selection for ``tier``.

    ``changed`` overrides the git diff (used by tests). ``base`` defaults to
    ``$PIPY_TEST_BASE`` or ``origin/main``.
    """

    repo = repo or Repo()
    if tier != TIER_DEFAULT:
        return _area_selection(tier, repo)

    base = base or os.environ.get(BASE_ENV) or DEFAULT_BASE
    if changed is None and os.environ.get("CI", "").lower() in {"1", "true", "yes"}:
        # CI's full-test jobs still call `just test`: .github/workflows/ci.yml is
        # hash-pinned by the durable reference manifest, so the switch to
        # `just test-full` waits for a batched manifest update. Until then CI
        # gets the full tier here.
        return Selection(
            tier=tier,
            files=None,
            escalation=TIER_FULL,
            base=base,
            reasons=["full: CI is set; CI runs every test"],
        )
    if changed is None:
        try:
            changed_list = git_changed_paths(repo.root, base)
        except (OSError, subprocess.CalledProcessError) as exc:
            detail = getattr(exc, "stderr", "") or str(exc)
            return Selection(
                tier=tier,
                files=None,
                escalation=TIER_FULL,
                base=base,
                reasons=[
                    f"full: cannot diff against {base} ({detail.strip()}); "
                    f"fetch it or set {BASE_ENV}"
                ],
            )
    else:
        changed_list = sorted(set(changed))

    selected, escalation, reasons = route(repo, changed_list)
    if escalation == TIER_FULL:
        return Selection(
            tier=tier,
            files=None,
            escalation=TIER_FULL,
            reasons=reasons,
            changed=tuple(changed_list),
            base=base,
        )
    if escalation == TIER_DURABLE:
        selected |= repo.durable_files
    whole = {entry for entry in selected if "::" not in entry}
    nodes: dict[str, set[str]] = {}
    for node in (*SMOKE_TESTS, *(entry for entry in selected if "::" in entry)):
        name = node.split("::", 1)[0]
        if name not in whole:
            nodes.setdefault(name, set()).add(node)
    if not changed_list:
        reasons.append("no changes: smoke tests only")
    return Selection(
        tier=tier,
        files=frozenset(whole | set(nodes)),
        partial={name: frozenset(ids) for name, ids in nodes.items()},
        escalation=escalation,
        reasons=reasons,
        changed=tuple(changed_list),
        base=base,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("--tier", choices=TIERS, default=TIER_DEFAULT)
    parser.add_argument(
        "--base", default=None, help=f"default: ${BASE_ENV} or {DEFAULT_BASE}"
    )
    parser.add_argument("--files", action="store_true", help="also list selected files")
    parser.add_argument("changed", nargs="*", help="treat these paths as changed")
    args = parser.parse_args(argv)
    selection = select(args.tier, changed=args.changed or None, base=args.base)
    print("\n".join(selection.summary_lines()))
    if args.files and selection.files is not None:
        print("\n".join(sorted(selection.files)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
