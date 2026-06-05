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
    category: str = ""
    published_at: str = ""
    engines: list[str] = Field(default_factory=list)


class WebSearchResult(BaseModel):
    """A web search response with provider status and normalized hits.

    ``provider`` is the provider that produced the returned hits. When an
    explicit degraded fallback was used, ``fallback_used`` is true and the
    primary provider/error fields preserve why LJS switched providers. This
    keeps agent/UI responses honest without treating fallback search as the
    normal healthy path.
    """

    query: str
    provider: str
    ok: bool
    hits: list[WebSearchHit] = Field(default_factory=list)
    error: str | None = None
    error_code: str = ""
    fallback_used: bool = False
    primary_provider: str = ""
    primary_error: str = ""
    primary_error_code: str = ""


class WebSearchHealth(BaseModel):
    """Runtime health for a configured web search provider."""

    provider: str
    configured: bool
    ok: bool
    last_error: str | None = None
    error_code: str = ""
    status_code: int | None = None
    endpoint: str = ""
    json_api: bool = False


class WebResearchBudget(BaseModel):
    """Bounds for one web-research operation.

    Search and page fetches can fan out quickly.  The budget keeps LJS from
    letting an agent loop over arbitrary public web queries while still giving
    category extensions enough room to corroborate a fact.
    """

    max_searches: int = 1
    max_pages_per_search: int = 1
    max_urls_to_fetch: int = 5
    require_page_extraction_before_facts: bool = True

    @model_validator(mode="after")
    def _clamp_budget(self) -> "WebResearchBudget":
        self.max_searches = max(1, min(int(self.max_searches or 1), 5))
        self.max_pages_per_search = max(1, min(int(self.max_pages_per_search or 1), 3))
        self.max_urls_to_fetch = max(0, min(int(self.max_urls_to_fetch or 5), 20))
        return self


class WebResearchRequest(BaseModel):
    """Category-neutral request to discover and fetch public web evidence."""

    query: str
    additional_queries: list[str] = Field(default_factory=list)
    intent: str = "general_research"
    category_id: str = ""
    item_id: str = ""
    item_name: str = ""
    language: str = "auto"
    categories: list[str] = Field(default_factory=lambda: ["general"])
    time_range: str = ""
    max_results: int = 5
    budget: WebResearchBudget = Field(default_factory=WebResearchBudget)

    @model_validator(mode="after")
    def _normalize_request(self) -> "WebResearchRequest":
        self.query = str(self.query or "").strip()
        self.additional_queries = [str(q).strip() for q in self.additional_queries if str(q).strip()]
        self.intent = str(self.intent or "general_research").strip() or "general_research"
        self.category_id = str(self.category_id or "").strip()
        self.item_id = str(self.item_id or "").strip()
        self.item_name = str(self.item_name or "").strip()
        self.language = str(self.language or "auto").strip() or "auto"
        self.categories = [str(c).strip() for c in self.categories if str(c).strip()] or ["general"]
        allowed_time_ranges = {"", "day", "month", "year"}
        if self.time_range not in allowed_time_ranges:
            self.time_range = ""
        self.max_results = max(1, min(int(self.max_results or 5), 20))
        return self


class WebResearchSource(BaseModel):
    """One candidate source discovered by web search and optionally fetched."""

    title: str = ""
    url: str
    canonical_url: str = ""
    snippet: str = ""
    source_name: str = ""
    source_kind: str = "unknown"
    rank: int = 0
    query: str = ""
    fetched: bool = False
    fetch_status: str = "search_result_only"
    status_code: int | None = None
    published_at: str = ""
    confidence: float = 0.0
    evidence_id: int | None = None


class WebEvidenceBundle(BaseModel):
    """Fetched public web evidence for an agent/category workflow.

    The bundle deliberately separates candidate sources from extracted evidence.
    Search snippets may appear as source context, but durable facts must be
    based on fetched/extracted pages and later category interpretation.
    """

    topic: str
    intent: str = "general_research"
    ok: bool = False
    provider: str = ""
    query_log_ids: list[int] = Field(default_factory=list)
    sources: list[WebResearchSource] = Field(default_factory=list)
    evidence: list[WebEvidence] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    unresolved_questions: list[str] = Field(default_factory=list)
    facts_authoritative: bool = False
    generated_at: datetime = Field(default_factory=datetime.now)


# --- Category Web Research Hook Models ---


class CategoryWebResearchInput(BaseModel):
    """Input passed to category-owned web-research planning hooks.

    The core orchestration layer provides only generic identity, intent, and
    optional user/request context.  Categories decide what those values mean
    for their own domain and whether public web research is useful.
    """

    category_id: str
    item_id: str = ""
    item_name: str = ""
    intent: str = "general_research"
    language: str = "auto"
    context: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _normalize_category_research_input(self) -> "CategoryWebResearchInput":
        self.category_id = str(self.category_id or "").strip()
        self.item_id = str(self.item_id or "").strip()
        self.item_name = str(self.item_name or self.item_id or "").strip()
        self.intent = str(self.intent or "general_research").strip() or "general_research"
        self.language = str(self.language or "auto").strip() or "auto"
        self.context = dict(self.context or {})
        return self


class CategoryWebResearchSearch(BaseModel):
    """One category-authored public web search request.

    The query remains a discovery instruction; it is not evidence until
    WebResearchService fetches and records candidate pages.
    """

    query: str
    intent: str = "general_research"
    categories: list[str] = Field(default_factory=lambda: ["general"])
    language: str = "auto"
    time_range: str = ""
    max_results: int = 5
    max_urls_to_fetch: int = 4

    @model_validator(mode="after")
    def _normalize_category_search(self) -> "CategoryWebResearchSearch":
        self.query = str(self.query or "").strip()
        self.intent = str(self.intent or "general_research").strip() or "general_research"
        self.categories = [str(value).strip() for value in self.categories if str(value).strip()] or ["general"]
        self.language = str(self.language or "auto").strip() or "auto"
        self.time_range = self.time_range if self.time_range in {"", "day", "month", "year"} else ""
        self.max_results = max(1, min(int(self.max_results or 5), 20))
        self.max_urls_to_fetch = max(0, min(int(self.max_urls_to_fetch or 4), 20))
        return self


class CategoryWebResearchPlan(BaseModel):
    """Category-owned plan for public web research.

    The plan is declarative: category code names useful searches, while the
    generic service owns provider calls, page fetching, budgets, and storage.
    """

    category_id: str = ""
    item_id: str = ""
    intent: str = "general_research"
    searches: list[CategoryWebResearchSearch] = Field(default_factory=list)
    max_searches: int = 4
    require_page_extraction_before_facts: bool = True
    notes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _normalize_category_plan(self) -> "CategoryWebResearchPlan":
        self.category_id = str(self.category_id or "").strip()
        self.item_id = str(self.item_id or "").strip()
        self.intent = str(self.intent or "general_research").strip() or "general_research"
        self.max_searches = max(1, min(int(self.max_searches or 4), 5))
        self.searches = [search for search in self.searches if search.query][: self.max_searches]
        self.notes = [str(note).strip() for note in self.notes if str(note).strip()]
        return self


class CategoryResearchFact(BaseModel):
    """One category-interpreted fact candidate with provenance.

    These facts are interpretations of fetched public pages.  They remain
    separate from category item mutation; callers must explicitly decide when
    an interpreted fact is strong enough to affect durable item state.
    """

    fact_type: str
    value: dict[str, Any] = Field(default_factory=dict)
    source_evidence_ids: list[int] = Field(default_factory=list)
    confidence: float = 0.0
    decided_by: str = "deterministic"
    authoritative: bool = False

    @model_validator(mode="after")
    def _normalize_category_fact(self) -> "CategoryResearchFact":
        self.fact_type = str(self.fact_type or "").strip()
        self.value = dict(self.value or {})
        self.source_evidence_ids = [int(value) for value in self.source_evidence_ids if value]
        self.confidence = max(0.0, min(float(self.confidence or 0.0), 1.0))
        self.decided_by = str(self.decided_by or "deterministic").strip() or "deterministic"
        return self


class CategoryResearchInterpretation(BaseModel):
    """Category interpretation of a web evidence bundle.

    The interpretation is category-owned but not self-mutating.  It can be
    persisted as provenance and displayed to users/agents before a separate
    coordinator path changes tracked item state.
    """

    category_id: str = ""
    item_id: str = ""
    intent: str = "general_research"
    summary: str = ""
    facts: list[CategoryResearchFact] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    unresolved_questions: list[str] = Field(default_factory=list)
    can_mutate_item: bool = False


class CategoryWebResearchResult(BaseModel):
    """Result from a category-owned web research run."""

    ok: bool = False
    category_id: str = ""
    item_id: str = ""
    intent: str = "general_research"
    plan: CategoryWebResearchPlan = Field(default_factory=CategoryWebResearchPlan)
    bundle: WebEvidenceBundle = Field(default_factory=lambda: WebEvidenceBundle(topic=""))
    interpretation: CategoryResearchInterpretation = Field(default_factory=CategoryResearchInterpretation)
    persisted_fact_ids: list[int] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


# --- Web Information Watch Models ---


class WebInformationWatch(BaseModel):
    """Durable user/category request to periodically track public information.

    A watch records what should be checked and when.  It does not make search
    snippets authoritative, mutate category items, or queue downloads. Runs
    produce ``WebInformationWatchEvent`` rows after using the normal
    web-research/category-research evidence pipeline.
    """

    id: str
    owner_type: Literal["user_task", "category_item", "system_suggestion"] = "user_task"
    title: str
    objective: str
    query: str = ""
    intent: str = "general_research"
    category_id: str = ""
    item_id: str = ""
    item_name: str = ""
    language: str = "auto"
    cadence_minutes: int = 10080
    enabled: bool = True
    notify_only_if_meaningful: bool = True
    llm_evaluation_required: bool = True
    allow_download_queueing: bool = False
    query_plan: dict[str, Any] = Field(default_factory=dict)
    user_feedback: dict[str, Any] = Field(default_factory=dict)
    last_run_at: Optional[datetime] = None
    next_run_at: Optional[datetime] = None
    last_event_id: Optional[int] = None
    last_evidence_signature: str = ""
    last_status: str = "never_run"
    last_error: str = ""
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)

    @model_validator(mode="after")
    def _normalize_watch(self) -> "WebInformationWatch":
        self.id = str(self.id or "").strip()
        self.title = str(self.title or self.objective or self.query or "Web information watch").strip()
        self.objective = str(self.objective or self.title or self.query).strip()
        self.query = str(self.query or "").strip()
        self.intent = str(self.intent or "general_research").strip() or "general_research"
        self.category_id = str(self.category_id or "").strip()
        self.item_id = str(self.item_id or "").strip()
        self.item_name = str(self.item_name or self.item_id or "").strip()
        self.language = str(self.language or "auto").strip() or "auto"
        self.cadence_minutes = max(60, min(int(self.cadence_minutes or 10080), 525600))
        self.query_plan = dict(self.query_plan or {})
        self.user_feedback = dict(self.user_feedback or {})
        self.last_status = str(self.last_status or "never_run").strip() or "never_run"
        self.last_error = str(self.last_error or "")[:1000]
        return self


class WebInformationWatchEvent(BaseModel):
    """One execution event for a web information watch."""

    id: Optional[int] = None
    watch_id: str
    status: str = "completed"
    summary: str = ""
    event_type: str = "no_change"
    evidence_signature: str = ""
    source_evidence_ids: list[int] = Field(default_factory=list)
    query_log_ids: list[int] = Field(default_factory=list)
    notification_recommended: bool = False
    llm_review_required: bool = True
    payload: dict[str, Any] = Field(default_factory=dict)
    error: str = ""
    created_at: datetime = Field(default_factory=datetime.now)

    @model_validator(mode="after")
    def _normalize_watch_event(self) -> "WebInformationWatchEvent":
        self.watch_id = str(self.watch_id or "").strip()
        self.status = str(self.status or "completed").strip() or "completed"
        self.summary = str(self.summary or "").strip()
        self.event_type = str(self.event_type or "no_change").strip() or "no_change"
        self.evidence_signature = str(self.evidence_signature or "").strip()
        self.source_evidence_ids = [int(value) for value in self.source_evidence_ids if value]
        self.query_log_ids = [int(value) for value in self.query_log_ids if value]
        self.payload = dict(self.payload or {})
        self.error = str(self.error or "")[:1000]
        return self


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
    """Diagnostics for one provider search.

    ``outcome`` is intentionally coarse and stable for orchestration: callers
    need to distinguish a credible empty result from a provider failure.  The
    older fields remain for UI compatibility.
    """

    provider: str
    ok: bool
    result_count: int = 0
    magnet_count: int = 0
    blocked_reason: str | None = None
    error: str | None = None
    used_browser: bool = False
    elapsed_ms: int = 0
    outcome: str = "ok_empty"


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

