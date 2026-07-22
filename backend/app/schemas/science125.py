from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field, model_validator


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
Science125CrossDomainTag = Annotated[
    str,
    Field(pattern=r"^[a-z][a-z0-9_]*$", max_length=80),
]


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


__all__ = [
    "Science125ClassificationReviewStatus",
    "Science125ContractModel",
    "Science125Method",
    "Science125MethodProfile",
    "Science125ProviderOut",
    "Science125ProviderReadinessOut",
    "Science125QuestionDetailOut",
    "Science125QuestionListOut",
    "Science125QuestionOut",
    "Science125QuestionProfileOut",
    "Science125RoutingEntry",
    "Science125RoutingManifest",
]
