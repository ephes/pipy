"""Actual native provider families through the new facade, all synthetic wire."""

import asyncio
import json
from dataclasses import fields

import pytest
from test_provider_checkpoint import Wire

from pipy_harness.native.catalog import NativeModelSpec
from pipy_harness.sdk import (
    DurableProviderProfile,
    create_durable_product_session,
    open_durable_product_session,
)


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    for name in ("HOME", "PIPY_AUTH_DIR", "PIPY_CONFIG_HOME", "XDG_STATE_HOME"):
        monkeypatch.setenv(name, str(tmp_path / name))


FAMILIES = [
    ("openai-completions", "openai", "gpt-test", {}),
    ("openai-responses", "openai", "gpt-test", {}),
    ("azure-openai-responses", "azure-openai", "gpt-test", {}),
    ("anthropic-messages", "anthropic", "claude-test", {}),
    ("google-generative-ai", "google", "gemini-test", {}),
    ("mistral", "mistral", "mistral-test", {}),
    ("cloudflare-workers-ai", "cloudflare", "model-test", {}),
    (
        "amazon-bedrock",
        "amazon-bedrock",
        "anthropic.claude-test",
        {
            "AWS_REGION": "us-east-2",
            "AWS_ACCESS_KEY_ID": "fixture-access",
            "AWS_SECRET_ACCESS_KEY": "fixture-sign",
        },
    ),
    (
        "google-vertex",
        "google-vertex",
        "gemini-test",
        {
            "GOOGLE_CLOUD_PROJECT": "fixture-project",
            "GOOGLE_CLOUD_LOCATION": "us-east1",
            "GOOGLE_ACCESS_TOKEN": "fixture-token",
        },
    ),
    (
        "google-vertex",
        "google-vertex",
        "gemini-test",
        {"GOOGLE_CLOUD_API_KEY": "fixture-express"},
    ),
]


@pytest.mark.parametrize("api,provider,model,env", FAMILIES)
def test_public_supported_family_actual_prepare_wire_settlement_private_dto(
    tmp_path, api, provider, model, env
):
    spec = NativeModelSpec(
        provider,
        model,
        "Test",
        api,
        base_url="https://wire.invalid/private-target",
        headers={"X-Private": "private-header-sentinel"},
    )

    class CompleteWire(Wire):
        def post_json(self, *args, **kwargs):
            from pipy_harness.native.http import JsonResponse

            response = super().post_json(*args, **kwargs)
            return JsonResponse(
                response.status_code, {**response.body, "status": "completed"}
            )

    wire = CompleteWire()
    profile = DurableProviderProfile(
        spec,
        auth_path=tmp_path / "auth.json",
        env=env,
        runtime_api_key=None
        if api in ("amazon-bedrock", "google-vertex")
        else "fixture-key",
        http_client=wire,
    )
    path = tmp_path / "product.sqlite"

    async def scenario():
        async with await create_durable_product_session(
            path, workspace=tmp_path, profile=profile, tools={}
        ) as product:
            admission = await product.submit("input")
            result = await product.resume()
            assert result.inspection.outcome == "completed", result.inspection
            assert result.status == "terminal" and len(wire.calls) == 1
            assert result.completed.messages[-1].decode().content.value == "done"
            assert {f.name for f in fields(result)} == {
                "task_id",
                "status",
                "inspection",
                "completed",
            }
            assert result.task_id == admission.generation_task_id
            public = (
                repr(result) + repr(profile) + json.dumps(result.inspection.to_tree())
            )
            public += json.dumps(
                [m.canonical.thaw() for m in result.completed.messages]
            )
            for sentinel in (
                "private-target",
                "private-header-sentinel",
                "fixture-key",
                *env.values(),
            ):
                assert sentinel not in public
                assert sentinel.encode("utf-8").hex() not in public
        # Metadata/terminal completed output do not need current credentials,
        # material, the provider profile or an executable tool registry.
        for material in tmp_path.glob("auth.json*"):
            material.unlink()
        async with await open_durable_product_session(
            path, workspace=tmp_path, tools={}
        ) as product:
            assert (await product.resume()).inspection.outcome == "completed"
            assert len(wire.calls) == 1

    asyncio.run(asyncio.wait_for(scenario(), 12))


def test_public_codex_actual_selected_credential_authority_and_sse(
    tmp_path, monkeypatch
):
    from test_native_openai_codex_provider import (
        FakeSseHTTPClient,
        completed_events,
        credentials,
    )

    from pipy_harness.native.openai_codex_provider import (
        FileOpenAICodexCredentialStore,
        SseResponse,
        UrllibSseHTTPClient,
    )
    from pipy_harness.native.provider_construction import ConstructionOptions

    auth = tmp_path / "selected" / "auth.json"
    auth.parent.mkdir()
    FileOpenAICodexCredentialStore(auth.with_name("openai-codex.json")).save(
        credentials()
    )
    wire = FakeSseHTTPClient(
        SseResponse(
            status_code=200, body="", event_stream=iter(completed_events("done"))
        )
    )
    monkeypatch.setattr(
        UrllibSseHTTPClient, "post_sse", lambda self, *a, **kw: wire.post_sse(*a, **kw)
    )
    profile = DurableProviderProfile(
        NativeModelSpec("openai-codex", "gpt-test", "Test", "openai-codex-responses"),
        auth_path=auth,
        env={},
        options=ConstructionOptions(openai_codex_transport="sse"),
    )

    async def scenario():
        async with await create_durable_product_session(
            tmp_path / "product.sqlite", workspace=tmp_path, profile=profile, tools={}
        ) as product:
            await product.submit("input")
            result = await product.resume()
            assert result.inspection.outcome == "completed"
            assert result.completed.messages[-1].decode().content.value == "done"
            assert len(wire.requests) == 1
            assert (
                wire.requests[0]["headers"]["Authorization"]
                == "Bearer " + credentials().access_token
            )

    asyncio.run(asyncio.wait_for(scenario(), 12))


def test_public_native_fake_family_is_supported_without_transport_or_auth(tmp_path):
    profile = DurableProviderProfile(
        NativeModelSpec("fake", "fake", "Test", "fake"),
        auth_path=tmp_path / "auth.json",
        env={},
    )

    async def scenario():
        async with await create_durable_product_session(
            tmp_path / "product.sqlite", workspace=tmp_path, profile=profile, tools={}
        ) as product:
            await product.submit("input")
            result = await product.resume()
            assert result.inspection.outcome == "completed"
            assert len(result.completed.messages) == 2

    asyncio.run(asyncio.wait_for(scenario(), 10))


@pytest.mark.parametrize("prepared", [False, True])
def test_explicit_current_profile_before_first_prepare_target_drift_after_prepare_refuses(
    tmp_path, monkeypatch, prepared
):
    from generation_support import Wire as GenerationWire

    from pipy_harness.native.durable import sqlite
    from pipy_harness.sdk import DurableProductError

    path = tmp_path / "product.sqlite"
    old_wire, new_wire = GenerationWire(), GenerationWire()

    def authority(target, wire):
        return DurableProviderProfile(
            NativeModelSpec(
                "openai",
                "gpt-test",
                "Test",
                "openai-completions",
                base_url="https://wire.invalid/" + target,
            ),
            auth_path=tmp_path / "auth.json",
            env={},
            runtime_api_key="fixture-key",
            http_client=wire,
        )

    async def scenario():
        product = await create_durable_product_session(
            path,
            workspace=tmp_path,
            profile=authority("original-target", old_wire),
            tools={},
        )
        await product.submit("input")
        if prepared:

            def lose_request_ack(connection):
                rows = [
                    sqlite._decode(r[0])
                    for r in connection.execute("SELECT record FROM tasks")
                ]
                if any(
                    r["state"]["status"] == "running"
                    and r["state"]["checkpoint"]["phase"] == "request"
                    for r in rows
                ):
                    raise RuntimeError("synthetic admitted request ACK loss")

            with monkeypatch.context() as patch:
                patch.setattr(sqlite, "_after_commit", lose_request_ack)
                with pytest.raises(DurableProductError):
                    await product.resume()
                await product.close()
        else:
            await product.close()
        assert old_wire.calls == []
        # This is explicit current-profile authorization only while unprepared;
        # it is not advertised as preserving an uncommitted private target.
        async with await open_durable_product_session(
            path,
            workspace=tmp_path,
            profile=authority("current-target", new_wire),
            tools={},
        ) as product:
            result = await product.resume()
            assert result.inspection.outcome == ("faulted" if prepared else "completed")
            assert len(new_wire.calls) == (0 if prepared else 1)
            if not prepared:
                assert "current-target" in new_wire.calls[0][0]

    asyncio.run(asyncio.wait_for(scenario(), 12))


def test_prepared_recovery_keeps_saved_options_and_actual_price_once(
    tmp_path, monkeypatch
):
    from generation_support import Wire as GenerationWire

    from pipy_harness.native.agent.usage import AgentTokenPricing
    from pipy_harness.native.durable import sqlite
    from pipy_harness.native.durable.inspection_read import entries
    from pipy_harness.native.provider_construction import ConstructionOptions
    from pipy_harness.sdk import DurableProductError

    path = tmp_path / "product.sqlite"
    wire = GenerationWire()
    spec = NativeModelSpec("openai", "gpt-test", "Test", "openai-completions")

    def authority(price, timeout):
        return DurableProviderProfile(
            spec,
            auth_path=tmp_path / "auth.json",
            env={},
            runtime_api_key="fixture-key",
            http_client=wire,
            pricing=AgentTokenPricing(price, price * 2, price * 3, price * 4),
            options=ConstructionOptions(openai_codex_timeout_seconds=timeout),
        )

    async def scenario():
        product = await create_durable_product_session(
            path, workspace=tmp_path, profile=authority(1, 7), tools={}
        )
        await product.submit("input")

        def cut(connection):
            rows = [
                sqlite._decode(r[0])
                for r in connection.execute("SELECT record FROM tasks")
            ]
            if any(
                r["state"]["status"] == "running"
                and r["state"]["checkpoint"]["phase"] == "request"
                for r in rows
            ):
                raise RuntimeError("synthetic request ACK cut")

        with monkeypatch.context() as patch:
            patch.setattr(sqlite, "_after_commit", cut)
            with pytest.raises(DurableProductError):
                await product.resume()
            await product.close()
        async with await open_durable_product_session(
            path, workspace=tmp_path, profile=authority(99, 999), tools={}
        ) as product:
            result = await product.resume()
            assistant = result.completed.messages[-1].decode()
            assert assistant.usage.cost.total == pytest.approx(48 / 1e6)
            assert (
                result.completed.usage.thaw()["models"]["openai/gpt-test"]["cost"][
                    "total"
                ]
                == assistant.usage.cost.total
            )
            assert len(result.completed.usage.thaw()["settlements"]) == 1
            value = await product._read(product._header)
            raw = await product._read(
                lambda: entries(product._session._owner.storage, value["conversation"])
            )
            saved = raw[result.completed.messages[-1].entry_id].to_tree()["data"][
                "receipt"
            ]["settings"]
            assert saved["options"]["timeout_seconds"] == 7
            assert saved["pricing"]["input_per_million"] == 1
            assert len(wire.calls) == 1
            await product.resume()
            assert len(wire.calls) == 1

    asyncio.run(asyncio.wait_for(scenario(), 12))


@pytest.mark.parametrize(
    "api,provider,wrong_api",
    [
        ("mistral", "mistral", "openai-completions"),
        ("cloudflare-workers-ai", "cloudflare", "mistral-conversations"),
        ("openai-completions", "openai", "mistral-conversations"),
    ],
)
def test_actual_provider_outcome_cross_family_replay_api_refuses_before_receipt(
    tmp_path, monkeypatch, api, provider, wrong_api
):
    from dataclasses import replace

    from generation_support import Wire as GenerationWire

    from pipy_harness.native.durable import generation

    original = generation.execute_provider
    wire = GenerationWire()

    async def wrong(*args):
        outcome, blocks = await original(*args)
        assert outcome.result is not None
        return replace(outcome, result=replace(outcome.result, api=wrong_api)), blocks

    monkeypatch.setattr(generation, "execute_provider", wrong)
    authority = DurableProviderProfile(
        NativeModelSpec(provider, "model-test", "Test", api),
        auth_path=tmp_path / "auth.json",
        env={},
        runtime_api_key="fixture-key",
        http_client=wire,
    )

    async def scenario():
        async with await create_durable_product_session(
            tmp_path / "product.sqlite", workspace=tmp_path, profile=authority, tools={}
        ) as product:
            await product.submit("input")
            result = await product.resume()
            assert result.inspection.outcome == "faulted"
            assert len(result.completed.messages) == 1
            assert len(wire.calls) == 1
            assert result.completed.usage is None

    asyncio.run(asyncio.wait_for(scenario(), 10))
