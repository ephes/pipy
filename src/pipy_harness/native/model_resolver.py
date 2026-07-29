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
    index = 0
    while index < len(pattern):
        translated, index = _translate_glob_token(pattern, index)
        out.append(translated)
    compiled = re.compile("(?s:" + "".join(out) + r")\Z", re.IGNORECASE)
    _GLOB_CACHE[pattern] = compiled
    return compiled


def _translate_glob_token(pattern: str, index: int) -> tuple[str, int]:
    char = pattern[index]
    if char == "*":
        if index + 1 < len(pattern) and pattern[index + 1] == "*":
            return ".*", index + 2
        return "[^/]*", index + 1
    if char == "?":
        return "[^/]", index + 1
    if char == "[":
        return _translate_glob_character_class(pattern, index)
    return re.escape(char), index + 1


def _translate_glob_character_class(pattern: str, index: int) -> tuple[str, int]:
    end = index + 1
    if end < len(pattern) and pattern[end] in ("!", "^"):
        end += 1
    if end < len(pattern) and pattern[end] == "]":
        end += 1
    while end < len(pattern) and pattern[end] != "]":
        end += 1
    if end >= len(pattern):
        return r"\[", index + 1
    inner = pattern[index + 1 : end]
    if inner.startswith(("!", "^")):
        inner = "^" + inner[1:]
    return "[" + inner + "]", end + 1


def _glob_match(name: str, pattern: str) -> bool:
    """Case-insensitive minimatch-style match (``*``/``?`` do not cross ``/``)."""

    return _compile_glob(pattern).match(name) is not None


def is_valid_thinking_level(value: str) -> bool:
    """True for the seven-value CLI thinking vocabulary (incl. ``off``)."""

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
        prefix,
        rows,
        allow_invalid_thinking_level_fallback=allow_invalid_thinking_level_fallback,
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
    for pattern in patterns:
        matching, level, pattern_warnings = _expand_scope_pattern(pattern, rows)
        warnings.extend(pattern_warnings)
        for model in matching:
            if model.reference in seen:
                continue
            seen.add(model.reference)
            scoped.append(ScopedModel(model=model, thinking_level=level))
    return ScopeResult(models=scoped, warnings=warnings)


def _expand_scope_pattern(
    pattern: str, rows: list[NativeModelSpec]
) -> tuple[list[NativeModelSpec], str | None, list[str]]:
    if _is_glob(pattern):
        return _expand_scope_glob(pattern, rows)
    result = parse_model_pattern(pattern, rows)
    warnings = [result.warning] if result.warning else []
    if result.model is None:
        warnings.append(f'No models match pattern "{pattern}"')
        return [], None, warnings
    return [result.model], result.thinking_level, warnings


def _expand_scope_glob(
    pattern: str, rows: list[NativeModelSpec]
) -> tuple[list[NativeModelSpec], str | None, list[str]]:
    glob_pattern = pattern
    level: str | None = None
    colon = pattern.rfind(":")
    if colon != -1 and is_valid_thinking_level(pattern[colon + 1 :]):
        level = pattern[colon + 1 :]
        glob_pattern = pattern[:colon]
    matching = [
        row
        for row in rows
        if _glob_match(row.reference, glob_pattern)
        or _glob_match(row.model_id, glob_pattern)
    ]
    warnings = [] if matching else [f'No models match pattern "{pattern}"']
    return matching, level, warnings


@dataclass(frozen=True, slots=True)
class ResolveCliModelResult:
    model: NativeModelSpec | None
    thinking_level: str | None = None
    warning: str | None = None
    error: str | None = None


def build_fallback_model(
    provider: str, model_id: str, rows: list[NativeModelSpec]
) -> NativeModelSpec | None:
    """Clone a provider's default row, replacing id/name (Pi buildFallbackModel).

    Preserves the base model's ``base_url``/headers/``compat``/api so a
    synthesized fallback selection still constructs from catalog values.
    """

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


# Backwards-compatible private alias (kept for existing internal callers).
_build_fallback_model = build_fallback_model


@dataclass(frozen=True, slots=True)
class _CliModelRequest:
    provider: str | None
    pattern: str
    inferred_provider: bool


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

    request, early_result = _prepare_cli_model_request(cli_provider, cli_model, rows)
    if early_result is not None:
        return early_result
    assert request is not None

    candidates = (
        [row for row in rows if row.provider_name == request.provider]
        if request.provider
        else rows
    )
    parsed = parse_model_pattern(
        request.pattern, candidates, allow_invalid_thinking_level_fallback=False
    )
    if parsed.model is not None:
        return _project_parsed_cli_model(parsed)

    if request.inferred_provider:
        inferred_fallback = _resolve_inferred_cli_fallback(cli_model, rows)
        if inferred_fallback is not None:
            return inferred_fallback

    if request.provider:
        custom_fallback = _build_cli_custom_fallback(request, parsed, rows)
        if custom_fallback is not None:
            return custom_fallback

    display = f"{request.provider}/{request.pattern}" if request.provider else cli_model
    return ResolveCliModelResult(
        model=None,
        warning=parsed.warning,
        error=f'Model "{display}" not found. Use --list-models to see available models.',
    )


def _prepare_cli_model_request(
    cli_provider: str | None, cli_model: str, rows: list[NativeModelSpec]
) -> tuple[_CliModelRequest | None, ResolveCliModelResult | None]:
    provider_map: dict[str, str] = {}
    for row in rows:
        provider_map.setdefault(row.provider_name.lower(), row.provider_name)

    provider = provider_map.get(cli_provider.lower()) if cli_provider else None
    if cli_provider and provider is None:
        return None, ResolveCliModelResult(
            model=None,
            error=(
                f'Unknown provider "{cli_provider}". '
                "Use --list-models to see available providers/models."
            ),
        )

    pattern = cli_model
    inferred_provider = False
    if provider is None:
        provider, pattern, inferred_provider = _infer_cli_provider(
            cli_model, provider_map
        )
    if provider is None:
        exact = _find_direct_cli_model(cli_model, rows)
        if exact is not None:
            return None, ResolveCliModelResult(model=exact)
    if cli_provider and provider:
        prefix = f"{provider}/"
        if cli_model.lower().startswith(prefix.lower()):
            pattern = cli_model[len(prefix) :]
    return _CliModelRequest(provider, pattern, inferred_provider), None


def _infer_cli_provider(
    cli_model: str, provider_map: dict[str, str]
) -> tuple[str | None, str, bool]:
    slash_index = cli_model.find("/")
    if slash_index == -1:
        return None, cli_model, False
    canonical = provider_map.get(cli_model[:slash_index].lower())
    if not canonical:
        return None, cli_model, False
    return canonical, cli_model[slash_index + 1 :], True


def _find_direct_cli_model(
    cli_model: str, rows: list[NativeModelSpec]
) -> NativeModelSpec | None:
    lowered = cli_model.lower()
    return next(
        (
            row
            for row in rows
            if row.model_id.lower() == lowered or row.reference.lower() == lowered
        ),
        None,
    )


def _project_parsed_cli_model(parsed: ParsedModelResult) -> ResolveCliModelResult:
    return ResolveCliModelResult(
        model=parsed.model,
        thinking_level=parsed.thinking_level,
        warning=parsed.warning,
    )


def _resolve_inferred_cli_fallback(
    cli_model: str, rows: list[NativeModelSpec]
) -> ResolveCliModelResult | None:
    exact = _find_direct_cli_model(cli_model, rows)
    if exact is not None:
        return ResolveCliModelResult(model=exact)
    fallback = parse_model_pattern(
        cli_model, rows, allow_invalid_thinking_level_fallback=False
    )
    if fallback.model is None:
        return None
    return _project_parsed_cli_model(fallback)


def _build_cli_custom_fallback(
    request: _CliModelRequest,
    parsed: ParsedModelResult,
    rows: list[NativeModelSpec],
) -> ResolveCliModelResult | None:
    assert request.provider is not None
    fallback_model = _build_fallback_model(request.provider, request.pattern, rows)
    if fallback_model is None:
        return None
    base_warning = (
        f'Model "{request.pattern}" not found for provider "{request.provider}". '
        "Using custom model id."
    )
    warning = f"{parsed.warning} {base_warning}" if parsed.warning else base_warning
    return ResolveCliModelResult(
        model=fallback_model, thinking_level=None, warning=warning
    )
