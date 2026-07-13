"""
Shared PDF image extraction helpers for document-scoped visual assets.

Both literature management and hypothesis multimodal analysis should use this
module so extracted images live under one canonical per-document directory:

    data/visualizations/user_{user_id}/doc_{doc_id}/pdf_image_{idx}.{ext}
"""

from __future__ import annotations

import logging
import os
from typing import Any, Iterable, List, Optional

from pdf_utils import extract_images_from_pdf, extract_text_from_pdf

logger = logging.getLogger(__name__)

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".tif", ".webp"}


def get_document_image_dir(base_dir: str, user_id: int, doc_id: int) -> str:
    """Return the canonical image directory for one imported document."""
    return os.path.join(
        str(base_dir),
        "data",
        "visualizations",
        f"user_{user_id}",
        f"doc_{doc_id}",
    )


def _is_supported_image(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in IMAGE_EXTENSIONS


def list_document_images(base_dir: str, user_id: int, doc_id: int) -> List[str]:
    """List already extracted images for one document, ignoring legacy flat dirs."""
    doc_img_dir = get_document_image_dir(base_dir, user_id, doc_id)
    if not os.path.isdir(doc_img_dir):
        return []
    paths = []
    for name in sorted(os.listdir(doc_img_dir)):
        path = os.path.join(doc_img_dir, name)
        if os.path.isfile(path) and _is_supported_image(path):
            paths.append(path)
    return paths


def _clear_existing_images(doc_img_dir: str) -> None:
    if not os.path.isdir(doc_img_dir):
        return
    for name in os.listdir(doc_img_dir):
        path = os.path.join(doc_img_dir, name)
        if os.path.isfile(path) and _is_supported_image(path):
            try:
                os.remove(path)
            except OSError as exc:
                logger.debug("Could not remove stale document image %s: %s", path, exc)


def _normalize_ext(ext: str) -> str:
    value = (ext or "png").strip().lower().lstrip(".")
    if not value or any(ch in value for ch in ("/", "\\", os.sep)):
        return "png"
    return value


def _resolve_page_hints(
    pdf_path: str,
    *,
    full_text: str = "",
    config: Any = None,
    use_llm_page_hints: bool = False,
) -> Optional[List[int]]:
    if not use_llm_page_hints:
        return None
    text = full_text or ""
    if not text.strip():
        try:
            text = extract_text_from_pdf(pdf_path)
        except Exception as exc:
            logger.debug("Could not extract text for image page hints: %s", exc)
            return None
    if not text.strip():
        return None
    try:
        from llm_client import find_image_pages

        pages = find_image_pages(text, config=config)
    except Exception as exc:
        logger.debug("LLM image page hint detection failed; extracting all pages: %s", exc)
        return None
    if not pages:
        return None
    normalized = []
    for page in pages:
        try:
            page_num = int(page)
        except (TypeError, ValueError):
            continue
        if page_num > 0:
            normalized.append(page_num)
    return normalized or None


def _write_images(
    images: Iterable[tuple[bytes, str, int, int]],
    doc_img_dir: str,
) -> List[str]:
    paths: List[str] = []
    for idx, (img_bytes, ext, _width, _height) in enumerate(images, 1):
        file_ext = _normalize_ext(ext)
        path = os.path.join(doc_img_dir, f"pdf_image_{idx}.{file_ext}")
        with open(path, "wb") as fh:
            fh.write(img_bytes)
        paths.append(path)
    return paths


def extract_document_pdf_images(
    pdf_path: str,
    base_dir: str,
    user_id: int,
    doc_id: int,
    *,
    full_text: str = "",
    config: Any = None,
    use_llm_page_hints: bool = False,
    clear_existing: bool = True,
) -> List[str]:
    """
    Extract PDF images using the same document-scoped layout as literature management.

    By default this does not call an LLM to guess image pages; it extracts all pages
    through ``pdf_utils.extract_images_from_pdf(..., filter_small=True)``.
    """
    if not pdf_path or not str(pdf_path).lower().endswith(".pdf"):
        return []
    if not os.path.exists(pdf_path):
        logger.warning("PDF image extraction skipped because source does not exist: %s", pdf_path)
        return []

    doc_img_dir = get_document_image_dir(base_dir, user_id, doc_id)
    os.makedirs(doc_img_dir, exist_ok=True)
    if clear_existing:
        _clear_existing_images(doc_img_dir)

    page_hints = _resolve_page_hints(
        pdf_path,
        full_text=full_text,
        config=config,
        use_llm_page_hints=use_llm_page_hints,
    )
    images = extract_images_from_pdf(
        pdf_path,
        pages=page_hints if page_hints else None,
        filter_small=True,
    )
    paths = _write_images(images, doc_img_dir)
    logger.info(
        "Extracted %s document images for user=%s doc=%s into %s",
        len(paths),
        user_id,
        doc_id,
        doc_img_dir,
    )
    return paths


def ensure_document_images(
    base_dir: str,
    user_id: int,
    doc_id: int,
    *,
    doc_source_path: Optional[str] = None,
    full_text: str = "",
    config: Any = None,
    use_llm_page_hints: bool = False,
    force_reextract: bool = False,
) -> List[str]:
    """Return existing document images, or extract them from the PDF source if needed."""
    existing = list_document_images(base_dir, user_id, doc_id)
    if existing and not force_reextract:
        return existing
    if not doc_source_path:
        return []
    try:
        return extract_document_pdf_images(
            doc_source_path,
            base_dir,
            user_id,
            doc_id,
            full_text=full_text,
            config=config,
            use_llm_page_hints=use_llm_page_hints,
            clear_existing=True,
        )
    except Exception as exc:
        logger.warning("Document PDF image extraction failed: %s", exc)
        return []
