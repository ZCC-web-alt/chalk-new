import json
import sys
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from db import Base, User  # noqa: E402
from evidence_database import (  # noqa: E402
    build_evidence_relationship_graph,
    ensure_computational_catalog,
    index_computational_manifest,
    list_evidence_records,
    register_computational_dataset_source,
    upsert_domain_evidence,
    upsert_literature_evidence,
)


def _session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = SessionLocal()
    user = User(username="workbench_user", password_hash="hash")
    session.add(user)
    session.commit()
    session.refresh(user)
    return session, user.id


def test_ensure_computational_catalog_adds_oc20_oc22_idempotently():
    session, user_id = _session()
    try:
        first = ensure_computational_catalog(session, user_id=user_id)
        second = ensure_computational_catalog(session, user_id=user_id)

        page = list_evidence_records(
            session,
            user_id=user_id,
            filters={"kind": "computational", "keyword": "OC22"},
            page_size=20,
        )

        assert first >= 2
        assert second == first
        assert page.total == 1
        assert page.records[0]["evidence_uid"].startswith("CE-")
        assert page.records[0]["dataset_name"] == "OC22"
        assert page.records[0]["reliability_level"] == "catalog"
    finally:
        session.close()


def test_register_computational_dataset_source_stores_metadata_without_scanning(tmp_path):
    session, user_id = _session()
    try:
        huge_lmdb = tmp_path / "oc22_train.lmdb"
        huge_lmdb.write_bytes(b"not a real lmdb")

        row = register_computational_dataset_source(
            session,
            user_id=user_id,
            dataset_name="OC22",
            source_path=str(huge_lmdb),
            source_format="lmdb",
            split="train",
            notes="local path only",
        )

        extra = json.loads(row.extra_json)
        assert row.dataset_name == "OC22"
        assert row.task_type == "dataset_source"
        assert row.reliability_level == "catalog"
        assert extra["source_path"] == str(huge_lmdb)
        assert extra["source_format"] == "lmdb"
        assert extra["split"] == "train"
    finally:
        session.close()


def test_index_computational_manifest_imports_lightweight_oc_rows(tmp_path):
    session, user_id = _session()
    try:
        manifest = tmp_path / "oc22_manifest.csv"
        manifest.write_text(
            "material_system,reaction_context,adsorbate,adsorption_energy,task_type,source_id\n"
            "NiFe oxide,OER,O*,-1.20 eV,IS2RE,oc22-1\n"
            "Co oxide,ORR,OOH*,-0.35 eV,S2EF,oc22-2\n",
            encoding="utf-8",
        )

        result = index_computational_manifest(
            session,
            user_id=user_id,
            dataset_name="OC22",
            path=str(manifest),
        )
        page = list_evidence_records(
            session,
            user_id=user_id,
            filters={"kind": "computational", "dataset_name": "OC22", "reaction_type": "OER"},
            page_size=20,
        )

        assert result.imported == 2
        assert result.skipped == 0
        assert any(r["material_system"] == "NiFe oxide" for r in page.records)
        indexed = [r for r in page.records if r["reliability_level"] == "computational_index"]
        assert indexed
        assert indexed[0]["adsorbate"] == "*O"
    finally:
        session.close()


def test_index_computational_manifest_preserves_distinct_source_ids(tmp_path):
    session, user_id = _session()
    try:
        manifest = tmp_path / "oc22_duplicate_adsorbate_manifest.jsonl"
        manifest.write_text(
            json.dumps(
                {
                    "material_system": "NiFe oxide",
                    "reaction_context": "OER",
                    "adsorbate": "O*",
                    "adsorption_energy": "-1.20 eV",
                    "task_type": "IS2RE",
                    "source_id": "oc22-a",
                },
                ensure_ascii=False,
            )
            + "\n"
            + json.dumps(
                {
                    "material_system": "NiFe oxide",
                    "reaction_context": "OER",
                    "adsorbate": "O*",
                    "adsorption_energy": "-1.32 eV",
                    "task_type": "IS2RE",
                    "source_id": "oc22-b",
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )

        result = index_computational_manifest(
            session,
            user_id=user_id,
            dataset_name="OC22",
            path=str(manifest),
        )
        page = list_evidence_records(
            session,
            user_id=user_id,
            filters={"kind": "computational", "dataset_name": "OC22", "keyword": "NiFe OER"},
            page_size=20,
        )

        indexed = [r for r in page.records if r["reliability_level"] == "computational_index"]
        assert result.imported == 2
        assert len(indexed) == 2
        assert {"oc22-a", "oc22-b"} == {r["source_url"] for r in indexed}
    finally:
        session.close()


def test_relationship_graph_links_oc22_computational_evidence_to_domain_and_literature():
    session, user_id = _session()
    try:
        upsert_literature_evidence(
            session,
            user_id=user_id,
            domain="electrocatalysis",
            title="NiFe oxide OER literature",
            material_system="NiFe oxide",
            reaction_type="OER",
            key_data="OER overpotential 260 mV",
            reliability_level="verified",
        )
        upsert_domain_evidence(
            session,
            user_id=user_id,
            domain="electrocatalysis",
            material_system="NiFe oxide",
            reaction_type="OER",
            metric_name="overpotential",
            metric_value="260",
            metric_unit="mV",
            reliability_level="verified",
        )
        manifest = ROOT / "tmp" / "test_oc22_graph_manifest.csv"
        manifest.parent.mkdir(exist_ok=True)
        manifest.write_text(
            "material_system,reaction_context,adsorbate,adsorption_energy,task_type\n"
            "NiFe oxide,OER,O*,-1.20 eV,IS2RE\n",
            encoding="utf-8",
        )
        try:
            index_computational_manifest(
                session,
                user_id=user_id,
                dataset_name="OC22",
                path=str(manifest),
            )
            page = list_evidence_records(
                session,
                user_id=user_id,
                filters={"keyword": "NiFe OER"},
                page_size=20,
            )
            graph = build_evidence_relationship_graph(page.records)
        finally:
            manifest.unlink(missing_ok=True)

        edge_types = {edge["relation_type"] for edge in graph["edges"]}
        node_ids = {node["id"] for node in graph["nodes"]}
        assert any(node_id.startswith("CE-") for node_id in node_ids)
        assert "same_material" in edge_types
        assert "same_reaction" in edge_types
        assert all(edge.get("label") for edge in graph["edges"])
        assert all(edge.get("reason") for edge in graph["edges"])
        assert all(node.get("kind_label") for node in graph["nodes"])
        assert all(node.get("reliability_label") for node in graph["nodes"])
    finally:
        session.close()


if __name__ == "__main__":
    test_ensure_computational_catalog_adds_oc20_oc22_idempotently()
    test_register_computational_dataset_source_stores_metadata_without_scanning(Path("tmp"))
    test_index_computational_manifest_imports_lightweight_oc_rows(Path("tmp"))
    test_index_computational_manifest_preserves_distinct_source_ids(Path("tmp"))
    test_relationship_graph_links_oc22_computational_evidence_to_domain_and_literature()
    print("evidence workbench tests passed")
