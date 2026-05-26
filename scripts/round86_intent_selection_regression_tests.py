#!/usr/bin/env python3
"""Round 86 regression traces for download intent, candidate choice, and queueing.

These checks target the exact failure class from the May 24 logs:

* a lower-seeded duplicate was marked as the recommended batch candidate;
* pending torrent-candidate state must remain available to the LLM router
  without natural-language phrase matching;
* queue_download batch resolution must have category-registry access and must
  not crash with a missing private attribute;
* direct category micro-download tools must be normalized back to the small
  generic search/queue chain;
* private self._attribute reads in the AI package must be backed by an
  assignment or method definition, so missing call/attribute regressions are
  caught before runtime.
"""

from __future__ import annotations

import ast
import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.ai.intent_router import IntentRouter
from src.ai.pending_actions import PendingActionContextBuilder
from src.ai.plan_coordinator import PlanCoordinator
from src.ai.tool_policy import AgentToolPolicy
from src.ai.tools.queue_download_support import CachedCandidateResolver
from src.ai.tools.scheduling import _build_batch_recommendation
from src.core.models import AgentPlan, Intent, PlanStep



class FakeBatchCategory:
    """Minimal category hook surface for batch grouping tests."""

    def batch_group_for_candidate(self, candidate: dict, request_context: dict) -> dict | None:
        descriptor = candidate.get("unit_descriptor") or {}
        key = descriptor.get("stable_key")
        if not key:
            return None
        return {
            "key": key,
            "label": descriptor.get("label") or key,
            "sort_key": descriptor.get("sort_key") or [key],
            "descriptor": descriptor,
        }


class FakeDownloadCategory:
    """Minimal category with micro-tools that must stay hidden from DOWNLOAD."""

    def declare_actions(self) -> list:
        return [
            SimpleNamespace(
                llm_visible=True, destructive=False, risk_level="read",
                requires_confirmation=False, exposed_tool_name="tv.inspect_library",
            ),
            SimpleNamespace(
                llm_visible=True, destructive=True, risk_level="destructive",
                requires_confirmation=True, exposed_tool_name="tv.download_missing_batch",
            ),
        ]

    def declare_workflows(self) -> list:
        return [
            SimpleNamespace(tool_name="tv.find_missing_episodes", risk_level="read", requires_confirmation=False),
            SimpleNamespace(tool_name="tv.download_missing_batch", risk_level="destructive", requires_confirmation=True),
        ]


def _candidate(candidate_id: str, *, episode: int, seeders: int, title: str) -> dict:
    return {
        "candidate_id": candidate_id,
        "title": title,
        "seeders": seeders,
        "size_bytes": 987_758_592,
        "languages": ["Italian", "English", "Spanish"],
        "resolution": "1080p",
        "codec": "h265",
        "quality_score": 0.6,
        "unit_descriptor": {
            "stable_key": f"tv:season:5:episode:{episode}",
            "label": f"S05E{episode:02d}",
            "coordinates": {"season": 5, "episode": episode},
            "sort_key": [5, episode],
        },
    }


class _FakeSystemPreferences:
    async def get_preference(self, key: str):
        if key == "torrent_result_sets_web_test":
            return json.dumps(["rs-test"])
        if key.startswith("torrent_result_set_web_test_rs-test"):
            return json.dumps({
                "result_set_id": "rs-test",
                "name": "For All Mankind",
                "category_id": "tv",
                "candidates": [
                    {
                        "candidate_id": "a8b83611a2b9dde1",
                        "title": "For All Mankind S05E04 1080p Ita Eng",
                        "seeders": 88,
                        "languages": ["Italian", "English"],
                        "unit_label": "S05E04",
                    }
                ],
                "batch_recommendation": {
                    "result_set_id": "rs-test",
                    "candidate_ids": ["a8b83611a2b9dde1"],
                    "queue_download_arguments": {
                        "result_set_id": "rs-test",
                        "candidate_ids": ["a8b83611a2b9dde1"],
                    },
                },
            })
        return None


class _FakeDB:
    def __init__(self):
        self.system = _FakeSystemPreferences()


class _FakeRoutingLLM:
    def __init__(self):
        self.prompt = ""

    async def completion(self, **kwargs):
        self.prompt = kwargs["messages"][0]["content"]
        return {"choices": [{"message": {"content": "DOWNLOAD"}}]}


async def test_pending_context_drives_llm_routing_without_phrase_helper() -> None:
    context = await PendingActionContextBuilder(_FakeDB()).build_for_session("web_test")
    assert "rs-test" in context
    assert "queue_download_arguments" in context

    llm = _FakeRoutingLLM()
    router = IntentRouter(llm_client=llm)
    routed = await router.route("metti in coda quello consigliato", context=context)
    assert routed == Intent.DOWNLOAD
    assert "rs-test" in llm.prompt
    assert "metti in coda quello consigliato" in llm.prompt


def test_batch_recommendation_uses_seeders_for_equivalent_candidates() -> None:
    candidates = [
        _candidate(
            "e7d1c7e8d25b909b",
            episode=4,
            seeders=24,
            title="For All Mankind S05e04 Ita Eng Spa 1080p h265 10bit SubS-Me7alh",
        ),
        _candidate(
            "a8b83611a2b9dde1",
            episode=4,
            seeders=88,
            title="For All Mankind S05e04 [1080p Ita Eng Spa h265 10bit SubS] byMe7alh",
        ),
        _candidate(
            "0120174e1f9cf0fe",
            episode=6,
            seeders=109,
            title="For All Mankind S05E06 No Sudden Moves 1080p ATVP WEB-DL DDP5.1 H265-TheBlackKing",
        ),
    ]
    recommendation = _build_batch_recommendation(
        name="For All Mankind",
        category_id="tv",
        season=5,
        episode=None,
        result_set_id="rs-test",
        candidates=candidates,
        category=FakeBatchCategory(),
        preferred_language="Italian",
    )
    assert recommendation, "expected multi-unit batch recommendation"
    assert recommendation["candidate_ids"] == ["a8b83611a2b9dde1", "0120174e1f9cf0fe"], recommendation
    assert recommendation["queue_download_arguments"]["candidate_ids"] == recommendation["candidate_ids"]


def test_cached_candidate_resolver_has_category_registry_attribute() -> None:
    categories = object()
    resolver = CachedCandidateResolver(database=object(), categories=categories)
    assert getattr(resolver, "_categories") is categories


def test_download_intent_exposes_only_generic_tools() -> None:
    allowed = AgentToolPolicy().allowed_tool_names(Intent.DOWNLOAD, category=FakeDownloadCategory())
    assert "search_media_torrents" in allowed
    assert "queue_download" in allowed
    assert "enquire_about_media" in allowed
    assert "tv.find_missing_episodes" not in allowed
    assert "tv.download_missing_batch" not in allowed



def test_intent_tool_surfaces_remain_intent_specific() -> None:
    policy = AgentToolPolicy()
    category = FakeDownloadCategory()

    chat_tools = policy.allowed_tool_names(Intent.CHAT, category=category)
    search_tools = policy.allowed_tool_names(Intent.SEARCH, category=category)
    download_tools = policy.allowed_tool_names(Intent.DOWNLOAD, category=category)
    config_tools = policy.allowed_tool_names(Intent.CONFIG, category=category, confirmed=True)

    assert "queue_download" not in chat_tools
    assert "queue_download" not in search_tools
    assert "search_media_torrents" in download_tools
    assert "queue_download" in download_tools
    assert "tv.find_missing_episodes" not in download_tools
    assert "tv.download_missing_batch" not in download_tools
    # CONFIG remains the place for explicit category actions/workflows. This is
    # intentionally separate from ordinary chat DOWNLOAD planning.
    assert "tv.find_missing_episodes" not in chat_tools
    assert "tv.download_missing_batch" in config_tools

def test_direct_category_download_plan_is_rewritten_to_generic_search() -> None:
    item = SimpleNamespace(key="For All Mankind", language="Italian", last_season=5)
    settings = SimpleNamespace(tracked_items=[item])
    coordinator = PlanCoordinator(tool_executor=None, llm_client=None, settings=settings)
    plan = AgentPlan(
        intent=Intent.DOWNLOAD,
        user_goal="grab missing episodes from latest season of For All Mankind",
        steps=[
            PlanStep(
                id="bad_direct_category_tool",
                tool_name="tv.download_missing_batch",
                arguments={"item_id": "<item_id_from_library>", "season": "latest"},
            )
        ],
    )
    normalized = coordinator._normalize_download_plan(
        plan,
        "Hi ! Can you please grab me the episodes i am missing from the latest season of For All Mankind ?",
        {"enquire_about_media", "search_media_torrents", "queue_download"},
    )
    assert [step.tool_name for step in normalized.steps] == ["search_media_torrents"]
    assert normalized.steps[0].arguments == {"name": "For All Mankind", "language": "Italian"}


def test_ai_private_attribute_reads_are_defined() -> None:
    missing: list[tuple[str, str, list[str]]] = []
    for path in (ROOT / "src/ai").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for cls in [node for node in ast.walk(tree) if isinstance(node, ast.ClassDef)]:
            methods = {node.name for node in cls.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
            class_attrs: set[str] = set()
            assigned: set[str] = set()
            used: set[str] = set()
            for stmt in cls.body:
                if isinstance(stmt, (ast.Assign, ast.AnnAssign)):
                    targets = stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target]
                    for target in targets:
                        if isinstance(target, ast.Name) and target.id.startswith("_"):
                            class_attrs.add(target.id)
            for node in ast.walk(cls):
                if (
                    isinstance(node, ast.Attribute)
                    and isinstance(node.value, ast.Name)
                    and node.value.id == "self"
                    and node.attr.startswith("_")
                ):
                    if isinstance(node.ctx, ast.Store):
                        assigned.add(node.attr)
                    elif isinstance(node.ctx, ast.Load):
                        used.add(node.attr)
            unresolved = sorted(used - assigned - class_attrs - methods)
            if unresolved:
                missing.append((str(path.relative_to(ROOT)), cls.name, unresolved))
    assert not missing, json.dumps(missing, indent=2)


def main() -> None:
    test_batch_recommendation_uses_seeders_for_equivalent_candidates()
    asyncio.run(test_pending_context_drives_llm_routing_without_phrase_helper())
    test_cached_candidate_resolver_has_category_registry_attribute()
    test_download_intent_exposes_only_generic_tools()
    test_intent_tool_surfaces_remain_intent_specific()
    test_direct_category_download_plan_is_rewritten_to_generic_search()
    test_ai_private_attribute_reads_are_defined()
    print("Round 86 intent/selection/queue regression traces passed")


if __name__ == "__main__":
    main()
