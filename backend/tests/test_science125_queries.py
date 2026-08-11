from __future__ import annotations

import sys
import unittest
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))


class Science125QueryExpansionTestCase(unittest.TestCase):
    def test_interface_profile_builds_bounded_domain_specific_refinements(self) -> None:
        from app.services.science125_queries import build_science125_refinement_queries

        queries = build_science125_refinement_queries(
            "chem_interface_v1",
            "interfacial chemistry microscopic measurement",
        )

        self.assertEqual(len(queries), 2)
        self.assertEqual(len(set(queries)), 2)
        self.assertTrue(any("operando" in query for query in queries))
        self.assertTrue(any("molecular dynamics" in query for query in queries))
        self.assertTrue(all(len(query) <= 500 for query in queries))

    def test_unknown_profile_does_not_guess_refinements(self) -> None:
        from app.services.science125_queries import build_science125_refinement_queries

        self.assertEqual(build_science125_refinement_queries("unknown", "query"), ())

    def test_preproduction_subdomain_builds_server_controlled_refinements(self) -> None:
        from app.services.science125_queries import build_science125_refinement_queries

        queries = build_science125_refinement_queries(
            "default",
            "color pigments discover possible create colors",
            primary_subdomain="chem.colorant_materials",
        )

        self.assertEqual(len(queries), 2)
        self.assertTrue(any("chromophore" in query for query in queries))
        self.assertTrue(any("solid-state synthesis" in query for query in queries))


if __name__ == "__main__":
    unittest.main()
