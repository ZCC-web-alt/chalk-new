# -*- coding: utf-8 -*-
"""Optional Docling-backed document parsing for Chalk.

The public contract of this module is deliberately small: callers receive a
plain text body plus typed chunks. Docling stays an implementation detail, so
the rest of Chalk can fall back to PyMuPDF without branching everywhere.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class ParsedBlock:
    text: str
    chunk_type: str = "text"
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ParsedDocument:
    text: str
    markdown: str = ""
    blocks: List[ParsedBlock] = field(default_factory=list)
    parser: str = "pymupdf"
    diagnostics: Dict[str, Any] = field(default_factory=dict)


_TABLE_SEPARATOR_RE = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$")
_FIGURE_RE = re.compile(r"^\s*(?:fig(?:ure)?\.?|图|图表)\s*\d+", re.IGNORECASE)
_FORMULA_HINT_RE = re.compile(r"(\$\$?|\\\[|\\\(|\\begin\{equation\}|[A-Za-z0-9]\s*=\s*[-+*/^A-Za-z0-9(){}\[\].]+)")


def parse_pdf_document(path: str | Path) -> ParsedDocument:
    """Parse a PDF using Docling when available, otherwise use PyMuPDF text."""
    path = Path(path)
    docling_error = ""
    try:
        markdown, raw = _parse_with_docling(path)
        if markdown.strip():
            blocks = markdown_to_blocks(markdown)
            return ParsedDocument(
                text=_blocks_to_text(blocks) or markdown,
                markdown=markdown,
                blocks=blocks,
                parser="docling",
                diagnostics={
                    "raw_type": type(raw).__name__,
                    "block_count": len(blocks),
                    "table_count": sum(1 for b in blocks if b.chunk_type == "table_data"),
                    "formula_count": sum(1 for b in blocks if b.chunk_type == "formula"),
                    "figure_caption_count": sum(1 for b in blocks if b.chunk_type == "figure_caption"),
                },
            )
    except Exception as exc:
        docling_error = str(exc)

    from pdf_utils import extract_text_from_pdf

    text = extract_text_from_pdf(str(path))
    blocks = [ParsedBlock(text=t, chunk_type="text") for t in _split_text_blocks(text)]
    return ParsedDocument(
        text=text,
        markdown="",
        blocks=blocks,
        parser="pymupdf",
        diagnostics={"docling_error": docling_error, "block_count": len(blocks)},
    )


def _parse_with_docling(path: Path) -> Tuple[str, Any]:
    from docling.document_converter import DocumentConverter  # type: ignore

    converter = DocumentConverter()
    result = converter.convert(str(path))
    document = getattr(result, "document", result)
    markdown = ""
    for attr in ("export_to_markdown", "export_to_md"):
        fn = getattr(document, attr, None)
        if callable(fn):
            markdown = str(fn())
            break
    if not markdown and hasattr(document, "export_to_dict"):
        markdown = _docling_dict_to_text(document.export_to_dict())
    if not markdown:
        markdown = str(document)
    return markdown, document


def _docling_dict_to_text(data: Any) -> str:
    if isinstance(data, dict):
        parts = []
        for value in data.values():
            text = _docling_dict_to_text(value)
            if text:
                parts.append(text)
        return "\n".join(parts)
    if isinstance(data, list):
        return "\n".join(_docling_dict_to_text(v) for v in data if v is not None)
    if isinstance(data, str):
        return data
    return ""


def markdown_to_blocks(markdown: str) -> List[ParsedBlock]:
    """Convert Markdown into typed blocks useful for scientific RAG."""
    lines = [line.rstrip() for line in str(markdown or "").splitlines()]
    blocks: List[ParsedBlock] = []
    paragraph: List[str] = []
    i = 0

    def flush_paragraph() -> None:
        nonlocal paragraph
        text = "\n".join(line for line in paragraph if line.strip()).strip()
        paragraph = []
        if not text:
            return
        chunk_type = _classify_text_block(text)
        blocks.append(ParsedBlock(text=text, chunk_type=chunk_type))

    while i < len(lines):
        line = lines[i]
        if not line.strip():
            flush_paragraph()
            i += 1
            continue

        if _is_table_start(lines, i):
            flush_paragraph()
            table_lines = [line]
            i += 1
            while i < len(lines) and "|" in lines[i] and lines[i].strip():
                table_lines.append(lines[i])
                i += 1
            blocks.append(ParsedBlock(text="\n".join(table_lines), chunk_type="table_data"))
            continue

        if line.lstrip().startswith("#"):
            flush_paragraph()
            blocks.append(ParsedBlock(text=line.lstrip("#").strip(), chunk_type="heading"))
            i += 1
            continue

        paragraph.append(line)
        i += 1

    flush_paragraph()
    return blocks


def chunks_from_parsed_document(
    parsed: ParsedDocument,
    max_chars: int = 900,
    overlap_chars: int = 180,
) -> List[Tuple[int, str, str]]:
    """Return (order, text, chunk_type) tuples ready for embedding."""
    out: List[Tuple[int, str, str]] = []
    order = 0
    blocks = parsed.blocks or [ParsedBlock(text=parsed.text, chunk_type="text")]
    for block in blocks:
        text = _decorate_block(block, parsed.parser)
        if not text:
            continue
        if block.chunk_type in {"table_data", "formula", "figure_caption", "heading"}:
            out.append((order, text[:4000], block.chunk_type))
            order += 1
            continue
        for piece in _split_long_text(text, max_chars=max_chars, overlap_chars=overlap_chars):
            out.append((order, piece, block.chunk_type or "text"))
            order += 1
    return out


def summarize_parsed_document(parsed: ParsedDocument) -> str:
    counts: Dict[str, int] = {}
    for block in parsed.blocks:
        counts[block.chunk_type] = counts.get(block.chunk_type, 0) + 1
    details = ", ".join(f"{key}={value}" for key, value in sorted(counts.items()))
    return f"parser={parsed.parser}; blocks={len(parsed.blocks)}; {details}".strip()


def _blocks_to_text(blocks: List[ParsedBlock]) -> str:
    return "\n\n".join(block.text for block in blocks if block.text.strip())


def _split_text_blocks(text: str) -> List[str]:
    return [part.strip() for part in re.split(r"\n\s*\n", str(text or "")) if part.strip()]


def _split_long_text(text: str, max_chars: int, overlap_chars: int) -> List[str]:
    text = str(text or "").strip()
    if len(text) <= max_chars:
        return [text] if text else []
    pieces: List[str] = []
    start = 0
    while start < len(text):
        end = min(len(text), start + max_chars)
        piece = text[start:end].strip()
        if piece:
            pieces.append(piece)
        if end >= len(text):
            break
        start = max(0, end - overlap_chars)
    return pieces


def _is_table_start(lines: List[str], index: int) -> bool:
    if index + 1 >= len(lines):
        return False
    return "|" in lines[index] and bool(_TABLE_SEPARATOR_RE.match(lines[index + 1]))


def _classify_text_block(text: str) -> str:
    first = text.splitlines()[0] if text else ""
    if _FIGURE_RE.match(first):
        return "figure_caption"
    if _FORMULA_HINT_RE.search(text) and len(text) < 800:
        return "formula"
    return "text"


def _decorate_block(block: ParsedBlock, parser: str) -> str:
    text = str(block.text or "").strip()
    if not text:
        return ""
    labels = {
        "table_data": "表格",
        "formula": "公式",
        "figure_caption": "图注",
        "heading": "章节",
        "text": "正文",
    }
    label = labels.get(block.chunk_type, block.chunk_type or "正文")
    return f"[{label} | parser={parser}]\n{text}"
