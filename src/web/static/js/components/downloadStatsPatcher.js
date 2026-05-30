/**
 * Applies lightweight real-time download stat updates to an existing card.
 *
 * Use this class when WebSocket telemetry can be patched in-place.  If the
 * download gained files or changed a structural state, the owning component
 * should re-render the card instead of using this patcher.
 */
class DownloadStatsPatcher {
    /**
     * Patch progress, speed, swarm, ETA, priority, status, and file rows.
     *
     * The method intentionally accepts plain data and a card element, making it
     * reusable by future Hold views without depending on DownloadManager internals.
     */
    static patch(card, dl) {
        this._patchProgress(card, dl);
        this._patchSpeed(card, dl);
        this._patchSwarm(card, dl);
        this._patchEta(card, dl);
        this._patchPriority(card, dl);
        this._patchStatus(card, dl);
        this._patchFileRows(card, dl);
    }

    static _patchProgress(card, dl) {
        const fill = card.querySelector('.progress-fill');
        if (fill) fill.style.width = `${Math.round((dl.progress || 0) * 100)}%`;
    }

    static _patchSpeed(card, dl) {
        const speedVal = card.querySelector('.dl-speed');
        const rate = dl.display_download_rate != null ? dl.display_download_rate : dl.download_rate;
        if (speedVal) speedVal.innerHTML = `<i class="fa-solid fa-arrow-down"></i> ${Math.round((rate || 0) / 1024)} kB/s`;
        const upSpeedEl = card.querySelector('.dl-upspeed');
        if (upSpeedEl) upSpeedEl.innerHTML = `<i class="fa-solid fa-arrow-up"></i> ${Math.round((dl.upload_rate || 0) / 1024)} kB/s`;
    }

    static _swarmDisplay(dl) {
        const liveSeeds = Number(dl.num_seeds || 0);
        const sourceSeeders = dl.source_seeders != null ? Number(dl.source_seeders || 0) : 0;
        const scrapeSeeds = Math.max(Number(dl.num_complete || 0), Number(dl.list_seeds || 0));
        if (dl.display_seeders != null) {
            return { seeds: Number(dl.display_seeders || 0), basis: dl.display_seeders_basis || 'display' };
        }
        if (liveSeeds > 0) return { seeds: liveSeeds, basis: 'connected' };
        if (scrapeSeeds > 0) return { seeds: scrapeSeeds, basis: 'tracker' };
        if (sourceSeeders > 0) return { seeds: sourceSeeders, basis: 'source' };
        return { seeds: 0, basis: 'none' };
    }

    static _patchSwarm(card, dl) {
        const peersEl = card.querySelector('.dl-peers');
        if (!peersEl) return;
        const swarm = this._swarmDisplay(dl);
        const liveSeeds = Number(dl.num_seeds || 0);
        const peers = Number(dl.num_peers || 0);
        const source = dl.source_seeders != null ? ` · src ${dl.source_seeders}` : '';
        const basisLabel = swarm.basis === 'source' ? 'source snapshot' : (swarm.basis === 'tracker' ? 'tracker scrape' : 'connected');
        peersEl.innerHTML = `<i class="fa-solid fa-users"></i> seeds ${swarm.seeds} · peers ${peers}${source}`;
        peersEl.title = dl.source_seeders != null
            ? `Displayed seeds use ${basisLabel}. Connected seeds: ${liveSeeds}. Source seeders (${dl.source_seeders}) were reported by the indexer when selected.`
            : `Displayed seeds use ${basisLabel}. Connected seeds: ${liveSeeds}.`;
    }

    static _patchEta(card, dl) {
        const etaEl = card.querySelector('.dl-eta');
        if (!etaEl) return;
        if (dl.eta_seconds > 0) {
            const m = Math.floor(dl.eta_seconds / 60), s = Math.floor(dl.eta_seconds % 60);
            etaEl.innerHTML = `<i class="fa-regular fa-clock"></i> ${m > 0 ? `${m}m${s}s` : `${s}s`}`;
        } else {
            etaEl.innerHTML = '<i class="fa-regular fa-clock"></i> —';
        }
    }

    static _patchPriority(card, dl) {
        const prioBadge = card.querySelector('.dl-priority-badge');
        if (!prioBadge) return;
        const prio = String(dl.priority || 'normal').toLowerCase();
        const colors = { high: 'rgba(230, 57, 70, 0.25)', normal: 'rgba(255, 255, 255, 0.08)', low: 'rgba(42, 157, 143, 0.15)' };
        const textColors = { high: '#ff6b6b', normal: 'var(--text-dim)', low: 'var(--accent-teal)' };
        prioBadge.style.background = colors[prio] || colors.normal;
        prioBadge.style.color = textColors[prio] || textColors.normal;
        prioBadge.textContent = prio.toUpperCase();
    }

    static _patchStatus(card, dl) {
        const statusStrong = card.querySelector(`.status-${dl.status}`);
        if (statusStrong) statusStrong.textContent = dl.status;
    }

    static _patchFileRows(card, dl) {
        const filesDiv = card.querySelector('.dl-files');
        if (!filesDiv || !Array.isArray(dl.files) || dl.files.length < 1) return;
        dl.files.forEach((file, idx) => this._patchFileRow(filesDiv, dl, file, idx));
    }

    static _patchFileRow(filesDiv, dl, file, idx) {
        const fileIdx = file.file_index != null ? file.file_index : idx;
        const row = filesDiv.querySelector(`.file-row[data-file-index="${fileIdx}"]`);
        if (!row) return;
        const progress = DownloadFileRowsRenderer.progressFor(file, dl);
        const fBar = row.querySelector('.file-bar');
        if (fBar) fBar.style.width = `${Math.max(0, Math.min(100, progress * 100))}%`;
        const sizeCell = row.querySelector('.file-size');
        if (sizeCell) sizeCell.textContent = `${formatBytes(DownloadFileRowsRenderer.downloadedBytes(file, dl))} / ${formatBytes(file.size || 0)}`;
        const filePrioLabel = row.querySelector('.file-prio-label');
        if (filePrioLabel && file.priority != null) filePrioLabel.textContent = DownloadFileRowsRenderer.priorityLabel(file.priority);
    }
}

window.DownloadStatsPatcher = DownloadStatsPatcher;
