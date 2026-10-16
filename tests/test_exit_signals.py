"""SIGTERM and SIGHUP end pipy like an exit (`pipy_harness.exit_signals`).

The context manager changes the signal handlers of its process, so each case
runs in a process of its own.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

_PRELUDE = """
import atexit, os, signal, sys, threading, time
from pipy_harness.exit_signals import exit_on_termination_signals

def say(text):
    print(text, flush=True)

def signal_self(name):
    os.kill(os.getpid(), getattr(signal, name))
    time.sleep(0.2)  # The handler runs on this thread, here at the latest.
"""


def _run(body: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        [sys.executable, "-c", _PRELUDE + body],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


@pytest.mark.parametrize(("name", "status"), [("SIGTERM", 143), ("SIGHUP", 129)])
def test_the_signal_unwinds_the_main_thread_and_runs_the_exit_hooks(
    name: str, status: int
) -> None:
    run = _run(
        f"""
atexit.register(say, "exit hook")
with exit_on_termination_signals():
    try:
        signal_self("{name}")
        say("not reached")
    finally:
        say("finally")
"""
    )

    assert run.returncode == status, run.stderr
    assert run.stdout.splitlines() == ["finally", "exit hook"]


def test_without_it_the_signal_ends_the_process_at_once() -> None:
    run = _run(
        """
atexit.register(say, "exit hook")
try:
    signal_self("SIGTERM")
finally:
    say("finally")
"""
    )

    assert run.returncode == -15
    assert run.stdout == ""


def test_a_later_signal_does_not_cut_the_shutdown_short() -> None:
    run = _run(
        """
def late():
    signal_self("SIGHUP")
    say("exit hook")

atexit.register(late)
with exit_on_termination_signals():
    try:
        signal_self("SIGTERM")
    finally:
        signal_self("SIGTERM")
        say("finally")
"""
    )

    # The status of the first signal; the hook ran after the context ended.
    assert run.returncode == 143, run.stderr
    assert run.stdout.splitlines() == ["finally", "exit hook"]


def test_an_ignored_signal_stays_ignored() -> None:
    run = _run(
        """
signal.signal(signal.SIGHUP, signal.SIG_IGN)
with exit_on_termination_signals():
    signal_self("SIGHUP")
    say("went on")
say(signal.getsignal(signal.SIGHUP) is signal.SIG_IGN)
"""
    )

    assert run.returncode == 0, run.stderr
    assert run.stdout.splitlines() == ["went on", "True"]


def test_a_handler_of_the_embedding_program_stays_in_charge() -> None:
    run = _run(
        """
def own(signum, frame):
    say("own handler")

signal.signal(signal.SIGTERM, own)
with exit_on_termination_signals():
    signal_self("SIGTERM")
    say("went on")
say(signal.getsignal(signal.SIGTERM) is own)
"""
    )

    assert run.returncode == 0, run.stderr
    assert run.stdout.splitlines() == ["own handler", "went on", "True"]


def test_the_default_actions_are_back_after_a_command_without_a_signal() -> None:
    run = _run(
        """
with exit_on_termination_signals():
    say(signal.getsignal(signal.SIGTERM) is signal.SIG_DFL)
say(signal.getsignal(signal.SIGTERM) is signal.SIG_DFL)
say(signal.getsignal(signal.SIGHUP) is signal.SIG_DFL)
"""
    )

    assert run.returncode == 0, run.stderr
    assert run.stdout.splitlines() == ["False", "True", "True"]


def test_off_the_main_thread_nothing_changes() -> None:
    run = _run(
        """
def work():
    with exit_on_termination_signals():
        say(signal.getsignal(signal.SIGTERM) is signal.SIG_DFL)

thread = threading.Thread(target=work)
thread.start()
thread.join()
"""
    )

    assert run.returncode == 0, run.stderr
    assert run.stdout.splitlines() == ["True"]
