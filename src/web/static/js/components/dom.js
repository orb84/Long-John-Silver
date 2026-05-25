/**
 * DOM helpers for LJS.
 *
 * Shared utilities for clean element creation, formatting, and constants
 * used across all UI components.
 */

const LANG_OPTIONS = ['English', 'Italian', 'French', 'German', 'Spanish', 'Japanese', 'Korean', 'Portuguese', 'Russian', 'Hindi', 'Chinese', 'Arabic', 'Dutch', 'Swedish', 'Norwegian', 'Danish', 'Finnish', 'Polish', 'Turkish', 'Other'];
const SUB_OPTIONS = ['en', 'it', 'fr', 'de', 'es', 'ja', 'ko', 'pt', 'ru', 'hi', 'zh', 'ar', 'nl', 'sv', 'no', 'da', 'fi', 'pl', 'tr'];

/**
 * Owns the DOM UI component or frontend service contract.
 *
 * Keep this class focused on one browser-facing responsibility and inject
 * collaborators through the constructor or window composition root. Extend by
 * adding small public methods and preserving DOM/event contracts used by templates.
 */
class DOM {
    /**
     * Public method for the DOM.el workflow.
     *
     * Keep DOM lookups local, prefer ActionClient/APIClient for server calls,
     * and preserve event names/data attributes so other components can extend
     * this behavior without reaching into private state.
     */
    static el(tag, props = {}, children = []) {
        const el = document.createElement(tag);
        const normalizedChildren = Array.isArray(children)
            ? children
            : (children === null || children === undefined ? [] : [children]);
        for (const [key, value] of Object.entries(props || {})) {
            if (value === null || value === undefined) continue;
            if (key === 'className') el.className = value;
            else if (key === 'style' && typeof value === 'object') Object.assign(el.style, value);
            else if (key === 'dataset') Object.assign(el.dataset, value);
            else if (key.startsWith('on')) el[key] = value;
            else if (key === 'innerHTML' || key === 'html' || key === 'content') el.innerHTML = value;
            else el.setAttribute(key, value);
        }
        DOM.hardenFormControl(el);
        normalizedChildren.forEach(child => {
            if (typeof child === 'string' || typeof child === 'number') {
                if (String(child)) el.appendChild(document.createTextNode(String(child)));
            }
            else if (child) el.appendChild(child);
        });
        return el;
    }

    /**
     * Mark app command/config inputs as non-credential fields.
     *
     * Chrome password manager can mistake command boxes or API-token fields for
     * login forms when a page contains many password-like controls.  LJS is a
     * command dashboard, so dynamic controls default to no credential capture
     * unless a dedicated auth page explicitly opts out of this hardening.
     */
    static hardenFormControl(el) {
        if (!el || !el.tagName) return;
        const tag = String(el.tagName || '').toLowerCase();
        if (tag === 'form') {
            el.setAttribute('autocomplete', 'off');
            el.setAttribute('data-ljs-form', el.getAttribute('data-ljs-auth-form') === 'true' ? 'auth' : 'app');
        }
        if (!['input', 'textarea', 'select'].includes(tag)) return;

        const isRealLogin = el.closest('[data-ljs-auth-form="true"]');
        const rawType = String(el.getAttribute('type') || (tag === 'textarea' ? 'textarea' : 'text')).toLowerCase();
        const idName = `${el.getAttribute('id') || ''} ${el.getAttribute('name') || ''} ${el.getAttribute('placeholder') || ''}`.toLowerCase();
        const looksLikeSecret = rawType === 'password' || /(token|api|key|secret|credential|webhook|verify)/.test(idName);
        const looksLikeCommand = /(chat|command|query|search|model|path|url|channel|phone)/.test(idName);

        if (!isRealLogin) {
            el.setAttribute('data-ljs-noncredential', 'true');
            el.setAttribute('data-lpignore', 'true');
            el.setAttribute('data-1p-ignore', 'true');
            el.setAttribute('data-bwignore', 'true');
            el.setAttribute('aria-autocomplete', 'none');

            // Chrome can still pair an arbitrary text command as "username" with
            // any hidden/non-visible password input in the SPA.  For API keys and
            // bridge tokens, do not expose real input[type=password] controls to
            // browser credential managers at all; visually mask them instead.
            if (rawType === 'password') {
                el.setAttribute('type', 'text');
                el.classList.add('ljs-secret-input');
                el.style.webkitTextSecurity = 'disc';
                el.style.textSecurity = 'disc';
                el.setAttribute('data-ljs-secret-field', 'true');
            }

            el.setAttribute('autocomplete', looksLikeSecret ? 'off' : 'off');
            if (!el.getAttribute('name')) {
                el.setAttribute('name', `ljs-${looksLikeCommand ? 'command' : looksLikeSecret ? 'secret' : 'field'}-${el.id || Math.random().toString(36).slice(2)}`);
            }
        }

        const type = String(el.getAttribute('type') || rawType).toLowerCase();
        if (['text', 'search', 'textarea', 'url', 'email'].includes(type)) {
            if (!el.getAttribute('autocapitalize')) el.setAttribute('autocapitalize', 'off');
            if (!el.getAttribute('autocorrect')) el.setAttribute('autocorrect', 'off');
            if (!el.getAttribute('spellcheck')) el.setAttribute('spellcheck', 'false');
        }
    }

    /** Harden existing template-rendered controls after page load. */
    static hardenExistingForms(root = document) {
        if (!root || !root.querySelectorAll) return;
        root.querySelectorAll('form, input, textarea, select').forEach(el => DOM.hardenFormControl(el));
    }

    /** Keep dynamically-rendered controls out of browser credential capture. */
    static startCredentialHardeningObserver(root = document.body) {
        if (!root || !window.MutationObserver) return;
        DOM.hardenExistingForms(document);
        const observer = new MutationObserver(records => {
            records.forEach(record => {
                record.addedNodes.forEach(node => {
                    if (!node || !node.tagName) return;
                    DOM.hardenFormControl(node);
                    DOM.hardenExistingForms(node);
                });
            });
        });
        observer.observe(root, { childList: true, subtree: true });
        return observer;
    }
    /**
     * Public method for the DOM.btn workflow.
     *
     * Keep DOM lookups local, prefer ActionClient/APIClient for server calls,
     * and preserve event names/data attributes so other components can extend
     * this behavior without reaching into private state.
     */
    static btn(text, className, onClick, props = {}) {
        const hasHTML = props.hasOwnProperty('content') || props.hasOwnProperty('innerHTML') || props.hasOwnProperty('html');
        return this.el('button', { className: `btn ${className}`, onclick: onClick, ...props }, hasHTML ? [] : [text]);
    }
}

/**
 * Public UI helper for the generateUUID workflow.
 *
 * Keep inputs DOM-safe, delegate server mutations through API or Action clients,
 * and preserve the return/side-effect contract because templates may call this
 * function directly from event handlers.
 */
function generateUUID() {
    return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function(c) {
        const r = Math.random() * 16 | 0, v = c === 'x' ? r : (r & 0x3 | 0x8);
        return v.toString(16);
    });
}

/**
 * Public UI helper for the formatBytes workflow.
 *
 * Keep inputs DOM-safe, delegate server mutations through API or Action clients,
 * and preserve the return/side-effect contract because templates may call this
 * function directly from event handlers.
 */
function formatBytes(bytes) {
    if (bytes >= 1e9) return (bytes / 1e9).toFixed(1) + ' GB';
    if (bytes >= 1e6) return (bytes / 1e6).toFixed(0) + ' MB';
    if (bytes >= 1e3) return (bytes / 1e3).toFixed(0) + ' kB';
    return bytes + ' B';
}

/**
 * Owns the Component UI component or frontend service contract.
 *
 * Keep this class focused on one browser-facing responsibility and inject
 * collaborators through the constructor or window composition root. Extend by
 * adding small public methods and preserving DOM/event contracts used by templates.
 */
class Component {
    /**
     * Construct and initialize the Component instance.
     *
     * Keep DOM lookups local, prefer ActionClient/APIClient for server calls,
     * and preserve event names/data attributes so other components can extend
     * this behavior without reaching into private state.
     */
    constructor(elementId) {
        this.container = document.getElementById(elementId);
        this.state = {};
    }
    _clear() { if (this.container) this.container.innerHTML = ''; }
}

window.LANG_OPTIONS = LANG_OPTIONS;
window.SUB_OPTIONS = SUB_OPTIONS;
window.DOM = DOM;
window.generateUUID = generateUUID;
window.formatBytes = formatBytes;
window.Component = Component;


document.addEventListener('DOMContentLoaded', () => DOM.startCredentialHardeningObserver(document.body));
