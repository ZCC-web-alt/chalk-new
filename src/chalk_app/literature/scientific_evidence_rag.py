# -*- coding: utf-8 -*-
"""Scientific evidence RAG adapter for Chalk.

PaperQA is optional and version-moving, so this module exposes a stable Chalk
contract and falls back to the existing vector retrieval context.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session


@dataclass
class EvidenceAnswer:
    question: str
    answer: str
    context: str
    citations: List[Dict[str, Any]] = field(default_factory=list)
    source: str = "fallback_rag"
    status: str = "fallback"
    warnings: List[str] = field(default_factory=list)


def build_scientific_evidence_context(
    session: Session,
    user_id: int,
    research_question: str,
    doc_id: Optional[int] = None,
    max_chunks: int = 20,
    min_score: float = 0.3,
    api_key: Optional[str] = None,
) -> EvidenceAnswer:
    """Build citation-aware evidence context for hypothesis generation."""
    paperqa_result = _try_paperqa(session, user_id, research_question, doc_id)
    if paperqa_result and paperqa_result.context.strip():
        return paperqa_result

    from rag import build_hypothesis_context

    context = build_hypothesis_context(
        session=session,
        user_id=user_id,
        research_question=research_question,
        doc_id=doc_id,
        max_chunks=max_chunks,
        min_score=min_score,
        api_key=api_key,
    )
    answer = _fallback_answer_from_context(research_question, context)
    return EvidenceAnswer(
        question=research_question,
        answer=answer,
        context=context,
        citations=_extract_citations_from_context(context),
        source="fallback_rag",
        status="fallback",
        warnings=["PaperQA 不可用或未返回可用证据，已回退到 Chalk 语义检索。"],
    )


def format_evidence_context(evidence: EvidenceAnswer) -> str:
    if not evidence.context:
        return ""
    header = [
        "【PaperQA/科学证据 RAG 上下文】",
        f"问题: {evidence.question}",
        f"来源: {evidence.source} | 状态: {evidence.status}",
    ]
    if evidence.answer:
        header.append(f"证据摘要: {evidence.answer}")
    if evidence.citations:
        header.append("引用/证据锚点:")
        for idx, citation in enumerate(evidence.citations[:12], 1):
            title = citation.get("title") or citation.get("source_title") or citation.get("source") or "未知来源"
            locator = citation.get("locator") or citation.get("chunk_id") or citation.get("path") or ""
            header.append(f"  [{idx}] {title} {locator}".strip())
    if evidence.warnings:
        header.append("提示: " + "; ".join(evidence.warnings[:3]))
    return "\n".join(header) + "\n\n" + evidence.context


def evidence_to_dict(evidence: EvidenceAnswer) -> Dict[str, Any]:
    return {
        "question": evidence.question,
        "answer": evidence.answer,
        "context": evidence.context,
        "citations": evidence.citations,
        "source": evidence.source,
        "status": evidence.status,
        "warnings": evidence.warnings,
    }


def _try_paperqa(
    session: Session,
    user_id: int,
    research_question: str,
    doc_id: Optional[int],
) -> Optional[EvidenceAnswer]:
    try:
        from paperqa import Docs, Settings  # type: ignore
    except Exception:
        try:
            from paperqa import Docs  # type: ignore
            Settings = None  # type: ignore
        except Exception:
            return None

    from db import Document

    query = session.query(Document).filter(Document.user_id == user_id)
    if doc_id is not None:
        query = query.filter(Document.id == doc_id)
    docs = [doc for doc in query.order_by(Document.created_at.desc()).limit(8).all() if doc.source_path]
    if not docs:
        return None

    try:
        library = Docs()
        citations = []
        for doc in docs:
            path = Path(str(doc.source_path))
            if not path.exists():
                continue
            try:
                library.add(str(path), citation=doc.title)
                citations.append({"title": doc.title, "path": str(path)})
            except TypeError:
                library.add(str(path))
                citations.append({"title": doc.title, "path": str(path)})
            except Exception:
                continue
        if not citations:
            return None

        if Settings is not None:
            try:
                response = library.query(research_question, settings=Settings())
            except TypeError:
                response = library.query(research_question)
        else:
            response = library.query(research_question)
        text = str(getattr(response, "answer", "") or response)
        context = str(getattr(response, "context", "") or text)
        if not context.strip():
            return None
        return EvidenceAnswer(
            question=research_question,
            answer=text[:2000],
            context=context,
            citations=citations,
            source="paperqa",
            status="ok",
        )
    except Exception as exc:
        return EvidenceAnswer(
            question=research_question,
            answer="",
            context="",
            citations=[],
            source="paperqa",
            status="failed",
            warnings=[str(exc)],
        )


def _fallback_answer_from_context(question: str, context: str) -> str:
    if not context:
        return ""
    first = context.strip().splitlines()[0][:220]
    return f"已基于 Chalk 语义检索为问题「{question[:80]}」返回相关文献片段；请在假设生成和 HITL 中逐条核对。首条证据: {first}"


def _extract_citations_from_context(context: str) -> List[Dict[str, Any]]:
    citations = []
    for line in str(context or "").splitlines():
        line = line.strip()
        if not line.startswith("["):
            continue
        if "文档ID=" in line or "文献片段" in line or "相关片段" in line:
            citations.append({"source": line[:240], "locator": line[:240]})
        if len(citations) >= 12:
            break
    return citations
