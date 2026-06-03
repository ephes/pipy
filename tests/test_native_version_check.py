"""Tests for the opt-in, default-off update-check gate (`version_check`).

pipy performs no network ping by default (the one intentional divergence from
Pi's default-on telemetry). `PIPY_TELEMETRY` overrides the
`enableInstallTelemetry` setting; `PIPY_OFFLINE` / `PIPY_SKIP_VERSION_CHECK`
force it off.
"""

from __future__ import annotations

from pipy_harness.native.version_check import (
    resolve_telemetry_enabled,
    update_check_enabled,
)


def test_default_off() -> None:
    assert resolve_telemetry_enabled(setting=False, env={}) is False
    assert update_check_enabled(setting=False, env={}) is False


def test_setting_opt_in() -> None:
    assert resolve_telemetry_enabled(setting=True, env={}) is True
    assert update_check_enabled(setting=True, env={}) is True


def test_env_telemetry_overrides_setting() -> None:
    assert resolve_telemetry_enabled(setting=False, env={"PIPY_TELEMETRY": "1"}) is True
    assert resolve_telemetry_enabled(setting=True, env={"PIPY_TELEMETRY": "0"}) is False
    assert resolve_telemetry_enabled(setting=True, env={"PIPY_TELEMETRY": "no"}) is False
    assert resolve_telemetry_enabled(setting=False, env={"PIPY_TELEMETRY": "yes"}) is True


def test_offline_forces_off_even_when_opted_in() -> None:
    assert update_check_enabled(setting=True, env={"PIPY_OFFLINE": "1"}) is False
    assert update_check_enabled(setting=True, env={"PIPY_SKIP_VERSION_CHECK": "1"}) is False
    assert (
        update_check_enabled(setting=True, env={"PIPY_TELEMETRY": "1", "PIPY_OFFLINE": "1"})
        is False
    )
