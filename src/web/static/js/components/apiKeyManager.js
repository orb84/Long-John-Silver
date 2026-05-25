/**
 * API key manager component for LJS.
 *
 * Loads, displays, activates, removes, and adds API keys for LLM providers.
 * Relies on window.currentProvider set by modelCatalog.js.
 */

async function loadProviderKeys(providerId) {
    const listEl = document.getElementById('provider-keys-list');
    const noKeysMsg = document.getElementById('no-keys-msg');

    try {
        const r = await fetch('/api/providers/' + providerId + '/keys');
        if (!r.ok) throw new Error('Failed to load keys');
        const data = await r.json();
        const keys = data.keys || [];

        listEl.querySelectorAll('.key-entry').forEach(function(el) { el.remove(); });

        if (keys.length === 0) {
            noKeysMsg.style.display = '';
            noKeysMsg.textContent = 'No API keys configured for this provider.';
        } else {
            noKeysMsg.style.display = 'none';
            for (const k of keys) {
                var div = document.createElement('div');
                div.className = 'key-entry';
                div.innerHTML = '<span class="key-label">' + escHtml(k.label) + '</span>' +
                    '<span class="key-preview">' + escHtml(k.key_preview) + '</span>' +
                    '<span class="' + (k.is_active ? 'key-active-badge' : 'key-inactive-badge') + '">' + (k.is_active ? 'active' : 'inactive') + '</span>' +
                    (!k.is_active ? '<button class="btn btn-teal btn-sm" onclick="activateKey(\'' + providerId + '\', \'' + k.id + '\')">Activate</button>' : '') +
                    '<button class="btn btn-danger btn-sm" onclick="removeKey(\'' + providerId + '\', \'' + k.id + '\')">Remove</button>';
                listEl.appendChild(div);
            }
        }
    } catch (e) {
        console.error('Error loading keys:', e);
        noKeysMsg.style.display = '';
        noKeysMsg.textContent = 'Failed to load API keys.';
    }
}

/**
 * Public UI helper for the escHtml workflow.
 *
 * Keep inputs DOM-safe, delegate server mutations through API or Action clients,
 * and preserve the return/side-effect contract because templates may call this
 * function directly from event handlers.
 */
function escHtml(str) {
    var div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}

/**
 * Public UI helper for the activateKey workflow.
 *
 * Keep inputs DOM-safe, delegate server mutations through API or Action clients,
 * and preserve the return/side-effect contract because templates may call this
 * function directly from event handlers.
 */
async function activateKey(providerId, keyId) {
    await APIClient.post('/api/providers/' + providerId + '/keys/' + keyId + '/activate', {});
    toast.show('Key activated');
    await loadProviderKeys(providerId);
}

/**
 * Public UI helper for the removeKey workflow.
 *
 * Keep inputs DOM-safe, delegate server mutations through API or Action clients,
 * and preserve the return/side-effect contract because templates may call this
 * function directly from event handlers.
 */
async function removeKey(providerId, keyId) {
    try {
        const r = await fetch('/api/providers/' + providerId + '/keys/' + keyId, { method: 'DELETE' });
        if (!r.ok) throw new Error('Failed to remove key');
        toast.show('Key removed');
        await loadProviderKeys(providerId);
    } catch (e) {
        toast.show(e.message, 'err');
    }
}

/**
 * Public UI helper for the addProviderKey workflow.
 *
 * Keep inputs DOM-safe, delegate server mutations through API or Action clients,
 * and preserve the return/side-effect contract because templates may call this
 * function directly from event handlers.
 */
async function addProviderKey() {
    if (!window.currentProvider) {
        toast.show('Select a provider first', 'err');
        return;
    }
    const label = document.getElementById('new-key-label').value.trim() || 'default';
    const key = document.getElementById('new-key-value').value.trim();
    if (!key) {
        toast.show('Enter an API key', 'err');
        return;
    }

    await APIClient.post('/api/providers/' + window.currentProvider + '/keys', { key: key, label: label, set_active: true });
    document.getElementById('new-key-label').value = '';
    document.getElementById('new-key-value').value = '';
    toast.show('API key added');
    await loadProviderKeys(window.currentProvider);
}
