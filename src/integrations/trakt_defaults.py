"""Public Trakt application defaults used by LJS.

Trakt's OAuth client ID is a public application identifier, not a user
credential.  LJS ships with one so normal users can link their own Trakt
account without creating a developer application.  User-specific OAuth tokens
remain private and are saved in ignored category config after authorization.
"""

from __future__ import annotations

import os

# Public LJS Trakt application client ID.  This is intentionally bundled with
# the app: it is not a user secret, and the matching Trakt app is configured for
# the out-of-band PIN/code redirect URI below.
_DEFAULT_BUNDLED_TRAKT_CLIENT_ID = "42bc6ba1535878e40f4773d3e064809f8caf7347e4ba2b3f3ddc61b32f1ab2ac"

# Advanced/developer builds may override the bundled app ID without patching
# source.  Empty env vars still fall back to the shipped public app ID.
BUNDLED_TRAKT_CLIENT_ID = (os.getenv("LJS_BUNDLED_TRAKT_CLIENT_ID") or _DEFAULT_BUNDLED_TRAKT_CLIENT_ID).strip()

# The bundled Trakt app is whitelisted for Trakt's out-of-band authorization
# flow.  Trakt shows the user a code/PIN after approval; LJS then exchanges that
# code using this exact redirect URI.  Do not replace this with a localhost URL
# for the bundled app, or the OAuth exchange will fail.
BUNDLED_TRAKT_REDIRECT_URI = "urn:ietf:wg:oauth:2.0:oob"


def resolve_trakt_client_id(settings: object | None = None, explicit_client_id: str | None = None) -> str | None:
    """Return the Trakt client ID to use for OAuth/API calls.

    Precedence:
    1. Explicit request query parameter or UI-supplied custom app ID.
    2. Category-owned media.services.trakt.client_id from private config.
    3. Bundled public LJS app client ID from this module/environment.

    The bundled ID is not a user credential.  OAuth access and refresh tokens
    remain private and are saved in ignored category config only after the user
    links their Trakt account.
    """
    explicit = (explicit_client_id or "").strip()
    if explicit:
        return explicit
    if settings is not None and hasattr(settings, "category_service_value"):
        try:
            configured = settings.category_service_value("media", "trakt", "client_id")
        except Exception:
            configured = None
        configured_text = str(configured or "").strip()
        if configured_text:
            return configured_text
    return BUNDLED_TRAKT_CLIENT_ID or None


def has_bundled_trakt_client_id() -> bool:
    """Return whether this build has a bundled public Trakt app client ID."""
    return bool(BUNDLED_TRAKT_CLIENT_ID)


def is_bundled_trakt_client_id(client_id: str | None) -> bool:
    """Return whether ``client_id`` is the shipped/public LJS Trakt app ID."""
    return bool(client_id) and str(client_id).strip() == BUNDLED_TRAKT_CLIENT_ID


def trakt_redirect_uri_for_client(client_id: str | None, base_url: str | None = None) -> str:
    """Return the OAuth redirect URI matching the selected Trakt app.

    The bundled public app uses Trakt's OOB/PIN flow.  A user-supplied custom app
    uses the browser callback URL tied to the current LJS origin.
    """
    if is_bundled_trakt_client_id(client_id):
        return BUNDLED_TRAKT_REDIRECT_URI
    origin = (base_url or "").rstrip("/")
    if "127.0.0.1" in origin:
        origin = origin.replace("127.0.0.1", "localhost")
    return f"{origin}/api/trakt/callback"
