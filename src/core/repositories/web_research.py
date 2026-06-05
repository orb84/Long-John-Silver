"""Repository for durable web-research query and evidence provenance."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from src.core.repositories.base import BaseRepository


class WebResearchRepository(BaseRepository):
    """Persist category-neutral web-research logs and extracted evidence.

    This repository stores what public pages were discovered/fetched.  It does
    not interpret TV, movie, book, music, or sports semantics; category hooks
    can later attach domain-specific facts to the stored evidence IDs.
    """

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    async def start_query_log(
        self,
        *,
        provider: str,
        query: str,
        parameters: dict[str, Any] | None = None,
        intent: str = "general_research",
        category_id: str = "",
        item_id: str = "",
    ) -> int:
        """Create a query-log row and return its durable id."""
        cursor = await self._db.execute(
            """INSERT INTO web_research_query_log
               (provider_id, provider, category_id, item_id, query,
                parameters_json, intent, status, started_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'running', ?)""",
            (
                str(provider or ""),
                str(provider or ""),
                str(category_id or ""),
                str(item_id or ""),
                str(query or ""),
                json.dumps(parameters or {}, ensure_ascii=False, default=str),
                str(intent or "general_research"),
                self._now(),
            ),
        )
        await self._db.commit()
        return int(cursor.lastrowid or 0)

    async def complete_query_log(
        self,
        query_log_id: int,
        *,
        status: str,
        result_count: int = 0,
        error_code: str = "",
    ) -> None:
        """Mark a query-log row complete or failed."""
        await self._db.execute(
            """UPDATE web_research_query_log
               SET status = ?, error_code = ?, result_count = ?, completed_at = ?
               WHERE id = ?""",
            (
                str(status or ""),
                str(error_code or ""),
                max(0, int(result_count or 0)),
                self._now(),
                int(query_log_id),
            ),
        )
        await self._db.commit()

    async def upsert_source_evidence(
        self,
        *,
        query_log_id: int | None = None,
        category_id: str = "",
        item_id: str = "",
        url: str,
        canonical_url: str,
        title: str = "",
        source_kind: str = "unknown",
        source_name: str = "",
        fetched_at: str = "",
        published_at: str = "",
        extracted_text_hash: str = "",
        confidence: float = 0.0,
        snippet: str = "",
        evidence: dict[str, Any] | None = None,
        status: str = "candidate",
        error: str = "",
    ) -> int:
        """Create/update one source-evidence row.

        The uniqueness surface is item-scoped plus canonical URL.  Generic
        unscoped research uses blank category/item identifiers and can update
        the same page provenance across repeated queries.
        """
        now = self._now()
        payload = json.dumps(evidence or {}, ensure_ascii=False, default=str)
        cursor = await self._db.execute(
            """SELECT id FROM web_source_evidence
               WHERE category_id = ? AND item_id = ? AND canonical_url = ?""",
            (str(category_id or ""), str(item_id or ""), str(canonical_url or url)),
        )
        existing = await cursor.fetchone()
        if existing:
            evidence_id = int(existing["id"])
            await self._db.execute(
                """UPDATE web_source_evidence
                   SET query_log_id = COALESCE(?, query_log_id), url = ?, title = ?,
                       source_kind = ?, source_name = ?, fetched_at = ?, published_at = ?,
                       extracted_text_hash = ?, confidence = ?, snippet = ?,
                       evidence_json = ?, status = ?, error = ?, updated_at = ?
                   WHERE id = ?""",
                (
                    query_log_id,
                    str(url or ""),
                    str(title or ""),
                    str(source_kind or "unknown"),
                    str(source_name or ""),
                    str(fetched_at or ""),
                    str(published_at or ""),
                    str(extracted_text_hash or ""),
                    float(confidence or 0.0),
                    str(snippet or ""),
                    payload,
                    str(status or "candidate"),
                    str(error or "")[:1000],
                    now,
                    evidence_id,
                ),
            )
            await self._db.commit()
            return evidence_id

        insert = await self._db.execute(
            """INSERT INTO web_source_evidence
               (query_log_id, category_id, item_id, url, canonical_url, title,
                source_kind, source_name, fetched_at, published_at,
                extracted_text_hash, confidence, snippet, evidence_json,
                status, error, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                query_log_id,
                str(category_id or ""),
                str(item_id or ""),
                str(url or ""),
                str(canonical_url or url),
                str(title or ""),
                str(source_kind or "unknown"),
                str(source_name or ""),
                str(fetched_at or ""),
                str(published_at or ""),
                str(extracted_text_hash or ""),
                float(confidence or 0.0),
                str(snippet or ""),
                payload,
                str(status or "candidate"),
                str(error or "")[:1000],
                now,
                now,
            ),
        )
        await self._db.commit()
        return int(insert.lastrowid or 0)

    async def add_fact_provenance(
        self,
        *,
        category_id: str,
        item_id: str,
        fact_type: str,
        value: dict[str, Any],
        source_evidence_ids: list[int],
        confidence: float = 0.0,
        decided_by: str = "deterministic",
    ) -> int:
        """Persist category-interpreted fact provenance.

        Category extensions should call this after interpreting an evidence
        bundle.  Core research code should not manufacture durable facts.
        """
        cursor = await self._db.execute(
            """INSERT INTO category_fact_provenance
               (category_id, item_id, fact_type, value_json, source_evidence_ids_json,
                confidence, decided_by, decided_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                str(category_id or ""),
                str(item_id or ""),
                str(fact_type or ""),
                json.dumps(value or {}, ensure_ascii=False, default=str),
                json.dumps([int(value) for value in source_evidence_ids], ensure_ascii=False),
                float(confidence or 0.0),
                str(decided_by or "deterministic"),
                self._now(),
            ),
        )
        await self._db.commit()
        return int(cursor.lastrowid or 0)

    async def list_evidence(
        self,
        *,
        category_id: str = "",
        item_id: str = "",
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Return recent source-evidence rows for diagnostics/UI."""
        clauses: list[str] = []
        params: list[Any] = []
        if category_id:
            clauses.append("category_id = ?")
            params.append(str(category_id))
        if item_id:
            clauses.append("item_id = ?")
            params.append(str(item_id))
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(max(1, min(int(limit or 50), 200)))
        cursor = await self._db.execute(
            f"""SELECT * FROM web_source_evidence
                {where}
                ORDER BY updated_at DESC, id DESC
                LIMIT ?""",
            tuple(params),
        )
        rows = await cursor.fetchall()
        return [self._row_to_dict(row) for row in rows]


    async def upsert_information_watch(self, watch: Any) -> dict[str, Any]:
        """Insert or update a durable web-information watch."""
        from src.core.models import WebInformationWatch

        model = watch if isinstance(watch, WebInformationWatch) else WebInformationWatch(**dict(watch or {}))
        now = self._now()
        created_at = model.created_at.isoformat() if getattr(model, "created_at", None) else now
        updated_at = now
        await self._db.execute(
            """INSERT INTO web_information_watch
               (id, owner_type, title, objective, query, intent, category_id, item_id,
                item_name, language, cadence_minutes, enabled, notify_only_if_meaningful,
                llm_evaluation_required, allow_download_queueing, query_plan_json,
                user_feedback_json, last_run_at, next_run_at, last_event_id,
                last_evidence_signature, last_status, last_error, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                    owner_type = excluded.owner_type,
                    title = excluded.title,
                    objective = excluded.objective,
                    query = excluded.query,
                    intent = excluded.intent,
                    category_id = excluded.category_id,
                    item_id = excluded.item_id,
                    item_name = excluded.item_name,
                    language = excluded.language,
                    cadence_minutes = excluded.cadence_minutes,
                    enabled = excluded.enabled,
                    notify_only_if_meaningful = excluded.notify_only_if_meaningful,
                    llm_evaluation_required = excluded.llm_evaluation_required,
                    allow_download_queueing = excluded.allow_download_queueing,
                    query_plan_json = excluded.query_plan_json,
                    user_feedback_json = excluded.user_feedback_json,
                    last_run_at = excluded.last_run_at,
                    next_run_at = excluded.next_run_at,
                    last_event_id = excluded.last_event_id,
                    last_evidence_signature = excluded.last_evidence_signature,
                    last_status = excluded.last_status,
                    last_error = excluded.last_error,
                    updated_at = excluded.updated_at
            """,
            (
                model.id, model.owner_type, model.title, model.objective, model.query, model.intent,
                model.category_id, model.item_id, model.item_name, model.language, int(model.cadence_minutes),
                1 if model.enabled else 0, 1 if model.notify_only_if_meaningful else 0,
                1 if model.llm_evaluation_required else 0, 1 if model.allow_download_queueing else 0,
                json.dumps(model.query_plan, ensure_ascii=False, default=str),
                json.dumps(model.user_feedback, ensure_ascii=False, default=str),
                model.last_run_at.isoformat() if model.last_run_at else None,
                model.next_run_at.isoformat() if model.next_run_at else None,
                model.last_event_id, model.last_evidence_signature, model.last_status, model.last_error,
                created_at, updated_at,
            ),
        )
        await self._db.commit()
        row = await self.get_information_watch(model.id)
        return row or model.model_dump(mode="json")

    async def get_information_watch(self, watch_id: str) -> dict[str, Any] | None:
        """Return one web-information watch by id."""
        cursor = await self._db.execute(
            "SELECT * FROM web_information_watch WHERE id = ?",
            (str(watch_id or ""),),
        )
        row = await cursor.fetchone()
        return self._watch_row_to_dict(row) if row else None

    async def list_information_watches(
        self,
        *,
        enabled_only: bool = False,
        category_id: str = "",
        item_id: str = "",
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """List web-information watches for diagnostics/UI/tools."""
        clauses: list[str] = []
        params: list[Any] = []
        if enabled_only:
            clauses.append("enabled = 1")
        if category_id:
            clauses.append("category_id = ?")
            params.append(str(category_id))
        if item_id:
            clauses.append("item_id = ?")
            params.append(str(item_id))
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(max(1, min(int(limit or 100), 500)))
        cursor = await self._db.execute(
            f"""SELECT * FROM web_information_watch
                {where}
                ORDER BY enabled DESC, COALESCE(next_run_at, updated_at, created_at) ASC
                LIMIT ?""",
            tuple(params),
        )
        rows = await cursor.fetchall()
        return [self._watch_row_to_dict(row) for row in rows]

    async def disable_information_watch(self, watch_id: str, *, reason: str = "") -> dict[str, Any] | None:
        """Disable a web-information watch without deleting its history."""
        await self._db.execute(
            """UPDATE web_information_watch
               SET enabled = 0, last_status = ?, last_error = '', updated_at = ?
               WHERE id = ?""",
            (f"disabled:{reason}" if reason else "disabled", self._now(), str(watch_id or "")),
        )
        await self._db.commit()
        return await self.get_information_watch(watch_id)

    async def add_information_watch_event(self, event: Any) -> int:
        """Persist one execution event for a web-information watch."""
        from src.core.models import WebInformationWatchEvent

        model = event if isinstance(event, WebInformationWatchEvent) else WebInformationWatchEvent(**dict(event or {}))
        cursor = await self._db.execute(
            """INSERT INTO web_information_watch_event
               (watch_id, status, summary, event_type, evidence_signature,
                source_evidence_ids_json, query_log_ids_json, notification_recommended,
                llm_review_required, payload_json, error, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                model.watch_id, model.status, model.summary, model.event_type, model.evidence_signature,
                json.dumps(model.source_evidence_ids, ensure_ascii=False),
                json.dumps(model.query_log_ids, ensure_ascii=False),
                1 if model.notification_recommended else 0,
                1 if model.llm_review_required else 0,
                json.dumps(model.payload, ensure_ascii=False, default=str),
                model.error,
                model.created_at.isoformat() if model.created_at else self._now(),
            ),
        )
        event_id = int(cursor.lastrowid or 0)
        await self._db.commit()
        return event_id

    async def update_information_watch_after_run(
        self,
        watch_id: str,
        *,
        status: str,
        last_event_id: int | None,
        evidence_signature: str,
        last_run_at: str,
        next_run_at: str | None,
        error: str = "",
    ) -> None:
        """Update watch run bookkeeping after an evaluation."""
        await self._db.execute(
            """UPDATE web_information_watch
               SET last_run_at = ?, next_run_at = ?, last_event_id = ?,
                   last_evidence_signature = ?, last_status = ?, last_error = ?, updated_at = ?
               WHERE id = ?""",
            (
                str(last_run_at or ""),
                next_run_at,
                last_event_id,
                str(evidence_signature or ""),
                str(status or "completed"),
                str(error or "")[:1000],
                self._now(),
                str(watch_id or ""),
            ),
        )
        await self._db.commit()

    async def list_information_watch_events(self, watch_id: str, *, limit: int = 20) -> list[dict[str, Any]]:
        """Return recent events for one web-information watch."""
        cursor = await self._db.execute(
            """SELECT * FROM web_information_watch_event
               WHERE watch_id = ?
               ORDER BY created_at DESC, id DESC
               LIMIT ?""",
            (str(watch_id or ""), max(1, min(int(limit or 20), 200))),
        )
        rows = await cursor.fetchall()
        return [self._event_row_to_dict(row) for row in rows]

    @staticmethod
    def _watch_row_to_dict(row: Any) -> dict[str, Any]:
        data = dict(row)
        for key in ("query_plan_json", "user_feedback_json"):
            try:
                data[key.replace("_json", "")] = json.loads(data.get(key) or "{}")
            except Exception:
                data[key.replace("_json", "")] = {}
        for key in ("enabled", "notify_only_if_meaningful", "llm_evaluation_required", "allow_download_queueing"):
            data[key] = bool(data.get(key))
        return data

    @staticmethod
    def _event_row_to_dict(row: Any) -> dict[str, Any]:
        data = dict(row)
        for key, fallback in (("source_evidence_ids_json", []), ("query_log_ids_json", []), ("payload_json", {})):
            try:
                data[key.replace("_json", "")] = json.loads(data.get(key) or json.dumps(fallback))
            except Exception:
                data[key.replace("_json", "")] = fallback
        data["notification_recommended"] = bool(data.get("notification_recommended"))
        data["llm_review_required"] = bool(data.get("llm_review_required"))
        return data

    @staticmethod
    def _row_to_dict(row: Any) -> dict[str, Any]:
        data = dict(row)
        for key in ("evidence_json", "value_json", "source_evidence_ids_json", "parameters_json"):
            if key in data:
                try:
                    data[key.replace("_json", "")] = json.loads(data.get(key) or "{}")
                except Exception:
                    data[key.replace("_json", "")] = data.get(key)
        return data
