"""Opt-in headless composition of the existing native durable SDK authority."""

from __future__ import annotations

import asyncio
import os
import sys
from typing import Any

from pipy_harness.native.agent.messages import AgentUserMessage
from pipy_harness.native.durable.generation_transport import owned_call
from pipy_harness.native.durable.inspection import inspect_store, inspect_store_task
from pipy_harness.native.durable.inspection_values import InspectionError
from pipy_harness.native.durable.tool_execution import ToolRegistration
from pipy_harness.sdk import (
    DurableProductError,
    DurableProductSession,
    DurableProviderProfile,
    DurableRunSelection,
    create_durable_product_session,
    open_durable_product_session,
)

from .native.automation.durable_args import (
    DurableCLIError,
    DurableCommand,
    DurableHelp,
    input_message,
    parse_args,
)
from .native.automation.durable_lifetime import (
    CommandInterrupted,
    CommandOwner,
    SignalOwner,
    read_input,
    signal_domain,
    stdin_fd,
    write_output,
)
from .native.automation.durable_output import final_text, json_output, run_output


def build_profile(command: DurableCommand) -> DurableProviderProfile:
    # Called only on the CLI owner thread for explicitly profiled execution.
    # Passive inspection/terminal reopening never constructs this catalog.
    from pipy_harness.native.catalog_state import ProviderCatalogState
    from pipy_harness.native.provider_construction import ConstructionOptions
    from pipy_harness.native.repl.turn_leaves import pricing_from_cost

    environment = dict(os.environ)
    catalog = ProviderCatalogState(env=environment)
    spec = catalog.find(command.provider or "", command.model or "")
    if catalog.error is not None or spec is None:
        raise DurableCLIError("incompatible", exit_code=1)
    assert catalog.auth_store is not None
    return DurableProviderProfile(
        spec,
        auth_path=catalog.auth_store.path.absolute(),
        env=environment,
        runtime_api_key=None,
        config=catalog._models_json_auth(spec.provider_name),
        thinking=command.thinking,
        options=ConstructionOptions(),
        pricing=pricing_from_cost(spec.cost),
    )


def inspect_metadata(command: DurableCommand) -> dict[str, Any]:
    path = str(command.store.absolute())
    if command.task_id is not None:
        value = inspect_store_task(
            path, command.task_id, restart_limit=command.restart_limit
        )
        return {"tasks": [] if value is None else [value.to_tree()]}
    cursor = None
    tasks: list[dict[str, Any]] = []
    while True:
        page = inspect_store(
            path,
            conversation_id=command.conversation_id,
            cursor=cursor,
            restart_limit=command.restart_limit,
        )
        tasks.extend(t.to_tree() for t in page.tasks)
        cursor = page.next
        if cursor is None:
            return {"tasks": tasks}


async def _execute(command: DurableCommand, owner: CommandOwner, fd: int | None) -> int:
    owner.check()
    if command.action == "inspect":
        value = await owner.stage(
            owner.accept(owned_call(lambda: inspect_metadata(command)))
        )
        envelope = {
            "format": "pipy-durable-output",
            "version": 1,
            "operation": "inspect",
            **value,
        }
        await owner.stage(
            owner.accept(
                write_output(
                    json_output(envelope),
                    owner,
                )
            )
        )
        return 0
    assert command.workspace is not None
    content = command.prompt
    if fd is not None:
        content = await read_input(fd, owner, AgentUserMessage.CONTENT_MAX_LENGTH * 4)
        input_message(content)
    owner.check()
    profile = None if command.provider is None else build_profile(command)
    owner.check()
    tools: dict[str, ToolRegistration] | None = (
        {} if command.no_tools or profile is None else None
    )
    if command.action == "start":
        assert profile is not None and content is not None
        offered = create_durable_product_session(
            command.store,
            workspace=command.workspace,
            profile=profile,
            tools=tools,
            restart_limit=command.restart_limit,
        )
    else:
        offered = open_durable_product_session(
            command.store,
            workspace=command.workspace,
            profile=profile,
            tools=tools,
            restart_limit=command.restart_limit,
        )
    owner.factory = owner.accept(offered)
    owner.product = await owner.stage(owner.factory)
    product = owner.product
    if command.action == "start":
        assert content is not None
        await owner.stage(owner.accept(product.submit(content)))
    if command.action in ("authorize-retry", "adjudicate-interrupted", "retry-task"):
        output = await _operator(command, owner, product)
        code = 0
    else:
        if command.nonce is not None:
            assert command.task_id is not None
            selection = DurableRunSelection(command.task_id, command.nonce)
        else:
            selection = None
        result = await owner.stage(owner.accept(product.resume(selection)))
        output = run_output(result)
        code = (
            0
            if result.status == "terminal" and result.inspection.outcome == "completed"
            else (3 if result.status == "held" else 1)
        )
    text = json_output(output)
    if command.format == "text" and command.action not in (
        "authorize-retry",
        "adjudicate-interrupted",
        "retry-task",
    ):
        text = final_text(result) + "\n" if code == 0 else ""
    if text:
        await owner.stage(owner.accept(write_output(text, owner)))
    if code:
        sys.stderr.write(
            "Durable command unavailable: " + ("held" if code == 3 else "failed") + "\n"
        )
    return code


async def _operator(
    command: DurableCommand, owner: CommandOwner, product: DurableProductSession
) -> dict[str, Any]:
    if command.action == "retry-task":
        assert command.target_task_id is not None
        selection = (
            DurableRunSelection(command.task_id, command.nonce)
            if command.task_id is not None and command.nonce is not None
            else None
        )
        reset = await owner.stage(
            owner.accept(
                product.retry_task(command.target_task_id, selection=selection)
            )
        )
        return {
            "format": "pipy-durable-output",
            "version": 1,
            "operation": command.action,
            **reset.to_tree(),
        }
    assert command.task_id is not None and command.nonce is not None
    payload: dict[str, Any]
    if command.action == "adjudicate-interrupted":
        decision = await owner.stage(
            owner.accept(
                product.adjudicate_interrupted(command.task_id, nonce=command.nonce)
            )
        )
        payload = decision.to_tree()
    else:
        authorization = await owner.stage(
            owner.accept(product.authorize_retry(command.task_id, nonce=command.nonce))
        )
        payload = authorization.to_tree()
    return {
        "format": "pipy-durable-output",
        "version": 1,
        "operation": command.action,
        **payload,
    }


async def _run(command: DurableCommand, owner: CommandOwner, fd: int | None) -> int:
    try:
        return await _execute(command, owner, fd)
    finally:
        cleanup = asyncio.create_task(owner.cleanup())
        while not cleanup.done():
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                continue
        cleanup.result()


def _drive(
    runner: asyncio.Runner, command: DurableCommand, owner: CommandOwner, fd: int | None
) -> int:
    try:
        return runner.run(_run(command, owner, fd))
    except CommandInterrupted:
        return owner.signals.exit_code or 1
    except BrokenPipeError:
        return 141
    except asyncio.CancelledError:
        sys.stderr.write("Durable command unavailable: cancelled\n")
    except DurableCLIError as error:
        sys.stderr.write(str(error) + "\n")
        return error.exit_code
    except (DurableProductError, InspectionError) as error:
        sys.stderr.write("Durable command unavailable: " + error.code + "\n")
    except Exception:  # noqa: BLE001 - raw private provider/backend failures stay private
        sys.stderr.write("Durable command unavailable: unavailable\n")
    return 1


def durable_main(argv: list[str]) -> int:
    try:
        command = parse_args(argv)
        prior = signal_domain()
        fd = stdin_fd() if command.stdin else None
    except DurableHelp:
        return 0
    except DurableCLIError as error:
        sys.stderr.write(str(error) + "\n")
        return error.exit_code
    signals = SignalOwner(prior)
    runner = asyncio.Runner()
    owner = CommandOwner(signals)
    code = 1
    try:
        signals.install()
        signals.loop = runner.get_loop()
        code = _drive(runner, command, owner, fd)
    except Exception:  # noqa: BLE001 - setup diagnostics are fixed and payload-free
        sys.stderr.write("Durable command unavailable: unavailable\n")
        code = 1
    finally:
        try:
            runner.close()
        except Exception:  # noqa: BLE001 - loop cleanup failure can never mean success
            sys.stderr.write("Durable command unavailable: unavailable\n")
            code = 1
        finally:
            signals.restore()
    # A notification during Runner.close still owns the process disposition.
    return signals.exit_code if signals.exit_code is not None else code
