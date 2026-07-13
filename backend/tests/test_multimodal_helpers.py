from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.services.multimodal_analysis import (
    select_multimodal_literature_context,
    workbook_coverage,
)
from app.services.modeling_generation import parse_structure_content


class MultimodalHelperTestCase(unittest.TestCase):
    def test_workbook_coverage_distinguishes_full_statistics_from_model_sample(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "large.csv"
            path.write_text(
                "sample,value\n" + "".join(f"S{index},{index}\n" for index in range(250)),
                encoding="utf-8",
            )

            coverage = workbook_coverage(path, ["CSV"])[0]

        self.assertEqual(coverage["rowsRead"], 250)
        self.assertEqual(coverage["rowsAnalyzed"], 250)
        self.assertEqual(coverage["rowsSentToModel"], 100)
        self.assertEqual(coverage["strategy"], "full-statistics-first-100-rows-for-model")

    def test_literature_context_ranks_segments_across_the_full_document(self) -> None:
        text = (
            "Abstract without relevant details. ".ljust(80, "a")
            + "Background only. ".ljust(80, "b")
            + "Experimental Raman peak calibration and acquisition method. ".ljust(80, "c")
            + "Conclusion. ".ljust(80, "d")
        )

        context, coverage = select_multimodal_literature_context(
            text,
            "How was the Raman peak calibrated?",
            segment_chars=80,
            max_chars=160,
        )

        self.assertIn("Raman peak calibration", context)
        self.assertIn(2, coverage["selectedSegmentIndices"])
        self.assertEqual(coverage["sourceSegmentCount"], 4)
        self.assertTrue(coverage["truncated"])

    def test_structure_preview_rejects_excessive_atom_counts(self) -> None:
        coordinates = "".join("0 0 0\n" for _ in range(5001))
        content = (
            "Oversized\n1.0\n10 0 0\n0 10 0\n0 0 10\nH\n5001\nDirect\n"
            + coordinates
        )

        with self.assertRaisesRegex(ValueError, "too many atoms"):
            parse_structure_content(content, "poscar")


if __name__ == "__main__":
    unittest.main()
