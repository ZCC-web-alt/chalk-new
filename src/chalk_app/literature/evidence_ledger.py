"""
Evidence ledger and reference gate for competition reports.

This module builds a lightweight claim-to-reference ledger after the final
hypothesis JSON is generated. It is intentionally deterministic: weakly related
open-search references are filtered before they reach the final References list.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Tuple


CLAIM_TEXT_FIELDS = [
    "problem_statement",
    "rationale",
    "technical_details",
    "paper_abstract",
    "methods",
    "expected_results",
]

CLAIM_NESTED_FIELDS = [
    ("datasets", "source"),
    ("datasets", "target"),
    ("experiments", "design"),
    ("results", "derivation"),
    ("results", "feasibility_conclusion"),
]


TERM_ALIASES = {
    "orr": ["orr", "oxygen reduction", "氧还原"],
    "oer": ["oer", "oxygen evolution", "析氧"],
    "her": ["her", "hydrogen evolution", "析氢"],
    "co2rr": ["co2rr", "co2 reduction", "二氧化碳还原"],
    "sac": ["single atom", "single-atom", "sac", "单原子"],
    "mn-n4": ["mn-n4", "mn n4", "mnn4", "mn–n4", "锰", "mn"],
    "fe-n4": ["fe-n4", "fe n4", "fen4", "fe–n4", "铁", "fe"],
    "co-n4": ["co-n4", "co n4", "con4", "co–n4", "钴", "co"],
    "m-n-c": ["m-n-c", "mnc", "m-n4", "n4-c", "m–n–c", "金属氮碳"],
    "h2o2": ["h2o2", "hydrogen peroxide", "过氧化氢"],
    "dft": ["dft", "density functional", "first principles", "第一性原理", "泛函"],
    "vasp": ["vasp"],
    "neb": ["neb", "ci-neb", "nudged elastic band"],
    "pdos": ["pdos", "projected density"],
    "cohp": ["cohp", "hamilton population"],
    "xas": ["xas", "xanes", "exafs", "吸收谱"],
    "xps": ["xps"],
    "rrde": ["rrde", "rotating ring", "旋转环盘"],
    "spin": ["spin", "自旋", "low-spin", "high-spin"],
    "eg": ["eg", "e_g", "eg轨道"],
    "o-o": ["o-o", "o–o", "oxygen-oxygen", "氧氧", "o2"],
    "adsorption": ["adsorption", "binding", "吸附"],
    "overpotential": ["overpotential", "过电位"],
    "faradaic": ["faradaic", "法拉第"],
    "electrocatalysis": ["electrocatal", "电催化"],
    "photocatalysis": ["photocatal", "光催化"],
    "battery": ["battery", "电池"],
}

GENERIC_TOKENS = {
    "study", "studies", "research", "method", "methods", "analysis",
    "effect", "effects", "new", "novel", "based", "using", "via",
    "approach", "material", "materials", "catalyst", "catalysts",
    "reaction", "reactions", "performance", "property", "properties",
    "model", "models", "simulation", "simulations", "result", "results",
}

METHOD_TERMS = {
    "dft", "vasp", "neb", "pdos", "cohp", "xas", "xps", "rrde",
    "machine", "learning", "modeling", "simulation", "simulations",
}

REACTION_TERMS = {"orr", "oer", "her", "co2rr"}


@dataclass
class EvidenceClaim:
    claim_id: str
    field: str
    text: str
    terms: List[str] = field(default_factory=list)
    supporting_refs: List[int] = field(default_factory=list)
    status: str = "unsupported"

    def to_dict(self) -> dict:
        return {
            "claim_id": self.claim_id,
            "field": self.field,
            "text": self.text,
            "terms": self.terms,
            "supporting_refs": self.supporting_refs,
            "status": self.status,
        }


def gate_references(
    hypothesis_data: dict,
    references: List[dict],
    *,
    max_total: int = 15,
    min_relevance: float = 0.18,
) -> Tuple[List[dict], dict]:
    """
    Filter references and build a claim-to-reference ledger.

    User-imported documents are retained as source documents. Other references
    must support at least one extracted claim with sufficient lexical/semantic
    overlap over domain terms.
    """
    claims = extract_claims(hypothesis_data)
    accepted: List[dict] = []
    rejected: List[dict] = []

    for ref in references or []:
        if not isinstance(ref, dict):
            rejected.append({
                "title": str(ref)[:180],
                "reason": "非标准引用格式",
                "best_score": 0.0,
            })
            continue

        ref_copy = dict(ref)
        is_user_doc = bool(ref_copy.get("is_user_document") or ref_copy.get("source_platform") == "user_imported")
        best_score, support = _score_reference_against_claims(ref_copy, claims)

        if is_user_doc:
            status = "accepted_source_document"
            reason = "用户导入文档作为源文献保留"
            should_accept = True
        else:
            should_accept = bool(support) and best_score >= min_relevance
            status = "accepted" if should_accept else "rejected"
            reason = "通过 claim 相关性准入" if should_accept else "未能支撑任何关键陈述或相关性不足"

        ref_copy["evidence_gate"] = {
            "status": status,
            "reason": reason,
            "relevance_score": round(best_score, 3),
            "supporting_claim_ids": [c.claim_id for c in support[:5]],
        }
        ref_copy["evidence_support"] = [c.claim_id for c in support[:5]]
        ref_copy["fidelity"] = _fidelity_label(best_score, is_user_doc)

        if should_accept:
            accepted.append(ref_copy)
        else:
            rejected.append({
                "title": ref_copy.get("title", ""),
                "doi": ref_copy.get("doi", ""),
                "source_platform": ref_copy.get("source_platform", ""),
                "reason": reason,
                "best_score": round(best_score, 3),
            })

    accepted = accepted[:max_total]
    for i, ref in enumerate(accepted, 1):
        ref["ref_number"] = i

    ref_number_by_support = {
        cid: ref["ref_number"]
        for ref in accepted
        for cid in ref.get("evidence_support", [])
    }
    for claim in claims:
        claim.supporting_refs = [
            ref_number_by_support[cid]
            for cid in [claim.claim_id]
            if cid in ref_number_by_support
        ]
        claim.status = "supported" if claim.supporting_refs else "unsupported"

    ledger_refs = []
    for ref in accepted:
        gate = ref.get("evidence_gate", {})
        ledger_refs.append({
            "ref_number": ref.get("ref_number"),
            "title": ref.get("title", ""),
            "doi": ref.get("doi", ""),
            "source_platform": ref.get("source_platform", ""),
            "relevance_score": gate.get("relevance_score", 0),
            "supporting_claim_ids": gate.get("supporting_claim_ids", []),
            "status": gate.get("status", "accepted"),
        })

    supported_claims = [c for c in claims if c.status == "supported"]
    ledger = {
        "summary": {
            "initial_reference_count": len(references or []),
            "accepted_reference_count": len(accepted),
            "rejected_reference_count": len(rejected),
            "claim_count": len(claims),
            "supported_claim_count": len(supported_claims),
            "unsupported_claim_count": len(claims) - len(supported_claims),
        },
        "claims": [c.to_dict() for c in claims],
        "references": ledger_refs,
        "rejected_references": rejected,
    }
    return accepted, ledger


def extract_claims(hypothesis_data: dict) -> List[EvidenceClaim]:
    claims: List[EvidenceClaim] = []

    for field_name in CLAIM_TEXT_FIELDS:
        text = hypothesis_data.get(field_name, "")
        claims.extend(_claims_from_value(field_name, text))

    for parent, child in CLAIM_NESTED_FIELDS:
        obj = hypothesis_data.get(parent, {})
        if isinstance(obj, dict):
            claims.extend(_claims_from_value(f"{parent}.{child}", obj.get(child, "")))

    filtered: List[EvidenceClaim] = []
    for raw in claims:
        text = _clean_text(raw.text)
        if not _is_claim_like(text):
            continue
        terms = sorted(_canonical_terms(text))
        if not terms:
            continue
        raw.claim_id = f"C{len(filtered) + 1:03d}"
        raw.text = text
        raw.terms = terms
        filtered.append(raw)
    return filtered[:80]


def _claims_from_value(field_name: str, value: Any) -> List[EvidenceClaim]:
    if value is None:
        return []
    if isinstance(value, dict):
        text = " ".join(str(v) for v in value.values())
    elif isinstance(value, list):
        text = "\n".join(str(v) for v in value)
    else:
        text = str(value)

    parts = re.split(r"(?<=[。！？；;])|\n+|(?<=\.)\s+(?=[A-Z])", text)
    claims = []
    for part in parts:
        part = _clean_text(part)
        if part:
            claims.append(EvidenceClaim(claim_id="", field=field_name, text=part))
    return claims


def _score_reference_against_claims(ref: dict, claims: List[EvidenceClaim]) -> Tuple[float, List[EvidenceClaim]]:
    ref_text = " ".join(str(ref.get(k, "")) for k in [
        "title", "abstract", "authors", "journal", "doi",
    ])
    ref_terms = _canonical_terms(ref_text)
    if not ref_terms:
        return 0.0, []

    title_terms = _canonical_terms(str(ref.get("title", "")))
    support: List[Tuple[float, EvidenceClaim]] = []
    for claim in claims:
        claim_terms = set(claim.terms)
        if not claim_terms:
            continue
        overlap = claim_terms & ref_terms
        if not overlap:
            continue
        anchor_overlap = overlap - METHOD_TERMS
        coverage = len(overlap) / max(len(claim_terms), 1)
        precision = len(overlap) / max(len(ref_terms), 1)
        title_bonus = 0.12 if overlap & title_terms else 0.0
        doi_bonus = 0.04 if ref.get("doi") else 0.0
        platform_bonus = 0.03 if ref.get("source_platform") in {"semantic_scholar", "pmc", "arxiv"} else 0.0
        score = (0.65 * coverage) + (0.20 * precision) + title_bonus + doi_bonus + platform_bonus
        if not anchor_overlap:
            score *= 0.35
        if (claim_terms & REACTION_TERMS) and not (ref_terms & claim_terms & REACTION_TERMS):
            score *= 0.60
        if _has_conflicting_reaction_focus(claim_terms, ref_terms):
            score *= 0.45
        support.append((min(score, 1.0), claim))

    support.sort(key=lambda item: item[0], reverse=True)
    best = support[0][0] if support else 0.0
    return best, [claim for score, claim in support if score >= max(0.14, best * 0.75)]


def _canonical_terms(text: str) -> set:
    text_l = _normalize_text(text)
    terms = set()

    for canonical, aliases in TERM_ALIASES.items():
        if any(_alias_in_text(alias, text_l) for alias in aliases):
            terms.add(canonical)

    for token in re.findall(r"[a-z][a-z0-9+\-*/]{2,}", text_l):
        token = token.strip("-")
        if token and token not in GENERIC_TOKENS:
            terms.add(token)

    for formula in re.findall(r"\b(?:[A-Z][a-z]?\d*){2,}\b", text):
        terms.add(formula.lower())

    return terms


def _alias_in_text(alias: str, normalized_text: str) -> bool:
    alias_l = _normalize_text(alias)
    if not alias_l:
        return False
    if re.fullmatch(r"[a-z0-9]{1,3}", alias_l):
        return re.search(rf"\b{re.escape(alias_l)}\b", normalized_text) is not None
    if re.fullmatch(r"[a-z0-9][a-z0-9+\-]*", alias_l) and len(alias_l) <= 5:
        return re.search(rf"(?<![a-z0-9]){re.escape(alias_l)}(?![a-z0-9])", normalized_text) is not None
    return alias_l in normalized_text


def _has_conflicting_reaction_focus(claim_terms: set, ref_terms: set) -> bool:
    reactions = {"orr", "oer", "her", "co2rr"}
    claim_reactions = claim_terms & reactions
    ref_reactions = ref_terms & reactions
    if not claim_reactions or not ref_reactions:
        return False
    return claim_reactions.isdisjoint(ref_reactions)


def _fidelity_label(score: float, is_user_doc: bool) -> str:
    if is_user_doc:
        return "高"
    if score >= 0.42:
        return "高"
    if score >= 0.24:
        return "中"
    return "低"


def _is_claim_like(text: str) -> bool:
    if len(text) < 18:
        return False
    if len(text) > 500:
        return False
    if text.startswith(("请", "本报告", "以下", "注意")):
        return False
    return True


def _normalize_text(text: str) -> str:
    text = str(text).lower()
    return (
        text.replace("–", "-")
        .replace("—", "-")
        .replace("−", "-")
        .replace("₂", "2")
        .replace("₂", "2")
        .replace("₄", "4")
        .replace("⁻", "-")
    )


def _clean_text(text: str) -> str:
    text = re.sub(r"\s+", " ", str(text)).strip()
    text = re.sub(r"\[[0-9]{1,3}\]", "", text).strip()
    return text
