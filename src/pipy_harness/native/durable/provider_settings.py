"""Closed nonsecret settings for a prepared native provider request."""

from __future__ import annotations

from typing import NoReturn

from pipy_harness.native.agent.usage import AgentTokenPricing, AgentTokenPricingTier
from pipy_harness.native.provider_construction import ConstructionOptions
from pipy_harness.native.retry import RetryPolicy

from .codec import OwnedJson


class ProviderCheckpointError(ValueError):
    """Payload-free compatibility failure, safe for scheduler fault records."""


def refuse() -> NoReturn:
    raise ProviderCheckpointError("Prepared provider request is incompatible") from None


RATES = (
    "input_per_million",
    "output_per_million",
    "cache_read_per_million",
    "cache_write_per_million",
)
RETRY = (
    "max_attempts",
    "initial_delay_seconds",
    "max_delay_seconds",
    "multiplier",
    "jitter_seconds",
)


def encode_pricing(value: AgentTokenPricing | None) -> object:
    if value is None:
        return None
    if type(value) is not AgentTokenPricing:
        refuse()
    if type(value.tiers) is not tuple or any(
        type(tier) is not AgentTokenPricingTier for tier in value.tiers
    ):
        refuse()
    return {
        **{key: getattr(value, key) for key in RATES},
        "tiers": [
            {key: getattr(tier, key) for key in ("input_tokens_above", *RATES)}
            for tier in value.tiers
        ],
    }


def decode_pricing(value: object) -> AgentTokenPricing | None:
    if value is None:
        return None
    try:
        tree = OwnedJson(value).thaw()
        if (
            type(tree) is not dict
            or set(tree) != {*RATES, "tiers"}
            or type(tree["tiers"]) is not list
        ):
            refuse()
        tiers = []
        for tier in tree["tiers"]:
            if type(tier) is not dict or set(tier) != {"input_tokens_above", *RATES}:
                refuse()
            tiers.append(AgentTokenPricingTier(**tier))
        return AgentTokenPricing(
            **{key: tree[key] for key in RATES}, tiers=tuple(tiers)
        )
    except (TypeError, ValueError, OverflowError):
        refuse()


def encode_options(value: ConstructionOptions) -> dict[str, object]:
    if type(value) is not ConstructionOptions:
        refuse()
    policy = value.retry_policy
    if policy is not None and type(policy) is not RetryPolicy:
        refuse()
    if policy is not None and (
        type(policy.retriable_statuses) is not frozenset
        or any(type(status) is not int for status in policy.retriable_statuses)
    ):
        refuse()
    return {
        "retry": None
        if policy is None
        else {
            **{key: getattr(policy, key) for key in RETRY},
            "retriable_statuses": sorted(policy.retriable_statuses),
        },
        "timeout_seconds": value.openai_codex_timeout_seconds,
        "transport": value.openai_codex_transport,
        "connect_timeout_seconds": value.openai_codex_websocket_connect_timeout_seconds,
    }


def decode_options(value: object) -> ConstructionOptions:
    try:
        tree = OwnedJson(value).thaw()
        if type(tree) is not dict or set(tree) != {
            "retry",
            "timeout_seconds",
            "transport",
            "connect_timeout_seconds",
        }:
            refuse()
        if type(tree["transport"]) is not str or tree["transport"] not in (
            "auto",
            "sse",
            "websocket",
        ):
            refuse()
        for key in ("timeout_seconds", "connect_timeout_seconds"):
            item = tree[key]
            if item is not None and type(item) not in (int, float):
                refuse()
        retry = tree["retry"]
        policy = None
        if retry is not None:
            if type(retry) is not dict or set(retry) != {*RETRY, "retriable_statuses"}:
                refuse()
            if type(retry["max_attempts"]) is not int or any(
                type(retry[key]) not in (int, float)
                for key in RETRY
                if key != "max_attempts"
            ):
                refuse()
            statuses = retry["retriable_statuses"]
            if (
                type(statuses) is not list
                or any(type(s) is not int for s in statuses)
                or statuses != sorted(set(statuses))
            ):
                refuse()
            policy = RetryPolicy(
                **{key: retry[key] for key in RETRY},
                retriable_statuses=frozenset(statuses),
            )
        return ConstructionOptions(
            retry_policy=policy,
            openai_codex_timeout_seconds=tree["timeout_seconds"],
            openai_codex_transport=tree["transport"],
            openai_codex_websocket_connect_timeout_seconds=tree[
                "connect_timeout_seconds"
            ],
        )
    except (TypeError, ValueError, OverflowError):
        refuse()
