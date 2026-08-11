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

_SUBDOMAIN_REFINEMENT_TERMS: dict[str, tuple[str, ...]] = {
    "math.number_theory": (
        "prime distribution analytic number theory zeta functions sieve methods",
        "prime gaps arithmetic patterns modular forms computational verification",
    ),
    "chem.colorant_materials": (
        "novel inorganic organic pigments colorant crystal structure chromophore optical absorption stability toxicity",
        "solid-state synthesis pigment color gamut durability weathering structural characterization",
    ),
    "med.pandemic_forecasting": (
        "epidemic forecasting outbreak prediction surveillance calibration uncertainty external validation",
        "mechanistic epidemic models ensemble forecasts prospective evaluation public health decisions",
    ),
    "bio.marine_conservation": (
        "marine biodiversity conservation protected areas population recovery ecosystem monitoring",
        "fisheries climate stressors before after control impact marine conservation effectiveness",
    ),
    "phys.imaging_resolution": (
        "imaging resolution diffraction limit super-resolution microscopy calibration point spread function",
        "spatial resolution sensitivity noise localization uncertainty standardized imaging benchmark",
    ),
    "eco.climate_mitigation": (
        "ecosystem climate mitigation carbon sequestration additionality permanence leakage monitoring",
        "nature based solutions lifecycle greenhouse gas counterfactual ecosystem restoration",
    ),
    "ai.medical_nanorobotics": (
        "medical nanorobotics targeted delivery sensing actuation biocompatibility safety",
        "micro nanorobot in vivo navigation control clearance therapeutic efficacy",
    ),
}


def _normalize_query(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", str(value or "")).split())[:500]


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


__all__ = ["build_science125_refinement_queries"]
