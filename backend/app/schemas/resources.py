from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class DocumentOut(BaseModel):
    id: int
    title: str
    source_type: str = Field(alias="sourceType")
    file_name: str | None = Field(default=None, alias="fileName")
    summary: str | None
    created_at: datetime = Field(alias="createdAt")

    model_config = {"populate_by_name": True}


class GlossaryTermOut(BaseModel):
    id: int
    en_term: str = Field(alias="enTerm")
    zh_term: str = Field(alias="zhTerm")
    note: str | None = None
    created_at: datetime = Field(alias="createdAt")

    model_config = {"populate_by_name": True}


class GlossaryTermInput(BaseModel):
    en_term: str = Field(alias="enTerm", min_length=1, max_length=200)
    zh_term: str = Field(alias="zhTerm", min_length=1, max_length=200)
    note: str | None = Field(default=None, max_length=4000)

    model_config = {"populate_by_name": True}


class GlossaryBatchInput(BaseModel):
    terms: list[GlossaryTermInput] = Field(min_length=1, max_length=100)


class GlossaryBatchOut(BaseModel):
    data: list[GlossaryTermOut]
    count: int


class LabRecordOut(BaseModel):
    id: int
    title: str
    content: str
    related_doc_ids: str | None = Field(alias="relatedDocIds")
    ai_suggestion: str | None = Field(alias="aiSuggestion")
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")

    model_config = {"populate_by_name": True}


class LabRecordCreate(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    content: str = Field(default="", max_length=200000)
    related_doc_ids: str | None = Field(default=None, alias="relatedDocIds", max_length=4000)

    model_config = {"populate_by_name": True}


class LabRecordUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    content: str | None = Field(default=None, max_length=200000)
    related_doc_ids: str | None = Field(default=None, alias="relatedDocIds", max_length=4000)
    ai_suggestion: str | None = Field(default=None, alias="aiSuggestion", max_length=200000)

    model_config = {"populate_by_name": True}


class HypothesisArtifactOut(BaseModel):
    id: str
    kind: str
    file_name: str = Field(alias="fileName")
    mime_type: str = Field(alias="mimeType")
    size_bytes: int = Field(alias="sizeBytes")
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")

    model_config = {"populate_by_name": True}


class HypothesisSummaryOut(BaseModel):
    id: int
    title: str
    research_question: str | None = Field(alias="researchQuestion")
    confidence: int
    feasibility: str
    iteration_count: int = Field(alias="iterationCount")
    source_document_ids: list[int] = Field(default_factory=list, alias="sourceDocumentIds")
    domain: str = ""
    tags: list[str] = Field(default_factory=list)
    status: str
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")

    model_config = {"populate_by_name": True}


class HypothesisDetailOut(HypothesisSummaryOut):
    hypothesis: dict[str, Any] = Field(default_factory=dict)
    source_documents: list[dict[str, Any]] = Field(default_factory=list, alias="sourceDocuments")
    iterations: list[Any] = Field(default_factory=list)
    critique_history: list[Any] = Field(default_factory=list, alias="critiqueHistory")
    reasoning_chain: Any | None = Field(default=None, alias="reasoningChain")
    debate_history: list[Any] = Field(default_factory=list, alias="debateHistory")
    hitl: dict[str, Any] = Field(default_factory=dict)
    verification: dict[str, Any] = Field(default_factory=dict)
    scientific_evidence: dict[str, Any] = Field(default_factory=dict, alias="scientificEvidence")
    multimodal_evidence: dict[str, Any] = Field(default_factory=dict, alias="multimodalEvidence")
    quantitative_report: dict[str, Any] = Field(default_factory=dict, alias="quantitativeReport")
    multimodal_runs: list[dict[str, Any]] = Field(default_factory=list, alias="multimodalRuns")
    workflow: dict[str, Any] = Field(default_factory=dict)
    artifacts: list[HypothesisArtifactOut] = Field(default_factory=list)


class HypothesisMetadataUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=500)
    status: Literal["draft", "reviewed", "approved", "rejected"] | None = None
    tags: list[str] | None = Field(default=None, max_length=20)

    model_config = {"populate_by_name": True, "extra": "forbid"}

    @field_validator("title")
    @classmethod
    def strip_title(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None

    @field_validator("tags")
    @classmethod
    def normalize_tags(cls, values: list[str] | None) -> list[str] | None:
        if values is None:
            return None
        normalized: list[str] = []
        seen: set[str] = set()
        for value in values:
            tag = str(value or "").strip()
            if not tag or len(tag) > 60:
                raise ValueError("Tags must contain 1 to 60 characters.")
            key = tag.casefold()
            if key in seen:
                continue
            seen.add(key)
            normalized.append(tag)
        return normalized


class EvidenceRecordOut(BaseModel):
    evidence_uid: str | None = Field(default=None, alias="evidenceUid")
    kind: str
    display_title: str | None = Field(default=None, alias="displayTitle")
    source_label: str | None = Field(default=None, alias="sourceLabel")
    material_system: str | None = Field(default=None, alias="materialSystem")
    reaction_type: str | None = Field(default=None, alias="reactionType")
    key_data: str | None = Field(default=None, alias="keyData")
    reliability_level: str | None = Field(default=None, alias="reliabilityLevel")
    raw: dict[str, Any]

    model_config = {"populate_by_name": True}


class EvidenceListOut(BaseModel):
    data: list[dict[str, Any]]
    pagination: dict[str, int]
    graph: dict[str, Any]


class DocumentAnalysisOut(BaseModel):
    id: str
    type: str
    document_ids: list[int] = Field(alias="documentIds")
    result: dict[str, Any]
    job_id: str | None = Field(default=None, alias="jobId")
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")

    model_config = {"populate_by_name": True}


class DocumentAnalysisListOut(BaseModel):
    data: list[DocumentAnalysisOut]


class DocumentAnalysisMaybeOut(BaseModel):
    data: DocumentAnalysisOut | None
