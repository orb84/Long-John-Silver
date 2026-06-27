/**
 * ReleaseWatchPanel component.
 *
 * Read-only diagnostics for the generic release-watch subsystem.  The panel is
 * intentionally category-neutral: it renders the category id, item id, unit key,
 * typed status, retry timing, and category-provided requirements/payload without
 * interpreting category-specific unit semantics in the frontend shell.
 */
class ReleaseWatchPanel extends Component {
    constructor(elementId, eventBus) {
        super(elementId);
        this._eventBus = eventBus;
        this._refreshTimer = null;
        this._statusFilter = '';
        this._inFlightLoad = null;
        this._needsRefreshOnVisible = false;
        if (this.container) {
            this.render();
            this.load();
            this._initEvents();
        }
    }

    render() {
        this._clear();
        const header = DOM.el('div', { className: 'release-watch-header' }, [
            DOM.el('div', {}, [
                DOM.el('p', { className: 'eyebrow' }, ['Release Watches']),
                DOM.el('h2', {}, ['Airing & Retry Watch']),
                DOM.el('p', { className: 'muted' }, [
                    'Category-owned watches for future releases or availability windows. Shows what LJS is retrying and why.'
                ])
            ]),
            DOM.el('div', { className: 'release-watch-actions' }, [
                DOM.el('select', { id: 'release-watch-status-filter', className: 'lib-select', 'aria-label': 'Filter release watches by status' }, [
                    DOM.el('option', { value: '' }, ['All statuses']),
                    DOM.el('option', { value: 'pending' }, ['Pending']),
                    DOM.el('option', { value: 'failed_retryable' }, ['Retryable']),
                    DOM.el('option', { value: 'candidate_found' }, ['Candidate found']),
                    DOM.el('option', { value: 'queued' }, ['Queued']),
                    DOM.el('option', { value: 'completed' }, ['Completed']),
                    DOM.el('option', { value: 'expired' }, ['Expired']),
                    DOM.el('option', { value: 'cancelled' }, ['Cancelled'])
                ]),
                DOM.btn('Refresh', 'btn-gold btn-sm', () => this.load(), { id: 'release-watch-refresh' })
            ])
        ]);
        const summary = DOM.el('div', { id: 'release-watch-summary', className: 'release-watch-summary' }, []);
        const list = DOM.el('div', { id: 'release-watch-list', className: 'release-watch-list' }, [
            DOM.el('p', { className: 'empty-msg' }, ['Loading release watches...'])
        ]);
        this.container.appendChild(header);
        this.container.appendChild(summary);
        this.container.appendChild(list);
    }

    _initEvents() {
        const filter = document.getElementById('release-watch-status-filter');
        if (filter) {
            filter.addEventListener('change', () => {
                this._statusFilter = filter.value || '';
                this.load();
            });
        }
        if (this._eventBus && typeof this._eventBus.subscribe === 'function') {
            this._eventBus.subscribe('system', (event) => {
                if (!event || !['category_item_added', 'category_item_removed', 'download_completed'].includes(event.subtype)) return;
                if (this._isVisible()) this.load({ quiet: true });
                else this._needsRefreshOnVisible = true;
            });
            this._eventBus.subscribe('ui:visibility', (state) => {
                if (state && state.visible && this._needsRefreshOnVisible && this._isVisible()) {
                    this._needsRefreshOnVisible = false;
                    this.load({ quiet: true });
                }
            });
            this._eventBus.subscribe('view:changed', () => {
                if (this._needsRefreshOnVisible && this._isVisible()) {
                    this._needsRefreshOnVisible = false;
                    this.load({ quiet: true });
                }
            });
        }
        if (window.ljsPerf) {
            this._refreshTimer = window.ljsPerf.registerAdaptiveInterval(() => this.load({ quiet: true }), {
                foregroundMs: 60000,
                backgroundMs: 180000,
                initialDelayMs: 60000,
                shouldRun: () => this._isVisible()
            });
        } else {
            this._refreshTimer = setInterval(() => {
                if (this._isVisible()) this.load({ quiet: true });
            }, 60000);
        }
    }

    async load({ quiet = false } = {}) {
        const list = document.getElementById('release-watch-list');
        if (!list || typeof APIClient === 'undefined') return;
        if (this._inFlightLoad) return this._inFlightLoad;
        if (!quiet) {
            list.innerHTML = '';
            list.appendChild(DOM.el('p', { className: 'empty-msg' }, ['Loading release watches...']));
        }
        this._inFlightLoad = (async () => {
            try {
                const params = new URLSearchParams({ limit: '100' });
                if (this._statusFilter) params.set('status', this._statusFilter);
                const data = await APIClient.get(`/api/release-watches?${params.toString()}`);
                this._renderSummary(data.status_counts || {}, data.count || 0);
                this._renderList(Array.isArray(data.watches) ? data.watches : []);
            } catch (err) {
                list.innerHTML = '';
                list.appendChild(DOM.el('div', { className: 'release-watch-error' }, [
                    `Release watches unavailable: ${err.message || err}`
                ]));
            } finally {
                this._inFlightLoad = null;
            }
        })();
        return this._inFlightLoad;
    }

    _isVisible() {
        const suggestions = document.getElementById('suggestions');
        if (!suggestions || !suggestions.classList.contains('active')) return false;
        return !window.ljsPerf || window.ljsPerf.isVisible();
    }

    _renderSummary(counts, count) {
        const summary = document.getElementById('release-watch-summary');
        if (!summary) return;
        summary.innerHTML = '';
        const order = ['pending', 'failed_retryable', 'candidate_found', 'queued', 'completed', 'expired', 'cancelled'];
        summary.appendChild(this._summaryPill('total', count));
        order.forEach(status => {
            if (counts[status]) summary.appendChild(this._summaryPill(status, counts[status]));
        });
    }

    _summaryPill(label, value) {
        return DOM.el('span', { className: `release-watch-pill is-${this._safeClass(label)}` }, [
            `${this._label(label)}: ${value}`
        ]);
    }

    _renderList(watches) {
        const list = document.getElementById('release-watch-list');
        if (!list) return;
        list.innerHTML = '';
        if (!watches.length) {
            list.appendChild(DOM.el('p', { className: 'empty-msg' }, ['No release watches match this filter.']));
            return;
        }
        const fragment = document.createDocumentFragment();
        watches.forEach(watch => fragment.appendChild(this._watchCard(watch)));
        list.appendChild(fragment);
    }

    _watchCard(watch) {
        const requirements = watch.requirements || {};
        const payload = watch.payload || {};
        const outcome = watch.last_outcome || {};
        const candidate = watch.last_candidate_summary || {};
        const metaRows = [
            ['Category', watch.category_id],
            ['Item', watch.item_id],
            ['Unit', watch.unit_key],
            ['Language', watch.preferred_language || requirements.preferred_language],
            ['Cadence', watch.cadence_profile],
            ['Next check', this._formatTime(watch.next_check_at)],
            ['Air window', this._formatTime(watch.expected_air_at) || this._formatTime(watch.watch_start_at)],
            ['Expires', this._formatTime(watch.expires_at)],
            ['Attempts', watch.attempts],
            ['Last error', watch.last_error]
        ].filter(([, value]) => value !== undefined && value !== null && String(value).trim() !== '');

        const card = DOM.el('article', { className: `release-watch-card is-${this._safeClass(watch.status)}` }, [
            DOM.el('div', { className: 'release-watch-card-top' }, [
                DOM.el('div', {}, [
                    DOM.el('strong', { className: 'release-watch-title' }, [`${watch.item_id || 'Unknown item'} ${watch.unit_key || ''}`.trim()]),
                    DOM.el('small', {}, [`${watch.category_id || 'category'} · updated ${this._formatTime(watch.updated_at) || 'unknown'}`])
                ]),
                DOM.el('span', { className: `release-watch-status is-${this._safeClass(watch.status)}` }, [this._label(watch.status || 'unknown')])
            ]),
            DOM.el('div', { className: 'release-watch-meta' }, metaRows.map(([key, value]) => DOM.el('div', {}, [
                DOM.el('span', {}, [key]),
                DOM.el('b', {}, [String(value)])
            ])))
        ]);

        const detailBits = [];
        const requirementText = this._compactJson(requirements);
        const payloadText = this._compactJson(payload);
        const outcomeText = this._compactJson(outcome);
        const candidateText = this._compactJson(candidate);
        if (requirementText) detailBits.push(['Requirements', requirementText]);
        if (candidateText) detailBits.push(['Candidate', candidateText]);
        if (outcomeText) detailBits.push(['Last outcome', outcomeText]);
        if (payloadText) detailBits.push(['Payload', payloadText]);
        if (detailBits.length) {
            const details = DOM.el('details', { className: 'release-watch-details' }, [
                DOM.el('summary', {}, ['Details']),
                ...detailBits.map(([label, text]) => DOM.el('div', { className: 'release-watch-detail-row' }, [
                    DOM.el('span', {}, [label]),
                    DOM.el('code', {}, [text])
                ]))
            ]);
            card.appendChild(details);
        }
        return card;
    }

    _formatTime(value) {
        const text = String(value || '').trim();
        if (!text) return '';
        const date = new Date(text);
        if (Number.isNaN(date.getTime())) return text;
        return date.toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' });
    }

    _compactJson(value) {
        if (!value || typeof value !== 'object' || !Object.keys(value).length) return '';
        try { return JSON.stringify(value); } catch (_) { return String(value); }
    }

    _safeClass(value) {
        return String(value || 'unknown').toLowerCase().replace(/[^a-z0-9_-]+/g, '-');
    }

    _label(value) {
        return String(value || 'unknown').replace(/_/g, ' ').replace(/\b\w/g, ch => ch.toUpperCase());
    }
}

window.ReleaseWatchPanel = ReleaseWatchPanel;
