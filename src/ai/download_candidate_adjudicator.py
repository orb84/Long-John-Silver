"""LLM adjudication for media download candidate workspaces.

Deterministic category hooks build a bounded candidate workspace and annotate
hard safety facts.  They must not be the final semantic judge for messy torrent
release names.  This helper asks the task LLM to compare the user's request,
category context, and compact candidate rows, then returns review metadata and
an optional recommended ordering.  Execution still validates candidate IDs,
queueability, and explicit side-effect rules.
"""

from __future__ import annotations

import json
from typing import Any

from loguru import logger

from src.utils.json_parser import LLMResponseParser


class DownloadCandidateAdjudicator:
    """Ask the task LLM to review a compact torrent candidate workspace.

    The adjudicator does not queue anything and does not discard candidates. It
    produces recommendations and reasoning that the normal assistant loop can
    use, while deterministic code continues to enforce hard queue rules.

    Long candidate lists are reviewed in context-budgeted chunks. Each chunk is
    judged by the task LLM, then a final tournament pass compares the chunk
    winners. This prevents small models from receiving an overlong prompt and
    prevents silent first-N truncation from hiding good candidates near the end
    of a result set.
    """

    _TASK_NAME = "torrent_ranker"
    _DEFAULT_CONTEXT_TOKENS = 8192
    _MIN_CONTEXT_TOKENS = 4096
    _OUTPUT_TOKEN_BUDGET = 550
    _PROMPT_SAFETY_TOKENS = 650
    _CHARS_PER_TOKEN_ESTIMATE = 4
    _MAX_CATEGORY_GUIDANCE_CHARS = 6000
    _MAX_RECOMMENDED = 8
    _MAX_REJECTED = 20

    def __init__(self, llm_client: Any | None = None) -> None:
        self._llm_client = llm_client

    @property
    def available(self) -> bool:
        """Return whether an LLM client is available for candidate review."""
        return self._llm_client is not None

    async def review(
        self,
        *,
        user_prompt: str | None,
        tool_arguments: dict[str, Any],
        search_result: dict[str, Any],
        candidates: list[dict[str, Any]],
        category_guidance: str | None = None,
    ) -> dict[str, Any] | None:
        """Return LLM candidate review metadata, or ``None`` if unavailable."""
        if not candidates:
            logger.info("DownloadCandidateAdjudicator skipped: no torrent candidates to review")
            return None
        if not self._llm_client:
            logger.warning("DownloadCandidateAdjudicator skipped: task LLM client is not available")
            return None
        try:
            context_limit = await self._resolve_context_limit()
            rows = [self._compact_candidate(row) for row in candidates]
            request = self._build_request(
                user_prompt=user_prompt,
                tool_arguments=tool_arguments,
                search_result=search_result,
            )
            chunks = self._chunk_rows(
                request=request,
                rows=rows,
                category_guidance=category_guidance,
                context_limit_tokens=context_limit,
            )
            if len(chunks) <= 1:
                prompt = self._build_prompt(
                    request=request,
                    rows=chunks[0] if chunks else rows,
                    category_guidance=category_guidance,
                    review_stage="single_pass",
                    chunk_index=1,
                    chunk_count=1,
                    context_limit_tokens=context_limit,
                )
                payload = await self._call_llm(prompt)
                review = self._normalize_payload(payload, candidates)
                review["candidate_review_mode"] = "single_pass"
                review["candidate_count_reviewed"] = len(candidates)
                review["context_limit_tokens"] = context_limit
                return review

            chunk_reviews: list[dict[str, Any]] = []
            finalist_ids: list[str] = []
            rejected_ids: list[str] = []
            row_by_id = {str(row.get("candidate_id") or ""): row for row in rows if row.get("candidate_id")}
            candidate_by_id = {str(row.get("candidate_id") or ""): row for row in candidates if row.get("candidate_id")}

            for idx, chunk in enumerate(chunks, 1):
                prompt = self._build_prompt(
                    request=request,
                    rows=chunk,
                    category_guidance=category_guidance,
                    review_stage="chunk_review",
                    chunk_index=idx,
                    chunk_count=len(chunks),
                    context_limit_tokens=context_limit,
                )
                payload = await self._call_llm(prompt)
                chunk_candidate_dicts = [candidate_by_id.get(str(row.get("candidate_id") or ""), row) for row in chunk]
                review = self._normalize_payload(payload, chunk_candidate_dicts)
                chunk_reviews.append(review)
                for cid in review.get("recommended_candidate_ids") or []:
                    if cid not in finalist_ids:
                        finalist_ids.append(cid)
                for cid in review.get("reject_candidate_ids") or []:
                    if cid not in rejected_ids:
                        rejected_ids.append(cid)
                if not review.get("recommended_candidate_ids"):
                    fallback_id = self._fallback_chunk_candidate_id(chunk)
                    if fallback_id and fallback_id not in finalist_ids:
                        finalist_ids.append(fallback_id)

            if not finalist_ids:
                return {
                    "reviewed_by": "llm_torrent_candidate_adjudicator",
                    "candidate_review_mode": "chunked_no_finalists",
                    "candidate_count_reviewed": len(candidates),
                    "chunk_count": len(chunks),
                    "context_limit_tokens": context_limit,
                    "recommended_candidate_ids": [],
                    "reject_candidate_ids": rejected_ids[: self._MAX_REJECTED],
                    "confidence": "low",
                    "should_queue_now": False,
                    "needs_user_choice": True,
                    "reason": "The candidate review did not find a clear matching torrent candidate.",
                    "answer_hint": "Tell the user that the candidate set was reviewed but no clear safe match was identified.",
                }

            initial_finalist_count = len(finalist_ids)
            final_review, tournament_rounds = await self._run_final_tournament(
                request=request,
                finalist_ids=finalist_ids,
                rejected_ids=rejected_ids,
                row_by_id=row_by_id,
                candidate_by_id=candidate_by_id,
                category_guidance=category_guidance,
                context_limit_tokens=context_limit,
            )
            final_review["candidate_review_mode"] = "chunked_tournament"
            final_review["candidate_count_reviewed"] = len(candidates)
            final_review["chunk_count"] = len(chunks)
            final_review["finalist_count"] = initial_finalist_count
            final_review["tournament_round_count"] = tournament_rounds
            final_review["finalist_omitted_due_to_context"] = 0
            final_review["context_limit_tokens"] = context_limit
            logger.info(
                "DownloadCandidateAdjudicator chunked review: candidates={} chunks={} finalists={} tournament_rounds={} context_limit={}",
                len(candidates), len(chunks), initial_finalist_count, tournament_rounds, context_limit,
            )
            return final_review
        except Exception as exc:
            logger.warning("DownloadCandidateAdjudicator failed: {}", exc)
            return None


    async def _run_final_tournament(
        self,
        *,
        request: dict[str, Any],
        finalist_ids: list[str],
        rejected_ids: list[str],
        row_by_id: dict[str, dict[str, Any]],
        candidate_by_id: dict[str, dict[str, Any]],
        category_guidance: str | None,
        context_limit_tokens: int,
    ) -> tuple[dict[str, Any], int]:
        """Compare all chunk winners without dropping tail finalists.

        Earlier versions reviewed only the first context-safe finalist chunk in
        the final pass and reported how many finalists were omitted.  That is
        precisely the wrong failure mode for torrent search: the best season
        pack can appear late in the provider result set.  This method runs
        tournament rounds until all surviving finalists fit in one prompt.
        """
        current_ids = [cid for cid in self._merge_unique(finalist_ids) if cid in row_by_id]
        all_rejected = list(rejected_ids or [])
        rounds = 0
        if not current_ids:
            return {
                "reviewed_by": "llm_torrent_candidate_adjudicator",
                "recommended_candidate_ids": [],
                "reject_candidate_ids": all_rejected[: self._MAX_REJECTED],
                "confidence": "low",
                "should_queue_now": False,
                "needs_user_choice": True,
                "reason": "No finalist candidate survived chunk review.",
                "answer_hint": "Tell the user that no clear safe candidate was found.",
            }, rounds

        while True:
            finalist_rows = [row_by_id[cid] for cid in current_ids if cid in row_by_id]
            finalist_chunks = self._chunk_rows(
                request=request,
                rows=finalist_rows,
                category_guidance=category_guidance,
                context_limit_tokens=context_limit_tokens,
            )
            if len(finalist_chunks) <= 1:
                rows = finalist_chunks[0] if finalist_chunks else finalist_rows
                prompt = self._build_prompt(
                    request=request,
                    rows=rows,
                    category_guidance=category_guidance,
                    review_stage="final_tournament",
                    chunk_index=1,
                    chunk_count=1,
                    context_limit_tokens=context_limit_tokens,
                    extra_instruction=(
                        "These candidates are winners from earlier chunk reviews. Pick the best overall match. "
                        "If category guidance identifies a strong complete-unit bundle/range candidate, compare it carefully against scattered lower-scope alternatives."
                    ),
                )
                payload = await self._call_llm(prompt)
                final_candidates = [candidate_by_id.get(str(row.get("candidate_id") or ""), row) for row in rows]
                review = self._normalize_payload(payload, final_candidates)
                if not review.get("recommended_candidate_ids"):
                    review["recommended_candidate_ids"] = current_ids[: self._MAX_RECOMMENDED]
                review["reject_candidate_ids"] = self._merge_unique(
                    list(review.get("reject_candidate_ids") or []), all_rejected,
                )[: self._MAX_REJECTED]
                return review, rounds + 1

            rounds += 1
            next_ids: list[str] = []
            before_count = len(current_ids)
            for idx, chunk in enumerate(finalist_chunks, 1):
                prompt = self._build_prompt(
                    request=request,
                    rows=chunk,
                    category_guidance=category_guidance,
                    review_stage=f"final_tournament_round_{rounds}",
                    chunk_index=idx,
                    chunk_count=len(finalist_chunks),
                    context_limit_tokens=context_limit_tokens,
                    extra_instruction=(
                        "This is a finalist-reduction round. Recommend at most three candidate_id values from this chunk. "
                        "Do not let a lower-scope result beat a valid category-approved bundle/range candidate when the user requested the broader unit."
                    ),
                )
                payload = await self._call_llm(prompt)
                chunk_candidates = [candidate_by_id.get(str(row.get("candidate_id") or ""), row) for row in chunk]
                review = self._normalize_payload(payload, chunk_candidates)
                for cid in review.get("recommended_candidate_ids") or []:
                    if cid not in next_ids:
                        next_ids.append(cid)
                for cid in review.get("reject_candidate_ids") or []:
                    if cid not in all_rejected:
                        all_rejected.append(cid)
                if not review.get("recommended_candidate_ids"):
                    fallback_id = self._fallback_chunk_candidate_id(chunk)
                    if fallback_id and fallback_id not in next_ids:
                        next_ids.append(fallback_id)

            if not next_ids:
                return {
                    "reviewed_by": "llm_torrent_candidate_adjudicator",
                    "recommended_candidate_ids": [],
                    "reject_candidate_ids": all_rejected[: self._MAX_REJECTED],
                    "confidence": "low",
                    "should_queue_now": False,
                    "needs_user_choice": True,
                    "reason": "Finalist tournament did not identify a clear matching torrent candidate.",
                    "answer_hint": "Tell the user that candidates were reviewed but no safe match was identified.",
                }, rounds

            current_ids = self._merge_unique(next_ids)
            # Defensive escape: if a tiny-context model keeps returning too many
            # winners, keep a bounded but non-empty set instead of looping
            # forever.  This still considers every finalist in at least one LLM
            # tournament prompt.
            if rounds >= 6 or len(current_ids) >= before_count:
                current_ids = current_ids[: self._MAX_RECOMMENDED]

    async def _call_llm(self, prompt: str) -> dict[str, Any]:
        response = await self._llm_client.completion(
            task=self._TASK_NAME,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=450,
            temperature=0.0,
        )
        raw = LLMResponseParser.safe_extract_content(response)
        return LLMResponseParser.extract_json_resilient(raw)

    async def _resolve_context_limit(self) -> int:
        client = self._llm_client
        if not client:
            return self._DEFAULT_CONTEXT_TOKENS
        try:
            ensure = getattr(client, "ensure_model_metadata_for_task", None)
            if callable(ensure):
                await ensure(self._TASK_NAME)
        except Exception as exc:
            logger.debug("DownloadCandidateAdjudicator context metadata warm-up skipped: {}", exc)
        for attr in ("endpoint_context_limit_for_task",):
            try:
                getter = getattr(client, attr, None)
                if callable(getter):
                    limit = getter(self._TASK_NAME)
                    if limit:
                        return max(self._MIN_CONTEXT_TOKENS, int(limit))
            except Exception:
                pass
        try:
            resolver = getattr(client, "resolve_task", None)
            if callable(resolver):
                resolved = resolver(self._TASK_NAME)
                limit = getattr(resolved, "context_limit", None)
                if limit:
                    return max(self._MIN_CONTEXT_TOKENS, int(limit))
        except Exception:
            pass
        return self._DEFAULT_CONTEXT_TOKENS

    def _chunk_rows(
        self,
        *,
        request: dict[str, Any],
        rows: list[dict[str, Any]],
        category_guidance: str | None,
        context_limit_tokens: int,
    ) -> list[list[dict[str, Any]]]:
        if not rows:
            return []
        budget = self._candidate_char_budget(
            request=request,
            category_guidance=category_guidance,
            context_limit_tokens=context_limit_tokens,
        )
        chunks: list[list[dict[str, Any]]] = []
        current: list[dict[str, Any]] = []
        current_chars = 2
        for row in rows:
            row_chars = len(json.dumps(row, ensure_ascii=False)) + 2
            if current and current_chars + row_chars > budget:
                chunks.append(current)
                current = []
                current_chars = 2
            current.append(row)
            current_chars += row_chars
        if current:
            chunks.append(current)
        return chunks

    def _candidate_char_budget(
        self,
        *,
        request: dict[str, Any],
        category_guidance: str | None,
        context_limit_tokens: int,
    ) -> int:
        usable_tokens = max(
            1200,
            int(context_limit_tokens) - self._OUTPUT_TOKEN_BUDGET - self._PROMPT_SAFETY_TOKENS,
        )
        total_chars = usable_tokens * self._CHARS_PER_TOKEN_ESTIMATE
        fixed_prompt = self._build_prompt(
            request=request,
            rows=[],
            category_guidance=category_guidance,
            review_stage="budget_probe",
            chunk_index=1,
            chunk_count=1,
            context_limit_tokens=context_limit_tokens,
        )
        return max(1200, total_chars - len(fixed_prompt))

    def _build_request(
        self,
        *,
        user_prompt: str | None,
        tool_arguments: dict[str, Any],
        search_result: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "user_prompt": user_prompt or "",
            "tool_arguments": self._safe_subset(tool_arguments, [
                "name", "category_id", "season", "episode", "language", "language_is_explicit", "search_scope",
            ]),
            "effective_search": self._safe_subset(search_result, [
                "name", "display_name", "category_id", "season", "episode", "language", "search_scope", "query",
                "quality_choice_policy",
            ]),
        }

    def _build_prompt(
        self,
        *,
        request: dict[str, Any],
        rows: list[dict[str, Any]],
        category_guidance: str | None,
        review_stage: str,
        chunk_index: int,
        chunk_count: int,
        context_limit_tokens: int,
        extra_instruction: str = "",
    ) -> str:
        schema = {
            "recommended_candidate_ids": ["candidate_id in best order"],
            "reject_candidate_ids": ["candidate_id that clearly mismatches the request"],
            "confidence": "high|medium|low",
            "should_queue_now": False,
            "needs_user_choice": True,
            "reason": "one concise sentence",
            "answer_hint": "what the assistant should tell the user",
        }
        chunk_note = ""
        if chunk_count > 1:
            chunk_note = (
                f"This is candidate chunk {chunk_index} of {chunk_count}. "
                "Review only this chunk and nominate the best candidate_id values from it. "
                "A later final pass will compare winners across chunks.\n"
            )
        return (
            "You are LJS's torrent-candidate reviewer. Return ONLY valid JSON.\n"
            "Do not search the web. Do not invent candidates. Do not queue downloads.\n"
            "Pick from the supplied candidate_id values only.\n"
            f"Review stage: {review_stage}. Context budget used by caller: {context_limit_tokens} tokens.\n"
            f"{chunk_note}"
            f"{extra_instruction}\n\n"
            "Rules:\n"
            "- Preserve the user's exact requested title, unit scope, and media language.\n"
            "- Use the owning category guidance below to interpret category-specific release names, bundle/range notation, language tags, unit coverage, and fallback strategy.\n"
            "- Treat title stopwords/articles as semantically important in the answer, but do not reject a candidate merely because another layer dropped one in a search query.\n"
            "- Respect category-owned annotations such as unit_descriptor, bundle_context, language_preference_status, request-fit fields, coverage notes, and selection warnings.\n"
            "- Reject obvious title collisions, wrong requested units, unrelated title collisions, and rows the candidate annotations mark as hard blockers.\n"
            "- If a candidate declares complete requested-unit coverage through category-owned fields, treat that coverage as authoritative unless another row contradicts it.\n"
            "- If the request/effective_search includes quality_choice_policy.requires_user_choice=true, recommend the best few option candidate_ids, set should_queue_now=false, needs_user_choice=true, and tell the assistant to present quality/size options.\n"
            "- If multiple plausible candidates trade resolution/codec/bitrate/size in materially different ways, do not collapse them into one proposal unless the user already gave that preference.\n"
            "- If only weak or ambiguous candidates exist, set should_queue_now=false and explain what to ask/inspect.\n"
            "- Seeder count matters among otherwise equivalent candidates.\n\n"
            f"Category guidance:\n{(category_guidance or '')[: self._MAX_CATEGORY_GUIDANCE_CHARS]}\n\n"
            f"Request JSON: {json.dumps(request, ensure_ascii=False, sort_keys=True)}\n"
            f"Candidates JSON: {json.dumps(rows, ensure_ascii=False)}\n"
            f"Output schema: {json.dumps(schema, ensure_ascii=False)}\n"
            "JSON:"
        )

    @staticmethod
    def _compact_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
        fields = [
            "candidate_id", "index", "title", "size", "size_bytes", "seeders", "source",
            "season", "episode", "languages", "resolution", "codec", "unit_descriptor",
            "bundle_context", "is_bundle", "bundle_scope", "pack_type", "bundle_unit_count",
            "selection_warnings", "selection_blockers", "auto_queue_allowed", "auto_queue_blocked_reason",
            "language_preference_status", "tv_request_fit", "availability_seeders",
            "per_episode_size_mb", "estimated_bitrate_kbps", "expected_episode_count", "requested_season_coverage", "coverage_note",
        ]
        return {k: candidate.get(k) for k in fields if candidate.get(k) not in (None, "", [], {})}

    @staticmethod
    def _safe_subset(payload: dict[str, Any], keys: list[str]) -> dict[str, Any]:
        return {k: payload.get(k) for k in keys if payload.get(k) not in (None, "", [], {})}

    @staticmethod
    def _normalize_payload(payload: dict[str, Any], candidates: list[dict[str, Any]]) -> dict[str, Any]:
        valid_ids = {str(c.get("candidate_id") or "") for c in candidates if c.get("candidate_id")}
        recommended = []
        for raw in payload.get("recommended_candidate_ids") or []:
            cid = str(raw or "").strip()
            if cid and cid in valid_ids and cid not in recommended:
                recommended.append(cid)
        rejected = []
        for raw in payload.get("reject_candidate_ids") or []:
            cid = str(raw or "").strip()
            if cid and cid in valid_ids and cid not in rejected:
                rejected.append(cid)
        confidence = str(payload.get("confidence") or "medium").lower()
        if confidence not in {"high", "medium", "low"}:
            confidence = "medium"
        return {
            "reviewed_by": "llm_torrent_candidate_adjudicator",
            "recommended_candidate_ids": recommended[: DownloadCandidateAdjudicator._MAX_RECOMMENDED],
            "reject_candidate_ids": rejected[: DownloadCandidateAdjudicator._MAX_REJECTED],
            "confidence": confidence,
            "should_queue_now": bool(payload.get("should_queue_now")),
            "needs_user_choice": bool(payload.get("needs_user_choice", not bool(recommended))),
            "reason": str(payload.get("reason") or "").strip()[:500],
            "answer_hint": str(payload.get("answer_hint") or "").strip()[:700],
        }

    @staticmethod
    def _fallback_chunk_candidate_id(rows: list[dict[str, Any]]) -> str:
        if not rows:
            return ""
        ranked = sorted(
            rows,
            key=lambda row: (
                not bool(row.get("is_bundle") or row.get("bundle_scope") or row.get("pack_type")),
                -DownloadCandidateAdjudicator.language_status_sort_rank(row),
                -int(row.get("seeders") or 0),
                int(row.get("index") or 9999),
            ),
        )
        return str(ranked[0].get("candidate_id") or "")

    @staticmethod
    def language_status_sort_rank(row: dict[str, Any]) -> int:
        status = str(row.get("language_preference_status") or "").lower()
        return {
            "preferred_only": 5,
            "preferred_by_title": 5,
            "unknown_acceptable": 4,
            "preferred_with_extra_audio": 3,
            "multi_language_fallback": 2,
            "not_applicable": 1,
            "mismatch": -100,
        }.get(status, 1)

    @staticmethod
    def _merge_unique(*groups: list[str]) -> list[str]:
        merged: list[str] = []
        for group in groups:
            for raw in group or []:
                value = str(raw or "").strip()
                if value and value not in merged:
                    merged.append(value)
        return merged

    @staticmethod
    def reorder_candidates(candidates: list[dict[str, Any]], review: dict[str, Any] | None) -> list[dict[str, Any]]:
        """Return candidates with LLM-recommended IDs moved to the front."""
        if not review:
            return candidates
        order = [str(cid) for cid in review.get("recommended_candidate_ids") or []]
        if not order:
            return candidates
        rank = {cid: idx for idx, cid in enumerate(order)}
        return sorted(
            candidates,
            key=lambda row: (rank.get(str(row.get("candidate_id") or ""), 10_000), int(row.get("index") or 9999)),
        )
