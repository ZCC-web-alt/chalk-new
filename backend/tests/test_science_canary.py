from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.test_research_contract import valid_contract


class ScienceCanaryTestCase(unittest.TestCase):
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

    def test_repairs_invalid_first_output_and_writes_raw_and_validated_artifacts(self) -> None:
        from app.services.science_canary import run_canary_item
        from chalk_app.core.llm_client import LLMBudget, LLMConfig

        repaired = valid_contract(3)
        events: list[dict[str, object]] = []
        with tempfile.TemporaryDirectory() as temp_dir, patch(
            "app.services.science_canary._chat_result",
            side_effect=[
                self.result("not-json", "request-first"),
                self.result(json.dumps(repaired), "request-repair"),
            ],
        ) as call:
            summary = run_canary_item(
                {
                    "id": "S125-006",
                    "question": "How can we measure interface phenomena on the microscopic level?",
                },
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

    def test_repairs_a_structurally_valid_response_with_the_wrong_contract_version(self) -> None:
        from app.services.science_canary import run_canary_item
        from chalk_app.core.llm_client import LLMBudget, LLMConfig

        wrong_version = valid_contract(3)
        wrong_version["contractVersion"] = "research-v0"
        repaired = valid_contract(3)

        with tempfile.TemporaryDirectory() as temp_dir, patch(
            "app.services.science_canary._chat_result",
            side_effect=[
                self.result(json.dumps(wrong_version), "request-wrong-version"),
                self.result(json.dumps(repaired), "request-repair"),
            ],
        ) as call:
            summary = run_canary_item(
                {"id": "S125-006", "question": "How can interfaces be measured?"},
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
            "app.services.science_canary._chat_result",
            return_value=self.result(json.dumps(valid_contract(3)), "", tokens=21),
        ) as call:
            with self.assertRaisesRegex(ValueError, "request ID"):
                run_canary_item(
                    {"id": "S125-006", "question": "How can interfaces be measured?"},
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
