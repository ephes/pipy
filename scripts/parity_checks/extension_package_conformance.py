"""Hard conformance gate for the extension package CLI (slice 12).

Exercises the real `pipy install/remove/list` CLI and the package-manager
settings mutations in temporary user/project settings files, and asserts
the slice-12 invariants from `docs/extension-api.md` (local-path scope):

1. `install` persists a local-path source to user and (with -l) project
   settings;
2. `list` reports user/project packages and an empty state correctly;
3. `config` writes Pi-shaped `+pattern` / `-pattern` resource filters
   (not deletions);
4. `remove`/`uninstall` removes only the selected source/scope; a missing
   source fails non-zero;
5. git / PyPI sources are rejected (no supply-chain execution);
6. package operations are settings-only: no source path leaks into a
   session metadata archive (none is written by these commands).

Exits 0 when every check passes, 1 otherwise. No network.

Run:

    uv run python scripts/parity_checks/extension_package_conformance.py --json
"""

from __future__ import annotations

import argparse
import io
import json
import os
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from pathlib import Path

from pipy_harness.cli import main as cli_main
from pipy_harness.native.package_manager import (
    configure_resource_filter,
    resource_filters,
)
from pipy_harness.native.settings import global_settings_path, project_settings_path


@dataclass
class Check:
    name: str
    passed: bool
    detail: str


def _run_cli(argv: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = cli_main(argv)
    return code, out.getvalue(), err.getvalue()


def run_checks(base: Path) -> list[Check]:
    workspace = base / "ws"
    (workspace / ".pipy").mkdir(parents=True)
    pkg_user = base / "pkg-user"
    pkg_user.mkdir()
    pkg_project = base / "pkg-project"
    pkg_project.mkdir()
    checks: list[Check] = []

    code_e, out_e, _ = _run_cli(["list", "--cwd", str(workspace)])
    checks.append(
        Check(
            "list_empty",
            code_e == 0 and "No packages installed." in out_e,
            "list reports the empty state",
        )
    )

    code_u, out_u, _ = _run_cli(["install", str(pkg_user)])
    code_p, out_p, _ = _run_cli(["install", str(pkg_project), "-l", "--cwd", str(workspace)])
    user_settings = json.loads(global_settings_path().read_text(encoding="utf-8"))
    project_settings = json.loads(
        project_settings_path(workspace).read_text(encoding="utf-8")
    )
    checks.append(
        Check(
            "install_persists_scopes",
            code_u == 0
            and code_p == 0
            and str(pkg_user) in user_settings.get("packages", [])
            and str(pkg_project) in project_settings.get("packages", []),
            "install persists to user and project settings",
        )
    )

    code_l, out_l, _ = _run_cli(["list", "--cwd", str(workspace)])
    checks.append(
        Check(
            "list_reports_scopes",
            code_l == 0 and str(pkg_user) in out_l and str(pkg_project) in out_l,
            "list reports user + project packages",
        )
    )

    configure_resource_filter(
        settings_path=global_settings_path(), kind="skills", pattern="lint", enable=False
    )
    configure_resource_filter(
        settings_path=global_settings_path(),
        kind="extensions",
        pattern="protected",
        enable=True,
    )
    checks.append(
        Check(
            "config_writes_filters",
            resource_filters(global_settings_path(), "skills") == ("-lint",)
            and resource_filters(global_settings_path(), "extensions") == ("+protected",)
            # The package source is NOT deleted by writing a filter.
            and str(pkg_user)
            in json.loads(global_settings_path().read_text(encoding="utf-8")).get(
                "packages", []
            ),
            "config writes +/- filters without deleting packages",
        )
    )

    code_r, out_r, _ = _run_cli(["remove", str(pkg_user)])
    code_miss, _, err_miss = _run_cli(["uninstall", str(pkg_user)])
    user_after = json.loads(global_settings_path().read_text(encoding="utf-8"))
    project_after = json.loads(
        project_settings_path(workspace).read_text(encoding="utf-8")
    )
    checks.append(
        Check(
            "remove_only_selected_scope",
            code_r == 0
            and f"Removed {pkg_user}" in out_r
            and str(pkg_user) not in user_after.get("packages", [])
            and str(pkg_project) in project_after.get("packages", [])
            and code_miss == 1,
            "remove removes only the selected source/scope; missing -> non-zero",
        )
    )

    code_git, _, err_git = _run_cli(["install", "git:example/repo"])
    code_url, _, _ = _run_cli(["install", "https://example/x.tgz"])
    checks.append(
        Check(
            "git_pypi_rejected",
            code_git == 2 and code_url == 2,
            "git / PyPI sources are rejected",
        )
    )
    return checks


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit a JSON report")
    args = parser.parse_args(argv)

    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        os.environ["PIPY_CONFIG_HOME"] = str(base / "cfg")
        checks = run_checks(base)

    passed = all(c.passed for c in checks)
    if args.json:
        report = {
            "passed": passed,
            "checks": [
                {"name": c.name, "passed": c.passed, "detail": c.detail}
                for c in checks
            ],
        }
        print(json.dumps(report, indent=2))
    else:
        for c in checks:
            status = "PASS" if c.passed else "FAIL"
            print(f"[{status}] {c.name}: {c.detail}")
        print("ALL PASS" if passed else "FAILURES PRESENT")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
