from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator


MAX_PAGE_EXCERPT_PAGES = 20
MAX_PAGE_EXCERPT_CHARS = 50_000


class DocumentPageExcerptInput(BaseModel):
    pages: list[int] | None = Field(default=None, min_length=1, max_length=MAX_PAGE_EXCERPT_PAGES)
    page_start: int | None = Field(default=None, alias="pageStart", ge=1)
    page_end: int | None = Field(default=None, alias="pageEnd", ge=1)
    max_chars: int = Field(default=12_000, alias="maxChars", ge=1, le=MAX_PAGE_EXCERPT_CHARS)

    model_config = {"extra": "forbid", "populate_by_name": True}

    @model_validator(mode="after")
    def validate_page_selection(self) -> "DocumentPageExcerptInput":
        has_pages = self.pages is not None
        has_range = self.page_start is not None or self.page_end is not None
        if has_pages == has_range:
            raise ValueError("Provide either pages or pageStart/pageEnd.")
        if has_pages:
            if self.pages is None:
                raise ValueError("pages must be provided.")
            if any(page < 1 for page in self.pages):
                raise ValueError("Page numbers must be positive.")
            if len(self.pages) != len(set(self.pages)):
                raise ValueError("Page numbers must be unique.")
            return self
        if self.page_start is None or self.page_end is None:
            raise ValueError("pageStart and pageEnd must be provided together.")
        if self.page_end < self.page_start:
            raise ValueError("pageEnd must not be less than pageStart.")
        if self.page_end - self.page_start + 1 > MAX_PAGE_EXCERPT_PAGES:
            raise ValueError(f"Select at most {MAX_PAGE_EXCERPT_PAGES} pages.")
        return self

    def requested_pages(self) -> list[int]:
        if self.pages is not None:
            return sorted(self.pages)
        if self.page_start is None or self.page_end is None:
            raise RuntimeError("Validated page range is unavailable.")
        return list(range(self.page_start, self.page_end + 1))


class DocumentPageExcerptProvenance(BaseModel):
    source_type: Literal["pdf"] = Field(alias="sourceType")
    pdf_sha256: str = Field(alias="pdfSha256", pattern=r"^[0-9a-f]{64}$")
    extractor: Literal["PyMuPDF"]
    page_count: int = Field(alias="pageCount", ge=1)
    max_chars: int = Field(alias="maxChars", ge=1)
    original_char_count: int = Field(alias="originalCharCount", ge=1)
    returned_char_count: int = Field(alias="returnedCharCount", ge=1)
    truncated: bool

    model_config = {"extra": "forbid", "populate_by_name": True}


class DocumentPageExcerptOut(BaseModel):
    document_id: int = Field(alias="documentId", ge=1)
    title: str = Field(max_length=255)
    pages: list[int] = Field(min_length=1, max_length=MAX_PAGE_EXCERPT_PAGES)
    text: str = Field(min_length=1, max_length=MAX_PAGE_EXCERPT_CHARS)
    hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    provenance: DocumentPageExcerptProvenance

    model_config = {"extra": "forbid", "populate_by_name": True}


__all__ = [
    "DocumentPageExcerptInput",
    "DocumentPageExcerptOut",
    "DocumentPageExcerptProvenance",
    "MAX_PAGE_EXCERPT_CHARS",
    "MAX_PAGE_EXCERPT_PAGES",
]
