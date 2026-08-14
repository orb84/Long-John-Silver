'use strict';

const fs = require('fs');
const vm = require('vm');

class FakeClassList {
    constructor(owner) { this.owner = owner; this.values = new Set(); }
    add(...names) { names.forEach(name => this.values.add(name)); this._sync(); }
    remove(...names) { names.forEach(name => this.values.delete(name)); this._sync(); }
    toggle(name, force) {
        const enabled = force === undefined ? !this.values.has(name) : Boolean(force);
        if (enabled) this.values.add(name); else this.values.delete(name);
        this._sync();
        return enabled;
    }
    contains(name) { return this.values.has(name); }
    _sync() { this.owner._className = [...this.values].join(' '); }
}

class FakeElement {
    constructor(tag = 'div', attrs = {}) {
        this.tagName = tag.toUpperCase();
        this.children = [];
        this.parentElement = null;
        this.dataset = {};
        this.style = {};
        this.attributes = {};
        this.listeners = {};
        this.disabled = false;
        this.value = '';
        this.placeholder = '';
        this.textContent = '';
        this.innerHTML = '';
        this.isConnected = false;
        this._className = '';
        this.classList = new FakeClassList(this);
        Object.entries(attrs || {}).forEach(([key, value]) => this._setAttr(key, value));
    }
    _setAttr(key, value) {
        if (key === 'className') {
            String(value || '').split(/\s+/).filter(Boolean).forEach(name => this.classList.add(name));
        } else if (key === 'dataset') {
            Object.assign(this.dataset, value || {});
        } else if (key === 'style') {
            Object.assign(this.style, value || {});
        } else if (key === 'id') {
            this.id = value;
            document._nodes.set(value, this);
        } else if (key.startsWith('on') && typeof value === 'function') {
            this.listeners[key.slice(2)] = value;
        } else {
            this[key] = value;
            this.attributes[key] = String(value);
        }
    }
    set className(value) {
        this.classList.values = new Set(String(value || '').split(/\s+/).filter(Boolean));
        this.classList._sync();
    }
    get className() { return this._className; }
    appendChild(child) { return this._attach(child, false); }
    append(...children) { children.forEach(child => this._attach(child, false)); }
    prepend(child) { return this._attach(child, true); }
    _attach(child, first) {
        if (child === null || child === undefined) return child;
        if (typeof child === 'string') child = Object.assign(new FakeElement('#text'), { textContent: child });
        child.parentElement = this;
        child.isConnected = true;
        if (first) this.children.unshift(child); else this.children.push(child);
        return child;
    }
    replaceChildren(...children) { this.children = []; children.forEach(child => this._attach(child, false)); }
    remove() {
        if (this.parentElement) this.parentElement.children = this.parentElement.children.filter(child => child !== this);
        this.isConnected = false;
    }
    addEventListener(type, handler) { this.listeners[type] = handler; }
    dispatch(type, extra = {}) { this.listeners[type]?.({ target: this, stopPropagation() {}, preventDefault() {}, ...extra }); }
    setAttribute(name, value) { this.attributes[name] = String(value); this[name] = value; }
    getAttribute(name) { return this.attributes[name]; }
    querySelector(selector) {
        const className = selector.startsWith('.') ? selector.slice(1) : null;
        const id = selector.startsWith('#') ? selector.slice(1) : null;
        const match = node => (className && node.classList.contains(className)) || (id && node.id === id);
        const queue = [...this.children];
        while (queue.length) {
            const node = queue.shift();
            if (match(node)) return node;
            queue.push(...(node.children || []));
        }
        return null;
    }
    querySelectorAll(selector) {
        const found = [];
        const className = selector.startsWith('.') ? selector.slice(1) : null;
        const queue = [...this.children];
        while (queue.length) {
            const node = queue.shift();
            if (className && node.classList.contains(className)) found.push(node);
            queue.push(...(node.children || []));
        }
        return found;
    }
    closest(selector) {
        if (selector === '.chat-input-area') {
            let node = this;
            while (node) {
                if (node.classList?.contains('chat-input-area')) return node;
                node = node.parentElement;
            }
        }
        return null;
    }
    focus() {}
    get lastElementChild() { return this.children[this.children.length - 1] || null; }
    get scrollHeight() { return 40; }
    set scrollTop(value) { this._scrollTop = value; }
}

const document = {
    _nodes: new Map(),
    body: new FakeElement('body'),
    getElementById(id) { return this._nodes.get(id) || null; },
    querySelector(selector) {
        if (selector === 'meta[name="ljs-asset-version"]') return { content: 'test-assets' };
        return null;
    },
    addEventListener(type, handler) { if (type === 'DOMContentLoaded') handler(); },
    querySelectorAll() { return []; },
};
document.body.isConnected = true;

global.document = document;
global.window = global;
global.location = { protocol: 'http:', host: 'localhost' };
global.requestAnimationFrame = callback => callback();
global.setTimeout = () => 0;
global.clearTimeout = () => {};
global.getComputedStyle = () => ({ lineHeight: '20px', paddingTop: '8px', paddingBottom: '8px' });
global.localStorage = {
    _data: new Map(),
    getItem(key) { return this._data.has(key) ? this._data.get(key) : null; },
    setItem(key, value) { this._data.set(key, String(value)); },
    removeItem(key) { this._data.delete(key); },
};
global.generateUUID = () => 'test-turn';
global.ljsConfirm = async () => true;
global.marked = null;

global.DOM = {
    el(tag, attrs = {}, children = []) {
        const node = new FakeElement(tag, attrs);
        (children || []).forEach(child => node.appendChild(child));
        return node;
    },
    btn(text, className, onClick, attrs = {}) {
        const node = new FakeElement('button', { ...attrs, className });
        node.textContent = text || '';
        node.addEventListener('click', onClick || (() => {}));
        return node;
    },
};

global.Component = class Component {
    constructor(id) { this.container = document.getElementById(id); }
    _clear() { if (this.container) this.container.replaceChildren(); }
};

class EventBus {
    constructor() { this.handlers = new Map(); }
    subscribe(name, handler) {
        if (!this.handlers.has(name)) this.handlers.set(name, []);
        this.handlers.get(name).push(handler);
        return () => {};
    }
    publish(name, payload) { (this.handlers.get(name) || []).forEach(handler => handler(payload)); }
}

class FakeWebSocket {
    static OPEN = 1;
    constructor() { this.readyState = FakeWebSocket.OPEN; this.sent = []; FakeWebSocket.last = this; }
    send(value) { this.sent.push(value); }
    close() {}
}
global.WebSocket = FakeWebSocket;

function load(path) {
    vm.runInThisContext(fs.readFileSync(path, 'utf8'), { filename: path });
}

function assert(condition, message) {
    if (!condition) throw new Error(message);
}

const root = process.argv[2];
if (!root) throw new Error('Project root argument required');

load(`${root}/src/web/static/js/components/llmProblemCards.js`);

const eventBus = new EventBus();
let openedCall = null;
const cards = new LLMProblemCards(eventBus, { openCall(callId) { openedCall = callId; } });
eventBus.publish('llm_activity_snapshot', {
    events: [{
        event_id: 'evt-1', event_type: 'attempt_timeout', severity: 'error',
        title: 'LLM request timed out', message: 'Attempt 1 timed out',
        call_id: 'call-1', task: 'intent_routing', model: 'new-model', attempt: 1, max_attempts: 2
    }]
});
assert(cards._root.children.length === 1, 'First telemetry snapshot did not render a problem card');
cards._root.children[0].dispatch('click');
assert(openedCall === 'call-1', 'Problem card did not open the related diagnostics call');

const feed = new FakeElement('div', { id: 'chat-feed' });
const area = new FakeElement('div', { className: 'chat-input-area' });
const state = new FakeElement('div', { id: 'chat-command-state', className: 'chat-command-state is-idle' });
state.appendChild(new FakeElement('span', { className: 'chat-command-state-dot' }));
state.appendChild(Object.assign(new FakeElement('span', { className: 'chat-command-state-label' }), { textContent: 'Ready' }));
const input = new FakeElement('textarea', { id: 'chat-input' });
const send = new FakeElement('button', { id: 'send-btn', className: 'send-btn' });
area.append(state, input, send);
document.body.append(feed, area);

load(`${root}/src/web/static/js/components/chatController.js`);
const chat = new AssistantChat(eventBus);
input.value = 'download Silo';
chat.send();
assert(chat.isBusy === true, 'Chat did not enter busy state synchronously');
assert(input.disabled === true, 'Chat input stayed enabled while request was active');
assert(send.classList.contains('is-working'), 'Send button did not become the working/stop control');
assert(state.querySelector('.chat-command-state-label').textContent === 'LLM working', 'Visible chat state did not show LLM working');

eventBus.publish('chat_turn_state', { session_id: chat.sessionId, turn_id: chat.activeTurnId, state: 'idle' });
assert(chat.isBusy === false, 'Server chat state did not release busy state');
assert(input.disabled === false, 'Chat input stayed disabled after server idle state');
assert(!send.classList.contains('is-working'), 'Stop control did not return to Send');

async function verifyLegacySettingsContract() {
    const ids = {
        'provider-select': 'nvidia_nim', model: 'openai/gpt-oss-20b',
        api_base: 'https://integrate.api.nvidia.com/v1',
        'tier-lightweight-provider': '', 'tier-lightweight-model': 'old-router',
        'tier-standard-provider': '', 'tier-standard-model': '',
        'tier-heavy-provider': '', 'tier-heavy-model': '',
    };
    Object.entries(ids).forEach(([id, value]) => {
        const node = new FakeElement(id.includes('provider') ? 'select' : 'input', { id });
        node.value = value;
        document.body.appendChild(node);
    });
    const applyAll = new FakeElement('input', { id: 'llm-apply-base-all' });
    applyAll.checked = true;
    document.body.appendChild(applyAll);
    document.body.appendChild(new FakeElement('div', { id: 'legacy-effective-llm-routes' }));

    const requests = [];
    global.APIClient = {
        async post(path, body) {
            requests.push({ path, body });
            return {
                config_revision: 4,
                cancelled_old_route_calls: 0,
                routes: [{ task: 'intent_routing', model: body.model, source: 'global' }],
            };
        },
        async get() {
            return { llm_routing: { config_revision: 4, routes: [] } };
        },
    };
    global.toast = { show() {}, error(message) { throw new Error(message); } };
    load(`${root}/src/web/static/js/components/settingsSavers.js`);
    await saveLLM();
    assert(requests.length === 1, 'Standalone settings performed more than one LLM mutation');
    assert(requests[0].path === '/api/settings/llm', 'Standalone settings used a non-authoritative endpoint');
    assert(requests[0].body.apply_base_to_all === true, 'Standalone base-model change did not claim task ownership');
    assert(requests[0].body.tiers.lightweight.model === 'old-router', 'Atomic save omitted the visible tier state');
}

verifyLegacySettingsContract()
    .then(() => console.log('ROUND289_FRONTEND_CONTRACT_PASS'))
    .catch(error => {
        console.error(error && error.stack ? error.stack : error);
        process.exitCode = 1;
    });
