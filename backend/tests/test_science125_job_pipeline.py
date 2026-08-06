from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_DIR = BACKEND_DIR.parent
sys.path.insert(0, str(BACKEND_DIR))
sys.path.insert(0, str(PROJECT_DIR / "src"))


class Science125JobPipelineTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "web.db"
        from app.services.job_store import WebJobStore
        from app.services.jobs import JobService

        self.store = WebJobStore(self.path)
        self.service = JobService(self.store)

    def tearDown(self) -> None:
        self.service.model_call_ledger.dispose()
        self.service.science125_rate_store.dispose()
        self.service.analysis_store.dispose()
        self.service.hypothesis_store.dispose()
        self.service._executor.shutdown(wait=True, cancel_futures=True)
        self.service._hypothesis_executor.shutdown(wait=True, cancel_futures=True)
        self.store.dispose()
        self.tmp.cleanup()

    @staticmethod
    def _environment() -> dict[str, str]:
        return {
            "CHALK_WEB_ENV": "test",
            "CHALK_RESEARCH_GENERATION_TRANSPORT": "deterministic",
            "DASHSCOPE_API_KEY": "server-only-test-key",
            "QWEN_INPUT_COST_PER_MILLION_CNY": "2",
            "QWEN_OUTPUT_COST_PER_MILLION_CNY": "12",
            "SCIENCE125_CROSSREF_MAILTO": "team@example.org",
            "SCIENCE125_OPENALEX_MAILTO": "team@example.org",
            "SCIENCE125_SEMANTIC_SCHOLAR_API_KEY": "semantic-test-key",
            "SCIENCE125_NASA_ADS_API_TOKEN": "ads-test-token",
            "SCIENCE125_NCBI_TOOL_EMAIL": "team@example.org",
            "SCIENCE125_NCBI_API_KEY": "ncbi-test-key",
        }

    def _complete_search(self, *, access_status: str = "open_full_text"):
        rows = [
            {
                "id": "doi:10.1000/one",
                "title": "Microscopic interface measurement with operando spectroscopy",
                "abstract": "Nanoscale interfacial dynamics are measured by microscopy and spectroscopy.",
                "sourcePlatform": "crossref",
                "providerFamily": "doi_registry",
                "accessStatus": access_status,
                "url": "https://example.test/one",
            },
            {
                "id": "openalex:W2",
                "title": "Microscopic interface measurement with calibrated microscopy",
                "abstract": "Nanoscale interfacial dynamics are measured by microscopy and spectroscopy.",
                "sourcePlatform": "openalex",
                "providerFamily": "scholarly_index",
                "accessStatus": access_status,
                "url": "https://example.test/two",
            },
            {
                "id": "pmid:3",
                "title": "Microscopic interface measurement for interfacial transport",
                "abstract": "Nanoscale interfacial dynamics are measured by microscopy and spectroscopy.",
                "sourcePlatform": "europe_pmc",
                "providerFamily": "biomedical_index",
                "accessStatus": access_status,
                "url": "https://example.test/three",
            },
        ]
        job = self.store.create(1, "literature_search", {
            "science125Id": "S125-006",
            "queryText": "microscopic interface measurement",
        })
        return self.store.update(job.id, status="SUCCEEDED", result={
            "results": rows,
            "query": {"queryHash": "a" * 64},
            "policyHashes": {"crossref": "b" * 64, "openalex": "c" * 64},
        })

    @staticmethod
    def _context_index():
        item = SimpleNamespace(
            id="S125-006",
            headline="How can we measure interface phenomena on the microscopic level?",
            source_context="The full hash-verified booklet context for microscopic interface measurements.",
            context_sha256="a" * 64,
            pdf_page=12,
            booklet_page=10,
        )
        return SimpleNamespace(
            extraction_version="pymupdf-block-anchor-v1",
            items={"S125-006": item},
        )

    def test_science125_search_uses_the_server_route_not_client_platforms(self) -> None:
        from app.services.science125_retrieval import (
            EvidenceRecord,
            ProviderDiagnostic,
            Science125SearchResult,
        )

        job = self.store.create(1, "literature_search", {
            "science125Id": "S125-006",
            "queryText": "microscopic interface measurement",
            "platforms": ["PMC"],
        })
        result = Science125SearchResult(
            profile_id="retrieval.chem.interface.v1",
            query_hash="a" * 64,
            cache_key="b" * 64,
            evidence=(EvidenceRecord(
                provider="crossref",
                stable_id="doi:10.1000/interface",
                title="Microscopic interface measurement study",
                abstract="Spectroscopy resolves nanoscale interfacial dynamics.",
                doi="10.1000/interface",
                access_status="open_full_text",
                full_text_url="https://example.test/interface",
            ),),
            diagnostics=(ProviderDiagnostic(provider="crossref", status="succeeded", status_code=200, attempts=1),),
        )
        with patch.dict(os.environ, self._environment(), clear=False), patch(
            "app.services.jobs.default_provider_adapters", return_value={"server": "selected"}
        ) as adapters, patch(
            "app.services.jobs.search_science125", return_value=result
        ) as search:
            output = self.service._run_literature_search(job)

        self.assertEqual(output["query"]["science125Id"], "S125-006")
        self.assertEqual(output["query"]["retrievalProfile"], "retrieval.chem.interface.v1")
        self.assertEqual(output["results"][0]["id"], "doi:10.1000/interface")
        self.assertEqual(output["results"][0]["sourcePlatform"], "crossref")
        self.assertGreater(output["results"][0]["relevanceScore"], 0.0)
        self.assertIn(output["results"][0]["relevanceLabel"], {"low", "medium", "high"})
        self.assertEqual(
            output["results"][0]["relevanceBreakdown"]["scoringVersion"],
            "science125-relevance-v1",
        )
        self.assertEqual(output["relevanceScoringVersion"], "science125-relevance-v1")
        self.assertEqual(output["evidenceStatus"], "evidence_insufficient")
        self.assertEqual(adapters.call_count, 1)
        self.assertEqual(search.call_args_list[0].args[0], "retrieval.chem.interface.v1")
        self.assertEqual(search.call_args_list[0].args[1], "microscopic interface measurement")
        self.assertEqual(search.call_count, 3)
        self.assertTrue(all("provider_ids" not in call.kwargs for call in search.call_args_list))

    def test_science125_search_refines_when_initial_evidence_is_not_eligible(self) -> None:
        from app.services.science125_retrieval import (
            EvidenceRecord,
            ProviderDiagnostic,
            Science125SearchResult,
        )

        job = self.store.create(1, "literature_search", {
            "science125Id": "S125-006",
            "queryText": "microscopic interface measurement",
        })
        initial = Science125SearchResult(
            profile_id="retrieval.chem.interface.v1",
            query_hash="a" * 64,
            cache_key="b" * 64,
            evidence=(EvidenceRecord(
                provider="crossref",
                stable_id="doi:10.1000/metadata",
                title="Microscopic interface measurement",
                abstract="Interfacial spectroscopy and microscopy.",
                access_status="metadata",
            ),),
            diagnostics=(ProviderDiagnostic(provider="crossref", status="succeeded"),),
        )
        refined = Science125SearchResult(
            profile_id="retrieval.chem.interface.v1",
            query_hash="c" * 64,
            cache_key="d" * 64,
            evidence=(
                EvidenceRecord(
                    provider="openalex",
                    stable_id="openalex:one",
                    title="Operando spectroscopy of microscopic interfaces",
                    abstract="Nanoscale interface measurement with calibrated microscopy.",
                    access_status="open_full_text",
                ),
                EvidenceRecord(
                    provider="semantic_scholar",
                    stable_id="semantic:two",
                    title="Molecular dynamics validation of interfacial transport",
                    abstract="Microscopic interface measurements are validated by simulation.",
                    access_status="open_full_text",
                ),
                EvidenceRecord(
                    provider="europe_pmc",
                    stable_id="pmid:three",
                    title="Nanoscale imaging of liquid solid interfaces",
                    abstract="Operando spectroscopy measures molecular interfacial dynamics.",
                    access_status="open_full_text",
                ),
            ),
            diagnostics=(ProviderDiagnostic(provider="openalex", status="succeeded"),),
        )
        with patch.dict(os.environ, self._environment(), clear=False), patch(
            "app.services.jobs.default_provider_adapters", return_value={}
        ), patch(
            "app.services.jobs.search_science125",
            side_effect=[initial, refined, refined],
        ) as search:
            output = self.service._run_literature_search(job)

        self.assertEqual(output["evidenceStatus"], "ready_for_review")
        self.assertEqual(output["evidenceReadiness"]["eligibleFullTextCount"], 3)
        self.assertGreaterEqual(output["evidenceReadiness"]["providerFamilyCount"], 2)
        self.assertEqual(len(output["refinementQueries"]), 2)
        self.assertEqual(search.call_count, 3)
        metadata_row = next(row for row in output["results"] if row["id"] == "doi:10.1000/metadata")
        self.assertFalse(metadata_row["evidenceEligibility"]["eligibleForGeneration"])
        self.assertIn("ACCESS_NOT_FULL_TEXT", metadata_row["evidenceEligibility"]["reasons"])

    def test_science125_search_uses_the_same_user_credential_environment_end_to_end(self) -> None:
        from app.services.science125_retrieval import Science125SearchResult

        job = self.store.create(7, "literature_search", {
            "science125Id": "S125-006",
            "queryText": "microscopic interface measurement",
        })
        credential_environment = {
            **self._environment(),
            "SCIENCE125_CROSSREF_MAILTO": "user@example.org",
            "SCIENCE125_OPENALEX_MAILTO": "user@example.org",
            "SCIENCE125_SEMANTIC_SCHOLAR_API_KEY": "user-semantic-key",
        }
        result = Science125SearchResult(
            profile_id="retrieval.chem.interface.v1",
            query_hash="a" * 64,
            cache_key="b" * 64,
            evidence=(),
            diagnostics=(),
        )
        with patch(
            "app.services.jobs.api_keys.api_key_store.science125_environment",
            return_value=credential_environment,
        ) as credentials, patch(
            "app.services.jobs.default_provider_adapters", return_value={}
        ) as adapters, patch(
            "app.services.jobs.search_science125", return_value=result
        ) as search:
            self.service._run_literature_search(job)

        credentials.assert_called_once_with(7)
        self.assertEqual(adapters.call_args.kwargs["environ"], credential_environment)
        self.assertEqual(search.call_args.kwargs["environ"], credential_environment)

    def test_generation_reloads_the_owned_reviewed_snapshot_and_records_hashes(self) -> None:
        search = self._complete_search()
        job = self.store.create(1, "hypothesis_generate", {
            "science125Id": "S125-006",
            "literatureSearchJobId": search.id,
            "reviewedEvidenceIds": ["doi:10.1000/one", "openalex:W2", "pmid:3"],
        })

        with patch.dict(os.environ, self._environment(), clear=False), patch(
            "app.services.jobs.load_science125_context_index", return_value=self._context_index()
        ):
            output = self.service._run_hypothesis_generate(job)

        research_output = output["researchOutput"]
        self.assertEqual(research_output["contractVersion"], "research-v1")
        self.assertEqual(research_output["profile"], "general_science")
        self.assertEqual(research_output["science125"]["questionId"], "S125-006")
        self.assertEqual(research_output["science125"]["evidenceStatus"], "sufficient")
        self.assertEqual(output["audit"]["model"], "qwen3.8-max")
        self.assertRegex(output["audit"]["policyHash"], r"^[0-9a-f]{64}$")
        self.assertRegex(output["audit"]["evidenceSnapshotHash"], r"^[0-9a-f]{64}$")
        rows = self.service.model_call_ledger.list_for_context("science125_item", job.id)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].provider, "DashScope")
        self.assertEqual(rows[0].model, "qwen3.8-max")
        self.assertEqual(rows[0].policy_hash, output["audit"]["policyHash"])
        self.assertEqual(rows[0].evidence_snapshot_hash, output["audit"]["evidenceSnapshotHash"])

    def test_generation_uses_the_current_users_saved_dashscope_key(self) -> None:
        search = self._complete_search()
        job = self.store.create(1, "hypothesis_generate", {
            "science125Id": "S125-006",
            "literatureSearchJobId": search.id,
            "reviewedEvidenceIds": ["doi:10.1000/one", "openalex:W2", "pmid:3"],
        })
        credential_environment = {
            **self._environment(),
            "DASHSCOPE_API_KEY": "saved-user-dashscope-key",
        }

        with patch.dict(os.environ, {**self._environment(), "DASHSCOPE_API_KEY": ""}, clear=False), patch(
            "app.services.jobs.api_keys.api_key_store.science125_environment",
            return_value=credential_environment,
        ) as credentials, patch(
            "app.services.jobs.load_science125_context_index", return_value=self._context_index()
        ):
            output = self.service._run_hypothesis_generate(job)

        credentials.assert_called_once_with(1)
        self.assertEqual(output["audit"]["provider"], "DashScope")
        self.assertEqual(output["audit"]["model"], "qwen3.8-max")

    def test_generation_uses_persistent_price_settings_when_process_prices_are_missing(self) -> None:
        from types import SimpleNamespace

        credential_environment = {
            "DASHSCOPE_API_KEY": "saved-user-dashscope-key",
        }
        settings = SimpleNamespace(
            qwen_input_cost_per_million_cny="3.2",
            qwen_output_cost_per_million_cny="12",
        )
        with patch(
            "app.services.jobs.api_keys.api_key_store.science125_environment",
            return_value=credential_environment,
        ), patch("app.services.jobs.get_settings", return_value=settings):
            config = self.service._required_science125_llm_config(1)

        self.assertEqual(config.model, "qwen3.8-max")
        self.assertEqual(config.input_cost_per_million_cny, 3.2)
        self.assertEqual(config.output_cost_per_million_cny, 12.0)

    def test_metadata_only_results_cannot_bypass_the_generation_evidence_gate(self) -> None:
        from app.services.jobs import SafeJobError

        search = self._complete_search(access_status="metadata")
        job = self.store.create(1, "hypothesis_generate", {
            "science125Id": "S125-006",
            "literatureSearchJobId": search.id,
            "reviewedEvidenceIds": ["doi:10.1000/one", "openalex:W2", "pmid:3"],
        })

        with patch("app.services.jobs.load_science125_context_index", return_value=self._context_index()), self.assertRaises(SafeJobError) as raised:
            self.service._run_hypothesis_generate(job)

        self.assertEqual(raised.exception.code, "EVIDENCE_INSUFFICIENT")

    def test_low_relevance_full_text_cannot_bypass_the_generation_evidence_gate(self) -> None:
        from app.services.jobs import SafeJobError

        search = self._complete_search()
        result = dict(search.result)
        result["results"] = [
            {
                **row,
                "title": "Unrelated administrative policy record",
                "abstract": "This record discusses an unrelated administrative process.",
            }
            for row in result["results"]
        ]
        search = self.store.update(search.id, result=result)
        job = self.store.create(1, "hypothesis_generate", {
            "science125Id": "S125-006",
            "literatureSearchJobId": search.id,
            "reviewedEvidenceIds": ["doi:10.1000/one", "openalex:W2", "pmid:3"],
        })

        with patch("app.services.jobs.load_science125_context_index", return_value=self._context_index()), self.assertRaises(SafeJobError) as raised:
            self.service._run_hypothesis_generate(job)

        self.assertEqual(raised.exception.code, "EVIDENCE_INSUFFICIENT")

    def test_two_provider_ids_in_one_family_do_not_satisfy_source_diversity(self) -> None:
        from app.services.jobs import SafeJobError

        search = self._complete_search()
        result = dict(search.result)
        result["results"] = [
            {**row, "providerFamily": "biomedical_index"}
            for row in result["results"]
        ]
        search = self.store.update(search.id, result=result)
        job = self.store.create(1, "hypothesis_generate", {
            "science125Id": "S125-006",
            "literatureSearchJobId": search.id,
            "reviewedEvidenceIds": ["doi:10.1000/one", "openalex:W2", "pmid:3"],
        })

        with patch("app.services.jobs.load_science125_context_index", return_value=self._context_index()), self.assertRaises(SafeJobError) as raised:
            self.service._run_hypothesis_generate(job)

        self.assertEqual(raised.exception.code, "EVIDENCE_INSUFFICIENT")

    def test_non_pilot_item_is_rejected_before_context_or_qwen_is_loaded(self) -> None:
        from app.services.jobs import SafeJobError

        job = self.store.create(1, "hypothesis_generate", {
            "science125Id": "S125-001",
            "literatureSearchJobId": "not-used",
            "reviewedEvidenceIds": ["evidence:1", "evidence:2", "evidence:3"],
        })

        with patch("app.services.jobs.load_science125_context_index", side_effect=AssertionError("context must not load")), self.assertRaises(SafeJobError) as raised:
            self.service._run_hypothesis_generate(job)

        self.assertEqual(raised.exception.code, "SCIENCE125_PILOT_NOT_ENABLED")


if __name__ == "__main__":
    unittest.main()
