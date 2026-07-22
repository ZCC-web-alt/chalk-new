from __future__ import annotations

import hashlib
import json
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from app.core.config import get_settings
from app.services.science125_catalog import Science125CatalogError, load_science125_catalog


INDEX_VERSION = "science125-context-v1"
EXTRACTION_VERSION = "pymupdf-block-anchor-v1"
EXPECTED_SOURCE_PDF_SHA256 = (
    "4bda50e8e3c90f8968f1bfd72ded4d9587ae80cd40ba66656a12c93abcf8e576"
)
MAX_CONTEXT_CHARS = 100_000


class Science125ContextError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("The Science 125 source context is unavailable.")


@dataclass(frozen=True, slots=True)
class Science125ContextItem:
    id: str
    headline: str
    source_context: str
    context_sha256: str
    pdf_page: int
    booklet_page: int


@dataclass(frozen=True, slots=True)
class Science125ContextIndex:
    index_version: str
    manifest_version: str
    source_pdf_sha256: str
    extraction_version: str
    items: Mapping[str, Science125ContextItem]


def canonical_context(value: str) -> str:
    return unicodedata.normalize("NFC", " ".join(value.split()))


def context_sha256(value: str) -> str:
    return hashlib.sha256(canonical_context(value).encode("utf-8")).hexdigest()


def _parse_index(raw: object) -> Science125ContextIndex:
    if not isinstance(raw, dict):
        raise ValueError("index must be an object")
    if raw.get("indexVersion") != INDEX_VERSION:
        raise ValueError("unexpected context index version")
    if raw.get("manifestVersion") != "science125-v1":
        raise ValueError("unexpected manifest version")
    if raw.get("sourcePdfSha256") != EXPECTED_SOURCE_PDF_SHA256:
        raise ValueError("source PDF hash mismatch")
    if raw.get("sourcePdfFilename") != "sjtu-booklet.pdf":
        raise ValueError("unexpected source PDF identity")
    extraction_version = raw.get("extractionVersion")
    if extraction_version != EXTRACTION_VERSION:
        raise ValueError("unexpected extraction version")

    raw_items = raw.get("items")
    if not isinstance(raw_items, list) or len(raw_items) != 125:
        raise ValueError("context index must contain exactly 125 items")

    try:
        catalog = load_science125_catalog()
    except Science125CatalogError as exc:
        raise ValueError("authoritative catalog is unavailable") from exc
    catalog_items = {item.id: item for item in catalog.data}

    parsed: dict[str, Science125ContextItem] = {}
    for raw_item in raw_items:
        if not isinstance(raw_item, dict):
            raise ValueError("context item must be an object")
        item_id = raw_item.get("id")
        if not isinstance(item_id, str) or item_id not in catalog_items or item_id in parsed:
            raise ValueError("context item ID is invalid")
        catalog_item = catalog_items[item_id]
        headline = raw_item.get("headline")
        source_context = raw_item.get("sourceContext")
        digest = raw_item.get("contextSha256")
        pdf_page = raw_item.get("pdfPage")
        booklet_page = raw_item.get("bookletPage")
        if not isinstance(headline, str) or canonical_context(headline) != canonical_context(catalog_item.question):
            raise ValueError("context headline does not match the catalog")
        if not isinstance(source_context, str):
            raise ValueError("source context must be text")
        canonical_source = canonical_context(source_context)
        if not canonical_source or len(source_context) > MAX_CONTEXT_CHARS:
            raise ValueError("source context is empty or too large")
        if canonical_context(catalog_item.question) not in canonical_source:
            raise ValueError("source context must include the authoritative headline")
        if not isinstance(digest, str) or digest != context_sha256(source_context):
            raise ValueError("source context hash mismatch")
        if pdf_page != catalog_item.pdf_page or booklet_page != catalog_item.booklet_page:
            raise ValueError("source page mapping does not match the catalog")
        parsed[item_id] = Science125ContextItem(
            id=item_id,
            headline=catalog_item.question,
            source_context=source_context,
            context_sha256=digest,
            pdf_page=catalog_item.pdf_page,
            booklet_page=catalog_item.booklet_page,
        )

    if set(parsed) != set(catalog_items):
        raise ValueError("context index is incomplete")
    return Science125ContextIndex(
        index_version=INDEX_VERSION,
        manifest_version="science125-v1",
        source_pdf_sha256=EXPECTED_SOURCE_PDF_SHA256,
        extraction_version=extraction_version,
        items=MappingProxyType(parsed),
    )


def load_science125_context_index(path: Path | None = None) -> Science125ContextIndex:
    index_path = path or get_settings().science125_context_index_path
    try:
        raw = json.loads(index_path.read_text(encoding="utf-8"))
        return _parse_index(raw)
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
        raise Science125ContextError() from None


__all__ = [
    "EXPECTED_SOURCE_PDF_SHA256",
    "EXTRACTION_VERSION",
    "INDEX_VERSION",
    "Science125ContextError",
    "Science125ContextIndex",
    "Science125ContextItem",
    "canonical_context",
    "context_sha256",
    "load_science125_context_index",
]
