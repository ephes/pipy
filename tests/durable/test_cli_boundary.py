"""The durable route admits only closed inputs before any legacy effects."""

import json
import os
import signal
import subprocess
import sys
import threading

import pytest

from pipy_harness import cli
from pipy_harness.native.automation.durable_args import DurableCLIError, parse_args
from pipy_harness.native.automation.durable_lifetime import signal_domain


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    for name in ("HOME", "PIPY_AUTH_DIR", "PIPY_CONFIG_HOME", "XDG_STATE_HOME"):
        monkeypatch.setenv(name, str(tmp_path / name))


@pytest.mark.parametrize(
    "extra",
    [
        ["--api-key", "private-value"],
        ["--api-keys", "private-value"],
        ["--format", "private-value"],
        ["--thinking", "private-value"],
        ["--fork"],
        ["--compact"],
        ["--stream"],
        ["--follow-up", "private-value"],
    ],
)
def test_fixed_argument_errors_never_echo_and_bypass_legacy(
    tmp_path, monkeypatch, capsys, extra
):
    def poison(*args, **kwargs):
        pytest.fail("legacy path executed")

    monkeypatch.setattr(cli, "_main", poison)
    monkeypatch.setattr(cli, "exit_on_termination_signals", poison)
    path = tmp_path / "absent" / "run.sqlite"
    code = cli.main(
        [
            "durable",
            "start",
            "--store",
            str(path),
            "--workspace",
            str(tmp_path),
            "--provider",
            "fake",
            "--model",
            "fake-native-bootstrap",
            "--prompt",
            "input",
            *extra,
        ]
    )
    assert code == 2
    out = capsys.readouterr()
    assert out.out == "" and out.err == "Durable command unavailable: invalid_request\n"
    assert not path.parent.exists()


def test_shared_profile_family_and_passive_terminal_arguments(tmp_path):
    base = ["--store", str(tmp_path / "run.sqlite"), "--workspace", str(tmp_path)]
    family = [
        "--provider",
        "fake",
        "--model",
        "fake-native-bootstrap",
        "--thinking",
        "off",
        "--no-tools",
    ]
    commands = [
        parse_args(["start", *base, *family, "--prompt", "input"]),
        parse_args(["resume", *base, *family]),
        parse_args(
            ["authorize-retry", *base, *family, "--task", "1", "--nonce", "a" * 32]
        ),
    ]
    assert all(c.no_tools and c.thinking == "off" for c in commands)
    assert parse_args(["resume", *base]).provider is None
    with pytest.raises(DurableCLIError):
        parse_args(["resume", *base, "--no-tools"])


def test_signal_domain_preserves_ignored_and_refuses_custom_or_foreign_thread():
    original = {
        s: signal.getsignal(s) for s in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT)
    }
    try:
        signal.signal(signal.SIGTERM, signal.SIG_DFL)
        signal.signal(signal.SIGHUP, signal.SIG_IGN)
        signal.signal(signal.SIGINT, signal.default_int_handler)
        assert signal_domain()[signal.SIGHUP] == signal.SIG_IGN
        signal.signal(signal.SIGTERM, lambda *_: None)
        with pytest.raises(DurableCLIError, match="unsupported"):
            signal_domain()
        outcomes = []

        def off_main():
            try:
                signal_domain()
            except DurableCLIError as error:
                outcomes.append(error.code)

        thread = threading.Thread(target=off_main)
        thread.start()
        thread.join(5)
        assert not thread.is_alive() and outcomes == ["unsupported"]
    finally:
        for sig, handler in original.items():
            signal.signal(sig, handler)


def test_actual_cli_custom_and_off_main_refuse_before_runner_or_catalog(
    tmp_path, monkeypatch, capsys
):
    from pipy_harness import durable_cli

    def poison(*args, **kwargs):
        pytest.fail("Runner/catalog created before signal-domain refusal")

    monkeypatch.setattr(durable_cli.asyncio, "Runner", poison)
    monkeypatch.setattr(durable_cli, "build_profile", poison)
    path = tmp_path / "absent" / "run.sqlite"
    argv = [
        "durable",
        "start",
        "--store",
        str(path),
        "--workspace",
        str(tmp_path),
        "--provider",
        "fake",
        "--model",
        "fake-native-bootstrap",
        "--prompt",
        "input",
    ]
    prior = signal.getsignal(signal.SIGTERM)
    handler = lambda *_: None  # noqa: E731 - identity under test
    try:
        signal.signal(signal.SIGTERM, handler)
        assert cli.main(argv) == 2 and signal.getsignal(signal.SIGTERM) is handler
    finally:
        signal.signal(signal.SIGTERM, prior)
    outcomes = []
    worker = threading.Thread(target=lambda: outcomes.append(cli.main(argv)))
    worker.start()
    worker.join(5)
    assert not worker.is_alive() and outcomes == [2] and not path.parent.exists()
    result = capsys.readouterr()
    assert (
        result.out == ""
        and result.err == "Durable command unavailable: unsupported\n" * 2
    )


@pytest.mark.parametrize(
    "data",
    [b"", b"\xff", b"x" * (256 * 1024 + 1)],
    ids=["empty", "invalid-utf8", "oversized"],
)
def test_actual_stdin_empty_invalid_or_oversized_refuses_before_store(tmp_path, data):
    from cli_process_support import run_cli

    path = tmp_path / "absent" / "run.sqlite"
    result = run_cli(
        tmp_path / "receipts",
        "stdin-refusal",
        [
            "start",
            "--store",
            str(path),
            "--workspace",
            str(tmp_path),
            "--provider",
            "fake",
            "--model",
            "fake-native-bootstrap",
            "--stdin",
        ],
        data=data,
    )
    assert result.returncode == 2 and not result.stdout
    assert result.stderr == b"Durable command unavailable: invalid_request\n"
    assert not path.parent.exists()


def test_actual_entrypoint_fake_start_terminal_no_profile_and_passive_inspect(tmp_path):
    store = tmp_path / "run.sqlite"
    base = [sys.executable, "-m", "pipy_harness.cli", "durable"]
    start = subprocess.run(
        [
            *base,
            "start",
            "--store",
            str(store),
            "--workspace",
            str(tmp_path),
            "--provider",
            "fake",
            "--model",
            "fake-native-bootstrap",
            "--no-tools",
            "--prompt",
            "input",
            "--format",
            "json",
        ],
        capture_output=True,
        text=True,
        env=dict(os.environ),
        timeout=20,
    )
    assert start.returncode == 0, start.stderr
    result = json.loads(start.stdout)
    assert result["inspection"]["outcome"] == "completed"
    assert [m["message"]["role"] for m in result["completed"]["messages"]] == [
        "user",
        "assistant",
    ]
    resumed = subprocess.run(
        [
            *base,
            "resume",
            "--store",
            str(store),
            "--workspace",
            str(tmp_path),
            "--format",
            "json",
        ],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert resumed.returncode == 0, resumed.stderr
    assert (
        json.loads(resumed.stdout)["completed"]["messages"]
        == result["completed"]["messages"]
    )
    inspected = subprocess.run(
        [*base, "inspect", "--store", str(store), "--task", str(result["task_id"])],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert inspected.returncode == 0, inspected.stderr
    assert json.loads(inspected.stdout)["tasks"][0]["outcome"] == "completed"
    assert not list(tmp_path.rglob("*.jsonl"))


def test_passive_inspect_and_terminal_reuse_poison_actual_auth_catalog_and_ports(
    tmp_path, monkeypatch, capsys
):
    import asyncio

    from generation_support import Wire
    from test_product_session import profile

    from pipy_harness import durable_cli
    from pipy_harness.native.auth_store import AuthStore
    from pipy_harness.native.catalog_state import ProviderCatalogState
    from pipy_harness.native.provider_construction import build_provider
    from pipy_harness.sdk import create_durable_product_session

    path = tmp_path / "poison.sqlite"

    async def populate():
        product = await create_durable_product_session(
            path, workspace=tmp_path, profile=profile(tmp_path, Wire()), tools={}
        )
        try:
            await product.submit("input")
            assert (await product.resume()).inspection.outcome == "completed"
        finally:
            await product.close()

    asyncio.run(populate())

    def poison(*args, **kwargs):
        pytest.fail("passive operation invoked execution authority")

    monkeypatch.setattr(AuthStore, "__init__", poison)
    monkeypatch.setattr(ProviderCatalogState, "__post_init__", poison)
    monkeypatch.setattr(durable_cli, "build_profile", poison)
    monkeypatch.setattr(
        "pipy_harness.native.provider_construction.build_provider", poison
    )
    assert callable(build_provider)
    original = {
        s: signal.getsignal(s) for s in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT)
    }
    try:
        signal.signal(signal.SIGTERM, signal.SIG_DFL)
        signal.signal(signal.SIGHUP, signal.SIG_DFL)
        signal.signal(signal.SIGINT, signal.default_int_handler)
        assert cli.main(["durable", "inspect", "--store", str(path)]) == 0
        assert json.loads(capsys.readouterr().out)["tasks"] == []
        assert (
            cli.main(
                [
                    "durable",
                    "resume",
                    "--store",
                    str(path),
                    "--workspace",
                    str(tmp_path),
                    "--format",
                    "json",
                ]
            )
            == 0
        )
        assert (
            json.loads(capsys.readouterr().out)["inspection"]["outcome"] == "completed"
        )
    finally:
        for sig, prior in original.items():
            signal.signal(sig, prior)
