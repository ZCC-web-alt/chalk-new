from __future__ import annotations

import sys
import unittest
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))


class Science125SearchBindingTestCase(unittest.TestCase):
    def test_presearch_query_may_use_context_keywords_while_the_item_id_stays_bound(self) -> None:
        from app.api.routers.jobs import _validate_science125_binding

        _validate_science125_binding("literature_search", {
            "science125Id": "S125-006",
            "queryText": "microscopic interface interfacial chemistry nanoscale optical measurement",
        })

    def test_hypothesis_question_still_requires_the_authoritative_headline(self) -> None:
        from app.api.routers.jobs import _validate_science125_binding
        from app.core.errors import ApiError

        with self.assertRaises(ApiError):
            _validate_science125_binding("hypothesis_generate", {
                "science125Id": "S125-006",
                "researchQuestion": "microscopic interface keywords",
            })

    def test_authoritative_headline_cannot_bypass_the_science125_gate_by_omitting_the_id(self) -> None:
        from app.api.routers.jobs import _validate_science125_binding
        from app.core.errors import ApiError

        with self.assertRaises(ApiError) as raised:
            _validate_science125_binding("hypothesis_generate", {
                "researchQuestion": "HOW CAN WE MEASURE INTERFACE PHENOMENA ON THE MICROSCOPIC LEVEL",
            })

        self.assertEqual(raised.exception.code, "SCIENCE125_ID_REQUIRED")


if __name__ == "__main__":
    unittest.main()
