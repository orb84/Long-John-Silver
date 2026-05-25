/**
 * SharingPanel component for LJS.
 *
 * Renders the opt-in seed-in-place library sharing view. It is intentionally a
 * read/status panel: changes to policy are made in Compass so the dedicated
 * sharing view can stay focused on ratios, quotas, paths, and swarm health.
 */
class SharingPanel extends Component {
    /**
     * Construct the sharing panel and bind event refreshes.
     *
     * @param {string} elementId - Container ID, normally ``sharing``.
     * @param {EventBus} eventBus - Shared app event bus.
     */
    constructor(elementId, eventBus) {
        super(elementId);
        this._eventBus = eventBus;
        this._snapshot = null;
        if (this.container) {
            this.renderSkeleton();
            this.load();
            this._bindEvents();
        }
    }

    /**
     * Render the panel chrome before data is loaded.
     */
    renderSkeleton() {
        this._clear();
        this.container.appendChild(DOM.el('section', { className: 'sharing-page glass-panel' }, [
            DOM.el('div', { className: 'sharing-hero' }, [
                DOM.el('div', {}, [
                    DOM.el('p', { className: 'eyebrow' }, ['Fair Share']),
                    DOM.el('h2', {}, ['Library Sharing']),
                    DOM.el('p', { className: 'muted' }, ['Seed-in-place torrents that remain connected to files in your library.'])
                ]),
                DOM.el('button', { className: 'quick-btn btn-secondary', type: 'button', onclick: () => this.load() }, [
                    DOM.el('i', { className: 'fa-solid fa-rotate' }), ' Refresh'
                ])
            ]),
            DOM.el('div', { id: 'sharing-summary', className: 'sharing-summary-grid' }, []),
            DOM.el('div', { id: 'sharing-list', className: 'sharing-list' }, [
                DOM.el('p', { className: 'empty-msg' }, ['Checking library shares...'])
            ])
        ]));
    }

    /**
     * Load sharing state from the backend and render it.
     */
    async load() {
        try {
            this._snapshot = await APIClient.get('/api/sharing/library');
            this.render();
        } catch (err) {
            this._renderError(err);
        }
    }

    /**
     * Render the current backend snapshot.
     */
    render() {
        if (!this._snapshot) return;
        const summaryEl = document.getElementById('sharing-summary');
        const listEl = document.getElementById('sharing-list');
        if (!summaryEl || !listEl) return;
        const summary = this._snapshot.summary || {};
        const policy = this._snapshot.policy || {};
        summaryEl.innerHTML = '';
        summaryEl.appendChild(this._summaryCard('Status', summary.enabled ? 'Enabled' : 'Disabled', policy.mode || 'disabled'));
        summaryEl.appendChild(this._summaryCard('Library upload cap', this._formatCap(summary.library_upload_speed_kbps || 0), 'separate from downloads'));
        summaryEl.appendChild(this._summaryCard('Active seed slots', String(summary.active_seed_slots || 0), 'library torrents'));
        summaryEl.appendChild(this._summaryCard('Uploaded', formatBytes(summary.uploaded_bytes || 0), this._formatRate(summary.active_upload_bps || 0) + ' now'));

        const items = this._snapshot.items || [];
        listEl.innerHTML = '';
        if (!items.length) {
            listEl.appendChild(DOM.el('div', { className: 'sharing-empty' }, [
                DOM.el('i', { className: 'fa-solid fa-seedling' }),
                DOM.el('h3', {}, ['No library torrents are being shared yet.']),
                DOM.el('p', {}, ['Enable seed-in-place sharing in Compass or during setup, then new torrent-backed library files will appear here.'])
            ]));
            return;
        }
        items.forEach(item => listEl.appendChild(this._itemCard(item)));
    }

    /**
     * Bind live events so the panel refreshes when torrent stats move.
     * @private
     */
    _bindEvents() {
        if (!this._eventBus || !this._eventBus.subscribe) return;
        this._eventBus.subscribe('download', event => {
            if (event && ['stats', 'completed', 'paused', 'resumed'].includes(event.subtype)) {
                window.clearTimeout(this._refreshTimer);
                this._refreshTimer = window.setTimeout(() => this.load(), 1200);
            }
        });
    }

    /**
     * Create a summary metric card.
     * @private
     */
    _summaryCard(label, value, hint) {
        return DOM.el('div', { className: 'sharing-summary-card' }, [
            DOM.el('span', { className: 'sharing-summary-label' }, [label]),
            DOM.el('strong', {}, [value]),
            DOM.el('small', {}, [hint || ''])
        ]);
    }

    /**
     * Render one shared library torrent row.
     * @private
     */
    _itemCard(item) {
        const ratio = Number(item.seed_ratio || 0).toFixed(2);
        const path = item.save_path || item.file_path || 'Unknown path';
        const titleBits = [item.item_name || item.torrent_title || item.id];
        if (item.season) titleBits.push(`S${String(item.season).padStart(2, '0')}`);
        if (item.episode) titleBits.push(`E${String(item.episode).padStart(2, '0')}`);
        return DOM.el('article', { className: 'sharing-card' }, [
            DOM.el('div', { className: 'sharing-card-main' }, [
                DOM.el('div', { className: 'sharing-card-title-row' }, [
                    DOM.el('h3', {}, [titleBits.join(' · ')]),
                    DOM.el('span', { className: `badge ${item.active ? 'success' : ''}` }, [item.status || 'unknown'])
                ]),
                DOM.el('p', { className: 'muted sharing-torrent-title' }, [item.torrent_title || 'Torrent title unavailable']),
                DOM.el('p', { className: 'sharing-path', title: path }, [path])
            ]),
            DOM.el('div', { className: 'sharing-metrics' }, [
                this._metric('Ratio', ratio),
                this._metric('Uploaded', formatBytes(item.uploaded_bytes || 0)),
                this._metric('Now', this._formatRate(item.upload_rate || 0)),
                this._metric('Seeds / peers', `${item.num_seeds || 0} / ${item.num_peers || 0}`)
            ])
        ]);
    }

    /**
     * Create a single metric chip.
     * @private
     */
    _metric(label, value) {
        return DOM.el('div', { className: 'sharing-metric' }, [
            DOM.el('span', {}, [label]),
            DOM.el('strong', {}, [value])
        ]);
    }

    /**
     * Convert bytes/second into a compact observed-rate string.
     * @private
     */
    _formatRate(bytesPerSecond) {
        const value = Number(bytesPerSecond || 0);
        return value > 0 ? `${formatBytes(value)}/s` : '0 B/s';
    }

    /**
     * Convert a kB/s policy cap into a display string.
     * @private
     */
    _formatCap(kbps) {
        const value = Number(kbps || 0);
        return value > 0 ? `${formatBytes(value * 1024)}/s` : 'Unlimited';
    }

    /**
     * Render a load error in the list container.
     * @private
     */
    _renderError(err) {
        const listEl = document.getElementById('sharing-list');
        if (!listEl) return;
        listEl.innerHTML = '';
        listEl.appendChild(DOM.el('p', { className: 'empty-msg' }, [`Sharing status unavailable: ${err.message}`]));
    }
}

window.SharingPanel = SharingPanel;
