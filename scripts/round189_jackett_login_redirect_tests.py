#!/usr/bin/env python3
"""Round 189 regression tests for Jackett login-redirect resilience."""

from __future__ import annotations

import asyncio
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import httpx

from src.search.jackett import JackettSearch
from src.search.jackett_indexer_config import JackettIndexerConfigurer


class FakeResponse:
    def __init__(self, status_code: int = 200, *, json_data: Any = None, text: str = "", headers: dict[str, str] | None = None):
        self.status_code = status_code
        self._json = json_data
        self.text = text
        self.headers = headers or {}

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            request = httpx.Request("GET", "http://example.test")
            response = httpx.Response(self.status_code, request=request)
            raise httpx.HTTPStatusError("boom", request=request, response=response)


class FakeClient:
    calls: list[tuple[str, dict[str, Any]]] = []

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url: str, params: dict[str, Any] | None = None, **kwargs):
        params = params or {}
        self.calls.append((url, dict(params)))
        if url.endswith("/api/v2.0/indexers"):
            return FakeResponse(302, headers={"location": "/UI/Login?ReturnUrl=/api/v2.0/indexers"})
        if "torznab/api" in url and params.get("t") == "indexers":
            configured = params.get("configured") == "true"
            xml = (
                "<indexers>"
                f"<indexer id='thepiratebay' name='The Pirate Bay' type='public' language='en' />"
                "</indexers>"
            ) if configured else (
                "<indexers>"
                "<indexer id='yts' name='YTS' type='public' language='en' />"
                "</indexers>"
            )
            return FakeResponse(200, text=xml)
        if url.endswith("/api/v2.0/indexers/all/results"):
            return FakeResponse(200, json_data={"Results": []})
        if "!type:private+lang:en" in url and url.endswith("/results"):
            return FakeResponse(200, json_data={"Results": [{"Title": "Example.Show.S01E01.1080p.WEB-DL", "MagnetUri": "magnet:?xt=urn:btih:abc", "Seeders": 12, "Size": 1234}]})
        if "!type:private+lang:en" in url and "torznab/api" in url:
            return FakeResponse(200, json_data={"Results": [{"Title": "Example.Show.S01E01.1080p.WEB-DL", "MagnetUri": "magnet:?xt=urn:btih:abc", "Seeders": 12, "Size": 1234}]})
        raise AssertionError(f"unexpected URL {url} params={params}")


async def main() -> None:
    original = httpx.AsyncClient
    FakeClient.calls = []
    httpx.AsyncClient = FakeClient  # type: ignore[assignment]
    try:
        configurer = JackettIndexerConfigurer("http://127.0.0.1:9117", "apikey")
        diagnostics = await configurer.diagnostics()
        assert diagnostics["status"] == "ok", diagnostics
        assert "error" not in diagnostics, diagnostics
        assert diagnostics.get("admin_error"), diagnostics
        assert diagnostics["summary"]["total_indexers"] == 2, diagnostics
        assert diagnostics["summary"]["configured_indexers"] == 1, diagnostics

        provider = JackettSearch("http://127.0.0.1:9117", "apikey", configured_indexers=0, allow_filter_indexers=True)
        results = await provider.search("Example Show S01E01")
        assert results == [], results
        assert not any(url.endswith("/api/v2.0/indexers/all/results") for url, _ in FakeClient.calls), FakeClient.calls
        assert not any("!type:private+lang:en" in url for url, _ in FakeClient.calls), FakeClient.calls

        configured_provider = JackettSearch("http://127.0.0.1:9117", "apikey", configured_indexers=1, allow_filter_indexers=True)
        configured_results = await configured_provider.search("Example Show S01E01")
        assert configured_results == [], configured_results
        assert any(url.endswith("/api/v2.0/indexers/all/results") for url, _ in FakeClient.calls), FakeClient.calls
    finally:
        httpx.AsyncClient = original  # type: ignore[assignment]

    print("round189 jackett login redirect diagnostics tests passed")


if __name__ == "__main__":
    asyncio.run(main())
