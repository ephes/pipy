#!/usr/bin/env python3
"""Report drift between pipy's built-in model catalog and Pi's generated catalog.

``catalog_data.py`` is pipy's curated catalog. Pi generates its catalog from
models.dev plus its own corrections (``packages/ai/scripts/generate-models.ts``).
This tool reads Pi's generated JSON and compares every row both sides carry,
and it lists the rows only one side has. Intentional differences live in
``scripts/catalog_drift_allowlist.json``; anything else is drift.

Get Pi's data first (the JSON is gitignored build output), either:

    npm run hydrate:model-data                  # in ~/src/pi-mono
    node scripts/generate-models.ts --strict --json-only --json-output DIR
                                                # in ~/src/pi-mono/packages/ai

Run:

    just catalog-drift [--pi-data PATH] [--verbose] [--json]

Exit codes: 0 clean, 1 drift or stale allowlist entries, 2 input error.
This is a manual check. It is not part of ``just check`` or CI.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from pathlib import Path
from typing import Any

from pipy_harness.native.catalog import THINKING_LEVELS, NativeModelSpec
from pipy_harness.native.catalog_data import BUILTIN_MODEL_ROWS

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ALLOWLIST = REPO_ROOT / "scripts" / "catalog_drift_allowlist.json"
HYDRATED_SUBPATH = Path("packages/ai/src/providers/data")
REGENERATE_HINT = (
    "Generate Pi's catalog first: `npm run hydrate:model-data` in pi-mono, or "
    "`node scripts/generate-models.ts --strict --json-only --json-output DIR` in "
    "pi-mono/packages/ai, then pass --pi-data."
)

# pipy provider name -> Pi provider name. Unlisted providers use the same name.
# ``openai-completions`` is a pipy-only provider serving Pi's ``openai`` models
# over Chat Completions.
PI_PROVIDER = {
    "azure-openai": "azure-openai-responses",
    "cloudflare": "cloudflare-workers-ai",
    "openai-completions": "openai",
}
SKIPPED_PROVIDERS = frozenset({"fake"})
# Pipy providers that mirror another pipy provider's Pi table. Their Pi rows
# are listed as not-carried under the primary provider only.
MIRROR_PROVIDERS = frozenset({"openai-completions"})

ORDINARY_LEVELS = ("minimal", "low", "medium", "high")
ABSENT = "<absent>"
# Compat keys pipy's request construction reads. Values compare as written and
# a missing key is ``ABSENT``: Pi's runtime defaults differ per key (for
# example ``supportsLongCacheRetention`` defaults to true), so a missing key is
# not the same as ``false``.
TRACKED_COMPAT = (
    "forceAdaptiveThinking",
    "supportsToolSearch",
    "supportsToolReferences",
    "supportsExplicitPromptCacheMode",
    "supportsLongCacheRetention",
    "supportsCacheControlOnTools",
    "sendSessionAffinityHeaders",
    "sessionAffinityFormat",
    "thinkingFormat",
    "supportsReasoningEffort",
    "openRouterRouting",
    "vercelGatewayRouting",
)
VALUE_FIELDS = (
    "api",
    "reasoning",
    "input",
    "cost",
    "contextWindow",
    "maxTokens",
    "thinkingLevelMap",
    "baseUrl",
    *(f"compat.{key}" for key in TRACKED_COMPAT),
)
ROW_FIELDS = ("pipy-only", "not-carried")
KINDS = ("deviation", "pending")


class InputError(Exception):
    """Pi data or the allowlist could not be read."""


# ---- Pi data ----------------------------------------------------------------


PiModels = dict[str, dict[str, Mapping[str, Any]]]


def load_pi_models(path: Path) -> tuple[PiModels, str | None]:
    """Load Pi's chat models as ``{provider: {id: model}}`` plus a generated-at stamp.

    Accepts a ``--json-output`` ``models.json`` file, a ``--json-output``
    directory, or the hydrated ``src/providers/data`` directory.
    """

    if path.is_dir():
        if (path / "models.json").is_file():
            return _load_models_json(path / "models.json"), None
        manifest = path / ".manifest.json"
        if manifest.is_file():
            return _load_hydrated(path), _generated_at(manifest)
        raise InputError(f"{path} holds neither models.json nor .manifest.json.")
    if path.is_file():
        return _load_models_json(path), None
    raise InputError(f"Pi catalog data not found at {path}.")


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise InputError(f"Cannot read {path}: {exc}") from exc


def _load_models_json(path: Path) -> PiModels:
    data = _read_json(path)
    if not isinstance(data, dict):
        raise InputError(f"{path} must hold a JSON object of providers.")
    out: PiModels = {}
    for provider, models in data.items():
        if not isinstance(models, dict):
            raise InputError(f"{path}: provider {provider!r} must map ids to models.")
        out[provider] = {
            model_id: model
            for model_id, model in models.items()
            if isinstance(model, dict) and model.get("type", "chat") == "chat"
        }
    return out


def _load_hydrated(directory: Path) -> PiModels:
    out: PiModels = {}
    for file in sorted(directory.glob("*.json")):
        if file.name.startswith("."):
            continue
        groups = _read_json(file)
        if not isinstance(groups, dict):
            raise InputError(f"{file} must hold API groups.")
        provider_rows = out.setdefault(file.stem, {})
        for models in groups.values():
            if not isinstance(models, dict):
                raise InputError(f"{file}: every API group must be an object.")
            for key, model in models.items():
                if key.startswith("chat:") and isinstance(model, dict):
                    provider_rows[str(model.get("id", key[5:]))] = model
    return out


def _generated_at(manifest: Path) -> str | None:
    data = _read_json(manifest)
    stamp = data.get("generatedAt") if isinstance(data, dict) else None
    return stamp if isinstance(stamp, str) else None


# ---- normalization ----------------------------------------------------------


def pi_thinking(model: Mapping[str, Any]) -> dict[str, Any]:
    """Pi's effective per-level map (``models.ts`` ``getSupportedThinkingLevels``).

    On a reasoning row an ordinary level the map omits is identity-supported;
    ``off``/``xhigh``/``max`` are taken as written. A non-reasoning row has no
    levels.
    """

    if not model.get("reasoning"):
        return dict.fromkeys(THINKING_LEVELS, ABSENT)
    raw = model.get("thinkingLevelMap") or {}
    effective: dict[str, Any] = {}
    for level in THINKING_LEVELS:
        if level in raw:
            effective[level] = raw[level]
        elif level in ORDINARY_LEVELS:
            effective[level] = level
        else:
            effective[level] = ABSENT
    return effective


def pipy_thinking(row: NativeModelSpec) -> dict[str, Any]:
    """Pipy's effective per-level map (``thinking.py``).

    An empty map on a reasoning row means ordinary identity. A non-empty map is
    read by key, so an ordinary level it omits is unsupported.
    """

    if not row.reasoning:
        return dict.fromkeys(THINKING_LEVELS, ABSENT)
    raw = dict(row.thinking_level_map)
    if not raw:
        return {
            level: level if level in ORDINARY_LEVELS else ABSENT
            for level in THINKING_LEVELS
        }
    return {level: raw.get(level, ABSENT) for level in THINKING_LEVELS}


def _compat(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def pi_values(model: Mapping[str, Any]) -> dict[str, Any]:
    cost = model.get("cost") or {}
    compat = _compat(model.get("compat"))
    values: dict[str, Any] = {
        "api": model.get("api"),
        "reasoning": bool(model.get("reasoning")),
        "input": sorted(model.get("input") or ["text"]),
        "cost": [
            float(cost.get(key, 0))
            for key in ("input", "output", "cacheRead", "cacheWrite")
        ],
        "contextWindow": model.get("contextWindow"),
        "maxTokens": model.get("maxTokens"),
        "thinkingLevelMap": pi_thinking(model),
        "baseUrl": model.get("baseUrl") or "",
    }
    for key in TRACKED_COMPAT:
        values[f"compat.{key}"] = compat.get(key, ABSENT)
    return values


def pipy_values(row: NativeModelSpec) -> dict[str, Any]:
    compat = _compat(row.compat)
    values: dict[str, Any] = {
        "api": row.api,
        "reasoning": row.reasoning,
        "input": sorted(row.input),
        "cost": [
            float(row.cost.input),
            float(row.cost.output),
            float(row.cost.cache_read),
            float(row.cost.cache_write),
        ],
        "contextWindow": row.context_window,
        "maxTokens": row.max_tokens,
        "thinkingLevelMap": pipy_thinking(row),
        "baseUrl": row.base_url or "",
    }
    for key in TRACKED_COMPAT:
        values[f"compat.{key}"] = compat.get(key, ABSENT)
    return values


def proposal(field_name: str, model: Mapping[str, Any]) -> str:
    """The Pi value in ``catalog_data._m(...)`` keyword form."""

    if field_name == "thinkingLevelMap":
        raw = dict(model.get("thinkingLevelMap") or {})
        if model.get("reasoning") and raw:
            # pipy reads map keys, so spell out Pi's identity ordinary levels.
            for level in ORDINARY_LEVELS:
                raw.setdefault(level, level)
        ordered = {level: raw[level] for level in THINKING_LEVELS if level in raw}
        return f"thinking={ordered!r}"
    values = pi_values(model)
    value = values[field_name]
    if field_name == "cost":
        return f"cost={tuple(value)!r}"
    if field_name == "input":
        return f"image={'image' in value!r}"
    if field_name.startswith("compat."):
        if value == ABSENT:
            return f"drop compat[{field_name[7:]!r}]"
        return f"compat[{field_name[7:]!r}]={value!r}"
    keyword = {
        "contextWindow": "context_window",
        "maxTokens": "max_tokens",
        "baseUrl": "base_url",
    }.get(field_name, field_name)
    return f"{keyword}={value!r}"


# ---- comparison -------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Finding:
    ref: str
    field: str
    pipy: Any = None
    pi: Any = None
    proposal: str | None = None


def compare(rows: Iterable[NativeModelSpec], pi_models: PiModels) -> list[Finding]:
    """Every difference between pipy's rows and Pi's, before the allowlist."""

    findings: list[Finding] = []
    carried: dict[str, set[str]] = {}
    for row in rows:
        if row.provider_name in SKIPPED_PROVIDERS:
            continue
        pi_provider = PI_PROVIDER.get(row.provider_name, row.provider_name)
        if row.provider_name not in MIRROR_PROVIDERS:
            carried.setdefault(pi_provider, set()).add(row.model_id)
        model = pi_models.get(pi_provider, {}).get(row.model_id)
        if model is None:
            findings.append(Finding(row.reference, "pipy-only"))
            continue
        ours, theirs = pipy_values(row), pi_values(model)
        for name in VALUE_FIELDS:
            if ours[name] != theirs[name]:
                findings.append(
                    Finding(
                        row.reference,
                        name,
                        ours[name],
                        theirs[name],
                        proposal(name, model),
                    )
                )
    pipy_name = {
        pi: pipy for pipy, pi in PI_PROVIDER.items() if pipy not in MIRROR_PROVIDERS
    }
    for pi_provider, ids in sorted(carried.items()):
        for model_id in sorted(pi_models.get(pi_provider, {})):
            if model_id not in ids:
                provider = pipy_name.get(pi_provider, pi_provider)
                findings.append(Finding(f"{provider}/{model_id}", "not-carried"))
    return findings


# ---- allowlist ----------------------------------------------------------------


@dataclass(slots=True)
class AllowEntry:
    kind: str
    match: str
    field: str
    reason: str
    excluded: tuple[str, ...] = ()
    hits: list[Finding] = dataclass_field(default_factory=list)

    def covers(self, finding: Finding) -> bool:
        return (
            self.field == finding.field
            and fnmatch.fnmatchcase(finding.ref, self.match)
            and not any(fnmatch.fnmatchcase(finding.ref, p) for p in self.excluded)
        )

    def label(self) -> str:
        return f"[{self.kind}] {self.match} {self.field}: {self.reason}"


def load_allowlist(path: Path) -> list[AllowEntry]:
    data = _read_json(path)
    if not isinstance(data, list):
        raise InputError(f"{path} must hold a JSON list of entries.")
    entries: list[AllowEntry] = []
    for index, item in enumerate(data):
        where = f"{path} entry {index}"
        if not isinstance(item, dict):
            raise InputError(f"{where} must be an object.")
        kind, match = item.get("kind"), item.get("match")
        field_name, reason = item.get("field"), item.get("reason")
        if kind not in KINDS:
            raise InputError(f"{where}: kind must be one of {', '.join(KINDS)}.")
        if field_name not in (*ROW_FIELDS, *VALUE_FIELDS):
            raise InputError(f"{where}: unknown field {field_name!r}.")
        if not isinstance(match, str) or not match:
            raise InputError(f"{where}: match must be a non-empty pattern.")
        if not isinstance(reason, str) or not reason.strip():
            raise InputError(f"{where}: reason is required.")
        excluded = item.get("except", [])
        if not isinstance(excluded, list) or not all(
            isinstance(p, str) and p for p in excluded
        ):
            raise InputError(f"{where}: except must be a list of patterns.")
        unknown = set(item) - {"kind", "match", "field", "reason", "except"}
        if unknown:
            raise InputError(f"{where}: unknown keys {sorted(unknown)}.")
        entries.append(AllowEntry(kind, match, field_name, reason, tuple(excluded)))
    return entries


@dataclass(slots=True)
class Report:
    drift: list[Finding]
    allowed: list[tuple[AllowEntry, Finding]]
    stale: list[AllowEntry]
    shared_rows: int
    pi_source: str
    generated_at: str | None

    @property
    def ok(self) -> bool:
        return not self.drift and not self.stale

    def by_kind(
        self, kind: str, fields: Sequence[str] | None = None
    ) -> list[tuple[AllowEntry, Finding]]:
        return [
            (entry, finding)
            for entry, finding in self.allowed
            if entry.kind == kind and (fields is None or finding.field in fields)
        ]


def build_report(
    findings: list[Finding],
    allowlist: list[AllowEntry],
    *,
    shared_rows: int,
    pi_source: str,
    generated_at: str | None,
) -> Report:
    drift: list[Finding] = []
    allowed: list[tuple[AllowEntry, Finding]] = []
    for finding in findings:
        entry = next((entry for entry in allowlist if entry.covers(finding)), None)
        if entry is None:
            drift.append(finding)
        else:
            entry.hits.append(finding)
            allowed.append((entry, finding))
    stale = [entry for entry in allowlist if not entry.hits]
    return Report(drift, allowed, stale, shared_rows, pi_source, generated_at)


# ---- output -------------------------------------------------------------------


def _describe(finding: Finding) -> str:
    if finding.field in ROW_FIELDS:
        return f"{finding.ref}  {finding.field}"
    pipy, pi = finding.pipy, finding.pi
    if finding.field == "thinkingLevelMap":
        pipy = {k: v for k, v in finding.pipy.items() if finding.pi[k] != v}
        pi = {k: finding.pi[k] for k in pipy}
    return (
        f"{finding.ref}  {finding.field}: pipy {pipy!r} != Pi {pi!r}"
        f"\n      proposed: {finding.proposal}"
    )


def render_text(report: Report, *, verbose: bool) -> str:
    lines = [f"Pi catalog: {report.pi_source}"]
    if report.generated_at:
        lines[0] += f" (generated {report.generated_at})"
    lines.append(f"Compared {report.shared_rows} rows both catalogs carry.")

    def section(title: str, items: list[str]) -> None:
        lines.append("")
        lines.append(f"{title} ({len(items)})")
        lines.extend(f"  {item}" for item in items)

    section("DRIFT: not allowlisted", [_describe(f) for f in report.drift])
    section("STALE allowlist entries: match nothing", [e.label() for e in report.stale])
    section(
        "PENDING: known drift awaiting a refresh",
        [
            f"{_describe(f)}\n      reason: {e.reason}"
            for e, f in report.by_kind("pending")
        ],
    )
    deviations = report.by_kind("deviation", VALUE_FIELDS)
    pipy_only = report.by_kind("deviation", ("pipy-only",))
    not_carried = report.by_kind("deviation", ("not-carried",))
    if verbose:
        section(
            "Allowlisted deviations",
            [f"{_describe(f)}\n      reason: {e.reason}" for e, f in deviations],
        )
        section("Pipy-only rows", [f"{f.ref}: {e.reason}" for e, f in pipy_only])
        section("Pi rows not carried", [f"{f.ref}: {e.reason}" for e, f in not_carried])
    lines.append("")
    lines.append(
        f"Summary: {len(report.drift)} drift, {len(report.stale)} stale, "
        f"{len(report.by_kind('pending'))} pending, {len(deviations)} allowlisted "
        f"field deviations, {len(pipy_only)} pipy-only rows, {len(not_carried)} "
        "Pi rows not carried."
    )
    lines.append("Result: " + ("CLEAN" if report.ok else "DRIFT"))
    return "\n".join(lines)


def _finding_json(finding: Finding) -> dict[str, Any]:
    return {
        "ref": finding.ref,
        "field": finding.field,
        "pipy": finding.pipy,
        "pi": finding.pi,
        "proposal": finding.proposal,
    }


def render_json(report: Report) -> str:
    payload = {
        "ok": report.ok,
        "pi_source": report.pi_source,
        "generated_at": report.generated_at,
        "shared_rows": report.shared_rows,
        "drift": [_finding_json(f) for f in report.drift],
        "stale": [
            {"kind": e.kind, "match": e.match, "field": e.field, "reason": e.reason}
            for e in report.stale
        ],
        "allowlisted": [
            {**_finding_json(f), "kind": e.kind, "reason": e.reason}
            for e, f in report.allowed
        ],
    }
    return json.dumps(payload, indent=2, sort_keys=True)


# ---- CLI ----------------------------------------------------------------------


def default_pi_data() -> Path:
    root = Path(os.environ.get("PI_MONO", Path.home() / "src" / "pi-mono"))
    return root.expanduser() / HYDRATED_SUBPATH


def run(
    pi_data: Path,
    allowlist_path: Path,
    rows: Sequence[NativeModelSpec] = BUILTIN_MODEL_ROWS,
) -> Report:
    pi_models, generated_at = load_pi_models(pi_data)
    allowlist = load_allowlist(allowlist_path)
    findings = compare(rows, pi_models)
    missing = sum(
        1
        for row in rows
        if row.provider_name not in SKIPPED_PROVIDERS
        and any(f.ref == row.reference and f.field == "pipy-only" for f in findings)
    )
    shared = (
        sum(1 for row in rows if row.provider_name not in SKIPPED_PROVIDERS) - missing
    )
    return build_report(
        findings,
        allowlist,
        shared_rows=shared,
        pi_source=str(pi_data),
        generated_at=generated_at,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0] if __doc__ else None
    )
    parser.add_argument(
        "--pi-data",
        type=Path,
        default=None,
        help="Pi models.json, a --json-output directory, or the hydrated data "
        "directory (default: $PI_MONO/packages/ai/src/providers/data, "
        "PI_MONO defaulting to ~/src/pi-mono).",
    )
    parser.add_argument("--allowlist", type=Path, default=DEFAULT_ALLOWLIST)
    parser.add_argument("--json", action="store_true", help="Print the report as JSON.")
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Also list allowlisted deviations, pipy-only rows and Pi rows not carried.",
    )
    args = parser.parse_args(argv)
    pi_data = args.pi_data or default_pi_data()
    try:
        report = run(pi_data, args.allowlist)
    except InputError as exc:
        print(f"catalog-drift: {exc}\n{REGENERATE_HINT}", file=sys.stderr)
        return 2
    print(
        render_json(report) if args.json else render_text(report, verbose=args.verbose)
    )
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
