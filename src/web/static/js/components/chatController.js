/**
 * ChatController for LJS.
 *
 * Implements the Silver AI Terminal assistant chat client. Manages connection to
 * /ws/chat, streams tokens in real-time, persists chat session IDs, and renders
 * beautiful glassmorphic message bubbles.
 */

class AssistantChat extends Component {
    /**
     * Construct and initialize the AssistantChat instance.
     *
     * Keep DOM lookups local, prefer ActionClient/APIClient for server calls,
     * and preserve event names/data attributes so other components can extend
     * this behavior without reaching into private state.
     */
    constructor(eventBus = null) {
        // Option 1 chat feed elements
        super('chat-feed');
        if (!this.container) return;

        this._eventBus = eventBus;
        this.input = document.getElementById('chat-input');
        this.sendBtn = document.getElementById('send-btn');
        this.sessionId = localStorage.getItem('ljs_chat_session') || ('web_' + generateUUID());
        localStorage.setItem('ljs_chat_session', this.sessionId);
        
        this.ws = null;
        this.assistantBubble = null;
        this.assistantRawMarkdown = '';
        this.isBusy = false;
        this.activeTurnId = null;
        this.httpAbortController = null;

        this._init();
        this._subscribeServerState();
        this._loadHistory();
        this._renderCommandState('idle');
    }

    /**
     * Bind input triggers and initiate WebSocket chat socket.
     * @private
     */
    _init() {
        this.connect();

        if (this.sendBtn) {
            this.sendBtn.onclick = () => this.isBusy ? this.cancel() : this.send();
        }

        if (this.input) {
            this._resizeInput();
            this.input.addEventListener('input', () => this._resizeInput());
            this.input.onkeydown = (e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault();
                    this.send();
                }
            };
        }
    }

    /**
     * Restore chat history from local storage.
     * @private
     */
    _loadHistory() {
        const raw = localStorage.getItem('ljs_chat_history');
        if (raw) {
            try {
                const history = JSON.parse(raw);
                if (Array.isArray(history) && history.length > 0) {
                    this._clear();
                    history.forEach(msg => {
                        this._appendMsg(msg.role, msg.content, false);
                    });
                }
            } catch (ex) {
                console.error('[AssistantChat] Failed to load chat history:', ex);
            }
        }
    }

    /**
     * Save a message to history in localStorage.
     * @param {string} role
     * @param {string} content
     * @private
     */
    _saveMessage(role, content) {
        try {
            const raw = localStorage.getItem('ljs_chat_history');
            let history = [];
            if (raw) {
                history = JSON.parse(raw);
            }
            if (!Array.isArray(history)) {
                history = [];
            }
            history.push({ role, content });
            localStorage.setItem('ljs_chat_history', JSON.stringify(history));
        } catch (ex) {
            console.error('[AssistantChat] Failed to save chat message:', ex);
        }
    }

    /**
     * Clear all chat history, reset session ID, and restore default welcome message.
     */
    async clearChat() {
        const ok = await ljsConfirm('Clear the chat history and start a fresh session?', { title: 'Clear Chat', confirmText: 'Clear' });
        if (!ok) return;

        localStorage.removeItem('ljs_chat_history');
        this.sessionId = 'web_' + generateUUID();
        localStorage.setItem('ljs_chat_session', this.sessionId);

        this._clear();
        
        const welcomeBubble = DOM.el('div', { className: 'msg-bubble' }, ['Ahoy Captain. The trackers are primed and the seas are calm. What are we hunting today?']);
        const welcomeMsg = DOM.el('div', { className: 'message system' }, [welcomeBubble]);
        this.container.appendChild(welcomeMsg);

        this.assistantBubble = null;
    }

    /**
     * Connect to `/ws/chat` endpoint.
     */
    connect() {
        const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
        const url = `${proto}//${location.host}/ws/chat`;
        
        console.log(`[AssistantChat] Establishing stream to ${url}...`);
        this.ws = new WebSocket(url);

        this.ws.onmessage = (e) => {
            try {
                const data = JSON.parse(e.data);
                if (data.turn_id && this.activeTurnId && data.turn_id !== this.activeTurnId) {
                    return;
                }
                if (data.type === 'started') {
                    this._setBusy(true, data.turn_id || this.activeTurnId, 'working');
                } else if (data.type === 'token') {
                    this._clearProcessingIndicator();
                    this._appendToken(data.content);
                } else if (data.type === 'status') {
                    this._appendMsg('status', data.content || '…', false);
                    this._showProcessingIndicator();
                } else if (data.type === 'done') {
                    this._clearProcessingIndicator();
                    if (this.assistantBubble) {
                        this._saveMessage('assistant', this.assistantRawMarkdown || this.assistantBubble.textContent);
                    }
                    this.assistantBubble = null;
                    this.assistantRawMarkdown = '';
                    this._setBusy(false, null, 'idle');
                } else if (data.type === 'error') {
                    this._clearProcessingIndicator();
                    this._appendMsg('system', data.content || 'An assistant error occurred.');
                    this.assistantBubble = null;
                    this.assistantRawMarkdown = '';
                    this._setBusy(false, null, 'failed');
                } else if (data.type === 'cancelled') {
                    this._clearProcessingIndicator();
                    if (this.assistantBubble && this.assistantRawMarkdown) {
                        this._saveMessage('assistant', this.assistantRawMarkdown);
                    }
                    this.assistantBubble = null;
                    this.assistantRawMarkdown = '';
                    this._appendMsg('status', data.content || 'Request stopped.', false);
                    this._setBusy(false, null, 'cancelled');
                } else if (data.type === 'busy') {
                    this._appendMsg('status', data.content || 'The previous request is still running.', false);
                    this._setBusy(true, data.turn_id || this.activeTurnId, 'working');
                }
            } catch (ex) {
                console.error('[AssistantChat] Message frame error:', ex);
            }
        };

        this.ws.onclose = () => {
            console.warn('[AssistantChat] Connection closed. Retrying in 4s...');
            if (this.isBusy) {
                this._clearProcessingIndicator();
                this._appendMsg('system', 'The assistant connection closed before the request completed.', false);
                this._setBusy(false, null, 'failed');
            }
            setTimeout(() => this.connect(), 4000);
        };
    }

    /**
     * Display a glassmorphic bouncing dots processing indicator.
     * @private
     */
    _showProcessingIndicator() {
        if (!this.container) return;
        this._clearProcessingIndicator();
        
        const dots = DOM.el('div', { className: 'thinking-dots' }, [
            DOM.el('span', { className: 'dot' }),
            DOM.el('span', { className: 'dot' }),
            DOM.el('span', { className: 'dot' })
        ]);
        const bubble = DOM.el('div', { className: 'msg-bubble msg-processing' }, [dots]);
        const msgDiv = DOM.el('div', { className: 'message system processing-container' }, [bubble]);
        
        this.container.appendChild(msgDiv);
        this.container.scrollTop = this.container.scrollHeight;
    }

    /**
     * Clear active processing indicators.
     * @private
     */
    _clearProcessingIndicator() {
        if (!this.container) return;
        this.container.querySelectorAll('.processing-container').forEach(el => el.remove());
    }

    /**
     * Appends a message bubble to the chat feed container.
     * @param {string} role - 'user', 'system', or 'assistant'
     * @param {string} text - Message text
     * @param {boolean} [save=true] - Whether to save message to history
     * @returns {HTMLElement} The created msg bubble element
     * @private
     */
    _appendMsg(role, text, save = true) {
        if (!this.container) return null;

        // map standard class names
        const classRole = (role === 'assistant' || role === 'status') ? 'system' : role; 
        
        const bubble = DOM.el('div', { className: `msg-bubble${role === 'status' ? ' msg-status' : ''}` });
        bubble.innerHTML = (role === 'assistant' || role === 'system' || classRole === 'system') && window.marked ? marked.parse(text) : text;
        const msgDiv = DOM.el('div', { className: `message ${classRole}${role === 'status' ? ' status' : ''}` }, [bubble]);

        this.container.appendChild(msgDiv);
        this.container.scrollTop = this.container.scrollHeight;

        if (save) {
            this._saveMessage(role, text);
        }

        return bubble;
    }

    /**
     * Streams incoming tokens.
     * @param {string} text - Next character token.
     * @private
     */
    _appendToken(text) {
        this._clearProcessingIndicator();
        if (!this.assistantRawMarkdown) {
            this.assistantRawMarkdown = '';
        }
        this.assistantRawMarkdown += text;
        
        if (this.assistantBubble) {
            this.assistantBubble.innerHTML = window.marked ? marked.parse(this.assistantRawMarkdown) : this.assistantRawMarkdown;
        } else {
            this.assistantBubble = this._appendMsg('assistant', this.assistantRawMarkdown, false);
        }
        this.container.scrollTop = this.container.scrollHeight;
    }

    /**
     * Auto-grow the command box up to four visible lines, then let it scroll.
     * Shift+Enter inserts a newline; Enter alone still sends.
     * @private
     */
    _resizeInput() {
        if (!this.input) return;
        const style = window.getComputedStyle(this.input);
        const lineHeight = parseFloat(style.lineHeight) || 20;
        const verticalPadding = (parseFloat(style.paddingTop) || 0) + (parseFloat(style.paddingBottom) || 0);
        const maxHeight = Math.ceil((lineHeight * 4) + verticalPadding);
        this.input.style.height = 'auto';
        const nextHeight = Math.min(this.input.scrollHeight, maxHeight);
        this.input.style.height = `${nextHeight}px`;
        this.input.style.overflowY = this.input.scrollHeight > maxHeight ? 'auto' : 'hidden';
    }

    /**
     * Send user chat message.
     */
    async send() {
        if (this.isBusy) return;
        const msg = this.input.value.trim();
        if (!msg) return;

        const turnId = generateUUID();
        this._setBusy(true, turnId, 'working');

        // Render user message bubble
        this._appendMsg('user', msg);
        this.input.value = '';
        this._resizeInput();
        
        // Show processing indicator
        this._showProcessingIndicator();

        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
            this.ws.send(JSON.stringify({
                type: 'message',
                message: msg,
                session_id: this.sessionId,
                turn_id: turnId,
                client_asset_version: this._assetVersion()
            }));
        } else {
            // Degrade to HTTP rest client if WebSocket is disconnected
            this.httpAbortController = new AbortController();
            let terminalState = 'idle';
            try {
                const response = await fetch('/api/chat', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    credentials: 'same-origin',
                    body: JSON.stringify({
                        message: msg,
                        session_id: this.sessionId,
                        turn_id: turnId,
                        client_asset_version: this._assetVersion()
                    }),
                    signal: this.httpAbortController.signal
                });
                if (!response.ok) throw new Error(`HTTP ${response.status}`);
                const data = await response.json();
                this._clearProcessingIndicator();
                if (data?.cancelled) {
                    terminalState = 'cancelled';
                    this._appendMsg('status', data.response || 'Request stopped.', false);
                } else if (data?.busy) {
                    terminalState = 'failed';
                    this._appendMsg('status', data.response || 'Another request is still running.', false);
                } else {
                    this._appendMsg('assistant', data.response);
                }
            } catch (err) {
                this._clearProcessingIndicator();
                if (err && err.name === 'AbortError') {
                    terminalState = 'cancelled';
                    this._appendMsg('status', 'Request stopped.', false);
                } else {
                    terminalState = 'failed';
                    this._appendMsg('system', 'System interface offline. Could not contact the AI assistant.');
                }
            } finally {
                this.httpAbortController = null;
                this._setBusy(false, null, terminalState);
            }
        }
    }

    /** Stop the active assistant turn and keep the chat session usable. */
    async cancel() {
        if (!this.isBusy) return;
        if (this.httpAbortController) {
            this.sendBtn?.classList.add('is-stopping');
            this._renderCommandState('stopping');
            try {
                const response = await fetch('/api/chat/cancel', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    credentials: 'same-origin',
                    body: JSON.stringify({
                        session_id: this.sessionId,
                        turn_id: this.activeTurnId,
                        client_asset_version: this._assetVersion()
                    })
                });
                const data = response.ok ? await response.json() : null;
                if (data?.cancellation_requested && data?.settled) {
                    // The server task is gone, so aborting the original REST
                    // response is now only local cleanup and cannot create
                    // hidden background work.
                    this.httpAbortController.abort();
                } else if (data?.cancellation_requested) {
                    // Keep the original REST request alive while the server is
                    // still unwinding. Its eventual cancelled response (or the
                    // shared state event) releases the UI truthfully.
                    this._appendMsg('status', 'Stop requested; cleanup is still finishing.', false);
                } else {
                    this._appendMsg('status', 'Could not confirm that the active request was stopped yet.', false);
                }
            } catch (_) {
                // Do not abort the local request when the cancellation endpoint
                // itself failed: that would make the UI look idle while the
                // server could still be searching in the background.
                this._appendMsg('status', 'Could not confirm the stop request; the current request is still being tracked.', false);
            }
            return;
        }
        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
            this.ws.send(JSON.stringify({
                type: 'cancel',
                session_id: this.sessionId,
                turn_id: this.activeTurnId,
                client_asset_version: this._assetVersion()
            }));
            this.sendBtn?.classList.add('is-stopping');
            this._renderCommandState('stopping');
            if (this.sendBtn) {
                this.sendBtn.title = 'Stopping request…';
                this.sendBtn.setAttribute('aria-label', 'Stopping request');
            }
        }
    }

    _subscribeServerState() {
        this._eventBus?.subscribe('chat_turn_state', payload => {
            if (!payload || payload.session_id !== this.sessionId) return;
            const state = String(payload.state || 'idle');
            const active = state === 'working' || state === 'stopping';
            this._setBusy(active, payload.turn_id || this.activeTurnId, state);
        });
    }

    _assetVersion() {
        return document.querySelector('meta[name="ljs-asset-version"]')?.content || '';
    }

    _renderCommandState(state) {
        const normalized = ['working', 'stopping', 'failed', 'cancelled', 'idle'].includes(state) ? state : 'idle';
        const root = document.getElementById('chat-command-state');
        const area = this.input?.closest('.chat-input-area');
        if (area) area.dataset.chatState = normalized;
        if (!root) return;
        root.className = `chat-command-state is-${normalized}`;
        const labels = {
            working: 'LLM working', stopping: 'Stopping', failed: 'Request failed',
            cancelled: 'Stopped', idle: 'Ready'
        };
        const label = root.querySelector('.chat-command-state-label');
        if (label) label.textContent = labels[normalized] || labels.idle;
    }

    /** Update the command controls for idle/running/stopping states. */
    _setBusy(busy, turnId = null, state = null) {
        this.isBusy = Boolean(busy);
        this.activeTurnId = this.isBusy ? (turnId || this.activeTurnId) : null;
        this._renderCommandState(state || (this.isBusy ? 'working' : 'idle'));
        if (this.input) {
            this.input.disabled = this.isBusy;
            this.input.setAttribute('aria-busy', this.isBusy ? 'true' : 'false');
            this.input.placeholder = this.isBusy ? 'Assistant is working…' : 'Give your orders, Captain...';
        }
        if (this.sendBtn) {
            this.sendBtn.disabled = false;
            this.sendBtn.classList.toggle('is-working', this.isBusy);
            this.sendBtn.classList.remove('is-stopping');
            if (this.isBusy) {
                this.sendBtn.innerHTML = '<span class="send-working-ring"></span><i class="fa-solid fa-stop"></i>';
                this.sendBtn.title = 'Stop current request';
                this.sendBtn.setAttribute('aria-label', 'Stop current request');
            } else {
                this.sendBtn.innerHTML = '<i class="fa-solid fa-paper-plane"></i>';
                this.sendBtn.title = 'Send message';
                this.sendBtn.setAttribute('aria-label', 'Send message');
            }
        }
        if (!this.isBusy && this.input) this.input.focus({ preventScroll: true });
    }
}

window.AssistantChat = AssistantChat;
