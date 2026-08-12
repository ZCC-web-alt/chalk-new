from __future__ import annotations

import sys
import unittest
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))


class Science125QueryExpansionTestCase(unittest.TestCase):
    def test_long_question_is_planned_as_short_keyword_queries(self) -> None:
        from app.services.science125_queries import build_science125_query_plan

        original = (
            "How can microscopic interface phenomena be measured under realistic operating "
            "conditions while separating transport, kinetics, and structural changes from "
            "instrument artefacts and computational predictions?"
        )
        plan = build_science125_query_plan(
            "chem_interface_v1",
            original,
            primary_subdomain="chem.interface",
            question_id="S125-006",
        )

        self.assertTrue(plan.topic_summary)
        self.assertGreaterEqual(len(plan.keywords), 6)
        self.assertLessEqual(len(plan.keywords), 12)
        self.assertGreaterEqual(len(plan.queries), 3)
        self.assertLessEqual(len(plan.queries), 4)
        self.assertTrue(all(len(query) <= 180 for query in plan.queries))
        self.assertTrue(all(original.casefold() not in query.casefold() for query in plan.queries))
        self.assertTrue(any("interface" in query.casefold() for query in plan.queries))
        self.assertTrue(any("operando" in query.casefold() for query in plan.queries))

    def test_pilot_profiles_keep_domain_specific_search_concepts(self) -> None:
        from app.services.science125_queries import build_science125_query_plan

        genome = build_science125_query_plan(
            "bio_genome_editing_v1",
            "How can genome editing be used to treat disease?",
            primary_subdomain="bio.genome_editing",
            question_id="S125-043",
        )
        cosmic = build_science125_query_plan(
            "astro_high_energy_v1",
            "Where do cosmic rays come from?",
            primary_subdomain="astro.cosmic_rays",
            question_id="S125-054",
        )

        genome_text = " ".join((*genome.keywords, *genome.queries)).casefold()
        cosmic_text = " ".join((*cosmic.keywords, *cosmic.queries)).casefold()
        for term in ("crispr", "delivery", "off-target", "safety", "clinical trial"):
            self.assertIn(term, genome_text)
        for term in ("cosmic ray", "anisotropy", "gamma ray", "neutrino", "multi-messenger"):
            self.assertIn(term, cosmic_text)

    def test_query_plan_handles_chinese_and_never_falls_back_to_a_long_raw_question(self) -> None:
        from app.services.science125_queries import build_science125_query_plan

        original = "我们如何在微观尺度上测量界面现象，并区分原位光谱观测、输运动力学与计算预测？" * 8
        plan = build_science125_query_plan(
            "chem_interface_v1",
            original,
            primary_subdomain="chem.interface",
            question_id="S125-006",
        )

        self.assertTrue(plan.topic_summary)
        self.assertTrue(plan.queries)
        self.assertTrue(all(len(query) <= 180 for query in plan.queries))
        self.assertTrue(all(query != original for query in plan.queries))
        self.assertIn("interface", " ".join(plan.queries).casefold())

    def test_unknown_profile_uses_a_bounded_keyword_fallback(self) -> None:
        from app.services.science125_queries import build_science125_query_plan

        original = "What evidence could distinguish several competing explanations for a poorly scoped phenomenon?" * 10
        plan = build_science125_query_plan("unknown", original)

        self.assertTrue(plan.queries)
        self.assertTrue(all(0 < len(query) <= 180 for query in plan.queries))
        self.assertTrue(all(query.casefold() != original.casefold() for query in plan.queries))

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
