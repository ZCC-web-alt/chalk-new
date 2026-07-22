from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import fitz


@dataclass(frozen=True)
class ExtractedPageExcerpt:
    pages: list[int]
    text: str
    text_sha256: str
    pdf_sha256: str
    page_count: int
    max_chars: int
    original_char_count: int
    truncated: bool


class DocumentPageExcerptError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _validated_pdf_path(source_path: str | None, *, allowed_root: Path | None = None) -> Path:
    if not source_path:
        raise DocumentPageExcerptError(
            "EXCERPT_SOURCE_UNAVAILABLE",
            "The document does not have an available PDF source.",
        )
    candidate = Path(source_path)
    try:
        if candidate.suffix.lower() != ".pdf" or candidate.is_symlink() or not candidate.is_file():
            raise DocumentPageExcerptError(
                "EXCERPT_SOURCE_UNAVAILABLE",
                "The document does not have an available PDF source.",
            )
        resolved = candidate.resolve(strict=True)
        if allowed_root is not None:
            root = allowed_root.resolve()
            if not resolved.is_relative_to(root):
                raise DocumentPageExcerptError(
                    "EXCERPT_SOURCE_UNAVAILABLE",
                    "The document does not have an available PDF source.",
                )
        with resolved.open("rb") as handle:
            if handle.read(5) != b"%PDF-":
                raise DocumentPageExcerptError(
                    "EXCERPT_SOURCE_UNAVAILABLE",
                    "The document does not have an available PDF source.",
                )
        return resolved
    except DocumentPageExcerptError:
        raise
    except OSError as exc:
        raise DocumentPageExcerptError(
            "EXCERPT_SOURCE_UNAVAILABLE",
            "The document does not have an available PDF source.",
        ) from exc


def extract_document_page_excerpt(
    source_path: str | None,
    *,
    pages: list[int],
    max_chars: int,
    allowed_root: Path | None = None,
) -> ExtractedPageExcerpt:
    source = _validated_pdf_path(source_path, allowed_root=allowed_root)
    try:
        pdf_sha256 = _sha256_file(source)
        document = fitz.open(source)
    except (OSError, RuntimeError, ValueError) as exc:
        raise DocumentPageExcerptError(
            "EXCERPT_SOURCE_UNAVAILABLE",
            "The document does not have an available PDF source.",
        ) from exc

    try:
        page_count = len(document)
        if page_count < 1 or any(page > page_count for page in pages):
            raise DocumentPageExcerptError(
                "EXCERPT_PAGE_RANGE_INVALID",
                "One or more requested pages are outside the PDF page range.",
            )

        page_texts: list[str] = []
        for page_number in pages:
            try:
                page_text = document[page_number - 1].get_text("text", sort=True) or ""
            except (RuntimeError, ValueError) as exc:
                raise DocumentPageExcerptError(
                    "EXCERPT_TEXT_UNAVAILABLE",
                    "Readable text is unavailable for the requested PDF pages.",
                ) from exc
            normalized = page_text.replace("\r\n", "\n").replace("\r", "\n").strip()
            if not normalized or not any(character.isalnum() for character in normalized):
                raise DocumentPageExcerptError(
                    "EXCERPT_TEXT_UNAVAILABLE",
                    "Readable text is unavailable for one or more requested PDF pages.",
                )
            page_texts.append(normalized)

        complete_text = "\n\n".join(page_texts).strip()
        if not complete_text:
            raise DocumentPageExcerptError(
                "EXCERPT_TEXT_UNAVAILABLE",
                "Readable text is unavailable for the requested PDF pages.",
            )
        original_char_count = len(complete_text)
        truncated = original_char_count > max_chars
        text = complete_text[:max_chars].rstrip()
        if not text:
            raise DocumentPageExcerptError(
                "EXCERPT_TEXT_UNAVAILABLE",
                "Readable text is unavailable for the requested PDF pages.",
            )
        return ExtractedPageExcerpt(
            pages=pages,
            text=text,
            text_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
            pdf_sha256=pdf_sha256,
            page_count=page_count,
            max_chars=max_chars,
            original_char_count=original_char_count,
            truncated=truncated,
        )
    finally:
        document.close()


__all__ = [
    "DocumentPageExcerptError",
    "ExtractedPageExcerpt",
    "extract_document_page_excerpt",
]
