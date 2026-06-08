"""Runtime date grounding helpers for agent-facing tool results.

These helpers make time-sensitive tool payloads self-contained.  Prompts can
include current-date guidance, but small LLMs and compacted tool results often
need the date comparison embedded directly next to the dates/sources they are
about to reason over.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timezone
from typing import Any

from src.utils.runtime_prompt_context import RuntimePromptContext


class RuntimeDateGrounding:
    """Compare metadata/source dates against the current runtime date."""

    _ISO_DATE_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
    _YEAR_RE = re.compile(r"\b(20\d{2}|19\d{2})\b")
    _MONTH_DATE_FORMATS = (
        "%b %d %Y",
        "%B %d %Y",
        "%b %d, %Y",
        "%B %d, %Y",
        "%d %b %Y",
        "%d %B %Y",
    )
    _MONTH_DATE_RE = re.compile(
        r"\b((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\s+\d{1,2},?\s+\d{4}|\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\s+\d{4})\b",
        re.IGNORECASE,
    )
    _UPCOMING_WORDS = (
        "upcoming",
        "coming",
        "next",
        "future",
        "premieres",
        "will air",
        "will premiere",
        "scheduled",
        "in arrivo",
        "prossima",
        "prossimo",
        "uscirà",
        "uscira",
    )

    @classmethod
    def runtime_context(cls) -> dict[str, Any]:
        """Return the current local runtime date context as a payload."""
        return RuntimePromptContext.payload()

    @classmethod
    def classify_date(cls, value: Any) -> dict[str, Any] | None:
        """Classify one date-like value relative to the current date."""
        text = str(value or "").strip()
        parsed = cls._parse_date(text)
        today = datetime.now(timezone.utc).astimezone().date()
        if parsed is not None:
            delta = (parsed - today).days
            relation = "future" if delta > 0 else "past" if delta < 0 else "today"
            return {
                "input": text,
                "date": parsed.isoformat(),
                "precision": "day",
                "relation": relation,
                "days_delta": delta,
                "current_date": today.isoformat(),
                "tense_guidance": cls._tense_guidance(relation),
            }
        year = cls._parse_year(text)
        if year is None:
            return None
        relation = "future" if year > today.year else "past" if year < today.year else "current_year"
        return {
            "input": text,
            "year": year,
            "precision": "year",
            "relation": relation,
            "current_date": today.isoformat(),
            "current_year": today.year,
            "tense_guidance": cls._tense_guidance(relation),
        }

    @classmethod
    def annotate_metadata_payload(cls, payload: dict[str, Any]) -> dict[str, Any]:
        """Add compact date-grounding metadata to a metadata_lookup payload."""
        payload["runtime_date_context"] = cls.runtime_context()
        best = payload.get("best") if isinstance(payload.get("best"), dict) else {}
        hints = payload.get("answer_hints") if isinstance(payload.get("answer_hints"), dict) else {}
        grounding: dict[str, Any] = {
            "date_relations": {},
            "episode_date_relations": [],
            "season_date_relations": [],
            "warnings": [],
        }
        for key in ("first_air_date", "last_air_date", "release_date"):
            value = best.get(key) or hints.get(key)
            relation = cls.classify_date(value)
            if relation:
                grounding["date_relations"][key] = relation
        next_ep = best.get("next_episode_to_air") or best.get("next_episode") or hints.get("next_episode")
        if isinstance(next_ep, dict):
            relation = cls.classify_date(next_ep.get("air_date") or next_ep.get("airdate") or next_ep.get("airstamp"))
            if relation:
                grounding["date_relations"]["next_episode"] = {**relation, "episode": cls._episode_label(next_ep)}
        for ep in cls._episode_rows(best, hints):
            relation = cls.classify_date(ep.get("air_date") or ep.get("airdate"))
            if relation:
                grounding["episode_date_relations"].append({**relation, "episode": cls._episode_label(ep), "title": ep.get("title") or ep.get("name")})
        for season in (best.get("seasons") if isinstance(best.get("seasons"), list) else []):
            if not isinstance(season, dict):
                continue
            relation = cls.classify_date(season.get("air_date"))
            if relation:
                grounding["season_date_relations"].append({**relation, "season": season.get("season_number"), "episode_count": season.get("episode_count")})
        cls._append_metadata_warnings(payload, grounding)
        payload["date_grounding"] = grounding
        return payload

    @classmethod
    def source_freshness_signals(cls, text: str, *, published_at: str = "", query: str = "", intent: str = "") -> dict[str, Any]:
        """Return current-date warnings for one fetched/public source."""
        runtime = cls.runtime_context()
        blob = str(text or "")
        query_blob = f"{query} {intent}".casefold()
        source_blob = blob.casefold()
        years = sorted({int(match.group(1)) for match in cls._YEAR_RE.finditer(blob)})
        stale_upcoming_years = [year for year in years if year < runtime["current_year"] and cls._contains_upcoming_language(source_blob)]
        published = cls.classify_date(published_at)
        current_request = any(term in query_blob for term in (
            "next", "upcoming", "latest", "current", "future", "renew", "rumor", "rumour", "news", "production", "filming", "release date", "air date",
        ))
        warnings: list[str] = []
        if stale_upcoming_years:
            warnings.append(
                "Source uses upcoming/future wording with past year(s) "
                f"{stale_upcoming_years} relative to current date {runtime['current_date']}; treat it as stale background, not current/future evidence."
            )
        if current_request and published and published.get("relation") == "past" and int(published.get("days_delta") or 0) < -365:
            warnings.append(
                f"Source publication/update date {published.get('date')} is more than one year old for a current/future query; do not use it as latest/upcoming evidence without newer corroboration."
            )
        return {
            "runtime_date_context": runtime,
            "published_at_relation": published,
            "year_mentions": years[:12],
            "stale_upcoming_years": stale_upcoming_years,
            "warnings": warnings,
        }

    @classmethod
    def _append_metadata_warnings(cls, payload: dict[str, Any], grounding: dict[str, Any]) -> None:
        question = f"{payload.get('question') or ''} {payload.get('query') or ''}".casefold()
        next_or_upcoming = any(term in question for term in ("next season", "upcoming season", "future season", "prossima stagione", "nuova stagione"))
        past_seasons = [row for row in grounding["season_date_relations"] if row.get("relation") == "past" and int(row.get("season") or 0) > 0]
        future_seasons = [row for row in grounding["season_date_relations"] if row.get("relation") in {"future", "today"} and int(row.get("season") or 0) > 0]
        if next_or_upcoming and past_seasons and not future_seasons:
            latest = max(past_seasons, key=lambda row: int(row.get("season") or 0))
            grounding["warnings"].append(
                f"The latest known season in structured metadata appears to be season {latest.get('season')} with an air date in the past relative to current_date. Do not describe that season as upcoming; for 'next season' ask public web/category research about a later season."
            )
        if next_or_upcoming:
            payload["requires_public_web_evidence"] = True
            payload.setdefault("source_sufficiency_warning", "")
            extra = " Next/upcoming season questions require current public evidence and date comparison; structured metadata alone can be stale or only describe already-released seasons."
            if extra not in payload["source_sufficiency_warning"]:
                payload["source_sufficiency_warning"] = (payload["source_sufficiency_warning"] + extra).strip()

    @classmethod
    def _episode_rows(cls, best: dict[str, Any], hints: dict[str, Any]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for source in (hints.get("episodes"), best.get("episodes"), (best.get("season_details") or {}).get("episodes") if isinstance(best.get("season_details"), dict) else None):
            if isinstance(source, list):
                rows.extend([row for row in source if isinstance(row, dict)])
        deduped: list[dict[str, Any]] = []
        seen: set[tuple[Any, Any, Any]] = set()
        for row in rows:
            key = (row.get("season"), row.get("episode_number") or row.get("number"), row.get("air_date") or row.get("airdate"))
            if key in seen:
                continue
            seen.add(key)
            deduped.append(row)
        return deduped[:40]

    @staticmethod
    def _episode_label(row: dict[str, Any]) -> str:
        season = row.get("season") or row.get("season_number")
        episode = row.get("episode_number") or row.get("number")
        if season and episode:
            return f"S{int(season):02d}E{int(episode):02d}"
        if episode:
            return f"E{int(episode):02d}"
        return ""

    @classmethod
    def _parse_date(cls, value: str) -> date | None:
        if not value:
            return None
        iso = cls._ISO_DATE_RE.search(value)
        if iso:
            try:
                return datetime.fromisoformat(iso.group(1)).date()
            except ValueError:
                return None
        month = cls._MONTH_DATE_RE.search(value)
        if month:
            raw = month.group(1).replace("Sept", "Sep")
            for fmt in cls._MONTH_DATE_FORMATS:
                try:
                    return datetime.strptime(raw, fmt).date()
                except ValueError:
                    continue
        return None

    @classmethod
    def _parse_year(cls, value: str) -> int | None:
        match = cls._YEAR_RE.search(value or "")
        if not match:
            return None
        return int(match.group(1))

    @classmethod
    def _contains_upcoming_language(cls, text: str) -> bool:
        return any(term in text for term in cls._UPCOMING_WORDS)

    @staticmethod
    def _tense_guidance(relation: str) -> str:
        if relation == "future":
            return "Use future wording: scheduled/upcoming; do not say already aired/released."
        if relation == "today":
            return "Same-day wording is allowed only if the source/time supports it."
        if relation == "current_year":
            return "Current-year mention is not enough by itself; compare exact dates or find fresher source context."
        return "Past wording only; do not describe this as upcoming/current unless a newer source says so."
