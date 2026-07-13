from __future__ import annotations

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.core.dependencies import current_user, get_db_session
from app.core.errors import ApiError
from app.core.legacy import db
from app.schemas.common import Page, paginate
from app.schemas.resources import GlossaryBatchInput, GlossaryBatchOut, GlossaryTermInput, GlossaryTermOut

router = APIRouter(prefix="/glossary", tags=["glossary"])


def _term_out(term) -> GlossaryTermOut:
    return GlossaryTermOut(
        id=term.id,
        enTerm=term.en_term,
        zhTerm=term.zh_term,
        note=term.note,
        createdAt=term.created_at,
    )


@router.get("", response_model=Page[GlossaryTermOut])
def list_terms(
    keyword: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, alias="pageSize", ge=1, le=100),
    session: Session = Depends(get_db_session),
    user=Depends(current_user),
) -> Page[GlossaryTermOut]:
    terms = db().get_user_glossary(session, user.id)
    if keyword:
        lowered = keyword.lower()
        terms = [
            term
            for term in terms
            if lowered in term.en_term.lower()
            or lowered in term.zh_term.lower()
            or lowered in (term.note or "").lower()
        ]
    rows, pagination = paginate([_term_out(term) for term in terms], page, page_size)
    return Page(data=rows, pagination=pagination)


@router.post("", response_model=GlossaryTermOut, status_code=status.HTTP_201_CREATED)
def upsert_term(payload: GlossaryTermInput, session: Session = Depends(get_db_session), user=Depends(current_user)) -> GlossaryTermOut:
    term = db().upsert_glossary_term(session, user.id, payload.en_term.strip(), payload.zh_term.strip(), payload.note)
    return _term_out(term)


@router.post("/batch", response_model=GlossaryBatchOut)
def upsert_terms_batch(
    payload: GlossaryBatchInput,
    session: Session = Depends(get_db_session),
    user=Depends(current_user),
) -> GlossaryBatchOut:
    terms = [
        db().upsert_glossary_term(
            session,
            user.id,
            item.en_term.strip(),
            item.zh_term.strip(),
            item.note,
        )
        for item in payload.terms
    ]
    rows = [_term_out(term) for term in terms]
    return GlossaryBatchOut(data=rows, count=len(rows))


@router.delete("/{term_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_term(term_id: int, session: Session = Depends(get_db_session), user=Depends(current_user)) -> None:
    if not db().delete_glossary_term(session, user.id, term_id):
        raise ApiError("NOT_FOUND", "Glossary term not found.", status.HTTP_404_NOT_FOUND)
    return None
