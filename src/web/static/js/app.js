/**
 * LJS frontend entry point & Composition Root.
 *
 * Coordinates initialization of the modular glassmorphic component framework,
 * instantiating views (HelmPanel, HoldPanel, BootyPanel, SettingsPanel) in exact dependency
 * order, setting up dependency injection, and managing live ambient micro-animations.
 */

class AppDeck {
    /**
     * Construct and initialize the AppDeck instance.
     *
     * Keep DOM lookups local, prefer ActionClient/APIClient for server calls,
     * and preserve event names/data attributes so other components can extend
     * this behavior without reaching into private state.
     */
    constructor() {
        console.log('[AppDeck] Commissioning Long John Silver Control Bridge...');
        this._eventBus = window.shipEvents;
        this._wsClient = null;
        this._viewManager = null;
        this._compass = null;
        
        // Dynamic Views
        this._helmPanel = null;
        this._holdPanel = null;
        this._bootyPanel = null;
        this._settingsPanel = null;
        this._sharingPanel = null;
        this._hydrateTimer = null;
        this._lastHydrateAt = 0;
        this._voyageLogLineLimit = 160;
        this._voyageLogLevel = 'all';
        
        this.init();
    }

    /**
     * Bootstrap dependencies, dynamic views, components, and spawn visual ambient helpers.
     */
    init() {
        // 1. Setup networking layer and attach globally
        this._wsClient = new WebSocketClient(this._eventBus);
        window.wsClient = this._wsClient;
        this._wsClient.connect();

        // 2. Instantiate stateful view panels (Object-Oriented dynamic DOM generation)
        this._helmPanel = new HelmPanel('helm', this._eventBus);
        window.helmPanel = this._helmPanel;

        this._holdPanel = new HoldPanel('hold', this._eventBus);
        window.holdPanel = this._holdPanel;

        this._bootyPanel = new BootyPanel('booty', this._eventBus);
        window.bootyPanel = this._bootyPanel;

        this._settingsPanel = new SettingsPanel('settings', this._eventBus);
        window.settingsPanel = this._settingsPanel;

        this._sharingPanel = new SharingPanel('sharing', this._eventBus);
        window.sharingPanel = this._sharingPanel;

        // 3. Instantiate view controllers & modules (Dependency Inversion over generated DOM)
        window.downloads = new DownloadManager();
        window.chatController = new AssistantChat();
        window.detailModal = new CategoryItemDetailModal();
        if (window.SuggestionManager) {
            window.suggestionManager = new SuggestionManager();
        }
        if (window.NotificationInbox) {
            window.notificationInbox = new NotificationInbox();
        }

        // Trigger active downloads UI load since DownloadManager is now fully defined
        if (window.holdPanel) {
            window.holdPanel.setFilter(window.holdPanel._currentFilter);
        }

        // 4. Setup visual layout managers & snapping fidget widgets
        this._viewManager = new ViewManager(this._eventBus);
        window.viewManager = this._viewManager;

        this._compass = new FidgetCompass(this._eventBus);
        window.compass = this._compass;

        // 5. Connect system status connection change triggers
        this._eventBus.subscribe('system:connection_status', (status) => {
            this._updateSystemStatus(status.connected);
            if (status && status.connected) this._hydrateInitialPanels('websocket_connected');
        });
        this._eventBus.subscribe('system', (event) => {
            if (event.subtype === 'background_status') {
                this._updateBackgroundStatus(event);
            }
        });

        // 6. Apply persona-owned avatar and bounded theme hints.  The backend
        // registry sanitizes these values; the frontend only maps them onto
        // known CSS variables so a persona package can add flavor without
        // rewriting the app layout.
        this._loadPersonaChrome();

        // 7. Spawn immersive animated ocean bubble elements
        this._spawnBubbles();

        // 8. Initialize the voyage logs loop and perform a second explicit
        // data hydration pass.  Some browsers paint the DOM before the first
        // WebSocket catch-up frame arrives; this keeps The Booty/Hold/Suggestions
        // populated without requiring the user to hard-refresh the page.
        this._initLogsInterval();
        this._hydrateInitialPanels('initial_dom_ready');
        this._checkCategoryOnboarding();
    }

    /**
     * Detect newly installed or unconfigured categories and surface a lightweight setup prompt.
     * @private
     */
    async _checkCategoryOnboarding() {
        if (typeof APIClient === 'undefined' || typeof window === 'undefined') return;
        const storageKey = 'ljs_known_categories_v1';
        try {
            const data = await APIClient.get('/api/setup/requirements');
            const categories = Array.isArray(data.categories) ? data.categories : [];
            const currentIds = categories.map(cat => cat.id || cat.category_id).filter(Boolean).sort();
            let knownIds = [];
            try { knownIds = JSON.parse(window.localStorage.getItem(storageKey) || '[]') || []; } catch (_) { knownIds = []; }
            const knownSet = new Set(Array.isArray(knownIds) ? knownIds : []);
            const unseen = categories.filter(cat => (cat.id || cat.category_id) && !knownSet.has(cat.id || cat.category_id));
            const missing = categories.filter(cat => (cat.setup_requirements || cat.requirements || []).some(req => req.required && !req.configured));
            if (unseen.length && window.toast) {
                const general = unseen.find(cat => (cat.id || cat.category_id) === 'general');
                const names = (general ? [general] : unseen).map(cat => cat.display_name || cat.id).join(', ');
                toast.show(`New category available: ${names}. Review its library folder in Compass → Library Categories.`);
            }
            if (missing.length && window.toast) {
                const names = missing.map(cat => cat.display_name || cat.id).join(', ');
                toast.show(`Category setup needed for: ${names}. Open Compass → Library Categories to configure required paths.`, 'err');
            }
            window.localStorage.setItem(storageKey, JSON.stringify(currentIds));
        } catch (err) {
            console.warn('[AppDeck] Category onboarding check unavailable:', err);
        }
    }

    /**
     * Refresh all panels that rely on server state after startup/reconnect.
     *
     * Initial page paint, the backend deferred startup jobs, and WebSocket
     * connection establishment are intentionally decoupled so the UI appears
     * quickly.  This catch-up pass asks each panel to re-read its authoritative
     * endpoint once the browser knows the API is reachable, preventing stale
     * empty panels that only fix themselves after a full page refresh.
     * @private
     */
    _hydrateInitialPanels(reason = 'manual') {
        const now = Date.now();
        if (now - this._lastHydrateAt < 1000) return;
        this._lastHydrateAt = now;
        if (this._hydrateTimer) clearTimeout(this._hydrateTimer);
        this._hydrateTimer = setTimeout(async () => {
            const jobs = [];
            // The Booty/library catalog performs its own progressive initial load.
            // Do not duplicate it during global hydration: large libraries were
            // issuing overlapping category/item reads before the tab could paint.
            if (window.downloads && typeof window.downloads.load === 'function') jobs.push(window.downloads.load());
            if (window.holdPanel && typeof window.holdPanel.loadRecentPlunder === 'function') jobs.push(window.holdPanel.loadRecentPlunder());
            if (window.suggestionManager && typeof window.suggestionManager.load === 'function') jobs.push(window.suggestionManager.load({ force: true }));
            if (window.sharingPanel && typeof window.sharingPanel.load === 'function') jobs.push(window.sharingPanel.load());
            if (window.helmPanel && typeof window.helmPanel.updateStats === 'function') jobs.push(window.helmPanel.updateStats());
            if (window.helmPanel && typeof window.helmPanel.updateStorageStatus === 'function') jobs.push(window.helmPanel.updateStorageStatus());
            if (typeof APIClient !== 'undefined') {
                jobs.push(APIClient.get('/api/library/status').then(data => {
                    if (data && data.scan && data.scan.message) this._updateBackgroundStatus(data.scan);
                }));
            }
            const results = await Promise.allSettled(jobs);
            const failed = results.filter(r => r.status === 'rejected');
            if (failed.length) console.warn(`[AppDeck] ${failed.length} panel hydration job(s) failed after ${reason}.`, failed);
        }, reason === 'initial_dom_ready' ? 500 : 150);
    }

    /**
     * Load the active persona package chrome into the header and CSS variables.
     * @private
     */
    async _loadPersonaChrome() {
        if (typeof APIClient === 'undefined') return;
        try {
            const data = await APIClient.get('/api/personas/active');
            const persona = data.active || {};
            this._applyPersonaChrome(persona);
        } catch (err) {
            console.warn('[AppDeck] Persona chrome unavailable:', err);
        }
    }

    /**
     * Apply a sanitized persona package summary returned by the backend.
     * @private
     */
    _applyPersonaChrome(persona) {
        if (!persona || !persona.id) return;
        const title = document.getElementById('persona-display-name') || document.querySelector('.brand-text h1');
        if (title && persona.display_name) {
            title.textContent = String(persona.display_name).toUpperCase();
        }

        const avatar = document.getElementById('brand-avatar');
        if (avatar && persona.avatar_url) {
            avatar.style.backgroundImage = `url('${persona.avatar_url}')`;
        }
        if (avatar) {
            const shape = (((persona.theme || {}).avatar_shape) || ((persona.theme || {}).styles || {}).avatar_shape || 'freeform');
            avatar.classList.remove('avatar-shape-freeform', 'avatar-shape-rounded', 'avatar-shape-circle', 'avatar-shape-square');
            avatar.classList.add(`avatar-shape-${shape}`);
            avatar.title = persona.description || persona.display_name || 'Assistant persona';
        }

        const root = document.documentElement;
        const theme = persona.theme || {};
        const colors = theme.colors || theme;
        const cssMap = {
            accent: '--accent-gold',
            accent_gold: '--accent-gold',
            accent_gold_glow: '--accent-gold-glow',
            accent_teal: '--accent-teal',
            accent_teal_glow: '--accent-teal-glow',
            accent_red: '--accent-red',
            accent_red_glow: '--accent-red-glow',
            background_deep: '--bg-deep',
            bg_deep: '--bg-deep',
            ocean_center: '--ocean-center',
            ocean_mid: '--ocean-mid',
            ocean_edge: '--ocean-edge',
            glass_bg: '--glass-bg',
            glass_border: '--glass-border',
            text_main: '--text-main',
            text_dim: '--text-dim',
            text: '--text',
            text_muted: '--text-muted',
            gold: '--gold',
            teal: '--teal',
            border: '--border',
            nav_bg: '--nav-bg',
            bubble_bg: '--bubble-bg',
            compass_bg: '--compass-bg'
        };
        Object.entries(cssMap).forEach(([key, cssVar]) => {
            if (colors[key]) root.style.setProperty(cssVar, colors[key]);
        });
        this._eventBus.publish('persona:changed', { persona });
    }

    /**
     * Updates header pulse status dynamically based on WebSocket connection.
     * @private
     */
    _updateBackgroundStatus(event) {
        const statusText = document.getElementById('background-status-text');
        if (!statusText) return;
        statusText.textContent = event.message || 'Idle';
        const statusLine = statusText.closest('.bg-status') || statusText;
        statusLine.classList.toggle('is-running', event.phase === 'running');
    }

    _updateSystemStatus(connected) {
        const pulseDot = document.querySelector('.pulse-dot');
        const statusText = document.getElementById('system-status-text');
        
        if (!pulseDot || !statusText) return;

        if (connected) {
            pulseDot.style.color = 'var(--accent-teal)';
            pulseDot.style.animation = 'pulse 2.5s infinite';
            statusText.textContent = 'Systems Operational';
        } else {
            pulseDot.style.color = 'var(--accent-red)';
            pulseDot.style.animation = 'pulse 0.8s infinite';
            statusText.textContent = 'Connection Interrupted';
        }
    }

    /**
     * Procedurally spawns rising sea bubbles at random intervals.
     * @private
     */
    _spawnBubbles() {
        const container = document.getElementById('bubbles');
        if (!container) return;

        const makeBubble = () => {
            const size = Math.random() * 15 + 5; // Size in px
            const left = Math.random() * 100;    // Position in %
            const duration = Math.random() * 10 + 6; // Float time in sec

            const bubble = document.createElement('div');
            bubble.className = 'bubble';
            bubble.style.width = `${size}px`;
            bubble.style.height = `${size}px`;
            bubble.style.left = `${left}%`;
            bubble.style.animationDuration = `${duration}s`;

            container.appendChild(bubble);

            // Clean up DOM node once animation ends
            setTimeout(() => {
                bubble.remove();
            }, duration * 1000);
        };

        // Seed initial floating bubbles
        for (let i = 0; i < 15; i++) {
            setTimeout(makeBubble, Math.random() * 3000);
        }

        // Spawn bubbles continuously
        setInterval(makeBubble, 1200);
    }

    /**
     * Periodically updates logs when logs terminal is visible.
     * @private
     */
    _initLogsInterval() {
        window.refreshLogs = () => this._refreshVoyageLogs();

        document.addEventListener('click', (event) => {
            const tab = event.target && event.target.closest ? event.target.closest('[data-log-level]') : null;
            if (!tab) return;
            this._voyageLogLevel = tab.getAttribute('data-log-level') || 'all';
            document.querySelectorAll('[data-log-level]').forEach(btn => {
                btn.classList.toggle('is-active', btn === tab);
            });
            this._refreshVoyageLogs();
        });

        // Trigger first log pull once HelmPanel has rendered its terminal.
        setTimeout(() => this._refreshVoyageLogs(), 1000);

        // Keep the log terminal as a bounded visible tail.  Polling only while
        // The Helm is visible avoids pointless network/DOM churn during long
        // sessions on other tabs, and every refresh replaces the old rows.
        setInterval(() => {
            const helm = document.getElementById('helm');
            if (!helm || helm.classList.contains('active')) this._refreshVoyageLogs();
        }, 10000);
    }

    /**
     * Pull the server-side LJS log tail into the Helm Voyage Logs panel.
     * @private
     */
    async _refreshVoyageLogs() {
        const container = document.getElementById('log-container');
        if (!container || typeof APIClient === 'undefined') return;
        try {
            const level = this._voyageLogLevel || 'all';
            const data = await APIClient.get(`/api/system/logs?lines=${this._voyageLogLineLimit}&level=${encodeURIComponent(level)}`);
            container.innerHTML = '';
            const lines = Array.isArray(data.logs) && data.logs.length
                ? data.logs.slice(-this._voyageLogLineLimit)
                : [level === 'warnings' ? 'No warnings or errors in the selected log window.' : 'No log lines returned.'];
            lines.forEach(line => {
                const text = String(line || '').trim();
                const row = DOM.el('div', { className: 'voyage-log-line' }, [text]);
                if (text.includes('| ERROR') || text.includes(' ERROR ')) row.classList.add('is-error');
                else if (text.includes('| WARNING') || text.includes(' WARNING ')) row.classList.add('is-warning');
                else if (text.includes('| DEBUG') || text.includes(' DEBUG ')) row.classList.add('is-debug');
                else row.classList.add('is-info');
                container.appendChild(row);
            });
            while (container.childElementCount > this._voyageLogLineLimit) {
                container.removeChild(container.firstElementChild);
            }
            const body = document.getElementById('voyage-log-body');
            if (body) body.scrollTop = body.scrollHeight;
        } catch (err) {
            container.innerHTML = '';
            container.appendChild(DOM.el('div', { className: 'voyage-log-line is-error' }, [
                `Could not load voyage logs: ${err.message || err}`
            ]));
        }
    }
}

// Instantiate the Composition Root when DOM loads
document.addEventListener('DOMContentLoaded', () => {
    window.appDeck = new AppDeck();
});
