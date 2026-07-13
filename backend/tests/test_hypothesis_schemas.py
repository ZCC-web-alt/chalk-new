from __future__ import annotations

import sys
import unittest
from pathlib import Path

from pydantic import ValidationError


BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))


class HypothesisSchemaTestCase(unittest.TestCase):
    def test_generation_payload_applies_web_defaults(self) -> None:
        from app.schemas.jobs import HypothesisGeneratePayload

        payload = HypothesisGeneratePayload.model_validate({"researchQuestion": "What should be tested?"})

        self.assertEqual(payload.max_iterations, 2)
        self.assertTrue(payload.hitl_enabled)
        self.assertTrue(payload.auto_verify)
        self.assertEqual(payload.source_doc_ids, [])

    def test_generation_payload_rejects_more_than_twenty_documents_and_empty_input(self) -> None:
        from app.schemas.jobs import HypothesisGeneratePayload

        with self.assertRaises(ValidationError):
            HypothesisGeneratePayload.model_validate({"sourceDocIds": list(range(1, 22))})
        with self.assertRaises(ValidationError):
            HypothesisGeneratePayload.model_validate({})

    def test_generation_payload_normalizes_unique_document_ids_and_legacy_text_alias(self) -> None:
        from app.schemas.jobs import HypothesisGeneratePayload

        payload = HypothesisGeneratePayload.model_validate({
            "literatureText": "manual context",
            "sourceDocIds": [5, 3],
        })

        self.assertEqual(payload.supplemental_context, "manual context")
        self.assertEqual(payload.source_doc_ids, [3, 5])
        self.assertEqual(payload.model_dump(by_alias=True)["supplementalContext"], "manual context")

    def test_feedback_schema_rejects_unknown_criteria_and_skip_with_edits(self) -> None:
        from app.schemas.jobs import HypothesisFeedbackInput

        approved = HypothesisFeedbackInput.model_validate({
            "action": "approve",
            "structuredFeedback": {"citationAuthenticity": "verified"},
        })
        self.assertEqual(approved.action, "approve")

        with self.assertRaises(ValidationError):
            HypothesisFeedbackInput.model_validate({
                "action": "revise",
                "structuredFeedback": {"unexpected": "value"},
            })
        with self.assertRaises(ValidationError):
            HypothesisFeedbackInput.model_validate({
                "action": "skip",
                "editedHypothesis": {"paper_title": "Edited"},
            })

    def test_hypothesis_artifact_jobs_require_positive_hypothesis_id(self) -> None:
        from app.schemas.jobs import HypothesisReportPayload, HypothesisWorkflowExportPayload

        report = HypothesisReportPayload.model_validate({"hypothesisId": 7})
        workflow = HypothesisWorkflowExportPayload.model_validate({"hypothesisId": 7})

        self.assertTrue(report.enhance)
        self.assertEqual(workflow.hypothesis_id, 7)
        with self.assertRaises(ValidationError):
            HypothesisReportPayload.model_validate({"hypothesisId": 0})


if __name__ == "__main__":
    unittest.main()
