from __future__ import annotations

import os
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch


BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))


class DeterministicResearchTransportTestCase(unittest.TestCase):
    def test_science125_budget_covers_generation_and_one_schema_repair(self) -> None:
        from app.services.research_generation import science125_generation_budget

        budget = science125_generation_budget()

        self.assertEqual(budget.max_total_tokens, 60_000)
        self.assertEqual(budget.max_estimated_cost_cny, 3.0)

    def request(self, *, profile: str = "general_science", candidate_count: int = 3):
        from app.services.research_generation import ResearchGenerationRequest

        return ResearchGenerationRequest(
            question="How can microscopic interface phenomena be measured reproducibly?",
            profile=profile,
            chemistry_subdomain="electrocatalysis" if profile == "chemistry" else None,
            candidate_count=candidate_count,
        )

    def test_deterministic_transport_is_rejected_outside_test_environment(self) -> None:
        from app.services.research_generation import DeterministicQwenTestTransport

        with patch.dict(os.environ, {"CHALK_WEB_ENV": "production"}, clear=False):
            with self.assertRaisesRegex(RuntimeError, "CHALK_WEB_ENV=test"):
                DeterministicQwenTestTransport()

    def test_environment_transport_requires_explicit_opt_in(self) -> None:
        from app.services.research_generation import ResearchGenerationService

        with patch.dict(
            os.environ,
            {
                "CHALK_WEB_ENV": "test",
                "CHALK_RESEARCH_GENERATION_TRANSPORT": "unsupported",
            },
            clear=False,
        ):
            with self.assertRaisesRegex(RuntimeError, "Unsupported research generation transport"):
                ResearchGenerationService.from_environment()

    def test_test_transport_generates_valid_contract_and_persists_dashscope_ledger(self) -> None:
        from app.services.model_call_ledger import ModelCallLedgerStore
        from app.services.research_generation import (
            DeterministicQwenTestTransport,
            ResearchGenerationService,
        )
        from chalk_app.core.llm_client import LLMBudget, LLMCallContext, LLMConfig

        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {"CHALK_WEB_ENV": "test"},
            clear=False,
        ), patch("app.services.research_generation._chat_result") as real_transport:
            ledger = ModelCallLedgerStore(Path(directory) / "web.db")
            try:
                service = ResearchGenerationService(transport=DeterministicQwenTestTransport())
                result = service.generate(
                    self.request(),
                    config=LLMConfig(
                        api_key="unused-test-key",
                        model="qwen3.8-max",
                        input_cost_per_million_cny=2.0,
                        output_cost_per_million_cny=12.0,
                    ),
                    budget=LLMBudget(max_total_tokens=20_000, max_estimated_cost_cny=3.0),
                    context=LLMCallContext(resource_type="science125_item", resource_id="S125-006"),
                    telemetry_sink=ledger.record,
                )
                rows = ledger.list_for_context("science125_item", "S125-006")
            finally:
                ledger.dispose()

        real_transport.assert_not_called()
        self.assertEqual(result.output.contract_version, "research-v1")
        self.assertEqual(result.output.profile, "general_science")
        self.assertEqual([item.id for item in result.output.hypotheses], ["H1", "H2", "H3"])
        self.assertEqual(result.output.null_hypothesis.id, "H0")
        self.assertEqual(result.call.provider, "DashScope")
        self.assertEqual(result.call.model, "qwen3.8-max")
        self.assertTrue(result.call.request_id.startswith("test-dashscope-"))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].provider, "DashScope")
        self.assertEqual(rows[0].model, "qwen3.8-max")
        self.assertEqual(rows[0].request_id, result.call.request_id)
        self.assertEqual(rows[0].status, "succeeded")
        self.assertGreater(rows[0].total_tokens, 0)
        self.assertGreaterEqual(rows[0].latency_ms, 0)

    def test_explicit_environment_transport_supports_chemistry_contract(self) -> None:
        from app.services.research_generation import ResearchGenerationService
        from chalk_app.core.llm_client import LLMBudget, LLMCallContext, LLMConfig

        with patch.dict(
            os.environ,
            {
                "CHALK_WEB_ENV": "test",
                "CHALK_RESEARCH_GENERATION_TRANSPORT": "deterministic",
            },
            clear=False,
        ):
            service = ResearchGenerationService.from_environment()
            result = service.generate(
                self.request(profile="chemistry", candidate_count=5),
                config=LLMConfig(api_key="unused-test-key", model="qwen3.8-max"),
                budget=LLMBudget(max_total_tokens=20_000),
                context=LLMCallContext(resource_type="science125_item", resource_id="S125-052"),
                telemetry_sink=lambda _: None,
            )

        self.assertEqual(result.output.profile, "chemistry")
        self.assertEqual(result.output.chemistry_subdomain, "electrocatalysis")
        self.assertIsNotNone(result.output.chemistry)
        self.assertEqual([item.id for item in result.output.hypotheses], ["H1", "H2", "H3", "H4", "H5"])

    def test_test_failure_marker_fails_once_per_context_then_allows_retry(self) -> None:
        from app.services.research_generation import (
            DETERMINISTIC_FAIL_ONCE_CONTEXT,
            DeterministicQwenTestTransport,
            ResearchGenerationService,
        )
        from chalk_app.core.llm_client import LLMBudget, LLMCallContext, LLMConfig

        request = replace(self.request(), supplemental_context=DETERMINISTIC_FAIL_ONCE_CONTEXT)
        with patch.dict(os.environ, {"CHALK_WEB_ENV": "test"}, clear=False):
            service = ResearchGenerationService(transport=DeterministicQwenTestTransport())
            with self.assertRaisesRegex(RuntimeError, "forced one failure"):
                service.generate(
                    request,
                    config=LLMConfig(api_key="unused", model="qwen3.8-max"),
                    budget=LLMBudget(max_total_tokens=20_000),
                    context=LLMCallContext(resource_type="science125_item", resource_id="S125-101"),
                    telemetry_sink=lambda _: None,
                )
            recovered = service.generate(
                request,
                config=LLMConfig(api_key="unused", model="qwen3.8-max"),
                budget=LLMBudget(max_total_tokens=20_000),
                context=LLMCallContext(resource_type="science125_item", resource_id="S125-101"),
                telemetry_sink=lambda _: None,
            )
        self.assertEqual(recovered.output.contract_version, "research-v1")

    def test_failed_dashscope_call_preserves_only_safe_diagnostics(self) -> None:
        from app.services.research_generation import ResearchGenerationCallError
        from chalk_app.core.llm_client import LLMCallResult, LLMUsage

        result = LLMCallResult(
            content="__CHALK_LLM_ERROR__: HTTP 401 secret response",
            provider="DashScope",
            model="qwen3.8-max",
            request_id="req-safe",
            status_code=401,
            attempts=4,
            prompt_hash="a" * 64,
            response_hash=None,
            usage=LLMUsage(),
            latency_ms=123,
            estimated_cost_cny=0.0,
            status="failed",
            error_type="api",
        )

        error = ResearchGenerationCallError(result)
        self.assertIn("status=401", str(error))
        self.assertIn("attempts=4", str(error))
        self.assertIn("requestId=req-safe", str(error))
        self.assertNotIn("secret response", str(error))

    def test_evidence_claims_cannot_reference_sources_outside_the_snapshot(self) -> None:
        import json

        from app.services.research_generation import (
            DeterministicQwenTestTransport,
            _validate,
        )
        from chalk_app.core.llm_client import LLMBudget, LLMCallContext, LLMConfig

        request = self.request()
        request = replace(request, source_documents=({"id": 42, "title": "Snapshot", "text": "Frozen evidence"},))
        with patch.dict(os.environ, {"CHALK_WEB_ENV": "test"}, clear=False):
            call = DeterministicQwenTestTransport().complete(
                request=request,
                prompt="test prompt",
                system_prompt="test system prompt",
                config=LLMConfig(api_key="unused", model="qwen3.8-max"),
                budget=LLMBudget(max_total_tokens=20_000),
                context=LLMCallContext(resource_type="science125_item", resource_id="S125-082"),
                telemetry_sink=lambda _: None,
            )
        payload = json.loads(call.content)
        payload["evidenceClaims"][0]["sourceRefs"] = ["document:invented"]
        with self.assertRaisesRegex(ValueError, "unavailable sources"):
            _validate(replace(call, content=json.dumps(payload)), request)

    def test_science125_request_uses_dedicated_prompt_and_server_extension(self) -> None:
        from app.services.research_generation import (
            DeterministicQwenTestTransport,
            ResearchGenerationRequest,
            ResearchGenerationService,
            generation_prompt,
        )
        from chalk_app.core.llm_client import LLMBudget, LLMCallContext, LLMConfig

        routing = {
            "routingVersion": "science125-routing-v1",
            "benchmarkDomain": "Astronomy",
            "primarySubdomain": "astro.cosmic_rays",
            "crossDomainTags": ["physics"],
            "methodProfile": {"primary": "observational", "secondary": ["computational"]},
            "promptProfile": "s125.astronomy.v1",
            "retrievalProfile": "retrieval.astro.high_energy.v1",
        }
        evidence = tuple({
            "stableId": f"doi:10.0000/cosmic-{index}",
            "provider": "crossref" if index < 3 else "ads",
            "title": f"Reviewed cosmic ray source {index}",
            "abstract": "Reviewed evidence.",
            "accessStatus": "open_access",
        } for index in range(1, 4))
        request = ResearchGenerationRequest(
            question="What is the origin of cosmic rays?",
            profile="general_science",
            chemistry_subdomain=None,
            candidate_count=3,
            science125_id="S125-054",
            science125_source_context="Hash-verified booklet context.",
            science125_routing=routing,
            evidence_records=evidence,
        )

        prompt = generation_prompt(request)
        self.assertIn("dedicated Science 125", prompt)
        self.assertIn("selection effects", prompt)
        self.assertIn('"promptVersion":"science125-prompts-v2"', prompt)
        self.assertIn("QUESTION MODULE [S125-054]", prompt)
        self.assertNotIn("electrocatalysis", prompt.lower())

        with patch.dict(os.environ, {"CHALK_WEB_ENV": "test"}, clear=False):
            result = ResearchGenerationService(transport=DeterministicQwenTestTransport()).generate(
                request,
                config=LLMConfig(api_key="unused", model="qwen3.8-max"),
                budget=LLMBudget(max_total_tokens=20_000),
                context=LLMCallContext(resource_type="science125_item", resource_id="S125-054"),
                telemetry_sink=lambda _: None,
            )

        self.assertEqual(result.output.profile, "general_science")
        self.assertIsNone(result.output.chemistry)
        self.assertEqual(result.output.science125.question_id, "S125-054")  # type: ignore[union-attr]
        self.assertEqual(result.output.science125.evidence_status, "sufficient")  # type: ignore[union-attr]
        self.assertEqual(
            result.output.science125.domain_checks,  # type: ignore[union-attr]
            [
                "selection_effects",
                "uncertainty_budget",
                "measurement_plan",
                "negative_evidence",
                "data_leakage",
                "replication",
                "applicability_boundary",
            ],
        )
        self.assertEqual(result.output.evidence_claims[0].source_refs, ["doi:10.0000/cosmic-1"])


if __name__ == "__main__":
    unittest.main()
