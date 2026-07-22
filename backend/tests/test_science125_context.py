from __future__ import annotations

import hashlib
import json
import sys
import unittest
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
# ``backend`` is the package root and its parent is the repository root.
PROJECT_ROOT = BACKEND_DIR.parent
sys.path.insert(0, str(BACKEND_DIR))


EXPECTED_SOURCE_PDF_SHA256 = (
    "4bda50e8e3c90f8968f1bfd72ded4d9587ae80cd40ba66656a12c93abcf8e576"
)


def _context_index_path(root: Path) -> Path:
    return root / "web-data" / "science125" / "science125-context-v1.json"


def _write_index(root: Path, *, source_sha256: str = EXPECTED_SOURCE_PDF_SHA256) -> Path:
    manifest = json.loads(
        (PROJECT_ROOT / "benchmarks" / "science125" / "science125-v1.json").read_text(
            encoding="utf-8"
        )
    )
    items = []
    for item in manifest["questions"]:
        context = (
            f"{item['question']} This is the complete booklet context for {item['id']}."
        )
        canonical = " ".join(context.split())
        items.append(
            {
                "id": item["id"],
                "headline": item["question"],
                "sourceContext": context,
                "contextSha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
                "pdfPage": item["pdfPage"],
                "bookletPage": item["bookletPage"],
            }
        )
    path = _context_index_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "indexVersion": "science125-context-v1",
                "manifestVersion": "science125-v1",
                "sourcePdfSha256": source_sha256,
                "sourcePdfFilename": "sjtu-booklet.pdf",
                "extractionVersion": "pymupdf-block-anchor-v1",
                "items": items,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


class Science125ContextStoreTestCase(unittest.TestCase):
    def test_loader_returns_non_empty_context_and_rejects_wrong_hash(self) -> None:
        from app.services.science125_context import (
            Science125ContextError,
            load_science125_context_index,
        )

        with self.subTest("valid index"):
            index = _write_index(Path(self._tmp.name))
            loaded = load_science125_context_index(index)
            self.assertEqual(loaded.source_pdf_sha256, EXPECTED_SOURCE_PDF_SHA256)
            self.assertTrue(loaded.items["S125-001"].source_context.strip())
            self.assertEqual(loaded.items["S125-001"].pdf_page, 7)

        with self.subTest("wrong hash"):
            wrong = _write_index(Path(self._tmp.name), source_sha256="0" * 64)
            with self.assertRaises(Science125ContextError):
                load_science125_context_index(wrong)

    def setUp(self) -> None:
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def test_loader_rejects_missing_and_malformed_indexes_without_path_leak(self) -> None:
        from app.services.science125_context import (
            Science125ContextError,
            load_science125_context_index,
        )

        missing = Path(self._tmp.name) / "missing.json"
        with self.assertRaises(Science125ContextError) as raised:
            load_science125_context_index(missing)
        self.assertNotIn(str(missing), str(raised.exception))

        malformed = Path(self._tmp.name) / "malformed.json"
        malformed.write_text("not-json", encoding="utf-8")
        with self.assertRaises(Science125ContextError):
            load_science125_context_index(malformed)


class Science125ContextBuilderTestCase(unittest.TestCase):
    def setUp(self) -> None:
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def test_source_pdf_hash_is_verified_before_extraction(self) -> None:
        from scripts.build_science125_context import SourcePdfMismatch, verify_source_pdf

        source = Path(self._tmp.name) / "wrong.pdf"
        source.write_bytes(b"%PDF-1.4\nnot the authoritative booklet")

        with self.assertRaises(SourcePdfMismatch):
            verify_source_pdf(source)

    def test_layout_blocks_keep_same_page_question_contexts_separate(self) -> None:
        import fitz

        from scripts.build_science125_context import extract_page_contexts

        document = fitz.open()
        page = document.new_page(width=600, height=800)
        page.insert_textbox(
            fitz.Rect(40, 70, 270, 110),
            "What is the first question?",
            fontsize=12,
        )
        page.insert_textbox(
            fitz.Rect(40, 115, 270, 250),
            "The first complete description mentions alpha evidence.",
            fontsize=10,
        )
        page.insert_textbox(
            fitz.Rect(320, 70, 550, 110),
            "What is the second question?",
            fontsize=12,
        )
        page.insert_textbox(
            fitz.Rect(320, 115, 550, 250),
            "The second complete description mentions beta evidence.",
            fontsize=10,
        )

        contexts = extract_page_contexts(
            page,
            [
                {"id": "S125-001", "question": "What is the first question?"},
                {"id": "S125-002", "question": "What is the second question?"},
            ],
        )

        self.assertIn("alpha evidence", contexts["S125-001"])
        self.assertNotIn("beta evidence", contexts["S125-001"])
        self.assertIn("beta evidence", contexts["S125-002"])
        self.assertNotIn("alpha evidence", contexts["S125-002"])
        document.close()


class Science125ContextApiTestCase(unittest.TestCase):
    def setUp(self) -> None:
        import os
        import tempfile

        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.tmp_path = Path(self.tmp.name)
        os.environ["CHALK_WEB_DATA_DIR"] = str(self.tmp_path / "web-data")
        os.environ["CHALK_SESSION_SECRET"] = "test-secret"

        from app.core.config import get_settings
        from app.core.legacy import db
        from app.core.security import SessionStore
        from app.services.api_keys import ApiKeyStore
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker

        get_settings.cache_clear()
        database = db()
        self.engine = create_engine(f"sqlite:///{self.tmp_path / 'test.db'}", future=True)
        database.ENGINE = self.engine
        database.SessionLocal = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)
        database.DB_PATH = str(self.tmp_path / "test.db")
        database.Base.metadata.create_all(self.engine)

        import app.core.dependencies as dependencies
        import app.services.api_keys as api_keys_module

        dependencies.session_store = SessionStore(self.tmp_path / "sessions.json")
        api_keys_module.api_key_store = ApiKeyStore(self.tmp_path / "api_keys.json")

        from fastapi.testclient import TestClient
        from app.main import create_app

        self.client = TestClient(create_app())
        self.addCleanup(self._dispose)

    def _dispose(self) -> None:
        from app.services.jobs import job_service

        job_service.dispose_stores()
        self.engine.dispose()

    def _register(self, username: str = "alice") -> None:
        response = self.client.post(
            "/api/auth/register",
            json={"username": username, "password": "secret-pass"},
        )
        self.assertEqual(response.status_code, 201, response.text)

    def test_detail_requires_auth_and_returns_context_without_private_paths(self) -> None:
        _write_index(self.tmp_path)
        unauthenticated = self.client.get("/api/science-125/questions/S125-001")
        self.assertEqual(unauthenticated.status_code, 401, unauthenticated.text)

        self._register()
        response = self.client.get("/api/science-125/questions/S125-001")
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["id"], "S125-001")
        self.assertEqual(payload["headline"], "What makes prime numbers so special?")
        self.assertTrue(payload["sourceContext"].strip())
        self.assertEqual(payload["availability"], "available")
        self.assertEqual(payload["extractionVersion"], "pymupdf-block-anchor-v1")
        self.assertEqual(payload["pdfPage"], 7)
        self.assertEqual(payload["bookletPage"], 5)
        self.assertNotIn("sourcePdfPath", payload)
        self.assertNotIn("sjtu-booklet.pdf", response.text)
        self.assertNotIn(str(self.tmp_path), response.text)

    def test_missing_or_invalid_runtime_index_returns_safe_service_error(self) -> None:
        self._register()
        response = self.client.get("/api/science-125/questions/S125-001")
        self.assertEqual(response.status_code, 503, response.text)
        self.assertEqual(response.json()["error"]["code"], "SCIENCE125_CONTEXT_UNAVAILABLE")

        _write_index(self.tmp_path, source_sha256="f" * 64)
        response = self.client.get("/api/science-125/questions/S125-001")
        self.assertEqual(response.status_code, 503, response.text)
        self.assertNotIn(str(self.tmp_path), response.text)

    def test_unknown_question_is_not_found(self) -> None:
        _write_index(self.tmp_path)
        self._register()
        response = self.client.get("/api/science-125/questions/S125-999")
        self.assertEqual(response.status_code, 404, response.text)


if __name__ == "__main__":
    unittest.main()
