/**
 * WebSocket client for LJS.
 *
 * Establishes and manages a stateful, auto-reconnecting WebSocket connection
 * to /ws/events, forwarding incoming events onto the global `shipEvents` bus.
 */

class WebSocketClient {
    /**
     * @param {EventBus} eventBus - The shared central event bus.
     * @param {string} endpoint - The WebSocket route to connect to.
     */
    constructor(eventBus, endpoint = '/ws/events') {
        this._eventBus = eventBus;
        this._endpoint = endpoint;
        this._socket = null;
        this._reconnectCount = 0;
        this._maxReconnects = 15;
        this._reconnectTimer = null;
    }

    /**
     * Initiates the stateful WebSocket connection.
     */
    connect() {
        if (this._socket && this._socket.readyState === WebSocket.OPEN) return;

        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const url = `${protocol}//${window.location.host}${this._endpoint}`;
        
        console.log(`[WebSocketClient] Connecting to ${url}...`);
        this._socket = new WebSocket(url);

        this._socket.onopen = () => {
            console.log('[WebSocketClient] Connected successfully.');
            this._reconnectCount = 0;
            this._eventBus.publish('system:connection_status', { connected: true });
        };

        this._socket.onmessage = (event) => {
            try {
                const data = JSON.parse(event.data);
                // Publish incoming websocket payload onto the event bus
                if (data.type) {
                    this._eventBus.publish(data.type, data);
                }
            } catch (err) {
                console.error('[WebSocketClient] Error parsing message frame:', err);
            }
        };

        this._socket.onclose = (event) => {
            console.warn(`[WebSocketClient] Connection closed (code: ${event.code}).`);
            this._eventBus.publish('system:connection_status', { connected: false });
            this._scheduleReconnect();
        };

        this._socket.onerror = (err) => {
            console.error('[WebSocketClient] Error encountered:', err);
            this._socket.close();
        };
    }

    /**
     * Sends a command payload to the server.
     * @param {string} type - Event payload type name.
     * @param {Object} payload - Associated variables.
     */
    send(type, payload = {}) {
        if (this._socket && this._socket.readyState === WebSocket.OPEN) {
            this._socket.send(JSON.stringify({ type, payload }));
        } else {
            console.error('[WebSocketClient] Cannot send. Socket is not open.');
        }
    }

    /**
     * Schedules connection retry with exponential backoff.
     * @private
     */
    _scheduleReconnect() {
        if (this._reconnectCount >= this._maxReconnects) {
            console.error('[WebSocketClient] Max reconnect attempts reached. Giving up.');
            return;
        }

        this._reconnectCount++;
        const backoffMs = Math.min(1000 * Math.pow(1.5, this._reconnectCount), 20000);
        
        console.log(`[WebSocketClient] Retrying connection in ${Math.round(backoffMs)}ms (attempt ${this._reconnectCount}/${this._maxReconnects})...`);
        
        if (this._reconnectTimer) clearTimeout(this._reconnectTimer);
        this._reconnectTimer = setTimeout(() => {
            this.connect();
        }, backoffMs);
    }
}

window.WebSocketClient = WebSocketClient;
