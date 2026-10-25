"""Real inherited descriptions: kill cannot change parent flags or detach I/O."""

import contextlib
import fcntl
import hashlib
import json
import os
import pty
import select
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest
from sqlite_child_support import group_exists, kill_group

ROOT = Path(__file__).resolve().parents[2]
PROGRAM = r"""
import sys,os,json,threading
from pathlib import Path
sys.path.insert(0,str(Path(sys.argv[1])/'src'))
from pipy_harness import cli,durable_cli
from pipy_harness.native.catalog import NativeModelSpec
from pipy_harness.native.http import JsonResponse
from pipy_harness.sdk import DurableProviderProfile
root=Path(sys.argv[2]); direction=sys.argv[3]; fd=int(sys.argv[4])
events=sys.stdout;control=sys.stdin
def say(event,**fields):
 events.write(json.dumps({'event':event,'pid':os.getpid(),**fields})+'\n');events.flush()
class Wire:
 def post_json(self,*a,**kw):
  return JsonResponse(200,{'choices':[{'message':{'content':'done'},'finish_reason':'stop'}]})
durable_cli.build_profile=lambda _:DurableProviderProfile(
 NativeModelSpec('openai','test','Test','openai-completions'),auth_path=root/'auth.json',
 env={},runtime_api_key='fixture-key',http_client=Wire())
original=os.read if direction=='input' else os.write
def actual_io(target,value):
 result=original(target,value)
 if target==fd:
  say('actual-io-held',thread=threading.get_ident(),main_thread=threading.main_thread().ident,
      module=cli.__file__,bytes=len(result) if isinstance(result,bytes) else result)
  assert control.readline().strip()=='release'
 return result
if direction=='input':
 os.read=actual_io;sys.stdin=os.fdopen(fd,'r',closefd=False)
else:
 os.write=actual_io;sys.stdout=os.fdopen(fd,'w',closefd=False)
args=['durable','start','--store',str(root/'run.sqlite'),'--workspace',str(root),
 '--provider','openai','--model','test','--no-tools','--format','json']
args+=['--stdin'] if direction=='input' else ['--prompt','input']
raise SystemExit(cli.main(args))
"""


def shared_descriptor(root, kind, direction):
    if kind == "regular":
        path = root / "stream"
        path.write_bytes(b"input" if direction == "input" else b"")
        fd = os.open(path, os.O_RDWR)
        return fd, [fd]
    if kind == "fifo":
        path = root / "stream.fifo"
        os.mkfifo(path)
        fd = os.open(path, os.O_RDWR)
        if direction == "input":
            os.write(fd, b"input")
        return fd, [fd]
    if kind == "pty":
        master, slave = pty.openpty()
        return slave, [master, slave]
    read, write = os.pipe()
    if direction == "input":
        os.write(write, b"input")
    return read if direction == "input" else write, [read, write]


def establish_output_history(fd, descriptors, kind):
    # Darwin reports kernel-maintained FWASWRITTEN after a positive write.
    # Establish that history before the initial baseline, discard the byte,
    # then compare ALL raw flags. Never mask O_NONBLOCK or any other bit.
    count = os.write(fd, b"W")
    assert count == 1
    if kind == "regular":
        observed = os.pread(fd, 1, 0)
        assert observed == b"W"
        os.ftruncate(fd, 0)
        offset = os.lseek(fd, 0, os.SEEK_SET)
        assert offset == 0 and os.fstat(fd).st_size == 0
    else:
        reader = fd if kind == "fifo" else descriptors[0]
        observed = os.read(reader, 1)
        assert observed == b"W"
        offset = None
    return {
        "written_bytes": count,
        "discarded_hex": observed.hex(),
        "initial_file_offset": offset,
    }


class Process:
    def __init__(self, code, root, *arguments, descriptors=()):
        self.argv = [sys.executable, "-I", "-c", code, str(ROOT), str(root), *arguments]
        self.process = subprocess.Popen(
            self.argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            pass_fds=descriptors,
            start_new_session=True,
            env={
                "PATH": os.defpath,
                "HOME": str(root),
                "PIPY_AUTH_DIR": str(root / "auth"),
                "PIPY_CONFIG_HOME": str(root / "config"),
                "XDG_STATE_HOME": str(root / "state"),
                "LC_ALL": "C.UTF-8",
            },
        )
        self.pending = b""
        self.events = []

    def expect(self, name):
        deadline = time.monotonic() + 15
        assert self.process.stdout is not None
        while time.monotonic() < deadline:
            if b"\n" in self.pending:
                line, self.pending = self.pending.split(b"\n", 1)
                event = json.loads(line)
                self.events.append(event)
                assert event["event"] == name, event
                return event
            ready = select.select(
                [self.process.stdout], [], [], max(0, deadline - time.monotonic())
            )[0]
            assert ready, "Child event deadline"
            data = os.read(self.process.stdout.fileno(), 65536)
            assert data, "Child exited before expected event"
            self.pending += data
        raise AssertionError("Child event deadline")

    def send(self, value):
        assert self.process.stdin is not None
        self.process.stdin.write((value + "\n").encode())
        self.process.stdin.flush()

    def finish(self, root, label, *, kill=False):
        if kill or self.process.poll() is None:
            kill_group(self.process.pid)
        stdout, stderr = self.process.communicate(timeout=10)
        assert not group_exists(self.process.pid)
        streams = self.pending + stdout
        record = {
            "argv": self.argv,
            "cwd": str(Path.cwd()),
            "pid": self.process.pid,
            "pgid": self.process.pid,
            "returncode": self.process.returncode,
            "reaped": True,
            "group_absent": True,
            "events": self.events,
            "stdout_sha256": hashlib.sha256(streams).hexdigest(),
            "stderr_sha256": hashlib.sha256(stderr).hexdigest(),
        }
        (root / (label + ".stdout")).write_bytes(streams)
        (root / (label + ".stderr")).write_bytes(stderr)
        (root / (label + ".json")).write_text(json.dumps(record, indent=2))
        return record


@pytest.mark.parametrize(
    "direction,kind",
    [
        ("input", "regular"),
        ("input", "pipe"),
        ("input", "fifo"),
        ("output", "regular"),
        ("output", "pipe"),
        ("output", "fifo"),
        ("output", "pty"),
    ],
)
@pytest.mark.parametrize("nonblocking", [False, True])
def test_sigkill_actual_cli_io_never_mutates_shared_description_flags(
    tmp_path, direction, kind, nonblocking
):
    fd, descriptors = shared_descriptor(tmp_path, kind, direction)
    child = None
    try:
        history = (
            establish_output_history(fd, descriptors, kind)
            if direction == "output"
            else None
        )
        if nonblocking:
            # Fixture setup, before product invocation; the product must preserve
            # either initial state without any F_SETFL of its own.
            os.set_blocking(fd, False)
        initial = fcntl.fcntl(fd, fcntl.F_GETFL)
        child = Process(PROGRAM, tmp_path, direction, str(fd), descriptors=(fd,))
        held = child.expect("actual-io-held")
        assert held["bytes"] > 0 and Path(held["module"]).is_relative_to(ROOT / "src")
        during = fcntl.fcntl(fd, fcntl.F_GETFL)
        child.finish(tmp_path, "killed", kill=True)
        after = fcntl.fcntl(fd, fcntl.F_GETFL)
        (tmp_path / "flags.json").write_text(
            json.dumps(
                {
                    "initial": initial,
                    "during": during,
                    "after": after,
                    "direction": direction,
                    "kind": kind,
                    "fixture_history": history,
                }
            )
        )
        assert during == initial, (
            "Actual CLI changed the parent's shared open-description flags"
        )
        assert after == initial, "SIGKILL left the parent's shared flags changed"
        assert child.process.returncode == -signal.SIGKILL
        if direction == "input":
            assert not (tmp_path / "run.sqlite").exists()
    finally:
        if child is not None and child.process.poll() is None:
            child.finish(tmp_path, "failure-cleanup", kill=True)
        for target in descriptors:
            with contextlib.suppress(OSError):
                os.close(target)
