"""Test-only actual pinned SDK actors and immutable origin/process receipts."""

import hashlib
import json
import os
import signal
import sqlite3
import subprocess
import sys
import time
from contextlib import closing
from pathlib import Path

Q = Path(__file__).parent
PYTHON = sys.executable


PINS = {
    "B": (
        "a069a8a81affb8672f78f33177c490dce84c1588",
        "28ee70a2f61eec9fd5eb3b221b335780b9ef9efc",
    ),
    "DE12": (
        "df5c31373a1cca45812200bf3b3e949244f924f1",
        "07f8a01cff191ff9e9a56ca5214add54c73682b1",
    ),
}
ANCHORS = {
    "B": {
        "src/pipy_harness/durable_product_api.py": "40c7f6baf5eb0d5d0916b8e5e28a8c8d8a8e011c53320f7d6ad5746ba2c565a9",
        "src/pipy_harness/native/durable/tool_execution.py": "4ca9ea19077f8c76a90d3fe9917b4e466bb74b8166c9fcd43011ee27f648bdfd",
        "src/pipy_harness/native/durable/tool_values.py": "bf8b63806867085afc5116e2fa650e1a52bf11729ea61697679f8d4d21e65c56",
        "tests/durable/test_product_session.py": "bdbdc6eace2b3ba9f68afa13ca24cf9bfd2ae05a8b610a084621be7bdb628bfc",
        "tests/durable/test_resolution.py": "b6934d819318d3e2389a29d9ad21d513f3ded8c7886ef6eeddd08a1dff04b755",
        "tests/durable/tool_support.py": "f4cebfd8a5105ab44584321a17a2a495272668f46607f4d107e71043256abbd2",
    },
    "DE12": {
        "src/pipy_harness/durable_product_api.py": "56dfdd91725efeea0bf0f5786806c1010d9172d447f4ddb4a4b3d9448b7378b4",
        "src/pipy_harness/native/durable/tool_execution.py": "2b4adf23cee4eeb3bbc7f468e4ed3c3effdc162824cda5036f26f718ccab095e",
        "src/pipy_harness/native/durable/tool_values.py": "2daffce4cddf37f2acaf9953a729f7cba17d76b33ee2efde6584a1e6f8358d5a",
        "tests/durable/test_product_session.py": "94a0246cace1a1936d79e1e27c62db058f412f3a88ee4e991c1b3b0a743e7a79",
        "tests/durable/test_resolution.py": "8b9a6caeba274f6fdcbacb65e75bbf35c46145d1c51583b8c8757db9b5db9486",
        "tests/durable/tool_support.py": "d2551f0a9ab068915e2c327fbc867b2c9d669cf2a4f576585c0c4f27fdd63eb1",
    },
}


def historical_sources(root):
    from interrupted_cut_support import archived_git_source

    override = os.environ.get("PIPY_C2_HISTORICAL_SOURCE_ROOT")
    sources = Path(override).resolve() if override else root / "historical-sources"
    evidence = {}
    for role, (commit, tree) in PINS.items():
        source = sources / role / "source"
        if override:
            evidence[role] = {
                "mode": "explicit pinned source override",
                "commit": commit,
                "tree": tree,
                "source": str(source),
            }
        else:
            evidence[role] = archived_git_source(source, commit, tree, ("src", "tests"))
        for name, digest in ANCHORS[role].items():
            assert hashlib.sha256((source / name).read_bytes()).hexdigest() == digest, (
                role,
                name,
            )
        evidence[role]["validated_anchors"] = ANCHORS[role]
    dump(root / "historical-source-provenance.json", evidence)
    return sources


def dump(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")


def absent(pgid):
    try:
        os.killpg(pgid, 0)
        return False
    except ProcessLookupError:
        return True


def minimal_env(root):
    env = {
        k: v
        for k, v in os.environ.items()
        if k in ["PATH", "LANG", "LC_ALL", "SYSTEMROOT", "DYLD_LIBRARY_PATH"]
    }
    for key in [
        "HOME",
        "PIPY_AUTH_DIR",
        "PIPY_CONFIG_HOME",
        "XDG_CONFIG_HOME",
        "XDG_STATE_HOME",
        "TMPDIR",
    ]:
        p = root / "private" / key
        p.mkdir(parents=True, exist_ok=True)
        env[key] = str(p)
    env.update(
        PYTHONDONTWRITEBYTECODE="1",
        PIPY_NATIVE_THEME_PATH=str(root / "private/theme.json"),
    )
    return env


def actor(root, role, action, cut):
    import pipy_harness

    source = (
        Path(pipy_harness.__file__).resolve().parents[2]
        if role == "C1"
        else Path(os.environ["PIPY_C2_HISTORICAL_SOURCE_ROOT"]) / role / "source"
    )
    program = (
        "import sys\nsys.dont_write_bytecode=True\nsys.path[:0]="
        + repr(
            [str(source / "src"), str(source / "tests"), str(source / "tests/durable")]
        )
        + "\n"
        + (Q / "c2_historical_actor.py").read_text()
    )
    argv = [PYTHON, "-I", "-c", program, str(root), role, action, cut]
    env = minimal_env(root)
    env["HIST_SOURCE"] = str(source)
    label = "actor-" + role + "-" + action + "-" + cut
    out = root / (label + ".stdout")
    err = root / (label + ".stderr")
    started = time.time()
    killed = False
    events = []
    with out.open("wb") as stdout, err.open("wb") as stderr:
        p = subprocess.Popen(
            argv,
            cwd=source,
            env=env,
            stdout=stdout,
            stderr=stderr,
            start_new_session=True,
        )
        receipt = {
            "argv": argv,
            "cwd": str(source),
            "pid": p.pid,
            "pgid": p.pid,
            "deadline_seconds": 45,
            "source": str(source),
            "start_unix": started,
            "minimal_noauth_env": True,
        }
        dump(root / (label + "-LAUNCH.json"), receipt)
        try:
            if (action == "produce" and cut in ["effect", "intent-after"]) or (
                action == "recover" and cut == "receipt-after"
            ):
                deadline = time.monotonic() + 40
                while time.monotonic() < deadline:
                    events = [
                        json.loads(line)
                        for line in out.read_text().splitlines()
                        if line
                    ]
                    if any(e["event"] == "cut" for e in events):
                        os.killpg(p.pid, signal.SIGKILL)
                        killed = True
                        break
                    if p.poll() is not None:
                        break
                    time.sleep(0.02)
            p.wait(timeout=max(1, 45 - (time.time() - started)))
        finally:
            if p.poll() is None:
                os.killpg(p.pid, signal.SIGKILL)
                p.wait()
            receipt.update(
                exit=p.returncode,
                reaped=True,
                group_absent=absent(p.pid),
                elapsed_seconds=time.time() - started,
                kill_issued=killed,
                streams={
                    v.name: hashlib.sha256(v.read_bytes()).hexdigest()
                    for v in [out, err]
                },
            )
            dump(root / (label + "-RESULT.json"), receipt)
    events = [json.loads(line) for line in out.read_text().splitlines() if line]
    assert receipt["group_absent"] and p.returncode == (-9 if killed else 0), (
        receipt,
        err.read_text(),
    )
    assert not err.read_text(), err.read_text()
    if killed:
        assert (
            len(events) == 1
            and events[0]["event"] == "cut"
            and events[0]["label"] == cut
        )
    else:
        assert events[-1]["event"] == "closed"
    return receipt


def historical(root, role, cut):
    root.mkdir(parents=True, exist_ok=True)
    actor(root, role, "produce", cut)
    if cut in ["intent-after", "effect"]:
        actor(root, role, "recover", cut)
    value = json.loads((root / "HISTORICAL-FRONTIER.json").read_text())
    child = value["child"]
    assert (
        child["state"]["status"] == "pending"
        and child["state"]["checkpoint"]["phase"] == "blocked"
    )
    if cut != "preintent":
        assert child["state"]["checkpoint"]["intent"]["workspace"] == str(root)
    assert not any(d["kind"] == "pi.tool.receipt" for d in value["graph"]["documents"])
    assert not any(e["kind"] == "pi.tool-result" for e in value["graph"]["entries"])
    assert value["counts"] == {
        "calls": 0 if cut in ["intent-after", "preintent"] else 1,
        "applications": 0 if cut in ["intent-after", "preintent"] else 1,
        "prepares": 0 if cut == "preintent" else 1,
        "providers": 1,
    }
    return value


def graph(root):
    with closing(
        sqlite3.connect("file:" + str(root / "product.sqlite") + "?mode=ro", uri=True)
    ) as db:
        db.execute("BEGIN")
        records = {
            table: [
                json.loads(bytes(r[0]).decode("utf8", "surrogatepass"))
                for r in db.execute("SELECT record FROM " + table + " ORDER BY id")
            ]
            for table in ["conversations", "tasks", "entries", "documents"]
        }
        for table in (
            "document_revisions",
            "durable_metadata",
            "record_ids",
            "submissions",
            "submission_requests",
        ):
            records[table] = [
                [value.hex() if type(value) is bytes else value for value in row]
                for row in db.execute("SELECT * FROM " + table + " ORDER BY rowid")
            ]
        return records


def keyed_counts(root, key):
    with closing(sqlite3.connect(root / "effects.sqlite")) as db:
        return {
            name: db.execute(
                "SELECT count(*) FROM " + name + " WHERE key=?", (key,)
            ).fetchone()[0]
            for name in ["calls", "applications"]
        }
