/**
 * Settings save handlers for LJS.
 *
 * Each function posts JSON to the relevant API endpoint and shows
 * a toast notification on success or failure.
 */

async function saveLLM() {
    const data = {
        provider: document.getElementById('provider-select').value,
        model: document.getElementById('model').value.trim(),
        api_base: document.getElementById('api_base').value.trim() || null,
    };
    await APIClient.post('/api/settings/llm', data);
    toast.show('Default LLM saved');
}

/**
 * Public UI helper for the saveTiers workflow.
 *
 * Keep inputs DOM-safe, delegate server mutations through API or Action clients,
 * and preserve the return/side-effect contract because templates may call this
 * function directly from event handlers.
 */
async function saveTiers() {
    const tiers = {};
    for (const tier of ['lightweight', 'standard', 'heavy']) {
        var modelEl = document.getElementById('tier-' + tier + '-model');
        var providerEl = document.getElementById('tier-' + tier + '-provider');
        if (!modelEl) continue;
        var model = modelEl.value.trim();
        var provider = providerEl ? providerEl.value : '';
        tiers[tier] = { model: model || null, provider: provider || null };
    }
    await APIClient.post('/api/settings/tiers', tiers);
    toast.show('Tiers saved');
}

/**
 * Public UI helper for the saveLibrary workflow.
 *
 * Keep inputs DOM-safe, delegate server mutations through API or Action clients,
 * and preserve the return/side-effect contract because templates may call this
 * function directly from event handlers.
 */
async function saveLibrary() {
    const data = {
        download_dir: (document.getElementById('download_dir') || {}).value || './downloads',
        max_concurrent: parseInt((document.getElementById('max_concurrent') || {}).value || '3'),
        category_settings: collectCategorySettings(),
        stall_check_interval_minutes: parseInt((document.getElementById('stall_check_interval_minutes') || {}).value || '30'),
        stall_alternative_hours: parseFloat((document.getElementById('stall_alternative_hours') || {}).value || '1.0'),
        stall_cancel_hours: parseFloat((document.getElementById('stall_cancel_hours') || {}).value || '5.0'),
    };
    await APIClient.post('/api/settings/library', data);
    toast.show('Library settings saved');
}

/**
 * Public UI helper for the collectCategorySettings workflow.
 *
 * Keep inputs DOM-safe, delegate server mutations through API or Action clients,
 * and preserve the return/side-effect contract because templates may call this
 * function directly from event handlers.
 */
function collectCategorySettings() {
    const settings = {};
    document.querySelectorAll('.category-property-input').forEach((input) => {
        const catId = input.dataset.categoryId;
        const propName = input.dataset.propertyName;
        const type = input.dataset.valueType;
        let value = input.value;
        if (type === 'int') {
            value = parseInt(value) || 0;
        } else if (type === 'float') {
            value = parseFloat(value) || 0.0;
        } else if (type === 'bool') {
            value = value === 'true';
        }
        if (!settings[catId]) {
            settings[catId] = {};
        }
        settings[catId][propName] = value;
    });
    return settings;
}

/**
 * Public UI helper for the saveSearch workflow.
 *
 * Keep inputs DOM-safe, delegate server mutations through API or Action clients,
 * and preserve the return/side-effect contract because templates may call this
 * function directly from event handlers.
 */
async function saveSearch() {
    const data = {
        jackett_url: (document.getElementById('jackett_url') || {}).value || '',
        jackett_api_key: (document.getElementById('jackett_api_key') || {}).value || '',
        direct_scraper_fallback: !!((document.getElementById('direct_scraper_fallback') || {}).checked),
    };
    await APIClient.post('/api/settings/search', data);
    toast.show('Search providers saved');
}

/**
 * Public UI helper for the saveIntegrations workflow.
 *
 * Keep inputs DOM-safe, delegate server mutations through API or Action clients,
 * and preserve the return/side-effect contract because templates may call this
 * function directly from event handlers.
 */
async function saveIntegrations() {
    const data = {
        tmdb_api_key: (document.getElementById('tmdb_api_key') || {}).value || '',
        trakt_client_id: (document.getElementById('trakt_client_id') || {}).value || '',
        plex_url: (document.getElementById('plex_url') || {}).value || '',
        plex_token: (document.getElementById('plex_token') || {}).value || '',
        opensubtitles_api_key: (document.getElementById('opensubtitles_api_key') || {}).value || '',
    };
    await APIClient.post('/api/settings/integrations', data);
    toast.show('Integrations saved');
}

/**
 * Public UI helper for the saveBridges workflow.
 *
 * Keep inputs DOM-safe, delegate server mutations through API or Action clients,
 * and preserve the return/side-effect contract because templates may call this
 * function directly from event handlers.
 */
async function saveBridges() {
    const data = {
        discord_token: (document.getElementById('discord_token') || {}).value || '',
        discord_channel_id: (document.getElementById('discord_channel_id') || {}).value || '',
        telegram_token: (document.getElementById('telegram_token') || {}).value || '',
    };
    await APIClient.post('/api/settings/bridges', data);
    toast.show('Signal lines saved');
}

/**
 * Public UI helper for the savePassword workflow.
 *
 * Keep inputs DOM-safe, delegate server mutations through API or Action clients,
 * and preserve the return/side-effect contract because templates may call this
 * function directly from event handlers.
 */
async function savePassword() {
    const pw = (document.getElementById('new_password') || {}).value || '';
    const confirm = (document.getElementById('new_password_confirm') || {}).value || '';
    if (pw !== confirm) {
        toast.show('Passwords do not match', 'err');
        return;
    }
    if (!pw) {
        toast.show('Password cannot be empty', 'err');
        return;
    }
    await APIClient.post('/api/settings/password', { new_password: pw, confirm: confirm });
    toast.show('Password changed');
    document.getElementById('new_password').value = '';
    document.getElementById('new_password_confirm').value = '';
}

/**
 * Public UI helper for the saveAutomation workflow.
 *
 * Keep inputs DOM-safe, delegate server mutations through API or Action clients,
 * and preserve the return/side-effect contract because templates may call this
 * function directly from event handlers.
 */
async function saveAutomation() {
    var autoDownload = document.querySelector('input[name="settings-automation"]:checked');
    var autoDiscover = document.getElementById('settings-auto-discover');
    try {
        await APIClient.post('/api/settings/auto_download', {
            auto_download: autoDownload ? autoDownload.value === 'auto' : false,
            auto_discover: autoDiscover ? autoDiscover.checked : true,
        });
        var badge = document.getElementById('automation-badge');
        if (badge) badge.textContent = autoDownload && autoDownload.value === 'auto' ? 'Auto' : 'Suggest';
        toast.show('Automation settings saved');
    } catch (e) {
        toast.show('Failed to save: ' + (e.message || 'error'), 'err');
    }
}

/**
 * Public UI helper for the updateSettingsAutoHighlight workflow.
 *
 * Keep inputs DOM-safe, delegate server mutations through API or Action clients,
 * and preserve the return/side-effect contract because templates may call this
 * function directly from event handlers.
 */
function updateSettingsAutoHighlight() {
    var val = document.querySelector('input[name="settings-automation"]:checked');
    var isSuggest = val && val.value === 'suggest';
    var suggestCard = document.getElementById('auto-suggest-card');
    var autoCard = document.getElementById('auto-auto-card');
    if (suggestCard) {
        suggestCard.style.borderColor = isSuggest ? 'var(--teal)' : 'var(--border)';
    }
    if (autoCard) {
        autoCard.style.borderColor = isSuggest ? 'var(--border)' : 'var(--gold)';
    }
}


/** Configure a Jackett profile from legacy settings templates. */
async function configureJackettProfile(profile) {
    const result = await APIClient.post('/api/jackett/configure-indexers', { profile: profile || 'all_open_public' });
    toast.show(`Jackett ${profile}: added ${result.added || 0}, skipped ${result.skipped || 0}, failed ${result.failed || 0}`);
    await loadJackettIndexersLegacy();
}

/** Refresh Jackett diagnostics in legacy settings templates. */
async function loadJackettIndexersLegacy() {
    const target = document.getElementById('jackett-indexer-health-old');
    if (!target) return;
    try {
        const data = await APIClient.get('/api/jackett/indexers');
        const s = data.summary || {};
        target.textContent = `${s.configured_indexers || 0}/${s.total_indexers || 0} configured · open/public available ${s.public_like_count || 0} · book/audio ${s.book_or_audio_like_configured || 0}/${s.book_or_audio_like_count || 0}`;
    } catch (err) {
        target.textContent = `Jackett diagnostics unavailable: ${err.message}`;
    }
}

/** Open Jackett dashboard from legacy settings templates. */
function openJackettUiLegacy() {
    const input = document.getElementById('jackett_url');
    const base = (input && input.value ? input.value : 'http://localhost:9117').replace(/\/$/, '');
    window.open(base + '/UI/Dashboard', '_blank', 'noopener,noreferrer');
}

/** Load native Jackett config schema into the legacy settings template. */
async function loadJackettCustomIndexerSchemaLegacy() {
    const id = ((document.getElementById('jackett_custom_indexer_id') || {}).value || '').trim();
    const container = document.getElementById('jackett_custom_indexer_fields');
    if (!id || !container) return;
    const data = await APIClient.get(`/api/jackett/indexers/${encodeURIComponent(id)}/config`);
    container.innerHTML = '';
    (data.fields || []).forEach(field => {
        const wrap = document.createElement('div');
        wrap.className = 'form-group';
        const label = document.createElement('label');
        label.textContent = field.name || field.id;
        const input = document.createElement('input');
        input.type = 'text';
        input.autocomplete = 'off';
        input.dataset.fieldId = field.id;
        input.className = field.secret ? 'ljs-secret-input jackett-custom-field' : 'jackett-custom-field';
        input.value = field.value || '';
        input.placeholder = field.help || field.name || field.id;
        input.setAttribute('data-lpignore', 'true');
        input.setAttribute('data-1p-ignore', 'true');
        input.setAttribute('data-bwignore', 'true');
        wrap.appendChild(label);
        wrap.appendChild(input);
        container.appendChild(wrap);
    });
}

/** Configure a user-selected Jackett indexer from legacy settings templates. */
async function configureJackettCustomIndexerLegacy() {
    const id = ((document.getElementById('jackett_custom_indexer_id') || {}).value || '').trim();
    if (!id) return toast.show('Enter a Jackett indexer id first.', 'err');
    const values = {};
    document.querySelectorAll('#jackett_custom_indexer_fields .jackett-custom-field').forEach((input) => {
        values[input.dataset.fieldId] = input.value;
    });
    const result = await APIClient.post(`/api/jackett/indexers/${encodeURIComponent(id)}/configure`, { values });
    if (result.status === 'ok' || result.configured) toast.show(`Configured Jackett indexer ${id}`);
    else toast.show(result.error || `Failed to configure Jackett indexer ${id}`, 'err');
    await loadJackettIndexersLegacy();
}

document.addEventListener('DOMContentLoaded', loadJackettIndexersLegacy);
