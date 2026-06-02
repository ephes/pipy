"""Pipy model-pattern matcher.

Reproduces every layer of Pi's ``model-resolver.ts`` over pipy
:class:`~pipy_harness.native.catalog.NativeModelSpec` rows:

1. exact ``provider/id`` reference match (case-insensitive, ambiguity rejected);
2. ``provider/id:thinking-level`` parsing (colon-in-id handled by trying the
   full pattern as a model first, via the fuzzy step, before any colon split);
3. fuzzy substring with alias-over-dated preference;
4. glob scoping (``fnmatch``) over ``provider/id`` and bare ``id``;
5. CLI resolution with provider inference and per-provider fallback synthesis.

This is the single matching surface for ``--native-model``/``--native-provider``,
``/model <ref>``, the ``/model`` selector, ``--models`` cycling, and
``--list-models`` filtering.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace

from pipy_harness.native.catalog import (
    THINKING_LEVELS,
    NativeModelSpec,
    default_model_per_provider,
)


_DATE_SUFFIX = re.compile(r"-\d{8}$")

# Glob-translation cache. Unlike stdlib ``fnmatch``, ``*`` and ``?`` here do NOT
# cross ``/`` (the minimatch default Pi relies on); ``**`` does. This matters
# for slash-bearing ids: minimatch ``openrouter/*`` does not match
# ``openrouter/openai/gpt-5.1-codex``, but ``fnmatch`` would.
_GLOB_CACHE: dict[str, re.Pattern[str]] = {}


def _compile_glob(pattern: str) -> re.Pattern[str]:
    cached = _GLOB_CACHE.get(pattern)
    if cached is not None:
        return cached
    out: list[str] = []
    i = 0
    n = len(pattern)
    while i < n:
        char = pattern[i]
        if char == "*":
            if i + 1 < n and pattern[i + 1] == "*":
                out.append(".*")
                i += 2
                continue
            out.append("[^/]*")
        elif char == "?":
            out.append("[^/]")
        elif char == "[":
            j = i + 1
            if j < n and pattern[j] in ("!", "^"):
                j += 1
            if j < n and pattern[j] == "]":
                j += 1
            while j < n and pattern[j] != "]":
                j += 1
            if j >= n:
                out.append(r"\[")
            else:
                inner = pattern[i + 1 : j]
                if inner.startswith(("!", "^")):
                    inner = "^" + inner[1:]
                out.append("[" + inner + "]")
                i = j + 1
                continue
        else:
            out.append(re.escape(char))
        i += 1
    compiled = re.compile("(?s:" + "".join(out) + r")\Z", re.IGNORECASE)
    _GLOB_CACHE[pattern] = compiled
    return compiled


def _glob_match(name: str, pattern: str) -> bool:
    """Case-insensitive minimatch-style match (``*``/``?`` do not cross ``/``)."""

    return _compile_glob(pattern).match(name) is not None


def is_valid_thinking_level(value: str) -> bool:
    """True for the six-value CLI thinking vocabulary (incl. ``off``)."""

    return value in THINKING_LEVELS


def is_alias(model_id: str) -> bool:
    """Pi's ``isAlias``: ids without a ``-YYYYMMDD`` date suffix are aliases.

    ``-latest`` counts as an alias.
    """

    if model_id.endswith("-latest"):
        return True
    return _DATE_SUFFIX.search(model_id) is None


def find_exact_model_reference(
    reference: str, rows: list[NativeModelSpec]
) -> NativeModelSpec | None:
    """Pi's ``findExactModelReferenceMatch``."""

    trimmed = reference.strip()
    if not trimmed:
        return None
    normalized = trimmed.lower()

    canonical = [r for r in rows if r.reference.lower() == normalized]
    if len(canonical) == 1:
        return canonical[0]
    if len(canonical) > 1:
        return None

    slash_index = trimmed.find("/")
    if slash_index != -1:
        provider = trimmed[:slash_index].strip()
        model_id = trimmed[slash_index + 1 :].strip()
        if provider and model_id:
            matches = [
                r
                for r in rows
                if r.provider_name.lower() == provider.lower()
                and r.model_id.lower() == model_id.lower()
            ]
            if len(matches) == 1:
                return matches[0]
            if len(matches) > 1:
                return None

    id_matches = [r for r in rows if r.model_id.lower() == normalized]
    return id_matches[0] if len(id_matches) == 1 else None


def _try_match_model(
    pattern: str, rows: list[NativeModelSpec]
) -> NativeModelSpec | None:
    """Pi's ``tryMatchModel``: exact reference, then fuzzy substring."""

    exact = find_exact_model_reference(pattern, rows)
    if exact is not None:
        return exact

    lowered = pattern.lower()
    matches = [
        r
        for r in rows
        if lowered in r.model_id.lower()
        or (r.display_name and lowered in r.display_name.lower())
    ]
    if not matches:
        return None

    aliases = [r for r in matches if is_alias(r.model_id)]
    dated = [r for r in matches if not is_alias(r.model_id)]
    pool = aliases if aliases else dated
    # Highest by reverse comparison on id. Pi uses ``b.id.localeCompare(a.id)``,
    # which orders case-insensitively; ``casefold`` as the primary key matches
    # that intent (a raw codepoint sort would group all uppercase first).
    pool = sorted(pool, key=lambda r: (r.model_id.casefold(), r.model_id), reverse=True)
    return pool[0]


@dataclass(frozen=True, slots=True)
class ParsedModelResult:
    model: NativeModelSpec | None
    thinking_level: str | None = None
    warning: str | None = None


def parse_model_pattern(
    pattern: str,
    rows: list[NativeModelSpec],
    *,
    allow_invalid_thinking_level_fallback: bool = True,
) -> ParsedModelResult:
    """Pi's ``parseModelPattern`` (recursive last-colon split)."""

    exact = _try_match_model(pattern, rows)
    if exact is not None:
        return ParsedModelResult(model=exact, thinking_level=None, warning=None)

    last_colon = pattern.rfind(":")
    if last_colon == -1:
        return ParsedModelResult(model=None)

    prefix = pattern[:last_colon]
    suffix = pattern[last_colon + 1 :]

    if is_valid_thinking_level(suffix):
        result = parse_model_pattern(
            prefix,
            rows,
            allow_invalid_thinking_level_fallback=allow_invalid_thinking_level_fallback,
        )
        if result.model is not None:
            return ParsedModelResult(
                model=result.model,
                thinking_level=None if result.warning else suffix,
                warning=result.warning,
            )
        return result

    if not allow_invalid_thinking_level_fallback:
        # Strict CLI mode: treat suffix as part of the id and fail, so we do not
        # silently resolve a neighbouring model.
        return ParsedModelResult(model=None)

    result = parse_model_pattern(
        prefix, rows, allow_invalid_thinking_level_fallback=allow_invalid_thinking_level_fallback
    )
    if result.model is not None:
        return ParsedModelResult(
            model=result.model,
            thinking_level=None,
            warning=(
                f'Invalid thinking level "{suffix}" in pattern "{pattern}". '
                "Using default instead."
            ),
        )
    return result


@dataclass(frozen=True, slots=True)
class ScopedModel:
    model: NativeModelSpec
    thinking_level: str | None = None


@dataclass(frozen=True, slots=True)
class ScopeResult:
    models: list[ScopedModel] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _is_glob(pattern: str) -> bool:
    return any(ch in pattern for ch in ("*", "?", "["))


def resolve_model_scope(
    patterns: list[str], rows: list[NativeModelSpec]
) -> ScopeResult:
    """Pi's ``resolveModelScope`` using ``fnmatch`` (case-insensitive globs)."""

    scoped: list[ScopedModel] = []
    warnings: list[str] = []
    seen: set[str] = set()

    def _add(model: NativeModelSpec, level: str | None) -> None:
        if model.reference in seen:
            return
        seen.add(model.reference)
        scoped.append(ScopedModel(model=model, thinking_level=level))

    for pattern in patterns:
        if _is_glob(pattern):
            glob_pattern = pattern
            level: str | None = None
            colon = pattern.rfind(":")
            if colon != -1:
                suffix = pattern[colon + 1 :]
                if is_valid_thinking_level(suffix):
                    level = suffix
                    glob_pattern = pattern[:colon]
            matching = [
                r
                for r in rows
                if _glob_match(r.reference, glob_pattern)
                or _glob_match(r.model_id, glob_pattern)
            ]
            if not matching:
                warnings.append(f'No models match pattern "{pattern}"')
                continue
            for model in matching:
                _add(model, level)
            continue

        result = parse_model_pattern(pattern, rows)
        if result.warning:
            warnings.append(result.warning)
        if result.model is None:
            warnings.append(f'No models match pattern "{pattern}"')
            continue
        _add(result.model, result.thinking_level)

    return ScopeResult(models=scoped, warnings=warnings)


@dataclass(frozen=True, slots=True)
class ResolveCliModelResult:
    model: NativeModelSpec | None
    thinking_level: str | None = None
    warning: str | None = None
    error: str | None = None


def _build_fallback_model(
    provider: str, model_id: str, rows: list[NativeModelSpec]
) -> NativeModelSpec | None:
    provider_rows = [r for r in rows if r.provider_name == provider]
    if not provider_rows:
        return None
    default_id = default_model_per_provider.get(provider)
    base = None
    if default_id:
        base = next((r for r in provider_rows if r.model_id == default_id), None)
    if base is None:
        base = provider_rows[0]
    return replace(base, model_id=model_id, display_name=model_id)


def resolve_cli_model(
    *,
    cli_provider: str | None,
    cli_model: str | None,
    rows: list[NativeModelSpec],
) -> ResolveCliModelResult:
    """Pi's ``resolveCliModel``."""

    if not cli_model:
        return ResolveCliModelResult(model=None)

    if not rows:
        return ResolveCliModelResult(
            model=None,
            error="No models available. Check your installation or add models to models.json.",
        )

    provider_map: dict[str, str] = {}
    for r in rows:
        provider_map.setdefault(r.provider_name.lower(), r.provider_name)

    provider: str | None = None
    if cli_provider:
        provider = provider_map.get(cli_provider.lower())
        if provider is None:
            return ResolveCliModelResult(
                model=None,
                error=(
                    f'Unknown provider "{cli_provider}". '
                    "Use --list-models to see available providers/models."
                ),
            )

    pattern = cli_model
    inferred_provider = False

    if provider is None:
        slash_index = cli_model.find("/")
        if slash_index != -1:
            maybe_provider = cli_model[:slash_index]
            canonical = provider_map.get(maybe_provider.lower())
            if canonical:
                provider = canonical
                pattern = cli_model[slash_index + 1 :]
                inferred_provider = True

    if provider is None:
        lower = cli_model.lower()
        exact = next(
            (
                r
                for r in rows
                if r.model_id.lower() == lower or r.reference.lower() == lower
            ),
            None,
        )
        if exact is not None:
            return ResolveCliModelResult(model=exact)

    if cli_provider and provider:
        prefix = f"{provider}/"
        if cli_model.lower().startswith(prefix.lower()):
            pattern = cli_model[len(prefix) :]

    candidates = (
        [r for r in rows if r.provider_name == provider] if provider else rows
    )
    parsed = parse_model_pattern(
        pattern, candidates, allow_invalid_thinking_level_fallback=False
    )
    if parsed.model is not None:
        return ResolveCliModelResult(
            model=parsed.model,
            thinking_level=parsed.thinking_level,
            warning=parsed.warning,
        )

    if inferred_provider:
        lower = cli_model.lower()
        exact = next(
            (
                r
                for r in rows
                if r.model_id.lower() == lower or r.reference.lower() == lower
            ),
            None,
        )
        if exact is not None:
            return ResolveCliModelResult(model=exact)
        fallback = parse_model_pattern(
            cli_model, rows, allow_invalid_thinking_level_fallback=False
        )
        if fallback.model is not None:
            return ResolveCliModelResult(
                model=fallback.model,
                thinking_level=fallback.thinking_level,
                warning=fallback.warning,
            )

    if provider:
        fallback_model = _build_fallback_model(provider, pattern, rows)
        if fallback_model is not None:
            base_warning = (
                f'Model "{pattern}" not found for provider "{provider}". '
                "Using custom model id."
            )
            warning = (
                f"{parsed.warning} {base_warning}" if parsed.warning else base_warning
            )
            return ResolveCliModelResult(
                model=fallback_model, thinking_level=None, warning=warning
            )

    display = f"{provider}/{pattern}" if provider else cli_model
    return ResolveCliModelResult(
        model=None,
        warning=parsed.warning,
        error=f'Model "{display}" not found. Use --list-models to see available models.',
    )
