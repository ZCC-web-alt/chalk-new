from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


MAX_SOURCE_CONTEXT_CHARS = 60000
MAX_SUPPLEMENTAL_CONTEXT_CHARS = 40000
MAX_TOTAL_CONTEXT_CHARS = 100000
_TRUNCATION_NOTE = "\n[This document was truncated for the hypothesis input. Full content remains available for RAG verification.]"
_PATH_KEYS = {
    "path",
    "managed_path",
    "source_path",
    "stored_path",
    "report_path",
    "web_report_path",
    "agent_trace_path",
    "trace_path",
    "exported_file",
    "source_report_path",
    "output_dir",
    "directory",
}


@dataclass(slots=True)
class HypothesisSourceDocument:
    id: int
    title: str
    source_type: str
    text: str


def _snake_to_camel(value: str) -> str:
    clean = value.lstrip("_")
    if "_" not in clean:
        return clean
    first, *rest = clean.split("_")
    return first + "".join(part[:1].upper() + part[1:] for part in rest if part)


def _is_path_key(key: str) -> bool:
    lowered = key.lstrip("_").lower()
    return lowered in _PATH_KEYS or lowered.endswith("_path") or lowered.endswith("_directory")


def _private_root_variants(private_roots: Iterable[Path]) -> list[str]:
    variants: set[str] = set()
    for root in private_roots:
        text = str(root)
        if not text:
            continue
        variants.update({text, text.replace("\\", "/"), text.replace("\\", "\\\\")})
    return sorted(variants, key=len, reverse=True)


def _redact_text(value: str, private_roots: Iterable[Path]) -> str:
    redacted = value
    for root in _private_root_variants(private_roots):
        redacted = redacted.replace(root, "[internal]")
    redacted = re.sub(r"file:///[^\s<'\"]+", "[internal]", redacted, flags=re.IGNORECASE)
    redacted = re.sub(
        r"(?i)\b[A-Z]:[\\/](?:[^\\/\s<'\"]+[\\/])*[^\\/\s<'\"]+",
        "[internal]",
        redacted,
    )
    redacted = re.sub(
        r"\\\\[^\\\s<'\"]+\\[^\\\s<'\"]+(?:\\[^\\\s<'\"]+)*",
        "[internal]",
        redacted,
    )
    redacted = re.sub(
        r"(?<![\w:])/(?:[^/\s<'\"]+/)+[^/\s<'\"]+",
        "[internal]",
        redacted,
    )
    return redacted


def sanitize_public_data(value: Any, *, private_roots: Iterable[Path] = ()) -> Any:
    if isinstance(value, dict):
        public: dict[str, Any] = {}
        for key, nested in value.items():
            key_text = str(key)
            if _is_path_key(key_text):
                continue
            public[_snake_to_camel(key_text)] = sanitize_public_data(nested, private_roots=private_roots)
        return public
    if isinstance(value, list):
        return [sanitize_public_data(item, private_roots=private_roots) for item in value]
    if isinstance(value, tuple):
        return [sanitize_public_data(item, private_roots=private_roots) for item in value]
    if isinstance(value, str):
        return _redact_text(value, private_roots)
    return value


def build_hypothesis_input(
    documents: list[HypothesisSourceDocument],
    supplemental_context: str = "",
) -> tuple[str, list[dict[str, Any]]]:
    supplemental = str(supplemental_context or "").strip()
    if len(supplemental) > MAX_SUPPLEMENTAL_CONTEXT_CHARS:
        raise ValueError("Supplemental context exceeds the configured limit.")
    usable = [document for document in documents if str(document.text or "").strip()]
    source_header = (
        "[Multi-document hypothesis context]\n"
        f"Documents: {len(usable)}. Synthesize evidence across sources and cite them as [D1], [D2], and so on.\n"
    ) if usable else ""
    supplemental_section = f"\n\n[Supplemental context]\n{supplemental}" if supplemental else ""

    document_headers = [
        f"\n\n[D{index} | documentId={document.id} | sourceType={document.source_type or 'unknown'}]\nTitle: {document.title}\n"
        for index, document in enumerate(usable, 1)
    ]
    fixed_size = len(source_header) + len(supplemental_section) + sum(len(header) for header in document_headers)
    reserved_notes = len(_TRUNCATION_NOTE) * len(usable)
    total_text_budget = max(0, MAX_TOTAL_CONTEXT_CHARS - fixed_size - reserved_notes)
    source_text_budget = min(MAX_SOURCE_CONTEXT_CHARS, total_text_budget)
    per_document_limit = min(12000, source_text_budget // max(len(usable), 1))

    parts = [source_header] if source_header else []
    metadata: list[dict[str, Any]] = []
    for index, (document, header) in enumerate(zip(usable, document_headers, strict=True), 1):
        raw_text = str(document.text or "").strip()
        take = min(len(raw_text), per_document_limit)
        truncated = take < len(raw_text)
        parts.extend([header, raw_text[:take]])
        if truncated:
            parts.append(_TRUNCATION_NOTE)
        metadata.append({
            "sourceId": f"D{index}",
            "documentId": document.id,
            "title": document.title,
            "sourceType": document.source_type,
            "includedChars": take,
            "sourceChars": len(raw_text),
            "truncated": truncated,
        })
    if supplemental_section:
        parts.append(supplemental_section)
    context = "".join(parts)
    if len(context) > MAX_TOTAL_CONTEXT_CHARS:
        raise ValueError("Combined hypothesis input exceeds the configured limit.")
    return context, metadata


def secure_report_html(html: str, *, private_roots: Iterable[Path] = ()) -> str:
    redacted = _redact_text(str(html or ""), private_roots)
    policy = (
        "default-src 'none'; "
        "script-src 'unsafe-inline' https://cdn.jsdelivr.net; "
        "style-src 'unsafe-inline'; "
        "img-src data: blob:; "
        "font-src data:; "
        "connect-src 'none'; base-uri 'none'; form-action 'none'; "
        "frame-ancestors 'none'; sandbox allow-scripts allow-downloads"
    )
    meta = f'<meta http-equiv="Content-Security-Policy" content="{policy}">'
    if re.search(r"<head(?:\s[^>]*)?>", redacted, flags=re.IGNORECASE):
        return re.sub(
            r"(<head(?:\s[^>]*)?>)",
            rf"\1{meta}",
            redacted,
            count=1,
            flags=re.IGNORECASE,
        )
    return meta + redacted


def parse_json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if not value:
        return {}
    try:
        parsed = __import__("json").loads(str(value))
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def parse_document_ids(value: Any) -> list[int]:
    if isinstance(value, str):
        try:
            value = __import__("json").loads(value)
        except (TypeError, ValueError):
            value = []
    if not isinstance(value, list):
        return []
    result: list[int] = []
    for item in value:
        try:
            document_id = int(item)
        except (TypeError, ValueError):
            continue
        if document_id > 0 and document_id not in result:
            result.append(document_id)
    return result


def normalize_tags(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    tags: list[str] = []
    seen: set[str] = set()
    for item in value:
        tag = str(item or "").strip()
        if not tag or tag.casefold() in seen:
            continue
        seen.add(tag.casefold())
        tags.append(tag[:60])
    return tags[:20]


def hypothesis_summary(hypothesis: Any) -> dict[str, Any]:
    result = parse_json_object(getattr(hypothesis, "result_json", None))
    extra = parse_json_object(getattr(hypothesis, "extra_json", None))
    source_ids = parse_document_ids(getattr(hypothesis, "related_doc_ids", None))
    if not source_ids:
        source_ids = parse_document_ids(extra.get("source_document_ids") or result.get("_source_document_ids"))
    domain = str(extra.get("domain") or result.get("_domain") or "")
    return {
        "id": hypothesis.id,
        "title": str(hypothesis.title or ""),
        "researchQuestion": hypothesis.research_question,
        "confidence": int(hypothesis.confidence or 5),
        "feasibility": str(hypothesis.feasibility or ""),
        "iterationCount": int(hypothesis.iteration_count or 0),
        "sourceDocumentIds": source_ids,
        "domain": domain,
        "tags": normalize_tags(extra.get("tags")),
        "status": str(hypothesis.status or "draft"),
        "createdAt": hypothesis.created_at,
        "updatedAt": hypothesis.updated_at,
    }


def hypothesis_detail(
    hypothesis: Any,
    *,
    feedback_rows: list[Any],
    artifacts: list[Any],
    private_roots: Iterable[Path] = (),
) -> dict[str, Any]:
    summary = hypothesis_summary(hypothesis)
    raw = parse_json_object(getattr(hypothesis, "result_json", None))
    extra = parse_json_object(getattr(hypothesis, "extra_json", None))
    source_documents = extra.get("source_documents") or raw.get("_source_documents") or []
    feedback_records = []
    for row in feedback_rows:
        feedback_records.append({
            "id": row.id,
            "sessionId": row.session_id,
            "mode": row.mode,
            "interaction": parse_json_object(row.interaction_json),
            "createdAt": row.created_at,
        })
    detail = {
        **summary,
        "hypothesis": raw,
        "sourceDocuments": source_documents if isinstance(source_documents, list) else [],
        "iterations": extra.get("iterations") or [],
        "critiqueHistory": extra.get("critique_history") or [],
        "reasoningChain": extra.get("reasoning_chain"),
        "debateHistory": extra.get("debate_history") or [],
        "hitl": {
            "interactionHistory": extra.get("interaction_history") or raw.get("_human_collaboration") or {},
            "feedbackRecords": feedback_records,
        },
        "verification": {
            "verificationReport": extra.get("verification_report"),
            "resultsVerification": extra.get("results_verification"),
            "closedLoopValidation": extra.get("closed_loop_validation"),
            "executableValidation": extra.get("executable_validation"),
            "scientificToolkit": extra.get("scientific_toolkit") or raw.get("_scientific_toolkit"),
        },
        "scientificEvidence": (
            extra.get("scientific_evidence_context")
            or raw.get("_scientific_evidence_context")
            or {}
        ),
        "multimodalEvidence": extra.get("multimodal_evidence") or raw.get("_multimodal_evidence") or {},
        "quantitativeReport": extra.get("quantitative_report") or raw.get("_quantitative_validation_source") or {},
        "multimodalRuns": extra.get("multimodal_run_snapshots") or [],
        "workflow": (
            extra.get("hypothesis_workflow_package")
            or raw.get("_hypothesis_workflow_package")
            or {}
        ),
        "artifacts": [artifact.to_dict() for artifact in artifacts],
    }
    return sanitize_public_data(detail, private_roots=private_roots)
