"""Startup history blocks for the native terminal shell."""

from __future__ import annotations

from pathlib import Path

from pipy_harness.native.chrome import (
    discover_loaded_resource_names,
    pipy_version_label,
)
from pipy_harness.native.ui.components.transcript import (
    HistoryBlock,
    HistoryBlockTuple,
)


def startup_history_blocks(
    cwd: Path, include_workspace_defaults: bool
) -> list[HistoryBlock]:
    """Build the initial terminal history and loaded-resource sections."""

    raw_blocks: list[tuple[str, tuple[str, ...]]] = [
        ("normal", ("",)),
        ("title", (f" pipy v{pipy_version_label()}",)),
        (
            "controls",
            (
                " escape interrupt · ctrl+c/ctrl+d clear/exit · ↑↓ history · "
                "/ commands · @ files · ! bash · tab paths",
                " shift+tab thinking · ctrl+p model · ctrl+o tool output · "
                "ctrl+t thinking fold · ctrl+v paste image · drop files to attach",
            ),
        ),
        (
            "dim",
            (" Type /hotkeys for the full key reference and loaded resources.",),
        ),
        ("normal", ("",)),
        (
            "dim",
            (
                " Pipy can explain its own features and look up its docs. "
                "Ask it how to use or extend pipy.",
            ),
        ),
        ("normal", ("", "")),
    ]
    blocks: list[HistoryBlock] = [
        HistoryBlockTuple(kind, lines) for kind, lines in raw_blocks
    ]
    context = discover_loaded_resource_names(
        cwd,
        "context",
        include_workspace_defaults=include_workspace_defaults,
    )
    if context:
        blocks.append(
            HistoryBlockTuple(
                "section",
                ("[Context]",),
                None,
            )
        )
        blocks.append(
            HistoryBlockTuple(
                "resource",
                (
                    f"  {', '.join(context)}",
                    "",
                ),
                None,
            )
        )
    skills = discover_loaded_resource_names(
        cwd,
        "skills",
        include_workspace_defaults=include_workspace_defaults,
    )
    if skills:
        blocks.append(
            HistoryBlockTuple(
                "section",
                ("[Skills]",),
                None,
            )
        )
        blocks.append(
            HistoryBlockTuple(
                "resource",
                (
                    f"  {', '.join(skills)}",
                    "",
                    "",
                ),
                None,
            )
        )
    return blocks
