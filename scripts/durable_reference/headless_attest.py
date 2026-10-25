"""Pure joins from retained actual producer/process records to common values."""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path


def process(m, folder, label, argv, cwd, code):
    record = m.d.load(folder / "processes" / (label + ".json"))
    m.proc.attest(m.d, record, folder / "processes", label)
    m.d.require(
        record["argv"] == argv
        and record["cwd"] == str(cwd)
        and record["returncode"] == code,
        "Headless process binding",
    )
    stdout = (folder / "processes" / (label + ".stdout")).read_bytes()
    stderr = (folder / "processes" / (label + ".stderr")).read_bytes()
    return record, stdout, stderr


def quiet(m, folder, label, argv, cwd, code):
    record, stdout, stderr = process(m, folder, label, argv, cwd, code)
    m.d.require(
        not stdout and (stderr.startswith(b"INVALID:") if code == 2 else not stderr),
        "Headless child diagnostics",
    )
    return record


def controls(m, folder):
    script = str(m.ROOT / "scripts/durable_headless_comparison.py")
    for name in m.values.MUTANTS:
        path = folder / "mutants" / (name + ".json")
        wanted = m.values.mutate(m.d.load(folder / "native.json"), name, m.v)
        m.d.require(m.d.same(wanted, m.d.load(path)), "Headless retained mutant")
        m.d.require(
            m.compare(m.d.load(folder / "observed.json"), wanted) == 1,
            "Headless mutant no longer discriminates",
        )
        for suffix, target, code in (
            ("positive", folder / "native.json", 0),
            ("negative", path, 1),
        ):
            quiet(
                m,
                folder,
                name + "-" + suffix,
                [
                    sys.executable,
                    script,
                    "compare",
                    str(folder / "observed.json"),
                    str(target),
                ],
                m.ROOT,
                code,
            )
    profile_controls(m, folder, script)


def profile_controls(m, folder, script):
    policy = m.d.load(folder / "profile-policy.json")
    for name in m.profiles.MUTANTS:
        path = folder / "mutants/profiles" / (name + ".json")
        wanted = copy.deepcopy(policy)
        wanted["native"] = m.profiles.mutate(wanted["native"], name)
        m.d.require(
            m.d.same(wanted, m.d.load(path)) and m.profile_check(wanted) == 1,
            "Headless retained profile mutant",
        )
        for suffix, target, code in (
            ("positive", folder / "profile-policy.json", 0),
            ("negative", path, 1),
        ):
            quiet(
                m,
                folder,
                "profile-" + name + "-" + suffix,
                [sys.executable, script, "profile-check", str(target)],
                m.ROOT,
                code,
            )
    for mode, source in (
        ("compare", "native.json"),
        ("profile-check", "profile-policy.json"),
    ):
        path = folder / "mutants" / (mode + "-invalid.json")
        wanted = m.d.load(folder / source)
        wanted["unknown"] = True
        m.d.require(
            m.d.same(wanted, m.d.load(path)), "Headless malformed body retention"
        )
        try:
            (m.trace if mode == "compare" else m.profile_protocol)(wanted)
        except m.o.EvidenceError:
            pass
        else:
            raise m.o.EvidenceError("Headless malformed body accepted")
        args = (
            [str(folder / "observed.json"), str(path)]
            if mode == "compare"
            else [str(path)]
        )
        quiet(
            m,
            folder,
            mode + "-invalid",
            [sys.executable, script, mode, *args],
            m.ROOT,
            2,
        )


def native(m, folder):
    argv = [
        sys.executable,
        "-I",
        str(m.NATIVE),
        "common",
        str(folder / "input.json"),
        str(folder),
    ]
    record = quiet(m, folder, "native-common", argv, m.ROOT, 0)
    raw = m.d.load(folder / "native-common-raw.json")
    m.d.require([r["id"] for r in raw] == list(m.CASES), "Headless native raw cases")
    actual = m.d.load(folder / "native.json")
    for case, projected in zip(raw, actual["cases"], strict=True):
        m.d.require(
            m.d.same(
                m.v.encode(m.values.project(case["raw"], m.parent)),
                projected["observed"],
            ),
            "Headless native raw projection drift",
        )
        native_invocations(m, folder, case, record["pid"])
    for name in m.profiles.IDS:
        native_profile(m, folder, name)


def native_invocations(m, folder, case, pid):
    records = folder / "native-records/common" / case["id"]
    m.d.require(
        m.d.same(case["raw"], m.d.load(records / "raw.json")),
        "Headless raw source join",
    )
    for label, key in (("start", "first"), ("terminal-resume", "repeated")):
        invocation = m.d.load(records / (label + "-invocation.json"))
        m.d.require(
            invocation["producer_pid"] == pid
            and invocation["code"] == 0
            and invocation["in_process_cli"] is True
            and invocation["argv"] == common_argv(folder, case["id"], label),
            "Headless actual CLI invocation binding",
        )
        m.d.require(
            m.d.same(m.d.load(records / (label + ".stdout")), case["raw"][key])
            and not (records / (label + ".stderr")).read_bytes(),
            "Headless serialized CLI source join",
        )


def common_argv(folder, name, label):
    path = folder / "stores/native" / name
    argv = [
        "durable",
        "start" if label == "start" else "resume",
        "--store",
        str(path / "store.sqlite"),
        "--workspace",
        str(path),
        "--format",
        "json",
    ]
    if label == "start":
        argv += ["--provider", "openai", "--model", "gpt-test"]
        if name == "plain":
            argv += ["--no-tools"]
        argv += ["--prompt", "input"]
    return argv


def native_profile(m, folder, name):
    records = folder / "native-records/profiles" / name
    for mode, code in (("profile-capture", -9), ("profile-resume", 0)):
        argv = [
            sys.executable,
            "-I",
            str(m.NATIVE),
            mode,
            str(folder / "input.json"),
            str(folder),
            "--case",
            name,
        ]
        record = quiet(m, folder, "native-" + name + "-" + mode, argv, m.ROOT, code)
        if mode == "profile-resume":
            invocation = m.d.load(records / (mode + "-invocation.json"))
            result = m.d.load(records / "resume.json")
            m.d.require(
                invocation["producer_pid"] == record["pid"]
                and invocation["code"] == result["code"]
                and invocation["argv"] == profile_argv(folder, name)
                and m.d.same(m.d.load(records / (mode + ".stdout")), result["output"])
                and (records / (mode + ".stderr")).read_bytes()
                == (
                    b"Durable command unavailable: failed\n"
                    if name == "prepared-current"
                    else b""
                ),
                "Headless P16 CLI result join",
            )
    capture = m.d.load(records / "capture.json")
    reader = capture["independent"]
    argv = [
        sys.executable,
        "-I",
        str(m.parent.READER),
        str(folder / "stores/native-profile" / name / "store.sqlite"),
        str(capture["entry"]),
        str(capture["task"]["id"]),
    ]
    process_record, stdout, stderr = process(
        m, folder, "native-profile-" + name + "-reader", argv, m.ROOT, 0
    )
    reader_value(m, reader, argv, m.ROOT, process_record["pgid"])
    m.d.require(
        m.d.same(process_record, reader["process"])
        and reader["observed"]["pid"] == process_record["pid"]
        and stdout.decode() == reader["stdout"]
        and not stderr,
        "Headless independent process receipt join",
    )


def profile_argv(folder, name):
    path = folder / "stores/native-profile" / name
    return [
        "durable",
        "resume",
        "--store",
        str(path / "store.sqlite"),
        "--workspace",
        str(path),
        "--format",
        "json",
        "--provider",
        "openai",
        "--model",
        "gpt-test",
        "--thinking",
        "low",
        "--no-tools",
    ]


def reader_value(m, value, argv, cwd, pgid):
    observed = json.loads(value["stdout"])
    m.d.require(
        value["argv"] == argv
        and value["cwd"] == str(cwd)
        and value["status"] == 0
        and not value["stderr"]
        and m.d.same(value["observed"], observed),
        "Headless committed reader inputs",
    )
    m.d.require(
        observed["pgid"] == pgid
        and observed["helper_sha256"] == m.o.sha(m.parent.READER)
        and Path(observed["executable"]).resolve() == Path(sys.executable).resolve(),
        "Headless reader identity",
    )
    if "pid" in value:
        m.d.require(value["pid"] == observed["pid"], "Headless reader PID join")


def reference(m, folder, snapshot):
    argv = [
        "node",
        "--import",
        str(snapshot / m.o.RESOLVER),
        str(snapshot / "headless.mts"),
        str(folder / "input.json"),
        str(folder),
        sys.executable,
        str(m.parent.READER),
    ]
    record = quiet(m, folder, "pi-headless", argv, snapshot, 0)
    runtime = m.d.load(folder / "runtime.json")
    runtime_value(m, runtime, record["pid"], record["executable"], snapshot)
    m.d.require(
        runtime["argv"] == [runtime["executable"], *argv[3:]],
        "Headless Node actual argv",
    )
    for name, source in m.DRIVERS.items():
        m.d.require(
            m.o.sha(snapshot / name) == m.o.sha(source), "Headless copied driver drift"
        )
    raw = m.d.load(folder / "raw-stored.json")
    m.d.require([r["id"] for r in raw] == list(m.CASES), "Headless Pi raw cases")
    for case, projected in zip(
        raw, m.d.load(folder / "observed.json")["cases"], strict=True
    ):
        m.d.require(
            m.d.same(m.v.encode(reference_projection(m, case)), projected["observed"]),
            "Headless Pi raw projection drift",
        )
        m.d.require(
            len(case["independent"]) == 1, "Headless Pi committed reader absent"
        )
        reader = case["independent"][0]
        observed = reader["observed"]["stored"]
        args = [
            sys.executable,
            "-I",
            str(m.parent.READER),
            str(folder / "stores/pi" / (case["id"] + ".sqlite")),
            str(observed["entry"]["id"]),
            str(observed["task"]["id"]),
        ]
        reader_value(m, reader, args, snapshot, record["pid"])
    for case in m.d.load(folder / "profile-reference.json"):
        reference_profile(m, folder, snapshot, case, record)


def runtime_value(m, value, pid, executable, cwd):
    m.d.require(
        value["pid"] == pid
        and Path(value["executable"]).resolve() == Path(executable).resolve()
        and value["cwd"] == str(cwd)
        and value["version"] == m.o.manifest()["runtime"]["node"]
        and type(value["sqlite"]) is str,
        "Headless actual Node runtime",
    )


def reference_profile(m, folder, snapshot, case, outer):
    for mode, field in (("capture", "capture"), ("resume", "resumed")):
        label = "pi-profile-" + case["id"] + "-" + mode
        record = m.d.load(folder / "processes" / (label + ".json"))
        raw = case[field]
        argv = [
            outer["executable"],
            "--import",
            str(snapshot / m.o.RESOLVER),
            str(snapshot / "headless-profile.mts"),
            "--profile-worker",
            str(folder / "input.json"),
            str(folder),
            case["id"],
            mode,
            sys.executable,
            str(m.parent.READER),
        ]
        m.d.require(
            record["argv"] == argv
            and record["cwd"] == str(snapshot)
            and record["status"] == (None if mode == "capture" else 0)
            and record["signal"] == ("SIGKILL" if mode == "capture" else None),
            "Headless Node child operation",
        )
        m.d.require(
            not record["stdout"]
            and not record["stderr"]
            and not (folder / "processes" / (label + ".stdout")).read_bytes()
            and not (folder / "processes" / (label + ".stderr")).read_bytes(),
            "Headless Node child streams",
        )
        m.d.require(
            record["input_before"]
            == record["input_after"]
            == m.o.sha(folder / "input.json")
            and record["worker_before"]
            == record["worker_after"]
            == m.o.sha(m.DRIVERS["headless-profile.mts"])
            and m.d.same(record, case["processes"][0 if mode == "capture" else 1]),
            "Headless Node child immutable input",
        )
        m.d.require(
            m.d.same(
                raw,
                m.d.load(
                    folder / "profile-records" / (case["id"] + "-" + mode + ".json")
                ),
            ),
            "Headless Node child raw output",
        )
        runtime_value(m, raw["runtime"], record["pid"], outer["executable"], snapshot)
        m.d.require(
            raw["runtime"]["argv"] == [raw["runtime"]["executable"], *argv[3:]]
            and raw["runtime"]["execArgv"] == argv[1:3],
            "Headless Node worker actual argv",
        )
    capture = case["capture"]
    argv = [
        sys.executable,
        "-I",
        str(m.parent.READER),
        str(folder / "stores/pi-profile" / (case["id"] + ".sqlite")),
        str(capture["entry"]),
        str(capture["task"]["id"]),
    ]
    reader_value(m, capture["independent"], argv, snapshot, outer["pid"])


def reference_projection(m, raw):
    messages = [
        e["model"][0]
        for e in raw["retained"]["entries"]
        if len(e.get("model", [])) == 1
    ]
    assistants = [x for x in messages if x["role"] == "assistant"]
    caller = next(
        (x for x in assistants if any(c["type"] == "toolCall" for c in x["content"])),
        None,
    )
    calls = (
        []
        if caller is None
        else [c for c in caller["content"] if c["type"] == "toolCall"]
    )
    results = [
        next(
            x
            for x in messages
            if x["role"] == "toolResult" and x["toolCallId"] == c["id"]
        )
        for c in calls
    ]
    users = [x for x in messages if x["role"] == "user"]
    logical = (
        users
        + ([] if caller is None else [caller])
        + results
        + [x for x in assistants if x is not caller]
    )
    usage = raw["ledger"]["models"]["openai/gpt-test"]
    tasks = [x for x in raw["tasks"] if x["kind"] in ("pi.generation", "pi.tool")]
    content = users[0]["content"]
    return {
        "user_text": content
        if type(content) is str
        else "".join(c["text"] for c in content if c["type"] == "text"),
        "completed": any(t["kind"] == "pi.generation" for t in tasks)
        and all(
            t["state"]["status"] == "terminal"
            and t["state"]["outcome"]["status"] == "completed"
            for t in tasks
        ),
        "roles": [x["role"] for x in logical],
        "result_names": [x["toolName"] for x in results],
        "result_text": [
            "".join(c["text"] for c in x["content"] if c["type"] == "text")
            for x in results
        ],
        "provider_calls": raw["after"]["providers"],
        "tool_calls": raw["after"]["calls"],
        "applications": raw["after"]["applications"],
        "prepares": raw["after"]["prepares"],
        "reconnect": m.d.same(raw["final"]["entries"], raw["retained"]["entries"]),
        "repeat_unchanged": m.d.same(raw["before"], raw["after"])
        and raw["repeated"]["status"] == "done",
        "usage": usage,
        "usage_once": m.parent.tools.usage_matches(
            [a["usage"] for a in assistants], usage
        ),
    }
