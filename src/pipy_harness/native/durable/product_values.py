"""Closed values for the opt-in async durable product facade."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from pipy_harness.native.agent.usage import AgentTokenPricing
from pipy_harness.native.auth_store import ProviderAuthRequestConfig
from pipy_harness.native.catalog import NativeModelSpec
from pipy_harness.native.http import JsonHTTPClient
from pipy_harness.native.provider_construction import ConstructionOptions

from .completed_values import CompletedSnapshot
from .generation import _capture_generation, _Generation, _generation_definition
from .inspection_values import TaskInspection
from .provider_checkpoint import FAMILIES
from .provider_inputs import own_construction_inputs
from .provider_settings import (
    decode_options,
    decode_pricing,
    encode_options,
    encode_pricing,
)
from .records import RecordError, safe_integer
from .recovery_catalog import RecoveryCatalog
from .tasks import Task


class DurableProductError(ValueError):
    """Payload-free product refusal, independent of private transport failures."""

    def __init__(self, code: str = "unavailable") -> None:
        allowed = {
            "unavailable",
            "closed",
            "wrong_loop",
            "reentrant",
            "incompatible",
            "uninitialized_product",
            "already_admitted",
            "no_input",
            "profile_required",
            "selection_required",
            "foreign_live_task",
            "successor_exists",
            "invalid_request",
            "busy",
            "stale",
            "conflict",
            "not_supported",
            "receipt_held_needs_repair",
            "retained_control",
        }
        self.code = code if type(code) is str and code in allowed else "unavailable"
        super().__init__("Durable product unavailable: " + self.code)


class UnsupportedDurableCapability(DurableProductError):
    def __init__(self) -> None:
        super().__init__("incompatible")


def product_task_id(value: object) -> int:
    try:
        return safe_integer(value)
    except RecordError:
        raise DurableProductError("invalid_request") from None


@dataclass(frozen=True, slots=True)
class DurableAdmission:
    conversation_id: int
    entry_id: int
    generation_task_id: int


@dataclass(frozen=True, slots=True)
class DurableAdjudication:
    """A committed decision acknowledgement, not a completion result."""

    task_id: int
    decision: str

    def __post_init__(self) -> None:
        product_task_id(self.task_id)
        if type(self.decision) is not str or self.decision not in (
            "interrupted",
            "receipt",
        ):
            raise DurableProductError("incompatible")

    def to_tree(self) -> dict[str, int | str]:
        return {"task_id": self.task_id, "decision": self.decision}


@dataclass(frozen=True, slots=True)
class DurableRunSelection:
    task_id: int
    resolution_nonce: str

    def __post_init__(self) -> None:
        import re

        product_task_id(self.task_id)
        if (
            type(self.resolution_nonce) is not str
            or re.fullmatch("[0-9a-f]{32}", self.resolution_nonce) is None
        ):
            raise DurableProductError("invalid_request")


@dataclass(frozen=True, slots=True, repr=False)
class DurableRunResult:
    task_id: int
    status: str
    inspection: TaskInspection
    completed: CompletedSnapshot


@dataclass(frozen=True, slots=True, init=False, repr=False)
class DurableProviderProfile:
    """Explicit owned current native construction authority; never persisted.

    Full private target/default/material binding occurs at first prepare. A
    reopened unprepared run therefore requires explicit current authority.
    """

    _definition: Task[Any, Any, Any] = field(repr=False)
    _driver: _Generation = field(repr=False)
    _selection: tuple[str, str, str] = field(repr=False)

    def __init__(
        self,
        spec: NativeModelSpec,
        *,
        auth_path: Path,
        env: Mapping[str, str],
        runtime_api_key: str | None = None,
        config: ProviderAuthRequestConfig | None = None,
        thinking: str | None = None,
        options: ConstructionOptions | None = None,
        pricing: AgentTokenPricing | None = None,
        http_client: JsonHTTPClient | None = None,
    ) -> None:
        try:
            spec, config, environment = own_construction_inputs(spec, config, env)
            if (
                not spec.provider_name
                or not spec.model_id
                or spec.api not in FAMILIES
                or type(auth_path) is not type(Path())
                or not auth_path.is_absolute()
                or runtime_api_key is not None
                and type(runtime_api_key) is not str
                or thinking is not None
                and type(thinking) is not str
            ):
                raise DurableProductError("incompatible")
            driver = _capture_generation(
                spec,
                auth_path=auth_path,
                env=environment,
                runtime_api_key=runtime_api_key,
                config=config,
                thinking=thinking,
                options=decode_options(
                    encode_options(options or ConstructionOptions())
                ),
                pricing=decode_pricing(encode_pricing(pricing)),
                http_client=http_client,
            )
        except Exception:  # noqa: BLE001 - never expose construction inputs
            raise UnsupportedDurableCapability from None
        object.__setattr__(self, "_driver", driver)
        object.__setattr__(self, "_definition", _generation_definition(driver))
        object.__setattr__(
            self, "_selection", (spec.provider_name, spec.model_id, spec.api)
        )

    def _bound_definition(self, catalog: RecoveryCatalog) -> Task[Any, Any, Any]:
        return _generation_definition(replace(self._driver, catalog=catalog))
