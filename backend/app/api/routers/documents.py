from __future__ import annotations

import logging
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.dependencies import current_user, get_db_session
from app.core.errors import ApiError
from app.core.legacy import db
from app.schemas.common import Page, paginate
from app.schemas.documents import DocumentPageExcerptInput, DocumentPageExcerptOut
from app.schemas.jobs import JobOut
from app.schemas.resources import DocumentOut
from app.services.document_excerpts import (
    DocumentPageExcerptError,
    extract_document_page_excerpt,
)
from app.services.jobs import job_service

router = APIRouter(prefix="/documents", tags=["documents"])
logger = logging.getLogger(__name__)

ALLOWED_PDF_CONTENT_TYPES = {"application/pdf", "application/octet-stream"}


def _document_out(doc) -> DocumentOut:
    file_name = Path(doc.source_path).name if doc.source_path else None
    return DocumentOut(
        id=doc.id,
        title=doc.title,
        sourceType=doc.source_type,
        fileName=file_name,
        summary=doc.summary,
        createdAt=doc.created_at,
    )


def _managed_upload_path(source_path: str | None, user_id: int) -> Path | None:
    if not source_path:
        return None
    settings = get_settings()
    user_root = (settings.uploads_dir / str(user_id)).resolve()
    candidate = Path(source_path).resolve()
    return candidate if candidate.is_relative_to(user_root) else None


@router.get("", response_model=Page[DocumentOut])
def list_documents(
    keyword: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, alias="pageSize", ge=1, le=100),
    session: Session = Depends(get_db_session),
    user=Depends(current_user),
) -> Page[DocumentOut]:
    docs = db().search_documents(session, user.id, keyword=keyword)
    rows, pagination = paginate([_document_out(doc) for doc in docs], page, page_size)
    return Page(data=rows, pagination=pagination)


@router.post("/import", response_model=JobOut, status_code=status.HTTP_202_ACCEPTED)
async def import_pdf(
    file: UploadFile = File(...),
    title: str | None = Form(default=None),
    user=Depends(current_user),
) -> JobOut:
    settings = get_settings()
    original_name = Path(file.filename or "").name
    if not original_name or Path(original_name).suffix.lower() != ".pdf":
        raise ApiError("INVALID_PDF", "Only PDF files can be imported.", status.HTTP_422_UNPROCESSABLE_CONTENT)
    if file.content_type not in ALLOWED_PDF_CONTENT_TYPES:
        raise ApiError("INVALID_PDF", "The uploaded file is not a PDF.", status.HTTP_422_UNPROCESSABLE_CONTENT)
    clean_title = (title or "").strip()
    if len(clean_title) > 255:
        raise ApiError("VALIDATION_ERROR", "Title must be 255 characters or fewer.", status.HTTP_422_UNPROCESSABLE_CONTENT)

    user_dir = (settings.uploads_dir / str(user.id)).resolve()
    user_dir.mkdir(parents=True, exist_ok=True)
    destination = user_dir / f"{uuid.uuid4()}.pdf"
    temporary = destination.with_suffix(".upload")
    job_created = False
    total = 0
    header = b""
    try:
        with temporary.open("wb") as handle:
            while chunk := await file.read(1024 * 1024):
                total += len(chunk)
                if total > settings.max_upload_bytes:
                    raise ApiError(
                        "FILE_TOO_LARGE",
                        "The PDF exceeds the configured upload limit.",
                        status.HTTP_413_CONTENT_TOO_LARGE,
                    )
                if not header:
                    header = chunk[:5]
                handle.write(chunk)
        if header != b"%PDF-":
            raise ApiError("INVALID_PDF", "The uploaded file does not contain a valid PDF header.", status.HTTP_422_UNPROCESSABLE_CONTENT)
        temporary.replace(destination)
        job = job_service.create(
            user.id,
            "pdf_import",
            {
                "storedPath": str(destination),
                "originalFilename": original_name,
                "title": clean_title,
            },
        )
        job_created = True
        return JobOut(**job.to_dict())
    except Exception:
        temporary.unlink(missing_ok=True)
        if not job_created:
            destination.unlink(missing_ok=True)
        raise
    finally:
        await file.close()


@router.post("/{document_id}/reimport", response_model=JobOut, status_code=status.HTTP_202_ACCEPTED)
async def reimport_pdf(
    document_id: int,
    file: UploadFile = File(...),
    session: Session = Depends(get_db_session),
    user=Depends(current_user),
) -> JobOut:
    owned = session.query(db().Document.id).filter(
        db().Document.id == document_id,
        db().Document.user_id == user.id,
    ).first()
    if not owned:
        raise ApiError("NOT_FOUND", "Document not found.", status.HTTP_404_NOT_FOUND)
    original_name = Path(file.filename or "").name
    if not original_name or Path(original_name).suffix.lower() != ".pdf" or file.content_type not in ALLOWED_PDF_CONTENT_TYPES:
        raise ApiError("INVALID_PDF", "Only PDF files can be imported.", status.HTTP_422_UNPROCESSABLE_CONTENT)

    settings = get_settings()
    user_dir = (settings.uploads_dir / str(user.id)).resolve()
    user_dir.mkdir(parents=True, exist_ok=True)
    destination = user_dir / f"{uuid.uuid4()}.pdf"
    temporary = destination.with_suffix(".upload")
    total = 0
    header = b""
    job_created = False
    try:
        with temporary.open("wb") as handle:
            while chunk := await file.read(1024 * 1024):
                total += len(chunk)
                if total > settings.max_upload_bytes:
                    raise ApiError("FILE_TOO_LARGE", "The PDF exceeds the configured upload limit.", status.HTTP_413_CONTENT_TOO_LARGE)
                if not header:
                    header = chunk[:5]
                handle.write(chunk)
        if header != b"%PDF-":
            raise ApiError("INVALID_PDF", "The uploaded file does not contain a valid PDF header.", status.HTTP_422_UNPROCESSABLE_CONTENT)
        temporary.replace(destination)
        job = job_service.create(user.id, "pdf_reimport", {
            "documentId": document_id,
            "storedPath": str(destination),
            "originalFilename": original_name,
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


@router.get("/{document_id}", response_model=DocumentOut)
def get_document(document_id: int, session: Session = Depends(get_db_session), user=Depends(current_user)) -> DocumentOut:
    doc = session.query(db().Document).filter(db().Document.id == document_id, db().Document.user_id == user.id).first()
    if not doc:
        raise ApiError("NOT_FOUND", "Document not found.", status.HTTP_404_NOT_FOUND)
    return _document_out(doc)


@router.get("/{document_id}/content", response_class=FileResponse)
def get_document_content(
    document_id: int,
    session: Session = Depends(get_db_session),
    user=Depends(current_user),
) -> FileResponse:
    document = session.query(db().Document).filter(
        db().Document.id == document_id,
        db().Document.user_id == user.id,
    ).first()
    if not document:
        raise ApiError("NOT_FOUND", "Document not found.", status.HTTP_404_NOT_FOUND)
    source = Path(document.source_path or "").resolve()
    if document.source_type != "pdf" or source.suffix.lower() != ".pdf" or not source.is_file() or source.is_symlink():
        raise ApiError("CONTENT_NOT_AVAILABLE", "The PDF source is not available.", status.HTTP_404_NOT_FOUND)
    try:
        with source.open("rb") as handle:
            header = handle.read(5)
        if header != b"%PDF-":
            raise ApiError("CONTENT_NOT_AVAILABLE", "The PDF source is not available.", status.HTTP_404_NOT_FOUND)
    except OSError as exc:
        raise ApiError("CONTENT_NOT_AVAILABLE", "The PDF source is not available.", status.HTTP_404_NOT_FOUND) from exc
    safe_name = Path(document.source_path).name
    return FileResponse(source, media_type="application/pdf", filename=safe_name, content_disposition_type="inline")


@router.post("/{document_id}/page-excerpts", response_model=DocumentPageExcerptOut)
def create_page_excerpt(
    document_id: int,
    payload: DocumentPageExcerptInput,
    session: Session = Depends(get_db_session),
    user=Depends(current_user),
) -> DocumentPageExcerptOut:
    document = session.query(db().Document).filter(
        db().Document.id == document_id,
        db().Document.user_id == user.id,
    ).first()
    if not document:
        raise ApiError("NOT_FOUND", "Document not found.", status.HTTP_404_NOT_FOUND)
    if document.source_type != "pdf":
        raise ApiError(
            "EXCERPT_SOURCE_UNAVAILABLE",
            "The document does not have an available PDF source.",
            status.HTTP_422_UNPROCESSABLE_CONTENT,
        )

    try:
        excerpt = extract_document_page_excerpt(
            document.source_path,
            pages=payload.requested_pages(),
            max_chars=payload.max_chars,
            allowed_root=(get_settings().uploads_dir / str(user.id)).resolve(),
        )
    except DocumentPageExcerptError as exc:
        raise ApiError(
            exc.code,
            exc.message,
            status.HTTP_422_UNPROCESSABLE_CONTENT,
        ) from exc

    return DocumentPageExcerptOut(
        documentId=document.id,
        title=document.title,
        pages=excerpt.pages,
        text=excerpt.text,
        hash=excerpt.text_sha256,
        provenance={
            "sourceType": "pdf",
            "pdfSha256": excerpt.pdf_sha256,
            "extractor": "PyMuPDF",
            "pageCount": excerpt.page_count,
            "maxChars": excerpt.max_chars,
            "originalCharCount": excerpt.original_char_count,
            "returnedCharCount": len(excerpt.text),
            "truncated": excerpt.truncated,
        },
    )


@router.get("/{document_id}/multimodal-sources")
def list_multimodal_sources(
    document_id: int,
    session: Session = Depends(get_db_session),
    user=Depends(current_user),
) -> dict:
    document = session.query(db().Document).filter(
        db().Document.id == document_id,
        db().Document.user_id == user.id,
    ).first()
    if not document:
        raise ApiError("NOT_FOUND", "Document not found.", status.HTTP_404_NOT_FOUND)
    assets = job_service.analysis_store.list_assets_for_document(user.id, document_id)
    return {
        "data": [{
            "id": asset.id,
            "documentId": document_id,
            "fileName": asset.file_name,
            "mimeType": asset.mime_type,
            "analysisType": asset.analysis_type,
            "contentUrl": f"/documents/{document_id}/assets/{asset.id}",
            "createdAt": asset.created_at,
        } for asset in assets if asset.mime_type.startswith("image/")],
        "extractionStatus": "ready" if assets else "empty",
    }


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_document(
    document_id: int,
    session: Session = Depends(get_db_session),
    user=Depends(current_user),
) -> None:
    database = db()
    document = session.query(database.Document).filter(
        database.Document.id == document_id,
        database.Document.user_id == user.id,
    ).first()
    if not document:
        raise ApiError("NOT_FOUND", "Document not found.", status.HTTP_404_NOT_FOUND)
    managed_file = _managed_upload_path(document.source_path, user.id)
    try:
        session.query(database.DocumentChunk).filter(
            database.DocumentChunk.document_id == document.id,
            database.DocumentChunk.user_id == user.id,
        ).delete(synchronize_session=False)
        session.delete(document)
        session.commit()
    except Exception:
        session.rollback()
        raise
    if managed_file:
        try:
            managed_file.unlink(missing_ok=True)
        except OSError:
            logger.warning("Could not delete managed upload for document %s", document_id, exc_info=True)
    try:
        asset_paths = job_service.analysis_store.delete_for_document(user.id, document_id)
        assets_root = job_service.assets_dir.resolve()
        for asset_path in asset_paths:
            resolved = Path(asset_path).resolve()
            if resolved.is_relative_to(assets_root):
                resolved.unlink(missing_ok=True)
                parent = resolved.parent
                while parent != assets_root and parent.is_relative_to(assets_root):
                    try:
                        parent.rmdir()
                    except OSError:
                        break
                    parent = parent.parent
    except Exception:
        logger.warning("Could not delete analysis data for document %s", document_id, exc_info=True)
