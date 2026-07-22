from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal, Union

import json

from pydantic import AliasChoices, BaseModel, Field, field_validator, model_validator

JobStatus = Literal["QUEUED", "RUNNING", "SUCCEEDED", "FAILED", "CANCELLED", "WAITING_FOR_FEEDBACK"]
JobType = Literal[
    "pdf_import",
    "pdf_reimport",
    "rag_qa",
    "literature_search",
    "evidence_manifest_import",
    "lab_suggest",
    "prompt_polish",
    "hypothesis_generate",
    "hypothesis_report",
    "hypothesis_workflow_export",
    "evidence_query",
    "document_analysis",
    "document_compare",
    "multimodal_analyze",
    "modeling_generate",
    "modeling_export",
]
CreatableJobType = Literal[
    "rag_qa",
    "literature_search",
    "lab_suggest",
    "prompt_polish",
    "hypothesis_generate",
    "hypothesis_report",
    "hypothesis_workflow_export",
    "evidence_query",
    "document_analysis",
    "document_compare",
    "multimodal_analyze",
    "modeling_generate",
    "modeling_export",
]
SearchPlatform = Literal["Crossref", "arXiv", "Semantic Scholar", "DOAJ", "PMC"]
DocumentAnalysisType = Literal["summary", "images", "structures", "safety", "sop", "reactions", "translation"]
HypothesisDomain = Literal[
    "",
    "electrocatalysis",
    "photocatalysis",
    "battery_materials",
    "synthetic_chemistry",
    "surface_interface_chemistry",
    "physical_chemistry",
    "quantum_chemistry",
    "analytical_chemistry",
    "organic_chemistry",
    "inorganic_chemistry",
    "polymer_chemistry",
    "chemical_biology",
    "materials_chemistry",
    "energy_chemistry",
    "environmental_chemistry",
    "nano_chemistry",
    "cluster_chemistry",
    "green_chemical_engineering",
]


class JobOut(BaseModel):
    id: str
    type: str
    status: JobStatus
    stage: str | None = None
    progress: int = Field(ge=0, le=100)
    message: str = ""
    result: Any | None = None
    error: dict[str, Any] | None = None
    feedback_prompt: dict[str, Any] | None = Field(default=None, alias="feedbackPrompt")
    resource: dict[str, Any] | None = None
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")

    model_config = {"populate_by_name": True}


class RagQaPayload(BaseModel):
    question: str = Field(min_length=1, max_length=12000)
    document_id: int | None = Field(default=None, alias="documentId", ge=1)
    top_k: int = Field(default=5, alias="topK", ge=1, le=20)

    model_config = {"populate_by_name": True, "extra": "forbid"}


class LiteratureSearchPayload(BaseModel):
    query_text: str = Field(alias="queryText", min_length=1, max_length=2000)
    domain: str = Field(default="", max_length=120)
    platforms: list[SearchPlatform] = Field(
        default_factory=lambda: ["Crossref", "arXiv", "Semantic Scholar", "DOAJ", "PMC"],
        min_length=1,
        max_length=5,
    )
    max_results: int = Field(default=20, alias="maxResults", ge=1, le=100)
    year_from: str = Field(default="", alias="yearFrom", max_length=4)
    year_to: str = Field(default="", alias="yearTo", max_length=4)
    science125_id: str | None = Field(
        default=None,
        alias="science125Id",
        pattern=r"^S125-(?:0(?:0[1-9]|[1-9]\d)|1(?:[01]\d|2[0-5]))$",
    )

    model_config = {"populate_by_name": True, "extra": "forbid"}

    @field_validator("year_from", "year_to")
    @classmethod
    def validate_year(cls, value: str) -> str:
        value = value.strip()
        if value and (len(value) != 4 or not value.isdigit()):
            raise ValueError("Year must be a four-digit value.")
        return value


class LabSuggestPayload(BaseModel):
    record_id: int = Field(alias="recordId", ge=1)

    model_config = {"populate_by_name": True, "extra": "forbid"}


class PromptPolishPayload(BaseModel):
    text: str = Field(min_length=1, max_length=12000)

    model_config = {"extra": "forbid"}

    @field_validator("text")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip()


class HypothesisSourcePageSelection(BaseModel):
    document_id: int = Field(alias="documentId", ge=1)
    pages: list[int] = Field(min_length=1, max_length=20)
    pdf_sha256: str = Field(alias="pdfSha256", pattern=r"^[0-9a-f]{64}$")
    text_sha256: str = Field(alias="textSha256", pattern=r"^[0-9a-f]{64}$")
    max_chars: int = Field(default=12_000, alias="maxChars", ge=1, le=50_000)

    model_config = {"populate_by_name": True, "extra": "forbid"}

    @field_validator("pages")
    @classmethod
    def normalize_pages(cls, values: list[int]) -> list[int]:
        if any(value < 1 for value in values):
            raise ValueError("Page numbers must be positive.")
        normalized = sorted(set(values))
        if len(normalized) != len(values):
            raise ValueError("Page numbers must be unique.")
        return normalized


class HypothesisGeneratePayload(BaseModel):
    research_question: str = Field(default="", alias="researchQuestion", max_length=12000)
    supplemental_context: str = Field(
        default="",
        alias="supplementalContext",
        validation_alias=AliasChoices("supplementalContext", "literatureText"),
        max_length=40000,
    )
    max_iterations: int = Field(default=2, alias="maxIterations", ge=1, le=5)
    hitl_enabled: bool = Field(default=True, alias="hitlEnabled")
    auto_verify: bool = Field(default=True, alias="autoVerify")
    domain: HypothesisDomain = ""
    source_doc_ids: list[int] = Field(default_factory=list, alias="sourceDocIds", max_length=20)
    source_page_selections: list[HypothesisSourcePageSelection] = Field(
        default_factory=list,
        alias="sourcePageSelections",
        max_length=10,
    )
    multimodal_run_ids: list[str] = Field(default_factory=list, alias="multimodalRunIds", max_length=20)
    science125_id: str | None = Field(
        default=None,
        alias="science125Id",
        pattern=r"^S125-(?:0(?:0[1-9]|[1-9]\d)|1(?:[01]\d|2[0-5]))$",
    )
    literature_search_job_id: str | None = Field(
        default=None,
        alias="literatureSearchJobId",
        min_length=1,
        max_length=64,
    )
    reviewed_evidence_ids: list[str] = Field(
        default_factory=list,
        alias="reviewedEvidenceIds",
        max_length=100,
    )

    model_config = {"populate_by_name": True, "extra": "forbid"}

    @field_validator("research_question", "supplemental_context")
    @classmethod
    def strip_hypothesis_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("source_doc_ids")
    @classmethod
    def normalize_source_doc_ids(cls, values: list[int]) -> list[int]:
        if any(value < 1 for value in values):
            raise ValueError("Source document IDs must be positive.")
        normalized = sorted(set(values))
        if len(normalized) != len(values):
            raise ValueError("Source document IDs must be unique.")
        return normalized

    @field_validator("multimodal_run_ids")
    @classmethod
    def normalize_multimodal_run_ids(cls, values: list[str]) -> list[str]:
        normalized = [value.strip() for value in values]
        if any(not value or len(value) > 64 for value in normalized):
            raise ValueError("Multimodal run IDs are invalid.")
        if len(set(normalized)) != len(normalized):
            raise ValueError("Multimodal run IDs must be unique.")
        return normalized

    @field_validator("reviewed_evidence_ids")
    @classmethod
    def normalize_reviewed_evidence_ids(cls, values: list[str]) -> list[str]:
        normalized = [value.strip() for value in values]
        if any(not value or len(value) > 300 for value in normalized):
            raise ValueError("Reviewed evidence IDs are invalid.")
        if len(set(normalized)) != len(normalized):
            raise ValueError("Reviewed evidence IDs must be unique.")
        return normalized

    @model_validator(mode="after")
    def require_hypothesis_input(self):
        if self.source_page_selections and not self.science125_id:
            raise ValueError("Page-level evidence is only available for Science 125 inputs.")
        document_ids = [selection.document_id for selection in self.source_page_selections]
        if len(document_ids) != len(set(document_ids)):
            raise ValueError("Each source document may have only one page selection.")
        if (
            not self.research_question
            and not self.supplemental_context
            and not self.source_doc_ids
            and not self.source_page_selections
            and not self.reviewed_evidence_ids
        ):
            raise ValueError("Provide a research question, source document, or supplemental context.")
        if self.science125_id:
            if self.research_question or self.supplemental_context or self.source_doc_ids or self.multimodal_run_ids or self.domain:
                raise ValueError(
                    "Science 125 generation accepts only its authoritative item ID and reviewed evidence/page selections."
                )
            if not self.literature_search_job_id:
                raise ValueError("Science 125 generation requires the completed literature search job ID.")
            if not self.reviewed_evidence_ids:
                raise ValueError("Science 125 generation requires reviewed evidence IDs.")
        return self


class EvidenceQueryPayload(BaseModel):
    domain: str = Field(default="", max_length=120)
    query_text: str = Field(default="", alias="queryText", max_length=12000)
    reaction_type: str = Field(default="", alias="reactionType", max_length=120)
    battery_type: str = Field(default="", alias="batteryType", max_length=120)
    ion_type: str = Field(default="", alias="ionType", max_length=120)
    material: str = Field(default="", max_length=240)
    metric_names: list[str] | None = Field(default=None, alias="metricNames", max_length=100)
    adsorbates: list[str] | None = Field(default=None, max_length=100)
    max_each: int = Field(default=6, alias="maxEach", ge=1, le=100)

    model_config = {"populate_by_name": True, "extra": "forbid"}


class HypothesisReportPayload(BaseModel):
    hypothesis_id: int = Field(alias="hypothesisId", ge=1)
    enhance: bool = True

    model_config = {"populate_by_name": True, "extra": "forbid"}


class HypothesisWorkflowExportPayload(BaseModel):
    hypothesis_id: int = Field(alias="hypothesisId", ge=1)

    model_config = {"populate_by_name": True, "extra": "forbid"}


class DocumentAnalysisPayload(BaseModel):
    document_id: int = Field(alias="documentId", ge=1)
    analysis_type: DocumentAnalysisType = Field(alias="analysisType")
    chemical_name: str | None = Field(default=None, alias="chemicalName", min_length=1, max_length=200)
    use_llm_page_hints: bool = Field(default=False, alias="useLlmPageHints")

    model_config = {"populate_by_name": True, "extra": "forbid"}

    @model_validator(mode="after")
    def validate_type_options(self):
        if self.chemical_name is not None:
            self.chemical_name = self.chemical_name.strip()
            if self.analysis_type != "structures":
                raise ValueError("chemicalName is only supported for structure analysis.")
        if self.use_llm_page_hints and self.analysis_type != "images":
            raise ValueError("useLlmPageHints is only supported for image analysis.")
        return self


class DocumentComparePayload(BaseModel):
    document_ids: list[int] = Field(alias="documentIds", min_length=2, max_length=5)

    model_config = {"populate_by_name": True, "extra": "forbid"}

    @field_validator("document_ids")
    @classmethod
    def validate_document_ids(cls, values: list[int]) -> list[int]:
        if any(value < 1 for value in values):
            raise ValueError("Document IDs must be positive.")
        normalized = sorted(set(values))
        if len(normalized) != len(values):
            raise ValueError("Document IDs must be unique.")
        return normalized


class UploadMultimodalSource(BaseModel):
    source_type: Literal["upload"] = Field(alias="sourceType")
    asset_id: str = Field(alias="assetId", min_length=1, max_length=64)
    sheet_names: list[str] = Field(default_factory=list, alias="sheetNames", max_length=10)

    model_config = {"populate_by_name": True, "extra": "forbid"}

    @field_validator("sheet_names")
    @classmethod
    def normalize_sheet_names(cls, values: list[str]) -> list[str]:
        normalized = [value.strip() for value in values]
        if any(not value or len(value) > 120 for value in normalized):
            raise ValueError("Sheet names are invalid.")
        if len(set(normalized)) != len(normalized):
            raise ValueError("Sheet names must be unique.")
        return normalized


class DocumentMultimodalSource(BaseModel):
    source_type: Literal["documentAsset"] = Field(alias="sourceType")
    document_id: int = Field(alias="documentId", ge=1)
    asset_id: str = Field(alias="assetId", min_length=1, max_length=64)

    model_config = {"populate_by_name": True, "extra": "forbid"}


MultimodalSource = Annotated[
    Union[UploadMultimodalSource, DocumentMultimodalSource],
    Field(discriminator="source_type"),
]


class MultimodalAnalyzePayload(BaseModel):
    sources: list[MultimodalSource] = Field(min_length=1, max_length=50)
    question: str = Field(default="", max_length=4000)
    use_literature_context: bool = Field(default=True, alias="useLiteratureContext")

    model_config = {"populate_by_name": True, "extra": "forbid"}

    @field_validator("question")
    @classmethod
    def strip_question(cls, value: str) -> str:
        return value.strip()

    @model_validator(mode="after")
    def unique_sources(self):
        identities = []
        for source in self.sources:
            if isinstance(source, UploadMultimodalSource):
                identities.append((source.source_type, source.asset_id))
            else:
                identities.append((source.source_type, source.document_id, source.asset_id))
        if len(set(identities)) != len(identities):
            raise ValueError("Multimodal sources must be unique.")
        return self


class ManualModelingSource(BaseModel):
    type: Literal["manual"]
    text: str = Field(min_length=1, max_length=60000)

    model_config = {"extra": "forbid"}

    @field_validator("text")
    @classmethod
    def strip_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Modeling text is required.")
        return value


class DocumentModelingSource(BaseModel):
    type: Literal["document"]
    document_id: int = Field(alias="documentId", ge=1)

    model_config = {"populate_by_name": True, "extra": "forbid"}


class HypothesisModelingSource(BaseModel):
    type: Literal["hypothesis"]
    hypothesis_id: int = Field(alias="hypothesisId", ge=1)

    model_config = {"populate_by_name": True, "extra": "forbid"}


ModelingSource = Annotated[
    Union[ManualModelingSource, DocumentModelingSource, HypothesisModelingSource],
    Field(discriminator="type"),
]
ModelingMode = Literal["vasp", "ms", "both"]
VaspCalcType = Literal["auto", "scf", "relax", "dos", "band", "phonon", "optics"]
VaspkitTask = Literal["", "geometry_optimization", "scf", "band_structure", "dos", "phonon", "elastic", "bader"]


class ModelingGeneratePayload(BaseModel):
    source: ModelingSource
    mode: ModelingMode = "vasp"
    calc_type: VaspCalcType = Field(default="auto", alias="calcType")
    vaspkit_task: VaspkitTask = Field(default="", alias="vaspkitTask")

    model_config = {"populate_by_name": True, "extra": "forbid"}


class ModelingExportPayload(BaseModel):
    workspace_id: str = Field(alias="workspaceId", min_length=1, max_length=64)
    expected_revision: int = Field(alias="expectedRevision", ge=1)

    model_config = {"populate_by_name": True, "extra": "forbid"}


PAYLOAD_MODELS = {
    "rag_qa": RagQaPayload,
    "literature_search": LiteratureSearchPayload,
    "lab_suggest": LabSuggestPayload,
    "prompt_polish": PromptPolishPayload,
    "hypothesis_generate": HypothesisGeneratePayload,
    "hypothesis_report": HypothesisReportPayload,
    "hypothesis_workflow_export": HypothesisWorkflowExportPayload,
    "evidence_query": EvidenceQueryPayload,
    "document_analysis": DocumentAnalysisPayload,
    "document_compare": DocumentComparePayload,
    "multimodal_analyze": MultimodalAnalyzePayload,
    "modeling_generate": ModelingGeneratePayload,
    "modeling_export": ModelingExportPayload,
}


class JobCreate(BaseModel):
    type: CreatableJobType
    payload: dict[str, Any] = Field(default_factory=dict)

    def validated_payload(self) -> dict[str, Any]:
        model = PAYLOAD_MODELS[self.type].model_validate(self.payload)
        return model.model_dump(by_alias=True, exclude_none=True)


class HypothesisStructuredFeedback(BaseModel):
    citation_authenticity: Literal["verified", "needsVerification", "suspectedInvalid"] | None = Field(
        default=None,
        alias="citationAuthenticity",
    )
    over_extension: Literal["none", "minor", "major"] | None = Field(default=None, alias="overExtension")
    falsifiability: Literal["falsifiable", "needsCriteria", "notFalsifiable"] | None = None
    experiment_feasibility: Literal["feasible", "needsAdjustment", "infeasible"] | None = Field(
        default=None,
        alias="experimentFeasibility",
    )
    baseline_need: Literal["none", "recommended", "required"] | None = Field(default=None, alias="baselineNeed")
    reference_replacement: Literal["none", "recommended", "required"] | None = Field(
        default=None,
        alias="referenceReplacement",
    )

    model_config = {"populate_by_name": True, "extra": "forbid"}


class HypothesisFeedbackInput(BaseModel):
    action: Literal["approve", "revise", "skip"]
    feedback_text: str = Field(default="", alias="feedbackText", max_length=12000)
    edited_hypothesis: dict[str, Any] | None = Field(default=None, alias="editedHypothesis")
    debate_stance: Literal["neutral", "devil", "optimist", "custom"] = Field(
        default="neutral",
        alias="debateStance",
    )
    selected_attack_indices: list[int] = Field(default_factory=list, alias="selectedAttackIndices", max_length=100)
    structured_feedback: HypothesisStructuredFeedback = Field(
        default_factory=HypothesisStructuredFeedback,
        alias="structuredFeedback",
    )

    model_config = {"populate_by_name": True, "extra": "forbid"}

    @field_validator("feedback_text")
    @classmethod
    def strip_feedback_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("selected_attack_indices")
    @classmethod
    def normalize_attack_indices(cls, values: list[int]) -> list[int]:
        if any(value < 0 for value in values):
            raise ValueError("Attack indices must be non-negative.")
        normalized = sorted(set(values))
        if len(normalized) != len(values):
            raise ValueError("Attack indices must be unique.")
        return normalized

    @model_validator(mode="after")
    def validate_feedback_action(self):
        if self.action == "skip" and self.edited_hypothesis is not None:
            raise ValueError("Edited hypotheses require approve or revise.")
        if self.edited_hypothesis is not None:
            encoded = json.dumps(self.edited_hypothesis, ensure_ascii=False, default=str)
            if len(encoded) > 200000:
                raise ValueError("The edited hypothesis is too large.")
        return self


class FeedbackInput(BaseModel):
    feedback: dict[str, Any] = Field(default_factory=dict)
