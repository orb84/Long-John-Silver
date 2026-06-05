"""First-class proactive public web-information watches.

Watches are durable, opt-in contracts for recurring public information checks.
They reuse WebResearchService and CategoryWebResearchService for evidence; they
never turn search snippets into facts, mutate category items, or queue downloads.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from loguru import logger

from src.core.models import (
    CategoryWebResearchInput,
    WebEvidenceBundle,
    WebInformationWatch,
    WebInformationWatchEvent,
    WebResearchBudget,
    WebResearchRequest,
    WebSearchConfig,
)
from src.search.web.category_research import CategoryWebResearchService
from src.search.web.research import WebResearchService
from src.search.web.research_guidance import WebResearchPromptGuidance
from src.ai.task_prompt_guidance import TaskPromptGuidance


class WebInformationWatchPromptBuilder:
    """Build bounded scheduled prompts for web-information watch runs."""

    @staticmethod
    def scheduled_prompt(watch: WebInformationWatch) -> str:
        """Return a scheduled assistant prompt that runs and evaluates a watch."""
        queue_rule = (
            "You may continue with media search/queue tools only if the original watch explicitly allows download queueing "
            "and the current evidence plus category/tool results prove the requested units are released and available."
            if watch.allow_download_queueing
            else "Never queue downloads from this scheduled information watch."
        )
        category_hint = (
            f" category_id={watch.category_id!r} item_id={watch.item_id!r}"
            if watch.category_id or watch.item_id
            else ""
        )
        return (
            f"Run web information watch {watch.id!r}.{category_hint}\n"
            f"Objective: {watch.objective}\n"
            f"{TaskPromptGuidance.scheduled_task_context('condition_check')}\n"
            "First call run_web_information_watch with this watch_id. Then evaluate the returned evidence. "
            "Notify the user only if there is credible new evidence, meaningful novelty, a conflict needing attention, or an explicit report condition. "
            "Use fetched evidence/provenance, not raw snippets, when stating facts. "
            "For old/undated/degraded evidence, lower confidence and avoid negative claims. "
            f"{queue_rule}"
        )


class WebInformationWatchService:
    """Create and execute bounded public web-information watches."""

    def __init__(
        self,
        *,
        repository: Any,
        config: WebSearchConfig | None = None,
        web_reader: Any = None,
        category_registry: Any = None,
        llm_client: Any = None,
    ) -> None:
        self._repository = repository
        self._config = config or WebSearchConfig()
        self._web_reader = web_reader
        self._category_registry = category_registry
        self._llm_client = llm_client

    async def create_watch(
        self,
        *,
        title: str,
        objective: str,
        query: str = "",
        intent: str = "general_research",
        owner_type: str = "user_task",
        category_id: str = "",
        item_id: str = "",
        item_name: str = "",
        language: str = "auto",
        cadence_minutes: int = 10080,
        notify_only_if_meaningful: bool = True,
        llm_evaluation_required: bool = True,
        allow_download_queueing: bool = False,
        query_plan: dict[str, Any] | None = None,
        user_feedback: dict[str, Any] | None = None,
        delay_minutes: int | None = None,
    ) -> WebInformationWatch:
        """Create a durable watch row without scheduling side effects."""
        if not self._repository:
            raise RuntimeError("Web research repository is not configured")
        now = datetime.now(timezone.utc)
        first_delay = delay_minutes if delay_minutes is not None else max(1, int(cadence_minutes or 10080))
        watch = WebInformationWatch(
            id=self._new_watch_id(title=title, objective=objective, query=query, category_id=category_id, item_id=item_id),
            owner_type=owner_type if owner_type in {"user_task", "category_item", "system_suggestion"} else "user_task",
            title=title or objective or query or "Web information watch",
            objective=objective or query or title,
            query=query,
            intent=intent,
            category_id=category_id,
            item_id=item_id,
            item_name=item_name,
            language=language or self._config.default_language,
            cadence_minutes=cadence_minutes,
            enabled=True,
            notify_only_if_meaningful=notify_only_if_meaningful,
            llm_evaluation_required=llm_evaluation_required,
            allow_download_queueing=allow_download_queueing,
            query_plan=query_plan or {},
            user_feedback=user_feedback or {},
            next_run_at=now + timedelta(minutes=max(1, int(first_delay or 1))),
            created_at=now,
            updated_at=now,
        )
        logger.info(
            "WebInformationWatchService: creating watch id={} owner={} category={} item={} intent={} cadence={} allow_queueing={}",
            watch.id,
            watch.owner_type,
            watch.category_id or "none",
            watch.item_id or watch.item_name or "none",
            watch.intent,
            watch.cadence_minutes,
            watch.allow_download_queueing,
        )
        row = await self._repository.upsert_information_watch(watch)
        return self._watch_from_row(row)

    async def run_watch(self, watch_id: str) -> dict[str, Any]:
        """Execute one watch immediately and persist an event."""
        if not self._repository:
            return {"ok": False, "error": "Web research repository is not configured."}
        row = await self._repository.get_information_watch(watch_id)
        if not row:
            return {"ok": False, "error": f"Web information watch '{watch_id}' not found."}
        watch = self._watch_from_row(row)
        if not watch.enabled:
            return {"ok": False, "watch": watch.model_dump(mode="json"), "error": "Watch is disabled."}
        logger.info(
            "WebInformationWatchService: running watch id={} owner={} category={} item={} intent={}",
            watch.id,
            watch.owner_type,
            watch.category_id or "none",
            watch.item_id or watch.item_name or "none",
            watch.intent,
        )
        now = datetime.now(timezone.utc)
        try:
            result_payload = await self._collect_watch_evidence(watch)
            signature = self._evidence_signature(result_payload)
            previous = watch.last_evidence_signature
            event_type = "new_evidence" if signature and signature != previous else "no_change"
            notification_recommended = event_type == "new_evidence" or bool(result_payload.get("warnings"))
            summary = self._event_summary(watch, result_payload, event_type)
            event = WebInformationWatchEvent(
                watch_id=watch.id,
                status="completed",
                summary=summary,
                event_type=event_type,
                evidence_signature=signature,
                source_evidence_ids=self._source_evidence_ids(result_payload),
                query_log_ids=[int(v) for v in result_payload.get("query_log_ids", []) if v],
                notification_recommended=notification_recommended,
                llm_review_required=watch.llm_evaluation_required,
                payload=result_payload,
                created_at=now,
            )
            event_id = await self._repository.add_information_watch_event(event)
            await self._repository.update_information_watch_after_run(
                watch.id,
                status=event.event_type,
                last_event_id=event_id,
                evidence_signature=signature,
                last_run_at=now.isoformat(),
                next_run_at=(now + timedelta(minutes=watch.cadence_minutes)).isoformat(),
                error="",
            )
            logger.info(
                "WebInformationWatchService: watch run completed id={} event_id={} event_type={} notification_recommended={} evidence_signature={}",
                watch.id,
                event_id,
                event.event_type,
                event.notification_recommended,
                signature[:16] if signature else "none",
            )
            updated = await self._repository.get_information_watch(watch.id)
            return {
                "ok": True,
                "watch": updated or watch.model_dump(mode="json"),
                "event": {**event.model_dump(mode="json"), "id": event_id},
                "notification_guidance": self._notification_guidance(watch, event),
                "download_queueing_allowed": bool(watch.allow_download_queueing),
                "warning": (
                    "This watch collected public evidence only. It did not mutate category items or queue downloads. "
                    "An LLM/user decision must use category tools before any download action."
                ),
            }
        except Exception as exc:
            logger.exception("WebInformationWatchService: watch run failed id={} error={}", watch.id, exc)
            event = WebInformationWatchEvent(
                watch_id=watch.id,
                status="failed",
                summary=f"Watch failed: {exc}",
                event_type="error",
                error=str(exc),
                notification_recommended=True,
                llm_review_required=False,
                created_at=now,
            )
            event_id = await self._repository.add_information_watch_event(event)
            await self._repository.update_information_watch_after_run(
                watch.id,
                status="error",
                last_event_id=event_id,
                evidence_signature=watch.last_evidence_signature,
                last_run_at=now.isoformat(),
                next_run_at=(now + timedelta(minutes=max(60, min(watch.cadence_minutes, 1440)))).isoformat(),
                error=str(exc),
            )
            return {"ok": False, "watch": watch.model_dump(mode="json"), "event_id": event_id, "error": str(exc)}

    async def list_watches(self, *, enabled_only: bool = False, category_id: str = "", item_id: str = "", limit: int = 100) -> list[dict[str, Any]]:
        """List watches through the repository."""
        if not self._repository:
            return []
        return await self._repository.list_information_watches(
            enabled_only=enabled_only,
            category_id=category_id,
            item_id=item_id,
            limit=limit,
        )

    async def disable_watch(self, watch_id: str, *, reason: str = "user_disabled") -> dict[str, Any] | None:
        """Disable one watch."""
        if not self._repository:
            return None
        logger.info("WebInformationWatchService: disabling watch id={} reason={}", watch_id, reason)
        return await self._repository.disable_information_watch(watch_id, reason=reason)

    async def _collect_watch_evidence(self, watch: WebInformationWatch) -> dict[str, Any]:
        if watch.category_id and self._category_registry:
            return await self._collect_category_watch_evidence(watch)
        return await self._collect_generic_watch_evidence(watch)

    async def _collect_category_watch_evidence(self, watch: WebInformationWatch) -> dict[str, Any]:
        research_input = CategoryWebResearchInput(
            category_id=watch.category_id,
            item_id=watch.item_id or watch.item_name,
            item_name=watch.item_name or watch.item_id,
            intent=watch.intent,
            language=watch.language or self._config.default_language,
            context={
                "watch_id": watch.id,
                "objective": watch.objective,
                "query": watch.query,
                "query_plan": watch.query_plan,
                "allow_download_queueing": watch.allow_download_queueing,
            },
        )
        result = await CategoryWebResearchService(
            category_registry=self._category_registry,
            config=self._config,
            web_reader=self._web_reader,
            repository=self._repository,
            llm_client=self._llm_client,
        ).research(research_input)
        payload = result.model_dump(mode="json")
        payload["mode"] = "category_web_research"
        payload["query_log_ids"] = result.bundle.query_log_ids
        payload["warnings"] = list(result.warnings or []) + list(result.bundle.warnings or [])
        return payload

    async def _collect_generic_watch_evidence(self, watch: WebInformationWatch) -> dict[str, Any]:
        queries = self._watch_queries(watch)
        request = WebResearchRequest(
            query=queries[0],
            additional_queries=queries[1:],
            intent=watch.intent,
            category_id=watch.category_id,
            item_id=watch.item_id,
            item_name=watch.item_name,
            language=watch.language or self._config.default_language,
            categories=self._watch_categories(watch),
            time_range=str(watch.query_plan.get("time_range") or "month"),
            max_results=int(watch.query_plan.get("max_results") or self._config.max_results or 5),
            budget=WebResearchBudget(
                max_searches=min(len(queries), 4),
                max_pages_per_search=1,
                max_urls_to_fetch=int(watch.query_plan.get("max_urls_to_fetch") or 6),
                require_page_extraction_before_facts=True,
            ),
        )
        bundle: WebEvidenceBundle = await WebResearchService(
            self._config,
            web_reader=self._web_reader,
            repository=self._repository,
        ).collect_evidence(request)
        payload = bundle.model_dump(mode="json")
        payload["mode"] = "web_research"
        payload["query_log_ids"] = bundle.query_log_ids
        return payload

    @staticmethod
    def _watch_from_row(row: dict[str, Any]) -> WebInformationWatch:
        return WebInformationWatch(
            id=str(row.get("id") or ""),
            owner_type=str(row.get("owner_type") or "user_task"),
            title=str(row.get("title") or ""),
            objective=str(row.get("objective") or ""),
            query=str(row.get("query") or ""),
            intent=str(row.get("intent") or "general_research"),
            category_id=str(row.get("category_id") or ""),
            item_id=str(row.get("item_id") or ""),
            item_name=str(row.get("item_name") or ""),
            language=str(row.get("language") or "auto"),
            cadence_minutes=int(row.get("cadence_minutes") or 10080),
            enabled=bool(row.get("enabled", True)),
            notify_only_if_meaningful=bool(row.get("notify_only_if_meaningful", True)),
            llm_evaluation_required=bool(row.get("llm_evaluation_required", True)),
            allow_download_queueing=bool(row.get("allow_download_queueing", False)),
            query_plan=row.get("query_plan") if isinstance(row.get("query_plan"), dict) else {},
            user_feedback=row.get("user_feedback") if isinstance(row.get("user_feedback"), dict) else {},
            last_run_at=WebInformationWatchService._parse_datetime(row.get("last_run_at")),
            next_run_at=WebInformationWatchService._parse_datetime(row.get("next_run_at")),
            last_event_id=row.get("last_event_id"),
            last_evidence_signature=str(row.get("last_evidence_signature") or ""),
            last_status=str(row.get("last_status") or "never_run"),
            last_error=str(row.get("last_error") or ""),
            created_at=WebInformationWatchService._parse_datetime(row.get("created_at")) or datetime.now(timezone.utc),
            updated_at=WebInformationWatchService._parse_datetime(row.get("updated_at")) or datetime.now(timezone.utc),
        )

    @staticmethod
    def _parse_datetime(value: Any) -> datetime | None:
        if not value:
            return None
        if isinstance(value, datetime):
            return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)

    @staticmethod
    def _new_watch_id(*, title: str, objective: str, query: str, category_id: str, item_id: str) -> str:
        seed = f"{uuid.uuid4()}:{title}:{objective}:{query}:{category_id}:{item_id}"
        return "wiw_" + hashlib.sha256(seed.encode("utf-8", errors="ignore")).hexdigest()[:16]

    @staticmethod
    def _watch_queries(watch: WebInformationWatch) -> list[str]:
        raw_queries = watch.query_plan.get("queries") if isinstance(watch.query_plan, dict) else None
        values = raw_queries if isinstance(raw_queries, list) else []
        queries = [str(v).strip() for v in values if str(v).strip()]
        if watch.query:
            queries.insert(0, watch.query)
        if not queries:
            queries.append(watch.objective or watch.title)
        deduped: list[str] = []
        seen: set[str] = set()
        for query in queries:
            key = query.casefold()
            if key in seen:
                continue
            seen.add(key)
            deduped.append(query)
        return deduped[:4]

    @staticmethod
    def _watch_categories(watch: WebInformationWatch) -> list[str]:
        categories = watch.query_plan.get("categories") if isinstance(watch.query_plan, dict) else None
        if isinstance(categories, list):
            cleaned = [str(value).strip() for value in categories if str(value).strip()]
            if cleaned:
                return cleaned
        if any(token in watch.intent for token in ("news", "rumor", "rumour", "patch")):
            return ["news", "general"]
        return ["general"]

    @staticmethod
    def _source_evidence_ids(payload: dict[str, Any]) -> list[int]:
        ids: list[int] = []
        bundle = payload.get("bundle") if isinstance(payload.get("bundle"), dict) else payload
        for source in bundle.get("sources", []) if isinstance(bundle, dict) else []:
            if isinstance(source, dict) and source.get("evidence_id"):
                ids.append(int(source["evidence_id"]))
        return sorted(set(ids))

    @staticmethod
    def _evidence_signature(payload: dict[str, Any]) -> str:
        rows: list[str] = []
        bundle = payload.get("bundle") if isinstance(payload.get("bundle"), dict) else payload
        for source in bundle.get("sources", []) if isinstance(bundle, dict) else []:
            if not isinstance(source, dict):
                continue
            status = str(source.get("fetch_status") or "")
            if status and status.startswith("not_fetched"):
                continue
            rows.append("|".join([
                str(source.get("canonical_url") or source.get("url") or ""),
                str(source.get("title") or ""),
                str(source.get("published_at") or ""),
                str(source.get("source_kind") or ""),
            ]))
        facts = payload.get("interpretation", {}).get("facts", []) if isinstance(payload.get("interpretation"), dict) else []
        for fact in facts:
            if isinstance(fact, dict):
                rows.append(json.dumps({"fact_type": fact.get("fact_type"), "value": fact.get("value")}, sort_keys=True, default=str))
        digest_input = "\n".join(sorted(row for row in rows if row.strip()))
        return hashlib.sha256(digest_input.encode("utf-8", errors="ignore")).hexdigest() if digest_input else ""

    @staticmethod
    def _event_summary(watch: WebInformationWatch, payload: dict[str, Any], event_type: str) -> str:
        if event_type == "no_change":
            return f"No new fetched public evidence was found for: {watch.title}."
        interpretation = payload.get("interpretation") if isinstance(payload.get("interpretation"), dict) else {}
        if interpretation.get("summary"):
            return str(interpretation["summary"])
        bundle = payload.get("bundle") if isinstance(payload.get("bundle"), dict) else payload
        evidence_count = len(bundle.get("evidence", [])) if isinstance(bundle, dict) else 0
        source_count = len(bundle.get("sources", [])) if isinstance(bundle, dict) else 0
        return f"Found changed public evidence for {watch.title}: {source_count} candidate sources, {evidence_count} fetched evidence items."

    @staticmethod
    def _notification_guidance(watch: WebInformationWatch, event: WebInformationWatchEvent) -> str:
        if not event.notification_recommended and watch.notify_only_if_meaningful:
            return "No notification is recommended. For scheduled assistant runs, reply exactly LJS_NO_NOTIFICATION."
        return "A notification may be useful after LLM review of the fetched evidence and novelty."
