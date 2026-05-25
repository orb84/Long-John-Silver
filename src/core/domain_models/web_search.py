"""Browser, torrent scraping, aggregation, and web research models."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
import re
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_serializer, model_validator


class WebSearchHit(BaseModel):
    """A normalized web search result from a configured provider."""

    title: str
    url: str
    snippet: str = ""
    source: str = ""
    rank: int = 0


class WebSearchResult(BaseModel):
    """A web search response with provider status and normalized hits."""

    query: str
    provider: str
    ok: bool
    hits: list[WebSearchHit] = Field(default_factory=list)
    error: str | None = None


class WebSearchHealth(BaseModel):
    """Runtime health for a configured web search provider."""

    provider: str
    configured: bool
    ok: bool
    last_error: str | None = None


# --- Storage / Disk Space Models ---


class StoragePathUsage(BaseModel):
    """Disk usage for one app-managed path such as a category root."""

    path: str
    purpose: str
    category_id: str | None = None
    category_name: str | None = None
    exists: bool = False
    volume_id: str
    mount_point: str
    total_bytes: int = 0
    used_bytes: int = 0
    free_bytes: int = 0
    free_percent: float = 0.0
    status: Literal["ok", "warning", "critical", "unknown"] = "unknown"
    message: str = ""


class StorageVolumeUsage(BaseModel):
    """Aggregated disk usage for one physical/logical storage volume."""

    volume_id: str
    mount_point: str
    total_bytes: int = 0
    used_bytes: int = 0
    free_bytes: int = 0
    free_percent: float = 0.0
    status: Literal["ok", "warning", "critical", "unknown"] = "unknown"
    paths: list[StoragePathUsage] = Field(default_factory=list)
    category_ids: list[str] = Field(default_factory=list)
    purpose_summary: str = ""
    message: str = ""


class StorageCapacityDecision(BaseModel):
    """Preflight decision for a planned download or file operation."""

    ok: bool
    status: Literal["ok", "warning", "critical", "unknown"] = "unknown"
    category_id: str | None = None
    estimated_bytes: int | None = None
    target_path: str = ""
    volume_id: str = ""
    free_bytes: int = 0
    projected_free_bytes: int | None = None
    reason: str = ""


class StorageReport(BaseModel):
    """Full category-aware storage report for UI, tools, and prompts."""

    ok: bool = True
    generated_at: datetime = Field(default_factory=datetime.utcnow)
    volumes: list[StorageVolumeUsage] = Field(default_factory=list)
    paths: list[StoragePathUsage] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    critical: list[str] = Field(default_factory=list)
    llm_summary: str = ""



# --- Browser Automation Models ---


class BrowserHealth(BaseModel):
    """Runtime health for Playwright browser automation."""

    package_installed: bool = False
    browser_installed: bool = False
    launch_ok: bool = False
    navigation_ok: bool = False
    last_error: str | None = None


class BrowserFetchRequest(BaseModel):
    """A browser page fetch request."""

    url: str
    wait_seconds: float = 2.0
    wait_for_selector: str | None = None
    expected_text: str | None = None
    max_content_chars: int = 8000
    screenshot_on_failure: bool = True
    purpose: str = "generic"


class PageLink(BaseModel):
    """A normalized link extracted from a rendered page."""

    text: str
    url: str
    rel: str | None = None


class BrowserFetchResult(BaseModel):
    """Structured result from a Playwright fetch."""

    ok: bool
    url: str
    final_url: str
    status: int
    title: str = ""
    text: str = ""
    html: str = ""
    links: list[PageLink] = Field(default_factory=list)
    challenge_detected: bool = False
    captcha_detected: bool = False
    selector_found: bool | None = None
    blocked_reason: str | None = None
    elapsed_ms: int = 0
    screenshot_path: str | None = None
    error: str | None = None


class ChallengeDetection(BaseModel):
    """Detected challenge, block, consent, or interstitial state."""

    is_challenge: bool = False
    challenge_type: str | None = None
    confidence: float = 0.0
    indicators: list[str] = Field(default_factory=list)


# --- Torrent Scraping Models ---


class TorrentScrapeCandidate(BaseModel):
    """Raw candidate extracted from one provider."""

    title: str
    detail_url: str | None = None
    magnet: str | None = None
    size: str = "Unknown"
    seeders: int | None = None
    leechers: int | None = None
    source: str
    extraction_method: str
    extraction_confidence: float = 0.0
    missing_fields: list[str] = Field(default_factory=list)


class TorrentScrapeResult(BaseModel):
    """Provider scrape result with diagnostics."""

    provider: str
    query: str
    ok: bool
    candidates: list[TorrentScrapeCandidate] = Field(default_factory=list)
    error: str | None = None
    blocked_reason: str | None = None
    elapsed_ms: int = 0


# --- Search Aggregation Diagnostics ---


class ProviderSearchDiagnostics(BaseModel):
    """Diagnostics for one provider search."""

    provider: str
    ok: bool
    result_count: int = 0
    magnet_count: int = 0
    blocked_reason: str | None = None
    error: str | None = None
    used_browser: bool = False
    elapsed_ms: int = 0


class SearchAggregateResult(BaseModel):
    """Search results plus provider diagnostics."""

    query: str
    results: list[SearchResult]
    provider_results: dict[str, ProviderSearchDiagnostics]
    elapsed_ms: int


# --- Candidate Normalization ---


class NormalizedTorrentCandidate(BaseModel):
    """LLM-friendly normalized torrent candidate."""

    title: str
    source: str
    magnet: str | None = None
    magnet_available: bool = False
    detail_url: str | None = None
    size: str = "Unknown"
    size_bytes: int | None = None
    seeders: int | None = None
    parsed_title: str | None = None
    media_type: str | None = None
    season: int | None = None
    episode: int | None = None
    is_bundle: bool = False
    bundle_type: str | None = None
    bundle_scope: str | None = None
    bundle_context: dict[str, Any] = Field(default_factory=dict)
    estimated_unit_size_mb: float | None = None
    resolution: str | None = None
    codec: str | None = None
    release_type: str | None = None
    release_group: str | None = None
    language: str | None = None
    red_flags: list[str] = Field(default_factory=list)
    quality_score: float = 0.0
    extraction_confidence: float = 0.0
    llm_summary: str = ""


# --- Web Research Models ---


class Fact(BaseModel):
    """A single extracted fact from a web page."""

    label: str
    value: str | None = None
    evidence: str = ""
    url: str | None = None
    confidence: float = 0.0


class ExtractedFacts(BaseModel):
    """Structured facts extracted from a rendered page."""

    schema_name: str
    facts: list[Fact] = Field(default_factory=list)


class WebEvidence(BaseModel):
    """A sourced claim extracted from the web."""

    claim: str
    value: str | None = None
    source_name: str
    url: str
    snippet: str = ""
    confidence: float = 0.0
    extracted_at: datetime = Field(default_factory=datetime.now)


class WebResearchReport(BaseModel):
    """A concise sourced report for the assistant."""

    topic: str
    summary: str
    evidence: list[WebEvidence] = Field(default_factory=list)
    visited_urls: list[str] = Field(default_factory=list)
    unresolved_questions: list[str] = Field(default_factory=list)

