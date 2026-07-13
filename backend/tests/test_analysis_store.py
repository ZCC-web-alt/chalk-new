from __future__ import annotations

import tempfile
import unittest
from pathlib import Path


class DocumentAnalysisStoreTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "web.db"
        self.stores = []

    def tearDown(self) -> None:
        for store in self.stores:
            store.dispose()
        self.tmp.cleanup()

    def make_store(self):
        from app.services.analysis_store import DocumentAnalysisStore

        store = DocumentAnalysisStore(self.db_path)
        self.stores.append(store)
        return store

    def test_latest_result_replaces_previous_result_for_the_same_scope(self) -> None:
        store = self.make_store()

        first = store.upsert(
            user_id=7,
            analysis_type="sop",
            document_ids=[12],
            result={"title": "first"},
            job_id="job-1",
        )
        second = store.upsert(
            user_id=7,
            analysis_type="sop",
            document_ids=[12],
            result={"title": "second"},
            job_id="job-2",
        )

        self.assertEqual(first.id, second.id)
        self.assertEqual(second.result, {"title": "second"})
        self.assertEqual(second.job_id, "job-2")
        self.assertEqual(len(store.list_for_document(7, 12)), 1)

    def test_results_and_assets_are_user_scoped(self) -> None:
        store = self.make_store()
        store.upsert(1, "translation", [5], {"segments": []})
        asset_path = Path(self.tmp.name) / "asset.png"
        asset_path.write_bytes(b"\x89PNG\r\n\x1a\n")
        asset = store.register_asset(
            user_id=1,
            document_id=5,
            analysis_type="structures",
            path=asset_path,
            file_name="structure.png",
            mime_type="image/png",
        )

        self.assertEqual(len(store.list_for_document(1, 5)), 1)
        self.assertEqual(store.list_for_document(2, 5), [])
        self.assertIsNotNone(store.get_asset_for_user(1, 5, asset.id))
        self.assertIsNone(store.get_asset_for_user(2, 5, asset.id))

    def test_comparison_scope_is_independent_of_document_order(self) -> None:
        store = self.make_store()
        created = store.upsert(3, "comparison", [9, 4, 7], {"markdown": "result"})

        loaded = store.get_comparison(3, [7, 9, 4])

        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.id, created.id)
        self.assertEqual(loaded.document_ids, [4, 7, 9])

    def test_deleting_a_document_removes_comparisons_and_returns_asset_paths(self) -> None:
        store = self.make_store()
        store.upsert(1, "safety", [5], {"chemicals": []})
        store.upsert(1, "comparison", [5, 6], {"markdown": "result"})
        asset_path = Path(self.tmp.name) / "image.png"
        asset_path.write_bytes(b"\x89PNG\r\n\x1a\n")
        store.register_asset(1, 5, "images", asset_path, "image.png", "image/png")

        paths = store.delete_for_document(1, 5)

        self.assertEqual(paths, [asset_path.resolve()])
        self.assertEqual(store.list_for_document(1, 5), [])
        self.assertIsNone(store.get_comparison(1, [5, 6]))


if __name__ == "__main__":
    unittest.main()
