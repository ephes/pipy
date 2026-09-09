"""Hermetic two-turn embedding with pipy's full-content product session API.

This executable example deliberately uses ``FakeNativeProvider`` only for a
deterministic offline demonstration. Production callers construct a real,
tool-capable provider through pipy's provider boundary and inject it into
``create_product_session`` in the same place.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from pipy_harness.native.fake import FakeNativeProvider  # type: ignore[import-untyped]
from pipy_harness.native.resource_loading import (  # type: ignore[import-untyped]
    RuntimeResourceOptions,
)
from pipy_harness.native.settings import SettingsManager  # type: ignore[import-untyped]
from pipy_harness.sdk import (  # type: ignore[import-untyped]
    CodingSessionResultSnapshot,
    create_product_session,
)

FIRST_SUBMISSION = "Summarize this isolated workspace without modifying it."
SECOND_SUBMISSION = "Retain the earlier request and state the next step."


@dataclass(frozen=True, slots=True)
class EmbeddingExampleResult:
    """The detached idle snapshots observed during the one product lifetime."""

    first_snapshot: CodingSessionResultSnapshot
    second_snapshot: CodingSessionResultSnapshot


def run_example(workspace: Path) -> EmbeddingExampleResult:
    """Submit two literal prompts within one construction-thread lifetime."""

    provider = FakeNativeProvider(
        model_id="example-tool-capable-fake",
        final_text="deterministic example response",
        supports_tool_calls=True,
    )
    resources = RuntimeResourceOptions(
        no_extensions=True,
        no_skills=True,
        no_prompt_templates=True,
        no_themes=True,
    )
    with TemporaryDirectory(prefix="pipy-product-session-settings-") as config_dir:
        settings = SettingsManager(
            global_path=Path(config_dir) / "settings.json",
            env={},
            project_trusted=False,
        )
        # Native system-prompt discovery is process-environment based, separate
        # from SettingsManager. Keep it on the same private root for the whole
        # lifetime, then restore the caller's environment on exit.
        with patch.dict(os.environ, {"PIPY_CONFIG_HOME": config_dir}):
            with create_product_session(
                workspace=workspace,
                provider=provider,
                settings=settings,
                resources=resources,
                load_context_files=False,
            ) as session:
                first_snapshot = session.submit(FIRST_SUBMISSION)
                second_snapshot = session.submit(SECOND_SUBMISSION)

    return EmbeddingExampleResult(first_snapshot, second_snapshot)


def main() -> None:
    """Run in a temporary workspace so the repository is never modified."""

    with TemporaryDirectory(prefix="pipy-product-session-example-") as temporary_dir:
        result = run_example(Path(temporary_dir))
    print(f"retained user turns: {result.second_snapshot.user_turn_count}")


if __name__ == "__main__":
    main()
