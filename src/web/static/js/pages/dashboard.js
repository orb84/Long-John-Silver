/**
 * Dashboard page for LJS.
 *
 * Page-specific logic: global download actions, log viewer,
 * toggle-header behavior, and application initialization. Category item UI is
 * delegated to BootyPanel and CategoryItemDetailModal.
 */

class DashboardManager extends Component {
    /**
     * Construct and initialize the DashboardManager instance.
     *
     * Keep DOM lookups local, prefer ActionClient/APIClient for server calls,
     * and preserve event names/data attributes so other components can extend
     * this behavior without reaching into private state.
     */
    constructor() {
        super('treasure-map');
        this._init();
    }

    _init() {
        shipEvents.subscribe('system', (e) => {
            if (['category_item_added', 'category_item_removed', 'category_item_updated', 'category_item_paused', 'category_item_resumed', 'category_action_completed'].includes(e.subtype)) {
                if (window.bootyPanel) window.bootyPanel.loadCatalog();
                if (window.settingsPanel) window.settingsPanel.loadSettings();
            }
        });
    }
}

/**
 * Public UI helper for the refreshLogs workflow.
 *
 * Keep inputs DOM-safe, delegate server mutations through API or Action clients,
 * and preserve the return/side-effect contract because templates may call this
 * function directly from event handlers.
 */
async function refreshLogs() {
    const cont = document.getElementById('log-container');
    if (!cont) return;
    try {
        const activeTab = document.querySelector('[data-log-level].is-active');
        const level = activeTab ? (activeTab.getAttribute('data-log-level') || 'all') : 'all';
        const data = await APIClient.get(`/api/system/logs?lines=200&level=${encodeURIComponent(level)}`);
        cont.innerHTML = '';
        (data.logs || []).forEach(line => {
            const p = DOM.el('div', {}, [line.trim()]);
            if (line.includes('| INFO')) p.style.color = '#fff';
            else if (line.includes('| WARNING')) p.style.color = '#ff0';
            else if (line.includes('| ERROR')) p.style.color = '#f00';
            else if (line.includes('| DEBUG')) p.style.color = '#666';
            cont.appendChild(p);
        });
        const body = document.getElementById('voyage-log-body');
        if (body) body.scrollTop = body.scrollHeight;
    } catch (e) {}
}

/**
 * Public UI helper for the promptManualUpload workflow.
 *
 * Keep inputs DOM-safe, delegate server mutations through API or Action clients,
 * and preserve the return/side-effect contract because templates may call this
 * function directly from event handlers.
 */
async function promptManualUpload() {
    const magnet = await ljsPrompt('Paste the magnet link or torrent URL.', { title: 'Add Torrent Link' });
    if (!magnet) return;
    const itemName = await ljsPrompt('Name this cargo.', { title: 'Cargo Name', defaultValue: 'Manual Upload' });
    try {
        await APIClient.post('/api/downloads/upload', { magnet, item_name: itemName });
        toast.show('Download started'); downloads.load();
    } catch (e) { toast.error(e.message); }
}

/**
 * Public UI helper for the clearCompleted workflow.
 *
 * Keep inputs DOM-safe, delegate server mutations through API or Action clients,
 * and preserve the return/side-effect contract because templates may call this
 * function directly from event handlers.
 */
async function clearCompleted() {
    if (!(await ljsConfirm('Clear all completed downloads from the list?', { title: 'Clear Completed', confirmText: 'Clear' }))) return;
    const data = await APIClient.get('/api/downloads');
    const completed = (data.active || []).filter(d => d.status === 'complete');
    if (!completed.length) { toast.show('No completed downloads.'); return; }
    let cleared = 0;
    for (const dl of completed) {
        try { await ActionClient.cancelDownload(dl.id); cleared++; } catch (e) { console.error('Clear failed:', dl.id, e); }
    }
    toast.show(`Cleared ${cleared}/${completed.length} completed download(s).`);
    downloads.load();
}

/**
 * Public UI helper for the scuttleAll workflow.
 *
 * Keep inputs DOM-safe, delegate server mutations through API or Action clients,
 * and preserve the return/side-effect contract because templates may call this
 * function directly from event handlers.
 */
async function scuttleAll() {
    if (!(await ljsConfirm('Cancel ALL active downloads? This cannot be undone.', { title: 'Cancel All Downloads', confirmText: 'Cancel All', danger: true }))) return;
    const data = await APIClient.get('/api/downloads');
    const active = (data.active || []).filter(d => d.status !== 'complete');
    if (!active.length) { toast.show('No active downloads to scuttle.'); return; }
    let scuttled = 0;
    for (const dl of active) {
        try { await ActionClient.cancelDownload(dl.id); scuttled++; } catch (e) { console.error('Scuttle failed:', dl.id, e); }
    }
    toast.show(`Scuttled ${scuttled}/${active.length} download(s).`);
    if (scuttled < active.length) toast.error('Some downloads could not be cancelled. Check console.');
    downloads.load();
}

window.DashboardManager = DashboardManager;
window.refreshLogs = refreshLogs;
window.promptManualUpload = promptManualUpload;
window.clearCompleted = clearCompleted;
window.scuttleAll = scuttleAll;

(function init() {
    window.chat = new AssistantChat();
    window.downloads = new DownloadManager();
    window.suggestions = new SuggestionManager();
    window.detailModal = new CategoryItemDetailModal();
    window.dashboard = new DashboardManager();

    document.addEventListener('click', (e) => {
        const header = e.target.closest('.toggle-header');
        if (header) {
            const body = document.getElementById(header.dataset.target);
            if (body) {
                const isOpen = body.style.display !== 'none';
                body.style.display = isOpen ? 'none' : 'block';
                const arrow = header.querySelector('.toggle-arrow');
                if (arrow) arrow.textContent = isOpen ? '▸' : '▾';
            }
        }
    });

    shipEvents.connect();

    setInterval(() => {
        if (document.getElementById('voyage-log-body')?.style.display !== 'none') refreshLogs();
    }, 10000);
})();
