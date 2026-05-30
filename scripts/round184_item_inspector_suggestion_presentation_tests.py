#!/usr/bin/env python3
"""Round 184 regression checks for item-inspector suggestion presentation."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def require(cond: bool, message: str) -> None:
    if not cond:
        raise AssertionError(message)


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_item_inspector_scopes_suggestions_by_category_and_item() -> None:
    router = read("src/web/routers/suggestions.py")
    js = read("src/web/static/js/components/categoryItemDetailModal.js")
    require("category_id: str | None = None, item_id: str | None = None" in router,
            "suggestion router must accept category_id plus item_id filters")
    require("get_suggested_actions(category_id=category_id, item_id=item_id, status=\"pending\")" in router,
            "suggestion router must pass both filters to the repository")
    require("params.set('category_id', categoryId)" in js and "params.set('item_id', itemId)" in js,
            "item inspector must request category-scoped item suggestions")


def test_item_inspector_limits_and_groups_suggestions() -> None:
    js = read("src/web/static/js/components/categoryItemDetailModal.js")
    css = read("src/web/static/css/style.css")
    require("const lead = suggestions.slice(0, 3)" in js,
            "item inspector must show only a few lead suggestions before overflow")
    require("More item suggestions" in js and "_renderSuggestionOverflowGroup" in js,
            "extra suggestions must be collapsed into overflow groups")
    require("Episode actions" in js and "Upgrade actions" in js and "Other actions" in js,
            "overflow suggestions must be grouped by broad action type")
    require("category-detail-suggestion-overflow" in css and "category-detail-suggestion-mini-row" in css,
            "compact overflow presentation must be styled")


def test_item_inspector_actions_remain_interactive_and_visible() -> None:
    js = read("src/web/static/js/components/categoryItemDetailModal.js")
    require("_approveSuggestion" in js and "_denySuggestion" in js,
            "item inspector suggestions must expose approve and dismiss actions")
    require("category-detail-action-overlay" in js and "item inspector is locked" in js,
            "item inspector actions must show the loading/locked overlay")
    require("_refreshCurrentItem" in js,
            "approving/dismissing an inspector suggestion must refresh item details and suggestions")


def main() -> None:
    test_item_inspector_scopes_suggestions_by_category_and_item()
    test_item_inspector_limits_and_groups_suggestions()
    test_item_inspector_actions_remain_interactive_and_visible()
    print("round184 item inspector suggestion presentation tests passed")


if __name__ == "__main__":
    main()
