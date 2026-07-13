"""Structured evidence database helpers for domain hypothesis generation.

This module keeps the first version intentionally lightweight: it stores and
retrieves compact evidence indexes instead of downloading large external
datasets such as OC20/OC22/OC25 LMDB files.
"""

from __future__ import annotations

import csv
import json
import pickle
import tarfile
from datetime import datetime
from pathlib import Path
import re
import sys
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional

from sqlalchemy import or_
from sqlalchemy.orm import Session

from db import (
    ComputationalCatalysisEvidence,
    Document,
    DocumentChunk,
    DomainEvidence,
    LabRecord,
    LiteratureEvidence,
)


REACTION_ADSORBATE_MAP: Dict[str, List[str]] = {
    "OER": ["*O", "*OH", "*OOH"],
    "ORR": ["*O", "*OH", "*OOH"],
    "HER": ["*H"],
    "HOR": ["*H"],
    "CO2RR": ["*CO2", "*COOH", "*CO", "*OCHO"],
    "NRR": ["*N2", "*NNH", "*N", "*NH", "*NH2", "*NH3"],
}

ADSORBATE_ALIASES: Dict[str, str] = {
    "O*": "*O",
    "*O": "*O",
    "OH*": "*OH",
    "*OH": "*OH",
    "OOH*": "*OOH",
    "*OOH": "*OOH",
    "H*": "*H",
    "*H": "*H",
    "CO*": "*CO",
    "*CO": "*CO",
    "CO2*": "*CO2",
    "*CO2": "*CO2",
    "COOH*": "*COOH",
    "*COOH": "*COOH",
    "OCHO*": "*OCHO",
    "*OCHO": "*OCHO",
    "N2*": "*N2",
    "*N2": "*N2",
    "NNH*": "*NNH",
    "*NNH": "*NNH",
    "N*": "*N",
    "*N": "*N",
    "NH*": "*NH",
    "*NH": "*NH",
    "NH2*": "*NH2",
    "*NH2": "*NH2",
    "NH3*": "*NH3",
    "*NH3": "*NH3",
}


OCP_DATASET_CATALOG: Dict[str, Dict[str, str]] = {
    "Open Catalyst Project / FAIR-Chem": {
        "scope": "FAIR-Chem 维护的开放催化机器学习项目入口。",
        "url": "https://fair-chem.github.io/",
        "note": "作为计算催化数据和模型生态入口，不等同于实验性能数据库。",
    },
    "OC20": {
        "scope": "吸附物-催化剂结构、能量和力数据，覆盖 IS2RE/S2EF/IS2RS 等任务。",
        "url": "https://opencatalystproject.org/",
        "note": "适合吸附能、结构弛豫和原子势模型预训练证据索引。",
    },
    "OC20-Dense": {
        "scope": "OCP 2023 AdsorbML/OC20-Dense 挑战验证数据，包含 adsorbate-surface 组合的 DFT 吸附能 targets。",
        "url": "https://opencatalystproject.org/challenge.html",
        "note": "适合作为真实 OCP 计算条目索引，用于假设生成中的结构-吸附能线索；不代表实验活性。",
    },
    "OC22": {
        "scope": "氧化物电催化场景扩展，重点支持 OER/ORR 相关计算建模。",
        "url": "https://arxiv.org/abs/2206.08917",
        "note": "适合作为电催化氧化物材料计算基线和机制证据。",
    },
    "OC25": {
        "scope": "固-液催化界面数据，强调显式溶剂/离子环境。",
        "url": "https://arxiv.org/abs/2509.17862",
        "note": "适合真实电催化界面、溶剂化和离子环境假设，不直接代表实验过电位。",
    },
    "ODAC23": {
        "scope": "MOF/多孔材料 CO2 吸附计算数据。",
        "url": "https://arxiv.org/abs/2311.00341",
        "note": "主要服务 CO2 捕集和 MOF 吸附，对 CO2RR 前处理和 MOF 衍生催化剂有间接参考价值。",
    },
}


EXTERNAL_DATA_SOURCE_WHITELIST: Dict[str, str] = {
    "Materials Project": "https://materialsproject.org/",
    "OQMD": "https://oqmd.org/",
    "NOMAD": "https://nomad-lab.eu/",
    "Catalysis-Hub": "https://www.catalysis-hub.org/",
    "Battery Archive": "https://www.batteryarchive.org/",
    "NASA battery dataset": "https://www.nasa.gov/intelligent-systems-division/discovery-and-systems-health/pcoe/pcoe-data-set-repository/",
    "CALCE battery dataset": "https://calce.umd.edu/data",
}

CURATED_ENERGY_EVIDENCE_PATH = Path(__file__).with_name("curated_energy_evidence.json")


def _curated_energy_evidence_path() -> Path:
    """Resolve the curated seed JSON in source and PyInstaller builds."""
    candidates = [CURATED_ENERGY_EVIDENCE_PATH]
    bundle_root = getattr(sys, "_MEIPASS", "")
    if bundle_root:
        root = Path(bundle_root)
        candidates.extend(
            [
                root / "curated_energy_evidence.json",
                root / "chalk_app" / "literature" / "curated_energy_evidence.json",
                root / "src" / "chalk_app" / "literature" / "curated_energy_evidence.json",
            ]
        )
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return CURATED_ENERGY_EVIDENCE_PATH


def load_curated_energy_evidence(path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Load auditable built-in energy evidence seeds from JSON."""
    seed_path = Path(path) if path is not None else _curated_energy_evidence_path()
    with seed_path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, list):
        raise ValueError(f"Curated energy evidence seed must be a list: {seed_path}")
    return data


CURATED_ENERGY_EVIDENCE: List[Dict[str, Any]] = load_curated_energy_evidence()


@dataclass
class EvidenceSearchBundle:
    literature_evidence: List[Dict[str, Any]] = field(default_factory=list)
    domain_evidence: List[Dict[str, Any]] = field(default_factory=list)
    computational_catalysis_evidence: List[Dict[str, Any]] = field(default_factory=list)
    adsorbate_energy_sets: List[Dict[str, Any]] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    @property
    def has_evidence(self) -> bool:
        return bool(
            self.literature_evidence
            or self.domain_evidence
            or self.computational_catalysis_evidence
        )


@dataclass
class ImportResult:
    imported: int = 0
    skipped: int = 0
    errors: List[str] = field(default_factory=list)


@dataclass
class EvidencePage:
    records: List[Dict[str, Any]] = field(default_factory=list)
    total: int = 0
    page: int = 1
    page_size: int = 50
    filters: Dict[str, Any] = field(default_factory=dict)


def upsert_literature_evidence(session: Session, *, user_id: int, **kwargs) -> LiteratureEvidence:
    """Create or update a literature evidence row scoped to one user."""
    title = _clean(kwargs.get("title"))
    doi = _clean(kwargs.get("doi"))
    paper_id = _clean(kwargs.get("paper_id"))
    query = session.query(LiteratureEvidence).filter(LiteratureEvidence.user_id == user_id)
    if doi:
        query = query.filter(LiteratureEvidence.doi == doi)
    elif paper_id:
        query = query.filter(LiteratureEvidence.paper_id == paper_id)
    else:
        query = query.filter(LiteratureEvidence.title == title)
    row = query.first()
    if row is None:
        row = LiteratureEvidence(user_id=user_id)
        session.add(row)
    _assign_fields(
        row,
        kwargs,
        [
            "document_id",
            "hypothesis_id",
            "domain",
            "paper_id",
            "title",
            "doi",
            "year",
            "journal",
            "material_system",
            "reaction_type",
            "battery_type",
            "ion_type",
            "key_data",
            "key_mechanism",
            "limitation",
            "source_text",
            "source_kind",
            "reliability_level",
            "extra_json",
        ],
    )
    session.commit()
    session.refresh(row)
    return row


def upsert_domain_evidence(session: Session, *, user_id: int, **kwargs) -> DomainEvidence:
    """Create or update a domain metric/mechanism evidence row scoped to one user."""
    metric_name = _clean(kwargs.get("metric_name"))
    metric_value = _clean(kwargs.get("metric_value"))
    material_system = _clean(kwargs.get("material_system"))
    reaction_type = _clean(kwargs.get("reaction_type"))
    literature_evidence_id = kwargs.get("literature_evidence_id")
    lab_record_id = kwargs.get("lab_record_id")
    hypothesis_id = kwargs.get("hypothesis_id")
    query = (
        session.query(DomainEvidence)
        .filter(DomainEvidence.user_id == user_id)
        .filter(DomainEvidence.metric_name == metric_name)
        .filter(DomainEvidence.metric_value == metric_value)
        .filter(DomainEvidence.material_system == material_system)
        .filter(DomainEvidence.reaction_type == reaction_type)
    )
    if literature_evidence_id is not None:
        query = query.filter(DomainEvidence.literature_evidence_id == literature_evidence_id)
    elif lab_record_id is not None:
        query = query.filter(DomainEvidence.lab_record_id == lab_record_id)
    elif hypothesis_id is not None:
        query = query.filter(DomainEvidence.hypothesis_id == hypothesis_id)
    else:
        source_kind = _clean(kwargs.get("source_kind")) or "manual"
        query = query.filter(DomainEvidence.source_kind == source_kind)
    row = query.first()
    if row is None:
        row = DomainEvidence(user_id=user_id)
        session.add(row)
    _assign_fields(
        row,
        kwargs,
        [
            "literature_evidence_id",
            "lab_record_id",
            "hypothesis_id",
            "domain",
            "material_system",
            "reaction_type",
            "battery_type",
            "ion_type",
            "metric_name",
            "metric_value",
            "metric_unit",
            "condition_text",
            "baseline",
            "key_mechanism",
            "source_text",
            "source_kind",
            "reliability_level",
            "extra_json",
        ],
    )
    session.commit()
    session.refresh(row)
    return row


def upsert_computational_catalysis_evidence(
    session: Session, *, user_id: int, **kwargs
) -> ComputationalCatalysisEvidence:
    """Create or update a lightweight computational catalysis evidence row."""
    dataset_name = _clean(kwargs.get("dataset_name"))
    task_type = _clean(kwargs.get("task_type"))
    reaction_context = _clean(kwargs.get("reaction_context"))
    material_system = _clean(kwargs.get("material_system"))
    if "adsorbate" in kwargs:
        kwargs["adsorbate"] = normalize_adsorbate(kwargs.get("adsorbate"))
    adsorbate = _clean(kwargs.get("adsorbate"))
    source_url = _clean(kwargs.get("source_url"), max_len=500)
    query = (
        session.query(ComputationalCatalysisEvidence)
        .filter(ComputationalCatalysisEvidence.user_id == user_id)
        .filter(ComputationalCatalysisEvidence.dataset_name == dataset_name)
        .filter(ComputationalCatalysisEvidence.task_type == task_type)
        .filter(ComputationalCatalysisEvidence.reaction_context == reaction_context)
        .filter(ComputationalCatalysisEvidence.material_system == material_system)
        .filter(ComputationalCatalysisEvidence.adsorbate == adsorbate)
        .filter(ComputationalCatalysisEvidence.source_url == source_url)
    )
    row = query.first()
    if row is None:
        row = ComputationalCatalysisEvidence(user_id=user_id)
        session.add(row)
    _assign_fields(
        row,
        kwargs,
        [
            "domain",
            "dataset_name",
            "task_type",
            "reaction_context",
            "material_system",
            "surface_facet",
            "adsorbate",
            "electrolyte_or_solvent",
            "dft_energy",
            "adsorption_energy",
            "model_name",
            "predicted_value",
            "reference_value",
            "source_url",
            "evidence_kind",
            "reliability_level",
            "notes",
            "extra_json",
        ],
    )
    if not row.domain:
        row.domain = "electrocatalysis"
    if not row.evidence_kind:
        row.evidence_kind = "computational"
    session.commit()
    session.refresh(row)
    return row


def seed_computational_source_catalog(session: Session, *, user_id: int) -> int:
    """Seed compact OCP/FAIR-Chem catalog rows for a user if missing."""
    created_or_seen = 0
    for name, meta in OCP_DATASET_CATALOG.items():
        existing = (
            session.query(ComputationalCatalysisEvidence)
            .filter(ComputationalCatalysisEvidence.user_id == user_id)
            .filter(ComputationalCatalysisEvidence.dataset_name == name)
            .filter(ComputationalCatalysisEvidence.task_type == "source_catalog")
            .filter(ComputationalCatalysisEvidence.reliability_level == "catalog")
            .first()
        )
        if existing:
            _assign_fields(
                existing,
                {
                    "domain": "electrocatalysis",
                    "task_type": "source_catalog",
                    "reaction_context": _catalog_reaction_context(name),
                    "material_system": meta["scope"],
                    "source_url": meta["url"],
                    "evidence_kind": "computational",
                    "reliability_level": "catalog",
                    "notes": meta["note"],
                    "extra_json": json.dumps(meta, ensure_ascii=False),
                },
                [
                    "domain",
                    "task_type",
                    "reaction_context",
                    "material_system",
                    "source_url",
                    "evidence_kind",
                    "reliability_level",
                    "notes",
                    "extra_json",
                ],
            )
            session.commit()
            created_or_seen += 1
            continue
        upsert_computational_catalysis_evidence(
            session,
            user_id=user_id,
            dataset_name=name,
            task_type="source_catalog",
            reaction_context=_catalog_reaction_context(name),
            material_system=meta["scope"],
            source_url=meta["url"],
            reliability_level="catalog",
            notes=meta["note"],
            extra_json=json.dumps(meta, ensure_ascii=False),
        )
        created_or_seen += 1
    return created_or_seen


def ensure_computational_catalog(session: Session, *, user_id: int) -> int:
    """Ensure built-in computational source catalog rows exist for one user."""
    return seed_computational_source_catalog(session, user_id=user_id)


def register_computational_dataset_source(
    session: Session,
    *,
    user_id: int,
    dataset_name: str,
    source_path: str,
    source_format: str,
    split: str = "",
    notes: str = "",
) -> ComputationalCatalysisEvidence:
    """Register a local OC/OCP source path without scanning large raw files."""
    path_text = _clean(source_path, max_len=2000)
    fmt = _clean(source_format).lower()
    dataset = _clean(dataset_name) or "OC dataset"
    source = Path(path_text).expanduser() if path_text else Path("")
    extra = {
        "source_path": path_text,
        "source_format": fmt,
        "split": _clean(split),
        "notes": _clean(notes, max_len=5000),
        "path_exists": bool(path_text and source.exists()),
        "registered_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "index_mode": "path_only",
    }
    return upsert_computational_catalysis_evidence(
        session,
        user_id=user_id,
        dataset_name=dataset,
        task_type="dataset_source",
        reaction_context=_catalog_reaction_context(dataset),
        material_system="local dataset source",
        source_url=path_text,
        evidence_kind="computational",
        reliability_level="catalog",
        notes=notes or "Registered local data source path only; raw files were not imported.",
        extra_json=extra,
    )


def index_computational_manifest(
    session: Session,
    *,
    user_id: int,
    dataset_name: str,
    path: str,
    max_rows: int = 5000,
) -> ImportResult:
    """Index a small CSV/JSON/JSONL computational manifest into lightweight rows."""
    manifest_path = Path(_clean(path, max_len=2000)).expanduser()
    result = ImportResult()
    if max_rows <= 0:
        result.errors.append("max_rows must be positive")
        return result
    if not manifest_path.exists() or not manifest_path.is_file():
        result.errors.append(f"Manifest not found: {manifest_path}")
        return result

    suffix = manifest_path.suffix.lower()
    if suffix not in {".csv", ".json", ".jsonl", ".ndjson"}:
        result.errors.append("Unsupported manifest format; use CSV, JSON, or JSONL.")
        return result

    try:
        rows = _read_manifest_rows(manifest_path, max_rows=max_rows)
    except Exception as exc:
        result.errors.append(f"Failed to read manifest: {exc}")
        return result

    dataset = _clean(dataset_name) or _infer_dataset_name(manifest_path)
    batch_id = f"{dataset}-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
    for index, raw_row in enumerate(rows, 1):
        if not isinstance(raw_row, dict):
            result.skipped += 1
            continue
        mapped = _map_computational_manifest_row(raw_row)
        material = mapped.get("material_system", "")
        reaction = mapped.get("reaction_context", "")
        if not material or not reaction:
            result.skipped += 1
            continue

        extra = {
            "manifest_path": str(manifest_path),
            "manifest_row": index,
            "manifest_batch_id": batch_id,
            "source_id": mapped.get("source_id", ""),
            "raw_manifest_row": _compact_raw_row(raw_row),
            "indexed_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        }
        upsert_computational_catalysis_evidence(
            session,
            user_id=user_id,
            domain=mapped.get("domain") or "electrocatalysis",
            dataset_name=dataset,
            task_type=mapped.get("task_type") or "manifest_index",
            reaction_context=reaction,
            material_system=material,
            surface_facet=mapped.get("surface_facet", ""),
            adsorbate=mapped.get("adsorbate", ""),
            electrolyte_or_solvent=mapped.get("electrolyte_or_solvent", ""),
            dft_energy=mapped.get("dft_energy", ""),
            adsorption_energy=mapped.get("adsorption_energy", ""),
            model_name=mapped.get("model_name", ""),
            predicted_value=mapped.get("predicted_value", ""),
            reference_value=mapped.get("reference_value", ""),
            source_url=mapped.get("source_url") or mapped.get("source_id", ""),
            evidence_kind="computational",
            reliability_level="computational_index",
            notes=mapped.get("notes") or "Lightweight manifest index; computational evidence only.",
            extra_json=extra,
        )
        result.imported += 1

    return result


def index_ocp_computational_evidence(
    session: Session,
    *,
    user_id: int,
    dataset_name: str,
    source_path: str,
    max_rows: int = 50000,
    checkpoint_path: Optional[str] = None,
    batch_size: int = 500,
) -> ImportResult:
    """Index local OCP/OC-style manifest rows as searchable evidence.

    This importer stores compact metadata and energies only. Raw structures,
    trajectories, and large LMDB/ASE payloads stay on disk and are referenced
    through ``extra_json`` so hypothesis generation can retrieve precise
    material/facet/site/adsorbate rows without bloating SQLite.
    """
    result = ImportResult()
    if max_rows <= 0:
        result.errors.append("max_rows must be positive")
        return result

    root = Path(_clean(source_path, max_len=2000)).expanduser()
    if not root.exists():
        result.errors.append(f"OCP source not found: {root}")
        return result

    files = _ocp_manifest_files(root)
    if not files:
        result.errors.append("Unsupported OCP source; provide CSV, JSON, JSONL, NDJSON, or a directory containing them.")
        return result

    checkpoint_file = Path(_clean(checkpoint_path, max_len=2000)).expanduser() if checkpoint_path else None
    processed_keys = _load_checkpoint_keys(checkpoint_file)
    rows_seen = 0
    dataset_default = _clean(dataset_name) or _infer_dataset_name(root if root.is_file() else files[0])
    batch_id = f"{dataset_default}-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"

    for manifest_file in files:
        if rows_seen >= max_rows:
            break
        remaining = max_rows - rows_seen
        try:
            rows = _read_manifest_rows(manifest_file, max_rows=remaining)
        except Exception as exc:
            result.errors.append(f"Failed to read {manifest_file}: {exc}")
            continue

        for index, raw_row in enumerate(rows, 1):
            if rows_seen >= max_rows:
                break
            rows_seen += 1
            if not isinstance(raw_row, dict):
                result.skipped += 1
                continue

            mapped = _map_computational_manifest_row(raw_row)
            row_dataset = (
                _manifest_row_value(raw_row, ["dataset", "dataset_name", "oc_dataset", "source_dataset"])
                or dataset_default
            )
            dataset = _clean(row_dataset) or dataset_default
            adsorbate = normalize_adsorbate(mapped.get("adsorbate") or _manifest_row_value(raw_row, ["ads", "adsorbate"]))
            material = (
                mapped.get("material_system")
                or _manifest_row_value(raw_row, ["mpid", "mp_id", "material_id", "bulk_id", "slab_id", "surface_id"])
            )
            material = _clean(material)
            if not material or not adsorbate:
                result.skipped += 1
                continue

            split = _manifest_row_value(raw_row, ["split", "subset", "partition"])
            system_id = _manifest_row_value(raw_row, ["system_id", "sid", "system", "entry_id", "oc_id"])
            config_id = _manifest_row_value(raw_row, ["config_id", "configuration_id", "config", "frame_id"])
            active_site = _manifest_row_value(raw_row, ["active_site", "site", "adsorption_site", "site_label"])
            facet = mapped.get("surface_facet") or _manifest_row_value(raw_row, ["facet", "surface_facet", "miller_index", "miller_idx"])
            facet = _format_miller_index(facet) if facet else ""
            reaction = mapped.get("reaction_context") or _infer_reaction_context_from_adsorbate(adsorbate)
            task_type = mapped.get("task_type") or "ocp_computational_index"
            row_source_path = _manifest_row_value(raw_row, ["source_path", "raw_path", "traj_path", "lmdb_path", "ase_path", "file_path"])
            explicit_url = _manifest_row_value(raw_row, ["source_url", "url", "uri", "download_url"])
            source_url = explicit_url or _ocp_evidence_source_url(
                dataset=dataset,
                system_id=system_id or mapped.get("source_id") or material,
                config_id=config_id or str(index),
                adsorbate=adsorbate,
            )
            unique_key = "|".join([dataset, task_type, reaction, material, adsorbate, source_url])
            if unique_key in processed_keys:
                result.skipped += 1
                continue

            existing = (
                session.query(ComputationalCatalysisEvidence)
                .filter(ComputationalCatalysisEvidence.user_id == user_id)
                .filter(ComputationalCatalysisEvidence.dataset_name == dataset)
                .filter(ComputationalCatalysisEvidence.task_type == task_type)
                .filter(ComputationalCatalysisEvidence.reaction_context == reaction)
                .filter(ComputationalCatalysisEvidence.material_system == material)
                .filter(ComputationalCatalysisEvidence.adsorbate == adsorbate)
                .filter(ComputationalCatalysisEvidence.source_url == source_url)
                .first()
            )
            if existing is not None:
                processed_keys.add(unique_key)
                result.skipped += 1
                continue

            extra = {
                "index_mode": "ocp_searchable_index",
                "dataset": dataset,
                "split": _clean(split),
                "system_id": _clean(system_id),
                "config_id": _clean(config_id),
                "active_site": _clean(active_site),
                "source_path": _clean(row_source_path, max_len=2000),
                "manifest_file": str(manifest_file),
                "manifest_row": index,
                "manifest_batch_id": batch_id,
                "raw_metadata": _compact_raw_row(raw_row),
                "indexed_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
                "evidence_boundary": "OCP/OC computational index only; not experimental performance.",
            }
            upsert_computational_catalysis_evidence(
                session,
                user_id=user_id,
                domain=mapped.get("domain") or "electrocatalysis",
                dataset_name=dataset,
                task_type=task_type,
                reaction_context=reaction,
                material_system=material,
                surface_facet=facet,
                adsorbate=adsorbate,
                electrolyte_or_solvent=mapped.get("electrolyte_or_solvent", ""),
                dft_energy=mapped.get("dft_energy", ""),
                adsorption_energy=_format_ev(mapped.get("adsorption_energy")) if mapped.get("adsorption_energy") else "",
                model_name=mapped.get("model_name", ""),
                predicted_value=mapped.get("predicted_value", ""),
                reference_value=mapped.get("reference_value", ""),
                source_url=source_url,
                evidence_kind="computational",
                reliability_level="computational_index",
                notes=mapped.get("notes") or "Lightweight OCP/OC searchable index; raw structures remain on disk.",
                extra_json=extra,
            )
            processed_keys.add(unique_key)
            result.imported += 1
            if checkpoint_file and batch_size > 0 and (result.imported + result.skipped) % batch_size == 0:
                _write_checkpoint_keys(checkpoint_file, processed_keys)

    if checkpoint_file:
        _write_checkpoint_keys(checkpoint_file, processed_keys)
    return result


def index_ocp_oc20dense_targets(
    session: Session,
    *,
    user_id: int,
    targets_path: str,
    mapping_archive_path: str,
    max_rows: int = 5000,
) -> ImportResult:
    """Index official OCP OC20-Dense target/mapping files as real computational rows.

    The official files are still treated as a lightweight index: this function
    stores one lowest-energy row per adsorbate-surface system, not the full
    LMDB trajectories.
    """
    result = ImportResult()
    if max_rows <= 0:
        result.errors.append("max_rows must be positive")
        return result

    targets_file = Path(_clean(targets_path, max_len=2000)).expanduser()
    mapping_archive = Path(_clean(mapping_archive_path, max_len=2000)).expanduser()
    if not targets_file.exists() or not targets_file.is_file():
        result.errors.append(f"OCP targets file not found: {targets_file}")
        return result
    if not mapping_archive.exists() or not mapping_archive.is_file():
        result.errors.append(f"OCP mapping archive not found: {mapping_archive}")
        return result

    try:
        with targets_file.open("rb") as handle:
            targets = pickle.load(handle)
        mapping, ref_energies = _read_oc20dense_mapping_archive(mapping_archive)
    except Exception as exc:
        result.errors.append(f"Failed to read OCP OC20-Dense files: {exc}")
        return result

    if not isinstance(targets, dict):
        result.errors.append("Unsupported OCP targets format; expected a dictionary.")
        return result

    mapping_by_system_config: Dict[tuple[str, str], Dict[str, Any]] = {}
    mapping_by_system: Dict[str, Dict[str, Any]] = {}
    if isinstance(mapping, dict):
        for item in mapping.values():
            if not isinstance(item, dict):
                continue
            system_id = _clean(item.get("system_id"))
            config_id = _clean(item.get("config_id"))
            if not system_id:
                continue
            mapping_by_system.setdefault(system_id, item)
            if config_id:
                mapping_by_system_config[(system_id, config_id)] = item

    ensure_computational_catalog(session, user_id=user_id)
    batch_id = f"OC20-Dense-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
    for system_id, target_values in list(targets.items())[:max_rows]:
        system = _clean(system_id)
        best_config, best_energy = _best_ocp_target(target_values)
        if not system or best_config == "" or best_energy is None:
            result.skipped += 1
            continue

        meta = mapping_by_system_config.get((system, best_config)) or mapping_by_system.get(system) or {}
        mpid = _clean(meta.get("mpid")) or system
        adsorbate = _clean(meta.get("adsorbate")) or _infer_adsorbate_from_system(system)
        surface_facet = _format_miller_index(meta.get("miller_idx"))
        adsorption_energy = _format_ev(best_energy)
        reference_energy = ""
        if isinstance(ref_energies, dict) and system in ref_energies:
            reference_energy = _format_ev(ref_energies.get(system))
        material_system = _join_nonempty([mpid, surface_facet and f"facet {surface_facet}"], " ")
        if not material_system:
            material_system = system

        extra = {
            "source_kind": "official_ocp_oc20dense_target",
            "official_dataset": "OC20-Dense",
            "system_id": system,
            "best_config_id": best_config,
            "target_count": len(target_values) if hasattr(target_values, "__len__") else "",
            "mapping_mpid": mpid,
            "miller_idx": surface_facet,
            "shift": _jsonable(meta.get("shift")),
            "top": _jsonable(meta.get("top")),
            "adsorption_site": _jsonable(meta.get("adsorption_site")),
            "targets_file": str(targets_file),
            "mapping_archive": str(mapping_archive),
            "manifest_batch_id": batch_id,
            "indexed_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
            "evidence_boundary": "OCP/DFT 计算证据，只能作为结构-吸附能线索，不等同于实验过电位、活性或稳定性。",
            "source_urls": [
                "https://opencatalystproject.org/challenge.html",
                "https://dl.fbaipublicfiles.com/opencatalystproject/data/neurips_2023/oc20dense_val_targets.pkl",
                "https://dl.fbaipublicfiles.com/opencatalystproject/data/adsorbml/oc20_dense_mappings.tar.gz",
            ],
        }

        upsert_computational_catalysis_evidence(
            session,
            user_id=user_id,
            domain="electrocatalysis",
            dataset_name="OC20-Dense",
            task_type="AdsorbML adsorption-energy target",
            reaction_context=_infer_reaction_context_from_adsorbate(adsorbate),
            material_system=material_system,
            surface_facet=surface_facet,
            adsorbate=adsorbate,
            dft_energy=reference_energy,
            adsorption_energy=adsorption_energy,
            reference_value=reference_energy,
            source_url=f"ocp://oc20-dense/{system}/{best_config}",
            evidence_kind="computational",
            reliability_level="computational_index",
            notes=(
                f"OCP OC20-Dense official DFT target: {adsorbate or 'adsorbate'} on {mpid}; "
                "computed evidence only, not experimental performance."
            ),
            extra_json=extra,
        )
        result.imported += 1

    return result


def seed_curated_energy_evidence(
    session: Session,
    *,
    user_id: int,
    domains: Optional[Iterable[str]] = None,
) -> Dict[str, int]:
    """Seed small, auditable battery/electrocatalysis literature and metric evidence."""
    allowed = {_normalize_domain(domain) for domain in domains or [] if _normalize_domain(domain)}
    stats = {
        "literature_evidence": 0,
        "domain_evidence": 0,
        "literature_imported": 0,
        "literature_updated": 0,
        "literature_skipped": 0,
        "domain_imported": 0,
        "domain_updated": 0,
        "domain_skipped": 0,
    }
    for item in load_curated_energy_evidence():
        domain = _normalize_domain(item.get("domain", ""))
        if allowed and domain not in allowed:
            continue
        title = item.get("title", "")
        doi = item.get("doi", "")
        existing_literature = (
            session.query(LiteratureEvidence)
            .filter(LiteratureEvidence.user_id == user_id)
            .filter(LiteratureEvidence.doi == doi if doi else LiteratureEvidence.title == title)
            .first()
        )
        extra = {
            "curated_seed": True,
            "source_url": item.get("source_text", ""),
            "evidence_boundary": "Curated public metadata; verify against source papers or datasets before treating as final numeric evidence.",
        }
        literature = upsert_literature_evidence(
            session,
            user_id=user_id,
            domain=domain,
            title=title,
            doi=doi,
            year=str(item.get("year", "")),
            journal=item.get("journal", ""),
            material_system=item.get("material_system", ""),
            reaction_type=item.get("reaction_type", ""),
            battery_type=item.get("battery_type", ""),
            ion_type=item.get("ion_type", ""),
            key_data=item.get("key_data", ""),
            key_mechanism=item.get("key_mechanism", ""),
            limitation="Use as curated source/metric metadata unless a concrete imported row supplies numeric values.",
            source_text=item.get("source_text", ""),
            source_kind=item.get("source_kind", "curated_public_metadata"),
            reliability_level=item.get("reliability_level", "curated_public_metadata"),
            extra_json=extra,
        )
        stats["literature_evidence"] += 1
        if existing_literature is None:
            stats["literature_imported"] += 1
        else:
            stats["literature_skipped"] += 1
        for metric in item.get("metrics", []) or []:
            metric_name = metric.get("metric_name", "")
            metric_value = metric.get("metric_value", "")
            material_system = metric.get("material_system") or item.get("material_system", "")
            reaction_type = metric.get("reaction_type") or item.get("reaction_type", "")
            existing_domain = (
                session.query(DomainEvidence)
                .filter(DomainEvidence.user_id == user_id)
                .filter(DomainEvidence.literature_evidence_id == literature.id)
                .filter(DomainEvidence.metric_name == _clean(metric_name))
                .filter(DomainEvidence.metric_value == _clean(metric_value))
                .filter(DomainEvidence.material_system == _clean(material_system))
                .filter(DomainEvidence.reaction_type == _clean(reaction_type))
                .first()
            )
            upsert_domain_evidence(
                session,
                user_id=user_id,
                literature_evidence_id=literature.id,
                domain=domain,
                material_system=material_system,
                reaction_type=reaction_type,
                battery_type=metric.get("battery_type") or item.get("battery_type", ""),
                ion_type=metric.get("ion_type") or item.get("ion_type", ""),
                metric_name=metric_name,
                metric_value=metric_value,
                metric_unit=metric.get("metric_unit", ""),
                condition_text=metric.get("condition_text", ""),
                baseline=metric.get("baseline", ""),
                key_mechanism=metric.get("key_mechanism", item.get("key_mechanism", "")),
                source_text=item.get("source_text", ""),
                source_kind=item.get("source_kind", "curated_public_metadata"),
                reliability_level=item.get("reliability_level", "curated_public_metadata"),
                extra_json=extra,
            )
            stats["domain_evidence"] += 1
            if existing_domain is None:
                stats["domain_imported"] += 1
            else:
                stats["domain_skipped"] += 1
    stats["imported"] = stats["literature_imported"] + stats["domain_imported"]
    stats["updated"] = stats["literature_updated"] + stats["domain_updated"]
    stats["skipped"] = stats["literature_skipped"] + stats["domain_skipped"]
    return stats


def list_evidence_records(
    session: Session,
    *,
    user_id: int,
    filters: Optional[Dict[str, Any]] = None,
    page: int = 1,
    page_size: int = 50,
) -> EvidencePage:
    """List mixed evidence records with simple filters and stable evidence IDs."""
    filters = dict(filters or {})
    page = max(1, int(page or 1))
    page_size = min(200, max(1, int(page_size or 50)))
    kind = _clean(filters.get("kind")).lower()

    records: List[Dict[str, Any]] = []
    if kind in {"", "all", "literature", "literature_evidence"}:
        query = session.query(LiteratureEvidence).filter(LiteratureEvidence.user_id == user_id)
        query = _apply_common_evidence_filters(query, LiteratureEvidence, filters, table_kind="literature")
        records.extend(_workbench_literature_record(row) for row in query.all())
    if kind in {"", "all", "domain", "domain_evidence"}:
        query = session.query(DomainEvidence).filter(DomainEvidence.user_id == user_id)
        query = _apply_common_evidence_filters(query, DomainEvidence, filters, table_kind="domain")
        records.extend(_workbench_domain_record(row) for row in query.all())
    if kind in {"", "all", "computational", "computational_catalysis_evidence"}:
        query = session.query(ComputationalCatalysisEvidence).filter(
            ComputationalCatalysisEvidence.user_id == user_id
        )
        query = _apply_common_evidence_filters(query, ComputationalCatalysisEvidence, filters, table_kind="computational")
        records.extend(_workbench_computational_record(row) for row in query.all())

    keyword = _clean(filters.get("keyword")).lower()
    if keyword:
        tokens = _tokens(keyword)
        records = [
            record
            for record in records
            if _record_matches_tokens(record, tokens)
        ]

    records.sort(key=lambda record: str(record.get("updated_at") or record.get("created_at") or ""), reverse=True)
    total = len(records)
    start = (page - 1) * page_size
    return EvidencePage(
        records=records[start:start + page_size],
        total=total,
        page=page,
        page_size=page_size,
        filters=filters,
    )


def build_evidence_relationship_graph(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Build a lightweight relationship graph from workbench evidence records."""
    nodes: List[Dict[str, Any]] = []
    edges: List[Dict[str, Any]] = []
    seen_nodes: set[str] = set()
    seen_edges: set[tuple[str, str, str]] = set()

    normalized_records = [dict(record) for record in records if isinstance(record, dict)]
    for record in normalized_records:
        node_id = record.get("evidence_uid") or _record_uid(record)
        if not node_id or node_id in seen_nodes:
            continue
        seen_nodes.add(node_id)
        nodes.append(
            {
                "id": node_id,
                "label": _record_label(record),
                "title": _record_label(record),
                "kind": record.get("kind", ""),
                "kind_label": _evidence_kind_label(record.get("kind", "")),
                "reliability_level": record.get("reliability_level", ""),
                "reliability_label": _reliability_label(record.get("reliability_level", "")),
                "dataset_name": record.get("dataset_name", ""),
                "material_system": record.get("material_system", ""),
                "reaction_context": record.get("reaction_type") or record.get("reaction_context", ""),
                "adsorbate": record.get("adsorbate", ""),
            }
        )

    for left_index, left in enumerate(normalized_records):
        for right in normalized_records[left_index + 1:]:
            left_id = left.get("evidence_uid") or _record_uid(left)
            right_id = right.get("evidence_uid") or _record_uid(right)
            if not left_id or not right_id or left_id == right_id:
                continue
            for relation_type in _relationship_types(left, right):
                edge_key = tuple(sorted([left_id, right_id]) + [relation_type])
                if edge_key in seen_edges:
                    continue
                seen_edges.add(edge_key)
                detail = _relationship_detail(relation_type, left, right)
                edges.append(
                    {
                        "source": left_id,
                        "target": right_id,
                        "relation_type": relation_type,
                        "label": detail["label"],
                        "reason": detail["reason"],
                    }
                )

    return {"nodes": nodes, "edges": edges}


def analyze_evidence_relationships(
    records: List[Dict[str, Any]],
    graph: Dict[str, Any],
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Summarize evidence coverage and important evidence-boundary warnings."""
    del config
    total = len(records)
    computational = [r for r in records if r.get("kind") == "computational"]
    literature = [r for r in records if r.get("kind") == "literature"]
    domain = [r for r in records if r.get("kind") == "domain"]
    warnings: List[str] = []
    if computational:
        warnings.append("计算证据不等同实验性能；报告和假设中必须标注证据边界。")
    if total == 0:
        warnings.append("未检索到可分析证据。")
    elif not literature and not domain:
        warnings.append("当前证据主要来自计算或目录索引，缺少文献/实验指标交叉验证。")

    return {
        "summary": {
            "record_count": total,
            "literature_evidence_count": len(literature),
            "domain_evidence_count": len(domain),
            "computational_catalysis_evidence_count": len(computational),
            "computational_ratio": round(len(computational) / total, 3) if total else 0,
            "node_count": len(graph.get("nodes", [])) if isinstance(graph, dict) else 0,
            "edge_count": len(graph.get("edges", [])) if isinstance(graph, dict) else 0,
        },
        "warnings": warnings,
        "strong_evidence_ids": [
            record.get("evidence_uid")
            for record in records
            if record.get("reliability_level") in {"verified", "lab_record"}
        ],
        "weak_evidence_ids": [
            record.get("evidence_uid")
            for record in records
            if record.get("reliability_level") in {"catalog", "computational_index", "needs_verification"}
        ],
    }


def index_document_chunks_as_evidence(
    session: Session,
    *,
    user_id: int,
    document_id: int,
    domain: str = "",
    max_chunks: int = 24,
) -> Dict[str, int]:
    """Create lightweight evidence rows from already imported document chunks.

    This does not modify the source document or chunks; it only adds a compact,
    user-scoped index into the new evidence tables.
    """
    document = (
        session.query(Document)
        .filter(Document.user_id == user_id)
        .filter(Document.id == document_id)
        .first()
    )
    if document is None:
        return {"literature_evidence": 0, "domain_evidence": 0}

    chunks = (
        session.query(DocumentChunk)
        .filter(DocumentChunk.user_id == user_id)
        .filter(DocumentChunk.document_id == document_id)
        .order_by(DocumentChunk.order.asc())
        .limit(max_chunks)
        .all()
    )
    text = "\n\n".join(chunk.text for chunk in chunks if chunk.text)
    if not text.strip():
        return {"literature_evidence": 0, "domain_evidence": 0}

    facets = extract_query_facets(text, domain)
    normalized_domain = facets.get("domain") or _infer_domain(text, domain)
    metric_snippets = _extract_metric_snippets(text, normalized_domain)
    literature = upsert_literature_evidence(
        session,
        user_id=user_id,
        document_id=document_id,
        domain=normalized_domain,
        paper_id=f"DOC-{document_id}",
        title=document.title,
        doi=_extract_doi(text),
        material_system=facets.get("material", ""),
        reaction_type=facets.get("reaction_type", ""),
        battery_type=facets.get("battery_type", ""),
        ion_type=facets.get("ion_type", ""),
        key_data="；".join(item["text"] for item in metric_snippets[:5]),
        key_mechanism=_extract_mechanism_hint(text),
        limitation=_extract_limitation_hint(text),
        source_text=text[:2000],
        source_kind="user_imported",
        reliability_level="needs_verification",
    )

    domain_count = 0
    for item in metric_snippets[:12]:
        upsert_domain_evidence(
            session,
            user_id=user_id,
            literature_evidence_id=literature.id,
            domain=normalized_domain,
            material_system=facets.get("material", ""),
            reaction_type=facets.get("reaction_type", ""),
            battery_type=facets.get("battery_type", ""),
            ion_type=facets.get("ion_type", ""),
            metric_name=item["metric_name"],
            metric_value=item["metric_value"],
            metric_unit=item["metric_unit"],
            condition_text=item["text"],
            source_text=item["text"],
            source_kind="document_chunk",
            reliability_level="needs_verification",
        )
        domain_count += 1
    return {"literature_evidence": 1, "domain_evidence": domain_count}


def index_lab_record_as_evidence(
    session: Session,
    *,
    user_id: int,
    lab_record_id: int,
    domain: str = "",
) -> Dict[str, int]:
    """Index an existing lab record as user-owned domain evidence."""
    record = (
        session.query(LabRecord)
        .filter(LabRecord.user_id == user_id)
        .filter(LabRecord.id == lab_record_id)
        .first()
    )
    if record is None or not record.content.strip():
        return {"domain_evidence": 0}

    text = f"{record.title}\n{record.content}"
    facets = extract_query_facets(text, domain)
    normalized_domain = facets.get("domain") or _infer_domain(text, domain)
    count = 0
    for item in _extract_metric_snippets(text, normalized_domain)[:16]:
        upsert_domain_evidence(
            session,
            user_id=user_id,
            lab_record_id=lab_record_id,
            domain=normalized_domain,
            material_system=facets.get("material", ""),
            reaction_type=facets.get("reaction_type", ""),
            battery_type=facets.get("battery_type", ""),
            ion_type=facets.get("ion_type", ""),
            metric_name=item["metric_name"],
            metric_value=item["metric_value"],
            metric_unit=item["metric_unit"],
            condition_text=item["text"],
            source_text=item["text"],
            source_kind="lab_record",
            reliability_level="lab_record",
        )
        count += 1
    return {"domain_evidence": count}


def index_hypothesis_json_as_evidence(
    session: Session,
    *,
    user_id: int,
    hypothesis_data: Dict[str, Any],
    hypothesis_id: Optional[int] = None,
    domain: str = "",
) -> Dict[str, int]:
    """Index structured hypothesis output as weak, reusable evidence hints."""
    if not isinstance(hypothesis_data, dict):
        return {"literature_evidence": 0, "domain_evidence": 0}

    normalized_domain = _normalize_domain(domain or hypothesis_data.get("_domain", ""))
    if not normalized_domain:
        normalized_domain = _infer_domain(json.dumps(hypothesis_data, ensure_ascii=False), domain)

    literature_count = 0
    domain_count = 0
    rows = hypothesis_data.get("structured_extraction_table", [])
    if isinstance(rows, list):
        for idx, row in enumerate(rows[:20], 1):
            if not isinstance(row, dict):
                continue
            text = _join_nonempty(
                [
                    row.get("title"),
                    row.get("materials_or_reaction") or row.get("material_system"),
                    row.get("key_data") or row.get("performance_data"),
                    row.get("mechanism"),
                    row.get("limitation"),
                ],
                "；",
            )
            facets = extract_query_facets(text, normalized_domain)
            literature = upsert_literature_evidence(
                session,
                user_id=user_id,
                hypothesis_id=hypothesis_id,
                domain=normalized_domain,
                paper_id=row.get("paper_id") or f"HYP-{hypothesis_id or 'draft'}-P{idx}",
                title=row.get("title", ""),
                doi=row.get("doi", ""),
                material_system=row.get("material_system") or row.get("materials_or_reaction") or facets.get("material", ""),
                reaction_type=row.get("reaction_type") or facets.get("reaction_type", ""),
                battery_type=row.get("battery_type") or facets.get("battery_type", ""),
                ion_type=row.get("ion_type") or facets.get("ion_type", ""),
                key_data=row.get("key_data") or row.get("performance_data") or "",
                key_mechanism=row.get("mechanism", ""),
                limitation=row.get("limitation", ""),
                source_text=text,
                source_kind="hypothesis_output",
                reliability_level=row.get("reliability_level") or row.get("evidence_status") or "needs_verification",
            )
            literature_count += 1
            for metric in _extract_metric_snippets(text, normalized_domain)[:4]:
                upsert_domain_evidence(
                    session,
                    user_id=user_id,
                    literature_evidence_id=literature.id,
                    hypothesis_id=hypothesis_id,
                    domain=normalized_domain,
                    material_system=row.get("material_system") or row.get("materials_or_reaction") or facets.get("material", ""),
                    reaction_type=row.get("reaction_type") or facets.get("reaction_type", ""),
                    battery_type=row.get("battery_type") or facets.get("battery_type", ""),
                    ion_type=row.get("ion_type") or facets.get("ion_type", ""),
                    metric_name=metric["metric_name"],
                    metric_value=metric["metric_value"],
                    metric_unit=metric["metric_unit"],
                    condition_text=metric["text"],
                    source_text=metric["text"],
                    source_kind="hypothesis_output",
                    reliability_level="needs_verification",
                )
                domain_count += 1

    result_text = json.dumps(hypothesis_data.get("results", {}), ensure_ascii=False)
    for metric in _extract_metric_snippets(result_text, normalized_domain)[:8]:
        upsert_domain_evidence(
            session,
            user_id=user_id,
            hypothesis_id=hypothesis_id,
            domain=normalized_domain,
            metric_name=metric["metric_name"],
            metric_value=metric["metric_value"],
            metric_unit=metric["metric_unit"],
            condition_text=metric["text"],
            source_text=metric["text"],
            source_kind="hypothesis_output",
            reliability_level="needs_verification",
        )
        domain_count += 1

    return {"literature_evidence": literature_count, "domain_evidence": domain_count}


def query_evidence(
    session: Session,
    *,
    user_id: int,
    domain: str = "",
    query_text: str = "",
    reaction_type: str = "",
    battery_type: str = "",
    ion_type: str = "",
    material: str = "",
    metric_names: Optional[Iterable[str]] = None,
    adsorbates: Optional[Iterable[str]] = None,
    max_each: int = 6,
) -> EvidenceSearchBundle:
    """Return a compact evidence bundle for hypothesis generation."""
    if _normalize_domain(domain) == "electrocatalysis":
        seed_computational_source_catalog(session, user_id=user_id)

    requested_adsorbates = _requested_adsorbates(query_text, reaction_type, adsorbates)
    tokens = _tokens(" ".join([query_text, reaction_type, battery_type, ion_type, material, " ".join(requested_adsorbates)]))
    bundle = EvidenceSearchBundle()

    lit_query = session.query(LiteratureEvidence).filter(LiteratureEvidence.user_id == user_id)
    if domain:
        lit_query = lit_query.filter(LiteratureEvidence.domain == _normalize_domain(domain))
    if reaction_type:
        lit_query = lit_query.filter(LiteratureEvidence.reaction_type.ilike(f"%{reaction_type}%"))
    if battery_type:
        lit_query = lit_query.filter(LiteratureEvidence.battery_type.ilike(f"%{battery_type}%"))
    if ion_type:
        lit_query = lit_query.filter(LiteratureEvidence.ion_type.ilike(f"%{ion_type}%"))
    if material:
        lit_query = lit_query.filter(LiteratureEvidence.material_system.ilike(f"%{material}%"))
    lit_rows = lit_query.order_by(LiteratureEvidence.updated_at.desc()).limit(40).all()
    bundle.literature_evidence = _ranked(
        [_literature_to_dict(row) for row in lit_rows],
        tokens,
        ["title", "material_system", "reaction_type", "key_data", "key_mechanism", "source_text"],
        max_each,
    )

    domain_query = session.query(DomainEvidence).filter(DomainEvidence.user_id == user_id)
    if domain:
        domain_query = domain_query.filter(DomainEvidence.domain == _normalize_domain(domain))
    if reaction_type:
        domain_query = domain_query.filter(DomainEvidence.reaction_type.ilike(f"%{reaction_type}%"))
    if battery_type:
        domain_query = domain_query.filter(DomainEvidence.battery_type.ilike(f"%{battery_type}%"))
    if ion_type:
        domain_query = domain_query.filter(DomainEvidence.ion_type.ilike(f"%{ion_type}%"))
    if material:
        domain_query = domain_query.filter(DomainEvidence.material_system.ilike(f"%{material}%"))
    if metric_names:
        metric_filters = [DomainEvidence.metric_name.ilike(f"%{m}%") for m in metric_names if m]
        if metric_filters:
            domain_query = domain_query.filter(or_(*metric_filters))
    domain_rows = domain_query.order_by(DomainEvidence.updated_at.desc()).limit(40).all()
    bundle.domain_evidence = _ranked(
        [_domain_to_dict(row) for row in domain_rows],
        tokens,
        ["material_system", "reaction_type", "metric_name", "metric_value", "key_mechanism", "source_text"],
        max_each,
    )

    if _normalize_domain(domain) == "electrocatalysis" or reaction_type or requested_adsorbates:
        comp_base_query = session.query(ComputationalCatalysisEvidence).filter(
            ComputationalCatalysisEvidence.user_id == user_id
        )
        comp_query = comp_base_query
        if reaction_type:
            comp_query = comp_query.filter(
                ComputationalCatalysisEvidence.reaction_context.ilike(f"%{reaction_type}%")
            )
        if material:
            comp_query = comp_query.filter(
                ComputationalCatalysisEvidence.material_system.ilike(f"%{material}%")
            )
        if requested_adsorbates:
            comp_query = comp_query.filter(_adsorbate_filter(ComputationalCatalysisEvidence, requested_adsorbates))
        comp_rows = comp_query.order_by(ComputationalCatalysisEvidence.updated_at.desc()).limit(80).all()
        if not comp_rows and (reaction_type or material or tokens):
            fallback_terms = [term for term in [reaction_type, material] + tokens if term]
            term_filters = []
            for term in fallback_terms[:12]:
                like = f"%{term}%"
                term_filters.extend(
                    [
                        ComputationalCatalysisEvidence.dataset_name.ilike(like),
                        ComputationalCatalysisEvidence.task_type.ilike(like),
                        ComputationalCatalysisEvidence.reaction_context.ilike(like),
                        ComputationalCatalysisEvidence.material_system.ilike(like),
                        ComputationalCatalysisEvidence.adsorbate.ilike(like),
                        ComputationalCatalysisEvidence.adsorption_energy.ilike(like),
                        ComputationalCatalysisEvidence.dft_energy.ilike(like),
                        ComputationalCatalysisEvidence.notes.ilike(like),
                    ]
                )
            if term_filters:
                comp_rows = (
                    comp_base_query
                    .filter(ComputationalCatalysisEvidence.reliability_level == "computational_index")
                    .filter(or_(*term_filters))
                    .order_by(ComputationalCatalysisEvidence.reliability_level.desc(), ComputationalCatalysisEvidence.updated_at.desc())
                    .limit(120)
                    .all()
                )
        if not comp_rows and _normalize_domain(domain) == "electrocatalysis":
            fallback_query = comp_base_query.filter(ComputationalCatalysisEvidence.reliability_level == "computational_index")
            if requested_adsorbates:
                fallback_query = fallback_query.filter(_adsorbate_filter(ComputationalCatalysisEvidence, requested_adsorbates))
            comp_rows = fallback_query.order_by(ComputationalCatalysisEvidence.updated_at.desc()).limit(40).all()
        if (
            not comp_rows
            and _normalize_domain(domain) == "electrocatalysis"
            and not bundle.literature_evidence
            and not bundle.domain_evidence
        ):
            catalog_query = comp_base_query.filter(
                ComputationalCatalysisEvidence.reliability_level == "catalog",
                ComputationalCatalysisEvidence.task_type == "source_catalog",
            )
            if reaction_type:
                catalog_query = catalog_query.filter(
                    or_(
                        ComputationalCatalysisEvidence.reaction_context.ilike(f"%{reaction_type}%"),
                        ComputationalCatalysisEvidence.material_system.ilike(f"%{reaction_type}%"),
                        ComputationalCatalysisEvidence.dataset_name.ilike(f"%{reaction_type}%"),
                        ComputationalCatalysisEvidence.notes.ilike(f"%{reaction_type}%"),
                    )
                )
            comp_rows = catalog_query.order_by(ComputationalCatalysisEvidence.updated_at.desc()).limit(12).all()
        bundle.computational_catalysis_evidence = _ranked(
            [_computational_to_dict(row) for row in comp_rows],
            tokens,
            [
                "dataset_name",
                "task_type",
                "reaction_context",
                "material_system",
                "adsorbate",
                "dft_energy",
                "adsorption_energy",
                "reference_value",
                "source_url",
                "notes",
            ],
            max_each,
        )
        bundle.adsorbate_energy_sets = _build_adsorbate_energy_sets(
            bundle.computational_catalysis_evidence,
            requested_adsorbates,
        )
        if requested_adsorbates:
            seen_adsorbates = {
                normalize_adsorbate(item.get("adsorbate"))
                for item in bundle.computational_catalysis_evidence
                if item.get("adsorbate")
            }
            for adsorbate in requested_adsorbates:
                if adsorbate not in seen_adsorbates:
                    bundle.warnings.append(f"数据库暂无 {adsorbate} 吸附物计算条目；不得补编该能量数值。")

    if not bundle.has_evidence:
        bundle.warnings.append("未检索到自建数据库证据；请补充文献、实验记录或计算证据索引。")
    return bundle


def build_database_evidence_context(
    session: Session,
    *,
    user_id: int,
    domain: str = "",
    research_question: str = "",
    reaction_type: str = "",
    battery_type: str = "",
    ion_type: str = "",
    material: str = "",
    adsorbates: Optional[Iterable[str]] = None,
    max_each: int = 6,
) -> str:
    bundle = query_evidence(
        session,
        user_id=user_id,
        domain=domain,
        query_text=research_question,
        reaction_type=reaction_type,
        battery_type=battery_type,
        ion_type=ion_type,
        material=material,
        adsorbates=adsorbates,
        max_each=max_each,
    )
    return format_database_evidence_context(bundle, domain=domain)


def format_database_evidence_context(bundle: EvidenceSearchBundle, *, domain: str = "") -> str:
    catalog_context = _format_static_source_catalog(domain)
    if not bundle.has_evidence:
        return (
            "【自建数据库证据上下文】\n"
            "未检索到可用证据。请输出“证据不足/需核验”，不得编造论文、DOI、数据集或实验结果。"
            f"{catalog_context}"
        )

    lines = [
        "【自建数据库证据上下文】",
        f"研究方向: {_domain_label(domain)}",
        "请把以下内容作为候选证据，并在 Source / References / Database Evidence 中明确溯源。",
        "计算证据只能支持结构-能量-吸附描述符或模型预测，不得直接等同于实验性能。",
    ]
    if bundle.literature_evidence:
        lines.append("\n[已引用文献证据 literature_evidence]")
        for item in bundle.literature_evidence:
            lines.append(
                "- "
                + _join_nonempty(
                    [
                        f"证据ID=LE-{item['id']}",
                        item.get("title"),
                        item.get("doi") and f"DOI={item.get('doi')}",
                        item.get("material_system"),
                        item.get("reaction_type") or item.get("battery_type"),
                        item.get("key_data"),
                        item.get("reliability_level") and f"可靠性={item.get('reliability_level')}",
                    ]
                )
            )
    if bundle.domain_evidence:
        lines.append("\n[自建数据库匹配证据 domain_evidence]")
        for item in bundle.domain_evidence:
            metric = _join_nonempty([item.get("metric_name"), item.get("metric_value"), item.get("metric_unit")], " ")
            lines.append(
                "- "
                + _join_nonempty(
                    [
                        f"证据ID=DE-{item['id']}",
                        item.get("material_system"),
                        item.get("reaction_type") or item.get("battery_type"),
                        metric,
                        item.get("condition_text"),
                        item.get("key_mechanism"),
                        item.get("reliability_level") and f"可靠性={item.get('reliability_level')}",
                    ]
                )
            )
    if bundle.computational_catalysis_evidence:
        lines.append("\n[计算催化证据 computational_catalysis_evidence]")
        for item in bundle.computational_catalysis_evidence:
            lines.append(
                "- "
                + _join_nonempty(
                    [
                        f"证据ID=CE-{item['id']}",
                        f"数据集={item.get('dataset_name')}",
                        item.get("task_type") and f"任务={item.get('task_type')}",
                        item.get("reaction_context") and f"反应={item.get('reaction_context')}",
                        item.get("material_system"),
                        item.get("adsorbate") and f"吸附物={item.get('adsorbate')}",
                        item.get("dft_energy") and f"DFT/参考能={item.get('dft_energy')}",
                        item.get("adsorption_energy") and f"吸附能={item.get('adsorption_energy')}",
                        item.get("source_url") and f"来源={item.get('source_url')}",
                        item.get("notes"),
                        "类型=计算证据，不得直接等同于实验性能",
                    ]
                )
            )
    if bundle.adsorbate_energy_sets:
        lines.append("\n[Same-site adsorbate energy sets]")
        for item in bundle.adsorbate_energy_sets[:8]:
            coverage = item.get("coverage", {}) if isinstance(item.get("coverage"), dict) else {}
            energies = item.get("energies", {}) if isinstance(item.get("energies"), dict) else {}
            energy_parts = []
            for adsorbate, row in energies.items():
                if not isinstance(row, dict):
                    continue
                value = row.get("adsorption_energy") or row.get("dft_energy") or row.get("reference_value") or ""
                energy_parts.append(
                    _join_nonempty(
                        [adsorbate, value, row.get("id") and f"CE-{row.get('id')}"],
                        "=",
                    )
                )
            missing = coverage.get("missing_adsorbates", []) if isinstance(coverage, dict) else []
            lines.append(
                "- "
                + _join_nonempty(
                    [
                        item.get("material_system"),
                        item.get("surface_facet") and f"facet={item.get('surface_facet')}",
                        item.get("active_site") and f"site={item.get('active_site')}",
                        "complete" if coverage.get("complete") else "missing=" + ",".join(missing),
                        "; ".join(energy_parts),
                        item.get("evidence_ids") and "evidence_ids=" + ",".join(item.get("evidence_ids", [])),
                    ]
                )
            )
    for warning in bundle.warnings:
        lines.append(f"\n[证据不足/需核验] {warning}")
    return "\n".join(lines) + catalog_context


def evidence_search_bundle_to_dict(bundle: EvidenceSearchBundle) -> Dict[str, Any]:
    return {
        "summary": {
            "literature_evidence_count": len(bundle.literature_evidence),
            "domain_evidence_count": len(bundle.domain_evidence),
            "computational_catalysis_evidence_count": len(bundle.computational_catalysis_evidence),
            "adsorbate_energy_set_count": len(bundle.adsorbate_energy_sets),
            "has_evidence": bundle.has_evidence,
        },
        "literature_evidence": bundle.literature_evidence,
        "domain_evidence": bundle.domain_evidence,
        "computational_catalysis_evidence": bundle.computational_catalysis_evidence,
        "adsorbate_energy_sets": bundle.adsorbate_energy_sets,
        "source_catalog": {
            "computational": OCP_DATASET_CATALOG,
            "external": EXTERNAL_DATA_SOURCE_WHITELIST,
        },
        "warnings": bundle.warnings,
    }


def extract_query_facets(text: str, domain: str = "") -> Dict[str, str]:
    """Best-effort facet extraction for database lookup."""
    lower = str(text or "").lower()
    reaction = _first_token_match(lower, ["HER", "HOR", "OER", "ORR", "CO2RR", "NRR"])
    ion = _first_ion_match(lower)
    battery_type = ""
    for candidate in ["lithium-ion", "sodium-ion", "potassium-ion", "zinc-ion", "solid-state"]:
        if candidate in lower:
            battery_type = candidate
            break
    material = _extract_material_hint(text)
    return {
        "domain": _normalize_domain(domain),
        "reaction_type": reaction,
        "ion_type": ion,
        "battery_type": battery_type,
        "material": material,
        "adsorbates": _requested_adsorbates(text, reaction, None),
    }


_MANIFEST_FIELD_ALIASES: Dict[str, List[str]] = {
    "domain": ["domain", "field", "领域"],
    "material_system": ["material_system", "material", "formula", "catalyst", "composition", "surface", "slab", "材料体系", "材料"],
    "reaction_context": ["reaction_context", "reaction", "reaction_type", "task_reaction", "scenario", "反应", "反应类型"],
    "adsorbate": ["adsorbate", "adsorbates", "intermediate", "adsorbate_formula", "吸附物"],
    "surface_facet": ["surface_facet", "facet", "surface_index", "miller_index", "晶面"],
    "electrolyte_or_solvent": ["electrolyte_or_solvent", "electrolyte", "solvent", "电解液", "溶剂"],
    "dft_energy": ["dft_energy", "energy", "total_energy", "dft_total_energy", "DFT能量"],
    "adsorption_energy": ["adsorption_energy", "e_ads", "ads_energy", "binding_energy", "吸附能"],
    "task_type": ["task_type", "task", "ml_task", "oc_task", "任务"],
    "model_name": ["model_name", "model", "checkpoint", "模型"],
    "predicted_value": ["predicted_value", "prediction", "predicted_energy", "y_pred", "预测值"],
    "reference_value": ["reference_value", "target", "label", "y_true", "参考值"],
    "source_url": ["source_url", "url", "uri", "download_url", "来源"],
    "source_id": ["source_id", "id", "sid", "entry_id", "system_id", "record_id"],
    "notes": ["notes", "note", "comment", "description", "说明"],
}


def _read_manifest_rows(path: Path, *, max_rows: int) -> List[Dict[str, Any]]:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            return [dict(row) for _, row in zip(range(max_rows), reader)]
    if suffix in {".jsonl", ".ndjson"}:
        rows: List[Dict[str, Any]] = []
        with path.open("r", encoding="utf-8-sig") as handle:
            for line in handle:
                if len(rows) >= max_rows:
                    break
                line = line.strip()
                if not line:
                    continue
                value = json.loads(line)
                if isinstance(value, dict):
                    rows.append(value)
        return rows
    with path.open("r", encoding="utf-8-sig") as handle:
        value = json.load(handle)
    if isinstance(value, dict):
        for key in ["records", "rows", "data", "items"]:
            if isinstance(value.get(key), list):
                return [item for item in value[key][:max_rows] if isinstance(item, dict)]
        return [value]
    if isinstance(value, list):
        return [item for item in value[:max_rows] if isinstance(item, dict)]
    return []


def _ocp_manifest_files(root: Path) -> List[Path]:
    supported = {".csv", ".json", ".jsonl", ".ndjson"}
    if root.is_file():
        return [root] if root.suffix.lower() in supported else []
    files = [
        path
        for path in root.rglob("*")
        if path.is_file() and path.suffix.lower() in supported
    ]
    files.sort(key=lambda path: str(path).lower())
    return files


def _load_checkpoint_keys(path: Optional[Path]) -> set[str]:
    if not path or not path.exists():
        return set()
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        keys = data.get("processed_keys", data) if isinstance(data, dict) else data
        if isinstance(keys, list):
            return {str(key) for key in keys if key}
    except Exception:
        return set()
    return set()


def _write_checkpoint_keys(path: Path, keys: set[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(
            {
                "processed_keys": sorted(keys),
                "updated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
            },
            handle,
            ensure_ascii=False,
            indent=2,
        )


def _read_oc20dense_mapping_archive(archive_path: Path) -> tuple[Dict[str, Any], Dict[str, Any]]:
    mapping: Dict[str, Any] = {}
    ref_energies: Dict[str, Any] = {}
    with tarfile.open(archive_path, "r:gz") as archive:
        for member in archive.getmembers():
            if not member.isfile():
                continue
            name = Path(member.name).name.lower()
            if name not in {"oc20dense_mapping.pkl", "oc20dense_ref_energies.pkl"}:
                continue
            extracted = archive.extractfile(member)
            if extracted is None:
                continue
            value = pickle.load(extracted)
            if "mapping" in name and isinstance(value, dict):
                mapping = value
            elif "ref_energies" in name and isinstance(value, dict):
                ref_energies = value
    return mapping, ref_energies


def _best_ocp_target(target_values: Any) -> tuple[str, Optional[float]]:
    if isinstance(target_values, dict):
        iterable = []
        for key, value in target_values.items():
            try:
                iterable.append((str(key), float(value)))
            except Exception:
                continue
    else:
        iterable = []
        for item in list(target_values or []):
            if isinstance(item, dict):
                config = item.get("config_id") or item.get("id") or item.get("name")
                value = item.get("energy") or item.get("target") or item.get("value")
            elif isinstance(item, (list, tuple)) and len(item) >= 2:
                config, value = item[0], item[1]
            else:
                continue
            try:
                iterable.append((str(config), float(value)))
            except Exception:
                continue
    if not iterable:
        return "", None
    config_id, energy = min(iterable, key=lambda pair: pair[1])
    return config_id, energy


def _infer_adsorbate_from_system(system_id: str) -> str:
    text = str(system_id or "").strip()
    if not text:
        return ""
    return ""


def _infer_reaction_context_from_adsorbate(adsorbate: str) -> str:
    ads = _clean(adsorbate).upper().replace(" ", "")
    if not ads:
        return "adsorption energetics"
    if ads in {"*O", "O*", "*OH", "OH*", "*OOH", "OOH*"}:
        return "OER / ORR adsorption energetics"
    if ads in {"*H", "H*"}:
        return "HER adsorption energetics"
    if ads in {"*CO", "CO*", "*COOH", "COOH*", "*OCHO", "OCHO*"}:
        return "CO2RR adsorption energetics"
    if "NO" in ads or "NH" in ads:
        return "NRR adsorption energetics"
    return "adsorption energetics"


def _format_ev(value: Any) -> str:
    try:
        return f"{float(value):.6f} eV"
    except Exception:
        text = _clean(value)
        return text


def _format_miller_index(value: Any) -> str:
    if value in (None, ""):
        return ""
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return ""
        stripped = text.strip("()[]")
        parts = [part for part in re.split(r"[\s,;]+", stripped) if part]
        looks_like_miller = bool(parts) and all(re.fullmatch(r"-?\d+", part) for part in parts)
        if looks_like_miller:
            return text if text.startswith("(") else f"({','.join(parts)})"
        return text
    if hasattr(value, "tolist") and callable(getattr(value, "tolist")):
        try:
            value = value.tolist()
        except Exception:
            pass
    if isinstance(value, (list, tuple)):
        parts = []
        for item in value:
            try:
                parts.append(str(int(item)))
            except Exception:
                parts.append(_clean(item))
        return "(" + ",".join(parts) + ")"
    return _clean(value)


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _jsonable(val) for key, val in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if hasattr(value, "tolist") and callable(getattr(value, "tolist")):
        try:
            return _jsonable(value.tolist())
        except Exception:
            pass
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return _clean(value, max_len=1000)


def _map_computational_manifest_row(raw_row: Dict[str, Any]) -> Dict[str, str]:
    normalized = {_normalize_manifest_key(key): value for key, value in raw_row.items()}
    mapped: Dict[str, str] = {}
    for target, aliases in _MANIFEST_FIELD_ALIASES.items():
        for alias in aliases:
            key = _normalize_manifest_key(alias)
            value = normalized.get(key)
            if value not in (None, ""):
                mapped[target] = _clean(value, max_len=2000)
                break
    if mapped.get("reaction_context"):
        mapped["reaction_context"] = _normalize_reaction_label(mapped["reaction_context"])
    return mapped


def _manifest_row_value(raw_row: Dict[str, Any], aliases: Iterable[str]) -> str:
    normalized = {_normalize_manifest_key(key): value for key, value in raw_row.items()}
    for alias in aliases:
        value = normalized.get(_normalize_manifest_key(alias))
        if value not in (None, ""):
            return _clean(value, max_len=2000)
    return ""


def _ocp_evidence_source_url(*, dataset: str, system_id: str, config_id: str, adsorbate: str) -> str:
    parts = [
        _clean(dataset).replace(" ", "-") or "ocp",
        _clean(system_id).replace(" ", "-") or "system",
        _clean(config_id).replace(" ", "-") or "config",
        normalize_adsorbate(adsorbate).replace("*", "star") or "adsorbate",
    ]
    safe = [re.sub(r"[^A-Za-z0-9_.:@+-]+", "-", part).strip("-") or "item" for part in parts]
    return "ocp://" + "/".join(safe)


def _normalize_manifest_key(key: Any) -> str:
    return re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "_", str(key or "").strip().lower()).strip("_")


def _normalize_reaction_label(value: str) -> str:
    upper = str(value or "").strip().upper()
    for reaction in ["OER", "ORR", "HER", "HOR", "CO2RR", "NRR"]:
        if reaction in upper:
            return reaction
    return _clean(value)


def _infer_dataset_name(path: Path) -> str:
    name = path.stem.upper()
    for dataset in ["OC20", "OC22", "OC25", "ODAC23"]:
        if dataset in name:
            return dataset
    return path.stem


def _compact_raw_row(row: Dict[str, Any]) -> Dict[str, str]:
    compact: Dict[str, str] = {}
    for key, value in list(row.items())[:40]:
        compact[str(key)[:120]] = _clean(value, max_len=500)
    return compact


def _apply_common_evidence_filters(query: Any, model: Any, filters: Dict[str, Any], *, table_kind: str) -> Any:
    domain = _clean(filters.get("domain"))
    if domain and hasattr(model, "domain"):
        query = query.filter(model.domain == _normalize_domain(domain))
    reliability = _clean(filters.get("reliability_level"))
    if reliability and hasattr(model, "reliability_level"):
        query = query.filter(model.reliability_level == reliability)
    material = _clean(filters.get("material") or filters.get("material_system"))
    if material and hasattr(model, "material_system"):
        query = query.filter(model.material_system.ilike(f"%{material}%"))
    reaction = _clean(filters.get("reaction_type") or filters.get("reaction_context"))
    if reaction:
        if table_kind == "computational" and hasattr(model, "reaction_context"):
            query = query.filter(model.reaction_context.ilike(f"%{reaction}%"))
        elif hasattr(model, "reaction_type"):
            query = query.filter(model.reaction_type.ilike(f"%{reaction}%"))
    dataset = _clean(filters.get("dataset_name"))
    if dataset and hasattr(model, "dataset_name"):
        query = query.filter(model.dataset_name.ilike(f"%{dataset}%"))
    return query.order_by(model.updated_at.desc() if hasattr(model, "updated_at") else model.id.desc())


def _workbench_literature_record(row: LiteratureEvidence) -> Dict[str, Any]:
    data = _literature_to_dict(row)
    data.update(
        {
            "kind": "literature",
            "table": "literature_evidence",
            "evidence_uid": f"LE-{row.id}",
            "display_title": row.title or row.paper_id or f"Literature evidence {row.id}",
            "primary_text": row.key_data or row.key_mechanism or row.source_text[:160],
            "extra_json": _safe_json_loads(row.extra_json),
            "created_at": row.created_at.isoformat(sep=" ", timespec="seconds") if row.created_at else "",
            "updated_at": row.updated_at.isoformat(sep=" ", timespec="seconds") if row.updated_at else "",
        }
    )
    return data


def _workbench_domain_record(row: DomainEvidence) -> Dict[str, Any]:
    data = _domain_to_dict(row)
    metric = _join_nonempty([row.metric_name, row.metric_value, row.metric_unit], " ")
    data.update(
        {
            "kind": "domain",
            "table": "domain_evidence",
            "evidence_uid": f"DE-{row.id}",
            "display_title": metric or f"Domain evidence {row.id}",
            "primary_text": row.condition_text or row.key_mechanism or row.source_text[:160],
            "extra_json": _safe_json_loads(row.extra_json),
            "created_at": row.created_at.isoformat(sep=" ", timespec="seconds") if row.created_at else "",
            "updated_at": row.updated_at.isoformat(sep=" ", timespec="seconds") if row.updated_at else "",
        }
    )
    return data


def _workbench_computational_record(row: ComputationalCatalysisEvidence) -> Dict[str, Any]:
    data = _computational_to_dict(row)
    data.update(
        {
            "kind": "computational",
            "table": "computational_catalysis_evidence",
            "evidence_uid": f"CE-{row.id}",
            "display_title": row.dataset_name or f"Computational evidence {row.id}",
            "primary_text": _join_nonempty(
                [row.task_type, row.reaction_context, row.adsorbate, row.adsorption_energy or row.dft_energy, row.notes]
            ),
            "extra_json": _safe_json_loads(row.extra_json),
            "created_at": row.created_at.isoformat(sep=" ", timespec="seconds") if row.created_at else "",
            "updated_at": row.updated_at.isoformat(sep=" ", timespec="seconds") if row.updated_at else "",
        }
    )
    return data


def _build_adsorbate_energy_sets(
    rows: List[Dict[str, Any]],
    requested_adsorbates: Optional[Iterable[str]] = None,
) -> List[Dict[str, Any]]:
    requested = []
    for adsorbate in requested_adsorbates or []:
        normalized = normalize_adsorbate(adsorbate)
        if normalized and normalized not in requested:
            requested.append(normalized)
    if not requested:
        requested = []
        for row in rows:
            normalized = normalize_adsorbate(row.get("adsorbate")) if isinstance(row, dict) else ""
            if normalized and normalized not in requested:
                requested.append(normalized)
            if len(requested) >= 20:
                break
    if not rows or not requested:
        return []

    groups: Dict[tuple[str, str, str], Dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        adsorbate = normalize_adsorbate(row.get("adsorbate"))
        if not adsorbate:
            continue
        extra = row.get("extra_json", {})
        if not isinstance(extra, dict):
            extra = _safe_json_loads(extra)
        active_site = _clean(
            extra.get("active_site")
            or extra.get("adsorption_site")
            or extra.get("site")
            or extra.get("site_label")
        )
        key = (
            _clean(row.get("material_system")),
            _clean(row.get("surface_facet")),
            active_site,
        )
        group = groups.setdefault(
            key,
            {
                "material_system": key[0],
                "surface_facet": key[1],
                "active_site": key[2],
                "dataset_names": [],
                "energies": {},
                "evidence_ids": [],
            },
        )
        dataset = _clean(row.get("dataset_name"))
        if dataset and dataset not in group["dataset_names"]:
            group["dataset_names"].append(dataset)
        evidence_id = row.get("id") and f"CE-{row.get('id')}"
        if evidence_id and evidence_id not in group["evidence_ids"]:
            group["evidence_ids"].append(evidence_id)
        current = group["energies"].get(adsorbate)
        row_value = row.get("adsorption_energy") or row.get("dft_energy") or row.get("reference_value") or ""
        if current and current.get("adsorption_energy"):
            continue
        group["energies"][adsorbate] = {
            "id": row.get("id"),
            "dataset_name": row.get("dataset_name", ""),
            "task_type": row.get("task_type", ""),
            "reaction_context": row.get("reaction_context", ""),
            "adsorbate": adsorbate,
            "adsorption_energy": row.get("adsorption_energy", ""),
            "dft_energy": row.get("dft_energy", ""),
            "reference_value": row.get("reference_value", ""),
            "energy_value": row_value,
            "source_url": row.get("source_url", ""),
            "reliability_level": row.get("reliability_level", ""),
        }

    results = []
    for group in groups.values():
        present = set(group["energies"].keys())
        missing = [adsorbate for adsorbate in requested if adsorbate not in present]
        group["requested_adsorbates"] = list(requested)
        group["coverage"] = {
            "present_adsorbates": [adsorbate for adsorbate in requested if adsorbate in present],
            "missing_adsorbates": missing,
            "complete": not missing,
            "coverage_count": len(present.intersection(set(requested))),
            "requested_count": len(requested),
        }
        results.append(group)
    results.sort(
        key=lambda item: (
            not item.get("coverage", {}).get("complete"),
            -int(item.get("coverage", {}).get("coverage_count", 0)),
            item.get("material_system", ""),
            item.get("surface_facet", ""),
            item.get("active_site", ""),
        )
    )
    return results[:12]


def _safe_json_loads(value: Any) -> Any:
    if not value:
        return {}
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value))
    except Exception:
        return {"raw": str(value)[:1000]}


def _record_matches_tokens(record: Dict[str, Any], tokens: List[str]) -> bool:
    if not tokens:
        return True
    haystack = " ".join(
        str(record.get(key, ""))
        for key in [
            "evidence_uid",
            "display_title",
            "title",
            "doi",
            "dataset_name",
            "task_type",
            "reaction_context",
            "reaction_type",
            "material_system",
            "adsorbate",
            "metric_name",
            "metric_value",
            "primary_text",
            "notes",
        ]
    ).lower()
    return all(token.lower() in haystack for token in tokens)


def _record_uid(record: Dict[str, Any]) -> str:
    prefix = {"literature": "LE", "domain": "DE", "computational": "CE"}.get(record.get("kind"), "EV")
    return record.get("evidence_uid") or f"{prefix}-{record.get('id')}"


def _record_label(record: Dict[str, Any]) -> str:
    return (
        record.get("display_title")
        or record.get("title")
        or record.get("dataset_name")
        or _join_nonempty([record.get("metric_name"), record.get("metric_value")], " ")
        or _record_uid(record)
    )


def _evidence_kind_label(kind: str) -> str:
    return {"literature": "文献证据", "domain": "领域指标", "computational": "计算证据"}.get(_clean(kind), _clean(kind) or "证据")


def _reliability_label(level: str) -> str:
    return {
        "catalog": "数据源目录项",
        "computational_index": "计算索引条目",
        "verified": "已验证",
        "lab_record": "实验记录",
        "user_imported": "用户导入",
        "needs_verification": "待验证",
        "需核验": "待验证",
    }.get(_clean(level), _clean(level) or "未标注")


def _relationship_types(left: Dict[str, Any], right: Dict[str, Any]) -> List[str]:
    relations: List[str] = []
    left_material = _clean(left.get("material_system")).lower()
    right_material = _clean(right.get("material_system")).lower()
    if left_material and right_material and (left_material in right_material or right_material in left_material):
        relations.append("same_material")

    left_reaction = _clean(left.get("reaction_type") or left.get("reaction_context")).upper()
    right_reaction = _clean(right.get("reaction_type") or right.get("reaction_context")).upper()
    if left_reaction and right_reaction and (left_reaction in right_reaction or right_reaction in left_reaction):
        relations.append("same_reaction")

    left_adsorbate = _clean(left.get("adsorbate")).lower()
    right_adsorbate = _clean(right.get("adsorbate")).lower()
    if left_adsorbate and right_adsorbate and left_adsorbate == right_adsorbate:
        relations.append("same_adsorbate")

    if (
        left.get("kind") == "domain"
        and left.get("literature_evidence_id")
        and right.get("kind") == "literature"
        and int(left["literature_evidence_id"]) == int(right.get("id") or 0)
    ) or (
        right.get("kind") == "domain"
        and right.get("literature_evidence_id")
        and left.get("kind") == "literature"
        and int(right["literature_evidence_id"]) == int(left.get("id") or 0)
    ):
        relations.append("derived_from_literature")
    elif {left.get("kind"), right.get("kind")} == {"literature", "domain"}:
        left_doi = _clean(left.get("doi")).lower()
        right_doi = _clean(right.get("doi")).lower()
        left_title = _clean(left.get("title") or left.get("display_title")).lower()
        right_title = _clean(right.get("title") or right.get("display_title")).lower()
        if (left_doi and right_doi and left_doi == right_doi) or (
            left_title and right_title and (left_title in right_title or right_title in left_title)
        ):
            relations.append("derived_from_literature")

    if left.get("dataset_name") and left.get("dataset_name") == right.get("dataset_name"):
        relations.append("same_dataset")
    return relations


def _relationship_detail(relation_type: str, left: Dict[str, Any], right: Dict[str, Any]) -> Dict[str, str]:
    labels = {
        "same_material": "同材料",
        "same_reaction": "同反应",
        "derived_from_literature": "文献派生",
        "same_dataset": "同数据集",
        "same_adsorbate": "同吸附物",
    }
    if relation_type == "same_material":
        value = _clean(left.get("material_system")) or _clean(right.get("material_system"))
        reason = f"材料体系相同或互相包含：{value}"
    elif relation_type == "same_reaction":
        value = _clean(left.get("reaction_type") or left.get("reaction_context")) or _clean(
            right.get("reaction_type") or right.get("reaction_context")
        )
        reason = f"反应/任务场景相同或互相包含：{value}"
    elif relation_type == "derived_from_literature":
        reason = "领域指标与文献证据存在外键、DOI 或标题对应关系"
    elif relation_type == "same_dataset":
        value = _clean(left.get("dataset_name")) or _clean(right.get("dataset_name"))
        reason = f"来自同一计算数据集：{value}"
    elif relation_type == "same_adsorbate":
        value = _clean(left.get("adsorbate")) or _clean(right.get("adsorbate"))
        reason = f"吸附物相同：{value}"
    else:
        reason = relation_type
    return {"label": labels.get(relation_type, relation_type), "reason": reason}


def _assign_fields(row: Any, kwargs: Dict[str, Any], fields: List[str]) -> None:
    for field_name in fields:
        if field_name not in kwargs:
            continue
        value = kwargs[field_name]
        if field_name == "extra_json" and isinstance(value, (dict, list)):
            value = json.dumps(value, ensure_ascii=False)
        elif value is None:
            value = None
        elif field_name.endswith("_id"):
            pass
        else:
            value = _clean(value, max_len=20000)
        setattr(row, field_name, value)


def _literature_to_dict(row: LiteratureEvidence) -> Dict[str, Any]:
    return {
        "id": row.id,
        "document_id": row.document_id,
        "hypothesis_id": row.hypothesis_id,
        "domain": row.domain,
        "paper_id": row.paper_id,
        "title": row.title,
        "doi": row.doi,
        "year": row.year,
        "journal": row.journal,
        "material_system": row.material_system,
        "reaction_type": row.reaction_type,
        "battery_type": row.battery_type,
        "ion_type": row.ion_type,
        "key_data": row.key_data,
        "key_mechanism": row.key_mechanism,
        "limitation": row.limitation,
        "source_text": row.source_text[:500],
        "source_kind": row.source_kind,
        "reliability_level": row.reliability_level,
    }


def _domain_to_dict(row: DomainEvidence) -> Dict[str, Any]:
    return {
        "id": row.id,
        "literature_evidence_id": row.literature_evidence_id,
        "lab_record_id": row.lab_record_id,
        "hypothesis_id": row.hypothesis_id,
        "domain": row.domain,
        "material_system": row.material_system,
        "reaction_type": row.reaction_type,
        "battery_type": row.battery_type,
        "ion_type": row.ion_type,
        "metric_name": row.metric_name,
        "metric_value": row.metric_value,
        "metric_unit": row.metric_unit,
        "condition_text": row.condition_text,
        "baseline": row.baseline,
        "key_mechanism": row.key_mechanism,
        "source_text": row.source_text[:500],
        "source_kind": row.source_kind,
        "reliability_level": row.reliability_level,
    }


def _computational_to_dict(row: ComputationalCatalysisEvidence) -> Dict[str, Any]:
    return {
        "id": row.id,
        "domain": row.domain,
        "dataset_name": row.dataset_name,
        "task_type": row.task_type,
        "reaction_context": row.reaction_context,
        "material_system": row.material_system,
        "surface_facet": row.surface_facet,
        "adsorbate": row.adsorbate,
        "electrolyte_or_solvent": row.electrolyte_or_solvent,
        "dft_energy": row.dft_energy,
        "adsorption_energy": row.adsorption_energy,
        "model_name": row.model_name,
        "predicted_value": row.predicted_value,
        "reference_value": row.reference_value,
        "source_url": row.source_url,
        "evidence_kind": row.evidence_kind,
        "reliability_level": row.reliability_level,
        "notes": row.notes,
        "extra_json": _safe_json_loads(row.extra_json),
    }


def _ranked(items: List[Dict[str, Any]], tokens: List[str], fields: List[str], limit: int) -> List[Dict[str, Any]]:
    if not tokens:
        return items[:limit]
    scored = []
    for item in items:
        text = " ".join(str(item.get(field, "")) for field in fields).lower()
        score = sum(1 for token in tokens if token in text)
        if score > 0:
            scored.append((score, item))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [item for _, item in scored[:limit]]


def _tokens(text: str) -> List[str]:
    raw = re.findall(r"[a-zA-Z0-9+\-*]+|[\u4e00-\u9fff]{2,}", str(text or "").lower())
    stop = {"the", "and", "for", "with", "from", "into", "that", "this", "研究", "材料", "性能"}
    seen = set()
    tokens = []
    for token in raw:
        if token in stop or len(token) < 2:
            continue
        if token not in seen:
            seen.add(token)
            tokens.append(token)
    return tokens[:24]


def normalize_adsorbate(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = text.replace("·", "").replace(" ", "")
    if text.startswith("$") and text.endswith("$"):
        text = text[1:-1]
    text = text.replace("\\ast", "*").replace("\\*", "*")
    upper = text.upper()
    if upper in ADSORBATE_ALIASES:
        return ADSORBATE_ALIASES[upper]
    if upper.startswith("*"):
        return "*" + upper[1:]
    if upper.endswith("*"):
        return "*" + upper[:-1]
    if upper in {"O", "OH", "OOH", "H", "CO", "CO2", "COOH", "OCHO", "N2", "N", "NH", "NH2"}:
        return "*" + upper
    return text


def infer_adsorbates_for_reaction(reaction_type: str) -> List[str]:
    reaction = str(reaction_type or "").upper().replace(" ", "")
    for key, values in REACTION_ADSORBATE_MAP.items():
        if key in reaction:
            return list(values)
    return []


def _extract_adsorbates_from_text(text: str) -> List[str]:
    found: List[str] = []
    pattern = r"(?<![A-Za-z0-9])(?:\*[A-Za-z0-9]{1,5}|[A-Za-z0-9]{1,5}\*)(?![A-Za-z0-9])"
    for match in re.finditer(pattern, str(text or "")):
        normalized = normalize_adsorbate(match.group(0))
        if normalized and normalized not in found:
            found.append(normalized)
    return found


def _requested_adsorbates(
    query_text: str = "",
    reaction_type: str = "",
    adsorbates: Optional[Iterable[str]] = None,
) -> List[str]:
    requested: List[str] = []
    for value in adsorbates or []:
        normalized = normalize_adsorbate(value)
        if normalized and normalized not in requested:
            requested.append(normalized)
    for value in _extract_adsorbates_from_text(query_text):
        if value not in requested:
            requested.append(value)
    for value in infer_adsorbates_for_reaction(reaction_type):
        if value not in requested:
            requested.append(value)
    return requested


def _adsorbate_variants(adsorbate: str) -> List[str]:
    normalized = normalize_adsorbate(adsorbate)
    if not normalized:
        return []
    core = normalized[1:] if normalized.startswith("*") else normalized
    return list(dict.fromkeys([normalized, f"{core}*"]))


def _adsorbate_filter(model: Any, adsorbates: Iterable[str]) -> Any:
    clauses = []
    for adsorbate in adsorbates:
        for variant in _adsorbate_variants(adsorbate):
            clauses.append(model.adsorbate == variant)
    return or_(*clauses) if clauses else True


def _clean(value: Any, max_len: int = 2000) -> str:
    if value is None:
        return ""
    return str(value).strip()[:max_len]


def _join_nonempty(values: Iterable[Any], sep: str = " | ") -> str:
    return sep.join(str(v).strip() for v in values if str(v or "").strip())


def _normalize_domain(domain: str) -> str:
    text = str(domain or "").strip().lower()
    if text in {"battery", "batteries", "battery_materials", "电池", "电池材料"}:
        return "battery"
    if text in {"electrocatalysis", "electrocatalyst", "electrocatalysts", "电催化"}:
        return "electrocatalysis"
    return text


def _domain_label(domain: str) -> str:
    key = _normalize_domain(domain)
    if key == "battery":
        return "电池"
    if key == "electrocatalysis":
        return "电催化"
    return domain or "未限定"


def _catalog_reaction_context(name: str) -> str:
    if name == "OC22":
        return "OER/ORR"
    if name == "OC25":
        return "solid-liquid electrocatalysis"
    if name == "ODAC23":
        return "CO2 adsorption"
    return "general catalysis"


def _format_static_source_catalog(domain: str) -> str:
    key = _normalize_domain(domain)
    lines = []
    if key == "electrocatalysis":
        lines.append("\n\n[内置计算催化数据源目录 - 仅作合规数据源线索]")
        for name in ["Open Catalyst Project / FAIR-Chem", "OC20", "OC20-Dense", "OC22", "OC25", "ODAC23"]:
            meta = OCP_DATASET_CATALOG[name]
            lines.append(f"- {name}: {meta['scope']} 来源={meta['url']} 说明={meta['note']}")
    if key in {"battery", "electrocatalysis"}:
        lines.append("\n[外部合规数据源白名单]")
        names = (
            ["Materials Project", "OQMD", "NOMAD", "Catalysis-Hub"]
            if key == "electrocatalysis"
            else ["Battery Archive", "NASA battery dataset", "CALCE battery dataset", "Materials Project", "NOMAD"]
        )
        for name in names:
            if name in EXTERNAL_DATA_SOURCE_WHITELIST:
                lines.append(f"- {name}: {EXTERNAL_DATA_SOURCE_WHITELIST[name]}")
    return "\n".join(lines)


def _first_match(text: str, candidates: List[str]) -> str:
    for candidate in candidates:
        if candidate.lower() in text:
            return candidate
    return ""


def _first_token_match(text: str, candidates: List[str]) -> str:
    for candidate in candidates:
        if re.search(rf"(?<![a-z0-9]){re.escape(candidate.lower())}(?![a-z0-9])", text):
            return candidate
    return ""


def _first_ion_match(text: str) -> str:
    aliases = [
        ("Li", [r"\bli\b", r"\bli\+", r"lithium", "锂"]),
        ("Na", [r"\bna\b", r"\bna\+", r"sodium", "钠"]),
        ("K", [r"\bk\b", r"\bk\+", r"potassium", "钾"]),
        ("Zn", [r"\bzn\b", r"\bzn2\+", r"zinc", "锌"]),
    ]
    for label, patterns in aliases:
        for pattern in patterns:
            if pattern.startswith("\\") or pattern.startswith("r\""):
                if re.search(pattern, text):
                    return label
            elif pattern in text:
                return label
    return ""


def _extract_material_hint(text: str) -> str:
    source = str(text or "")
    insensitive_patterns = [
        r"\bmp-\d+\b",
        r"\bHigh[-–]Ni(?:\s+(?:cathode|layered oxide|正极))?\b",
        r"\bLiFePO4\b|\bLFP\b",
        r"\bNiFe(?:\s+oxide)?\b",
        r"\b(?:Fe|Co|Ni|Mn|Cu|Pt|Pd|Ru|Ir)[-–]N[-–]C\b",
        r"\b(?:Fe|Co|Ni|Mn)[-–]N4\b",
    ]
    formula_patterns = [
        r"\b[A-Z][a-z]?(?:[-–]?[A-Z][a-z]?|\d|[A-Z]){1,12}\b",
    ]
    ignored = {
        "DOI", "PDF", "HTML", "XRD", "SEM", "TEM", "XPS", "EIS", "CV",
        "HER", "HOR", "OER", "ORR", "NRR", "CO2RR", "FE", "CE",
        "O", "OH", "OOH", "H", "CO", "CO2", "COOH", "OCHO", "N2", "N", "NH", "NH2",
        "OCP", "OC20", "OC22", "OC25", "ODAC23", "ADSORBML",
    }
    for pattern, flags in (
        *[(pattern, re.IGNORECASE) for pattern in insensitive_patterns],
        *[(pattern, 0) for pattern in formula_patterns],
    ):
        for match in re.finditer(pattern, source, flags=flags):
            value = match.group(0).strip()
            if value.upper() in ignored:
                continue
            return value
    return ""


_METRIC_PATTERNS: Dict[str, List[tuple[str, str]]] = {
    "battery": [
        ("specific_capacity", r"(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>mAh\s*g(?:\^-?1|−1|-1|⁻¹)?)"),
        ("capacity_retention", r"(?:capacity\s*retention|容量保持率)\s*(?:=|:|of)?\s*(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>%)"),
        ("capacity_retention", r"(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>%)\s*(?:capacity\s*)?retention"),
        ("coulombic_efficiency", r"(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>%)(?:\s*(?:coulombic efficiency|CE|库伦效率))"),
        ("cycle_number", r"(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>cycles?|圈)"),
        ("EIS_Rct", r"(?:Rct|charge[-\s]?transfer resistance|电荷转移阻抗)\s*(?:=|:)?\s*(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>Ω|ohm|Ohm)"),
        ("energy_density", r"(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>Wh\s*kg(?:\^-?1|−1|-1|⁻¹)?)"),
    ],
    "electrocatalysis": [
        ("overpotential", r"(?:overpotential|过电位|η)\s*(?:=|:|at)?\s*(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>mV|V)"),
        ("Tafel_slope", r"(?:Tafel(?:\s*slope)?|塔菲尔斜率)\s*(?:=|:)?\s*(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>mV\s*dec(?:\^-?1|−1|-1|⁻¹)?)"),
        ("current_density", r"(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>mA\s*cm(?:\^-?2|−2|-2|⁻²)?)"),
        ("Faradaic_efficiency", r"(?:Faradaic efficiency|FE|法拉第效率)\s*(?:=|:)?\s*(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>%)"),
        ("selectivity", r"(?:selectivity|选择性)\s*(?:=|:)?\s*(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>%)"),
        ("stability_time", r"(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>h|hours?|小时)(?:\s*(?:stability|稳定性|durability))?"),
    ],
}


def _extract_metric_snippets(text: str, domain: str = "") -> List[Dict[str, str]]:
    normalized_domain = _normalize_domain(domain)
    patterns = _METRIC_PATTERNS.get(normalized_domain, [])
    if not patterns:
        patterns = _METRIC_PATTERNS["battery"] + _METRIC_PATTERNS["electrocatalysis"]
    snippets = []
    seen = set()
    source = str(text or "")
    for metric_name, pattern in patterns:
        for match in re.finditer(pattern, source, flags=re.IGNORECASE):
            start = max(0, match.start() - 90)
            end = min(len(source), match.end() + 90)
            snippet = " ".join(source[start:end].split())
            key = (metric_name, match.group("value"), match.group("unit"), snippet[:80])
            if key in seen:
                continue
            seen.add(key)
            snippets.append(
                {
                    "metric_name": metric_name,
                    "metric_value": match.group("value"),
                    "metric_unit": match.group("unit"),
                    "text": snippet,
                }
            )
    return snippets


def _extract_doi(text: str) -> str:
    match = re.search(r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+\b", str(text or ""), flags=re.IGNORECASE)
    return match.group(0).rstrip(".,);") if match else ""


def _extract_mechanism_hint(text: str) -> str:
    source = str(text or "")
    keywords = [
        "SEI",
        "CEI",
        "dendrite",
        "枝晶",
        "副反应",
        "oxygen release",
        "吸附",
        "active site",
        "活性位点",
        "rate-determining",
        "速率决定",
    ]
    for keyword in keywords:
        idx = source.lower().find(keyword.lower())
        if idx >= 0:
            return " ".join(source[max(0, idx - 100): min(len(source), idx + 180)].split())
    return ""


def _extract_limitation_hint(text: str) -> str:
    source = str(text or "")
    keywords = ["limitation", "challenge", "bottleneck", "degradation", "衰减", "局限", "不足", "失效"]
    for keyword in keywords:
        idx = source.lower().find(keyword.lower())
        if idx >= 0:
            return " ".join(source[max(0, idx - 100): min(len(source), idx + 180)].split())
    return ""


def _infer_domain(text: str, fallback: str = "") -> str:
    normalized = _normalize_domain(fallback)
    if normalized:
        return normalized
    lower = str(text or "").lower()
    if any(token in lower for token in ["her", "oer", "orr", "co2rr", "tafel", "faradaic", "overpotential", "电催化", "过电位"]):
        return "electrocatalysis"
    if any(token in lower for token in ["battery", "cathode", "anode", "mAh".lower(), "循环", "正极", "负极", "电池"]):
        return "battery"
    return ""
