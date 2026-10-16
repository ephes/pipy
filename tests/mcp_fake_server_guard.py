"""Fails a test module that leaves a fake MCP server process behind.

A pytest plugin, loaded by ``conftest.py``. The fake server
(``fixtures/mcp_fake_server.py``) can be told to ignore the end of its stdin
and SIGTERM, so a test that does not close its transport, or that kills the
pipy process it started, leaves the server running. After the last test of
each module the guard looks for fake servers, and their ``spawn`` children,
whose scenario file is in the temporary directory of this test run. It ends
them and fails the module with their command lines; the directory in a
command line is named after the test that started the process.

Limits:

- Only processes started with a scenario file below this run's base temporary
  directory are seen, which is where ``tmp_path`` puts them. Another test run
  on the same machine is never touched.
- A process that is on its way out is waited for, for up to
  ``_EXIT_WAIT_SECONDS``, so a server that ends late is not reported.
- A fake server ends itself some minutes after its parent process ended (see
  the fixture). One that a killed pipy left behind that long before the end
  of the module is gone when the guard looks.
"""

from __future__ import annotations

import os
import signal
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

FAKE_SERVER_NAME = "mcp_fake_server.py"
_EXIT_WAIT_SECONDS = 2.0


def fake_servers(under: Path) -> dict[int, str]:
    """The command lines, by pid, of the running fake server processes whose
    scenario file is below ``under``."""

    roots = {f"{root}{os.sep}" for root in (str(under), os.path.realpath(under))}
    listing = subprocess.run(  # noqa: S603
        ["ps", "-axww", "-o", "pid=,command="],  # noqa: S607
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    found: dict[int, str] = {}
    for line in listing.splitlines():
        pid, _, command = line.strip().partition(" ")
        if FAKE_SERVER_NAME in command and any(root in command for root in roots):
            found[int(pid)] = command.strip()
    return found


def end_fake_servers(under: Path) -> dict[int, str]:
    """Kill the fake servers below ``under`` that do not end by themselves.

    Returns the ones that had to be killed.
    """

    deadline = time.monotonic() + _EXIT_WAIT_SECONDS
    left = fake_servers(under)
    while left and time.monotonic() < deadline:
        time.sleep(0.05)
        left = fake_servers(under)
    for pid in left:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass  # It ended after the listing.
    return left


@pytest.fixture(autouse=True, scope="module")
def _no_fake_mcp_server_left_behind(
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[None]:
    yield
    if os.name != "posix":
        return
    left = end_fake_servers(tmp_path_factory.getbasetemp())
    if left:
        commands = "\n".join(f"  {pid}: {command}" for pid, command in left.items())
        pytest.fail(
            "A test in this module left fake MCP server processes running "
            f"(they were killed now):\n{commands}",
            pytrace=False,
        )
