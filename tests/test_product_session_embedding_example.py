"""Execute the checked-in product-session embedding example itself."""

from __future__ import annotations

import importlib.util
import os
import sys
from collections.abc import Callable
from dataclasses import FrozenInstanceError
from pathlib import Path
from types import ModuleType
from typing import Protocol, cast

import pytest

from pipy_harness.sdk import CodingSessionResultSnapshot  # type: ignore[import-untyped]


class _ExampleResult(Protocol):
    first_snapshot: CodingSessionResultSnapshot
    second_snapshot: CodingSessionResultSnapshot


def _load_example() -> ModuleType:
    example_path = (
        Path(__file__).resolve().parents[1]
        / "docs"
        / "examples"
        / "product_session_embedding.py"
    )
    spec = importlib.util.spec_from_file_location(
        "product_session_embedding_example", example_path
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_product_session_embedding_example_is_hermetic_and_retains_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    isolated_home = tmp_path / "home"
    isolated_config = tmp_path / "config"
    archive = tmp_path / "workflow-archive"
    isolated_home.mkdir()
    isolated_config.mkdir()
    (isolated_config / "SYSTEM.md").write_text("MUST NOT ENTER EXAMPLE PROMPT")
    monkeypatch.setenv("HOME", str(isolated_home))
    monkeypatch.setenv("PIPY_CONFIG_HOME", str(isolated_config))
    monkeypatch.setenv("PIPY_SESSION_DIR", str(archive))

    module = _load_example()
    run_example = cast(Callable[[Path], _ExampleResult], getattr(module, "run_example"))

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    result = run_example(workspace)
    first = result.first_snapshot
    second = result.second_snapshot

    assert first.user_turn_count == 1
    assert second.user_turn_count == 2
    assert first.messages is not second.messages
    assert first.messages == second.messages[: len(first.messages)]
    with pytest.raises(FrozenInstanceError):
        first.user_turn_count = 0  # type: ignore[misc]
    assert isinstance(first.messages, tuple)
    assert Path(os.environ["PIPY_CONFIG_HOME"]) == isolated_config
    assert not archive.exists()
