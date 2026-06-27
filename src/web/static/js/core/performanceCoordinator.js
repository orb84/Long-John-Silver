/**
 * FrontendPerformanceCoordinator for LJS.
 *
 * Centralizes browser-tab visibility, reduced-motion, adaptive timers, and
 * throttled DOM work so long-running tabs do not keep burning CPU/GPU while the
 * user is elsewhere. Components should ask this coordinator before polling,
 * rendering large lists, or running decorative animation loops.
 */
class FrontendPerformanceCoordinator {
    /**
     * @param {EventBus} eventBus - Shared central event bus.
     */
    constructor(eventBus) {
        this._eventBus = eventBus || null;
        this._visible = !document.hidden;
        this._activeView = this._detectActiveView();
        this._timers = new Set();
        this._visibleCallbacks = [];
        this._reducedMotionQuery = window.matchMedia ? window.matchMedia('(prefers-reduced-motion: reduce)') : null;
        this._reducedMotion = Boolean(this._reducedMotionQuery && this._reducedMotionQuery.matches);
        this._lowPower = this._reducedMotion;
        this._onVisibility = () => this._handleVisibilityChange();
        this._onFocus = () => this._handleVisibilityChange();
        this._onBlur = () => this._handleVisibilityChange();
        document.addEventListener('visibilitychange', this._onVisibility);
        window.addEventListener('focus', this._onFocus);
        window.addEventListener('blur', this._onBlur);
        if (this._reducedMotionQuery && this._reducedMotionQuery.addEventListener) {
            this._reducedMotionQuery.addEventListener('change', (event) => {
                this._reducedMotion = Boolean(event.matches);
                this._lowPower = this._reducedMotion || !this._visible;
                this._applyChromeClasses();
                this._publishState('motion_pref_changed');
            });
        }
        if (this._eventBus && typeof this._eventBus.subscribe === 'function') {
            this._eventBus.subscribe('view:changed', (data) => {
                if (data && data.viewId) this._activeView = data.viewId;
                else this._activeView = this._detectActiveView();
                this._publishState('view_changed');
            });
        }
        this._applyChromeClasses();
    }

    /** True when the browser tab is foreground-visible. */
    isVisible() {
        return this._visible;
    }

    /** True when reduced-motion or hidden-tab low-power mode should disable decoration. */
    isLowPower() {
        return this._lowPower || this._reducedMotion;
    }

    /** True when a named LJS view is active and the tab is visible. */
    isViewActive(viewId) {
        if (!this._visible) return false;
        if (!viewId) return true;
        const el = document.getElementById(viewId);
        return Boolean(el && el.classList.contains('active'));
    }

    /** Decorative animations are allowed only in a visible, non-reduced-motion tab. */
    allowAmbientAnimation() {
        return this._visible && !this.isLowPower();
    }

    /** Run callback immediately if visible, otherwise once when the tab becomes visible again. */
    whenVisible(callback) {
        if (typeof callback !== 'function') return;
        if (this._visible) {
            callback();
            return;
        }
        this._visibleCallbacks.push(callback);
    }

    /**
     * Create a visibility-aware adaptive interval.
     *
     * The callback is never allowed to overlap with itself.  A hidden tab uses
     * the background cadence, and `shouldRun` can skip work entirely for views
     * that are not currently active.
     */
    registerAdaptiveInterval(callback, options = {}) {
        const foregroundMs = Math.max(1000, Number(options.foregroundMs || options.intervalMs || 10000));
        const backgroundMs = Math.max(foregroundMs, Number(options.backgroundMs || 60000));
        const shouldRun = typeof options.shouldRun === 'function' ? options.shouldRun : () => true;
        let stopped = false;
        let timer = null;
        let running = false;
        const tick = async () => {
            if (stopped) return;
            const nextDelay = this._visible ? foregroundMs : backgroundMs;
            try {
                if (!running && shouldRun()) {
                    running = true;
                    await callback();
                }
            } catch (err) {
                console.warn('[FrontendPerformanceCoordinator] Adaptive interval callback failed:', err);
            } finally {
                running = false;
                if (!stopped) timer = window.setTimeout(tick, nextDelay);
            }
        };
        timer = window.setTimeout(tick, Number(options.initialDelayMs || foregroundMs));
        const stop = () => {
            stopped = true;
            if (timer) window.clearTimeout(timer);
            this._timers.delete(stop);
        };
        this._timers.add(stop);
        return stop;
    }

    /** Schedule DOM-heavy work for the next animation frame when visible. */
    scheduleFrame(callback) {
        if (typeof callback !== 'function') return null;
        if (!this._visible) {
            const timer = window.setTimeout(callback, 250);
            return () => window.clearTimeout(timer);
        }
        const raf = window.requestAnimationFrame ? window.requestAnimationFrame(callback) : window.setTimeout(callback, 16);
        return () => {
            if (window.cancelAnimationFrame) window.cancelAnimationFrame(raf);
            else window.clearTimeout(raf);
        };
    }

    /** Debounce high-volume input or event streams. */
    debounce(callback, delayMs = 150) {
        let timer = null;
        return (...args) => {
            if (timer) window.clearTimeout(timer);
            timer = window.setTimeout(() => callback(...args), delayMs);
        };
    }

    _handleVisibilityChange() {
        const wasVisible = this._visible;
        this._visible = !document.hidden;
        this._lowPower = this._reducedMotion || !this._visible;
        this._activeView = this._detectActiveView();
        this._applyChromeClasses();
        if (!wasVisible && this._visible) {
            const callbacks = this._visibleCallbacks.splice(0);
            callbacks.forEach(callback => {
                try { callback(); } catch (err) { console.warn('[FrontendPerformanceCoordinator] Visibility callback failed:', err); }
            });
        }
        this._publishState('visibility_changed');
    }

    _detectActiveView() {
        const active = document.querySelector('.view.active');
        return active ? active.id : '';
    }

    _applyChromeClasses() {
        document.documentElement.classList.toggle('ljs-background-tab', !this._visible);
        document.documentElement.classList.toggle('ljs-perf-low-power', this.isLowPower());
        document.documentElement.classList.toggle('ljs-reduced-motion', this._reducedMotion);
    }

    _publishState(reason) {
        if (!this._eventBus || typeof this._eventBus.publish !== 'function') return;
        this._eventBus.publish('ui:visibility', {
            visible: this._visible,
            activeView: this._activeView,
            lowPower: this.isLowPower(),
            reducedMotion: this._reducedMotion,
            reason
        });
    }
}

window.FrontendPerformanceCoordinator = FrontendPerformanceCoordinator;
