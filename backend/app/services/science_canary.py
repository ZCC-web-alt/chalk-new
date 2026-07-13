from __future__ import annotations

import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Mapping

from app.core.legacy import llm_client as load_llm_client
from app.schemas.research import ResearchOutput


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
    return (
        "Generate a research-v1 JSON object for the scientific question below. "
        "Return JSON only, with exactly H1, H2, H3 and null hypothesis H0. "
        "Use profile general_science. This transport canary has no retrieved evidence: "
        "do not invent citations. Create evidenceClaims with IDs such as E1. "
        "supportingEvidenceRefs must contain only evidenceClaims IDs; for example, a "
        "hypothesis may reference E1. sourceRefs may use unavailable:canary-no-evidence "
        "to mark missing retrieved evidence. Provide an executable, falsifiable plan.\n\n"
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

    context = LLMCallContext(resource_type="science125_item", resource_id=item_id)
    output_dir = Path(output_dir).resolve()
    results: list[LLMCallResult] = []
    first = _chat_result(
        _generation_prompt(question),
        config,
        task="hypothesis",
        system_prompt="You produce cautious, falsifiable scientific research plans as strict JSON.",
        timeout=180,
        context=context,
        budget=budget,
        telemetry_sink=telemetry_sink,
        telemetry_required=True,
        max_attempts=4,
    )
    results.append(first)
    _write_raw_output(output_dir, item_id, "initial", first.content)
    _ensure_succeeded(first)
    _ensure_auditable_transport(first)
    repaired = False
    try:
        output = _validated_output(first)
    except Exception as first_error:
        repaired = True
        repair = _chat_result(
            _repair_prompt(question, first.content, first_error),
            config,
            task="hypothesis",
            system_prompt="You repair scientific JSON without adding unsupported facts or citations.",
            timeout=180,
            context=context,
            budget=budget,
            telemetry_sink=telemetry_sink,
            telemetry_required=True,
            max_attempts=4,
        )
        results.append(repair)
        _write_raw_output(output_dir, item_id, "repair", repair.content)
        _ensure_succeeded(repair)
        _ensure_auditable_transport(repair)
        output = _validated_output(repair)

    final = results[-1]
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
        "attempts": sum(result.attempts for result in results),
        "totalTokens": sum(result.usage.total_tokens for result in results),
        "latencyMs": sum(result.latency_ms for result in results),
        "retryCount": sum(max(0, result.attempts - 1) for result in results),
        "schemaRepaired": repaired,
        "promptHash": final.prompt_hash,
        "responseHash": final.response_hash,
        "estimatedCostCny": sum(result.estimated_cost_cny for result in results),
    }


__all__ = ["parse_model_json", "require_dashscope_key", "run_canary_item"]
