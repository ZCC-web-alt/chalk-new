from __future__ import annotations

import json
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, GetJsonSchemaHandler, field_validator, model_validator
from pydantic.alias_generators import to_camel
from pydantic.json_schema import JsonSchemaValue


ResearchProfile = Literal["general_science", "chemistry"]
HypothesisVerdict = Literal["supports", "refutes", "insufficient_evidence"]
NonEmptyString = Annotated[str, Field(min_length=1)]
JsonPointer = Annotated[str, Field(min_length=1, pattern=r"^/(?:[^~]|~[01])*$")]
Science125Domain = Literal[
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
]
Science125Method = Literal[
    "proof",
    "experimental",
    "observational",
    "clinical",
    "engineering",
    "computational",
    "systems_policy",
]
Science125CrossDomainTag = Literal[
    "mathematics",
    "chemistry",
    "physics",
    "biology",
    "medicine",
    "astronomy",
    "engineering",
    "materials",
    "information",
    "ai",
    "neuroscience",
    "ecology",
    "energy",
    "earth_science",
    "climate",
    "space_systems",
    "social_science",
    "cognitive_science",
    "psychology",
    "evolution",
    "genetics",
    "immunology",
    "pharmacology",
    "public_health",
    "nanomedicine",
    "robotics",
    "agriculture",
    "geology",
    "philosophy",
    "economics",
    "quantum",
    "mathematical_sciences",
    "medicine_health",
    "engineering_materials",
    "information_science",
    "energy_science",
    "artificial_intelligence",
]
Science125DomainCheck = Literal[
    "source_traceability",
    "negative_evidence",
    "measurement_plan",
    "operational_definition",
    "uncertainty_budget",
    "selection_effects",
    "replication",
    "safety_boundary",
    "data_leakage",
    "applicability_boundary",
]


class ResearchContractModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
        str_strip_whitespace=True,
    )


class ResearchBrief(ResearchContractModel):
    research_question: NonEmptyString
    background: NonEmptyString
    objectives: list[NonEmptyString] = Field(min_length=1)
    scope: NonEmptyString


class ObservablePrediction(ResearchContractModel):
    id: NonEmptyString
    observable: NonEmptyString
    expected_direction: Literal["increase", "decrease", "no_change", "nonlinear", "qualitative"]
    measurement: NonEmptyString
    falsification_threshold: NonEmptyString


class HypothesisFields(ResearchContractModel):
    title: NonEmptyString
    statement: NonEmptyString
    mechanism: NonEmptyString
    prerequisites: list[NonEmptyString] = Field(min_length=1)
    predictions: list[ObservablePrediction] = Field(min_length=1)
    supporting_evidence_refs: list[NonEmptyString]
    counter_evidence: list[NonEmptyString]
    evidence_gaps: list[NonEmptyString]
    falsification_criteria: list[NonEmptyString] = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)


class CandidateHypothesis(HypothesisFields):
    id: Literal["H1", "H2", "H3", "H4", "H5"]
    status: Literal["candidate", "supported", "refuted", "insufficient_evidence"]


class NullHypothesis(HypothesisFields):
    id: Literal["H0"]
    status: Literal["null"]


class EvidenceClaim(ResearchContractModel):
    id: NonEmptyString
    claim: NonEmptyString
    stance: Literal["supports", "refutes", "context", "uncertain"]
    source_refs: list[NonEmptyString] = Field(min_length=1)
    strength: float = Field(ge=0, le=1)
    limitations: list[NonEmptyString]


class MeasurementMethod(ResearchContractModel):
    variable: NonEmptyString
    method: NonEmptyString
    unit: NonEmptyString
    schedule: NonEmptyString


class ResearchPlan(ResearchContractModel):
    independent_variables: list[NonEmptyString] = Field(min_length=1)
    dependent_variables: list[NonEmptyString] = Field(min_length=1)
    control_variables: list[NonEmptyString] = Field(min_length=1)
    measurements: list[MeasurementMethod] = Field(min_length=1)
    decision_thresholds: list[NonEmptyString] = Field(min_length=1)
    stop_conditions: list[NonEmptyString] = Field(min_length=1)
    resources: list[NonEmptyString]
    risks: list[NonEmptyString]
    uncertainties: list[NonEmptyString]
    applicability_boundaries: list[NonEmptyString] = Field(min_length=1)


class ResearchQuality(ResearchContractModel):
    factual_accuracy: float = Field(ge=0, le=1)
    explainability: float = Field(ge=0, le=1)
    completeness: float = Field(ge=0, le=1)
    technical_depth: float = Field(ge=0, le=1)
    applicability: float = Field(ge=0, le=1)
    overall: float = Field(ge=0, le=1)
    notes: list[NonEmptyString]


class Science125MethodProfile(ResearchContractModel):
    primary: Science125Method
    secondary: list[Science125Method] = Field(default_factory=list, max_length=2)

    @field_validator("secondary")
    @classmethod
    def require_unique_secondary(cls, values: list[Science125Method]) -> list[Science125Method]:
        if len(set(values)) != len(values):
            raise ValueError("Science 125 secondary methods must be unique.")
        return values


class Science125Extension(ResearchContractModel):
    question_id: str = Field(alias="questionId", pattern=r"^S125-(?:0(?:0[1-9]|[1-9]\d)|1(?:[01]\d|2[0-5]))$")
    routing_version: Literal["science125-routing-v1"] = Field(alias="routingVersion")
    benchmark_domain: Science125Domain = Field(alias="benchmarkDomain")
    primary_subdomain: NonEmptyString = Field(alias="primarySubdomain", max_length=120)
    cross_domain_tags: list[Science125CrossDomainTag] = Field(alias="crossDomainTags", max_length=8)
    method_profile: Science125MethodProfile = Field(alias="methodProfile")
    prompt_profile: NonEmptyString = Field(alias="promptProfile", max_length=120)
    retrieval_profile: NonEmptyString = Field(alias="retrievalProfile", max_length=120)
    evidence_status: Literal["sufficient", "partial", "insufficient"] = Field(alias="evidenceStatus")
    domain_checks: list[Science125DomainCheck] = Field(alias="domainChecks", min_length=1, max_length=16)

    @field_validator("cross_domain_tags", "domain_checks")
    @classmethod
    def require_unique_items(cls, values: list[str]) -> list[str]:
        if len(set(values)) != len(values):
            raise ValueError("Science 125 extension lists must not contain duplicates.")
        return values


class ModelProvenance(ResearchContractModel):
    provider: NonEmptyString
    model: NonEmptyString
    request_id: NonEmptyString
    generated_at: datetime
    prompt_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    response_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class ChemistryDetails(ResearchContractModel):
    catalyst_system: str | None = None
    active_sites: list[NonEmptyString] = Field(default_factory=list)
    reaction_pathways: list[NonEmptyString] = Field(default_factory=list)
    intermediates: list[NonEmptyString] = Field(default_factory=list)
    descriptors: list[NonEmptyString] = Field(default_factory=list)
    electrolyte: str | None = None
    adsorption_free_energies: list[NonEmptyString] = Field(default_factory=list)
    electrochemical_metrics: list[NonEmptyString] = Field(default_factory=list)
    dft_plan: list[NonEmptyString] = Field(default_factory=list)
    characterization_plan: list[NonEmptyString] = Field(default_factory=list)


class ResearchOutput(ResearchContractModel):
    contract_version: Literal["research-v1"]
    profile: ResearchProfile
    chemistry_subdomain: str | None = Field(default=None, min_length=1, max_length=120)
    chemistry: ChemistryDetails | None = None
    brief: ResearchBrief
    hypotheses: list[CandidateHypothesis] = Field(min_length=3, max_length=5)
    null_hypothesis: NullHypothesis
    evidence_claims: list[EvidenceClaim]
    research_plan: ResearchPlan
    quality: ResearchQuality
    provenance: ModelProvenance
    science125: Science125Extension | None = None

    @field_validator("hypotheses")
    @classmethod
    def require_sequential_hypothesis_ids(
        cls,
        hypotheses: list[CandidateHypothesis],
    ) -> list[CandidateHypothesis]:
        expected = [f"H{index}" for index in range(1, len(hypotheses) + 1)]
        actual = [hypothesis.id for hypothesis in hypotheses]
        if actual != expected:
            raise ValueError("Candidate hypothesis IDs must be sequential and ordered from H1.")
        return hypotheses

    @model_validator(mode="after")
    def require_profile_specific_fields(self) -> ResearchOutput:
        if self.profile == "chemistry" and self.chemistry_subdomain is None:
            raise ValueError("chemistrySubdomain is required for the chemistry profile.")
        if self.profile == "general_science" and (
                self.chemistry_subdomain is not None or self.chemistry is not None
        ):
            raise ValueError("Chemistry-specific fields are only valid for the chemistry profile.")
        if self.science125 is not None and self.profile != "general_science":
            raise ValueError("Science 125 output must use the general_science profile.")

        evidence_ids = [claim.id for claim in self.evidence_claims]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("Evidence claim IDs must be unique.")
        known_evidence_ids = set(evidence_ids)
        for hypothesis in [*self.hypotheses, self.null_hypothesis]:
            unknown_refs = set(hypothesis.supporting_evidence_refs) - known_evidence_ids
            if unknown_refs:
                refs = ", ".join(sorted(unknown_refs))
                raise ValueError(
                    f"Hypothesis {hypothesis.id} references unknown evidence claims: {refs}."
                )
        return self

    @classmethod
    def __get_pydantic_json_schema__(
        cls,
        core_schema: Any,
        handler: GetJsonSchemaHandler,
    ) -> JsonSchemaValue:
        schema = handler(core_schema)
        hypotheses = schema["properties"]["hypotheses"]
        candidate_ref = hypotheses["items"]
        hypotheses["prefixItems"] = [
            {
                "allOf": [candidate_ref],
                "properties": {"id": {"const": f"H{index}"}},
            }
            for index in range(1, 6)
        ]
        schema["allOf"] = [
            {
                "if": {"properties": {"profile": {"const": "chemistry"}}, "required": ["profile"]},
                "then": {
                    "properties": {"chemistrySubdomain": {"minLength": 1, "type": "string"}},
                    "required": ["chemistrySubdomain"],
                },
            },
            {
                "if": {"properties": {"profile": {"const": "general_science"}}, "required": ["profile"]},
                "then": {
                    "properties": {
                        "chemistry": {"type": "null"},
                        "chemistrySubdomain": {"type": "null"},
                    }
                },
            },
        ]
        return schema


class FeedbackSource(ResearchContractModel):
    type: Literal["csv", "multimodal", "lab_record", "manual_observation", "external_api"]
    reference: NonEmptyString
    description: str | None = None


class ResultFeedback(ResearchContractModel):
    source: FeedbackSource
    observations: list[NonEmptyString] = Field(min_length=1)
    data_quality: Literal["low", "medium", "high", "unknown"]
    verdict: HypothesisVerdict
    reason: NonEmptyString
    affected_paths: list[JsonPointer] = Field(min_length=1)


class RoundChange(ResearchContractModel):
    evidence_ref: NonEmptyString
    path: JsonPointer
    before: Any
    after: Any
    reason: NonEmptyString

    @field_validator("before", "after")
    @classmethod
    def require_json_value(cls, value: Any) -> Any:
        try:
            json.dumps(value, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("Round changes must contain JSON-serializable values.") from exc
        return value


class RoundChangeSet(ResearchContractModel):
    from_round: int = Field(ge=1)
    to_round: int = Field(ge=2)
    changes: list[RoundChange] = Field(min_length=1)

    @model_validator(mode="after")
    def require_forward_round(self) -> RoundChangeSet:
        if self.to_round <= self.from_round:
            raise ValueError("toRound must be greater than fromRound.")
        return self


__all__ = [
    "CandidateHypothesis",
    "ChemistryDetails",
    "EvidenceClaim",
    "HypothesisVerdict",
    "NullHypothesis",
    "ResearchBrief",
    "ResearchOutput",
    "ResearchPlan",
    "ResearchProfile",
    "ResultFeedback",
    "RoundChange",
    "RoundChangeSet",
    "Science125CrossDomainTag",
    "Science125Domain",
    "Science125DomainCheck",
    "Science125Extension",
    "Science125Method",
    "Science125MethodProfile",
]
