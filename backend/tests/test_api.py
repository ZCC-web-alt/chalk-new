from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import json
import base64
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


BACKEND_DIR = Path(__file__).resolve().parents[1]
ROOT_DIR = BACKEND_DIR.parents[1]
sys.path.insert(0, str(BACKEND_DIR))
sys.path.insert(0, str(ROOT_DIR / "src"))
sys.path.insert(0, str(ROOT_DIR))


class ApiTestCase(unittest.TestCase):
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
        self.engine = create_engine(f"sqlite:///{self.tmp_path / 'test.db'}", future=True)
        testing_session = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)
        database.ENGINE = self.engine
        database.SessionLocal = testing_session
        database.DB_PATH = str(self.tmp_path / "test.db")
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

    def register(self, username: str = "alice", password: str = "secret-pass"):
        response = self.client.post("/api/auth/register", json={"username": username, "password": password})
        self.assertEqual(response.status_code, 201, response.text)
        return response

    def test_auth_flow_and_protected_me(self) -> None:
        unauthenticated = self.client.get("/api/auth/me")
        self.assertEqual(unauthenticated.status_code, 401)
        self.assertEqual(unauthenticated.json()["error"]["code"], "UNAUTHENTICATED")

        created = self.register()
        self.assertEqual(created.json()["user"]["username"], "alice")

        current = self.client.get("/api/auth/me")
        self.assertEqual(current.status_code, 200, current.text)
        self.assertEqual(current.json()["user"]["username"], "alice")

        logged_out = self.client.post("/api/auth/logout")
        self.assertEqual(logged_out.status_code, 200, logged_out.text)
        after_logout = self.client.get("/api/auth/me")
        self.assertEqual(after_logout.status_code, 401)

        login = self.client.post("/api/auth/login", json={"username": "alice", "password": "secret-pass"})
        self.assertEqual(login.status_code, 200, login.text)

    def test_science125_questions_require_auth_and_expose_only_public_fields(self) -> None:
        unauthenticated = self.client.get("/api/science-125/questions")
        self.assertEqual(unauthenticated.status_code, 401, unauthenticated.text)
        self.assertEqual(
            unauthenticated.json()["error"]["code"],
            "UNAUTHENTICATED",
        )

        self.register("alice")
        response = self.client.get("/api/science-125/questions")

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(set(payload), {"manifestVersion", "routingVersion", "data"})
        self.assertEqual(payload["manifestVersion"], "science125-v1")
        self.assertEqual(payload["routingVersion"], "science125-routing-v1")
        self.assertEqual(len(payload["data"]), 125)
        expected_question_fields = {
                "id",
                "question",
                "sourceDomain",
                "benchmarkDomain",
                "pdfPage",
                "bookletPage",
                "primarySubdomain",
                "crossDomainTags",
                "methodProfile",
                "promptProfile",
                "retrievalProfile",
                "classificationReviewStatus",
        }
        self.assertTrue(
            all(set(question) == expected_question_fields for question in payload["data"])
        )
        self.assertEqual(
            payload["data"][0],
            {
                "id": "S125-001",
                "question": "What makes prime numbers so special?",
                "sourceDomain": "Mathematical Sciences",
                "benchmarkDomain": "Mathematical Sciences",
                "pdfPage": 7,
                "bookletPage": 5,
                "primarySubdomain": "math.number_theory",
                "crossDomainTags": [],
                "methodProfile": {"primary": "proof", "secondary": ["computational"]},
                "promptProfile": "s125.mathematics.v1",
                "retrievalProfile": "retrieval.mathematics.v1",
                "classificationReviewStatus": "reviewed",
            },
        )
        self.assertEqual(payload["data"][-1]["id"], "S125-125")
        self.assertNotIn("Sha256", response.text)
        self.assertNotIn("sourcePdf", response.text)
        self.assertNotIn("sjtu-booklet.pdf", response.text)

        self.client.post("/api/auth/logout")
        self.register("bob")
        bob_response = self.client.get("/api/science-125/questions")
        self.assertEqual(bob_response.status_code, 200, bob_response.text)
        self.assertEqual(bob_response.json(), payload)

    def test_science125_question_profile_exposes_routing_and_safe_provider_readiness(self) -> None:
        self.register("alice")

        response = self.client.get("/api/science-125/questions/S125-006/profile")

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["questionId"], "S125-006")
        self.assertEqual(payload["routingVersion"], "science125-routing-v1")
        self.assertEqual(payload["primarySubdomain"], "chem.interface")
        self.assertEqual(payload["retrievalProfile"], "retrieval.chem.interface.v1")
        self.assertFalse(payload["ready"])
        self.assertTrue(payload["missingConfigurationCodes"])
        self.assertTrue(payload["providers"])
        self.assertTrue(payload["providerReadiness"])
        self.assertTrue(any(item["providerId"] == "semantic_scholar" and item["isRequired"] for item in payload["providers"]))
        self.assertNotIn("DASHSCOPE_API_KEY", response.text)
        self.assertNotIn("SCIENCE125_", response.text)
        self.assertNotIn("Authorization", response.text)
        self.assertNotIn("api_keys.json", response.text)

        unknown = self.client.get("/api/science-125/questions/S125-999/profile")
        self.assertEqual(unknown.status_code, 404, unknown.text)

    def test_science125_questions_are_read_only(self) -> None:
        self.register()

        response = self.client.post("/api/science-125/questions", json={})

        self.assertEqual(response.status_code, 405, response.text)

    def test_science125_catalog_failure_returns_safe_service_error(self) -> None:
        from app.services.science125_catalog import Science125CatalogError

        self.register()
        private_path = str(self.tmp_path / "private" / "science125-v1.json")
        with patch(
            "app.api.routers.science125.get_science125_catalog",
            side_effect=Science125CatalogError(),
        ):
            response = self.client.get("/api/science-125/questions")

        self.assertEqual(response.status_code, 503, response.text)
        self.assertEqual(
            response.json()["error"]["code"],
            "SCIENCE125_CATALOG_UNAVAILABLE",
        )
        self.assertNotIn(private_path, response.text)

    def test_science125_jobs_are_bound_to_the_authoritative_question(self) -> None:
        from app.services.jobs import job_service

        self.register()
        question = "How can we measure interface phenomena on the microscopic level?"
        generic = job_service.store.create(1, "literature_search", {"queryText": "unrelated chemistry"})
        matching = job_service.store.create(1, "literature_search", {
            "queryText": question,
            "science125Id": "S125-006",
        })

        filtered = self.client.get("/api/jobs?type=literature_search&science125Id=S125-006&pageSize=20")
        self.assertEqual(filtered.status_code, 200, filtered.text)
        self.assertEqual([job["id"] for job in filtered.json()["data"]], [matching.id])
        self.assertEqual(filtered.json()["data"][0]["resource"]["science125Id"], "S125-006")

        unbound = self.client.get("/api/jobs?type=literature_search&science125Scope=unbound&pageSize=20")
        self.assertEqual(unbound.status_code, 200, unbound.text)
        self.assertEqual([job["id"] for job in unbound.json()["data"]], [generic.id])

        with patch.object(job_service, "create", return_value=matching) as create:
            created = self.client.post("/api/jobs", json={
                "type": "literature_search",
                "payload": {"queryText": question, "science125Id": "S125-006"},
            })
        self.assertEqual(created.status_code, 202, created.text)
        self.assertEqual(created.json()["resource"]["science125Id"], "S125-006")
        self.assertEqual(create.call_args.args[2]["science125Id"], "S125-006")

        mismatch = self.client.post("/api/jobs", json={
            "type": "hypothesis_generate",
            "payload": {"researchQuestion": "a different question", "science125Id": "S125-006"},
        })
        self.assertEqual(mismatch.status_code, 422, mismatch.text)
        self.assertEqual(mismatch.json()["error"]["code"], "SCIENCE125_QUESTION_MISMATCH")

        with patch.object(job_service, "create", side_effect=AssertionError("Unreviewed Science 125 input must not create a job")):
            deferred = self.client.post("/api/jobs", json={
                "type": "hypothesis_generate",
                "payload": {"researchQuestion": question, "science125Id": "S125-006"},
            })
        self.assertEqual(deferred.status_code, 422, deferred.text)
        self.assertEqual(deferred.json()["error"]["code"], "VALIDATION_ERROR")

    def test_session_store_remains_valid_under_concurrent_authentication(self) -> None:
        from app.core.security import SessionStore

        path = self.tmp_path / "concurrent-sessions.json"
        store = SessionStore(path)
        tokens = []
        with ThreadPoolExecutor(max_workers=8) as executor:
            tokens = list(executor.map(store.create, range(1, 33)))
        with ThreadPoolExecutor(max_workers=16) as executor:
            resolved = list(executor.map(store.get_user_id, tokens * 4))

        self.assertEqual(resolved, list(range(1, 33)) * 4)
        parsed = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(len(parsed), 32)

    def test_api_responses_include_security_headers(self) -> None:
        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")
        self.assertEqual(response.headers["x-frame-options"], "DENY")
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertNotIn("legacyRoot", response.json())

    def test_validation_errors_do_not_echo_sensitive_inputs(self) -> None:
        self.register()
        secret = "sensitive-key-" + ("x" * 500)

        response = self.client.put(
            "/api/settings/api-keys",
            json={"provider": "dashscope", "apiKey": secret},
        )

        self.assertEqual(response.status_code, 422, response.text)
        self.assertNotIn(secret, response.text)

    def test_api_key_status_supports_literature_providers_without_returning_values(self) -> None:
        self.register()
        for provider, value in [
            ("dashscope", "sk-test"),
            ("semantic_scholar", "semantic-test"),
            ("ncbi", "ncbi-test"),
            ("crossref_mailto", "researcher@example.com"),
        ]:
            response = self.client.put(
                "/api/settings/api-keys",
                json={"provider": provider, "apiKey": value},
            )
            self.assertEqual(response.status_code, 200, response.text)

        status_response = self.client.get("/api/settings/api-keys")
        self.assertEqual(status_response.status_code, 200, status_response.text)
        configured = status_response.json()["configured"]
        self.assertEqual(configured, {
            "dashscope": True,
            "semantic_scholar": True,
            "ncbi": True,
            "crossref_mailto": True,
        })
        self.assertNotIn("sk-test", status_response.text)
        self.assertNotIn("semantic-test", status_response.text)

        invalid = self.client.put(
            "/api/settings/api-keys",
            json={"provider": "arbitrary", "apiKey": "secret"},
        )
        self.assertEqual(invalid.status_code, 422, invalid.text)

    def test_production_disables_plaintext_api_key_storage(self) -> None:
        from app.services.api_keys import ApiKeyStore

        self.register()
        production = SimpleNamespace(is_production=True)
        with patch("app.api.routers.settings.get_settings", return_value=production):
            response = self.client.put(
                "/api/settings/api-keys",
                json={"provider": "dashscope", "apiKey": "must-not-be-written"},
            )

        self.assertEqual(response.status_code, 403, response.text)
        self.assertEqual(response.json()["error"]["code"], "CREDENTIAL_STORAGE_DISABLED")

        path = self.tmp_path / "production-api-keys.json"
        store = ApiKeyStore(path)
        store.set_key(1, "dashscope", "development-key")
        with patch("app.services.api_keys.get_settings", return_value=production):
            with self.assertRaises(RuntimeError):
                store.set_key(1, "dashscope", "must-not-be-written")
            self.assertIsNone(store.get_key(1, "dashscope"))
            self.assertTrue(all(not value for value in store.configured(1).values()))

        persisted = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(persisted["1"]["dashscope"], "development-key")

    def test_duplicate_registration_returns_conflict(self) -> None:
        self.register()
        duplicate = self.client.post("/api/auth/register", json={"username": "alice", "password": "secret-pass"})
        self.assertEqual(duplicate.status_code, 409)
        self.assertEqual(duplicate.json()["error"]["code"], "USERNAME_TAKEN")

    def test_glossary_crud_is_scoped_to_current_user(self) -> None:
        self.register("alice")
        created = self.client.post(
            "/api/glossary",
            json={"enTerm": "Faradaic Efficiency", "zhTerm": "法拉第效率", "note": "FE"},
        )
        self.assertEqual(created.status_code, 201, created.text)
        term_id = created.json()["id"]

        listed = self.client.get("/api/glossary")
        self.assertEqual(listed.status_code, 200, listed.text)
        self.assertEqual(listed.json()["pagination"]["totalItems"], 1)

        self.client.post("/api/auth/logout")
        self.register("bob")
        bob_list = self.client.get("/api/glossary")
        self.assertEqual(bob_list.status_code, 200, bob_list.text)
        self.assertEqual(bob_list.json()["pagination"]["totalItems"], 0)

        forbidden_delete = self.client.delete(f"/api/glossary/{term_id}")
        self.assertEqual(forbidden_delete.status_code, 404)

    def test_lab_record_crud(self) -> None:
        self.register()
        created = self.client.post("/api/lab-records", json={"title": "CO2RR test", "content": "0.1 M KHCO3"})
        self.assertEqual(created.status_code, 201, created.text)
        record_id = created.json()["id"]

        updated = self.client.patch(f"/api/lab-records/{record_id}", json={"content": "updated"})
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertEqual(updated.json()["content"], "updated")

        listed = self.client.get("/api/lab-records")
        self.assertEqual(listed.json()["pagination"]["totalItems"], 1)

        deleted = self.client.delete(f"/api/lab-records/{record_id}")
        self.assertEqual(deleted.status_code, 204, deleted.text)
        missing = self.client.patch(f"/api/lab-records/{record_id}", json={"content": "again"})
        self.assertEqual(missing.status_code, 404)

    def test_lab_suggestion_job_is_scoped_to_current_user(self) -> None:
        self.register("alice")
        created = self.client.post("/api/lab-records", json={"title": "Run", "content": "Observed heat"})
        record_id = created.json()["id"]

        self.client.post("/api/auth/logout")
        self.register("bob")
        denied = self.client.post(
            "/api/jobs",
            json={"type": "lab_suggest", "payload": {"recordId": record_id}},
        )
        self.assertEqual(denied.status_code, 404, denied.text)

    def test_search_references_and_owned_resources_can_be_indexed_as_evidence(self) -> None:
        self.register()
        reference = {
            "id": "search-1",
            "title": "Owned search result",
            "authors": "A. Author",
            "journal": "Journal",
            "year": "2025",
            "doi": "10.1000/owned",
            "abstract": "Measured activity under stated conditions.",
            "sourcePlatform": "Crossref",
            "url": "https://example.test/owned",
            "isOpenAccess": True,
            "accessStatus": "open_access",
            "warning": "",
        }
        indexed = self.client.post(
            "/api/evidence/literature",
            json={"domain": "materials", "references": [reference]},
        )
        self.assertEqual(indexed.status_code, 200, indexed.text)
        self.assertEqual(indexed.json()["count"], 1)

        lab = self.client.post(
            "/api/lab-records",
            json={"title": "Measured run", "content": "Current density was 10 mA cm-2."},
        ).json()
        lab_index = self.client.post(f"/api/evidence/lab-records/{lab['id']}/index")
        self.assertEqual(lab_index.status_code, 200, lab_index.text)

        listed = self.client.get("/api/evidence?pageSize=100")
        self.assertEqual(listed.status_code, 200, listed.text)
        self.assertTrue(any(item.get("title") == "Owned search result" for item in listed.json()["data"]))

        self.client.post("/api/auth/logout")
        self.register("bob")
        denied = self.client.post(f"/api/evidence/lab-records/{lab['id']}/index")
        self.assertEqual(denied.status_code, 404, denied.text)

    def test_evidence_manifest_upload_creates_managed_job_and_rejects_pickle(self) -> None:
        self.register()
        from app.services.jobs import job_service

        queued = job_service.store.create(user_id=1, job_type="evidence_manifest_import", payload={})
        with patch.object(job_service, "create", return_value=queued) as create_job:
            response = self.client.post(
                "/api/evidence/manifests",
                data={"datasetName": "OC22"},
                files={"file": ("manifest.csv", b"material_system,reaction_context\nNiFe,OER\n", "text/csv")},
            )
        self.assertEqual(response.status_code, 202, response.text)
        payload = create_job.call_args.args[2]
        stored_path = Path(payload["storedPath"])
        self.assertTrue(stored_path.is_file())
        self.assertEqual(stored_path.parent, (self.tmp_path / "web-data" / "evidence-imports" / "1").resolve())
        self.assertEqual(payload["datasetName"], "OC22")

        rejected = self.client.post(
            "/api/evidence/manifests",
            files={"file": ("targets.pkl", b"not trusted", "application/octet-stream")},
        )
        self.assertEqual(rejected.status_code, 422, rejected.text)
        self.assertEqual(rejected.json()["error"]["code"], "INVALID_MANIFEST")

    def test_unknown_job_type_is_rejected(self) -> None:
        self.register()
        created = self.client.post("/api/jobs", json={"type": "unsupported_test", "payload": {}})
        self.assertEqual(created.status_code, 422, created.text)
        self.assertEqual(created.json()["error"]["code"], "VALIDATION_ERROR")

    def test_pdf_upload_rejects_non_pdf_content(self) -> None:
        self.register()
        response = self.client.post(
            "/api/documents/import",
            files={"file": ("notes.pdf", b"not a pdf", "application/pdf")},
        )
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(response.json()["error"]["code"], "INVALID_PDF")

    def test_pdf_upload_stores_file_in_user_directory_and_returns_job(self) -> None:
        self.register()
        from app.services.jobs import job_service

        queued = job_service.store.create(user_id=1, job_type="pdf_import", payload={})
        with patch.object(job_service, "create", return_value=queued) as create_job:
            response = self.client.post(
                "/api/documents/import",
                data={"title": "Uploaded paper"},
                files={"file": ("paper.pdf", b"%PDF-1.4\n%%EOF", "application/pdf")},
            )

        self.assertEqual(response.status_code, 202, response.text)
        self.assertEqual(response.json()["type"], "pdf_import")
        payload = create_job.call_args.args[2]
        stored_path = Path(payload["storedPath"])
        self.assertTrue(stored_path.is_file())
        self.assertEqual(stored_path.parent, (self.tmp_path / "web-data" / "uploads" / "1").resolve())
        self.assertEqual(payload["originalFilename"], "paper.pdf")
        self.assertEqual(payload["title"], "Uploaded paper")

    def test_pdf_upload_removes_partial_file_when_size_limit_is_exceeded(self) -> None:
        self.register()
        from app.core.config import get_settings

        settings = get_settings()
        previous_limit = settings.max_upload_bytes
        settings.max_upload_bytes = 8
        try:
            response = self.client.post(
                "/api/documents/import",
                files={"file": ("large.pdf", b"%PDF-1.4 too large", "application/pdf")},
            )
        finally:
            settings.max_upload_bytes = previous_limit

        self.assertEqual(response.status_code, 413, response.text)
        upload_dir = self.tmp_path / "web-data" / "uploads" / "1"
        self.assertEqual(list(upload_dir.glob("*")), [])

    def test_document_api_does_not_expose_server_path(self) -> None:
        self.register()
        from app.core.legacy import db

        session = db().get_session()
        try:
            db().create_document(
                session,
                user_id=1,
                title="Owned paper",
                source_type="pdf",
                source_path=str(self.tmp_path / "private" / "paper.pdf"),
            )
        finally:
            session.close()

        listed = self.client.get("/api/documents")
        self.assertEqual(listed.status_code, 200, listed.text)
        document = listed.json()["data"][0]
        self.assertNotIn("sourcePath", document)
        self.assertEqual(document["fileName"], "paper.pdf")

    def test_owned_pdf_content_can_be_opened_without_exposing_its_path(self) -> None:
        self.register("alice")
        from app.core.legacy import db

        pdf_path = self.tmp_path / "private" / "owned.pdf"
        pdf_path.parent.mkdir(parents=True)
        pdf_path.write_bytes(b"%PDF-1.4\n%%EOF")
        session = db().get_session()
        try:
            document = db().create_document(session, 1, "Owned", "pdf", str(pdf_path))
            document_id = document.id
        finally:
            session.close()

        opened = self.client.get(f"/api/documents/{document_id}/content")
        self.assertEqual(opened.status_code, 200, opened.text)
        self.assertEqual(opened.headers["content-type"], "application/pdf")
        self.assertNotIn(str(pdf_path), opened.headers.get("content-disposition", ""))

        self.client.post("/api/auth/logout")
        self.register("bob")
        denied = self.client.get(f"/api/documents/{document_id}/content")
        self.assertEqual(denied.status_code, 404, denied.text)

    def test_pdf_reimport_upload_is_scoped_and_creates_reimport_job(self) -> None:
        self.register("alice")
        from app.core.legacy import db
        from app.services.jobs import job_service

        session = db().get_session()
        try:
            document = db().create_document(session, 1, "Owned", "pdf", None)
            document_id = document.id
        finally:
            session.close()

        queued = job_service.store.create(user_id=1, job_type="pdf_reimport", payload={})
        with patch.object(job_service, "create", return_value=queued) as create_job:
            response = self.client.post(
                f"/api/documents/{document_id}/reimport",
                files={"file": ("replacement.pdf", b"%PDF-1.4\n%%EOF", "application/pdf")},
            )
        self.assertEqual(response.status_code, 202, response.text)
        payload = create_job.call_args.args[2]
        self.assertEqual(payload["documentId"], document_id)
        self.assertTrue(Path(payload["storedPath"]).is_file())

        self.client.post("/api/auth/logout")
        self.register("bob")
        denied = self.client.post(
            f"/api/documents/{document_id}/reimport",
            files={"file": ("replacement.pdf", b"%PDF-1.4\n%%EOF", "application/pdf")},
        )
        self.assertEqual(denied.status_code, 404, denied.text)

    def test_document_delete_is_user_scoped_and_removes_chunks_and_managed_file(self) -> None:
        self.register("alice")
        from app.core.legacy import db

        upload_dir = self.tmp_path / "web-data" / "uploads" / "1"
        upload_dir.mkdir(parents=True)
        managed_file = upload_dir / "owned.pdf"
        managed_file.write_bytes(b"%PDF-1.4\n%%EOF")
        session = db().get_session()
        try:
            document = db().create_document(session, 1, "Owned", "pdf", str(managed_file))
            db().add_document_chunks(session, 1, document.id, [(0, "content", b"vector", "text")])
            document_id = document.id
        finally:
            session.close()

        self.client.post("/api/auth/logout")
        self.register("bob")
        denied = self.client.delete(f"/api/documents/{document_id}")
        self.assertEqual(denied.status_code, 404, denied.text)
        self.assertTrue(managed_file.exists())

        self.client.post("/api/auth/logout")
        login = self.client.post("/api/auth/login", json={"username": "alice", "password": "secret-pass"})
        self.assertEqual(login.status_code, 200, login.text)
        deleted = self.client.delete(f"/api/documents/{document_id}")
        self.assertEqual(deleted.status_code, 204, deleted.text)
        self.assertFalse(managed_file.exists())
        session = db().get_session()
        try:
            self.assertEqual(session.query(db().Document).count(), 0)
            self.assertEqual(session.query(db().DocumentChunk).count(), 0)
        finally:
            session.close()

    def test_job_list_filters_by_type_and_status(self) -> None:
        self.register()
        from app.services.jobs import job_service

        qa = job_service.store.create(1, "rag_qa", {"question": "q"})
        search = job_service.store.create(1, "literature_search", {"queryText": "x"})
        job_service.store.update(qa.id, status="SUCCEEDED", progress=100)
        job_service.store.update(search.id, status="FAILED")

        response = self.client.get("/api/jobs?type=rag_qa&status=SUCCEEDED")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["pagination"]["totalItems"], 1)
        self.assertEqual(response.json()["data"][0]["id"], qa.id)

    def test_completed_science_jobs_expose_only_safe_result_resource_ids(self) -> None:
        self.register()
        from app.services.jobs import job_service

        multimodal = job_service.store.create(
            1,
            "multimodal_analyze",
            {
                "sources": [{"sourceType": "upload", "assetId": "asset-1"}],
                "question": "",
                "useLiteratureContext": False,
            },
        )
        job_service.store.update(
            multimodal.id,
            status="SUCCEEDED",
            progress=100,
            result={"runId": "run-1", "internalPath": "D:/private/result.json"},
        )
        modeling = job_service.store.create(
            1,
            "modeling_generate",
            {
                "source": {"type": "manual", "text": "PBE relaxation"},
                "mode": "vasp",
                "calcType": "relax",
            },
        )
        job_service.store.update(
            modeling.id,
            status="SUCCEEDED",
            progress=100,
            result={"workspaceId": "workspace-1", "serverPath": "D:/private/workspace"},
        )

        multimodal_response = self.client.get(f"/api/jobs/{multimodal.id}")
        modeling_response = self.client.get(f"/api/jobs/{modeling.id}")

        self.assertEqual(multimodal_response.status_code, 200, multimodal_response.text)
        self.assertEqual(multimodal_response.json()["resource"], {
            "sourceIds": ["asset-1"],
            "runId": "run-1",
        })
        self.assertNotIn("internalPath", multimodal_response.json()["resource"])
        self.assertEqual(modeling_response.status_code, 200, modeling_response.text)
        self.assertEqual(modeling_response.json()["resource"]["workspaceId"], "workspace-1")
        self.assertNotIn("serverPath", modeling_response.json()["resource"])

    def test_document_analysis_job_contract_returns_only_safe_resource_context(self) -> None:
        self.register()
        from app.core.legacy import db
        from app.services.jobs import job_service

        session = db().get_session()
        try:
            document = db().create_document(session, 1, "Owned paper", "pdf", None)
            document_id = document.id
        finally:
            session.close()

        queued = job_service.store.create(
            1,
            "document_analysis",
            {"documentId": document_id, "analysisType": "summary"},
        )
        with patch.object(job_service, "create", return_value=queued):
            response = self.client.post(
                "/api/jobs",
                json={
                    "type": "document_analysis",
                    "payload": {"documentId": document_id, "analysisType": "summary"},
                },
            )

        self.assertEqual(response.status_code, 202, response.text)
        self.assertEqual(response.json()["resource"], {
            "analysisType": "summary",
            "documentId": document_id,
        })
        self.assertNotIn("payload", response.json())

        invalid = self.client.post(
            "/api/jobs",
            json={
                "type": "document_analysis",
                "payload": {
                    "documentId": document_id,
                    "analysisType": "summary",
                    "chemicalName": "ethanol",
                },
            },
        )
        self.assertEqual(invalid.status_code, 422, invalid.text)
        self.assertEqual(invalid.json()["error"]["code"], "VALIDATION_ERROR")

    def test_document_analysis_job_cannot_target_another_users_document(self) -> None:
        self.register("alice")
        from app.core.legacy import db

        session = db().get_session()
        try:
            document = db().create_document(session, 1, "Alice paper", "pdf", None)
            document_id = document.id
        finally:
            session.close()
        self.client.post("/api/auth/logout")
        self.register("bob")

        response = self.client.post(
            "/api/jobs",
            json={
                "type": "document_analysis",
                "payload": {"documentId": document_id, "analysisType": "summary"},
            },
        )

        self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(response.json()["error"]["code"], "NOT_FOUND")

    def test_hypothesis_job_cannot_target_another_users_document(self) -> None:
        self.register("alice")
        from app.core.legacy import db

        session = db().get_session()
        try:
            document = db().create_document(session, 1, "Alice paper", "pdf", None)
            document_id = document.id
        finally:
            session.close()
        self.client.post("/api/auth/logout")
        self.register("bob")

        response = self.client.post(
            "/api/jobs",
            json={
                "type": "hypothesis_generate",
                "payload": {"researchQuestion": "What should be tested?", "sourceDocIds": [document_id]},
            },
        )

        self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(response.json()["error"]["code"], "NOT_FOUND")

    def test_active_hypothesis_job_returns_conflict_with_existing_job_id(self) -> None:
        self.register()
        from app.services.jobs import ActiveHypothesisJobError, job_service

        with patch.object(job_service, "create", side_effect=ActiveHypothesisJobError("job-existing")):
            response = self.client.post(
                "/api/jobs",
                json={
                    "type": "hypothesis_generate",
                    "payload": {"researchQuestion": "What should be tested?"},
                },
            )

        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["error"]["code"], "HYPOTHESIS_JOB_ACTIVE")
        self.assertEqual(response.json()["error"]["details"]["existingJobId"], "job-existing")

    def test_hypothesis_feedback_requires_waiting_job_and_strong_schema(self) -> None:
        self.register()
        from app.services.jobs import job_service

        job = job_service.store.create(1, "hypothesis_generate", {"researchQuestion": "q"})
        job_service.store.update(job.id, status="RUNNING")

        not_waiting = self.client.post(
            f"/api/jobs/{job.id}/feedback",
            json={"action": "approve"},
        )
        invalid = self.client.post(
            f"/api/jobs/{job.id}/feedback",
            json={"action": "revise", "structuredFeedback": {"unknown": "value"}},
        )

        self.assertEqual(not_waiting.status_code, 409, not_waiting.text)
        self.assertEqual(not_waiting.json()["error"]["code"], "JOB_NOT_WAITING")
        self.assertEqual(invalid.status_code, 422, invalid.text)

    def test_hypothesis_list_and_detail_are_structured_filtered_and_path_safe(self) -> None:
        self.register()
        from app.core.legacy import db
        from app.services.jobs import job_service

        private_path = self.tmp_path / "private" / "trace.json"
        session = db().get_session()
        try:
            hypothesis = db().Hypothesis(
                user_id=1,
                title="Owned hypothesis",
                research_question="Can the catalyst remain stable?",
                result_json=json.dumps({
                    "paper_title": "Owned hypothesis",
                    "problem_statement": "Test stability",
                    "_domain": "materials_chemistry",
                    "web_report_path": str(private_path),
                }),
                confidence=8,
                feasibility="high",
                iteration_count=2,
                related_doc_ids=json.dumps([3, 5]),
                extra_json=json.dumps({
                    "tags": ["stability", "catalyst"],
                    "domain": "materials_chemistry",
                    "iterations": [{"round": 1}],
                    "critique_history": [{"score": 8}],
                    "reasoning_chain": {"steps": []},
                    "debate_history": [],
                    "agent_trace_path": str(private_path),
                }),
                status="reviewed",
            )
            session.add(hypothesis)
            session.flush()
            hypothesis_id = hypothesis.id
            session.add(db().HypothesisFeedback(
                hypothesis_id=hypothesis_id,
                user_id=1,
                session_id="session-detail",
                interaction_json=json.dumps({"interactions": [{"user_action": "approve"}]}),
                mode="hitl",
            ))
            session.commit()
        finally:
            session.close()

        artifact_path = job_service.hypothesis_assets_dir / "1" / str(hypothesis_id) / "agent-trace.json"
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_path.write_text("{}", encoding="utf-8")
        job_service.hypothesis_store.upsert(
            1,
            hypothesis_id,
            "agent_trace_json",
            artifact_path,
            "agent-trace.json",
            "application/json",
        )

        listed = self.client.get(
            "/api/hypotheses?status=reviewed&domain=materials_chemistry&tag=stability&sourceDocumentId=5"
        )
        detail = self.client.get(f"/api/hypotheses/{hypothesis_id}")

        self.assertEqual(listed.status_code, 200, listed.text)
        self.assertEqual(listed.json()["pagination"]["totalItems"], 1)
        summary = listed.json()["data"][0]
        self.assertNotIn("resultJson", summary)
        self.assertNotIn("extraJson", summary)
        self.assertEqual(summary["sourceDocumentIds"], [3, 5])
        self.assertEqual(summary["tags"], ["stability", "catalyst"])
        self.assertEqual(detail.status_code, 200, detail.text)
        body = detail.json()
        self.assertEqual(body["hypothesis"]["problemStatement"], "Test stability")
        self.assertEqual(body["critiqueHistory"][0]["score"], 8)
        self.assertEqual(body["hitl"]["feedbackRecords"][0]["mode"], "hitl")
        self.assertEqual(body["artifacts"][0]["kind"], "agent_trace_json")
        self.assertNotIn(str(self.tmp_path), detail.text)

    def test_hypothesis_patch_only_updates_metadata_and_preserves_scientific_result(self) -> None:
        self.register()
        from app.core.legacy import db

        session = db().get_session()
        try:
            hypothesis = db().Hypothesis(
                user_id=1,
                title="Before",
                result_json=json.dumps({"paper_title": "Immutable result"}),
                confidence=7,
                feasibility="high",
                iteration_count=1,
                extra_json=json.dumps({"reasoning_chain": {"steps": [1]}}),
                status="draft",
            )
            session.add(hypothesis)
            session.commit()
            hypothesis_id = hypothesis.id
        finally:
            session.close()

        invalid = self.client.patch(
            f"/api/hypotheses/{hypothesis_id}",
            json={"resultJson": "{}"},
        )
        updated = self.client.patch(
            f"/api/hypotheses/{hypothesis_id}",
            json={"title": "After", "status": "approved", "tags": ["validated"]},
        )

        self.assertEqual(invalid.status_code, 422, invalid.text)
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertEqual(updated.json()["title"], "After")
        self.assertEqual(updated.json()["tags"], ["validated"])
        session = db().get_session()
        try:
            row = session.get(db().Hypothesis, hypothesis_id)
            self.assertEqual(json.loads(row.result_json)["paper_title"], "Immutable result")
            self.assertEqual(json.loads(row.extra_json)["reasoning_chain"], {"steps": [1]})
        finally:
            session.close()

    def test_hypothesis_artifacts_are_user_scoped_and_delete_cascades_managed_resources(self) -> None:
        self.register("alice")
        from app.core.legacy import db
        from app.services.jobs import job_service

        session = db().get_session()
        try:
            hypothesis = db().Hypothesis(
                user_id=1,
                title="Delete me",
                result_json="{}",
                confidence=5,
                feasibility="medium",
                iteration_count=0,
                status="draft",
            )
            session.add(hypothesis)
            session.flush()
            hypothesis_id = hypothesis.id
            session.add(db().HypothesisFeedback(
                hypothesis_id=hypothesis_id,
                user_id=1,
                session_id="delete-session",
                interaction_json="{}",
                mode="hitl",
            ))
            session.commit()
        finally:
            session.close()

        report = job_service.hypothesis_assets_dir / "1" / str(hypothesis_id) / "report.html"
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text("<html>owned</html>", encoding="utf-8")
        artifact, _ = job_service.hypothesis_store.upsert(
            1, hypothesis_id, "interactive_report_html", report, "report.html", "text/html"
        )

        owned = self.client.get(f"/api/hypotheses/{hypothesis_id}/artifacts/{artifact.id}")
        self.assertEqual(owned.status_code, 200, owned.text)
        self.client.post("/api/auth/logout")
        self.register("bob")
        denied = self.client.get(f"/api/hypotheses/{hypothesis_id}/artifacts/{artifact.id}")
        self.assertEqual(denied.status_code, 404, denied.text)

        self.client.post("/api/auth/logout")
        self.client.post("/api/auth/login", json={"username": "alice", "password": "secret-pass"})
        deleted = self.client.delete(f"/api/hypotheses/{hypothesis_id}")
        self.assertEqual(deleted.status_code, 204, deleted.text)
        self.assertFalse(report.exists())
        self.assertEqual(job_service.hypothesis_store.list_for_hypothesis(1, hypothesis_id), [])
        session = db().get_session()
        try:
            self.assertIsNone(session.get(db().Hypothesis, hypothesis_id))
            self.assertEqual(session.query(db().HypothesisFeedback).count(), 0)
        finally:
            session.close()

    def test_hypothesis_artifact_jobs_require_owned_hypothesis_and_expose_safe_resource(self) -> None:
        self.register("alice")
        from app.core.legacy import db
        from app.services.jobs import job_service

        session = db().get_session()
        try:
            hypothesis = db().Hypothesis(
                user_id=1,
                title="Owned",
                result_json="{}",
                confidence=5,
                feasibility="medium",
                iteration_count=0,
                status="draft",
            )
            session.add(hypothesis)
            session.commit()
            hypothesis_id = hypothesis.id
        finally:
            session.close()
        queued = job_service.store.create(1, "hypothesis_report", {"hypothesisId": hypothesis_id, "enhance": True})
        with patch.object(job_service, "create", return_value=queued):
            response = self.client.post(
                "/api/jobs",
                json={"type": "hypothesis_report", "payload": {"hypothesisId": hypothesis_id}},
            )
        self.assertEqual(response.status_code, 202, response.text)
        self.assertEqual(response.json()["resource"], {
            "hypothesisId": hypothesis_id,
            "artifactKind": "interactive_report_html",
        })
        self.assertNotIn("payload", response.json())

        self.client.post("/api/auth/logout")
        self.register("bob")
        denied = self.client.post(
            "/api/jobs",
            json={"type": "hypothesis_workflow_export", "payload": {"hypothesisId": hypothesis_id}},
        )
        self.assertEqual(denied.status_code, 404, denied.text)

    def test_document_compare_requires_two_to_five_unique_documents(self) -> None:
        self.register()

        duplicate = self.client.post(
            "/api/jobs",
            json={"type": "document_compare", "payload": {"documentIds": [2, 2]}},
        )
        too_many = self.client.post(
            "/api/jobs",
            json={"type": "document_compare", "payload": {"documentIds": [1, 2, 3, 4, 5, 6]}},
        )

        self.assertEqual(duplicate.status_code, 422, duplicate.text)
        self.assertEqual(too_many.status_code, 422, too_many.text)

    def test_analysis_and_asset_endpoints_are_user_scoped(self) -> None:
        self.register("alice")
        from app.core.legacy import db
        from app.services.jobs import job_service

        session = db().get_session()
        try:
            document = db().create_document(session, 1, "Owned paper", "pdf", None)
            document_id = document.id
        finally:
            session.close()
        job_service.analysis_store.upsert(1, "sop", [document_id], {"title": "Owned SOP"})
        asset_path = job_service.assets_dir / "1" / str(document_id) / "images" / "figure.png"
        asset_path.parent.mkdir(parents=True, exist_ok=True)
        asset_path.write_bytes(b"\x89PNG\r\n\x1a\n")
        asset = job_service.analysis_store.register_asset(
            1, document_id, "images", asset_path, "figure.png", "image/png"
        )

        analyses = self.client.get(f"/api/documents/{document_id}/analyses")
        image = self.client.get(f"/api/documents/{document_id}/assets/{asset.id}")
        self.assertEqual(analyses.status_code, 200, analyses.text)
        self.assertEqual(analyses.json()["data"][0]["result"]["title"], "Owned SOP")
        self.assertEqual(image.status_code, 200, image.text)
        self.assertEqual(image.headers["content-type"], "image/png")

        self.client.post("/api/auth/logout")
        self.register("bob")
        denied_analyses = self.client.get(f"/api/documents/{document_id}/analyses")
        denied_image = self.client.get(f"/api/documents/{document_id}/assets/{asset.id}")
        self.assertEqual(denied_analyses.status_code, 404, denied_analyses.text)
        self.assertEqual(denied_image.status_code, 404, denied_image.text)

    def test_latest_document_comparison_is_order_independent(self) -> None:
        self.register()
        from app.core.legacy import db
        from app.services.jobs import job_service

        session = db().get_session()
        try:
            first = db().create_document(session, 1, "First", "pdf", None)
            second = db().create_document(session, 1, "Second", "pdf", None)
            document_ids = [first.id, second.id]
        finally:
            session.close()
        job_service.analysis_store.upsert(1, "comparison", document_ids, {"markdown": "comparison"})

        response = self.client.get(
            f"/api/document-comparisons/latest?documentId={document_ids[1]}&documentId={document_ids[0]}"
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["data"]["result"]["markdown"], "comparison")
        self.assertEqual(response.json()["data"]["documentIds"], sorted(document_ids))

    def test_glossary_batch_upserts_terms_for_the_current_user(self) -> None:
        self.register()

        response = self.client.post(
            "/api/glossary/batch",
            json={
                "terms": [
                    {"enTerm": "overpotential", "zhTerm": "过电位", "note": ""},
                    {"enTerm": "Faradaic efficiency", "zhTerm": "法拉第效率"},
                ]
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["count"], 2)
        self.assertEqual(len(response.json()["data"]), 2)
        listed = self.client.get("/api/glossary")
        self.assertEqual(listed.json()["pagination"]["totalItems"], 2)

    def test_multimodal_asset_upload_validates_content_and_is_user_scoped(self) -> None:
        self.register("alice")
        png = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
        )

        uploaded = self.client.post(
            "/api/multimodal/assets",
            files=[("files", ("figure.png", png, "image/png"))],
        )
        self.assertEqual(uploaded.status_code, 201, uploaded.text)
        asset = uploaded.json()["data"][0]
        self.assertEqual(asset["fileName"], "figure.png")
        self.assertEqual(asset["assetType"], "image")
        self.assertNotIn("path", json.dumps(asset).lower())

        content = self.client.get(f"/api/multimodal/assets/{asset['id']}/content")
        self.assertEqual(content.status_code, 200, content.text)
        self.assertEqual(content.content, png)

        rejected = self.client.post(
            "/api/multimodal/assets",
            files=[("files", ("fake.png", b"not-an-image", "image/png"))],
        )
        self.assertEqual(rejected.status_code, 422, rejected.text)
        self.assertEqual(rejected.json()["error"]["code"], "INVALID_MULTIMODAL_FILE")

        self.client.post("/api/auth/logout")
        self.register("bob")
        self.assertEqual(self.client.get("/api/multimodal/assets").json()["pagination"]["totalItems"], 0)
        denied = self.client.get(f"/api/multimodal/assets/{asset['id']}/content")
        self.assertEqual(denied.status_code, 404, denied.text)

    def test_multimodal_batch_upload_rolls_back_all_files_when_one_is_invalid(self) -> None:
        self.register()
        png = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
        )
        response = self.client.post(
            "/api/multimodal/assets",
            files=[
                ("files", ("valid.png", png, "image/png")),
                ("files", ("invalid.png", b"not an image", "image/png")),
            ],
        )

        self.assertEqual(response.status_code, 422, response.text)
        listed = self.client.get("/api/multimodal/assets")
        self.assertEqual(listed.json()["pagination"]["totalItems"], 0)
        user_dir = self.tmp_path / "web-data" / "multimodal-assets" / "1"
        self.assertEqual(list(user_dir.glob("*")), [])

    def test_multimodal_upload_rejects_declared_mime_mismatch(self) -> None:
        self.register()
        png = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
        )
        response = self.client.post(
            "/api/multimodal/assets",
            files=[("files", ("figure.png", png, "text/plain"))],
        )
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(response.json()["error"]["code"], "INVALID_MULTIMODAL_FILE")

    def test_multimodal_csv_content_returns_user_scoped_paginated_preview(self) -> None:
        self.register("alice")
        uploaded = self.client.post(
            "/api/multimodal/assets",
            files=[("files", ("measurements.csv", b"sample,value\nA,1\nB,2\nC,3\n", "text/csv"))],
        )
        self.assertEqual(uploaded.status_code, 201, uploaded.text)
        asset_id = uploaded.json()["data"][0]["id"]

        preview = self.client.get(
            f"/api/multimodal/assets/{asset_id}/content?sheetName=CSV&page=2&pageSize=2"
        )

        self.assertEqual(preview.status_code, 200, preview.text)
        self.assertEqual(preview.json()["columns"], ["sample", "value"])
        self.assertEqual(preview.json()["rows"], [["C", "3"]])
        self.assertEqual(preview.json()["pagination"]["totalItems"], 3)
        self.assertEqual(preview.json()["coverage"]["strategy"], "paged-read-only-preview")

        self.client.post("/api/auth/logout")
        self.register("bob")
        denied = self.client.get(f"/api/multimodal/assets/{asset_id}/content")
        self.assertEqual(denied.status_code, 404, denied.text)

    def test_multimodal_run_patch_uses_revision_conflict_semantics(self) -> None:
        self.register()
        from app.services.jobs import job_service

        run = job_service.science_store.create_multimodal_run(
            user_id=1,
            question="",
            options={},
            source_refs=[],
            result={
                "summary": {"total": 1, "succeeded": 1, "failed": 0, "dataPoints": 1, "associations": 0},
                "items": [{
                    "status": "succeeded",
                    "source": {"sourceType": "upload", "assetId": "asset-1", "fileName": "figure.png"},
                    "imageType": "XRD",
                    "summary": "Peak summary",
                    "dataPoints": [{"parameter": "peak", "value": 1.0, "unit": "eV", "source": "figure.png"}],
                    "annotations": [],
                }],
                "associations": [],
                "quantitative": {"summary": {}, "scalingRelations": [], "correlations": []},
                "context": "stale context",
                "evidence": {"figures": [{"points": [{"value": 1.0}]}], "associations": []},
                "warnings": [],
            },
            job_id="job-1",
        )
        patched = self.client.patch(
            f"/api/multimodal/runs/{run.id}",
            json={
                "expectedRevision": 1,
                "changes": {"itemCorrections": [{
                    "itemIndex": 0,
                    "dataPoints": [{
                        "parameter": "peak",
                        "value": 2.0,
                        "unit": "eV",
                        "source": "figure.png",
                    }],
                    "annotations": [{
                        "id": "note-1",
                        "x": 0.1,
                        "y": 0.2,
                        "width": 0.3,
                        "height": 0.4,
                        "note": "main peak",
                    }],
                }]},
            },
        )
        self.assertEqual(patched.status_code, 200, patched.text)
        self.assertEqual(patched.json()["revision"], 2)
        self.assertIn("2.0 eV", patched.json()["result"]["context"])
        self.assertEqual(patched.json()["result"]["evidence"]["figures"][0]["points"][0]["value"], 2.0)

        stale = self.client.patch(
            f"/api/multimodal/runs/{run.id}",
            json={
                "expectedRevision": 1,
                "changes": {"itemCorrections": [{"itemIndex": 0, "dataPoints": []}]},
            },
        )
        self.assertEqual(stale.status_code, 409, stale.text)
        self.assertEqual(stale.json()["error"]["code"], "REVISION_CONFLICT")

    def test_multimodal_run_patch_rejects_arbitrary_result_replacement_and_bad_indices(self) -> None:
        self.register()
        from app.services.jobs import job_service

        run = job_service.science_store.create_multimodal_run(
            user_id=1,
            question="",
            options={},
            source_refs=[],
            result={"items": [{"dataPoints": [], "annotations": []}]},
            job_id="job-correction-boundary",
        )

        arbitrary = self.client.patch(
            f"/api/multimodal/runs/{run.id}",
            json={"expectedRevision": 1, "changes": {"items": []}},
        )
        bad_index = self.client.patch(
            f"/api/multimodal/runs/{run.id}",
            json={
                "expectedRevision": 1,
                "changes": {"itemCorrections": [{"itemIndex": 4, "dataPoints": []}]},
            },
        )

        self.assertEqual(arbitrary.status_code, 422, arbitrary.text)
        self.assertEqual(arbitrary.json()["error"]["code"], "VALIDATION_ERROR")
        self.assertEqual(bad_index.status_code, 422, bad_index.text)
        self.assertEqual(bad_index.json()["error"]["code"], "INVALID_CORRECTION")

    def test_multimodal_jobs_and_hypothesis_inputs_reject_other_users_resources(self) -> None:
        self.register("alice")
        from app.services.jobs import job_service

        asset_path = job_service.multimodal_assets_dir / "1" / "owned.png"
        asset_path.parent.mkdir(parents=True, exist_ok=True)
        asset_path.write_bytes(b"owned")
        asset = job_service.science_store.create_multimodal_asset(
            user_id=1,
            managed_path=asset_path,
            file_name="owned.png",
            mime_type="image/png",
            asset_type="image",
            metadata={},
        )
        run = job_service.science_store.create_multimodal_run(
            user_id=1,
            question="",
            options={},
            source_refs=[{"sourceType": "upload", "assetId": asset.id}],
            result={"items": []},
            job_id="owned-run-job",
        )

        self.client.post("/api/auth/logout")
        self.register("bob")
        multimodal = self.client.post("/api/jobs", json={
            "type": "multimodal_analyze",
            "payload": {
                "sources": [{"sourceType": "upload", "assetId": asset.id}],
                "question": "",
                "useLiteratureContext": False,
            },
        })
        hypothesis = self.client.post("/api/jobs", json={
            "type": "hypothesis_generate",
            "payload": {"researchQuestion": "Question", "multimodalRunIds": [run.id]},
        })

        self.assertEqual(multimodal.status_code, 404, multimodal.text)
        self.assertEqual(hypothesis.status_code, 404, hypothesis.text)

    def test_modeling_options_and_workspace_patch_are_server_driven(self) -> None:
        self.register()
        from app.services.jobs import job_service

        options = self.client.get("/api/modeling/options")
        self.assertEqual(options.status_code, 200, options.text)
        self.assertIn("relax", {item["id"] for item in options.json()["calcTypes"]})
        self.assertIn("geometry_optimization", {item["id"] for item in options.json()["vaspkitTasks"]})

        workspace = job_service.science_store.create_modeling_workspace(
            user_id=1,
            source={"type": "manual"},
            mode="vasp",
            original_result={"incar": {"ENCUT": {"value": 520, "source": "default"}}},
            job_id="job-2",
        )
        patched = self.client.patch(
            f"/api/modeling/workspaces/{workspace.id}",
            json={
                "expectedRevision": 1,
                "changes": {"incar": {"ENCUT": {"value": 600, "source": "user"}}},
            },
        )
        self.assertEqual(patched.status_code, 200, patched.text)
        self.assertEqual(patched.json()["result"]["incar"]["ENCUT"]["value"], 600)
        self.assertIn("ENCUT = 600", patched.json()["result"]["files"]["incar"])
        self.assertEqual(len(patched.json()["revisions"]), 1)

        self.client.post("/api/auth/logout")
        self.register("bob")
        denied = self.client.get(f"/api/modeling/workspaces/{workspace.id}")
        self.assertEqual(denied.status_code, 404, denied.text)

    def test_modeling_workspace_patch_rejects_invalid_grid_and_element(self) -> None:
        self.register()
        from app.services.jobs import job_service

        workspace = job_service.science_store.create_modeling_workspace(
            user_id=1,
            source={"type": "manual"},
            mode="vasp",
            original_result={
                "incar": {},
                "kpoints": {"mode": "Gamma", "mesh": [6, 6, 6], "source": "default"},
                "potcarElements": [],
            },
            job_id="job-invalid-modeling-patch",
        )

        bad_grid = self.client.patch(
            f"/api/modeling/workspaces/{workspace.id}",
            json={
                "expectedRevision": 1,
                "changes": {"kpoints": {"mode": "Gamma", "mesh": [0, 6, 6]}},
            },
        )
        bad_element = self.client.patch(
            f"/api/modeling/workspaces/{workspace.id}",
            json={
                "expectedRevision": 1,
                "changes": {"potcarElements": [{"element": "Xx", "potential": "Xx"}]},
            },
        )

        self.assertEqual(bad_grid.status_code, 422, bad_grid.text)
        self.assertEqual(bad_grid.json()["error"]["code"], "VALIDATION_ERROR")
        self.assertEqual(bad_element.status_code, 422, bad_element.text)
        self.assertEqual(bad_element.json()["error"]["code"], "VALIDATION_ERROR")

    def test_modeling_structure_upload_requires_parseable_content_and_returns_geometry(self) -> None:
        self.register()
        from app.services.jobs import job_service

        workspace = job_service.science_store.create_modeling_workspace(
            user_id=1,
            source={"type": "manual"},
            mode="vasp",
            original_result={"files": {}},
            job_id="job-structure",
        )
        poscar = b"""Silicon
1.0
5.43 0 0
0 5.43 0
0 0 5.43
Si
1
Direct
0 0 0
"""
        uploaded = self.client.post(
            f"/api/modeling/workspaces/{workspace.id}/structures",
            files={"file": ("POSCAR", poscar, "text/plain")},
        )
        self.assertEqual(uploaded.status_code, 201, uploaded.text)
        structure = uploaded.json()
        self.assertEqual(structure["format"], "poscar")
        self.assertEqual(structure["geometry"]["atoms"][0]["element"], "Si")
        self.assertEqual(len(structure["geometry"]["cell"]), 3)

        fetched = self.client.get(
            f"/api/modeling/workspaces/{workspace.id}/structures/{structure['id']}"
        )
        self.assertEqual(fetched.status_code, 200, fetched.text)
        self.assertNotIn(str(self.tmp_path), fetched.text)

        invalid = self.client.post(
            f"/api/modeling/workspaces/{workspace.id}/structures",
            files={"file": ("bad.cif", b"not a cif", "chemical/x-cif")},
        )
        self.assertEqual(invalid.status_code, 422, invalid.text)
        self.assertEqual(invalid.json()["error"]["code"], "INVALID_STRUCTURE")


if __name__ == "__main__":
    unittest.main()
