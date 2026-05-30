/** Persistent notification inbox for the header bell. */
class NotificationInbox {
    /** Create the inbox and subscribe to live notification updates. */
    constructor() {
        this.button = document.getElementById('notification-bell');
        this.panel = null;
        this.unread = 0;
        if (!this.button) return;
        this.button.addEventListener('click', () => this.toggle());
        shipEvents.subscribe('system', (e) => {
            if (e.subtype === 'notifications_updated') this.load({ silent: true });
        });
        this.load({ silent: true });
    }

    _ensurePanel() {
        if (this.panel) return this.panel;
        this.panel = DOM.el('div', { className: 'notification-inbox hidden', id: 'notification-inbox' }, []);
        document.body.appendChild(this.panel);
        return this.panel;
    }

    /** Toggle the notification inbox dropdown. */
    async toggle() {
        const panel = this._ensurePanel();
        panel.classList.toggle('hidden');
        if (!panel.classList.contains('hidden')) await this.load();
    }

    /** Load notification rows and refresh the bell badge. */
    async load(options = {}) {
        try {
            const data = await APIClient.get('/api/notifications?limit=30');
            this.unread = data.unread || 0;
            this._render(data.notifications || []);
        } catch (e) {
            if (!options.silent) toast.error(e.message || 'Failed to load notifications');
        }
    }

    _render(rows) {
        const panel = this._ensurePanel();
        this.button.classList.toggle('has-unread', this.unread > 0);
        this.button.setAttribute('data-count', String(this.unread || ''));
        panel.innerHTML = '';
        panel.appendChild(DOM.el('div', { className: 'notification-inbox-header' }, [
            DOM.el('strong', {}, ['Notifications']),
            DOM.btn('Mark all read', 'btn-secondary btn-sm', () => this.markAllRead())
        ]));
        if (!rows.length) {
            panel.appendChild(DOM.el('p', { className: 'empty-msg' }, ['No notifications.']));
            return;
        }
        rows.forEach(row => panel.appendChild(this._row(row)));
    }

    _row(row) {
        const actions = Array.isArray(row.actions) ? row.actions : [];
        return DOM.el('article', { className: `notification-row ${row.status === 'unread' ? 'unread' : ''}` }, [
            DOM.el('div', {}, [
                DOM.el('strong', {}, [row.title || 'Notification']),
                DOM.el('p', { className: 'muted' }, [row.body || '']),
                row.created_at ? DOM.el('small', { className: 'muted' }, [new Date(row.created_at).toLocaleString()]) : null
            ].filter(Boolean)),
            DOM.el('div', { className: 'notification-actions' }, [
                ...actions.map(a => DOM.btn(a.label || 'Run', 'btn-gold btn-sm', () => this.runAction(row.id, a.key || a.id))),
                row.status === 'unread' ? DOM.btn('Read', 'btn-secondary btn-sm', () => this.markRead(row.id)) : null
            ].filter(Boolean))
        ]);
    }

    /** Execute one action attached to a notification. */
    async runAction(id, key) {
        try {
            const data = await APIClient.post(`/api/notifications/${id}/actions/${encodeURIComponent(key)}`);
            const receipt = data?.receipt || {};
            const msg = receipt.user_message || receipt.message || 'Notification action executed';
            if (receipt.status === 'failed') toast.error(msg);
            else if (receipt.status === 'partial' || receipt?.data?.queued === false) toast.show(msg, 'warning');
            else toast.show(msg);
            await this.load({ silent: true });
            if (window.downloads) downloads.load();
        } catch (e) { toast.error(e.message); }
    }

    /** Mark one notification as read. */
    async markRead(id) {
        await APIClient.post(`/api/notifications/${id}/read`);
        await this.load({ silent: true });
    }

    /** Mark all notifications as read. */
    async markAllRead() {
        await APIClient.post('/api/notifications/read-all');
        await this.load({ silent: true });
    }
}
window.NotificationInbox = NotificationInbox;
