"""Parse the actual published authorization/reuse examples before any effects."""

import re
import shlex
from pathlib import Path

from pipy_harness.native.automation.durable_args import parse_args


def test_actual_documented_retry_commands_have_valid_matching_nonces():
    guide = Path(__file__).resolve().parents[2] / "docs/durable-headless.md"
    blocks = re.findall(r"```bash\n(.*?)```", guide.read_text(), re.DOTALL)
    block = next(body for body in blocks if "pipy durable authorize-retry" in body)
    commands = [
        shlex.split(line)
        for line in block.replace("\\\n", " ").splitlines()
        if line.strip()
    ]
    assert len(commands) == 2
    parsed = []
    for command in commands:
        assert command[:2] == ["pipy", "durable"]
        parsed.append(parse_args(command[2:]))
    assert [c.action for c in parsed] == ["authorize-retry", "resume"]
    assert parsed[0].nonce == parsed[1].nonce
    assert re.fullmatch("[0-9a-f]{32}", parsed[0].nonce or "")
    assert all(c.thinking == "low" and c.no_tools for c in parsed)
