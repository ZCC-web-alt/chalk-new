from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Mapping

from app.core.legacy import llm_client as load_llm_client
from app.schemas.research import ResearchOutput
from app.services.research_generation import (
    ResearchGenerationRequest,
    ResearchGenerationValidationError,
    research_generation_service,
)
from app.services.science125_catalog import get_science125_route, is_science125_pilot_enabled
from app.services.science125_context import load_science125_context_index
from app.services.science125_retrieval import get_provider, get_science125_retrieval_profile


_llm_client = load_llm_client()
LLMBudget = _llm_client.LLMBudget
LLMCallContext = _llm_client.LLMCallContext
LLMCallResult = _llm_client.LLMCallResult
LLMConfig = _llm_client.LLMConfig
_chat_result = _llm_client._chat_result


CANARY_ID_PATTERN = re.compile(r"^S125-\d{3}$")
TelemetrySink = Callable[[Mapping[str, Any]], None]


def require_dashscope_key(environment: Mapping[str, str] | None = None) -> str:
    env = os.environ if environment is None else environment
    key = str(env.get("DASHSCOPE_API_KEY") or "").strip()
    if not key:
        raise RuntimeError("DASHSCOPE_API_KEY is required for real Science 125 canary calls.")
    return key


def parse_model_json(content: str) -> dict[str, Any]:
    text = str(content or "").strip()
    if text.startswith("```"):
        first_newline = text.find("\n")
        if first_newline >= 0:
            text = text[first_newline + 1 :]
        if text.endswith("```"):
            text = text[:-3]
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("The model response did not contain a JSON object.")
    value = json.loads(text[start : end + 1])
    if not isinstance(value, dict):
        raise ValueError("The model response JSON must be an object.")
    return value


def _contract_schema_text() -> str:
    return json.dumps(
        ResearchOutput.model_json_schema(by_alias=True, mode="serialization"),
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _generation_prompt(question: str) -> str:
    """Legacy transport prompt retained only for unit compatibility tests."""
    return (
        "Generate research-v1 JSON. supportingEvidenceRefs must contain only evidenceClaims IDs. "
        "sourceRefs may use unavailable:canary-no-evidence; a hypothesis may reference E1.\n\n"
        f"QUESTION:\n{question}\n\nJSON SCHEMA:\n{_contract_schema_text()}"
    )


def _repair_prompt(question: str, raw_output: str, validation_error: Exception) -> str:
    return (
        "Repair the candidate response into a valid research-v1 JSON object. Return JSON only. "
        "Keep exactly H1, H2, H3 and H0, use general_science, and do not invent citations.\n\n"
        "Keep unavailable:canary-no-evidence only in evidenceClaims.sourceRefs. "
        "supportingEvidenceRefs must contain only IDs defined in evidenceClaims, such as E1.\n\n"
        f"QUESTION:\n{question}\n\nVALIDATION ERROR:\n{validation_error}\n\n"
        f"CANDIDATE RESPONSE:\n{raw_output}\n\nJSON SCHEMA:\n{_contract_schema_text()}"
    )


def _validated_output(result: LLMCallResult) -> ResearchOutput:
    payload = parse_model_json(result.content)
    hypotheses = payload.get("hypotheses")
    if not isinstance(hypotheses, list) or len(hypotheses) != 3:
        raise ValueError("Canary output must contain exactly three candidate hypotheses.")
    payload["provenance"] = {
        "provider": result.provider,
        "model": result.model,
        "requestId": result.request_id,
        "generatedAt": datetime.now(UTC).isoformat(),
        "promptHash": result.prompt_hash,
        "responseHash": result.response_hash,
    }
    return ResearchOutput.model_validate(payload)


def _ensure_succeeded(result: LLMCallResult) -> None:
    if result.status != "succeeded":
        raise RuntimeError(f"DashScope canary call failed with status {result.status}.")


def _ensure_auditable_transport(result: LLMCallResult) -> None:
    if not result.request_id:
        raise ValueError("DashScope did not return a request ID.")
    if result.usage.total_tokens <= 0:
        raise ValueError("DashScope did not return token usage.")


def _write_raw_output(output_dir: Path, item_id: str, stage: str, content: str) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / f"{item_id}.{stage}.raw.txt").write_text(content, encoding="utf-8")


def run_canary_item(
    item: Mapping[str, Any],
    *,
    config: LLMConfig,
    budget: LLMBudget,
    telemetry_sink: TelemetrySink,
    output_dir: Path,
) -> dict[str, Any]:
    item_id = str(item.get("id") or "").strip()
    question = str(item.get("question") or item.get("questionText") or "").strip()
    if not CANARY_ID_PATTERN.fullmatch(item_id):
        raise ValueError("Canary item ID must match S125-NNN.")
    if not question:
        raise ValueError("Canary item must contain a question.")
    if not is_science125_pilot_enabled(item_id):
        raise ValueError("The dedicated Science 125 canary is limited to the three pilot IDs.")
    context_index = load_science125_context_index()
    context_item = context_index.items.get(item_id)
    if context_item is None or context_item.headline != question:
        raise ValueError("Canary item does not match the hash-verified Science 125 context index.")
    route = get_science125_route(item_id)
    raw_evidence = item.get("evidenceRecords")
    if not isinstance(raw_evidence, list) or len(raw_evidence) < 3:
        raise ValueError("The dedicated Science 125 canary requires reviewed evidence records.")
    evidence_records = tuple(record for record in raw_evidence if isinstance(record, Mapping))
    if len(evidence_records) != len(raw_evidence):
        raise ValueError("Canary evidence records must be objects.")
    retrieval_profile = get_science125_retrieval_profile(route.retrieval_profile)
    policy_hash = hashlib.sha256(json.dumps(
        {
            provider_id: get_provider(provider_id).policy.policy_hash
            for provider_id in retrieval_profile.provider_ids
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")).hexdigest()
    evidence_snapshot_hash = hashlib.sha256(
        json.dumps(raw_evidence, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()

    def audited_telemetry(event: Mapping[str, Any]) -> None:
        telemetry_sink({
            **event,
            "policy_hash": policy_hash,
            "evidence_snapshot_hash": evidence_snapshot_hash,
        })

    context = LLMCallContext(resource_type="science125_item", resource_id=item_id)
    # The Canary and project rounds share the same strict Qwen JSON generation path.
    try:
        generated = research_generation_service.generate(
            ResearchGenerationRequest(
                question=question,
                profile="general_science",
                chemistry_subdomain=None,
                candidate_count=3,
                science125_id=item_id,
                science125_source_context=context_item.source_context,
                science125_routing={
                    **route.model_dump(by_alias=True),
                    "routingVersion": "science125-routing-v1",
                },
                evidence_records=evidence_records,
            ),
            config=config,
            budget=budget,
            context=context,
            telemetry_sink=audited_telemetry,
        )
    except ResearchGenerationValidationError as exc:
        # The shared service intentionally owns schema repair. Preserve raw
        # files here only for the Canary's controlled evidence artifact.
        _write_raw_output(output_dir, item_id, "initial", exc.initial_content)
        if exc.repair_content is not None:
            _write_raw_output(output_dir, item_id, "repair", exc.repair_content)
        raise
    output = generated.output
    final = generated.call
    output_dir = Path(output_dir).resolve()
    _write_raw_output(output_dir, item_id, "initial", generated.initial_content)
    if generated.repair_content is not None:
        _write_raw_output(output_dir, item_id, "repair", generated.repair_content)
    output_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = output_dir / f"{item_id}.json"
    artifact_path.write_text(
        json.dumps(output.model_dump(mode="json", by_alias=True), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return {
        "itemId": item_id,
        "status": "succeeded",
        "contractVersion": output.contract_version,
        "model": final.model,
        "requestId": final.request_id,
        "attempts": final.attempts,
        "totalTokens": generated.total_tokens,
        "latencyMs": generated.latency_ms,
        "retryCount": generated.retry_count,
        "schemaRepaired": generated.schema_repaired,
        "promptHash": final.prompt_hash,
        "responseHash": final.response_hash,
        "estimatedCostCny": generated.estimated_cost_cny,
        "routingVersion": "science125-routing-v1",
        "policyHash": policy_hash,
        "evidenceSnapshotHash": evidence_snapshot_hash,
    }


__all__ = ["parse_model_json", "require_dashscope_key", "run_canary_item"]
