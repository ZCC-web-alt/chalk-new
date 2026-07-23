from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))


class Science125CatalogTestCase(unittest.TestCase):
    def test_loader_returns_the_authoritative_125_question_contract(self) -> None:
        from app.services.science125_catalog import load_science125_catalog

        catalog = load_science125_catalog()

        self.assertEqual(catalog.manifest_version, "science125-v1")
        self.assertEqual(len(catalog.data), 125)
        self.assertEqual(catalog.data[0].id, "S125-001")
        self.assertEqual(catalog.data[-1].id, "S125-125")
        self.assertTrue(all(item.question_zh for item in catalog.data))
        self.assertEqual(catalog.data[0].question_zh, "素数为何如此特殊？")
        self.assertEqual(catalog.data[-1].question_zh, "量子人工智能能模仿人脑吗？")
        pilot = next(item for item in catalog.data if item.id == "S125-006")
        self.assertEqual(pilot.question_zh, "我们如何在微观尺度上测量界面现象？")
        self.assertEqual(pilot.question, "How can we measure interface phenomena on the microscopic level?")

        from app.services.science125_localization import load_science125_localizations

        localizations = load_science125_localizations()
        self.assertEqual(set(localizations), {f"S125-{index:03d}" for index in range(1, 126)})
        self.assertTrue(all(item.search_intent_zh for item in localizations.values()))
        reviewed_ids = {
            question_id
            for question_id, item in localizations.items()
            if item.translation_review_status == "reviewed"
        }
        self.assertEqual(reviewed_ids, {"S125-006", "S125-043", "S125-054"})
        self.assertEqual(
            sum(item.translation_review_status == "translated_pending_review" for item in localizations.values()),
            122,
        )

    def test_loader_rejects_invalid_manifests_without_disclosing_the_path(self) -> None:
        from app.services.science125_catalog import (
            Science125CatalogError,
            load_science125_catalog,
        )

        invalid_payloads = (
            "not-json",
            json.dumps({"manifestVersion": "science125-v1", "questions": []}),
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            private_path = Path(temp_dir) / "private-manifest.json"
            for payload in invalid_payloads:
                with self.subTest(payload=payload[:20]):
                    private_path.write_text(payload, encoding="utf-8")

                    with self.assertRaises(Science125CatalogError) as raised:
                        load_science125_catalog(private_path)

                    self.assertEqual(
                        str(raised.exception),
                        "The Science 125 question catalog is unavailable.",
                    )
                    self.assertNotIn(str(private_path), str(raised.exception))


if __name__ == "__main__":
    unittest.main()
