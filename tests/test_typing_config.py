"""Characterization for the complete strict-equivalent source frontier."""

from __future__ import annotations

import tomllib
from pathlib import Path


_STRICT_OVERRIDE_MODULES = (
    "pipy_harness.*",
    "pipy_session.*",
)

_PYTHON_SOURCE_SUFFIXES = frozenset({".py", ".pyi"})

_STRICT_OVERRIDE_FLAGS = {
    "check_untyped_defs",
    "disallow_any_generics",
    "disallow_incomplete_defs",
    "disallow_subclassing_any",
    "disallow_untyped_calls",
    "disallow_untyped_decorators",
    "disallow_untyped_defs",
    "extra_checks",
    "no_implicit_reexport",
    "strict_equality",
    "warn_return_any",
    "warn_unused_ignores",
}


def test_strict_frontier_has_exact_source_package_patterns() -> None:
    repo_root = Path(__file__).parents[1]
    config_path = repo_root / "pyproject.toml"
    config = tomllib.loads(config_path.read_text(encoding="utf-8"))
    mypy_config = config["tool"]["mypy"]
    overrides = mypy_config["overrides"]
    source_root = repo_root / "src"
    python_source_entries = tuple(
        sorted(
            path.name
            for path in source_root.iterdir()
            if (
                (path.is_file() and path.suffix in _PYTHON_SOURCE_SUFFIXES)
                or (
                    path.is_dir()
                    and path.name != "__pycache__"
                    and any(
                        candidate.is_file()
                        and candidate.suffix in _PYTHON_SOURCE_SUFFIXES
                        for candidate in path.rglob("*")
                    )
                )
            )
        )
    )
    source_package_patterns = tuple(
        sorted(
            path.stem if path.is_file() else f"{path.name}.*"
            for path in source_root.iterdir()
            if (
                (path.is_file() and path.suffix in _PYTHON_SOURCE_SUFFIXES)
                or (
                    path.is_dir()
                    and any(
                        (path / init_name).is_file()
                        for init_name in ("__init__.py", "__init__.pyi")
                    )
                )
            )
        )
    )

    assert len(overrides) == 1
    strict_override = overrides[0]
    assert python_source_entries == ("pipy_harness", "pipy_session"), (
        "top-level Python-bearing src entries changed; classify each new entry "
        "and extend the strict frontier only when it is importable"
    )
    assert source_package_patterns == _STRICT_OVERRIDE_MODULES, (
        "top-level source modules or packages changed; update the complete "
        "strict frontier intentionally"
    )
    assert tuple(strict_override["module"]) == source_package_patterns, (
        "Mypy override must cover every top-level source module and package"
    )
    assert set(strict_override) == {"module", *_STRICT_OVERRIDE_FLAGS}
    assert all(strict_override[name] is True for name in _STRICT_OVERRIDE_FLAGS)
    assert set(mypy_config) == {
        "warn_unused_configs",
        "warn_redundant_casts",
        "strict_bytes",
        "overrides",
    }
    assert mypy_config["warn_unused_configs"] is True
    assert mypy_config["warn_redundant_casts"] is True
    assert mypy_config["strict_bytes"] is True
    assert "strict" not in mypy_config
    assert "exclude" not in mypy_config
