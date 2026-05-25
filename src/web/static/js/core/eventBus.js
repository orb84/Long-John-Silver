/**
 * Event bus for LJS.
 *
 * Provides publish/subscribe eventing for decoupled component communication.
 * Backed by a WebSocket connection proxy for backend push events, and supporting
 * custom client-side events.
 */

class EventBus {
    /**
     * Construct and initialize the EventBus instance.
     *
     * Keep DOM lookups local, prefer ActionClient/APIClient for server calls,
     * and preserve event names/data attributes so other components can extend
     * this behavior without reaching into private state.
     */
    constructor() {
        this.listeners = new Map();
    }

    /**
     * Subscribe to a typed event.
     * @param {string} type - Event identifier.
     * @param {Function} callback - Invoked when event is published.
     * @returns {Function} Unsubscribe trigger.
     */
    subscribe(type, callback) {
        if (!this.listeners.has(type)) {
            this.listeners.set(type, []);
        }
        this.listeners.get(type).push(callback);
        return () => this.unsubscribe(type, callback);
    }

    /**
     * Unsubscribe a listener.
     * @param {string} type - Event identifier.
     * @param {Function} callback - Original callback reference.
     */
    unsubscribe(type, callback) {
        if (!this.listeners.has(type)) return;
        const list = this.listeners.get(type);
        const index = list.indexOf(callback);
        if (index !== -1) {
            list.splice(index, 1);
        }
    }

    /**
     * Publish an event.
     * @param {string} type - Event identifier.
     * @param {Object} data - Event payload.
     */
    publish(type, data = {}) {
        if (!this.listeners.has(type)) return;
        const list = this.listeners.get(type);
        // Execute callbacks safely
        list.forEach(cb => {
            try {
                cb(data);
            } catch (err) {
                console.error(`[EventBus] Error in callback for event ${type}:`, err);
            }
        });
    }

    /**
     * Legacy compatibility connection stub.
     * Connecting is now delegated to the modular WebSocketClient.
     */
    connect() {
        if (window.wsClient) {
            window.wsClient.connect();
        } else {
            console.warn('[EventBus] wsClient not initialized yet.');
        }
    }
}

window.EventBus = EventBus;
window.shipEvents = new EventBus();
