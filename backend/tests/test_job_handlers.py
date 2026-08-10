from __future__ import annotations

import json
import zipfile
import sys
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier, Lock
from types import SimpleNamespace
from unittest.mock import patch

import fitz
import numpy as np
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


BACKEND_DIR = Path(__file__).resolve().parents[1]
ROOT_DIR = BACKEND_DIR.parents[1]
sys.path.insert(0, str(BACKEND_DIR))
sys.path.insert(0, str(ROOT_DIR / "src"))
sys.path.insert(0, str(ROOT_DIR))


class JobHandlerTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp.name)
        from app.core.legacy import db
        from app.services.job_store import WebJobStore
        from app.services.jobs import JobService

        database = db()
        self.engine = create_engine(f"sqlite:///{self.tmp_path / 'legacy.db'}", future=True)
        database.SessionLocal = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)
        database.ENGINE = self.engine
        database.Base.metadata.create_all(self.engine)
        self.store = WebJobStore(self.tmp_path / "web.db")
        self.service = JobService(self.store)

    def create_document_with_chunks(
        self,
        texts: list[str],
        *,
        user_id: int = 1,
        title: str = "Owned paper",
        source_path: str | None = None,
    ) -> int:
        from app.core.legacy import db

        session = db().get_session()
        try:
            document = db().create_document(session, user_id, title, "pdf", source_path)
            db().add_document_chunks(
                session,
                user_id,
                document.id,
                [(index, text, b"vector", "text") for index, text in enumerate(texts)],
            )
            return document.id
        finally:
            session.close()

    def tearDown(self) -> None:
        self.service._executor.shutdown(wait=True, cancel_futures=True)
        if hasattr(self.service, "_hypothesis_executor"):
            self.service._hypothesis_executor.shutdown(wait=True, cancel_futures=True)
        self.service.model_call_ledger.dispose()
        self.service.science125_rate_store.dispose()
        self.service.analysis_store.dispose()
        if hasattr(self.service, "hypothesis_store"):
            self.service.hypothesis_store.dispose()
        if hasattr(self.service, "science_store"):
            self.service.science_store.dispose()
        self.store.dispose()
        self.engine.dispose()
        self.tmp.cleanup()

    def test_literature_search_normalizes_results_and_platform_diagnostics(self) -> None:
        class Result:
            def to_reference_dict(self):
                return {
                    "title": "Result title",
                    "authors": "A. Author",
                    "journal": "Journal",
                    "year": "2025",
                    "doi": "10.1000/result",
                    "abstract": "Abstract",
                    "source_platform": "crossref",
                    "url": "https://example.test/result",
                    "is_open_access": True,
                    "search_relevance_score": 0.75,
                    "access_status": "metadata_only",
                    "needs_fulltext": True,
                    "warning": "Upload full text",
                }

        class Diagnostics:
            results = [Result()]
            platform_status = {"crossref": {"status": "ok", "count": 1}}
            warnings = []

        captured = {}

        class Engine:
            def __init__(self, platforms):
                captured["platforms"] = platforms
                self.semantic_scholar_api_key = ""
                self.ncbi_api_key = ""
                self.crossref_mailto = ""
                self.session = SimpleNamespace(headers={})

            def search_with_diagnostics(self, query):
                captured["credentials"] = {
                    "semantic": self.semantic_scholar_api_key,
                    "ncbi": self.ncbi_api_key,
                    "mailto": self.crossref_mailto,
                    "userAgent": self.session.headers.get("User-Agent"),
                }
                captured["query"] = query
                return Diagnostics()

        class SearchModule:
            SearchQuery = __import__("chalk_app.literature.literature_search", fromlist=["SearchQuery"]).SearchQuery
            LiteratureSearchEngine = Engine

        job = self.store.create(
            1,
            "literature_search",
            {"queryText": "electrocatalysis", "platforms": ["Crossref"], "maxResults": 7},
        )
        provider_values = {
            "semantic_scholar": "semantic-key",
            "ncbi": "ncbi-key",
            "crossref_mailto": "researcher@example.com",
        }
        with (
            patch("app.services.jobs.literature_search", return_value=SearchModule),
            patch(
                "app.services.jobs.api_keys.api_key_store.get_key",
                side_effect=lambda _user_id, provider="dashscope": provider_values.get(provider),
            ),
        ):
            result = self.service._run_literature_search(job)

        self.assertTrue(captured["platforms"]["crossref"])
        self.assertFalse(captured["platforms"]["arxiv"])
        self.assertEqual(captured["query"].max_results, 7)
        self.assertEqual(result["results"][0]["sourcePlatform"], "crossref")
        self.assertEqual(result["results"][0]["relevanceScore"], 0.75)
        self.assertEqual(result["platformStatus"]["crossref"]["count"], 1)
        self.assertEqual(captured["credentials"]["semantic"], "semantic-key")
        self.assertEqual(captured["credentials"]["ncbi"], "ncbi-key")
        self.assertEqual(captured["credentials"]["mailto"], "researcher@example.com")
        self.assertIn("researcher@example.com", captured["credentials"]["userAgent"])

    def test_successful_science125_job_is_adopted_without_changing_job_success(self) -> None:
        search = self.store.create(1, "literature_search", {"science125Id": "S125-006"})
        self.store.update(search.id, status="SUCCEEDED", result={"results": []})
        job = self.store.create(1, "hypothesis_generate", {
            "science125Id": "S125-006",
            "literatureSearchJobId": search.id,
        })
        self.service._handlers["hypothesis_generate"] = lambda _job: {
            "researchOutput": {"contractVersion": "research-v1"},
            "_reportSnapshot": {"reviewedEvidence": [{"abstract": "private evidence text"}]},
        }
        adopted = SimpleNamespace(
            id="11111111-1111-1111-1111-111111111111",
            batch_id="22222222-2222-2222-2222-222222222222",
            source_type="interactive_job",
            source_job_id=job.id,
        )
        report_service = SimpleNamespace(import_interactive_job=lambda **_kwargs: adopted)

        with patch("app.services.science125_report_service.get_science125_report_service", return_value=report_service):
            self.service._execute(job.id)

        completed = self.store.get(job.id)
        self.assertEqual(completed.status, "SUCCEEDED")
        self.assertEqual(completed.result["science125Report"]["reportId"], adopted.id)
        self.assertNotIn("_reportSnapshot", completed.result)

    def test_report_adoption_failure_does_not_rewrite_successful_science125_job(self) -> None:
        search = self.store.create(1, "literature_search", {"science125Id": "S125-006"})
        self.store.update(search.id, status="SUCCEEDED", result={"results": []})
        job = self.store.create(1, "hypothesis_generate", {
            "science125Id": "S125-006",
            "literatureSearchJobId": search.id,
        })
        result = {"researchOutput": {"contractVersion": "research-v1"}}
        self.service._handlers["hypothesis_generate"] = lambda _job: result
        report_service = SimpleNamespace(import_interactive_job=lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("report failed")))

        with patch("app.services.science125_report_service.get_science125_report_service", return_value=report_service):
            self.service._execute(job.id)

        completed = self.store.get(job.id)
        self.assertEqual(completed.status, "SUCCEEDED")
        self.assertEqual(completed.result, result)

    def test_lab_suggestion_uses_owned_record_context_and_persists_result(self) -> None:
        from app.core.legacy import db

        document_id = self.create_document_with_chunks(["paper text"], title="Related paper")
        session = db().get_session()
        try:
            document = session.get(db().Document, document_id)
            document.summary = "Related paper summary"
            record = db().create_lab_record(
                session,
                1,
                "Run",
                "Observed heat",
                json.dumps([document_id]),
            )
            record_id = record.id
        finally:
            session.close()

        class LlmModule:
            LLM_ERROR_PREFIX = "__CHALK_LLM_ERROR__:"

            class LLMConfig:
                def __init__(self, api_key=None):
                    self.api_key = api_key

            @staticmethod
            def lab_record_suggest(content, summaries, config=None):
                self.assertEqual(content, "Observed heat")
                self.assertIn("Related paper summary", summaries[0])
                return "Check temperature and stop heating if it rises unexpectedly."

        job = self.store.create(1, "lab_suggest", {"recordId": record_id})
        with (
            patch("app.services.jobs.llm_client", return_value=LlmModule),
            patch("app.services.jobs.api_keys.api_key_store.get_key", return_value="test-key"),
        ):
            result = self.service._run_lab_suggest(job)

        self.assertEqual(result["recordId"], record_id)
        session = db().get_session()
        try:
            self.assertEqual(session.get(db().LabRecord, record_id).ai_suggestion, result["suggestion"])
        finally:
            session.close()

    def test_evidence_manifest_job_indexes_rows_and_removes_temporary_upload(self) -> None:
        from app.core.legacy import db

        manifest = self.tmp_path / "evidence-imports" / "1" / "manifest.csv"
        manifest.parent.mkdir(parents=True)
        manifest.write_text(
            "material_system,reaction_context,adsorbate,adsorption_energy\nNiFe,OER,*OH,-1.2\n",
            encoding="utf-8",
        )
        job = self.store.create(1, "evidence_manifest_import", {
            "storedPath": str(manifest),
            "originalFilename": "manifest.csv",
            "datasetName": "OC22",
        })

        result = self.service._run_evidence_manifest_import(job)

        self.assertEqual(result["imported"], 1)
        self.assertFalse(manifest.exists())
        session = db().get_session()
        try:
            rows = session.query(db().ComputationalCatalysisEvidence).filter_by(user_id=1).all()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].dataset_name, "OC22")
        finally:
            session.close()

    def test_multimodal_active_job_deduplication_includes_question_and_options(self) -> None:
        first_payload = {
            "sources": [{"sourceType": "upload", "assetId": "asset-1", "sheetNames": []}],
            "question": "Analyze activity",
            "useLiteratureContext": False,
        }
        second_payload = {
            **first_payload,
            "question": "Analyze stability",
        }

        with patch.object(self.service._executor, "submit"):
            first = self.service.create(1, "multimodal_analyze", first_payload)
            duplicate = self.service.create(1, "multimodal_analyze", dict(first_payload))
            different = self.service.create(1, "multimodal_analyze", second_payload)

        self.assertEqual(duplicate.id, first.id)
        self.assertNotEqual(different.id, first.id)

    def test_rag_qa_returns_real_chunk_citations(self) -> None:
        from app.core.legacy import db

        session = db().get_session()
        try:
            document = db().create_document(session, 1, "Owned paper", "pdf", None)
            document_id = document.id
        finally:
            session.close()

        chunks = [
            {
                "text": "Measured result from the uploaded paper.",
                "score": 0.81,
                "source_title": "Owned paper",
                "document_id": document_id,
                "chunk_id": 9,
                "context_markers": "[有后文延续]",
            }
        ]

        class RagModule:
            @staticmethod
            def search_with_context(*args, **kwargs):
                return chunks

        class LlmModule:
            LLM_ERROR_PREFIX = "__CHALK_LLM_ERROR__:"

            class LLMConfig:
                def __init__(self, api_key=None):
                    self.api_key = api_key

            @staticmethod
            def call_llm(question, contexts, config=None):
                return "Answer based on fragment 1."

        job = self.store.create(1, "rag_qa", {"question": "What was measured?", "documentId": document_id})
        with (
            patch("app.services.jobs.rag", return_value=RagModule),
            patch("app.services.jobs.llm_client", return_value=LlmModule),
            patch("app.services.jobs.api_keys.api_key_store.get_key", return_value="test-key"),
        ):
            result = self.service._run_rag_qa(job)

        self.assertEqual(result["answer"], "Answer based on fragment 1.")
        self.assertEqual(result["citations"][0]["documentId"], document_id)
        self.assertEqual(result["citations"][0]["chunkId"], 9)
        self.assertEqual(result["citations"][0]["score"], 0.81)

    def test_pdf_import_uses_legacy_extraction_and_saves_document_chunks(self) -> None:
        from app.core.legacy import db

        pdf_path = self.tmp_path / "uploads" / "1" / "paper.pdf"
        pdf_path.parent.mkdir(parents=True)
        pdf = fitz.open()
        page = pdf.new_page()
        page.insert_text((72, 72), "Measured catalytic performance from an uploaded paper.")
        pdf.save(pdf_path)
        pdf.close()

        class RagModule:
            @staticmethod
            def embed_texts(texts, api_key=None):
                return np.ones((len(texts), 4), dtype="float32")

        job = self.store.create(1, "pdf_import", {
            "storedPath": str(pdf_path),
            "originalFilename": "paper.pdf",
            "title": "Imported paper",
        })
        with (
            patch("app.services.jobs.rag", return_value=RagModule),
            patch("app.services.jobs.api_keys.api_key_store.get_key", return_value=None),
        ):
            result = self.service._run_pdf_import(job)

        self.assertTrue(pdf_path.exists())
        self.assertEqual(result["title"], "Imported paper")
        self.assertGreaterEqual(result["chunkCount"], 1)
        session = db().get_session()
        try:
            document = session.get(db().Document, result["documentId"])
            self.assertEqual(document.user_id, 1)
            self.assertEqual(document.source_path, str(pdf_path.resolve()))
            self.assertGreaterEqual(session.query(db().DocumentChunk).filter_by(document_id=document.id).count(), 1)
        finally:
            session.close()

    def test_pdf_import_removes_upload_when_extraction_fails(self) -> None:
        pdf_path = self.tmp_path / "uploads" / "1" / "broken.pdf"
        pdf_path.parent.mkdir(parents=True)
        pdf_path.write_bytes(b"%PDF-1.4\n%%EOF")
        job = self.store.create(1, "pdf_import", {
            "storedPath": str(pdf_path),
            "originalFilename": "broken.pdf",
        })

        class PdfModule:
            @staticmethod
            def extract_text_from_pdf(path):
                raise RuntimeError("parser failed")

        with patch("app.services.jobs.pdf_utils", return_value=PdfModule):
            with self.assertRaises(RuntimeError):
                self.service._run_pdf_import(job)

        self.assertFalse(pdf_path.exists())

    def test_pdf_reimport_replaces_chunks_without_creating_a_second_document(self) -> None:
        from app.core.legacy import db

        old_path = self.tmp_path / "uploads" / "1" / "old.pdf"
        old_path.parent.mkdir(parents=True)
        old_path.write_bytes(b"%PDF-1.4\n%%EOF")
        document_id = self.create_document_with_chunks(["old text"], source_path=str(old_path))
        replacement = self.tmp_path / "uploads" / "1" / "replacement.pdf"
        replacement.write_bytes(b"%PDF-1.4\n%%EOF")

        class PdfModule:
            @staticmethod
            def extract_text_from_pdf(path):
                return "new extracted text"

            @staticmethod
            def split_text_into_chunks(text):
                return [text]

        class RagModule:
            @staticmethod
            def embed_texts(texts, api_key=None):
                return np.ones((len(texts), 4), dtype="float32")

        job = self.store.create(1, "pdf_reimport", {
            "documentId": document_id,
            "storedPath": str(replacement),
            "originalFilename": "replacement.pdf",
        })
        with (
            patch("app.services.jobs.pdf_utils", return_value=PdfModule),
            patch("app.services.jobs.rag", return_value=RagModule),
            patch("app.services.jobs.api_keys.api_key_store.get_key", return_value=None),
        ):
            result = self.service._run_pdf_reimport(job)

        self.assertEqual(result["documentId"], document_id)
        self.assertFalse(old_path.exists())
        self.assertTrue(replacement.exists())
        session = db().get_session()
        try:
            self.assertEqual(session.query(db().Document).count(), 1)
            chunks = session.query(db().DocumentChunk).filter_by(document_id=document_id).all()
            self.assertEqual([chunk.text for chunk in chunks], ["new extracted text"])
        finally:
            session.close()

    def test_document_summary_processes_all_segments_and_persists_latest_result(self) -> None:
        from app.core.legacy import db

        document_id = self.create_document_with_chunks(["a" * 7000, "b" * 7000])
        calls: list[str] = []

        class LlmModule:
            LLM_ERROR_PREFIX = "__CHALK_LLM_ERROR__:"

            class LLMConfig:
                def __init__(self, api_key=None):
                    self.api_key = api_key

            @staticmethod
            def summarize_chemistry_document(text, config=None):
                calls.append(text)
                return f"summary-{len(calls)}"

        job = self.store.create(1, "document_analysis", {
            "documentId": document_id,
            "analysisType": "summary",
        })
        with (
            patch("app.services.jobs.llm_client", return_value=LlmModule),
            patch("app.services.jobs.api_keys.api_key_store.get_key", return_value="test-key"),
        ):
            result = self.service._run_document_analysis(job)

        self.assertEqual(result["type"], "summary")
        self.assertEqual(result["result"]["segmentCount"], 2)
        self.assertEqual(result["result"]["sourceCharCount"], 14002)
        self.assertIn("a" * 100, calls[0])
        self.assertTrue(any("b" * 100 in value for value in calls[:2]))
        self.assertGreaterEqual(len(calls), 3)
        session = db().get_session()
        try:
            self.assertEqual(session.get(db().Document, document_id).summary, result["result"]["summary"])
        finally:
            session.close()

    def test_document_translation_keeps_source_segments_and_merges_glossary(self) -> None:
        document_id = self.create_document_with_chunks(["x" * 9000])
        call_count = 0

        class LlmModule:
            LLM_ERROR_PREFIX = "__CHALK_LLM_ERROR__:"

            class LLMConfig:
                def __init__(self, api_key=None):
                    self.api_key = api_key

            @staticmethod
            def translate_with_glossary(text, glossary=None, config=None):
                nonlocal call_count
                call_count += 1
                term = "Faradaic efficiency" if call_count == 1 else "faradaic efficiency"
                return (
                    '{"translation":"translated-' + str(call_count) + '","glossary":['
                    '{"en":"' + term + '","zh":"法拉第效率","note":"FE"},'
                    '{"en":"overpotential","zh":"过电位","note":""}]}'
                )

        job = self.store.create(1, "document_analysis", {
            "documentId": document_id,
            "analysisType": "translation",
        })
        with (
            patch("app.services.jobs.llm_client", return_value=LlmModule),
            patch("app.services.jobs.api_keys.api_key_store.get_key", return_value="test-key"),
        ):
            result = self.service._run_document_analysis(job)["result"]

        self.assertEqual(len(result["segments"]), 2)
        self.assertEqual("".join(segment["sourceText"] for segment in result["segments"]), "x" * 9000)
        self.assertEqual(len(result["glossary"]), 2)

    def test_invalid_structured_model_output_fails_without_replacing_previous_result(self) -> None:
        from app.services.jobs import SafeJobError

        document_id = self.create_document_with_chunks(["experimental procedure"])
        previous = self.service.analysis_store.upsert(
            1, "sop", [document_id], {"title": "previous"}, job_id="old-job"
        )

        class LlmModule:
            LLM_ERROR_PREFIX = "__CHALK_LLM_ERROR__:"

            class LLMConfig:
                def __init__(self, api_key=None):
                    self.api_key = api_key

            @staticmethod
            def extract_sop(text, config=None):
                return "not-json"

        job = self.store.create(1, "document_analysis", {
            "documentId": document_id,
            "analysisType": "sop",
        })
        with (
            patch("app.services.jobs.llm_client", return_value=LlmModule),
            patch("app.services.jobs.api_keys.api_key_store.get_key", return_value="test-key"),
        ):
            with self.assertRaises(SafeJobError) as context:
                self.service._run_document_analysis(job)

        self.assertEqual(context.exception.code, "INVALID_MODEL_OUTPUT")
        loaded = self.service.analysis_store.get_for_scope(1, "sop", [document_id])
        self.assertEqual(loaded.id, previous.id)
        self.assertEqual(loaded.result, {"title": "previous"})

    def test_document_analysis_rejects_oversized_text_before_model_call(self) -> None:
        from app.services.jobs import SafeJobError

        document_id = self.create_document_with_chunks(["12345678901"])
        self.service._max_analysis_chars = 10
        job = self.store.create(1, "document_analysis", {
            "documentId": document_id,
            "analysisType": "summary",
        })

        with self.assertRaises(SafeJobError) as context:
            self.service._run_document_analysis(job)

        self.assertEqual(context.exception.code, "DOCUMENT_TOO_LARGE")

    def test_image_extraction_registers_managed_assets_without_returning_paths(self) -> None:
        pdf_path = self.tmp_path / "paper.pdf"
        pdf_path.write_bytes(b"%PDF-1.4\n%%EOF")
        document_id = self.create_document_with_chunks(["figure text"], source_path=str(pdf_path))

        class ImageExtractorModule:
            @staticmethod
            def extract_document_pdf_images(pdf_path, base_dir, user_id, doc_id, **kwargs):
                output = Path(base_dir) / "data" / "visualizations" / f"user_{user_id}" / f"doc_{doc_id}"
                output.mkdir(parents=True, exist_ok=True)
                image = output / "pdf_image_1.png"
                image.write_bytes(b"\x89PNG\r\n\x1a\nimage")
                return [str(image)]

        job = self.store.create(1, "document_analysis", {
            "documentId": document_id,
            "analysisType": "images",
            "useLlmPageHints": False,
        })
        with patch("app.services.jobs.document_image_extractor", return_value=ImageExtractorModule):
            result = self.service._run_document_analysis(job)["result"]

        self.assertEqual(result["count"], 1)
        self.assertNotIn("path", result["images"][0])
        asset = self.service.analysis_store.get_asset_for_user(
            1, document_id, result["images"][0]["assetId"]
        )
        self.assertTrue(asset.path.is_file())
        self.assertTrue(asset.path.is_relative_to(self.service.assets_dir))

    def test_manual_structure_lookup_does_not_require_an_llm_key(self) -> None:
        document_id = self.create_document_with_chunks(["document text"])

        class Info:
            cid = 702
            iupac_name = "ethanol"
            molecular_formula = "C2H6O"
            molecular_weight = "46.07"
            cas = "64-17-5"
            canonical_smiles = "CCO"
            inchi = "InChI=1S/C2H6O"
            inchikey = "LFQSCWFLJHTTHZ-UHFFFAOYSA-N"
            melting_point = "-114 C"
            boiling_point = "78 C"
            density = "0.789 g/cm3"
            solubility = "miscible"
            appearance = "colorless liquid"
            pubchem_url = "https://pubchem.ncbi.nlm.nih.gov/compound/702"

        class ChemModule:
            @staticmethod
            def query_chem_info(name):
                return Info()

            @staticmethod
            def fetch_structure_image(name):
                return b"\x89PNG\r\n\x1a\nstructure"

        job = self.store.create(1, "document_analysis", {
            "documentId": document_id,
            "analysisType": "structures",
            "chemicalName": "ethanol",
        })
        with patch("app.services.jobs.chem_structure", return_value=ChemModule):
            result = self.service._run_document_analysis(job)["result"]

        self.assertEqual(result["compounds"][0]["molecularFormula"], "C2H6O")
        self.assertIn("assetId", result["compounds"][0])

    def test_document_comparison_uses_full_document_summaries_and_persists_result(self) -> None:
        first_id = self.create_document_with_chunks(["first source"], title="First")
        second_id = self.create_document_with_chunks(["second source"], title="Second")
        compared: list[dict] = []

        class LlmModule:
            LLM_ERROR_PREFIX = "__CHALK_LLM_ERROR__:"

            class LLMConfig:
                def __init__(self, api_key=None):
                    self.api_key = api_key

            @staticmethod
            def summarize_chemistry_document(text, config=None):
                return f"digest:{text}"

            @staticmethod
            def multi_doc_compare(documents, config=None):
                compared.extend(documents)
                return "| Document | Finding |\n|---|---|\n| First | result |"

        job = self.store.create(1, "document_compare", {"documentIds": [second_id, first_id]})
        with (
            patch("app.services.jobs.llm_client", return_value=LlmModule),
            patch("app.services.jobs.api_keys.api_key_store.get_key", return_value="test-key"),
        ):
            result = self.service._run_document_compare(job)

        self.assertEqual([item["title"] for item in compared], ["First", "Second"])
        self.assertIn("digest:first source", compared[0]["text"])
        self.assertEqual(result["result"]["markdown"].splitlines()[0], "| Document | Finding |")
        persisted = self.service.analysis_store.get_comparison(1, [first_id, second_id])
        self.assertIsNotNone(persisted)

    def test_model_analysis_without_an_api_key_returns_an_explicit_error(self) -> None:
        from app.services.jobs import SafeJobError

        document_id = self.create_document_with_chunks(["document text"])
        job = self.store.create(1, "document_analysis", {
            "documentId": document_id,
            "analysisType": "summary",
        })
        with (
            patch("app.services.jobs.api_keys.api_key_store.get_key", return_value=None),
            patch.dict("os.environ", {"DASHSCOPE_API_KEY": "", "QWEN_API_KEY": ""}),
        ):
            with self.assertRaises(SafeJobError) as context:
                self.service._run_document_analysis(job)

        self.assertEqual(context.exception.code, "API_KEY_REQUIRED")

    def test_safety_analysis_combines_local_and_pubchem_results(self) -> None:
        document_id = self.create_document_with_chunks(["ethanol and unknown reagent"])

        class LlmModule:
            LLM_ERROR_PREFIX = "__CHALK_LLM_ERROR__:"

            class LLMConfig:
                def __init__(self, api_key=None):
                    self.api_key = api_key

            @staticmethod
            def extract_chemical_names_llm(text, config=None):
                return ["ethanol", "unknown reagent"]

        class HazardModule:
            @staticmethod
            def lookup(name):
                if name != "ethanol":
                    return None
                return {
                    "signal_word": "Danger",
                    "ghs_codes": ["H225"],
                    "pictograms": ["Flame"],
                    "hazards": ["Highly flammable liquid"],
                    "high_risk": True,
                }

        online = {
            "name": "unknown reagent",
            "source": "pubchem",
            "status": "unavailable",
            "signalWord": "",
            "ghsCodes": [],
            "pictograms": [],
            "hazards": [],
            "highRisk": False,
            "warning": "PubChem safety data could not be retrieved.",
        }
        job = self.store.create(1, "document_analysis", {
            "documentId": document_id,
            "analysisType": "safety",
        })
        with (
            patch("app.services.jobs.llm_client", return_value=LlmModule),
            patch("app.services.jobs.hazard_db", return_value=HazardModule),
            patch("app.services.jobs.lookup_pubchem_hazard", return_value=online),
            patch("app.services.jobs.api_keys.api_key_store.get_key", return_value="test-key"),
        ):
            result = self.service._run_document_analysis(job)["result"]

        self.assertEqual(result["highRiskNames"], ["ethanol"])
        self.assertEqual(result["chemicals"][0]["source"], "local")
        self.assertEqual(result["chemicals"][1]["source"], "pubchem")
        self.assertEqual(len(result["warnings"]), 1)

    def test_duplicate_active_document_analysis_returns_the_existing_job(self) -> None:
        payload = {"documentId": 5, "analysisType": "summary"}
        with patch.object(self.service._executor, "submit") as submit:
            first = self.service.create(1, "document_analysis", payload)
            second = self.service.create(1, "document_analysis", payload)

        self.assertEqual(first.id, second.id)
        submit.assert_called_once()

    def test_concurrent_duplicate_analysis_creation_is_serialized(self) -> None:
        payload = {"documentId": 5, "analysisType": "summary"}
        start = Barrier(2)
        counter_lock = Lock()
        active_calls = 0
        max_active_calls = 0
        original_list = self.store.list_for_user

        def slow_list(*args, **kwargs):
            nonlocal active_calls, max_active_calls
            with counter_lock:
                active_calls += 1
                max_active_calls = max(max_active_calls, active_calls)
            try:
                time.sleep(0.05)
                return original_list(*args, **kwargs)
            finally:
                with counter_lock:
                    active_calls -= 1

        def create_job():
            start.wait()
            return self.service.create(1, "document_analysis", payload)

        with (
            patch.object(self.service._executor, "submit") as submit,
            patch.object(self.store, "list_for_user", side_effect=slow_list),
            ThreadPoolExecutor(max_workers=2) as pool,
        ):
            jobs = [future.result() for future in [pool.submit(create_job), pool.submit(create_job)]]

        self.assertEqual(jobs[0].id, jobs[1].id)
        self.assertEqual(max_active_calls, 1)
        submit.assert_called_once()

    def test_hypothesis_generation_uses_owned_documents_and_persists_the_full_result(self) -> None:
        first_id = self.create_document_with_chunks(["first chunk", "second chunk"], title="First paper")
        second_id = self.create_document_with_chunks(["third chunk"], title="Second paper")
        multimodal_run = self.service.science_store.create_multimodal_run(
            user_id=1,
            question="",
            options={},
            source_refs=[{"sourceType": "documentAsset", "documentId": first_id, "assetId": "figure-1"}],
            result={
                "context": "verified multimodal context",
                "evidence": {"figures": [{"imageType": "XRD"}]},
                "quantitative": {"scalingRelations": [{"equation": "y = x"}]},
            },
            job_id="multimodal-job",
        )
        captured: dict = {}

        class FakeOrchestrator:
            def __init__(self, **kwargs):
                captured["orchestratorOptions"] = kwargs

            def run(self, literature_text, research_question):
                captured["literatureText"] = literature_text
                captured["researchQuestion"] = research_question
                return SimpleNamespace(
                    raw_json={
                        "paper_title": "Persisted hypothesis",
                        "problem_statement": "A testable problem",
                        "confidence": 8,
                        "feasibility": "high",
                    },
                    final_output="",
                    confidence=8,
                    feasibility="high",
                    iterations=[{"round": 1}],
                    critique_history=[{"round": 1, "score": 8}],
                    reasoning_chain={"steps": []},
                    verification_report={"status": "ok"},
                    results_verification={"status": "ok"},
                    cross_domain_analogies={},
                    debate_history=[],
                    closed_loop_validation={},
                    executable_validation={},
                    scientific_toolkit={},
                    interaction_history={"session_id": "session-auto", "mode": "auto", "interactions": []},
                )

        job = self.store.create(1, "hypothesis_generate", {
            "researchQuestion": "What should be tested?",
            "sourceDocIds": [first_id, second_id],
            "supplementalContext": "manual context",
            "maxIterations": 2,
            "hitlEnabled": False,
            "autoVerify": True,
            "domain": "materials_chemistry",
            "multimodalRunIds": [multimodal_run.id],
        })
        self.store.update(job.id, status="RUNNING")
        running = self.store.get(job.id)

        with (
            patch("app.services.jobs.agent_framework", return_value=SimpleNamespace(HypothesisOrchestrator=FakeOrchestrator)),
            patch.object(self.service, "_build_hypothesis_evidence", return_value=("rag context", {"status": "ok"}, [])),
            patch.object(self.service, "_create_hypothesis_artifacts", return_value=([], [])),
            patch("app.services.jobs.api_keys.api_key_store.get_key", return_value="test-key"),
        ):
            result = self.service._run_hypothesis_generate(running)

        from app.core.legacy import db

        session = db().get_session()
        try:
            hypotheses = session.query(db().Hypothesis).all()
            feedback = session.query(db().HypothesisFeedback).all()
        finally:
            session.close()

        self.assertEqual(len(hypotheses), 1)
        self.assertEqual(feedback, [])
        self.assertEqual(result["hypothesisId"], hypotheses[0].id)
        self.assertNotIn("rawJson", result)
        self.assertIn("First paper", captured["literatureText"])
        self.assertIn("Second paper", captured["literatureText"])
        self.assertIn("manual context", captured["literatureText"])
        self.assertEqual(json.loads(hypotheses[0].related_doc_ids), [first_id, second_id])
        self.assertEqual(captured["orchestratorOptions"]["multimodal_context"], "verified multimodal context")
        self.assertEqual(captured["orchestratorOptions"]["multimodal_evidence"]["figures"][0]["imageType"], "XRD")
        self.assertEqual(captured["orchestratorOptions"]["quantitative_report"]["scalingRelations"][0]["equation"], "y = x")
        self.assertEqual(
            captured["orchestratorOptions"]["external_data_api_keys"],
            {"materials_project": "test-key"},
        )
        extra = json.loads(hypotheses[0].extra_json)
        self.assertEqual(extra["multimodal_run_snapshots"][0]["revision"], 1)

    def test_dataset_enricher_passes_the_user_materials_project_key(self) -> None:
        from app.core.legacy import agent_framework

        captured: dict[str, str] = {}

        class ExternalKnowledgeAdapter:
            @staticmethod
            def query_material(formula, api_key=None, session=None):
                captured["formula"] = formula
                captured["apiKey"] = api_key
                if formula != "TiO2":
                    return None
                return {
                    "material_id": "mp-2657",
                    "formula_pretty": "TiO2",
                    "band_gap": 3.2,
                    "formation_energy_per_atom": -3.1,
                }

            @staticmethod
            def query_compound_properties(_name):
                return None

        hypothesis = json.dumps({
            "technical_details": "Evaluate TiO2 electrocatalyst interfaces",
            "methods": "DFT and operando spectroscopy",
            "problem_statement": "Resolve the TiO2 active surface",
        })
        with patch.dict(
            sys.modules,
            {"knowledge_base": SimpleNamespace(ExternalKnowledgeAdapter=ExternalKnowledgeAdapter)},
        ):
            output = agent_framework().DatasetSourceEnricher(
                materials_project_api_key="private-user-mp-key",
            ).enrich(hypothesis)

        self.assertEqual(captured, {"formula": "TiO2", "apiKey": "private-user-mp-key"})
        self.assertIn("Materials Project", output)
        self.assertNotIn("private-user-mp-key", output)

    def test_science125_input_assembler_uses_authoritative_context_and_hash_bound_pdf_pages(self) -> None:
        from app.services.document_excerpts import extract_document_page_excerpt

        user_uploads = self.service.uploads_dir / "1"
        user_uploads.mkdir(parents=True, exist_ok=True)
        pdf_path = user_uploads / "reviewed-source.pdf"
        document = fitz.open()
        page = document.new_page()
        page.insert_text((72, 72), "Reviewed page evidence: nanoscale spectroscopy resolves interface transport.")
        document.save(pdf_path)
        document.close()
        document_id = self.create_document_with_chunks([], title="Reviewed source", source_path=str(pdf_path))
        excerpt = extract_document_page_excerpt(
            str(pdf_path),
            pages=[1],
            max_chars=12_000,
            allowed_root=user_uploads,
        )
        context_item = SimpleNamespace(
            id="S125-006",
            headline="How can we measure interface phenomena on the microscopic level?",
            source_context="Authoritative booklet explanation about microscopic interface measurements.",
            context_sha256="a" * 64,
            pdf_page=12,
            booklet_page=10,
        )
        context_index = SimpleNamespace(
            extraction_version="pymupdf-block-anchor-v1",
            items={"S125-006": context_item},
        )
        job = self.store.create(1, "hypothesis_generate", {
            "science125Id": "S125-006",
            "researchQuestion": "tampered client question",
            "supplementalContext": "tampered client context",
            "_science125ContextSha256": context_item.context_sha256,
            "_science125ExtractionVersion": context_index.extraction_version,
            "sourcePageSelections": [{
                "documentId": document_id,
                "pages": [1],
                "pdfSha256": excerpt.pdf_sha256,
                "textSha256": excerpt.text_sha256,
                "maxChars": excerpt.max_chars,
            }],
            "sourceDocIds": [],
            "multimodalRunIds": [],
            "maxIterations": 1,
            "hitlEnabled": False,
            "autoVerify": True,
            "domain": "",
        })
        self.store.update(job.id, status="RUNNING")
        running = self.store.get(job.id)

        with patch("app.services.jobs.load_science125_context_index", return_value=context_index):
            prepared = self.service._prepare_science125_generation_input(running)

        self.assertEqual(prepared["researchQuestion"], context_item.headline)
        self.assertIn(context_item.source_context, prepared["literatureText"])
        self.assertIn("Reviewed page evidence", prepared["literatureText"])
        self.assertLess(
            prepared["literatureText"].index(context_item.source_context),
            prepared["literatureText"].index("tampered client context"),
        )
        self.assertIn("不属于权威题册原文", prepared["literatureText"])
        self.assertEqual(prepared["science125SourceContext"]["contextSha256"], context_item.context_sha256)
        self.assertEqual(prepared["sourcePageSelections"][0]["textSha256"], excerpt.text_sha256)

        from app.services.jobs import SafeJobError

        with pdf_path.open("ab") as handle:
            handle.write(b"\n")
        with (
            patch("app.services.jobs.load_science125_context_index", return_value=context_index),
            self.assertRaises(SafeJobError) as changed_snapshot,
        ):
            self.service._prepare_science125_generation_input(running)
        self.assertEqual(changed_snapshot.exception.code, "SOURCE_PAGE_SNAPSHOT_CHANGED")


    def test_hypothesis_hitl_waits_for_feedback_and_persists_interaction_history(self) -> None:
        document_id = self.create_document_with_chunks(["owned document"], title="Owned source")
        captured_feedback: list = []

        class FakeOrchestrator:
            def __init__(self, **kwargs):
                self.callback = kwargs["user_feedback_callback"]

            def run(self, literature_text, research_question):
                feedback = self.callback(
                    {"paper_title": "Draft", "confidence": 5},
                    {"overall_score": 6, "critical_flaws": ["Needs evidence"]},
                    1,
                    {"session_id": "session-hitl", "mode": "hitl", "interactions": []},
                )
                captured_feedback.append(feedback)
                interactions = [{
                    "round": 1,
                    "user_action": feedback.action,
                    "user_feedback_text": feedback.user_feedback_text,
                    "structured_feedback": feedback.structured_feedback,
                }]
                return SimpleNamespace(
                    raw_json={"paper_title": "Reviewed hypothesis", "confidence": 7, "feasibility": "medium"},
                    final_output="",
                    confidence=7,
                    feasibility="medium",
                    iterations=[{"round": 1}],
                    critique_history=[{"round": 1, "score": 6}],
                    reasoning_chain={},
                    verification_report=None,
                    results_verification={},
                    cross_domain_analogies={},
                    debate_history=[],
                    closed_loop_validation={},
                    executable_validation={},
                    scientific_toolkit={},
                    interaction_history={"session_id": "session-hitl", "mode": "hitl", "interactions": interactions},
                )

        job = self.store.create(1, "hypothesis_generate", {
            "researchQuestion": "Review this hypothesis",
            "sourceDocIds": [document_id],
            "supplementalContext": "",
            "maxIterations": 2,
            "hitlEnabled": True,
            "autoVerify": True,
            "domain": "",
        })
        self.store.update(job.id, status="RUNNING")
        running = self.store.get(job.id)
        outcomes: list[dict] = []
        failures: list[Exception] = []

        def run_job():
            try:
                outcomes.append(self.service._run_hypothesis_generate(running))
            except Exception as exc:
                failures.append(exc)

        with (
            patch("app.services.jobs.agent_framework", return_value=SimpleNamespace(HypothesisOrchestrator=FakeOrchestrator)),
            patch.object(self.service, "_build_hypothesis_evidence", return_value=("rag context", {"status": "ok"}, [])),
            patch.object(self.service, "_create_hypothesis_artifacts", return_value=([], [])),
            patch("app.services.jobs.api_keys.api_key_store.get_key", return_value="test-key"),
        ):
            thread = __import__("threading").Thread(target=run_job)
            thread.start()
            deadline = time.time() + 2
            while time.time() < deadline:
                waiting = self.store.get(job.id)
                if waiting and waiting.status == "WAITING_FOR_FEEDBACK":
                    break
                time.sleep(0.01)
            else:
                self.fail("Hypothesis job did not enter WAITING_FOR_FEEDBACK.")

            self.assertEqual(waiting.feedback_prompt["kind"], "hypothesis_review")
            self.assertNotIn("literatureText", json.dumps(waiting.feedback_prompt))
            resumed = self.service.submit_hypothesis_feedback(1, job.id, {
                "action": "revise",
                "feedbackText": "Add a stronger baseline.",
                "editedHypothesis": {"paperTitle": "Edited draft", "confidence": 6},
                "debateStance": "devil",
                "selectedAttackIndices": [0],
                "structuredFeedback": {"baselineNeed": "required"},
            })
            self.assertEqual(resumed.status, "RUNNING")
            thread.join(timeout=3)

        self.assertFalse(thread.is_alive())
        self.assertEqual(failures, [])
        self.assertEqual(len(outcomes), 1)
        self.assertEqual(captured_feedback[0].action, "revise")
        self.assertEqual(captured_feedback[0].user_edited_hypothesis["paper_title"], "Edited draft")
        self.assertTrue(captured_feedback[0].manual_revision_diff)

        from app.core.legacy import db

        session = db().get_session()
        try:
            feedback_rows = session.query(db().HypothesisFeedback).all()
        finally:
            session.close()
        self.assertEqual(len(feedback_rows), 1)
        self.assertIn("Add a stronger baseline", feedback_rows[0].interaction_json)

    def test_only_one_active_hypothesis_job_is_allowed_per_user(self) -> None:
        from app.services.jobs import ActiveHypothesisJobError

        first_payload = {"researchQuestion": "first", "sourceDocIds": [], "hitlEnabled": True}
        second_payload = {"researchQuestion": "second", "sourceDocIds": [], "hitlEnabled": True}
        with patch.object(self.service._hypothesis_executor, "submit") as submit:
            first = self.service.create(1, "hypothesis_generate", first_payload)
            duplicate = self.service.create(1, "hypothesis_generate", first_payload)
            with self.assertRaises(ActiveHypothesisJobError) as context:
                self.service.create(1, "hypothesis_generate", second_payload)

        self.assertEqual(first.id, duplicate.id)
        self.assertEqual(context.exception.existing_job_id, first.id)
        submit.assert_called_once()

    def test_multimodal_job_keeps_successful_items_when_one_source_fails(self) -> None:
        first_path = self.tmp_path / "first.png"
        second_path = self.tmp_path / "second.png"
        first_path.write_bytes(b"first")
        second_path.write_bytes(b"second")
        first = self.service.science_store.create_multimodal_asset(
            user_id=1,
            managed_path=first_path,
            file_name="first.png",
            mime_type="image/png",
            asset_type="image",
            metadata={},
        )
        second = self.service.science_store.create_multimodal_asset(
            user_id=1,
            managed_path=second_path,
            file_name="second.png",
            mime_type="image/png",
            asset_type="image",
            metadata={},
        )
        job = self.store.create(1, "multimodal_analyze", {
            "sources": [
                {"sourceType": "upload", "assetId": first.id, "sheetNames": []},
                {"sourceType": "upload", "assetId": second.id, "sheetNames": []},
            ],
            "question": "Analyze",
            "useLiteratureContext": False,
        })

        with patch.object(
            self.service,
            "_analyze_multimodal_source",
            side_effect=[
                {
                    "source": {"sourceType": "upload", "assetId": first.id, "fileName": "first.png"},
                    "imageType": "XRD",
                    "analysisMarkdown": "A real analysis",
                    "summary": "A real analysis",
                    "dataPoints": [{"parameter": "peak", "value": 28.5, "unit": "deg"}],
                    "coverage": {},
                    "warnings": [],
                },
                RuntimeError("provider failed"),
            ],
        ):
            result = self.service._run_multimodal_analyze(job)

        self.assertEqual(result["summary"]["succeeded"], 1)
        self.assertEqual(result["summary"]["failed"], 1)
        self.assertNotIn("provider failed", json.dumps(result))
        run = self.service.science_store.get_multimodal_run(user_id=1, run_id=result["runId"])
        self.assertIsNotNone(run)
        self.assertEqual(run.corrected_result["items"][0]["imageType"], "XRD")

    def test_modeling_job_marks_parameter_provenance_and_full_text_coverage(self) -> None:
        text = "Methods section " + ("x" * 14000)
        job = self.store.create(1, "modeling_generate", {
            "source": {"type": "manual", "text": text},
            "mode": "vasp",
            "calcType": "relax",
            "vaspkitTask": "geometry_optimization",
        })

        class LlmModule:
            LLM_ERROR_PREFIX = "__CHALK_LLM_ERROR__:"

            class LLMConfig:
                def __init__(self, api_key=None):
                    self.api_key = api_key

            @staticmethod
            def extract_vasp_parameters(source_text, calc_type, config=None):
                return json.dumps({
                    "calc_type": "relax",
                    "system_name": "Fe2O3",
                    "functional": "PBE",
                    "is_metal": False,
                    "incar": {"ENCUT": 600},
                    "kpoints": {"mode": "Gamma", "mesh": "7 7 7"},
                    "poscar": {"lattice_type": "unknown", "lattice_constants": {}, "basis_atoms": []},
                    "potcar_elements": ["Fe", "O"],
                    "missing_params": [],
                    "notes": "Extracted",
                })

        with (
            patch("app.services.jobs.llm_client", return_value=LlmModule),
            patch("app.services.jobs.api_keys.api_key_store.get_key", return_value="test-key"),
        ):
            result = self.service._run_modeling_generate(job)

        workspace = self.service.science_store.get_modeling_workspace(
            user_id=1,
            workspace_id=result["workspaceId"],
        )
        self.assertEqual(workspace.current_result["incar"]["ENCUT"]["value"], 600)
        self.assertEqual(workspace.current_result["incar"]["ENCUT"]["source"], "manual")
        self.assertEqual(workspace.current_result["incar"]["EDIFF"]["source"], "default")
        self.assertEqual(workspace.current_result["coverage"]["sourceChars"], len(text))
        self.assertEqual(workspace.current_result["coverage"]["includedChars"], len(text))

    def test_modeling_job_rejects_nonexistent_potcar_elements(self) -> None:
        from app.services.jobs import SafeJobError

        job = self.store.create(1, "modeling_generate", {
            "source": {"type": "manual", "text": "A hypothetical material"},
            "mode": "vasp",
            "calcType": "relax",
            "vaspkitTask": "",
        })

        class LlmModule:
            LLM_ERROR_PREFIX = "__CHALK_LLM_ERROR__:"

            class LLMConfig:
                def __init__(self, api_key=None):
                    self.api_key = api_key

            @staticmethod
            def extract_vasp_parameters(source_text, calc_type, config=None):
                return json.dumps({
                    "calc_type": "relax",
                    "system_name": "invalid",
                    "functional": "PBE",
                    "is_metal": False,
                    "incar": {},
                    "kpoints": {"mode": "Gamma", "mesh": "6 6 6"},
                    "poscar": {"lattice_type": "unknown", "lattice_constants": {}, "basis_atoms": []},
                    "potcar_elements": ["Xx"],
                    "missing_params": [],
                    "notes": "",
                })

        with (
            patch("app.services.jobs.llm_client", return_value=LlmModule),
            patch("app.services.jobs.api_keys.api_key_store.get_key", return_value="test-key"),
            self.assertRaises(SafeJobError) as raised,
        ):
            self.service._run_modeling_generate(job)
        self.assertEqual(raised.exception.code, "INVALID_MODELING_OUTPUT")

    def test_modeling_export_omits_poscar_without_validated_structure(self) -> None:
        workspace = self.service.science_store.create_modeling_workspace(
            user_id=1,
            source={"type": "manual"},
            mode="vasp",
            original_result={
                "files": {
                    "incar": "ENCUT = 520\n",
                    "kpoints": "K-points\n",
                    "potcarGuide": "POTCAR hints\n",
                },
                "riskNotices": ["No validated structure is available."],
            },
            job_id="generate-job",
        )
        job = self.store.create(1, "modeling_export", {
            "workspaceId": workspace.id,
            "expectedRevision": 1,
        })

        result = self.service._run_modeling_export(job)
        artifact = self.service.science_store.get_modeling_artifact(
            user_id=1,
            workspace_id=workspace.id,
            artifact_id=result["artifact"]["id"],
        )
        with zipfile.ZipFile(artifact.path) as archive:
            names = set(archive.namelist())
            manifest = json.loads(archive.read("manifest.json"))

        self.assertIn("INCAR", names)
        self.assertNotIn("POSCAR", names)
        self.assertFalse(manifest["includesValidatedPoscar"])

    def test_hypothesis_modeling_imports_validated_structure_and_workflow_dry_run(self) -> None:
        from app.core.legacy import db

        structure_path = self.service.hypothesis_assets_dir / "1" / "candidate.vasp"
        structure_path.parent.mkdir(parents=True, exist_ok=True)
        structure_path.write_text(
            """Silicon
1.0
5.43 0 0
0 5.43 0
0 0 5.43
Si
1
Direct
0 0 0
""",
            encoding="utf-8",
        )
        package = {
            "structures": [{
                "index": 1,
                "name": "silicon",
                "format": "poscar",
                "path": str(structure_path),
                "parse_status": "ok",
            }],
            "vasp_recommendations": [{
                "target": "Si",
                "incar_relax": {"ENCUT": 520},
                "potcar_hints": {"Si": "Si"},
            }],
            "atomate2_dryrun": {
                "status": "ready",
                "dry_run_only": True,
                "execution_policy": "not_submitted",
                "workflows": [{"name": "relax"}],
            },
            "risk_notices": ["Review before execution."],
        }
        session = db().get_session()
        try:
            hypothesis = db().Hypothesis(
                user_id=1,
                title="Silicon workflow",
                research_question="Relax silicon",
                result_json=json.dumps({
                    "paper_title": "Silicon workflow",
                    "_hypothesis_workflow_package": package,
                }),
                confidence=8,
                feasibility="high",
                iteration_count=1,
                related_doc_ids="[]",
                extra_json="{}",
                status="generated",
            )
            session.add(hypothesis)
            session.commit()
            hypothesis_id = hypothesis.id
        finally:
            session.close()

        generate_job = self.store.create(1, "modeling_generate", {
            "source": {"type": "hypothesis", "hypothesisId": hypothesis_id},
            "mode": "vasp",
            "calcType": "relax",
            "vaspkitTask": "",
        })
        generated = self.service._run_modeling_generate(generate_job)
        workspace = self.service.science_store.get_modeling_workspace(
            user_id=1,
            workspace_id=generated["workspaceId"],
        )
        structures = self.service.science_store.list_modeling_structures(
            user_id=1,
            workspace_id=workspace.id,
        )

        self.assertEqual(len(structures), 1)
        self.assertEqual(workspace.active_structure_id, structures[0].id)
        self.assertNotIn(str(self.tmp_path), json.dumps(workspace.source))

        export_job = self.store.create(1, "modeling_export", {
            "workspaceId": workspace.id,
            "expectedRevision": workspace.revision,
        })
        exported = self.service._run_modeling_export(export_job)
        artifact = self.service.science_store.get_modeling_artifact(
            user_id=1,
            workspace_id=workspace.id,
            artifact_id=exported["artifact"]["id"],
        )
        with zipfile.ZipFile(artifact.path) as archive:
            names = set(archive.namelist())

        self.assertIn("POSCAR", names)
        self.assertIn("ATOMATE2_DRY_RUN.json", names)

    def test_hypothesis_modeling_rejects_invalid_workflow_parameters(self) -> None:
        from app.services.jobs import SafeJobError

        with self.assertRaises(SafeJobError) as raised:
            self.service._modeling_result_from_workflow({
                "workflow": {
                    "vaspRecommendations": [{
                        "target": "invalid",
                        "incarRelax": {"bad-key": {"nested": "value"}},
                        "potcarHints": {"Xx": "../../POTCAR"},
                    }],
                },
            })
        self.assertEqual(raised.exception.code, "INVALID_MODELING_OUTPUT")


if __name__ == "__main__":
    unittest.main()
