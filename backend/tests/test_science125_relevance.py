from __future__ import annotations

import sys
import unittest
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))


class Science125RelevanceTestCase(unittest.TestCase):
    def test_hybrid_score_separates_relevant_and_unrelated_records(self) -> None:
        from app.services.science125_relevance import assess_science125_relevance
        from app.services.science125_retrieval import EvidenceRecord

        query = "microscopic interfacial phenomena measurement spectroscopy microscopy nanoscale dynamics"
        relevant = EvidenceRecord(
            provider="semantic_scholar",
            stable_id="semantic_scholar:relevant",
            title="Operando spectroscopy and microscopy of nanoscale interfacial dynamics",
            abstract=(
                "We measure molecular-scale interface phenomena with spectroscopy, "
                "microscopy, calibrated imaging, and atomistic validation."
            ),
            doi="10.1000/relevant",
            full_text_url="https://example.test/relevant.pdf",
            access_status="open_full_text",
        )
        unrelated = EvidenceRecord(
            provider="semantic_scholar",
            stable_id="semantic_scholar:unrelated",
            title="A survey of medieval manuscript catalogues",
            abstract="Historical bibliography and archival description methods.",
            doi="10.1000/unrelated",
            access_status="metadata",
        )

        relevant_score = assess_science125_relevance("S125-006", query, relevant)
        unrelated_score = assess_science125_relevance("S125-006", query, unrelated)

        self.assertGreaterEqual(relevant_score.score, 0.75)
        self.assertEqual(relevant_score.label, "high")
        self.assertLess(unrelated_score.score, 0.20)
        self.assertEqual(unrelated_score.label, "very_low")
        self.assertGreater(relevant_score.components["conceptCoverage"], 0.9)
        self.assertIn("界面", relevant_score.matched_concepts)
        self.assertEqual(relevant_score.scoring_version, "science125-relevance-v1")

    def test_missing_abstract_and_full_text_reduce_but_do_not_zero_title_relevance(self) -> None:
        from app.services.science125_relevance import assess_science125_relevance
        from app.services.science125_retrieval import EvidenceRecord

        record = EvidenceRecord(
            provider="crossref",
            stable_id="doi:10.1000/title-only",
            title="Microscopic measurement of interfacial phenomena",
            doi="10.1000/title-only",
            access_status="metadata",
        )

        score = assess_science125_relevance(
            "S125-006",
            "microscopic interfacial phenomena measurement spectroscopy microscopy",
            record,
        )

        self.assertGreater(score.score, 0.30)
        self.assertLess(score.components["evidenceCompleteness"], 0.5)


if __name__ == "__main__":
    unittest.main()
