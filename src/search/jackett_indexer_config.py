"""
Jackett indexer auto-configuration for LJS.

Automatically configures open/public indexers in a running Jackett instance,
and exposes diagnostics plus schema-driven custom configuration for private
or closed indexers the user has access to.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any

from loguru import logger
import httpx


# Curated baseline: broadly useful public indexers that tend to work without
# account cookies/FlareSolverr. It remains available as a conservative profile,
# but first-run setup now targets every open/public indexer Jackett exposes.
DEFAULT_JACKETT_PROFILE = "all_open_public"
DEFAULT_PUBLIC_INDEXERS = [
    "yts",
    "thepiratebay",
    "torrentgalaxyclone",
    "limetorrents",
    "therarbg",
    "nyaasi",
    "internetarchive",
    "torrentproject2",
    "knaben",
    "magnetz",
    "torrentdownload",
    "damagnet",
    "magnetdownload",
]

# Domain-oriented optional profiles.  IDs are best-effort Jackett identifiers;
# unavailable IDs are skipped cleanly after the live catalogue is fetched.
JACKETT_INDEXER_PROFILES: dict[str, list[str]] = {
    "balanced_public": DEFAULT_PUBLIC_INDEXERS,
    "books": [
        "audiobookbay",
        "ebookbay",
        "internetarchive",
        "thepiratebay",
        "torrentgalaxyclone",
        "limetorrents",
        "torrentdownload",
        "knaben",
        "magnetz",
    ],
    "audiobooks": [
        "audiobookbay",
        "internetarchive",
        "thepiratebay",
        "torrentgalaxyclone",
        "limetorrents",
        "torrentdownload",
        "knaben",
    ],
    "anime": ["nyaasi", "anirena", "acgrip", "bangumi-moe", "dmhy"],
    "all_open_public": [],  # computed dynamically from the live Jackett catalogue
    "broad_public": [],  # backwards-compatible alias for all_open_public
}

_CONFIG_TIMEOUT = 15.0


@dataclass(frozen=True)
class JackettIndexerInfo:
    """Normalized Jackett indexer catalogue entry."""

    id: str
    name: str
    configured: bool
    type: str = "unknown"
    language: str = ""
    categories: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()


class JackettIndexerConfigurer:
    """Auto-configures and diagnoses indexers in a running Jackett instance."""

    def __init__(self, jackett_url: str, api_key: str):
        self._url = jackett_url.rstrip("/")
        self._api_key = api_key
        self._cookies: dict[str, str] = {}
        self._last_catalogue_error: str | None = None

    async def configure_defaults(self, indexer_ids: list[str] | None = None) -> tuple[int, int, int]:
        """Configure first-run open/public indexers in Jackett.

        Passing explicit IDs preserves older callers/tests.  Without explicit
        IDs, use the same live-catalogue profile as first-launch setup so LJS
        starts with Jackett's full open/free reach instead of a tiny curated
        subset.
        """
        if indexer_ids is not None:
            return await self.configure_indexers(indexer_ids)
        result = await self.configure_profile(DEFAULT_JACKETT_PROFILE)
        return int(result.get("added", 0)), int(result.get("skipped", 0)), int(result.get("failed", 0))

    async def configure_profile(self, profile: str = DEFAULT_JACKETT_PROFILE) -> dict[str, Any]:
        """Configure an indexer profile and return a diagnostic summary.

        ``all_open_public``/``broad_public`` are computed from the live Jackett
        catalogue and attempt to add every public/open indexer Jackett reports.
        Private and semi-private trackers remain opt-in because they need
        per-indexer credentials, cookies, passkeys, or FlareSolverr settings.
        """
        profile = (profile or DEFAULT_JACKETT_PROFILE).strip()
        catalogue = await self.fetch_indexer_catalogue()
        if not catalogue and self._last_catalogue_error:
            return {
                "status": "degraded",
                "profile": profile,
                "requested": 0,
                "missing_ids": [],
                "added": 0,
                "skipped": 0,
                "failed": 0,
                "error": self._last_catalogue_error,
                "diagnostics": self.summarize_catalogue([]),
            }
        available_ids = {entry.id for entry in catalogue}
        if profile in {"all_open_public", "broad_public"}:
            requested = [entry.id for entry in catalogue if self._is_public_like(entry)]
        else:
            requested = list(JACKETT_INDEXER_PROFILES.get(profile, []))
        missing = [idx for idx in requested if idx not in available_ids]
        requested = [idx for idx in requested if idx in available_ids]
        added, skipped, failed = await self.configure_indexers(requested)
        return {
            "status": "ok",
            "profile": profile,
            "requested": len(requested),
            "missing_ids": missing,
            "added": added,
            "skipped": skipped,
            "failed": failed,
            "diagnostics": self.summarize_catalogue(await self.fetch_indexer_catalogue()),
        }

    async def configure_indexers(self, indexer_ids: list[str]) -> tuple[int, int, int]:
        """Configure explicit Jackett indexer IDs."""
        if not indexer_ids:
            return 0, 0, 0
        if not await self._authenticate():
            logger.info("Jackett: indexer configuration skipped because admin session is unavailable")
            return 0, 0, len(indexer_ids)

        configured = await self._fetch_configured_ids()
        added = skipped = failed = 0
        for indexer_id in indexer_ids:
            if indexer_id in configured:
                skipped += 1
                continue
            if await self._configure_indexer(indexer_id):
                added += 1
                configured.add(indexer_id)
            else:
                failed += 1

        logger.info(
            f"Jackett indexer configuration complete: {added} added, "
            f"{skipped} already configured, {failed} failed"
        )
        return added, skipped, failed

    async def fetch_indexer_catalogue(self) -> list[JackettIndexerInfo]:
        """Fetch configured and unconfigured indexers from Jackett.

        Jackett search endpoints use the API key, but indexer administration can
        redirect to the UI login when Jackett has an admin password/session
        policy.  Treat that as a degraded, actionable condition instead of
        spamming warning logs or pretending the catalogue is healthy.
        """
        self._last_catalogue_error = None
        try:
            async with httpx.AsyncClient(
                timeout=_CONFIG_TIMEOUT,
                verify=False,
                follow_redirects=False,
                cookies=self._cookies or None,
            ) as client:
                response = await client.get(
                    f"{self._url}/api/v2.0/indexers",
                    params={"apikey": self._api_key},
                    headers={"Accept": "application/json"},
                )
                if self._is_login_redirect(response):
                    self._last_catalogue_error = (
                        "Jackett indexer administration redirected to the UI login. "
                        "Search may still work for already configured indexers, but LJS cannot "
                        "auto-configure indexers until Jackett admin auth is cleared or handled."
                    )
                    logger.info("Jackett: {}", self._last_catalogue_error)
                    return []
                response.raise_for_status()
            data = response.json()
        except Exception as exc:
            self._last_catalogue_error = f"Jackett indexer catalogue unavailable: {exc}"
            logger.info("Jackett: {}", self._last_catalogue_error)
            return []
        if not isinstance(data, list):
            self._last_catalogue_error = "Jackett indexer catalogue returned an unexpected payload."
            logger.info("Jackett: {}", self._last_catalogue_error)
            return []
        return [self._normalize_indexer(raw) for raw in data if isinstance(raw, dict)]

    def summarize_catalogue(self, catalogue: list[JackettIndexerInfo]) -> dict[str, Any]:
        """Return a compact summary of Jackett catalogue utilization."""
        total = len(catalogue)
        configured = sum(1 for entry in catalogue if entry.configured)
        type_counts = Counter(entry.type or "unknown" for entry in catalogue)
        configured_type_counts = Counter(entry.type or "unknown" for entry in catalogue if entry.configured)
        language_counts = Counter((entry.language or "unknown").lower() for entry in catalogue)
        book_like = [entry for entry in catalogue if self._matches_domain(entry, {"book", "ebook", "audiobook", "audio"})]
        public_like = [entry for entry in catalogue if self._is_public_like(entry)]
        return {
            "total_indexers": total,
            "configured_indexers": configured,
            "unconfigured_indexers": max(0, total - configured),
            "configured_ratio": (configured / total) if total else 0.0,
            "type_counts": dict(type_counts),
            "configured_type_counts": dict(configured_type_counts),
            "language_counts_top": dict(language_counts.most_common(10)),
            "public_like_count": len(public_like),
            "book_or_audio_like_count": len(book_like),
            "book_or_audio_like_configured": sum(1 for entry in book_like if entry.configured),
            "recommended_profiles": sorted(JACKETT_INDEXER_PROFILES),
            "note": (
                "Jackett's /all search only queries configured indexers. Use profiles or explicit "
                "indexer selection to exploit more of the live catalogue."
            ),
        }

    async def diagnostics(self) -> dict[str, Any]:
        """Fetch and summarize current Jackett indexer coverage."""
        catalogue = await self.fetch_indexer_catalogue()
        return {
            "status": "ok" if catalogue else ("degraded" if self._last_catalogue_error else "unknown"),
            "error": self._last_catalogue_error,
            "summary": self.summarize_catalogue(catalogue),
            "configured": [entry.__dict__ for entry in catalogue if entry.configured][:500],
            "unconfigured": [entry.__dict__ for entry in catalogue if not entry.configured][:500],
            "open_public_recommended": [entry.__dict__ for entry in catalogue if self._is_public_like(entry)][:500],
            "profiles": {name: ids for name, ids in JACKETT_INDEXER_PROFILES.items() if name not in {"broad_public", "all_open_public"}},
            "dynamic_profiles": ["all_open_public", "broad_public"],
        }

    async def get_indexer_config_schema(self, indexer_id: str) -> dict[str, Any]:
        """Return a Jackett indexer configuration schema for UI-driven setup."""
        indexer_id = (indexer_id or "").strip()
        if not indexer_id:
            return {"status": "error", "error": "indexer_id is required"}
        if not await self._authenticate():
            return {"status": "error", "error": "Jackett authentication failed"}
        schema = await self._fetch_config_schema(indexer_id)
        if schema is None:
            return {"status": "error", "error": f"No config schema for indexer '{indexer_id}'"}
        return {
            "status": "ok",
            "indexer_id": indexer_id,
            "fields": self._public_config_fields(schema),
            "raw_field_count": len(schema),
        }

    async def configure_custom_indexer(self, indexer_id: str, values: dict[str, Any]) -> dict[str, Any]:
        """Configure an explicit Jackett indexer with user-supplied fields.

        This is the generic path for private/closed trackers. LJS does not try
        to understand every tracker-specific credential shape; it asks Jackett
        for the schema, lets the user fill fields, then posts the completed
        schema back through Jackett's native configuration API.
        """
        indexer_id = (indexer_id or "").strip()
        if not indexer_id:
            return {"status": "error", "error": "indexer_id is required"}
        if not await self._authenticate():
            return {"status": "error", "error": "Jackett authentication failed"}
        schema = await self._fetch_config_schema(indexer_id)
        if schema is None:
            return {"status": "error", "error": f"No config schema for indexer '{indexer_id}'"}
        patched = self._apply_config_values(schema, values or {})
        ok = await self._post_config(indexer_id, patched)
        return {
            "status": "ok" if ok else "error",
            "indexer_id": indexer_id,
            "configured": ok,
            "diagnostics": self.summarize_catalogue(await self.fetch_indexer_catalogue()),
        }

    async def _authenticate(self) -> bool:
        try:
            async with httpx.AsyncClient(
                timeout=_CONFIG_TIMEOUT,
                verify=False,
                follow_redirects=False,
            ) as client:
                response = await client.get(f"{self._url}/UI/Dashboard")
                if self._is_login_redirect(response):
                    self._last_catalogue_error = (
                        "Jackett admin UI requires login; automatic indexer configuration is unavailable."
                    )
                    logger.info("Jackett: {}", self._last_catalogue_error)
                    return False
                self._cookies = dict(client.cookies)
                return response.status_code < 400 and "Jackett" in self._cookies
        except Exception as e:
            self._last_catalogue_error = f"Jackett auth failed: {e}"
            logger.info("Jackett: {}", self._last_catalogue_error)
            return False

    @staticmethod
    def _is_login_redirect(response: httpx.Response) -> bool:
        """Return whether Jackett redirected an admin/API call to UI login."""
        if response.status_code not in {301, 302, 303, 307, 308}:
            return False
        location = str(response.headers.get("location") or "").lower()
        return "/ui/login" in location

    async def _fetch_configured_ids(self) -> set[str]:
        catalogue = await self.fetch_indexer_catalogue()
        return {entry.id for entry in catalogue if entry.configured}

    async def _configure_indexer(self, indexer_id: str) -> bool:
        try:
            config = await self._fetch_config_schema(indexer_id)
            if config is None:
                return False
            return await self._post_config(indexer_id, config)
        except Exception as e:
            logger.warning(f"Jackett: failed to configure indexer '{indexer_id}': {e}")
            return False

    async def _fetch_config_schema(self, indexer_id: str) -> list[dict] | None:
        async with httpx.AsyncClient(
            timeout=_CONFIG_TIMEOUT,
            verify=False,
            cookies=self._cookies,
        ) as client:
            response = await client.get(
                f"{self._url}/api/v2.0/indexers/{indexer_id}/config",
                headers={"Accept": "application/json"},
            )
            if response.status_code != 200:
                logger.debug(f"Jackett: indexer '{indexer_id}' config fetch returned {response.status_code}")
                return None
            return response.json()

    async def _post_config(self, indexer_id: str, config: list[dict]) -> bool:
        async with httpx.AsyncClient(
            timeout=_CONFIG_TIMEOUT,
            verify=False,
            cookies=self._cookies,
        ) as client:
            response = await client.post(
                f"{self._url}/api/v2.0/indexers/{indexer_id}/config",
                headers={"Content-Type": "application/json"},
                json=config,
            )
            if response.status_code == 204:
                logger.debug(f"Jackett: configured indexer '{indexer_id}'")
                return True
            logger.debug(f"Jackett: indexer '{indexer_id}' config POST returned {response.status_code}")
            return False

    @staticmethod
    def _public_config_fields(schema: list[dict]) -> list[dict[str, Any]]:
        """Expose safe config-field metadata for the settings UI."""
        fields: list[dict[str, Any]] = []
        for item in schema or []:
            if not isinstance(item, dict):
                continue
            field_id = str(item.get("id") or item.get("name") or item.get("Name") or "").strip()
            if not field_id:
                continue
            field_type = str(item.get("type") or item.get("Type") or "inputstring").strip()
            label = str(item.get("name") or item.get("label") or item.get("Name") or field_id).strip()
            value = item.get("value") if "value" in item else item.get("Value")
            looks_secret = any(token in field_id.lower() or token in label.lower() for token in ("pass", "key", "token", "cookie", "secret"))
            fields.append({
                "id": field_id,
                "name": label,
                "type": field_type,
                "required": bool(item.get("required") or item.get("Required")),
                "options": item.get("options") or item.get("Options") or [],
                "value": "" if looks_secret else ("" if value is None else str(value)),
                "secret": looks_secret,
                "help": item.get("help") or item.get("Help") or item.get("description") or item.get("Description") or "",
            })
        return fields

    @staticmethod
    def _apply_config_values(schema: list[dict], values: dict[str, Any]) -> list[dict]:
        """Patch Jackett config schema values by id/name without logging secrets."""
        normalized = {str(k).strip().lower(): v for k, v in (values or {}).items()}
        patched: list[dict] = []
        for item in schema or []:
            if not isinstance(item, dict):
                patched.append(item)
                continue
            updated = dict(item)
            keys = [
                str(updated.get("id") or "").strip().lower(),
                str(updated.get("name") or "").strip().lower(),
                str(updated.get("Name") or "").strip().lower(),
            ]
            for key in keys:
                if key and key in normalized:
                    if "value" in updated or "Value" not in updated:
                        updated["value"] = normalized[key]
                    else:
                        updated["Value"] = normalized[key]
                    break
            patched.append(updated)
        return patched

    @staticmethod
    def _normalize_indexer(raw: dict[str, Any]) -> JackettIndexerInfo:
        """Normalize Jackett's changing indexer JSON shape."""
        categories = raw.get("categories") or raw.get("Categories") or []
        tags = raw.get("tags") or raw.get("Tags") or []
        return JackettIndexerInfo(
            id=str(raw.get("id") or raw.get("ID") or raw.get("tracker") or "").strip(),
            name=str(raw.get("name") or raw.get("Name") or raw.get("title") or "").strip(),
            configured=bool(raw.get("configured") or raw.get("Configured")),
            type=str(raw.get("type") or raw.get("Type") or "unknown").strip().lower(),
            language=str(raw.get("language") or raw.get("Language") or "").strip(),
            categories=tuple(str(cat).lower() for cat in categories if cat is not None),
            tags=tuple(str(tag).lower() for tag in tags if tag is not None),
        )

    @staticmethod
    def _is_public_like(entry: JackettIndexerInfo) -> bool:
        text = " ".join([entry.id, entry.name, entry.type, *entry.tags]).lower()
        if any(token in text for token in ("private", "semi-private", "invite", "cookie")):
            return False
        return entry.type in {"public", "unknown", ""} or "public" in text

    @staticmethod
    def _matches_domain(entry: JackettIndexerInfo, needles: set[str]) -> bool:
        text = " ".join([entry.id, entry.name, entry.type, entry.language, *entry.categories, *entry.tags]).lower()
        return any(needle in text for needle in needles)
