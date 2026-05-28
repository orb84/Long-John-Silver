/**
 * ViewManager for LJS.
 *
 * Manages responsive section/tab switching for the Glassmorphic viewports,
 * adding sliding animation transitions and notifying the fidget compass to snap.
 */

class ViewManager extends Component {
    /**
     * @param {EventBus} eventBus - Shared central event bus.
     */
    constructor(eventBus) {
        super('view-container');
        this._eventBus = eventBus;
        this._activeViewId = 'helm';
        // Map target view sections to snapping angles on the compass
        this._compassAngles = {
            'helm': 0,      // North
            'hold': 90,     // East
            'booty': 180,   // South
            'suggestions': 225,
            'sharing': 270,
            'settings': 315 // West-ish
        };
        
        if (this.container) {
            this._init();
        }
    }

    /**
     * Set up nav button click listeners and hash routing support.
     * @private
     */
    _init() {
        const navBtns = document.querySelectorAll('.nav-dock .nav-btn');
        navBtns.forEach(btn => {
            btn.addEventListener('click', () => {
                const targetId = btn.getAttribute('data-target');
                this.switchView(targetId);
            });
        });

        // Listen for internal redirection events (e.g. from notifications or suggestions)
        this._eventBus.subscribe('navigation:request', (data) => {
            if (data.target) this.switchView(data.target);
        });

        // Handle URL hash routes if any (e.g. #hold, #booty)
        this._handleHashRoute();
        window.addEventListener('hashchange', () => this._handleHashRoute());
    }

    /**
     * Switch current view with animated translations.
     * @param {string} targetId - ID of the section view to switch to.
     */
    switchView(targetId) {
        const targetView = document.getElementById(targetId);
        if (!targetView) {
            console.error(`[ViewManager] Target view section "${targetId}" not found.`);
            return;
        }

        this._activeViewId = targetId;

        // Toggle active states on nav buttons
        document.querySelectorAll('.nav-dock .nav-btn').forEach(btn => {
            const btnTarget = btn.getAttribute('data-target');
            btn.classList.toggle('active', btnTarget === targetId);
        });

        // Toggle active classes on view sections
        document.querySelectorAll('.view-container .view').forEach(view => {
            if (view.id === targetId) {
                view.classList.add('active');
                view.focus();
            } else {
                view.classList.remove('active');
            }
        });

        // Publish view change so the snapping compass needle can rotate
        const angle = this._compassAngles[targetId] !== undefined ? this._compassAngles[targetId] : 0;
        this._eventBus.publish('view:changed', { viewId: targetId, angle: angle });
        
        if (targetId === 'hold') {
            if (window.downloads) {
                window.downloads.load();
            }
            if (window.holdPanel) {
                window.holdPanel.loadRecentPlunder();
            }
        }
        if (targetId === 'suggestions' && window.suggestionManager) {
            window.suggestionManager.load({ force: true });
        }
        if (targetId === 'sharing' && window.sharingPanel) {
            window.sharingPanel.load();
        }
        
        console.log(`[ViewManager] Switched active view to "${targetId}" (pointing compass to ${angle}deg).`);
    }

    /**
     * Check URL hash and navigate if valid.
     * @private
     */
    _handleHashRoute() {
        const hash = window.location.hash.substring(1);
        if (hash && this._compassAngles[hash] !== undefined) {
            this.switchView(hash);
        }
    }
}

window.ViewManager = ViewManager;
