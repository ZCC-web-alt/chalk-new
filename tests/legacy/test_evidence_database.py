import sys
import json
import tempfile
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import evidence_database as evidence_database_module  # noqa: E402

from db import (  # noqa: E402
    Base,
    ComputationalCatalysisEvidence,
    DomainEvidence,
    LiteratureEvidence,
    User,
    add_document_chunks,
    create_document,
    create_lab_record,
)
from evidence_database import (  # noqa: E402
    CURATED_ENERGY_EVIDENCE_PATH,
    OCP_DATASET_CATALOG,
    build_database_evidence_context,
    evidence_search_bundle_to_dict,
    extract_query_facets,
    index_ocp_oc20dense_targets,
    index_ocp_computational_evidence,
    index_document_chunks_as_evidence,
    index_hypothesis_json_as_evidence,
    index_lab_record_as_evidence,
    load_curated_energy_evidence,
    query_evidence,
    seed_computational_source_catalog,
    seed_curated_energy_evidence,
    upsert_computational_catalysis_evidence,
    upsert_domain_evidence,
    upsert_literature_evidence,
)


def _session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = SessionLocal()
    user = User(username="evidence_user", password_hash="hash")
    session.add(user)
    session.commit()
    session.refresh(user)
    return session, user.id


def test_evidence_tables_are_available_on_new_database():
    session, user_id = _session()
    try:
        lit = LiteratureEvidence(
            user_id=user_id,
            domain="battery",
            title="Silicon anode interface stabilization",
            source_text="Si-C anode capacity retention improves after SEI tuning.",
            reliability_level="user_imported",
        )
        domain = DomainEvidence(
            user_id=user_id,
            domain="battery",
            material_system="Si-C anode",
            metric_name="capacity_retention",
            metric_value="82",
            metric_unit="%",
            source_text="Retention after 200 cycles.",
            reliability_level="verified",
        )
        comp = ComputationalCatalysisEvidence(
            user_id=user_id,
            dataset_name="OC22",
            reaction_context="OER",
            material_system="NiFe oxide",
            adsorbate="O*",
            adsorption_energy="-1.2",
            evidence_kind="computational",
            reliability_level="catalog",
        )
        session.add_all([lit, domain, comp])
        session.commit()

        assert session.query(LiteratureEvidence).count() == 1
        assert session.query(DomainEvidence).count() == 1
        assert session.query(ComputationalCatalysisEvidence).count() == 1
    finally:
        session.close()


def test_query_evidence_filters_by_domain_material_and_reaction():
    session, user_id = _session()
    try:
        upsert_literature_evidence(
            session,
            user_id=user_id,
            domain="electrocatalysis",
            title="Fe-N-C ORR selectivity",
            source_text="Fe-N-C catalysts tune OOH* adsorption and H2O2 selectivity.",
            material_system="Fe-N-C",
            reaction_type="ORR",
            key_mechanism="OOH* adsorption",
            reliability_level="verified",
        )
        upsert_domain_evidence(
            session,
            user_id=user_id,
            domain="battery",
            material_system="LiFePO4 cathode",
            metric_name="specific_capacity",
            metric_value="160",
            metric_unit="mAh g^-1",
            source_text="LiFePO4 baseline capacity.",
            reliability_level="verified",
        )
        upsert_computational_catalysis_evidence(
            session,
            user_id=user_id,
            dataset_name="OC22",
            reaction_context="OER",
            material_system="NiFe oxide",
            adsorbate="O*",
            adsorption_energy="-1.2",
            source_url="https://fair-chem.github.io/",
            reliability_level="catalog",
        )

        bundle = query_evidence(
            session,
            user_id=user_id,
            domain="electrocatalysis",
            query_text="Fe-N-C ORR OOH selectivity",
            reaction_type="ORR",
            material="Fe-N-C",
        )

        assert len(bundle.literature_evidence) == 1
        assert bundle.literature_evidence[0]["title"] == "Fe-N-C ORR selectivity"
        assert len(bundle.domain_evidence) == 0
        assert len(bundle.computational_catalysis_evidence) == 0
    finally:
        session.close()


def test_indexers_create_lightweight_evidence_without_mutating_sources():
    session, user_id = _session()
    try:
        doc = create_document(
            session,
            user_id,
            "High-Ni cathode evidence",
            "pdf",
            "authorized.pdf",
            "Imported by user",
        )
        original_doc_title = doc.title
        add_document_chunks(
            session,
            user_id,
            doc.id,
            [
                (
                    0,
                    "DOI 10.1000/test. High-Ni cathode shows 180 mAh g^-1 and "
                    "82% capacity retention after 200 cycles. CEI degradation is the limitation.",
                    b"embedding",
                    "text",
                )
            ],
        )

        doc_counts = index_document_chunks_as_evidence(
            session,
            user_id=user_id,
            document_id=doc.id,
            domain="battery_materials",
        )
        assert doc_counts["literature_evidence"] == 1
        assert doc_counts["domain_evidence"] >= 2
        assert session.get(type(doc), doc.id).title == original_doc_title

        lab = create_lab_record(
            session,
            user_id,
            "OER screening",
            "NiFe oxide OER overpotential 260 mV at 10 mA cm^-2. Tafel 45 mV dec^-1 after 20 h stability.",
        )
        lab_counts = index_lab_record_as_evidence(
            session,
            user_id=user_id,
            lab_record_id=lab.id,
            domain="electrocatalysis",
        )
        assert lab_counts["domain_evidence"] >= 2
        assert session.get(type(lab), lab.id).content.startswith("NiFe oxide OER")

        hypothesis_counts = index_hypothesis_json_as_evidence(
            session,
            user_id=user_id,
            domain="battery_materials",
            hypothesis_data={
                "structured_extraction_table": [
                    {
                        "paper_id": "P1",
                        "title": "LFP baseline",
                        "materials_or_reaction": "LiFePO4 cathode",
                        "key_data": "specific capacity 160 mAh g^-1",
                        "mechanism": "olivine cathode baseline",
                        "evidence_status": "需核验",
                    }
                ],
                "results": {"expected": "capacity retention 90% after 300 cycles"},
            },
        )
        assert hypothesis_counts["literature_evidence"] == 1
        assert hypothesis_counts["domain_evidence"] >= 1

        bundle = query_evidence(
            session,
            user_id=user_id,
            domain="battery_materials",
            query_text="High-Ni cathode capacity retention CEI",
            material="High-Ni",
        )
        assert bundle.literature_evidence
        assert bundle.domain_evidence
        assert bundle.domain_evidence[0]["reliability_level"] in {
            "needs_verification",
            "需核验",
            "lab_record",
        }
    finally:
        session.close()


def test_database_context_marks_ocp_as_computational_evidence():
    session, user_id = _session()
    try:
        seed_computational_source_catalog(session, user_id=user_id)
        assert {"OC20", "OC22", "OC25", "ODAC23"}.issubset(OCP_DATASET_CATALOG)

        context = build_database_evidence_context(
            session,
            user_id=user_id,
            domain="electrocatalysis",
            research_question="OER oxide catalyst adsorbate energetics",
            reaction_type="OER",
        )
        data = evidence_search_bundle_to_dict(
            query_evidence(
                session,
                user_id=user_id,
                domain="electrocatalysis",
                query_text="OER oxide catalyst",
                reaction_type="OER",
            )
        )

        assert "自建数据库证据上下文" in context
        assert "计算证据" in context
        assert "不得直接等同于实验性能" in context
        assert data["summary"]["computational_catalysis_evidence_count"] >= 1
    finally:
        session.close()


def test_query_evidence_uses_reaction_adsorbates_not_only_o_star():
    session, user_id = _session()
    try:
        for adsorbate, energy in [("O*", "-1.0 eV"), ("*OH", "-0.4 eV"), ("OOH*", "0.8 eV")]:
            upsert_computational_catalysis_evidence(
                session,
                user_id=user_id,
                domain="electrocatalysis",
                dataset_name="OC20-Dense",
                task_type="AdsorbML adsorption-energy target",
                reaction_context="OER adsorption energetics",
                material_system="mp-1219797 facet (1,0,2)",
                adsorbate=adsorbate,
                adsorption_energy=energy,
                evidence_kind="computational",
                reliability_level="computational_index",
                source_url=f"ocp://test/{adsorbate}",
            )

        bundle = query_evidence(
            session,
            user_id=user_id,
            domain="electrocatalysis",
            query_text="mp-1219797 OER adsorption descriptor hypothesis",
            reaction_type="OER",
            material="mp-1219797",
            max_each=10,
        )

        adsorbates = {item["adsorbate"] for item in bundle.computational_catalysis_evidence}
        assert {"*O", "*OH", "*OOH"}.issubset(adsorbates)
        assert not any("数据库暂无 *O" in warning for warning in bundle.warnings)
    finally:
        session.close()


def test_query_evidence_groups_adsorbate_energy_set_for_same_site():
    session, user_id = _session()
    try:
        for adsorbate, energy in [("*OOH", "3.21 eV"), ("*O", "1.72 eV"), ("*OH", "0.84 eV")]:
            upsert_computational_catalysis_evidence(
                session,
                user_id=user_id,
                domain="electrocatalysis",
                dataset_name="OC20-Dense",
                task_type="AdsorbML adsorption-energy target",
                reaction_context="ORR adsorption energetics",
                material_system="Co-N4@COF active site A",
                surface_facet="pore-A",
                adsorbate=adsorbate,
                adsorption_energy=energy,
                evidence_kind="computational",
                reliability_level="computational_index",
                source_url=f"ocp://cof/site-a/{adsorbate}",
                extra_json={"active_site": "Co-N4 pore A", "system_id": "cof_site_a"},
            )

        bundle = query_evidence(
            session,
            user_id=user_id,
            domain="electrocatalysis",
            query_text="Co-N4@COF ORR 4e water pathway",
            reaction_type="ORR",
            material="Co-N4@COF",
            adsorbates=["*OOH", "*O", "*OH"],
            max_each=10,
        )
        data = evidence_search_bundle_to_dict(bundle)

        sets = data["adsorbate_energy_sets"]
        assert sets
        complete = [item for item in sets if item["coverage"]["complete"]]
        assert complete
        energy_map = complete[0]["energies"]
        assert energy_map["*OOH"]["adsorption_energy"] == "3.21 eV"
        assert energy_map["*O"]["adsorption_energy"] == "1.72 eV"
        assert energy_map["*OH"]["adsorption_energy"] == "0.84 eV"
        assert complete[0]["active_site"] == "Co-N4 pore A"
    finally:
        session.close()


def test_query_evidence_warns_when_requested_adsorbate_is_missing():
    session, user_id = _session()
    try:
        upsert_computational_catalysis_evidence(
            session,
            user_id=user_id,
            domain="electrocatalysis",
            dataset_name="OC20-Dense",
            task_type="AdsorbML adsorption-energy target",
            reaction_context="OER adsorption energetics",
            material_system="mp-1",
            adsorbate="*O",
            adsorption_energy="-1.0 eV",
            evidence_kind="computational",
            reliability_level="computational_index",
            source_url="ocp://test/o",
        )

        bundle = query_evidence(
            session,
            user_id=user_id,
            domain="electrocatalysis",
            query_text="OER mp-1",
            reaction_type="OER",
            material="mp-1",
            max_each=10,
        )

        assert any("数据库暂无 *OH" in warning for warning in bundle.warnings)
        assert any("数据库暂无 *OOH" in warning for warning in bundle.warnings)
    finally:
        session.close()


def test_reaction_adsorbate_sets_cover_multiple_electrocatalysis_reactions():
    facets = extract_query_facets(
        "CO2RR Cu catalyst needs *COOH and *OCCO selectivity evidence",
        "electrocatalysis",
    )
    assert facets["reaction_type"] == "CO2RR"
    assert {"*CO2", "*COOH", "*CO", "*OCHO", "*OCCO"}.issubset(set(facets["adsorbates"]))

    facets = extract_query_facets(
        "NRR Fe-N-C catalyst isotope protocol with *N2 and *NNH intermediates",
        "electrocatalysis",
    )
    assert facets["reaction_type"] == "NRR"
    assert {"*N2", "*NNH", "*N", "*NH", "*NH2", "*NH3"}.issubset(set(facets["adsorbates"]))

    facets = extract_query_facets("HOR Pt benchmark needs H* adsorption evidence", "electrocatalysis")
    assert facets["reaction_type"] == "HOR"
    assert facets["adsorbates"] == ["*H"]


def test_query_evidence_builds_same_site_sets_for_co2rr_and_nrr():
    session, user_id = _session()
    try:
        for adsorbate, energy in [("*CO2", "-0.10 eV"), ("COOH*", "0.42 eV"), ("CO*", "-0.25 eV"), ("OCHO*", "0.37 eV")]:
            upsert_computational_catalysis_evidence(
                session,
                user_id=user_id,
                domain="electrocatalysis",
                dataset_name="OC22",
                task_type="ocp_computational_index",
                reaction_context="CO2RR adsorption energetics",
                material_system="Cu(111)",
                surface_facet="(111)",
                adsorbate=adsorbate,
                adsorption_energy=energy,
                source_url=f"ocp://co2rr/cu111/{adsorbate}",
                reliability_level="computational_index",
                extra_json={"active_site": "terrace"},
            )
        co2rr = query_evidence(
            session,
            user_id=user_id,
            domain="electrocatalysis",
            query_text="Cu(111) CO2RR formate CO pathway",
            reaction_type="CO2RR",
            material="Cu(111)",
            max_each=10,
        )
        co2rr_data = evidence_search_bundle_to_dict(co2rr)
        assert co2rr_data["adsorbate_energy_sets"]
        assert co2rr_data["adsorbate_energy_sets"][0]["coverage"]["complete"] is True
        assert set(co2rr_data["adsorbate_energy_sets"][0]["energies"]) == {"*CO2", "*COOH", "*CO", "*OCHO"}

        for adsorbate, energy in [("*N2", "0.12 eV"), ("*NNH", "0.88 eV")]:
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
        nrr = query_evidence(
            session,
            user_id=user_id,
            domain="electrocatalysis",
            query_text="Fe-N-C NRR ammonia isotope control",
            reaction_type="NRR",
            material="Fe-N-C",
            max_each=10,
        )
        nrr_data = evidence_search_bundle_to_dict(nrr)
        assert nrr_data["adsorbate_energy_sets"]
        coverage = nrr_data["adsorbate_energy_sets"][0]["coverage"]
        assert coverage["complete"] is False
        assert "*N2" in coverage["present_adsorbates"]
        assert "*NNH" in coverage["present_adsorbates"]
        assert {"*N", "*NH", "*NH2", "*NH3"}.issubset(set(coverage["missing_adsorbates"]))
    finally:
        session.close()


def test_index_ocp_computational_evidence_is_idempotent_for_manifest(tmp_path):
    session, user_id = _session()
    try:
        manifest = tmp_path / "ocp_manifest.jsonl"
        rows = [
            {
                "dataset": "OC22",
                "split": "val",
                "system_id": "sys-1",
                "config_id": "cfg-ooh",
                "material": "Co-N4@COF",
                "facet": "pore-A",
                "active_site": "Co-N4 pore A",
                "adsorbate": "OOH*",
                "adsorption_energy": 3.21,
                "reference_energy": -120.0,
                "source_path": "/data/oc22/sys-1.traj",
            },
            {
                "dataset": "OC22",
                "split": "val",
                "system_id": "sys-1",
                "config_id": "cfg-o",
                "material": "Co-N4@COF",
                "facet": "pore-A",
                "active_site": "Co-N4 pore A",
                "adsorbate": "O*",
                "adsorption_energy": 1.72,
                "reference_energy": -118.0,
                "source_path": "/data/oc22/sys-1.traj",
            },
        ]
        manifest.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")

        first = index_ocp_computational_evidence(
            session,
            user_id=user_id,
            dataset_name="OC22",
            source_path=str(manifest),
            max_rows=100,
        )
        second = index_ocp_computational_evidence(
            session,
            user_id=user_id,
            dataset_name="OC22",
            source_path=str(manifest),
            max_rows=100,
        )

        assert first.imported == 2
        assert second.imported == 0
        assert second.skipped >= 2
        rows_in_db = (
            session.query(ComputationalCatalysisEvidence)
            .filter(ComputationalCatalysisEvidence.dataset_name == "OC22")
            .filter(ComputationalCatalysisEvidence.reliability_level == "computational_index")
            .all()
        )
        assert len(rows_in_db) == 2
        assert {row.adsorbate for row in rows_in_db} == {"*OOH", "*O"}
    finally:
        session.close()


def test_indexed_ocp_manifest_supports_same_site_orr_adsorbate_sets(tmp_path):
    session, user_id = _session()
    try:
        manifest = tmp_path / "ocp_orr_manifest.jsonl"
        rows = []
        for adsorbate, energy in [("OOH*", 3.21), ("O*", 1.72), ("OH*", 0.84)]:
            rows.append(
                {
                    "dataset": "OC22",
                    "split": "val",
                    "system_id": "cof-site-a",
                    "config_id": f"cfg-{adsorbate.strip('*').lower()}",
                    "material": "Co-N4@COF",
                    "facet": "pore-A",
                    "active_site": "Co-N4 pore A",
                    "reaction": "ORR",
                    "adsorbate": adsorbate,
                    "adsorption_energy": energy,
                    "reference_energy": -120.0,
                    "source_path": f"/data/oc22/cof-site-a/{adsorbate}.traj",
                }
            )
        manifest.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")

        result = index_ocp_computational_evidence(
            session,
            user_id=user_id,
            dataset_name="OC22",
            source_path=str(manifest),
            max_rows=100,
        )
        bundle = query_evidence(
            session,
            user_id=user_id,
            domain="electrocatalysis",
            query_text="Co-N4@COF ORR 4e water pathway *OOH *O *OH",
            reaction_type="ORR",
            material="Co-N4@COF",
            adsorbates=["*OOH", "*O", "*OH"],
            max_each=10,
        )
        data = evidence_search_bundle_to_dict(bundle)

        assert result.imported == 3
        assert data["adsorbate_energy_sets"]
        same_site = data["adsorbate_energy_sets"][0]
        assert same_site["coverage"]["complete"] is True
        assert same_site["material_system"] == "Co-N4@COF"
        assert same_site["surface_facet"] == "pore-A"
        assert same_site["active_site"] == "Co-N4 pore A"
        assert set(same_site["energies"]) == {"*OOH", "*O", "*OH"}
    finally:
        session.close()


def test_extract_query_facets_keeps_adsorbates_out_of_material_hint():
    facets = extract_query_facets(
        "CO2RR catalyst with *COOH, CO*, and OCHO* adsorption intermediates",
        "electrocatalysis",
    )

    assert facets["reaction_type"] == "CO2RR"
    assert {"*COOH", "*CO", "*OCHO", "*CO2"}.issubset(set(facets["adsorbates"]))
    assert facets["material"] == ""


def test_curated_energy_seed_json_is_loadable_and_auditable():
    assert CURATED_ENERGY_EVIDENCE_PATH.exists()
    with CURATED_ENERGY_EVIDENCE_PATH.open("r", encoding="utf-8") as handle:
        raw = json.load(handle)
    data = load_curated_energy_evidence()

    assert isinstance(raw, list)
    assert data == raw
    assert len([item for item in data if item.get("domain") == "electrocatalysis"]) >= 14
    required = {
        "domain",
        "title",
        "year",
        "journal",
        "material_system",
        "key_data",
        "key_mechanism",
        "source_text",
        "source_kind",
        "reliability_level",
        "metrics",
    }
    allowed_metric_values = {
        "benchmark-required",
        "report-required",
        "protocol-required",
        "descriptor",
        "dataset-dependent",
        "database-query",
    }
    for item in data:
        assert required.issubset(item)
        assert item["source_text"].startswith(("http://", "https://"))
        assert isinstance(item["metrics"], list)
        for metric in item["metrics"]:
            assert metric.get("metric_name")
            assert metric.get("metric_value") in allowed_metric_values


def test_curated_energy_seed_loader_supports_bundled_json_path():
    with tempfile.TemporaryDirectory() as bundle_dir:
        bundled_path = Path(bundle_dir) / "chalk_app" / "literature" / "curated_energy_evidence.json"
        bundled_path.parent.mkdir(parents=True)
        payload = [
            {
                "domain": "electrocatalysis",
                "title": "Bundled seed smoke test",
                "year": "2026",
                "journal": "test",
                "material_system": "test",
                "key_data": "test",
                "key_mechanism": "test",
                "source_text": "https://example.com",
                "source_kind": "curated_public_metadata",
                "reliability_level": "curated_public_metadata",
                "metrics": [],
            }
        ]
        bundled_path.write_text(json.dumps(payload), encoding="utf-8")

        original_path = evidence_database_module.CURATED_ENERGY_EVIDENCE_PATH
        had_meipass = hasattr(sys, "_MEIPASS")
        original_meipass = getattr(sys, "_MEIPASS", None)
        try:
            evidence_database_module.CURATED_ENERGY_EVIDENCE_PATH = Path(bundle_dir) / "missing.json"
            sys._MEIPASS = bundle_dir

            assert load_curated_energy_evidence() == payload
        finally:
            evidence_database_module.CURATED_ENERGY_EVIDENCE_PATH = original_path
            if had_meipass:
                sys._MEIPASS = original_meipass
            else:
                delattr(sys, "_MEIPASS")


def test_seed_curated_energy_evidence_is_idempotent_and_searchable():
    session, user_id = _session()
    try:
        first = seed_curated_energy_evidence(session, user_id=user_id)
        second = seed_curated_energy_evidence(session, user_id=user_id)

        assert first["literature_evidence"] >= 4
        assert second["literature_evidence"] == first["literature_evidence"]
        assert session.query(LiteratureEvidence).count() == first["literature_evidence"]

        bundle = query_evidence(
            session,
            user_id=user_id,
            domain="battery",
            query_text="Battery Archive capacity retention cycle life lithium-ion",
            battery_type="lithium-ion",
            ion_type="Li",
            max_each=10,
        )
        assert bundle.literature_evidence
        assert bundle.domain_evidence
        assert any("Battery Archive" in item.get("title", "") for item in bundle.literature_evidence)
    finally:
        session.close()


def test_seed_curated_electrocatalysis_evidence_is_broad_and_searchable():
    session, user_id = _session()
    try:
        first = seed_curated_energy_evidence(
            session,
            user_id=user_id,
            domains=("electrocatalysis",),
        )
        literature_count = (
            session.query(LiteratureEvidence)
            .filter(LiteratureEvidence.domain == "electrocatalysis")
            .count()
        )
        domain_count = (
            session.query(DomainEvidence)
            .filter(DomainEvidence.domain == "electrocatalysis")
            .count()
        )
        second = seed_curated_energy_evidence(
            session,
            user_id=user_id,
            domains=("electrocatalysis",),
        )

        assert first["literature_evidence"] >= 14
        assert first["domain_evidence"] >= 30
        assert literature_count >= 14
        assert domain_count >= 30
        assert second["imported"] == 0
        assert second["skipped"] >= literature_count + domain_count
        assert (
            session.query(LiteratureEvidence)
            .filter(LiteratureEvidence.domain == "electrocatalysis")
            .count()
            == literature_count
        )
        assert (
            session.query(DomainEvidence)
            .filter(DomainEvidence.domain == "electrocatalysis")
            .count()
            == domain_count
        )

        scenarios = [
            ("OER overpotential Tafel", {"literature": "McCrory", "metric": "Tafel_slope"}),
            ("ORR mass activity 0.9 V", {"literature": "Gasteiger", "metric": "mass_activity"}),
            (
                "CO2RR Faradaic efficiency partial current",
                {"literature": "CO2", "metric": "partial_current_density"},
            ),
            ("NRR isotope ammonia", {"literature": "ammonia", "metric": "15N2_isotope_control"}),
        ]
        for query_text, expected in scenarios:
            bundle = query_evidence(
                session,
                user_id=user_id,
                domain="electrocatalysis",
                query_text=query_text,
                max_each=10,
            )
            literature_text = " ".join(item.get("title", "") for item in bundle.literature_evidence)
            metric_names = {item.get("metric_name", "") for item in bundle.domain_evidence}
            assert expected["literature"].lower() in literature_text.lower()
            assert expected["metric"] in metric_names
    finally:
        session.close()


def test_index_ocp_oc20dense_targets_creates_real_computational_rows(tmp_path):
    import pickle
    import tarfile

    session, user_id = _session()
    try:
        targets_path = tmp_path / "oc20dense_val_targets.pkl"
        mapping_archive = tmp_path / "oc20_dense_mappings.tar.gz"
        targets = {
            "0_1190_0": [("rand4", -0.90488161), ("rand55", -0.90484483)],
            "1_222_3": [("heur1", 0.1234)],
        }
        mapping = {
            1: {
                "system_id": "0_1190_0",
                "config_id": "rand4",
                "mpid": "mp-865382",
                "miller_idx": (1, 1, 1),
                "shift": 0.042,
                "top": True,
                "adsorbate": "*O",
                "adsorption_site": [2.1, 1.6, 21.5],
            },
            2: {
                "system_id": "1_222_3",
                "config_id": "heur1",
                "mpid": "mp-1",
                "miller_idx": (1, 0, 0),
                "adsorbate": "*CO",
            },
        }
        ref_energies = {"0_1190_0": -404.56678916, "1_222_3": -50.0}
        with targets_path.open("wb") as handle:
            pickle.dump(targets, handle)
        mapping_pickle = tmp_path / "oc20dense_mapping.pkl"
        refs_pickle = tmp_path / "oc20dense_ref_energies.pkl"
        with mapping_pickle.open("wb") as handle:
            pickle.dump(mapping, handle)
        with refs_pickle.open("wb") as handle:
            pickle.dump(ref_energies, handle)
        with tarfile.open(mapping_archive, "w:gz") as archive:
            archive.add(mapping_pickle, arcname="oc20dense_mapping.pkl")
            archive.add(refs_pickle, arcname="oc20dense_ref_energies.pkl")

        result = index_ocp_oc20dense_targets(
            session,
            user_id=user_id,
            targets_path=str(targets_path),
            mapping_archive_path=str(mapping_archive),
            max_rows=10,
        )

        assert result.imported == 2
        assert result.skipped == 0
        bundle = query_evidence(
            session,
            user_id=user_id,
            domain="electrocatalysis",
            query_text="OER *O mp-865382 adsorption energy",
            reaction_type="OER",
            material="mp-865382",
        )
        assert bundle.computational_catalysis_evidence
        row = bundle.computational_catalysis_evidence[0]
        assert row["dataset_name"] == "OC20-Dense"
        assert row["reliability_level"] == "computational_index"
        assert row["adsorbate"] == "*O"
        assert row["adsorption_energy"] == "-0.904882 eV"
        assert "mp-865382" in row["material_system"]

        context = build_database_evidence_context(
            session,
            user_id=user_id,
            domain="electrocatalysis",
            research_question="OER *O mp-865382 adsorption energy",
            reaction_type="OER",
            material="mp-865382",
        )
        assert "OC20-Dense" in context
        assert "CE-" in context
        assert "-0.904882 eV" in context
        assert "不等同于实验性能" in context
    finally:
        session.close()


if __name__ == "__main__":
    test_evidence_tables_are_available_on_new_database()
    test_query_evidence_filters_by_domain_material_and_reaction()
    test_indexers_create_lightweight_evidence_without_mutating_sources()
    test_database_context_marks_ocp_as_computational_evidence()
    test_query_evidence_uses_reaction_adsorbates_not_only_o_star()
    test_query_evidence_groups_adsorbate_energy_set_for_same_site()
    test_query_evidence_warns_when_requested_adsorbate_is_missing()
    test_reaction_adsorbate_sets_cover_multiple_electrocatalysis_reactions()
    test_query_evidence_builds_same_site_sets_for_co2rr_and_nrr()
    with tempfile.TemporaryDirectory() as tmp_dir:
        test_index_ocp_computational_evidence_is_idempotent_for_manifest(Path(tmp_dir))
    with tempfile.TemporaryDirectory() as tmp_dir:
        test_indexed_ocp_manifest_supports_same_site_orr_adsorbate_sets(Path(tmp_dir))
    test_extract_query_facets_keeps_adsorbates_out_of_material_hint()
    test_curated_energy_seed_json_is_loadable_and_auditable()
    test_curated_energy_seed_loader_supports_bundled_json_path()
    test_seed_curated_energy_evidence_is_idempotent_and_searchable()
    test_seed_curated_electrocatalysis_evidence_is_broad_and_searchable()
    with tempfile.TemporaryDirectory() as tmp_dir:
        test_index_ocp_oc20dense_targets_creates_real_computational_rows(Path(tmp_dir))
    print("evidence database tests passed")
