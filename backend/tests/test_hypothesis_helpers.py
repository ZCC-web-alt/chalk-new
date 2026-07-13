from __future__ import annotations

import sys
import unittest
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
ROOT_DIR = BACKEND_DIR.parents[1]
sys.path.insert(0, str(BACKEND_DIR))
sys.path.insert(0, str(ROOT_DIR / "src"))
sys.path.insert(0, str(ROOT_DIR))


class HypothesisHelperTestCase(unittest.TestCase):
    def test_balanced_context_includes_all_twenty_documents_and_reports_truncation(self) -> None:
        from app.services.hypotheses import HypothesisSourceDocument, build_hypothesis_input

        documents = [
            HypothesisSourceDocument(id=index, title=f"Paper {index}", source_type="pdf", text="x" * 5000)
            for index in range(1, 21)
        ]

        context, metadata = build_hypothesis_input(documents, "y" * 40000)

        self.assertLessEqual(len(context), 100000)
        self.assertEqual(len(metadata), 20)
        self.assertTrue(all(item["includedChars"] > 0 for item in metadata))
        self.assertTrue(all(item["truncated"] for item in metadata))
        self.assertIn("Paper 20", context)
        self.assertIn("y" * 100, context)

    def test_public_data_removes_paths_and_converts_keys_to_camel_case(self) -> None:
        from app.services.hypotheses import sanitize_public_data

        value = {
            "paper_title": "Result",
            "_scientific_toolkit": {
                "generated_structures": [{"path": r"D:\\private\\structure.cif", "parse_status": "ok"}],
                "warning": r"Generated under D:\\private\\run",
            },
            "web_report_path": r"D:\\private\\report.html",
        }

        public = sanitize_public_data(value, private_roots=[Path(r"D:\\private")])

        self.assertEqual(public["paperTitle"], "Result")
        self.assertNotIn("webReportPath", public)
        structure = public["scientificToolkit"]["generatedStructures"][0]
        self.assertNotIn("path", structure)
        self.assertEqual(structure["parseStatus"], "ok")
        self.assertNotIn(r"D:\\private", str(public))

    def test_report_html_gets_a_restrictive_csp_and_redacts_private_roots(self) -> None:
        from app.services.hypotheses import secure_report_html

        html = '<html><head></head><body>D:\\private\\report.html</body></html>'
        secured = secure_report_html(html, private_roots=[Path(r"D:\\private")])

        self.assertIn("Content-Security-Policy", secured)
        self.assertIn("sandbox allow-scripts allow-downloads", secured)
        self.assertNotIn(r"D:\\private", secured)

    def test_public_data_redacts_unmanaged_absolute_paths_inside_text(self) -> None:
        from app.services.hypotheses import sanitize_public_data

        public = sanitize_public_data({
            "warning": r"Generated at C:\\external\\secret\\file.json",
            "unix_warning": "Generated at /srv/chalk/private/file.json",
        })

        self.assertNotIn("C:\\external", public["warning"])
        self.assertNotIn("/srv/chalk", public["unixWarning"])


if __name__ == "__main__":
    unittest.main()
