"""Helpers for building hypothesis input from multiple user documents."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Tuple


@dataclass
class DocumentContextItem:
    document_id: int
    title: str
    source_type: str = ""
    text: str = ""


def build_multi_document_context(
    documents: Iterable[DocumentContextItem],
    *,
    per_document_char_limit: int = 12000,
    total_char_limit: int = 60000,
) -> Tuple[str, List[dict]]:
    """Build a source-labeled context block for multi-paper hypothesis generation."""
    docs = [doc for doc in documents if str(doc.text or "").strip()]
    if not docs:
        return "", []

    parts = [
        "【多文献输入上下文】",
        f"共输入 {len(docs)} 篇用户文献。请进行跨文献综合分析，不要逐篇孤立总结；引用用户文献证据时使用 [D1]、[D2] 等来源编号。",
        "需要识别文献之间的共同材料/反应、性能指标差异、机理证据链、局限性与可验证假设。",
    ]
    metadata: List[dict] = []
    used = sum(len(part) for part in parts)

    for idx, doc in enumerate(docs, 1):
        source_id = f"D{idx}"
        raw_text = str(doc.text or "").strip()
        doc_limit = max(1000, per_document_char_limit)
        remaining = max(0, total_char_limit - used)
        if remaining <= 0:
            metadata.append(
                {
                    "source_id": source_id,
                    "document_id": doc.document_id,
                    "title": doc.title,
                    "source_type": doc.source_type,
                    "included_chars": 0,
                    "truncated": True,
                }
            )
            continue

        take = min(len(raw_text), doc_limit, remaining)
        text = raw_text[:take]
        truncated = take < len(raw_text)
        section = (
            f"\n\n[{source_id} | document_id={doc.document_id} | source_type={doc.source_type or 'unknown'}]\n"
            f"标题: {doc.title}\n"
            f"{text}"
        )
        if truncated:
            section += "\n[该文献内容已按输入长度限制截断，完整内容请回到用户导入文献核验。]"
        parts.append(section)
        used += len(section)
        metadata.append(
            {
                "source_id": source_id,
                "document_id": doc.document_id,
                "title": doc.title,
                "source_type": doc.source_type,
                "included_chars": take,
                "truncated": truncated,
            }
        )

    return "\n".join(parts), metadata
