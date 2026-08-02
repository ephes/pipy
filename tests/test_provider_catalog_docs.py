"""Focused consistency checks for contributor-facing provider-catalog status."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _normalized(path: str) -> str:
    text = (REPO_ROOT / path).read_text(encoding="utf-8")
    return " ".join(text.split())


def test_readme_matches_shipped_provider_catalog_construction_status() -> None:
    readme = _normalized("README.md")
    catalog_spec = _normalized("docs/provider-catalog.md")

    stale_claim = (
        "Catalog-driven construction for the non-completions families "
        "and startup/`pipy run` resolution remain"
    )
    assert stale_claim not in readme

    for shipped_surface in (
        "Chat Completions family",
        "all current non-completions adapter families",
        "startup `--native-provider`/`--native-model`",
        "one-shot `pipy run`",
        "same catalog-backed boundary",
    ):
        assert shipped_surface in readme

    assert "construction wiring for current provider sources" in readme
    assert "not full Pi catalog parity" in readme
    assert "fully wired for current provider sources" in catalog_spec
    assert "only documented adapter follow-ons remain" in catalog_spec
