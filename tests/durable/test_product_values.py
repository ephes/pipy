"""Canonical product values survive admission, execution and exact reopening."""

import asyncio
import math
from dataclasses import replace

import pytest
from test_canonical_codec import SNAPSHOT
from test_product_session import profile
from test_tool_boundaries import MultiRound, RichTool

from pipy_harness.native.agent.content import (
    NO_STRUCTURED_CONTENT,
    ImageContent,
    TextContent,
)
from pipy_harness.native.agent.request import snapshot_provider_request
from pipy_harness.native.durable.canonical_codec import encode_canonical
from pipy_harness.native.durable.codec import OwnedJson
from pipy_harness.native.durable.tool_execution import ToolRegistration
from pipy_harness.native.durable.tool_values import read_native_tool_output
from pipy_harness.sdk import (
    create_durable_product_session,
    open_durable_product_session,
)


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    for name in ("HOME", "PIPY_AUTH_DIR", "PIPY_CONFIG_HOME", "XDG_STATE_HOME"):
        monkeypatch.setenv(name, str(tmp_path / name))


@pytest.mark.parametrize(
    "structured",
    [
        NO_STRUCTURED_CONTENT,
        None,
        {"number": [0, 0.0, -0.0], "nested": [None, False, []]},
    ],
)
def test_public_rich_template_owned_tool_output_usage_and_detached_reopen(
    tmp_path, structured
):
    port = RichTool(structured)
    registry = {"rich": ToolRegistration(port, "fixture.rich", "1")}
    wire = MultiRound((("rich",), ()))
    template = snapshot_provider_request(
        replace(
            SNAPSHOT.request,
            provider_name="openai",
            model_id="gpt-test",
            cwd=tmp_path,
            session_id=None,
            user_prompt="",
            messages=(),
            available_tools=(port.definition,),
        )
    )
    path = tmp_path / "product.sqlite"

    async def scenario():
        async with await create_durable_product_session(
            path,
            workspace=tmp_path,
            profile=profile(tmp_path, wire),
            tools=registry,
            request_template=template,
        ) as product:
            await product.submit("input\nUnicode Ä")
            value = await product._read(product._header)
            assert OwnedJson(value["template"]) == OwnedJson(encode_canonical(template))
            result = await product.resume()
            assert result.inspection.outcome == "completed", result.inspection
            tool = next(
                m for m in result.completed.messages if m.tool_output is not None
            )
            decoded = tool.decode()
            raw, outcome = read_native_tool_output(tool.tool_output.thaw())
            assert (
                decoded.blocks
                == outcome.result.blocks
                == (
                    TextContent("before"),
                    ImageContent("aW1hZ2U=", "image/png"),
                    TextContent("after"),
                )
            )
            assert decoded.details == {
                "nested": {"null": None, "empty": [], "false": False}
            }
            assert decoded.is_error and outcome.execution_seconds >= 0
            assert decoded.provider_correlation_id == "call:0"
            assert decoded.tool_request_id == port.calls[0].tool_request_id
            if structured is NO_STRUCTURED_CONTENT:
                assert decoded.structured_content is NO_STRUCTURED_CONTENT
            else:
                assert decoded.structured_content == structured
                if structured is not None:
                    numbers = decoded.structured_content["number"]
                    assert [type(n) for n in numbers] == [int, float, float]
                    assert math.copysign(1, numbers[2]) == -1
            assistant = result.completed.messages[1].decode()
            assert assistant.tool_calls[0].arguments_json.value == ' { "x":  1 } '
            assert assistant.usage.reasoning == 3
            assert len(result.completed.usage.thaw()["settlements"]) == 2
            canonical = tool.canonical
            decoded.details["nested"]["empty"].append("consumer only")
            assert tool.decode().details["nested"]["empty"] == []
            assert tool.canonical is canonical
            expected = tuple(
                (
                    m.entry_id,
                    m.commit_seq,
                    m.canonical,
                    m.provider_outcome,
                    m.tool_output,
                )
                for m in result.completed.messages
            )
        async with await open_durable_product_session(
            path, workspace=tmp_path, tools={}
        ) as product:
            reopened = await product.resume()
            assert (
                tuple(
                    (
                        m.entry_id,
                        m.commit_seq,
                        m.canonical,
                        m.provider_outcome,
                        m.tool_output,
                    )
                    for m in reopened.completed.messages
                )
                == expected
            )
            assert len(wire.calls) == 2
            assert not list(tmp_path.rglob("*.jsonl"))

    asyncio.run(asyncio.wait_for(scenario(), 15))
