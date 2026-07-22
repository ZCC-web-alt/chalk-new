from __future__ import annotations

import hashlib
import os
import sys
import tempfile
import unittest
from pathlib import Path

import fitz
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


BACKEND_DIR = Path(__file__).resolve().parents[1]
ROOT_DIR = BACKEND_DIR.parents[1]
sys.path.insert(0, str(BACKEND_DIR))
sys.path.insert(0, str(ROOT_DIR / "src"))
sys.path.insert(0, str(ROOT_DIR))


class DocumentPageExcerptApiTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.tmp_path = Path(self.tmp.name)
        os.environ["CHALK_WEB_DATA_DIR"] = str(self.tmp_path / "web-data")
        os.environ["CHALK_SESSION_SECRET"] = "test-secret"

        from app.core.config import get_settings
        from app.core.legacy import db
        from app.core.security import SessionStore
        from app.services.api_keys import ApiKeyStore

        get_settings.cache_clear()
        database = db()
        self.engine = create_engine(
            f"sqlite:///{self.tmp_path / 'legacy.db'}", future=True
        )
        testing_session = sessionmaker(
            bind=self.engine, autoflush=False, autocommit=False
        )
        database.ENGINE = self.engine
        database.SessionLocal = testing_session
        database.DB_PATH = str(self.tmp_path / "legacy.db")
        database.Base.metadata.create_all(self.engine)

        import app.core.dependencies as dependencies
        import app.services.api_keys as api_keys_module

        dependencies.session_store = SessionStore(self.tmp_path / "sessions.json")
        api_keys_module.api_key_store = ApiKeyStore(self.tmp_path / "api_keys.json")

        from app.main import create_app

        self.client = TestClient(create_app())

    def tearDown(self) -> None:
        from app.services.jobs import job_service

        job_service.dispose_stores()
        self.engine.dispose()

    def register(self, username: str = "alice") -> None:
        response = self.client.post(
            "/api/auth/register",
            json={"username": username, "password": "secret-pass"},
        )
        self.assertEqual(response.status_code, 201, response.text)

    def create_pdf_document(
        self,
        *,
        user_id: int = 1,
        source_type: str = "pdf",
        with_text: bool = True,
    ) -> tuple[int, Path]:
        from app.core.config import get_settings
        from app.core.legacy import db

        user_uploads = get_settings().uploads_dir / str(user_id)
        user_uploads.mkdir(parents=True, exist_ok=True)
        pdf_path = user_uploads / f"{user_id}-{source_type}.pdf"
        pdf = fitz.open()
        for page_number in range(1, 4):
            page = pdf.new_page()
            if with_text:
                page.insert_text(
                    (72, 72),
                    f"Page {page_number} source text. " + ("x" * 120),
                )
        pdf.save(pdf_path)
        pdf.close()

        session = db().get_session()
        try:
            document = db().create_document(
                session,
                user_id,
                "Owned source",
                source_type,
                str(pdf_path),
            )
            return document.id, pdf_path
        finally:
            session.close()

    def test_owned_pdf_excerpt_is_bounded_and_has_hash_provenance(self) -> None:
        self.register()
        document_id, pdf_path = self.create_pdf_document()

        response = self.client.post(
            f"/api/documents/{document_id}/page-excerpts",
            json={"pages": [2, 1], "maxChars": 36},
        )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(
            set(payload), {"documentId", "title", "pages", "text", "hash", "provenance"}
        )
        self.assertEqual(payload["documentId"], document_id)
        self.assertEqual(payload["title"], "Owned source")
        self.assertEqual(payload["pages"], [1, 2])
        self.assertLessEqual(len(payload["text"]), 36)
        self.assertEqual(
            payload["hash"], hashlib.sha256(payload["text"].encode("utf-8")).hexdigest()
        )
        self.assertEqual(payload["provenance"]["sourceType"], "pdf")
        self.assertEqual(payload["provenance"]["extractor"], "PyMuPDF")
        self.assertEqual(payload["provenance"]["pageCount"], 3)
        self.assertTrue(payload["provenance"]["truncated"])
        self.assertEqual(
            payload["provenance"]["pdfSha256"],
            hashlib.sha256(pdf_path.read_bytes()).hexdigest(),
        )
        self.assertNotIn(str(pdf_path), response.text)

        range_response = self.client.post(
            f"/api/documents/{document_id}/page-excerpts",
            json={"pageStart": 2, "pageEnd": 3, "maxChars": 500},
        )
        self.assertEqual(range_response.status_code, 200, range_response.text)
        self.assertEqual(range_response.json()["pages"], [2, 3])

    def test_excerpt_rejects_bad_page_ranges_and_pages_without_text(self) -> None:
        self.register()
        document_id, _ = self.create_pdf_document()

        for body in (
            {"pages": []},
            {"pages": [0]},
            {"pages": [1, 1]},
            {"pages": [4]},
            {"pages": [1], "maxChars": 0},
            {"pageStart": 2, "pageEnd": 1},
        ):
            with self.subTest(body=body):
                response = self.client.post(
                    f"/api/documents/{document_id}/page-excerpts", json=body
                )
                self.assertEqual(response.status_code, 422, response.text)

        empty_id, _ = self.create_pdf_document(with_text=False)
        empty_response = self.client.post(
            f"/api/documents/{empty_id}/page-excerpts",
            json={"pages": [1]},
        )
        self.assertEqual(empty_response.status_code, 422, empty_response.text)
        self.assertEqual(
            empty_response.json()["error"]["code"], "EXCERPT_TEXT_UNAVAILABLE"
        )

    def test_excerpt_requires_a_pdf_source(self) -> None:
        self.register()
        document_id, _ = self.create_pdf_document(source_type="web")

        response = self.client.post(
            f"/api/documents/{document_id}/page-excerpts",
            json={"pages": [1]},
        )

        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(response.json()["error"]["code"], "EXCERPT_SOURCE_UNAVAILABLE")

        from app.core.legacy import db

        invalid_path = self.tmp_path / "invalid.pdf"
        invalid_path.write_bytes(b"not a PDF")
        session = db().get_session()
        try:
            invalid_document = db().create_document(
                session,
                1,
                "Invalid source",
                "pdf",
                str(invalid_path),
            )
            missing_document = db().create_document(
                session,
                1,
                "Missing source",
                "pdf",
                None,
            )
            invalid_id = invalid_document.id
            missing_id = missing_document.id
        finally:
            session.close()

        for unavailable_id in (invalid_id, missing_id):
            with self.subTest(document_id=unavailable_id):
                unavailable = self.client.post(
                    f"/api/documents/{unavailable_id}/page-excerpts",
                    json={"pages": [1]},
                )
                self.assertEqual(unavailable.status_code, 422, unavailable.text)
                self.assertEqual(
                    unavailable.json()["error"]["code"],
                    "EXCERPT_SOURCE_UNAVAILABLE",
                )

        outside_id, managed_path = self.create_pdf_document()
        outside_path = self.tmp_path / "outside-managed-storage.pdf"
        outside_path.write_bytes(managed_path.read_bytes())
        session = db().get_session()
        try:
            outside_document = session.get(db().Document, outside_id)
            outside_document.source_path = str(outside_path)
            session.commit()
        finally:
            session.close()
        outside = self.client.post(
            f"/api/documents/{outside_id}/page-excerpts",
            json={"pages": [1]},
        )
        self.assertEqual(outside.status_code, 422, outside.text)
        self.assertEqual(outside.json()["error"]["code"], "EXCERPT_SOURCE_UNAVAILABLE")

    def test_excerpt_is_scoped_to_the_document_owner(self) -> None:
        self.register("alice")
        document_id, _ = self.create_pdf_document()
        self.client.post("/api/auth/logout")
        self.register("bob")

        response = self.client.post(
            f"/api/documents/{document_id}/page-excerpts",
            json={"pages": [1]},
        )

        self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(response.json()["error"]["code"], "NOT_FOUND")

    def test_science125_rejects_client_supplied_question_or_context_before_generation(self) -> None:
        self.register()
        document_id, pdf_path = self.create_pdf_document()
        excerpt = self.client.post(
            f"/api/documents/{document_id}/page-excerpts",
            json={"pages": [1], "maxChars": 12000},
        )
        self.assertEqual(excerpt.status_code, 200, excerpt.text)
        selected = excerpt.json()
        payload = {
            "researchQuestion": "How can we measure interface phenomena on the microscopic level?",
            "science125Id": "S125-006",
            "sourcePageSelections": [{
                "documentId": document_id,
                "pages": selected["pages"],
                "pdfSha256": selected["provenance"]["pdfSha256"],
                "textSha256": selected["hash"],
                "maxChars": selected["provenance"]["maxChars"],
            }],
        }
        accepted_snapshot = self.client.post("/api/jobs", json={"type": "hypothesis_generate", "payload": payload})
        self.assertEqual(accepted_snapshot.status_code, 422, accepted_snapshot.text)
        self.assertEqual(accepted_snapshot.json()["error"]["code"], "VALIDATION_ERROR")

        with pdf_path.open("ab") as handle:
            handle.write(b"\n")
        changed_snapshot = self.client.post("/api/jobs", json={"type": "hypothesis_generate", "payload": payload})
        self.assertEqual(changed_snapshot.status_code, 422, changed_snapshot.text)
        self.assertEqual(changed_snapshot.json()["error"]["code"], "VALIDATION_ERROR")


if __name__ == "__main__":
    unittest.main()
