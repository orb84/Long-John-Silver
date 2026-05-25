/**
 * Category item detail modal component for LJS.
 *
 * Renders category-owned item detail payloads. The API payload decides what
 * units/metadata mean; this component only maps known generic components into
 * readable cards/grids.
 */
class CategoryItemDetailModal extends Component {
    /**
     * Construct and initialize the CategoryItemDetailModal instance.
     *
     * Keep DOM lookups local, prefer ActionClient/APIClient for server calls,
     * and preserve event names/data attributes so other components can extend
     * this behavior without reaching into private state.
     */
    constructor() {
        super('category-item-detail-modal');
        this.currentCategoryId = null;
        this.currentItemId = null;
        this.manifest = null;
    }

    /**
     * Run the public open interaction for CategoryItemDetailModal.
     *
     * Keep DOM lookups local, prefer ActionClient/APIClient for server calls,
     * and preserve event names/data attributes so other components can extend
     * this behavior without reaching into private state.
     */
    async open(categoryId, itemId) {
        this.currentCategoryId = categoryId;
        this.currentItemId = itemId;
        try {
            this.manifest = await CategoryApiClient.getManifest(categoryId);
            const itemResponse = await CategoryApiClient.getItem(categoryId, itemId);
            this.renderItem(itemResponse.item || {});
            if (this.container) this.container.style.display = 'flex';
        } catch (err) {
            const isMissing = err && (err.status === 404 || String(err.message || '').toLowerCase().includes('not found'));
            if (isMissing) {
                this.close();
                if (window.toast) toast.show('That library item is no longer aboard. Refreshing the manifest...', 'err');
                if (window.bootyPanel && typeof window.bootyPanel.loadCatalog === 'function') {
                    window.bootyPanel.loadCatalog();
                }
                return;
            }
            if (window.toast) toast.error(err.message || 'Failed to load item details');
        }
    }

    /**
     * Run the public close interaction for CategoryItemDetailModal.
     *
     * Keep DOM lookups local, prefer ActionClient/APIClient for server calls,
     * and preserve event names/data attributes so other components can extend
     * this behavior without reaching into private state.
     */
    close() {
        if (this.container) this.container.style.display = 'none';
    }

    /**
     * Render CategoryItemDetailModal state into the DOM.
     *
     * Keep DOM lookups local, prefer ActionClient/APIClient for server calls,
     * and preserve event names/data attributes so other components can extend
     * this behavior without reaching into private state.
     */
    renderItem(item) {
        if (!this.container) return;
        this._clear();
        const content = DOM.el('div', { className: 'modal-content glass-panel category-detail-modal' }, [
            this._hero(item),
            this._overview(item),
            this._sections(item),
            DOM.el('div', { className: 'category-detail-footer' }, [
                DOM.el('button', { className: 'btn btn-secondary category-detail-close-footer', onclick: () => this.close() }, [DOM.el('i', { className: 'fa-solid fa-xmark' }), ' Close'])
            ])
        ]);
        this.container.appendChild(content);
    }

    _hero(item) {
        const title = item.display_name || item.title || item.key || item.item_id || 'Category Item';
        const posterUrl = item.poster_url || item.local_poster_url || this._tmdbPoster(item.poster_path);
        const poster = DOM.el('div', { className: 'category-detail-poster' }, [
            DOM.el('i', { className: `fa-solid ${this._icon()}` })
        ]);
        if (posterUrl) {
            poster.style.backgroundImage = `url(${posterUrl})`;
            const icon = poster.querySelector('i');
            if (icon) icon.style.display = 'none';
        }

        const chips = [];
        if (this.manifest?.display_name) chips.push(this.manifest.display_name);
        if (item.language) chips.push(item.language);
        if (item.status) chips.push(item.status);
        if (item.year) chips.push(String(item.year));
        const progress = this._progressText(item.progress || item.library_progress || item);
        if (progress) chips.push(progress);

        return DOM.el('div', { className: 'category-detail-hero' }, [
            poster,
            DOM.el('div', { className: 'category-detail-hero-body' }, [
                DOM.el('div', { className: 'category-detail-title-row' }, [
                    DOM.el('h2', {}, title),
                    DOM.el('button', { className: 'category-detail-close-btn', onclick: () => this.close(), title: 'Close details', 'aria-label': 'Close details' }, [DOM.el('i', { className: 'fa-solid fa-xmark' })])
                ]),
                DOM.el('div', { className: 'category-detail-chip-row' }, chips.map(chip =>
                    DOM.el('span', { className: 'pill' }, chip)
                )),
                item.overview ? DOM.el('p', { className: 'category-detail-overview' }, item.overview) : DOM.el('p', { className: 'muted' }, 'No overview metadata available yet.')
            ])
        ]);
    }

    _overview(item) {
        const cells = [];
        const add = (label, value) => {
            if (value === null || value === undefined || value === '' || (Array.isArray(value) && !value.length)) return;
            cells.push(DOM.el('div', { className: 'category-detail-stat' }, [
                DOM.el('span', { className: 'muted' }, label),
                DOM.el('strong', {}, Array.isArray(value) ? value.join(', ') : String(value))
            ]));
        };
        add('Tracked key', item.key || item.item_id);
        add('Language', item.language || item.configured_language);
        add('Quality', this._qualityText(item.quality));
        add('Downloaded units', item.downloaded_episodes_count ?? item.total_units);
        add('Total seasons', item.total_seasons);
        add('Total episodes', item.total_episodes);
        add('Genres', item.genres);
        return cells.length ? DOM.el('section', { className: 'category-detail-stats' }, cells) : DOM.el('div');
    }

    _sections(item) {
        const sectionNodes = [];
        const sections = this.manifest?.ui_sections || [];
        for (const section of sections) {
            const rendered = this._renderSection(section, item);
            if (rendered) sectionNodes.push(rendered);
        }
        if (!sectionNodes.length) {
            sectionNodes.push(this._genericUnitsSection(item));
            sectionNodes.push(this._metadataSection(item));
        }
        return DOM.el('div', { className: 'category-detail-sections' }, sectionNodes.filter(Boolean));
    }

    _renderSection(section, item) {
        const component = section.component || '';
        if (component === 'season_episode_grid') return this._seasonEpisodeGrid(section, item);
        if (component === 'missing_episode_list') return this._missingEpisodeList(section, item);
        if (component === 'metadata_summary') return this._metadataSummary(section, item);
        if (component === 'download_list') return this._downloadList(section, item);
        if (component === 'file_list') return this._genericUnitsSection(item, section.title);
        return null;
    }

    _metadataSummary(section, item) {
        const rows = [];
        const metadata = item.metadata || {};
        const add = (label, value) => {
            if (value === null || value === undefined || value === '' || (Array.isArray(value) && !value.length)) return;
            rows.push(DOM.el('div', { className: 'detail-kv-row' }, [
                DOM.el('span', { className: 'muted' }, label),
                DOM.el('span', {}, Array.isArray(value) ? value.join(', ') : String(value))
            ]));
        };
        add('Provider title', metadata.display_name || metadata.title);
        add('Status', metadata.status || item.status);
        add('Runtime', metadata.runtime ? `${metadata.runtime} min` : null);
        add('TMDB ID', metadata.tmdb_id || item.tmdb_id);
        add('TVMaze ID', metadata.tvmaze_id || item.tvmaze_id);
        add('IMDb ID', metadata.imdb_id || item.imdb_id);
        return this._panel(section.title || 'Metadata', rows.length ? rows : [DOM.el('p', { className: 'muted' }, 'No metadata cached yet.')]);
    }

    _seasonEpisodeGrid(section, item) {
        const seasons = item.seasons || this._seasonsFromUnitGroups(item.unit_groups);
        if (!seasons.length) return this._panel(section.title || 'Seasons', [DOM.el('p', { className: 'muted' }, 'No downloaded episodes recorded yet.')]);
        const seasonNodes = seasons.map(season => {
            const title = season.season === null || season.season === undefined ? 'Unknown season' : `Season ${season.season}`;
            const episodes = (season.episodes || []).map(ep => {
                const files = Array.isArray(ep.files) ? ep.files : [];
                const primary = files[files.length - 1] || ep;
                const fileCount = ep.file_count || files.length || (ep.unit_key ? 1 : 0);
                const audioLanguages = (ep.audio_languages || primary.audio_languages || []);
                const audioText = audioLanguages.length ? audioLanguages.join(', ') : (primary.language || ep.language || '—');
                const subtitleLanguages = (ep.subtitle_languages || primary.subtitle_languages || []);
                const subtitleCount = subtitleLanguages.length || (ep.subtitle_files || []).length || files.reduce((sum, file) => sum + ((file.subtitle_files || []).length), 0);
                return DOM.el('tr', {}, [
                    DOM.el('td', {}, ep.episode ? `E${String(ep.episode).padStart(2, '0')}` : '—'),
                    DOM.el('td', {}, ep.title || ep.display_name || ep.episode_key || ep.unit_key || 'Episode'),
                    DOM.el('td', {}, ep.best_resolution || ep.quality || primary.quality || '—'),
                    DOM.el('td', {}, audioText),
                    DOM.el('td', {}, String(fileCount || '—')),
                    DOM.el('td', {}, this._formatBytes(ep.total_size_bytes || primary.size_bytes)),
                    DOM.el('td', {}, this._formatBitrate(ep.average_bitrate_kbps || primary.estimated_bitrate_kbps)),
                    DOM.el('td', {}, subtitleCount ? String(subtitleCount) : '—')
                ]);
            });
            return DOM.el('details', { className: 'season-detail-group', open: true }, [
                DOM.el('summary', {}, `${title} · ${season.episode_count || (season.episodes || []).length} episodes`),
                DOM.el('div', { className: 'table-scroll' }, [
                    DOM.el('table', { className: 'category-detail-table' }, [
                        DOM.el('thead', {}, DOM.el('tr', {}, [
                            DOM.el('th', {}, '#'), DOM.el('th', {}, 'Title'), DOM.el('th', {}, 'Quality'), DOM.el('th', {}, 'Language'),
                            DOM.el('th', {}, 'Files'), DOM.el('th', {}, 'Size'), DOM.el('th', {}, 'Bitrate'), DOM.el('th', {}, 'Subs')
                        ])),
                        DOM.el('tbody', {}, episodes)
                    ])
                ])
            ]);
        });
        return this._panel(section.title || 'Seasons', seasonNodes);
    }

    _missingEpisodeList(section, item) {
        const missing = item.missing_aired_episodes || item.missing_episodes || [];
        if (!missing.length) return null;
        const nodes = missing.map(ep => DOM.el('li', {}, `S${String(ep.season || 0).padStart(2, '0')}E${String(ep.episode || 0).padStart(2, '0')} ${ep.title || ''} ${ep.air_date ? `(${ep.air_date})` : ''}`));
        return this._panel(section.title || 'Missing Episodes', [DOM.el('ul', { className: 'compact-list' }, nodes)]);
    }

    _downloadList(section, item) {
        const downloads = item.downloading || [];
        if (!downloads.length) return null;
        const nodes = downloads.map(dl => DOM.el('li', {}, `${dl.status || 'download'} · ${Math.round((dl.progress || 0) * 100)}%`));
        return this._panel(section.title || 'Downloads', [DOM.el('ul', { className: 'compact-list' }, nodes)]);
    }

    _genericUnitsSection(item, title = 'Files / Units') {
        const units = item.units || [];
        if (!units.length) return null;
        const rows = units.map(unit => DOM.el('tr', {}, [
            DOM.el('td', {}, unit.logical_key || unit.unit_key || '—'),
            DOM.el('td', {}, unit.display_name || unit.title || '—'),
            DOM.el('td', {}, unit.status || '—'),
            DOM.el('td', {}, unit.quality || unit.resolution || '—'),
            DOM.el('td', {}, (unit.audio_languages || []).length ? unit.audio_languages.join(', ') : (unit.language || '—')),
            DOM.el('td', {}, this._formatBytes(unit.size_bytes)),
            DOM.el('td', {}, this._formatBitrate(unit.estimated_bitrate_kbps)),
            DOM.el('td', {}, (unit.subtitle_files || []).length ? String((unit.subtitle_files || []).length) : '—')
        ]));
        return this._panel(title, [
            DOM.el('div', { className: 'table-scroll' }, [
                DOM.el('table', { className: 'category-detail-table' }, [
                    DOM.el('thead', {}, DOM.el('tr', {}, [
                        DOM.el('th', {}, 'Key'), DOM.el('th', {}, 'Name'), DOM.el('th', {}, 'Status'), DOM.el('th', {}, 'Quality'), DOM.el('th', {}, 'Language'), DOM.el('th', {}, 'Size'), DOM.el('th', {}, 'Bitrate'), DOM.el('th', {}, 'Subs')
                    ])),
                    DOM.el('tbody', {}, rows)
                ])
            ])
        ]);
    }


    _formatBytes(value) {
        const bytes = Number(value || 0);
        if (!bytes || bytes <= 0) return '—';
        const units = ['B', 'KB', 'MB', 'GB', 'TB'];
        let size = bytes;
        let index = 0;
        while (size >= 1024 && index < units.length - 1) {
            size /= 1024;
            index += 1;
        }
        const decimals = index >= 2 ? 1 : 0;
        return `${size.toFixed(decimals)} ${units[index]}`;
    }

    _formatBitrate(value) {
        const kbps = Number(value || 0);
        if (!kbps || kbps <= 0) return '—';
        return kbps >= 1000 ? `${(kbps / 1000).toFixed(1)} Mbps` : `${Math.round(kbps)} kbps`;
    }

    _metadataSection(item) {
        const rows = item.metadata_rows || [];
        if (!rows.length) return null;
        return this._panel('Metadata Rows', rows.map(row => DOM.el('div', { className: 'metadata-row' }, [
            DOM.el('strong', {}, row.provider || 'metadata'),
            DOM.el('span', { className: 'muted' }, row.refreshed_at || '')
        ])));
    }

    _panel(title, children) {
        return DOM.el('section', { className: 'glass-panel detail-section' }, [
            DOM.el('h3', {}, title),
            ...children
        ]);
    }

    _seasonsFromUnitGroups(unitGroups = {}) {
        const episodeGroups = unitGroups.episode || {};
        return Object.keys(episodeGroups).map(key => ({
            season: key === 'default' ? null : Number(key),
            episodes: episodeGroups[key] || [],
            episode_count: (episodeGroups[key] || []).length
        })).sort((a, b) => (a.season || 0) - (b.season || 0));
    }

    _tmdbPoster(path) {
        if (!path) return null;
        if (String(path).startsWith('http') || String(path).startsWith('/category-data/')) return path;
        if (String(path).startsWith('/')) return `https://image.tmdb.org/t/p/w500${path}`;
        return null;
    }

    _progressText(progress) {
        if (!progress) return '';
        if (progress.last_season && progress.last_episode) return `S${String(progress.last_season).padStart(2, '0')}E${String(progress.last_episode).padStart(2, '0')}`;
        if (progress.progress) return progress.progress;
        return '';
    }

    _qualityText(quality) {
        if (!quality) return '';
        if (typeof quality === 'string') return quality;
        return [quality.preferred_resolution, quality.preferred_codecs?.join('/')].filter(Boolean).join(' · ');
    }

    _icon() {
        const icon = this.manifest?.icon || 'box-archive';
        if (icon.includes('fa-')) return icon;
        return icon === 'tv' ? 'fa-tv' : icon === 'film' ? 'fa-film' : `fa-${icon}`;
    }
}
window.CategoryItemDetailModal = CategoryItemDetailModal;
