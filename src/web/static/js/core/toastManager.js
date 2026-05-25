/**
 * Toast manager for LJS.
 *
 * Lightweight notification overlay. Shows auto-dismissing messages
 * at the top of the viewport with type-based styling (ok, err).
 */

class ToastManager {
    /**
     * Construct and initialize the ToastManager instance.
     *
     * Keep DOM lookups local, prefer ActionClient/APIClient for server calls,
     * and preserve event names/data attributes so other components can extend
     * this behavior without reaching into private state.
     */
    constructor() { this.timer = null; }
    /**
     * Public method for the ToastManager.show workflow.
     *
     * Keep DOM lookups local, prefer ActionClient/APIClient for server calls,
     * and preserve event names/data attributes so other components can extend
     * this behavior without reaching into private state.
     */
    show(msg, type = 'ok') {
        this.remove();
        const t = DOM.el('div', { id: 'ljs-toast', className: `toast toast-${type}` }, [msg]);
        document.body.appendChild(t);
        this.timer = setTimeout(() => this.remove(), 3500);
    }
    /**
     * Public method for the ToastManager.error workflow.
     *
     * Keep DOM lookups local, prefer ActionClient/APIClient for server calls,
     * and preserve event names/data attributes so other components can extend
     * this behavior without reaching into private state.
     */
    error(msg) { this.show(msg, 'err'); }
    /**
     * Public method for the ToastManager.remove workflow.
     *
     * Keep DOM lookups local, prefer ActionClient/APIClient for server calls,
     * and preserve event names/data attributes so other components can extend
     * this behavior without reaching into private state.
     */
    remove() {
        if (this.timer) clearTimeout(this.timer);
        const t = document.getElementById('ljs-toast');
        if (t) t.remove();
    }
}

window.ToastManager = ToastManager;
window.toast = new ToastManager();
