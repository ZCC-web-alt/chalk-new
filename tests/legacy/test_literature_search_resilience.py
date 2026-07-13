from __future__ import annotations

import requests

from chalk_app.literature import literature_search
from chalk_app.literature.literature_search import LiteratureSearchEngine, SearchQuery


def test_semantic_scholar_429_sets_cooldown_and_keeps_other_results(monkeypatch):
    monkeypatch.delenv("SEMANTIC_SCHOLAR_API_KEY", raising=False)
    engine = LiteratureSearchEngine(
        platforms={
            "arxiv": False,
            "crossref": True,
            "semantic_scholar": True,
            "doaj": False,
            "pmc": False,
        },
        timeout=1,
        retry_backoff=0,
    )
    literature_search._platform_cooldowns.clear()
    monkeypatch.setattr(engine, "_throttle", lambda platform: None)

    class FakeResponse:
        def __init__(self, status_code, payload):
            self.status_code = status_code
            self._payload = payload
            self.headers = {}
            self.url = "https://example.test"

        def raise_for_status(self):
            if self.status_code >= 400:
                raise requests.exceptions.HTTPError(
                    f"{self.status_code} Client Error",
                    response=self,
                )

        def json(self):
            return self._payload

    calls = {"crossref": 0, "semantic": 0}

    def fake_get(url, **kwargs):
        if "crossref" in url:
            calls["crossref"] += 1
            return FakeResponse(
                200,
                {
                    "message": {
                        "items": [
                            {
                                "title": ["Stable Crossref paper"],
                                "DOI": "10.1000/stable",
                            }
                        ]
                    }
                },
            )
        if "semanticscholar" in url:
            calls["semantic"] += 1
            return FakeResponse(429, {})
        raise AssertionError(url)

    monkeypatch.setattr(engine.session, "get", fake_get)

    first = engine.search_with_diagnostics(
        SearchQuery(keywords=["ORR", "single atom catalyst"], max_results=2)
    )

    assert [item.title for item in first.results] == ["Stable Crossref paper"]
    assert first.platform_status["crossref"]["status"] == "ok"
    assert first.platform_status["semantic_scholar"]["status"] == "rate_limited"
    assert first.warnings
    assert calls == {"crossref": 1, "semantic": 1}

    second = engine.search_with_diagnostics(
        SearchQuery(keywords=["ORR", "single atom catalyst"], max_results=2)
    )

    assert [item.title for item in second.results] == ["Stable Crossref paper"]
    assert second.platform_status["semantic_scholar"]["status"] == "rate_limited"
    assert calls == {"crossref": 2, "semantic": 1}


def test_default_search_disables_semantic_scholar_without_api_key(monkeypatch):
    monkeypatch.delenv("SEMANTIC_SCHOLAR_API_KEY", raising=False)

    engine = LiteratureSearchEngine()

    assert engine.PLATFORMS["crossref"] is True
    assert engine.PLATFORMS["arxiv"] is True
    assert engine.PLATFORMS["semantic_scholar"] is False


def test_default_search_enables_semantic_scholar_with_api_key(monkeypatch):
    monkeypatch.setenv("SEMANTIC_SCHOLAR_API_KEY", "test-key")

    engine = LiteratureSearchEngine()

    assert engine.PLATFORMS["semantic_scholar"] is True
