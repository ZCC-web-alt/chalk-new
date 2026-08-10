from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
import sys


BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))


class Science125ReportStoreTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_path = Path(self.tmp.name) / "web.db"

    def make_store(self):
        from app.services.science125_report_store import Science125ReportStore

        store = Science125ReportStore(self.db_path)
        self.addCleanup(store.dispose)
        return store

    def test_create_batch_defaults_to_all_authoritative_questions_and_is_user_scoped(self) -> None:
        store = self.make_store()

        batch = store.create_batch(
            user_id=7,
            question_ids=None,
            manifest_version="science125-v1",
            manifest_sha256="a" * 64,
            routing_version="science125-routing-v1",
            routing_sha256="b" * 64,
            prompt_version="science125-prompts-v1",
            prompt_registry_sha256="c" * 64,
            model="qwen3.8-max",
        )

        self.assertEqual(batch.total_count, 125)
        self.assertEqual(batch.question_ids[0], "S125-001")
        self.assertEqual(batch.question_ids[-1], "S125-125")
        self.assertEqual(store.list_batches(user_id=7)[0].id, batch.id)
        self.assertEqual(store.list_batches(user_id=8), [])
        self.assertEqual(len(store.list_batch_items(user_id=7, batch_id=batch.id)), 125)
        self.assertEqual(batch.prompt_registry_sha256, "c" * 64)

    def test_reports_and_exports_are_user_scoped_and_join_to_their_batch(self) -> None:
        store = self.make_store()
        batch = store.create_batch(
            user_id=1,
            question_ids=("S125-006",),
            manifest_version="science125-v1",
            manifest_sha256="a" * 64,
            routing_version="science125-routing-v1",
            routing_sha256="b" * 64,
            prompt_version="science125-prompts-v1",
            prompt_registry_sha256="c" * 64,
            model="qwen3.8-max",
        )
        report = store.upsert_report(
            user_id=1,
            batch_id=batch.id,
            question_id="S125-006",
            question="How can we measure interface phenomena on the microscopic level?",
            question_zh="我们如何在微观尺度上测量界面现象？",
            benchmark_domain="Chemistry",
            primary_subdomain="chem.interface",
            attempt_number=1,
            status="SUCCEEDED",
            evidence_status="sufficient",
            selected_evidence_count=3,
            provider_families=("doi_registry", "scholarly_index"),
            total_tokens=1234,
            latency_ms=456,
            estimated_cost_cny=0.42,
            source_type="interactive_job",
            source_job_id="73e07aff-d4e7-47c7-b8fa-a99cfdae8c64",
        )
        export = store.create_export(
            user_id=1,
            batch_id=batch.id,
            report_id=report.id,
            format="json",
            file_name="S125-006-v1.json",
            mime_type="application/json",
            size_bytes=9,
            managed_path=Path(self.tmp.name) / "S125-006-v1.json",
        )

        self.assertIsNotNone(store.get_report_for_user(user_id=1, report_id=report.id))
        self.assertIsNone(store.get_report_for_user(user_id=2, report_id=report.id))
        self.assertEqual(store.list_reports(user_id=1, batch_id=batch.id)[0].question_id, "S125-006")
        self.assertEqual(store.list_reports_by_question(user_id=1, question_id="S125-006")[0].id, report.id)
        self.assertEqual(report.source_type, "interactive_job")
        self.assertEqual(report.source_job_id, "73e07aff-d4e7-47c7-b8fa-a99cfdae8c64")
        self.assertEqual(
            store.get_report_by_source_job(
                user_id=1,
                source_job_id="73e07aff-d4e7-47c7-b8fa-a99cfdae8c64",
            ).id,
            report.id,
        )
        self.assertIsNotNone(store.get_export_for_user(user_id=1, export_id=export.id))
        self.assertIsNone(store.get_export_for_user(user_id=2, export_id=export.id))
        self.assertEqual(store.list_exports_for_batch(user_id=1, batch_id=batch.id)[0].id, export.id)

    def test_existing_batch_table_gains_prompt_registry_hash_without_losing_rows(self) -> None:
        store = self.make_store()
        batch = store.create_batch(
            user_id=3,
            question_ids=("S125-006",),
            manifest_version="science125-v1",
            manifest_sha256="a" * 64,
            routing_version="science125-routing-v1",
            routing_sha256="b" * 64,
            prompt_version="science125-prompts-v1",
            prompt_registry_sha256="0" * 64,
            model="qwen3.8-max",
        )
        store.dispose()
        reopened = self.make_store()
        restored = reopened.get_batch_for_user(user_id=3, batch_id=batch.id)

        self.assertIsNotNone(restored)
        self.assertEqual(restored.prompt_registry_sha256, "0" * 64)  # type: ignore[union-attr]

    def test_report_source_columns_survive_reopen_without_losing_rows(self) -> None:
        store = self.make_store()
        batch = store.create_batch(
            user_id=3,
            question_ids=("S125-006",),
            manifest_version="science125-v1",
            manifest_sha256="a" * 64,
            routing_version="science125-routing-v1",
            routing_sha256="b" * 64,
            prompt_version="science125-prompts-v1",
            prompt_registry_sha256="c" * 64,
            model="qwen3.8-max",
        )
        report = store.upsert_report(
            user_id=3,
            batch_id=batch.id,
            question_id="S125-006",
            question="How can interfaces be measured?",
            benchmark_domain="Chemistry",
            primary_subdomain="chem.interface",
            attempt_number=1,
            status="SUCCEEDED",
            evidence_status="sufficient",
            selected_evidence_count=3,
        )
        store.dispose()

        reopened = self.make_store()
        restored = reopened.get_report_for_user(user_id=3, report_id=report.id)

        self.assertIsNotNone(restored)
        self.assertEqual(restored.source_type, "batch")  # type: ignore[union-attr]
        self.assertIsNone(restored.source_job_id)  # type: ignore[union-attr]


if __name__ == "__main__":
    unittest.main()
