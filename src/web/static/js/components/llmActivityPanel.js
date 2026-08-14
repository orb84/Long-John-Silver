/**
 * Full-screen LLM diagnostics workspace.
 *
 * Provides at-a-glance telemetry, exact per-call payload inspection, retry
 * timelines, and bounded raw LLM/application logs. Exact prompts and schemas
 * are fetched only when the user selects a call.
 */
class LLMActivityPanel {
    /** Initialize diagnostics state, DOM, controls, and bounded polling. */
    constructor(eventBus = null) {
        this._eventBus = eventBus;
        this.toggle = document.getElementById('llm-activity-toggle');
        this.dialog = null;
        this.calls = [];
        this.selectedCallId = null;
        this.activeTab = 'activity';
        this.timer = null;
        this.detailCache = new Map();
        this.logCache = new Map();
        this.detailRequestId = 0;
        this.refreshInFlight = false;
        this._renderDialog();
        this._bind();
    }

    /** Start bounded telemetry polling after all notification subscribers exist. */
    start() {
        if (this.timer) return;
        this.refresh();
        this.timer = setInterval(() => this.refresh(), 1500);
    }

    _bind() {
        this.toggle?.addEventListener('click', () => this.open());
        document.getElementById('llm-diagnostics-close')?.addEventListener('click', () => this.close());
        document.getElementById('llm-diagnostics-backdrop')?.addEventListener('click', () => this.close());
        document.getElementById('llm-diagnostics-refresh')?.addEventListener('click', () => this.refreshActiveView(true));
        this.dialog?.querySelectorAll('[data-llm-diagnostics-tab]').forEach(button => {
            button.addEventListener('click', () => this.selectTab(button.dataset.llmDiagnosticsTab));
        });
        document.addEventListener('keydown', event => {
            if (event.key === 'Escape' && this.dialog?.classList.contains('is-open')) this.close();
        });
    }

    _renderDialog() {
        if (document.getElementById('llm-diagnostics-dialog')) {
            this.dialog = document.getElementById('llm-diagnostics-dialog');
            return;
        }
        const backdrop = DOM.el('div', { id: 'llm-diagnostics-backdrop', className: 'llm-diagnostics-backdrop' });
        const dialog = DOM.el('section', {
            id: 'llm-diagnostics-dialog',
            className: 'llm-diagnostics-dialog glass-panel',
            role: 'dialog',
            'aria-modal': 'true',
            'aria-hidden': 'true',
            'aria-label': 'LLM diagnostics'
        }, [
            DOM.el('header', { className: 'llm-diagnostics-header' }, [
                DOM.el('div', { className: 'llm-diagnostics-title' }, [
                    DOM.el('p', { className: 'llm-activity-eyebrow' }, ['MODEL TELEMETRY & LOGS']),
                    DOM.el('h2', {}, ['LLM Diagnostics']),
                    DOM.el('p', { className: 'llm-diagnostics-subtitle' }, [
                        'Live calls, retries, timeouts, context payloads, provider usage, and raw diagnostics.'
                    ]),
                    DOM.el('code', { className: 'llm-diagnostics-build-id' }, [
                        `Build ${document.querySelector('meta[name="ljs-build-id"]')?.content || 'unknown'}`
                    ])
                ]),
                DOM.el('div', { className: 'llm-diagnostics-header-actions' }, [
                    DOM.btn('', 'icon-btn', () => {}, {
                        id: 'llm-diagnostics-refresh',
                        title: 'Refresh diagnostics',
                        content: '<i class="fa-solid fa-rotate"></i>'
                    }),
                    DOM.btn('', 'icon-btn', () => {}, {
                        id: 'llm-diagnostics-close',
                        title: 'Close diagnostics',
                        content: '<i class="fa-solid fa-xmark"></i>'
                    })
                ])
            ]),
            DOM.el('nav', { className: 'llm-diagnostics-tabs', 'aria-label': 'LLM diagnostic views' }, [
                this._tabButton('activity', 'Activity', 'fa-wave-square'),
                this._tabButton('context', 'Context log', 'fa-align-left'),
                this._tabButton('responses', 'Raw responses', 'fa-message'),
                this._tabButton('routing', 'Routing log', 'fa-code-branch'),
                this._tabButton('turns', 'Turn lifecycle', 'fa-arrows-rotate'),
                this._tabButton('searches', 'Searches', 'fa-magnifying-glass'),
                this._tabButton('application', 'LLM app log', 'fa-terminal')
            ]),
            DOM.el('div', { id: 'llm-activity-summary', className: 'llm-activity-summary' }),
            DOM.el('main', { className: 'llm-diagnostics-main' }, [
                DOM.el('section', { id: 'llm-diagnostics-activity', className: 'llm-diagnostics-pane is-active' }, [
                    DOM.el('aside', { className: 'llm-activity-history-wrap' }, [
                        DOM.el('div', { className: 'llm-activity-section-title' }, ['Recent calls']),
                        DOM.el('div', { id: 'llm-activity-history', className: 'llm-activity-history' })
                    ]),
                    DOM.el('div', { className: 'llm-activity-detail-wrap' }, [
                        DOM.el('div', { id: 'llm-activity-detail', className: 'llm-activity-detail' }, [
                            DOM.el('div', { className: 'llm-activity-empty' }, ['No LLM call has been recorded yet.'])
                        ])
                    ])
                ]),
                DOM.el('section', { id: 'llm-diagnostics-context', className: 'llm-diagnostics-pane llm-log-pane' }, [
                    this._logHeader('Raw LLM context log', 'Exact bounded task/model context records with secrets redacted.', 'context'),
                    DOM.el('pre', { id: 'llm-context-log', className: 'llm-diagnostics-log' }, ['Open this tab to load the context log.'])
                ]),
                DOM.el('section', { id: 'llm-diagnostics-responses', className: 'llm-diagnostics-pane llm-log-pane' }, [
                    this._logHeader('Raw LLM response log', 'Unparsed model output captured before planner or router interpretation.', 'responses'),
                    DOM.el('pre', { id: 'llm-responses-log', className: 'llm-diagnostics-log' }, ['Open this tab to load raw responses.'])
                ]),
                DOM.el('section', { id: 'llm-diagnostics-routing', className: 'llm-diagnostics-pane llm-log-pane' }, [
                    this._logHeader('Structured routing log', 'Intent decisions, confidence, operational state, and routing errors.', 'routing'),
                    DOM.el('pre', { id: 'llm-routing-log', className: 'llm-diagnostics-log' }, ['Open this tab to load routing decisions.'])
                ]),
                DOM.el('section', { id: 'llm-diagnostics-turns', className: 'llm-diagnostics-pane llm-log-pane' }, [
                    this._logHeader('Chat turn lifecycle', 'Explicit received, started, cancel, completion, and failure events keyed by session and turn ID.', 'turns'),
                    DOM.el('pre', { id: 'llm-turns-log', className: 'llm-diagnostics-log' }, ['Open this tab to load chat turn events.'])
                ]),
                DOM.el('section', { id: 'llm-diagnostics-searches', className: 'llm-diagnostics-pane llm-log-pane' }, [
                    this._logHeader('Search execution log', 'Exact queries, provider timings, candidate counts, and the owning turn ID.', 'searches'),
                    DOM.el('pre', { id: 'llm-searches-log', className: 'llm-diagnostics-log' }, ['Open this tab to load search events.'])
                ]),
                DOM.el('section', { id: 'llm-diagnostics-application', className: 'llm-diagnostics-pane llm-log-pane' }, [
                    this._logHeader('LLM application log', 'Provider, routing, retry, timeout, and context-budget rows from ljs.log.', 'application'),
                    DOM.el('pre', { id: 'llm-application-log', className: 'llm-diagnostics-log' }, ['Open this tab to load the application log.'])
                ])
            ])
        ]);
        document.body.appendChild(backdrop);
        document.body.appendChild(dialog);
        this.dialog = dialog;
    }

    _tabButton(tab, label, icon) {
        return DOM.el('button', {
            type: 'button',
            className: `llm-diagnostics-tab${tab === 'activity' ? ' is-active' : ''}`,
            dataset: { llmDiagnosticsTab: tab }
        }, [
            DOM.el('i', { className: `fa-solid ${icon}` }),
            DOM.el('span', {}, [label])
        ]);
    }

    _logHeader(title, subtitle, source) {
        const lineSelect = DOM.el('select', {
            className: 'llm-log-line-select',
            dataset: { llmLogLines: source },
            'aria-label': `${title} line count`
        }, [
            DOM.el('option', { value: '200' }, ['200 lines']),
            DOM.el('option', { value: '500', selected: 'selected' }, ['500 lines']),
            DOM.el('option', { value: '1000' }, ['1,000 lines']),
            DOM.el('option', { value: '2000' }, ['2,000 lines'])
        ]);
        lineSelect.addEventListener('change', () => this.loadLog(source, true));
        return DOM.el('div', { className: 'llm-log-pane-header' }, [
            DOM.el('div', {}, [DOM.el('h3', {}, [title]), DOM.el('p', {}, [subtitle])]),
            lineSelect
        ]);
    }

    /** Refresh bounded activity summaries and any visible call detail. */
    async refresh() {
        if (this.refreshInFlight) return;
        this.refreshInFlight = true;
        try {
            const data = await APIClient.get('/api/system/llm-activity?limit=40');
            this.calls = Array.isArray(data.calls) ? data.calls : [];
            this._eventBus?.publish('llm_activity_snapshot', data);
            this._updateBadge(data);
            if (this.dialog?.classList.contains('is-open')) {
                this._renderSummary(data);
                if (this.activeTab === 'activity') await this._refreshActivityPane();
            }
        } catch (error) {
            this._markTelemetryUnavailable();
        } finally {
            this.refreshInFlight = false;
        }
    }

    /** Refresh the currently selected diagnostics view. */
    async refreshActiveView(force = false) {
        if (this.activeTab === 'activity') {
            if (force && this.selectedCallId) this.detailCache.delete(this.selectedCallId);
            await this.refresh();
            return;
        }
        await this.loadLog(this.activeTab, force);
    }

    async _refreshActivityPane() {
        this._renderHistory();
        const selected = this.selectedCallId || this.calls[0]?.call_id;
        if (!selected) return;
        const summary = this.calls.find(call => call.call_id === selected);
        const cached = this.detailCache.get(selected);
        const refreshDetail = !cached || summary?.status === 'running' || cached.status !== summary?.status;
        await this.selectCall(selected, { preserveHistory: true, refreshDetail });
    }

    _markTelemetryUnavailable() {
        if (!this.toggle) return;
        this.toggle.classList.remove('is-running', 'is-failed');
        this.toggle.classList.add('is-unavailable');
        this.toggle.replaceChildren(
            DOM.el('i', { className: 'fa-solid fa-triangle-exclamation' }),
            DOM.el('span', {}, ['LLM telemetry offline'])
        );
    }

    _updateBadge(data) {
        if (!this.toggle) return;
        const active = (data.active || [])[0];
        const last = data.last_call;
        this.toggle.classList.remove('is-running', 'is-failed', 'is-unavailable', 'is-idle');
        this.toggle.replaceChildren();
        if (active) {
            this.toggle.classList.add('is-running');
            this.toggle.append(
                DOM.el('i', { className: 'fa-solid fa-wave-square' }),
                DOM.el('span', {}, [`LLM ${this._duration(active.duration_seconds)} · ${this._tokens(this._promptTokens(active))} ctx`])
            );
            this.toggle.title = `${active.task || 'LLM'} is running. Open diagnostics.`;
        } else if (last?.status === 'failed') {
            this.toggle.classList.add('is-failed');
            this.toggle.append(
                DOM.el('i', { className: 'fa-solid fa-circle-exclamation' }),
                DOM.el('span', {}, [`LLM failed · ${this._duration(last.duration_seconds)}`])
            );
            this.toggle.title = 'The last LLM call failed. Open diagnostics.';
        } else {
            this.toggle.classList.add('is-idle');
            const suffix = last ? ` · ${this._duration(last.duration_seconds)}` : '';
            this.toggle.append(
                DOM.el('i', { className: 'fa-solid fa-microchip' }),
                DOM.el('span', {}, [`LLM idle${suffix}`])
            );
            this.toggle.title = 'Open LLM diagnostics.';
        }
    }

    /** Open the dedicated diagnostics workspace. */
    open() {
        if (!this.dialog) return;
        this.dialog.classList.add('is-open');
        document.getElementById('llm-diagnostics-backdrop')?.classList.add('is-open');
        this.dialog.setAttribute('aria-hidden', 'false');
        document.body.classList.add('llm-diagnostics-open');
        this.refreshActiveView();
    }

    /** Open diagnostics and focus the call associated with a notification. */
    async openCall(callId) {
        this.selectTab('activity');
        this.open();
        if (callId) await this.selectCall(callId, { refreshDetail: true });
    }

    /** Close diagnostics without stopping lightweight activity polling. */
    close() {
        this.dialog?.classList.remove('is-open');
        document.getElementById('llm-diagnostics-backdrop')?.classList.remove('is-open');
        this.dialog?.setAttribute('aria-hidden', 'true');
        document.body.classList.remove('llm-diagnostics-open');
    }

    /** Select the live activity or one of the persisted LLM log views. */
    selectTab(tab) {
        if (!['activity', 'context', 'responses', 'routing', 'turns', 'searches', 'application'].includes(tab)) return;
        this.activeTab = tab;
        this.dialog?.querySelectorAll('[data-llm-diagnostics-tab]').forEach(button => {
            button.classList.toggle('is-active', button.dataset.llmDiagnosticsTab === tab);
        });
        this.dialog?.querySelectorAll('.llm-diagnostics-pane').forEach(pane => pane.classList.remove('is-active'));
        document.getElementById(`llm-diagnostics-${tab}`)?.classList.add('is-active');
        if (tab === 'activity') this.refresh();
        else this.loadLog(tab);
    }

    /** Load one bounded secret-redacted diagnostic log source. */
    async loadLog(source, force = false) {
        const root = document.getElementById(`llm-${source}-log`);
        if (!root) return;
        const select = this.dialog?.querySelector(`[data-llm-log-lines="${source}"]`);
        const lines = Number(select?.value || 500);
        const cacheKey = `${source}:${lines}`;
        if (!force && this.logCache.has(cacheKey)) {
            root.textContent = this.logCache.get(cacheKey);
            return;
        }
        root.textContent = 'Loading diagnostics…';
        try {
            const data = await APIClient.get(`/api/system/llm-logs?source=${encodeURIComponent(source)}&lines=${lines}`);
            const text = Array.isArray(data.logs) ? data.logs.join('\n') : 'No diagnostics were returned.';
            this.logCache.set(cacheKey, text);
            root.textContent = text;
            root.scrollTop = root.scrollHeight;
        } catch (error) {
            root.textContent = `Unable to load diagnostics: ${error.message || error}`;
        }
    }

    _renderSummary(data) {
        const root = document.getElementById('llm-activity-summary');
        if (!root) return;
        const active = (data.active || [])[0];
        const last = active || data.last_call;
        const status = active ? 'Running' : (last ? this._title(last.status) : 'Idle');
        root.replaceChildren();
        [
            ['State', status],
            ['Task', last?.task || '—'],
            ['Last duration', last ? this._duration(last.duration_seconds) : '—'],
            ['Context', last ? this._tokens(this._promptTokens(last)) : '—']
        ].forEach(([label, value]) => root.appendChild(DOM.el('div', { className: 'llm-metric-card' }, [
            DOM.el('span', { className: 'llm-metric-label' }, [label]),
            DOM.el('strong', {}, [String(value)])
        ])));
    }

    _renderHistory() {
        const root = document.getElementById('llm-activity-history');
        if (!root) return;
        root.replaceChildren();
        if (!this.calls.length) {
            root.appendChild(DOM.el('div', { className: 'llm-activity-empty' }, ['No calls yet.']));
            return;
        }
        this.calls.forEach(call => {
            const button = DOM.el('button', {
                type: 'button',
                className: `llm-call-row${call.call_id === this.selectedCallId ? ' is-selected' : ''}`,
                dataset: { callId: call.call_id }
            }, [
                DOM.el('span', { className: `llm-call-state state-${call.status}` }),
                DOM.el('span', { className: 'llm-call-main' }, [
                    DOM.el('strong', {}, [call.task || 'LLM call']),
                    DOM.el('small', {}, [`${call.model || 'unknown model'} · ${this._time(call.started_at)}`])
                ]),
                DOM.el('span', { className: 'llm-call-duration' }, [this._duration(call.duration_seconds)])
            ]);
            button.addEventListener('click', () => this.selectCall(call.call_id));
            root.appendChild(button);
        });
    }

    /** Select a call and lazily retrieve its exact messages and tool schemas. */
    async selectCall(callId, { preserveHistory = false, refreshDetail = true } = {}) {
        if (!callId) return;
        this.selectedCallId = callId;
        if (!preserveHistory) this._renderHistory();
        const root = document.getElementById('llm-activity-detail');
        if (!root) return;
        const cached = this.detailCache.get(callId);
        if (cached && !refreshDetail) {
            this._renderDetail(cached);
            return;
        }
        if (!cached) root.replaceChildren(DOM.el('div', { className: 'llm-activity-empty' }, ['Loading call context…']));
        const requestId = ++this.detailRequestId;
        try {
            const data = await APIClient.get(`/api/system/llm-activity/${encodeURIComponent(callId)}`);
            if (requestId !== this.detailRequestId || this.selectedCallId !== callId) return;
            const call = data.call || {};
            this.detailCache.set(callId, call);
            while (this.detailCache.size > 12) this.detailCache.delete(this.detailCache.keys().next().value);
            this._renderDetail(call);
        } catch (error) {
            if (requestId !== this.detailRequestId || this.selectedCallId !== callId) return;
            if (cached) this._renderDetail(cached);
            else root.replaceChildren(DOM.el('div', { className: 'llm-activity-empty' }, [
                'This call is no longer in the bounded activity history.'
            ]));
        }
    }

    _renderDetail(call) {
        const root = document.getElementById('llm-activity-detail');
        if (!root) return;
        root.replaceChildren();
        const budget = call.budget || {};
        const payload = budget.payload || {};
        const promptEstimate = this._promptTokens(call);
        const metrics = [
            ['Status', this._title(call.status)], ['Task', call.task || '—'],
            ['Provider', call.provider || '—'], ['Model', call.model || '—'],
            ['Duration', this._duration(call.duration_seconds)], ['Messages', call.message_count ?? '—'],
            ['Tools', call.tool_count ?? '—'], ['Message payload', this._bytes(call.message_chars)],
            ['Tool schemas', this._bytes(call.tool_schema_chars)],
            ['Prompt tokens', call.prompt_tokens != null ? `${this._tokens(call.prompt_tokens)} actual` : `${this._tokens(promptEstimate)} estimated`],
            ['Estimated total', payload.total_tokens_estimated != null ? `${this._tokens(payload.total_tokens_estimated)} incl. output reserve` : '—'],
            ['Interactive target', budget.target_context_tokens != null ? this._tokens(budget.target_context_tokens) : '—'],
            ['Hard context ceiling', budget.provider_call_context_tokens != null ? this._tokens(budget.provider_call_context_tokens) : '—'],
            ['Selected/model window', budget.model_context_tokens != null ? this._tokens(budget.model_context_tokens) : '—'],
            ['Context policy', this._contextPolicy(budget, payload)],
            ['Output tokens', call.completion_tokens != null ? this._tokens(call.completion_tokens) : 'not reported']
        ];
        root.appendChild(DOM.el('div', { className: 'llm-call-detail-head' }, [
            DOM.el('div', {}, [
                DOM.el('p', { className: 'llm-activity-eyebrow' }, [call.turn_id ? `TURN ${call.turn_id.slice(0, 12)}` : 'BACKGROUND / INTERNAL']),
                DOM.el('h3', {}, [`${call.task || 'LLM'} · ${this._title(call.status)}`])
            ]),
            DOM.el('span', { className: `llm-detail-status status-${call.status}` }, [this._duration(call.duration_seconds)])
        ]));
        const grid = DOM.el('div', { className: 'llm-detail-grid' });
        metrics.forEach(([label, value]) => grid.appendChild(DOM.el('div', { className: 'llm-detail-row' }, [
            DOM.el('span', {}, [label]), DOM.el('strong', {}, [String(value)])
        ])));
        root.appendChild(grid);
        if (call.error) root.appendChild(DOM.el('div', { className: 'llm-call-error' }, [call.error]));
        root.appendChild(this._detailsBlock('Attempts and retries', call.attempts || [], true));
        root.appendChild(this._detailsBlock('Context budget and measured payload', call.budget || {}));
        root.appendChild(this._detailsBlock('Messages sent', call.context?.messages || []));
        root.appendChild(this._detailsBlock('Tool schemas sent', call.context?.tools || []));
        root.appendChild(this._detailsBlock('Generation settings', call.generation || {}));
    }

    _detailsBlock(title, value, open = false) {
        const details = DOM.el('details', { className: 'llm-context-block' });
        if (open) details.open = true;
        details.appendChild(DOM.el('summary', {}, [title]));
        details.appendChild(DOM.el('pre', {}, [JSON.stringify(value, null, 2) || 'No data.']));
        return details;
    }

    _promptTokens(call) {
        if (!call) return null;
        if (call.prompt_tokens != null) return call.prompt_tokens;
        return call.budget?.payload?.prompt_tokens_estimated ?? call.estimated_prompt_tokens;
    }

    _contextPolicy(budget, payload) {
        if (!budget || !Object.keys(budget).length) return 'not recorded';
        if (payload?.over_hard_limit) return 'hard limit exceeded · rejected';
        if (payload?.over_target) return 'soft target exceeded · allowed';
        return `within soft target · ${String(budget.context_cap_source || 'unknown').replaceAll('_', ' ')}`;
    }

    _duration(value) {
        const seconds = Number(value);
        if (!Number.isFinite(seconds)) return '—';
        if (seconds < 1) return `${Math.round(seconds * 1000)} ms`;
        if (seconds < 60) return `${seconds.toFixed(seconds < 10 ? 1 : 0)} s`;
        return `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`;
    }

    _tokens(value) {
        const tokens = Number(value);
        if (!Number.isFinite(tokens)) return '—';
        if (tokens >= 1000000) return `${(tokens / 1000000).toFixed(1)}M`;
        if (tokens >= 1000) return `${(tokens / 1000).toFixed(tokens >= 10000 ? 0 : 1)}k`;
        return `${Math.round(tokens)}`;
    }

    _bytes(chars) {
        const value = Number(chars);
        if (!Number.isFinite(value)) return '—';
        if (value >= 1000000) return `${(value / 1000000).toFixed(1)} MB`;
        if (value >= 1000) return `${(value / 1000).toFixed(1)} kB`;
        return `${value} B`;
    }

    _time(value) {
        if (!value) return '—';
        const date = new Date(value);
        return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleTimeString([], {
            hour: '2-digit', minute: '2-digit', second: '2-digit'
        });
    }

    _title(value) {
        const text = String(value || 'unknown').replaceAll('_', ' ');
        return text.charAt(0).toUpperCase() + text.slice(1);
    }
}

window.LLMActivityPanel = LLMActivityPanel;
