from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field, model_validator
from app.schemas.research import Science125Domain


Science125Method = Literal[
    "proof",
    "experimental",
    "observational",
    "clinical",
    "engineering",
    "computational",
    "systems_policy",
]
Science125ClassificationReviewStatus = Literal["reviewed"]
Science125PromptReviewStatus = Literal["draft_pending_review", "team_reviewed"]
Science125CrossDomainTag = Annotated[
    str,
    Field(pattern=r"^[a-z][a-z0-9_]*$", max_length=80),
]
Science125BatchStatus = Literal["DRAFT", "RUNNING", "PAUSED", "SUCCEEDED", "FAILED", "CANCELLED"]
Science125ReportStatus = Literal["PENDING", "RETRIEVING", "EVIDENCE_READY", "BLOCKED_EVIDENCE", "GENERATING", "SUCCEEDED", "FAILED", "RETRYING"]
Science125ExportFormat = Literal["docx", "json"]


class Science125ContractModel(BaseModel):
    model_config = {
        "extra": "forbid",
        "frozen": True,
        "populate_by_name": True,
    }


class Science125MethodProfile(Science125ContractModel):
    primary: Science125Method
    secondary: tuple[Science125Method, ...] = Field(default_factory=tuple, max_length=2)

    @model_validator(mode="after")
    def require_distinct_methods(self) -> Science125MethodProfile:
        if self.primary in self.secondary:
            raise ValueError("The primary method cannot also be a secondary method.")
        if len(self.secondary) != len(set(self.secondary)):
            raise ValueError("Secondary methods must be unique.")
        return self


class Science125RoutingFields(Science125ContractModel):
    benchmark_domain: str = Field(alias="benchmarkDomain", min_length=1, max_length=100)
    primary_subdomain: str = Field(
        alias="primarySubdomain",
        pattern=r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$",
        max_length=120,
    )
    cross_domain_tags: tuple[Science125CrossDomainTag, ...] = Field(
        alias="crossDomainTags",
        max_length=4,
    )
    method_profile: Science125MethodProfile = Field(alias="methodProfile")
    prompt_profile: str = Field(
        alias="promptProfile",
        pattern=r"^s125\.[a-z_]+\.v1$",
        max_length=100,
    )
    retrieval_profile: str = Field(
        alias="retrievalProfile",
        pattern=r"^retrieval\.[a-z0-9_.]+\.v1$",
        max_length=140,
    )
    classification_review_status: Science125ClassificationReviewStatus = Field(
        alias="classificationReviewStatus"
    )


class Science125RoutingEntry(Science125RoutingFields):
    question_id: str = Field(alias="questionId", pattern=r"^S125-\d{3}$")


class Science125RoutingManifest(Science125ContractModel):
    routing_version: Literal["science125-routing-v1"] = Field(alias="routingVersion")
    base_manifest_version: Literal["science125-v1"] = Field(alias="baseManifestVersion")
    base_manifest_content_sha256: str = Field(
        alias="baseManifestContentSha256",
        pattern=r"^[0-9a-f]{64}$",
    )
    routing_content_sha256: str = Field(
        alias="routingContentSha256",
        pattern=r"^[0-9a-f]{64}$",
    )
    questions: tuple[Science125RoutingEntry, ...] = Field(min_length=125, max_length=125)


class Science125QuestionOut(Science125RoutingFields):
    id: str = Field(pattern=r"^S125-\d{3}$")
    question: str = Field(min_length=1, max_length=1000)
    question_zh: str | None = Field(default=None, alias="questionZh", min_length=1, max_length=1000)
    source_domain: str = Field(alias="sourceDomain", min_length=1, max_length=100)
    pdf_page: int = Field(alias="pdfPage", ge=1)
    booklet_page: int = Field(alias="bookletPage", ge=1)


class Science125QuestionListOut(Science125ContractModel):
    manifest_version: Literal["science125-v1"] = Field(alias="manifestVersion")
    routing_version: Literal["science125-routing-v1"] = Field(alias="routingVersion")
    data: tuple[Science125QuestionOut, ...]


class Science125QuestionDetailOut(Science125ContractModel):
    id: str = Field(pattern=r"^S125-\d{3}$")
    headline: str = Field(min_length=1, max_length=1000)
    headline_zh: str | None = Field(default=None, alias="headlineZh", min_length=1, max_length=1000)
    source_context: str = Field(alias="sourceContext", min_length=1, max_length=100_000)
    context_sha256: str = Field(alias="contextSha256", pattern=r"^[0-9a-f]{64}$")
    pdf_page: int = Field(alias="pdfPage", ge=1)
    booklet_page: int = Field(alias="bookletPage", ge=1)
    extraction_version: str = Field(alias="extractionVersion", min_length=1, max_length=100)
    availability: Literal["available"] = "available"



class Science125ProviderOut(Science125ContractModel):
    provider_id: str = Field(alias="providerId", pattern=r"^[a-z][a-z0-9_]*$", max_length=80)
    display_name: str = Field(alias="displayName", min_length=1, max_length=120)
    base_url: str = Field(alias="baseUrl", pattern=r"^https://", max_length=500)
    policy_source_url: str = Field(alias="policySourceUrl", pattern=r"^https://", max_length=500)
    auth_mode: Literal["none", "optional", "required"] = Field(alias="authMode")
    is_required: bool = Field(alias="isRequired")


class Science125ProviderReadinessOut(Science125ContractModel):
    provider_id: str = Field(alias="providerId", pattern=r"^[a-z][a-z0-9_]*$", max_length=80)
    ready: bool
    status: Literal["ready", "degraded", "blocked"]
    missing_configuration_codes: tuple[str, ...] = Field(
        alias="missingConfigurationCodes",
        max_length=8,
    )


class Science125QuestionProfileOut(Science125RoutingFields):
    question_id: str = Field(alias="questionId", pattern=r"^S125-\d{3}$")
    routing_version: Literal["science125-routing-v1"] = Field(alias="routingVersion")
    localization_version: Literal["science125-zh-CN-v1"] | None = Field(
        default=None,
        alias="localizationVersion",
    )
    question_zh: str | None = Field(default=None, alias="questionZh", min_length=1, max_length=1000)
    search_intent_zh: str | None = Field(default=None, alias="searchIntentZh", min_length=1, max_length=4000)
    recommended_query: str | None = Field(default=None, alias="recommendedQuery", min_length=1, max_length=4000)
    translation_review_status: Literal["reviewed", "translated_pending_review"] | None = Field(
        default=None,
        alias="translationReviewStatus",
    )
    prompt_version: Literal["science125-prompts-v1", "science125-prompts-v2"] = Field(
        alias="promptVersion"
    )
    prompt_module_hash: str = Field(alias="promptModuleHash", pattern=r"^[0-9a-f]{64}$")
    prompt_review_status: Science125PromptReviewStatus = Field(alias="promptReviewStatus")
    ready: bool
    pilot_enabled: bool = Field(alias="pilotEnabled")
    missing_configuration_codes: tuple[str, ...] = Field(
        alias="missingConfigurationCodes",
        max_length=12,
    )
    providers: tuple[Science125ProviderOut, ...]
    provider_readiness: tuple[Science125ProviderReadinessOut, ...] = Field(
        alias="providerReadiness"
    )


class Science125BatchCreateInput(Science125ContractModel):
    question_ids: tuple[str, ...] | None = Field(default=None, alias="questionIds")

    @model_validator(mode="after")
    def validate_question_ids(self) -> Science125BatchCreateInput:
        if self.question_ids is not None and len(set(self.question_ids)) != len(self.question_ids):
            raise ValueError("Science 125 batch question IDs must be unique.")
        return self


class Science125BatchRetryInput(Science125ContractModel):
    question_ids: tuple[str, ...] | None = Field(default=None, alias="questionIds")

    @model_validator(mode="after")
    def validate_question_ids(self) -> Science125BatchRetryInput:
        if self.question_ids is not None and len(set(self.question_ids)) != len(self.question_ids):
            raise ValueError("Science 125 retry question IDs must be unique.")
        return self


class Science125BatchSummaryOut(Science125ContractModel):
    batch_id: str = Field(alias="batchId", pattern=r"^[0-9a-f-]{36}$")
    manifest_version: Literal["science125-v1"] = Field(alias="manifestVersion")
    manifest_sha256: str = Field(alias="manifestSha256", pattern=r"^[0-9a-f]{64}$")
    routing_version: Literal["science125-routing-v1"] = Field(alias="routingVersion")
    routing_sha256: str = Field(alias="routingSha256", pattern=r"^[0-9a-f]{64}$")
    prompt_version: str = Field(alias="promptVersion", min_length=1, max_length=80)
    prompt_registry_sha256: str = Field(
        alias="promptRegistrySha256",
        pattern=r"^[0-9a-f]{64}$",
    )
    model: str = Field(min_length=1, max_length=120)
    status: Science125BatchStatus
    total_count: int = Field(alias="totalCount", ge=0)
    succeeded_count: int = Field(alias="succeededCount", ge=0)
    failed_count: int = Field(alias="failedCount", ge=0)
    blocked_evidence_count: int = Field(alias="blockedEvidenceCount", ge=0)
    total_tokens: int = Field(alias="totalTokens", ge=0)
    estimated_cost_cny: float = Field(alias="estimatedCostCny", ge=0)
    started_at: str | None = Field(default=None, alias="startedAt")
    completed_at: str | None = Field(default=None, alias="completedAt")
    created_at: str = Field(alias="createdAt")
    updated_at: str = Field(alias="updatedAt")


class Science125ExportOut(Science125ContractModel):
    export_id: str = Field(alias="exportId", pattern=r"^[0-9a-f-]{36}$")
    batch_id: str = Field(alias="batchId", pattern=r"^[0-9a-f-]{36}$")
    report_id: str | None = Field(default=None, alias="reportId", pattern=r"^[0-9a-f-]{36}$")
    format: Science125ExportFormat
    file_name: str = Field(alias="fileName", min_length=1, max_length=255)
    mime_type: str = Field(alias="mimeType", min_length=1, max_length=120)
    size_bytes: int = Field(alias="sizeBytes", ge=0)
    created_at: str = Field(alias="createdAt")


class Science125ReportSummaryOut(Science125ContractModel):
    report_id: str = Field(alias="reportId", pattern=r"^[0-9a-f-]{36}$")
    batch_id: str = Field(alias="batchId", pattern=r"^[0-9a-f-]{36}$")
    question_id: str = Field(alias="questionId", pattern=r"^S125-\d{3}$")
    question: str = Field(min_length=1, max_length=1000)
    question_zh: str | None = Field(default=None, alias="questionZh", min_length=1, max_length=1000)
    benchmark_domain: Science125Domain = Field(alias="benchmarkDomain")
    primary_subdomain: str = Field(alias="primarySubdomain", min_length=1, max_length=120)
    status: Science125ReportStatus
    attempt_number: int = Field(alias="attemptNumber", ge=1)
    selected_hypothesis_id: str | None = Field(default=None, alias="selectedHypothesisId")
    selected_hypothesis_confidence: float | None = Field(default=None, alias="selectedHypothesisConfidence", ge=0, le=1)
    selected_hypothesis_reason: str | None = Field(default=None, alias="selectedHypothesisReason", max_length=4000)
    evidence_status: Literal["sufficient", "partial", "insufficient"] = Field(alias="evidenceStatus")
    selected_evidence_count: int = Field(alias="selectedEvidenceCount", ge=0)
    provider_families: tuple[str, ...] = Field(alias="providerFamilies")
    model: str | None = Field(default=None, min_length=1, max_length=120)
    request_id: str | None = Field(default=None, alias="requestId", min_length=1, max_length=200)
    total_tokens: int = Field(alias="totalTokens", ge=0)
    latency_ms: int = Field(alias="latencyMs", ge=0)
    estimated_cost_cny: float = Field(alias="estimatedCostCny", ge=0)
    created_at: str = Field(alias="createdAt")
    updated_at: str = Field(alias="updatedAt")
    source_type: Literal["interactive_job", "batch"] = Field(alias="sourceType")
    source_job_id: str | None = Field(default=None, alias="sourceJobId", pattern=r"^[0-9a-f-]{36}$")


class Science125ReportOut(Science125ReportSummaryOut):
    retrieval_query: str = Field(alias="retrievalQuery", max_length=4000)
    retrieval_query_zh: str | None = Field(default=None, alias="retrievalQueryZh", min_length=1, max_length=4000)
    refinement_queries: tuple[str, ...] = Field(alias="refinementQueries")
    retrieval_snapshot: dict[str, object] = Field(alias="retrievalSnapshot")
    evidence_snapshot: dict[str, object] = Field(alias="evidenceSnapshot")
    evidence_snapshot_sha256: str = Field(alias="evidenceSnapshotSha256", pattern=r"^[0-9a-f]{64}$")
    research_output: dict[str, object] | None = Field(default=None, alias="researchOutput")
    provenance: dict[str, object] | None = None
    export_artifacts: tuple[Science125ExportOut, ...] = Field(alias="exportArtifacts")


class Science125BatchOut(Science125BatchSummaryOut):
    question_ids: tuple[str, ...] = Field(alias="questionIds")
    reports: tuple[Science125ReportSummaryOut, ...] = Field(default_factory=tuple)


class Science125BatchExportInput(Science125ContractModel):
    format: Science125ExportFormat


class Science125ReportExportInput(Science125ContractModel):
    format: Science125ExportFormat


__all__ = [
    "Science125ClassificationReviewStatus",
    "Science125ContractModel",
    "Science125Method",
    "Science125MethodProfile",
    "Science125BatchCreateInput",
    "Science125BatchExportInput",
    "Science125BatchOut",
    "Science125BatchRetryInput",
    "Science125BatchStatus",
    "Science125BatchSummaryOut",
    "Science125ExportFormat",
    "Science125ExportOut",
    "Science125ReportExportInput",
    "Science125ReportOut",
    "Science125ReportStatus",
    "Science125ReportSummaryOut",
    "Science125ProviderOut",
    "Science125ProviderReadinessOut",
    "Science125PromptReviewStatus",
    "Science125QuestionDetailOut",
    "Science125QuestionListOut",
    "Science125QuestionOut",
    "Science125QuestionProfileOut",
    "Science125RoutingEntry",
    "Science125RoutingManifest",
]
