/**
 * HelmPanel component for LJS.
 *
 * Implements class-based dynamic DOM rendering for the Helm section,
 * setting up the Silver AI Terminal, Quick Action buttons, Fleet Status stats,
 * and the green Voyage Logs terminal.
 */

class HelmPanel extends Component {
    /**
     * @param {string} elementId - ID of the container element ('helm').
     * @param {EventBus} eventBus - Shared event bus.
     */
    constructor(elementId, eventBus) {
        super(elementId);
        this._eventBus = eventBus;
        this._model = 'GPT-4o';
        
        if (this.container) {
            this.render();
            this._init();
        }
    }

    /**
     * Render the full layout structure.
     */
    render() {
        this._clear();

        const grid = DOM.el('div', { className: 'helm-grid' });

        // Column 1: AI Chat Terminal
        const chatContainer = DOM.el('div', { className: 'chat-container glass-panel' });
        
        const chatHeader = DOM.el('div', { className: 'chat-header' }, [
            DOM.el('h2', {}, [
                DOM.el('i', { className: 'fa-solid fa-robot' }),
                ' Silver AI Terminal'
            ]),
            DOM.el('div', { className: 'header-tools', style: { display: 'flex', gap: '10px', alignItems: 'center' } }, [
                DOM.el('span', { className: 'badge', id: 'ai-model-badge' }, [`Model: ${this._model}`]),
                DOM.btn('', 'btn-clear-chat', () => {
                    if (window.chatController) {
                        window.chatController.clearChat();
                    }
                }, {
                    id: 'clear-chat-btn',
                    content: '<i class="fa-solid fa-trash"></i> Clear'
                })
            ])
        ]);

        const chatFeed = DOM.el('div', { className: 'chat-feed', id: 'chat-feed' }, [
            DOM.el('div', { className: 'message system' }, [
                DOM.el('div', { className: 'msg-bubble' }, ['Ahoy Captain. The trackers are primed and the seas are calm. What are we hunting today?'])
            ])
        ]);

        const chatInputArea = DOM.el('div', { className: 'chat-input-area' }, [
            DOM.el('textarea', { placeholder: 'Give your orders, Captain...', id: 'chat-input', name: 'ljs-command-chat', rows: '1', autocomplete: 'off', autocapitalize: 'off', autocorrect: 'off', spellcheck: 'false', role: 'searchbox', 'aria-label': 'Assistant command input', 'data-ljs-command-input': 'true', 'data-ljs-noncredential': 'true', 'data-lpignore': 'true', 'data-1p-ignore': 'true', 'data-bwignore': 'true' }),
            DOM.btn('', 'send-btn', () => {}, { id: 'send-btn', content: '<i class="fa-solid fa-paper-plane"></i>' })
        ]);

        chatContainer.appendChild(chatHeader);
        chatContainer.appendChild(chatFeed);
        chatContainer.appendChild(chatInputArea);
        grid.appendChild(chatContainer);

        // Column 2: Dashboard Widgets
        const widgets = DOM.el('div', { className: 'widgets-container' });

        // Quick Actions
        const quickActions = DOM.el('div', { className: 'widget glass-panel' }, [
            DOM.el('h3', {}, [
                DOM.el('i', { className: 'fa-solid fa-bolt' }),
                ' Quick Actions'
            ]),
            DOM.el('div', { className: 'quick-grid' }, [
                DOM.btn('', 'quick-btn', () => this._promptManualUpload(), {
                    content: '<i class="fa-solid fa-plus"></i> Add Torrent Link'
                }),
                DOM.btn('', 'quick-btn', () => this._clearCompleted(), {
                    content: '<i class="fa-solid fa-broom"></i> Clear Completed'
                }),
                DOM.btn('', 'quick-btn danger', () => this._scuttleAll(), {
                    content: '<i class="fa-solid fa-fire"></i> Cancel All Downloads'
                })
            ])
        ]);
        widgets.appendChild(quickActions);

        // Fleet Status
        const fleetStatus = DOM.el('div', { className: 'widget glass-panel' }, [
            DOM.el('h3', {}, [
                DOM.el('i', { className: 'fa-solid fa-chart-pie' }),
                ' Fleet Status'
            ]),
            DOM.el('div', { className: 'stats-list' }, [
                DOM.el('div', { className: 'stat-item' }, [
                    DOM.el('span', { className: 'stat-label' }, ['Tracked Items']),
                    DOM.el('span', { className: 'stat-value highlight', id: 'stat-tracked-count' }, ['...'])
                ]),
                DOM.el('div', { className: 'stat-item' }, [
                    DOM.el('span', { className: 'stat-label' }, ['Active Downloads']),
                    DOM.el('span', { className: 'stat-value', id: 'stat-active-count' }, ['...'])
                ])
            ])
        ]);
        widgets.appendChild(fleetStatus);

        // Storage Status
        const storageStatus = DOM.el('div', { className: 'widget glass-panel', id: 'helm-storage-widget' }, [
            DOM.el('h3', {}, [
                DOM.el('i', { className: 'fa-solid fa-hard-drive' }),
                ' Storage Watch'
            ]),
            DOM.el('div', { className: 'stats-list', id: 'storage-status-list' }, [
                DOM.el('div', { className: 'stat-item' }, [
                    DOM.el('span', { className: 'stat-label' }, ['Disk status']),
                    DOM.el('span', { className: 'stat-value', id: 'storage-status-summary' }, ['...'])
                ])
            ])
        ]);
        widgets.appendChild(storageStatus);

        // Voyage Logs
        const voyageLogs = DOM.el('div', { className: 'widget glass-panel voyage-logs-widget' }, [
            DOM.el('div', { className: 'voyage-log-header' }, [
                DOM.el('h3', {}, [
                    DOM.el('i', { className: 'fa-solid fa-terminal' }),
                    ' Voyage Logs'
                ]),
                DOM.el('div', { className: 'voyage-log-tabs', role: 'tablist', 'aria-label': 'Voyage log filters' }, [
                    DOM.el('button', { className: 'voyage-log-tab is-active', type: 'button', 'data-log-level': 'all' }, ['All']),
                    DOM.el('button', { className: 'voyage-log-tab', type: 'button', 'data-log-level': 'warnings' }, ['Warnings & Errors'])
                ])
            ]),
            DOM.el('div', { id: 'voyage-log-body', className: 'voyage-log-body' }, [
                DOM.el('div', { id: 'log-container', className: 'voyage-log-container' }, [
                    DOM.el('p', { style: { color: '#666' } }, ['Waiting for logs...'])
                ])
            ])
        ]);
        widgets.appendChild(voyageLogs);

        grid.appendChild(widgets);
        this.container.appendChild(grid);
    }

    /**
     * Subscribe to stats feeds and start logs retrieval.
     * @private
     */
    _init() {
        this._eventBus.subscribe('system', (e) => {
            if (e.subtype === 'category_item_added' || e.subtype === 'category_item_removed') {
                this.updateStats();
            }
        });
        
        this.updateStats();
        this.updateModelBadge();
        this.updateStorageStatus();
        this._storageTimer = setInterval(() => this.updateStorageStatus(), 60000);
    }


    async _promptManualUpload() {
        const magnet = await ljsPrompt('Paste the magnet link or torrent URL to add to the hold.', {
            title: 'Add Torrent Link',
            placeholder: 'magnet:?xt=... or https://...',
            confirmText: 'Add Cargo'
        });
        if (!magnet) return;
        const itemName = await ljsPrompt('Name this cargo so it is easy to recognize.', {
            title: 'Cargo Name',
            defaultValue: 'Manual Upload',
            confirmText: 'Continue'
        });
        try {
            await APIClient.post('/api/downloads/upload', { magnet, item_name: itemName || 'Manual Upload' });
            toast.show('Download queued.');
            if (window.downloads) window.downloads.load();
        } catch (e) {
            toast.error(e.message);
        }
    }

    async _clearCompleted() {
        const ok = await ljsConfirm('Clear completed downloads from the visible list?', {
            title: 'Clear Completed Cargo',
            confirmText: 'Clear'
        });
        if (!ok) return;
        const data = await APIClient.get('/api/downloads');
        const completed = (data.active || []).filter(d => d.status === 'complete' || d.status === 'seeding');
        if (!completed.length) { toast.show('No completed downloads.'); return; }
        const ids = completed.map(d => d.id);
        await ActionClient.cancelDownloads(ids);
        toast.show(`Cleared ${ids.length} completed download(s).`);
        if (window.downloads) window.downloads.load();
    }

    async _scuttleAll() {
        const data = await APIClient.get('/api/downloads');
        const active = (data.active || []).filter(d => !['complete', 'cancelled', 'failed'].includes(d.status));
        if (!active.length) { toast.show('No active downloads to cancel.'); return; }
        const ok = await ljsConfirm(`Cancel ${active.length} active download(s)? This removes partial files.`, {
            title: 'Cancel All Downloads',
            confirmText: 'Cancel All',
            danger: true
        });
        if (!ok) return;
        await ActionClient.cancelDownloads(active.map(d => d.id));
        toast.show(`Cancelled ${active.length} download(s).`);
        if (window.downloads) window.downloads.load();
    }

    /**
     * Retrieve current active LLM model from settings and update the badge.
     */
    async updateModelBadge() {
        try {
            const data = await APIClient.get('/api/settings');
            if (data && data.settings && data.settings.llm) {
                const llm = data.settings.llm;
                let activeModel = llm.chat?.model || llm.model || 'GPT-4o';
                
                // Clean up name for a premium, compact display (e.g. removing provider prefixes)
                if (activeModel.includes('/')) {
                    activeModel = activeModel.split('/').pop();
                }
                
                const badgeEl = document.getElementById('ai-model-badge');
                if (badgeEl) {
                    badgeEl.textContent = `Model: ${activeModel}`;
                }
            }
        } catch (err) {
            console.error('[HelmPanel] Failed to retrieve active AI model setting:', err);
        }
    }

    /**
     * Update numerical indicators on active counts.
     */
    async updateStats() {
        try {
            const categoriesData = await CategoryApiClient.listCategories();
            const categories = categoriesData.categories || [];
            const itemGroups = await Promise.all(categories.map(async (category) => {
                const categoryId = category.category_id || category.id;
                if (!categoryId) return [];
                const data = await CategoryApiClient.listItems(categoryId);
                return data.items || [];
            }));
            const items = itemGroups.flat();
            
            const trackedEl = document.getElementById('stat-tracked-count');
            if (trackedEl) {
                trackedEl.textContent = items.length;
            }

            const activeEl = document.getElementById('stat-active-count');
            if (activeEl && window.downloads) {
                activeEl.textContent = window.downloads.downloads.size;
            }
        } catch (err) {
            console.error('[HelmPanel] Failed to retrieve fleet stats:', err);
        }
    }
    /**
     * Update category-aware disk-space status.
     */
    async updateStorageStatus() {
        const summaryEl = document.getElementById('storage-status-summary');
        const listEl = document.getElementById('storage-status-list');
        if (!summaryEl || !listEl) return;
        try {
            const data = await APIClient.get('/api/storage/status');
            const volumes = data.volumes || [];
            const critical = volumes.filter(v => v.status === 'critical').length;
            const warning = volumes.filter(v => v.status === 'warning').length;
            summaryEl.textContent = critical ? `${critical} critical` : (warning ? `${warning} warning` : 'OK');
            summaryEl.className = `stat-value ${critical ? 'danger' : (warning ? 'highlight' : '')}`;

            const existing = listEl.querySelectorAll('.storage-volume-row');
            existing.forEach(e => e.remove());
            volumes.slice(0, 3).forEach(v => {
                const freeGb = (v.free_bytes / (1024 ** 3)).toFixed(1);
                const totalGb = (v.total_bytes / (1024 ** 3)).toFixed(1);
                const cats = (v.category_ids && v.category_ids.length) ? v.category_ids.join(', ') : 'downloads';
                listEl.appendChild(DOM.el('div', { className: 'stat-item storage-volume-row' }, [
                    DOM.el('span', { className: 'stat-label', title: v.mount_point }, [`${v.status.toUpperCase()} ${cats}`]),
                    DOM.el('span', { className: 'stat-value' }, [`${freeGb}/${totalGb} GB`])
                ]));
            });
        } catch (err) {
            console.error('[HelmPanel] Failed to retrieve storage status:', err);
            summaryEl.textContent = 'Unknown';
        }
    }

}

window.HelmPanel = HelmPanel;
