from __future__ import annotations

import json
import logging
import shutil
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.dependencies import current_user, get_db_session
from app.core.errors import ApiError
from app.core.legacy import db
from app.schemas.common import Page, paginate
from app.schemas.resources import HypothesisDetailOut, HypothesisMetadataUpdate, HypothesisSummaryOut
from app.services.hypotheses import hypothesis_detail, hypothesis_summary, parse_json_object
from app.services.jobs import job_service


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/hypotheses", tags=["hypotheses"])


def _owned_hypothesis(session: Session, user_id: int, hypothesis_id: int):
    return session.query(db().Hypothesis).filter(
        db().Hypothesis.id == hypothesis_id,
        db().Hypothesis.user_id == user_id,
    ).first()


@router.get("", response_model=Page[HypothesisSummaryOut])
def list_hypotheses(
    keyword: str | None = Query(default=None, max_length=500),
    hypothesis_status: str | None = Query(default=None, alias="status", max_length=20),
    domain: str | None = Query(default=None, max_length=120),
    tag: str | None = Query(default=None, max_length=60),
    source_document_id: int | None = Query(default=None, alias="sourceDocumentId", ge=1),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, alias="pageSize", ge=1, le=100),
    session: Session = Depends(get_db_session),
    user=Depends(current_user),
) -> Page[HypothesisSummaryOut]:
    summaries = [hypothesis_summary(item) for item in db().get_hypotheses(session, user.id)]
    if keyword:
        lowered = keyword.casefold()
        summaries = [
            item for item in summaries
            if lowered in item["title"].casefold()
            or lowered in str(item.get("researchQuestion") or "").casefold()
            or any(lowered in value.casefold() for value in item["tags"])
        ]
    if hypothesis_status:
        summaries = [item for item in summaries if item["status"] == hypothesis_status]
    if domain:
        summaries = [item for item in summaries if item["domain"] == domain]
    if tag:
        lowered_tag = tag.casefold()
        summaries = [item for item in summaries if any(value.casefold() == lowered_tag for value in item["tags"])]
    if source_document_id is not None:
        summaries = [item for item in summaries if source_document_id in item["sourceDocumentIds"]]
    rows, pagination = paginate([HypothesisSummaryOut(**item) for item in summaries], page, page_size)
    return Page(data=rows, pagination=pagination)


@router.get("/{hypothesis_id}", response_model=HypothesisDetailOut)
def get_hypothesis(
    hypothesis_id: int,
    session: Session = Depends(get_db_session),
    user=Depends(current_user),
) -> HypothesisDetailOut:
    hypothesis = _owned_hypothesis(session, user.id, hypothesis_id)
    if not hypothesis:
        raise ApiError("NOT_FOUND", "Hypothesis not found.", status.HTTP_404_NOT_FOUND)
    feedback = session.query(db().HypothesisFeedback).filter(
        db().HypothesisFeedback.hypothesis_id == hypothesis_id,
        db().HypothesisFeedback.user_id == user.id,
    ).order_by(db().HypothesisFeedback.created_at.asc()).all()
    artifacts = job_service.hypothesis_store.list_for_hypothesis(user.id, hypothesis_id)
    payload = hypothesis_detail(
        hypothesis,
        feedback_rows=feedback,
        artifacts=artifacts,
        private_roots=[get_settings().legacy_root, get_settings().data_dir, job_service.hypothesis_assets_dir],
    )
    return HypothesisDetailOut(**payload)


@router.patch("/{hypothesis_id}", response_model=HypothesisSummaryOut)
def update_hypothesis(
    hypothesis_id: int,
    payload: HypothesisMetadataUpdate,
    session: Session = Depends(get_db_session),
    user=Depends(current_user),
) -> HypothesisSummaryOut:
    hypothesis = _owned_hypothesis(session, user.id, hypothesis_id)
    if not hypothesis:
        raise ApiError("NOT_FOUND", "Hypothesis not found.", status.HTTP_404_NOT_FOUND)
    if payload.title is not None:
        hypothesis.title = payload.title
    if payload.status is not None:
        hypothesis.status = payload.status
    if payload.tags is not None:
        extra = parse_json_object(hypothesis.extra_json)
        extra["tags"] = payload.tags
        hypothesis.extra_json = json.dumps(extra, ensure_ascii=False, default=str)
    hypothesis.updated_at = datetime.now()
    session.commit()
    session.refresh(hypothesis)
    return HypothesisSummaryOut(**hypothesis_summary(hypothesis))


@router.delete("/{hypothesis_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_hypothesis(
    hypothesis_id: int,
    session: Session = Depends(get_db_session),
    user=Depends(current_user),
) -> None:
    hypothesis = _owned_hypothesis(session, user.id, hypothesis_id)
    if not hypothesis:
        raise ApiError("NOT_FOUND", "Hypothesis not found.", status.HTTP_404_NOT_FOUND)
    session.query(db().HypothesisFeedback).filter(
        db().HypothesisFeedback.hypothesis_id == hypothesis_id,
        db().HypothesisFeedback.user_id == user.id,
    ).delete(synchronize_session=False)
    session.delete(hypothesis)
    session.commit()

    try:
        paths = job_service.hypothesis_store.delete_for_hypothesis(user.id, hypothesis_id)
        root = job_service.hypothesis_assets_dir.resolve()
        for path in paths:
            resolved = Path(path).resolve()
            if not resolved.is_relative_to(root):
                logger.warning("Refusing to delete unmanaged hypothesis artifact %s", resolved)
                continue
            if resolved.is_dir():
                shutil.rmtree(resolved)
            else:
                resolved.unlink(missing_ok=True)
        managed_dir = (root / str(user.id) / str(hypothesis_id)).resolve()
        if managed_dir.is_relative_to(root):
            shutil.rmtree(managed_dir, ignore_errors=True)
    except Exception:
        logger.warning("Could not completely clean hypothesis artifacts for %s", hypothesis_id, exc_info=True)
    return None


@router.get("/{hypothesis_id}/artifacts/{artifact_id}")
def get_hypothesis_artifact(
    hypothesis_id: int,
    artifact_id: str,
    session: Session = Depends(get_db_session),
    user=Depends(current_user),
):
    if not _owned_hypothesis(session, user.id, hypothesis_id):
        raise ApiError("NOT_FOUND", "Hypothesis not found.", status.HTTP_404_NOT_FOUND)
    artifact = job_service.hypothesis_store.get_for_user(user.id, hypothesis_id, artifact_id)
    root = job_service.hypothesis_assets_dir.resolve()
    if (
        not artifact
        or artifact.path.is_symlink()
        or not artifact.path.is_file()
        or not artifact.path.resolve().is_relative_to(root)
    ):
        raise ApiError("NOT_FOUND", "Artifact not found.", status.HTTP_404_NOT_FOUND)
    disposition = "inline" if artifact.kind == "interactive_report_html" else "attachment"
    headers = {"Content-Disposition": f'{disposition}; filename="{Path(artifact.file_name).name}"'}
    if artifact.kind == "interactive_report_html":
        headers["Content-Security-Policy"] = (
            "default-src 'none'; script-src 'unsafe-inline' https://cdn.jsdelivr.net; "
            "style-src 'unsafe-inline'; img-src data: blob:; font-src data:; connect-src 'none'; "
            "base-uri 'none'; form-action 'none'; frame-ancestors 'none'; "
            "sandbox allow-scripts allow-downloads"
        )
    return FileResponse(
        artifact.path,
        media_type=artifact.mime_type,
        headers=headers,
    )
