"""Round 285 regressions for plural category routing and TV language recall."""

from __future__ import annotations

import asyncio
from types import MethodType, SimpleNamespace

import pytest
from unittest.mock import AsyncMock

from src.ai.category_resolver import CategoryResolver
from src.ai.tools.library import EnquireAboutMediaTool
from src.ai.tools.scheduling import SearchMediaTorrentsTool
from src.ai.tool_policy import AgentToolPolicy
from src.core.categories.registry import CategoryRegistry
from src.core.categories.identity_resolution import CategoryIdentityResolver
from src.core.categories.router_matching import router_token_matches
from src.core.categories.tv import TvShowCategory
from src.core.models import Intent, Settings, ToolExecutionContext
from src.core.search_pipeline import SearchPipeline


class _RecordingScheduler:
    def __init__(self, registry: object | None = None) -> None:
        self.category_registry = registry
        self.calls: list[dict] = []

    async def search_media_torrents(self, **kwargs):
        self.calls.append(kwargs)
        return {"ok": True, "query": kwargs["name"], "category_id": kwargs.get("category_id")}


class _EnquiryCategory:
    category_id = "tv"

    async def enquire(self, item_name, settings, database):
        return {"resolved_by": "tv", "title": item_name}


class _EnquiryRegistry:
    def __init__(self) -> None:
        self.category = _EnquiryCategory()

    def get(self, category_id: str):
        return self.category if category_id == "tv" else None

    def resolve_from_text(self, item_name: str, tracked_items=None):
        return None


def test_regular_plural_router_tokens_match_without_reintroducing_substrings() -> None:
    assert router_token_matches("available episodes", "episode")
    assert router_token_matches("download these movies", "movie")
    assert router_token_matches("find albums and tracks", "album")
    assert router_token_matches("find albums and tracks", "track")
    assert router_token_matches("season packs", "season pack")
    assert not router_token_matches("please find Blur", "ep")
    assert not router_token_matches("seasonal recommendations", "season")
    assert not router_token_matches("moviegoer", "movie")


def test_download_surface_cannot_bypass_category_owned_search() -> None:
    policy = AgentToolPolicy()
    tv = CategoryRegistry.with_defaults().get("tv")
    allowed = policy.allowed_tool_names(Intent.DOWNLOAD, category=tv)
    assert "search_media_torrents" in allowed
    assert "search_torrents" not in allowed
    assert "search_soulseek" not in allowed
    assert "enqueue_soulseek_download" in allowed


def test_exact_silo_incident_prompt_resolves_tv() -> None:
    resolver = CategoryResolver(CategoryRegistry.with_defaults(), Settings())
    resolution = resolver.resolve_with_reason(
        "please grab me the available episodes of Silo in italian",
        Intent.DOWNLOAD,
    )
    assert resolution.category_id == "tv"
    assert resolution.confidence == pytest.approx(0.65)
    assert "verification" in resolution.reason


@pytest.mark.asyncio
async def test_enquiry_uses_resolved_context_category_when_model_omits_argument() -> None:
    tracked = TvShowCategory().create_item("Silo", language="Italian")
    tool = EnquireAboutMediaTool(
        settings_manager=SimpleNamespace(settings=Settings(tracked_items=[tracked])),
        database=object(),
        category_registry=_EnquiryRegistry(),
    )
    result = await tool.execute(
        {"item_name": "Silo"},
        ToolExecutionContext(category_id="tv", user_prompt="available episodes of Silo"),
    )
    assert result["resolved_by"] == "tv"
    assert result["category_id"] == "tv"


@pytest.mark.asyncio
async def test_search_uses_resolved_context_category_when_model_omits_argument() -> None:
    scheduler = _RecordingScheduler(CategoryRegistry.with_defaults())
    tool = SearchMediaTorrentsTool(scheduler=scheduler)
    result = await tool.execute(
        {"name": "Silo", "language": "Italian", "language_is_explicit": True},
        ToolExecutionContext(
            category_id="tv",
            user_prompt="please grab me the available episodes of Silo in italian",
        ),
    )
    assert result["category_id"] == "tv"
    assert scheduler.calls[0]["category_id"] == "tv"
    assert scheduler.calls[0]["language"] == "Italian"
    assert scheduler.calls[0]["search_constraints"]["request_text"] == (
        "please grab me the available episodes of Silo in italian"
    )


@pytest.mark.asyncio
async def test_lexical_prompt_hint_alone_cannot_authorize_search() -> None:
    scheduler = _RecordingScheduler(CategoryRegistry.with_defaults())
    tool = SearchMediaTorrentsTool(scheduler=scheduler)
    result = await tool.execute(
        {"name": "Silo", "language": "Italian", "language_is_explicit": True},
        ToolExecutionContext(
            user_prompt="please grab me the available episodes of Silo in italian",
        ),
    )
    assert result["error_code"] == "category_resolution_required"
    assert scheduler.calls == []


@pytest.mark.asyncio
async def test_unresolved_rich_media_search_fails_before_abstract_media_fallback() -> None:
    scheduler = _RecordingScheduler(CategoryRegistry.with_defaults())
    tool = SearchMediaTorrentsTool(scheduler=scheduler)
    result = await tool.execute(
        {"name": "Ambiguous Title"},
        ToolExecutionContext(user_prompt="please download Ambiguous Title"),
    )
    assert result["error_code"] == "category_resolution_required"
    assert scheduler.calls == []


def test_preferred_exact_episode_suppresses_unknown_bundle_fallback() -> None:
    from src.core.domain_models.downloads import SearchResult

    tv = TvShowCategory()
    exact = SearchResult(
        title="Silo S02E01 1080p WEB-DL ITA",
        magnet="magnet:?xt=urn:btih:exact-ita",
        size="2 GB",
        seeders=12,
        source="fixture",
    )
    unknown_pack = SearchResult(
        title="Silo S02 Complete 1080p WEB-DL",
        magnet="magnet:?xt=urn:btih:pack-unknown",
        size="20 GB",
        seeders=30,
        source="fixture",
    )

    assert tv._select_exact_label_results([exact], [unknown_pack], "Italian") == [exact]


def test_bundle_remains_when_no_direct_episode_release_exists() -> None:
    from src.core.domain_models.downloads import SearchResult

    tv = TvShowCategory()
    pack = SearchResult(
        title="Silo S02 Complete 1080p WEB-DL ITA",
        magnet="magnet:?xt=urn:btih:pack-ita",
        size="20 GB",
        seeders=30,
        source="fixture",
    )

    assert tv._select_exact_label_results([], [pack], "Italian") == [pack]


def test_non_english_unknown_pack_cannot_stop_exact_episode_fallback() -> None:
    tv = TvShowCategory()
    unknown_pack = SimpleNamespace(title="Silo S02 Complete 1080p WEB-DL")
    italian_pack = SimpleNamespace(title="Silo S02 Complete 1080p WEB-DL ITA")
    assert tv._pack_results_need_individual_fallback([unknown_pack], language="Italian") is True
    assert tv._pack_results_need_individual_fallback([italian_pack], language="Italian") is False


@pytest.mark.asyncio
async def test_italian_episode_results_are_searched_after_untagged_pack() -> None:
    tv = TvShowCategory()
    item = tv.create_item("Silo", language="Italian")
    unknown_pack = SimpleNamespace(title="Silo S02 Complete 1080p WEB-DL", magnet="pack")
    italian_episode = SimpleNamespace(title="Silo S02E01 1080p WEB-DL ITA", magnet="episode")
    calls: list[str] = []

    async def fake_pack(self, item, season, *, language, context, summary_suffix=None):
        calls.append("pack")
        return [unknown_pack], "pack summary"

    async def fake_labels(self, item, labels, *, language, season, episode, context, summary_suffix=None):
        calls.append("episodes")
        assert labels == ["S02E01"]
        return [italian_episode], "episode summary"

    async def fake_fallback_labels(self, item, season, context):
        return ["S02E01"]

    tv._run_agent_pack_queries = MethodType(fake_pack, tv)
    tv._run_agent_labels = MethodType(fake_labels, tv)
    tv._episode_fallback_labels_for_agent = MethodType(fake_fallback_labels, tv)

    results, _ = await tv.search_agent_candidates(
        item,
        season=2,
        language="Italian",
        search_scope="bundle_preferred",
        context=SimpleNamespace(),
    )
    assert calls == ["pack", "episodes"]
    assert results == [italian_episode]


def test_latest_pack_season_excludes_provider_announced_future_season() -> None:
    tv = TvShowCategory()
    payload = {
        "number_of_seasons": 3,
        "seasons": [
            {"season_number": 1, "air_date": "2023-05-05", "episode_count": 10},
            {"season_number": 2, "air_date": "2024-11-15", "episode_count": 10},
            {"season_number": 3, "air_date": "2999-01-01", "episode_count": 10},
        ],
    }
    assert tv._latest_season_from_payload(payload) == 2


@pytest.mark.asyncio
async def test_episode_count_without_airdate_does_not_invent_downloadable_units() -> None:
    tv = TvShowCategory()
    item = tv.create_item("Silo", language="Italian")

    async def no_aired_units(self, item, season, context):
        return []

    async def should_not_be_used(self, title, season, arguments, context):
        raise AssertionError("catalogue episode_count must not become release evidence")

    tv._infer_missing_or_likely_episodes_for_agent = MethodType(no_aired_units, tv)
    tv._expected_episode_count = MethodType(should_not_be_used, tv)
    assert await tv._episode_fallback_labels_for_agent(item, 3, SimpleNamespace()) == []


def test_tmdb_name_cleanup_has_module_level_regex_dependency() -> None:
    from src.core.categories.metadata.enricher import TMDBMetadataEnricher

    cleaned = TMDBMetadataEnricher()._clean_item_name("Nobody 2 2025 1080p ITA", media_hint="movie")
    assert cleaned


@pytest.mark.asyncio
async def test_candidate_review_timeout_returns_deterministic_workspace() -> None:
    import asyncio

    class CandidateScheduler(_RecordingScheduler):
        async def search_media_torrents(self, **kwargs):
            self.calls.append(kwargs)
            return {
                "name": "Silo",
                "display_name": "Silo",
                "category_id": "tv",
                "language": "Italian",
                "query": "Silo S02 ITA",
                "search_scope": "bundle_preferred",
                "candidates": [
                    {
                        "title": "Silo S02E01 1080p WEB-DL ITA",
                        "magnet": "magnet:?xt=urn:btih:round285",
                        "size": "2147483648",
                        "size_bytes": 2147483648,
                        "seeders": 12,
                        "source": "test",
                        "languages": ["Italian"],
                        "resolution": "1080p",
                        "codec": "h264",
                    }
                ],
            }

    scheduler = CandidateScheduler(CategoryRegistry.with_defaults())
    tool = SearchMediaTorrentsTool(scheduler=scheduler, llm_client=object())
    tool._CANDIDATE_REVIEW_TIMEOUT_SECONDS = 0.01

    async def slow_review(**kwargs):
        await asyncio.sleep(1)
        return {"recommended_candidate_ids": []}

    tool._candidate_adjudicator.review = slow_review
    result = await tool.execute(
        {"name": "Silo", "category_id": "tv", "language": "Italian", "language_is_explicit": True},
        ToolExecutionContext(category_id="tv", user_prompt="available episodes of Silo in italian"),
    )
    assert result["llm_candidate_review_status"] == "timed_out_deterministic_fallback"
    assert result["candidate_count"] == 1
    assert result["candidate_picker"][0]["title"].startswith("Silo S02E01")


@pytest.mark.asyncio
async def test_scheduler_service_itself_rejects_abstract_media_fallback() -> None:
    from src.core.scheduler_services import SchedulerServiceContext, SchedulerTorrentSearchService

    # The category-resolution guard runs before provider/downloader collaborators
    # are needed, so inert placeholders are sufficient for this contract test.
    context = SchedulerServiceContext(
        db=object(),
        downloader=object(),
        pipeline=object(),
        aggregator=object(),
        settings_manager=SimpleNamespace(settings=Settings()),
        categories=CategoryRegistry.with_defaults(),
        metadata_enricher=None,
        tvmaze=None,
    )
    service = SchedulerTorrentSearchService(context)
    result = await service.search_media_torrents("Ambiguous Title")
    assert result["ok"] is False
    assert result["error_code"] == "category_resolution_required"


@pytest.mark.asyncio
async def test_literal_user_language_survives_missing_model_explicit_flag() -> None:
    scheduler = _RecordingScheduler(CategoryRegistry.with_defaults())
    tool = SearchMediaTorrentsTool(scheduler=scheduler)
    await tool.execute(
        {"name": "Silo", "language": "Italian"},
        ToolExecutionContext(
            category_id="tv",
            user_prompt="please grab me the available episodes of Silo in italian",
        ),
    )
    assert scheduler.calls[0]["language"] == "Italian"
    assert scheduler.calls[0]["language_explicit"] is True


@pytest.mark.asyncio
async def test_localized_language_alias_survives_missing_model_explicit_flag() -> None:
    scheduler = _RecordingScheduler(CategoryRegistry.with_defaults())
    tool = SearchMediaTorrentsTool(scheduler=scheduler)
    await tool.execute(
        {"name": "Silo", "language": "Italian"},
        ToolExecutionContext(
            category_id="tv",
            user_prompt="scarica gli episodi disponibili di Silo in italiano",
        ),
    )
    assert scheduler.calls[0]["language"] == "Italian"
    assert scheduler.calls[0]["language_explicit"] is True


@pytest.mark.asyncio
async def test_tv_pack_query_ladder_uses_release_tag_and_language_word() -> None:
    tv = TvShowCategory()
    item = tv.create_item("Silo", language="Italian")
    queries = await tv.agent_pack_search_queries(item, 2, language="Italian", context=None)
    folded = [query.casefold() for query in queries]
    assert any("s02 ita" in query for query in folded)
    assert any("s02 italian" in query for query in folded)


def test_tv_exact_episode_query_ladder_includes_ita_variant() -> None:
    tv = TvShowCategory()
    item = tv.create_item("Silo", language="Italian")
    queries = tv._agent_exact_label_queries(item, "S02E01", "Italian")
    assert any("s02e01" in query.casefold() and "ita" in query.casefold() for query in queries)

@pytest.mark.asyncio
async def test_interactive_tv_search_skips_legacy_pipeline_ranker() -> None:
    tv = TvShowCategory()
    item = tv.create_item("Silo", language="Italian")
    pipeline = SimpleNamespace(run_search=AsyncMock(return_value=[]))
    context = SimpleNamespace(pipeline=pipeline, settings=Settings(), db=None)
    results, _ = await tv._run_agent_labels(
        item,
        [None],
        language="Italian",
        season=2,
        episode=None,
        context=context,
    )
    assert results == []
    assert pipeline.run_search.await_args.kwargs["rank_candidates"] is False


@pytest.mark.asyncio
async def test_legacy_pipeline_ranker_is_bounded_and_falls_back() -> None:
    pipeline = object.__new__(SearchPipeline)
    pipeline._LLM_RANK_TIMEOUT_SECONDS = 0.01

    async def _slow_rank(self, candidates, item, episode_label, language):
        await asyncio.sleep(1)
        return candidates

    pipeline._llm_rank = MethodType(_slow_rank, pipeline)
    result = await pipeline._safe_llm_rank(
        [SimpleNamespace(title="Silo S02E01 ITA")],
        SimpleNamespace(key="Silo"),
        "S02E01",
        "Italian",
    )
    assert result is None

@pytest.mark.asyncio
async def test_available_episodes_request_resolves_latest_aired_season_before_search() -> None:
    tv = TvShowCategory()
    item = tv.create_item("Silo", language="Italian")

    async def _identity(self, value, context):
        return value

    async def _latest(self, value, context):
        return 2

    async def _pack(self, value, season, *, language, context, summary_suffix=None):
        assert season == 2
        return [], "Silo S02 pack"

    async def _fallback_labels(self, value, season, context):
        assert season == 2
        return ["S02E01", "S02E02"]

    async def _labels(self, value, labels, *, language, season, episode, context, summary_suffix=None):
        assert season == 2
        assert labels == ["S02E01", "S02E02"]
        return [SimpleNamespace(title="Silo S02E01 ITA")], "Italian episode fallback"

    tv._ensure_agent_title_authority = MethodType(_identity, tv)
    tv.resolve_agent_pack_season = MethodType(_latest, tv)
    tv._run_agent_pack_queries = MethodType(_pack, tv)
    tv._episode_fallback_labels_for_agent = MethodType(_fallback_labels, tv)
    tv._run_agent_labels = MethodType(_labels, tv)

    context = SimpleNamespace(
        search_constraints={
            "request_text": "please grab me the available episodes of Silo in italian",
            "unit_scope": "available_units",
        }
    )
    results, summary = await tv.search_agent_candidates(
        item,
        language="Italian",
        context=context,
    )
    assert [row.title for row in results] == ["Silo S02E01 ITA"]
    assert "Italian episode fallback" in summary


def test_literal_request_language_is_not_used_as_unit_scope_authority() -> None:
    context = SimpleNamespace(search_constraints={"request_text": "download all Silo episodes"})
    assert TvShowCategory._requested_agent_unit_scope(context) == ""


@pytest.mark.asyncio
async def test_plain_tv_title_search_fails_closed_instead_of_running_broad_query() -> None:
    tv = TvShowCategory()
    item = tv.create_item("Silo", language="Italian")

    async def _identity(self, value, context):
        return value

    tv._ensure_agent_title_authority = MethodType(_identity, tv)
    results, summary = await tv.search_agent_candidates(
        item,
        language="Italian",
        context=SimpleNamespace(search_constraints={"request_text": "Silo"}),
    )
    assert results == []
    assert "needs a season, episode, or structured unit_scope" in summary



def _registry_with_identity_evidence(
    evidence_by_category: dict[str, list[dict]],
) -> CategoryRegistry:
    registry = CategoryRegistry.with_defaults()
    for category in registry.list_all():
        rows = list(evidence_by_category.get(category.category_id, []))

        async def identify(self, name, *, settings, db=None, metadata_clients=None, _rows=rows):
            return list(_rows)

        category.identify_agent_item = MethodType(identify, category)
    return registry


class _IdentityScheduler(_RecordingScheduler):
    def __init__(self, identity):
        super().__init__(CategoryRegistry.with_defaults())
        self.identity = identity
        self.identity_calls = []

    async def resolve_agent_media_identity(self, item_name, *, category_hint=None, request_text=None):
        self.identity_calls.append({
            "item_name": item_name,
            "category_hint": category_hint,
            "request_text": request_text,
        })
        return dict(self.identity)


@pytest.mark.asyncio
async def test_metadata_identity_resolution_does_not_depend_on_request_language() -> None:
    resolver = CategoryIdentityResolver(
        settings_manager=SimpleNamespace(settings=Settings()),
        database=None,
        category_registry=_registry_with_identity_evidence({
            "tv": [
                {
                    "category_id": "tv",
                    "title": "Silo",
                    "source": "tvmaze",
                    "base_score": 0.22,
                    "external_id": "72211",
                    "year": "2023",
                }
            ]
        }),
    )
    for request in (
        "scarica gli episodi disponibili di Silo in italiano",
        "descarga los capítulos disponibles de Silo en italiano",
        "Silo の利用可能なエピソードをイタリア語で取得して",
    ):
        result = await resolver.resolve("Silo", request_text=request)
        assert result["resolved"] is True
        assert result["category_id"] == "tv"
        assert result["source"] == "category_metadata"


@pytest.mark.asyncio
async def test_model_category_hint_cannot_override_provider_identity() -> None:
    resolver = CategoryIdentityResolver(
        settings_manager=SimpleNamespace(settings=Settings()),
        database=None,
        category_registry=_registry_with_identity_evidence({
            "tv": [
                {
                    "category_id": "tv",
                    "title": "Silo",
                    "source": "tvmaze",
                    "base_score": 0.22,
                    "external_id": "72211",
                    "year": "2023",
                }
            ]
        }),
    )
    result = await resolver.resolve("Silo", category_hint="movie", request_text="download Silo")
    assert result["resolved"] is True
    assert result["category_id"] == "tv"


@pytest.mark.asyncio
async def test_cross_category_metadata_collision_requires_clarification() -> None:
    registry = _registry_with_identity_evidence({
        "movie": [{
            "category_id": "movie",
            "title": "Dune",
            "source": "tmdb_movie",
            "base_score": 0.24,
            "external_id": "438631",
            "year": "2021",
        }],
        "ebooks": [{
            "category_id": "ebooks",
            "title": "Dune",
            "source": "open_library",
            "base_score": 0.25,
            "external_id": "OL45804W",
            "year": "1965",
        }],
    })
    resolver = CategoryIdentityResolver(
        settings_manager=SimpleNamespace(settings=Settings()),
        database=None,
        category_registry=registry,
    )
    result = await resolver.resolve("Dune", request_text="download Dune")
    assert result["status"] == "ambiguous"
    assert set(result["ambiguous_categories"]) >= {"movie", "ebooks"}
    assert result["clarification_required"] is True


@pytest.mark.asyncio
async def test_search_uses_metadata_resolution_even_without_category_words() -> None:
    scheduler = _IdentityScheduler({
        "status": "resolved",
        "resolved": True,
        "category_id": "tv",
        "confidence": 0.95,
        "source": "metadata_provider",
    })
    tool = SearchMediaTorrentsTool(scheduler=scheduler)
    await tool.execute(
        {"name": "Silo", "language": "Italian", "language_is_explicit": True},
        ToolExecutionContext(user_prompt="Silo, per favore, in italiano"),
    )
    assert scheduler.identity_calls[0]["item_name"] == "Silo"
    assert scheduler.calls[0]["category_id"] == "tv"


@pytest.mark.asyncio
async def test_search_returns_provider_backed_clarification_instead_of_guessing() -> None:
    scheduler = _IdentityScheduler({
        "status": "ambiguous",
        "resolved": False,
        "category_id": None,
        "reason": "Metadata found a movie and an ebook.",
        "clarification_question": "Do you mean the Dune movie or ebook?",
        "ambiguous_categories": ["movie", "ebooks"],
    })
    tool = SearchMediaTorrentsTool(scheduler=scheduler)
    result = await tool.execute(
        {"name": "Dune"},
        ToolExecutionContext(user_prompt="download Dune"),
    )
    assert result["error_code"] == "category_ambiguous"
    assert result["clarification_question"] == "Do you mean the Dune movie or ebook?"
    assert scheduler.calls == []


def test_search_argument_constraints_preserve_structured_multilingual_unit_scope() -> None:
    from src.ai.tools.search_workspace import SearchArgumentConstraints

    assert SearchArgumentConstraints.from_arguments({"unit_scope": "available_units"}) == {
        "unit_scope": "available_units"
    }
    assert SearchArgumentConstraints.from_arguments({"unit_scope": "whatever-the-model-guessed"}) == {}


@pytest.mark.asyncio
async def test_tv_category_owns_tvmaze_identity_probe() -> None:
    class FakeTVMaze:
        async def search(self, query):
            assert query == "Silo"
            return [{"id": 72211, "name": "Silo", "year": "2023"}]

    rows = await TvShowCategory().identify_agent_item(
        "Silo",
        settings=Settings(),
        metadata_clients={"tvmaze": FakeTVMaze()},
    )
    assert rows == [{
        "category_id": "tv",
        "title": "Silo",
        "source": "tvmaze",
        "base_score": 0.22,
        "external_id": "72211",
        "year": "2023",
        "evidence": [],
    }]


@pytest.mark.asyncio
async def test_movie_category_owns_tmdb_movie_identity_probe() -> None:
    from src.core.categories.movie import MovieCategory

    class FakeTMDB:
        async def search(self, query, media_type="multi"):
            assert (query, media_type) == ("Dune", "movie")
            return [{"id": 438631, "title": "Dune", "year": "2021"}]

    settings = Settings(category_settings={
        "movie": {"services": {"tmdb": {"enabled": True, "api_key": "test-key"}}}
    })
    rows = await MovieCategory().identify_agent_item(
        "Dune",
        settings=settings,
        metadata_clients={"tmdb": FakeTMDB()},
    )
    assert rows[0]["category_id"] == "movie"
    assert rows[0]["source"] == "tmdb_movie"
    assert rows[0]["external_id"] == "438631"


@pytest.mark.asyncio
async def test_category_cannot_return_identity_evidence_for_another_category() -> None:
    registry = _registry_with_identity_evidence({
        "tv": [{
            "category_id": "movie",
            "title": "Silo",
            "source": "bad_probe",
            "base_score": 0.35,
        }]
    })
    result = await CategoryIdentityResolver(
        settings_manager=SimpleNamespace(settings=Settings()),
        database=None,
        category_registry=registry,
    ).resolve("Silo")
    assert result["resolved"] is False
    assert result["status"] == "unresolved"


def test_search_tool_ignores_mock_synthesized_identity_resolver() -> None:
    """Only real scheduler identity seams may trigger metadata resolution."""
    from unittest.mock import MagicMock

    scheduler = MagicMock()
    assert SearchMediaTorrentsTool._scheduler_identity_resolver(scheduler) is None


def test_scheduler_normalizes_structured_scope_and_literal_request() -> None:
    from src.core.scheduler_services import SchedulerServiceContext, SchedulerTorrentSearchService

    context = SchedulerServiceContext(
        db=object(),
        downloader=object(),
        pipeline=object(),
        aggregator=object(),
        settings_manager=SimpleNamespace(settings=Settings()),
        categories=CategoryRegistry.with_defaults(),
        metadata_enricher=None,
        tvmaze=None,
    )
    service = SchedulerTorrentSearchService(context)
    assert service._normalize_search_constraints({
        "unit_scope": "available_units",
        "request_text": "scarica gli episodi disponibili di Silo in italiano",
    }) == {
        "unit_scope": "available_units",
        "request_text": "scarica gli episodi disponibili di Silo in italiano",
    }


@pytest.mark.asyncio
async def test_scheduler_does_not_treat_lexical_title_words_as_category_authority() -> None:
    from src.core.scheduler_services import SchedulerServiceContext, SchedulerTorrentSearchService

    context = SchedulerServiceContext(
        db=object(),
        downloader=object(),
        pipeline=object(),
        aggregator=object(),
        settings_manager=SimpleNamespace(settings=Settings()),
        categories=CategoryRegistry.with_defaults(),
        metadata_enricher=None,
        tvmaze=None,
    )
    service = SchedulerTorrentSearchService(context)
    result = await service.search_media_torrents("Silo episodes in Italian")
    assert result["error_code"] == "category_resolution_required"
    assert result["ok"] is False


@pytest.mark.asyncio
async def test_tool_transports_structured_scope_to_scheduler_without_language_keywords() -> None:
    scheduler = _IdentityScheduler({
        "status": "resolved",
        "resolved": True,
        "category_id": "tv",
        "confidence": 0.95,
        "source": "category_metadata",
    })
    tool = SearchMediaTorrentsTool(scheduler=scheduler)
    await tool.execute(
        {
            "name": "Silo",
            "language": "Italian",
            "language_is_explicit": True,
            "unit_scope": "available_units",
        },
        ToolExecutionContext(user_prompt="Silo, per favore, in italiano"),
    )
    constraints = scheduler.calls[0]["search_constraints"]
    assert constraints["unit_scope"] == "available_units"
    assert constraints["request_text"] == "Silo, per favore, in italiano"


def test_tv_bare_title_does_not_implicitly_become_latest_season_pack() -> None:
    tv = TvShowCategory()
    item = tv.create_item("Silo", language="Italian")
    assert tv.default_agent_search_scope(
        item,
        season=None,
        episode=None,
        search_scope="default",
        language="Italian",
        context=SimpleNamespace(search_constraints={}),
    ) == "default"


def test_tv_structured_available_scope_becomes_pack_preferred() -> None:
    tv = TvShowCategory()
    item = tv.create_item("Silo", language="Italian")
    assert tv.default_agent_search_scope(
        item,
        season=None,
        episode=None,
        search_scope="default",
        language="Italian",
        context=SimpleNamespace(search_constraints={"unit_scope": "available_units"}),
    ) == "bundle_preferred"


def test_download_prompt_requires_metadata_identity_and_reuses_enquiry_arguments() -> None:
    from src.ai.task_prompt_guidance import TaskPromptGuidance

    rules = TaskPromptGuidance.download_task_rules()
    assert "clarification_question" in rules
    assert "recommended_search_arguments" in rules
    assert "English keywords" in rules


@pytest.mark.asyncio
async def test_full_scheduler_tv_path_reaches_exact_italian_aired_episode_queries() -> None:
    """Reproduce the Silo incident through scheduler -> TV search, not helpers."""
    from src.core.domain_models.downloads import SearchResult
    from src.core.scheduler_services import SchedulerServiceContext, SchedulerTorrentSearchService

    class FakeMediaRepo:
        async def get_category_metadata(self, category_id, item_id):
            assert (category_id, item_id) == ("tv", "Silo")
            return [{
                "metadata": {
                    "number_of_seasons": 2,
                    "seasons": [
                        {"season_number": 1, "air_date": "2023-05-05", "episode_count": 10},
                        {"season_number": 2, "air_date": "2024-11-15", "episode_count": 2},
                    ],
                }
            }]

        async def list_category_units(self, category_id, item_id, status=None):
            assert (category_id, item_id, status) == ("tv", "Silo", "downloaded")
            return []

        async def get_item_progress(self, category_id, item_id):
            return {}

    class FakeDB:
        media = FakeMediaRepo()

    class FakeTVMaze:
        async def search(self, title):
            return [{"id": 72211, "name": "Silo", "year": "2023"}]

        async def get_episode_list(self, show_id):
            assert show_id == 72211
            return [
                {"season": 2, "number": 1, "airdate": "2024-11-15"},
                {"season": 2, "number": 2, "airdate": "2024-11-22"},
            ]

    class FakeAggregator:
        def __init__(self):
            self.queries = []

        async def search(self, query, **kwargs):
            self.queries.append((query, kwargs))
            folded = query.casefold()
            if "s02e01" in folded and "ita" in folded:
                return [SearchResult(
                    title="Silo S02E01 1080p WEB-DL ITA",
                    magnet="magnet:?xt=urn:btih:silo-e01",
                    size="2 GB",
                    seeders=25,
                    source="fixture",
                )]
            if "s02e02" in folded and "ita" in folded:
                return [SearchResult(
                    title="Silo S02E02 1080p WEB-DL ITA",
                    magnet="magnet:?xt=urn:btih:silo-e02",
                    size="2 GB",
                    seeders=18,
                    source="fixture",
                )]
            if "silo" in folded and "s02" in folded and "e0" not in folded:
                return [SearchResult(
                    title="Silo S02 Complete 1080p WEB-DL",
                    magnet="magnet:?xt=urn:btih:silo-pack-unknown-language",
                    size="20 GB",
                    seeders=40,
                    source="fixture",
                )]
            return []

        def last_search_degraded(self):
            return False

    tv = TvShowCategory()
    tracked = tv.create_item("Silo", language="Italian")
    settings = Settings(tracked_items=[tracked])
    aggregator = FakeAggregator()
    service = SchedulerTorrentSearchService(SchedulerServiceContext(
        db=FakeDB(),
        downloader=object(),
        pipeline=object(),
        aggregator=aggregator,
        settings_manager=SimpleNamespace(settings=settings),
        categories=CategoryRegistry.with_defaults(),
        metadata_enricher=None,
        tvmaze=FakeTVMaze(),
    ))

    result = await service.search_media_torrents(
        "Silo",
        category_id="tv",
        language="Italian",
        language_explicit=True,
        search_constraints={
            "unit_scope": "available_units",
            "request_text": "scarica gli episodi disponibili di Silo in italiano",
        },
    )

    titles = [row["title"] for row in result["candidates"]]
    queries = [query for query, _ in aggregator.queries]
    assert result["category_id"] == "tv"
    assert result["season"] == 2
    assert result["search_scope"] == "bundle_preferred"
    assert set(titles) == {
        "Silo S02E01 1080p WEB-DL ITA",
        "Silo S02E02 1080p WEB-DL ITA",
    }
    assert any("Silo S02" in query and "ITA" in query for query in queries)
    assert any("Silo S02E01" in query and "ITA" in query for query in queries)
    assert any("Silo S02E02" in query and "ITA" in query for query in queries)
    assert all(query != "Silo Italian" for query in queries)
