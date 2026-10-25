"""Closed, effect-free argument admission for the opt-in durable command."""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn

from pipy_harness.native.agent.content import ProductContent
from pipy_harness.native.agent.messages import AgentUserMessage
from pipy_harness.native.catalog import THINKING_LEVELS
from pipy_harness.native.durable.product_values import DurableRunSelection


class DurableCLIError(ValueError):
    """Only fixed codes cross the process output boundary."""

    def __init__(self, code: str = "invalid_request", *, exit_code: int = 2):
        self.code = code
        self.exit_code = exit_code
        super().__init__("Durable command unavailable: " + code)


class DurableHelp(Exception):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        # argparse's diagnostic contains arbitrary caller values. Never echo it.
        raise DurableCLIError from None

    def exit(self, status: int = 0, message: str | None = None) -> NoReturn:
        if status == 0:
            raise DurableHelp
        raise DurableCLIError from None


@dataclass(frozen=True, slots=True)
class DurableCommand:
    action: str
    store: Path
    workspace: Path | None
    provider: str | None
    model: str | None
    thinking: str | None
    no_tools: bool
    format: str
    prompt: str | None
    stdin: bool
    task_id: int | None
    conversation_id: int | None
    nonce: str | None


def _profile(parser: argparse.ArgumentParser, *, required: bool) -> None:
    parser.add_argument("--provider", required=required)
    parser.add_argument("--model", required=required)
    parser.add_argument("--thinking", choices=tuple(THINKING_LEVELS))
    parser.add_argument("--no-tools", action="store_true")


def build_parser() -> argparse.ArgumentParser:
    parser = _Parser(prog="pipy durable", allow_abbrev=False)
    subs = parser.add_subparsers(dest="action", required=True)
    for action in ("start", "resume", "inspect", "authorize-retry"):
        sub = subs.add_parser(action, allow_abbrev=False)
        sub.add_argument("--store", required=True)
        sub.add_argument("--format", choices=("text", "json"), default="text")
        if action == "inspect":
            select = sub.add_mutually_exclusive_group()
            select.add_argument("--task")
            select.add_argument("--conversation")
        else:
            sub.add_argument("--workspace", required=True)
            _profile(sub, required=action != "resume")
        if action == "start":
            source = sub.add_mutually_exclusive_group(required=True)
            source.add_argument("--prompt")
            source.add_argument("--stdin", action="store_true")
        if action == "resume":
            sub.add_argument("--task")
            sub.add_argument("--resolution-nonce", dest="nonce")
        if action == "authorize-retry":
            sub.add_argument("--task", required=True)
            sub.add_argument("--nonce", required=True)
    return parser


def _id(value: str | None) -> int | None:
    if value is None:
        return None
    if re.fullmatch("[0-9]{1,16}", value) is None or int(value) > 2**53 - 1:
        raise DurableCLIError
    return int(value)


def input_message(value: str) -> AgentUserMessage:
    if not value:
        raise DurableCLIError
    try:
        return AgentUserMessage(ProductContent(value))
    except (TypeError, ValueError):
        raise DurableCLIError from None


def parse_args(argv: list[str]) -> DurableCommand:
    values = vars(build_parser().parse_args(argv))
    provider, model = values.get("provider"), values.get("model")
    if bool(provider) != bool(model) or (
        provider is None and (values.get("thinking") or values.get("no_tools"))
    ):
        raise DurableCLIError
    task, nonce = _id(values.get("task")), values.get("nonce")
    if values["action"] == "resume" and (task is None) != (nonce is None):
        raise DurableCLIError
    if nonce is not None:
        if task is None:
            raise DurableCLIError
        try:
            DurableRunSelection(task, nonce)
        except ValueError:
            raise DurableCLIError from None
    prompt = values.get("prompt")
    if prompt is not None:
        input_message(prompt)
    store = values["store"]
    workspace = values.get("workspace")
    if (
        not store
        or "\0" in store
        or workspace is not None
        and (not workspace or "\0" in workspace)
    ):
        raise DurableCLIError
    return DurableCommand(
        values["action"],
        Path(store),
        None if workspace is None else Path(workspace),
        provider,
        model,
        values.get("thinking"),
        bool(values.get("no_tools")),
        values["format"],
        prompt,
        bool(values.get("stdin")),
        task,
        _id(values.get("conversation")),
        nonce,
    )
