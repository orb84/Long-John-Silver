/**
 * Generic LJS modal manager.
 *
 * Replaces browser confirm/alert/prompt with a glassmorphic, promise-based
 * modal that any component can use for approvals, notifications, and short
 * text input.  This keeps destructive actions visually consistent with the app.
 */
class LJSModalManager {
    /**
     * Construct and initialize the LJSModalManager instance.
     *
     * Keep DOM lookups local, prefer ActionClient/APIClient for server calls,
     * and preserve event names/data attributes so other components can extend
     * this behavior without reaching into private state.
     */
    constructor() {
        this._queue = [];
        this._active = false;
        this._root = null;
        this._ensureRoot();
    }

    _ensureRoot() {
        if (this._root) return this._root;
        let root = document.getElementById('ljs-modal-root');
        if (!root) {
            root = document.createElement('div');
            root.id = 'ljs-modal-root';
            document.body.appendChild(root);
        }
        this._root = root;
        return root;
    }

    /**
     * Public method for the LJSModalManager.confirm workflow.
     *
     * Keep DOM lookups local, prefer ActionClient/APIClient for server calls,
     * and preserve event names/data attributes so other components can extend
     * this behavior without reaching into private state.
     */
    confirm({ title = 'Confirm Order', message = '', confirmText = 'Confirm', cancelText = 'Cancel', danger = false } = {}) {
        return this._enqueue({ type: 'confirm', title, message, confirmText, cancelText, danger });
    }

    /**
     * Public method for the LJSModalManager.alert workflow.
     *
     * Keep DOM lookups local, prefer ActionClient/APIClient for server calls,
     * and preserve event names/data attributes so other components can extend
     * this behavior without reaching into private state.
     */
    alert({ title = 'Notice', message = '', confirmText = 'Aye' } = {}) {
        return this._enqueue({ type: 'alert', title, message, confirmText, cancelText: null, danger: false });
    }

    /**
     * Public method for the LJSModalManager.prompt workflow.
     *
     * Keep DOM lookups local, prefer ActionClient/APIClient for server calls,
     * and preserve event names/data attributes so other components can extend
     * this behavior without reaching into private state.
     */
    prompt({ title = 'Input Needed', message = '', placeholder = '', defaultValue = '', confirmText = 'Submit', cancelText = 'Cancel' } = {}) {
        return this._enqueue({ type: 'prompt', title, message, placeholder, defaultValue, confirmText, cancelText, danger: false });
    }

    _enqueue(config) {
        return new Promise((resolve) => {
            this._queue.push({ config, resolve });
            if (!this._active) this._showNext();
        });
    }

    _showNext() {
        const job = this._queue.shift();
        if (!job) {
            this._active = false;
            return;
        }
        this._active = true;
        this._render(job.config, (value) => {
            job.resolve(value);
            this._close();
            this._showNext();
        });
    }

    _render(config, done) {
        const root = this._ensureRoot();
        root.innerHTML = '';
        const overlay = DOM.el('div', { className: 'ljs-modal-overlay' });
        const panel = DOM.el('div', { className: `ljs-modal glass-panel ${config.danger ? 'danger' : ''}` });

        const icon = config.danger ? 'fa-triangle-exclamation' : (config.type === 'alert' ? 'fa-circle-info' : 'fa-compass');
        panel.appendChild(DOM.el('div', { className: 'ljs-modal-header' }, [
            DOM.el('div', { className: 'ljs-modal-icon' }, [DOM.el('i', { className: `fa-solid ${icon}` })]),
            DOM.el('div', {}, [
                DOM.el('h3', {}, [config.title]),
                DOM.el('p', { className: 'ljs-modal-message' }, [config.message])
            ])
        ]));

        let input = null;
        if (config.type === 'prompt') {
            input = DOM.el('input', {
                className: 'ljs-modal-input',
                type: 'text',
                placeholder: config.placeholder || '',
                value: config.defaultValue || ''
            });
            panel.appendChild(input);
        }

        const actions = DOM.el('div', { className: 'ljs-modal-actions' });
        if (config.cancelText) {
            actions.appendChild(DOM.btn(config.cancelText, 'btn-ghost', () => done(config.type === 'prompt' ? null : false)));
        }
        actions.appendChild(DOM.btn(config.confirmText, config.danger ? 'btn-danger' : 'btn-gold', () => {
            done(config.type === 'prompt' ? (input ? input.value : '') : true);
        }));
        panel.appendChild(actions);
        overlay.appendChild(panel);
        root.appendChild(overlay);

        const keyHandler = (ev) => {
            if (ev.key === 'Escape') {
                document.removeEventListener('keydown', keyHandler);
                done(config.type === 'prompt' ? null : false);
            }
            if (ev.key === 'Enter' && config.type === 'prompt') {
                document.removeEventListener('keydown', keyHandler);
                done(input ? input.value : '');
            }
        };
        document.addEventListener('keydown', keyHandler, { once: false });
        if (input) setTimeout(() => input.focus(), 50);
    }

    _close() {
        if (this._root) this._root.innerHTML = '';
    }
}

window.ljsModal = new LJSModalManager();
window.ljsConfirm = (message, options = {}) => window.ljsModal.confirm({ message, ...options });
window.ljsAlert = (message, options = {}) => window.ljsModal.alert({ message, ...options });
window.ljsPrompt = (message, options = {}) => window.ljsModal.prompt({ message, ...options });
