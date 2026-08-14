/**
 * Compact real-time LLM problem notifications.
 *
 * Each provider retry/timeout/failure becomes an individual card. Selecting a
 * card opens the authoritative full diagnostics workspace at the related call.
 */
class LLMProblemCards {
    /** Subscribe to live LLM events and create the compact card stack. */
    constructor(eventBus, diagnosticsPanel) {
        this._eventBus = eventBus;
        this._diagnosticsPanel = diagnosticsPanel;
        this._seen = new Set();
        this._root = this._ensureRoot();
        this._unsubscribe = this._eventBus?.subscribe('llm_activity', payload => this._handle(payload));
        this._snapshotUnsubscribe = this._eventBus?.subscribe(
            'llm_activity_snapshot', payload => this._reconcileSnapshot(payload)
        );
    }

    _ensureRoot() {
        let root = document.getElementById('llm-problem-cards');
        if (root) return root;
        root = DOM.el('aside', {
            id: 'llm-problem-cards',
            className: 'llm-problem-cards',
            'aria-label': 'LLM problem notifications',
            'aria-live': 'polite'
        });
        document.body.appendChild(root);
        return root;
    }

    _handle(payload) {
        const event = payload?.event || payload;
        if (!event || !this._shouldDisplay(event)) return;
        const key = this._canonicalKey(event);
        if (this._seen.has(key)) return;
        this._seen.add(key);
        while (this._seen.size > 200) this._seen.delete(this._seen.values().next().value);
        this._show(event);
    }

    _reconcileSnapshot(payload) {
        const authoritative = Array.isArray(payload?.events) ? payload.events : [];
        if (authoritative.length) {
            authoritative.forEach(event => this._handle(event));
            return;
        }
        this._legacySnapshotEvents(payload).forEach(event => this._handle(event));
    }

    _legacySnapshotEvents(payload) {
        const events = [];
        (payload?.calls || []).forEach(call => {
            (call.attempts || []).forEach(attempt => {
                if (Number(attempt.attempt) > 1) {
                    events.push({
                        event_type: 'retry_started', severity: 'warning', title: 'Retrying LLM request',
                        message: `Attempt ${attempt.attempt} of ${attempt.max_attempts} started for ${call.task || 'LLM task'}.`,
                        call_id: call.call_id, task: call.task, model: call.model,
                        attempt: attempt.attempt, max_attempts: attempt.max_attempts
                    });
                }
                if (attempt.status === 'failed') {
                    const timedOut = /timed?\s*out|timeout/i.test(String(attempt.error || ''));
                    events.push({
                        event_type: timedOut ? 'attempt_timeout' : 'attempt_failed',
                        severity: Number(attempt.attempt) < Number(attempt.max_attempts) ? 'warning' : 'error',
                        title: timedOut ? 'LLM request timed out' : 'LLM request attempt failed',
                        message: `Attempt ${attempt.attempt} of ${attempt.max_attempts} for ${call.task || 'LLM task'} failed: ${attempt.error || 'unknown provider error'}`,
                        call_id: call.call_id, task: call.task, model: call.model,
                        attempt: attempt.attempt, max_attempts: attempt.max_attempts
                    });
                }
            });
            if (call.status === 'failed') {
                events.push({
                    event_type: 'call_failed', severity: 'error', title: 'LLM call failed',
                    message: `${call.task || 'LLM task'} failed: ${call.error || 'unknown provider error'}`,
                    call_id: call.call_id, task: call.task, model: call.model
                });
            } else if (call.status === 'cancelled') {
                events.push({
                    event_type: 'call_cancelled', severity: 'info', title: 'LLM request cancelled',
                    message: `${call.task || 'LLM task'} was cancelled.`,
                    call_id: call.call_id, task: call.task, model: call.model
                });
            }
        });
        return events;
    }

    _canonicalKey(event) {
        const attempt = Number.isFinite(Number(event.attempt)) ? Number(event.attempt) : 'call';
        return `${event.call_id || 'unknown'}:${event.event_type || 'unknown'}:${attempt}`;
    }

    _shouldDisplay(event) {
        return new Set([
            'retry_started', 'attempt_timeout', 'attempt_failed', 'call_failed',
            'context_rejected', 'rate_limited', 'provider_auth_error', 'call_cancelled',
            'route_configuration_changed'
        ]).has(String(event.event_type || ''));
    }

    _show(event) {
        const severity = ['error', 'warning', 'info'].includes(event.severity) ? event.severity : 'warning';
        const card = DOM.el('article', {
            className: `llm-problem-card severity-${severity}`,
            tabindex: '0',
            role: 'button',
            'aria-label': `${event.title || 'LLM problem'}. Open diagnostics.`
        }, [
            DOM.el('div', { className: 'llm-problem-card-icon' }, [
                DOM.el('i', { className: `fa-solid ${this._icon(event.event_type)}` })
            ]),
            DOM.el('div', { className: 'llm-problem-card-copy' }, [
                DOM.el('div', { className: 'llm-problem-card-heading' }, [
                    DOM.el('strong', {}, [event.title || 'LLM problem']),
                    DOM.el('span', {}, [this._attemptLabel(event)])
                ]),
                DOM.el('p', {}, [event.message || 'Open diagnostics for details.']),
                DOM.el('small', {}, [`${event.task || 'LLM'} · ${event.model || 'selected model'}`])
            ]),
            DOM.btn('', 'icon-btn llm-problem-card-close', clickEvent => {
                clickEvent.stopPropagation();
                this._dismiss(card);
            }, {
                title: 'Dismiss notification',
                content: '<i class="fa-solid fa-xmark"></i>'
            })
        ]);
        const open = () => this._diagnosticsPanel?.openCall(event.call_id);
        card.addEventListener('click', open);
        card.addEventListener('keydown', keyEvent => {
            if (keyEvent.key === 'Enter' || keyEvent.key === ' ') {
                keyEvent.preventDefault();
                open();
            }
        });
        this._root.prepend(card);
        requestAnimationFrame(() => card.classList.add('is-visible'));
        while (this._root.children.length > 5) this._dismiss(this._root.lastElementChild, true);
        const lifetime = severity === 'error' ? 60000 : severity === 'warning' ? 40000 : 18000;
        window.setTimeout(() => this._dismiss(card), lifetime);
    }

    _dismiss(card, immediate = false) {
        if (!card?.isConnected) return;
        if (immediate) {
            card.remove();
            return;
        }
        card.classList.remove('is-visible');
        window.setTimeout(() => card.remove(), 220);
    }

    _attemptLabel(event) {
        const attempt = Number(event.attempt);
        const maximum = Number(event.max_attempts);
        if (Number.isFinite(attempt) && Number.isFinite(maximum)) return `${attempt}/${maximum}`;
        return 'DETAILS';
    }

    _icon(eventType) {
        const type = String(eventType || '');
        if (type.includes('timeout')) return 'fa-clock';
        if (type.includes('retry')) return 'fa-rotate';
        if (type.includes('context')) return 'fa-file-circle-exclamation';
        if (type.includes('auth')) return 'fa-key';
        if (type.includes('rate')) return 'fa-gauge-high';
        if (type.includes('cancel')) return 'fa-ban';
        return 'fa-triangle-exclamation';
    }
}

window.LLMProblemCards = LLMProblemCards;
