/**
 * SettingsPanel component for LJS.
 *
 * Owns the Compass view.  The layout groups settings by operational domain so
 * download/queue controls live together, media-selection preferences live
 * together, and integrations/AI credentials are clearly separated.
 */
class SettingsPanel extends Component {
    /**
     * Construct the Compass settings panel.
     *
     * @param {string} elementId - ID of the container element, normally
     *   ``settings``.
     * @param {EventBus} eventBus - Shared UI event bus for future settings
     *   refresh events.
     */
    constructor(elementId, eventBus) {
        super(elementId);
        this._eventBus = eventBus;
        this._settings = null;
        this._categories = [];
        this._personas = [];
        this._activePersona = null;
        this._llmModelCache = {};

        if (this.container) {
            this.render();
            this._init();
        }
    }

    /**
     * Render the Compass section using cohesive domain panels.
     *
     * Extension guidance: add new settings to the panel that owns the runtime
     * concept rather than appending another unrelated top-level card.  If a new
     * domain is truly needed, add one panel and one save method for that domain.
     */
    render() {
        this._clear();
        if (!this.container) return;

        const grid = DOM.el('div', { className: 'settings-grid compass-settings-grid' }, [
            this._buildAppPanel(),
            this._buildPersonaPanel(),
            this._buildDownloadsPanel(),
            this._buildSharingPanel(),
            this._buildContentPanel(),
            this._buildCategoryPanel(),
            this._buildStoragePanel(),
            this._buildServicesPanel(),
            this._buildLlmPanel(),
            this._buildSemanticMemoryPanel(),
            this._buildBridgesPanel(),
            this._buildManifestPanel()
        ]);

        Array.from(grid.querySelectorAll('.settings-panel')).forEach(panel => this._makeCollapsible(panel));
        this.container.appendChild(grid);
    }

    /**
     * Populate rendered controls from the most recent settings snapshot.
     */
    populateForm() {
        if (!this._settings) return;
        const defaultQuality = this._settings.default_quality || {};
        const mediaProfile = ((((this._settings.category_settings || {}).media || {}).download_profile) || {});
        const llm = this._settings.llm || {};

        this._setVal('pref-download-dir', this._settings.download_dir || '');
        this._setVal('pref-library-root', this._settings.library_root || './library');
        this._setVal('pref-max-concurrent', this._settings.max_concurrent_downloads || '3');
        this._setVal('pref-max-dl-speed', defaultQuality.max_download_speed_kbps || '');
        this._setVal('pref-max-ul-speed', defaultQuality.max_upload_speed_kbps || '');
        this._setCheck('pref-auto-start', !!this._settings.auto_start_at_login);
        this._setCheck('pref-auto-download', !!this._settings.auto_download);
        this._setCheck('pref-auto-discover', !!this._settings.auto_discover);
        this._setVal('pref-stall-interval', this._settings.stall_check_interval_minutes || '30');
        this._setVal('pref-stall-alt', this._settings.stall_alternative_hours || '1.0');
        this._setVal('pref-stall-cancel', this._settings.stall_cancel_hours || '5.0');

        const sharing = this._settings.sharing || {};
        this._setCheck('pref-sharing-enabled', !!sharing.enabled);
        this._setVal('pref-sharing-mode', sharing.mode || 'disabled');
        this._setVal('pref-sharing-upload-speed', sharing.library_upload_speed_kbps || '');
        this._setVal('pref-sharing-seed-slots', sharing.active_seed_slots || '2');
        this._setVal('pref-sharing-ratio', sharing.seed_ratio_target || '2.0');
        this._setVal('pref-sharing-duration', sharing.seed_duration_hours || '168');
        this._setCheck('pref-sharing-pause-when-downloading', !!sharing.pause_when_downloading);

        this._setVal('pref-size-limit-mode', mediaProfile.size_limit_mode || defaultQuality.size_limit_mode || 'smart');
        this._setVal('pref-max-bitrate', mediaProfile.max_bitrate_kbps || defaultQuality.max_bitrate_kbps || '');
        this._setVal('pref-max-file-size', mediaProfile.max_file_size_mb || defaultQuality.max_file_size_mb || '');
        this._setVal('pref-resolution', mediaProfile.preferred_resolution || defaultQuality.preferred_resolution || '1080p');
        this._setVal('pref-language', mediaProfile.language || this._settings.language || 'English');

        this._setVal('pref-llm-provider', llm.active_provider || 'openrouter');
        this._setVal('pref-llm-model', llm.model || '');
        this._setVal('pref-llm-api-base', llm.api_base || '');
        this._setVal('pref-llm-api-key', llm.api_key || '');
        this._setVal('pref-llm-lw-model', (llm.lightweight || {}).model || '');
        this._setVal('pref-llm-lw-provider', (llm.lightweight || {}).provider || '');
        this._setVal('pref-llm-std-model', (llm.standard || {}).model || '');
        this._setVal('pref-llm-std-provider', (llm.standard || {}).provider || '');
        this._setVal('pref-llm-hv-model', (llm.heavy || {}).model || '');
        this._setVal('pref-llm-hv-provider', (llm.heavy || {}).provider || '');
        this._syncLlmModelPickers(false);
        this._setVal('pref-llm-max-context', llm.max_context_tokens === null || llm.max_context_tokens === undefined ? '' : llm.max_context_tokens);
        this._setVal('pref-llm-context-budget-percent', llm.context_budget_percent || 85);
        this._setVal('pref-llm-raw-recent-percent', llm.raw_recent_context_percent === null || llm.raw_recent_context_percent === undefined ? 30 : llm.raw_recent_context_percent);
        this._setVal('pref-llm-reserved-output', llm.reserved_output_tokens === null || llm.reserved_output_tokens === undefined ? '' : llm.reserved_output_tokens);
        this._syncLlmContextWindowControl(false);

        const embeddings = this._settings.embeddings || {};
        this._setCheck('pref-embeddings-enabled', embeddings.enabled !== false);
        this._setVal('pref-embeddings-provider', embeddings.provider || 'builtin');
        this._setVal('pref-embeddings-model', embeddings.builtin_model || 'sentence-transformers/all-MiniLM-L6-v2');
        this._setVal('pref-embeddings-cache', embeddings.cache_dir || './data/embedding_models');
        this._setCheck('pref-embeddings-auto-download', embeddings.auto_download !== false);
        this._setCheck('pref-embeddings-warmup', embeddings.warmup_on_startup !== false);

        this._setVal('pref-jackett-url', this._settings.jackett_url || '');
        this._setVal('pref-jackett-key', this._settings.jackett_api_key || '');
        this._setCheck('pref-direct-scraper-fallback', !!this._settings.direct_scraper_fallback);
        const webSearch = this._settings.web_search || {};
        this._setCheck('pref-web-search-enabled', webSearch.enabled !== false);
        this._setVal('pref-web-search-provider', webSearch.provider || 'searxng');
        this._setVal('pref-web-search-mode', webSearch.mode || 'managed');
        this._setVal('pref-web-search-base', webSearch.api_base || '');
        this._setVal('pref-web-search-key', webSearch.api_key || '');
        this._setVal('pref-web-search-language', webSearch.default_language || 'auto');
        this._setVal('pref-web-search-categories', (webSearch.default_categories || ['general']).join('\n'));
        this._setVal('pref-web-search-safe', webSearch.safe_search === undefined ? '1' : webSearch.safe_search);
        this._setVal('pref-web-search-timeout', webSearch.request_timeout_seconds || '8');
        this._setVal('pref-web-search-source-ref', webSearch.managed_source_ref || 'master');
        this._setCheck('pref-web-search-duckduckgo-fallback', !!webSearch.allow_duckduckgo_fallback);
        this._updateSearxngStatus({ status: webSearch.status || 'not_installed', error: webSearch.status_message || '', url: webSearch.api_base || '' }, { silent: true });
        const soulseek = this._settings.soulseek || {};
        this._setCheck('pref-soulseek-enabled', !!soulseek.enabled);
        this._setVal('pref-soulseek-host', soulseek.host || 'http://127.0.0.1:5030');
        this._setVal('pref-soulseek-api-key', soulseek.api_key || '');
        this._setVal('pref-soulseek-username', soulseek.soulseek_username || '');
        this._setVal('pref-soulseek-password', soulseek.soulseek_password || '');
        this._setVal('pref-soulseek-share-mode', soulseek.share_mode || 'full_library');
        this._setCheck('pref-soulseek-parallel', soulseek.parallel_search_enabled !== false);
        this._setVal('pref-soulseek-download-preference', soulseek.download_preference || 'torrent_first');
        this._setCheck('pref-soulseek-auto-retry', soulseek.auto_retry_unmatched_searches !== false);
        this._setVal('pref-soulseek-retry-interval', soulseek.retry_search_interval_minutes || 360);
        this._setVal('pref-soulseek-retry-max-runs', soulseek.retry_search_max_runs || 12);
        this._setVal('pref-soulseek-categories', (soulseek.search_enabled_categories || ['music','audiobooks','ebooks','tv','movie','general']).join('\n'));
        this._setVal('pref-soulseek-shares', (soulseek.share_directories || []).join('\n'));
        this._setVal('pref-soulseek-exclusions', (soulseek.excluded_share_directories || []).join('\n'));
        this._updateSoulseekLoginStatus({
            status: soulseek.account_status || 'not_checked',
            ready: soulseek.account_status === 'ready',
            account_status: soulseek.account_status || 'not_checked',
            account_status_message: soulseek.account_status_message || '',
            account_checked_at: soulseek.account_checked_at || '',
            credentials_configured: !!(soulseek.soulseek_username && soulseek.soulseek_password)
        }, { silent: true });
        const mediaServices = (((this._settings.category_settings || {}).media || {}).services || {});
        this._setVal('pref-tmdb-key', ((mediaServices.tmdb || {}).api_key) || '');
        this._setVal('pref-opensubtitles-key', ((mediaServices.opensubtitles || {}).api_key) || '');
        this._setVal('pref-plex-url', ((mediaServices.plex || {}).url) || '');
        this._setVal('pref-plex-token', ((mediaServices.plex || {}).token) || '');
        this._setVal('pref-trakt-custom-id', ((mediaServices.trakt || {}).client_id) || '');
        const traktIdEl = document.getElementById('pref-trakt-id');
        if (traktIdEl) traktIdEl.value = ((mediaServices.trakt || {}).client_id) || "";
        this._renderTraktStatus();

        this._setVal('pref-discord-token', this._settings.discord_token || '');
        this._setVal('pref-discord-channel', this._settings.discord_channel_id || '');
        this._setVal('pref-telegram-token', this._settings.telegram_token || '');
        this._setVal('pref-whatsapp-token', this._settings.whatsapp_token || '');
        this._setVal('pref-whatsapp-phone', this._settings.whatsapp_phone_number_id || '');
        this._setVal('pref-whatsapp-verify', this._settings.whatsapp_verify_token || '');

        this._setVal('pref-active-persona', (this._activePersona || {}).id || this._settings.active_persona || 'default');
        this._renderPersonaPreview((this._activePersona || {}).id || this._settings.active_persona || 'default');

        this._populateCategoryControls();
        this._populateCategoryProviderControls();
        this._populateCategoryDownloadProfileControls();
        this._populateCategoryNestedControls();
        this._populateCategoryServiceControls();
    }

    /**
     * Save the launch-at-login preference.
     */
    async saveStartup() {
        const enabled = !!(document.getElementById('pref-auto-start') || {}).checked;
        try {
            const result = await APIClient.post('/api/settings/startup', { enabled });
            const status = result.autostart || {};
            if (result.status === 'warning') {
                toast.show(status.message || 'Auto-start could not be changed on this system.', 'err');
            } else {
                toast.show(enabled ? 'LJS will start automatically at login.' : 'LJS will no longer start automatically at login.');
            }
            this._setCheck('pref-auto-start', !!result.auto_start_at_login);
        } catch (err) {
            toast.error(err.message);
        }
    }

    /**
     * Save the active persona package and refresh the visible app chrome.
     */
    async savePersona() {
        const personaId = this._valueById('pref-active-persona', 'default');
        try {
            const result = await APIClient.post('/api/personas/active', { persona_id: personaId });
            this._activePersona = result.active || this._personas.find(p => p.id === result.active_persona) || { id: result.active_persona };
            toast.show(`Persona switched to ${(this._activePersona || {}).display_name || result.active_persona}.`);
            this._renderPersonaPreview((this._activePersona || {}).id || result.active_persona);
            if (window.appDeck && typeof window.appDeck._applyPersonaChrome === 'function') {
                window.appDeck._applyPersonaChrome(this._activePersona);
            }
        } catch (err) {
            toast.error(err.message);
        }
    }

    /**
     * Save global download, queue, speed, automation, and stall controls.
     */
    async saveDownloadQueue() {
        const downloadDir = this._input('pref-download-dir');
        const maxConcurrent = this._input('pref-max-concurrent');
        const libraryRoot = this._input('pref-library-root');
        const autoDownload = document.getElementById('pref-auto-download');
        const autoDiscover = document.getElementById('pref-auto-discover');

        try {
            await APIClient.post('/api/settings/library', {
                download_dir: downloadDir ? downloadDir.value : '',
                library_root: libraryRoot ? libraryRoot.value : './library',
                max_concurrent: this._intValue(maxConcurrent, 3),
                stall_check_interval_minutes: this._intById('pref-stall-interval', 30),
                stall_alternative_hours: this._floatById('pref-stall-alt', 1.0),
                stall_cancel_hours: this._floatById('pref-stall-cancel', 5.0)
            });
            await APIClient.post('/api/settings/auto_download', {
                auto_download: autoDownload ? autoDownload.checked : false,
                auto_discover: autoDiscover ? autoDiscover.checked : false
            });
            await APIClient.post('/api/settings', {
                default_quality: {
                    max_download_speed_kbps: this._intOrNullById('pref-max-dl-speed'),
                    max_upload_speed_kbps: this._intOrNullById('pref-max-ul-speed')
                }
            });
            toast.show('Download, queue, and bandwidth controls saved.');
        } catch (err) {
            toast.error(err.message);
        }
    }

    /**
     * Save seed-in-place library sharing preferences.
     */
    async saveSharing() {
        const enabled = document.getElementById('pref-sharing-enabled');
        const sharingEnabled = enabled ? enabled.checked : false;
        try {
            await APIClient.post('/api/settings/sharing', {
                enabled: sharingEnabled,
                mode: sharingEnabled ? this._valueById('pref-sharing-mode', 'seed_in_place') : 'disabled',
                library_upload_speed_kbps: this._intOrZeroById('pref-sharing-upload-speed'),
                active_seed_slots: this._intById('pref-sharing-seed-slots', 2),
                seed_ratio_target: this._floatById('pref-sharing-ratio', 2.0),
                seed_duration_hours: this._intById('pref-sharing-duration', 168),
                pause_when_downloading: !!(document.getElementById('pref-sharing-pause-when-downloading') || {}).checked
            });
            toast.show('Library sharing policy saved.');
            if (window.sharingPanel) window.sharingPanel.load();
        } catch (err) {
            toast.error(err.message);
        }
    }

    /**
     * Save shared media download-profile preferences.
     *
     * These are category preferences, not global UI settings: TV Shows and
     * Movies inherit them from the abstract ``media`` category unless they
     * override the values in their own private category config.
     */
    async saveContentPreferences() {
        try {
            await APIClient.post('/api/settings/library', {
                category_settings: {
                    media: {
                        download_profile: {
                            size_limit_mode: this._valueById('pref-size-limit-mode', 'smart'),
                            max_bitrate_kbps: this._intOrNullById('pref-max-bitrate'),
                            max_file_size_mb: this._intOrNullById('pref-max-file-size'),
                            preferred_resolution: this._valueById('pref-resolution', '1080p'),
                            language: this._valueById('pref-language', 'English')
                        }
                    }
                }
            });
            toast.show('Shared Media category content preferences saved.');
        } catch (err) {
            toast.error(err.message);
        }
    }

    /**
     * Save category-owned library and workflow configuration values.
     */
    async saveCategorySettings() {
        const categorySettings = {};
        document.querySelectorAll('.pref-category-prop-input').forEach(input => {
            const catId = input.dataset.categoryId;
            const propName = input.dataset.propertyName;
            const type = input.dataset.valueType;
            if (!catId || !propName) return;
            if (!categorySettings[catId]) categorySettings[catId] = {};
            categorySettings[catId][propName] = this._coerceCategoryValue(input, type);
        });
        document.querySelectorAll('.pref-category-provider-input').forEach(input => {
            const catId = input.dataset.categoryId;
            const provider = input.dataset.providerName;
            if (!catId || !provider) return;
            if (!categorySettings[catId]) categorySettings[catId] = {};
            if (!categorySettings[catId].metadata) categorySettings[catId].metadata = { providers: {} };
            if (!categorySettings[catId].metadata.providers) categorySettings[catId].metadata.providers = {};
            categorySettings[catId].metadata.providers[provider] = { enabled: !!input.checked };
        });
        document.querySelectorAll('.pref-category-download-profile-input').forEach(input => {
            const catId = input.dataset.categoryId;
            const prop = input.dataset.profileName;
            const type = input.dataset.valueType;
            if (!catId || !prop) return;
            if (!categorySettings[catId]) categorySettings[catId] = {};
            if (!categorySettings[catId].download_profile) categorySettings[catId].download_profile = {};
            categorySettings[catId].download_profile[prop] = this._coerceCategoryValue(input, type);
        });
        document.querySelectorAll('.pref-category-nested-input').forEach(input => {
            const catId = input.dataset.categoryId;
            const section = input.dataset.sectionName;
            const prop = input.dataset.propertyName;
            const type = input.dataset.valueType;
            if (!catId || !section || !prop) return;
            if (!categorySettings[catId]) categorySettings[catId] = {};
            if (!categorySettings[catId][section]) categorySettings[catId][section] = {};
            categorySettings[catId][section][prop] = this._coerceCategoryValue(input, type);
        });

        const serviceSettings = this._categoryServiceSettingsPayload();
        Object.entries(serviceSettings).forEach(([catId, values]) => {
            if (!categorySettings[catId]) categorySettings[catId] = {};
            categorySettings[catId].services = {
                ...(categorySettings[catId].services || {}),
                ...(values.services || {})
            };
        });

        try {
            await APIClient.post('/api/settings/library', { category_settings: categorySettings });
            toast.show('Category configuration and category services saved.');
        } catch (err) {
            toast.error(err.message);
        }
    }

    /**
     * Save AI provider and tier routing parameters.
     */
    async saveLLM() {
        try {
            await APIClient.post('/api/settings/llm', {
                provider: this._valueById('pref-llm-provider', 'openrouter'),
                model: this._valueById('pref-llm-model', ''),
                api_base: this._nullableValueById('pref-llm-api-base'),
                api_key: this._nullableValueById('pref-llm-api-key'),
                max_context_tokens: this._llmContextCapPayload(),
                context_budget_percent: this._intById('pref-llm-context-budget-percent', 85),
                raw_recent_context_percent: this._intById('pref-llm-raw-recent-percent', 30),
                reserved_output_tokens: this._nonNegativeIntOrNullById('pref-llm-reserved-output')
            });
            await APIClient.post('/api/settings/tiers', {
                lightweight: {
                    model: this._nullableValueById('pref-llm-lw-model'),
                    provider: this._nullableValueById('pref-llm-lw-provider')
                },
                standard: {
                    model: this._nullableValueById('pref-llm-std-model'),
                    provider: this._nullableValueById('pref-llm-std-provider')
                },
                heavy: {
                    model: this._nullableValueById('pref-llm-hv-model'),
                    provider: this._nullableValueById('pref-llm-hv-provider')
                }
            });
            toast.show('AI Gateway configuration saved.');
        } catch (err) {
            toast.error(err.message);
        }
    }


    /**
     * Save semantic-memory embedding runtime preferences.
     */
    async saveSemanticMemory() {
        try {
            await APIClient.post('/api/settings/embeddings', {
                enabled: !!(document.getElementById('pref-embeddings-enabled') || {}).checked,
                provider: this._valueById('pref-embeddings-provider', 'builtin'),
                builtin_model: this._valueById('pref-embeddings-model', 'sentence-transformers/all-MiniLM-L6-v2'),
                cache_dir: this._valueById('pref-embeddings-cache', './data/embedding_models'),
                dimension: 384,
                auto_download: !!(document.getElementById('pref-embeddings-auto-download') || {}).checked,
                warmup_on_startup: !!(document.getElementById('pref-embeddings-warmup') || {}).checked,
                max_model_size_mb: 150
            });
            toast.show('Semantic memory settings saved. Restart LJS to swap embedding runtime cleanly.');
        } catch (err) {
            toast.error(err.message);
        }
    }


    /**
     * Load semantic-memory health into the Compass diagnostics row.
     */
    async loadSemanticMemoryHealth() {
        const container = document.getElementById('semantic-memory-health');
        if (!container) return;
        try {
            const data = await APIClient.get('/api/settings/embeddings/status');
            const health = data.health || {};
            const semantic = health.semantic === true;
            const status = health.status || data.status || 'unknown';
            const namespace = health.namespace || health.vector_namespace || 'not initialized';
            const provider = health.provider || health.provider_label || 'unknown';
            const dimension = health.dimension || health.vector_dimension || '—';
            const lastError = health.last_error || health.error || '';
            container.innerHTML = '';
            container.appendChild(DOM.el('div', { className: 'semantic-health-grid' }, [
                DOM.el('span', { className: `badge ${semantic ? 'success' : 'highlight'}` }, [semantic ? 'Semantic' : 'Fallback / degraded']),
                DOM.el('span', {}, [`Status: ${status}`]),
                DOM.el('span', {}, [`Provider: ${provider}`]),
                DOM.el('span', {}, [`Dimension: ${dimension}`]),
                DOM.el('span', {}, [`Namespace: ${namespace}`])
            ]));
            if (lastError) {
                container.appendChild(DOM.el('p', { className: 'empty-msg' }, [`Last error: ${lastError}`]));
            }
        } catch (err) {
            container.innerHTML = '';
            container.appendChild(DOM.el('p', { className: 'empty-msg' }, [`Semantic memory status unavailable: ${err.message}`]));
        }
    }

    /**
     * Request a conversation-vector rebuild for the active namespace.
     */
    async reindexSemanticMemory() {
        try {
            const result = await APIClient.post('/api/settings/embeddings/reindex', { limit: 10000, mode: 'all' });
            const count = (result.result || {}).reindexed || (result.result || {}).count || 0;
            toast.show(`Semantic memory reindex requested. Rebuilt ${count} vector(s) across conversations and taste signals.`);
            await this.loadSemanticMemoryHealth();
        } catch (err) {
            toast.error(err.message);
        }
    }

    /**
     * Load Jackett indexer coverage diagnostics into the services panel.
     */
    async loadJackettIndexers() {
        const container = document.getElementById('jackett-indexer-health');
        if (!container) return;
        try {
            const data = await APIClient.get('/api/jackett/indexers');
            const summary = data.summary || {};
            const configured = summary.configured_indexers || 0;
            const total = summary.total_indexers || 0;
            const openPublic = summary.public_like_count || (data.open_public_recommended || []).length || 0;
            const bookConfigured = summary.book_or_audio_like_configured || 0;
            const bookTotal = summary.book_or_audio_like_count || 0;
            container.innerHTML = '';
            container.appendChild(DOM.el('div', { className: 'semantic-health-grid' }, [
                DOM.el('span', { className: 'badge success' }, [`${configured}/${total} configured`]),
                DOM.el('span', {}, [`Open/public available: ${openPublic}`]),
                DOM.el('span', {}, [`Book/audio coverage: ${bookConfigured}/${bookTotal}`]),
                DOM.el('span', {}, [`Dynamic profile: all_open_public`])
            ]));
            container.appendChild(DOM.el('p', { className: 'empty-msg' }, [summary.note || 'Jackett /all searches every configured indexer.']));
        } catch (err) {
            container.innerHTML = '';
            container.appendChild(DOM.el('p', { className: 'empty-msg' }, [`Jackett diagnostics unavailable: ${err.message}`]));
        }
    }

    /**
     * Configure a Jackett indexer profile and refresh diagnostics.
     */
    async configureJackettProfile(profile) {
        try {
            const result = await APIClient.post('/api/jackett/configure-indexers', { profile });
            toast.show(`Jackett profile ${profile}: added ${result.added || 0}, skipped ${result.skipped || 0}, failed ${result.failed || 0}.`);
            await this.loadJackettIndexers();
        } catch (err) {
            toast.error(err.message);
        }
    }

    /**
     * Open the native Jackett dashboard for full tracker-specific controls.
     */
    openJackettUi() {
        const url = this._valueById('pref-jackett-url', 'http://localhost:9117') || 'http://localhost:9117';
        window.open(url.replace(/\/$/, '') + '/UI/Dashboard', '_blank', 'noopener,noreferrer');
    }

    /**
     * Load Jackett's native config schema for a private/closed indexer.
     */
    async loadJackettCustomIndexerSchema() {
        const id = this._valueById('jackett-custom-indexer-id', '').trim();
        const container = document.getElementById('jackett-custom-indexer-fields');
        if (!id || !container) return;
        try {
            const data = await APIClient.get(`/api/jackett/indexers/${encodeURIComponent(id)}/config`);
            container.innerHTML = '';
            (data.fields || []).forEach(field => {
                const inputType = field.secret ? 'text' : (field.type === 'checkbox' ? 'checkbox' : 'text');
                const input = DOM.el('input', {
                    type: inputType,
                    className: field.secret ? 'ljs-secret-input jackett-custom-field' : 'jackett-custom-field',
                    autocomplete: 'off',
                    'data-lpignore': 'true',
                    'data-1p-ignore': 'true',
                    'data-bwignore': 'true',
                    dataset: { fieldId: field.id },
                    placeholder: field.name || field.id,
                    value: field.value || ''
                });
                container.appendChild(this._createSettingItem(field.name || field.id, field.help || `Jackett field: ${field.id}`, input));
            });
            if (!(data.fields || []).length) container.appendChild(DOM.el('p', { className: 'empty-msg' }, ['No configurable fields returned by Jackett.']));
        } catch (err) {
            toast.error(err.message);
        }
    }

    /**
     * Configure a user-selected Jackett indexer from the rendered schema fields.
     */
    async configureJackettCustomIndexer() {
        const id = this._valueById('jackett-custom-indexer-id', '').trim();
        if (!id) return toast.error('Enter a Jackett indexer ID first.');
        const values = {};
        document.querySelectorAll('#jackett-custom-indexer-fields .jackett-custom-field').forEach(input => {
            const key = input.dataset.fieldId;
            if (!key) return;
            values[key] = input.type === 'checkbox' ? input.checked : input.value;
        });
        try {
            const result = await APIClient.post(`/api/jackett/indexers/${encodeURIComponent(id)}/configure`, { values });
            if (result.status === 'ok' || result.configured) toast.show(`Configured Jackett indexer ${id}.`);
            else toast.error(result.error || `Jackett could not configure ${id}.`);
            await this.loadJackettIndexers();
        } catch (err) {
            toast.error(err.message);
        }
    }


    /**
     * Collect general web research controls into backend settings shape.
     * @private
     */
    _collectWebSearchPayload() {
        const lines = ((document.getElementById('pref-web-search-categories') || {}).value || '')
            .split(/\r?\n/)
            .map(v => v.trim())
            .filter(Boolean);
        const provider = (document.getElementById('pref-web-search-provider') || {}).value || 'searxng';
        const mode = (document.getElementById('pref-web-search-mode') || {}).value || (provider === 'searxng' ? 'managed' : 'manual');
        return {
            enabled: !!(document.getElementById('pref-web-search-enabled') || {}).checked,
            provider: provider,
            mode: mode,
            auto_install: provider === 'searxng' && mode === 'managed',
            api_base: this._nullableValueById('pref-web-search-base') || '',
            api_key: this._nullableValueById('pref-web-search-key') || '',
            default_language: this._nullableValueById('pref-web-search-language') || 'auto',
            default_categories: lines.length ? lines : ['general'],
            safe_search: parseInt((document.getElementById('pref-web-search-safe') || {}).value || '1', 10),
            request_timeout_seconds: parseFloat((document.getElementById('pref-web-search-timeout') || {}).value || '8') || 8,
            managed_source_ref: this._nullableValueById('pref-web-search-source-ref') || 'master',
            max_results: 5,
            allow_duckduckgo_fallback: !!(document.getElementById('pref-web-search-duckduckgo-fallback') || {}).checked
        };
    }

    /**
     * Install and configure the managed local SearXNG sidecar from Compass.
     */
    async installSearxng() {
        const provider = document.getElementById('pref-web-search-provider');
        const mode = document.getElementById('pref-web-search-mode');
        if (provider) provider.value = 'searxng';
        if (mode) mode.value = 'managed';
        this._updateSearxngStatus({ status: 'installing', error: 'Installing managed local SearXNG...' });
        try {
            await APIClient.post('/api/settings/search', { web_search: this._collectWebSearchPayload() });
            const result = await APIClient.post('/api/searxng/install', {});
            if (result.url) this._setVal('pref-web-search-base', result.url);
            this._updateSearxngStatus(result);
            if (result.ready || result.status === 'ready') toast.show('Managed SearXNG is installed and ready for web research.');
            else toast.error(result.error || 'SearXNG install finished but health is not ready.');
        } catch (err) {
            this._updateSearxngStatus({ status: 'error', error: err.message || 'SearXNG install failed.' });
            toast.error(err.message || 'SearXNG install failed.');
        }
    }


    /**
     * Upgrade/reinstall managed SearXNG while keeping a rollback backup.
     */
    async upgradeSearxng() {
        this._updateSearxngStatus({ status: 'checking', error: 'Upgrading managed local SearXNG...' });
        try {
            await APIClient.post('/api/settings/search', { web_search: this._collectWebSearchPayload() });
            const result = await APIClient.post('/api/searxng/upgrade', {});
            if (result.url) this._setVal('pref-web-search-base', result.url);
            this._updateSearxngStatus(result);
            if (result.ready || result.status === 'ready') toast.show('Managed SearXNG upgrade completed.');
            else toast.error(result.error || 'SearXNG upgrade did not finish cleanly.');
        } catch (err) {
            this._updateSearxngStatus({ status: 'error', error: err.message || 'SearXNG upgrade failed.' });
            toast.error(err.message || 'SearXNG upgrade failed.');
        }
    }

    /**
     * Roll back managed SearXNG to the most recent LJS-owned backup.
     */
    async rollbackSearxng() {
        this._updateSearxngStatus({ status: 'checking', error: 'Rolling back managed local SearXNG...' });
        try {
            const result = await APIClient.post('/api/searxng/rollback', {});
            if (result.url) this._setVal('pref-web-search-base', result.url);
            this._updateSearxngStatus(result);
            if (result.ready || result.status === 'ready') toast.show('Managed SearXNG rollback completed.');
            else toast.error(result.error || 'SearXNG rollback did not finish cleanly.');
        } catch (err) {
            this._updateSearxngStatus({ status: 'error', error: err.message || 'SearXNG rollback failed.' });
            toast.error(err.message || 'SearXNG rollback failed.');
        }
    }

    /**
     * Test the configured web-research provider.
     */
    async testWebSearchProvider() {
        this._updateSearxngStatus({ status: 'checking', error: 'Checking web research provider...' });
        try {
            await APIClient.post('/api/settings/search', { web_search: this._collectWebSearchPayload() });
            const result = await APIClient.get('/api/web-search/health');
            this._updateSearxngStatus(result);
            if (result.ok || result.json_api) toast.show('Web research provider health check passed.');
            else toast.error(result.last_error || result.error || 'Web research provider is not ready.');
        } catch (err) {
            this._updateSearxngStatus({ status: 'error', error: err.message || 'Web research provider check failed.' });
            toast.error(err.message || 'Web research provider check failed.');
        }
    }

    /**
     * Render SearXNG/web research status in Compass.
     * @private
     */
    _updateSearxngStatus(state, opts = {}) {
        const el = document.getElementById('pref-searxng-status');
        if (!el || !state) return;
        const status = state.status || (state.ok || state.ready ? 'ready' : 'not_installed');
        const message = state.error || state.last_error || state.status_message || (state.url ? `Endpoint: ${state.url}` : 'Managed SearXNG not installed yet.');
        el.textContent = `${this._humanizeProviderName(status)}: ${message}`;
        el.className = `setting-status ${(state.ok || state.ready || state.json_api || status === 'ready') ? 'success' : (status === 'checking' || status === 'installing' ? 'checking' : (status === 'not_installed' ? 'neutral' : 'danger'))}`;
    }

    /**
     * Collect Soulseek controls into the backend settings shape.
     * @private
     */
    _collectSoulseekPayload(forceEnabled = false) {
        const linesFrom = (id) => ((document.getElementById(id) || {}).value || '')
            .split(/\r?\n/)
            .map(v => v.trim())
            .filter(Boolean);
        return {
            enabled: forceEnabled || !!(document.getElementById('pref-soulseek-enabled') || {}).checked,
            managed: true,
            auto_install: true,
            host: this._nullableValueById('pref-soulseek-host') || 'http://127.0.0.1:5030',
            api_key: this._nullableValueById('pref-soulseek-api-key') || '',
            soulseek_username: this._nullableValueById('pref-soulseek-username') || '',
            soulseek_password: this._nullableValueById('pref-soulseek-password') || '',
            share_mode: (document.getElementById('pref-soulseek-share-mode') || {}).value || 'full_library',
            parallel_search_enabled: !!(document.getElementById('pref-soulseek-parallel') || {}).checked,
            download_preference: (document.getElementById('pref-soulseek-download-preference') || {}).value || 'torrent_first',
            auto_retry_unmatched_searches: !!(document.getElementById('pref-soulseek-auto-retry') || {}).checked,
            retry_search_interval_minutes: parseInt((document.getElementById('pref-soulseek-retry-interval') || {}).value || '360', 10) || 360,
            retry_search_max_runs: parseInt((document.getElementById('pref-soulseek-retry-max-runs') || {}).value || '12', 10) || 12,
            search_enabled_categories: linesFrom('pref-soulseek-categories'),
            share_directories: linesFrom('pref-soulseek-shares'),
            excluded_share_directories: linesFrom('pref-soulseek-exclusions')
        };
    }

    /**
     * Immediate Soulseek login verification from Compass.
     */
    async checkSoulseekLogin() {
        const enabledEl = document.getElementById('pref-soulseek-enabled');
        if (enabledEl) enabledEl.checked = true;
        const payload = this._collectSoulseekPayload(true);
        const statusEl = document.getElementById('pref-soulseek-login-status');
        if (!payload.soulseek_username || !payload.soulseek_password) {
            const msg = 'Enter a Soulseek username and password, then press Check login again.';
            this._updateSoulseekLoginStatus({ status: 'needs_credentials', ready: false, error: msg, account_status_message: msg });
            toast.error(msg);
            return;
        }
        const btn = document.getElementById('pref-soulseek-check-login');
        if (btn) btn.disabled = true;
        if (statusEl) {
            statusEl.textContent = 'Installing/starting slskd and checking Soulseek login...';
            statusEl.className = 'soulseek-login-status setting-status checking';
        }
        try {
            const result = await APIClient.post('/api/soulseek/check-login', {
                soulseek: payload,
                timeout_seconds: 45
            });
            this._updateSoulseekLoginStatus(result);
            if (result.ready || result.status === 'ready') toast.show('Soulseek login verified. slskd is running and search is available.');
            else if (result.status === 'auth_failed') toast.error(result.error || 'Soulseek rejected these credentials. Change them and press Check login again.');
            else if (result.status === 'needs_credentials') toast.error(result.error || 'Soulseek username/password are required.');
            else if (result.error) toast.error(result.error);
            else toast.show(result.account_status_message || 'Soulseek login is still being checked. Press Check login again in a few seconds.');
        } catch (err) {
            const msg = err.message || 'Soulseek login check failed.';
            this._updateSoulseekLoginStatus({ status: 'error', ready: false, error: msg, account_status_message: msg });
            toast.error(msg);
        } finally {
            if (btn) btn.disabled = false;
        }
    }

    /**
     * Render Soulseek login status in the Settings panel.
     * @private
     */
    _updateSoulseekLoginStatus(state, opts = {}) {
        const el = document.getElementById('pref-soulseek-login-status');
        if (!el || !state) return;
        const status = state.status || state.account_status || 'not_checked';
        const ready = !!(state.ready || status === 'ready');
        const msg = state.error || state.account_status_message || (ready ? 'Soulseek login verified.' : 'Soulseek login not checked yet.');
        const checked = state.account_checked_at ? ` Last checked: ${String(state.account_checked_at).replace('T', ' ').slice(0, 19)}.` : '';
        const probe = state.search_probe_ok ? ' Search probe succeeded.' : '';
        el.textContent = ready
            ? `Ready: Soulseek login verified and usable.${probe}${checked}`
            : `${this._humanizeProviderName(status)}: ${msg}${checked}`;
        el.className = `soulseek-login-status setting-status ${ready ? 'success' : (status === 'checking' ? 'checking' : (status === 'not_checked' ? 'neutral' : 'danger'))}`;
    }

    /**
     * Save search provider and metadata integration credentials.
     */
    async saveServices() {
        try {
            const result = await APIClient.post('/api/settings/search', {
                jackett_url: this._nullableValueById('pref-jackett-url'),
                jackett_api_key: this._nullableValueById('pref-jackett-key'),
                direct_scraper_fallback: !!(document.getElementById('pref-direct-scraper-fallback') || {}).checked,
                web_search: this._collectWebSearchPayload(),
                soulseek: this._collectSoulseekPayload(false)
            });
            const sl = result && result.soulseek;
            if (sl) this._updateSoulseekLoginStatus(sl);
            if (sl && (sl.status === 'ready' || sl.ready)) toast.show('Shared search services saved. Soulseek/slskd is installed, running, and logged in.');
            else if (sl && sl.status === 'checking') toast.show(sl.account_status_message || 'Soulseek/slskd is running; login validation is still pending.');
            else if (sl && sl.status === 'needs_credentials') toast.error(sl.error || 'Soulseek needs username and password. Use an existing account, or try a new unique username/password.');
            else if (sl && (sl.status === 'auth_failed' || sl.status === 'error' || sl.error)) toast.error(sl.error || sl.account_status_message || 'Soulseek/slskd could not confirm login.');
            else if (sl && sl.status === 'disabled') toast.show('Shared search services saved. Soulseek/slskd disabled and stopped.');
            else toast.show('Shared search services saved.');
        } catch (err) {
            toast.error(err.message);
        }
    }

    /**
     * Load and render category-aware storage status.
     */
    async loadStorageStatus() {
        const container = document.getElementById('storage-volume-list');
        if (!container) return;
        try {
            const data = await APIClient.get('/api/storage/status');
            const volumes = data.volumes || [];
            container.innerHTML = '';
            if (!volumes.length) {
                container.appendChild(DOM.el('p', { className: 'empty-msg' }, ['No monitored storage roots are configured.']));
                return;
            }
            volumes.forEach(v => container.appendChild(this._renderStorageVolume(v)));
        } catch (err) {
            container.innerHTML = '';
            container.appendChild(DOM.el('p', { className: 'empty-msg' }, [`Storage status unavailable: ${err.message}`]));
        }
    }

    /**
     * Save chat bridge credentials.
     */
    async saveBridges() {
        try {
            await APIClient.post('/api/settings/bridges', {
                discord_token: this._nullableValueById('pref-discord-token'),
                discord_channel_id: this._nullableValueById('pref-discord-channel'),
                telegram_token: this._nullableValueById('pref-telegram-token'),
                whatsapp_token: this._nullableValueById('pref-whatsapp-token'),
                whatsapp_phone_number_id: this._nullableValueById('pref-whatsapp-phone'),
                whatsapp_verify_token: this._nullableValueById('pref-whatsapp-verify')
            });
            toast.show('Bridge and chatbot credentials saved.');
        } catch (err) {
            toast.error(err.message);
        }
    }

    /**
     * Backwards-compatible alias for older templates/tests.
     */
    async savePreferences() {
        await this.saveContentPreferences();
    }

    /**
     * Backwards-compatible alias for older templates/tests.
     */
    async saveLibrary() {
        await this.saveCategorySettings();
    }

    /**
     * Load settings from the backend and hydrate all panels.
     * @private
     */
    async _init() {
        try {
            const data = await APIClient.get('/api/settings');
            const personaData = await APIClient.get('/api/personas');
            this._settings = data.settings || {};
            this._categories = (data.categories || []).map(cat => ({ ...cat, id: cat.id || cat.category_id }));
            this._personas = personaData.personas || [];
            this._activePersona = personaData.active || null;
            this.render();
            this.populateForm();
            await this.loadStorageStatus();
            await this.loadSemanticMemoryHealth();
            await this.loadJackettIndexers();
            if (window.CategoryManifestPanel) window.categoryManifestPanel = new CategoryManifestPanel();
        } catch (err) {
            console.error('[SettingsPanel] Failed to retrieve system preferences:', err);
        }
    }

    /**
     * Build application-level startup controls.
     * @private
     */
    _buildAppPanel() {
        return this._panel('fa-solid fa-power-off', 'Application Startup', 'One simple boot option for always-on media boxes and home servers.', [
            this._createSettingItem(
                'Start LJS when I log in',
                'Creates a normal user-level startup entry on macOS, Windows, or Linux. Disable it here to remove that entry; no administrator permissions are required.',
                this._toggle('pref-auto-start')
            ),
            this._saveButton('Save Startup Option', 'fa-solid fa-circle-check', () => this.saveStartup())
        ], 'settings-startup-panel');
    }

    /**
     * Build persona package controls.
     * @private
     */
    _buildPersonaPanel() {
        const options = (this._personas || []).map(persona => DOM.el('option', { value: persona.id }, [persona.display_name || persona.id]));
        if (!options.length) options.push(DOM.el('option', { value: 'default' }, ['Long John Silver']));
        return this._panel('fa-solid fa-user-astronaut', 'Assistant Persona', 'Prompt, avatar, display name, and bounded theme colors are loaded from config/personas/<persona_id>/.', [
            this._createSettingItem('Active persona', 'Switches the assistant prompt package and applies its local avatar and theme.json colors to the interface.', DOM.el('select', { id: 'pref-active-persona', onchange: e => this._renderPersonaPreview(e.target.value) }, options)),
            DOM.el('div', { id: 'persona-package-preview', className: 'setting-item persona-package-preview' }, [
                DOM.el('p', { className: 'empty-msg' }, ['Persona package metadata will appear here.'])
            ]),
            this._saveButton('Save Persona', 'fa-solid fa-circle-check', () => this.savePersona())
        ], 'settings-persona-panel');
    }

    /**
     * Render selected persona metadata without reading raw prompt text into UI.
     * @private
     */
    _renderPersonaPreview(personaId) {
        const container = document.getElementById('persona-package-preview');
        if (!container) return;
        const persona = (this._personas || []).find(p => p.id === personaId) || this._activePersona || {};
        container.innerHTML = '';
        const avatar = DOM.el('div', { className: 'persona-preview-avatar' });
        if (persona.avatar_url) avatar.style.backgroundImage = `url('${persona.avatar_url}')`;
        const theme = persona.theme || {};
        const styles = theme.styles || theme;
        container.appendChild(DOM.el('div', { className: 'persona-preview-card' }, [
            avatar,
            DOM.el('div', { className: 'persona-preview-copy' }, [
                DOM.el('h4', {}, [persona.display_name || persona.id || 'Unknown persona']),
                DOM.el('p', {}, [persona.description || 'No description provided.']),
                DOM.el('p', { className: 'empty-msg' }, [
                    `Package: config/personas/${persona.id || personaId}/ · avatar: ${persona.avatar_filename || 'fallback'} · avatar shape: ${styles.avatar_shape || 'freeform'}`
                ])
            ])
        ]));
    }

    /**
     * Build the Downloads & Queue operational control panel.
     * @private
     */
    _buildDownloadsPanel() {
        return this._panel('fa-solid fa-download', 'Downloads & Queue', 'Active torrent behavior, queue concurrency, aggregate speed caps, and stall automation live together here.', [
            this._sectionTitle('Storage & active slots'),
            this._createSettingItem('Download staging folder', 'Temporary path where active torrent payloads are written before library filing.', DOM.el('input', { type: 'text', id: 'pref-download-dir', placeholder: '/home/downloads' })),
            this._createSettingItem('Main library root', 'Default parent folder for category libraries. Blank category paths use this root plus the category default folder, for example Music or Ebooks.', DOM.el('input', { type: 'text', id: 'pref-library-root', placeholder: '/mnt/media/LJS' })),
            this._createSettingItem('Max active downloads', 'Number of torrents allowed to actively transfer at once.', DOM.el('input', { type: 'number', id: 'pref-max-concurrent', min: '1', placeholder: '3' })),
            this._sectionTitle('Aggregate bandwidth caps'),
            this._createSettingItem('Download cap (kB/s)', 'Global session cap shared across all active torrents. Empty or 0 means unlimited.', DOM.el('input', { type: 'number', id: 'pref-max-dl-speed', min: '0', placeholder: '0 = unlimited' })),
            this._createSettingItem('Upload cap (kB/s)', 'Global upload cap shared across all torrents. A value of 50 means about 50 kB/s total, not per torrent.', DOM.el('input', { type: 'number', id: 'pref-max-ul-speed', min: '0', placeholder: '0 = unlimited' })),
            this._sectionTitle('Automation mode'),
            this._createSettingItem('Captain mode', 'Automatically start approved/newly discovered releases instead of only suggesting them.', this._toggle('pref-auto-download')),
            this._createSettingItem('Auto-discover library items', 'Let scans register discovered category items automatically.', this._toggle('pref-auto-discover')),
            this._sectionTitle('Stall handling'),
            this._createSettingItem('Stall check interval (min)', 'How often LJS evaluates stalled or unhealthy transfers.', DOM.el('input', { type: 'number', id: 'pref-stall-interval', min: '5', placeholder: '30' })),
            this._createSettingItem('Find alternative after (hrs)', 'How long a torrent may be stalled before LJS searches for a replacement candidate.', DOM.el('input', { type: 'number', id: 'pref-stall-alt', step: '0.5', min: '0.5', placeholder: '1.0' })),
            this._createSettingItem('Cancel warning after (hrs)', 'How long a torrent may remain stalled before LJS asks whether to cancel it.', DOM.el('input', { type: 'number', id: 'pref-stall-cancel', step: '0.5', min: '1.0', placeholder: '5.0' })),
            this._saveButton('Save Download & Queue Controls', 'fa-solid fa-circle-check', () => this.saveDownloadQueue())
        ], 'settings-downloads-panel');
    }

    /**
     * Build seed-in-place library sharing controls.
     * @private
     */
    _buildSharingPanel() {
        return this._panel('fa-solid fa-seedling', 'Sharing & Seeding', 'Opt-in seeding for completed torrent downloads that are already part of your library, with its own upload budget.', [
            this._sectionTitle('Library sharing mode'),
            this._createSettingItem('Keep completed library torrents available to others', 'When this is on, new torrent downloads can keep seeding after they appear in your library. Turn it off for a private consume-only library.', this._toggle('pref-sharing-enabled')),
            this._createSettingItem('How files are stored', 'Seed in place means LJS keeps the original torrent folder as the library copy. The file may keep its release-group name, but Plex/Jellyfin usually still recognize it from metadata.', DOM.el('select', { id: 'pref-sharing-mode' }, [
                DOM.el('option', { value: 'seed_in_place' }, ['Seed in place: share the library copy']),
                DOM.el('option', { value: 'disabled' }, ['Disabled'])
            ])),
            this._sectionTitle('Dedicated sharing quota'),
            this._createSettingItem('Library sharing upload cap (kB/s)', 'Total upload speed reserved for completed library items. This is separate from the upload cap used by torrents that are still downloading.', DOM.el('input', { type: 'number', id: 'pref-sharing-upload-speed', min: '0', placeholder: '0 = unlimited' })),
            this._createSettingItem('Active shared library items', 'Maximum number of completed library torrents allowed to upload at the same time.', DOM.el('input', { type: 'number', id: 'pref-sharing-seed-slots', min: '0', placeholder: '2' })),
            this._sectionTitle('Stop conditions'),
            this._createSettingItem('Share until ratio', 'Example: 2.0 means upload about twice as much as was downloaded before LJS may stop sharing the item.', DOM.el('input', { type: 'number', id: 'pref-sharing-ratio', step: '0.1', min: '0', placeholder: '2.0' })),
            this._createSettingItem('Minimum share time (hours)', 'Keep the item available for at least this long, even if the ratio target is reached earlier.', DOM.el('input', { type: 'number', id: 'pref-sharing-duration', min: '0', placeholder: '168' })),
            this._createSettingItem('Pause library sharing while downloads are active', 'Use this when your connection is small: completed library items will stop uploading while active downloads need upload bandwidth to trade pieces.', this._toggle('pref-sharing-pause-when-downloading')),
            this._saveButton('Save Sharing & Seeding', 'fa-solid fa-circle-check', () => this.saveSharing())
        ], 'settings-sharing-panel');
    }

    /**
     * Build media-quality and candidate-selection controls.
     * @private
     */
    _buildContentPanel() {
        return this._panel('fa-solid fa-filter', 'Content Selection', 'Shared Media category quality, size, resolution, and language preferences inherited by TV Shows and Movies.', [
            this._createSettingItem('Size limit mode', 'How the shared Media category constrains torrent size during candidate selection.', DOM.el('select', { id: 'pref-size-limit-mode' }, [
                DOM.el('option', { value: 'smart' }, ['Smart (LLM decides)']),
                DOM.el('option', { value: 'bitrate' }, ['Max bitrate']),
                DOM.el('option', { value: 'file_size' }, ['Max file size'])
            ])),
            this._createSettingItem('Max bitrate (kbps)', 'Optional shared Media category video bitrate ceiling.', DOM.el('input', { type: 'number', id: 'pref-max-bitrate', min: '0', placeholder: 'e.g. 8000' })),
            this._createSettingItem('Max file size (MB)', 'Optional shared Media category per-release size ceiling.', DOM.el('input', { type: 'number', id: 'pref-max-file-size', min: '0', placeholder: 'e.g. 4000' })),
            this._createSettingItem('Preferred resolution', 'Default Media category target resolution for search and ranking.', DOM.el('select', { id: 'pref-resolution' }, [
                DOM.el('option', { value: '2160p' }, ['4K / 2160p']),
                DOM.el('option', { value: '1080p' }, ['1080p']),
                DOM.el('option', { value: '720p' }, ['720p'])
            ])),
            this._createSettingItem('Preferred language', 'Default Media category audio language for discovery and ranking.', DOM.el('select', { id: 'pref-language' }, LANG_OPTIONS.map(lang => DOM.el('option', { value: lang }, [lang])))),
            this._saveButton('Save Content Preferences', 'fa-solid fa-circle-check', () => this.saveContentPreferences())
        ], 'settings-content-panel');
    }

    /**
     * Build category-specific library/workflow controls.
     * @private
     */
    _buildCategoryPanel() {
        const controls = [];
        const categories = this._categories || [];
        if (!categories.length) {
            controls.push(DOM.el('p', { className: 'empty-msg' }, ['Category settings will appear here after the registry loads.']));
        }
        if (categories.some(cat => (cat.setup_requirements || []).some(req => String(req.setting_key || '').includes('.services.trakt.client_id')))) {
            const mediaTrakt = (((((this._settings || {}).category_settings || {}).media || {}).services || {}).trakt || {}).client_id;
            controls.push(DOM.el('input', { type: 'hidden', id: 'pref-trakt-id', value: mediaTrakt || '' }));
        }
        controls.push(this._mediaDefaultsBlock());
        categories.forEach(cat => controls.push(this._categorySettingsBlock(cat)));
        controls.push(this._saveButton('Save Category Settings & Services', 'fa-solid fa-folder-tree', () => this.saveCategorySettings()));
        return this._panel('fa-solid fa-folder-open', 'Library Categories', 'Optional per-category path overrides, provider toggles, service credentials, naming, lifecycle cadence, and workflow options declared by each category manifest.', controls, 'settings-category-panel');
    }



    /**
     * Render the abstract Media base config shared by TV Shows and Movies.
     * @private
     */
    _mediaDefaultsBlock() {
        const services = [
            ['tmdb', 'api_key', 'TMDB API key', 'Shared canonical metadata/artwork key for TV Shows and Movies.', true, '••••••••'],
            ['trakt', 'client_id', 'Custom Trakt Client ID (optional)', 'Leave blank to use the bundled LJS Trakt app and its PIN/code login flow. Only set this for an advanced custom Trakt developer app.', false, 'Use bundled LJS app'],
            ['plex', 'url', 'Plex server URL', 'Optional shared Plex endpoint for media reconciliation.', false, 'http://localhost:32400'],
            ['plex', 'token', 'Plex token', 'Optional shared Plex token.', true, '••••••••'],
            ['opensubtitles', 'api_key', 'OpenSubtitles API key', 'Optional shared subtitle provider key.', true, '••••••••']
        ];
        const children = [
            DOM.el('p', { className: 'empty-msg' }, [
                'These local values live in ', DOM.el('code', {}, ['config/categories/media.yaml']),
                ' and are inherited by TV Shows and Movies. Shareable service/tool/LLM definitions live separately in ',
                DOM.el('code', {}, ['config/category-definitions/media.yaml']), '.'
            ])
        ];
        services.forEach(([serviceId, fieldKey, label, desc, secret, placeholder]) => {
            children.push(this._createSettingItem(label, desc, this._categoryServiceControl(
                { id: 'media', display_name: 'Media defaults' },
                { setting_key: `category_config.media.services.${serviceId}.${fieldKey}` },
                { label, description: desc, secret, placeholder, trakt: serviceId === 'trakt' && fieldKey === 'client_id', parsed: { categoryId: 'media', section: 'services', serviceId, fieldKey } }
            )));
        });
        return DOM.el('details', { className: 'settings-details category-settings-details category-root-details' }, [
            DOM.el('summary', {}, ['Media defaults · inherited by TV/Movies']),
            DOM.el('div', { className: 'category-settings-body' }, children)
        ]);
    }

    /**
     * Return manifest-driven setup notices for a category.
     * @private
     */
    _categorySetupNotices(cat) {
        const notices = [];
        const requirements = cat.setup_requirements || [];
        const missing = requirements.filter(req => req.required && !req.configured);
        missing.forEach(req => {
            notices.push(DOM.el('div', { className: 'setting-item category-setup-notice category-setup-warning' }, [
                DOM.el('label', {}, [`Configuration needed: ${req.label || req.id}`]),
                DOM.el('p', {}, [req.why_it_matters || req.description || 'This category needs a required setting before it can operate safely.']),
                req.help_url ? DOM.el('a', { href: req.help_url, target: '_blank', rel: 'noopener noreferrer', className: 'api-link' }, ['Service docs / signup ↗']) : DOM.el('span', {})
            ]));
        });
        if (cat.id === 'general') {
            notices.push(DOM.el('div', { className: 'setting-item category-setup-notice category-general-notice' }, [
                DOM.el('label', {}, ['General Files category']),
                DOM.el('p', {}, [
                    'Use this for exact one-off torrent targets such as documents, archives, datasets, manuals, or audio files. ',
                    'Review the library path below; richer categories such as TV and Movies still win when they match.'
                ])
            ]));
        }
        return notices;
    }



    /**
     * Build one manifest-driven category settings block.
     * @private
     */
    _categorySettingsBlock(cat) {
        const title = `${cat.display_name || cat.id} · ${cat.id}`;
        const groups = [];

        const basics = [];
        this._categorySetupNotices(cat).forEach(node => basics.push(node));
        if (cat.default_library_path) {
            basics.push(this._createSettingItem(
                'Default library folder',
                'Used when the category library path below is blank. The folder is created during setup/path-save when possible.',
                DOM.el('code', {}, [cat.default_library_path])
            ));
        }
        const properties = cat.properties || [];
        properties.forEach(prop => {
            const desc = prop.description || `Category property: ${prop.name}`;
            basics.push(this._createSettingItem(prop.label || prop.name, desc, this._categoryInput(cat, prop)));
        });
        if (basics.length) groups.push(this._settingsSubsection('Basics & private paths', basics, { open: true, indent: false }));

        const providerRows = this._categoryProviderRows(cat);
        if (providerRows.length) groups.push(this._settingsSubsection('Metadata providers', providerRows, { indent: true }));

        const downloadProfileRows = this._categoryDownloadProfileRows(cat);
        if (downloadProfileRows.length) groups.push(this._settingsSubsection('Download / conversion preferences', downloadProfileRows, { indent: true }));

        const nestedRows = this._categoryNestedConfigRows(cat);
        if (nestedRows.length) groups.push(this._settingsSubsection('Automation, storage & lifecycle', nestedRows, { indent: true }));

        const serviceRows = this._categoryServiceRows(cat);
        if (serviceRows.length) groups.push(this._settingsSubsection('Service credentials', serviceRows, { indent: true }));

        if (!groups.length) {
            groups.push(DOM.el('p', { className: 'empty-msg' }, ['This category has no user-editable settings yet.']));
        }

        return DOM.el('details', { className: 'settings-details category-settings-details category-root-details' }, [
            DOM.el('summary', {}, [title]),
            DOM.el('div', { className: 'category-settings-body category-settings-body-nested' }, groups)
        ]);
    }

    /**
     * Render category-owned provider enable/disable switches.
     * @private
     */
    _categoryProviderRows(cat) {
        const providers = cat.metadata_providers || [];
        return providers.map(providerName => {
            const provider = String(providerName || '').trim();
            const dataset = { categoryId: cat.id, providerName: provider };
            const toggle = DOM.el('label', { className: 'toggle-switch' }, [
                DOM.el('input', { type: 'checkbox', className: 'pref-category-provider-input', dataset }),
                DOM.el('span', { className: 'slider' })
            ]);
            return this._createSettingItem(
                this._humanizeProviderName(provider),
                `Enable ${provider} for ${cat.display_name || cat.id}. Saved as metadata.providers.${provider}.enabled in private config; provider meaning lives in the category definition.`,
                toggle
            );
        });
    }



    /**
     * Render category-owned download profile controls for non-video domains.
     * @private
     */
    _categoryDownloadProfileRows(cat) {
        const id = String(cat.id || '');
        const rows = [];
        if (id === 'music') {
            rows.push(this._createSettingItem(
                'Preferred lossless music format',
                'Controls whether FLAC is kept as the preferred lossless target or imported lossless files get Apple Lossless M4A sidecars.',
                this._categoryProfileSelect(cat, 'preferred_lossless_format', 'string', [
                    ['flac', 'FLAC / keep lossless source'],
                    ['alac_m4a', 'ALAC in .m4a for Apple Music/iOS']
                ])
            ));
            rows.push(this._createSettingItem(
                'Auto-convert lossless to lossy AAC',
                'When enabled and AAC M4A is the preferred portable format, only lossless sources are transcoded. MP3-to-AAC is intentionally not automatic.',
                this._categoryProfileToggle(cat, 'auto_convert_lossless_to_preferred')
            ));
        } else if (id === 'audiobooks') {
            rows.push(this._createSettingItem(
                'Preferred audiobook format',
                'M4B is best for Apple-style audiobook playback with chapters; MP3 remains useful for broad compatibility.',
                this._categoryProfileSelect(cat, 'preferred_audio_format', 'string', [
                    ['m4b', 'M4B / Apple-friendly chaptered audiobook'],
                    ['mp3', 'MP3 / broad compatibility'],
                    ['flac', 'FLAC / archival lossless']
                ])
            ));
            rows.push(this._createSettingItem(
                'Auto-convert lossless audiobook sources',
                'Creates an M4B/M4A sidecar from FLAC/WAV/AIFF audiobook sources while preserving the source and chapter metadata where available.',
                this._categoryProfileToggle(cat, 'auto_convert_lossless_to_preferred')
            ));
        } else if (id === 'ebooks') {
            rows.push(this._createSettingItem(
                'Preferred ebook format',
                'Used by search/ranking hints. LJS does not auto-convert ebooks yet because editions, DRM, scans, and layout semantics are too easy to damage.',
                this._categoryProfileSelect(cat, 'preferred_ebook_format', 'string', [
                    ['epub', 'EPUB / reflowable text'],
                    ['azw3', 'AZW3 / Kindle-oriented'],
                    ['pdf', 'PDF / fixed-layout scans']
                ])
            ));
        }
        return rows;
    }

    /**
     * Build a download-profile select control.
     * @private
     */
    _categoryProfileSelect(cat, profileName, valueType, options) {
        const dataset = { categoryId: cat.id, profileName, valueType };
        return DOM.el('select', { className: 'pref-category-download-profile-input', dataset },
            options.map(([value, label]) => DOM.el('option', { value }, [label]))
        );
    }

    /**
     * Build a download-profile toggle control.
     * @private
     */
    _categoryProfileToggle(cat, profileName) {
        const dataset = { categoryId: cat.id, profileName, valueType: 'bool' };
        return DOM.el('label', { className: 'toggle-switch' }, [
            DOM.el('input', { type: 'checkbox', className: 'pref-category-download-profile-input', dataset }),
            DOM.el('span', { className: 'slider' })
        ]);
    }

    /**
     * Render small editable controls for nested category config sections.
     * @private
     */
    _categoryNestedConfigRows(cat) {
        const rows = [];
        const catSettings = (((this._settings || {}).category_settings || {})[cat.id]) || {};
        const scheduler = catSettings.scheduler || null;
        if (scheduler && typeof scheduler === 'object') {
            rows.push(this._createSettingItem(
                'Scheduled category checks',
                'Enables this category in the background scheduler. Saved as scheduler.enabled in the private category config.',
                this._categoryNestedToggle(cat, 'scheduler', 'enabled', 'bool')
            ));
        }
        const storage = catSettings.storage || null;
        if (storage && typeof storage === 'object' && Object.prototype.hasOwnProperty.call(storage, 'inherit_global_thresholds')) {
            rows.push(this._createSettingItem(
                'Inherit global storage thresholds',
                'Uses the shared disk-space thresholds for this category root. Saved as storage.inherit_global_thresholds in private config.',
                this._categoryNestedToggle(cat, 'storage', 'inherit_global_thresholds', 'bool')
            ));
        }
        const lifecycle = catSettings.lifecycle_policy || null;
        if (lifecycle && typeof lifecycle === 'object') {
            const version = lifecycle.policy_version ? `Policy v${lifecycle.policy_version}` : 'Policy declared';
            rows.push(this._createSettingItem(
                'Lifecycle / suggestion policy',
                'Read-only summary from the tracked category definition. Personal config stores only toggles/preferences.',
                DOM.el('span', { className: 'badge' }, [version])
            ));
        }
        return rows;
    }

    /**
     * Build a nested category config toggle.
     * @private
     */
    _categoryNestedToggle(cat, sectionName, propertyName, valueType) {
        const dataset = { categoryId: cat.id, sectionName, propertyName, valueType };
        const id = `pref-cat-nested-${cat.id}-${sectionName}-${propertyName}`;
        return DOM.el('label', { className: 'toggle-switch' }, [
            DOM.el('input', { type: 'checkbox', id, className: 'pref-category-nested-input', dataset }),
            DOM.el('span', { className: 'slider' })
        ]);
    }

    /**
     * Render service credentials/toggles declared by a category manifest.
     * @private
     */
    _categoryServiceRows(cat) {
        const rows = [];
        const seen = new Set();
        (cat.setup_requirements || []).forEach(req => {
            const key = req.setting_key || req.id;
            if (!key) return;
            if (key === 'library_path') return;
            if (String(key).startsWith('category_config.media.')) return;
            const meta = this._serviceSettingMeta(key);
            if (meta) {
                const unique = `${cat.id}:${key}`;
                if (seen.has(unique)) return;
                seen.add(unique);
                rows.push(this._createSettingItem(
                    req.label || meta.label,
                    req.why_it_matters || req.description || meta.description,
                    this._categoryServiceControl(cat, req, meta)
                ));
                return;
            }
            rows.push(this._createSettingItem(
                req.label || key,
                req.why_it_matters || req.description || 'Declared by this category manifest.',
                this._requirementStatusBadge(req)
            ));
        });
        return rows;
    }

    /**
     * Known shared integration settings surfaced inside the categories that use them.
     * @private
     */
    _serviceSettingMeta(settingKey) {
        const parsed = this._parseCategorySettingKey(settingKey);
        const service = parsed.serviceId || '';
        const field = parsed.fieldKey || settingKey;
        const labelMap = {
            tmdb: 'TMDB', tvmaze: 'TVMaze', opensubtitles: 'OpenSubtitles', trakt: 'Trakt', plex: 'Plex',
            musicbrainz: 'MusicBrainz', cover_art_archive: 'Cover Art Archive', discogs: 'Discogs', acoustid: 'AcoustID',
            open_library: 'Open Library', internet_archive: 'Internet Archive', google_books: 'Google Books',
            librivox: 'LibriVox', gutendex: 'Gutendex', apple_itunes_search: 'Apple iTunes / Books', comic_vine: 'Comic Vine'
        };
        const fieldLabels = { api_key: 'API key', token: 'Token', url: 'URL', client_id: 'Client ID', enabled: 'Enabled' };
        const secret = /key|token|secret|password/i.test(field);
        const placeholders = {
            api_key: '••••••••',
            token: '••••••••',
            url: service === 'plex' ? 'http://localhost:32400' : '',
            client_id: service === 'trakt' ? 'Custom Trakt Client ID (optional)' : ''
        };
        if (parsed.categoryId && service && field) {
            return {
                label: `${labelMap[service] || this._humanizeProviderName(service)} ${fieldLabels[field] || this._humanizeProviderName(field)}`,
                description: `Saved in private category config as services.${service}.${field}; service meaning lives in the category definition.`,
                secret,
                placeholder: placeholders[field] || '',
                trakt: service === 'trakt' && field === 'client_id',
                parsed
            };
        }
        return null;
    }

    /**
     * Parse a manifest setting key like category_config.tv.services.tmdb.api_key.
     * @private
     */
    _parseCategorySettingKey(settingKey, fallbackCategoryId = null) {
        const text = String(settingKey || '');
        const parts = text.split('.');
        if (parts[0] === 'category_config' && parts.length >= 5) {
            return {
                categoryId: parts[1],
                section: parts[2],
                serviceId: parts[2] === 'services' ? parts[3] : null,
                fieldKey: parts.slice(4).join('.'),
                legacyKey: null
            };
        }
        return { categoryId: fallbackCategoryId, section: null, serviceId: null, fieldKey: null, legacyKey: null };
    }

    /**
     * Create a category service control.
     * @private
     */
    _categoryServiceControl(cat, req, meta) {
        const settingKey = req.setting_key || req.id;
        const parsed = meta.parsed || this._parseCategorySettingKey(settingKey, cat.id);
        const id = `pref-cat-service-${parsed.categoryId || cat.id}-${parsed.serviceId || 'legacy'}-${parsed.fieldKey || settingKey}`.replace(/[^A-Za-z0-9_-]/g, '-');
        const dataset = {
            settingKey,
            categoryId: parsed.categoryId || cat.id,
            serviceId: parsed.serviceId || '',
            serviceKey: parsed.fieldKey || '',
            legacyKey: parsed.legacyKey || ''
        };
        if (meta.trakt) {
            return DOM.el('div', { className: 'trakt-control category-service-control' }, [
                DOM.el('div', { className: 'trakt-status-row' }, [
                    DOM.el('span', { className: 'badge pref-trakt-status' }, ['Not Connected']),
                    DOM.el('button', { className: 'btn btn-sm btn-secondary', type: 'button', onclick: () => this._startTraktAuth() }, ['Link Account'])
                ]),
                DOM.el('input', {
                    type: 'text',
                    id,
                    className: 'pref-category-service-input',
                    dataset,
                    placeholder: meta.placeholder,
                    autocomplete: 'off'
                })
            ]);
        }
        if (parsed.fieldKey === 'enabled') {
            return DOM.el('label', { className: 'toggle-switch' }, [
                DOM.el('input', { type: 'checkbox', id, className: 'pref-category-service-input', dataset }),
                DOM.el('span', { className: 'slider' })
            ]);
        }
        return DOM.el('input', {
            type: 'text',
            id,
            className: `pref-category-service-input${meta.secret ? ' ljs-secret-input' : ''}`,
            dataset,
            placeholder: meta.placeholder || '',
            autocomplete: 'off',
            'data-lpignore': meta.secret ? 'true' : undefined,
            'data-1p-ignore': meta.secret ? 'true' : undefined,
            'data-bwignore': meta.secret ? 'true' : undefined
        });
    }

    /**
     * Render a read-only setup requirement status.
     * @private
     */
    _requirementStatusBadge(req) {
        const configured = req.configured === true || req.required === false;
        const label = configured ? (req.required === false ? 'Optional' : 'Configured') : 'Needs attention';
        const cls = configured ? 'success' : 'danger';
        return DOM.el('span', { className: `badge ${cls}` }, [label]);
    }

    /**
     * Build integration payload from category service controls.
     * @private
     */
    _categoryServiceSettingsPayload() {
        const categorySettings = {};
        document.querySelectorAll('.pref-category-service-input').forEach(input => {
            const catId = input.dataset.categoryId;
            const serviceId = input.dataset.serviceId;
            const serviceKey = input.dataset.serviceKey;
            if (!catId || !serviceId || !serviceKey) return;
            if (!categorySettings[catId]) categorySettings[catId] = {};
            if (!categorySettings[catId].services) categorySettings[catId].services = {};
            if (!categorySettings[catId].services[serviceId]) categorySettings[catId].services[serviceId] = {};
            const value = input.type === 'checkbox' ? !!input.checked : (String(input.value || '').trim() || null);
            categorySettings[catId].services[serviceId][serviceKey] = value;
        });
        return categorySettings;
    }

    /**
     * Populate category provider toggles from category_settings.
     * @private
     */
    _populateCategoryProviderControls() {
        document.querySelectorAll('.pref-category-provider-input').forEach(input => {
            const catId = input.dataset.categoryId;
            const provider = input.dataset.providerName;
            const catSettings = ((this._settings || {}).category_settings || {})[catId] || {};
            const providerConfig = (((catSettings.metadata || {}).providers || {})[provider]) || null;
            input.checked = !(providerConfig && providerConfig.enabled === false);
        });
    }


    /**
     * Populate category-owned download profile controls.
     * @private
     */
    _populateCategoryDownloadProfileControls() {
        document.querySelectorAll('.pref-category-download-profile-input').forEach(input => {
            const catId = input.dataset.categoryId;
            const prop = input.dataset.profileName;
            const catSettings = ((this._settings || {}).category_settings || {})[catId] || {};
            const profile = catSettings.download_profile || {};
            const value = profile[prop];
            if (input.type === 'checkbox') input.checked = !!value;
            else if (value !== undefined && value !== null) input.value = value;
        });
    }

    /**
     * Populate nested category config controls.
     * @private
     */
    _populateCategoryNestedControls() {
        document.querySelectorAll('.pref-category-nested-input').forEach(input => {
            const catId = input.dataset.categoryId;
            const section = input.dataset.sectionName;
            const prop = input.dataset.propertyName;
            const catSettings = ((this._settings || {}).category_settings || {})[catId] || {};
            const sectionConfig = catSettings[section] || {};
            if (input.type === 'checkbox') input.checked = !!sectionConfig[prop];
            else input.value = sectionConfig[prop] !== undefined && sectionConfig[prop] !== null ? sectionConfig[prop] : '';
        });
    }

    /**
     * Populate category service credentials from shared integration settings.
     * @private
     */
    _populateCategoryServiceControls() {
                document.querySelectorAll('.pref-category-service-input').forEach(input => {
            const catId = input.dataset.categoryId;
            const serviceId = input.dataset.serviceId;
            const serviceKey = input.dataset.serviceKey;
            const legacyKey = input.dataset.legacyKey;
            const catSettings = ((this._settings || {}).category_settings || {})[catId] || {};
            const serviceConfig = (((catSettings.services || {})[serviceId]) || {});
            const value = serviceConfig[serviceKey] !== undefined && serviceConfig[serviceKey] !== null
                ? serviceConfig[serviceKey]
                : '';
            if (input.type === 'checkbox') input.checked = value !== false;
            else input.value = value || '';
        });
        const hidden = document.getElementById('pref-trakt-id');
        if (hidden) {
            const mediaTrakt = (((((this._settings || {}).category_settings || {}).media || {}).services || {}).trakt || {}).client_id;
            hidden.value = mediaTrakt || "";
        }
        this._renderTraktStatus();
    }

    /**
     * Keep duplicate shared service controls synchronized across categories.
     * @private
     */
    _syncSharedServiceInputs(settingKey, value) {
        document.querySelectorAll('.pref-category-service-input').forEach(input => {
            if (input.dataset.settingKey !== settingKey) return;
            if (input.value !== value) input.value = value;
        });
        if (String(settingKey || '').includes('services.trakt.client_id')) {
            const hidden = document.getElementById('pref-trakt-id');
            if (hidden) hidden.value = String(value || '').trim();
        }
    }

    /**
     * Humanize provider identifiers for labels.
     * @private
     */
    _humanizeProviderName(provider) {
        const known = { tmdb: 'TMDB', tvmaze: 'TVMaze', opensubtitles: 'OpenSubtitles', trakt: 'Trakt', plex: 'Plex' };
        return known[provider] || String(provider || '').replace(/[_-]+/g, ' ').replace(/\b\w/g, ch => ch.toUpperCase());
    }

    /**
     * Build storage monitoring panel.
     * @private
     */
    _buildStoragePanel() {
        return this._panel('fa-solid fa-hard-drive', 'Storage Watch', 'Disk-space status is grouped by physical drive and sent to the assistant before download planning.', [
            DOM.el('div', { id: 'storage-volume-list', className: 'storage-volume-list' }, [DOM.el('p', { className: 'empty-msg' }, ['Checking storage...'])]),
            this._saveButton('Refresh Storage Status', 'fa-solid fa-rotate', () => this.loadStorageStatus(), 'quick-btn btn-secondary')
        ], 'storage-monitor-panel');
    }

    /**
     * Build torrent search and metadata services panel.
     * @private
     */
    _buildServicesPanel() {
        const webResearch = [
            DOM.el('p', { className: 'empty-msg' }, ['Optional general web-research provider for category/item research, release-date news, rumours, ambiguity resolution, and metadata gaps. It discovers public sources only; downloadable release search remains Jackett/Soulseek/category-owned.']),
            this._createSettingItem('Enable web research', 'Allows the assistant and category extensions to discover public evidence sources. Disable to avoid all general web-search calls.', this._toggle('pref-web-search-enabled')),
            this._createSettingItem('Provider', 'Default is managed local SearXNG. API providers and manual SearXNG endpoints remain advanced options.', DOM.el('select', { id: 'pref-web-search-provider' }, [
                DOM.el('option', { value: 'searxng' }, ['SearXNG']),
                DOM.el('option', { value: 'brave' }, ['Brave Search API']),
                DOM.el('option', { value: 'tavily' }, ['Tavily']),
                DOM.el('option', { value: 'kagi' }, ['Kagi']),
                DOM.el('option', { value: 'duckduckgo_html' }, ['DuckDuckGo HTML fallback'])
            ])),
            this._createSettingItem('SearXNG mode', 'Managed installs into LJS-owned folders and refuses to adopt pre-existing system instances. Manual uses the URL below.', DOM.el('select', { id: 'pref-web-search-mode' }, [
                DOM.el('option', { value: 'managed' }, ['Automatic local SearXNG']),
                DOM.el('option', { value: 'manual' }, ['Manual/existing endpoint'])
            ])),
            this._createSettingItem('Base URL', 'Auto-filled for managed SearXNG; required for manual/external SearXNG or custom provider endpoints.', DOM.el('input', { type: 'text', id: 'pref-web-search-base', placeholder: 'http://127.0.0.1:18888' })),
            this._createSettingItem('API key', 'Only needed for API providers or protected manual endpoints.', DOM.el('input', { type: 'text', className: 'ljs-secret-input', autocomplete: 'off', 'data-lpignore': 'true', 'data-1p-ignore': 'true', 'data-bwignore': 'true', id: 'pref-web-search-key', placeholder: 'Optional' })),
            this._createSettingItem('Language', 'SearXNG language value, e.g. auto, all, it-IT, en-US.', DOM.el('input', { type: 'text', id: 'pref-web-search-language', placeholder: 'auto' })),
            this._createSettingItem('Categories', 'One SearXNG category per line. Start with general; news can be added for release/delay checks.', DOM.el('textarea', { id: 'pref-web-search-categories', rows: '3', placeholder: `general\nnews` })),
            this._createSettingItem('Safe search', 'SearXNG safe-search level: 0 off, 1 moderate, 2 strict.', DOM.el('select', { id: 'pref-web-search-safe' }, [
                DOM.el('option', { value: '0' }, ['0 — off']),
                DOM.el('option', { value: '1' }, ['1 — moderate']),
                DOM.el('option', { value: '2' }, ['2 — strict'])
            ])),
            this._createSettingItem('Request timeout seconds', 'Timeout for provider health/search calls. Keep bounded so agent research cannot stall the UI.', DOM.el('input', { type: 'number', id: 'pref-web-search-timeout', min: '1', max: '30', step: '1', placeholder: '8' })),
            this._createSettingItem('Managed SearXNG source ref', 'Advanced: SearXNG git/archive ref used by automatic install and upgrade. Keep master unless testing a pinned ref on a clean machine.', DOM.el('input', { type: 'text', id: 'pref-web-search-source-ref', placeholder: 'master' })),
            this._createSettingItem('DuckDuckGo degraded fallback', 'Advanced/off by default: only use DuckDuckGo HTML if the configured web-research provider fails. This is not used for media-download acquisition.', this._toggle('pref-web-search-duckduckgo-fallback')),
            DOM.el('div', { id: 'pref-searxng-status', className: 'setting-status neutral' }, ['Managed SearXNG not installed yet.']),
            DOM.el('div', { className: 'settings-button-row' }, [
                DOM.el('button', { type: 'button', className: 'quick-btn btn-secondary', onclick: () => this.installSearxng() }, [DOM.el('i', { className: 'fa-solid fa-magnifying-glass-location' }), ' Auto install/configure SearXNG']),
                DOM.el('button', { type: 'button', className: 'quick-btn btn-secondary', onclick: () => this.upgradeSearxng() }, [DOM.el('i', { className: 'fa-solid fa-arrow-up-from-bracket' }), ' Upgrade managed SearXNG']),
                DOM.el('button', { type: 'button', className: 'quick-btn btn-secondary', onclick: () => this.rollbackSearxng() }, [DOM.el('i', { className: 'fa-solid fa-clock-rotate-left' }), ' Roll back managed SearXNG']),
                DOM.el('button', { type: 'button', className: 'quick-btn btn-secondary', onclick: () => this.testWebSearchProvider() }, [DOM.el('i', { className: 'fa-solid fa-vial-circle-check' }), ' Test web research'])
            ])
        ];
        const torrentBackend = [
            this._createSettingItem('Jackett URL', 'Primary torrent indexer endpoint shared by downloadable categories.', DOM.el('input', { type: 'text', id: 'pref-jackett-url', placeholder: 'http://localhost:9117' })),
            this._createSettingItem('Jackett API key', 'API key for the configured Jackett server.', DOM.el('input', { type: 'text', className: 'ljs-secret-input', autocomplete: 'off', 'data-lpignore': 'true', 'data-1p-ignore': 'true', 'data-bwignore': 'true', id: 'pref-jackett-key', placeholder: '••••••••' })),
            this._createSettingItem('Direct scraper fallback', 'Advanced/off by default: use slower public scrapers only if Jackett returns no usable candidates or is unavailable. Keep disabled for normal Jackett/Soulseek flows.', this._toggle('pref-direct-scraper-fallback'))
        ];
        const soulseekRuntime = [
            DOM.el('p', { className: 'empty-msg' }, ['Optional managed Soulseek/slskd runtime. LJS installs, configures, starts, and stops slskd automatically. Users provide only Soulseek credentials and sharing/source preferences.']),
            this._createSettingItem('Enable Soulseek companion', 'Install/start managed slskd automatically and use it as a parallel companion source. It remains separate from torrent/magnet queueing.', this._toggle('pref-soulseek-enabled')),
            this._createSettingItem('Soulseek username', 'Soulseek network username. Existing accounts work; a new unique username/password may create an account if the network accepts it.', DOM.el('input', { type: 'text', id: 'pref-soulseek-username', autocomplete: 'off', placeholder: 'Soulseek username' })),
            this._createSettingItem('Soulseek password', 'Soulseek network password. LJS validates login after starting slskd and will not mark Soulseek ready if credentials are rejected.', DOM.el('input', { type: 'password', className: 'ljs-secret-input', autocomplete: 'off', 'data-lpignore': 'true', 'data-1p-ignore': 'true', 'data-bwignore': 'true', id: 'pref-soulseek-password', placeholder: '••••••••' })),
            DOM.el('div', { id: 'pref-soulseek-login-status', className: 'soulseek-login-status setting-status neutral' }, ['Soulseek login not checked yet.']),
            DOM.el('div', { className: 'settings-button-row' }, [
                DOM.el('button', { type: 'button', id: 'pref-soulseek-check-login', className: 'quick-btn btn-secondary', onclick: () => this.checkSoulseekLogin() }, [DOM.el('i', { className: 'fa-solid fa-plug-circle-check' }), ' Check Soulseek Login'])
            ]),
            this._createSettingItem('Search Soulseek in parallel', 'When Soulseek is ready, category torrent searches also fetch a Soulseek companion result set so the LLM compares both sources in one decision.', this._toggle('pref-soulseek-parallel')),
            this._createSettingItem('First download attempt', 'When both torrent and Soulseek options are viable, choose which backend the assistant should prefer first.', DOM.el('select', { id: 'pref-soulseek-download-preference' }, [
                DOM.el('option', { value: 'torrent_first' }, ['Prefer torrents first']),
                DOM.el('option', { value: 'soulseek_first' }, ['Prefer Soulseek first']),
                DOM.el('option', { value: 'ask' }, ['Ask me when both look good'])
            ])),
            this._createSettingItem('Auto-retry no-match searches', 'When both torrent and Soulseek find nothing, create a recurring assistant check so rare P2P results can be found at another time of day.', this._toggle('pref-soulseek-auto-retry')),
            this._createSettingItem('Retry every minutes', 'Cadence for automatic no-match retry checks. Six hours samples morning/evening/weekend Soulseek peer availability without flooding.', DOM.el('input', { type: 'number', id: 'pref-soulseek-retry-interval', min: '30', step: '30', placeholder: '360' })),
            this._createSettingItem('Max retry runs', 'How many automatic checks to run before the missed-search watch retires.', DOM.el('input', { type: 'number', id: 'pref-soulseek-retry-max-runs', min: '1', max: '100', placeholder: '12' })),
            this._createSettingItem('Soulseek-enabled categories', 'One category id per line. Defaults include music, audiobooks, ebooks, TV, movies, and general exact files.', DOM.el('textarea', { id: 'pref-soulseek-categories', rows: '4', placeholder: `music\naudiobooks\nebooks\ntv\nmovie\ngeneral` })),
            this._createSettingItem('Share mode', 'Default is full LJS library. Pick custom to share only selected folders, or disabled to download without advertising LJS folders.', DOM.el('select', { id: 'pref-soulseek-share-mode' }, [
                DOM.el('option', { value: 'full_library' }, ['Full LJS library root']),
                DOM.el('option', { value: 'custom' }, ['Custom folders only']),
                DOM.el('option', { value: 'disabled' }, ['Do not share LJS folders'])
            ])),
            this._createSettingItem('Custom share folders', 'One folder per line. Used only in custom mode. Paths are aliased before slskd exposes them.', DOM.el('textarea', { id: 'pref-soulseek-shares', rows: '3', placeholder: `/path/to/Music\n/path/to/Audiobooks` })),
            this._createSettingItem('Excluded share folders', 'One folder per line. Downloads/incomplete folders are automatically excluded too.', DOM.el('textarea', { id: 'pref-soulseek-exclusions', rows: '3', placeholder: '/path/to/private-recordings' }))
        ];
        const soulseekAdvanced = [
            this._createSettingItem('slskd URL', 'Advanced: local managed slskd endpoint. Normally leave this at the default; LJS fills it automatically.', DOM.el('input', { type: 'text', id: 'pref-soulseek-host', placeholder: 'http://127.0.0.1:5030' })),
            this._createSettingItem('slskd API key', 'Advanced: generated automatically for the managed local slskd runtime. Leave blank unless connecting to a remote/manual slskd.', DOM.el('input', { type: 'text', className: 'ljs-secret-input', autocomplete: 'off', 'data-lpignore': 'true', 'data-1p-ignore': 'true', 'data-bwignore': 'true', id: 'pref-soulseek-api-key', placeholder: '••••••••' }))
        ];
        const jackettIndexers = [
            DOM.el('div', { id: 'jackett-indexer-health', className: 'setting-item jackett-indexer-health' }, [
                DOM.el('p', { className: 'empty-msg' }, ['Indexer diagnostics not loaded yet.'])
            ]),
            DOM.el('div', { className: 'settings-button-row' }, [
                this._saveButton('Configure all open/public indexers', 'fa-solid fa-compass', () => this.configureJackettProfile('all_open_public'), 'quick-btn btn-secondary'),
                this._saveButton('Refresh indexers', 'fa-solid fa-rotate', () => this.loadJackettIndexers(), 'quick-btn btn-secondary'),
                this._saveButton('Open Jackett UI', 'fa-solid fa-arrow-up-right-from-square', () => this.openJackettUi(), 'quick-btn btn-secondary')
            ]),
            DOM.el('details', { className: 'settings-details nested-settings-details' }, [
                DOM.el('summary', {}, ['Advanced: add private/closed tracker indexer']),
                DOM.el('p', { className: 'empty-msg' }, ['Enter a Jackett indexer ID, load the schema, fill credentials/cookies/passkeys, then configure.']),
                DOM.el('div', { className: 'tier-control' }, [
                    DOM.el('input', { type: 'text', id: 'jackett-custom-indexer-id', autocomplete: 'off', placeholder: 'indexer id, e.g. mytracker' }),
                    DOM.el('button', { className: 'btn btn-sm btn-secondary', type: 'button', onclick: () => this.loadJackettCustomIndexerSchema() }, ['Load fields'])
                ]),
                DOM.el('div', { id: 'jackett-custom-indexer-fields', className: 'jackett-custom-indexer-fields' }, []),
                DOM.el('button', { className: 'btn btn-sm btn-gold', type: 'button', onclick: () => this.configureJackettCustomIndexer() }, ['Configure indexer'])
            ])
        ];
        return this._panel('fa-solid fa-plug', 'Search Sources', 'Torrent infrastructure plus managed Soulseek/slskd, with source preference kept separate from category behavior.', [
            this._settingsSubsection('Web research', webResearch, { open: true }),
            this._settingsSubsection('Torrent backend', torrentBackend, { open: true }),
            this._settingsSubsection('Soulseek / slskd runtime', soulseekRuntime, { open: true }),
            this._settingsSubsection('Advanced slskd endpoint', soulseekAdvanced),
            this._settingsSubsection('Jackett indexers', jackettIndexers),
            this._saveButton('Save Search Sources', 'fa-solid fa-circle-check', () => this.saveServices())
        ], 'settings-services-panel');
    }

    /**
     * Build LLM provider and tier-routing controls.
     * @private
     */
    _buildLlmPanel() {
        return this._panel('fa-solid fa-brain', 'AI & LLM Gateway', 'Provider, base model, and tier overrides for routing cheap vs. heavy reasoning tasks.', [
            this._createSettingItem('Active provider', 'Primary inference backend. Choose a provider, save or configure its key, then refresh models to pick from the endpoint list.', DOM.el('select', { id: 'pref-llm-provider', onchange: () => this._onPrimaryLlmProviderChanged() }, this._llmProviderOptions())),
            this._createSettingItem('Base model', 'Main model used when a task has no tier override. This list is loaded from the selected provider endpoint; the current custom value is preserved when the endpoint is unavailable.', this._modelSelectControl('pref-llm-model', 'pref-llm-provider', 'base')),
            this._createSettingItem('API base URL', 'Optional provider endpoint override.', DOM.el('input', { type: 'text', id: 'pref-llm-api-base', placeholder: 'Defaults if blank' })),
            this._createSettingItem('API key', 'Optional active provider key.', DOM.el('input', { type: 'text', className: 'ljs-secret-input', autocomplete: 'off', 'data-lpignore': 'true', 'data-1p-ignore': 'true', 'data-bwignore': 'true', id: 'pref-llm-api-key', placeholder: '••••••••' })),
            DOM.el('p', { className: 'empty-msg' }, ['Model menus use the provider /models endpoint. If a key or custom API base changed, save the gateway first, then refresh the model list.']),
            this._sectionTitle('Context budget'),
            this._createSettingItem('Context window cap', 'Maximum prompt context the app may assemble for the selected model. Defaults to the endpoint-reported maximum. Minimum is 10k tokens unless the endpoint itself is smaller.', this._contextWindowControl()),
            this._createSettingItem('Context budget percent', 'Safety headroom applied inside the cap before reserving output tokens.', DOM.el('input', { type: 'number', id: 'pref-llm-context-budget-percent', min: '20', max: '100', step: '1', placeholder: '85' })),
            this._createSettingItem('Raw recent history reserve', 'Percent of conversation-history budget kept uncompressed for the latest turns. Older conversation is compressed into the remaining history budget.', DOM.el('input', { type: 'number', id: 'pref-llm-raw-recent-percent', min: '0', max: '100', step: '1', placeholder: '30' })),
            this._createSettingItem('Reserved output tokens', 'Optional explicit response-token reserve. Leave blank to use task defaults. The model context window includes these output tokens.', DOM.el('input', { type: 'number', id: 'pref-llm-reserved-output', min: '0', step: '1', placeholder: 'auto' })),
            this._sectionTitle('Tier overrides'),
            this._createSettingItem('Lightweight tier', 'Intent routing, summarization, and simple parsing.', this._tierControl('lw')),
            this._createSettingItem('Standard tier', 'Chat, search orchestration, and normal planning.', this._tierControl('std')),
            this._createSettingItem('Heavy tier', 'Complex research, comparison, and multi-step reasoning.', this._tierControl('hv')),
            this._saveButton('Save AI Gateway', 'fa-solid fa-circle-check', () => this.saveLLM())
        ], 'settings-llm-panel');
    }


    /**
     * Build local semantic-memory controls and diagnostics.
     * @private
     */
    _buildSemanticMemoryPanel() {
        return this._panel('fa-solid fa-memory', 'Semantic Memory', 'Local embeddings for long-term chat/context retrieval. Model download is automatic; dependency packaging remains part of the app install.', [
            this._createSettingItem('Enable semantic memory', 'When enabled, conversations and taste evidence are indexed for category-aware context recall.', this._toggle('pref-embeddings-enabled')),
            this._createSettingItem('Embedding provider', 'Builtin is the recommended local ONNX/FastEmbed path. Hash fallback is only a visible degraded mode.', DOM.el('select', { id: 'pref-embeddings-provider' }, [
                DOM.el('option', { value: 'builtin' }, ['Builtin local embeddings']),
                DOM.el('option', { value: 'disabled' }, ['Disabled']),
                DOM.el('option', { value: 'hash_fallback' }, ['Hash fallback / diagnostics only'])
            ])),
            this._createSettingItem('Builtin model', 'Default target is sentence-transformers/all-MiniLM-L6-v2, a tiny 384-dimensional model under the project size budget.', DOM.el('input', { type: 'text', id: 'pref-embeddings-model', placeholder: 'sentence-transformers/all-MiniLM-L6-v2' })),
            this._createSettingItem('Model cache folder', 'Where downloaded embedding model files are stored inside app/user data.', DOM.el('input', { type: 'text', id: 'pref-embeddings-cache', placeholder: './data/embedding_models' })),
            this._createSettingItem('Auto-download model files', 'Fetch missing model files silently during setup/startup instead of asking users to manage them manually.', this._toggle('pref-embeddings-auto-download')),
            this._createSettingItem('Warm up at startup', 'Initialize the embedding runtime early so first chat recall is not delayed.', this._toggle('pref-embeddings-warmup')),
            this._sectionTitle('Health & maintenance'),
            DOM.el('div', { id: 'semantic-memory-health', className: 'setting-item semantic-memory-health' }, [
                DOM.el('p', { className: 'empty-msg' }, ['Checking semantic memory status...'])
            ]),
            DOM.el('div', { className: 'settings-button-row' }, [
                this._saveButton('Save Semantic Memory', 'fa-solid fa-circle-check', () => this.saveSemanticMemory()),
                this._saveButton('Refresh Health', 'fa-solid fa-rotate', () => this.loadSemanticMemoryHealth(), 'quick-btn btn-secondary'),
                this._saveButton('Reindex Memory', 'fa-solid fa-arrows-rotate', () => this.reindexSemanticMemory(), 'quick-btn btn-secondary')
            ])
        ], 'settings-semantic-memory-panel');
    }

    /**
     * Build communication bridge credentials panel.
     * @private
     */
    _buildBridgesPanel() {
        return this._panel('fa-solid fa-tower-broadcast', 'Communication Bridges', 'External chat channels for commanding LJS outside the web UI.', [
            this._createSettingItem('Discord bot token', 'Bot credential for Discord integration.', DOM.el('input', { type: 'text', className: 'ljs-secret-input', autocomplete: 'off', 'data-lpignore': 'true', 'data-1p-ignore': 'true', 'data-bwignore': 'true', id: 'pref-discord-token', placeholder: '••••••••' })),
            this._createSettingItem('Discord channel ID', 'Target channel for notifications and commands.', DOM.el('input', { type: 'text', id: 'pref-discord-channel', placeholder: 'Channel ID number' })),
            this._createSettingItem('Telegram bot token', 'Bot token from @BotFather.', DOM.el('input', { type: 'text', className: 'ljs-secret-input', autocomplete: 'off', 'data-lpignore': 'true', 'data-1p-ignore': 'true', 'data-bwignore': 'true', id: 'pref-telegram-token', placeholder: '••••••••' })),
            this._createSettingItem('WhatsApp API token', 'Permanent WhatsApp business developer endpoint token.', DOM.el('input', { type: 'text', className: 'ljs-secret-input', autocomplete: 'off', 'data-lpignore': 'true', 'data-1p-ignore': 'true', 'data-bwignore': 'true', id: 'pref-whatsapp-token', placeholder: '••••••••' })),
            this._createSettingItem('WhatsApp phone ID', 'Phone number ID of the sending client node.', DOM.el('input', { type: 'text', id: 'pref-whatsapp-phone', placeholder: 'WhatsApp Phone ID' })),
            this._createSettingItem('WhatsApp verify token', 'Webhook verification token.', DOM.el('input', { type: 'text', className: 'ljs-secret-input', autocomplete: 'off', 'data-lpignore': 'true', 'data-1p-ignore': 'true', 'data-bwignore': 'true', id: 'pref-whatsapp-verify', placeholder: '••••••••' })),
            this._saveButton('Save Chat Bridges', 'fa-solid fa-circle-check', () => this.saveBridges(), 'quick-btn btn-secondary')
        ], 'settings-bridges-panel');
    }

    /**
     * Build the developer-facing category manifest panel.
     * @private
     */
    _buildManifestPanel() {
        return this._panel('fa-solid fa-layer-group', 'Advanced Category Contracts', 'Read-only diagnostics showing what each backend category declares: capabilities, UI sections, actions, provider contracts, setup requirements, and config ownership.', [
            DOM.el('p', { className: 'empty-msg' }, ['This is not another settings form. It is the contract LJS exposes to the UI and LLM so generic code does not hardcode TV/movie behavior.']),
            DOM.el('div', { id: 'category-manifest-panel' })
        ], 'settings-manifest-panel');
    }

    /**
     * Create a reusable settings panel.
     * @private
     */
    _panel(iconClass, title, desc, children, id) {
        return DOM.el('section', { className: 'settings-panel glass-panel', id }, [
            DOM.el('h2', {}, [DOM.el('i', { className: iconClass }), ` ${title}`]),
            DOM.el('p', { className: 'card-desc settings-panel-desc' }, [desc]),
            DOM.el('div', { className: 'settings-panel-body' }, children)
        ]);
    }

    /**
     * Create a labelled setting row.
     * @private
     */
    _createSettingItem(title, desc, controlEl) {
        return DOM.el('div', { className: 'setting-item' }, [
            DOM.el('div', { className: 'setting-info' }, [DOM.el('h4', {}, [title]), DOM.el('p', {}, [desc])]),
            DOM.el('div', { className: 'setting-control' }, [controlEl])
        ]);
    }

    /**
     * Create a collapsible settings subsection.
     * @private
     */
    _settingsSubsection(title, children, opts = {}) {
        const attrs = { className: `settings-details nested-settings-details${opts.indent === false ? '' : ' indented-settings-details'}` };
        if (opts.open) attrs.open = true;
        return DOM.el('details', attrs, [
            DOM.el('summary', {}, [title]),
            DOM.el('div', { className: 'settings-subsection-body' }, children || [])
        ]);
    }

    /**
     * Create a section heading inside a panel.
     * @private
     */
    _sectionTitle(text) {
        return DOM.el('h3', { className: 'settings-section-title' }, [text]);
    }

    /**
     * Create a toggle switch control.
     * @private
     */
    _toggle(id) {
        return DOM.el('label', { className: 'toggle-switch' }, [
            DOM.el('input', { type: 'checkbox', id }),
            DOM.el('span', { className: 'slider' })
        ]);
    }

    /**
     * Create a standard save button.
     * @private
     */
    _saveButton(text, icon, onClick, className = 'quick-btn') {
        return DOM.btn('', className, onClick, {
            type: 'button',
            content: `<i class="${icon}"></i> ${text}`
        });
    }

    /**
     * Create category property input controls.
     * @private
     */
    _categoryInput(cat, prop) {
        const inputId = `pref-cat-prop-${cat.id}-${prop.name}`;
        const dataset = { categoryId: cat.id, propertyName: prop.name, valueType: prop.value_type };
        if (prop.value_type === 'bool') {
            return DOM.el('label', { className: 'toggle-switch' }, [
                DOM.el('input', { type: 'checkbox', className: 'pref-category-prop-input', dataset, id: inputId }),
                DOM.el('span', { className: 'slider' })
            ]);
        }
        if (prop.value_type === 'int') {
            return DOM.el('input', { type: 'number', className: 'pref-category-prop-input', dataset, id: inputId });
        }
        if (prop.value_type === 'float') {
            return DOM.el('input', { type: 'number', step: '0.1', className: 'pref-category-prop-input', dataset, id: inputId });
        }
        return DOM.el('input', { type: 'text', className: 'pref-category-prop-input', dataset, id: inputId });
    }

    /**
     * Build the context-window cap control.
     * @private
     */
    _contextWindowControl() {
        return DOM.el('div', { className: 'context-window-control' }, [
            DOM.el('input', {
                type: 'number',
                id: 'pref-llm-max-context',
                min: '10000',
                step: '1024',
                placeholder: 'endpoint maximum',
                oninput: () => this._markManualContextCap()
            }),
            DOM.el('div', { id: 'pref-llm-context-help', className: 'empty-msg' }, ['Endpoint context window will be loaded from the selected provider/model.']),
            DOM.el('button', { className: 'btn btn-sm btn-secondary', type: 'button', onclick: () => this._syncLlmContextWindowControl(true) }, ['Refresh endpoint maximum'])
        ]);
    }


    /**
     * Return provider options shared by base and tier model selectors.
     * @private
     */
    _llmProviderOptions() {
        const providers = [
            ['openrouter', 'OpenRouter'],
            ['nvidia_nim', 'NVIDIA NIM'],
            ['ollama_cloud', 'Ollama Cloud'],
            ['ollama_local', 'Ollama Local'],
            ['lm_studio', 'LM Studio'],
            ['custom', 'Custom']
        ];
        return providers.map(([value, label]) => DOM.el('option', { value }, [label]));
    }

    /**
     * Build a model selector backed by the selected provider's /models endpoint.
     * @private
     */
    _modelSelectControl(modelId, providerId, tierName) {
        return DOM.el('div', { className: 'llm-model-select-control' }, [
            DOM.el('select', {
                id: modelId,
                'data-provider-input': providerId,
                'data-tier-name': tierName || '',
                onchange: () => {
                    if (modelId === 'pref-llm-model') this._syncLlmContextWindowControl(false);
                }
            }, [DOM.el('option', { value: '' }, ['Refresh models to choose…'])]),
            DOM.el('button', {
                className: 'btn btn-sm btn-secondary',
                type: 'button',
                onclick: () => this._refreshLlmModelSelect(modelId, providerId, true)
            }, ['Refresh models'])
        ]);
    }

    /**
     * Handle active-provider changes by reloading endpoint model choices.
     * @private
     */
    _onPrimaryLlmProviderChanged() {
        this._refreshLlmModelSelect('pref-llm-model', 'pref-llm-provider', false);
        this._syncLlmContextWindowControl(false);
    }

    /**
     * Refresh all visible LLM model selectors from their provider endpoints.
     * @private
     */
    _syncLlmModelPickers(refresh) {
        ['pref-llm-model', 'pref-llm-lw-model', 'pref-llm-std-model', 'pref-llm-hv-model'].forEach(modelId => {
            const el = this._input(modelId);
            if (!el) return;
            const providerId = el.dataset.providerInput || 'pref-llm-provider';
            this._refreshLlmModelSelect(modelId, providerId, refresh === true);
        });
    }

    /**
     * Populate one model select with models pulled from the selected endpoint.
     * @private
     */
    async _refreshLlmModelSelect(modelId, providerInputId, refresh) {
        const select = this._input(modelId);
        if (!select) return;
        const providerEl = this._input(providerInputId);
        const provider = (providerEl && providerEl.value) || this._valueById('pref-llm-provider', 'openrouter');
        const previous = select.value || this._configuredModelValue(modelId) || '';
        if (!provider) {
            this._populateModelSelect(select, [], previous, 'Choose a provider first');
            return;
        }
        const cacheKey = `${provider}:${refresh ? 'refresh' : 'cached'}`;
        select.innerHTML = '';
        select.appendChild(DOM.el('option', { value: previous || '' }, [previous ? `Loading models… (${previous})` : 'Loading models…']));
        try {
            let models = this._llmModelCache[provider];
            if (refresh || !Array.isArray(models)) {
                const data = await APIClient.get(`/api/providers/${encodeURIComponent(provider)}/models?refresh=${refresh ? 'true' : 'false'}`);
                models = data.models || [];
                this._llmModelCache[provider] = models;
            }
            this._populateModelSelect(select, models, previous, 'No models returned by endpoint');
            if (modelId === 'pref-llm-model') this._syncLlmContextWindowControl(false);
        } catch (err) {
            this._populateModelSelect(select, [], previous, `Model list unavailable: ${err.message}`);
        }
        void cacheKey;
    }

    /**
     * Return the saved model value for a base/tier selector.
     * @private
     */
    _configuredModelValue(modelId) {
        const llm = (this._settings || {}).llm || {};
        if (modelId === 'pref-llm-model') return llm.model || '';
        const key = modelId.includes('-lw-') ? 'lightweight' : (modelId.includes('-std-') ? 'standard' : (modelId.includes('-hv-') ? 'heavy' : ''));
        return key ? ((llm[key] || {}).model || '') : '';
    }

    /**
     * Render endpoint models into a select while preserving current custom IDs.
     * @private
     */
    _populateModelSelect(select, models, selectedValue, emptyLabel) {
        const current = String(selectedValue || '').trim();
        select.innerHTML = '';
        const seen = new Set();
        if (!models.length) {
            select.appendChild(DOM.el('option', { value: current || '' }, [current || emptyLabel]));
            select.value = current || '';
            return;
        }
        select.appendChild(DOM.el('option', { value: '' }, ['Choose a model…']));
        models.forEach(model => {
            const id = String(model.id || model.name || '').trim();
            if (!id || seen.has(id)) return;
            seen.add(id);
            const label = model.name && model.name !== id ? `${model.name} · ${id}` : id;
            select.appendChild(DOM.el('option', { value: id }, [label]));
        });
        if (current && !seen.has(current)) {
            select.appendChild(DOM.el('option', { value: current }, [`Current/custom: ${current}`]));
        }
        select.value = current && (seen.has(current) || current) ? current : '';
    }

    /**
     * Mark context cap as explicitly edited by the user.
     * @private
     */
    _markManualContextCap() {
        const el = this._input('pref-llm-max-context');
        if (el) el.dataset.ljsAutomaticContext = 'false';
    }

    /**
     * Load endpoint-reported context-window metadata for the selected model.
     * @private
     */
    async _syncLlmContextWindowControl(refresh) {
        const provider = this._valueById('pref-llm-provider', 'openrouter');
        const model = this._valueById('pref-llm-model', '');
        const input = this._input('pref-llm-max-context');
        const help = document.getElementById('pref-llm-context-help');
        if (!input || !provider || !model) return;
        try {
            const data = await APIClient.get(`/api/settings/llm/context?provider=${encodeURIComponent(provider)}&model=${encodeURIComponent(model)}&refresh=${refresh ? 'true' : 'false'}`);
            const maxSelectable = data.max_selectable_context_tokens || 16384;
            const minSelectable = data.min_selectable_context_tokens || Math.min(10000, maxSelectable);
            const defaultContext = data.default_context_tokens || 16384;
            const endpointReported = data.endpoint_context_reported === true;
            const saved = data.configured_context_tokens;
            input.min = String(minSelectable);
            input.max = String(maxSelectable);
            input.dataset.endpointMaxContext = String(maxSelectable);
            input.dataset.endpointContextReported = endpointReported ? 'true' : 'false';
            input.dataset.minimumContext = String(minSelectable);
            if (saved === null || saved === undefined || input.dataset.ljsAutomaticContext !== 'false') {
                input.value = String(data.selected_context_tokens || defaultContext);
                input.dataset.ljsAutomaticContext = saved === null || saved === undefined ? 'true' : 'false';
            }
            if (help) {
                const source = endpointReported ? 'endpoint' : 'fallback default';
                const loaded = data.loaded_context_tokens ? ` Loaded runtime: ${this._formatTokenCount(data.loaded_context_tokens)}.` : '';
                const current = saved === null || saved === undefined
                    ? (endpointReported ? 'endpoint maximum' : 'automatic fallback')
                    : this._formatTokenCount(data.selected_context_tokens || 0);
                const manualNote = endpointReported
                    ? ''
                    : ` Manual caps up to ${this._formatTokenCount(maxSelectable)} are allowed because this endpoint did not report its real maximum.`;
                help.textContent = `Detected ${this._formatTokenCount(defaultContext)} context from ${source}.${loaded} Minimum selectable: ${this._formatTokenCount(minSelectable)}. Current saved cap: ${current}.${manualNote}`;
            }
        } catch (err) {
            if (help) help.textContent = `Could not load endpoint context metadata: ${err.message}`;
        }
    }

    /**
     * Return the context-cap payload, preserving 0 and using null for endpoint max.
     * @private
     */
    _llmContextCapPayload() {
        const el = this._input('pref-llm-max-context');
        if (!el || el.value === '') return null;
        const parsed = parseInt(el.value, 10);
        if (!Number.isFinite(parsed)) return null;
        const min = parseInt(el.dataset.minimumContext || el.min || '10000', 10);
        const boundedMin = Number.isFinite(min) && min > 0 ? Math.max(min, parsed) : Math.max(10000, parsed);
        const max = parseInt(el.dataset.endpointMaxContext || el.max || '0', 10);
        const bounded = Number.isFinite(max) && max > 0 ? Math.min(boundedMin, max) : boundedMin;
        const endpointReported = el.dataset.endpointContextReported === 'true';
        if (endpointReported && Number.isFinite(max) && max > 0 && bounded >= max) return null;
        return bounded;
    }

    /**
     * Render token counts compactly for settings labels.
     * @private
     */
    _formatTokenCount(value) {
        const n = parseInt(value, 10);
        if (!Number.isFinite(n)) return 'unknown';
        if (n >= 1000000) return `${(n / 1000000).toFixed(n % 1000000 === 0 ? 0 : 1)}M tokens`;
        if (n >= 1000) return `${Math.round(n / 1000)}k tokens`;
        return `${n} tokens`;
    }

    /**
     * Create the pair of model/provider controls for a tier.
     * @private
     */
    _tierControl(prefix) {
        const providerId = `pref-llm-${prefix}-provider`;
        const modelId = `pref-llm-${prefix}-model`;
        return DOM.el('div', { className: 'tier-control llm-tier-control' }, [
            DOM.el('select', { id: providerId, onchange: () => this._refreshLlmModelSelect(modelId, providerId, true) }, [
                DOM.el('option', { value: '' }, ['Use active provider']),
                ...this._llmProviderOptions()
            ]),
            this._modelSelectControl(modelId, providerId, prefix)
        ]);
    }

    /**
     * Create Trakt connection controls.
     * @private
     */
    _traktControl() {
        return DOM.el('div', { className: 'trakt-control' }, [
            DOM.el('input', { type: 'hidden', id: 'pref-trakt-id', value: '' }),
            DOM.el('div', { className: 'trakt-status-row' }, [
                DOM.el('span', { id: 'pref-trakt-status', className: 'badge' }, ['Not Connected']),
                DOM.el('button', { className: 'btn btn-sm btn-secondary', type: 'button', onclick: () => this._startTraktAuth() }, ['Link Account'])
            ]),
            DOM.el('details', { className: 'settings-details' }, [
                DOM.el('summary', {}, ['Remote setup / custom Trakt app']),
                DOM.el('input', { type: 'text', id: 'pref-trakt-custom-id', placeholder: 'Custom Trakt Client ID', oninput: e => {
                    const hidden = document.getElementById('pref-trakt-id');
                    if (hidden) hidden.value = e.target.value.trim();
                }})
            ])
        ]);
    }

    /**
     * Render a storage status row.
     * @private
     */
    _renderStorageVolume(v) {
        const freeGb = (v.free_bytes / (1024 ** 3)).toFixed(1);
        const totalGb = (v.total_bytes / (1024 ** 3)).toFixed(1);
        const usedPct = v.total_bytes ? Math.min(100, Math.max(0, 100 - v.free_percent)).toFixed(1) : '0.0';
        const categories = (v.category_ids && v.category_ids.length) ? v.category_ids.join(', ') : 'download staging';
        const statusClass = v.status === 'critical' ? 'danger' : (v.status === 'warning' ? 'highlight' : '');
        const paths = (v.paths || []).map(p => `${p.category_id || p.purpose}: ${p.path}`).join('\n');
        return DOM.el('div', { className: 'setting-item', title: paths }, [
            DOM.el('div', { className: 'setting-info' }, [
                DOM.el('h4', {}, [`${v.mount_point} · ${categories}`]),
                DOM.el('p', {}, [v.message || `${freeGb} GB free`]),
                DOM.el('div', { className: 'storage-meter' }, [DOM.el('div', { style: { width: `${usedPct}%` } })])
            ]),
            DOM.el('span', { className: `stat-value ${statusClass}` }, [`${freeGb}/${totalGb} GB`])
        ]);
    }

    /**
     * Populate category property controls.
     * @private
     */
    _populateCategoryControls() {
        const categories = this._categories || [];
        categories.forEach(cat => {
            const catSettings = (this._settings.category_settings || {})[cat.id] || {};
            (cat.properties || []).forEach(prop => {
                const inputId = `pref-cat-prop-${cat.id}-${prop.name}`;
                const val = catSettings[prop.name] !== undefined ? catSettings[prop.name] : prop.default_value;
                if (prop.value_type === 'bool') this._setCheck(inputId, !!val);
                else this._setVal(inputId, val !== null && val !== undefined ? val : '');
            });
        });
    }

    /**
     * Render current Trakt connection state.
     * @private
     */
    _renderTraktStatus() {
        const statuses = document.querySelectorAll('#pref-trakt-status, .pref-trakt-status');
        if (!statuses.length) return;
        const categorySettings = ((this._settings || {}).category_settings || {});
        const connected = !!(
            (((((this._settings || {}).category_settings || {}).media || {}).services || {}).trakt || {}).access_token ||
            Object.values(categorySettings).some(cat => (((cat || {}).services || {}).trakt || {}).access_token)
        );
        statuses.forEach(status => {
            status.textContent = connected ? 'Connected' : 'Not Connected';
            status.classList.toggle('success', connected);
            status.classList.toggle('danger', !connected);
        });
    }

    /**
     * Start the Trakt auth flow when the optional helper is loaded.
     * @private
     */
    _startTraktAuth() {
        if (typeof window.startTraktAuth === 'function') window.startTraktAuth();
        else ljsAlert('Trakt Auth script not loaded.', { title: 'Trakt Setup' });
    }

    /**
     * Make a settings panel collapsible while preserving form contents.
     * @private
     */
    _makeCollapsible(panelEl) {
        const header = panelEl.querySelector('h2');
        if (!header) return;
        const chevron = DOM.el('i', { className: 'fa-solid fa-chevron-up toggle-chevron' });
        header.appendChild(chevron);
        const cardId = panelEl.id || header.innerText.trim().toLowerCase().replace(/[^a-z0-9]/g, '-');
        const isCollapsed = localStorage.getItem(`settings-collapsed-${cardId}`) === 'true';
        panelEl.classList.toggle('collapsed', isCollapsed);
        chevron.className = isCollapsed ? 'fa-solid fa-chevron-down toggle-chevron' : 'fa-solid fa-chevron-up toggle-chevron';
        header.addEventListener('click', () => {
            const collapsed = panelEl.classList.toggle('collapsed');
            localStorage.setItem(`settings-collapsed-${cardId}`, collapsed);
            chevron.className = collapsed ? 'fa-solid fa-chevron-down toggle-chevron' : 'fa-solid fa-chevron-up toggle-chevron';
        });
    }

    /**
     * Return a DOM input by ID.
     * @private
     */
    _input(id) {
        return document.getElementById(id);
    }

    /**
     * Set an input value when it exists.
     * @private
     */
    _setVal(id, val) {
        const el = this._input(id);
        if (el) el.value = val;
    }

    /**
     * Set a checkbox value when it exists.
     * @private
     */
    _setCheck(id, checked) {
        const el = this._input(id);
        if (el) el.checked = checked;
    }

    /**
     * Read a raw string value with a fallback.
     * @private
     */
    _valueById(id, fallback) {
        const el = this._input(id);
        return el ? el.value : fallback;
    }

    /**
     * Read a nullable string value.
     * @private
     */
    _nullableValueById(id) {
        const el = this._input(id);
        const value = el ? String(el.value || '').trim() : '';
        return value || null;
    }

    /**
     * Read an integer value with a fallback.
     * @private
     */
    _intValue(el, fallback) {
        const parsed = el && el.value !== '' ? parseInt(el.value, 10) : NaN;
        return Number.isFinite(parsed) ? parsed : fallback;
    }

    /**
     * Read a non-negative integer by ID, preserving zero.
     * @private
     */
    _intOrZeroById(id) {
        const el = this._input(id);
        if (!el || el.value === '') return 0;
        const parsed = parseInt(el.value, 10);
        return Number.isFinite(parsed) && parsed >= 0 ? parsed : 0;
    }

    /**
     * Read a nullable non-negative integer by ID, preserving zero.
     * @private
     */
    _nonNegativeIntOrNullById(id) {
        const el = this._input(id);
        if (!el || el.value === '') return null;
        const parsed = parseInt(el.value, 10);
        return Number.isFinite(parsed) && parsed >= 0 ? parsed : null;
    }

    /**
     * Read a nullable integer by ID.
     * @private
     */
    _intOrNullById(id) {
        const el = this._input(id);
        if (!el || el.value === '') return null;
        const parsed = parseInt(el.value, 10);
        return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
    }

    /**
     * Read an integer by ID with a fallback.
     * @private
     */
    _intById(id, fallback) {
        return this._intValue(this._input(id), fallback);
    }

    /**
     * Read a float by ID with a fallback.
     * @private
     */
    _floatById(id, fallback) {
        const el = this._input(id);
        const parsed = el && el.value !== '' ? parseFloat(el.value) : NaN;
        return Number.isFinite(parsed) ? parsed : fallback;
    }

    /**
     * Coerce category input values using manifest type metadata.
     * @private
     */
    _coerceCategoryValue(input, type) {
        if (type === 'bool') return input.checked;
        if (type === 'int') return parseInt(input.value, 10) || 0;
        if (type === 'float') return parseFloat(input.value) || 0.0;
        return input.value.trim();
    }

}

window.SettingsPanel = SettingsPanel;
