/**
 * Server-side path browser used by setup and settings screens.
 *
 * The visible filesystem belongs to the LJS server process. The frontend may
 * be opened from another computer, so native browser file inputs cannot be
 * used for library/download folders.
 */
class ServerPathBrowserUI {
    /** Create the browser with empty UI state. */
    constructor() {
        this.state = {
            inputId: null,
            label: 'Folder',
            currentPath: '',
            selectedPath: '',
            parentPath: ''
        };
    }

    /** Open the browser for a text input identified by DOM id. */
    open(inputId, label) {
        var input = document.getElementById(inputId);
        if (!input) return;
        this.state.inputId = inputId;
        this.state.label = label || 'Folder';
        this.state.currentPath = input.value.trim();
        this.state.selectedPath = input.value.trim();
        this.ensureModal();
        this.showModal();
        this.load(this.state.currentPath);
    }

    /** Open the browser for a dynamic category path input element. */
    openForInput(input) {
        if (!input) return;
        if (!input.id) {
            input.id = 'setup-path-input-' + Math.random().toString(36).slice(2);
        }
        this.open(input.id, (input.dataset.categoryName || 'Category') + ' Library Folder');
    }

    /** Ensure the reusable browser modal exists in the DOM. */
    ensureModal() {
        if (document.getElementById('path-browser-overlay')) return;
        var overlay = document.createElement('div');
        overlay.id = 'path-browser-overlay';
        overlay.className = 'path-browser-overlay';
        overlay.style.display = 'none';
        overlay.innerHTML = [
            '<div class="path-browser-modal" role="dialog" aria-modal="true" aria-labelledby="path-browser-title">',
            '  <div class="path-browser-head">',
            '    <div>',
            '      <div class="path-browser-title" id="path-browser-title"><i class="fa-solid fa-folder-tree"></i> Choose server folder</div>',
            '      <div class="path-browser-subtitle">This navigates the filesystem on the machine running Long John Silver.</div>',
            '    </div>',
            '    <button type="button" class="path-browser-btn" onclick="closePathBrowserModal()"><i class="fa-solid fa-xmark"></i> Close</button>',
            '  </div>',
            '  <div class="path-browser-toolbar">',
            '    <button type="button" class="path-browser-btn" onclick="pathBrowserGoUp()"><i class="fa-solid fa-arrow-up"></i> Up</button>',
            '    <button type="button" class="path-browser-btn" onclick="loadServerPath(\'~\')"><i class="fa-solid fa-house"></i> Home</button>',
            '    <input type="text" id="path-browser-current" placeholder="/server/path" onkeydown="if(event.key === \'Enter\') loadServerPath(this.value)">',
            '    <button type="button" class="path-browser-btn" onclick="loadServerPath(document.getElementById(\'path-browser-current\').value)">Go</button>',
            '  </div>',
            '  <div class="path-browser-body">',
            '    <aside class="path-browser-sidebar" aria-label="Server locations">',
            '      <div class="path-browser-sidebar-title"><i class="fa-solid fa-hard-drive"></i> Server locations</div>',
            '      <div class="path-browser-sidebar-hint">Drives shown here belong to the LJS server.</div>',
            '      <div class="path-browser-roots" id="path-browser-roots"></div>',
            '    </aside>',
            '    <main class="path-browser-main">',
            '      <div class="path-browser-status" id="path-browser-status">Loading…</div>',
            '      <div id="path-browser-entries"></div>',
            '    </main>',
            '  </div>',
            '  <div class="path-browser-footer">',
            '    <div class="path-browser-new-folder">',
            '      <input type="text" id="path-browser-new-name" placeholder="New folder name" onkeydown="if(event.key === \'Enter\') createServerFolder()">',
            '      <button type="button" class="path-browser-btn" onclick="createServerFolder()"><i class="fa-solid fa-folder-plus"></i> Create here</button>',
            '    </div>',
            '    <button type="button" class="btn btn-gold" onclick="selectCurrentServerPath()"><i class="fa-solid fa-check"></i> Use this folder</button>',
            '  </div>',
            '</div>'
        ].join('');
        overlay.addEventListener('click', function(event) {
            if (event.target === overlay) closePathBrowserModal();
        });
        document.body.appendChild(overlay);
    }

    /** Show the browser modal with the active field label. */
    showModal() {
        var overlay = document.getElementById('path-browser-overlay');
        if (!overlay) return;
        overlay.style.display = 'flex';
        var title = document.getElementById('path-browser-title');
        if (title) title.innerHTML = '<i class="fa-solid fa-folder-tree"></i> Choose ' + this.escapeHtml(this.state.label);
    }

    /** Hide the browser modal without changing the selected path. */
    closeModal() {
        var overlay = document.getElementById('path-browser-overlay');
        if (overlay) overlay.style.display = 'none';
    }

    /** Navigate to the parent folder reported by the server. */
    goUp() {
        if (this.state.parentPath) {
            this.load(this.state.parentPath);
        }
    }

    /** Load a server directory payload and render it into the modal. */
    async load(path) {
        this.ensureModal();
        var status = document.getElementById('path-browser-status');
        var entries = document.getElementById('path-browser-entries');
        if (status) status.textContent = 'Reading server folder…';
        if (entries) entries.innerHTML = '';
        try {
            var url = '/api/storage/browse';
            if (path && String(path).trim()) url += '?path=' + encodeURIComponent(String(path).trim());
            var response = await fetch(url);
            var data = await response.json();
            if (!response.ok) throw new Error(data.detail || data.message || 'Could not browse folder');
            this.render(data);
        } catch (e) {
            if (status) status.textContent = e.message || 'Could not browse folder';
            if (window.toast) toast.show(status.textContent, 'err');
        }
    }

    /** Render sidebar roots grouped by server location kind. */
    renderRoots(data) {
        var roots = document.getElementById('path-browser-roots');
        if (!roots) return;
        roots.innerHTML = '';

        var groups = data.root_groups || [];
        if (!groups.length && (data.roots || []).length) {
            groups = [{ label: 'Server locations', entries: data.roots }];
        }

        groups.forEach((group) => {
            var visibleEntries = (group.entries || []).filter(function(root) { return root.exists; });
            if (!visibleEntries.length) return;

            var label = document.createElement('div');
            label.className = 'path-browser-root-group-label';
            label.textContent = group.label || 'Locations';
            roots.appendChild(label);

            visibleEntries.forEach((root) => {
                var item = document.createElement('button');
                item.type = 'button';
                item.className = 'path-browser-root-item' + (this.pathsMatch(root.path, data.path) ? ' active' : '');
                item.title = root.path;
                item.innerHTML = '<i class="fa-solid ' + this.iconForKind(root.kind) + '"></i>' +
                    '<span><strong>' + this.escapeHtml(root.name || root.path) + '</strong>' +
                    '<small>' + this.escapeHtml(root.path) + '</small></span>';
                item.onclick = () => this.load(root.path);
                roots.appendChild(item);
            });
        });

        if (!roots.children.length) {
            var empty = document.createElement('div');
            empty.className = 'path-browser-sidebar-hint';
            empty.textContent = 'No server drives were detected. Type a path manually above.';
            roots.appendChild(empty);
        }
    }

    /** Return the Font Awesome icon for a server location kind. */
    iconForKind(kind) {
        switch (kind) {
            case 'home': return 'fa-house';
            case 'drive': return 'fa-hard-drive';
            case 'mounts': return 'fa-plug';
            case 'network': return 'fa-network-wired';
            case 'configured': return 'fa-bookmark';
            case 'computer': return 'fa-computer';
            default: return 'fa-folder';
        }
    }

    /** Compare paths while ignoring trailing separators. */
    pathsMatch(a, b) {
        var left = String(a || '').replace(/[\\/]+$/, '');
        var right = String(b || '').replace(/[\\/]+$/, '');
        return left && right && left === right;
    }

    /** Render the current directory list and status message. */
    render(data) {
        this.state.currentPath = data.path || '';
        this.state.selectedPath = data.path || '';
        this.state.parentPath = data.parent || '';

        var current = document.getElementById('path-browser-current');
        if (current) current.value = data.path || '';

        this.renderRoots(data);

        var status = document.getElementById('path-browser-status');
        if (status) {
            var parts = [];
            if (data.message) parts.push(data.message);
            if (data.truncated) parts.push('Showing first batch only; type a more specific path if needed.');
            if (data.exists && !data.can_write) parts.push('This folder may not be writable by the server process.');
            status.textContent = parts.join(' ') || 'Select this folder, open a child folder, or create a new child folder.';
        }

        var entries = document.getElementById('path-browser-entries');
        if (!entries) return;
        entries.innerHTML = '';
        (data.entries || []).forEach((entry) => {
            var row = document.createElement('div');
            row.className = 'path-browser-row';
            row.title = entry.path;
            row.onclick = () => this.load(entry.path);
            row.innerHTML = '<i class="fa-solid fa-folder"></i>' +
                '<div><div class="path-browser-name">' + this.escapeHtml(entry.name) + '</div>' +
                '<div class="path-browser-meta">' + this.escapeHtml(entry.path) + '</div></div>' +
                '<i class="fa-solid fa-chevron-right"></i>';
            entries.appendChild(row);
        });
        if (!(data.entries || []).length) {
            var empty = document.createElement('div');
            empty.className = 'path-browser-status';
            empty.textContent = 'No child folders found here.';
            entries.appendChild(empty);
        }
    }

    /** Create a child folder below the current server path. */
    async createFolder() {
        var input = document.getElementById('path-browser-new-name');
        var name = input ? input.value.trim() : '';
        if (!name) {
            if (window.toast) toast.show('Choose a folder name first.', 'err');
            return;
        }
        try {
            var response = await fetch('/api/storage/mkdir', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ parent: this.state.currentPath, name: name })
            });
            var data = await response.json();
            if (!response.ok || data.ok === false) throw new Error(data.detail || data.message || 'Could not create folder');
            if (input) input.value = '';
            this.render(data);
            if (window.toast) toast.show('Folder created');
        } catch (e) {
            if (window.toast) toast.show(e.message || 'Could not create folder', 'err');
        }
    }

    /** Copy the current server path into the setup/settings input. */
    selectCurrentPath() {
        var input = document.getElementById(this.state.inputId);
        if (input && this.state.currentPath) {
            input.value = this.state.currentPath;
            input.dispatchEvent(new Event('change', { bubbles: true }));
        }
        this.closeModal();
    }

    /** Escape HTML entities before interpolating user/server strings. */
    escapeHtml(value) {
        return String(value || '')
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#039;');
    }
}

window.serverPathBrowserUI = new ServerPathBrowserUI();
window.openServerPathBrowser = function(inputId, label) { window.serverPathBrowserUI.open(inputId, label); };
window.openServerPathBrowserForInput = function(input) { window.serverPathBrowserUI.openForInput(input); };
window.closePathBrowserModal = function() { window.serverPathBrowserUI.closeModal(); };
window.pathBrowserGoUp = function() { window.serverPathBrowserUI.goUp(); };
window.loadServerPath = function(path) { window.serverPathBrowserUI.load(path); };
window.createServerFolder = function() { window.serverPathBrowserUI.createFolder(); };
window.selectCurrentServerPath = function() { window.serverPathBrowserUI.selectCurrentPath(); };
