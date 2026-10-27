"""Actual public SDK crash and cold CLI recovery; synthetic tools/HTTP only."""

from __future__ import annotations

import json
import signal
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "scripts"), str(ROOT / "tests/durable")]
from durable_reference import interrupted_values as values  # noqa: E402
from interrupted_cut_support import counts, origins, provider_bodies, raw  # noqa: E402
from sqlite_child_support import PRELUDE as PRELUDE  # noqa: E402
from sqlite_child_support import Child, group_exists  # noqa: E402

CODE = """
from pathlib import Path
from interrupted_cut_support import child_main
child_main(Path(sys.argv[1]),sys.argv[2],sys.argv[3],sys.argv[4],sys.argv[5],say)
"""


def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n")


def actor(folder, records, label, replay, current, action, cut=""):
    with Child(CODE, str(folder), action, replay, current, cut, home=folder) as child:
        events = []
        if action == "start":
            events.append(child.expect("admitted"))
        if cut:
            events.append(child.expect("cut"))
            assert events[-1]["stage"] == cut
            child.kill_group()
            assert child.wait() == -signal.SIGKILL
        else:
            events.append(child.expect("result"))
            assert events[-1]["status"] == "terminal"
            events.append(child.expect("closed"))
            assert child.wait() == 0
        child.wait_stdout_eof()
        evidence = child.evidence()
    evidence.update(
        argv=child.process.args,
        leader_reaped=child.process.poll() is not None,
        group_absent=not group_exists(child.group),
    )
    assert evidence["leader_reaped"] and evidence["group_absent"]
    dump(records / (label + "-process.json"), {"events": events, "process": evidence})
    return events


def run(recipe, output):
    assert recipe == values.RECIPE
    rows = []
    for case in recipe["cases"]:
        folder = output / "stores/native" / case["id"]
        records = output / "native-records" / case["id"]
        folder.mkdir(parents=True)
        actor(folder, records, "cut", case["stored"], "same", "start", "progress-after")
        prefix = raw(folder)
        before = counts(folder)
        actor(folder, records, "resume", case["stored"], case["current"], "cli")
        completed = raw(folder)
        after = counts(folder)
        first = json.loads((folder / "cli.stdout").read_text())
        invocation = json.loads((folder / "cli-invocation.json").read_text())
        assert invocation["code"] == 0 and not (folder / "cli.stderr").read_bytes()
        (folder / "auth/auth.json").unlink(missing_ok=True)
        (folder / "config/models.json").unlink(missing_ok=True)
        actor(folder, records, "reopen", case["stored"], "same", "cli-terminal")
        repeated = json.loads((folder / "cli.stdout").read_text())
        row = {
            "id": case["id"],
            "prefix": prefix,
            "completed": completed,
            "first": first,
            "repeated": repeated,
            "before": before,
            "after": after,
            "reopened_counts": counts(folder),
            "reopened": raw(folder),
            "wire": provider_bodies(folder),
            "invocation": invocation,
            "cut": "SIGKILL-after-progress-COMMIT-before-details-ACK",
        }
        dump(records / "raw.json", row)
        rows.append(row)
    dump(output / "native-raw.json", rows)
    dump(
        output / "native-parent-modules.json",
        {
            str(Path(row["path"]).relative_to(ROOT)): row["sha256"]
            for row in origins().values()
        },
    )

    child_modules = {}
    for path in (output / "native-records").rglob("*-process.json"):
        saved = json.loads(path.read_text())
        for binding in saved["events"][-1]["native_origins"].values():
            child_modules[str(Path(binding["path"]).relative_to(ROOT))] = binding[
                "sha256"
            ]
    dump(output / "native-modules.json", child_modules)


if __name__ == "__main__":
    run(json.loads(Path(sys.argv[1]).read_text()), Path(sys.argv[2]))
