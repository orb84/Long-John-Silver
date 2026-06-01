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
import xml.etree.ElementTree as ET

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
    link: str = ""


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
                        "Jackett admin indexer API redirected to the UI login. Falling back to "
                        "Torznab t=indexers because Jackett search endpoints still accept API-key auth."
                    )
                    logger.info("Jackett: {}", self._last_catalogue_error)
                    fallback = await self._fetch_indexer_catalogue_via_torznab()
                    if fallback:
                        return fallback
                    return []
                response.raise_for_status()
            data = response.json()
        except Exception as exc:
            self._last_catalogue_error = f"Jackett indexer catalogue unavailable: {exc}"
            logger.info("Jackett: {}", self._last_catalogue_error)
            fallback = await self._fetch_indexer_catalogue_via_torznab()
            if fallback:
                return fallback
            return []
        if not isinstance(data, list):
            self._last_catalogue_error = "Jackett indexer catalogue returned an unexpected payload."
            logger.info("Jackett: {}", self._last_catalogue_error)
            fallback = await self._fetch_indexer_catalogue_via_torznab()
            if fallback:
                return fallback
            return []
        return [self._normalize_indexer(raw) for raw in data if isinstance(raw, dict)]

    async def _fetch_indexer_catalogue_via_torznab(self) -> list[JackettIndexerInfo]:
        """Fetch indexer info through Jackett's Torznab t=indexers endpoint.

        Recent Jackett builds can protect /api/v2.0/indexers behind the admin UI
        login even for localhost+API-key clients.  Jackett documents
        ``.../indexers/all/results/torznab/api?t=indexers`` as the API-key
        compatible way to get indexer information, so use it for diagnostics.
        """
        configured = await self._fetch_torznab_indexers(configured=True)
        unconfigured = await self._fetch_torznab_indexers(configured=False)
        merged: dict[str, JackettIndexerInfo] = {}
        for entry in unconfigured:
            if entry.id:
                merged[entry.id] = entry
        for entry in configured:
            if entry.id:
                merged[entry.id] = entry
        if merged:
            logger.info(
                "Jackett: indexer diagnostics recovered through Torznab t=indexers "
                f"(configured={len(configured)}, unconfigured={len(unconfigured)})"
            )
            return list(merged.values())
        return []

    async def _fetch_torznab_indexers(self, *, configured: bool) -> list[JackettIndexerInfo]:
        try:
            async with httpx.AsyncClient(timeout=_CONFIG_TIMEOUT, verify=False, follow_redirects=False) as client:
                response = await client.get(
                    f"{self._url}/api/v2.0/indexers/all/results/torznab/api",
                    params={
                        "apikey": self._api_key,
                        "t": "indexers",
                        "configured": str(bool(configured)).lower(),
                    },
                    headers={"Accept": "application/xml,text/xml,*/*"},
                )
                if self._is_login_redirect(response):
                    logger.info(
                        "Jackett: Torznab t=indexers configured={} also redirected to UI login",
                        configured,
                    )
                    return []
                response.raise_for_status()
            text = response.text or ""
            parsed = self._parse_torznab_indexers(text, configured=configured)
            if not parsed:
                logger.info(
                    "Jackett: Torznab t=indexers configured={} returned no parseable indexers (status={}, content_type={!r}, body_prefix={!r})",
                    configured,
                    response.status_code,
                    response.headers.get("content-type") or "",
                    self._safe_body_prefix(response, limit=180),
                )
            return parsed
        except Exception as exc:
            logger.info(f"Jackett: Torznab t=indexers configured={configured} unavailable: {exc}")
            return []

    def _parse_torznab_indexers(self, xml_text: str, *, configured: bool) -> list[JackettIndexerInfo]:
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError:
            return []
        entries: list[JackettIndexerInfo] = []
        # Jackett has used multiple shapes here.  Be liberal: either
        # <indexer id="..."> nodes or RSS <item> entries with id/name attrs.
        for node in root.iter():
            tag = self._strip_xml_ns(node.tag).lower()
            if tag not in {"indexer", "item"}:
                continue
            raw_id = node.get("id") or node.get("ID") or node.get("tracker")
            raw_name = node.get("name") or node.get("Name")
            if not raw_name:
                title = node.findtext("title") or node.findtext("name")
                raw_name = title
            if not raw_id:
                raw_id = node.findtext("id") or node.findtext("tracker") or raw_name
            idx = str(raw_id or "").strip()
            name = str(raw_name or idx).strip()
            if not idx:
                continue
            idx_type = str(node.get("type") or node.get("Type") or node.findtext("type") or "unknown").strip().lower()
            language = str(node.get("language") or node.get("Language") or node.findtext("language") or "").strip()
            link = str(node.get("link") or node.get("site_link") or node.findtext("link") or node.findtext("site") or "").strip()
            tags = []
            cats = []
            for child in list(node):
                child_tag = self._strip_xml_ns(child.tag).lower()
                raw_value = (child.text or "").strip()
                value = raw_value.lower()
                if child_tag == "link" and raw_value:
                    link = raw_value
                elif child_tag == "language" and raw_value and not language:
                    language = raw_value
                elif child_tag == "type" and raw_value and (not idx_type or idx_type == "unknown"):
                    idx_type = raw_value.lower()
                elif child_tag in {"tag", "tags"} and value:
                    tags.append(value)
                elif child_tag in {"category", "categories"} and value:
                    cats.append(value)
            entries.append(JackettIndexerInfo(
                id=idx,
                name=name,
                configured=configured,
                type=idx_type,
                language=language,
                categories=tuple(cats),
                tags=tuple(tags),
                link=link,
            ))
        return entries

    @staticmethod
    def _strip_xml_ns(tag: str) -> str:
        if "}" in tag:
            return tag.rsplit("}", 1)[-1]
        return tag

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
            # Do not use a top-level key named "error" here: diagnostics are a
            # readable status payload, and the unified UI action gateway treats
            # any returned dict containing "error" as a failed action.
            "admin_error": self._last_catalogue_error,
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
                api_probe = await client.get(
                    f"{self._url}/api/v2.0/indexers",
                    params={"apikey": self._api_key},
                    headers={"Accept": "application/json"},
                )
                self._log_probe("admin_api_indexers", api_probe)
                if api_probe.status_code == 200 and not self._is_login_redirect(api_probe):
                    self._cookies = dict(client.cookies)
                    return True

                # Newer Jackett builds may redirect admin/API calls to
                # /UI/Login even when AdminPassword is unset. In no-password
                # mode, GET /UI/Login issues the local auth cookie; if a real
                # password exists the retry still redirects and we fail closed.
                if self._is_login_redirect(api_probe):
                    if await self._bootstrap_login_cookie(client, api_probe.headers.get("location")):
                        retry = await client.get(
                            f"{self._url}/api/v2.0/indexers",
                            params={"apikey": self._api_key},
                            headers={"Accept": "application/json"},
                        )
                        self._log_probe("admin_api_indexers_after_login_cookie", retry)
                        if retry.status_code == 200 and not self._is_login_redirect(retry):
                            self._cookies = dict(client.cookies)
                            return True

                dashboard = await client.get(f"{self._url}/UI/Dashboard")
                self._log_probe("ui_dashboard", dashboard)
                if self._is_login_redirect(dashboard):
                    if await self._bootstrap_login_cookie(client, dashboard.headers.get("location")):
                        dashboard_retry = await client.get(f"{self._url}/UI/Dashboard")
                        self._log_probe("ui_dashboard_after_login_cookie", dashboard_retry)
                        if dashboard_retry.status_code < 400 and not self._is_login_redirect(dashboard_retry):
                            self._cookies = dict(client.cookies)
                            return True
                    self._last_catalogue_error = (
                        "Jackett admin UI requires login and the API-key admin probe did not succeed; automatic indexer configuration is unavailable for this runtime."
                    )
                    logger.info("Jackett: {}", self._last_catalogue_error)
                    return False
                self._cookies = dict(client.cookies)
                return dashboard.status_code < 400
        except Exception as e:
            self._last_catalogue_error = f"Jackett auth failed: {e}"
            logger.info("Jackett: {}", self._last_catalogue_error)
            return False

    async def _bootstrap_login_cookie(self, client: httpx.AsyncClient, location: str | None) -> bool:
        """Try Jackett's no-password UI login-cookie bootstrap."""
        try:
            target = str(location or "/UI/Login?ReturnUrl=%2FUI%2FDashboard")
            if not target.lower().startswith("http"):
                target = self._url + (target if target.startswith("/") else "/" + target)
            login = await client.get(target)
            self._log_probe("ui_login_cookie_bootstrap", login)
            if login.status_code in {301, 302, 303, 307, 308}:
                redirected = str(login.headers.get("location") or "")
                if redirected:
                    if not redirected.lower().startswith("http"):
                        redirected = self._url + (redirected if redirected.startswith("/") else "/" + redirected)
                    follow = await client.get(redirected)
                    self._log_probe("ui_login_cookie_bootstrap_follow", follow)
            return bool(client.cookies)
        except Exception as exc:
            logger.info(f"Jackett: login-cookie bootstrap failed: {exc}")
            return False

    @staticmethod
    def _safe_body_prefix(response: httpx.Response, limit: int = 120) -> str:
        try:
            text = response.text.replace("\n", " ").replace("\r", " ")
            return text[:limit]
        except Exception:
            return ""

    def _log_probe(self, label: str, response: httpx.Response) -> None:
        location = response.headers.get("location") or ""
        content_type = response.headers.get("content-type") or ""
        logger.info(
            "Jackett admin probe {}: status={} location={!r} content_type={!r} login_redirect={} body_prefix={!r}",
            label,
            response.status_code,
            location[:160],
            content_type[:120],
            self._is_login_redirect(response),
            self._safe_body_prefix(response),
        )

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
                params={"apikey": self._api_key},
                headers={"Accept": "application/json"},
            )
            if response.status_code != 200:
                logger.info(
                    "Jackett: indexer '{}' config fetch returned status={} location={!r} content_type={!r} body_prefix={!r}",
                    indexer_id,
                    response.status_code,
                    (response.headers.get("location") or "")[:160],
                    (response.headers.get("content-type") or "")[:120],
                    self._safe_body_prefix(response),
                )
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
                params={"apikey": self._api_key},
                headers={"Content-Type": "application/json"},
                json=config,
            )
            if response.status_code == 204:
                logger.debug(f"Jackett: configured indexer '{indexer_id}'")
                return True
            logger.info(
                "Jackett: indexer '{}' config POST returned status={} location={!r} content_type={!r} body_prefix={!r}",
                indexer_id,
                response.status_code,
                (response.headers.get("location") or "")[:160],
                (response.headers.get("content-type") or "")[:120],
                self._safe_body_prefix(response),
            )
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
            link=str(raw.get("link") or raw.get("Link") or raw.get("site_link") or raw.get("SiteLink") or raw.get("website") or "").strip(),
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
