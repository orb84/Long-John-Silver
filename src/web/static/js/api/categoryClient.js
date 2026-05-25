/**
 * Category API client for LJS.
 *
 * Wraps the category-first REST API.
 */
class CategoryApiClient {
    /**
     * Public method for the CategoryApiClient.listCategories workflow.
     *
     * Keep DOM lookups local, prefer ActionClient/APIClient for server calls,
     * and preserve event names/data attributes so other components can extend
     * this behavior without reaching into private state.
     */
    static async listCategories() {
        return APIClient.get('/api/categories');
    }

    /**
     * Public method for the CategoryApiClient.getManifest workflow.
     *
     * Keep DOM lookups local, prefer ActionClient/APIClient for server calls,
     * and preserve event names/data attributes so other components can extend
     * this behavior without reaching into private state.
     */
    static async getManifest(categoryId) {
        return APIClient.get(`/api/categories/${encodeURIComponent(categoryId)}/manifest`);
    }

    /**
     * Public method for the CategoryApiClient.listItems workflow.
     *
     * Keep DOM lookups local, prefer ActionClient/APIClient for server calls,
     * and preserve event names/data attributes so other components can extend
     * this behavior without reaching into private state.
     */
    static async listItems(categoryId) {
        return APIClient.get(`/api/categories/${encodeURIComponent(categoryId)}/items`);
    }

    /**
     * Public method for the CategoryApiClient.addItem workflow.
     *
     * Keep DOM lookups local, prefer ActionClient/APIClient for server calls,
     * and preserve event names/data attributes so other components can extend
     * this behavior without reaching into private state.
     */
    static async addItem(categoryId, payload) {
        return APIClient.post(`/api/categories/${encodeURIComponent(categoryId)}/items`, payload);
    }

    /**
     * Public method for the CategoryApiClient.createItem workflow.
     *
     * Keep DOM lookups local, prefer ActionClient/APIClient for server calls,
     * and preserve event names/data attributes so other components can extend
     * this behavior without reaching into private state.
     */
    static async createItem(categoryId, payload) {
        return this.addItem(categoryId, payload);
    }

    /**
     * Public method for the CategoryApiClient.getItem workflow.
     *
     * Keep DOM lookups local, prefer ActionClient/APIClient for server calls,
     * and preserve event names/data attributes so other components can extend
     * this behavior without reaching into private state.
     */
    static async getItem(categoryId, itemId) {
        return APIClient.get(`/api/categories/${encodeURIComponent(categoryId)}/items/${encodeURIComponent(itemId)}`);
    }

    /**
     * Public method for the CategoryApiClient.updateItem workflow.
     *
     * Keep DOM lookups local, prefer ActionClient/APIClient for server calls,
     * and preserve event names/data attributes so other components can extend
     * this behavior without reaching into private state.
     */
    static async updateItem(categoryId, itemId, payload) {
        return APIClient.patch(`/api/categories/${encodeURIComponent(categoryId)}/items/${encodeURIComponent(itemId)}`, payload);
    }

    /**
     * Public method for the CategoryApiClient.removeItem workflow.
     *
     * Keep DOM lookups local, prefer ActionClient/APIClient for server calls,
     * and preserve event names/data attributes so other components can extend
     * this behavior without reaching into private state.
     */
    static async removeItem(categoryId, itemId) {
        return APIClient.delete(`/api/categories/${encodeURIComponent(categoryId)}/items/${encodeURIComponent(itemId)}`);
    }

    /**
     * Public method for the CategoryApiClient.deleteItem workflow.
     *
     * Keep DOM lookups local, prefer ActionClient/APIClient for server calls,
     * and preserve event names/data attributes so other components can extend
     * this behavior without reaching into private state.
     */
    static async deleteItem(categoryId, itemId) {
        return this.removeItem(categoryId, itemId);
    }

    /**
     * Public method for the CategoryApiClient.pauseItem workflow.
     *
     * Keep DOM lookups local, prefer ActionClient/APIClient for server calls,
     * and preserve event names/data attributes so other components can extend
     * this behavior without reaching into private state.
     */
    static async pauseItem(categoryId, itemId) {
        return APIClient.post(`/api/categories/${encodeURIComponent(categoryId)}/items/${encodeURIComponent(itemId)}/pause`);
    }

    /**
     * Public method for the CategoryApiClient.resumeItem workflow.
     *
     * Keep DOM lookups local, prefer ActionClient/APIClient for server calls,
     * and preserve event names/data attributes so other components can extend
     * this behavior without reaching into private state.
     */
    static async resumeItem(categoryId, itemId) {
        return APIClient.post(`/api/categories/${encodeURIComponent(categoryId)}/items/${encodeURIComponent(itemId)}/resume`);
    }

    /**
     * Public method for the CategoryApiClient.executeItemAction workflow.
     *
     * Keep DOM lookups local, prefer ActionClient/APIClient for server calls,
     * and preserve event names/data attributes so other components can extend
     * this behavior without reaching into private state.
     */
    static async executeItemAction(categoryId, itemId, actionName, argumentsPayload = {}) {
        return APIClient.post(
            `/api/categories/${encodeURIComponent(categoryId)}/items/${encodeURIComponent(itemId)}/actions/${encodeURIComponent(actionName)}`,
            { arguments: argumentsPayload }
        );
    }

    /**
     * Public method for the CategoryApiClient.executeCategoryAction workflow.
     *
     * Keep DOM lookups local, prefer ActionClient/APIClient for server calls,
     * and preserve event names/data attributes so other components can extend
     * this behavior without reaching into private state.
     */
    static async executeCategoryAction(categoryId, actionName, argumentsPayload = {}) {
        return APIClient.post(
            `/api/categories/${encodeURIComponent(categoryId)}/actions/${encodeURIComponent(actionName)}`,
            argumentsPayload
        );
    }

    /**
     * Public method for the CategoryApiClient.executeWorkflow workflow.
     *
     * Keep DOM lookups local, prefer ActionClient/APIClient for server calls,
     * and preserve event names/data attributes so other components can extend
     * this behavior without reaching into private state.
     */
    static async executeWorkflow(categoryId, workflowName, argumentsPayload = {}) {
        return APIClient.post(
            `/api/categories/${encodeURIComponent(categoryId)}/workflows/${encodeURIComponent(workflowName)}`,
            argumentsPayload
        );
    }

    /**
     * Public method for the CategoryApiClient.scaffoldSkill workflow.
     *
     * Keep DOM lookups local, prefer ActionClient/APIClient for server calls,
     * and preserve event names/data attributes so other components can extend
     * this behavior without reaching into private state.
     */
    static async scaffoldSkill() {
        return APIClient.get('/api/categories/scaffold/skill');
    }

    /**
     * Public method for the CategoryApiClient.scaffoldPreview workflow.
     *
     * Keep DOM lookups local, prefer ActionClient/APIClient for server calls,
     * and preserve event names/data attributes so other components can extend
     * this behavior without reaching into private state.
     */
    static async scaffoldPreview(spec) {
        return APIClient.post('/api/categories/scaffold/preview', { spec });
    }

    /**
     * Public method for the CategoryApiClient.scaffoldApply workflow.
     *
     * Keep DOM lookups local, prefer ActionClient/APIClient for server calls,
     * and preserve event names/data attributes so other components can extend
     * this behavior without reaching into private state.
     */
    static async scaffoldApply(spec, approved = false) {
        return APIClient.post('/api/categories/scaffold/apply', { spec, approved });
    }
}
window.CategoryApiClient = CategoryApiClient;
