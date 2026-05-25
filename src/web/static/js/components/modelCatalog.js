/**
 * Model catalog component for LJS.
 *
 * Fetches model lists from LLM provider API endpoints, caches them,
 * and populates <select> elements with catalog search/filter.
 * Also manages the default-LLM provider selection flow.
 */

window.modelCache = {};
window.currentProvider = '';
window.tierModels = { lightweight: null, standard: null, heavy: null };

/**
 * Public UI helper for the fetchModels workflow.
 *
 * Keep inputs DOM-safe, delegate server mutations through API or Action clients,
 * and preserve the return/side-effect contract because templates may call this
 * function directly from event handlers.
 */
async function fetchModels(providerId) {
    if (window.modelCache[providerId]) return window.modelCache[providerId];
    try {
        const r = await fetch(`/api/providers/${providerId}/models?refresh=false`);
        if (!r.ok) throw new Error(`Failed to load models for ${providerId}`);
        const data = await r.json();
        window.modelCache[providerId] = data.models || [];
        return window.modelCache[providerId];
    } catch (e) {
        console.error('Model fetch error:', e);
        return [];
    }
}

/**
 * Public UI helper for the refreshModels workflow.
 *
 * Keep inputs DOM-safe, delegate server mutations through API or Action clients,
 * and preserve the return/side-effect contract because templates may call this
 * function directly from event handlers.
 */
async function refreshModels(providerId) {
    try {
        const r = await fetch(`/api/providers/${providerId}/models?refresh=true`);
        if (!r.ok) throw new Error(`Failed to refresh models for ${providerId}`);
        const data = await r.json();
        window.modelCache[providerId] = data.models || [];
        return window.modelCache[providerId];
    } catch (e) {
        console.error('Model refresh error:', e);
        return window.modelCache[providerId] || [];
    }
}

/**
 * Public UI helper for the updateModelSelect workflow.
 *
 * Keep inputs DOM-safe, delegate server mutations through API or Action clients,
 * and preserve the return/side-effect contract because templates may call this
 * function directly from event handlers.
 */
function updateModelSelect(selectEl, models, searchValue) {
    const query = (searchValue || '').toLowerCase();
    const filtered = query
        ? models.filter(function(m) { return m.id.toLowerCase().includes(query) || m.name.toLowerCase().includes(query); })
        : models;

    selectEl.innerHTML = '<option value="">— Select from catalog —</option>';
    for (const m of filtered.slice(0, 200)) {
        const opt = document.createElement('option');
        opt.value = m.id;
        let label = m.name || m.id;
        if (m.pricing && m.pricing.prompt_per_million != null) {
            label += ' ($' + m.pricing.prompt_per_million + '/M)';
        }
        if (m.context && m.context.max_context_tokens) {
            label += ' [' + (m.context.max_context_tokens / 1000).toFixed(0) + 'k]';
        }
        opt.textContent = label;
        selectEl.appendChild(opt);
    }
    if (filtered.length > 200) {
        const opt = document.createElement('option');
        opt.disabled = true;
        opt.textContent = '... and ' + (filtered.length - 200) + ' more (use search to narrow)';
        selectEl.appendChild(opt);
    }
}

/**
 * Public UI helper for the updateModelInfo workflow.
 *
 * Keep inputs DOM-safe, delegate server mutations through API or Action clients,
 * and preserve the return/side-effect contract because templates may call this
 * function directly from event handlers.
 */
function updateModelInfo(modelId, models) {
    const infoEl = document.getElementById('model-info');
    if (!infoEl || !modelId) { if (infoEl) infoEl.textContent = ''; return; }
    const m = models.find(function(x) { return x.id === modelId; });
    if (!m) { infoEl.textContent = modelId; return; }
    const parts = [];
    if (m.context && m.context.max_context_tokens) parts.push((m.context.max_context_tokens / 1000).toFixed(0) + 'k ctx');
    if (m.pricing && m.pricing.prompt_per_million != null) parts.push('$' + m.pricing.prompt_per_million + '/M in');
    if (m.pricing && m.pricing.completion_per_million != null) parts.push('$' + m.pricing.completion_per_million + '/M out');
    if (m.context && m.context.supports_vision) parts.push('vision');
    if (m.context && m.context.supports_tools) parts.push('tools');
    infoEl.textContent = parts.length ? parts.join(' · ') : modelId;
}

/**
 * Public UI helper for the onProviderChange workflow.
 *
 * Keep inputs DOM-safe, delegate server mutations through API or Action clients,
 * and preserve the return/side-effect contract because templates may call this
 * function directly from event handlers.
 */
async function onProviderChange(selectEl) {
    const providerId = selectEl.value;
    window.currentProvider = providerId;
    const modelsSection = document.getElementById('provider-models-section');

    if (!providerId) {
        modelsSection.style.display = 'none';
        return;
    }
    modelsSection.style.display = '';

    const modelSelect = document.getElementById('model-select');
    const searchInput = document.getElementById('model-search');
    searchInput.value = '';
    modelSelect.innerHTML = '<option value="">Loading models...</option>';

    const models = await fetchModels(providerId);
    updateModelSelect(modelSelect, models, '');
    await loadProviderKeys(providerId);

    var presetBases = {
        openrouter: 'https://openrouter.ai/api/v1',
        nvidia_nim: 'https://integrate.api.nvidia.com/v1',
        ollama_cloud: 'https://api.ollama.ai/v1',
        ollama_local: 'http://localhost:11434/v1',
        lm_studio: 'http://localhost:1234/v1',
    };
    var apiBaseEl = document.getElementById('api_base');
    if (presetBases[providerId] && !apiBaseEl.value) {
        apiBaseEl.value = presetBases[providerId];
    }
}

/**
 * Public UI helper for the onModelSelect workflow.
 *
 * Keep inputs DOM-safe, delegate server mutations through API or Action clients,
 * and preserve the return/side-effect contract because templates may call this
 * function directly from event handlers.
 */
function onModelSelect(selectEl) {
    const modelId = selectEl.value;
    if (!modelId) return;
    document.getElementById('model').value = modelId;
    updateModelInfo(modelId, window.modelCache[window.currentProvider] || []);
}

/**
 * Public UI helper for the filterModels workflow.
 *
 * Keep inputs DOM-safe, delegate server mutations through API or Action clients,
 * and preserve the return/side-effect contract because templates may call this
 * function directly from event handlers.
 */
function filterModels(query) {
    const models = window.modelCache[window.currentProvider] || [];
    updateModelSelect(document.getElementById('model-select'), models, query);
}

/**
 * Public UI helper for the onTierProviderChange workflow.
 *
 * Keep inputs DOM-safe, delegate server mutations through API or Action clients,
 * and preserve the return/side-effect contract because templates may call this
 * function directly from event handlers.
 */
function onTierProviderChange(tier, selectEl) {
    const providerId = selectEl.value;
    if (!providerId) {
        var modelSelect = document.getElementById('tier-' + tier + '-select');
        modelSelect.innerHTML = '<option value="">— Select from catalog —</option>';
        document.getElementById('tier-' + tier + '-search').value = '';
        window.tierModels[tier] = null;
        return;
    }

    var modelSelect = document.getElementById('tier-' + tier + '-select');
    var searchInput = document.getElementById('tier-' + tier + '-search');
    searchInput.value = '';
    modelSelect.innerHTML = '<option value="">Loading models...</option>';

    fetchModels(providerId).then(function(models) {
        window.tierModels[tier] = models;
        updateModelSelect(modelSelect, models, '');
    });
}

/**
 * Public UI helper for the onTierModelSelect workflow.
 *
 * Keep inputs DOM-safe, delegate server mutations through API or Action clients,
 * and preserve the return/side-effect contract because templates may call this
 * function directly from event handlers.
 */
function onTierModelSelect(tier, selectEl) {
    const modelId = selectEl.value;
    if (!modelId) return;
    document.getElementById('tier-' + tier + '-model').value = modelId;
}

/**
 * Public UI helper for the filterTierModels workflow.
 *
 * Keep inputs DOM-safe, delegate server mutations through API or Action clients,
 * and preserve the return/side-effect contract because templates may call this
 * function directly from event handlers.
 */
function filterTierModels(tier, query) {
    const models = window.tierModels[tier] || [];
    updateModelSelect(document.getElementById('tier-' + tier + '-select'), models, query);
}
