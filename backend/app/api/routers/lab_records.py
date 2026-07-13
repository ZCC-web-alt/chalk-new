from __future__ import annotations

import json
from datetime import datetime

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.core.dependencies import current_user, get_db_session
from app.core.errors import ApiError
from app.core.legacy import db
from app.schemas.common import Page, paginate
from app.schemas.resources import LabRecordCreate, LabRecordOut, LabRecordUpdate

router = APIRouter(prefix="/lab-records", tags=["lab-records"])


def _validate_related_documents(value: str | None, session: Session, user_id: int) -> str | None:
    if value is None or not value.strip():
        return None
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError) as exc:
        raise ApiError("VALIDATION_ERROR", "relatedDocIds must be a JSON array.", status.HTTP_422_UNPROCESSABLE_CONTENT) from exc
    if not isinstance(parsed, list) or len(parsed) > 100:
        raise ApiError("VALIDATION_ERROR", "relatedDocIds must contain at most 100 document IDs.", status.HTTP_422_UNPROCESSABLE_CONTENT)
    try:
        document_ids = [int(item) for item in parsed]
    except (TypeError, ValueError) as exc:
        raise ApiError("VALIDATION_ERROR", "relatedDocIds contains an invalid document ID.", status.HTTP_422_UNPROCESSABLE_CONTENT) from exc
    if any(item < 1 for item in document_ids) or len(set(document_ids)) != len(document_ids):
        raise ApiError("VALIDATION_ERROR", "relatedDocIds must contain unique positive IDs.", status.HTTP_422_UNPROCESSABLE_CONTENT)
    if document_ids:
        owned_count = session.query(db().Document.id).filter(
            db().Document.user_id == user_id,
            db().Document.id.in_(document_ids),
        ).count()
        if owned_count != len(document_ids):
            raise ApiError("NOT_FOUND", "One or more related documents were not found.", status.HTTP_404_NOT_FOUND)
    return json.dumps(document_ids)


def _record_out(record) -> LabRecordOut:
    return LabRecordOut(
        id=record.id,
        title=record.title,
        content=record.content,
        relatedDocIds=record.related_doc_ids,
        aiSuggestion=record.ai_suggestion,
        createdAt=record.created_at,
        updatedAt=record.updated_at,
    )


@router.get("", response_model=Page[LabRecordOut])
def list_records(
    keyword: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, alias="pageSize", ge=1, le=100),
    session: Session = Depends(get_db_session),
    user=Depends(current_user),
) -> Page[LabRecordOut]:
    records = db().get_lab_records(session, user.id)
    if keyword:
        lowered = keyword.lower()
        records = [record for record in records if lowered in record.title.lower() or lowered in record.content.lower()]
    rows, pagination = paginate([_record_out(record) for record in records], page, page_size)
    return Page(data=rows, pagination=pagination)


@router.post("", response_model=LabRecordOut, status_code=status.HTTP_201_CREATED)
def create_record(payload: LabRecordCreate, session: Session = Depends(get_db_session), user=Depends(current_user)) -> LabRecordOut:
    related_doc_ids = _validate_related_documents(payload.related_doc_ids, session, user.id)
    record = db().create_lab_record(session, user.id, payload.title, payload.content, related_doc_ids)
    return _record_out(record)


@router.patch("/{record_id}", response_model=LabRecordOut)
def update_record(record_id: int, payload: LabRecordUpdate, session: Session = Depends(get_db_session), user=Depends(current_user)) -> LabRecordOut:
    record = session.query(db().LabRecord).filter(
        db().LabRecord.id == record_id,
        db().LabRecord.user_id == user.id,
    ).first()
    if not record:
        raise ApiError("NOT_FOUND", "Lab record not found.", status.HTTP_404_NOT_FOUND)
    changed = payload.model_fields_set
    if "title" in changed:
        record.title = payload.title
    if "content" in changed:
        record.content = payload.content
    if "related_doc_ids" in changed:
        record.related_doc_ids = _validate_related_documents(payload.related_doc_ids, session, user.id)
    if "ai_suggestion" in changed:
        record.ai_suggestion = payload.ai_suggestion
    record.updated_at = datetime.utcnow()
    session.commit()
    session.refresh(record)
    return _record_out(record)


@router.delete("/{record_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_record(record_id: int, session: Session = Depends(get_db_session), user=Depends(current_user)) -> None:
    if not db().delete_lab_record(session, user.id, record_id):
        raise ApiError("NOT_FOUND", "Lab record not found.", status.HTTP_404_NOT_FOUND)
    return None
