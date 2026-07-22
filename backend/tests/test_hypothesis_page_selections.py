from __future__ import annotations

import sys
import unittest
from pathlib import Path

from pydantic import ValidationError


BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))


class HypothesisPageSelectionSchemaTestCase(unittest.TestCase):
    def test_science125_accepts_a_hash_bound_page_selection(self) -> None:
        from app.schemas.jobs import HypothesisGeneratePayload

        payload = HypothesisGeneratePayload.model_validate({
            "researchQuestion": "How can we measure interface phenomena on the microscopic level?",
            "science125Id": "S125-006",
            "sourcePageSelections": [{
                "documentId": 17,
                "pages": [2, 4, 5],
                "pdfSha256": "a" * 64,
                "textSha256": "b" * 64,
                "maxChars": 12000,
            }],
        })

        self.assertEqual(payload.source_page_selections[0].document_id, 17)
        self.assertEqual(payload.source_page_selections[0].pages, [2, 4, 5])

    def test_page_selection_is_science125_only_and_rejects_unstable_inputs(self) -> None:
        from app.schemas.jobs import HypothesisGeneratePayload

        invalid_payloads = [
            {
                "researchQuestion": "legacy chemistry question",
                "sourcePageSelections": [{
                    "documentId": 17,
                    "pages": [2],
                    "pdfSha256": "a" * 64,
                    "textSha256": "b" * 64,
                    "maxChars": 12000,
                }],
            },
            {
                "researchQuestion": "Science question",
                "science125Id": "S125-006",
                "sourcePageSelections": [{
                    "documentId": 17,
                    "pages": [2, 2],
                    "pdfSha256": "a" * 64,
                    "textSha256": "b" * 64,
                    "maxChars": 12000,
                }],
            },
            {
                "researchQuestion": "Science question",
                "science125Id": "S125-006",
                "sourcePageSelections": [{
                    "documentId": 17,
                    "pages": [2],
                    "pdfSha256": "not-a-hash",
                    "textSha256": "b" * 64,
                    "maxChars": 12000,
                }],
            },
        ]
        for raw in invalid_payloads:
            with self.subTest(raw=raw), self.assertRaises(ValidationError):
                HypothesisGeneratePayload.model_validate(raw)


if __name__ == "__main__":
    unittest.main()
