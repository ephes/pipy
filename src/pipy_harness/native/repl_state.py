"""Native REPL provider/model selection state."""

from __future__ import annotations

import inspect
import json
import os
import stat
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO, cast

from pipy_harness.capture import sanitize_text
from pipy_harness.native.openai_codex_provider import (
    OpenAICodexAuthManager,
    default_openai_codex_auth_path,
)
from pipy_harness.native.fake import AUTOMATION_FAKE_MODEL_ID
from pipy_harness.native.catalog import NativeModelSpec
from pipy_harness.native.catalog_state import ProviderCatalogState
from pipy_harness.native.provider_construction import ConstructionOptions
from pipy_harness.native.provider_registry import (
    DEFAULT_NATIVE_MODELS,
    SUPPORTED_NATIVE_PROVIDERS,
    native_provider_available,
)
from pipy_harness.native.provider import ProviderPort


@dataclass(frozen=True, slots=True)
class NativeModelSelection:
    """Current provider/model selection for one native REPL."""

    provider_name: str
    model_id: str

    @property
    def reference(self) -> str:
        return f"{self.provider_name}/{self.model_id}"


# Last-resort fake selection for the product REPL. The product REPL always
# builds the tool-loop session, which requires a tool-capable provider, so the
# fake fallback must advertise tool calls (``fake/fake-tools`` ->
# ``AutomationFakeProvider``) rather than the inert ``fake-native-bootstrap``
# used by the one-shot ``pipy run`` path.
REPL_FAKE_FALLBACK_SELECTION = NativeModelSelection("fake", AUTOMATION_FAKE_MODEL_ID)


@dataclass(frozen=True, slots=True)
class _ExtensionOAuthCallbacks:
    input_stream: TextIO
    output_stream: TextIO

    def on_auth(self, info: Mapping[str, object]) -> None:
        url = sanitize_text(str(info.get("url", ""))).strip()
        instructions = sanitize_text(str(info.get("instructions", ""))).strip()
        if url:
            print(f"Open this URL in your browser:\n{url}", file=self.output_stream)
        if instructions:
            print(instructions, file=self.output_stream)

    def onAuth(self, info: Mapping[str, object]) -> None:  # noqa: N802 - Pi shape
        self.on_auth(info)

    def on_device_code(self, info: Mapping[str, object]) -> None:
        uri = sanitize_text(str(info.get("verificationUri", info.get("url", "")))).strip()
        code = sanitize_text(str(info.get("userCode", ""))).strip()
        if uri:
            print(f"Open this URL in your browser:\n{uri}", file=self.output_stream)
        if code:
            print(f"Enter code: {code}", file=self.output_stream)

    def onDeviceCode(self, info: Mapping[str, object]) -> None:  # noqa: N802 - Pi shape
        self.on_device_code(info)

    def on_prompt(self, prompt: Mapping[str, object]) -> str:
        message = sanitize_text(str(prompt.get("message", ""))).strip() or "Prompt"
        placeholder = sanitize_text(str(prompt.get("placeholder", ""))).strip()
        suffix = f" ({placeholder})" if placeholder else ""
        print(f"{message}{suffix}: ", end="", file=self.output_stream)
        self.output_stream.flush()
        return self.input_stream.readline().rstrip("\r\n")

    def onPrompt(self, prompt: Mapping[str, object]) -> str:  # noqa: N802 - Pi shape
        return self.on_prompt(prompt)

    def on_select(self, prompt: Mapping[str, object]) -> object | None:
        message = sanitize_text(str(prompt.get("message", ""))).strip() or "Select"
        options = prompt.get("options")
        option_list = list(options) if isinstance(options, list) else []
        print(message, file=self.output_stream)
        for index, option in enumerate(option_list, start=1):
            label = option.get("label") if isinstance(option, Mapping) else option
            print(f"  {index}. {sanitize_text(str(label))}", file=self.output_stream)
        print(f"Enter number (1-{len(option_list)}): ", end="", file=self.output_stream)
        self.output_stream.flush()
        try:
            selected = int(self.input_stream.readline().strip()) - 1
        except ValueError:
            return None
        if selected < 0 or selected >= len(option_list):
            return None
        option = option_list[selected]
        if isinstance(option, Mapping):
            return option.get("id")
        return option

    def onSelect(self, prompt: Mapping[str, object]) -> object | None:  # noqa: N802 - Pi shape
        return self.on_select(prompt)

    def on_progress(self, message: object) -> None:
        print(sanitize_text(str(message)), file=self.output_stream)

    def onProgress(self, message: object) -> None:  # noqa: N802 - Pi shape
        self.on_progress(message)


def normalize_repl_fake_selection(
    selection: NativeModelSelection,
) -> NativeModelSelection:
    """Upgrade a ``fake`` REPL selection to the tool-capable fake.

    The product REPL always builds the tool-loop session, which requires a
    tool-capable provider. Whenever the resolved provider is ``fake`` — from the
    no-provider fallback, an explicit ``--native-provider fake`` (with or
    without the inert ``fake-native-bootstrap`` model), or a stored default —
    normalize to ``fake/fake-tools`` (``AutomationFakeProvider``). Real
    (non-fake) providers are returned unchanged so genuinely tool-incapable real
    providers still error at the session gate. The one-shot ``pipy run`` path
    does not use this helper and keeps ``fake-native-bootstrap``.
    """

    if selection.provider_name == "fake":
        return REPL_FAKE_FALLBACK_SELECTION
    return selection


@dataclass(frozen=True, slots=True)
class NativeModelOption:
    """A model reference exposed by the REPL selector / settings overlay.

    Capability metadata (context window, reasoning, image input) is populated
    from the catalog row so the selector can render Pi-equivalent rows. The
    fields stay optional because individual catalog rows may omit a given
    capability (e.g. a ``models.json`` row without a declared context window).
    """

    selection: NativeModelSelection
    available: bool
    reason: str | None = None
    context_window: int | None = None
    max_tokens: int | None = None
    reasoning: bool | None = None
    image_input: bool | None = None


@dataclass(frozen=True, slots=True)
class ModelRuntime:
    """Single owner of catalog spec resolution and provider construction.

    Composes the merged provider/model catalog (:class:`ProviderCatalogState`)
    with the catalog-driven ``provider_construction`` boundary so one object
    resolves *which* :class:`~pipy_harness.native.catalog.NativeModelSpec` a
    selection maps to and constructs the concrete ``ProviderPort`` for it.
    :meth:`construct` is total — every selection yields a provider through the
    construction boundary, threading the settings-derived
    :class:`ConstructionOptions`. It covers the construction shapes:

    * a catalog-wired API family (built from the resolved spec + auth + routing +
      mapped thinking through :func:`build_provider`);
    * ``openai-codex-responses`` and the deterministic ``fake`` bootstrap (built
      directly in :func:`build_provider`, codex from the spec + options);
    * an extension-provider row (built through the extension runtime); and
    * the bare built-in ``ds4`` selection that has no catalog spec (built by name
      in :func:`build_builtin_provider`).

    :class:`NativeReplProviderState` holds one and delegates every provider build
    to it; there is no separate legacy provider factory.
    """

    catalog: ProviderCatalogState

    def resolve_spec(self, selection: NativeModelSelection) -> NativeModelSpec | None:
        """Resolve the catalog spec (with thinking map) for a selection, or None.

        Falls back to a synthesized row cloned from the provider's catalog base
        (baseUrl/headers/auth) for a not-yet-cataloged model id on a known
        provider, mirroring the prior ``_spec_for`` behavior.
        """

        from pipy_harness.native.model_resolver import build_fallback_model

        spec = self.catalog.find(selection.provider_name, selection.model_id)
        if spec is None:
            spec = build_fallback_model(
                selection.provider_name, selection.model_id, self.catalog.get_all()
            )
        return spec

    def thinking_levels(self, selection: NativeModelSelection) -> list[str]:
        """Ordered Shift+Tab cycle levels for a selection (Pi-aware).

        Returns the model's ``available_thinking_levels`` (``off`` plus the
        ordinary tier, with ``xhigh``/``max`` appended only when the row maps
        them). Falls back to the ordinary tier when the spec is unavailable so a
        custom or not-yet-cataloged reasoning model still cycles.
        """

        spec = self.resolve_spec(selection)
        if spec is None:
            return ["off", "minimal", "low", "medium", "high"]
        from pipy_harness.native.thinking import available_thinking_levels

        return available_thinking_levels(spec)

    def construct(
        self,
        selection: NativeModelSelection,
        *,
        thinking_level: str | None,
        options: ConstructionOptions,
    ) -> ProviderPort:
        """Construct the provider for any selection (total, catalog-owned).

        Resolves the catalog spec and builds through the construction boundary:
        an extension-provider row via the extension runtime; ``openai-codex`` /
        ``fake`` / a catalog-wired API family via :func:`build_provider` (codex
        threading the settings-derived ``options``); and the spec-less bare
        built-in ``ds4`` selection by name via :func:`build_builtin_provider`.
        A catalog-wired family whose auth fails yields a fail-closed provider
        (no silent fallback). There is no legacy provider factory.
        """

        from pipy_harness.native.provider_construction import (
            build_builtin_provider,
            build_provider,
            resolve_construction,
            try_build_extension_provider_port,
        )

        state = self.catalog
        spec = self.resolve_spec(selection)
        if spec is None:
            return build_builtin_provider(selection, options)
        if spec.api == "extension-provider":
            registered = state.extension_provider_for(spec.provider_name)
            if registered is None:
                return build_builtin_provider(selection, options)
            build_result = try_build_extension_provider_port(
                registered, model_id=spec.model_id
            )
            if build_result.port is None:
                diagnostic = (
                    f"extension provider factory failed: {build_result.diagnostic}"
                    if build_result.diagnostic
                    else "extension provider factory failed"
                )
                return _FailedExtensionProvider(
                    provider_name=spec.provider_name,
                    model_id=spec.model_id,
                    error=diagnostic,
                )
            return cast(ProviderPort, build_result.port)
        assert state.auth_store is not None
        resolved = resolve_construction(
            spec,
            store=state.auth_store,
            env=state._env(),
            runtime_api_key=state.runtime_api_key,
            models_json_auth=state._models_json_auth(spec.provider_name),
            thinking_level=thinking_level,
        )
        return build_provider(
            resolved, spec=spec, thinking_level=thinking_level, options=options
        )


class NativeDefaultsStore:
    """Private JSON store for non-secret native provider/model defaults."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_native_defaults_path()

    def load(self) -> NativeModelSelection | None:
        try:
            body = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(body, dict):
            return None
        if body.get("schema") != "pipy.native-defaults" or body.get("schema_version") != 1:
            return None
        provider_name = body.get("provider")
        model_id = body.get("model_id")
        if not isinstance(provider_name, str) or provider_name not in SUPPORTED_NATIVE_PROVIDERS:
            return None
        if not isinstance(model_id, str) or not model_id.strip():
            return None
        return NativeModelSelection(provider_name=provider_name, model_id=model_id.strip())

    def save(self, selection: NativeModelSelection) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.path.parent.chmod(0o700)
        except OSError:
            pass
        payload = {
            "schema": "pipy.native-defaults",
            "schema_version": 1,
            "provider": selection.provider_name,
            "model_id": selection.model_id,
        }
        temporary_path = self.path.with_name(f"{self.path.name}.partial")
        with temporary_path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
        temporary_path.chmod(stat.S_IRUSR | stat.S_IWUSR)
        temporary_path.replace(self.path)
        self.path.chmod(stat.S_IRUSR | stat.S_IWUSR)


@dataclass(slots=True)
class NativeReplProviderState:
    """Late-bound provider state for local REPL auth/model commands."""

    selection: NativeModelSelection
    # The runtime composes the full pipy catalog (built-in + models.json) with the
    # catalog-driven construction boundary: model_options() and select_model() read
    # the merged catalog with the shared matcher and availability gate (mirroring
    # Pi's /model selector over getAvailable()), and current_provider()/
    # provider_for() construct through it. It is always bound (production and tests
    # both supply one); every model listing, selection, availability, and provider
    # build flows through the catalog it owns.
    model_runtime: ModelRuntime
    # Settings-derived knobs threaded into every provider build (codex
    # retry/transport/timeouts). The default reproduces the built-in provider
    # defaults for callers that pass no settings.
    construction_options: ConstructionOptions = ConstructionOptions()
    defaults_store: NativeDefaultsStore | None = None
    auth_manager_factory: Callable[[], OpenAICodexAuthManager] = OpenAICodexAuthManager
    persist_defaults: bool = True
    thinking_level: str | None = None

    @property
    def _catalog(self) -> ProviderCatalogState:
        """The merged catalog owned by the runtime."""

        return self.model_runtime.catalog

    def current_selection(self) -> NativeModelSelection:
        return self.selection

    def current_provider(self) -> ProviderPort:
        return self.provider_for(self.selection)

    def provider_for(self, selection: NativeModelSelection) -> ProviderPort:
        """Construct the provider for any selection through the runtime.

        Used by ``current_provider`` and by the ``/model`` selector's
        tool-capability probe so a ``models.json`` custom provider/model is
        constructed the same way it will be used. The bound :class:`ModelRuntime`
        owns the whole construction switch, threading ``construction_options``.
        """

        return self.model_runtime.construct(
            selection,
            thinking_level=self.thinking_level,
            options=self.construction_options,
        )

    def current_thinking_levels(self) -> list[str]:
        """Ordered Shift+Tab cycle levels for the current model (Pi-aware)."""

        return self.model_runtime.thinking_levels(self.selection)

    def provider_available(self, provider_name: str) -> bool:
        return self._catalog.provider_available(provider_name)

    def model_options(self) -> list[NativeModelOption]:
        state = self._catalog
        options: list[NativeModelOption] = []
        for row in state.get_all():
            available = state.provider_available(row.provider_name)
            reason = (
                None
                if available
                else state.availability_reason(row.provider_name)
            )
            options.append(
                NativeModelOption(
                    NativeModelSelection(row.provider_name, row.model_id),
                    available=available,
                    reason=reason,
                    context_window=row.context_window,
                    max_tokens=row.max_tokens,
                    reasoning=row.reasoning,
                    image_input="image" in row.input,
                )
            )
        return options

    def select_model(self, reference: str) -> tuple[bool, str]:
        parsed = reference.strip()
        if not parsed:
            return False, "pipy: malformed /model command. Provide <provider>/<model> or <model>."

        return self._catalog_select_model(parsed)

    def current_selection_supported(self) -> bool:
        """Return whether the current selection is still backed by catalog rows."""

        state = self._catalog
        if state.find(self.selection.provider_name, self.selection.model_id):
            return True
        # A user-selected custom model id on a known provider is supported via a
        # fallback row cloned from that provider's catalog defaults.
        return bool(state.models_for(self.selection.provider_name))

    def current_selection_uses_extension_provider(self) -> bool:
        """Return whether the current selection is backed by an extension row."""

        state = self._catalog
        spec = state.find(
            self.selection.provider_name,
            self.selection.model_id,
        )
        return spec is not None and spec.api == "extension-provider"

    def reset_to_first_available_model(
        self,
        *,
        require_tool_calls: bool = False,
    ) -> NativeModelSelection | None:
        """Reset to the first available catalog option, optionally tool-capable."""

        for option in self.model_options():
            if not option.available:
                continue
            if require_tool_calls:
                try:
                    provider = self.provider_for(option.selection)
                except Exception:
                    continue
                if not getattr(provider, "supports_tool_calls", False):
                    continue
            self.selection = option.selection
            self._save_default(self.selection)
            return self.selection
        return None

    def _catalog_select_model(self, reference: str) -> tuple[bool, str]:
        """Resolve direct ``/model <ref>`` through the shared catalog resolver.

        Uses :func:`resolve_cli_model` so exact ``provider/id``, bare id, fuzzy
        alias, ``provider/id:level``, colon-in-id models, and the strict invalid-
        suffix / per-provider fallback behaviour all match Pi's model-resolver.
        Selection is then gated by availability (an unavailable target is refused
        with the prior selection intact).
        """

        from pipy_harness.native.model_resolver import resolve_cli_model

        state = self._catalog
        result = resolve_cli_model(
            cli_provider=None, cli_model=reference, rows=state.get_all()
        )
        if result.error is not None:
            return False, f"pipy: {sanitize_text(result.error)}"
        model = result.model
        if model is None:
            return False, "pipy: unsupported or unknown model reference."

        if not state.provider_available(model.provider_name):
            reason = state.availability_reason(model.provider_name)
            return False, (
                f"pipy: {model.provider_name} is unavailable ({reason or 'unknown'}); "
                "selection unchanged."
            )

        selection = NativeModelSelection(model.provider_name, model.model_id)
        self.selection = selection
        if result.thinking_level is not None:
            self.thinking_level = result.thinking_level
        self._save_default(selection)

        notes: list[str] = []
        if result.thinking_level is not None:
            notes.append(f"thinking: {result.thinking_level}")
        if result.warning:
            notes.append(sanitize_text(result.warning))
        suffix = f" ({'; '.join(notes)})" if notes else ""
        return True, f"pipy: selected model {selection.reference}{suffix}."

    def login(self, provider_name: str, *, input_stream: TextIO, output_stream: TextIO) -> tuple[bool, str]:
        provider = provider_name.strip() or "openai-codex"
        if provider == "openai-codex":
            self.auth_manager_factory().login_interactive(
                input_stream=input_stream,
                output_stream=output_stream,
                open_browser=True,
            )
            return True, "pipy: openai-codex OAuth login stored."
        catalog = self._catalog
        if catalog is not None:
            registered = catalog.extension_oauth_provider_for(provider)
            if registered is not None:
                return self._extension_oauth_login(
                    registered, input_stream=input_stream, output_stream=output_stream
                )
        return False, "pipy: unsupported login provider."

    def logout(self, provider_name: str) -> tuple[bool, str]:
        provider = provider_name.strip() or "openai-codex"
        if provider == "openai-codex":
            removed = self.auth_manager_factory().logout()
            if self.selection.provider_name == "openai-codex":
                # Persist the shared inert default; the product REPL normalizes the
                # live selection to a tool-capable fake at its consumption point.
                self.selection = NativeModelSelection("fake", DEFAULT_NATIVE_MODELS["fake"])
                self._save_default(self.selection)
            if removed:
                return True, "pipy: openai-codex OAuth credentials removed."
            return True, "pipy: no openai-codex OAuth credentials were stored."
        catalog = self._catalog
        if catalog is not None:
            registered = catalog.extension_oauth_provider_for(provider)
            if registered is not None:
                return self._extension_oauth_logout(registered)
        return False, "pipy: unsupported logout provider."

    def _extension_oauth_login(
        self,
        registered: object,
        *,
        input_stream: TextIO,
        output_stream: TextIO,
    ) -> tuple[bool, str]:
        oauth = registered.provider.oauth  # type: ignore[attr-defined]
        provider_name = registered.provider.name  # type: ignore[attr-defined]
        try:
            credentials = oauth.login(
                _ExtensionOAuthCallbacks(input_stream=input_stream, output_stream=output_stream)
            )
        except (KeyboardInterrupt, SystemExit):
            raise
        except BaseException as err:  # noqa: BLE001 - extension-owned callback
            return False, f"pipy: {provider_name} OAuth login failed with {type(err).__name__}."
        if inspect.isawaitable(credentials):
            close = getattr(credentials, "close", None)
            if callable(close):
                close()
            return False, "pipy: extension OAuth login returned unsupported awaitable."
        if not isinstance(credentials, Mapping):
            return False, "pipy: extension OAuth login returned invalid credentials."
        catalog = self._catalog
        assert catalog is not None
        store = catalog.auth_store
        assert store is not None
        store.set(provider_name, {"type": "oauth", **dict(credentials)})
        return True, f"pipy: {provider_name} OAuth login stored."

    def _extension_oauth_logout(self, registered: object) -> tuple[bool, str]:
        provider_name = registered.provider.name  # type: ignore[attr-defined]
        catalog = self._catalog
        assert catalog is not None
        store = catalog.auth_store
        assert store is not None
        removed = store.remove(provider_name)
        if self.selection.provider_name == provider_name:
            self.reset_to_first_available_model(require_tool_calls=False)
        if removed:
            return True, f"pipy: {provider_name} OAuth credentials removed."
        return True, f"pipy: no {provider_name} OAuth credentials were stored."

    def _save_default(self, selection: NativeModelSelection) -> None:
        if not self.persist_defaults or self.defaults_store is None:
            return
        try:
            self.defaults_store.save(selection)
        except OSError:
            pass


@dataclass(slots=True)
class StaticNativeReplProviderState:
    """Compatibility state for tests and callers that inject one provider."""

    provider: ProviderPort

    def current_selection(self) -> NativeModelSelection:
        return NativeModelSelection(self.provider.name, self.provider.model_id)

    def current_provider(self) -> ProviderPort:
        return self.provider

    def model_options(self) -> list[NativeModelOption]:
        return [
            NativeModelOption(
                NativeModelSelection(self.provider.name, self.provider.model_id),
                available=True,
            )
        ]

    def select_model(self, reference: str) -> tuple[bool, str]:
        return False, "pipy: /model is unavailable for this REPL provider state."

    def login(self, provider_name: str, *, input_stream: TextIO, output_stream: TextIO) -> tuple[bool, str]:
        return False, "pipy: /login is unavailable for this REPL provider state."

    def logout(self, provider_name: str) -> tuple[bool, str]:
        return False, "pipy: /logout is unavailable for this REPL provider state."


def settings_overlay_lines(
    provider_state: "NativeReplProviderState | StaticNativeReplProviderState",
    settings_manager: "object | None" = None,
) -> list[str]:
    """Build the read-only settings/status display lines.

    Shared by the no-tool ``/settings`` command and the product-TUI
    ``/settings`` overlay so both surface the same safe selection, the
    registered defaults, and the local availability (with reasons) of each
    supported provider. It is strictly read-only: it neither switches
    models/providers, starts login/logout, mutates auth state, invokes
    tools, nor creates a provider turn. Availability is derived from local
    environment and credential-file probes only.

    The builder deliberately emits no command-availability footer. Each
    caller appends a footer honest for its own command surface (both the
    no-tool REPL and the product tool-loop TUI can run
    ``/model``/``/login``/``/logout``; a static single-provider state can run
    none of them), so no surface advertises a command it cannot execute.
    """

    current = provider_state.current_selection()
    lines = [
        "pipy native REPL settings:",
        f"  active: {sanitize_text(current.provider_name)}/{sanitize_text(current.model_id)}",
        "  registered providers:",
    ]
    for option in provider_state.model_options():
        availability = (
            "available"
            if option.available
            else f"unavailable ({option.reason or 'unknown'})"
        )
        lines.append(
            "    "
            f"{sanitize_text(option.selection.provider_name)}/"
            f"{sanitize_text(option.selection.model_id)} "
            f"[{availability}]"
        )
    if settings_manager is not None:
        from pipy_harness.native.settings import (
            SettingsManager,
            settings_report_lines,
        )

        if isinstance(settings_manager, SettingsManager):
            lines.extend(settings_report_lines(settings_manager))
    return lines


def default_native_defaults_path() -> Path:
    configured_path = os.environ.get("PIPY_NATIVE_DEFAULTS_PATH")
    if configured_path:
        return Path(configured_path).expanduser()
    return Path.home() / ".local" / "state" / "pipy" / "native-defaults.json"


AUTO_DEFAULT_PROVIDER_PRIORITY: tuple[str, ...] = (
    "openai-codex",
    "openai",
    "anthropic",
    "google",
    "openrouter",
    "mistral",
    "amazon-bedrock",
    "azure-openai",
    "cloudflare",
    "google-vertex",
    "openai-completions",
)
"""Order in which the REPL chooses a real provider for the default session.

The default invocation `pipy` should not show `fake/fake-native-bootstrap`
to a user who has a real provider configured. This priority list scans the
ambient environment (OAuth credential files and conventional API-key env
vars) and selects the first match. The list intentionally mirrors common
Pi defaults — `openai-codex` first because Pi uses it when the user has
logged in, then keyed providers in rough order of how widely deployed they
are. `fake` remains the last-resort fallback.
"""


def auto_default_selection(
    *,
    env: Mapping[str, str] | None = None,
    openai_codex_auth_path: Path | None = None,
) -> NativeModelSelection | None:
    """Probe the ambient environment for an available real provider.

    Returns `None` when no real provider is available; callers fall back to
    the deterministic fake provider in that case.
    """

    probe_env = env if env is not None else os.environ
    codex_path = openai_codex_auth_path or default_openai_codex_auth_path()
    for provider_name in AUTO_DEFAULT_PROVIDER_PRIORITY:
        if _provider_available_in_env(
            provider_name, env=probe_env, openai_codex_auth_path=codex_path
        ):
            return NativeModelSelection(
                provider_name=provider_name,
                model_id=DEFAULT_NATIVE_MODELS[provider_name],
            )
    return None


def _provider_available_in_env(
    provider_name: str,
    *,
    env: Mapping[str, str],
    openai_codex_auth_path: Path,
) -> bool:
    return native_provider_available(
        provider_name,
        env=env,
        openai_codex_credentials_exist=openai_codex_auth_path.exists(),
        for_auto_default=True,
    )


def resolve_cli_selection(
    native_provider: str | None,
    native_model: str | None,
    rows: list,
) -> tuple[NativeModelSelection | None, str | None]:
    """Resolve startup ``--native-provider``/``--native-model`` against the catalog.

    Mirrors mid-session ``/model`` resolution (Pi's ``resolveCliModel``): a bare
    ``--native-model`` infers its provider, a ``provider/id`` ref or fuzzy match
    resolves, and a custom ``models.json`` provider name is accepted. With only
    ``--native-provider``, the provider's default catalog model is selected.

    Returns ``(selection, None)`` on success, ``(None, error)`` on an unknown
    provider/model, or ``(None, None)`` when neither flag is set (the caller
    falls back to stored/auto/fake defaults).
    """

    from pipy_harness.native.model_resolver import resolve_cli_model

    if native_model is not None:
        result = resolve_cli_model(
            cli_provider=native_provider, cli_model=native_model, rows=rows
        )
        if result.error is not None:
            return None, result.error
        if result.model is None:
            return None, (
                f'Unknown model "{native_model}". '
                "Use --list-models to see available providers/models."
            )
        return (
            NativeModelSelection(result.model.provider_name, result.model.model_id),
            None,
        )

    if native_provider is not None:
        provider_map = {r.provider_name.lower(): r.provider_name for r in rows}
        canonical = provider_map.get(native_provider.lower())
        if canonical is None:
            return None, (
                f'Unknown provider "{native_provider}". '
                "Use --list-models to see available providers/models."
            )
        provider_rows = [r for r in rows if r.provider_name == canonical]
        default_id = _default_model_for_provider(canonical)
        model_id = (
            default_id
            if default_id and any(r.model_id == default_id for r in provider_rows)
            else provider_rows[0].model_id
        )
        return NativeModelSelection(canonical, model_id), None

    return None, None


def default_selection_for(
    *,
    native_provider: str | None,
    native_model: str | None,
    defaults_store: NativeDefaultsStore | None = None,
    env: Mapping[str, str] | None = None,
    openai_codex_auth_path: Path | None = None,
    rows: list | None = None,
) -> NativeModelSelection:
    # Catalog-aware startup resolution (accepts custom models.json providers and
    # bare model refs). ``rows`` is the merged catalog; when omitted, the legacy
    # registry-validated path below is used (direct/test callers).
    if rows is not None and (native_provider is not None or native_model is not None):
        selection, error = resolve_cli_selection(native_provider, native_model, rows)
        if error is not None:
            raise ValueError(error)
        if selection is not None:
            return selection
    if native_provider is not None:
        if native_provider not in SUPPORTED_NATIVE_PROVIDERS:
            raise ValueError(f"unsupported native provider: {native_provider}")
        return NativeModelSelection(
            provider_name=native_provider,
            model_id=native_model or DEFAULT_NATIVE_MODELS[native_provider],
        )
    if native_model is not None:
        return NativeModelSelection(provider_name="fake", model_id=native_model)
    if defaults_store is not None:
        loaded = defaults_store.load()
        if loaded is not None and loaded.provider_name != "fake":
            return loaded
    auto = auto_default_selection(
        env=env, openai_codex_auth_path=openai_codex_auth_path
    )
    if auto is not None:
        return auto
    # Shared default: the inert ``fake-native-bootstrap``. The product REPL
    # upgrades any ``fake`` selection to the tool-capable ``fake-tools`` at its
    # own resolution point (see ``normalize_repl_fake_selection``); non-REPL
    # callers (e.g. one-shot ``pipy run``) keep ``fake-native-bootstrap``.
    return NativeModelSelection("fake", DEFAULT_NATIVE_MODELS["fake"])


def _default_model_for_provider(
    provider: str,
) -> str | None:
    from pipy_harness.native.catalog import default_model_per_provider

    return default_model_per_provider.get(provider)


@dataclass(frozen=True, slots=True)
class _FailedExtensionProvider:
    """Fail-closed provider for an extension factory that could not build."""

    provider_name: str
    model_id: str
    error: str
    supports_tool_calls: bool = False

    @property
    def name(self) -> str:
        return self.provider_name

    def complete(
        self, request, *, stream_sink=None, reasoning_sink=None, cancel_token=None
    ):
        from pipy_harness.native._provider_helpers import (
            failed_provider_result,
            utc_now,
        )

        del stream_sink, reasoning_sink, cancel_token
        return failed_provider_result(
            request,
            provider_name=self.provider_name,
            started_at=utc_now(),
            error_type="ExtensionProviderFactoryError",
            error_message=self.error,
        )
