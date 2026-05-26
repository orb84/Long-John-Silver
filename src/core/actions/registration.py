"""
Action registration service for LJS.

Centralizes all ActionGateway registrations so ``create_app()`` in
``src/web/app.py`` remains a thin composition root instead of a 100+
line registration block. Each domain area is registered by a dedicated
method.

Usage::

    registrar = ActionRegistrationService(gateway, deps)
    registrar.register_all()
"""

from src.core.actions.gateway import ActionGateway
from src.web.dependencies import WebDependencies
from src.web.action_handlers.category_items import CategoryItemActionHandler
from src.web.action_handlers.settings import SettingsActionHandler
from src.web.action_handlers.library import LibraryActionHandler
from src.web.action_handlers.system import SystemActionHandler
from src.web.action_handlers.providers import ProvidersActionHandler
from src.web.action_handlers.setup import SetupActionHandler
from src.web.action_handlers.upgrades import UpgradesActionHandler
from src.web.action_handlers.suggestions import SuggestionsActionHandler
from src.web.action_handlers.downloads import DownloadsActionHandler


class ActionRegistrationService:
    """Centralized registrar for all ActionGateway action handlers.

    Wires every deterministic UI mutation through the shared ActionGateway
    so that button clicks and LLM tool calls use the same handler.
    """

    def __init__(self, gateway: ActionGateway, deps: WebDependencies) -> None:
        self._gateway = gateway
        self._deps = deps

    def register_all(self) -> None:
        """Register all action handlers across every domain."""
        self._register_downloader_actions()
        self._register_category_item_actions()
        self._register_settings_actions()
        self._register_library_actions()
        self._register_system_actions()
        self._register_provider_actions()
        self._register_setup_actions()
        self._register_upgrade_actions()
        self._register_suggestion_actions()
        self._register_download_batch_actions()

    # ── Downloader core actions (pause, resume, priority, cancel, restart) ──

    def _register_downloader_actions(self) -> None:
        gw = self._gateway
        dl = self._deps.downloader
        for action_name, method_name in (
            ("pause_download", "pause_download"),
            ("resume_download", "resume_download"),
            ("download_set_priority", "set_priority"),
            ("cancel_download", "cancel_download"),
            ("restart_download", "restart_download"),
            ("set_file_priority", "set_file_priority"),
        ):
            gw.register(action_name, getattr(dl, method_name))

    # ── Category item management actions ──

    def _register_category_item_actions(self) -> None:
        deps = self._deps
        handler = CategoryItemActionHandler(
            deps.settings_manager,
            deps.category_registry,
            deps.db,
            deps.scheduler,
        )
        gw = self._gateway
        for action_name, method in (
            ("category_item_add", handler.add),
            ("category_item_remove", handler.remove),
            ("category_item_update", handler.update),
            ("category_item_pause", handler.pause),
            ("category_item_resume", handler.resume),
            ("category_action_execute", handler.execute_category_action),
        ):
            gw.register(action_name, method)

    # ── Settings actions ──

    def _register_settings_actions(self) -> None:
        deps = self._deps
        handler = SettingsActionHandler(
            deps.settings_manager, deps.assistant, deps.downloader,
            deps.auth_service, deps.llm_manager,
        )
        gw = self._gateway
        for action_name, method in (
            ("settings_update_persona", handler.update_persona),
            ("settings_update_llm", handler.update_llm),
            ("settings_update_quality", handler.update_quality),
            ("settings_update_tokens", handler.update_tokens),
            ("settings_update_auto_download", handler.update_auto_download),
            ("settings_update_tiers", handler.update_tiers),
            ("settings_update_embeddings", handler.update_embeddings),
            ("settings_update_library", handler.update_settings_library),
            ("settings_update_bandwidth", handler.update_bandwidth),
            ("settings_update_search", handler.update_search),
            ("settings_update_integrations", handler.update_integrations),
            ("settings_update_bridges", handler.update_bridges),
            ("settings_update_password", handler.update_password),
            ("settings_update_whatsapp", handler.update_whatsapp),
            ("settings_update_sharing", handler.update_sharing),
            ("settings_update_startup", handler.update_startup),
        ):
            gw.register(action_name, method)

    # ── Library actions ──

    def _register_library_actions(self) -> None:
        deps = self._deps
        handler = LibraryActionHandler(
            deps.scheduler, deps.settings_manager, deps.librarian,
        )
        gw = self._gateway
        for action_name, method in (
            ("library_scan", handler.scan),
            ("library_update_category_item_config", handler.update_category_item_config),
            ("library_consolidate", handler.consolidate),
        ):
            gw.register(action_name, method)

    # ── System actions ──

    def _register_system_actions(self) -> None:
        deps = self._deps
        handler = SystemActionHandler(
            deps.settings_manager, deps.browser_runtime,
            deps.jackett_manager, deps.comms_registry, deps.db,
            deps.auth_service,
        )
        gw = self._gateway
        for action_name, method in (
            ("system_install_playwright", handler.install_playwright),
            ("system_install_jackett", handler.install_jackett),
            ("system_start_jackett", handler.start_jackett),
            ("system_configure_default_indexers", handler.configure_default_indexers),
            ("system_configure_jackett_indexers", handler.configure_jackett_indexers),
            ("system_jackett_indexer_diagnostics", handler.jackett_indexer_diagnostics),
            ("system_jackett_indexer_config_schema", handler.jackett_indexer_config_schema),
            ("system_configure_jackett_custom_indexer", handler.configure_jackett_custom_indexer),
            ("system_auth_register", handler.auth_register),
            ("system_install_comms_bridge", handler.install_comms_bridge),
        ):
            gw.register(action_name, method)

    # ── Provider actions ──

    def _register_provider_actions(self) -> None:
        deps = self._deps
        handler = ProvidersActionHandler(
            deps.llm_manager, deps.settings_manager, deps.assistant,
        )
        gw = self._gateway
        for action_name, method in (
            ("provider_add_key", handler.add_key),
            ("provider_remove_key", handler.remove_key),
            ("provider_activate_key", handler.activate_key),
            ("provider_activate", handler.activate),
        ):
            gw.register(action_name, method)

    # ── Setup wizard actions ──

    def _register_setup_actions(self) -> None:
        deps = self._deps
        handler = SetupActionHandler(
            deps.settings_manager, deps.auth_service,
            deps.llm_manager, deps.assistant,
        )
        gw = self._gateway
        for action_name, method in (
            ("setup_password", handler.setup_password),
            ("setup_paths", handler.setup_paths),
            ("setup_category_config", handler.setup_category_config),
            ("setup_llm", handler.setup_llm),
            ("setup_embeddings", handler.setup_embeddings),
            ("setup_channels", handler.setup_channels),
            ("setup_language", handler.setup_language),
            ("setup_sharing", handler.setup_sharing),
            ("setup_startup", handler.setup_startup),
            ("setup_complete", handler.setup_complete),
        ):
            gw.register(action_name, method)

    # ── Upgrade actions ──

    def _register_upgrade_actions(self) -> None:
        deps = self._deps
        handler = UpgradesActionHandler(deps.db, deps.downloader)
        gw = self._gateway
        for action_name, method in (
            ("upgrade_approve", handler.approve),
            ("upgrade_deny", handler.deny),
        ):
            gw.register(action_name, method)

    # ── Suggestion actions ──

    def _register_suggestion_actions(self) -> None:
        deps = self._deps
        handler = SuggestionsActionHandler(
            deps.db, deps.settings_manager, deps.scheduler,
            deps.supervisor,
        )
        gw = self._gateway
        gw.register(
            "suggestions_list",
            handler.list,
            description=(
                "List pending suggestions with item names, action types, human-readable "
                "explanations, confidence, and category evidence. Use this before answering "
                "questions about why a suggestion exists."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "category_id": {"type": "string", "description": "Optional category filter using a registered category id."},
                    "item_id": {"type": "string", "description": "Optional category item id/name filter."},
                    "limit": {"type": "integer", "description": "Maximum pending suggestions to return, capped at 100."},
                },
                "required": [],
            },
        )
        for action_name, method in (
            ("suggestion_approve", handler.approve),
            ("suggestion_deny", handler.deny),
            ("suggestion_approve_all", handler.approve_all),
        ):
            gw.register(action_name, method)

    # ── Batch download actions ──

    def _register_download_batch_actions(self) -> None:
        deps = self._deps
        handler = DownloadsActionHandler(deps.downloader)
        gw = self._gateway
        gw.register("download_upload", handler.upload)
        gw.register("pause_downloads", handler.pause_downloads)
        gw.register("resume_downloads", handler.resume_downloads)
        gw.register("cancel_downloads", handler.cancel_downloads)
