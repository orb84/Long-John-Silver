
/**
 * Return a stable dedupe key for setup requirement rows.
 * Shared runtime dependencies can be inherited by multiple categories.
 */
function setupRequirementKey(item) {
    var rawId = String((item && item.id) || '');
    if (rawId.indexOf('.runtime_') !== -1) {
        rawId = 'runtime_' + rawId.split('.runtime_')[1];
    }
    var label = String((item && item.label) || '').trim().toLowerCase();
    var message = String((item && (item.message || item.description)) || '').trim().toLowerCase();
    return rawId || (label + ':' + message);
}

/**
 * De-duplicate setup requirement rows before rendering counts or toast text.
 */
function uniqueSetupItems(items) {
    var seen = {};
    return (items || []).filter(function(item) {
        var key = setupRequirementKey(item);
        if (seen[key]) return false;
        seen[key] = true;
        return true;
    });
}
/**
 * LJS Setup Wizard — handles multi-step first-time configuration.
 *
 * Steps: password -> paths -> LLM -> channels -> automation -> complete.
 * Uses shared components: modelCatalog.js (fetchModels, updateModelSelect),
 * core/toastManager.js (toast), api/actionClient.js (APIClient).
 */

var currentStep = 1;
var TOTAL_STEPS = 5;
var setupProvider = '';
var setupModels = [];

var PRESET_INFO = {
    openrouter: 'Multi-provider gateway. Supports GPT-4, Claude, Llama, and 200+ more models. Requires an API key.',
    nvidia_nim: 'GPU-accelerated inference microservices. Fast local models. Requires NVIDIA API key.',
    ollama_cloud: 'Ollama managed cloud. Run open-source models without local hardware. Requires API key.',
    ollama_local: 'Run models locally with Ollama. No API key needed. Requires Ollama installed.',
    lm_studio: 'Run models locally with LM Studio. No API key needed. Requires LM Studio running.',
};

var PRESET_BASES = {
    openrouter: 'https://openrouter.ai/api/v1',
    nvidia_nim: 'https://integrate.api.nvidia.com/v1',
    ollama_cloud: 'https://api.ollama.ai/v1',
    ollama_local: 'http://localhost:11434/v1',
    lm_studio: 'http://localhost:1234/v1',
};

var PRESET_NEEDS_KEY = {
    openrouter: true,
    nvidia_nim: true,
    ollama_cloud: true,
    ollama_local: false,
    lm_studio: false,
};

var selectedChannels = new Set(['web']);

/**
 * Public UI helper for the goStep workflow.
 *
 * Keep inputs DOM-safe, delegate server mutations through API or Action clients,
 * and preserve the return/side-effect contract because templates may call this
 * function directly from event handlers.
 */
function goStep(step) {
    document.getElementById('step-' + currentStep).classList.remove('active');
    document.getElementById('step-' + step).classList.add('active');
    currentStep = step;
    updateStepper();
}

/**
 * Public UI helper for the updateStepper workflow.
 *
 * Keep inputs DOM-safe, delegate server mutations through API or Action clients,
 * and preserve the return/side-effect contract because templates may call this
 * function directly from event handlers.
 */
function updateStepper() {
    for (var i = 1; i <= TOTAL_STEPS; i++) {
        var dot = document.getElementById('dot-' + i);
        var line = document.getElementById('line-' + i);
        dot.classList.remove('active', 'completed');
        if (i < currentStep) {
            dot.classList.add('completed');
            dot.textContent = '\u2713';
            if (line) line.classList.add('completed');
        } else if (i === currentStep) {
            dot.classList.add('active');
            dot.textContent = i;
            if (line) line.classList.remove('completed');
        } else {
            dot.textContent = i;
            if (line) line.classList.remove('completed');
        }
    }
}

/**
 * Public UI helper for the installBridge workflow.
 *
 * Keep inputs DOM-safe, delegate server mutations through API or Action clients,
 * and preserve the return/side-effect contract because templates may call this
 * function directly from event handlers.
 */
async function installBridge(bridgeId) {
    try {
        var r = await fetch('/api/comms/bridges/' + bridgeId + '/install', { method: 'POST' });
        if (r.ok) {
            var result = await r.json();
            if (result.status === 'installed') {
                toast.show(bridgeId + ' package installed');
            }
        }
    } catch (e) {
        console.warn('Bridge install for ' + bridgeId + ' failed:', e);
    }
}

/**
 * Collect first-run Soulseek credential fields into the managed slskd payload.
 */
function collectSetupSoulseekPayload(forceEnabled) {
    var enabledEl = document.getElementById('setup-soulseek-enabled');
    return {
        enabled: !!forceEnabled || (enabledEl ? enabledEl.checked : false),
        managed: true,
        auto_install: true,
        host: ((document.getElementById('setup-soulseek-host') || {}).value || 'http://127.0.0.1:5030').trim(),
        api_key: ((document.getElementById('setup-soulseek-api-key') || {}).value || '').trim(),
        soulseek_username: ((document.getElementById('setup-soulseek-username') || {}).value || '').trim(),
        soulseek_password: ((document.getElementById('setup-soulseek-password') || {}).value || '').trim(),
        share_mode: ((document.getElementById('setup-soulseek-share-mode') || {}).value || 'full_library'),
        parallel_search_enabled: true,
        download_preference: 'torrent_first',
        search_enabled_categories: ['music', 'audiobooks', 'ebooks', 'tv', 'movie', 'general']
    };
}

/**
 * Render immediate Soulseek login-check feedback in setup.
 */
function renderSetupSoulseekStatus(state) {
    var el = document.getElementById('setup-soulseek-login-status');
    if (!el || !state) return;
    var status = state.status || state.account_status || 'not_checked';
    var ready = !!(state.ready || status === 'ready');
    var msg = state.error || state.account_status_message || (ready ? 'Soulseek login verified.' : 'Soulseek login not checked yet.');
    var checked = state.account_checked_at ? ' Last checked: ' + String(state.account_checked_at).replace('T', ' ').slice(0, 19) + '.' : '';
    el.textContent = ready ? 'Ready: Soulseek login verified and search is available.' + checked : status + ': ' + msg + checked;
    el.style.color = ready ? 'var(--accent-teal)' : (status === 'checking' || status === 'not_checked' ? 'var(--text-dim)' : 'var(--danger, #f87171)');
}

/**
 * Install/start slskd and verify the entered Soulseek credentials from setup.
 */
async function checkSetupSoulseekLogin() {
    var enabledEl = document.getElementById('setup-soulseek-enabled');
    if (enabledEl) enabledEl.checked = true;
    var payload = collectSetupSoulseekPayload(true);
    var btn = document.getElementById('setup-soulseek-check-login');
    if (!payload.soulseek_username || !payload.soulseek_password) {
        var msg = 'Enter a Soulseek username and password, then press Check Soulseek Login again.';
        renderSetupSoulseekStatus({ status: 'needs_credentials', error: msg });
        toast.show(msg, 'err');
        return;
    }
    if (btn) btn.disabled = true;
    renderSetupSoulseekStatus({ status: 'checking', account_status_message: 'Installing/starting slskd and checking Soulseek login...' });
    try {
        var result = await APIClient.post('/api/soulseek/check-login', { soulseek: payload, timeout_seconds: 45 });
        renderSetupSoulseekStatus(result);
        if (result.ready || result.status === 'ready') toast.show('Soulseek login verified. slskd is running and search is available.');
        else if (result.status === 'auth_failed') toast.show(result.error || 'Soulseek rejected these credentials. Change them and check again.', 'err');
        else if (result.error) toast.show(result.error, 'err');
        else toast.show(result.account_status_message || 'Soulseek login is still being checked. Try again in a few seconds.');
    } catch (e) {
        renderSetupSoulseekStatus({ status: 'error', error: e.message || 'Soulseek login check failed.' });
        toast.show(e.message || 'Soulseek login check failed.', 'err');
    } finally {
        if (btn) btn.disabled = false;
    }
}

/**
 * Public UI helper for the savePassword workflow.
 *
 * Keep inputs DOM-safe, delegate server mutations through API or Action clients,
 * and preserve the return/side-effect contract because templates may call this
 * function directly from event handlers.
 */
function savePassword() {
    var password = document.getElementById('setup-password').value;
    var confirm = document.getElementById('setup-password-confirm').value;

    if (password && password !== confirm) {
        toast.show('Passwords do not match', 'err');
        return;
    }

    APIClient.post('/api/setup/password', { password: password, confirm: confirm }).then(function() {
        toast.show('Password saved');
        goStep(2);
    }).catch(function(e) {
        toast.show(e.message, 'err');
    });
}

/**
 * Public UI helper for the skipPassword workflow.
 *
 * Keep inputs DOM-safe, delegate server mutations through API or Action clients,
 * and preserve the return/side-effect contract because templates may call this
 * function directly from event handlers.
 */
function skipPassword() {
    APIClient.post('/api/setup/password', { password: '', confirm: '' }).then(function() {
        toast.show('No password set — open access');
        goStep(2);
    }).catch(function(e) {
        toast.show(e.message, 'err');
    });
}

/**
 * Public UI helper for the savePaths workflow.
 *
 * Keep inputs DOM-safe, delegate server mutations through API or Action clients,
 * and preserve the return/side-effect contract because templates may call this
 * function directly from event handlers.
 */
function savePaths() {
    var data = {
        download_dir: document.getElementById('setup-download-dir').value.trim() || './downloads',
        library_root: document.getElementById('setup-library-root').value.trim() || './library',
        library_paths: collectSetupCategoryPaths(),
    };
    APIClient.post('/api/setup/paths', data).then(function() {
        toast.show('Paths saved');
        goStep(3);
        onSetupProviderChange(document.getElementById('setup-provider'));
    }).catch(function(e) {
        toast.show(e.message, 'err');
    });
}

/**
 * Public UI helper for the collectSetupCategoryPaths workflow.
 *
 * Keep inputs DOM-safe, delegate server mutations through API or Action clients,
 * and preserve the return/side-effect contract because templates may call this
 * function directly from event handlers.
 */
function collectSetupCategoryPaths() {
    var paths = {};
    document.querySelectorAll('.setup-category-path').forEach(function(input) {
        var value = input.value.trim();
        if (value) paths[input.dataset.categoryId] = value;
    });
    return paths;
}

/**
 * Public UI helper for the onSetupProviderChange workflow.
 *
 * Keep inputs DOM-safe, delegate server mutations through API or Action clients,
 * and preserve the return/side-effect contract because templates may call this
 * function directly from event handlers.
 */
async function onSetupProviderChange(selectEl) {
    var providerId = selectEl.value;
    setupProvider = providerId;

    var infoEl = document.getElementById('setup-provider-info');
    if (infoEl) {
        if (PRESET_INFO[providerId]) {
            infoEl.textContent = PRESET_INFO[providerId];
            infoEl.style.display = 'block';
        } else {
            infoEl.style.display = 'none';
        }
    }

    var apiBaseEl = document.getElementById('setup-api-base');
    if (PRESET_BASES[providerId]) {
        apiBaseEl.value = PRESET_BASES[providerId];
    }

    var keyGroup = document.getElementById('setup-api-key-group');
    if (keyGroup) {
        keyGroup.style.display = PRESET_NEEDS_KEY[providerId] ? '' : 'none';
    }

    // Dynamic key instructions link updates
    var providerLinks = {
        openrouter: { name: 'OpenRouter Keys', url: 'https://openrouter.ai/keys' },
        openai: { name: 'OpenAI API Keys', url: 'https://platform.openai.com/api-keys' },
        nvidia_nim: { name: 'NVIDIA NIM Catalog', url: 'https://build.nvidia.com/' },
        ollama_cloud: { name: 'Ollama Cloud Console', url: 'https://ollama.com' }
    };
    var linkHelp = document.getElementById('key-help-link');
    var signupLink = document.getElementById('provider-signup-link');
    if (linkHelp && signupLink) {
        if (providerLinks[providerId]) {
            signupLink.textContent = providerLinks[providerId].name + ' \u2197';
            signupLink.href = providerLinks[providerId].url;
            linkHelp.style.display = 'block';
        } else {
            linkHelp.style.display = 'none';
        }
    }

    var modelSelect = document.getElementById('setup-model-select');
    var searchInput = document.getElementById('setup-model-search');
    if (searchInput) searchInput.value = '';
    if (modelSelect) {
        modelSelect.innerHTML = '<option value="">Loading models...</option>';
    }

    setupModels = await fetchModels(providerId);
    if (modelSelect) {
        updateModelSelect(modelSelect, setupModels, '');
    }
}

/**
 * Public UI helper for the onSetupModelSelect workflow.
 *
 * Keep inputs DOM-safe, delegate server mutations through API or Action clients,
 * and preserve the return/side-effect contract because templates may call this
 * function directly from event handlers.
 */
function onSetupModelSelect(selectEl) {
    var modelId = selectEl.value;
    if (modelId) {
        document.getElementById('setup-model').value = modelId;
    }
}

/**
 * Public UI helper for the filterSetupModels workflow.
 *
 * Keep inputs DOM-safe, delegate server mutations through API or Action clients,
 * and preserve the return/side-effect contract because templates may call this
 * function directly from event handlers.
 */
function filterSetupModels(query) {
    var modelSelect = document.getElementById('setup-model-select');
    var filtered = query
        ? setupModels.filter(function(m) { return m.id.toLowerCase().includes(query.toLowerCase()) || m.name.toLowerCase().includes(query.toLowerCase()); })
        : setupModels;
    updateModelSelect(modelSelect, setupModels, query);
}

/**
 * Public UI helper for the saveLLM workflow.
 *
 * Keep inputs DOM-safe, delegate server mutations through API or Action clients,
 * and preserve the return/side-effect contract because templates may call this
 * function directly from event handlers.
 */
function saveLLM() {
    var provider = document.getElementById('setup-provider').value;
    var model = document.getElementById('setup-model').value.trim();
    var apiBase = document.getElementById('setup-api-base').value.trim();
    var apiKey = document.getElementById('setup-api-key').value.trim();

    if (!model) {
        toast.show('Please select or enter a model ID', 'err');
        return;
    }

    var data = { provider: provider, model: model, api_base: apiBase };
    if (apiKey) data.api_key = apiKey;

    var webProvider = document.getElementById('setup-web-search-provider');
    var webKey = document.getElementById('setup-web-search-key');
    var webBase = document.getElementById('setup-web-search-base');
    var webFallback = document.getElementById('setup-web-search-fallback');
    if (webProvider) {
        data.web_search = {
            enabled: true,
            provider: webProvider.value,
            api_key: webKey ? webKey.value.trim() : '',
            api_base: webBase ? webBase.value.trim() : '',
            max_results: 5,
            allow_duckduckgo_fallback: webFallback ? webFallback.checked : false,
        };
    }

    APIClient.post('/api/setup/llm', data).then(function() {
        return APIClient.post('/api/setup/embeddings', collectSetupEmbeddings());
    }).then(function() {
        return saveSetupMediaServices();
    }).then(function() {
        toast.show('AI brain connected');
        goStep(4);
    }).catch(function(e) {
        toast.show(e.message, 'err');
    });
}



/**
 * Save shared Media service credentials into the private media category config.
 *
 * The setup wizard collects TMDB/Trakt beside the LLM controls because users
 * think of them as "make the assistant smarter" keys, but the values belong to
 * the abstract media category, not to global settings.
 */
function saveSetupMediaServices() {
    var tmdbKey = document.getElementById('setup-tmdb-key');
    var traktId = document.getElementById('setup-trakt-id');
    var mediaServices = {};
    if (tmdbKey && tmdbKey.value.trim()) mediaServices.tmdb = { enabled: true, api_key: tmdbKey.value.trim() };
    if (traktId && traktId.value.trim()) mediaServices.trakt = { enabled: true, client_id: traktId.value.trim() };
    if (!Object.keys(mediaServices).length) {
        return Promise.resolve({ status: 'skipped' });
    }
    return APIClient.post('/api/setup/category-config', {
        category_settings: { media: { services: mediaServices } }
    });
}

/**
 * Save first-run shared Media search preferences into category config.
 *
 * These defaults are inherited by TV and Movies through the media category
 * definition; global language remains the chat/UI language, not a torrent
 * search preference.
 */
function saveSetupMediaPreferences() {
    var lang = document.getElementById('setup-language');
    var res = document.getElementById('setup-resolution');
    var mode = document.getElementById('setup-size-mode');
    var profile = {};
    if (lang) profile.language = lang.value;
    if (res) profile.preferred_resolution = res.value;
    if (mode) profile.size_limit_mode = mode.value;
    if (!Object.keys(profile).length) {
        return Promise.resolve({ status: 'skipped' });
    }
    return APIClient.post('/api/setup/category-config', {
        category_settings: { media: { download_profile: profile } }
    });
}


/**
 * Save first-run Music/Audiobook/Ebook format preferences into category config.
 */
function saveSetupBookAudioPreferences() {
    var musicLossless = document.getElementById('setup-music-lossless-format');
    var musicAutoConvert = document.getElementById('setup-music-auto-convert');
    var audiobookFormat = document.getElementById('setup-audiobook-format');
    var audiobookAutoConvert = document.getElementById('setup-audiobook-auto-convert');
    var ebookFormat = document.getElementById('setup-ebook-format');
    var payload = { category_settings: {} };
    if (musicLossless || musicAutoConvert) {
        payload.category_settings.music = { download_profile: {} };
        if (musicLossless) payload.category_settings.music.download_profile.preferred_lossless_format = musicLossless.value;
        if (musicAutoConvert) payload.category_settings.music.download_profile.auto_convert_lossless_to_preferred = !!musicAutoConvert.checked;
    }
    if (audiobookFormat || audiobookAutoConvert) {
        payload.category_settings.audiobooks = { download_profile: {} };
        if (audiobookFormat) payload.category_settings.audiobooks.download_profile.preferred_audio_format = audiobookFormat.value;
        if (audiobookAutoConvert) payload.category_settings.audiobooks.download_profile.auto_convert_lossless_to_preferred = !!audiobookAutoConvert.checked;
    }
    if (ebookFormat) {
        payload.category_settings.ebooks = { download_profile: { preferred_ebook_format: ebookFormat.value } };
    }
    if (!Object.keys(payload.category_settings).length) {
        return Promise.resolve({ status: 'skipped' });
    }
    return APIClient.post('/api/setup/category-config', payload);
}

/**
 * Collect optional semantic-memory embedding settings from setup.
 */
function collectSetupEmbeddings() {
    var enabled = document.getElementById('setup-embeddings-enabled');
    var provider = document.getElementById('setup-embeddings-provider');
    var model = document.getElementById('setup-embeddings-model');
    var autoDownload = document.getElementById('setup-embeddings-auto-download');
    return {
        enabled: enabled ? enabled.checked : true,
        provider: provider ? provider.value : 'builtin',
        builtin_model: model && model.value.trim() ? model.value.trim() : 'sentence-transformers/all-MiniLM-L6-v2',
        dimension: 384,
        auto_download: autoDownload ? autoDownload.checked : true,
        warmup_on_startup: true,
        max_model_size_mb: 150,
    };
}

/**
 * Public UI helper for the toggleChannel workflow.
 *
 * Keep inputs DOM-safe, delegate server mutations through API or Action clients,
 * and preserve the return/side-effect contract because templates may call this
 * function directly from event handlers.
 */
function toggleChannel(cardEl, channel) {
    cardEl.classList.toggle('selected');
    if (selectedChannels.has(channel)) {
        selectedChannels.delete(channel);
    } else {
        selectedChannels.add(channel);
    }

    document.getElementById('config-discord').classList.toggle('visible', selectedChannels.has('discord'));
    document.getElementById('config-telegram').classList.toggle('visible', selectedChannels.has('telegram'));
    document.getElementById('config-whatsapp').classList.toggle('visible', selectedChannels.has('whatsapp'));
}

/**
 * Public UI helper for the updateAutomationHighlight workflow.
 *
 * Keep inputs DOM-safe, delegate server mutations through API or Action clients,
 * and preserve the return/side-effect contract because templates may call this
 * function directly from event handlers.
 */
function updateAutomationHighlight() {
    var suggestCard = document.getElementById('auto-option-suggest');
    var autoCard = document.getElementById('auto-option-auto');
    var isSuggest = document.querySelector('input[name="automation"]:checked').value === 'suggest';
    if (suggestCard) {
        suggestCard.style.borderColor = isSuggest ? 'var(--teal)' : 'var(--border)';
        suggestCard.style.background = isSuggest ? 'rgba(46,196,182,0.08)' : 'var(--input)';
    }
    if (autoCard) {
        autoCard.style.borderColor = isSuggest ? 'var(--border)' : 'var(--gold)';
        autoCard.style.background = isSuggest ? 'var(--input)' : 'rgba(212,162,78,0.08)';
    }
}

/**
 * Public UI helper for the updateSharingHighlight workflow.
 *
 * Keeps first-run sharing choices visually clear without saving until finish.
 */
function updateSharingHighlight() {
    var privateCard = document.getElementById('sharing-option-private');
    var seedCard = document.getElementById('sharing-option-seed');
    var selected = document.querySelector('input[name="sharing-mode"]:checked');
    var isSeed = selected && selected.value === 'seed_in_place';
    if (privateCard) {
        privateCard.style.borderColor = isSeed ? 'var(--border)' : 'var(--teal)';
        privateCard.style.background = isSeed ? 'var(--input)' : 'rgba(46,196,182,0.08)';
    }
    if (seedCard) {
        seedCard.style.borderColor = isSeed ? 'var(--gold)' : 'var(--border)';
        seedCard.style.background = isSeed ? 'rgba(212,162,78,0.08)' : 'var(--input)';
    }
}

/**
 * Public UI helper for the finishSetup workflow.
 *
 * Keep inputs DOM-safe, delegate server mutations through API or Action clients,
 * and preserve the return/side-effect contract because templates may call this
 * function directly from event handlers.
 */
async function finishSetup() {
    var finishBtn = document.getElementById('setup-finish-btn');
    var finishStatus = document.getElementById('setup-finish-status');
    var originalBtnHtml = finishBtn ? finishBtn.innerHTML : '';
    if (finishBtn) {
        finishBtn.classList.add('is-loading');
        finishBtn.disabled = true;
        finishBtn.innerHTML = '<i class="fa-solid fa-spinner"></i> Setting sail…';
    }
    if (finishStatus) {
        finishStatus.classList.add('is-visible');
        finishStatus.textContent = 'Saving setup and starting background services…';
    }
    try {
    var bridgeInstalls = [];
    if (selectedChannels.has('discord')) {
        bridgeInstalls.push(installBridge('discord'));
    }
    if (selectedChannels.has('telegram')) {
        bridgeInstalls.push(installBridge('telegram'));
    }

    await Promise.allSettled(bridgeInstalls);

    var installPlaywright = document.getElementById('setup-playwright');
    if (installPlaywright && installPlaywright.checked) {
        toast.show('Installing Playwright browser engine (may take a minute)...');
        try {
            var r = await fetch('/api/browser/install', { method: 'POST' });
            var result = await r.json();
            if (result.status === 'installed') {
                toast.show('Playwright installed successfully');
            } else {
                toast.show('Playwright install failed — you can install manually later', 'err');
            }
        } catch (e) {
            toast.show('Playwright install failed — you can install manually later', 'err');
        }
    }

    var installJackett = document.getElementById('setup-jackett');
    if (installJackett && installJackett.checked) {
        toast.show('Installing Jackett torrent search engine (may take a minute)...');
        try {
            var r = await fetch('/api/jackett/install', { method: 'POST' });
            var result = await r.json();
            if (result.status === 'installed') {
                toast.show('Jackett installed — configured open/public indexers via ' + result.url);
            } else {
                toast.show('Jackett install failed — you can set it up manually later', 'err');
            }
        } catch (e) {
            toast.show('Jackett install failed — you can set it up manually later', 'err');
        }
    }


    var directFallback = document.getElementById('setup-direct-scraper-fallback');
    var soulseekEnabled = document.getElementById('setup-soulseek-enabled');
    var soulseekPayload = collectSetupSoulseekPayload(soulseekEnabled ? soulseekEnabled.checked : false);
    try {
        const soulseekResponse = await fetch('/api/settings/search', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                direct_scraper_fallback: directFallback ? directFallback.checked : false,
                soulseek: soulseekPayload
            })
        });
        const soulseekResult = await soulseekResponse.json().catch(() => ({}));
        if (soulseekPayload.enabled) {
            const state = soulseekResult && soulseekResult.soulseek;
            renderSetupSoulseekStatus(state);
            if (state && (state.status === 'ready' || state.ready)) toast.show('Soulseek/slskd installed, running, and logged in.');
            else if (state && state.status === 'checking') toast.show(state.account_status_message || 'Soulseek/slskd is running; login validation is still pending.');
            else if (state && (state.error || state.account_status_message)) toast.show('Soulseek/slskd setup needs attention: ' + (state.error || state.account_status_message), 'err');
        }
    } catch (e) {
        console.warn('Failed to save search/Soulseek preference', e);
    }

    var automationMode = document.querySelector('input[name="automation"]:checked');
    var autoDiscover = document.getElementById('setup-auto-discover');
    try {
        await APIClient.post('/api/settings/auto_download', {
            auto_download: automationMode ? automationMode.value === 'auto' : false,
            auto_discover: autoDiscover ? autoDiscover.checked : true,
        });
    } catch (e) { /* non-critical */ }

    try {
        var sharingMode = document.querySelector('input[name="sharing-mode"]:checked');
        var sharingEnabled = sharingMode && sharingMode.value === 'seed_in_place';
        await APIClient.post('/api/setup/sharing', {
            enabled: sharingEnabled,
            mode: sharingEnabled ? 'seed_in_place' : 'disabled',
            library_upload_speed_kbps: parseInt((document.getElementById('setup-sharing-upload') || {}).value || '0', 10) || 0,
            active_seed_slots: parseInt((document.getElementById('setup-sharing-slots') || {}).value || '2', 10) || 2,
            seed_ratio_target: parseFloat((document.getElementById('setup-sharing-ratio') || {}).value || '2.0') || 2.0,
            seed_duration_hours: 168
        });
    } catch (e) { /* non-critical */ }

    try {
        var autoStart = document.getElementById('setup-auto-start');
        await APIClient.post('/api/setup/startup', { enabled: autoStart ? autoStart.checked : false });
    } catch (e) { /* non-critical */ }

    try {
        await saveSetupMediaPreferences();
        await saveSetupBookAudioPreferences();
    } catch (e) {
        console.warn('Failed to save category preferences during setup', e);
    }

    var data = {};
    if (selectedChannels.has('discord')) {
        data.discord_token = (document.getElementById('setup-discord-token').value || '').trim() || null;
        data.discord_channel_id = (document.getElementById('setup-discord-channel').value || '').trim() || null;
    } else {
        data.discord_token = null;
        data.discord_channel_id = null;
    }
    if (selectedChannels.has('telegram')) {
        data.telegram_token = (document.getElementById('setup-telegram-token').value || '').trim() || null;
    } else {
        data.telegram_token = null;
    }
    if (selectedChannels.has('whatsapp')) {
        data.whatsapp_token = (document.getElementById('setup-whatsapp-token').value || '').trim() || null;
        data.whatsapp_phone_number_id = (document.getElementById('setup-whatsapp-phone-id').value || '').trim() || null;
        data.whatsapp_verify_token = (document.getElementById('setup-whatsapp-verify-token').value || '').trim() || null;
    } else {
        data.whatsapp_token = null;
        data.whatsapp_phone_number_id = null;
        data.whatsapp_verify_token = null;
    }

    await APIClient.post('/api/setup/channels', data);

    var result = await APIClient.post('/api/setup/complete', {});
    if (!result || result.status === 'blocked' || result.setup_complete === false) {
        var missing = uniqueSetupItems((result && result.missing_required) ? result.missing_required : []);
        var message = missing.length
            ? 'Setup is missing required items: ' + missing.map(function(item) { return item.label || item.id; }).join(', ')
            : 'Setup could not be completed. Please review the highlighted requirements.';
        toast.show(message, 'err');
        loadSetupRequirements();
        return;
    }

    document.getElementById('step-' + currentStep).classList.remove('active');
    document.getElementById('step-success').classList.add('active');

    for (var i = 1; i <= TOTAL_STEPS; i++) {
        var dot = document.getElementById('dot-' + i);
        dot.classList.remove('active');
        dot.classList.add('completed');
        dot.textContent = '\u2713';
        var line = document.getElementById('line-' + i);
        if (line) line.classList.add('completed');
    }

    toast.show('Setup complete!');
    } catch (e) {
        console.error('Setup completion failed:', e);
        toast.show('Setup completion failed. Please review the console or logs.', 'err');
    } finally {
        if (finishBtn) {
            finishBtn.classList.remove('is-loading');
            finishBtn.disabled = false;
            finishBtn.innerHTML = originalBtnHtml || '<i class="fa-solid fa-anchor"></i> Set Sail';
        }
        if (finishStatus) {
            finishStatus.classList.remove('is-visible');
        }
    }
}

/**
 * Public UI helper for the loadSetupRequirements workflow.
 *
 * Keep inputs DOM-safe, delegate server mutations through API or Action clients,
 * and preserve the return/side-effect contract because templates may call this
 * function directly from event handlers.
 */
async function loadSetupRequirements() {
    var summary = document.getElementById('setup-requirements-summary');
    if (!summary) return;
    try {
        var response = await fetch('/api/setup/requirements');
        if (!response.ok) return;
        var data = await response.json();
        var requiredItems = [];
        var configuredItems = [];
        (data.categories || []).forEach(function(category) {
            (category.requirements || []).forEach(function(req) {
                if (!req.required) return;
                requiredItems.push(req);
                if (req.configured) configuredItems.push(req);
            });
        });
        var required = uniqueSetupItems(requiredItems).length;
        var configured = uniqueSetupItems(configuredItems).length;
        summary.style.display = 'block';
        summary.innerHTML = '<strong>Category-first setup status:</strong> ' + configured + '/' + required + ' required items configured. ' +
            'Use these requirements to understand why folders, Jackett, TMDB, TVMaze, and web search matter.';
    } catch (e) {
        console.warn('Failed to load setup requirements:', e);
    }
}


document.addEventListener('DOMContentLoaded', function() {
    var webCard = document.querySelector('[data-channel="web"]');
    if (webCard) webCard.classList.add('selected');
    updateAutomationHighlight();
    updateSharingHighlight();
    loadSetupRequirements();
    var providerSelect = document.getElementById('setup-provider');
    if (providerSelect) {
        onSetupProviderChange(providerSelect);
    }
});
