"""Soulseek/slskd agent tools.

These tools keep Soulseek separate from torrent/magnet queueing.  slskd is a
source companion: LJS can search it, enqueue a slskd transfer, and preview the
sharing plan, while torrent queue semantics remain owned by libtorrent.
"""

from __future__ import annotations

from typing import Any, Optional, TYPE_CHECKING
import json
import re

from src.core.models import Intent, ToolExecutionContext
from src.integrations.slskd_client import SlskdClient
from src.integrations.slskd_config import build_slskd_share_plan, render_slskd_yaml
from src.integrations.slskd_transfer_view import SlskdTransferReadModel
from src.utils.candidate_ids import load_result_set

if TYPE_CHECKING:
    from src.core.config import SettingsManager
    from src.core.database import Database


class SearchSoulseekTool:
    """Search Soulseek through a configured local/remote slskd instance."""

    name = "search_soulseek"
    description = (
        "Search Soulseek through slskd as a companion source for music, audiobooks, ebooks, and rare files. "
        "Use this when torrents fail, when a user explicitly asks to use Soulseek, or when music/book material is likely better on Soulseek. "
        "Do not pass these candidates to queue_download; use enqueue_soulseek_download for selected Soulseek files."
    )
    intents = {Intent.SEARCH, Intent.DOWNLOAD}
    allow_direct = True
    requires_confirmation = False
    destructive = False
    required_dependencies = ["settings_manager"]

    def __init__(self, settings_manager: Optional["SettingsManager"] = None, database: Optional["Database"] = None) -> None:
        self._settings_manager = settings_manager
        self._database = database

    def parameters(self) -> dict:
        """Return the JSON schema for Soulseek search arguments."""
        return {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Soulseek search text."},
                "category_id": {"type": "string", "description": "Optional category such as music, audiobooks, or ebooks."},
                "max_results": {"type": "integer", "minimum": 1, "maximum": 100, "description": "Maximum normalized files to return."},
            },
            "required": ["query"],
        }

    async def execute(self, arguments: dict[str, Any], context: ToolExecutionContext) -> object:
        """Search slskd and return normalized, recoverable results."""
        if not self._settings_manager:
            return self._not_configured("Settings manager is not available.")
        settings = self._settings_manager.settings
        cfg = settings.soulseek
        category_id = str(arguments.get("category_id") or "").strip().lower()
        if category_id and cfg.search_enabled_categories and category_id not in set(cfg.search_enabled_categories):
            return {
                "ok": False,
                "recoverable": True,
                "error_code": "SLSKD_CATEGORY_DISABLED",
                "error": f"Soulseek search is not enabled for category '{category_id}'.",
                "next_actions": ["Use torrent search", "Enable the category in Soulseek settings"],
            }
        if not cfg.api_configured:
            return self._not_configured("Soulseek/slskd is disabled or missing an API key.")
        if getattr(cfg, "managed", True):
            if not cfg.soulseek_credentials_configured:
                return self._not_configured("Soulseek username and password are required before searching.", error_code="SLSKD_NEEDS_CREDENTIALS")
            if str(getattr(cfg, "account_status", "")).lower() == "auth_failed":
                return self._not_configured(cfg.account_status_message or "Soulseek rejected these credentials.", error_code="SLSKD_AUTH_FAILED")
        client = SlskdClient(cfg)
        original_query_text = str(arguments.get("query") or "")
        query_variants = _soulseek_query_variants(original_query_text) if category_id == "music" else []
        query_text = query_variants[0] if query_variants else original_query_text
        max_results = arguments.get("max_results")
        result = await client.search(query_text, max_results=max_results)
        # Direct Soulseek retries should behave like the companion search: music
        # album/track requests often need concise artist/title permutations, and
        # the first wording from the LLM may still contain request-shape words.
        if (
            category_id == "music"
            and isinstance(result, dict)
            and result.get("ok") is True
            and not result.get("candidates")
        ):
            tried = [query_text]
            for variant in (query_variants or _soulseek_query_variants(query_text)):
                if variant.casefold() in {str(q).casefold() for q in tried}:
                    continue
                tried.append(variant)
                retry = await client.search(variant, max_results=max_results)
                if isinstance(retry, dict) and retry.get("ok") is True and retry.get("candidates"):
                    retry["query"] = variant
                    retry["queries_tried"] = tried
                    result = retry
                    break
            if isinstance(result, dict):
                result.setdefault("queries_tried", tried)
        if isinstance(result, dict) and result.get("ok") is False:
            await self._record_runtime_result(cfg, result)
            result.setdefault("next_actions", ["Use torrent search fallback", "Open Settings > Shared Search & Indexers > Soulseek/slskd"])
        elif isinstance(result, dict):
            await self._mark_account_ready(cfg)
            result["queueing_note"] = "Use enqueue_soulseek_download with candidate_id and result_set_id when available. For direct raw candidates without an id, pass username plus filename or filenames; do not use queue_download for Soulseek candidates."
            result.setdefault("search_notes", []).append("Private/locked Soulseek files are filtered out before candidates are shown.")
        return result

    async def _mark_account_ready(self, cfg: Any) -> None:
        """Treat a successful slskd search as proof the Soulseek session works."""
        cfg.account_status = "ready"
        cfg.account_status_message = "Soulseek account authenticated."
        try:
            from datetime import datetime, timezone
            cfg.account_checked_at = datetime.now(timezone.utc).isoformat()
        except Exception:
            pass
        self._save_settings_if_possible()

    async def _record_runtime_result(self, cfg: Any, result: dict[str, Any]) -> None:
        """Update account status from live slskd errors without blocking probes."""
        error = str(result.get("error") or "").lower()
        if any(token in error for token in ("username and/or password invalid", "invalid username", "invalid password", "invalid credentials")):
            cfg.account_status = "auth_failed"
            cfg.account_status_message = "Soulseek rejected these credentials. Use an existing account or try a different new username/password."
        elif any(token in error for token in ("not logged in", "not connected", "connect to server")):
            cfg.account_status = "checking"
            cfg.account_status_message = "slskd is running but not connected/logged in to Soulseek yet. LJS will keep probing instead of treating this as a permanent failure."
        self._save_settings_if_possible()

    def _save_settings_if_possible(self) -> None:
        try:
            if self._settings_manager and hasattr(self._settings_manager, "save"):
                self._settings_manager.save(self._settings_manager.settings)
        except Exception:
            pass

    @staticmethod
    def _not_configured(message: str, *, error_code: str = "SLSKD_NOT_CONFIGURED") -> dict[str, Any]:
        return {
            "ok": False,
            "recoverable": True,
            "error_code": error_code,
            "error": message,
            "next_actions": [
                "Use torrent search fallback",
                "Configure Soulseek credentials and slskd API key in Settings",
            ],
        }


def _clean_soulseek_query(value: str) -> str:
    text = str(value or "")
    text = re.sub(r"\b(?:album|track|song|single|ep|music|release|download|torrent|torrents|grab|get|please)\b", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"\b(?:flac|mp3|aac|alac|m4a|lossless|bitrate|kbps)\b", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"[\[\]{}()]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip(" -_.,")
    return text


def _soulseek_query_variants(query: str) -> list[str]:
    queries: list[str] = []
    seen: set[str] = set()

    def add(value: str) -> None:
        cleaned = _clean_soulseek_query(value)
        cleaned = re.sub(r"\b(?:from|by|di|da)\b", " ", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s+", " ", cleaned).strip(" -_.,")
        if cleaned and cleaned.casefold() not in seen:
            seen.add(cleaned.casefold())
            queries.append(cleaned)

    raw = str(query or "").strip()
    natural = re.search(r"(?P<title>.+?)\s+(?:by|from|di|da)\s+(?P<artist>.+)$", _clean_soulseek_query(raw), re.IGNORECASE)
    if natural:
        title = natural.group("title")
        artist = natural.group("artist")
        add(f"{title} {artist}")
        add(f"{artist} {title}")
        add(title)
        add(artist)
    add(raw)
    cleaned = _clean_soulseek_query(raw)
    if " - " in cleaned:
        left, right = [part.strip() for part in cleaned.split(" - ", 1)]
        add(f"{left} {right}")
        add(f"{right} {left}")
        add(left)
        add(right)
    tokens = re.sub(r"\b(?:from|by|di|da)\b", " ", cleaned, flags=re.IGNORECASE).split()
    if len(tokens) >= 4:
        mid = len(tokens) // 2
        first = " ".join(tokens[:mid])
        second = " ".join(tokens[mid:])
        add(f"{second} {first}")
        add(first)
        add(second)
    elif len(tokens) == 3:
        add(" ".join(tokens[1:] + tokens[:1]))
    return queries[:8]


class EnqueueSoulseekDownloadTool:
    """Queue a selected Soulseek file through slskd."""

    name = "enqueue_soulseek_download"
    description = (
        "Queue a Soulseek/slskd search result for download. Prefer candidate_id + result_set_id from soulseek_candidate_picker; username plus exact filename/filenames is a fallback. "
        "This is separate from torrent queue_download because Soulseek has no magnet link or swarm semantics."
    )
    intents = {Intent.DOWNLOAD}
    allow_direct = True
    requires_confirmation = True
    destructive = False
    required_dependencies = ["settings_manager"]

    def __init__(self, settings_manager: Optional["SettingsManager"] = None, database: Optional["Database"] = None) -> None:
        self._settings_manager = settings_manager
        self._database = database

    def parameters(self) -> dict:
        """Return the JSON schema for queueing a selected slskd file."""
        return {
            "type": "object",
            "properties": {
                "candidate_id": {"type": "string", "description": "Preferred: Soulseek candidate_id from soulseek_candidate_picker or companion_soulseek."},
                "result_set_id": {"type": "string", "description": "Result set id returned by search_media_torrents/search_soulseek."},
                "username": {"type": "string", "description": "Soulseek username from the selected slskd result. Used only when candidate_id is not available."},
                "filename": {"type": "string", "description": "Exact remote filename/path from the selected slskd result, or the folder label when filenames is supplied."},
                "filenames": {"type": "array", "items": {"type": "string"}, "description": "Exact remote filenames from a folder/album candidate. Usually omitted when candidate_id is provided."},
                "file_requests": {"type": "array", "items": {"type": "object"}, "description": "Advanced fallback: slskd QueueDownloadRequest rows with filename and optional size. Usually omitted when candidate_id is provided."},
                "category_id": {"type": "string", "description": "Optional target category for later import, e.g. music."},
            },
            "required": [],
        }

    async def _resolve_candidate_from_cache(self, arguments: dict[str, Any], context: ToolExecutionContext) -> dict[str, Any] | None:
        """Resolve a Soulseek candidate_id or partial username from recent cached search results."""
        if not self._database or not getattr(context, "session_id", None):
            return None
        candidate_id = str(arguments.get("candidate_id") or "").strip()
        result_set_id = str(arguments.get("result_set_id") or "").strip() or None
        username = str(arguments.get("username") or "").strip().casefold()

        async def load_one(rid: str | None) -> dict[str, Any] | None:
            try:
                return await load_result_set(self._database, session_id=context.session_id, result_set_id=rid)
            except Exception:
                return None

        result_sets: list[dict[str, Any]] = []
        first = await load_one(result_set_id)
        if first:
            result_sets.append(first)
        if not result_set_id:
            latest = await load_one(None)
            if latest and all(latest.get("result_set_id") != r.get("result_set_id") for r in result_sets):
                result_sets.append(latest)
            try:
                raw_ids = await self._database.system.get_preference(f"torrent_result_sets_{context.session_id}")
                recent_ids = json.loads(raw_ids) if raw_ids else []
            except Exception:
                recent_ids = []
            for rid in recent_ids[:10]:
                data = await load_one(str(rid))
                if data and all(data.get("result_set_id") != r.get("result_set_id") for r in result_sets):
                    result_sets.append(data)

        all_candidates: list[dict[str, Any]] = []
        for data in result_sets:
            companion = data.get("companion_soulseek") if isinstance(data.get("companion_soulseek"), dict) else {}
            for cand in companion.get("candidates") or []:
                if isinstance(cand, dict):
                    enriched = dict(cand)
                    enriched.setdefault("result_set_id", data.get("result_set_id"))
                    all_candidates.append(enriched)
        if candidate_id:
            for cand in all_candidates:
                if str(cand.get("candidate_id") or "") == candidate_id:
                    return cand
            return None
        if username:
            matches = [c for c in all_candidates if str(c.get("username") or "").casefold() == username]
            if not matches:
                return None
            folder_matches = [c for c in matches if c.get("candidate_type") == "folder" and int(c.get("audio_file_count") or 0) >= 2]
            strong = [c for c in folder_matches if str(c.get("folder_relevance") or "").lower() in {"strong", "partial"}]
            return (strong or folder_matches or matches)[0]
        return None

    @staticmethod
    def _target_from_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
        filenames = [str(item).strip() for item in (candidate.get("filenames") or []) if str(item).strip()]
        filename = str(candidate.get("filename") or "").strip()
        file_requests = [
            dict(item)
            for item in (candidate.get("file_requests") or [])
            if isinstance(item, dict) and str(item.get("filename") or "").strip()
        ]
        return {
            "username": str(candidate.get("username") or "").strip(),
            "filename": filename or (filenames[0] if filenames else ""),
            "filenames": filenames,
            "file_requests": file_requests,
            "candidate": candidate,
        }

    async def execute(self, arguments: dict[str, Any], context: ToolExecutionContext) -> object:
        """Queue the selected Soulseek file through slskd."""
        if not self._settings_manager:
            return {"ok": False, "recoverable": True, "error_code": "SLSKD_NOT_CONFIGURED", "error": "Settings manager is not available."}
        cfg = self._settings_manager.settings.soulseek
        if not cfg.api_configured:
            return {
                "ok": False,
                "recoverable": True,
                "error_code": "SLSKD_NOT_CONFIGURED",
                "error": "Soulseek/slskd is disabled or missing an API key.",
                "next_actions": ["Use a torrent candidate instead", "Configure Soulseek/slskd in Settings"],
            }
        if getattr(cfg, "managed", True):
            if not cfg.soulseek_credentials_configured:
                return {
                    "ok": False,
                    "recoverable": True,
                    "error_code": "SLSKD_NEEDS_CREDENTIALS",
                    "error": "Soulseek username and password are required before queueing downloads.",
                    "next_actions": ["Use a torrent candidate instead", "Open Settings > Shared Search & Indexers > Soulseek/slskd and enter credentials"],
                }
            if str(getattr(cfg, "account_status", "")).lower() == "auth_failed":
                return {
                    "ok": False,
                    "recoverable": True,
                    "error_code": "SLSKD_AUTH_FAILED",
                    "error": cfg.account_status_message or "Soulseek rejected these credentials.",
                    "next_actions": ["Use a torrent candidate instead", "Open Settings > Shared Search & Indexers > Soulseek/slskd and fix credentials"],
                }
        target = {
            "username": str(arguments.get("username") or "").strip(),
            "filename": str(arguments.get("filename") or "").strip(),
            "filenames": [str(item).strip() for item in (arguments.get("filenames") or []) if str(item).strip()],
            "file_requests": [
                dict(item)
                for item in (arguments.get("file_requests") or [])
                if isinstance(item, dict) and str(item.get("filename") or "").strip()
            ],
            "candidate": None,
        }
        if arguments.get("candidate_id") or (target["username"] and not target["filename"] and not target["filenames"]):
            cached = await self._resolve_candidate_from_cache(arguments, context)
            if cached:
                target = self._target_from_candidate(cached)
        if not target["username"] or (not target["filename"] and not target["filenames"] and not target.get("file_requests")):
            return {
                "ok": False,
                "recoverable": True,
                "error_code": "MISSING_SLSKD_TARGET",
                "error": "Soulseek queueing needs a candidate_id from soulseek_candidate_picker, or username plus exact filename/filenames.",
                "next_actions": [
                    "Use enqueue_soulseek_download with candidate_id and result_set_id from soulseek_candidate_picker.",
                    "Call search_soulseek or search_media_torrents again if no Soulseek candidate_id is visible.",
                ],
            }
        result = await SlskdClient(cfg).enqueue_download(
            username=target["username"],
            filename=target["filename"],
            filenames=target["filenames"],
            file_requests=target.get("file_requests") or None,
        )
        if isinstance(result, dict):
            if target.get("candidate"):
                result["candidate_id"] = target["candidate"].get("candidate_id")
                result["candidate_type"] = target["candidate"].get("candidate_type")
                result["folder"] = target["candidate"].get("folder")
            if result.get("ok") is True:
                shadow = None
                try:
                    shadow = await SlskdTransferReadModel(self._settings_manager, self._database).add_shadow_transfer(
                        username=target["username"],
                        filename=target["filename"],
                        filenames=target["filenames"],
                        file_requests=target.get("file_requests") or None,
                        category_id=str(arguments.get("category_id") or (target.get("candidate") or {}).get("category_id") or "music"),
                        candidate=target.get("candidate") or {},
                        receipt=result.get("receipt") if isinstance(result.get("receipt"), dict) else {},
                    )
                except Exception:
                    shadow = None
                if shadow:
                    result["visible_download"] = shadow
                result.setdefault("import_note", "The Soulseek transfer is queued in slskd and mirrored in the LJS Downloads view. Completed-file import will be handled by the Soulseek transfer monitor when files finish.")
            else:
                result.setdefault("next_actions", [
                    "Do not retry the same Soulseek queue request unchanged.",
                    "Run search_soulseek again and choose another candidate if this user/source rejects the queue.",
                ])
        return result


class GetSoulseekSharePlanTool:
    """Preview the effective slskd share/download configuration."""

    name = "get_soulseek_share_plan"
    description = (
        "Preview what LJS would share through slskd and render a redacted slskd YAML config. "
        "Use this before changing Soulseek sharing preferences or explaining what will be visible to other Soulseek users."
    )
    intents = {Intent.CONFIG, Intent.CHAT, Intent.SEARCH, Intent.DOWNLOAD}
    allow_direct = True
    requires_confirmation = False
    destructive = False
    required_dependencies = ["settings_manager"]

    def __init__(self, settings_manager: Optional["SettingsManager"] = None) -> None:
        self._settings_manager = settings_manager

    def parameters(self) -> dict:
        """Return the JSON schema for share-plan preview arguments."""
        return {"type": "object", "properties": {"include_yaml": {"type": "boolean", "default": True}}, "required": []}

    async def execute(self, arguments: dict[str, Any], context: ToolExecutionContext) -> object:
        """Return the effective share plan and optional redacted YAML."""
        if not self._settings_manager:
            return {"ok": False, "recoverable": True, "error_code": "SLSKD_NOT_CONFIGURED", "error": "Settings manager is not available."}
        settings = self._settings_manager.settings
        plan = build_slskd_share_plan(settings)
        result: dict[str, Any] = {
            "ok": True,
            "source": "slskd",
            "configured": settings.soulseek.api_configured,
            "soulseek_credentials_configured": settings.soulseek.soulseek_credentials_configured,
            "plan": plan.as_public_dict(),
        }
        if arguments.get("include_yaml", True):
            result["redacted_slskd_yaml"] = render_slskd_yaml(settings, redact_secrets=True)
        return result


class SoulseekToolProvider:
    """Tool provider for the slskd/Soulseek companion source."""

    def __init__(self, settings_manager: Optional["SettingsManager"] = None, database: Optional["Database"] = None) -> None:
        self._settings_manager = settings_manager
        self._database = database

    def get_tools(self) -> list:
        """Return Soulseek/slskd tools for the agent registry."""
        return [
            SearchSoulseekTool(settings_manager=self._settings_manager),
            EnqueueSoulseekDownloadTool(settings_manager=self._settings_manager, database=self._database),
            GetSoulseekSharePlanTool(settings_manager=self._settings_manager),
        ]
