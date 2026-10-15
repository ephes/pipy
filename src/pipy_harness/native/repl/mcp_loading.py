"""Loading the MCP servers of a session.

``load_mcp_servers`` is the session's ``McpServersLoader`` once its first
arguments are bound: it reads the configuration as it is now and builds the
manager for it, which connects nothing until it is started.
"""

from __future__ import annotations

import os
from functools import partial
from pathlib import Path

from pipy_harness.native.extension_types import ExtensionProvider
from pipy_harness.native.mcp.config import LoadedMcpConfig, load_mcp_config
from pipy_harness.native.mcp.connection import ProviderToken, create_default_transport
from pipy_harness.native.mcp.credentials import (
    McpOAuthCredentialStore,
    mcp_auth_path,
)
from pipy_harness.native.mcp.discovery import McpDiscovery
from pipy_harness.native.mcp.log import McpServerLog, mcp_log_path
from pipy_harness.native.mcp.manager import (
    McpManager,
    RegisteredServers,
    update_config_file,
)
from pipy_harness.native.mcp.manager_flow import McpBackgroundActions
from pipy_harness.native.mcp.options import McpOptions
from pipy_harness.native.mcp.readiness import McpReadiness
from pipy_harness.native.openai_codex_provider import (
    FileOpenAICodexCredentialStore,
    OpenAICodexAuthManager,
    default_openai_codex_auth_path,
)
from pipy_harness.native.provider_login_token import provider_login_token
from pipy_harness.native.repl.mcp_session import McpServers
from pipy_harness.native.repl_state import (
    NativeReplProviderState,
    StaticNativeReplProviderState,
)
from pipy_harness.native.resource_loading import RuntimeResourceOptions
from pipy_harness.native.settings import SettingsManager
from pipy_harness.native.tool_capabilities import NativeToolCapabilities
from pipy_harness.native.version_check import pipy_version


def mcp_wanted(
    resource_options: RuntimeResourceOptions, settings: SettingsManager
) -> bool:
    """Whether the session has MCP, before a replacing extension is counted.

    The ``pipy`` command (no ``builtins``) follows its settings and flags. An
    embedded session has MCP when the embedder gave it MCP options, whatever
    the ``extensions`` settings say; ``no_mcp`` still turns it off.
    """

    builtins = resource_options.builtins
    if builtins is None:
        return resource_options.mcp_enabled(
            configured=settings.builtin_extension_enabled("mcp")
        )
    return builtins.mcp is not None and not resource_options.no_mcp


def load_mcp_servers(
    cwd: Path,
    settings: SettingsManager,
    resource_options: RuntimeResourceOptions,
    tool_capabilities: NativeToolCapabilities,
    provider_state: NativeReplProviderState | StaticNativeReplProviderState | None,
    registered: RegisteredServers,
    replaced_by: str | None,
) -> McpServers:
    """The session's MCP servers as ``mcp.json`` and the settings say now,
    not started yet. Called at the start of the session and by every reload.

    The user-level file is the one next to the user-level settings file; the
    project file is read only for a trusted project. With MCP turned off
    (``--no-mcp``, ``-builtin:mcp``, an embedded session that was not given
    MCP, or ``replaced_by``, an extension that has the ``/mcp`` command) no
    configuration is read and the manager has no server, so it starts nothing
    and reports nothing. What an embedder's ``McpOptions`` leave out is as
    for the ``pipy`` command.
    """

    builtins = resource_options.builtins
    wanted = mcp_wanted(resource_options, settings)
    enabled = wanted and replaced_by is None
    options = (None if builtins is None else builtins.mcp) or McpOptions()
    config_home = settings.global_path.parent
    manager = McpManager(
        config=(options.load_config or load_mcp_config)(
            config_home=config_home,
            cwd=cwd,
            project_trusted=settings.project_trusted,
        )
        if enabled
        else LoadedMcpConfig(servers=[], errors=[]),
        cwd=cwd,
        client_version=pipy_version(),
        publish_tools=tool_capabilities.publish_mcp_tools,
        deactivate_tools=tool_capabilities.deactivate_indirect_mcp_tools,
        log=McpServerLog(options.log_path or mcp_log_path(config_home)),
        create_transport=options.create_transport or create_default_transport,
        update_config=options.update_config or update_config_file,
        credentials=options.credentials
        or McpOAuthCredentialStore(mcp_auth_path(config_home)),
        provider_token=provider_token(provider_state),
        registered=registered if enabled else None,
    )
    return McpServers(
        manager=manager,
        discovery=McpDiscovery(
            configured_exposures=manager.configured_exposures,
            auto_enable_codemode=manager.auto_enable_codemode,
            tools=tool_capabilities,
            notify=manager.post_notice,
        ),
        readiness=McpReadiness(
            manager,
            tool_capabilities.all_tools,
            startup_wait_seconds=options.startup_wait_seconds,
        ),
        enabled=enabled,
        actions=McpBackgroundActions(manager),
        replaced_by=replaced_by if wanted else None,
        open_url=options.open_url,
    )


def provider_token(
    provider_state: NativeReplProviderState | StaticNativeReplProviderState | None,
) -> ProviderToken | None:
    """What answers an MCP server's ``auth.provider`` with that provider's
    current token, or ``None`` for a session without provider logins: one
    with an injected provider, or one whose auth store is unavailable.

    The function it returns runs on MCP request threads. It is given the
    path of ``auth.json``, the environment, the lock-protected OAuth cache
    and the Codex login file, and not the catalog's auth store, which belongs
    to the session thread. Of the catalog it reads three things there, each
    an attribute the session thread replaces by assignment and does not
    change in place: the provider an extension registered under the name,
    the ``--api-key`` with the provider it belongs to, and the
    ``models.json`` request settings. A snapshot taken
    here would be the one before a reload's new extensions and
    ``models.json``, because the servers are loaded first.
    """

    if not isinstance(provider_state, NativeReplProviderState):
        return None
    catalog = provider_state.model_runtime.catalog
    auth_store = catalog.auth_store
    if auth_store is None:
        return None
    # The Codex login a model request uses (``OpenAICodexAuthManager``).
    codex = OpenAICodexAuthManager(
        store=FileOpenAICodexCredentialStore(
            catalog.openai_codex_auth_path or default_openai_codex_auth_path()
        )
    )

    def models_json_key(provider: str) -> str | None:
        config = catalog.catalog.provider_request_configs.get(provider)
        return config.api_key if config is not None else None

    def extension_provider(provider: str) -> ExtensionProvider | None:
        registered = catalog.extension_provider_for(provider)
        return registered.provider if registered is not None else None

    return partial(
        provider_login_token,
        auth_path=auth_store.path,
        env=catalog.env if catalog.env is not None else os.environ,
        oauth_credentials=catalog.oauth_credentials,
        extension_provider=extension_provider,
        runtime_api_key=catalog.runtime_api_key_for,
        models_json_key=models_json_key,
        codex_login=codex.get_credentials,
    )
