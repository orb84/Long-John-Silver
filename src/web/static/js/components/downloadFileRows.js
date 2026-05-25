/**
 * Renders and updates file-level rows within a torrent download card.
 *
 * The renderer keeps per-file progress math and priority controls isolated from
 * DownloadManager.  Extension views can reuse it by passing callbacks for file
 * priority changes rather than subclassing the main Hold component.
 */
class DownloadFileRowsRenderer {
    /**
     * Construct a renderer with the callbacks needed for file priority actions.
     */
    constructor({ onSetFilePriority, nextPriority }) {
        this.onSetFilePriority = onSetFilePriority;
        this.nextPriority = nextPriority;
    }

    /**
     * Render all files for a download into the supplied container element.
     */
    render(dl, filesDiv) {
        filesDiv.innerHTML = '';
        (dl.files || []).forEach((file, index) => filesDiv.appendChild(this._row(dl, file, index)));
    }

    /**
     * Return downloaded bytes with single-file torrent fallback behavior.
     */
    static downloadedBytes(file, dl) {
        let downloaded = file.downloaded_bytes != null ? file.downloaded_bytes : (file.downloaded || 0);
        if (!downloaded && (dl.files || []).length === 1 && dl.downloaded_bytes) {
            downloaded = Math.min(dl.downloaded_bytes, file.size || dl.total_size || dl.downloaded_bytes);
        }
        return downloaded;
    }

    /**
     * Return normalized file progress while respecting single-file torrent fallback.
     */
    static progressFor(file, dl) {
        const downloaded = this.downloadedBytes(file, dl);
        const fallback = ((dl.files || []).length === 1 && dl.progress != null) ? dl.progress : 0;
        let progress = file.progress != null ? file.progress : (file.size ? (downloaded / file.size) : fallback);
        return Math.max(fallback, progress || 0);
    }

    /**
     * Return the compact display label for a libtorrent file priority value.
     */
    static priorityLabel(priority) {
        return ({ 0: 'Skip', 1: 'Low', 4: 'Norm', 7: 'High' })[priority] || String(priority);
    }

    _row(dl, file, index) {
        const fileIdx = file.file_index != null ? file.file_index : index;
        const ep = file.season && file.episode ? `S${String(file.season).padStart(2, '0')}E${String(file.episode).padStart(2, '0')}` : null;
        const fullPath = file.file_path || file.path || '';
        const fileName = fullPath.split('/').pop() || file.name || '?';
        const row = DOM.el('div', { className: `file-row ${ep ? 'has-ep' : 'no-ep'}`, dataset: { fileIndex: fileIdx } });
        if (ep) row.appendChild(DOM.el('span', { className: 'file-ep-badge', style: { color: 'var(--accent-teal)', fontFamily: 'monospace' } }, [ep]));
        row.appendChild(DOM.el('span', { className: 'file-name', title: fullPath }, [fileName]));
        row.appendChild(DOM.el('span', { className: 'file-size' }, [`${formatBytes(DownloadFileRowsRenderer.downloadedBytes(file, dl))} / ${formatBytes(file.size || 0)}`]));
        row.appendChild(this._progressTrack(file, dl));
        if (file.priority != null) row.appendChild(this._priorityControls(dl.id, fileIdx, file.priority));
        return row;
    }

    _progressTrack(file, dl) {
        const track = DOM.el('div', { className: 'file-bar-wrap' });
        track.appendChild(DOM.el('div', { className: 'file-bar', style: { width: `${DownloadFileRowsRenderer.progressFor(file, dl) * 100}%` } }));
        return track;
    }

    _priorityControls(downloadId, fileIndex, priority) {
        const group = DOM.el('span', { className: 'file-prio-group', style: { display: 'inline-flex', alignItems: 'center', gap: '4px' } });
        group.appendChild(this._priorityButton('▲', 'Increase priority', () => this.onSetFilePriority(downloadId, fileIndex, this.nextPriority(priority, 1))));
        group.appendChild(DOM.el('span', { className: 'file-prio-label', style: { fontFamily: 'monospace', fontSize: '0.75rem', color: 'var(--text)', minWidth: '32px', textAlign: 'center' } }, [DownloadFileRowsRenderer.priorityLabel(priority)]));
        group.appendChild(this._priorityButton('▼', 'Decrease priority', () => this.onSetFilePriority(downloadId, fileIndex, this.nextPriority(priority, -1))));
        return group;
    }

    _priorityButton(label, title, onclick) {
        const button = DOM.btn(label, 'file-prio-step', onclick, { title, style: { background: 'none', border: 'none', color: 'var(--text-dim)', cursor: 'pointer', fontSize: '0.7rem', padding: '0 2px', display: 'flex', alignItems: 'center' } });
        button.onmouseenter = () => { button.style.color = 'var(--accent-teal)'; };
        button.onmouseleave = () => { button.style.color = 'var(--text-dim)'; };
        return button;
    }
}

window.DownloadFileRowsRenderer = DownloadFileRowsRenderer;
