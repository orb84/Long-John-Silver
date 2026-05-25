"""Round 42 Jackett/indexer configuration regression audit."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def must_contain(path: str, needle: str) -> None:
    text = (ROOT / path).read_text()
    assert needle in text, f"{path} missing: {needle}"


def main() -> None:
    must_contain('src/search/jackett_indexer_config.py', 'DEFAULT_JACKETT_PROFILE = "all_open_public"')
    must_contain('src/search/jackett_manager.py', 'configure_indexer_profile("all_open_public")')
    must_contain('src/web/action_handlers/system.py', 'indexers = await self._jackett.configure_default_indexers()')
    must_contain('src/web/routers/system.py', '/api/jackett/indexers/{indexer_id}/config')
    must_contain('src/web/routers/system.py', '/api/jackett/indexers/{indexer_id}/configure')
    must_contain('src/web/static/js/components/settingsPanel.js', 'Configure all open/public indexers')
    must_contain('src/web/static/js/components/settingsPanel.js', 'loadJackettCustomIndexerSchema')
    must_contain('skills/category_creation_guide.md', 'search all configured Jackett indexers first')
    must_contain('RELIABILITY_FIXES_ROUND42.md', 'Category vs. indexer policy')
    print('Round 42 Jackett/indexer audit passed.')


if __name__ == '__main__':
    main()
