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

    # The README points readers to the maintained contract rather than duplicating
    # an adapter-by-adapter implementation status that can drift independently.
    assert "[provider catalog](docs/provider-catalog.md)" in readme
    assert "--native-provider" in readme
    assert "--native-model" in readme
    assert "fully wired for current provider sources" in catalog_spec
    assert "only documented adapter follow-ons remain" in catalog_spec
