from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from test_research_contract import valid_contract


class ScienceCanaryTestCase(unittest.TestCase):
    @staticmethod
    def canary_contract(candidate_count: int) -> dict:
        payload = valid_contract(candidate_count)
        for claim in payload["evidenceClaims"]:
            claim["sourceRefs"] = ["doi:10.1000/canary-1"]
        return payload

    @staticmethod
    def canary_item() -> dict:
        return {
            "id": "S125-006",
            "question": "How can we measure interface phenomena on the microscopic level?",
            "evidenceRecords": [
                {"stableId": "doi:10.1000/canary-1", "provider": "crossref", "providerFamily": "doi_registry", "title": "Reviewed one", "abstract": "Full text one", "accessStatus": "open_full_text"},
                {"stableId": "openalex:canary-2", "provider": "openalex", "providerFamily": "scholarly_index", "title": "Reviewed two", "abstract": "Full text two", "accessStatus": "open_full_text"},
                {"stableId": "pmid:canary-3", "provider": "europe_pmc", "providerFamily": "biomedical_index", "title": "Reviewed three", "abstract": "Full text three", "accessStatus": "open_full_text"},
            ],
        }

    @staticmethod
    def context_index():
        return SimpleNamespace(items={"S125-006": SimpleNamespace(
            headline="How can we measure interface phenomena on the microscopic level?",
            source_context="The complete hash-verified Science 125 chemistry context.",
        )})

    @staticmethod
    def route():
        return SimpleNamespace(retrieval_profile="retrieval.chem.interface.v1", model_dump=lambda **_kwargs: {
            "questionId": "S125-006",
            "benchmarkDomain": "Chemistry",
            "primarySubdomain": "chem.interface",
            "crossDomainTags": ["physics", "materials"],
            "methodProfile": {"primary": "experimental", "secondary": ["computational"]},
            "promptProfile": "s125.chemistry.v1",
            "retrievalProfile": "retrieval.chem.interface.v1",
            "classificationReviewStatus": "reviewed",
        })

    def result(self, content: str, request_id: str, *, tokens: int = 21):
        from chalk_app.core.llm_client import LLMCallResult, LLMUsage

        return LLMCallResult(
            content=content,
            provider="DashScope",
            model="qwen-test",
            request_id=request_id,
            status_code=200,
            attempts=1,
            prompt_hash="a" * 64,
            response_hash="b" * 64,
            usage=LLMUsage(prompt_tokens=tokens - 8, completion_tokens=8, total_tokens=tokens),
            latency_ms=25,
            estimated_cost_cny=0.0,
            status="succeeded",
        )

    def test_extracts_json_from_a_markdown_fence(self) -> None:
        from app.services.science_canary import parse_model_json

        self.assertEqual(parse_model_json('```json\n{"ok":true}\n```'), {"ok": True})

    def test_generation_prompt_separates_claim_ids_from_source_placeholders(self) -> None:
        from app.services.science_canary import _generation_prompt

        prompt = _generation_prompt("How can interfaces be measured?")

        self.assertIn("supportingEvidenceRefs must contain only evidenceClaims IDs", prompt)
        self.assertIn("sourceRefs may use unavailable:canary-no-evidence", prompt)
        self.assertIn("hypothesis may reference E1", prompt)

    def test_repairs_invalid_first_output_and_writes_raw_and_validated_artifacts(self) -> None:
        from app.services.science_canary import run_canary_item
        from chalk_app.core.llm_client import LLMBudget, LLMConfig

        repaired = self.canary_contract(3)
        events: list[dict[str, object]] = []
        with tempfile.TemporaryDirectory() as temp_dir, patch(
            "app.services.research_generation._chat_result",
            side_effect=[
                self.result("not-json", "request-first"),
                self.result(json.dumps(repaired), "request-repair"),
            ],
        ) as call, patch("app.services.science_canary.load_science125_context_index", return_value=self.context_index()), patch("app.services.science_canary.get_science125_route", return_value=self.route()):
            summary = run_canary_item(
                self.canary_item(),
                config=LLMConfig(api_key="test-key", model="qwen-test"),
                budget=LLMBudget(max_total_tokens=1000),
                telemetry_sink=events.append,
                output_dir=Path(temp_dir),
            )

            artifact = json.loads((Path(temp_dir) / "S125-006.json").read_text(encoding="utf-8"))
            first_raw = (Path(temp_dir) / "S125-006.initial.raw.txt").read_text(encoding="utf-8")
            repair_raw = (Path(temp_dir) / "S125-006.repair.raw.txt").read_text(encoding="utf-8")

        self.assertEqual(call.call_count, 2)
        self.assertTrue(summary["schemaRepaired"])
        self.assertEqual(summary["requestId"], "request-repair")
        self.assertEqual(summary["totalTokens"], 42)
        self.assertEqual(summary["contractVersion"], "research-v1")
        self.assertNotIn("content", summary)
        self.assertNotIn("prompt", summary)
        self.assertEqual(artifact["provenance"]["requestId"], "request-repair")
        self.assertEqual(artifact["provenance"]["model"], "qwen-test")
        self.assertEqual(artifact["provenance"]["promptHash"], "a" * 64)
        self.assertEqual(first_raw, "not-json")
        self.assertEqual(json.loads(repair_raw)["contractVersion"], "research-v1")

    def test_failed_schema_repair_keeps_raw_outputs_without_validated_artifact(self) -> None:
        from app.services.science_canary import run_canary_item
        from chalk_app.core.llm_client import LLMBudget, LLMConfig

        with tempfile.TemporaryDirectory() as temp_dir, patch(
            "app.services.research_generation._chat_result",
            side_effect=[
                self.result("not-json", "request-first"),
                self.result("still-not-json", "request-repair"),
            ],
        ) as call, patch("app.services.science_canary.load_science125_context_index", return_value=self.context_index()), patch("app.services.science_canary.get_science125_route", return_value=self.route()):
            output_dir = Path(temp_dir)
            with self.assertRaisesRegex(ValueError, "did not contain a JSON object"):
                run_canary_item(
                    self.canary_item(),
                    config=LLMConfig(api_key="test-key", model="qwen-test"),
                    budget=LLMBudget(max_total_tokens=1000),
                    telemetry_sink=lambda _: None,
                    output_dir=output_dir,
                )

            self.assertEqual(call.call_count, 2)
            self.assertEqual(
                (output_dir / "S125-006.initial.raw.txt").read_text(encoding="utf-8"),
                "not-json",
            )
            self.assertEqual(
                (output_dir / "S125-006.repair.raw.txt").read_text(encoding="utf-8"),
                "still-not-json",
            )
            self.assertFalse((output_dir / "S125-006.json").exists())

    def test_repairs_a_structurally_valid_response_with_the_wrong_contract_version(self) -> None:
        from app.services.science_canary import run_canary_item
        from chalk_app.core.llm_client import LLMBudget, LLMConfig

        wrong_version = self.canary_contract(3)
        wrong_version["contractVersion"] = "research-v0"
        repaired = self.canary_contract(3)

        with tempfile.TemporaryDirectory() as temp_dir, patch(
            "app.services.research_generation._chat_result",
            side_effect=[
                self.result(json.dumps(wrong_version), "request-wrong-version"),
                self.result(json.dumps(repaired), "request-repair"),
            ],
        ) as call, patch("app.services.science_canary.load_science125_context_index", return_value=self.context_index()), patch("app.services.science_canary.get_science125_route", return_value=self.route()):
            summary = run_canary_item(
                self.canary_item(),
                config=LLMConfig(api_key="test-key", model="qwen-test"),
                budget=LLMBudget(max_total_tokens=1000),
                telemetry_sink=lambda _: None,
                output_dir=Path(temp_dir),
            )

        self.assertEqual(call.call_count, 2)
        self.assertTrue(summary["schemaRepaired"])
        self.assertEqual(summary["requestId"], "request-repair")

    def test_missing_transport_provenance_does_not_trigger_a_schema_repair_call(self) -> None:
        from app.services.science_canary import run_canary_item
        from chalk_app.core.llm_client import LLMBudget, LLMConfig

        with tempfile.TemporaryDirectory() as temp_dir, patch(
            "app.services.research_generation._chat_result",
            return_value=self.result(json.dumps(self.canary_contract(3)), "", tokens=21),
        ) as call, patch("app.services.science_canary.load_science125_context_index", return_value=self.context_index()), patch("app.services.science_canary.get_science125_route", return_value=self.route()):
            with self.assertRaisesRegex(ValueError, "request ID"):
                run_canary_item(
                    self.canary_item(),
                    config=LLMConfig(api_key="test-key", model="qwen-test"),
                    budget=LLMBudget(max_total_tokens=1000),
                    telemetry_sink=lambda _: None,
                    output_dir=Path(temp_dir),
                )

        self.assertEqual(call.call_count, 1)

    def test_missing_environment_key_fails_before_creating_outputs(self) -> None:
        from app.services.science_canary import require_dashscope_key

        with self.assertRaisesRegex(RuntimeError, "DASHSCOPE_API_KEY"):
            require_dashscope_key({})


if __name__ == "__main__":
    unittest.main()
