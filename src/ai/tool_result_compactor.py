"""Prompt-safe compaction for LLM tool results.

Tool handlers often return rich machine payloads so UI, cache, and direct
callers can inspect every field.  The chat model does not need that whole
surface on every loop iteration.  This module keeps the LLM-facing tool message
small while preserving stable IDs, queue arguments, and the decision evidence
needed for the next tool call.
"""

from __future__ import annotations

import json
from typing import Any


class ToolResultCompactor:
    """Build compact, loss-aware tool results for assistant prompts.

    The compactor never mutates the original result.  It preserves queueable
    identifiers (`result_set_id`, `candidate_id`, `candidate_ids`) and evidence
    that affects download decisions: language, resolution, size/bitrate, seeders,
    source, and category unit descriptors.  Bulky raw payloads remain available
    in result caches and logs, but are not fed back wholesale to the model.
    """

    _DEFAULT_MAX_CHARS = 6000
    _SEARCH_CANDIDATE_LIMIT = 8
    _SEARCH_PICKER_LIMIT = 60
    _SEARCH_GROUP_LIMIT = 40
    _DOWNLOAD_LIMIT = 12

    def compact_for_message(self, tool_name: str, result: Any) -> str:
        """Return a JSON/text payload suitable for a chat tool message."""
        compacted = self.compact(tool_name, result)
        if isinstance(compacted, str):
            return compacted
        try:
            return json.dumps(compacted, ensure_ascii=False, separators=(",", ":"))
        except (TypeError, ValueError):
            return self._truncate_middle(str(compacted), self._DEFAULT_MAX_CHARS, "tool result compressed")

    def compact(self, tool_name: str, result: Any) -> Any:
        """Return a compact Python object for a known tool result."""
        if tool_name == "search_media_torrents" and isinstance(result, dict):
            return self._compact_media_search(result)
        if tool_name == "search_torrents":
            return self._compact_generic_search(result)
        if tool_name == "search_soulseek" and isinstance(result, dict):
            return {
                "ok": result.get("ok"),
                "query": result.get("query"),
                "source": result.get("source"),
                "error_code": result.get("error_code"),
                "error": result.get("error"),
                "recoverable": result.get("recoverable"),
                "next_actions": result.get("next_actions"),
                "agent_instruction": result.get("agent_instruction"),
                "queueing_note": result.get("queueing_note"),
                "candidate_count": len(result.get("candidates") or []),
                "candidates": list(result.get("candidates") or [])[:12],
            }
        if tool_name == "list_downloads" and isinstance(result, (dict, list)):
            return self._compact_download_list(result)
        if tool_name == "web_research" and isinstance(result, dict):
            return self._compact_web_research(result)
        if tool_name in {"read_web_page", "browse_page", "browser_read_selected"}:
            return self._compact_textual(result, 3500, "web content compressed")
        if isinstance(result, str):
            return self._compact_textual(result, self._DEFAULT_MAX_CHARS, "tool result compressed")
        return self._compact_jsonish(result, self._DEFAULT_MAX_CHARS)

    def _compact_media_search(self, result: dict[str, Any]) -> dict[str, Any]:
        if result.get("ok") is False:
            return {
                "ok": False,
                "tool": result.get("tool"),
                "error_code": result.get("error_code"),
                "recoverable": result.get("recoverable", True),
                "error": result.get("error"),
                "next_actions": result.get("next_actions"),
                "agent_instruction": result.get("agent_instruction"),
            }
        candidates = list(result.get("candidates") or [])
        batch = result.get("batch_recommendation") if isinstance(result.get("batch_recommendation"), dict) else None
        review = result.get("llm_candidate_review") if isinstance(result.get("llm_candidate_review"), dict) else None
        keep_ids = self._recommended_candidate_ids(batch)
        keep_ids.update(self._llm_recommended_candidate_ids(review))
        selected = self._select_search_candidates(candidates, keep_ids)
        compact: dict[str, Any] = {
            "query": result.get("query"),
            "language": result.get("language"),
            "expected_episode_count": result.get("expected_episode_count"),
            "category_id": result.get("category_id"),
            "name": result.get("name"),
            "item_id": result.get("item_id"),
            "display_name": result.get("display_name"),
            "season": result.get("season"),
            "episode": result.get("episode"),
            "result_set_id": result.get("result_set_id"),
            "result_handle": result.get("result_handle"),
            "search_scope": result.get("search_scope"),
            "search_summary": result.get("search_summary"),
            "next_actions": result.get("next_actions"),
            "agent_instruction": result.get("agent_instruction"),
            "llm_next_action": result.get("llm_next_action"),
            "source_result_status": result.get("source_result_status"),
            "torrent_candidate_count": result.get("torrent_candidate_count", len(candidates)),
            "soulseek_candidate_count": result.get("soulseek_candidate_count", 0),
            "downloadable_candidate_count": result.get("downloadable_candidate_count", len(candidates)),
            "candidate_count": len(candidates),
            "candidate_count_note": "candidate_count is torrent-only for search_media_torrents; use downloadable_candidate_count and soulseek_candidate_count before concluding nothing was found.",
            "estimated_total_size_bytes": result.get("estimated_total_size_bytes"),
            "results_total_size_gb": result.get("results_total_size_gb"),
            "candidate_picker": self._compact_candidate_picker(result.get("candidate_picker")),
            "candidates": [self._compact_candidate(c, fallback_result_set_id=result.get("result_set_id")) for c in selected],
        }
        quality_policy = self._compact_quality_choice_policy(result.get("quality_choice_policy"))
        if quality_policy:
            compact["quality_choice_policy"] = quality_policy
        if result.get("llm_candidate_review_status"):
            compact["llm_candidate_review_status"] = result.get("llm_candidate_review_status")
        if result.get("recommended_candidate_id"):
            compact["recommended_candidate_id"] = result.get("recommended_candidate_id")
        compact_review = self._compact_llm_candidate_review(review)
        if compact_review:
            compact["llm_candidate_review"] = compact_review
            compact["llm_review_note"] = (
                "The candidate workspace was semantically reviewed by the task LLM. "
                "Prefer recommended_candidate_ids when they still satisfy queue/inspection constraints."
            )
        if result.get("soulseek_summary"):
            compact["soulseek_summary"] = result.get("soulseek_summary")
        companion = result.get("companion_soulseek") if isinstance(result.get("companion_soulseek"), dict) else None
        if companion and companion.get("candidate_count"):
            compact["companion_soulseek"] = {
                "status": companion.get("status"),
                "candidate_count": companion.get("candidate_count"),
                "queries": companion.get("queries"),
                "candidates": list(companion.get("candidates") or [])[:self._SEARCH_CANDIDATE_LIMIT],
                "queueing_note": companion.get("queueing_note"),
                "recommended_candidate_id": companion.get("recommended_candidate_id"),
            }
            if result.get("soulseek_candidate_picker"):
                compact["soulseek_candidate_picker"] = result.get("soulseek_candidate_picker")
        omitted = max(0, len(candidates) - len(selected))
        if omitted:
            compact["omitted_candidates_count"] = omitted
            compact["omission_note"] = "Use result_set_id/candidate_id; omitted entries remain cached for queue_download resolution."
        if result.get("llm_next_action"):
            compact["llm_next_action"] = result.get("llm_next_action")
        if batch:
            compact["batch_recommendation"] = self._compact_batch_recommendation(batch, result.get("result_set_id"))
        return compact



    def _compact_quality_choice_policy(self, policy: Any) -> dict[str, Any]:
        """Preserve quality/size choice requirements for the final chat model."""
        if not isinstance(policy, dict) or not policy.get("requires_user_choice"):
            return {}
        choices = []
        for choice in list(policy.get("choices") or [])[:8]:
            if not isinstance(choice, dict):
                continue
            keys = (
                "candidate_id", "title", "resolution", "codec", "size", "size_bytes",
                "per_episode_size", "per_episode_size_mb", "estimated_bitrate_kbps",
                "seeders", "languages", "requested_season_coverage",
            )
            choices.append({key: choice.get(key) for key in keys if choice.get(key) not in (None, "", [], {})})
        compact = {
            "requires_user_choice": True,
            "reason": policy.get("reason"),
            "tradeoff_type": policy.get("tradeoff_type"),
            "message": policy.get("message"),
            "candidate_ids": policy.get("candidate_ids"),
            "choices": choices,
            "comparison": policy.get("comparison"),
        }
        return {key: value for key, value in compact.items() if value not in (None, "", [], {})}

    def _compact_llm_candidate_review(self, review: Any) -> dict[str, Any]:
        """Keep torrent-candidate LLM adjudication visible after compaction.

        The search tool caches full candidate records and may send dozens or
        hundreds of raw rows through the adjudicator.  The final chat model still
        needs to know whether that review actually ran, which IDs it preferred,
        and why.  Without this compact review, the final model only sees a
        reordered list and can easily treat the order as opaque provider ranking.
        """
        if not isinstance(review, dict):
            return {}
        keys = (
            "reviewed_by",
            "candidate_review_mode",
            "candidate_count_reviewed",
            "chunk_count",
            "finalist_count",
            "tournament_round_count",
            "context_limit_tokens",
            "recommended_candidate_ids",
            "confidence",
            "should_queue_now",
            "needs_user_choice",
            "reason",
            "answer_hint",
        )
        compact = {key: review.get(key) for key in keys if review.get(key) not in (None, "", [], {})}
        rejected = review.get("reject_candidate_ids")
        if isinstance(rejected, list) and rejected:
            compact["reject_candidate_ids_preview"] = rejected[:8]
            if len(rejected) > 8:
                compact["rejected_candidate_count"] = len(rejected)
        return compact

    def _compact_generic_search(self, result: Any) -> Any:
        if isinstance(result, dict) and isinstance(result.get("candidates"), list):
            compact = dict(result)
            candidates = result.get("candidates") or []
            compact["candidate_count"] = len(candidates)
            compact["candidates"] = [self._compact_candidate(c, fallback_result_set_id=result.get("result_set_id")) for c in candidates[:self._SEARCH_CANDIDATE_LIMIT]]
            if len(candidates) > self._SEARCH_CANDIDATE_LIMIT:
                compact["omitted_candidates_count"] = len(candidates) - self._SEARCH_CANDIDATE_LIMIT
            return self._compact_jsonish(compact, self._DEFAULT_MAX_CHARS)
        if isinstance(result, list):
            return [self._compact_candidate(c) if isinstance(c, dict) else c for c in result[:self._SEARCH_CANDIDATE_LIMIT]]
        return self._compact_jsonish(result, self._DEFAULT_MAX_CHARS)

    def _compact_web_research(self, result: dict[str, Any]) -> dict[str, Any]:
        """Keep fetched web evidence visible without flooding the prompt."""
        sources = list(result.get("sources") or [])
        evidence = list(result.get("evidence") or [])
        return {
            "ok": result.get("ok"),
            "topic": result.get("topic"),
            "intent": result.get("intent"),
            "provider": result.get("provider"),
            "facts_authoritative": result.get("facts_authoritative", False),
            "query_log_ids": result.get("query_log_ids"),
            "source_count": len(sources),
            "evidence_count": len(evidence),
            "sources": [self._compact_web_source(row) for row in sources[:8] if isinstance(row, dict)],
            "evidence": [self._compact_web_evidence(row) for row in evidence[:6] if isinstance(row, dict)],
            "warnings": list(result.get("warnings") or [])[:5],
            "unresolved_questions": list(result.get("unresolved_questions") or [])[:5],
            "warning": result.get("warning"),
        }

    @staticmethod
    def _compact_web_source(row: dict[str, Any]) -> dict[str, Any]:
        keys = ("title", "canonical_url", "source_kind", "rank", "fetched", "fetch_status", "confidence", "evidence_id")
        return {key: row.get(key) for key in keys if row.get(key) not in (None, "", [], {})}

    @staticmethod
    def _compact_web_evidence(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "claim": row.get("claim"),
            "value": row.get("value"),
            "source_name": row.get("source_name"),
            "url": row.get("url"),
            "snippet": str(row.get("snippet") or "")[:900],
            "confidence": row.get("confidence"),
        }

    def _compact_download_list(self, result: dict[str, Any] | list[Any]) -> Any:
        if isinstance(result, list):
            rows = result
            return {
                "count": len(rows),
                "downloads": [self._compact_download_row(row) for row in rows[:self._DOWNLOAD_LIMIT]],
                "omitted_count": max(0, len(rows) - self._DOWNLOAD_LIMIT),
            }
        compact = dict(result)
        for key in ("downloads", "items", "active", "queued"):
            value = compact.get(key)
            if isinstance(value, list):
                compact[f"{key}_count"] = len(value)
                compact[key] = [self._compact_download_row(row) for row in value[:self._DOWNLOAD_LIMIT]]
                if len(value) > self._DOWNLOAD_LIMIT:
                    compact[f"{key}_omitted_count"] = len(value) - self._DOWNLOAD_LIMIT
        return self._compact_jsonish(compact, self._DEFAULT_MAX_CHARS)

    def _compact_batch_recommendation(self, batch: dict[str, Any], fallback_result_set_id: Any) -> dict[str, Any]:
        groups = list(batch.get("groups") or [])
        compact = {
            "intent": batch.get("intent"),
            "reason": batch.get("reason"),
            "result_set_id": batch.get("result_set_id") or fallback_result_set_id,
            "candidate_ids": list(batch.get("candidate_ids") or []),
            "queue_download_arguments": batch.get("queue_download_arguments"),
            "group_count": len(groups),
            "groups": [self._compact_batch_group(group) for group in groups[:self._SEARCH_GROUP_LIMIT]],
        }
        if len(groups) > self._SEARCH_GROUP_LIMIT:
            compact["omitted_groups_count"] = len(groups) - self._SEARCH_GROUP_LIMIT
        return compact

    def _compact_batch_group(self, group: dict[str, Any]) -> dict[str, Any]:
        return {
            "unit": group.get("unit"),
            "recommended_candidate_id": group.get("recommended_candidate_id"),
            "title": group.get("title"),
            "size": group.get("size"),
            "seeders": group.get("seeders"),
            "candidate_count": group.get("candidate_count"),
            "unit_descriptor": self._compact_descriptor(group.get("unit_descriptor")),
            "coordinates": group.get("coordinates") or {},
        }

    def _recommended_candidate_ids(self, batch: dict[str, Any] | None) -> set[str]:
        ids: set[str] = set()
        if not batch:
            return ids
        for value in batch.get("candidate_ids") or []:
            if value:
                ids.add(str(value))
        args = batch.get("queue_download_arguments") or {}
        for value in args.get("candidate_ids") or [] if isinstance(args, dict) else []:
            if value:
                ids.add(str(value))
        for group in batch.get("groups") or []:
            value = group.get("recommended_candidate_id") if isinstance(group, dict) else None
            if value:
                ids.add(str(value))
        return ids

    @staticmethod
    def _llm_recommended_candidate_ids(review: dict[str, Any] | None) -> set[str]:
        """Return candidate IDs explicitly recommended by LLM adjudication."""
        ids: set[str] = set()
        if not isinstance(review, dict):
            return ids
        for value in review.get("recommended_candidate_ids") or []:
            if value:
                ids.add(str(value))
        return ids

    def _select_search_candidates(self, candidates: list[Any], keep_ids: set[str]) -> list[dict[str, Any]]:
        selected: list[dict[str, Any]] = []
        seen: set[str] = set()
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            if len(selected) < self._SEARCH_CANDIDATE_LIMIT:
                selected.append(candidate)
                cid = candidate.get("candidate_id")
                if cid:
                    seen.add(str(cid))
                continue
            cid = candidate.get("candidate_id")
            if cid and str(cid) in keep_ids and str(cid) not in seen:
                selected.append(candidate)
                seen.add(str(cid))
        return selected

    def _compact_candidate_picker(self, rows: Any) -> list[dict[str, Any]]:
        """Keep many candidate rows in a very small ID/title/size format."""
        if not isinstance(rows, list):
            return []
        compact_rows: list[dict[str, Any]] = []
        keys = (
            "id", "candidate_id", "index", "title", "size", "size_bytes", "seeders",
            "languages", "resolution", "source", "unit", "selection_warnings", "selection_blockers",
            "auto_queue_allowed", "blocked_reason", "is_bundle", "bundle_scope", "pack_type",
            "per_episode_size", "per_episode_size_mb", "estimated_bitrate_kbps", "codec",
            "bundle_unit_count", "expected_episode_count", "requested_season_coverage", "coverage_note", "llm_recommended",
        )
        for row in rows[: self._SEARCH_PICKER_LIMIT]:
            if not isinstance(row, dict):
                continue
            compact_rows.append({key: row.get(key) for key in keys if row.get(key) not in (None, "", [], {})})
        return compact_rows

    def _compact_candidate(self, candidate: dict[str, Any], fallback_result_set_id: Any = None) -> dict[str, Any]:
        keys = (
            "index", "option_index", "candidate_id", "title", "size", "size_bytes",
            "seeders", "source", "quality_score", "season", "episode", "languages",
            "resolution", "codec", "per_episode_size", "per_episode_size_bytes",
            "estimated_bitrate_kbps", "selection_warnings", "selection_blockers", "auto_queue_allowed", "auto_queue_blocked_reason", "is_bundle", "bundle_scope", "pack_type", "bundle_unit_count", "expected_episode_count", "requested_season_coverage", "coverage_note", "llm_recommended",
        )
        compact = {key: candidate.get(key) for key in keys if candidate.get(key) not in (None, "", [])}
        compact["result_set_id"] = candidate.get("result_set_id") or fallback_result_set_id
        descriptor = self._compact_descriptor(candidate.get("unit_descriptor"))
        if descriptor:
            compact["unit_descriptor"] = descriptor
        return compact

    def _compact_descriptor(self, descriptor: Any) -> dict[str, Any]:
        if not isinstance(descriptor, dict):
            return {}
        keys = ("stable_key", "label", "granularity", "sort_key", "coordinates")
        return {key: descriptor.get(key) for key in keys if descriptor.get(key) not in (None, "", [], {})}

    def _compact_download_row(self, row: Any) -> Any:
        if not isinstance(row, dict):
            return row
        keys = (
            "id", "download_id", "name", "item_name", "title", "torrent_title", "status",
            "progress", "priority", "category_id", "season", "episode", "unit_label",
            "unit_key", "save_path", "error", "health", "seeders", "peers",
        )
        return {key: row.get(key) for key in keys if row.get(key) not in (None, "", [])}

    def _compact_jsonish(self, result: Any, max_chars: int) -> Any:
        try:
            text = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
        except (TypeError, ValueError):
            text = str(result)
        if len(text) <= max_chars:
            return result
        return self._truncate_middle(text, max_chars, "tool result compressed")

    def _compact_textual(self, result: Any, max_chars: int, label: str) -> str:
        if isinstance(result, str):
            text = result
        else:
            try:
                text = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
            except (TypeError, ValueError):
                text = str(result)
        return self._truncate_middle(text, max_chars, label) if len(text) > max_chars else text

    @staticmethod
    def _truncate_middle(text: str, max_chars: int, label: str) -> str:
        if len(text) <= max_chars:
            return text
        if max_chars < 240:
            return text[:max_chars]
        head = int(max_chars * 0.66)
        tail = max(100, max_chars - head - 90)
        omitted = len(text) - head - tail
        return f"{text[:head].rstrip()}\n...[{label}: {omitted} chars omitted]...\n{text[-tail:].lstrip()}"
