from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

from pydantic import ValidationError


PROJECT_DIR = Path(__file__).resolve().parents[2]
BACKEND_DIR = PROJECT_DIR / "backend"
sys.path.insert(0, str(BACKEND_DIR))


def candidate(hypothesis_id: str) -> dict[str, object]:
    return {
        "id": hypothesis_id,
        "title": f"Candidate {hypothesis_id}",
        "statement": "A measurable intervention changes the observed outcome.",
        "mechanism": "The intervention changes an intermediate state.",
        "prerequisites": ["The intermediate can be measured."],
        "predictions": [
            {
                "id": f"P-{hypothesis_id}",
                "observable": "The response increases relative to the control.",
                "expectedDirection": "increase",
                "measurement": "A calibrated assay",
                "falsificationThreshold": "No increase within the 95% confidence interval",
            }
        ],
        "supportingEvidenceRefs": ["E1"],
        "counterEvidence": ["A competing mechanism can produce the same response."],
        "evidenceGaps": ["The intermediate has not been observed directly."],
        "falsificationCriteria": ["The intervention and control are indistinguishable."],
        "confidence": 0.62,
        "status": "candidate",
    }


def valid_contract(candidate_count: int = 3) -> dict[str, object]:
    return {
        "contractVersion": "research-v1",
        "profile": "general_science",
        "brief": {
            "researchQuestion": "Does the intervention alter the outcome?",
            "background": "Prior work suggests a causal relationship.",
            "objectives": ["Test the causal relationship."],
            "scope": "Controlled laboratory conditions",
        },
        "hypotheses": [candidate(f"H{index}") for index in range(1, candidate_count + 1)],
        "nullHypothesis": {
            **candidate("H0"),
            "title": "Null hypothesis",
            "statement": "The intervention does not alter the outcome.",
            "status": "null",
        },
        "evidenceClaims": [
            {
                "id": "E1",
                "claim": "Prior observations are consistent with the mechanism.",
                "stance": "supports",
                "sourceRefs": ["doi:10.0000/example"],
                "strength": 0.7,
                "limitations": ["Observational evidence only."],
            }
        ],
        "researchPlan": {
            "independentVariables": ["Intervention level"],
            "dependentVariables": ["Measured response"],
            "controlVariables": ["Temperature"],
            "measurements": [
                {
                    "variable": "Measured response",
                    "method": "Calibrated assay",
                    "unit": "relative units",
                    "schedule": "At the predefined endpoint",
                }
            ],
            "decisionThresholds": ["Reject H0 when adjusted p < 0.05 and effect size exceeds 0.2."],
            "stopConditions": ["Stop for a predefined safety event."],
            "resources": ["Assay instrumentation"],
            "risks": ["Measurement drift"],
            "uncertainties": ["Unknown confounders"],
            "applicabilityBoundaries": ["Laboratory conditions only"],
        },
        "quality": {
            "factualAccuracy": 0.8,
            "explainability": 0.8,
            "completeness": 0.8,
            "technicalDepth": 0.7,
            "applicability": 0.7,
            "overall": 0.76,
            "notes": ["Requires expert review."],
        },
        "provenance": {
            "provider": "DashScope",
            "model": "qwen-plus",
            "requestId": "request-redacted",
            "generatedAt": "2026-07-13T10:00:00Z",
            "promptHash": "a" * 64,
            "responseHash": "b" * 64,
        },
    }


class ResearchContractTestCase(unittest.TestCase):
    def test_accepts_three_and_five_sequential_candidates_and_serializes_camel_case(self) -> None:
        from app.schemas.research import ResearchOutput

        for count in (3, 5):
            output = ResearchOutput.model_validate(valid_contract(count))
            encoded = output.model_dump(mode="json", by_alias=True)

            self.assertEqual([item["id"] for item in encoded["hypotheses"]], [f"H{i}" for i in range(1, count + 1)])
            self.assertEqual(encoded["nullHypothesis"]["id"], "H0")
            self.assertIn("researchQuestion", encoded["brief"])
            self.assertNotIn("research_question", encoded["brief"])

    def test_rejects_candidate_count_outside_three_to_five(self) -> None:
        from app.schemas.research import ResearchOutput

        for count in (2, 6):
            with self.subTest(count=count), self.assertRaises(ValidationError):
                ResearchOutput.model_validate(valid_contract(count))

    def test_rejects_nonsequential_candidate_ids_and_missing_null_hypothesis(self) -> None:
        from app.schemas.research import ResearchOutput

        nonsequential = valid_contract()
        nonsequential["hypotheses"][1]["id"] = "H3"  # type: ignore[index]
        with self.assertRaises(ValidationError):
            ResearchOutput.model_validate(nonsequential)

        missing_h0 = valid_contract()
        del missing_h0["nullHypothesis"]
        with self.assertRaises(ValidationError):
            ResearchOutput.model_validate(missing_h0)

    def test_rejects_unknown_profile_and_requires_chemistry_subdomain_only_for_chemistry(self) -> None:
        from app.schemas.research import ResearchOutput

        unknown = valid_contract()
        unknown["profile"] = "astronomy"
        with self.assertRaises(ValidationError):
            ResearchOutput.model_validate(unknown)

        missing_subdomain = valid_contract()
        missing_subdomain["profile"] = "chemistry"
        with self.assertRaises(ValidationError):
            ResearchOutput.model_validate(missing_subdomain)

        chemistry = valid_contract()
        chemistry["profile"] = "chemistry"
        chemistry["chemistrySubdomain"] = "electrocatalysis"
        self.assertEqual(
            ResearchOutput.model_validate(chemistry).chemistry_subdomain,
            "electrocatalysis",
        )

        polluted_general = valid_contract()
        polluted_general["chemistrySubdomain"] = "electrocatalysis"
        with self.assertRaises(ValidationError):
            ResearchOutput.model_validate(polluted_general)

    def test_feedback_and_change_set_validate_json_pointer_paths(self) -> None:
        from app.schemas.research import ResultFeedback, RoundChangeSet

        feedback = ResultFeedback.model_validate(
            {
                "source": {"type": "csv", "reference": "upload:run-1"},
                "observations": ["The observed effect was below threshold."],
                "dataQuality": "medium",
                "verdict": "refutes",
                "reason": "The primary prediction was not observed.",
                "affectedPaths": ["/hypotheses/0/mechanism"],
            }
        )
        self.assertEqual(feedback.affected_paths, ["/hypotheses/0/mechanism"])

        change_set = RoundChangeSet.model_validate(
            {
                "fromRound": 1,
                "toRound": 2,
                "changes": [
                    {
                        "evidenceRef": "upload:run-1",
                        "path": "/researchPlan/decisionThresholds/0",
                        "before": "effect > 0.2",
                        "after": "effect > 0.3",
                        "reason": "The uploaded result exposed measurement noise.",
                    }
                ],
            }
        )
        self.assertEqual(change_set.changes[0].path, "/researchPlan/decisionThresholds/0")

        for invalid_path in ("hypotheses/0/mechanism", "", "/hypotheses/~2/mechanism"):
            invalid = {
                "source": {"type": "manual_observation", "reference": "note:1"},
                "observations": ["Observation"],
                "dataQuality": "low",
                "verdict": "insufficient_evidence",
                "reason": "More data are required.",
                "affectedPaths": [invalid_path],
            }
            with self.subTest(path=invalid_path), self.assertRaises(ValidationError):
                ResultFeedback.model_validate(invalid)

            invalid_change = {
                "fromRound": 1,
                "toRound": 2,
                "changes": [
                    {
                        "evidenceRef": "note:1",
                        "path": invalid_path,
                        "before": "old",
                        "after": "new",
                        "reason": "Observation changed the plan.",
                    }
                ],
            }
            with self.subTest(change_path=invalid_path), self.assertRaises(ValidationError):
                RoundChangeSet.model_validate(invalid_change)

    def test_evidence_claim_ids_are_unique_and_hypothesis_references_resolve(self) -> None:
        from app.schemas.research import ResearchOutput

        duplicate_claim = valid_contract()
        duplicate_claim["evidenceClaims"].append(  # type: ignore[union-attr]
            dict(duplicate_claim["evidenceClaims"][0])  # type: ignore[index]
        )
        with self.assertRaises(ValidationError):
            ResearchOutput.model_validate(duplicate_claim)

        dangling_reference = valid_contract()
        dangling_reference["hypotheses"][0]["supportingEvidenceRefs"] = ["E404"]  # type: ignore[index]
        with self.assertRaises(ValidationError):
            ResearchOutput.model_validate(dangling_reference)

    def test_generated_json_schema_and_readonly_types_are_current(self) -> None:
        result = subprocess.run(
            [sys.executable, str(PROJECT_DIR / "scripts" / "generate_research_contract.py"), "--check"],
            cwd=PROJECT_DIR,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        json_schema_path = PROJECT_DIR / "docs" / "contracts" / "research-v1.schema.json"
        types_path = PROJECT_DIR / "frontend" / "lib" / "api" / "generated" / "research-v1.ts"
        schema = json.loads(json_schema_path.read_text(encoding="utf-8"))
        types = types_path.read_text(encoding="utf-8")

        self.assertEqual(schema["title"], "ResearchOutput")
        self.assertEqual(schema["properties"]["contractVersion"]["const"], "research-v1")
        self.assertIn("ResultFeedback", schema["$defs"])
        self.assertIn("RoundChangeSet", schema["$defs"])
        self.assertEqual(
            schema["$defs"]["RoundChange"]["properties"]["path"]["pattern"],
            r"^/(?:[^~]|~[01])*$",
        )
        self.assertEqual(len(schema["properties"]["hypotheses"]["prefixItems"]), 5)
        self.assertEqual(len(schema["allOf"]), 2)
        self.assertIn("export type ResearchOutput", types)
        self.assertIn("export type ResultFeedback", types)
        self.assertIn("export type RoundChangeSet", types)
        self.assertIn("readonly contractVersion", types)
        self.assertIn("ReadonlyArray", types)
        self.assertIn("export type CandidateHypothesisTuple =", types)
        self.assertIn('readonly profile: "general_science"', types)
        self.assertIn('readonly profile: "chemistry"', types)
        self.assertIn("readonly chemistrySubdomain: string", types)
        self.assertIn('CandidateHypothesisAt<"H5">', types)
        self.assertTrue(types.startswith("// Generated by scripts/generate_research_contract.py; do not edit.\n"))


if __name__ == "__main__":
    unittest.main()
