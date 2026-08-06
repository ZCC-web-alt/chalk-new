from __future__ import annotations

from fastapi import APIRouter, Depends, status

from app.core.dependencies import current_user
from app.core.errors import ApiError
from app.schemas.science125 import (
    Science125QuestionDetailOut,
    Science125QuestionListOut,
    Science125QuestionProfileOut,
)
from app.services.science125_catalog import (
    Science125CatalogError,
    Science125RoutingError,
    get_science125_catalog,
    get_science125_question_profile,
)
from app.services.science125_context import (
    Science125ContextError,
    load_science125_context_index,
)
from app.services import api_keys


router = APIRouter(prefix="/science-125", tags=["science-125"])


@router.get("/questions", response_model=Science125QuestionListOut)
def list_questions(_user=Depends(current_user)) -> Science125QuestionListOut:
    try:
        return get_science125_catalog()
    except Science125CatalogError as exc:
        raise ApiError(
            "SCIENCE125_CATALOG_UNAVAILABLE",
            "The Science 125 question catalog is temporarily unavailable.",
            status.HTTP_503_SERVICE_UNAVAILABLE,
        ) from exc


@router.get("/questions/{question_id}/profile", response_model=Science125QuestionProfileOut)
def get_question_profile(
    question_id: str,
    user=Depends(current_user),
) -> Science125QuestionProfileOut:
    try:
        catalog = get_science125_catalog()
    except Science125CatalogError as exc:
        raise ApiError(
            "SCIENCE125_CATALOG_UNAVAILABLE",
            "The Science 125 question catalog is temporarily unavailable.",
            status.HTTP_503_SERVICE_UNAVAILABLE,
        ) from exc
    if not any(item.id == question_id for item in catalog.data):
        raise ApiError(
            "NOT_FOUND",
            "Science 125 question not found.",
            status.HTTP_404_NOT_FOUND,
        )
    try:
        return get_science125_question_profile(
            question_id,
            environ=api_keys.api_key_store.science125_environment(user.id),
        )
    except Science125RoutingError as exc:
        raise ApiError(
            "SCIENCE125_ROUTING_UNAVAILABLE",
            "The Science 125 routing profile is temporarily unavailable.",
            status.HTTP_503_SERVICE_UNAVAILABLE,
        ) from exc


@router.get("/questions/{question_id}", response_model=Science125QuestionDetailOut)
def get_question_detail(
    question_id: str,
    _user=Depends(current_user),
) -> Science125QuestionDetailOut:
    try:
        catalog = get_science125_catalog()
    except Science125CatalogError as exc:
        raise ApiError(
            "SCIENCE125_CATALOG_UNAVAILABLE",
            "The Science 125 question catalog is temporarily unavailable.",
            status.HTTP_503_SERVICE_UNAVAILABLE,
        ) from exc
    catalog_item = next((item for item in catalog.data if item.id == question_id), None)
    if catalog_item is None:
        raise ApiError(
            "NOT_FOUND",
            "Science 125 question not found.",
            status.HTTP_404_NOT_FOUND,
        )
    try:
        index = load_science125_context_index()
        item = index.items[question_id]
    except (Science125ContextError, KeyError) as exc:
        raise ApiError(
            "SCIENCE125_CONTEXT_UNAVAILABLE",
            "The Science 125 source context is temporarily unavailable.",
            status.HTTP_503_SERVICE_UNAVAILABLE,
        ) from exc
    return Science125QuestionDetailOut(
        id=item.id,
        headline=item.headline,
        headlineZh=catalog_item.question_zh,
        sourceContext=item.source_context,
        contextSha256=item.context_sha256,
        pdfPage=item.pdf_page,
        bookletPage=item.booklet_page,
        extractionVersion=index.extraction_version,
        availability="available",
    )
