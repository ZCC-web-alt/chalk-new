from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


MAX_QUERY_LENGTH = 180
MAX_QUERY_COUNT = 4
MIN_KEYWORD_COUNT = 6
MAX_KEYWORD_COUNT = 12

_STOP_WORDS = frozenset({
    "a", "about", "all", "an", "and", "are", "as", "at", "be", "been", "being",
    "by", "can", "could", "do", "does", "for", "from", "how", "if", "in", "into",
    "is", "it", "its", "may", "might", "of", "on", "or", "our", "several", "should",
    "that", "the", "their", "these", "this", "those", "through", "to", "under", "used",
    "using", "we", "what", "when", "where", "which", "while", "why", "with", "would",
})

_GENERIC_EVIDENCE_TERMS = (
    "mechanism",
    "measurement",
    "validation",
    "evidence",
    "uncertainty",
    "review",
)

_QUERY_PROFILE_TERMS: dict[str, dict[str, tuple[str, ...]]] = {
    "chem_interface_v1": {
        "core": ("interface", "interfacial phenomena", "microscopic", "nanoscale"),
        "methods": ("operando spectroscopy", "in situ microscopy", "structural characterization"),
        "constraints": ("transport kinetics", "molecular dynamics", "calibration"),
    },
    "bio_genome_editing_v1": {
        "core": ("genome editing", "CRISPR", "disease therapy"),
        "methods": ("in vivo delivery", "ex vivo delivery", "clinical trial"),
        "constraints": ("off-target", "safety", "efficacy", "long-term follow-up"),
    },
    "astro_high_energy_v1": {
        "core": ("cosmic ray", "source", "particle acceleration"),
        "methods": ("anisotropy", "gamma ray", "neutrino"),
        "constraints": ("composition", "propagation", "multi-messenger", "sensitivity limit"),
    },
}

_SUBDOMAIN_QUERY_TERMS: dict[str, dict[str, tuple[str, ...]]] = {
    "math.number_theory": {
        "core": ("prime distribution", "analytic number theory", "zeta functions"),
        "methods": ("sieve methods", "modular forms", "formal proof"),
        "constraints": ("counterexample", "boundary cases", "computational verification"),
    },
    "chem.colorant_materials": {
        "core": ("pigment", "colorant", "chromophore", "crystal structure"),
        "methods": ("solid-state synthesis", "optical absorption", "structural characterization"),
        "constraints": ("stability", "toxicity", "weathering", "color gamut"),
    },
    "med.pandemic_forecasting": {
        "core": ("epidemic forecasting", "outbreak prediction", "surveillance"),
        "methods": ("mechanistic model", "ensemble forecast", "prospective evaluation"),
        "constraints": ("calibration", "uncertainty", "external validation"),
    },
    "bio.marine_conservation": {
        "core": ("marine biodiversity", "conservation", "population recovery"),
        "methods": ("protected areas", "ecosystem monitoring", "before after control impact"),
        "constraints": ("fisheries", "climate stressors", "detectability"),
    },
    "phys.imaging_resolution": {
        "core": ("imaging resolution", "diffraction limit", "spatial resolution"),
        "methods": ("super-resolution microscopy", "point spread function", "calibration"),
        "constraints": ("sensitivity", "noise", "localization uncertainty"),
    },
    "eco.climate_mitigation": {
        "core": ("ecosystem climate mitigation", "carbon sequestration", "nature based solutions"),
        "methods": ("counterfactual", "ecosystem monitoring", "lifecycle assessment"),
        "constraints": ("additionality", "permanence", "leakage"),
    },
    "ai.medical_nanorobotics": {
        "core": ("medical nanorobotics", "targeted delivery", "micro nanorobot"),
        "methods": ("in vivo navigation", "sensing actuation", "therapeutic efficacy"),
        "constraints": ("biocompatibility", "clearance", "safety"),
    },
}

# Kept as a compatibility surface for existing callers and stored diagnostics.
_REFINEMENT_TERMS: dict[str, tuple[str, ...]] = {
    profile: (" ".join(groups["methods"]), " ".join(groups["constraints"]))
    for profile, groups in _QUERY_PROFILE_TERMS.items()
}
_SUBDOMAIN_REFINEMENT_TERMS: dict[str, tuple[str, ...]] = {
    subdomain: (
        " ".join((*groups["core"], *groups["methods"])),
        " ".join((*groups["methods"], *groups["constraints"])),
    )
    for subdomain, groups in _SUBDOMAIN_QUERY_TERMS.items()
}


@dataclass(frozen=True, slots=True)
class Science125QueryPlan:
    topic_summary: str
    keywords: tuple[str, ...]
    queries: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "topicSummary": self.topic_summary,
            "keywords": list(self.keywords),
            "queries": list(self.queries),
        }


def _normalize_query(value: str, *, limit: int = 500) -> str:
    return " ".join(unicodedata.normalize("NFKC", str(value or "")).split())[:limit]


def _english_terms(value: str) -> tuple[str, ...]:
    normalized = unicodedata.normalize("NFKC", str(value or "")).casefold()
    raw = re.findall(r"[a-z][a-z0-9]*(?:[-'][a-z0-9]+)*", normalized)
    return tuple(dict.fromkeys(
        token for token in raw
        if len(token) >= 3 and token not in _STOP_WORDS
    ))


def _profile_groups(
    query_adapter: str,
    primary_subdomain: str | None,
) -> dict[str, tuple[str, ...]]:
    profile = _QUERY_PROFILE_TERMS.get(str(query_adapter).strip())
    if profile is not None:
        return profile
    if primary_subdomain:
        subdomain_profile = _SUBDOMAIN_QUERY_TERMS.get(str(primary_subdomain).strip())
        if subdomain_profile is not None:
            return subdomain_profile
        subdomain_terms = tuple(
            term for term in re.split(r"[._-]+", str(primary_subdomain).casefold())
            if len(term) >= 3 and term not in _STOP_WORDS
        )
        if subdomain_terms:
            return {
                "core": subdomain_terms,
                "methods": ("measurement", "validation"),
                "constraints": ("uncertainty", "evidence"),
            }
    return {"core": (), "methods": (), "constraints": ()}


def _unique_terms(*groups: tuple[str, ...], limit: int) -> tuple[str, ...]:
    selected: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for raw_term in group:
            term = _normalize_query(raw_term, limit=MAX_QUERY_LENGTH).strip(" ,.;:?")
            key = term.casefold()
            if not term or key in seen:
                continue
            seen.add(key)
            selected.append(term)
            if len(selected) >= limit:
                return tuple(selected)
    return tuple(selected)


def _bounded_query(terms: tuple[str, ...]) -> str:
    selected: list[str] = []
    for term in terms:
        candidate = " ".join((*selected, term))
        if len(candidate) > MAX_QUERY_LENGTH:
            break
        selected.append(term)
    return " ".join(selected)


def build_science125_query_plan(
    query_adapter: str,
    original_query: str,
    *,
    primary_subdomain: str | None = None,
    question_id: str | None = None,
) -> Science125QueryPlan:
    """Build deterministic short searches without sending a long question to providers."""

    del question_id  # Reserved for versioned per-question query policies.
    original_terms = _english_terms(original_query)
    groups = _profile_groups(query_adapter, primary_subdomain)
    fallback_terms = tuple(_GENERIC_EVIDENCE_TERMS)
    core = _unique_terms(groups["core"], original_terms, fallback_terms, limit=5)
    methods = _unique_terms(groups["methods"], original_terms[3:], fallback_terms, limit=4)
    constraints = _unique_terms(groups["constraints"], original_terms[6:], fallback_terms, limit=4)
    keywords = _unique_terms(core, methods, constraints, fallback_terms, limit=MAX_KEYWORD_COUNT)
    if len(keywords) < MIN_KEYWORD_COUNT:
        keywords = _unique_terms(keywords, fallback_terms, limit=MIN_KEYWORD_COUNT)

    candidates = (
        _bounded_query(_unique_terms(core, methods[:2], limit=7)),
        _bounded_query(_unique_terms(core[:3], methods, limit=7)),
        _bounded_query(_unique_terms(core[:3], constraints, limit=7)),
        _bounded_query(_unique_terms(core[:2], methods[1:3], constraints[:2], limit=7)),
    )
    queries = _unique_terms(tuple(query for query in candidates if query), limit=MAX_QUERY_COUNT)
    if not queries:
        queries = (_bounded_query(keywords[:6]),)

    topic_terms = _unique_terms(core[:4], constraints[:2], limit=6)
    topic_summary = _normalize_query("; ".join(topic_terms), limit=200)
    return Science125QueryPlan(
        topic_summary=topic_summary,
        keywords=keywords,
        queries=queries,
    )


def build_science125_refinement_queries(
    query_adapter: str,
    original_query: str,
    *,
    primary_subdomain: str | None = None,
) -> tuple[str, ...]:
    original = _normalize_query(original_query)
    additions = _REFINEMENT_TERMS.get(str(query_adapter).strip(), ())
    if not additions and primary_subdomain:
        additions = _SUBDOMAIN_REFINEMENT_TERMS.get(str(primary_subdomain).strip(), ())
    queries: list[str] = []
    for addition in additions:
        refined = _normalize_query(f"{original} {addition}")
        if refined and refined.casefold() != original.casefold() and refined not in queries:
            queries.append(refined)
    return tuple(queries[:2])


__all__ = [
    "Science125QueryPlan",
    "build_science125_query_plan",
    "build_science125_refinement_queries",
]
