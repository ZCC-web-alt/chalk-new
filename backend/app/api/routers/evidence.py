from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.dependencies import current_user, get_db_session
from app.core.config import get_settings
from app.core.errors import ApiError
from app.core.legacy import db, evidence_database
from app.schemas.resources import EvidenceListOut
from app.schemas.jobs import JobOut
from app.services.hypotheses import sanitize_public_data
from app.services.jobs import job_service

router = APIRouter(prefix="/evidence", tags=["evidence"])
MANIFEST_SUFFIXES = {".csv", ".json", ".jsonl", ".ndjson"}
MANIFEST_CONTENT_TYPES = {
    "text/csv",
    "text/plain",
    "application/csv",
    "application/json",
    "application/x-ndjson",
    "application/octet-stream",
}


class LiteratureReferenceInput(BaseModel):
    id: str = Field(default="", max_length=100)
    title: str = Field(min_length=1, max_length=1000)
    authors: str = Field(default="", max_length=4000)
    journal: str = Field(default="", max_length=500)
    year: str = Field(default="", max_length=20)
    doi: str = Field(default="", max_length=300)
    abstract: str = Field(default="", max_length=50000)
    source_platform: str = Field(default="", alias="sourcePlatform", max_length=100)
    url: str = Field(default="", max_length=2000)
    is_open_access: bool = Field(default=False, alias="isOpenAccess")
    access_status: str = Field(default="", alias="accessStatus", max_length=100)
    warning: str = Field(default="", max_length=2000)

    model_config = {"populate_by_name": True, "extra": "forbid"}


class LiteratureEvidenceInput(BaseModel):
    domain: str = Field(default="", max_length=120)
    references: list[LiteratureReferenceInput] = Field(min_length=1, max_length=100)

    model_config = {"extra": "forbid"}


@router.get("", response_model=EvidenceListOut)
def list_evidence(
    kind: str | None = None,
    keyword: str | None = None,
    domain: str | None = None,
    dataset_name: str | None = Query(default=None, alias="datasetName"),
    reaction_type: str | None = Query(default=None, alias="reactionType"),
    material: str | None = None,
    reliability_level: str | None = Query(default=None, alias="reliabilityLevel"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, alias="pageSize", ge=1, le=100),
    session: Session = Depends(get_db_session),
    user=Depends(current_user),
) -> dict:
    filters = {
        "kind": kind or "",
        "keyword": keyword or "",
        "domain": domain or "",
        "dataset_name": dataset_name or "",
        "reaction_type": reaction_type or "",
        "material": material or "",
        "reliability_level": reliability_level or "",
    }
    page_obj = evidence_database().list_evidence_records(
        session,
        user_id=user.id,
        filters=filters,
        page=page,
        page_size=page_size,
    )
    graph = evidence_database().build_evidence_relationship_graph(page_obj.records)
    settings = get_settings()
    private_roots = (settings.legacy_root, settings.data_dir)
    return {
        "data": sanitize_public_data(page_obj.records, private_roots=private_roots),
        "pagination": {
            "page": page_obj.page,
            "pageSize": page_obj.page_size,
            "totalItems": page_obj.total,
            "totalPages": (page_obj.total + page_obj.page_size - 1) // page_obj.page_size if page_obj.total else 0,
        },
        "graph": sanitize_public_data(graph, private_roots=private_roots),
    }


@router.post("/literature")
def index_literature_references(
    payload: LiteratureEvidenceInput,
    session: Session = Depends(get_db_session),
    user=Depends(current_user),
) -> dict[str, int]:
    count = 0
    for index, reference in enumerate(payload.references, 1):
        raw = reference.model_dump(by_alias=True)
        evidence_database().upsert_literature_evidence(
            session,
            user_id=user.id,
            domain=payload.domain,
            paper_id=reference.id or reference.doi or f"web-search-{index}",
            title=reference.title,
            doi=reference.doi,
            year=reference.year,
            journal=reference.journal,
            key_data=reference.abstract,
            source_text=reference.abstract or reference.title,
            source_kind="oa_search" if reference.is_open_access else "metadata_search",
            reliability_level=(
                "open_access" if reference.is_open_access else "metadata_only_needs_fulltext"
            ),
            extra_json=json.dumps(raw, ensure_ascii=False),
        )
        count += 1
    return {"count": count}


@router.post("/manifests", response_model=JobOut, status_code=status.HTTP_202_ACCEPTED)
async def import_evidence_manifest(
    file: UploadFile = File(...),
    dataset_name: str = Form(default="OC dataset", alias="datasetName", max_length=120),
    user=Depends(current_user),
) -> JobOut:
    original_name = Path(file.filename or "").name
    suffix = Path(original_name).suffix.lower()
    if not original_name or suffix not in MANIFEST_SUFFIXES or file.content_type not in MANIFEST_CONTENT_TYPES:
        raise ApiError(
            "INVALID_MANIFEST",
            "Only CSV, JSON, JSONL, and NDJSON manifests can be imported.",
            status.HTTP_422_UNPROCESSABLE_CONTENT,
        )
    clean_dataset = dataset_name.strip() or "OC dataset"
    settings = get_settings()
    user_dir = (settings.data_dir / "evidence-imports" / str(user.id)).resolve()
    user_dir.mkdir(parents=True, exist_ok=True)
    destination = user_dir / f"{uuid.uuid4().hex}{suffix}"
    temporary = destination.with_suffix(destination.suffix + ".upload")
    total = 0
    job_created = False
    try:
        with temporary.open("wb") as handle:
            while chunk := await file.read(1024 * 1024):
                total += len(chunk)
                if total > settings.max_upload_bytes:
                    raise ApiError("FILE_TOO_LARGE", "The manifest exceeds the upload limit.", status.HTTP_413_CONTENT_TOO_LARGE)
                handle.write(chunk)
        if total == 0:
            raise ApiError("INVALID_MANIFEST", "The manifest is empty.", status.HTTP_422_UNPROCESSABLE_CONTENT)
        temporary.replace(destination)
        job = job_service.create(user.id, "evidence_manifest_import", {
            "storedPath": str(destination),
            "originalFilename": original_name,
            "datasetName": clean_dataset,
        })
        job_created = True
        return JobOut(**job_service.to_public_dict(job))
    except Exception:
        temporary.unlink(missing_ok=True)
        if not job_created:
            destination.unlink(missing_ok=True)
        raise
    finally:
        await file.close()


@router.post("/lab-records/{record_id}/index")
def index_lab_record(
    record_id: int,
    session: Session = Depends(get_db_session),
    user=Depends(current_user),
) -> dict[str, Any]:
    owned = session.query(db().LabRecord.id).filter(
        db().LabRecord.id == record_id,
        db().LabRecord.user_id == user.id,
    ).first()
    if not owned:
        raise ApiError("NOT_FOUND", "Lab record not found.", status.HTTP_404_NOT_FOUND)
    counts = evidence_database().index_lab_record_as_evidence(
        session,
        user_id=user.id,
        lab_record_id=record_id,
    )
    return {"domainEvidence": int(counts.get("domain_evidence", 0))}


@router.post("/hypotheses/{hypothesis_id}/index")
def index_hypothesis(
    hypothesis_id: int,
    session: Session = Depends(get_db_session),
    user=Depends(current_user),
) -> dict[str, Any]:
    hypothesis = session.query(db().Hypothesis).filter(
        db().Hypothesis.id == hypothesis_id,
        db().Hypothesis.user_id == user.id,
    ).first()
    if not hypothesis:
        raise ApiError("NOT_FOUND", "Hypothesis not found.", status.HTTP_404_NOT_FOUND)
    try:
        result = json.loads(hypothesis.result_json or "{}")
    except (TypeError, ValueError):
        result = {}
    counts = evidence_database().index_hypothesis_json_as_evidence(
        session,
        user_id=user.id,
        hypothesis_data=result,
        hypothesis_id=hypothesis_id,
    )
    return {
        "literatureEvidence": int(counts.get("literature_evidence", 0)),
        "domainEvidence": int(counts.get("domain_evidence", 0)),
    }


@router.post("/curated")
def import_curated_evidence(
    session: Session = Depends(get_db_session),
    user=Depends(current_user),
) -> dict[str, int]:
    counts = evidence_database().seed_curated_energy_evidence(
        session,
        user_id=user.id,
        domains=("battery", "electrocatalysis"),
    )
    return {
        "literatureEvidence": int(counts.get("literature_evidence", 0)),
        "domainEvidence": int(counts.get("domain_evidence", 0)),
        "imported": int(counts.get("imported", 0)),
        "updated": int(counts.get("updated", 0)),
        "skipped": int(counts.get("skipped", 0)),
    }
