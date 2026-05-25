"""
Trakt.tv integration for LJS.

Syncs watch status and retrieves recommendations from Trakt.
"""

import httpx
import hashlib
import base64
import secrets
from loguru import logger
from typing import Optional


class TraktClient:
    """Client for the Trakt.tv API with PKCE authentication support."""

    BASE_URL = "https://api.trakt.tv"
    AUTH_URL = "https://trakt.tv/oauth/authorize"
    TOKEN_URL = "https://trakt.tv/oauth/token"

    def __init__(self, client_id: str, access_token: Optional[str] = None):
        self._client_id = client_id
        self._access_token = access_token
        self._headers = {
            "Content-Type": "application/json",
            "trakt-api-version": "2",
            "trakt-api-key": client_id,
        }
        if access_token:
            self._headers["Authorization"] = f"Bearer {access_token}"

    @staticmethod
    def generate_pkce_pair() -> tuple[str, str]:
        """Generates a code_verifier and code_challenge for PKCE."""
        verifier = secrets.token_urlsafe(64)
        sha256_hash = hashlib.sha256(verifier.encode("ascii")).digest()
        challenge = (
            base64.urlsafe_b64encode(sha256_hash)
            .decode("ascii")
            .replace("=", "")
        )
        return verifier, challenge

    def get_auth_url(self, redirect_uri: str, state: str, challenge: str) -> str:
        """Construct the URL for the user to visit for authorization."""
        import urllib.parse
        params = {
            "response_type": "code",
            "client_id": self._client_id,
            "redirect_uri": redirect_uri,
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        # Properly URL-encode all parameter values to prevent malformed queries
        query = "&".join(f"{k}={urllib.parse.quote(str(v), safe='')}" for k, v in params.items())
        return f"{self.AUTH_URL}?{query}"

    async def exchange_code_for_token(
        self, code: str, redirect_uri: str, verifier: str
    ) -> Optional[dict]:
        """Exchange authorization code for an access token using PKCE verifier."""
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": self._client_id,
            "redirect_uri": redirect_uri,
            "code_verifier": verifier,
        }
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(self.TOKEN_URL, json=data)
                response.raise_for_status()
                tokens = response.json()
                self._access_token = tokens.get("access_token")
                if self._access_token:
                    self._headers["Authorization"] = f"Bearer {self._access_token}"
                return tokens
        except Exception as e:
            logger.error(f"Trakt token exchange failed: {e}")
            return None

    async def refresh_token(self, refresh_token: str) -> Optional[dict]:
        """Refresh the access token using a refresh token."""
        # Note: Trakt documentation says refresh_token requires client_secret.
        # However, for PKCE clients, some providers allow refresh without secret.
        # If Trakt fails here, we may need to re-auth or investigate secret-less refresh.
        data = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": self._client_id,
        }
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(self.TOKEN_URL, json=data)
                response.raise_for_status()
                tokens = response.json()
                self._access_token = tokens.get("access_token")
                if self._access_token:
                    self._headers["Authorization"] = f"Bearer {self._access_token}"
                return tokens
        except Exception as e:
            logger.error(f"Trakt token refresh failed: {e}")
            return None

    async def search_show(self, query: str) -> list[dict]:
        """Search for a TV show on Trakt."""
        logger.info(f"Searching Trakt for: {query}")
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(
                    f"{self.BASE_URL}/search/show",
                    params={"query": query},
                    headers=self._headers,
                )
                response.raise_for_status()
                return response.json()
        except Exception as e:
            logger.error(f"Trakt search failed: {e}")
            return []

    async def get_item_progress(self, trakt_id: int) -> Optional[dict]:
        """Get the watched progress for a show (requires user auth)."""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(
                    f"{self.BASE_URL}/shows/{trakt_id}/progress/watched",
                    headers=self._headers,
                )
                response.raise_for_status()
                return response.json()
        except Exception as e:
            logger.error(f"Trakt progress lookup failed: {e}")
            return None

    async def get_popular_shows(self, limit: int = 10) -> list[dict]:
        """Get a list of popular TV shows from Trakt."""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(
                    f"{self.BASE_URL}/shows/popular",
                    params={"limit": limit},
                    headers=self._headers,
                )
                response.raise_for_status()
                return response.json()
        except Exception as e:
            logger.error(f"Trakt popular shows failed: {e}")
            return []

    async def get_recommended_shows(self, limit: int = 10) -> list[dict]:
        """Get trending/recommended shows from Trakt."""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(
                    f"{self.BASE_URL}/shows/trending",
                    params={"limit": limit},
                    headers=self._headers,
                )
                response.raise_for_status()
                return response.json()
        except Exception as e:
            logger.error(f"Trakt recommendations failed: {e}")
            return []

    async def get_personal_recommendations(self, limit: int = 10) -> list[dict]:
        """Get personalized show recommendations (requires user auth)."""
        if "Authorization" not in self._headers:
            return []
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(
                    f"{self.BASE_URL}/recommendations/shows",
                    params={"limit": limit, "ignore_collected": "true"},
                    headers=self._headers,
                )
                response.raise_for_status()
                return response.json()
        except Exception as e:
            logger.error(f"Trakt personal recommendations failed: {e}")
            return []

    async def get_show_details(self, trakt_id: str) -> Optional[dict]:
        """Get full details for a show, including ratings and IDs."""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(
                    f"{self.BASE_URL}/shows/{trakt_id}",
                    params={"extended": "full"},
                    headers=self._headers,
                )
                response.raise_for_status()
                return response.json()
        except Exception as e:
            logger.error(f"Trakt show details failed for {trakt_id}: {e}")
            return None

    async def get_calendar(self, days: int = 7) -> list[dict]:
        """Get upcoming episode releases. Personalized if authenticated, else global."""
        is_auth = "Authorization" in self._headers
        endpoint = "my/shows" if is_auth else "all/shows"
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(
                    f"{self.BASE_URL}/calendars/{endpoint}",
                    params={"days": days},
                    headers=self._headers,
                )
                response.raise_for_status()
                return response.json()
        except Exception as e:
            logger.error(f"Trakt calendar lookup failed: {e}")
            return []