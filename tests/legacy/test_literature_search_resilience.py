from __future__ import annotations

import requests

from chalk_app.literature import literature_search
from chalk_app.literature.literature_search import (
    LiteratureSearchEngine,
    SearchQuery,
    SearchResult,
)


def test_platform_rate_limits_match_source_policies():
    engine = LiteratureSearchEngine()

    assert engine._rate_limit_interval("arxiv") == 3.0
    assert engine._rate_limit_interval("crossref") == 0.1
    assert engine._rate_limit_interval("pmc") == 1 / 3
    assert engine._rate_limit_interval("doaj") == 0.5
    assert engine._rate_limit_interval("pubchem") == 0.2


def test_search_keeps_platform_results_when_one_item_has_malformed_fields(monkeypatch):
    engine = LiteratureSearchEngine(
        platforms={
            "arxiv": False,
            "crossref": True,
            "semantic_scholar": False,
            "doaj": False,
            "pmc": False,
        },
        timeout=1,
        retry_backoff=0,
    )
    monkeypatch.setattr(engine, "_throttle", lambda platform: None)

    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "message": {
                    "items": [
                        {
                            "title": ["Malformed count field should not drop platform"],
                            "is-referenced-by-count": "many",
                        },
                        {"title": ["Valid ORR catalyst paper"], "DOI": "10.1000/valid"},
                        {"title": "malformed title should be skipped"},
                    ]
                }
            }

    monkeypatch.setattr(engine.session, "get", lambda *args, **kwargs: FakeResponse())

    results = engine.search(SearchQuery(keywords=["ORR", "single atom catalyst"], max_results=2))

    assert [item.title for item in results] == [
        "Malformed count field should not drop platform",
        "Valid ORR catalyst paper",
    ]


def test_search_does_not_raise_when_all_remote_sources_fail(monkeypatch):
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
    monkeypatch.setattr(engine, "_throttle", lambda platform: None)

    def fail(*args, **kwargs):
        raise requests.exceptions.Timeout("network timeout")

    monkeypatch.setattr(engine.session, "get", fail)

    assert engine.search(SearchQuery(keywords=["perovskite", "DFT"], max_results=2)) == []


def test_crossref_retries_transient_timeout_then_keeps_results(monkeypatch):
    engine = LiteratureSearchEngine(
        platforms={
            "arxiv": False,
            "crossref": True,
            "semantic_scholar": False,
            "doaj": False,
            "pmc": False,
        },
        timeout=1,
        retry_backoff=0,
    )
    monkeypatch.setattr(engine, "_throttle", lambda platform: None)
    calls = {"count": 0}

    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "message": {
                    "items": [
                        {"title": ["Recovered Crossref paper"], "DOI": "10.1000/recovered"}
                    ]
                }
            }

    def flaky_get(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            raise requests.exceptions.Timeout("transient timeout")
        return FakeResponse()

    monkeypatch.setattr(engine.session, "get", flaky_get)

    results = engine.search(SearchQuery(keywords=["graphene", "DFT"], max_results=1))

    assert calls["count"] == 2
    assert [item.title for item in results] == ["Recovered Crossref paper"]


def test_merge_dedup_ignores_empty_titles_and_none_results():
    merged = []
    seen_titles = set()

    LiteratureSearchEngine._merge_dedup(
        merged,
        [
            SearchResult(title="", doi="", source_platform="crossref"),
            None,
            SearchResult(title="A real paper", doi="10.1000/a", source_platform="crossref"),
        ],
        seen_titles,
    )

    assert [item.title for item in merged] == ["A real paper"]


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


def test_crossref_results_are_metadata_only_not_open_access(monkeypatch):
    engine = LiteratureSearchEngine(
        platforms={
            "arxiv": False,
            "crossref": True,
            "semantic_scholar": False,
            "doaj": False,
            "pmc": False,
        },
        timeout=1,
        retry_backoff=0,
    )
    monkeypatch.setattr(engine, "_throttle", lambda platform: None)

    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "message": {
                    "items": [
                        {
                            "title": ["Metadata-only Crossref paper"],
                            "DOI": "10.1000/meta",
                            "is-referenced-by-count": 42,
                        }
                    ]
                }
            }

    monkeypatch.setattr(engine.session, "get", lambda *args, **kwargs: FakeResponse())

    results = engine.search(SearchQuery(keywords=["battery"], max_results=1))

    assert len(results) == 1
    assert results[0].source_platform == "crossref"
    assert results[0].is_open_access is False
    assert results[0].access_status == "metadata_only"
    assert results[0].needs_fulltext is True
