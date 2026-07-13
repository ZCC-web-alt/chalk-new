from __future__ import annotations

import unittest


class DocumentAnalysisHelperTestCase(unittest.TestCase):
    def test_segment_texts_preserves_all_input_in_order(self) -> None:
        from app.services.document_analysis import segment_texts

        source = ["alpha", "bravo-charlie", "delta"]
        segments = segment_texts(source, max_chars=10)

        self.assertGreater(len(segments), 1)
        self.assertEqual("".join(segments), "\n\n".join(source))
        self.assertTrue(all(len(segment) <= 10 for segment in segments))

    def test_merge_sop_results_deduplicates_and_renumbers_steps(self) -> None:
        from app.services.document_analysis import merge_sop_results

        merged = merge_sop_results([
            {
                "title": "Synthesis",
                "chemicals": [{"name": "Ethanol", "amount": "5 mL", "role": "solvent", "safety": "flammable"}],
                "steps": [{"step": 4, "action": "Stir", "params": "1 h", "safetyNote": ""}],
                "postProcessing": "Filter",
                "characterization": "XRD",
            },
            {
                "title": "",
                "chemicals": [{"name": "ethanol", "amount": "5 mL", "role": "solvent", "safety": "flammable"}],
                "steps": [
                    {"step": 1, "action": "Stir", "params": "1 h", "safetyNote": ""},
                    {"step": 2, "action": "Dry", "params": "60 C", "safetyNote": "ventilate"},
                ],
                "postProcessing": "Filter",
                "characterization": "SEM",
            },
        ])

        self.assertEqual(merged["title"], "Synthesis")
        self.assertEqual(len(merged["chemicals"]), 1)
        self.assertEqual([item["step"] for item in merged["steps"]], [1, 2])
        self.assertEqual(merged["postProcessing"], "Filter")
        self.assertEqual(merged["characterization"], "XRD\n\nSEM")

    def test_merge_reaction_results_deduplicates_reactions(self) -> None:
        from app.services.document_analysis import merge_reaction_results

        reaction = {
            "name": "Hydrogenation",
            "reactants": ["A", "H2"],
            "products": ["B"],
            "catalyst": "Pd/C",
            "solvent": "ethanol",
            "temperature": "25 C",
            "time": "2 h",
            "pressure": "1 bar",
            "ph": "",
            "yield": "90%",
            "workup": "filter",
            "notes": "",
        }
        merged = merge_reaction_results([
            {"summary": "First summary", "reactions": [reaction]},
            {"summary": "Second summary", "reactions": [{**reaction, "name": "hydrogenation"}]},
        ])

        self.assertEqual(len(merged["reactions"]), 1)
        self.assertEqual(merged["summary"], "First summary\n\nSecond summary")

    def test_merge_glossary_prefers_first_non_empty_translation(self) -> None:
        from app.services.document_analysis import merge_glossary

        merged = merge_glossary([
            {"en": "Faradaic efficiency", "zh": "法拉第效率", "note": "FE"},
            {"en": "faradaic efficiency", "zh": "", "note": ""},
            {"en": "overpotential", "zh": "过电位", "note": ""},
        ])

        self.assertEqual(len(merged), 2)
        self.assertEqual(merged[0]["zh"], "法拉第效率")


if __name__ == "__main__":
    unittest.main()
