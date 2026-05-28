/**
 * DownloadManager for LJS.
 *
 * Coordinates Hold downloads lists. Renders modern Glassmorphic cards
 * with shimmering progress bars, real-time speed rates, and file-level priorities.
 */

class DownloadManager extends Component {
    /**
     * Construct and initialize the DownloadManager instance.
     *
     * Keep DOM lookups local, prefer ActionClient/APIClient for server calls,
     * and preserve event names/data attributes so other components can extend
     * this behavior without reaching into private state.
     */
    constructor() {
        super('hold');
        this.gridContainer = document.getElementById('active-downloads');
        this.activeBadge = document.getElementById('stat-active-count');
        this.downloads = new Map();
        this._pollTimer = null;
        this._expandedFilePanels = new Set();
        
        if (this.gridContainer) {
            this._init();
        }
    }

    /**
     * Subscribe to WebSocket events and load active downloads list.
     * @private
     */
    _init() {
        // Subscribe to central WebSocket push events
        shipEvents.subscribe('dl_stats', (e) => this._updateStats(e.id, e.stats));
        shipEvents.subscribe('dl_event', (e) => this._handleEvent(e));
        
        this.load();
        // slskd/Soulseek transfers do not currently emit torrent telemetry events.
        // Poll the unified downloads endpoint so Soulseek progress, speed, and
        // completed/cleared state update without a full browser refresh.
        if (!this._pollTimer) {
            this._pollTimer = window.setInterval(() => this.load({ silent: true }), 5000);
        }
    }

    /**
     * Load initial download list state from FastAPI endpoint.
     */
    async load(options = {}) {
        try {
            const data = await APIClient.get('/api/downloads');
            this.downloads.clear();
            (data.active || []).forEach(d => this.downloads.set(d.id, d));
            this.render();
        } catch (err) {
            if (!options.silent) console.error('[DownloadManager] Failed to load downloads:', err);
        }
    }

    /**
     * Handle incoming real-time lifecycle event.
     * @private
     */
    _handleEvent(e) {
        if (e.subtype === 'cancelled' || e.subtype === 'removed') {
            this.downloads.delete(e.id);
            this._expandedFilePanels.delete(e.id);
        } else if (e.download) {
            this.downloads.set(e.id, e.download);
        }
        this.render();
    }

    /**
     * Update progress and speed values in real-time.
     * @private
     */
    _updateStats(id, stats) {
        if (!this.downloads.has(id)) return;
        const dl = this.downloads.get(id);
        const hadFiles = Array.isArray(dl.files) && dl.files.length > 0;
        const oldStatus = dl.status;
        Object.assign(dl, stats);
        if (!['downloading', 'seeding'].includes(String(dl.status || '').toLowerCase())) {
            dl.download_rate = 0;
            dl.upload_rate = 0;
            dl.eta_seconds = 0;
        }
        const nowHasFiles = Array.isArray(dl.files) && dl.files.length > 0;
        const structuralStatus = ['paused', 'queued', 'downloading', 'complete', 'seeding'].includes(dl.status);
        if ((!hadFiles && nowHasFiles) || (oldStatus !== dl.status && structuralStatus)) {
            this.render();
            return;
        }
        const card = document.querySelector(`.download-card[data-id="${id}"]`);
        if (!card) return;
        DownloadStatsPatcher.patch(card, dl);
        this._updateBadge();
    }

    /**
     * Execute a bulk action over the currently visible download set.
     */
    async bulkAction(actionType) {
        const all = Array.from(this.downloads.values());
        let targets = all;
        if (actionType === 'pause') {
            targets = all.filter(d => ['downloading', 'queued'].includes(d.status));
        } else if (actionType === 'resume') {
            targets = all.filter(d => ['paused'].includes(d.status));
        } else if (actionType === 'cancel') {
            targets = all.filter(d => {
                const isSoulseek = d.source === 'slskd' || d.backend === 'soulseek';
                return isSoulseek || !['complete', 'cancelled', 'failed'].includes(d.status);
            });
        }
        if (!targets.length) {
            toast.show('No matching downloads.');
            return;
        }
        if (actionType === 'cancel') {
            const ok = await ljsConfirm(`Cancel ${targets.length} download(s)? Partial files will be removed.`, {
                title: 'Cancel Downloads',
                confirmText: 'Cancel All',
                danger: true
            });
            if (!ok) return;
        }
        try {
            const ids = targets.map(d => d.id);
            let res;
            if (actionType === 'pause') res = await ActionClient.pauseDownloads(ids);
            else if (actionType === 'resume') res = await ActionClient.resumeDownloads(ids);
            else if (actionType === 'cancel') res = await ActionClient.cancelDownloads(ids);
            else throw new Error(`Unknown bulk action: ${actionType}`);
            const data = res && res.data ? res.data : res;
            const succeeded = data && data.succeeded ? data.succeeded.length : ids.length;
            toast.show(`${actionType} sent for ${succeeded}/${ids.length} download(s).`);
            await this.load();
        } catch (e) {
            toast.error(`Bulk ${actionType} failed: ${e.message}`);
            await this.load();
        }
    }

    /**
     * Trigger a backend Action Client payload command.
     * @private
     */
    async _action(id, actionType) {
        try {
            if (actionType === 'cancel' && !(await ljsConfirm('Cancel this download and remove partial files?', { title: 'Cancel Download', confirmText: 'Cancel Download', danger: true }))) return;
            
            // Set transient loading state for instant visual feedback
            const dl = this.downloads.get(id);
            if (dl) {
                if (actionType === 'pause') dl.status = 'pausing';
                else if (actionType === 'resume') dl.status = 'resuming';
                else if (actionType === 'cancel') dl.status = 'cancelling';
                else if (actionType === 'restart') dl.status = 'restarting';
                else if (actionType === 'priority') dl.status = 'updating priority';
                this.render();
            }

            let res;
            switch (actionType) {
                case 'pause':
                    res = await ActionClient.pauseDownload(id);
                    break;
                case 'resume':
                    res = await ActionClient.resumeDownload(id);
                    break;
                case 'cancel':
                    res = await ActionClient.cancelDownload(id);
                    break;
                case 'restart':
                    res = await ActionClient.restartDownload(id);
                    break;
                case 'priority': {
                    const nextPrio = this._nextDownloadPriority(dl ? dl.priority : 'normal');
                    res = await ActionClient.setDownloadPriority(id, nextPrio);
                    break;
                }
                default:
                    throw new Error(`Unknown action request: ${actionType}`);
            }

            if (actionType === 'cancel') {
                this.downloads.delete(id);
                this._expandedFilePanels.delete(id);
                toast.show('Cargo scuttled.');
            } else if (res && res.ok && res.data) {
                this.downloads.set(id, res.data);
                toast.show(`Action "${actionType}" dispatched.`);
            }
            this.load();
        } catch (err) {
            toast.error(err.message);
            this.load(); // Reload original backend state if error occurs
        }
    }

    /**
     * Render the download list dynamically.
     */
    render() {
        if (!this.gridContainer) return;
        this.gridContainer.innerHTML = '';

        let filterVal = 'all';
        if (window.holdPanel && window.holdPanel._currentFilter) {
            filterVal = window.holdPanel._currentFilter;
        }

        const allDownloads = Array.from(this.downloads.values());
        const filtered = allDownloads.filter(dl => this._downloadMatchesFilter(dl, filterVal));

        if (filtered.length === 0) {
            this.gridContainer.appendChild(DOM.el('p', { className: 'empty-msg' }, [this._emptyMessage(filterVal)]));
            this._updateBadge();
            return;
        }

        const groups = this._groupDownloadsForDisplay(filtered, filterVal);
        groups.forEach(group => {
            const section = DOM.el('section', { className: `download-state-section download-state-${group.key}` });
            section.appendChild(DOM.el('div', { className: 'download-state-header' }, [
                DOM.el('span', { className: 'download-state-title' }, [group.label]),
                DOM.el('span', { className: 'download-state-count' }, [`${group.items.length} ${group.items.length === 1 ? 'item' : 'items'}`])
            ]));
            const list = DOM.el('div', { className: 'download-state-list' });
            group.items.forEach(dl => list.appendChild(this._buildCard(dl)));
            section.appendChild(list);
            this.gridContainer.appendChild(section);
        });

        this._updateBadge();
    }

    _normalizedStatus(dl) {
        return String(dl && dl.status ? dl.status : '').toLowerCase();
    }

    _statusGroup(dl) {
        const status = this._normalizedStatus(dl);
        if (status === 'downloading') return 'downloading';
        if (status === 'queued') return 'queued';
        if (status === 'paused' || status === 'stalled' || status === 'parked') return 'paused';
        if (status === 'complete' || status === 'seeding') return 'complete';
        if (status === 'failed' || status === 'cancelled') return 'finished';
        return 'other';
    }

    _downloadMatchesFilter(dl, filterVal) {
        const group = this._statusGroup(dl);
        if (filterVal === 'downloading') return group === 'downloading';
        if (filterVal === 'queued') return group === 'queued';
        if (filterVal === 'paused') return group === 'paused';
        if (filterVal === 'complete') return group === 'complete';
        return group !== 'finished';
    }

    _emptyMessage(filterVal) {
        const labels = {
            downloading: 'No downloads are currently transferring.',
            queued: 'No downloads are waiting in the queue.',
            paused: 'No downloads are paused or parked.',
            complete: 'No completed or seeding downloads in the hold.',
        };
        return labels[filterVal] || 'No active cargo in the hold.';
    }

    _groupDownloadsForDisplay(downloads, filterVal) {
        const meta = {
            downloading: { label: 'Downloading Now', order: 0 },
            queued: { label: 'Queued', order: 1 },
            paused: { label: 'Paused / Parked', order: 2 },
            complete: { label: 'Seeding / Complete', order: 3 },
            other: { label: 'Other', order: 4 },
        };
        const buckets = new Map();
        downloads.forEach(dl => {
            const key = this._statusGroup(dl);
            if (!buckets.has(key)) buckets.set(key, []);
            buckets.get(key).push(dl);
        });
        const priorityRank = { high: 0, normal: 1, low: 2 };
        return Array.from(buckets.entries())
            .sort(([a], [b]) => (meta[a]?.order ?? 99) - (meta[b]?.order ?? 99))
            .map(([key, items]) => {
                items.sort((a, b) => {
                    const prio = (priorityRank[String(a.priority || 'normal').toLowerCase()] ?? 1) - (priorityRank[String(b.priority || 'normal').toLowerCase()] ?? 1);
                    if (prio !== 0) return prio;
                    const seasonDelta = (a.season || 0) - (b.season || 0);
                    if (seasonDelta !== 0) return seasonDelta;
                    const episodeDelta = (a.episode || 0) - (b.episode || 0);
                    if (episodeDelta !== 0) return episodeDelta;
                    return String(a.created_at || '').localeCompare(String(b.created_at || ''));
                });
                return { key, label: meta[key]?.label || key, items };
            });
    }

    _displayTorrentTitle(dl) {
        const itemName = String(dl.item_name || '').trim();
        const candidates = [
            dl.torrent_title,
            dl.release_title,
            dl.title,
            dl.name,
        ];
        if (Array.isArray(dl.files) && dl.files.length) {
            const first = dl.files.find(f => f && (f.file_path || f.path || f.name)) || dl.files[0];
            if (first) candidates.push((first.file_path || first.path || first.name || '').split('/').pop());
        }
        if (dl.magnet && String(dl.magnet).startsWith('magnet:?')) {
            try {
                const urlParams = new URLSearchParams(String(dl.magnet).replace('magnet:?', ''));
                const dn = urlParams.get('dn');
                if (dn) candidates.push(decodeURIComponent(dn).replace(/\+/g, ' '));
            } catch (e) {
                // Ignore malformed magnet display fallback.
            }
        }
        for (const raw of candidates) {
            const title = String(raw || '').trim();
            if (!title) continue;
            if (itemName && title.toLowerCase() === itemName.toLowerCase()) continue;
            return title;
        }
        return 'Torrent release name not available yet';
    }

    _downloadExpansionKeys(dl) {
        const id = String((dl && dl.id) || '').trim();
        const isSoulseek = dl && (dl.source === 'slskd' || dl.backend === 'soulseek');
        const keys = new Set();
        if (id) keys.add(id);
        if (isSoulseek) {
            const user = String(dl.slskd_username || '').trim().toLowerCase();
            const folder = String(dl.slskd_folder || '').trim().toLowerCase();
            const item = String(dl.item_name || '').trim().toLowerCase();
            const magnet = String(dl.magnet || '').trim().toLowerCase();
            if (user || folder || item) keys.add(`slskd:${user}:${folder || item}`);
            if (user || item) keys.add(`slskd-item:${user}:${item}`);
            if (magnet) keys.add(`magnet:${magnet}`);
        }
        return Array.from(keys).filter(Boolean);
    }

    _isFilesPanelExpanded(dl) {
        return this._downloadExpansionKeys(dl).some(key => this._expandedFilePanels.has(key));
    }

    _setFilesPanelExpanded(dl, expanded) {
        const keys = this._downloadExpansionKeys(dl);
        keys.forEach(key => {
            if (expanded) this._expandedFilePanels.add(key);
            else this._expandedFilePanels.delete(key);
        });
    }

    /**
     * Compiles a single download card using Option 1 markup specs.
     * @private
     */
    _buildCard(dl) {
        const hasFiles = dl.files && dl.files.length >= 1;
        const isPending = ['pausing', 'resuming', 'cancelling', 'restarting', 'updating priority'].includes(dl.status);

        const group = this._statusGroup(dl);
        const isFilesExpanded = this._isFilesPanelExpanded(dl);
        const card = DOM.el('div', { 
            className: `download-card glass-panel dl-state-${group} ${dl.status === 'paused' ? 'dl-card-paused' : ''} ${isPending ? 'dl-card-pending' : ''}`, 
            dataset: { id: dl.id, status: dl.status, stateGroup: group } 
        });

        // Top icon
        const iconWrap = DOM.el('div', { className: 'dl-icon' });
        const isSoulseek = dl.source === 'slskd' || dl.backend === 'soulseek';
        const iconClass = isSoulseek ? 'fa-music' : ((dl.status === 'seeding' || dl.status === 'complete') ? 'fa-box-open' : 'fa-ship');
        iconWrap.appendChild(DOM.el('i', { className: `fa-solid ${iconClass}` }));
        card.appendChild(iconWrap);

        // Core info
        const info = DOM.el('div', { className: 'dl-info' });
        info.appendChild(DOM.el('h3', {}, [dl.item_name]));

        const torrentTitle = this._displayTorrentTitle(dl);
        info.appendChild(DOM.el('div', {
            className: `dl-torrent-title ${torrentTitle.includes('not available') ? 'dl-torrent-title-missing' : ''}`,
            title: torrentTitle,
        }, [torrentTitle]));

        const meta = DOM.el('div', { className: 'dl-meta' });
        if (isSoulseek) {
            meta.appendChild(DOM.el('span', { className: 'badge', title: 'Soulseek/slskd transfer' }, ['Soulseek']));
        }
        if (dl.season && dl.episode) {
            meta.appendChild(DOM.el('span', { className: 'badge' }, [`S${String(dl.season).padStart(2, '0')}E${String(dl.episode).padStart(2, '0')}`]));
        }
        
        // Download rate
        const speedVal = (dl.download_rate / 1024).toFixed(0);
        meta.appendChild(DOM.el('span', { className: 'dl-speed' }, [
            DOM.el('i', { className: 'fa-solid fa-arrow-down' }),
            ` ${speedVal} kB/s`
        ]));

        // Upload rate
        const upSpeedVal = (dl.upload_rate / 1024).toFixed(0);
        meta.appendChild(DOM.el('span', { className: 'dl-upspeed', style: { marginLeft: '8px' } }, [
            DOM.el('i', { className: 'fa-solid fa-arrow-up' }),
            ` ${upSpeedVal} kB/s`
        ]));

        // Live swarm counts. These are not the original tracker/indexer seeder
        // number; that snapshot is shown separately as "src" when available.
        const peers = dl.num_peers || 0;
        const liveSeeds = dl.num_seeds || 0;
        const sourceSeeders = dl.source_seeders;
        const sourceText = sourceSeeders != null ? ` · src ${sourceSeeders}` : '';
        meta.appendChild(DOM.el('span', {
            className: 'dl-peers dl-swarm',
            style: { marginLeft: '8px' },
            title: isSoulseek
                ? 'Soulseek is peer-to-peer through one remote user/queue, not a torrent swarm.'
                : (sourceSeeders != null
                    ? `Live seeds/peers from libtorrent. Source seeders (${sourceSeeders}) were reported by the indexer when selected.`
                    : 'Live seeds/peers from libtorrent.')
        }, [
            DOM.el('i', { className: 'fa-solid fa-users' }),
            isSoulseek ? ` user ${dl.slskd_username || 'unknown'}` : ` seeds ${liveSeeds} · peers ${peers}${sourceText}`
        ]));

        // ETA
        const m = Math.floor((dl.eta_seconds || 0) / 60), s = Math.floor((dl.eta_seconds || 0) % 60);
        const etaText = dl.eta_seconds > 0 ? (m > 0 ? `${m}m${s}s` : `${s}s`) : '—';
        meta.appendChild(DOM.el('span', { className: 'dl-eta', style: { marginLeft: '8px' } }, [
            DOM.el('i', { className: 'fa-regular fa-clock' }),
            ` ${etaText}`
        ]));

        // Status string
        meta.appendChild(DOM.el('span', { style: { marginLeft: '8px' } }, [
            'Status: ',
            DOM.el('strong', { className: `status-${dl.status}` }, [dl.status])
        ]));

        // Total size
        const sizeGb = dl.total_size ? (dl.total_size / 1e9).toFixed(1) : '?';
        meta.appendChild(DOM.el('span', { style: { marginLeft: '8px' } }, [`Size: ${sizeGb} GB`]));

        // Priority badge
        const prio = dl.priority || 'normal';
        const priorityColors = {
            'high': 'rgba(230, 57, 70, 0.25)',
            'normal': 'rgba(255, 255, 255, 0.08)',
            'low': 'rgba(42, 157, 143, 0.15)'
        };
        const priorityTextColors = {
            'high': '#ff6b6b',
            'normal': 'var(--text-dim)',
            'low': 'var(--accent-teal)'
        };
        meta.appendChild(DOM.el('span', { 
            className: 'dl-priority-badge badge', 
            style: { 
                marginLeft: '8px', 
                background: priorityColors[prio.toLowerCase()] || priorityColors['normal'], 
                color: priorityTextColors[prio.toLowerCase()] || priorityTextColors['normal'],
                textTransform: 'uppercase',
                fontWeight: '600'
            } 
        }, [prio.toUpperCase()]));
        
        if (hasFiles) {
            const toggle = DOM.el('span', { 
                className: 'dl-expand-toggle badge', 
                style: { 
                    marginLeft: '8px', 
                    cursor: 'pointer',
                    background: 'rgba(255, 255, 255, 0.05)',
                    color: 'var(--text-dim)',
                    padding: '2px 8px',
                    borderRadius: '4px',
                    fontSize: '0.75rem',
                    transition: 'all 0.2s'
                } 
            }, [DOM.el('i', { className: 'fa-solid fa-list' }), ' Files']);
            meta.appendChild(toggle);
        }

        info.appendChild(meta);

        // Progress bar
        const track = DOM.el('div', { className: 'progress-track' });
        track.appendChild(DOM.el('div', { 
            className: 'progress-fill', 
            style: { width: `${Math.round(dl.progress * 100)}%` } 
        }));
        info.appendChild(track);
        card.appendChild(info);

        // Active control actions
        const acts = DOM.el('div', { className: 'dl-actions' });
        
        if (isSoulseek) {
            if (hasFiles) {
                const filesButton = DOM.btn('', 'icon-btn dl-files-btn', () => {
                    this._toggleFilesPanel(dl, card);
                }, {
                    title: 'Show Soulseek files',
                    content: '<i class="fa-solid fa-list-check"></i>'
                });
                acts.appendChild(filesButton);
            }
            acts.appendChild(DOM.btn('', 'icon-btn danger cancel-btn', () => this._action(dl.id, 'cancel'), {
                title: ['complete', 'failed', 'cancelled'].includes(String(dl.status || '').toLowerCase()) ? 'Clear Soulseek transfer' : 'Cancel Soulseek transfer',
                content: '<i class="fa-solid fa-trash"></i>'
            }));
        } else if (isPending) {
            const loader = DOM.el('div', { 
                className: 'dl-pending-loader'
            }, [
                DOM.el('i', { className: 'fa-solid fa-circle-notch fa-spin' }),
                ` ${dl.status}...`
            ]);
            acts.appendChild(loader);
        } else {
            if (dl.status === 'paused') {
                acts.appendChild(DOM.btn('', 'icon-btn dl-resume-btn', () => this._action(dl.id, 'resume'), { 
                    title: 'Resume',
                    content: '<i class="fa-solid fa-play"></i>'
                }));
            } else if (['downloading', 'queued'].includes(dl.status)) {
                acts.appendChild(DOM.btn('', 'icon-btn dl-pause-btn', () => this._action(dl.id, 'pause'), { 
                    title: 'Pause',
                    content: '<i class="fa-solid fa-pause"></i>'
                }));
            } else {
                acts.appendChild(DOM.btn('', 'icon-btn dl-restart-btn', () => this._action(dl.id, 'restart'), { 
                    title: 'Restart',
                    content: '<i class="fa-solid fa-rotate"></i>'
                }));
            }

            if (hasFiles) {
                const filesButton = DOM.btn('', 'icon-btn dl-files-btn', () => {
                    this._toggleFilesPanel(dl, card);
                }, {
                    title: 'Show files and per-file priority',
                    content: '<i class="fa-solid fa-list-check"></i>'
                });
                acts.appendChild(filesButton);
            }

            const prioSelect = DOM.el('select', {
                className: 'dl-priority-select',
                title: 'Download priority',
                onchange: (ev) => this._setDownloadPriority(dl.id, ev.target.value)
            }, [
                DOM.el('option', { value: 'high' }, ['High']),
                DOM.el('option', { value: 'normal' }, ['Normal']),
                DOM.el('option', { value: 'low' }, ['Low'])
            ]);
            prioSelect.value = String(dl.priority || 'normal').toLowerCase();
            acts.appendChild(prioSelect);

            // Cycle priority control button, retained for keyboard/compact use.
            acts.appendChild(DOM.btn('', 'icon-btn dl-prio-btn', () => this._action(dl.id, 'priority'), { 
                title: 'Cycle Priority',
                content: '<i class="fa-solid fa-angles-up"></i>'
            }));

            acts.appendChild(DOM.btn('', 'icon-btn danger cancel-btn', () => this._action(dl.id, 'cancel'), {
                title: 'Cancel',
                content: '<i class="fa-solid fa-trash"></i>'
            }));
        }

        card.appendChild(acts);

        // Handle inline file expand toggle panel
        if (hasFiles) {
            const filesDiv = DOM.el('div', {
                className: 'dl-files',
                style: { display: isFilesExpanded ? 'block' : 'none', width: '100%', marginTop: '16px' }
            });
            this._buildFileRows(dl, filesDiv);
            card.appendChild(filesDiv);

            const toggleBtn = meta.querySelector('.dl-expand-toggle');
            if (toggleBtn) {
                this._syncFilesToggle(toggleBtn, isFilesExpanded);
                toggleBtn.addEventListener('click', (e) => {
                    e.stopPropagation();
                    this._toggleFilesPanel(dl, card);
                });
            }
        }

        return card;
    }

    _toggleFilesPanel(downloadOrId, card) {
        const filesDiv = card.querySelector('.dl-files');
        if (!filesDiv) return;
        const dl = typeof downloadOrId === 'object' ? downloadOrId : this.downloads.get(downloadOrId) || { id: downloadOrId };
        const nextExpanded = filesDiv.style.display === 'none';
        filesDiv.style.display = nextExpanded ? 'block' : 'none';
        this._setFilesPanelExpanded(dl, nextExpanded);
        const toggleBtn = card.querySelector('.dl-expand-toggle');
        if (toggleBtn) this._syncFilesToggle(toggleBtn, nextExpanded);
    }

    _syncFilesToggle(toggleBtn, expanded) {
        toggleBtn.innerHTML = expanded ? '<i class="fa-solid fa-list"></i> Hide files' : '<i class="fa-solid fa-list"></i> Files';
        toggleBtn.style.color = expanded ? 'var(--accent-teal)' : 'var(--text-dim)';
        toggleBtn.style.background = expanded ? 'rgba(42, 157, 143, 0.15)' : 'rgba(255, 255, 255, 0.05)';
    }

    /**
     * Compiles detailed list of file items within a download card.
     * @private
     */
    _buildFileRows(dl, filesDiv) {
        new DownloadFileRowsRenderer({
            onSetFilePriority: (downloadId, fileIndex, priority) => this._setFilePriority(downloadId, fileIndex, priority),
            nextPriority: (current, direction) => this._nextPrio(current, direction)
        }).render(dl, filesDiv);
    }

    /**
     * Cycles through 'low', 'normal', 'high' download priorities.
     * @private
     */
    _nextDownloadPriority(current) {
        const levels = ['low', 'normal', 'high'];
        let idx = levels.indexOf(current || 'normal');
        if (idx === -1) idx = 1;
        idx = (idx + 1) % levels.length;
        return levels[idx];
    }

    /**
     * Helper to cycle file-level priorities: [0, 1, 4, 7]
     * @private
     */
    _nextPrio(current, direction) {
        const levels = [0, 1, 4, 7];
        let idx = levels.indexOf(current);
        if (idx === -1) idx = 2; // Default to normal index
        idx += direction;
        if (idx < 0) idx = 0;
        if (idx >= levels.length) idx = levels.length - 1;
        return levels[idx];
    }


    async _setDownloadPriority(downloadId, priority) {
        try {
            await ActionClient.setDownloadPriority(downloadId, priority);
            toast.show(`Priority set to ${priority}`);
            this.load();
        } catch (e) {
            toast.error('Failed to set download priority: ' + e.message);
            this.load();
        }
    }

    /**
     * Call the backend API to update the priority of a single file in a torrent.
     * @private
     */
    async _setFilePriority(downloadId, fileIndex, priority) {
        try {
            await ActionClient.setFilePriority(downloadId, fileIndex, priority);
            this.load();
        } catch (e) {
            toast.error('Failed to set file priority: ' + e.message);
        }
    }

    /**
     * Updates active count indicator in dashboard widgets.
     * @private
     */
    _updateBadge() {
        if (this.activeBadge) {
            this.activeBadge.textContent = this.downloads.size;
        }
    }
}

window.DownloadManager = DownloadManager;
