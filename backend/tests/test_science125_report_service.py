from __future__ import annotations

import unittest
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))


class Science125ReportServiceSelectionTestCase(unittest.TestCase):
    def test_auto_selects_highest_nonzero_candidate_and_excludes_h0(self) -> None:
        from app.services.science125_report_service import select_science125_hypothesis

        result = select_science125_hypothesis({
            "hypotheses": [
                {
                    "id": "H1",
                    "confidence": 0.4,
                    "supportingEvidenceRefs": ["E1"],
                    "falsificationCriteria": ["criterion"],
                },
                {
                    "id": "H2",
                    "confidence": 0.7,
                    "supportingEvidenceRefs": ["E1", "E2"],
                    "falsificationCriteria": ["criterion"],
                },
                {
                    "id": "H3",
                    "confidence": 0.7,
                    "supportingEvidenceRefs": ["E1", "E2"],
                    "falsificationCriteria": ["criterion", "criterion 2"],
                },
            ],
            "nullHypothesis": {
                "id": "H0",
                "confidence": 1.0,
                "supportingEvidenceRefs": ["E1", "E2", "E3"],
                "falsificationCriteria": ["null criterion"],
            },
        })

        self.assertEqual(result.hypothesis_id, "H3")
        self.assertEqual(result.confidence, 0.7)
        self.assertIn("置信度最高", result.reason)

    def test_returns_no_selection_when_all_candidate_confidence_is_zero(self) -> None:
        from app.services.science125_report_service import select_science125_hypothesis

        result = select_science125_hypothesis({
            "hypotheses": [
                {"id": "H1", "confidence": 0, "supportingEvidenceRefs": [], "falsificationCriteria": ["a"]},
                {"id": "H2", "confidence": 0, "supportingEvidenceRefs": ["E1"], "falsificationCriteria": ["a"]},
            ],
            "nullHypothesis": {"id": "H0", "confidence": 1.0},
        })

        self.assertIsNone(result.hypothesis_id)
        self.assertEqual(result.confidence, 0.0)

    def test_auto_evidence_selection_requires_full_text_medium_relevance_and_two_families(self) -> None:
        from app.services.science125_report_service import select_science125_evidence
        from app.services.science125_retrieval import EvidenceRecord

        records = (
            EvidenceRecord(
                provider="crossref",
                stable_id="doi:10.1/one",
                title="Microscopic interface measurement with operando spectroscopy",
                abstract="Nanoscale interfacial dynamics are measured by microscopy and spectroscopy.",
                access_status="open_full_text",
            ),
            EvidenceRecord(
                provider="openalex",
                stable_id="openalex:W2",
                title="Calibrated microscopy of microscopic interface phenomena",
                abstract="Operando spectroscopy measures nanoscale interfacial transport dynamics.",
                access_status="open_full_text",
            ),
            EvidenceRecord(
                provider="europe_pmc",
                stable_id="pmid:3",
                title="Molecular interface measurement by nanoscale spectroscopy",
                abstract="Interfacial transport is quantified by calibrated microscopy and spectroscopy.",
                access_status="open_full_text",
            ),
            EvidenceRecord(
                provider="semantic_scholar",
                stable_id="semantic:metadata",
                title="Microscopic interface measurement metadata-only signal",
                abstract="Nanoscale interface measurement.",
                access_status="metadata",
            ),
            EvidenceRecord(
                provider="crossref",
                stable_id="doi:10.1/unrelated",
                title="Unrelated administrative policy record",
                abstract="This record discusses an unrelated administrative process.",
                access_status="open_full_text",
            ),
        )

        selection = select_science125_evidence(
            question_id="S125-006",
            query="microscopic interface measurement spectroscopy",
            records=records,
            max_selected=6,
        )

        self.assertEqual(selection.evidence_status, "sufficient")
        self.assertEqual(len(selection.selected), 3)
        self.assertEqual(selection.eligible_full_text_count, 3)
        self.assertGreaterEqual(selection.provider_family_count, 2)
        excluded = {item.stable_id: item.reasons for item in selection.excluded}
        self.assertIn("ACCESS_NOT_FULL_TEXT", excluded["semantic:metadata"])
        self.assertIn("RELEVANCE_BELOW_MEDIUM", excluded["doi:10.1/unrelated"])

    def test_auto_evidence_selection_blocks_when_only_one_provider_family_is_eligible(self) -> None:
        from app.services.science125_report_service import select_science125_evidence
        from app.services.science125_retrieval import EvidenceRecord

        records = tuple(
            EvidenceRecord(
                provider="crossref",
                stable_id=f"doi:10.1/{index}",
                title="Microscopic interface measurement with operando spectroscopy",
                abstract="Nanoscale interfacial dynamics are measured by microscopy and spectroscopy.",
                access_status="open_full_text",
            )
            for index in range(4)
        )

        selection = select_science125_evidence(
            question_id="S125-006",
            query="microscopic interface measurement spectroscopy",
            records=records,
            max_selected=6,
        )

        self.assertEqual(selection.evidence_status, "insufficient")
        self.assertEqual(selection.provider_family_count, 1)
        self.assertEqual(len(selection.selected), 0)


class Science125InteractiveJobImportTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        from app.services.science125_report_store import Science125ReportStore
        from app.services.science125_report_service import Science125ReportService

        self.store = Science125ReportStore(Path(self.tmp.name) / "web.db")
        self.service = Science125ReportService(store=self.store)
        self.addCleanup(self.service.dispose)

    @staticmethod
    def make_jobs():
        from app.services.job_store import StoredJob
        from test_research_contract import valid_contract

        output = valid_contract()
        output["provenance"] = {
            "provider": "DashScope",
            "model": "qwen3.8-max",
            "requestId": "request-006",
            "generatedAt": "2026-08-07T03:01:53Z",
            "promptHash": "a" * 64,
            "responseHash": "b" * 64,
        }
        output["science125"] = {
            "questionId": "S125-006",
            "routingVersion": "science125-routing-v1",
            "benchmarkDomain": "Chemistry",
            "primarySubdomain": "chem.interface",
            "crossDomainTags": ["physics", "engineering_materials"],
            "methodProfile": {"primary": "experimental", "secondary": ["computational"]},
            "promptProfile": "s125.chemistry.v1",
            "retrievalProfile": "retrieval.chem.interface.v1",
            "evidenceStatus": "sufficient",
            "domainChecks": ["source_traceability", "negative_evidence", "measurement_plan"],
        }
        output["hypotheses"][0]["confidence"] = 0.6
        output["hypotheses"][1]["confidence"] = 0.8
        output["hypotheses"][2]["confidence"] = 0.7
        now = datetime.now(UTC)
        search_result = {
            "results": [
                {
                    "id": f"doi:10.1000/{index}",
                    "title": f"Evidence {index}",
                    "abstract": "Microscopic interface measurement by calibrated spectroscopy.",
                    "sourcePlatform": provider,
                    "providerFamily": family,
                    "url": f"https://example.test/{index}",
                    "accessStatus": "open_full_text",
                    "relevanceScore": 0.8,
                    "relevanceLabel": "high",
                }
                for index, (provider, family) in enumerate(
                    (("crossref", "doi_registry"), ("openalex", "scholarly_index"), ("crossref", "doi_registry")),
                    start=1,
                )
            ],
            "query": {"queryText": "microscopic interface measurement", "queryHash": "c" * 64},
            "refinementQueries": ["operando interface spectroscopy"],
            "policyHashes": {"crossref": "d" * 64},
        }
        search_job = StoredJob(
            id="898cc53c-16ed-4bdf-91ee-7ff30e07e88a",
            user_id=1,
            type="literature_search",
            payload={"science125Id": "S125-006", "queryText": "microscopic interface measurement"},
            status="SUCCEEDED",
            stage=None,
            progress=100,
            message="Completed.",
            result=search_result,
            error=None,
            feedback_prompt=None,
            created_at=now,
            updated_at=now,
            cancel_requested=False,
        )
        job = StoredJob(
            id="73e07aff-d4e7-47c7-b8fa-a99cfdae8c64",
            user_id=1,
            type="hypothesis_generate",
            payload={
                "science125Id": "S125-006",
                "literatureSearchJobId": search_job.id,
                "reviewedEvidenceIds": [item["id"] for item in search_result["results"]],
                "sourcePageSelections": [],
            },
            status="SUCCEEDED",
            stage=None,
            progress=100,
            message="Completed.",
            result={
                "researchOutput": output,
                "audit": {
                    "provider": "DashScope",
                    "model": "qwen3.8-max",
                    "requestId": "request-006",
                    "totalTokens": 24112,
                    "latencyMs": 384115,
                    "retryCount": 0,
                    "estimatedCostCny": 0.72804,
                    "policyHash": "e" * 64,
                    "evidenceSnapshotHash": "f" * 64,
                    "evidenceCount": 3,
                    "providerFamilies": ["doi_registry", "scholarly_index"],
                },
            },
            error=None,
            feedback_prompt=None,
            created_at=now,
            updated_at=now,
            cancel_requested=False,
        )
        return job, search_job

    def test_import_is_idempotent_preserves_audit_and_does_not_call_model_or_retrieval(self) -> None:
        job, search_job = self.make_jobs()
        with patch(
            "app.services.science125_report_service.ResearchGenerationService.from_environment",
            side_effect=AssertionError("model must not be called"),
        ), patch(
            "app.services.science125_report_service.search_science125",
            side_effect=AssertionError("retrieval must not be called"),
        ):
            first = self.service.import_interactive_job(job=job, literature_job=search_job)
            second = self.service.import_interactive_job(job=job, literature_job=search_job)

        self.assertEqual(first.id, second.id)
        self.assertEqual(len(self.store.list_reports_by_question(user_id=1, question_id="S125-006")), 1)
        self.assertEqual(first.source_type, "interactive_job")
        self.assertEqual(first.source_job_id, job.id)
        self.assertEqual(first.selected_hypothesis_id, "H2")
        self.assertEqual(first.model, "qwen3.8-max")
        self.assertEqual(first.request_id, "request-006")
        self.assertEqual(first.total_tokens, 24112)
        self.assertEqual(first.research_output["nullHypothesis"]["id"], "H0")
        self.assertEqual(first.provenance["promptHash"], "a" * 64)
        exports = self.store.list_exports_for_report(user_id=1, report_id=first.id)
        self.assertEqual({item.format for item in exports}, {"json", "docx"})

    def test_private_review_snapshot_preserves_user_pdf_evidence(self) -> None:
        from dataclasses import replace

        job, search_job = self.make_jobs()
        result = dict(job.result)
        result["_reportSnapshot"] = {
            "reviewedEvidence": [
                {
                    "provider": "user_pdf",
                    "providerFamily": "user_pdf",
                    "stableId": "user_pdf:9:abc",
                    "title": "Reviewed PDF pages",
                    "abstract": "Private reviewed excerpt",
                    "accessStatus": "reviewed_full_text_excerpt",
                },
                {
                    "provider": "crossref",
                    "providerFamily": "doi_registry",
                    "stableId": "doi:10.1000/1",
                    "title": "Evidence 1",
                    "abstract": "Microscopic interface measurement by calibrated spectroscopy.",
                    "accessStatus": "open_full_text",
                },
                {
                    "provider": "openalex",
                    "providerFamily": "scholarly_index",
                    "stableId": "openalex:W2",
                    "title": "Evidence 2",
                    "abstract": "Microscopic interface measurement by calibrated microscopy.",
                    "accessStatus": "open_full_text",
                },
            ],
            "evidenceSnapshotHash": "9" * 64,
        }

        report = self.service.import_interactive_job(job=replace(job, id="33333333-3333-3333-3333-333333333333", result=result), literature_job=search_job)

        selected = report.evidence_snapshot["selectedEvidence"]
        self.assertEqual(report.selected_evidence_count, 3)
        self.assertEqual({item["provider"] for item in selected}, {"user_pdf", "crossref", "openalex"})
        self.assertEqual(report.evidence_snapshot_sha256, "9" * 64)

    def test_import_rejects_failed_non_science125_and_wrong_owner_jobs(self) -> None:
        from dataclasses import replace
        from app.services.science125_report_service import Science125ReportError

        job, search_job = self.make_jobs()
        cases = (
            replace(job, status="FAILED"),
            replace(job, payload={}),
            replace(job, user_id=2),
        )
        for invalid in cases:
            with self.subTest(status=invalid.status, payload=invalid.payload, user_id=invalid.user_id):
                with self.assertRaises(Science125ReportError):
                    self.service.import_interactive_job(job=invalid, literature_job=search_job)

    def test_full_batch_runs_all_questions_in_authoritative_order_without_model_transport(self) -> None:
        batch = self.service.create_batch(user_id=1)
        seen: list[str] = []

        def record_item(**kwargs):
            seen.append(kwargs["question_id"])
            return None

        with patch.object(self.service, "run_report_item", side_effect=record_item):
            self.service.run_batch(user_id=1, batch_id=batch.id)

        self.assertEqual(len(seen), 125)
        self.assertEqual(seen[0], "S125-001")
        self.assertEqual(seen[-1], "S125-125")
        self.assertEqual(seen, sorted(seen))

    def test_batch_retrieval_uses_short_query_plan_and_persists_its_audit_snapshot(self) -> None:
        from app.services.science125_localization import get_science125_localization
        from app.services.science125_retrieval import Science125SearchResult

        batch = self.service.create_batch(user_id=1, question_ids=("S125-006",))
        localization = get_science125_localization("S125-006")
        self.assertIsNotNone(localization)
        original_query = str(localization.recommended_query)
        empty = Science125SearchResult(
            profile_id="retrieval.chem.interface.v1",
            query_hash="a" * 64,
            cache_key="b" * 64,
            evidence=(),
            diagnostics=(),
        )
        context_index = SimpleNamespace(items={
            "S125-006": SimpleNamespace(
                source_context="Hash-verified booklet context about microscopic interface measurement.",
            ),
        })

        with patch(
            "app.services.science125_report_service.load_science125_context_index",
            return_value=context_index,
        ), patch(
            "app.services.science125_report_service.api_keys.api_key_store.science125_environment",
            return_value={},
        ), patch(
            "app.services.science125_report_service.default_provider_adapters",
            return_value={},
        ), patch(
            "app.services.science125_report_service.search_science125",
            return_value=empty,
        ) as search:
            report = self.service.run_report_item(
                user_id=1,
                batch_id=batch.id,
                question_id="S125-006",
            )

        self.assertEqual(report.status, "BLOCKED_EVIDENCE")
        self.assertGreaterEqual(search.call_count, 3)
        self.assertLessEqual(search.call_count, 4)
        executed = [call.args[1] for call in search.call_args_list]
        self.assertTrue(all(query != original_query for query in executed))
        self.assertTrue(all(len(query) <= 180 for query in executed))
        self.assertEqual(report.retrieval_snapshot["originalQueryText"], original_query)
        self.assertTrue(report.retrieval_snapshot["topicSummary"])
        self.assertGreaterEqual(len(report.retrieval_snapshot["keywords"]), 6)
        self.assertEqual(
            [item["query"] for item in report.retrieval_snapshot["queries"]],
            executed,
        )

    def test_batch_pauses_on_rate_limit_and_retry_increments_attempt(self) -> None:
        from app.services.science125_report_service import Science125ReportError

        batch = self.service.create_batch(user_id=1, question_ids=("S125-006",))
        with patch.object(
            self.service,
            "run_report_item",
            side_effect=Science125ReportError("SCIENCE125_QWEN_FAILED", "status=429, retry later"),
        ):
            paused = self.service.run_batch(user_id=1, batch_id=batch.id)

        self.assertEqual(paused.status, "PAUSED")
        item = self.store.list_batch_items(user_id=1, batch_id=batch.id)[0]
        self.assertEqual(item.status, "FAILED")
        retry_ids = self.store.prepare_items_for_retry(user_id=1, batch_id=batch.id)
        retried = self.store.list_batch_items(user_id=1, batch_id=batch.id)[0]
        self.assertEqual(retry_ids, ("S125-006",))
        self.assertEqual(retried.status, "RETRYING")
        self.assertEqual(retried.attempt_number, 2)

    def test_submitted_runner_reschedules_when_resume_races_with_pause_exit(self) -> None:
        batch = self.service.create_batch(user_id=1, question_ids=("S125-006", "S125-043"))
        self.store.mark_batch_running(user_id=1, batch_id=batch.id)

        with patch.object(self.service, "_run_batch", return_value=batch), patch.object(
            self.service,
            "_submit_batch",
        ) as submit:
            self.service._run_submitted_batch(
                batch_id=batch.id,
                user_id=1,
                question_ids=None,
            )

        submit.assert_called_once_with(batch_id=batch.id, user_id=1, question_ids=None)

    def test_final_docx_is_blocked_until_batch_has_125_succeeded_reports(self) -> None:
        from app.services.science125_report_service import Science125ReportError

        batch = self.service.create_batch(user_id=1, question_ids=("S125-006",))
        with self.assertRaises(Science125ReportError) as raised:
            self.service.create_batch_export(user_id=1, batch_id=batch.id, format="docx")
        self.assertEqual(raised.exception.code, "BATCH_FINAL_DOCX_NOT_READY")


if __name__ == "__main__":
    unittest.main()
