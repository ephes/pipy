"""Version and self-update surface for pipy.

Version checks are stdlib-only and fail open: ``PIPY_OFFLINE`` and
``PIPY_SKIP_VERSION_CHECK`` disable network access, and network errors simply
return no latest-version result. The legacy ``PIPY_TELEMETRY`` gate is kept for
existing settings callers, but the product self-update helpers are explicit
user actions rather than background telemetry.
"""

from __future__ import annotations

from collections.abc import Mapping

from pipy_harness.native.chrome import pipy_version_label
from pipy_harness.native.export_distribution import (
    UpdatePlan,
    compare_versions,
    detect_install_method,
    fetch_latest_pipy_version,
    self_update_plan,
)

PIPY_TELEMETRY_ENV = "PIPY_TELEMETRY"
PIPY_OFFLINE_ENV = "PIPY_OFFLINE"
PIPY_SKIP_VERSION_CHECK_ENV = "PIPY_SKIP_VERSION_CHECK"

_TRUE_VALUES = {"1", "true", "yes"}
_FALSE_VALUES = {"0", "false", "no"}


def pipy_version() -> str:
    """Return the pipy package version string (for ``--version``)."""

    return pipy_version_label()


def resolve_telemetry_enabled(*, setting: bool, env: Mapping[str, str]) -> bool:
    """Resolve install-telemetry enablement: ``PIPY_TELEMETRY`` overrides setting.

    Accepts ``1``/``true``/``yes`` and ``0``/``false``/``no`` (case-insensitive)
    for the env override; any other value falls back to the setting.
    """

    raw = env.get(PIPY_TELEMETRY_ENV)
    if raw is not None:
        lowered = raw.strip().lower()
        if lowered in _TRUE_VALUES:
            return True
        if lowered in _FALSE_VALUES:
            return False
    return setting


def update_check_enabled(*, setting: bool, env: Mapping[str, str]) -> bool:
    """Whether an update check may run; default OFF, and never under offline."""

    if env.get(PIPY_OFFLINE_ENV, "").strip().lower() in _TRUE_VALUES:
        return False
    if env.get(PIPY_SKIP_VERSION_CHECK_ENV, "").strip().lower() in _TRUE_VALUES:
        return False
    return resolve_telemetry_enabled(setting=setting, env=env)


__all__ = [
    "PIPY_OFFLINE_ENV",
    "PIPY_SKIP_VERSION_CHECK_ENV",
    "PIPY_TELEMETRY_ENV",
    "UpdatePlan",
    "compare_versions",
    "detect_install_method",
    "fetch_latest_pipy_version",
    "pipy_version",
    "resolve_telemetry_enabled",
    "self_update_plan",
    "update_check_enabled",
]
