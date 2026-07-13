from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.core.dependencies import current_user, get_db_session
from app.core.errors import ApiError
from app.core.legacy import db
from app.schemas.resources import DocumentAnalysisListOut, DocumentAnalysisMaybeOut, DocumentAnalysisOut
from app.services.jobs import job_service

router = APIRouter(tags=["document analyses"])


def _owned_document(session: Session, user_id: int, document_id: int):
    document = session.query(db().Document).filter(
        db().Document.id == document_id,
        db().Document.user_id == user_id,
    ).first()
    if not document:
        raise ApiError("NOT_FOUND", "Document not found.", status.HTTP_404_NOT_FOUND)
    return document


def _analysis_out(record) -> DocumentAnalysisOut:
    return DocumentAnalysisOut(**record.to_dict())


@router.get("/documents/{document_id}/analyses", response_model=DocumentAnalysisListOut)
def list_document_analyses(
    document_id: int,
    session: Session = Depends(get_db_session),
    user=Depends(current_user),
) -> DocumentAnalysisListOut:
    _owned_document(session, user.id, document_id)
    records = job_service.analysis_store.list_for_document(user.id, document_id)
    return DocumentAnalysisListOut(data=[_analysis_out(record) for record in records])


@router.get("/document-comparisons/latest", response_model=DocumentAnalysisMaybeOut)
def get_latest_document_comparison(
    document_ids: list[int] = Query(alias="documentId"),
    session: Session = Depends(get_db_session),
    user=Depends(current_user),
) -> DocumentAnalysisMaybeOut:
    normalized = sorted(set(document_ids))
    if len(normalized) != len(document_ids) or not 2 <= len(normalized) <= 5:
        raise ApiError(
            "VALIDATION_ERROR",
            "Select between two and five unique documents.",
            status.HTTP_422_UNPROCESSABLE_CONTENT,
        )
    owned_count = session.query(db().Document.id).filter(
        db().Document.user_id == user.id,
        db().Document.id.in_(normalized),
    ).count()
    if owned_count != len(normalized):
        raise ApiError("NOT_FOUND", "One or more documents were not found.", status.HTTP_404_NOT_FOUND)
    record = job_service.analysis_store.get_comparison(user.id, normalized)
    return DocumentAnalysisMaybeOut(data=_analysis_out(record) if record else None)


@router.get("/documents/{document_id}/assets/{asset_id}", response_class=FileResponse)
def get_document_asset(
    document_id: int,
    asset_id: str,
    session: Session = Depends(get_db_session),
    user=Depends(current_user),
):
    _owned_document(session, user.id, document_id)
    asset = job_service.analysis_store.get_asset_for_user(user.id, document_id, asset_id)
    if not asset:
        raise ApiError("NOT_FOUND", "Analysis asset not found.", status.HTTP_404_NOT_FOUND)
    root = job_service.assets_dir.resolve()
    path = Path(asset.path).resolve()
    if path.is_symlink() or not path.is_relative_to(root) or not path.is_file():
        raise ApiError("NOT_FOUND", "Analysis asset not found.", status.HTTP_404_NOT_FOUND)
    return FileResponse(
        path,
        media_type=asset.mime_type,
        filename=asset.file_name,
        content_disposition_type="inline",
    )
