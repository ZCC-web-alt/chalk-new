from __future__ import annotations

import unicodedata


_REFINEMENT_TERMS: dict[str, tuple[str, ...]] = {
    "chem_interface_v1": (
        "operando interfacial spectroscopy nanoscale interface measurement calibrated microscopy",
        "in situ microscopy molecular dynamics validation interfacial transport kinetics",
    ),
    "bio_genome_editing_v1": (
        "CRISPR therapeutic delivery off-target safety efficacy clinical trial",
        "genome editing disease treatment longitudinal cohort functional validation",
    ),
    "astro_high_energy_v1": (
        "cosmic ray sources composition anisotropy gamma ray neutrino observations",
        "cosmic ray acceleration propagation multi-messenger calibrated observatory",
    ),
}


def _normalize_query(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", str(value or "")).split())[:500]


def build_science125_refinement_queries(query_adapter: str, original_query: str) -> tuple[str, ...]:
    original = _normalize_query(original_query)
    additions = _REFINEMENT_TERMS.get(str(query_adapter).strip(), ())
    queries: list[str] = []
    for addition in additions:
        refined = _normalize_query(f"{original} {addition}")
        if refined and refined.casefold() != original.casefold() and refined not in queries:
            queries.append(refined)
    return tuple(queries[:2])


__all__ = ["build_science125_refinement_queries"]
