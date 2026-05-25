/**
 * FidgetCompass for LJS.
 *
 * Controls the interactive compass.  It can be dragged anywhere on the
 * viewport, spins when clicked, and snaps back to point to the active view's
 * coordinate system.
 */

class FidgetCompass extends Component {
    /**
     * @param {EventBus} eventBus - Shared central event bus.
     */
    constructor(eventBus) {
        super('fidget-compass');
        this._eventBus = eventBus;
        this._needle = document.getElementById('compass-needle');
        this._activeAngle = 0;
        this._isSpinning = false;
        this._dragState = null;
        this._dragThreshold = 4;
        
        if (this.container && this._needle) {
            this._restorePosition();
            this._init();
        }
    }

    /**
     * Set up drag, click spin, and snapping subscriptions.
     * @private
     */
    _init() {
        this.container.addEventListener('pointerdown', (event) => this._beginDrag(event));
        document.addEventListener('pointermove', (event) => this._moveDrag(event));
        document.addEventListener('pointerup', (event) => this._endDrag(event));
        document.addEventListener('pointercancel', (event) => this._endDrag(event));

        document.addEventListener('mousemove', (e) => {
            if (this._isSpinning || this._dragState) return;
            this._pointNeedleAtCursor(e.clientX, e.clientY);
        });

        document.addEventListener('mouseleave', () => {
            this.pointTo(this._activeAngle);
        });

        this._eventBus.subscribe('view:changed', (data) => {
            if (data.angle !== undefined) {
                this.pointTo(data.angle);
            }
        });
    }

    /**
     * Restore the last captain-chosen compass position.
     * @private
     */
    _restorePosition() {
        try {
            const raw = localStorage.getItem('ljs_compass_position');
            if (!raw) return;
            const pos = JSON.parse(raw);
            if (!Number.isFinite(pos.left) || !Number.isFinite(pos.top)) return;
            this._placeAt(pos.left, pos.top, true);
        } catch (err) {
            console.warn('[FidgetCompass] Could not restore compass position:', err);
        }
    }

    /**
     * Persist a safe viewport-bounded compass position.
     * @private
     */
    _savePosition() {
        if (!this.container) return;
        const rect = this.container.getBoundingClientRect();
        localStorage.setItem('ljs_compass_position', JSON.stringify({
            left: Math.round(rect.left),
            top: Math.round(rect.top)
        }));
    }

    /**
     * Start a drag gesture without deciding yet whether it is a click.
     * @param {PointerEvent} event
     * @private
     */
    _beginDrag(event) {
        if (!this.container) return;
        event.preventDefault();
        const rect = this.container.getBoundingClientRect();
        this.container.setPointerCapture?.(event.pointerId);
        this.container.classList.add('is-dragging');
        this._dragState = {
            pointerId: event.pointerId,
            startX: event.clientX,
            startY: event.clientY,
            offsetX: event.clientX - rect.left,
            offsetY: event.clientY - rect.top,
            moved: false
        };
    }

    /**
     * Move the compass during an active drag.
     * @param {PointerEvent} event
     * @private
     */
    _moveDrag(event) {
        if (!this._dragState || !this.container) return;
        const dx = event.clientX - this._dragState.startX;
        const dy = event.clientY - this._dragState.startY;
        if (Math.hypot(dx, dy) >= this._dragThreshold) {
            this._dragState.moved = true;
        }
        if (!this._dragState.moved) return;
        this._placeAt(event.clientX - this._dragState.offsetX, event.clientY - this._dragState.offsetY);
    }

    /**
     * End a drag; a non-moving pointer gesture remains the old fidget spin.
     * @param {PointerEvent} event
     * @private
     */
    _endDrag(event) {
        if (!this._dragState || !this.container) return;
        const wasMove = this._dragState.moved;
        this.container.releasePointerCapture?.(event.pointerId);
        this.container.classList.remove('is-dragging');
        this._dragState = null;
        if (wasMove) {
            this._savePosition();
            this.pointTo(this._activeAngle);
        } else {
            this.spin();
        }
    }

    /**
     * Move the compass to a viewport-bounded location.
     * @param {number} left
     * @param {number} top
     * @param {boolean} [clamp=true]
     * @private
     */
    _placeAt(left, top, clamp = true) {
        if (!this.container) return;
        const rect = this.container.getBoundingClientRect();
        const width = rect.width || 100;
        const height = rect.height || 100;
        let nextLeft = left;
        let nextTop = top;
        if (clamp) {
            nextLeft = Math.max(8, Math.min(window.innerWidth - width - 8, nextLeft));
            nextTop = Math.max(8, Math.min(window.innerHeight - height - 8, nextTop));
        }
        this.container.style.left = `${nextLeft}px`;
        this.container.style.top = `${nextTop}px`;
        this.container.style.right = 'auto';
        this.container.style.bottom = 'auto';
    }

    /**
     * Point the needle toward a screen coordinate.
     * @param {number} clientX
     * @param {number} clientY
     * @private
     */
    _pointNeedleAtCursor(clientX, clientY) {
        const rect = this.container.getBoundingClientRect();
        const centerX = rect.left + rect.width / 2;
        const centerY = rect.top + rect.height / 2;
        const deltaX = clientX - centerX;
        const deltaY = clientY - centerY;
        let angleDeg = (Math.atan2(deltaY, deltaX) + Math.PI / 2) * (180 / Math.PI);
        angleDeg = (angleDeg + 360) % 360;
        this._needle.style.transition = 'none';
        this._needle.style.transform = `rotate(${angleDeg}deg)`;
    }

    /**
     * Rotate the needle smoothly to an exact angle.
     * @param {number} angle - Target rotation degree.
     */
    pointTo(angle) {
        this._activeAngle = angle;
        this._needle.style.transition = 'transform 0.7s cubic-bezier(0.34, 1.56, 0.64, 1)';
        this._needle.style.transform = `rotate(${angle}deg)`;
    }

    /**
     * Spin the compass needle procedurally with wild drag inertia.
     */
    spin() {
        this._isSpinning = true;
        const randomRotations = Math.random() * 720 + 360;
        const targetSpin = this._activeAngle + randomRotations;
        this._needle.style.transition = 'transform 1.2s cubic-bezier(0.1, 1, 0.3, 1)';
        this._needle.style.transform = `rotate(${targetSpin}deg)`;
        console.log(`[FidgetCompass] Compass spun by Captain! (spinning to ${Math.round(targetSpin)}deg)`);
        setTimeout(() => this.snapBack(), 650);
    }

    /**
     * Snap the needle back to its active view angle.
     */
    snapBack() {
        if (!this._isSpinning) return;
        this._isSpinning = false;
        this.pointTo(this._activeAngle);
    }
}

window.FidgetCompass = FidgetCompass;
