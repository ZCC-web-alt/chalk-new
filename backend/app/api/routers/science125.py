from __future__ import annotations

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import FileResponse

from app.core.dependencies import current_user
from app.core.errors import ApiError
from app.schemas.science125 import (
    Science125BatchCreateInput,
    Science125BatchExportInput,
    Science125BatchOut,
    Science125BatchRetryInput,
    Science125BatchSummaryOut,
    Science125ExportOut,
    Science125QuestionDetailOut,
    Science125QuestionListOut,
    Science125QuestionProfileOut,
    Science125ReportExportInput,
    Science125ReportOut,
    Science125ReportSummaryOut,
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
from app.services.science125_report_service import (
    Science125ReportError,
    get_science125_report_service,
)


router = APIRouter(prefix="/science-125", tags=["science-125"])


def _report_service():
    return get_science125_report_service()


def _raise_science125_error(exc: Science125ReportError) -> None:
    status_code = {
        "NOT_FOUND": status.HTTP_404_NOT_FOUND,
        "BATCH_FINAL_DOCX_NOT_READY": status.HTTP_409_CONFLICT,
        "REPORT_NOT_SUCCEEDED": status.HTTP_409_CONFLICT,
        "SCIENCE125_BATCH_DELETE_CONFLICT": status.HTTP_409_CONFLICT,
        "UNSUPPORTED_EXPORT_FORMAT": status.HTTP_422_UNPROCESSABLE_CONTENT,
        "DASHSCOPE_API_KEY_REQUIRED": status.HTTP_409_CONFLICT,
        "SCIENCE125_MODEL_PRICING_REQUIRED": status.HTTP_409_CONFLICT,
    }.get(exc.code, status.HTTP_422_UNPROCESSABLE_CONTENT)
    raise ApiError(exc.code, exc.message, status_code)


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


@router.post("/batches", response_model=Science125BatchOut, status_code=status.HTTP_201_CREATED)
def create_batch(
    payload: Science125BatchCreateInput,
    user=Depends(current_user),
) -> Science125BatchOut:
    try:
        batch = _report_service().create_batch(user_id=user.id, question_ids=payload.question_ids)
        out = _report_service().batch_out(user_id=user.id, batch_id=batch.id)
    except ValueError as exc:
        raise ApiError(
            "SCIENCE125_BATCH_INVALID",
            str(exc),
            status.HTTP_422_UNPROCESSABLE_CONTENT,
        ) from exc
    if out is None:
        raise ApiError("NOT_FOUND", "Science 125 batch not found.", status.HTTP_404_NOT_FOUND)
    return Science125BatchOut.model_validate(out)


@router.get("/batches", response_model=list[Science125BatchSummaryOut])
def list_batches(user=Depends(current_user)) -> list[Science125BatchSummaryOut]:
    payloads = []
    for batch in _report_service().list_batches(user_id=user.id):
        payload = batch.to_dict()
        payload.pop("questionIds", None)
        payload.pop("reports", None)
        payloads.append(Science125BatchSummaryOut.model_validate(payload))
    return payloads


@router.get("/batches/{batch_id}", response_model=Science125BatchOut)
def get_batch(batch_id: str, user=Depends(current_user)) -> Science125BatchOut:
    out = _report_service().batch_out(user_id=user.id, batch_id=batch_id)
    if out is None:
        raise ApiError("NOT_FOUND", "Science 125 batch not found.", status.HTTP_404_NOT_FOUND)
    return Science125BatchOut.model_validate(out)


@router.delete("/batches/{batch_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_batch(batch_id: str, user=Depends(current_user)) -> None:
    try:
        batch = _report_service().delete_batch(user_id=user.id, batch_id=batch_id)
    except Science125ReportError as exc:
        _raise_science125_error(exc)
    if batch is None:
        raise ApiError("NOT_FOUND", "Science 125 batch not found.", status.HTTP_404_NOT_FOUND)


@router.post("/batches/{batch_id}/pause", response_model=Science125BatchOut)
def pause_batch(batch_id: str, user=Depends(current_user)) -> Science125BatchOut:
    batch = _report_service().pause_batch(user_id=user.id, batch_id=batch_id)
    if batch is None:
        raise ApiError("NOT_FOUND", "Science 125 batch not found.", status.HTTP_404_NOT_FOUND)
    out = _report_service().batch_out(user_id=user.id, batch_id=batch_id)
    return Science125BatchOut.model_validate(out)


@router.post("/batches/{batch_id}/resume", response_model=Science125BatchOut)
def resume_batch(batch_id: str, user=Depends(current_user)) -> Science125BatchOut:
    batch = _report_service().resume_batch(user_id=user.id, batch_id=batch_id)
    if batch is None:
        raise ApiError("NOT_FOUND", "Science 125 batch not found.", status.HTTP_404_NOT_FOUND)
    out = _report_service().batch_out(user_id=user.id, batch_id=batch_id)
    return Science125BatchOut.model_validate(out)


@router.post("/batches/{batch_id}/retries", response_model=Science125BatchOut)
def retry_batch(
    batch_id: str,
    payload: Science125BatchRetryInput,
    user=Depends(current_user),
) -> Science125BatchOut:
    batch = _report_service().retry_batch(
        user_id=user.id,
        batch_id=batch_id,
        question_ids=payload.question_ids,
    )
    if batch is None:
        raise ApiError("NOT_FOUND", "Science 125 batch not found.", status.HTTP_404_NOT_FOUND)
    out = _report_service().batch_out(user_id=user.id, batch_id=batch_id)
    return Science125BatchOut.model_validate(out)


@router.get("/reports", response_model=list[Science125ReportSummaryOut])
def list_reports(
    batch_id: str | None = Query(default=None, alias="batchId"),
    question_id: str | None = Query(default=None, alias="questionId"),
    report_status: str | None = Query(default=None, alias="status"),
    benchmark_domain: str | None = Query(default=None, alias="benchmarkDomain"),
    sort_by: str = Query(default="updatedAt", alias="sortBy"),
    sort_order: str = Query(default="desc", alias="sortOrder"),
    user=Depends(current_user),
) -> list[Science125ReportSummaryOut]:
    reports = _report_service().list_reports(
        user_id=user.id,
        batch_id=batch_id,
        question_id=question_id,
        status=report_status,
        benchmark_domain=benchmark_domain,
        sort_by=sort_by,
        sort_order=sort_order,
    )
    return [Science125ReportSummaryOut.model_validate(report.to_summary_dict()) for report in reports]


@router.get("/reports/by-question/{question_id}", response_model=list[Science125ReportSummaryOut])
def list_reports_by_question(
    question_id: str,
    user=Depends(current_user),
) -> list[Science125ReportSummaryOut]:
    return [
        Science125ReportSummaryOut.model_validate(report.to_summary_dict())
        for report in _report_service().list_reports_by_question(user_id=user.id, question_id=question_id)
    ]


@router.get("/reports/{report_id}", response_model=Science125ReportOut)
def get_report(report_id: str, user=Depends(current_user)) -> Science125ReportOut:
    out = _report_service().report_out(user_id=user.id, report_id=report_id)
    if out is None:
        raise ApiError("NOT_FOUND", "Science 125 report not found.", status.HTTP_404_NOT_FOUND)
    return Science125ReportOut.model_validate(out)


@router.post("/reports/{report_id}/exports", response_model=Science125ExportOut, status_code=status.HTTP_201_CREATED)
def create_report_export(
    report_id: str,
    payload: Science125ReportExportInput,
    user=Depends(current_user),
) -> Science125ExportOut:
    try:
        export = _report_service().create_report_export(
            user_id=user.id,
            report_id=report_id,
            format=payload.format,
        )
    except Science125ReportError as exc:
        _raise_science125_error(exc)
    return Science125ExportOut.model_validate(export.to_dict())


@router.post("/batches/{batch_id}/exports", response_model=Science125ExportOut, status_code=status.HTTP_201_CREATED)
def create_batch_export(
    batch_id: str,
    payload: Science125BatchExportInput,
    user=Depends(current_user),
) -> Science125ExportOut:
    try:
        export = _report_service().create_batch_export(
            user_id=user.id,
            batch_id=batch_id,
            format=payload.format,
        )
    except Science125ReportError as exc:
        _raise_science125_error(exc)
    return Science125ExportOut.model_validate(export.to_dict())


@router.get("/exports/{export_id}")
def download_export(export_id: str, user=Depends(current_user)) -> FileResponse:
    try:
        export = _report_service().get_export(user_id=user.id, export_id=export_id)
    except Science125ReportError as exc:
        _raise_science125_error(exc)
    if export is None or not export.path.is_file():
        raise ApiError("NOT_FOUND", "Science 125 export not found.", status.HTTP_404_NOT_FOUND)
    return FileResponse(
        export.path,
        filename=export.file_name,
        media_type=export.mime_type,
    )
