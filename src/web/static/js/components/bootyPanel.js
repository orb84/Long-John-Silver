/**
 * BootyPanel component for LJS.
 *
 * Renders category-generic tracked items from CategoryManifest +
 * /api/categories/{category_id}/items instead of assuming TV shows.
 */

class BootyPanel extends Component {
    /**
     * @param {string} elementId - ID of the container element ('booty').
     * @param {EventBus} eventBus - Shared event bus.
     */
    constructor(elementId, eventBus) {
        super(elementId);
        this._eventBus = eventBus;
        this._categories = [];
        this._items = [];
        this._collapsedCategories = new Set();
        this._categoryViewModes = new Map();
        this._loadAttempts = 0;
        this._hasLoadedCatalogOnce = false;
        this._catalogLoadPromise = null;
        this._catalogLoadToken = 0;
        this._loadingCategories = new Set();
        this._lastCatalogLoadedAt = 0;

        if (this.container) {
            this.render();
            this._init();
        }
    }

    /** Render the template layout framework. */
    render() {
        this._clear();

        const header = DOM.el('div', { className: 'page-header glass-panel' }, [
            DOM.el('h2', {}, ['The Booty (Tracked Items)']),
            DOM.el('div', { className: 'search-input spyglass' }, [
                DOM.el('i', { className: 'fa-solid fa-magnifying-glass' }),
                DOM.el('input', {
                    type: 'text',
                    placeholder: 'Search the library...',
                    id: 'library-search',
                    onkeyup: (e) => this.filterGrid(e.target.value)
                })
            ])
        ]);
        this.container.appendChild(header);

        const gridContainer = DOM.el('div', { id: 'library-grid-container', style: { display: 'flex', flexDirection: 'column', gap: '32px' } }, [
            DOM.el('p', { className: 'empty-msg' }, ['Scanning the holds for booty...'])
        ]);
        this.container.appendChild(gridContainer);

        const formPanel = DOM.el('div', { className: 'glass-panel', style: { marginTop: '40px', padding: '32px' } }, [
            DOM.el('h3', { style: { fontFamily: 'var(--font-display)', fontSize: '1.1rem', color: 'var(--accent-gold)', marginBottom: '20px' } }, [
                DOM.el('i', { className: 'fa-solid fa-anchor' }),
                ' Commission a New Hunt'
            ]),
            DOM.el('div', { style: { display: 'grid', gridTemplateColumns: '1fr 2fr 1fr 100px auto', gap: '16px', alignItems: 'end' } }, [
                DOM.el('div', { className: 'form-group', style: { margin: 0 } }, [
                    DOM.el('label', {}, ['Category']),
                    DOM.el('select', { id: 'new-item-category', style: { width: '100%' } })
                ]),
                DOM.el('div', { className: 'form-group', style: { margin: 0 } }, [
                    DOM.el('label', {}, ['Item Name']),
                    DOM.el('input', { type: 'text', id: 'new-item-name', placeholder: 'Enter item name...', style: { width: '100%' } })
                ]),
                DOM.el('div', { className: 'form-group', style: { margin: 0 } }, [
                    DOM.el('label', {}, ['Language']),
                    DOM.el('input', { type: 'text', id: 'new-item-language', value: 'English', style: { width: '100%' } })
                ]),
                DOM.el('div', { className: 'form-group', style: { margin: 0 } }, [
                    DOM.el('label', {}, ['Interval']),
                    DOM.el('input', { type: 'number', id: 'new-item-interval', value: '7', min: '1', max: '365', style: { width: '100%' } })
                ]),
                DOM.btn('Add Item', 'quick-btn', () => this.addHuntItem(), {
                    id: 'add-item-btn',
                    style: { background: 'var(--accent-gold)', color: '#0b0e14', border: 'none', padding: '12px 24px', borderRadius: '8px' }
                })
            ]),
            DOM.el('div', { style: { marginTop: '20px', borderTop: '1px solid var(--glass-border)', paddingTop: '20px' } }, [
                DOM.btn(' Scan Local Library', 'quick-btn', () => this.scanLocalLibrary(), {
                    id: 'scan-library-btn',
                    style: { background: 'rgba(42, 157, 143, 0.2)', color: 'var(--accent-teal)', border: '1px solid var(--accent-teal)' }
                })
            ])
        ]);
        this.container.appendChild(formPanel);
    }

    /** Subscribe to events and load catalog entries. */
    _init() {
        this.loadCatalog();
        this._eventBus.subscribe('system', (e) => {
            if (['category_item_added', 'category_item_removed', 'category_item_updated', 'category_item_paused', 'category_item_resumed', 'category_action_completed', 'library_scan_completed', 'library_reconciled', 'library_metadata_refresh_completed'].includes(e.subtype)) {
                this.loadCatalog();
            }
        });
    }

    /** Retrieve all categories and their tracked items. */
    async loadCatalog(options = {}) {
        const grid = document.getElementById('library-grid-container');
        if (!grid) return;
        const force = Boolean(options && options.force);
        if (this._catalogLoadPromise && !force) return this._catalogLoadPromise;

        const token = ++this._catalogLoadToken;
        this._catalogLoadPromise = (async () => {
            try {
                if (!this._categories.length) {
                    grid.innerHTML = '<p class="empty-msg">Loading library categories...</p>';
                }

                const categoriesResponse = await CategoryApiClient.listCategories();
                if (token !== this._catalogLoadToken) return;

                this._categories = categoriesResponse.categories || [];
                this._items = [];
                this._loadingCategories = new Set(this._categories.map(category => category.category_id).filter(Boolean));
                this._populateCategorySelect();
                this.renderCatalogGrid();

                const loadedItems = [];
                const jobs = this._categories.map(async (category) => {
                    const categoryId = category.category_id;
                    try {
                        const itemResponse = await CategoryApiClient.listItems(categoryId);
                        if (token !== this._catalogLoadToken) return;
                        (itemResponse.items || []).forEach(item => {
                            loadedItems.push({ ...item, category_id: categoryId, category_display_name: category.display_name });
                        });
                    } catch (err) {
                        // One category endpoint should not blank the whole library.
                        console.warn(`[BootyPanel] Failed to load category ${categoryId}:`, err);
                    } finally {
                        if (token === this._catalogLoadToken) {
                            this._loadingCategories.delete(categoryId);
                            this._items = loadedItems.slice();
                            this.renderCatalogGrid();
                        }
                    }
                });

                await Promise.allSettled(jobs);
                if (token !== this._catalogLoadToken) return;

                this._items = loadedItems;
                this._loadingCategories.clear();
                this._lastCatalogLoadedAt = Date.now();
                this._loadAttempts = 0;
                this.renderCatalogGrid();

                if (!this._hasLoadedCatalogOnce && this._categories.length && loadedItems.length === 0) {
                    this._hasLoadedCatalogOnce = true;
                    setTimeout(() => this.loadCatalog({ force: true }), 1200);
                } else {
                    this._hasLoadedCatalogOnce = true;
                }
            } catch (err) {
                console.error('[BootyPanel] Failed to load the library catalog:', err);
                grid.innerHTML = '<p class="empty-msg">Failed to load the library catalog</p>';
                if (this._loadAttempts < 3) {
                    this._loadAttempts += 1;
                    setTimeout(() => this.loadCatalog({ force: true }), 1000 * this._loadAttempts);
                }
            } finally {
                if (token === this._catalogLoadToken) this._catalogLoadPromise = null;
            }
        })();
        return this._catalogLoadPromise;
    }

    /** Fill the add-item category select from manifests. */
    _populateCategorySelect() {
        const select = document.getElementById('new-item-category');
        if (!select) return;
        const selected = select.value;
        select.innerHTML = '';
        this._categories.forEach(category => {
            select.appendChild(DOM.el('option', { value: category.category_id }, [category.display_name || category.category_id]));
        });
        if (selected) select.value = selected;
    }

    /** Render grouped category item cards. */
    renderCatalogGrid() {
        const grid = document.getElementById('library-grid-container');
        if (!grid) return;

        grid.innerHTML = '';
        if (!this._categories.length) {
            grid.appendChild(DOM.el('p', { className: 'empty-msg' }, ['No categories are available yet.']));
            return;
        }

        const grouped = new Map();
        this._items.forEach(item => {
            const categoryId = item.category_id || item.item_type || 'media';
            if (!grouped.has(categoryId)) grouped.set(categoryId, []);
            grouped.get(categoryId).push(item);
        });

        this._categories.forEach(manifest => {
            const categoryId = manifest.category_id;
            const list = grouped.get(categoryId) || [];
            const displayName = manifest.display_name || categoryId;
            const viewMode = this._categoryViewModes.get(categoryId) || 'icons';
            const isCollapsed = this._collapsedCategories.has(categoryId);
            const mediaGrid = DOM.el('div', {
                className: `media-grid category-view-${viewMode}`,
                style: {
                    display: isCollapsed ? 'none' : (viewMode === 'list' ? 'flex' : 'grid'),
                    flexDirection: viewMode === 'list' ? 'column' : undefined,
                    gap: viewMode === 'list' ? '10px' : undefined,
                    transition: 'all 0.3s ease'
                }
            });

            const chevron = DOM.el('i', {
                className: `fa-solid fa-chevron-down category-chevron ${isCollapsed ? 'collapsed' : ''}`,
                style: { marginLeft: 'auto', marginRight: '16px', transform: isCollapsed ? 'rotate(-90deg)' : 'rotate(0deg)' }
            });
            const title = DOM.el('span', {}, [`${displayName.toUpperCase()} `, DOM.el('small', { style: { color: 'var(--text-dim)', fontFamily: 'var(--font-body)' } }, [`${list.length} item${list.length === 1 ? '' : 's'}`])]);
            const modeControls = DOM.el('span', { className: 'category-view-controls', style: { display: 'inline-flex', gap: '6px', marginRight: '10px' } }, [
                DOM.btn('', `icon-btn ${viewMode === 'icons' ? 'active' : ''}`, (e) => { e.stopPropagation(); this._setCategoryViewMode(categoryId, 'icons'); }, { title: 'Icon view', content: '<i class="fa-solid fa-grip"></i>' }),
                DOM.btn('', `icon-btn ${viewMode === 'list' ? 'active' : ''}`, (e) => { e.stopPropagation(); this._setCategoryViewMode(categoryId, 'list'); }, { title: 'List view', content: '<i class="fa-solid fa-list"></i>' })
            ]);
            const header = DOM.el('h3', {
                style: {
                    fontFamily: 'var(--font-display)', fontSize: '1.15rem', color: 'var(--accent-gold)',
                    marginBottom: '16px', borderLeft: '4px solid var(--accent-gold)', paddingLeft: '10px',
                    display: 'flex', alignItems: 'center', cursor: 'pointer', userSelect: 'none'
                },
                onclick: () => this._toggleCategoryCollapse(categoryId, mediaGrid, chevron)
            }, [title, chevron, modeControls]);

            const section = DOM.el('div', { className: `category-section category-section-${categoryId}` }, [header]);
            if (!list.length) {
                const message = this._loadingCategories.has(categoryId)
                    ? 'Loading tracked items...'
                    : `No ${displayName} items yet. Add one below or scan the category folder after adding files.`;
                mediaGrid.appendChild(DOM.el('p', { className: 'empty-msg', style: { margin: '0.5rem 0 1rem 0' } }, [message]));
            } else {
                list.forEach(item => mediaGrid.appendChild(viewMode === 'list' ? this._renderListRow(categoryId, manifest, item) : this._renderCard(categoryId, manifest, item)));
            }
            section.appendChild(mediaGrid);
            grid.appendChild(section);
        });
    }

    _setCategoryViewMode(categoryId, mode) {
        this._categoryViewModes.set(categoryId, mode === 'list' ? 'list' : 'icons');
        this.renderCatalogGrid();
    }

    _categoryIcon(manifest) {
        const icon = String(manifest.icon || '').toLowerCase();
        const known = { film: 'fa-film', tv: 'fa-tv', music: 'fa-music', headphones: 'fa-headphones', 'book-open': 'fa-book-open', book: 'fa-book-open', folder: 'fa-folder-open' };
        return known[icon] || 'fa-folder-open';
    }

    _renderListRow(categoryId, manifest, item) {
        const itemId = item.item_id || item.key;
        const itemTitle = item.display_name || item.title || item.name || itemId;
        const isPaused = item.enabled === false;
        const statusLabel = item.progress || item.status || this._formatItemProgress(item);
        const icon = this._categoryIcon(manifest);
        return DOM.el('div', {
            className: 'media-card media-list-row glass-panel',
            dataset: { item: itemId, category: categoryId },
            ondblclick: (event) => { event.preventDefault(); this.showDetails(categoryId, itemId); },
            style: { display: 'grid', gridTemplateColumns: '44px minmax(0, 1fr) auto', alignItems: 'center', gap: '14px', minHeight: '58px', padding: '12px 14px' }
        }, [
            DOM.el('div', { className: 'poster-placeholder', style: { width: '44px', height: '44px', minHeight: '44px' } }, [DOM.el('i', { className: `fa-solid ${icon}` })]),
            DOM.el('div', { className: 'card-content', style: { minWidth: 0 } }, [
                DOM.el('h3', { style: { margin: 0, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' } }, [itemTitle]),
                DOM.el('p', { style: { margin: '4px 0 0 0', color: 'var(--text-dim)' } }, [`${manifest.display_name || categoryId} · ${statusLabel} · ${item.language || 'Default'}`])
            ]),
            DOM.el('div', { style: { display: 'flex', gap: '8px' } }, [
                DOM.btn('', 'play-btn category-detail-btn', () => this.showDetails(categoryId, itemId), { content: '<i class="fa-solid fa-circle-info"></i> Details' }),
                DOM.btn('', 'play-btn pause-toggle-btn', () => this.togglePause(categoryId, itemId, isPaused), { style: { background: 'rgba(255,255,255,0.05)' }, content: `<i class="fa-solid ${isPaused ? 'fa-play' : 'fa-pause'}"></i>` })
            ])
        ]);
    }

    /** Render one item card. */
    _renderCard(categoryId, manifest, item) {
        const itemId = item.item_id || item.key;
        const itemTitle = item.display_name || item.title || item.name || itemId;
        const isPaused = item.enabled === false;
        const statusLabel = item.progress || item.status || this._formatItemProgress(item);
        const icon = this._categoryIcon(manifest);
        const posterUrl = this._posterUrlFor(item);
        const placeholder = DOM.el('div', { className: 'poster-placeholder' }, [DOM.el('i', { className: `fa-solid ${icon}` })]);
        if (posterUrl) {
            placeholder.style.backgroundImage = `url("${String(posterUrl).replace(/"/g, '%22')}")`;
            placeholder.style.backgroundSize = 'cover';
            placeholder.style.backgroundPosition = 'center';
            const iconNode = placeholder.querySelector('i');
            if (iconNode) iconNode.style.display = 'none';
        }

        const titleBanner = DOM.el('div', { className: 'card-title-banner' }, [
            DOM.el('span', { className: 'banner-title' }, [itemTitle]),
            DOM.el('span', { className: 'banner-badge' }, [statusLabel])
        ]);

        return DOM.el('div', {
            className: 'media-card',
            dataset: { item: itemId, category: categoryId },
            ondblclick: (event) => {
                event.preventDefault();
                this.showDetails(categoryId, itemId);
            },
            title: 'Double-click to open details'
        }, [
            placeholder,
            titleBanner,
            DOM.el('div', { className: 'card-overlay' }, [
                DOM.el('div', { className: 'quality-badge' }, [statusLabel]),
                DOM.el('div', { className: 'card-content' }, [
                    DOM.el('h3', {}, [itemTitle]),
                    DOM.el('p', {}, [`Language: ${item.language || 'Default'} (${item.check_interval_days || '—'}d check)`]),
                    DOM.el('div', { style: { display: 'flex', gap: '8px' } }, [
                        DOM.btn('', 'play-btn category-detail-btn', () => this.showDetails(categoryId, itemId), {
                            content: '<i class="fa-solid fa-circle-info"></i> Details'
                        }),
                        DOM.btn('', 'play-btn pause-toggle-btn', () => this.togglePause(categoryId, itemId, isPaused), {
                            style: { background: 'rgba(255,255,255,0.05)' },
                            content: `<i class="fa-solid ${isPaused ? 'fa-play' : 'fa-pause'}"></i>`
                        })
                    ])
                ])
            ])
        ]);
    }

    /** Collapse or expand a category section. */
    _toggleCategoryCollapse(categoryId, mediaGrid, chevron) {
        if (this._collapsedCategories.has(categoryId)) {
            this._collapsedCategories.delete(categoryId);
            mediaGrid.style.display = (this._categoryViewModes.get(categoryId) || 'icons') === 'list' ? 'flex' : 'grid';
            chevron.style.transform = 'rotate(0deg)';
        } else {
            this._collapsedCategories.add(categoryId);
            mediaGrid.style.display = 'none';
            chevron.style.transform = 'rotate(-90deg)';
        }
    }

    /** Resolve a browser-safe poster URL from category metadata. */
    _posterUrlFor(item) {
        const metadata = item.metadata || {};
        const direct = item.local_poster_url || item.poster_url || metadata.local_poster_url || metadata.poster_url;
        if (direct) return direct;
        const posterPath = item.poster_path || metadata.poster_path;
        if (!posterPath) return null;
        const value = String(posterPath);
        if (value.startsWith('http://') || value.startsWith('https://') || value.startsWith('/category-data/')) return value;
        if (value.startsWith('/')) return `https://image.tmdb.org/t/p/w500${value}`;
        return null;
    }

    /** Return a compact progress label for any category item. */
    _formatItemProgress(item) {
        const computed = item.computed || (item.canonical_object && item.canonical_object.computed) || {};
        if (computed.downloaded_episode_count) return `${computed.downloaded_episode_count} local episodes`;
        if (computed.downloaded_file_count) return `${computed.downloaded_file_count} local files`;
        if (computed.downloaded_unit_count) return `${computed.downloaded_unit_count} local units`;
        if (computed.has_local_files) return 'In library';
        if (item.resolution) return item.resolution;
        return item.enabled === false ? 'Paused' : 'Tracked';
    }

    /** Filter grids dynamically based on typing inputs. */
    filterGrid(val) {
        const query = val.toLowerCase();
        const cards = document.querySelectorAll('#library-grid-container .media-card');
        cards.forEach(card => {
            const h3 = card.querySelector('h3');
            card.style.display = h3 && h3.textContent.toLowerCase().includes(query) ? 'block' : 'none';
        });
    }

    /** Open details dialog modal. */
    showDetails(categoryId, itemId) {
        if (!window.detailModal) return;
        window.detailModal.open(categoryId, itemId).catch((err) => {
            if (window.toast) toast.error(err.message || 'Failed to open item details');
            this.loadCatalog();
        });
    }

    /** Pause or resume category item checks. */
    async togglePause(categoryId, itemId, currentlyPaused) {
        try {
            const data = currentlyPaused ? await CategoryApiClient.resumeItem(categoryId, itemId) : await CategoryApiClient.pauseItem(categoryId, itemId);
            toast.show(data.message || 'Updated status');
            this.loadCatalog();
        } catch (err) {
            toast.error(err.message);
        }
    }

    /** Submit form to register category item. */
    async addHuntItem() {
        const categoryEl = document.getElementById('new-item-category');
        const nameEl = document.getElementById('new-item-name');
        const langEl = document.getElementById('new-item-language');
        const intervalEl = document.getElementById('new-item-interval');
        if (!nameEl || !nameEl.value.trim()) {
            toast.error('Cap\'n, enter a hunt target name!');
            return;
        }
        try {
            const payload = {
                name: nameEl.value.trim(),
                language: langEl ? langEl.value.trim() : 'English',
                check_interval_days: intervalEl ? parseInt(intervalEl.value) : 7
            };
            const categoryId = categoryEl && categoryEl.value
                ? categoryEl.value
                : ((this._categories[0] || {}).category_id || 'media');
            const data = await CategoryApiClient.addItem(categoryId, payload);
            toast.show(data.message || 'Hunting orders added!');
            nameEl.value = '';
            this.loadCatalog();
        } catch (err) {
            toast.error(err.message);
        }
    }

    /** Trigger background folder parsing scans. */
    async scanLocalLibrary() {
        try {
            toast.show('Starting fleet scanner...');
            const data = await APIClient.post('/api/library/scan');
            toast.show(data.message || 'Scan triggered!');
            this.loadCatalog();
        } catch (err) {
            toast.error(err.message);
        }
    }
}

window.BootyPanel = BootyPanel;
