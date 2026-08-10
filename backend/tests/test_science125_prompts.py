from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import unittest
import sys
import unicodedata
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_DIR.parent
sys.path.insert(0, str(BACKEND_DIR))

PROMPT_REGISTRY_PATH = PROJECT_ROOT / "benchmarks" / "science125" / "science125-prompts-v2.json"
ROUTING_PATH = PROJECT_ROOT / "benchmarks" / "science125" / "science125-routing-v1.json"
GENERATOR_PATH = PROJECT_ROOT / "scripts" / "generate_science125_prompts_v2.py"


def _canonical_json(payload: dict[str, object]) -> str:
    return unicodedata.normalize(
        "NFC",
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
    )


def _content_hash(payload: dict[str, object], field: str) -> str:
    value = copy.deepcopy(payload)
    value.pop(field, None)
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


class Science125PromptRegistryTestCase(unittest.TestCase):
    def test_all_twelve_domain_modules_are_versioned_and_non_empty(self) -> None:
        from app.services.science125_prompts import (
            SCIENCE125_DOMAIN_PROFILES,
            SCIENCE125_PROMPT_VERSION,
        )

        self.assertEqual(SCIENCE125_PROMPT_VERSION, "science125-prompts-v2")
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

    def test_v2_registry_covers_every_routed_question_and_has_stable_hashes(self) -> None:
        from app.services.science125_prompts import load_science125_prompt_registry

        raw = json.loads(PROMPT_REGISTRY_PATH.read_text(encoding="utf-8"))
        routing = json.loads(ROUTING_PATH.read_text(encoding="utf-8"))
        registry = load_science125_prompt_registry()

        self.assertEqual(raw["promptVersion"], "science125-prompts-v2")
        self.assertEqual(raw["baseManifestContentSha256"], routing["baseManifestContentSha256"])
        self.assertEqual(raw["routingContentSha256"], routing["routingContentSha256"])
        self.assertEqual(raw["registryContentSha256"], _content_hash(raw, "registryContentSha256"))
        self.assertEqual(registry.registry_content_sha256, raw["registryContentSha256"])
        self.assertEqual(
            [item.question_id for item in registry.question_modules],
            [f"S125-{number:03d}" for number in range(1, 126)],
        )
        self.assertEqual(len({item.question_id for item in registry.question_modules}), 125)

        routing_by_id = {item["questionId"]: item for item in routing["questions"]}
        required_fields = (
            "research_objective",
            "required_concepts",
            "evidence_requirements",
            "variables_and_observables",
            "comparators_and_controls",
            "discriminating_tests",
            "negative_evidence_and_failure_modes",
            "scope_boundaries",
            "forbidden_inferences",
        )
        allowed_checks = {
            "source_traceability",
            "negative_evidence",
            "measurement_plan",
            "operational_definition",
            "uncertainty_budget",
            "selection_effects",
            "replication",
            "safety_boundary",
            "data_leakage",
            "applicability_boundary",
        }
        for item, raw_item in zip(registry.question_modules, raw["questionModules"], strict=True):
            with self.subTest(question_id=item.question_id):
                self.assertEqual(item.primary_subdomain, routing_by_id[item.question_id]["primarySubdomain"])
                self.assertTrue(all(getattr(item, field).strip() for field in required_fields))
                self.assertTrue(item.required_domain_checks)
                self.assertLessEqual(set(item.required_domain_checks), allowed_checks)
                self.assertEqual(item.review_status, "draft_pending_review")
                self.assertEqual(raw_item["moduleSha256"], _content_hash(raw_item, "moduleSha256"))

    def test_committed_v2_registry_has_no_generator_drift(self) -> None:
        spec = importlib.util.spec_from_file_location("science125_prompt_generator", GENERATOR_PATH)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)  # type: ignore[union-attr]
        module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
        spec.loader.exec_module(module)  # type: ignore[union-attr]

        committed = json.loads(PROMPT_REGISTRY_PATH.read_text(encoding="utf-8"))
        generated = module.build_registry()

        self.assertEqual(_canonical_json(committed), _canonical_json(generated))

    def test_prompt_snapshot_resolves_current_question_without_exposing_other_modules(self) -> None:
        from app.services.science125_prompts import resolve_science125_prompt_snapshot

        routing = json.loads(ROUTING_PATH.read_text(encoding="utf-8"))["questions"]
        route = next(item for item in routing if item["questionId"] == "S125-054")
        snapshot = resolve_science125_prompt_snapshot(
            question_id="S125-054",
            routing=route,
        )

        self.assertEqual(snapshot.prompt_version, "science125-prompts-v2")
        self.assertRegex(snapshot.registry_hash, r"^[0-9a-f]{64}$")
        self.assertRegex(snapshot.question_module_hash, r"^[0-9a-f]{64}$")
        self.assertEqual(snapshot.review_status, "draft_pending_review")
        self.assertIn("cosmic rays", snapshot.rendered_question_module.lower())
        self.assertNotIn("Riemann", snapshot.rendered_question_module)

    def test_question_modules_are_constraints_not_answers_or_citations(self) -> None:
        raw = json.loads(PROMPT_REGISTRY_PATH.read_text(encoding="utf-8"))
        serialized = json.dumps(raw["questionModules"], ensure_ascii=False).casefold()

        self.assertNotIn("doi:", serialized)
        self.assertNotIn("pmid:", serialized)
        self.assertNotIn("arxiv:", serialized)
        self.assertNotIn("the answer is", serialized)
        self.assertNotIn("we prove that", serialized)

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
        self.assertLess(prompt.index("DOMAIN MODULE"), prompt.index("QUESTION MODULE"))
        self.assertLess(prompt.index("QUESTION MODULE"), prompt.index("METHOD MODULES"))
        self.assertIn("microscopic level", prompt)

    def test_v1_prompt_remains_available_without_a_question_module(self) -> None:
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
            prompt_version="science125-prompts-v1",
        )

        self.assertIn('"promptVersion":"science125-prompts-v1"', prompt)
        self.assertNotIn("QUESTION MODULE", prompt)

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
