/**
 * Assistant chat component for LJS.
 *
 * Manages the chat window WebSocket connection to /ws/chat, handles
 * message send/receive, token streaming, and session persistence.
 */

class AssistantChat extends Component {
    /**
     * Construct and initialize the AssistantChat instance.
     *
     * Keep DOM lookups local, prefer ActionClient/APIClient for server calls,
     * and preserve event names/data attributes so other components can extend
     * this behavior without reaching into private state.
     */
    constructor() {
        super('ship-wheel');
        if (!this.container) return;
        this.window = document.getElementById('chat-window');
        this.input = document.getElementById('chat-input');
        this.sendBtn = document.getElementById('send-btn');
        this.sessionId = localStorage.getItem('ljs_chat_session') || ('web_' + generateUUID());
        localStorage.setItem('ljs_chat_session', this.sessionId);
        this.ws = null;
        this.assistantBubble = null;
        this._init();
    }
    _init() {
        this.connect();
        if (this.sendBtn) this.sendBtn.onclick = () => this.send();
        if (this.input) this.input.onkeydown = (e) => { if (e.key === 'Enter') this.send(); };
    }
    /**
     * Run the public connect interaction for AssistantChat.
     *
     * Keep DOM lookups local, prefer ActionClient/APIClient for server calls,
     * and preserve event names/data attributes so other components can extend
     * this behavior without reaching into private state.
     */
    connect() {
        const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
        this.ws = new WebSocket(`${proto}//${location.host}/ws/chat`);
        this.ws.onmessage = (e) => {
            try {
                const data = JSON.parse(e.data);
                if (data.type === 'token') this._appendToken(data.content);
                else if (data.type === 'status') this._appendMsg('assistant', data.content || '…');
                else if (data.type === 'done') this.assistantBubble = null;
            } catch (ex) {}
        };
    }
    _appendMsg(role, text) {
        if (!this.window) return;
        const el = DOM.el('div', { className: `msg msg-${role}` }, [text]);
        this.window.appendChild(el);
        this.window.scrollTop = this.window.scrollHeight;
        return el;
    }
    _appendToken(text) {
        if (this.assistantBubble) this.assistantBubble.textContent += text;
        else this.assistantBubble = this._appendMsg('assistant', text);
        this.window.scrollTop = this.window.scrollHeight;
    }
    /**
     * Run the public send interaction for AssistantChat.
     *
     * Keep DOM lookups local, prefer ActionClient/APIClient for server calls,
     * and preserve event names/data attributes so other components can extend
     * this behavior without reaching into private state.
     */
    async send() {
        const msg = this.input.value.trim();
        if (!msg) return;
        this._appendMsg('user', msg);
        this.input.value = '';
        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
            this.ws.send(JSON.stringify({ message: msg, session_id: this.sessionId }));
        } else {
            try {
                const data = await APIClient.post('/api/chat', { message: msg, session_id: this.sessionId });
                this._appendMsg('assistant', data.response);
            } catch (e) { this._appendMsg('system', 'Assistant unreachable.'); }
        }
    }
}

window.AssistantChat = AssistantChat;
