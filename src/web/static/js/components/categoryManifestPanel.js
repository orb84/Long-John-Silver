/**
 * CategoryManifestPanel component for LJS.
 *
 * Renders read-only category contracts from /api/categories.  This panel is a
 * diagnostics view: category settings are edited in the Library Categories
 * panel, while this view explains what each backend category declares to the UI
 * and to the LLM runtime.
 */

class CategoryManifestPanel extends Component {
    /**
     * Construct and initialize the CategoryManifestPanel instance.
     */
    constructor(elementId = 'category-manifest-panel') {
        super(elementId);
        this._categories = [];
        if (this.container) this.load();
    }

    /**
     * Load category contracts from the backend registry.
     */
    async load() {
        try {
            const data = await APIClient.get('/api/categories');
            this._categories = data.categories || [];
            this.render();
        } catch (err) {
            console.error('[CategoryManifestPanel] Failed to load categories:', err);
            if (this.container) {
                this._clear();
                this.container.appendChild(DOM.el('p', { className: 'empty-msg' }, [`Category contracts could not be loaded: ${err.message || err}`]));
            }
        }
    }

    /**
     * Render CategoryManifestPanel state into the DOM.
     */
    render() {
        if (!this.container) return;
        this._clear();
        if (!this._categories.length) {
            this.container.appendChild(DOM.el('p', { className: 'empty-msg' }, [
                'No category contracts returned yet. When the registry is available, this panel will list read-only manifests, not editable settings.'
            ]));
            return;
        }
        const cards = this._categories.map(cat => this._renderCategoryCard(cat));
        this.container.appendChild(DOM.el('div', { className: 'category-manifest-grid' }, cards));
    }

    _renderCategoryCard(cat) {
        const categoryId = cat.category_id || cat.id;
        const sections = (cat.ui_sections || []).map(section =>
            DOM.el('li', {}, [`${section.title || section.id || 'Section'} (${section.component || 'component'})`])
        );
        const actions = (cat.actions || []).filter(action => action.ui_visible !== false).map(action =>
            DOM.el('button', {
                className: 'quick-btn category-action-btn',
                onclick: () => this._runAction(categoryId, action.name),
                title: action.description || action.label,
            }, [action.label || action.name])
        );
        const properties = (cat.properties || []).map(prop => {
            const value = prop.current_value !== undefined ? prop.current_value : prop.default_value;
            const suffix = value === undefined || value === null || value === '' ? '' : ` · ${value}`;
            return DOM.el('li', {}, [`${prop.name || prop.id}${suffix}`]);
        });
        const setupRequirements = (cat.setup_requirements || []).map(req => {
            const status = req.configured ? 'configured' : (req.required ? 'needs setup' : 'optional');
            const setting = req.setting_key || req.id || 'setting';
            return DOM.el('li', {}, [`${req.label || setting}: ${status} (${setting})`]);
        });
        const discovery = (cat.discovery_sources || []).map(src =>
            DOM.el('li', {}, [`${src.provider || src.id || 'provider'} · ${src.purpose || src.kind || 'metadata/discovery'}`])
        );

        return DOM.el('div', { className: 'settings-panel glass-panel category-manifest-card' }, [
            DOM.el('h3', {}, [
                DOM.el('i', { className: `fa-solid fa-${cat.icon || 'folder'}` }),
                ` ${cat.display_name || categoryId}`
            ]),
            DOM.el('p', { className: 'muted' }, [cat.description || cat.llm_summary || 'Category contract']),
            DOM.el('p', { className: 'empty-msg' }, [`Live config: config/categories/${categoryId}.yaml`]),
            DOM.el('div', { className: 'chip-row' }, (cat.capabilities || []).map(cap => DOM.el('span', { className: 'chip' }, [cap]))),
            DOM.el('h4', {}, ['Editable properties declared by manifest']),
            DOM.el('ul', {}, properties.length ? properties : [DOM.el('li', {}, ['No editable properties declared'])]),
            DOM.el('h4', {}, ['Setup requirements / services']),
            DOM.el('ul', {}, setupRequirements.length ? setupRequirements : [DOM.el('li', {}, ['No setup requirements declared'])]),
            DOM.el('h4', {}, ['Discovery sources']),
            DOM.el('ul', {}, discovery.length ? discovery : [DOM.el('li', {}, ['No discovery sources declared'])]),
            DOM.el('h4', {}, ['UI Sections']),
            DOM.el('ul', {}, sections.length ? sections : [DOM.el('li', {}, ['No custom sections declared'])]),
            DOM.el('h4', {}, ['Actions']),
            DOM.el('div', { className: 'action-bar' }, actions.length ? actions : [DOM.el('span', { className: 'muted' }, ['No UI actions'])]),
        ]);
    }

    async _runAction(categoryId, actionName) {
        try {
            const receipt = await APIClient.post(`/api/categories/${encodeURIComponent(categoryId)}/actions/${encodeURIComponent(actionName)}`, {});
            toast.show(receipt.user_message || `${actionName} completed`);
        } catch (err) {
            toast.error(err.message || 'Category action failed');
        }
    }
}
