"""Metadata candidate grouping and LLM disambiguation helpers.

Provider adapters are deliberately imperfect: MusicBrainz, Open Library,
LibriVox, Discogs, Google Books, and catalog fallbacks expose different entity
levels and identifier schemes.  This module keeps the deterministic part small:
score obvious evidence, group candidates that likely describe the same real-world
object, expose conflicts, and produce a compact packet for the LLM to make the
judgement calls that are brittle to encode as rules.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Protocol


class MetadataCandidate(Protocol):
    """Structural protocol for provider results used by this module."""

    provider: str
    title: str
    contributors: list[str] | None
    year: str | None
    stable_id: str
    identifiers: dict[str, str]
    object_model: dict[str, Any] | None
    entity_type: str
    score: float
    evidence: list[str]


PROVIDER_PRIORITY = {
    "musicbrainz": 1.0,
    "open_library": 0.95,
    "librivox": 0.95,
    "gutendex": 0.82,
    "internet_archive": 0.78,
    "google_books": 0.76,
    "discogs": 0.74,
    "apple_itunes_search": 0.70,
    "comic_vine": 0.70,
}

IDENTIFIER_GROUPS: tuple[tuple[str, ...], ...] = (
    ("musicbrainz_release_group_id",),
    ("musicbrainz_release_id",),
    ("barcode",),
    ("isrc",),
    ("isbn_13", "isbn13"),
    ("isbn_10", "isbn10"),
    ("openlibrary_work_key",),
    ("openlibrary_edition_key",),
    ("librivox_id",),
    ("gutenberg_id",),
    ("internet_archive_identifier",),
    ("discogs_id",),
    ("google_books_id",),
    ("apple_track_id",),
)


@dataclass(frozen=True)
class RankedMetadata:
    """Result of deterministic ranking/grouping before LLM selection."""

    ranked: list[MetadataCandidate]
    groups: list[dict[str, Any]]
    disambiguation: dict[str, Any]


def norm_text(value: str) -> str:
    """Normalize human text for scoring and grouping."""
    return re.sub(r"\s+", " ", re.sub(r"[^\w]+", " ", str(value or "").lower())).strip()


def title_query_score(query: str, title: str, contributors: list[str] | None) -> tuple[float, list[str]]:
    """Return deterministic evidence score for the user's text against a candidate."""
    q = norm_text(query)
    t = norm_text(title)
    c = " ".join(norm_text(x) for x in (contributors or []))
    evidence: list[str] = []
    score = 0.0
    if q and t:
        if q == t:
            score += 0.45
            evidence.append("exact title/query match")
        elif q in t or t in q:
            score += 0.28
            evidence.append("partial title/query match")
        q_tokens = {token for token in q.split() if len(token) > 2}
        t_tokens = set(t.split()) | set(c.split())
        if q_tokens:
            overlap = len(q_tokens & t_tokens) / max(1, len(q_tokens))
            score += min(0.25, overlap * 0.25)
            if overlap >= 0.5:
                evidence.append("query tokens overlap title/contributors")
    return score, evidence


def rank_and_group(query: str, results: list[MetadataCandidate], *, limit: int) -> RankedMetadata:
    """Score, deduplicate, and expose conflict/disambiguation facts."""
    deduped: dict[str, MetadataCandidate] = {}
    for item in results:
        base, evidence = title_query_score(query, item.title, item.contributors or [])
        item.score = max(float(item.score or 0.0), 0.0) + base + PROVIDER_PRIORITY.get(item.provider, 0.5) * 0.1
        item.evidence = sorted(set((item.evidence or []) + evidence))
        key = canonical_group_key(item) or item.stable_id
        existing = deduped.get(key)
        if not existing or item.score > existing.score:
            deduped[key] = item
        elif existing:
            existing.evidence = sorted(set(existing.evidence + [f"also matched {item.provider}: {item.stable_id}"]))
    ranked = sorted(deduped.values(), key=lambda r: (r.score, bool(getattr(r, "cover_url", None)), r.provider), reverse=True)[:limit]
    groups = conflict_groups(ranked)
    disambiguation = disambiguation_report(ranked, groups)
    return RankedMetadata(ranked=ranked, groups=groups, disambiguation=disambiguation)


def canonical_group_key(item: MetadataCandidate) -> str:
    """Return a cross-provider grouping key when identifiers/text strongly match."""
    identifiers = item.identifiers or {}
    model = item.object_model or {}
    for group in IDENTIFIER_GROUPS:
        for key in group:
            values = _identifier_values(identifiers.get(key) or model.get(key))
            if values:
                return f"id:{group[0]}:{values[0]}"
    title = norm_text(item.title)
    contributors = [norm_text(x) for x in (item.contributors or [])[:2] if norm_text(x)]
    year = str(item.year or model.get("year") or model.get("first_publish_year") or model.get("published_date") or "")[:4]
    if title and contributors:
        return "text:" + "|".join([title, ",".join(contributors), year])
    if title:
        return "title:" + "|".join([title, year])
    return ""


def conflict_groups(results: list[MetadataCandidate]) -> list[dict[str, Any]]:
    """Group likely-equivalent candidates and report meaningful conflicts."""
    buckets: dict[str, list[MetadataCandidate]] = {}
    for item in results:
        buckets.setdefault(canonical_group_key(item) or item.stable_id, []).append(item)
    groups: list[dict[str, Any]] = []
    for key, items in buckets.items():
        if len(items) < 2:
            continue
        years = sorted(set(_year(i) for i in items if _year(i)))
        titles = sorted(set(str(i.title or "").strip() for i in items if str(i.title or "").strip()))
        contributors = sorted(set(", ".join(i.contributors or []) for i in items if i.contributors))
        entity_types = sorted(set((i.entity_type or (i.object_model or {}).get("model_type") or "") for i in items))
        providers = sorted(set(i.provider for i in items))
        conflicts = []
        if len(years) > 1:
            conflicts.append("year")
        if len({norm_text(x) for x in titles}) > 1:
            conflicts.append("title")
        if len({norm_text(x) for x in contributors if x}) > 1:
            conflicts.append("contributors")
        if len({norm_text(x) for x in entity_types if x}) > 1:
            conflicts.append("entity_type")
        groups.append({
            "group_key": key,
            "providers": providers,
            "stable_ids": [i.stable_id for i in items],
            "conflicts": conflicts,
            "years": years,
            "titles": titles,
            "contributors": contributors,
            "entity_types": entity_types,
        })
    return groups


def disambiguation_report(ranked: list[MetadataCandidate], groups: list[dict[str, Any]]) -> dict[str, Any]:
    """Build a compact LLM-facing report for candidate selection."""
    needs = False
    reason = "top result is clearly ahead" if ranked else "no candidates returned"
    if len(ranked) >= 2 and ranked[0].score - ranked[1].score < 0.12:
        needs = True
        reason = "top candidates are close in score"
    if any(group.get("conflicts") for group in groups):
        needs = True
        reason = "candidate metadata has title/year/contributor/type conflicts"
    return {
        "needs_llm_selection": needs,
        "reason": reason,
        "top_candidate_stable_id": ranked[0].stable_id if ranked else "",
        "safe_autoselect": bool(ranked and not needs and ranked[0].score >= 0.72),
        "selection_facets": selection_facets(ranked[:5]),
        "llm_tasks": [
            "Prefer explicit user constraints over provider rank when they conflict.",
            "Discard candidates whose entity type cannot satisfy the category request.",
            "When evidence is close or conflicting, ask one concise clarification instead of guessing.",
            "When selecting for torrent search, convert the chosen metadata into category-native search terms only.",
        ],
    }


def selection_facets(results: list[MetadataCandidate]) -> list[dict[str, Any]]:
    """Return human/LLM-useful candidate facts without bulky raw provider JSON."""
    facets: list[dict[str, Any]] = []
    for item in results:
        model = item.object_model or {}
        facets.append({
            "stable_id": item.stable_id,
            "provider": item.provider,
            "title": item.title,
            "contributors": item.contributors or [],
            "year": item.year,
            "entity_type": item.entity_type or model.get("model_type", ""),
            "release_type": model.get("release_type") or model.get("edition_name") or "",
            "language": model.get("languages") or "",
            "series": model.get("series") or "",
            "volume": model.get("series_index") or "",
            "narrators": model.get("narrators") or model.get("readers") or [],
            "formats": model.get("formats") or model.get("audio_formats") or [],
            "score": round(float(item.score or 0.0), 4),
            "evidence": item.evidence,
        })
    return facets


def _identifier_values(value: Any) -> list[str]:
    if value is None:
        return []
    values = value if isinstance(value, list) else [value]
    return [re.sub(r"\s+", "", str(v).strip().lower()) for v in values if str(v).strip()]


def _year(item: MetadataCandidate) -> str:
    model = item.object_model or {}
    return str(item.year or model.get("year") or model.get("first_publish_year") or model.get("published_date") or "")[:4]
