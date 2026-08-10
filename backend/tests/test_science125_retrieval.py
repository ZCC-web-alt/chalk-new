from __future__ import annotations

import sqlite3
import sys
import tempfile
import threading
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))


class FakeResponse:
    def __init__(self, status_code: int, headers: dict[str, str] | None = None, body: object = None):
        self.status_code = status_code
        self.headers = headers or {}
        self.body = body


class Science125RetrievalRegistryTestCase(unittest.TestCase):
    def test_registry_contains_official_policy_metadata_and_domain_profiles(self) -> None:
        from app.services.science125_retrieval import (
            PROVIDER_REGISTRY,
            RETRIEVAL_PROFILE_REGISTRY,
            get_provider,
            get_retrieval_profile,
        )

        arxiv = get_provider("arxiv")
        self.assertEqual(arxiv.policy.mode, "fixed_interval")
        self.assertEqual(arxiv.policy.min_interval_ms, 3_000)
        self.assertEqual(arxiv.policy.max_concurrency, 1)
        self.assertTrue(arxiv.policy.policy_source_url.startswith("https://"))
        self.assertRegex(arxiv.policy.policy_hash, r"^[0-9a-f]{64}$")
        self.assertIn("retrieval.chem.interface.v1", RETRIEVAL_PROFILE_REGISTRY)
        self.assertIn("crossref", get_retrieval_profile("retrieval.chem.interface.v1").primary)
        self.assertIn("materials_project", get_retrieval_profile("retrieval.chem.interface.v1").conditional)
        materials_project = get_provider("materials_project")
        self.assertEqual(materials_project.family, "materials_database")
        self.assertEqual(materials_project.capabilities, ("structured_material_data",))
        self.assertEqual(materials_project.required_env_vars, ("MATERIALS_PROJECT_API_KEY",))
        self.assertGreaterEqual(len(PROVIDER_REGISTRY), 10)

    def test_readiness_does_not_expose_secret_and_reports_profile_missing_key(self) -> None:
        from app.services.science125_retrieval import profile_readiness

        readiness = profile_readiness(
            "retrieval.astro.high_energy.v1",
            environ={"SCIENCE125_CROSSREF_MAILTO": "team@example.org"},
        )
        self.assertFalse(readiness.ready)
        self.assertIn("MISSING_SCIENCE125_NASA_ADS_API_TOKEN", readiness.missing_configuration_codes)
        serialized = repr(readiness)
        self.assertNotIn("Authorization", serialized)
        self.assertNotIn("api_key", serialized.lower())

    def test_ncbi_policy_changes_from_three_to_ten_rps_with_api_key(self) -> None:
        from app.services.science125_retrieval import profile_readiness, resolve_provider

        anonymous = resolve_provider("ncbi", environ={"SCIENCE125_NCBI_TOOL_EMAIL": "team@example.org"})
        keyed = resolve_provider(
            "ncbi",
            environ={
                "SCIENCE125_NCBI_TOOL_EMAIL": "team@example.org",
                "SCIENCE125_NCBI_API_KEY": "secret",
            },
        )
        self.assertEqual(anonymous.policy.min_interval_ms, 334)
        self.assertEqual(keyed.policy.min_interval_ms, 100)
        self.assertEqual(anonymous.credential_scope, "anonymous")
        self.assertTrue(keyed.credential_scope.startswith("key:"))
        self.assertNotIn("secret", keyed.credential_scope)

        pilot = profile_readiness(
            "retrieval.bio.genome_editing.v1",
            environ={"SCIENCE125_NCBI_TOOL_EMAIL": "team@example.org"},
        )
        self.assertFalse(pilot.ready)
        self.assertIn("MISSING_SCIENCE125_NCBI_API_KEY", pilot.missing_configuration_codes)


class Science125RateLimiterTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "web.db"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_arxiv_reservations_are_at_least_three_seconds_apart_and_survive_reopen(self) -> None:
        from app.services.science125_retrieval import (
            ProviderRateStateStore,
            ProviderRateLimiter,
            resolve_provider,
        )

        now = datetime(2026, 7, 20, 0, 0, tzinfo=UTC)
        sleeps: list[float] = []
        store = ProviderRateStateStore(self.db_path)
        resolved = resolve_provider("arxiv", environ={})
        limiter = ProviderRateLimiter(resolved, store, now=lambda: now, sleep=sleeps.append)
        self.assertEqual(limiter.acquire(), 0.0)
        self.assertEqual(sleeps, [])
        limiter.release()
        now = now + timedelta(milliseconds=1_000)
        self.assertAlmostEqual(limiter.acquire(), 2.0, places=6)
        self.assertEqual(sleeps, [2.0])
        limiter.release()
        store.dispose()

        reopened = ProviderRateStateStore(self.db_path)
        now = datetime(2026, 7, 20, 0, 0, tzinfo=UTC) + timedelta(seconds=1)
        sleeps2: list[float] = []
        resumed = ProviderRateLimiter(resolved, reopened, now=lambda: now, sleep=sleeps2.append)
        self.assertGreaterEqual(resumed.acquire(), 2.0)
        self.assertEqual(len(sleeps2), 1)
        resumed.release()
        reopened.dispose()

    def test_semantic_scholar_403_is_reported_as_credential_rejected_without_secret_details(self) -> None:
        from app.services.science125_retrieval import (
            ProviderRateStateStore,
            ProviderSearchResponse,
            search_science125,
        )

        store = ProviderRateStateStore(self.db_path)
        try:
            result = search_science125(
                "retrieval.chem.interface.v1",
                "microscopic interface measurement",
                adapters={
                    "semantic_scholar": lambda _provider, _query: ProviderSearchResponse(
                        status_code=403,
                    ),
                },
                store=store,
                environ={"SCIENCE125_SEMANTIC_SCHOLAR_API_KEY": "private-key"},
                provider_ids=("semantic_scholar",),
                use_cache=False,
            )
        finally:
            store.dispose()

        diagnostic = result.diagnostics[0]
        self.assertEqual(diagnostic.status, "credential_rejected")
        self.assertEqual(diagnostic.status_code, 403)
        self.assertIn("configured credential", diagnostic.message or "")
        self.assertNotIn("private-key", repr(diagnostic))

        connection = sqlite3.connect(self.db_path)
        try:
            columns = {row[1] for row in connection.execute("PRAGMA table_info(science125_provider_rate_state)")}
        finally:
            connection.close()
        self.assertTrue({"provider_id", "credential_scope", "last_request_at", "cooldown_until", "policy_hash"}.issubset(columns))

    def test_retry_after_sets_provider_cooldown(self) -> None:
        from app.services.science125_retrieval import (
            ProviderRateStateStore,
            Science125RetrievalClient,
            resolve_provider,
        )

        now = datetime(2026, 7, 20, tzinfo=UTC)
        sleeps: list[float] = []
        store = ProviderRateStateStore(self.db_path)
        client = Science125RetrievalClient(
            store=store,
            now=lambda: now,
            sleep=sleeps.append,
            random_uniform=lambda _a, _b: 0.0,
        )
        responses = iter([FakeResponse(429, {"Retry-After": "2.5"}), FakeResponse(200, {"X-Request-Id": "ok"})])
        result = client.request(
            "arxiv",
            lambda: next(responses),
            max_attempts=2,
        )
        self.assertEqual(result.status_code, 200)
        self.assertTrue(any(delay >= 2.5 for delay in sleeps))
        state = store.get("arxiv", resolve_provider("arxiv", environ={}).credential_scope)
        self.assertIsNotNone(state)
        self.assertIsNotNone(state.cooldown_until)
        store.dispose()

    def test_dynamic_remaining_and_reset_headers_create_a_persistent_cooldown(self) -> None:
        from app.services.science125_retrieval import ProviderRateLimiter, ProviderRateStateStore, resolve_provider

        now = datetime(2026, 7, 20, tzinfo=UTC)
        store = ProviderRateStateStore(self.db_path)
        provider = resolve_provider(
            "openalex",
            environ={"SCIENCE125_OPENALEX_MAILTO": "team@example.org"},
        )
        limiter = ProviderRateLimiter(provider, store, now=lambda: now, sleep=lambda _delay: None)
        limiter.acquire()
        limiter.release()
        store.record_response(
            provider,
            status_code=200,
            headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "5"},
            now=now,
        )
        sleeps: list[float] = []
        resumed = ProviderRateLimiter(provider, store, now=lambda: now, sleep=sleeps.append)
        self.assertGreaterEqual(resumed.acquire(), 5.0)
        resumed.release()
        store.dispose()

    def test_same_provider_requests_are_single_concurrency(self) -> None:
        from app.services.science125_retrieval import ProviderRateStateStore, Science125RetrievalClient

        store = ProviderRateStateStore(self.db_path)
        client = Science125RetrievalClient(
            store=store,
            now=lambda: datetime(2026, 7, 20, tzinfo=UTC),
            sleep=lambda _delay: None,
        )
        first_entered = threading.Event()
        release_first = threading.Event()
        second_entered = threading.Event()

        def first_transport():
            first_entered.set()
            release_first.wait(timeout=2)
            return FakeResponse(200)

        def second_transport():
            second_entered.set()
            return FakeResponse(200)

        first = threading.Thread(target=lambda: client.request("arxiv", first_transport), daemon=True)
        second = threading.Thread(target=lambda: client.request("arxiv", second_transport), daemon=True)
        first.start()
        self.assertTrue(first_entered.wait(timeout=1))
        second.start()
        self.assertFalse(second_entered.wait(timeout=0.1))
        release_first.set()
        first.join(timeout=2)
        second.join(timeout=2)
        self.assertTrue(second_entered.is_set())
        store.dispose()

    def test_retryable_5xx_and_timeout_retry_but_ordinary_4xx_does_not(self) -> None:
        from app.services.science125_retrieval import ProviderRateStateStore, Science125RetrievalClient

        store = ProviderRateStateStore(self.db_path)
        sleeps: list[float] = []
        client = Science125RetrievalClient(
            store=store,
            environ={"SCIENCE125_OPENALEX_MAILTO": "team@example.org"},
            sleep=sleeps.append,
            random_uniform=lambda _a, _b: 0.0,
        )

        attempts = iter([FakeResponse(500), FakeResponse(200)])
        self.assertEqual(client.request("openalex", lambda: next(attempts), max_attempts=2).status_code, 200)

        timeout_calls = 0

        def timeout_then_success():
            nonlocal timeout_calls
            timeout_calls += 1
            if timeout_calls == 1:
                raise TimeoutError("read timeout")
            return FakeResponse(200)

        self.assertEqual(client.request("openalex", timeout_then_success, max_attempts=2).status_code, 200)

        calls = 0

        def ordinary_bad_request():
            nonlocal calls
            calls += 1
            return FakeResponse(400)

        self.assertEqual(client.request("openalex", ordinary_bad_request, max_attempts=4).status_code, 400)
        self.assertEqual(calls, 1)
        store.dispose()

    def test_cache_key_is_stable_and_does_not_include_secret(self) -> None:
        from app.services.science125_retrieval import retrieval_cache_key

        first = retrieval_cache_key("retrieval.chem.interface.v1", "  interface phenomena ", {"page": 1})
        second = retrieval_cache_key("retrieval.chem.interface.v1", "interface   phenomena", {"page": 1})
        self.assertEqual(first, second)
        self.assertRegex(first, r"^[0-9a-f]{64}$")
        self.assertNotIn("secret", retrieval_cache_key("x", "secret", {}))

    def test_search_service_returns_evidence_and_provider_diagnostics_without_network(self) -> None:
        from app.services.science125_retrieval import (
            EvidenceRecord,
            ProviderRateStateStore,
            ProviderSearchResponse,
            search_science125,
        )

        store = ProviderRateStateStore(self.db_path)

        def crossref_adapter(_provider, query: str) -> ProviderSearchResponse:
            return ProviderSearchResponse(
                status_code=200,
                headers={},
                records=(
                    EvidenceRecord(
                        provider="crossref",
                        stable_id="doi:10.1000/example",
                        title="Microscopic interface measurement",
                        authors=("A. Researcher",),
                        abstract="A calibrated interface measurement study.",
                        doi="10.1000/example",
                        retrieved_at=datetime(2026, 7, 20, tzinfo=UTC),
                        query_hash="",
                    ),
                ),
            )

        result = search_science125(
            "retrieval.chem.interface.v1",
            "microscopic interface measurement",
            adapters={"crossref": crossref_adapter},
            store=store,
            environ={"SCIENCE125_CROSSREF_MAILTO": "team@example.org"},
            provider_ids=("crossref",),
        )
        self.assertEqual(len(result.evidence), 1)
        self.assertEqual(result.evidence[0].doi, "10.1000/example")
        self.assertRegex(result.evidence[0].query_hash, r"^[0-9a-f]{64}$")
        self.assertEqual(result.diagnostics[0].status, "succeeded")
        store.dispose()

    def test_default_adapters_normalize_official_json_and_xml_shapes(self) -> None:
        from app.services.science125_retrieval import default_provider_adapters

        class Response:
            def __init__(self, payload=None, text="", status_code=200):
                self._payload = payload
                self.text = text
                self.status_code = status_code
                self.headers = {}

            def json(self):
                return self._payload

            def raise_for_status(self):
                if self.status_code >= 400:
                    raise RuntimeError("HTTP error")

        class Session:
            def get(self, url, **kwargs):
                if "crossref" in url:
                    return Response({"message": {"items": [{"title": ["Interface study"], "DOI": "10.1000/interface", "author": [{"given": "A", "family": "Researcher"}], "abstract": "Abstract"}]}})
                if "arxiv" in url:
                    return Response(text="""<?xml version='1.0'?><feed xmlns='http://www.w3.org/2005/Atom'><entry><id>http://arxiv.org/abs/1234.5678</id><title>Cosmic rays</title><summary>Abstract</summary><author><name>A Researcher</name></author></entry></feed>""")
                if "eutils" in url:
                    return Response({"esearchresult": {"idlist": ["123456"]}})
                raise AssertionError(url)

        adapters = default_provider_adapters(
            environ={"SCIENCE125_CROSSREF_MAILTO": "team@example.org"},
            session=Session(),
        )
        crossref = adapters["crossref"](None, "interface phenomena")  # type: ignore[arg-type]
        arxiv = adapters["arxiv"](None, "cosmic rays")  # type: ignore[arg-type]
        ncbi = adapters["ncbi"](None, "genome editing")  # type: ignore[arg-type]
        self.assertEqual(crossref.records[0].doi, "10.1000/interface")
        self.assertEqual(arxiv.records[0].arxiv_id, "1234.5678")
        self.assertEqual(ncbi.records[0].pmid, "123456")

    def test_materials_project_adapter_uses_configured_key_and_returns_non_literature_enrichment(self) -> None:
        from app.services.science125_retrieval import default_provider_adapters

        captured: dict[str, object] = {}

        class Response:
            status_code = 200
            headers = {"X-RateLimit-Remaining": "99"}

            def json(self):
                return {
                    "data": [{
                        "material_id": "mp-149",
                        "formula_pretty": "Si",
                        "band_gap": 1.1,
                        "formation_energy_per_atom": -0.25,
                        "energy_above_hull": 0.0,
                    }],
                }

        class Session:
            def get(self, url, **kwargs):
                captured["url"] = url
                captured["kwargs"] = kwargs
                return Response()

        adapters = default_provider_adapters(
            environ={"MATERIALS_PROJECT_API_KEY": "private-mp-key"},
            session=Session(),
        )
        response = adapters["materials_project"](None, "DFT study of TiO2 interfaces")  # type: ignore[arg-type]

        self.assertEqual(captured["url"], "https://api.materialsproject.org/materials/summary/")
        kwargs = captured["kwargs"]
        self.assertEqual(kwargs["headers"]["X-API-KEY"], "private-mp-key")  # type: ignore[index]
        self.assertEqual(kwargs["params"]["formula"], "TiO2")  # type: ignore[index]
        record = response.records[0]
        self.assertEqual(record.stable_id, "materials_project:mp-149")
        self.assertEqual(record.access_status, "metadata")
        self.assertIn("structured enrichment", record.warning)
        self.assertNotIn("private-mp-key", repr(record))

    def test_pilot_adapters_normalize_domain_specific_official_shapes(self) -> None:
        from app.services.science125_retrieval import default_provider_adapters

        class Response:
            status_code = 200
            headers: dict[str, str] = {}

            def __init__(self, payload):
                self.payload = payload

            def json(self):
                return self.payload

        payloads = {
            "openalex": {
                "results": [{
                    "id": "https://openalex.org/W1",
                    "display_name": "OpenAlex interface study",
                    "doi": "https://doi.org/10.1000/openalex",
                    "authorships": [{"author": {"display_name": "A Researcher"}}],
                    "abstract_inverted_index": {"Microscopic": [0], "interface": [1]},
                    "open_access": {"is_oa": True, "oa_url": "https://example.test/openalex.pdf"},
                }],
            },
            "europepmc": {
                "resultList": {"result": [{
                    "id": "MED:1",
                    "pmid": "7654321",
                    "title": "Genome editing study",
                    "authorString": "A Researcher, B Scientist",
                    "abstractText": "Clinical evidence.",
                    "doi": "10.1000/europepmc",
                    "isOpenAccess": "Y",
                }]},
            },
            "semanticscholar": {
                "data": [{
                    "paperId": "paper-1",
                    "title": "Semantic Scholar interface study",
                    "abstract": "Interface evidence.",
                    "authors": [{"name": "A Researcher"}],
                    "externalIds": {"DOI": "10.1000/semantic"},
                }],
            },
            "clinicaltrials": {
                "studies": [{"protocolSection": {
                    "identificationModule": {"nctId": "NCT00000001", "briefTitle": "Editing trial"},
                    "descriptionModule": {"briefSummary": "A registered study."},
                }}],
            },
            "inspirehep": {
                "hits": {"hits": [{"id": "hep-1", "metadata": {
                    "titles": [{"title": "Cosmic ray origin"}],
                    "abstracts": [{"value": "High-energy observations."}],
                    "authors": [{"full_name": "A Researcher"}],
                    "dois": [{"value": "10.1000/inspire"}],
                    "arxiv_eprints": [{"value": "2401.00001"}],
                }}]},
            },
            "adsabs": {
                "response": {"docs": [{
                    "bibcode": "2026ApJ...1A",
                    "title": ["ADS cosmic ray study"],
                    "abstract": "Observational evidence.",
                    "author": ["A Researcher"],
                    "doi": ["10.1000/ads"],
                }]},
            },
        }

        class Session:
            def get(self, url, **_kwargs):
                for fragment, payload in payloads.items():
                    if fragment in url:
                        return Response(payload)
                raise AssertionError(url)

        adapters = default_provider_adapters(
            environ={
                "SCIENCE125_OPENALEX_MAILTO": "team@example.org",
                "SCIENCE125_SEMANTIC_SCHOLAR_API_KEY": "secret",
                "SCIENCE125_NASA_ADS_API_TOKEN": "secret",
            },
            session=Session(),
        )
        openalex = adapters["openalex"](None, "interface").records[0]  # type: ignore[arg-type]
        self.assertEqual(openalex.doi, "10.1000/openalex")
        self.assertEqual(openalex.access_status, "open_full_text")
        self.assertEqual(openalex.full_text_url, "https://example.test/openalex.pdf")
        self.assertEqual(adapters["europe_pmc"](None, "editing").records[0].pmid, "7654321")  # type: ignore[arg-type]
        self.assertEqual(adapters["semantic_scholar"](None, "interface").records[0].stable_id, "semantic_scholar:paper-1")  # type: ignore[arg-type]
        self.assertEqual(adapters["clinical_trials"](None, "editing").records[0].stable_id, "nct:NCT00000001")  # type: ignore[arg-type]
        self.assertEqual(adapters["inspire"](None, "cosmic rays").records[0].arxiv_id, "2401.00001")  # type: ignore[arg-type]
        self.assertEqual(adapters["nasa_ads"](None, "cosmic rays").records[0].ads_id, "2026ApJ...1A")  # type: ignore[arg-type]

    def test_search_cache_hits_without_calling_adapter_and_survives_reopen(self) -> None:
        from app.services.science125_retrieval import (
            EvidenceRecord,
            ProviderSearchResponse,
            ProviderRateStateStore,
            search_science125,
        )

        calls = 0
        now = datetime(2026, 7, 20, tzinfo=UTC)

        def adapter(_provider, _query):
            nonlocal calls
            calls += 1
            return ProviderSearchResponse(
                status_code=200,
                records=(EvidenceRecord(provider="crossref", stable_id="doi:10.1/cache", title="Cached result"),),
            )

        store = ProviderRateStateStore(self.db_path)
        first = search_science125(
            "retrieval.chemistry.v1",
            "cacheable question",
            adapters={"crossref": adapter},
            provider_ids=("crossref",),
            store=store,
            environ={},
            user_scope="user-1",
            credential_scope="key:credential-a",
            now=lambda: now,
            sleep=lambda _delay: None,
        )
        second = search_science125(
            "retrieval.chemistry.v1",
            "cacheable question",
            adapters={"crossref": adapter},
            provider_ids=("crossref",),
            store=store,
            environ={},
            user_scope="user-1",
            credential_scope="key:credential-a",
            now=lambda: now,
            sleep=lambda _delay: None,
        )
        self.assertEqual(calls, 1)
        self.assertEqual(first.evidence, second.evidence)
        store.dispose()

        reopened = ProviderRateStateStore(self.db_path)
        third = search_science125(
            "retrieval.chemistry.v1",
            "cacheable question",
            adapters={"crossref": adapter},
            provider_ids=("crossref",),
            store=reopened,
            environ={},
            user_scope="user-1",
            credential_scope="key:credential-a",
            now=lambda: now,
            sleep=lambda _delay: None,
        )
        self.assertEqual(calls, 1)
        self.assertEqual(third.evidence, first.evidence)
        reopened.dispose()

    def test_search_cache_expires_and_isolated_by_user_and_credential_scope(self) -> None:
        from app.services.science125_retrieval import EvidenceRecord, ProviderRateStateStore, ProviderSearchResponse, search_science125

        calls = 0
        clock = [datetime(2026, 7, 20, tzinfo=UTC)]

        def adapter(_provider, _query):
            nonlocal calls
            calls += 1
            return ProviderSearchResponse(
                status_code=200,
                records=(EvidenceRecord(provider="crossref", stable_id=f"doi:10.1/{calls}", title="Result"),),
            )

        store = ProviderRateStateStore(self.db_path)
        common = {
            "profile_id": "retrieval.chemistry.v1",
            "query": "same query",
            "adapters": {"crossref": adapter},
            "provider_ids": ("crossref",),
            "store": store,
            "environ": {},
            "sleep": lambda _delay: None,
            "now": lambda: clock[0],
        }
        search_science125(**common, user_scope="user-1", credential_scope="key:a")
        search_science125(**common, user_scope="user-2", credential_scope="key:a")
        search_science125(**common, user_scope="user-1", credential_scope="key:b")
        self.assertEqual(calls, 3)
        clock[0] += timedelta(days=2)
        search_science125(**common, user_scope="user-1", credential_scope="key:a")
        self.assertEqual(calls, 4)
        store.dispose()

    def test_failed_provider_result_is_not_cached(self) -> None:
        from app.services.science125_retrieval import ProviderRateStateStore, search_science125

        calls = 0

        def failing(_provider, _query):
            nonlocal calls
            calls += 1
            raise TimeoutError("temporary provider outage")

        store = ProviderRateStateStore(self.db_path)
        options = {
            "profile_id": "retrieval.chemistry.v1",
            "query": "failed query",
            "adapters": {"crossref": failing},
            "provider_ids": ("crossref",),
            "store": store,
            "environ": {},
            "user_scope": "user-1",
            "credential_scope": "key:a",
            "max_attempts": 1,
            "sleep": lambda _delay: None,
            "random_uniform": lambda _a, _b: 0.0,
        }
        first = search_science125(**options)
        second = search_science125(**options)
        self.assertEqual(first.evidence, ())
        self.assertEqual(second.evidence, ())
        self.assertEqual(calls, 2)
        self.assertEqual(first.diagnostics[0].status, "transport_error")
        store.dispose()


if __name__ == "__main__":
    unittest.main()
