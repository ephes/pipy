"""DE11 comparison children: actual process identities and retained pure inputs."""

from __future__ import annotations

import hashlib
import os
import shutil
import signal
import subprocess
import time
from pathlib import Path

BASE = {
    "argv",
    "cwd",
    "returncode",
    "leader_reaped",
    "signal",
    "stdout_sha256",
    "stderr_sha256",
    "seconds",
    "timeout",
}
EXTRA = {
    "pid",
    "pgid",
    "executable",
    "group_absent",
    "inputs_before",
    "inputs_after",
    "runner_sha256",
}


def inputs(argv):
    result = {}
    for index, argument in enumerate(argv):
        if index > 0 and argv[index - 1] == "-c":
            result["argv:" + str(index)] = hashlib.sha256(argument.encode()).hexdigest()
        else:
            path = Path(argument)
            if (
                path.is_absolute()
                and path.is_file()
                and path.suffix in (".py", ".mts", ".json")
            ):
                result[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def run(d, argv, cwd, output, label, env, timeout=120):
    before, started = inputs(argv), time.monotonic()
    executable = shutil.which(argv[0], path=env.get("PATH"))
    d.require(executable is not None, "Product child executable absent")
    child = subprocess.Popen(
        argv,
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    pgid = os.getpgid(child.pid)
    stdout, stderr, killed = b"", b"", False
    try:
        stdout, stderr = child.communicate(timeout=timeout)
    finally:
        if child.returncode is None:
            killed = True
            # The unreaped leader still reserves this process-group identity.
            try:
                os.killpg(pgid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            stdout, stderr = child.communicate(timeout=10)
        try:
            os.killpg(pgid, 0)
            absent = False
        except ProcessLookupError:
            absent = True
        (output / (label + ".stdout")).write_bytes(stdout)
        (output / (label + ".stderr")).write_bytes(stderr)
        record = {
            "argv": argv,
            "cwd": str(cwd),
            "returncode": child.returncode,
            "leader_reaped": child.returncode is not None,
            "signal": "SIGKILL" if killed else None,
            "stdout_sha256": d.o.sha(output / (label + ".stdout")),
            "stderr_sha256": d.o.sha(output / (label + ".stderr")),
            "seconds": time.monotonic() - started,
            "timeout": timeout,
            "pid": child.pid,
            "pgid": pgid,
            "executable": str(Path(executable).resolve()),
            "group_absent": absent,
            "inputs_before": before,
            "inputs_after": inputs(argv),
            "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        }
        d.dump(output / (label + ".json"), record)
    attest(d, record, output, label)
    return stdout, stderr, child.returncode


def attest(d, record, output, label):
    d.shape(record, BASE | EXTRA, "Product process receipt")
    d.process_record({key: record[key] for key in BASE}, output, label)
    d.require(
        type(record["pid"]) is int
        and record["pid"] > 0
        and record["pid"] == record["pgid"],
        "Product process identity",
    )
    d.require(
        record["group_absent"] is True and record["leader_reaped"] is True,
        "Product process still owned",
    )
    d.require(
        Path(record["executable"]).is_absolute()
        and Path(record["executable"]).is_file(),
        "Product executable identity",
    )
    d.require(
        record["inputs_before"] == record["inputs_after"] == inputs(record["argv"]),
        "Product process input drift",
    )
    d.require(
        record["runner_sha256"]
        == hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "Product runner drift",
    )
