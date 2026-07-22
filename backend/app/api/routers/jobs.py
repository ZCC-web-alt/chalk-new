from __future__ import annotations

import unicodedata
from typing import Literal

from fastapi import APIRouter, Depends, Query, status
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.core.dependencies import current_user, get_db_session
from app.core.errors import ApiError, validation_error_details
from app.core.legacy import db
from app.schemas.common import Page, paginate
from app.schemas.jobs import HypothesisFeedbackInput, JobCreate, JobOut, JobStatus, JobType
from app.services.jobs import ActiveHypothesisJobError, job_service
from app.services.science125_catalog import (
    Science125CatalogError,
    get_science125_catalog,
    is_science125_pilot_enabled,
)

router = APIRouter(prefix="/jobs", tags=["jobs"])


def _job_out(job) -> JobOut:
    return JobOut(**job_service.to_public_dict(job))


def _canonical_question(value: object) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return " ".join("".join(character if character.isalnum() else " " for character in normalized).split())


def _validate_science125_binding(job_type: str, payload: dict) -> None:
    science125_id = payload.get("science125Id")
    if not science125_id and job_type != "hypothesis_generate":
        return
    try:
        catalog = get_science125_catalog()
    except Science125CatalogError as exc:
        raise ApiError(
            "SCIENCE125_CATALOG_UNAVAILABLE",
            "The Science 125 question catalog is temporarily unavailable.",
            status.HTTP_503_SERVICE_UNAVAILABLE,
        ) from exc
    if not science125_id:
        if _canonical_question(payload.get("researchQuestion")) in {
            _canonical_question(question.question) for question in catalog.data
        }:
            raise ApiError(
                "SCIENCE125_ID_REQUIRED",
                "A Science 125 question must include its authoritative item ID; the legacy chemistry prompt cannot be used for it.",
                status.HTTP_422_UNPROCESSABLE_CONTENT,
            )
        return
    item = next((question for question in catalog.data if question.id == science125_id), None)
    expected = item.question if item else ""
    if not expected:
        raise ApiError(
            "SCIENCE125_QUESTION_MISMATCH",
            "The Science 125 item is not part of the authoritative catalog.",
            status.HTTP_422_UNPROCESSABLE_CONTENT,
        )
    if job_type == "literature_search":
        return
    actual = payload.get("researchQuestion")
    if job_type == "hypothesis_generate" and not actual:
        return
    if _canonical_question(actual) != _canonical_question(expected):
        raise ApiError(
            "SCIENCE125_QUESTION_MISMATCH",
            "The Science 125 item must use its authoritative question text.",
            status.HTTP_422_UNPROCESSABLE_CONTENT,
        )


@router.get("", response_model=Page[JobOut])
def list_jobs(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, alias="pageSize", ge=1, le=100),
    job_type: JobType | None = Query(default=None, alias="type"),
    job_status: JobStatus | None = Query(default=None, alias="status"),
    science125_id: str | None = Query(default=None, alias="science125Id"),
    science125_scope: Literal["all", "unbound"] = Query(default="all", alias="science125Scope"),
    user=Depends(current_user),
) -> Page[JobOut]:
    jobs = job_service.list_for_user(user.id, job_type=job_type, status=job_status)
    if science125_id:
        jobs = [job for job in jobs if job.payload.get("science125Id") == science125_id]
    elif science125_scope == "unbound":
        jobs = [job for job in jobs if not job.payload.get("science125Id")]
    rows, pagination = paginate([_job_out(job) for job in jobs], page, page_size)
    return Page(data=rows, pagination=pagination)


@router.post("", response_model=JobOut, status_code=status.HTTP_202_ACCEPTED)
def create_job(
    payload: JobCreate,
    session: Session = Depends(get_db_session),
    user=Depends(current_user),
) -> JobOut:
    if payload.type in {"literature_search", "hypothesis_generate"}:
        # Preserve the authoritative-question error contract even when a
        # Science 125 request is otherwise incomplete.
        _validate_science125_binding(payload.type, payload.payload)
    try:
        validated_payload = payload.validated_payload()
    except ValidationError as exc:
        raise ApiError(
            "VALIDATION_ERROR",
            "Invalid job payload.",
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            validation_error_details(exc.errors(include_url=False)),
        ) from exc
    if payload.type == "hypothesis_generate" and validated_payload.get("science125Id"):
        if not is_science125_pilot_enabled(validated_payload["science125Id"]):
            raise ApiError(
                "SCIENCE125_PILOT_NOT_ENABLED",
                "Science 125 generation is currently limited to the three audited pilot items.",
                status.HTTP_409_CONFLICT,
            )
        search_job = job_service.get_for_user(
            user.id,
            str(validated_payload.get("literatureSearchJobId") or ""),
        )
        if not search_job or search_job.type != "literature_search":
            raise ApiError(
                "SCIENCE125_SEARCH_NOT_FOUND",
                "The reviewed Science 125 literature search was not found.",
                status.HTTP_404_NOT_FOUND,
            )
        if search_job.status != "SUCCEEDED":
            raise ApiError(
                "SCIENCE125_SEARCH_NOT_READY",
                "The reviewed Science 125 literature search has not completed.",
                status.HTTP_409_CONFLICT,
            )
        if search_job.payload.get("science125Id") != validated_payload["science125Id"]:
            raise ApiError(
                "SCIENCE125_SEARCH_MISMATCH",
                "The reviewed literature search belongs to a different Science 125 item.",
                status.HTTP_422_UNPROCESSABLE_CONTENT,
            )
    if payload.type == "document_analysis":
        owned = session.query(db().Document.id).filter(
            db().Document.id == validated_payload["documentId"],
            db().Document.user_id == user.id,
        ).first()
        if not owned:
            raise ApiError("NOT_FOUND", "Document not found.", status.HTTP_404_NOT_FOUND)
    elif payload.type == "document_compare":
        document_ids = validated_payload["documentIds"]
        owned_count = session.query(db().Document.id).filter(
            db().Document.id.in_(document_ids),
            db().Document.user_id == user.id,
        ).count()
        if owned_count != len(document_ids):
            raise ApiError("NOT_FOUND", "One or more documents were not found.", status.HTTP_404_NOT_FOUND)
    elif payload.type == "hypothesis_generate":
        document_ids = validated_payload.get("sourceDocIds") or []
        if document_ids:
            owned_count = session.query(db().Document.id).filter(
                db().Document.id.in_(document_ids),
                db().Document.user_id == user.id,
            ).count()
            if owned_count != len(document_ids):
                raise ApiError("NOT_FOUND", "One or more source documents were not found.", status.HTTP_404_NOT_FOUND)
        for run_id in validated_payload.get("multimodalRunIds") or []:
            if not job_service.science_store.get_multimodal_run(user_id=user.id, run_id=run_id):
                raise ApiError("NOT_FOUND", "A multimodal run was not found.", status.HTTP_404_NOT_FOUND)
    elif payload.type == "lab_suggest":
        owned = session.query(db().LabRecord.id).filter(
            db().LabRecord.id == validated_payload["recordId"],
            db().LabRecord.user_id == user.id,
        ).first()
        if not owned:
            raise ApiError("NOT_FOUND", "Lab record not found.", status.HTTP_404_NOT_FOUND)
    elif payload.type in {"hypothesis_report", "hypothesis_workflow_export"}:
        owned = session.query(db().Hypothesis.id).filter(
            db().Hypothesis.id == validated_payload["hypothesisId"],
            db().Hypothesis.user_id == user.id,
        ).first()
        if not owned:
            raise ApiError("NOT_FOUND", "Hypothesis not found.", status.HTTP_404_NOT_FOUND)
    elif payload.type == "multimodal_analyze":
        for source in validated_payload["sources"]:
            if source["sourceType"] == "upload":
                asset = job_service.science_store.get_multimodal_asset(
                    user_id=user.id,
                    asset_id=source["assetId"],
                )
                if not asset:
                    raise ApiError("NOT_FOUND", "A multimodal asset was not found.", status.HTTP_404_NOT_FOUND)
            else:
                document_id = source["documentId"]
                owned = session.query(db().Document.id).filter(
                    db().Document.id == document_id,
                    db().Document.user_id == user.id,
                ).first()
                asset = job_service.analysis_store.get_asset_for_user(user.id, document_id, source["assetId"])
                if not owned or not asset:
                    raise ApiError("NOT_FOUND", "A document image was not found.", status.HTTP_404_NOT_FOUND)
    elif payload.type == "modeling_generate":
        source = validated_payload["source"]
        if source["type"] == "document":
            owned = session.query(db().Document.id).filter(
                db().Document.id == source["documentId"],
                db().Document.user_id == user.id,
            ).first()
            if not owned:
                raise ApiError("NOT_FOUND", "Document not found.", status.HTTP_404_NOT_FOUND)
        elif source["type"] == "hypothesis":
            owned = session.query(db().Hypothesis.id).filter(
                db().Hypothesis.id == source["hypothesisId"],
                db().Hypothesis.user_id == user.id,
            ).first()
            if not owned:
                raise ApiError("NOT_FOUND", "Hypothesis not found.", status.HTTP_404_NOT_FOUND)
    elif payload.type == "modeling_export":
        workspace = job_service.science_store.get_modeling_workspace(
            user_id=user.id,
            workspace_id=validated_payload["workspaceId"],
        )
        if not workspace:
            raise ApiError("NOT_FOUND", "Modeling workspace not found.", status.HTTP_404_NOT_FOUND)
    try:
        job = job_service.create(user.id, payload.type, validated_payload)
    except ActiveHypothesisJobError as exc:
        raise ApiError(
            "HYPOTHESIS_JOB_ACTIVE",
            "Another hypothesis generation job is already active.",
            status.HTTP_409_CONFLICT,
            {"existingJobId": exc.existing_job_id},
        ) from exc
    return _job_out(job)


@router.get("/{job_id}", response_model=JobOut)
def get_job(job_id: str, user=Depends(current_user)) -> JobOut:
    job = job_service.get_for_user(user.id, job_id)
    if not job:
        raise ApiError("NOT_FOUND", "Job not found.", status.HTTP_404_NOT_FOUND)
    return _job_out(job)


@router.post("/{job_id}/cancel", response_model=JobOut)
def cancel_job(job_id: str, user=Depends(current_user)) -> JobOut:
    job = job_service.cancel(user.id, job_id)
    if not job:
        raise ApiError("NOT_FOUND", "Job not found.", status.HTTP_404_NOT_FOUND)
    return _job_out(job)


@router.post("/{job_id}/feedback", response_model=JobOut)
def submit_feedback(job_id: str, payload: HypothesisFeedbackInput, user=Depends(current_user)) -> JobOut:
    existing = job_service.get_for_user(user.id, job_id)
    if not existing:
        raise ApiError("NOT_FOUND", "Job not found.", status.HTTP_404_NOT_FOUND)
    if existing.type != "hypothesis_generate" or existing.status != "WAITING_FOR_FEEDBACK":
        raise ApiError(
            "JOB_NOT_WAITING",
            "The job is not waiting for hypothesis feedback.",
            status.HTTP_409_CONFLICT,
        )
    feedback = payload.model_dump(by_alias=True, exclude_none=True)
    job = job_service.submit_hypothesis_feedback(user.id, job_id, feedback)
    if not job:
        raise ApiError("NOT_FOUND", "Job not found.", status.HTTP_404_NOT_FOUND)
    if job.status != "RUNNING":
        raise ApiError(
            "JOB_NOT_WAITING",
            "The job is no longer accepting hypothesis feedback.",
            status.HTTP_409_CONFLICT,
        )
    return _job_out(job)
