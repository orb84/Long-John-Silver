"""Stable torrent candidate identifiers and cache helpers."""

from __future__ import annotations

import hashlib
import json
import re
import time
from typing import Any


def magnet_info_hash(magnet: str | None) -> str | None:
    """Extract the BTIH info hash from a magnet URI when present."""
    if not magnet:
        return None
    match = re.search(r"xt=urn:btih:([a-z0-9]+)", magnet, re.IGNORECASE)
    return match.group(1).lower() if match else None


def stable_candidate_id(title: str, magnet: str | None = None, source: str | None = None) -> str:
    """Return a durable, non-secret ID for a torrent candidate.

    The magnet itself is never exposed to the LLM.  When a BTIH exists we use
    that as the stable core.  Otherwise we hash source + title + magnet/link so
    repeated searches can still resolve the same candidate safely.
    """
    info_hash = magnet_info_hash(magnet)
    if info_hash:
        raw = f"btih:{info_hash}"
    else:
        raw = f"{source or ''}|{title or ''}|{magnet or ''}"
    return hashlib.sha256(raw.encode("utf-8", "ignore")).hexdigest()[:16]


def stable_result_set_id(*, session_id: str, name: str, query: str | None, season: int | None, episode: int | None, candidate_ids: list[str]) -> str:
    """Return an ID for a displayed result set.

    Includes a millisecond timestamp so old displayed result sets remain
    independently addressable instead of being overwritten by later searches.
    """
    payload = {
        "session_id": session_id or "default",
        "name": name or query or "",
        "query": query or "",
        "season": season,
        "episode": episode,
        "candidate_ids": candidate_ids,
        "ts_ms": int(time.time() * 1000),
    }
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()
    return digest[:16]


def attach_candidate_ids(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return copies of candidate dictionaries with stable candidate IDs."""
    out: list[dict[str, Any]] = []
    for candidate in candidates:
        c = dict(candidate)
        c["candidate_id"] = c.get("candidate_id") or stable_candidate_id(
            str(c.get("title") or ""),
            c.get("magnet"),
            c.get("source"),
        )
        out.append(c)
    return out


async def store_result_set(db: Any, *, session_id: str, cache_data: dict[str, Any]) -> None:
    """Persist a result set and remember it as the latest visible options list."""
    if not db:
        return
    result_set_id = cache_data.get("result_set_id")
    if not result_set_id:
        raise ValueError("cache_data requires result_set_id")
    serialized = json.dumps(cache_data)
    await db.system.set_preference(f"last_options_{session_id}", serialized)
    await db.system.set_preference(f"torrent_result_set_{session_id}_{result_set_id}", serialized)

    ids_key = f"torrent_result_sets_{session_id}"
    raw_ids = await db.system.get_preference(ids_key)
    try:
        ids = json.loads(raw_ids) if raw_ids else []
    except Exception:
        ids = []
    ids = [rid for rid in ids if rid != result_set_id]
    ids.insert(0, result_set_id)
    await db.system.set_preference(ids_key, json.dumps(ids[:20]))


async def load_result_set(db: Any, *, session_id: str, result_set_id: str | None = None) -> dict[str, Any] | None:
    """Load a result set by ID or the latest one for a session."""
    if not db:
        return None
    key = f"torrent_result_set_{session_id}_{result_set_id}" if result_set_id else f"last_options_{session_id}"
    raw = await db.system.get_preference(key)
    if not raw:
        return None
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


async def find_candidate_in_cached_sets(db: Any, *, session_id: str, candidate_id: str, result_set_id: str | None = None) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Find a candidate by stable ID in a specific or recent session result set."""
    if not db:
        return None, None
    search_ids: list[str | None] = [result_set_id] if result_set_id else [None]
    if not result_set_id:
        raw_ids = await db.system.get_preference(f"torrent_result_sets_{session_id}")
        try:
            recent_ids = json.loads(raw_ids) if raw_ids else []
        except Exception:
            recent_ids = []
        search_ids.extend(recent_ids[:20])

    seen = set()
    for rid in search_ids:
        marker = rid or "__latest__"
        if marker in seen:
            continue
        seen.add(marker)
        data = await load_result_set(db, session_id=session_id, result_set_id=rid)
        if not data:
            continue
        for candidate in data.get("candidates", []):
            if candidate.get("candidate_id") == candidate_id:
                return data, candidate
    return None, None
