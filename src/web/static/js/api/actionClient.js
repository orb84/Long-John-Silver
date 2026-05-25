/**
 * Action client for LJS.
 *
 * ActionClient sends typed ActionCommand objects to POST /api/actions
 * for all deterministic mutations (pause, resume, cancel, priority, etc.).
 * Read-only data fetching uses APIClient (GET endpoints).
 *
 * ActionCommand format:
 *   { name: "pause_download", arguments: { download_id: "..." }, source: "ui" }
 */

class APIClient {
    /**
     * Load data required by APIClient.
     *
     * Keep DOM lookups local, prefer ActionClient/APIClient for server calls,
     * and preserve event names/data attributes so other components can extend
     * this behavior without reaching into private state.
     */
    static async fetch(url, opts = {}) {
        opts.headers = opts.headers || {};
        if (!opts.headers['Content-Type'] && opts.method && opts.method !== 'GET') {
            opts.headers['Content-Type'] = 'application/json';
        }
        try {
            const response = await fetch(url, opts);
            const contentType = response.headers.get('content-type') || '';
            if (!response.ok) {
                let detail = response.statusText;
                if (contentType.includes('application/json')) {
                    const body = await response.json();
                    detail = body.detail || body.error || detail;
                }
                const error = new Error(detail);
                error.status = response.status;
                error.url = url;
                throw error;
            }
            if (contentType.includes('application/json')) return await response.json();
            return await response.text();
        } catch (error) {
            console.error(`API Error [${url}]:`, error);
            throw error;
        }
    }
    /**
     * Public method for the APIClient.get workflow.
     *
     * Keep DOM lookups local, prefer ActionClient/APIClient for server calls,
     * and preserve event names/data attributes so other components can extend
     * this behavior without reaching into private state.
     */
    static get(url) { return this.fetch(url, { method: 'GET' }); }
    /**
     * Public method for the APIClient.post workflow.
     *
     * Keep DOM lookups local, prefer ActionClient/APIClient for server calls,
     * and preserve event names/data attributes so other components can extend
     * this behavior without reaching into private state.
     */
    static post(url, body) { return this.fetch(url, { method: 'POST', body: JSON.stringify(body || {}) }); }
    /**
     * Public method for the APIClient.patch workflow.
     *
     * Keep DOM lookups local, prefer ActionClient/APIClient for server calls,
     * and preserve event names/data attributes so other components can extend
     * this behavior without reaching into private state.
     */
    static patch(url, body) { return this.fetch(url, { method: 'PATCH', body: JSON.stringify(body || {}) }); }
    /**
     * Public method for the APIClient.delete workflow.
     *
     * Keep DOM lookups local, prefer ActionClient/APIClient for server calls,
     * and preserve event names/data attributes so other components can extend
     * this behavior without reaching into private state.
     */
    static delete(url) { return this.fetch(url, { method: 'DELETE' }); }
}

/**
 * Owns the ActionClient UI component or frontend service contract.
 *
 * Keep this class focused on one browser-facing responsibility and inject
 * collaborators through the constructor or window composition root. Extend by
 * adding small public methods and preserving DOM/event contracts used by templates.
 */
class ActionClient {
    /**
     * Public method for the ActionClient.execute workflow.
     *
     * Keep DOM lookups local, prefer ActionClient/APIClient for server calls,
     * and preserve event names/data attributes so other components can extend
     * this behavior without reaching into private state.
     */
    static async execute(name, args = {}, source = 'ui') {
        return APIClient.post('/api/actions', { name, arguments: args, source });
    }

    /**
     * Public method for the ActionClient.pauseDownload workflow.
     *
     * Keep DOM lookups local, prefer ActionClient/APIClient for server calls,
     * and preserve event names/data attributes so other components can extend
     * this behavior without reaching into private state.
     */
    static async pauseDownload(downloadId) {
        return this.execute('pause_download', { download_id: downloadId });
    }
    /**
     * Public method for the ActionClient.resumeDownload workflow.
     *
     * Keep DOM lookups local, prefer ActionClient/APIClient for server calls,
     * and preserve event names/data attributes so other components can extend
     * this behavior without reaching into private state.
     */
    static async resumeDownload(downloadId) {
        return this.execute('resume_download', { download_id: downloadId });
    }
    /**
     * Public method for the ActionClient.cancelDownload workflow.
     *
     * Keep DOM lookups local, prefer ActionClient/APIClient for server calls,
     * and preserve event names/data attributes so other components can extend
     * this behavior without reaching into private state.
     */
    static async cancelDownload(downloadId) {
        return this.execute('cancel_download', { download_id: downloadId });
    }
    /**
     * Public method for the ActionClient.restartDownload workflow.
     *
     * Keep DOM lookups local, prefer ActionClient/APIClient for server calls,
     * and preserve event names/data attributes so other components can extend
     * this behavior without reaching into private state.
     */
    static async restartDownload(downloadId) {
        return this.execute('restart_download', { download_id: downloadId });
    }
    /**
     * Update ActionClient state through a public setter.
     *
     * Keep DOM lookups local, prefer ActionClient/APIClient for server calls,
     * and preserve event names/data attributes so other components can extend
     * this behavior without reaching into private state.
     */
    static async setDownloadPriority(downloadId, priority) {
        return this.execute('download_set_priority', { download_id: downloadId, priority });
    }
    /**
     * Update ActionClient state through a public setter.
     *
     * Keep DOM lookups local, prefer ActionClient/APIClient for server calls,
     * and preserve event names/data attributes so other components can extend
     * this behavior without reaching into private state.
     */
    static async setFilePriority(downloadId, fileIndex, priority) {
        return this.execute('set_file_priority', {
            download_id: downloadId, file_index: fileIndex, priority,
        });
    }
    /**
     * Public method for the ActionClient.pauseDownloads workflow.
     *
     * Keep DOM lookups local, prefer ActionClient/APIClient for server calls,
     * and preserve event names/data attributes so other components can extend
     * this behavior without reaching into private state.
     */
    static async pauseDownloads(downloadIds) {
        return this.execute('pause_downloads', { download_ids: downloadIds });
    }
    /**
     * Public method for the ActionClient.resumeDownloads workflow.
     *
     * Keep DOM lookups local, prefer ActionClient/APIClient for server calls,
     * and preserve event names/data attributes so other components can extend
     * this behavior without reaching into private state.
     */
    static async resumeDownloads(downloadIds) {
        return this.execute('resume_downloads', { download_ids: downloadIds });
    }
    /**
     * Public method for the ActionClient.cancelDownloads workflow.
     *
     * Keep DOM lookups local, prefer ActionClient/APIClient for server calls,
     * and preserve event names/data attributes so other components can extend
     * this behavior without reaching into private state.
     */
    static async cancelDownloads(downloadIds) {
        return this.execute('cancel_downloads', { download_ids: downloadIds });
    }
}

window.APIClient = APIClient;
window.ActionClient = ActionClient;
