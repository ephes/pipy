"""Conformance gate for native export/import/share/distribution.

Run:

    uv run python scripts/parity_checks/export_distribution_conformance.py --json

The gate is deterministic and uses only local temp files plus a stubbed GitHub
boundary. It proves the product export/import/share helpers operate on the
native session tree rather than the metadata-only ``pipy-session`` catalog.
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pipy_harness.native.export_distribution import (
    ShareCancelled,
    compare_versions,
    create_secret_gist,
    detect_install_method,
    export_from_file,
    export_native_branch_to_jsonl,
    export_native_session_to_html,
    fetch_latest_pipy_version,
    import_native_session_jsonl,
    self_update_plan,
)
from pipy_harness.native.models import ProviderToolCall
from pipy_harness.native.session_tree import NativeSessionTree
from pipy_harness.native.tools.messages import AssistantMessage, ToolResultMessage, UserMessage
from pipy_session.recorder import append_event, finalize_session, init_session
from pipy_session.export import export_session


@dataclass(frozen=True, slots=True)
class Check:
    name: str
    passed: bool
    detail: str


def _seed_tree(tmp_path: Path) -> NativeSessionTree:
    cwd = tmp_path / "workspace"
    cwd.mkdir()
    tree = NativeSessionTree.create(cwd, session_dir=tmp_path / "native-store")
    tree.append_message(UserMessage(content="ROOT export prompt"))
    root_reply = tree.append_message(AssistantMessage(content="ROOT export answer"))
    tree.append_message(UserMessage(content="MAIN export prompt"))
    tree.append_message(
        AssistantMessage(
            content=(
                "MAIN export answer ghp_GATE_BARE_SHOULD_NOT_LEAK "
                "github_pat_GATE_BARE_SHOULD_NOT_LEAK "
                "ya29.GATEBARESHOULDNOTLEAK "
                'password="gate-quoted-secret" '
                '{"api_key":"gate-json-secret","password":"gate-json-password"}'
            ),
            tool_calls=(
                ProviderToolCall(
                    provider_correlation_id="call-1",
                    tool_name="write",
                    arguments_json=(
                        '{"path":"demo.txt","content":"demo",'
                        '"token":"gate-tool-json-token"}'
                    ),
                ),
            ),
        )
    )
    tree.append_message(
        ToolResultMessage(
            tool_request_id="pipy-tool-1",
            output_text="write ok api_key=sk-CONFORMANCE-SHOULD-NOT-LEAK",
            provider_correlation_id="call-1",
        )
    )
    main_leaf = tree.get_leaf_id()
    tree.branch(root_reply.id)
    tree.append_message(UserMessage(content="OFFBRANCH export prompt"))
    tree.append_message(AssistantMessage(content="OFFBRANCH export answer"))
    assert main_leaf is not None
    tree.branch(main_leaf)
    return tree


def _decode_html(path: Path) -> dict[str, Any]:
    html = path.read_text(encoding="utf-8")
    marker = '<script id="pipy-session-data" type="application/pipy-session+base64">'
    encoded = html.split(marker, 1)[1].split("</script>", 1)[0]
    return json.loads(base64.b64decode(encoded).decode("utf-8"))


def _check(name: str, predicate: bool, detail: str) -> Check:
    return Check(name=name, passed=bool(predicate), detail=detail)


def run_gate() -> list[Check]:
    checks: list[Check] = []
    with tempfile.TemporaryDirectory(prefix="pipy-export-gate-") as raw:
        tmp = Path(raw)
        tree = _seed_tree(tmp)

        html = tmp / "session.html"
        export_native_session_to_html(tree, html)
        payload = _decode_html(html)
        html_blob = json.dumps(payload)
        checks.append(
            _check(
                "html-full-tree",
                len(payload["entries"]) == len(tree.get_entries())
                and "MAIN export prompt" in html_blob
                and "OFFBRANCH export prompt" in html_blob
                and payload["leafId"] == tree.get_leaf_id(),
                "HTML payload contains header, all entries including off-branch entries, and leaf id.",
            )
        )
        checks.append(
            _check(
                "html-redaction",
                "sk-CONFORMANCE-SHOULD-NOT-LEAK" not in html_blob
                and "ghp_GATE_BARE_SHOULD_NOT_LEAK" not in html_blob
                and "github_pat_GATE_BARE_SHOULD_NOT_LEAK" not in html_blob
                and "ya29.GATEBARESHOULDNOTLEAK" not in html_blob
                and "gate-quoted-secret" not in html_blob
                and "gate-json-secret" not in html_blob
                and "gate-json-password" not in html_blob
                and "gate-tool-json-token" not in html_blob
                and "[REDACTED]" in html_blob,
                "HTML export redacts auth-token and assignment-shaped transcript content.",
            )
        )

        jsonl = tmp / "portable.jsonl"
        export_native_branch_to_jsonl(tree, jsonl)
        before_import = jsonl.read_text(encoding="utf-8")
        lines = [json.loads(line) for line in before_import.splitlines()]
        entries = lines[1:]
        linear = bool(entries) and entries[0]["parentId"] is None and all(
            current["parentId"] == previous["id"]
            for previous, current in zip(entries, entries[1:])
        )
        jsonl_blob = json.dumps(lines)
        checks.append(
            _check(
                "jsonl-active-branch-linear",
                linear
                and "MAIN export prompt" in jsonl_blob
                and "OFFBRANCH export prompt" not in jsonl_blob,
                "JSONL export carries only the active branch with a linear parent chain.",
            )
        )
        imported = import_native_session_jsonl(jsonl, session_dir=tmp / "import-store")
        imported_text = "\n".join(
            getattr(message, "content", getattr(message, "output_text", ""))
            for message in imported.build_context().messages
        )
        checks.append(
            _check(
                "import-round-trip",
                "MAIN export prompt" in imported_text
                and "OFFBRANCH export prompt" not in imported_text
                and jsonl.read_text(encoding="utf-8") == before_import,
                "Import copies into the store, leaves the source unchanged, and rebuilds active context.",
            )
        )
        collision_store = tmp / "collision-store"
        collision_store.mkdir()
        (collision_store / jsonl.name).write_text("existing\n", encoding="utf-8")
        imported_collision = import_native_session_jsonl(
            jsonl, session_dir=collision_store
        )
        checks.append(
            _check(
                "import-collision-safe",
                imported_collision.path == collision_store / "portable-1.jsonl"
                and (collision_store / jsonl.name).read_text(encoding="utf-8")
                == "existing\n",
                "Import chooses a unique destination instead of overwriting an existing session file.",
            )
        )

        assert tree.path is not None
        cli_html = export_from_file(tree.path, tmp / "cli.html")
        checks.append(
            _check(
                "noninteractive-export",
                cli_html.is_file() and _decode_html(cli_html)["header"]["id"] == tree.session_id,
                "--export/export_from_file writes self-contained HTML from a native session file.",
            )
        )

        captured: dict[str, Any] = {}

        class Response:
            status = 201

            def read(self) -> bytes:
                return json.dumps(
                    {"id": "gategist", "html_url": "https://gist.github.com/u/gategist"}
                ).encode("utf-8")

        def opener(request, timeout: float):  # noqa: ANN001
            captured["headers"] = dict(request.header_items())
            captured["body"] = request.data.decode("utf-8")
            captured["timeout"] = timeout
            return Response()

        share = create_secret_gist(
            html=html.read_text(encoding="utf-8"),
            filename="session.html",
            token="ghp_GATE_TOKEN_SHOULD_NOT_LEAK",
            opener=opener,
        )
        share_body = json.loads(captured["body"])
        checks.append(
            _check(
                "share-secret-gist-stub",
                share.gist_id == "gategist"
                and share_body["public"] is False
                and "ghp_GATE_TOKEN_SHOULD_NOT_LEAK" not in captured["body"],
                "Share uses a fake HTTP boundary, creates public:false gist payload, and keeps token out of the body.",
            )
        )
        cancelled_called = False

        def cancelled_opener(request, timeout: float):  # noqa: ANN001
            nonlocal cancelled_called
            cancelled_called = True
            return Response()

        try:
            create_secret_gist(
                html="<html></html>",
                filename="cancel.html",
                token="ghp_GATE_TOKEN_SHOULD_NOT_LEAK",
                opener=cancelled_opener,
                cancelled=lambda: True,
            )
            cancelled = False
        except ShareCancelled:
            cancelled = True
        checks.append(
            _check(
                "share-cancellation",
                cancelled and not cancelled_called,
                "Share cancellation aborts before the fake network boundary is called.",
            )
        )

        active_record = init_session(
            agent="codex", slug="metadata-export", root=tmp / "archive"
        )
        append_event(
            active_record,
            root=tmp / "archive",
            event_type="codex.turn.observed",
            summary="Observed native export check.",
            payload={"prompt": "ROOT export prompt raw body should stay out"},
        )
        record = finalize_session(active_record, root=tmp / "archive")
        catalog_export = export_session(
            record.jsonl_path.name, session_root=tmp / "archive"
        )
        checks.append(
            _check(
                "pipy-session-export-metadata-only",
                catalog_export.get("transcript_events") is None
                and "ROOT export prompt" not in json.dumps(catalog_export),
                "pipy-session export remains a separate metadata-only catalog utility.",
            )
        )

        uv_root = tmp / "uv-tools"
        exe = uv_root / "pipy" / "bin" / "pipy"
        exe.parent.mkdir(parents=True)
        exe.write_text("", encoding="utf-8")
        method = detect_install_method(executable=str(exe), env={"UV_TOOL_DIR": str(uv_root)})
        plan = self_update_plan(method=method)
        configured_plan = self_update_plan(
            method=method, distribution_name="pipy-test-package"
        )
        checks.append(
            _check(
                "self-update-plan",
                method == "uv-tool"
                and not plan.automatic
                and configured_plan.command
                == ("uv", "tool", "upgrade", "pipy-test-package")
                and compare_versions("0.1.0", "0.2.0") < 0,
                "Install-method detection and self-update command planning are deterministic.",
            )
        )
        network_called = False

        def version_opener(request, timeout: float):  # noqa: ANN001
            nonlocal network_called
            network_called = True
            raise AssertionError("version check should not open network")

        offline_result = fetch_latest_pipy_version(
            env={"PIPY_OFFLINE": "1", "PIPY_SELF_UPDATE_PACKAGE": "pipy-test"},
            opener=version_opener,
        )
        skip_result = fetch_latest_pipy_version(
            env={
                "PIPY_SKIP_VERSION_CHECK": "1",
                "PIPY_SELF_UPDATE_PACKAGE": "pipy-test",
            },
            opener=version_opener,
        )
        checks.append(
            _check(
                "version-check-opt-out",
                offline_result is None and skip_result is None and not network_called,
                "Version checks honor PIPY_OFFLINE/PIPY_SKIP_VERSION_CHECK before opening the network.",
            )
        )

    return checks


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    checks = run_gate()
    ok = all(check.passed for check in checks)
    payload = {
        "ok": ok,
        "checks": [
            {"name": check.name, "passed": check.passed, "detail": check.detail}
            for check in checks
        ],
    }
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        for check in checks:
            status = "ok" if check.passed else "FAIL"
            print(f"{status}: {check.name} - {check.detail}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
