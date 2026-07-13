from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from chalk_app.core.db import Base, User
from chalk_app.literature.evidence_database import (
    evidence_search_bundle_to_dict,
    extract_query_facets,
    query_evidence,
    upsert_computational_catalysis_evidence,
)


def _session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = session_factory()
    user = User(username="nrr_evidence_user", password_hash="hash")
    session.add(user)
    session.commit()
    session.refresh(user)
    return session, user.id


def test_nrr_facets_include_the_complete_standard_intermediate_set():
    facets = extract_query_facets(
        "NRR Fe-N-C catalyst isotope protocol with *N2 and *NNH intermediates",
        "electrocatalysis",
    )

    assert facets["reaction_type"] == "NRR"
    assert set(facets["adsorbates"]) == {"*N2", "*NNH", "*N", "*NH", "*NH2", "*NH3"}


def test_nrr_same_site_energy_set_normalizes_tail_star_aliases():
    session, user_id = _session()
    try:
        for adsorbate, energy in [("N2*", "0.12 eV"), ("NNH*", "0.88 eV"), ("NH3*", "-0.34 eV")]:
            upsert_computational_catalysis_evidence(
                session,
                user_id=user_id,
                domain="electrocatalysis",
                dataset_name="OC22",
                task_type="ocp_computational_index",
                reaction_context="NRR adsorption energetics",
                material_system="Fe-N-C",
                surface_facet="basal",
                adsorbate=adsorbate,
                adsorption_energy=energy,
                source_url=f"ocp://nrr/fenc/{adsorbate}",
                reliability_level="computational_index",
                extra_json={"active_site": "Fe-N4"},
            )

        result = query_evidence(
            session,
            user_id=user_id,
            domain="electrocatalysis",
            query_text="Fe-N-C NRR ammonia isotope control",
            reaction_type="NRR",
            material="Fe-N-C",
            max_each=10,
        )
        data = evidence_search_bundle_to_dict(result)

        assert data["adsorbate_energy_sets"]
        energy_set = data["adsorbate_energy_sets"][0]
        assert set(energy_set["energies"]) == {"*N2", "*NNH", "*NH3"}
        coverage = energy_set["coverage"]
        assert set(coverage["present_adsorbates"]) == {"*N2", "*NNH", "*NH3"}
        assert set(coverage["missing_adsorbates"]) == {"*N", "*NH", "*NH2"}
        assert coverage["complete"] is False
        assert coverage["coverage_count"] == 3
        assert coverage["requested_count"] == 6
    finally:
        session.close()
