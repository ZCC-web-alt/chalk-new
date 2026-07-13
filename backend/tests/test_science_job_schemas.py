from __future__ import annotations

import unittest
from pathlib import Path
import sys

from pydantic import ValidationError

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.schemas.jobs import JobCreate


class ScienceJobSchemaTestCase(unittest.TestCase):
    def test_multimodal_payload_normalizes_sources_and_limits_sheet_selection(self) -> None:
        payload = JobCreate.model_validate({
            "type": "multimodal_analyze",
            "payload": {
                "sources": [
                    {"sourceType": "upload", "assetId": "asset-1", "sheetNames": ["A", "B"]},
                    {"sourceType": "documentAsset", "documentId": 4, "assetId": "asset-2"},
                ],
                "question": "  Analyze the peaks  ",
                "useLiteratureContext": True,
            },
        }).validated_payload()

        self.assertEqual(payload["question"], "Analyze the peaks")
        self.assertEqual(payload["sources"][0]["sheetNames"], ["A", "B"])
        with self.assertRaises(ValidationError):
            JobCreate.model_validate({
                "type": "multimodal_analyze",
                "payload": {
                    "sources": [{
                        "sourceType": "upload",
                        "assetId": "asset-1",
                        "sheetNames": [str(index) for index in range(11)],
                    }],
                },
            }).validated_payload()

    def test_modeling_payload_uses_discriminated_source_and_strict_options(self) -> None:
        payload = JobCreate.model_validate({
            "type": "modeling_generate",
            "payload": {
                "source": {"type": "document", "documentId": 9},
                "mode": "both",
                "calcType": "relax",
                "vaspkitTask": "geometry_optimization",
            },
        }).validated_payload()

        self.assertEqual(payload["source"]["documentId"], 9)
        with self.assertRaises(ValidationError):
            JobCreate.model_validate({
                "type": "modeling_generate",
                "payload": {
                    "source": {"type": "manual", "text": "x" * 60001},
                    "mode": "vasp",
                },
            }).validated_payload()

    def test_hypothesis_payload_accepts_unique_multimodal_run_ids(self) -> None:
        payload = JobCreate.model_validate({
            "type": "hypothesis_generate",
            "payload": {
                "researchQuestion": "Question",
                "multimodalRunIds": ["run-a", "run-b"],
            },
        }).validated_payload()
        self.assertEqual(payload["multimodalRunIds"], ["run-a", "run-b"])

        with self.assertRaises(ValidationError):
            JobCreate.model_validate({
                "type": "hypothesis_generate",
                "payload": {
                    "researchQuestion": "Question",
                    "multimodalRunIds": ["run-a", "run-a"],
                },
            }).validated_payload()


if __name__ == "__main__":
    unittest.main()
