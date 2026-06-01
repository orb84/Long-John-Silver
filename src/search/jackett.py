"""
Jackett search provider.

Round 188 Linux behavior is the contract: the first request remains Jackett's
native aggregate JSON endpoint, ``/api/v2.0/indexers/all/results`` with the
``Query`` parameter.  Newer fixes must not replace that known-good path.

The recovery path below exists only for manual-search parity.  If the aggregate
request times out, errors, or returns an implausible empty set while Jackett has
configured indexers, LJS asks the configured indexers directly, the same logical
shape as Jackett's own manual-search UI.  A timeout is never converted into a
credible "zero results" answer.
"""

from __future__ import annotations

import asyncio
import re
import time
import xml.etree.ElementTree as ET
from typing import Any
from urllib.parse import quote

import httpx
from loguru import logger

from src.core.models import SearchResult
from src.search.base import SearchProvider


class JackettSearch(SearchProvider):
    """Search Jackett using native JSON, with bounded manual-search parity fallback."""

    # Preferred direct selectors are tried first when they are configured, but
    # the actual selector list is discovered from Jackett's own t=indexers feed.
    # This is only an ordering hint, not a hardcoded replacement for Jackett.
    PREFERRED_RECOVERY_ORDER = (
        "eztv", "showrss", "thepiratebay", "1337x", "torrentgalaxyclone",
        "therarbg", "limetorrents", "torrentdownloads", "torrentdownload",
        "torrentproject2", "knaben", "magnetdownload", "torrentkitty",
        "nyaasi", "subsplease", "animetosho", "tokyotosho", "yts",
        "internetarchive", "torrentcore", "magnetz", "damagnet",
    )

    def __init__(
        self,
        url: str,
        api_key: str,
        timeout: float = 75.0,
        *,
        configured_indexers: int | None = None,
        enable_direct_recovery: bool = True,
        allow_filter_indexers: bool | None = None,
    ) -> None:
        """Create a Jackett provider.

        Args:
            url: Base Jackett URL, for example ``http://127.0.0.1:9117``.
            api_key: Jackett API key.
            timeout: Aggregate endpoint timeout. Defaults to the Round 188 value.
            configured_indexers: Optional readiness count collected at startup.
            enable_direct_recovery: Try direct configured-indexer fallback after
                aggregate empty/degraded results.
            allow_filter_indexers: Deprecated compatibility argument. Accepted
                to avoid startup branching; intentionally ignored.
        """
        super().__init__()
        self._url = str(url or "").rstrip("/")
        self._api_key = str(api_key or "")
        self._timeout = max(10.0, float(timeout or 75.0))
        self._direct_timeout = max(4.0, min(8.0, self._timeout / 10.0))
        self._direct_total_timeout = max(12.0, min(24.0, self._timeout / 3.0))
        self._configured_indexers = configured_indexers
        self._enable_direct_recovery = bool(enable_direct_recovery)
        self._configured_selectors_cache: tuple[str, ...] | None = None
        self._last_error_detail: str | None = None
        # Let SearchAggregator wait for Jackett's internal aggregate timeout.
        self.timeout_seconds = int(self._timeout) + 5

    @property
    def name(self) -> str:
        return "Jackett"

    @property
    def supported_categories(self) -> list[str]:
        """Jackett can search all LJS registry categories; indexers decide coverage."""
        return ["*"]

    @property
    def categories(self) -> list[str]:
        """Backward-compatible alias for older UI/status callers."""
        return ["*"]

    async def search(self, query: str, category: str | None = None) -> list[SearchResult]:
        """Search Jackett for one LJS query.

        The v188 aggregate endpoint is still started first and remains the
        compatibility baseline.  To match Jackett's manual UI behavior, direct
        configured-indexer probes run in parallel for interactive recall.  A
        single slow ``all`` aggregate request must not hold every fallback
        hostage for 75 seconds and then masquerade as a legitimate empty result.
        """
        normalized_query = self._normalize_query(query)
        self._last_error_detail = None
        if not normalized_query:
            self.record_error_category("empty_query")
            return []
        if self._configured_indexers is not None and self._configured_indexers <= 0:
            self.record_error_category("no_configured_indexers")
            logger.error("[Jackett] Search skipped: Jackett has zero configured indexers.")
            return []

        logger.info("[Jackett] Starting manual-parity search: aggregate=started direct_recovery={} query={!r}", bool(self._enable_direct_recovery), normalized_query)
        aggregate_task = asyncio.create_task(self._search_aggregate(normalized_query), name="jackett-aggregate")
        direct_task = (
            asyncio.create_task(
                self._search_direct_configured_indexers(normalized_query, category=category),
                name="jackett-direct-manual-parity",
            )
            if self._enable_direct_recovery
            else None
        )

        aggregate_error: str | None = None
        aggregate_empty = False
        direct_empty = False
        pending = {task for task in (aggregate_task, direct_task) if task is not None}
        while pending:
            done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                if task is aggregate_task:
                    try:
                        aggregate_results, aggregate_error = task.result()
                    except Exception as exc:
                        aggregate_results, aggregate_error = [], "unknown"
                        logger.warning("[Jackett] Aggregate task failed for query={!r}: {}", normalized_query, exc)
                    if aggregate_results:
                        self.record_error_category("")
                        if direct_task and not direct_task.done():
                            direct_task.cancel()
                        logger.info(
                            "[Jackett] Native aggregate JSON returned {} parsed result(s) for query={!r}.",
                            len(aggregate_results), normalized_query,
                        )
                        return aggregate_results
                    aggregate_empty = True
                    if aggregate_error:
                        logger.warning(
                            "[Jackett] Aggregate JSON degraded for query={!r}: {}; waiting for direct manual-parity results if still running.",
                            normalized_query, aggregate_error,
                        )
                    else:
                        logger.warning(
                            "[Jackett] Aggregate JSON returned 0 result(s) for query={!r}; waiting for direct manual-parity verification if still running.",
                            normalized_query,
                        )
                elif direct_task is not None and task is direct_task:
                    try:
                        recovery_results = task.result()
                    except Exception as exc:
                        recovery_results = []
                        logger.warning("[Jackett] Direct manual-parity task failed for query={!r}: {}", normalized_query, exc)
                    if recovery_results:
                        self.record_error_category("")
                        if not aggregate_task.done():
                            aggregate_task.cancel()
                        logger.info(
                            "[Jackett] Direct configured-indexer manual-parity search returned {} parsed result(s) for query={!r}.",
                            len(recovery_results), normalized_query,
                        )
                        return recovery_results
                    direct_empty = True
                    logger.warning(
                        "[Jackett] Direct configured-indexer manual-parity search returned 0 result(s) for query={!r}.",
                        normalized_query,
                    )
                    if not aggregate_empty and not aggregate_task.done():
                        # The direct probe has already exercised the manual-UI
                        # equivalent path.  Do not keep the user waiting for a
                        # stuck all-indexer aggregate; escalate to emergency
                        # providers/Soulseek at the orchestration layer.
                        aggregate_error = "aggregate_cancelled_after_direct_probe_empty"
                        aggregate_task.cancel()
                        pending.discard(aggregate_task)

        # Preserve degraded markers so SearchAggregator can run emergency
        # providers.  If aggregate was merely empty and direct verified empty,
        # this is a credible empty result; if aggregate timed out/errored, it is
        # provider degradation, not a real zero.
        marker = aggregate_error or "empty_verified"
        self.record_error_category(marker)
        logger.warning(
            "[Jackett] Query {!r} produced 0 result(s) after aggregate_empty={} direct_empty={} aggregate_error={!r}.",
            normalized_query, aggregate_empty, direct_empty, aggregate_error,
        )
        return []

    async def health_check(self) -> bool:
        """Check that Jackett accepts the configured API key on a search endpoint."""
        try:
            async with httpx.AsyncClient(timeout=10.0, verify=False) as client:
                response = await client.get(
                    f"{self._url}/api/v2.0/indexers/all/results",
                    params={"apikey": self._api_key, "Query": "ljs-health-probe"},
                    follow_redirects=False,
                )
            if self._is_login_redirect(response):
                self.record_error_category("auth_redirect")
                return False
            if response.status_code in {401, 403}:
                self.record_error_category("auth_failed")
                return False
            return response.status_code < 500
        except Exception:
            return False

    async def _search_aggregate(self, query: str) -> tuple[list[SearchResult], str | None]:
        """Run the Round 188 aggregate JSON query and return rows plus error marker."""
        endpoint = f"{self._url}/api/v2.0/indexers/all/results"
        params = {"apikey": self._api_key, "Query": query}
        logger.info("[Jackett] GET /api/v2.0/indexers/all/results Query={!r} apikey=<redacted>", query)
        started = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=self._timeout, verify=False) as client:
                response = await client.get(endpoint, params=params, follow_redirects=False)
            elapsed_ms = int((time.monotonic() - started) * 1000)
            if self._is_login_redirect(response):
                logger.warning(
                    "[Jackett] Aggregate JSON redirected to UI login for query={!r} status={} elapsed_ms={}.",
                    query, response.status_code, elapsed_ms,
                )
                return [], "auth_redirect"
            response.raise_for_status()
            payload = response.json()
            rows = self._payload_rows(payload)
            parsed = self._parse_payload(payload, source_prefix="Jackett")
            logger.info(
                "[Jackett] Aggregate JSON response query={!r} status={} elapsed_ms={} content_type={!r} bytes={} raw_rows={} parsed_rows={}",
                query,
                response.status_code,
                elapsed_ms,
                response.headers.get("content-type") or "",
                len(response.content or b""),
                len(rows),
                len(parsed),
            )
            return parsed, None
        except httpx.TimeoutException:
            self._last_error_detail = f"aggregate timeout after {self._timeout:.1f}s"
            logger.warning("[Jackett] Aggregate JSON timed out after {:.1f}s for query={!r}.", self._timeout, query)
            return [], "timeout"
        except httpx.HTTPStatusError as exc:
            marker = f"http_{exc.response.status_code}"
            self._last_error_detail = marker
            logger.warning("[Jackett] Aggregate JSON HTTP {} for query={!r}.", exc.response.status_code, query)
            return [], marker
        except ValueError as exc:
            self._last_error_detail = "invalid_json"
            logger.warning("[Jackett] Aggregate JSON returned invalid JSON for query={!r}: {}", query, exc)
            return [], "invalid_json"
        except Exception as exc:
            self._last_error_detail = str(exc)
            logger.warning("[Jackett] Aggregate JSON failed for query={!r}: {}", query, exc)
            return [], "unknown"

    async def _search_direct_configured_indexers(self, query: str, *, category: str | None) -> list[SearchResult]:
        """Search configured indexers directly, mirroring Jackett manual search.

        The selector list comes from Jackett's documented Torznab ``t=indexers``
        feed.  Static selector names are used only for ordering and as a last
        resort if Jackett cannot return the configured list.
        """
        selectors = await self._configured_selectors()
        if not selectors:
            selectors = self._fallback_selectors_for_category(category)
            logger.warning(
                "[Jackett] Could not fetch configured selector list; using {} fallback selector hint(s).",
                len(selectors),
            )
        if not selectors:
            return []
        variants = self._query_variants(query, category=category)
        logger.info(
            "[Jackett] Manual-parity fallback starting: selectors={} query_variants={} per_indexer_timeout={:.1f}s total_timeout={:.1f}s",
            len(selectors), variants, self._direct_timeout, self._direct_total_timeout,
        )
        semaphore = asyncio.Semaphore(10)

        async def one(selector: str, variant: str) -> list[SearchResult]:
            async with semaphore:
                rows = await self._search_selector_json(selector, variant)
                if rows:
                    return rows
                return await self._search_selector_torznab(selector, variant)

        tasks = [asyncio.create_task(one(selector, variant)) for variant in variants for selector in selectors]
        rows: list[SearchResult] = []
        try:
            for future in asyncio.as_completed(tasks, timeout=self._direct_total_timeout):
                try:
                    batch = await future
                except Exception as exc:
                    logger.debug("[Jackett] Direct fallback task failed: {}", exc)
                    continue
                if not batch:
                    continue
                rows.extend(batch)
                if len(rows) >= 250:
                    logger.info("[Jackett] Manual-parity fallback stopping early after {} raw row(s).", len(rows))
                    break
        except asyncio.TimeoutError:
            logger.warning(
                "[Jackett] Manual-parity fallback reached total timeout after {:.1f}s with {} raw row(s).",
                self._direct_total_timeout, len(rows),
            )
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
        return self._dedupe(rows)

    async def _configured_selectors(self) -> tuple[str, ...]:
        if self._configured_selectors_cache is not None:
            return self._configured_selectors_cache
        endpoint = f"{self._url}/api/v2.0/indexers/all/results/torznab/api"
        params = {"apikey": self._api_key, "t": "indexers", "configured": "true"}
        try:
            async with httpx.AsyncClient(timeout=12.0, verify=False) as client:
                response = await client.get(endpoint, params=params, headers={"Accept": "application/xml,text/xml,*/*"}, follow_redirects=False)
            if self._is_login_redirect(response):
                logger.warning("[Jackett] Configured-indexer catalogue redirected to UI login.")
                self._configured_selectors_cache = ()
                return ()
            response.raise_for_status()
            selectors = self._parse_configured_indexer_selectors(response.text or "")
            selectors = self._ordered_selectors(selectors)
            logger.info(
                "[Jackett] Configured-indexer catalogue returned {} selector(s) via t=indexers configured=true.",
                len(selectors),
            )
            self._configured_selectors_cache = selectors
            return selectors
        except Exception as exc:
            logger.warning("[Jackett] Failed to fetch configured-indexer selector list: {}", exc)
            self._configured_selectors_cache = ()
            return ()

    @classmethod
    def _ordered_selectors(cls, selectors: list[str]) -> tuple[str, ...]:
        seen = {selector.casefold(): selector for selector in selectors if selector}
        ordered: list[str] = []
        for preferred in cls.PREFERRED_RECOVERY_ORDER:
            match = seen.pop(preferred.casefold(), None)
            if match:
                ordered.append(match)
        ordered.extend(selector for _, selector in sorted(seen.items()))
        return tuple(ordered)

    @staticmethod
    def _parse_configured_indexer_selectors(xml_text: str) -> list[str]:
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError:
            return []
        selectors: list[str] = []
        for node in root.iter():
            tag = JackettSearch._strip_xml_ns(node.tag).lower()
            if tag not in {"indexer", "item"}:
                continue
            raw_id = node.get("id") or node.get("ID") or node.get("tracker") or node.findtext("id") or node.findtext("tracker")
            if not raw_id:
                raw_id = node.get("name") or node.findtext("title") or node.findtext("name")
            selector = str(raw_id or "").strip()
            if selector:
                selectors.append(selector)
        deduped: list[str] = []
        seen: set[str] = set()
        for selector in selectors:
            marker = selector.casefold()
            if marker in seen:
                continue
            seen.add(marker)
            deduped.append(selector)
        return deduped

    async def _search_selector_json(self, selector: str, query: str) -> list[SearchResult]:
        """Search one configured Jackett indexer through native JSON."""
        endpoint = f"{self._url}/api/v2.0/indexers/{quote(selector, safe='')}/results"
        params = {"apikey": self._api_key, "Query": query}
        started = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=self._direct_timeout, verify=False) as client:
                response = await client.get(endpoint, params=params, follow_redirects=False)
            elapsed_ms = int((time.monotonic() - started) * 1000)
            if self._is_login_redirect(response):
                logger.debug("[Jackett] Direct JSON selector={} redirected to login.", selector)
                return []
            response.raise_for_status()
            payload = response.json()
            rows = self._payload_rows(payload)
            parsed = self._parse_payload(payload, source_prefix=f"Jackett:{selector}")
            if parsed or rows:
                logger.info(
                    "[Jackett] Direct JSON selector={} query={!r} status={} elapsed_ms={} raw_rows={} parsed_rows={}",
                    selector, query, response.status_code, elapsed_ms, len(rows), len(parsed),
                )
            return parsed
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 404:
                logger.debug("[Jackett] Direct JSON selector={} HTTP {}", selector, exc.response.status_code)
        except (httpx.TimeoutException, ValueError) as exc:
            logger.debug("[Jackett] Direct JSON selector={} failed for query={!r}: {}", selector, query, self._redact_exception(exc))
        except Exception as exc:
            logger.debug("[Jackett] Direct JSON selector={} failed for query={!r}: {}", selector, query, self._redact_exception(exc))
        return []

    async def _search_selector_torznab(self, selector: str, query: str) -> list[SearchResult]:
        """Search one configured indexer through Torznab XML as a compatibility fallback."""
        endpoint = f"{self._url}/api/v2.0/indexers/{quote(selector, safe='')}/results/torznab/api"
        params = {"apikey": self._api_key, "t": "search", "q": query}
        started = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=self._direct_timeout, verify=False) as client:
                response = await client.get(endpoint, params=params, headers={"Accept": "application/xml,text/xml,*/*"}, follow_redirects=False)
            elapsed_ms = int((time.monotonic() - started) * 1000)
            if self._is_login_redirect(response):
                return []
            response.raise_for_status()
            parsed = self._parse_torznab_results(response.text or "", source_prefix=f"Jackett:{selector}")
            if parsed:
                logger.info(
                    "[Jackett] Direct Torznab selector={} query={!r} status={} elapsed_ms={} parsed_rows={}",
                    selector, query, response.status_code, elapsed_ms, len(parsed),
                )
            return parsed
        except Exception as exc:
            logger.debug("[Jackett] Direct Torznab selector={} failed for query={!r}: {}", selector, query, self._redact_exception(exc))
            return []

    @staticmethod
    def _redact_exception(value: object) -> str:
        """Return a log-safe exception string with Jackett API keys removed."""
        return re.sub(r"(?i)(apikey=)[^&\s]+", r"\1<redacted>", str(value or ""))

    def _fallback_selectors_for_category(self, category: str | None) -> tuple[str, ...]:
        # Category is intentionally only an ordering/coverage hint. The dynamic
        # t=indexers list above is the authoritative manual-parity path.
        return self.PREFERRED_RECOVERY_ORDER

    def _query_variants(self, query: str, *, category: str | None) -> list[str]:
        """Add one broad TV title variant without replacing the exact query."""
        variants = [query]
        if (category or "").strip().lower() == "tv":
            broad = re.sub(r"\bS\d{1,2}(?:E\d{1,2})?\b", " ", query, flags=re.IGNORECASE)
            broad = re.sub(r"\b(?:Season|Stagione)\s*\d{1,2}\b", " ", broad, flags=re.IGNORECASE)
            broad = re.sub(r"\b(?:Complete|Pack|Full|Series|All Seasons)\b", " ", broad, flags=re.IGNORECASE)
            broad = self._normalize_query(broad)
            if broad and broad.casefold() != query.casefold():
                variants.append(broad)
        deduped: list[str] = []
        seen: set[str] = set()
        for item in variants:
            marker = item.casefold()
            if marker in seen:
                continue
            seen.add(marker)
            deduped.append(item)
        return deduped[:2]

    @staticmethod
    def _normalize_query(query: str) -> str:
        return re.sub(r"\s+", " ", str(query or "").replace("_", " ").strip())

    @staticmethod
    def _dedupe(rows: list[SearchResult]) -> list[SearchResult]:
        seen: set[str] = set()
        out: list[SearchResult] = []
        for row in rows:
            marker = (row.magnet or row.url or row.title or "").strip().lower()
            if not marker or marker in seen:
                continue
            seen.add(marker)
            out.append(row)
        return out

    @staticmethod
    def _is_login_redirect(response: httpx.Response) -> bool:
        if response.status_code not in {301, 302, 303, 307, 308}:
            return False
        return "/ui/login" in str(response.headers.get("location") or "").lower()

    @staticmethod
    def _strip_xml_ns(tag: str) -> str:
        if "}" in tag:
            return tag.rsplit("}", 1)[-1]
        return tag

    @staticmethod
    def _payload_rows(payload: Any) -> list[Any]:
        if isinstance(payload, dict):
            rows = payload.get("Results") or payload.get("results") or payload.get("Releases") or payload.get("releases") or []
        elif isinstance(payload, list):
            rows = payload
        else:
            rows = []
        return rows if isinstance(rows, list) else []

    def _parse_payload(self, payload: Any, *, source_prefix: str) -> list[SearchResult]:
        """Parse Jackett native JSON response variants."""
        parsed: list[SearchResult] = []
        for row in self._payload_rows(payload):
            if not isinstance(row, dict):
                continue
            result = self._parse_row(row, source_prefix=source_prefix)
            if result:
                parsed.append(result)
        return parsed

    def _parse_torznab_results(self, xml_text: str, *, source_prefix: str) -> list[SearchResult]:
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError:
            return []
        parsed: list[SearchResult] = []
        for item in root.iter():
            if self._strip_xml_ns(item.tag).lower() != "item":
                continue
            row: dict[str, Any] = {
                "Title": item.findtext("title") or "",
                "Link": item.findtext("link") or "",
                "Guid": item.findtext("guid") or "",
                "Details": item.findtext("comments") or "",
                "Tracker": source_prefix.split(":", 1)[-1],
            }
            for child in list(item):
                tag = self._strip_xml_ns(child.tag).lower()
                attrs = {self._strip_xml_ns(k).lower(): v for k, v in child.attrib.items()}
                name = str(attrs.get("name") or "").lower()
                value = attrs.get("value") or child.text or ""
                if tag == "size":
                    row["Size"] = value
                elif tag == "enclosure":
                    row["Link"] = attrs.get("url") or row.get("Link") or ""
                elif tag == "attr":
                    if name in {"seeders", "grabs"}:
                        row["Seeders"] = value
                    elif name in {"size"}:
                        row["Size"] = value
                    elif name in {"magneturl", "magneturi"}:
                        row["MagnetUri"] = value
            result = self._parse_row(row, source_prefix=source_prefix)
            if result:
                parsed.append(result)
        return parsed

    def _parse_row(self, row: dict[str, Any], *, source_prefix: str) -> SearchResult | None:
        title = str(row.get("Title") or row.get("title") or "").strip()
        if not title:
            return None
        magnet = row.get("MagnetUri") or row.get("MagnetUrl") or row.get("magnet")
        link = row.get("Link") or row.get("link") or ""
        guid = row.get("Guid") or row.get("guid") or ""
        details = row.get("Details") or row.get("Comments") or row.get("details") or ""
        if not magnet and isinstance(link, str) and link.startswith(("magnet:", "http://", "https://")):
            magnet = link
        if not magnet and isinstance(guid, str) and guid.startswith("magnet:"):
            magnet = guid
        detail_url = details or (None if str(link).startswith(("magnet:", "http://", "https://")) else link) or guid
        size_bytes = self._safe_int(row.get("Size") or row.get("size"))
        seeders = self._safe_int(row.get("Seeders") or row.get("seeders"))
        tracker = row.get("Tracker") or row.get("TrackerId") or row.get("Indexer") or ""
        source = f"{source_prefix}:{tracker}" if tracker and str(tracker) not in source_prefix else source_prefix
        result = SearchResult(
            title=title,
            magnet=str(magnet) if magnet else None,
            size=str(size_bytes or row.get("Size") or "Unknown"),
            seeders=seeders,
            source=source,
            url=str(detail_url) if detail_url else None,
        )
        if size_bytes is not None:
            result.size_bytes = size_bytes
        return result

    @staticmethod
    def _safe_int(value: Any) -> int | None:
        try:
            if value is None or value == "":
                return None
            return int(float(value))
        except (TypeError, ValueError):
            return None
