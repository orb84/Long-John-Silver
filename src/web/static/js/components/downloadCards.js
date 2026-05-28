/**
 * Download cards component for LJS.
 *
 * Renders the active download grid with per-card progress bars, stats,
 * file-level metadata, and action buttons. All mutation actions use
 * ActionClient calling POST /api/actions with ActionCommand format.
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
        super('cargo-hold');
        this.gridContainer = document.getElementById('active-downloads');
        this.activeBadge = document.getElementById('active-count');
        this.summaryLine = this.container ? this.container.querySelector('.summary-line') : null;
        this.downloads = new Map();
        this._init();
    }
    _init() {
        if (!this.container) return;
        shipEvents.subscribe('dl_stats', (e) => this._updateStats(e.id, e.stats));
        shipEvents.subscribe('dl_event', (e) => this._handleEvent(e));
        this.load();
    }
    /**
     * Load data required by DownloadManager.
     *
     * Keep DOM lookups local, prefer ActionClient/APIClient for server calls,
     * and preserve event names/data attributes so other components can extend
     * this behavior without reaching into private state.
     */
    async load() {
        try {
            const data = await APIClient.get('/api/downloads');
            (data.active || []).forEach(d => this.downloads.set(d.id, d));
            this.render();
        } catch (e) {}
    }
    _handleEvent(e) {
        if (e.subtype === 'cancelled' || e.subtype === 'removed') this.downloads.delete(e.id);
        else if (e.download) this.downloads.set(e.id, e.download);
        this.render();
    }
    _updateStats(id, stats) {
        if (!this.downloads.has(id)) return;
        const dl = this.downloads.get(id);
        Object.assign(dl, stats);
        const card = document.querySelector(`.dl-card[data-id="${id}"]`);
        if (!card) return;
        const bar = card.querySelector('.dl-bar');
        if (bar) bar.style.width = (dl.progress * 100).toFixed(1) + '%';
        const pctEl = card.querySelector('.dl-pct');
        if (pctEl) pctEl.textContent = (dl.progress * 100).toFixed(0) + '%';
        card.querySelector('.dl-stat-speed .stat-val').textContent = (dl.download_rate / 1024).toFixed(0);
        card.querySelector('.dl-stat-up .stat-val').textContent = (dl.upload_rate / 1024).toFixed(0);
        const swarmEl = card.querySelector('.dl-stat-peers .stat-val');
        if (swarmEl) {
            const source = dl.source_seeders != null ? ` / src ${dl.source_seeders}` : '';
            swarmEl.textContent = `${dl.num_seeds || 0} seeds · ${dl.num_peers || 0} peers${source}`;
        }
        const etaEl = card.querySelector('.dl-stat-eta .stat-val');
        if (dl.eta_seconds > 0) {
            const m = Math.floor(dl.eta_seconds / 60), s = Math.floor(dl.eta_seconds % 60);
            etaEl.textContent = m > 0 ? `${m}m${s}s` : `${s}s`;
        } else etaEl.textContent = '—';
        if (dl.files && dl.files.length > 0) {
            const filesDiv = card.querySelector('.dl-files');
            if (filesDiv) {
                const existingRows = filesDiv.querySelectorAll('.file-row');
                if (existingRows.length === 0 && dl.files.length > 0) {
                    this._buildFileRows(dl, filesDiv);
                    filesDiv.style.display = 'block';
                    const toggle = card.querySelector('.dl-expand-toggle');
                    if (toggle) toggle.textContent = '▴';
                } else {
                    dl.files.forEach((f, idx) => {
                        const fileIdx = f.file_index != null ? f.file_index : idx;
                        const row = filesDiv.querySelector(`.file-row[data-file-index="${fileIdx}"]`);
                        if (!row) return;
                        let downloadedBytes = f.downloaded_bytes != null ? f.downloaded_bytes : (f.downloaded || 0);
                        if (!downloadedBytes && dl.files.length === 1 && dl.downloaded_bytes) downloadedBytes = Math.min(dl.downloaded_bytes, f.size || dl.total_size || dl.downloaded_bytes);
                        const fallbackProgress = (dl.files.length === 1 && dl.progress != null) ? dl.progress : 0;
                        const bar = row.querySelector('.file-bar');
                        if (bar) {
                            let prog = f.progress != null ? f.progress : (f.size ? (downloadedBytes / f.size) : fallbackProgress);
                            if (fallbackProgress > prog) prog = fallbackProgress;
                            bar.style.width = (prog * 100) + '%';
                        }
                        const sizeEl = row.querySelector('.file-size');
                        if (sizeEl) {
                            sizeEl.textContent = `${formatBytes(downloadedBytes)} / ${formatBytes(f.size || 0)}`;
                        }
                        if (f.status) {
                            const badge = row.querySelector('.file-status-badge');
                            if (badge) {
                                badge.className = `file-status-badge status-${f.status}`;
                                const labels = { organized: '📦', complete: '✓', downloading: '↓', pending: '○' };
                                badge.textContent = labels[f.status] || '○';
                            }
                        }
                        const fullPathEl = row.querySelector('.file-fullpath');
                        if (fullPathEl) fullPathEl.textContent = f.file_path || f.path || '';
                        const nameEl = row.querySelector('.file-name');
                        if (nameEl) nameEl.title = f.file_path || f.path || '';
                    });
                }
            }
        }
        this._updateSummary();
    }
    async _action(id, type) {
        try {
            if (type === 'cancel' && !(await ljsConfirm('Stop this download and remove partial files?', { title: 'Cancel Download', confirmText: 'Cancel Download', danger: true }))) return;
            let result;
            switch (type) {
                case 'pause':
                    result = await ActionClient.pauseDownload(id);
                    break;
                case 'resume':
                    result = await ActionClient.resumeDownload(id);
                    break;
                case 'cancel':
                    result = await ActionClient.cancelDownload(id);
                    break;
                case 'restart':
                    result = await ActionClient.restartDownload(id);
                    break;
                case 'priority':
                    result = await ActionClient.setDownloadPriority(id, null);
                    break;
                default:
                    throw new Error('Unknown action: ' + type);
            }
            if (type === 'cancel') this.downloads.delete(id);
            else if (result.ok && result.data) this.downloads.set(id, result.data);
            this.render();
        } catch (e) { toast.error(e.message); }
    }
    /**
     * Render DownloadManager state into the DOM.
     *
     * Keep DOM lookups local, prefer ActionClient/APIClient for server calls,
     * and preserve event names/data attributes so other components can extend
     * this behavior without reaching into private state.
     */
    render() {
        if (!this.gridContainer) return;
        this.gridContainer.innerHTML = '';
        if (this.downloads.size === 0) {
            this.gridContainer.appendChild(DOM.el('p', { className: 'empty-msg' }, ['No active downloads.']));
            if (this.activeBadge) this.activeBadge.textContent = '0 active';
            if (this.summaryLine) this.summaryLine.textContent = 'No active downloads';
            return;
        }
        const groups = new Map();
        for (const dl of this.downloads.values()) {
            if (!groups.has(dl.item_name)) groups.set(dl.item_name, []);
            groups.get(dl.item_name).push(dl);
        }
        for (const [showName, items] of groups.entries()) {
            const groupEl = DOM.el('div', { className: 'dl-show-group', dataset: { show: showName } });
            const header = DOM.el('div', { className: 'dl-show-header' });
            header.appendChild(DOM.el('span', { className: 'dl-show-name' }, [showName]));
            header.appendChild(DOM.el('span', { className: 'dl-show-count' }, [`${items.length} download${items.length !== 1 ? 's' : ''}`]));
            groupEl.appendChild(header);
            const grid = DOM.el('div', { className: 'dl-grid' });
            items.forEach(dl => grid.appendChild(this._buildCard(dl)));
            groupEl.appendChild(grid);
            this.gridContainer.appendChild(groupEl);
        }
        this._updateSummary();
    }
    _buildFileRows(dl, filesDiv) {
        filesDiv.innerHTML = '';
        (dl.files || []).forEach((f, idx) => {
            const ep = f.season && f.episode ? `S${String(f.season).padStart(2, '0')}E${String(f.episode).padStart(2, '0')}` : null;
            let rowDownloaded = f.downloaded_bytes != null ? f.downloaded_bytes : (f.downloaded || 0);
            if (!rowDownloaded && (dl.files || []).length === 1 && dl.downloaded_bytes) rowDownloaded = Math.min(dl.downloaded_bytes, f.size || dl.total_size || dl.downloaded_bytes);
            const rowFallbackProgress = ((dl.files || []).length === 1 && dl.progress != null) ? dl.progress : 0;
            let prog = f.progress != null ? f.progress : (f.size ? (rowDownloaded / f.size) : rowFallbackProgress);
            if (rowFallbackProgress > prog) prog = rowFallbackProgress;
            const fileIdx = f.file_index != null ? f.file_index : idx;
            const fileName = (f.file_path || f.path || '').split('/').pop() || f.name || '?';
            const fullPath = f.file_path || f.path || '';
            const downloadedBytes = rowDownloaded;
            const totalSize = f.size || 0;
            const prio = f.priority;
            const st = f.status || 'downloading';

            const row = DOM.el('div', { className: `file-row ${ep ? 'has-ep' : 'no-ep'}`, dataset: { fileIndex: fileIdx } });
            if (ep) row.appendChild(DOM.el('span', { className: 'file-ep-badge' }, [ep]));
            const nameCell = DOM.el('div', { className: 'file-name-cell' });
            nameCell.appendChild(DOM.el('span', { className: 'file-name', title: fullPath }, [fileName]));
            if (fullPath) nameCell.appendChild(DOM.el('span', { className: 'file-fullpath' }, [fullPath]));
            row.appendChild(nameCell);
            row.appendChild(DOM.el('span', { className: 'file-size' }, [`${formatBytes(downloadedBytes)} / ${formatBytes(totalSize)}`]));
            const bw = DOM.el('div', { className: 'file-bar-wrap' });
            bw.appendChild(DOM.el('div', { className: 'file-bar', style: { width: (prog * 100) + '%' } }));
            row.appendChild(bw);

            const fileIndexForApi = fileIdx;
            if (prio != null) {
                const prioLabels = { 0: 'Skip', 1: 'Low', 4: 'Norm', 7: 'High' };
                const prioGroup = DOM.el('span', { className: 'file-prio-group' });
                prioGroup.appendChild(DOM.btn('▲', 'file-prio-up', () => this._setFilePriority(dl.id, fileIndexForApi, this._nextPrio(prio, 1)), { title: 'Increase priority' }));
                prioGroup.appendChild(DOM.el('span', { className: 'file-prio-label' }, [prioLabels[prio] || prio]));
                prioGroup.appendChild(DOM.btn('▼', 'file-prio-down', () => this._setFilePriority(dl.id, fileIndexForApi, this._nextPrio(prio, -1)), { title: 'Decrease priority' }));
                row.appendChild(prioGroup);
            }

            const stLabels = { organized: '📦', complete: '✓', downloading: '↓', pending: '○' };
            row.appendChild(DOM.el('span', { className: `file-status-badge status-${st}` }, [stLabels[st] || '○']));
            filesDiv.appendChild(row);
        });
    }
    _buildCard(dl) {
        const hasFiles = dl.files && dl.files.length > 0;
        const card = DOM.el('div', { className: `dl-card ${dl.status === 'paused' ? 'dl-card-paused' : ''}`, dataset: { id: dl.id } });
        const top = DOM.el('div', { className: 'dl-card-top' });
        if (dl.season && dl.episode) top.appendChild(DOM.el('span', { className: 'ep-badge ep-badge-lg' }, [`S${String(dl.season).padStart(2, '0')}E${String(dl.episode).padStart(2, '0')}`]));
        top.appendChild(DOM.el('span', { className: 'dl-reason' }, [(dl.reason || 'queued').replace(/_/g, ' ')]));
        top.appendChild(DOM.el('span', { className: `dl-status status-${dl.status}` }, [dl.status]));
        top.appendChild(DOM.el('span', { className: `dl-priority-badge priority-${dl.priority || 'normal'}` }, [dl.priority || 'normal']));
        if (dl.language) {
            top.appendChild(DOM.el('span', { className: `dl-lang-badge ${dl.language === 'English' ? 'lang-default' : ''}` }, [dl.language]));
        }
        if (hasFiles) {
            top.appendChild(DOM.el('span', { className: 'dl-expand-toggle', title: 'Toggle file details' }, ['▾']));
        }
        card.appendChild(top);
        const bw = DOM.el('div', { className: 'dl-bar-wrap' });
        bw.appendChild(DOM.el('div', { className: 'dl-bar', style: { width: (dl.progress * 100) + '%' } }));
        bw.appendChild(DOM.el('span', { className: 'dl-pct' }, [Math.round(dl.progress * 100) + '%']));
        card.appendChild(bw);
        const stats = DOM.el('div', { className: 'dl-stats' });
        const source = dl.source_seeders != null ? ` / src ${dl.source_seeders}` : '';
        const initialDownKbps = ((dl.download_rate || 0) / 1024).toFixed(0);
        const initialUpKbps = ((dl.upload_rate || 0) / 1024).toFixed(0);
        stats.innerHTML = `<span class="dl-stat dl-stat-speed"><span class="stat-icon">⬇</span><span class="stat-val">${initialDownKbps}</span> kB/s</span>` +
                         `<span class="dl-stat dl-stat-up"><span class="stat-icon">⬆</span><span class="stat-val">${initialUpKbps}</span> kB/s</span>` +
                         `<span class="dl-stat dl-stat-peers" title="Live seeds/peers from libtorrent; src is the search-time indexer snapshot."><span class="stat-icon">👥</span><span class="stat-val">${dl.num_seeds || 0} seeds · ${dl.num_peers || 0} peers${source}</span></span>` +
                         '<span class="dl-stat dl-stat-eta"><span class="stat-icon">⏳</span><span class="stat-val">—</span></span>';
        card.appendChild(stats);

        const filesDiv = DOM.el('div', { className: 'dl-files', dataset: { id: dl.id }, style: { display: 'none' } });
        if (hasFiles) {
            this._buildFileRows(dl, filesDiv);
            top.querySelector('.dl-expand-toggle').addEventListener('click', (e) => {
                e.stopPropagation();
                const expanded = filesDiv.style.display !== 'none';
                filesDiv.style.display = expanded ? 'none' : 'block';
                e.target.textContent = expanded ? '▾' : '▴';
            });
        }
        card.appendChild(filesDiv);

        const acts = DOM.el('div', { className: 'dl-actions' });
        if (dl.status === 'paused') acts.appendChild(DOM.btn('▶', 'btn-outline dl-resume-btn', () => this._action(dl.id, 'resume'), { title: 'Resume' }));
        else if (['downloading', 'queued'].includes(dl.status)) acts.appendChild(DOM.btn('⏸', 'btn-outline dl-pause-btn', () => this._action(dl.id, 'pause'), { title: 'Pause' }));
        else acts.appendChild(DOM.btn('🔄', 'btn-outline dl-restart-btn', () => this._action(dl.id, 'restart'), { title: 'Restart' }));
        acts.appendChild(DOM.btn('🔺', 'btn-outline dl-prio-btn', () => this._action(dl.id, 'priority'), { title: 'Cycle Priority' }));
        acts.appendChild(DOM.btn('Cancel', 'btn-danger cancel-btn', () => this._action(dl.id, 'cancel')));
        card.appendChild(acts);
        return card;
    }
    _nextPrio(current, direction) {
        const levels = [0, 1, 4, 7];
        let idx = levels.indexOf(current);
        if (idx === -1) idx = 2;
        idx += direction;
        if (idx < 0) idx = 0;
        if (idx >= levels.length) idx = levels.length - 1;
        return levels[idx];
    }
    async _setFilePriority(downloadId, fileIndex, priority) {
        try {
            await ActionClient.setFilePriority(downloadId, fileIndex, priority);
            this.load();
        } catch (e) {
            toast.error('Failed to set file priority: ' + e.message);
        }
    }
    _updateSummary() {
        const count = this.downloads.size;
        if (this.activeBadge) this.activeBadge.textContent = count + ' active';
        if (this.summaryLine) {
            const names = Array.from(this.downloads.values()).slice(0, 3).map(d => d.item_name);
            this.summaryLine.textContent = names.length ? names.join(', ') + (count > 3 ? ' +' + (count - 3) + ' more' : '') : 'No active downloads';
        }
    }
}

window.DownloadManager = DownloadManager;
