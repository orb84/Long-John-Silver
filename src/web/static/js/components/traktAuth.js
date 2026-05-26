/**
 * Trakt authentication component for LJS.
 *
 * Handles the PKCE/OOB OAuth flow from both setup and settings screens. LJS
 * ships with a public app client ID, so normal users only link their account;
 * advanced users may supply a custom Trakt application client ID.
 */

function getTraktClientIdInput() {
    return document.getElementById('trakt_client_id')
        || document.getElementById('setup-trakt-id')
        || document.getElementById('pref-trakt-id');
}

/**
 * Public UI helper for the getTraktStatusBadge workflow.
 *
 * Keep inputs DOM-safe, delegate server mutations through API or Action clients,
 * and preserve the return/side-effect contract because templates may call this
 * function directly from event handlers.
 */
function getTraktStatusBadge() {
    return document.getElementById('setup-trakt-status')
        || document.getElementById('settings-trakt-status')
        || document.getElementById('pref-trakt-status');
}

/**
 * Public UI helper for the markTraktConnected workflow.
 *
 * Keep inputs DOM-safe, delegate server mutations through API or Action clients,
 * and preserve the return/side-effect contract because templates may call this
 * function directly from event handlers.
 */
function markTraktConnected() {
    var status = getTraktStatusBadge();
    if (!status) {
        window.location.reload();
        return;
    }
    status.textContent = 'Connected';
    status.style.background = 'rgba(42, 157, 143, 0.2)';
    status.style.color = 'var(--accent-teal)';
    status.style.borderColor = 'rgba(42, 157, 143, 0.4)';
    if (window.toast && typeof window.toast.show === 'function') {
        window.toast.show('Trakt connected!');
    }
}

/**
 * Public UI helper for the startTraktAuth workflow.
 *
 * Keep inputs DOM-safe, delegate server mutations through API or Action clients,
 * and preserve the return/side-effect contract because templates may call this
 * function directly from event handlers.
 */
async function startTraktAuth() {
    try {
        var clientIdInput = getTraktClientIdInput();
        // Empty value intentionally means "use the bundled public LJS Trakt client ID".
        var clientId = clientIdInput ? clientIdInput.value.trim() : '';

        var url = '/api/trakt/auth';
        if (clientId) {
            url += '?client_id=' + encodeURIComponent(clientId);
        }

        var response = await fetch(url);
        var data = await response.json();

        if (!response.ok) {
            ljsAlert(data.error || 'Failed to start Trakt authentication', { title: 'Trakt Authentication' });
            return;
        }

        var width = 600;
        var height = 700;
        var left = (window.innerWidth / 2) - (width / 2);
        var top = (window.innerHeight / 2) - (height / 2);

        var popup = window.open(
            data.auth_url,
            'TraktAuth',
            'width=' + width + ',height=' + height + ',left=' + left + ',top=' + top
        );

        if (!popup) {
            ljsAlert('Popup blocked. Please allow popups for this site.', { title: 'Trakt Authentication' });
        }
    } catch (error) {
        console.error('Trakt auth error:', error);
        ljsAlert('An error occurred while starting Trakt authentication.', { title: 'Trakt Authentication' });
    }
}

window.addEventListener('message', function(event) {
    if (event.data === 'trakt_connected') {
        markTraktConnected();
    }
});

window.startTraktAuth = startTraktAuth;
window.markTraktConnected = markTraktConnected;
