"""Round 40 regressions: chat box, category downloads, log hygiene, Jackett profiles."""

from pathlib import Path

from src.search.jackett_indexer_config import JackettIndexerConfigurer, JackettIndexerInfo, JACKETT_INDEXER_PROFILES
from src.utils.log_sanitizer import redact_secrets, redact_url


def test_chat_input_is_multiline_textarea_with_shift_enter() -> None:
    helm = Path("src/web/static/js/components/helmPanel.js").read_text()
    chat = Path("src/web/static/js/components/chatController.js").read_text()
    css = Path("src/web/static/css/style.css").read_text()

    assert "DOM.el('textarea'" in helm
    assert "id: 'chat-input'" in helm
    assert "e.key === 'Enter' && !e.shiftKey" in chat
    assert "this._resizeInput();" in chat
    assert ".chat-input-area textarea" in css
    assert "max-height" in css
    assert "resize: none" in css


def test_category_creation_guidance_requires_researched_download_profiles() -> None:
    prompt = Path("src/ai/prompt_builder.py").read_text()
    guide = Path("skills/category_creation_guide.md").read_text()
    tools = Path("src/ai/tools/categories.py").read_text()

    assert "research_category_download_profile" in prompt
    assert "CATEGORY-DESIGN SAFETY RULE" in prompt
    assert "Category-Specific Download Profiles" in guide
    assert "Downloadability does not mean" in guide
    assert "download_profile" in Path("src/core/domain_models/categories.py").read_text()
    assert "download_profile_research" in Path("src/core/domain_models/categories.py").read_text()
    assert "Do not copy movie/TV release vocabulary" in tools
    assert "creating/refining category types" in Path("src/ai/intent_router.py").read_text()


def test_log_sanitizer_redacts_jackett_api_keys() -> None:
    raw = "http://127.0.0.1:9117/api/v2.0/indexers/all/results/torznab/api?apikey=wr1ofgtr6pferb07qkm784r51hp4dezi&t=search&q="
    redacted = redact_url(raw)
    assert "wr1ofgtr" not in redacted
    assert "apikey=%3Credacted%3E" in redacted or "apikey=<redacted>" in redacted
    assert redact_secrets("token=abc123&x=1") == "token=<redacted>&x=1"


def test_jackett_profiles_and_catalogue_summary_are_domain_aware() -> None:
    assert "audiobooks" in JACKETT_INDEXER_PROFILES
    assert "books" in JACKETT_INDEXER_PROFILES
    configurer = JackettIndexerConfigurer("http://127.0.0.1:9117", "key")
    catalogue = [
        JackettIndexerInfo(id="audiobookbay", name="AudioBook Bay", configured=False, type="public", categories=("audio/audiobook",)),
        JackettIndexerInfo(id="yts", name="YTS", configured=True, type="public", categories=("movie",)),
    ]
    summary = configurer.summarize_catalogue(catalogue)
    assert summary["total_indexers"] == 2
    assert summary["configured_indexers"] == 1
    assert summary["book_or_audio_like_count"] == 1
    assert "Jackett's /all search only queries configured indexers" in summary["note"]
