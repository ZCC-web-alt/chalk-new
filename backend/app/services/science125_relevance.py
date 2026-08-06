from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Mapping

from app.services.science125_localization import get_science125_localization
from app.services.science125_retrieval import EvidenceRecord


SCORING_VERSION = "science125-relevance-v1"
ELIGIBILITY_VERSION = "science125-evidence-eligibility-v1"
MIN_GENERATION_RELEVANCE_SCORE = 0.50
_NON_FULL_TEXT_STATUSES = {"", "metadata", "metadata_only", "needs_verification"}
_STOP_WORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "can", "for", "from", "how",
    "in", "is", "it", "of", "on", "or", "the", "to", "used", "using", "we", "what",
    "with", "might", "level", "phenomena",
}
_TOKEN_ALIASES = {
    "interfaces": "interface",
    "interfacial": "interface",
    "measure": "measurement",
    "measured": "measurement",
    "measuring": "measurement",
    "measurements": "measurement",
    "phenomenon": "phenomena",
    "rays": "ray",
    "sources": "source",
}


@dataclass(frozen=True, slots=True)
class RelevanceAssessment:
    score: float
    label: str
    components: Mapping[str, float]
    matched_concepts: tuple[str, ...]
    scoring_version: str = SCORING_VERSION

    def to_dict(self) -> dict[str, object]:
        return {
            "scoringVersion": self.scoring_version,
            **self.components,
            "matchedConcepts": list(self.matched_concepts),
        }


@dataclass(frozen=True, slots=True)
class EvidenceQualification:
    relevance: RelevanceAssessment
    eligible_for_generation: bool
    reasons: tuple[str, ...]
    eligibility_version: str = ELIGIBILITY_VERSION

    def to_dict(self) -> dict[str, object]:
        return {
            "eligibleForGeneration": self.eligible_for_generation,
            "reasons": list(self.reasons),
            "minimumRelevanceScore": MIN_GENERATION_RELEVANCE_SCORE,
            "minimumRelevanceLabel": "medium",
            "eligibilityVersion": self.eligibility_version,
        }


def _normalized_text(value: str) -> str:
    return unicodedata.normalize("NFKC", str(value or "")).casefold()


def _tokens(value: str) -> tuple[str, ...]:
    raw = re.findall(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*", _normalized_text(value))
    return tuple(_TOKEN_ALIASES.get(token, token) for token in raw)


def _query_terms(query: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(token for token in _tokens(query) if len(token) >= 3 and token not in _STOP_WORDS))


def _coverage(query_terms: tuple[str, ...], text_tokens: set[str]) -> float:
    if not query_terms:
        return 0.0
    return sum(term in text_tokens for term in query_terms) / len(query_terms)


def _phrase_match(query_terms: tuple[str, ...], text: str) -> float:
    normalized = " ".join(_tokens(text))
    if not normalized or len(query_terms) < 2:
        return 0.0
    return 1.0 if any(
        f"{left} {right}" in normalized
        for left, right in zip(query_terms, query_terms[1:])
    ) else 0.0


def _concept_matches(question_id: str, text: str) -> tuple[float, tuple[str, ...]]:
    localization = get_science125_localization(question_id)
    if localization is None or not localization.relevance_concepts:
        return 0.0, ()
    normalized = _normalized_text(text)
    token_set = set(_tokens(text))
    matched: list[str] = []
    for concept in localization.relevance_concepts:
        if any(
            (_TOKEN_ALIASES.get(term, term) in token_set if " " not in term else term in normalized)
            for term in concept.terms
        ):
            matched.append(concept.label_zh)
    return len(matched) / len(localization.relevance_concepts), tuple(matched)


def _evidence_completeness(record: EvidenceRecord) -> float:
    score = 0.0
    if record.abstract.strip():
        score += 0.4
    if record.doi or record.pmid or record.arxiv_id or record.ads_id:
        score += 0.2
    if record.access_status.strip().casefold() not in {"", "metadata", "metadata_only", "needs_verification"}:
        score += 0.4
    return min(1.0, score)


def _label(score: float) -> str:
    if score >= 0.75:
        return "high"
    if score >= 0.50:
        return "medium"
    if score >= 0.30:
        return "low"
    return "very_low"


def assess_science125_relevance(
    question_id: str,
    query: str,
    record: EvidenceRecord,
) -> RelevanceAssessment:
    query_terms = _query_terms(query)
    title_tokens = set(_tokens(record.title))
    abstract_tokens = set(_tokens(record.abstract))
    combined_text = f"{record.title}\n{record.abstract}"
    title_coverage = _coverage(query_terms, title_tokens)
    abstract_coverage = _coverage(query_terms, abstract_tokens)
    concept_coverage, matched_concepts = _concept_matches(question_id, combined_text)
    phrase_match = _phrase_match(query_terms, combined_text)
    evidence_completeness = _evidence_completeness(record)
    components = {
        "titleCoverage": round(title_coverage, 4),
        "abstractCoverage": round(abstract_coverage, 4),
        "conceptCoverage": round(concept_coverage, 4),
        "phraseMatch": round(phrase_match, 4),
        "evidenceCompleteness": round(evidence_completeness, 4),
    }
    score = round(
        0.30 * title_coverage
        + 0.20 * abstract_coverage
        + 0.35 * concept_coverage
        + 0.10 * phrase_match
        + 0.05 * evidence_completeness,
        4,
    )
    return RelevanceAssessment(
        score=score,
        label=_label(score),
        components=components,
        matched_concepts=matched_concepts,
    )


def qualify_science125_evidence(
    question_id: str,
    query: str,
    record: EvidenceRecord,
) -> EvidenceQualification:
    relevance = assess_science125_relevance(question_id, query, record)
    reasons: list[str] = []
    if record.access_status.strip().casefold() in _NON_FULL_TEXT_STATUSES:
        reasons.append("ACCESS_NOT_FULL_TEXT")
    if relevance.score < MIN_GENERATION_RELEVANCE_SCORE:
        reasons.append("RELEVANCE_BELOW_MEDIUM")
    return EvidenceQualification(
        relevance=relevance,
        eligible_for_generation=not reasons,
        reasons=tuple(reasons),
    )


__all__ = [
    "ELIGIBILITY_VERSION",
    "EvidenceQualification",
    "MIN_GENERATION_RELEVANCE_SCORE",
    "RelevanceAssessment",
    "SCORING_VERSION",
    "assess_science125_relevance",
    "qualify_science125_evidence",
]
