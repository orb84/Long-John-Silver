"""Category-owned public web research orchestration."""

from __future__ import annotations

from typing import Any

from loguru import logger

from src.core.models import (
    CategoryResearchInterpretation,
    CategoryWebResearchInput,
    CategoryWebResearchResult,
    WebEvidenceBundle,
    WebResearchBudget,
    WebResearchRequest,
    WebSearchConfig,
)
from src.search.web.llm_category_research import LLMCategoryWebResearchPlanner
from src.search.web.research import WebResearchService


class CategoryWebResearchService:
    """Run category-authored public web research plans.

    This service is the handoff point between generic web evidence collection
    and category-owned interpretation. It never branches on category ids and it
    never mutates category items directly.
    """

    def __init__(
        self,
        *,
        category_registry: Any,
        config: WebSearchConfig | None = None,
        web_reader: Any = None,
        repository: Any = None,
        llm_client: Any = None,
    ) -> None:
        self._category_registry = category_registry
        self._config = config or WebSearchConfig()
        self._web_reader = web_reader
        self._repository = repository
        self._llm_client = llm_client

    async def research(self, research_input: CategoryWebResearchInput) -> CategoryWebResearchResult:
        """Execute a category web-research plan and persist interpreted provenance."""
        logger.info(
            "CategoryWebResearchService: research started category={} item={} intent={} item_name='{}'",
            research_input.category_id,
            research_input.item_id or "none",
            research_input.intent,
            research_input.item_name,
        )
        category = self._resolve_category(research_input.category_id)
        if not category:
            logger.warning("CategoryWebResearchService: unknown category {}", research_input.category_id)
            return CategoryWebResearchResult(
                ok=False,
                category_id=research_input.category_id,
                item_id=research_input.item_id,
                intent=research_input.intent,
                warnings=[f"Unknown category '{research_input.category_id}'."],
            )

        fallback_plan = category.build_web_research_plan(research_input)
        llm_plan = await LLMCategoryWebResearchPlanner(self._llm_client).build_plan(
            category=category,
            research_input=research_input,
            fallback_plan=fallback_plan,
        )
        plan = llm_plan or fallback_plan
        plan_origin = "llm" if llm_plan is not None else "category_fallback"
        logger.info(
            "CategoryWebResearchService: category plan origin={} searches={} max_searches={} require_fetch={} notes={}",
            plan_origin,
            len(getattr(plan, "searches", []) or []),
            getattr(plan, "max_searches", None),
            getattr(plan, "require_page_extraction_before_facts", None),
            len(getattr(plan, "notes", []) or []),
        )
        aggregate = WebEvidenceBundle(
            topic=research_input.item_name or research_input.item_id or research_input.category_id,
            intent=research_input.intent,
            facts_authoritative=False,
        )
        warnings: list[str] = []
        if not getattr(plan, "searches", None):
            warnings.extend(list(getattr(plan, "notes", []) or []))
            logger.info("CategoryWebResearchService: category returned no searches category={} intent={}", research_input.category_id, research_input.intent)
            interpretation = await category.interpret_web_evidence(aggregate, research_input)
            return CategoryWebResearchResult(
                ok=False,
                category_id=research_input.category_id,
                item_id=research_input.item_id,
                intent=research_input.intent,
                plan=plan,
                bundle=aggregate,
                interpretation=interpretation,
                warnings=warnings or ["The category returned no web-research searches."],
            )

        collector = WebResearchService(
            self._config,
            web_reader=self._web_reader,
            repository=self._repository,
        )
        for search in list(plan.searches)[: int(plan.max_searches or 1)]:
            logger.info(
                "CategoryWebResearchService: executing category search intent={} query='{}' categories={} time_range={}",
                search.intent or research_input.intent,
                self._query_preview(search.query),
                search.categories,
                search.time_range or "none",
            )
            request = WebResearchRequest(
                query=search.query,
                intent=search.intent or research_input.intent,
                category_id=research_input.category_id,
                item_id=research_input.item_id,
                item_name=research_input.item_name,
                categories=search.categories or self._config.default_categories,
                language=search.language or research_input.language or self._config.default_language,
                time_range=search.time_range,
                max_results=search.max_results,
                budget=WebResearchBudget(
                    max_searches=1,
                    max_pages_per_search=1,
                    max_urls_to_fetch=search.max_urls_to_fetch,
                    require_page_extraction_before_facts=bool(plan.require_page_extraction_before_facts),
                ),
            )
            bundle = await collector.collect_evidence(request)
            self._merge_bundle(aggregate, bundle)

        interpretation = await category.interpret_web_evidence(aggregate, research_input)
        persisted_fact_ids = await self._persist_interpreted_facts(interpretation)
        ok = bool(aggregate.ok or interpretation.facts or persisted_fact_ids)
        logger.info(
            "CategoryWebResearchService: research finished ok={} category={} item={} evidence={} facts={} persisted_facts={} warnings={}",
            ok,
            research_input.category_id,
            research_input.item_id or "none",
            len(aggregate.evidence),
            len(interpretation.facts),
            len(persisted_fact_ids),
            len(warnings) + len(aggregate.warnings),
        )
        warnings.extend(aggregate.warnings)
        return CategoryWebResearchResult(
            ok=ok,
            category_id=research_input.category_id,
            item_id=research_input.item_id,
            intent=research_input.intent,
            plan=plan,
            bundle=aggregate,
            interpretation=interpretation,
            persisted_fact_ids=persisted_fact_ids,
            warnings=warnings,
        )

    def _resolve_category(self, category_id: str) -> Any:
        if not self._category_registry:
            return None
        getter = getattr(self._category_registry, "get", None)
        if callable(getter):
            return getter(category_id)
        return None

    @staticmethod
    def _merge_bundle(target: WebEvidenceBundle, source: WebEvidenceBundle) -> None:
        target.provider = target.provider or source.provider
        target.query_log_ids.extend([value for value in source.query_log_ids if value not in target.query_log_ids])
        target.sources.extend(source.sources)
        target.evidence.extend(source.evidence)
        target.warnings.extend(source.warnings)
        target.unresolved_questions.extend(source.unresolved_questions)
        target.ok = target.ok or source.ok

    async def _persist_interpreted_facts(self, interpretation: CategoryResearchInterpretation) -> list[int]:
        if not self._repository:
            return []
        persisted: list[int] = []
        for fact in interpretation.facts:
            if not fact.fact_type:
                continue
            try:
                fact_id = await self._repository.add_fact_provenance(
                    category_id=interpretation.category_id,
                    item_id=interpretation.item_id,
                    fact_type=fact.fact_type,
                    value={**fact.value, "authoritative": bool(fact.authoritative)},
                    source_evidence_ids=fact.source_evidence_ids,
                    confidence=fact.confidence,
                    decided_by=fact.decided_by,
                )
                persisted.append(fact_id)
                logger.info("CategoryWebResearchService: persisted fact type={} id={} confidence={}", fact.fact_type, fact_id, fact.confidence)
            except Exception as exc:
                logger.debug(f"Failed to persist category web-research fact {fact.fact_type}: {exc}")
        return persisted

    @staticmethod
    def _query_preview(query: str, *, max_chars: int = 120) -> str:
        cleaned = " ".join(str(query or "").split())
        if len(cleaned) <= max_chars:
            return cleaned
        return cleaned[: max_chars - 1].rstrip() + "…"
