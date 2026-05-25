/**
 * HoldPanel component for LJS.
 *
 * Implements class-based dynamic DOM rendering for the Hold section.
 * Coordinates active cargo lists, status filters, speed indicators, progress fill animations,
 * and a recent plunder log database table.
 */

class HoldPanel extends Component {
    /**
     * @param {string} elementId - ID of the container element ('hold').
     * @param {EventBus} eventBus - Shared event bus.
     */
    constructor(elementId, eventBus) {
        super(elementId);
        this._eventBus = eventBus;
        this._currentFilter = 'all';

        if (this.container) {
            this.render();
            this._init();
        }
    }

    /**
     * Render layout skeleton.
     */
    render() {
        this._clear();

        // Page Header
        const header = DOM.el('div', { className: 'page-header glass-panel hold-actions' }, [
            DOM.el('h2', {}, ['The Hold (Active Downloads)']),
            DOM.el('div', { className: 'bulk-download-actions' }, [
                DOM.el('div', { className: 'filter-controls', id: 'hold-filter-controls' }, [
                    DOM.btn('All', 'filter-btn active', () => this.setFilter('all'), { 'data-filter': 'all' }),
                    DOM.btn('Downloading', 'filter-btn', () => this.setFilter('downloading'), { 'data-filter': 'downloading' }),
                    DOM.btn('Queued', 'filter-btn', () => this.setFilter('queued'), { 'data-filter': 'queued' }),
                    DOM.btn('Paused', 'filter-btn', () => this.setFilter('paused'), { 'data-filter': 'paused' }),
                    DOM.btn('Complete', 'filter-btn', () => this.setFilter('complete'), { 'data-filter': 'complete' })
                ]),
                DOM.btn('', 'quick-btn', () => window.downloads && window.downloads.bulkAction('pause'), { title: 'Pause all active downloads', content: '<i class="fa-solid fa-pause"></i> Pause All' }),
                DOM.btn('', 'quick-btn', () => window.downloads && window.downloads.bulkAction('resume'), { title: 'Resume all paused downloads', content: '<i class="fa-solid fa-play"></i> Resume All' }),
                DOM.btn('', 'quick-btn danger', () => window.downloads && window.downloads.bulkAction('cancel'), { title: 'Cancel all downloads', content: '<i class="fa-solid fa-trash"></i> Cancel All' })
            ])
        ]);
        this.container.appendChild(header);

        // List Container
        const list = DOM.el('div', { className: 'downloads-list', id: 'active-downloads' });
        this.container.appendChild(list);

        // Recent Plunder Header
        const recentHeader = DOM.el('div', { className: 'page-header glass-panel', style: { marginTop: '40px', marginBottom: '16px' } }, [
            DOM.el('h2', {}, ['Recent Plunder Log'])
        ]);
        this.container.appendChild(recentHeader);

        // Recent Plunder Table
        const tableContainer = DOM.el('div', { className: 'glass-panel', style: { padding: '24px', overflowX: 'auto' } });
        
        const tbl = DOM.el('table', { className: 'tbl', style: { width: '100%', borderCollapse: 'collapse', textAlign: 'left' } });
        const thead = DOM.el('thead', {}, [
            DOM.el('tr', { style: { borderBottom: '1px solid var(--glass-border)', color: 'var(--text-dim)', fontSize: '0.8rem', textTransform: 'uppercase' } }, [
                DOM.el('th', { style: { padding: '12px' } }, ['Cargo']),
                DOM.el('th', { style: { padding: '12px' } }, ['Episode']),
                DOM.el('th', { style: { padding: '12px' } }, ['Reason']),
                DOM.el('th', { style: { padding: '12px' } }, ['Fate']),
                DOM.el('th', { style: { padding: '12px' } }, ['Date'])
            ])
        ]);
        tbl.appendChild(thead);

        const tbody = DOM.el('tbody', { id: 'recent-downloads' });
        tbl.appendChild(tbody);

        tableContainer.appendChild(tbl);
        this.container.appendChild(tableContainer);
    }

    /**
     * Subscribe to downloads actions and load initial tables.
     * @private
     */
    _init() {
        this.setFilter(this._currentFilter);
        this.loadRecentPlunder();
        
        this._eventBus.subscribe('system', (e) => {
            if (e.subtype === 'downloads_refreshed') {
                this.loadRecentPlunder();
            }
        });
    }

    /**
     * Set active filter button states.
     * @param {string} filterVal - 'all', 'downloading', 'queued', 'paused', 'complete'
     */
    setFilter(filterVal) {
        this._currentFilter = filterVal;
        
        const btns = document.querySelectorAll('#hold-filter-controls .filter-btn');
        btns.forEach(btn => {
            if (btn.getAttribute('data-filter') === filterVal) {
                btn.classList.add('active');
            } else {
                btn.classList.remove('active');
            }
        });

        // Trigger visual filter updating
        if (window.downloads) {
            window.downloads.load();
        }
    }

    /**
     * Load recent downloads list from server.
     */
    async loadRecentPlunder() {
        const tbody = document.getElementById('recent-downloads');
        if (!tbody) return;

        try {
            const data = await APIClient.get('/api/downloads/recent');
            const recent = data.downloads || [];

            tbody.innerHTML = '';
            if (recent.length === 0) {
                tbody.appendChild(DOM.el('tr', {}, [
                    DOM.el('td', { colSpan: 5, className: 'empty-msg', style: { padding: '24px', textAlign: 'center' } }, ['No plunder logged yet.'])
                ]));
                return;
            }

            recent.forEach(dl => {
                const tr = DOM.el('tr', { style: { borderBottom: '1px solid rgba(255,255,255,0.04)', fontSize: '0.88rem' } }, [
                    DOM.el('td', { style: { padding: '12px', fontWeight: '600' } }, [dl.item_name]),
                    DOM.el('td', { style: { padding: '12px', fontFamily: 'monospace' } }, [
                        dl.season && dl.episode ? `S${String(dl.season).padStart(2, '0')}E${String(dl.episode).padStart(2, '0')}` : '—'
                    ]),
                    DOM.el('td', { style: { padding: '12px', color: 'var(--text-dim)' } }, [
                        dl.reason ? dl.reason.replace(/_/g, ' ') : '—'
                    ]),
                    DOM.el('td', { style: { padding: '12px' } }, [
                        DOM.el('span', {
                            className: 'badge',
                            style: { background: 'rgba(42, 157, 143, 0.15)', color: 'var(--accent-teal)' }
                        }, [dl.status])
                    ]),
                    DOM.el('td', { style: { padding: '12px', color: 'var(--text-dim)' } }, [
                        dl.created_at ? dl.created_at.replace('T', ' ').substring(0, 16) : '—'
                    ])
                ]);
                tbody.appendChild(tr);
            });
        } catch (err) {
            console.error('[HoldPanel] Failed to retrieve recent plunder:', err);
        }
    }
}

window.HoldPanel = HoldPanel;
