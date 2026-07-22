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
