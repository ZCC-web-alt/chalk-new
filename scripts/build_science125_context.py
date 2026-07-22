from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = PROJECT_ROOT / "backend"
for import_root in (BACKEND_DIR, PROJECT_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from app.services.science125_catalog import load_science125_catalog
from app.services.science125_context import (
    EXPECTED_SOURCE_PDF_SHA256,
    EXTRACTION_VERSION,
    INDEX_VERSION,
    canonical_context,
    context_sha256,
    load_science125_context_index,
)


DEFAULT_OUTPUT_PATH = PROJECT_ROOT / "data" / "science125" / "science125-context-v1.json"


class SourcePdfMismatch(RuntimeError):
    pass


class ContextExtractionError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class TextBlock:
    index: int
    x0: float
    y0: float
    x1: float
    y1: float
    text: str


_NOISE_LABELS = {
    "125 QUESTIONS",
    "125 QUESTIONS: EXPLORATION AND DISCOVERY",
    "Mathematical Sciences",
    "Chemistry",
    "Medicine & Health",
    "Biology",
    "Astronomy",
    "Physics",
    "Engineering & Materials Science",
    "Information Science",
    "Neuroscience",
    "Ecology",
    "Energy Science",
    "Artificial Intelligence",
}


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_source_pdf(
    path: Path,
    *,
    expected_sha256: str = EXPECTED_SOURCE_PDF_SHA256,
) -> str:
    try:
        digest = _file_sha256(path)
    except OSError:
        raise SourcePdfMismatch("The authoritative Science 125 PDF is unavailable.") from None
    if digest != expected_sha256:
        raise SourcePdfMismatch("The selected PDF does not match the authoritative Science 125 source.")
    return digest


def _comparison_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value).casefold()
    # The booklet embeds several punctuation glyphs with a broken ToUnicode map.
    normalized = normalized.replace("\ufffdc", " ").replace("\ufffd", " ")
    normalized = re.sub(r"[^a-z0-9]+", " ", normalized)
    return " ".join(normalized.split())


def _anchor_score(question: str, block_text: str) -> float:
    expected = _comparison_text(question)
    candidate = _comparison_text(block_text)
    if not expected or not candidate:
        return 0.0
    if expected == candidate:
        return 1.0
    if expected in candidate or candidate in expected:
        shorter = min(len(expected), len(candidate))
        longer = max(len(expected), len(candidate))
        return 0.9 + 0.1 * (shorter / longer)
    return SequenceMatcher(None, expected, candidate).ratio()


def _page_blocks(page: Any) -> list[TextBlock]:
    blocks: list[TextBlock] = []
    for index, raw in enumerate(page.get_text("blocks", sort=False)):
        if len(raw) >= 7 and raw[6] != 0:
            continue
        text = str(raw[4] or "").strip()
        if not text:
            continue
        blocks.append(
            TextBlock(
                index=index,
                x0=float(raw[0]),
                y0=float(raw[1]),
                x1=float(raw[2]),
                y1=float(raw[3]),
                text=text,
            )
        )
    return blocks


def _is_noise(block: TextBlock, page_height: float) -> bool:
    text = canonical_context(block.text)
    if not text:
        return True
    # Footer page numbers sit at the very bottom; long body blocks can also
    # reach the lower margin, so only discard a bottom block when it is short.
    if block.y0 < 45 or (
        block.y0 > page_height - 45 and (block.y1 - block.y0) < 35
    ):
        return True
    if text in _NOISE_LABELS:
        return True
    if re.fullmatch(r"\d+(?:\s+\d+)?", text):
        return True
    return False


def extract_page_contexts(
    page: Any,
    questions: Sequence[dict[str, Any]],
) -> dict[str, str]:
    blocks = _page_blocks(page)
    if not blocks:
        raise ContextExtractionError("The source page has no extractable text.")

    available = set(range(len(blocks)))
    anchors: dict[int, dict[str, Any]] = {}
    for question in questions:
        ranked = sorted(
            (
                (_anchor_score(str(question["question"]), blocks[index].text), index)
                for index in available
            ),
            reverse=True,
        )
        score, selected = ranked[0] if ranked else (0.0, -1)
        if selected < 0 or score < 0.72:
            raise ContextExtractionError(
                f"Could not locate the heading for {question['id']} on PDF page {page.number + 1}."
            )
        anchors[selected] = question
        available.remove(selected)

    ordered_anchor_indexes = sorted(anchors)
    contexts: dict[str, str] = {}
    for position, anchor_index in enumerate(ordered_anchor_indexes):
        question = anchors[anchor_index]
        next_anchor = (
            ordered_anchor_indexes[position + 1]
            if position + 1 < len(ordered_anchor_indexes)
            else len(blocks)
        )
        paragraphs: list[str] = []
        for block in blocks[anchor_index + 1 : next_anchor]:
            if _is_noise(block, float(page.rect.height)):
                continue
            paragraph = canonical_context(block.text)
            if paragraph:
                paragraphs.append(paragraph)
        if not paragraphs:
            raise ContextExtractionError(
                f"No source description was extracted for {question['id']} on PDF page {page.number + 1}."
            )
        contexts[str(question["id"])] = (
            str(question["question"]).strip() + "\n\n" + "\n\n".join(paragraphs)
        )

    return contexts


def build_context_index(source_pdf: Path, output_path: Path = DEFAULT_OUTPUT_PATH) -> Path:
    source_digest = verify_source_pdf(source_pdf)
    catalog = load_science125_catalog()
    questions = [
        {
            "id": item.id,
            "question": item.question,
            "pdfPage": item.pdf_page,
            "bookletPage": item.booklet_page,
        }
        for item in catalog.data
    ]
    questions_by_page: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for question in questions:
        questions_by_page[int(question["pdfPage"])].append(question)

    try:
        import fitz

        document = fitz.open(source_pdf)
    except (ImportError, OSError, RuntimeError, ValueError):
        raise ContextExtractionError("The authoritative Science 125 PDF could not be opened.") from None

    contexts: dict[str, str] = {}
    try:
        for pdf_page, page_questions in sorted(questions_by_page.items()):
            if pdf_page < 1 or pdf_page > len(document):
                raise ContextExtractionError(f"PDF page {pdf_page} is unavailable.")
            contexts.update(extract_page_contexts(document[pdf_page - 1], page_questions))
    finally:
        document.close()

    if set(contexts) != {str(question["id"]) for question in questions}:
        raise ContextExtractionError("The Science 125 source context index is incomplete.")

    items = []
    for question in questions:
        item_id = str(question["id"])
        source_context = contexts[item_id]
        items.append(
            {
                "id": item_id,
                "headline": question["question"],
                "sourceContext": source_context,
                "contextSha256": context_sha256(source_context),
                "pdfPage": question["pdfPage"],
                "bookletPage": question["bookletPage"],
            }
        )

    payload = {
        "indexVersion": INDEX_VERSION,
        "manifestVersion": "science125-v1",
        "sourcePdfSha256": source_digest,
        "sourcePdfFilename": "sjtu-booklet.pdf",
        "extractionVersion": EXTRACTION_VERSION,
        "items": items,
    }
    output_path = output_path.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_name(f".{output_path.name}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        load_science125_context_index(temporary)
        temporary.replace(output_path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return output_path


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build the machine-local Science 125 source-context index."
    )
    parser.add_argument("source_pdf", nargs="?", type=Path, help="Authoritative sjtu-booklet.pdf")
    parser.add_argument("--pdf", dest="source_pdf_option", type=Path, help=argparse.SUPPRESS)
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_PATH,
        help="Runtime index path (defaults to data/science125/science125-context-v1.json).",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    source_pdf = args.source_pdf_option or args.source_pdf
    if source_pdf is None:
        parser.error("an authoritative Science 125 PDF is required")
    try:
        output = build_context_index(source_pdf, args.output)
        loaded = load_science125_context_index(output)
    except (SourcePdfMismatch, ContextExtractionError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(
        f"{loaded.index_version}: valid ({len(loaded.items)} source contexts, "
        f"extraction={loaded.extraction_version})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
