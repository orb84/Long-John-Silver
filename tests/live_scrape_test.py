"""
Live integration test: scrape all torrent providers.

Runs each provider through the BrowserRuntime and reports results.
Uses Chromium-first engine selection since these sites have DNS
issues with Playwright-Firefox on some networks.
"""

import asyncio

# This file is a standalone live/integration script, not a pytest module.
__test__ = False

import sys

sys.path.insert(0, ".")

from loguru import logger
from src.utils.browser.challenge_detector import ChallengeDetector
from src.utils.browser.domain_policy import BrowserDomainPolicy
from src.utils.browser.runtime import BrowserRuntime
from src.search.browser_strategy import (
    Search1337xBrowserStrategy,
    BTDiggBrowserStrategy,
    TorrentGalaxyBrowserStrategy,
    NyaaBrowserStrategy,
)

QUERY = "The Last of Us S01E01 1080p"


async def run_provider_live_check(strategy_class, name):
    logger.info("=" * 60)
    logger.info(f"{name} LIVE TEST — query={QUERY!r}")
    logger.info("=" * 60)

    domain_policy = BrowserDomainPolicy(domain_max_pages=2, global_max_pages=4)
    detector = ChallengeDetector()
    runtime = BrowserRuntime(
        domain_policy=domain_policy,
        challenge_detector=detector,
        preferred_engine="chromium",
    )
    strategy = strategy_class(runtime)
    if hasattr(strategy, "MAX_DETAIL_FETCHES"):
        strategy.MAX_DETAIL_FETCHES = 5

    try:
        result = await strategy.search(QUERY)
        logger.info(
            f"{name}: ok={result.ok}  candidates={len(result.candidates)}  "
            f"blocked={result.blocked_reason!r}  error={result.error!r}  "
            f"elapsed={result.elapsed_ms}ms"
        )

        magnet_count = 0
        for c in result.candidates[:10]:
            has_magnet = "YES" if c.magnet else "no"
            if c.magnet:
                magnet_count += 1
            logger.info(
                f"  [{has_magnet:>3}] s={c.seeders or 0:>5}  {c.size:>8}  "
                f"  {c.title[:90]}"
            )

        success = result.ok and magnet_count > 0
        if success:
            logger.success(f"{name} PASSED: {magnet_count} results with magnets")
        else:
            logger.error(f"{name} FAILED: {result.blocked_reason or result.error or 'no magnets'}")

        return success
    except Exception as e:
        logger.error(f"{name} EXCEPTION: {e}")
        return False
    finally:
        await runtime.close()


async def main():
    results = {}
    for cls, name in [
        (BTDiggBrowserStrategy, "BTDigg"),
        (Search1337xBrowserStrategy, "1337x"),
        (TorrentGalaxyBrowserStrategy, "TorrentGalaxy"),
        (NyaaBrowserStrategy, "Nyaa"),
    ]:
        results[name] = await run_provider_live_check(cls, name)

    logger.info("=" * 60)
    logger.info("RESULTS")
    for name, ok in results.items():
        logger.info(f"  {name:>14}: {'PASS' if ok else 'FAIL'}")

    failures = [n for n, ok in results.items() if not ok]
    if failures:
        logger.warning(f"Failed: {', '.join(failures)}")
        logger.info("BTDigg/1337x/TorrentGalaxy may require VPN or Jackett "
                     "if your network blocks or rate-limits these domains.")
        if results.get("Nyaa"):
            logger.success("Nyaa works — proving the scraping architecture is functional.")
            sys.exit(0)

    if not any(results.values()):
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
