/**
 * Suggestions panel component for LJS.
 *
 * Renders category-owned suggestions as macro groups first, with optional
 * episode/item-level controls hidden behind expanders.  This keeps hundreds of
 * granular suggestions useful without turning the UI into a wall of buttons.
 */

class SuggestionManager extends Component {
    /**
     * Construct and initialize the SuggestionManager instance.
     *
     * Keep DOM lookups local, prefer ActionClient/APIClient for server calls,
     * and preserve event names/data attributes so other components can extend
     * this behavior without reaching into private state.
     */
    constructor() {
        super('crows-nest');
        this.list = document.getElementById('suggestion-list');
        this.badge = document.getElementById('suggestion-count');
        this.typeLabels = {
            missing_episode: 'Missing episode',
            download_next: 'Download next',
            download_all_missing: 'Download all missing',
            download_remaining_next: 'Catch up',
            quality_upgrade: 'Quality upgrade',
            related_media: 'Related item',
            new_season: 'New season'
        };

        shipEvents.subscribe('system', (e) => {
            if (e.subtype === 'suggestions_updated') this.load();
        });

        this.load();
    }

    /**
     * Load data required by SuggestionManager.
     *
     * Keep DOM lookups local, prefer ActionClient/APIClient for server calls,
     * and preserve event names/data attributes so other components can extend
     * this behavior without reaching into private state.
     */
    async load() {
        if (!this.list) return;
        try {
            const data = await APIClient.get('/api/suggestions');
            const sugs = data.suggestions || [];
            this._renderList(sugs, Boolean(data.compiling));
            if (data.compiling) {
                clearTimeout(this._pollTimer);
                this._pollTimer = setTimeout(() => this.load(), 2500);
            }
        } catch (e) {
            this.list.innerHTML = '<p class="empty-msg">Failed to load suggestions</p>';
        }
    }

    _renderList(sugs, compiling = false) {
        this.list.innerHTML = '';
        if (!sugs.length) {
            if (this.badge) this.badge.textContent = compiling ? 'Scanning…' : '0 groups';
            this.list.appendChild(DOM.el('p', { className: 'empty-msg' }, [
                compiling ? 'Scanning the horizon for suggestions…' : 'No pending suggestions.'
            ]));
            return;
        }

        const groups = this._groupSuggestions(sugs);
        if (this.badge) this.badge.textContent = `${groups.length} groups · ${sugs.length} actions`;

        const categoryBuckets = new Map();
        groups.forEach(group => {
            const key = group.category_id || 'library';
            if (!categoryBuckets.has(key)) categoryBuckets.set(key, []);
            categoryBuckets.get(key).push(group);
        });

        categoryBuckets.forEach((categoryGroups, categoryId) => {
            const section = DOM.el('section', { className: 'suggestion-category-section' }, [
                DOM.el('div', { className: 'suggestion-category-heading' }, [
                    DOM.el('i', { className: `fa-solid ${categoryId === 'movie' ? 'fa-film' : categoryId === 'tv' ? 'fa-tv' : 'fa-layer-group'}` }),
                    DOM.el('h3', {}, [this._categoryLabel(categoryId)]),
                    DOM.el('span', { className: 'pill' }, [`${categoryGroups.length} items`])
                ])
            ]);
            categoryGroups.sort((a, b) => a.item_name.localeCompare(b.item_name)).forEach(group => {
                section.appendChild(this._renderGroup(group));
            });
            this.list.appendChild(section);
        });
    }

    _groupSuggestions(sugs) {
        const byItem = new Map();
        sugs.forEach(s => {
            const key = `${s.category_id || 'library'}::${s.item_id || s.item_name || 'unknown'}`;
            if (!byItem.has(key)) byItem.set(key, { category_id: s.category_id, item_id: s.item_id, item_name: s.item_name || s.item_id, macro: [], episodes: [], upgrades: [], related: [], all: [], explanations: [] });
            const group = byItem.get(key);
            group.all.push(s);
            if (s.explanation) group.explanations.push(s.explanation);
            if (['download_next', 'download_all_missing', 'download_remaining_next', 'new_season'].includes(s.action_type)) group.macro.push(s);
            else if (s.action_type === 'missing_episode') group.episodes.push(s);
            else if (s.action_type === 'quality_upgrade') group.upgrades.push(s);
            else group.related.push(s);
        });
        return Array.from(byItem.values());
    }

    _renderGroup(group) {
        const missingCount = group.episodes.length;
        const upgradeCount = group.upgrades.length;
        const relatedCount = group.related.length;
        const macro = group.macro.length ? group.macro : this._synthesizeMacroActions(group);
        const leadExplanation = this._leadExplanation(group);

        const card = DOM.el('article', { className: 'suggestion-card' }, [
            DOM.el('div', { className: 'suggestion-card-header' }, [
                DOM.el('div', {}, [
                    DOM.el('h4', {}, [group.item_name || 'Library item']),
                    DOM.el('p', { className: 'muted' }, [
                        [missingCount ? `${missingCount} missing` : '', upgradeCount ? `${upgradeCount} upgrades` : '', relatedCount ? `${relatedCount} related` : '']
                            .filter(Boolean).join(' · ') || 'Suggested actions'
                    ])
                ]),
                DOM.el('span', { className: 'pill' }, [this._categoryLabel(group.category_id)])
            ]),
            leadExplanation ? DOM.el('div', { className: 'suggestion-why' }, [
                DOM.el('i', { className: 'fa-solid fa-compass' }),
                DOM.el('span', {}, [leadExplanation])
            ]) : null,
            DOM.el('div', { className: 'suggestion-macro-actions' }, macro.map(s => this._renderMacroAction(s))),
        ].filter(Boolean));

        if (group.episodes.length || group.upgrades.length || group.related.length) {
            card.appendChild(DOM.el('details', { className: 'suggestion-micro-details' }, [
                DOM.el('summary', {}, ['Fine tune episodes, upgrades, languages and related items']),
                this._renderMicroList('Episodes', group.episodes),
                this._renderMicroList('Upgrades', group.upgrades),
                this._renderMicroList('Related', group.related)
            ]));
        }
        return card;
    }

    _synthesizeMacroActions(group) {
        const actions = [];
        if (group.episodes.length) {
            actions.push({ ...group.episodes[0], title: `Download next missing episode`, description: group.episodes[0].title });
            if (group.episodes.length > 1) {
                actions.push({
                    id: `batch-${group.category_id}-${group.item_id}`,
                    category_id: group.category_id,
                    item_id: group.item_id,
                    item_name: group.item_name,
                    action_type: 'download_all_missing',
                    title: `Download all ${group.episodes.length} missing episodes`,
                    description: 'Queues every missing aired episode shown in this group.',
                    endpoint: `/api/suggestions/approve-all/${encodeURIComponent(group.item_id || group.item_name)}`,
                    method: 'POST',
                    synthetic: true
                });
            }
        }
        return actions;
    }

    _renderMacroAction(s) {
        return DOM.el('div', { className: 'suggestion-action-card' }, [
            DOM.el('div', {}, [
                DOM.el('strong', {}, [s.title || this.typeLabels[s.action_type] || s.action_type]),
                DOM.el('p', { className: 'muted' }, [s.description || '']),
                this._renderEvidence(s)
            ].filter(Boolean)),
            DOM.el('div', { className: 'suggestion-actions' }, [
                DOM.btn('Approve', 'btn-gold btn-sm', () => this.approve(s)),
                s.synthetic ? null : DOM.btn('Dismiss', 'btn-danger btn-sm', () => this.deny(s.id))
            ].filter(Boolean))
        ]);
    }

    _renderMicroList(title, items) {
        if (!items.length) return DOM.el('div');
        return DOM.el('div', { className: 'suggestion-micro-list' }, [
            DOM.el('h5', {}, [title]),
            ...items.slice(0, 80).map(s => DOM.el('div', { className: 'suggestion-micro-row', id: `suggestion-${s.id}` }, [
                DOM.el('span', {}, [
                    DOM.el('strong', {}, [s.title || this.typeLabels[s.action_type] || s.action_type]),
                    s.explanation ? DOM.el('small', { className: 'suggestion-row-reason' }, [s.explanation]) : null,
                    this._renderEvidence(s)
                ].filter(Boolean)),
                DOM.el('div', { className: 'suggestion-actions' }, [
                    DOM.btn('Approve', 'btn-secondary btn-sm', () => this.approve(s)),
                    DOM.btn('Dismiss', 'btn-danger btn-sm', () => this.deny(s.id))
                ])
            ])),
            items.length > 80 ? DOM.el('p', { className: 'muted' }, [`Showing first 80 of ${items.length}. Use macro actions above for the rest.`]) : null
        ].filter(Boolean));
    }


    _leadExplanation(group) {
        const macro = group.macro[0];
        const fromMacro = macro && (macro.explanation || macro.description);
        const fromAny = group.explanations && group.explanations[0];
        const text = fromMacro || fromAny || '';
        return text.length > 260 ? `${text.slice(0, 257).trim()}…` : text;
    }

    _renderEvidence(s) {
        const evidence = s.evidence || {};
        const pills = [];
        if (s.confidence) pills.push(`confidence: ${s.confidence}`);
        if (evidence.provider_episode_count !== undefined) pills.push(`${evidence.provider_episode_count} aired`);
        if (evidence.downloaded_episode_count !== undefined) pills.push(`${evidence.downloaded_episode_count} local`);
        if (evidence.missing_episode_count !== undefined) pills.push(`${evidence.missing_episode_count} missing`);
        if (evidence.library_evidence_source) pills.push(evidence.library_evidence_source);
        if (evidence.current_quality && evidence.target_quality) pills.push(`${evidence.current_quality} → ${evidence.target_quality}`);
        if (!pills.length) return null;
        return DOM.el('div', { className: 'suggestion-evidence-pills' }, pills.slice(0, 5).map(text =>
            DOM.el('span', { className: 'pill pill-subtle' }, [text])
        ));
    }

    /**
     * Public method for the SuggestionManager.approve workflow.
     *
     * Keep DOM lookups local, prefer ActionClient/APIClient for server calls,
     * and preserve event names/data attributes so other components can extend
     * this behavior without reaching into private state.
     */
    async approve(s) {
        try {
            if (s.endpoint) {
                let payload = {};
                if (s.body_json && s.body_json !== '{}') {
                    try { payload = JSON.parse(s.body_json); } catch (e) {}
                }
                if (Object.keys(payload).length === 0 && s.body) payload = s.body;
                await APIClient.fetch(s.endpoint, { method: s.method || 'POST', body: JSON.stringify(payload) });
            }
            if (!s.synthetic && s.id) {
                await APIClient.post(`/api/suggestions/${s.id}/approve`);
            }
            toast.show('Approved');
            this.load();
            if (window.downloads) downloads.load();
        } catch (e) { toast.error(e.message); }
    }

    /**
     * Public method for the SuggestionManager.deny workflow.
     *
     * Keep DOM lookups local, prefer ActionClient/APIClient for server calls,
     * and preserve event names/data attributes so other components can extend
     * this behavior without reaching into private state.
     */
    async deny(id) {
        try {
            await APIClient.post(`/api/suggestions/${id}/deny`);
            toast.show('Dismissed');
            this.load();
        } catch (e) { toast.error(e.message); }
    }

    _categoryLabel(categoryId) {
        if (categoryId === 'tv') return 'TV shows';
        if (categoryId === 'movie') return 'Movies';
        return categoryId || 'Library';
    }
}

window.SuggestionManager = SuggestionManager;
