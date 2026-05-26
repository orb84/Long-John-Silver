#!/usr/bin/env python3
"""Round 110 checks for follow-up context routing, lean prompts, and README refresh."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(rel: str) -> str:
    """Read a repository file as UTF-8 text."""
    return (ROOT / rel).read_text(encoding="utf-8")


def require(condition: bool, message: str) -> None:
    """Raise a clear Round 110 assertion failure."""
    if not condition:
        raise AssertionError(message)


def require_all(text: str, needles: tuple[str, ...], label: str) -> None:
    """Require all substrings to be present in text."""
    missing = [needle for needle in needles if needle not in text]
    require(not missing, f"Missing {label}: " + ", ".join(missing))


def test_intent_router_receives_recent_conversation_context() -> None:
    """Short correction turns must not be routed from an empty context packet."""
    binding = read("src/ai/conversation_binding.py")
    assistant = read("src/ai/assistant.py")
    router = read("src/ai/intent_router.py")
    require_all(binding, (
        "build_intent_routing_context",
        "I meant released movie",
        "RECENT CONVERSATION CONTEXT FOR INTENT ROUTING",
        "Use this only to understand semantic follow-ups",
        "tool",
    ), "compact intent-routing context builder")
    require("build_intent_routing_context" in assistant, "assistant should pass compact recent context to intent routing")
    require("routing_context = await" in assistant, "intent routing should use routing_context, not only pending torrent handles")
    require_all(router, (
        "Short correction/refinement follow-ups inherit the last relevant user goal",
        "If a follow-up refines an information question, classify SEARCH",
        "intent rather than CLARIFY when context makes the target obvious",
    ), "LLM routing instructions for semantic follow-ups")


def test_clarify_path_cannot_crash_execution_context() -> None:
    """CLARIFY should produce a complete ExecutionContext instead of throwing TypeError."""
    assistant = read("src/ai/assistant.py")
    require_all(assistant, (
        "messages=[]",
        "allowed_tool_names=set()",
        "max_iterations=0",
        "task=\"chat\"",
        "clarification=clarification",
    ), "complete clarification ExecutionContext")


def test_category_context_no_longer_dumps_unmatched_library() -> None:
    """A generic word like movie/show should not push huge library packets into prompt context."""
    context = read("src/core/categories/base_context.py")
    require_all(context, (
        "category_router_overview",
        "summaries = []",
        "do *not* dump dozens of unrelated library rows",
        "treat the item sample as orientation only",
    ), "lean unmatched category context")


def test_person_metadata_lookup_does_not_treat_people_as_tv_ids() -> None:
    """TMDB person hits should return person credits, not get_tv_details(person_id)."""
    tmdb = read("src/integrations/tmdb.py")
    research = read("src/ai/tools/research.py")
    support = read("src/ai/tools/metadata_lookup_support.py")
    require_all(tmdb, (
        "get_person_details",
        "movie_credits,tv_credits",
        "directed_movies",
        "safer than letting the model",
    ), "TMDB person details support")
    require_all(research, (
        "result_type == \"person\"",
        "client.get_person_details",
        "result_type = \"person\"",
    ), "metadata_lookup person branch")
    require_all(support, (
        "media_type in {\"tv\", \"movie\", \"person\"}",
        "directed_movies",
        "person_name",
    ), "normalizer person support")


def test_readme_is_human_and_uses_new_public_assets() -> None:
    """README should target beginners and use the newly supplied visuals."""
    readme = read("README.md")
    require_all(readme, (
        "You do **not** need to be an experienced developer",
        "NVIDIA NIM",
        "free development alternative",
        "Local models",
        "TV Shows",
        "Movies",
        "General Files",
        "TMDB API key is highly recommended",
        "Discord, Telegram, WhatsApp",
        "Automatic startup at login",
        "screenshot-helm-chat.png",
        "screenshot-suggestions.png",
        "screenshot-booty-library.png",
    ), "human README refresh")
    require("## Project status" not in readme, "README should not end with Project status")
    for asset in (
        "docs/assets/ljs-avatar.png",
        "docs/assets/screenshot-helm-chat.png",
        "docs/assets/screenshot-suggestions.png",
        "docs/assets/screenshot-booty-library.png",
    ):
        require((ROOT / asset).exists(), f"missing README asset {asset}")


def main() -> None:
    """Run Round 110 checks as a standalone script."""
    for test in (
        test_intent_router_receives_recent_conversation_context,
        test_clarify_path_cannot_crash_execution_context,
        test_category_context_no_longer_dumps_unmatched_library,
        test_person_metadata_lookup_does_not_treat_people_as_tv_ids,
        test_readme_is_human_and_uses_new_public_assets,
    ):
        test()
        print(f"PASS {test.__name__}")
    print("Round 110 context and README checks passed.")


if __name__ == "__main__":
    main()
