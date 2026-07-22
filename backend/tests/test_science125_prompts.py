from __future__ import annotations

import unittest
import sys
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))


class Science125PromptRegistryTestCase(unittest.TestCase):
    def test_all_twelve_domain_modules_are_versioned_and_non_empty(self) -> None:
        from app.services.science125_prompts import (
            SCIENCE125_DOMAIN_PROFILES,
            SCIENCE125_PROMPT_VERSION,
        )

        self.assertEqual(SCIENCE125_PROMPT_VERSION, "science125-prompts-v1")
        self.assertEqual(set(SCIENCE125_DOMAIN_PROFILES), {
            "Mathematical Sciences",
            "Chemistry",
            "Medicine & Health",
            "Biology",
            "Astronomy",
            "Physics",
            "Engineering & Materials Science",
            "Information Science",
            "Neuroscience",
            "Ecology",
            "Energy Science",
            "Artificial Intelligence",
        })
        self.assertTrue(all(text.strip() for text in SCIENCE125_DOMAIN_PROFILES.values()))

    def test_domain_modules_preserve_the_required_scientific_guardrails(self) -> None:
        from app.services.science125_prompts import SCIENCE125_DOMAIN_PROFILES

        required_terms = {
            "Mathematical Sciences": ("theorems", "definitions", "boundary", "counterexamples", "formal verification"),
            "Chemistry": ("reaction conditions", "structural characterization", "thermodynamics", "kinetics", "measured"),
            "Medicine & Health": ("guidelines", "systematic reviews", "RCT", "cohort", "PICO", "ethics", "safety endpoints"),
            "Biology": ("functional perturbation", "multi-level", "genetic", "evolution", "cross-species"),
            "Astronomy": ("calibrated", "selection", "multi-wavelength", "multi-messenger", "sensitivity limits"),
            "Physics": ("conserved quantities", "units", "calibration", "uncertainty budgets", "applicability domain"),
            "Engineering & Materials Science": ("common baselines", "lifetime", "reliability", "manufacturability", "cost", "safety"),
            "Information Science": ("Version data", "baselines", "leakage", "ablations", "external reproducibility", "compute"),
            "Neuroscience": ("neural and behavioral endpoints", "preregister", "causal perturbation", "reverse inference"),
            "Ecology": ("spatial", "temporal", "detectability", "environmental covariates", "local"),
            "Energy Science": ("energy and mass conservation", "efficiency", "lifetime", "lifecycle", "safety"),
            "Artificial Intelligence": ("held-out", "robustness", "calibration", "fairness", "energy use", "operationally", "language performance"),
        }

        for domain, terms in required_terms.items():
            with self.subTest(domain=domain):
                text = SCIENCE125_DOMAIN_PROFILES[domain].casefold()
                for term in terms:
                    self.assertIn(term.casefold(), text)

    def test_prompt_composes_authoritative_context_route_and_reviewed_evidence(self) -> None:
        from app.services.science125_prompts import compose_science125_prompt

        prompt = compose_science125_prompt(
            question_id="S125-006",
            question="How can we measure interface phenomena on the microscopic level?",
            source_context="The booklet context is authoritative and hash verified.",
            routing={
                "benchmarkDomain": "Chemistry",
                "primarySubdomain": "chem.interface",
                "crossDomainTags": ["physics", "materials"],
                "methodProfile": {"primary": "experimental", "secondary": ["computational"]},
                "promptProfile": "s125.chemistry.v1",
                "retrievalProfile": "retrieval.chem.interface.v1",
            },
            evidence_records=(
                {
                    "stableId": "doi:10.1000/example",
                    "title": "Interface measurement",
                    "abstract": "A reviewed abstract.",
                    "provider": "crossref",
                    "accessStatus": "metadata_only",
                },
            ),
            schema_text="{\"title\":\"ResearchOutput\"}",
        )

        self.assertIn("S125-006", prompt)
        self.assertIn("The booklet context is authoritative", prompt)
        self.assertIn("doi:10.1000/example", prompt)
        self.assertIn("chem.interface", prompt)
        self.assertIn("Do not reuse a chemistry legacy prompt", prompt)
        self.assertIn("Return JSON only", prompt)

    def test_non_chemistry_prompt_does_not_inherit_electrocatalysis_language(self) -> None:
        from app.services.science125_prompts import compose_science125_prompt

        prompt = compose_science125_prompt(
            question_id="S125-054",
            question="What is the origin of cosmic rays?",
            source_context="A hash verified astronomy context.",
            routing={
                "benchmarkDomain": "Astronomy",
                "primarySubdomain": "astro.cosmic_rays",
                "crossDomainTags": ["physics"],
                "methodProfile": {"primary": "observational", "secondary": ["computational"]},
                "promptProfile": "s125.astronomy.v1",
                "retrievalProfile": "retrieval.astro.high_energy.v1",
            },
            evidence_records=(),
            schema_text="{}",
        )

        self.assertNotIn("electrocatalysis", prompt.lower())
        self.assertNotIn("catalyst system", prompt.lower())
        self.assertIn("selection effects", prompt.lower())


if __name__ == "__main__":
    unittest.main()
