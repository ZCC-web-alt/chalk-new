from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))


class HypothesisArtifactStoreTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_path = Path(self.tmp.name) / "web.db"

    def make_store(self):
        from app.services.hypothesis_store import HypothesisArtifactStore

        store = HypothesisArtifactStore(self.db_path)
        self.addCleanup(store.dispose)
        return store

    def test_artifacts_are_user_scoped(self) -> None:
        store = self.make_store()
        report = Path(self.tmp.name) / "report.html"
        report.write_text("<html></html>", encoding="utf-8")
        artifact, replaced = store.upsert(
            user_id=1,
            hypothesis_id=9,
            kind="interactive_report_html",
            path=report,
            file_name="report.html",
            mime_type="text/html",
        )

        self.assertEqual(replaced, [])
        self.assertIsNotNone(store.get_for_user(1, 9, artifact.id))
        self.assertIsNone(store.get_for_user(2, 9, artifact.id))
        self.assertEqual(len(store.list_for_hypothesis(1, 9)), 1)
        self.assertEqual(store.list_for_hypothesis(2, 9), [])

    def test_successful_replacement_keeps_artifact_id_and_returns_old_path(self) -> None:
        store = self.make_store()
        first_path = Path(self.tmp.name) / "first.html"
        second_path = Path(self.tmp.name) / "second.html"
        first_path.write_text("first", encoding="utf-8")
        second_path.write_text("second", encoding="utf-8")
        first, _ = store.upsert(1, 4, "interactive_report_html", first_path, "first.html", "text/html")

        second, replaced = store.upsert(1, 4, "interactive_report_html", second_path, "second.html", "text/html")

        self.assertEqual(second.id, first.id)
        self.assertEqual(second.path, second_path.resolve())
        self.assertEqual(replaced, [first_path.resolve()])

    def test_delete_for_hypothesis_returns_managed_paths(self) -> None:
        store = self.make_store()
        report = Path(self.tmp.name) / "report.html"
        package = Path(self.tmp.name) / "workflow.zip"
        report.write_text("report", encoding="utf-8")
        package.write_bytes(b"zip")
        store.upsert(1, 7, "interactive_report_html", report, report.name, "text/html")
        store.upsert(1, 7, "workflow_package_zip", package, package.name, "application/zip")

        paths = store.delete_for_hypothesis(1, 7)

        self.assertEqual(set(paths), {report.resolve(), package.resolve()})
        self.assertEqual(store.list_for_hypothesis(1, 7), [])


if __name__ == "__main__":
    unittest.main()
