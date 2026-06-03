"""Version surface and the opt-in, default-off update-check gate.

pipy is a local ``uv``-driven project, not a published auto-updating binary, so
unlike Pi (default-on install telemetry) pipy performs **no network ping by
default**. The update check is opt-in via the ``enableInstallTelemetry`` setting
(pipy default ``False``); ``PIPY_TELEMETRY`` overrides the setting, and
``PIPY_OFFLINE`` / ``PIPY_SKIP_VERSION_CHECK`` force it off. No secrets or
identifiers are ever sent — only a version string, and only when opted in.

This module deliberately contains no network code: the gate decides whether a
check *would* run; today there is nothing to send, so a default run is silent.
"""

from __future__ import annotations

from collections.abc import Mapping

from pipy_harness.native.chrome import pipy_version_label

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
