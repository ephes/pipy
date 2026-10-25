"""Retained actual CLI argv/streams/reap evidence for synthetic fixtures only."""

import hashlib
import json
import subprocess
import sys
from pathlib import Path

from sqlite_child_support import group_exists, kill_group


def run_cli(
    root: Path,
    label: str,
    arguments: list[str],
    *,
    data: bytes | None = None,
    stdin=None,
) -> subprocess.CompletedProcess[bytes]:
    argv = [sys.executable, "-m", "pipy_harness.cli", "durable", *arguments]
    process = subprocess.Popen(
        argv,
        stdin=subprocess.PIPE if data is not None else stdin,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        stdout, stderr = process.communicate(input=data, timeout=30)
    finally:
        if process.poll() is None:
            kill_group(process.pid)
            process.communicate(timeout=10)
    assert not group_exists(process.pid)
    root.mkdir(parents=True, exist_ok=True)
    (root / (label + ".stdout")).write_bytes(stdout)
    (root / (label + ".stderr")).write_bytes(stderr)
    receipt = {
        "argv": argv,
        "cwd": str(Path.cwd()),
        "pid": process.pid,
        "pgid": process.pid,
        "exit_code": process.returncode,
        "reaped": True,
        "group_absent": True,
        "stdout_sha256": hashlib.sha256(stdout).hexdigest(),
        "stderr_sha256": hashlib.sha256(stderr).hexdigest(),
        "fixture_only": True,
    }
    (root / (label + "-process.json")).write_text(json.dumps(receipt, indent=2) + "\n")
    return subprocess.CompletedProcess(argv, process.returncode, stdout, stderr)
