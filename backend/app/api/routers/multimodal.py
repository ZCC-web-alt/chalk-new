from __future__ import annotations

import logging
import math
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Query, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator, model_validator

from app.core.config import get_settings
from app.core.dependencies import current_user
from app.core.errors import ApiError
from app.schemas.common import paginate
from app.services.jobs import job_service
from app.services.science_files import (
    InvalidScienceFile,
    inspect_multimodal_file,
    read_table_preview,
    sha256_file,
)


router = APIRouter(prefix="/multimodal", tags=["multimodal"])
logger = logging.getLogger(__name__)


class MultimodalDataPointCorrection(BaseModel):
    parameter: str = Field(min_length=1, max_length=240)
    parameter_cn: str | None = Field(default=None, alias="parameterCn", max_length=240)
    value: str | int | float | None
    unit: str = Field(default="", max_length=80)
    original_value: str | None = Field(default=None, alias="originalValue", max_length=500)
    original_unit: str | None = Field(default=None, alias="originalUnit", max_length=80)
    source: str | None = Field(default=None, max_length=500)
    is_suspicious: bool | None = Field(default=None, alias="isSuspicious")
    suspicious_reason: str | None = Field(default=None, alias="suspiciousReason", max_length=2000)
    precision: int | None = Field(default=None, ge=0, le=20)
    note: str | None = Field(default=None, max_length=4000)

    model_config = {"populate_by_name": True, "extra": "forbid"}

    @field_validator("value")
    @classmethod
    def validate_value(cls, value: str | int | float | None):
        if isinstance(value, bool):
            raise ValueError("Data-point values cannot be boolean.")
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("Data-point values must be finite.")
        if isinstance(value, str) and len(value) > 500:
            raise ValueError("Data-point values are too long.")
        return value


class MultimodalAnnotationCorrection(BaseModel):
    id: str = Field(min_length=1, max_length=100)
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    width: float = Field(gt=0, le=1)
    height: float = Field(gt=0, le=1)
    note: str = Field(default="", max_length=4000)
    data_point_index: int | None = Field(default=None, alias="dataPointIndex", ge=0)

    model_config = {"populate_by_name": True, "extra": "forbid"}

    @model_validator(mode="after")
    def validate_bounds(self):
        if self.x + self.width > 1 or self.y + self.height > 1:
            raise ValueError("Annotation bounds must stay inside the source image.")
        return self


class MultimodalItemCorrection(BaseModel):
    item_index: int = Field(alias="itemIndex", ge=0)
    data_points: list[MultimodalDataPointCorrection] | None = Field(
        default=None,
        alias="dataPoints",
        max_length=5000,
    )
    annotations: list[MultimodalAnnotationCorrection] | None = Field(default=None, max_length=500)

    model_config = {"populate_by_name": True, "extra": "forbid"}

    @model_validator(mode="after")
    def validate_change(self):
        if self.data_points is None and self.annotations is None:
            raise ValueError("An item correction must change data points or annotations.")
        return self


class MultimodalCorrectionSet(BaseModel):
    item_corrections: list[MultimodalItemCorrection] = Field(
        alias="itemCorrections",
        min_length=1,
        max_length=50,
    )

    model_config = {"populate_by_name": True, "extra": "forbid"}

    @field_validator("item_corrections")
    @classmethod
    def validate_unique_items(cls, values: list[MultimodalItemCorrection]):
        indices = [value.item_index for value in values]
        if len(indices) != len(set(indices)):
            raise ValueError("Each run item may be corrected once per request.")
        return values


class MultimodalRunPatch(BaseModel):
    expected_revision: int = Field(alias="expectedRevision", ge=1)
    changes: MultimodalCorrectionSet

    model_config = {"populate_by_name": True, "extra": "forbid"}

    @model_validator(mode="after")
    def validate_size(self):
        encoded = self.changes.model_dump_json(by_alias=True).encode("utf-8")
        if len(encoded) > 500_000:
            raise ValueError("The correction payload is too large.")
        return self


@router.post("/assets", status_code=status.HTTP_201_CREATED)
async def upload_assets(
    files: list[UploadFile] = File(...),
    user=Depends(current_user),
) -> dict[str, Any]:
    settings = get_settings()
    if not files or len(files) > 20:
        raise ApiError("VALIDATION_ERROR", "Upload between 1 and 20 files.", status.HTTP_422_UNPROCESSABLE_CONTENT)
    user_root = (settings.multimodal_assets_dir / str(user.id)).resolve()
    user_root.mkdir(parents=True, exist_ok=True)
    stored = []
    created_paths: list[Path] = []
    registered_ids: list[str] = []
    try:
        for upload in files:
            original_name = Path(upload.filename or "").name
            if not original_name:
                raise ApiError("INVALID_MULTIMODAL_FILE", "The uploaded file has no name.", status.HTTP_422_UNPROCESSABLE_CONTENT)
            suffix = Path(original_name).suffix.lower()
            destination = (user_root / f"{uuid.uuid4().hex}{suffix}").resolve()
            temporary = destination.with_suffix(destination.suffix + ".upload")
            total = 0
            try:
                with temporary.open("wb") as handle:
                    while chunk := await upload.read(1024 * 1024):
                        total += len(chunk)
                        if total > settings.max_upload_bytes:
                            raise ApiError("FILE_TOO_LARGE", "A file exceeds the configured upload limit.", status.HTTP_413_CONTENT_TOO_LARGE)
                        handle.write(chunk)
                temporary.replace(destination)
                created_paths.append(destination)
                asset_type, mime_type, metadata = inspect_multimodal_file(destination, original_name)
                client_type = (upload.content_type or "application/octet-stream").lower()
                accepted_client_types = {
                    mime_type.lower(),
                    "application/octet-stream",
                    "image/jpg" if mime_type == "image/jpeg" else mime_type.lower(),
                    "application/csv" if mime_type == "text/csv" else mime_type.lower(),
                }
                if client_type not in accepted_client_types:
                    raise InvalidScienceFile("The declared MIME type does not match the uploaded content.")
                asset = job_service.science_store.create_multimodal_asset(
                    user_id=user.id,
                    managed_path=destination,
                    file_name=original_name,
                    mime_type=mime_type,
                    asset_type=asset_type,
                    metadata={**metadata, "sizeBytes": total},
                    sha256=sha256_file(destination),
                )
                registered_ids.append(asset.id)
                stored.append(asset.to_dict())
            except InvalidScienceFile as exc:
                temporary.unlink(missing_ok=True)
                destination.unlink(missing_ok=True)
                raise ApiError("INVALID_MULTIMODAL_FILE", str(exc), status.HTTP_422_UNPROCESSABLE_CONTENT) from exc
            finally:
                await upload.close()
        return {"data": stored}
    except Exception:
        for asset_id in registered_ids:
            job_service.science_store.delete_multimodal_asset(user_id=user.id, asset_id=asset_id)
        for path in created_paths:
            path.unlink(missing_ok=True)
        raise


@router.get("/assets")
def list_assets(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, alias="pageSize", ge=1, le=100),
    user=Depends(current_user),
) -> dict[str, Any]:
    assets = [asset.to_dict() for asset in job_service.science_store.list_multimodal_assets(user_id=user.id)]
    rows, pagination = paginate(assets, page, page_size)
    return {"data": rows, "pagination": pagination.model_dump(by_alias=True)}


@router.get("/assets/{asset_id}/content")
def get_asset_content(
    asset_id: str,
    sheet_name: str | None = Query(default=None, alias="sheetName", max_length=200),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, alias="pageSize", ge=1, le=100),
    user=Depends(current_user),
):
    asset = job_service.science_store.get_multimodal_asset(user_id=user.id, asset_id=asset_id)
    if not asset or not asset.path.is_file():
        raise ApiError("NOT_FOUND", "Multimodal asset not found.", status.HTTP_404_NOT_FOUND)
    root = (get_settings().multimodal_assets_dir / str(user.id)).resolve()
    if not asset.path.is_relative_to(root) or asset.path.is_symlink():
        raise ApiError("NOT_FOUND", "Multimodal asset not found.", status.HTTP_404_NOT_FOUND)
    if asset.asset_type == "workbook":
        try:
            preview = read_table_preview(
                asset.path,
                metadata=asset.metadata,
                sheet_name=sheet_name,
                page=page,
                page_size=page_size,
            )
        except InvalidScienceFile as exc:
            raise ApiError("INVALID_MULTIMODAL_FILE", str(exc), status.HTTP_422_UNPROCESSABLE_CONTENT) from exc
        return {"assetId": asset.id, **preview}
    return FileResponse(asset.path, media_type=asset.mime_type, filename=asset.file_name)


@router.delete("/assets/{asset_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_asset(asset_id: str, user=Depends(current_user)) -> None:
    path = job_service.science_store.delete_multimodal_asset(user_id=user.id, asset_id=asset_id)
    if path is None:
        raise ApiError("NOT_FOUND", "Multimodal asset not found.", status.HTTP_404_NOT_FOUND)
    root = (get_settings().multimodal_assets_dir / str(user.id)).resolve()
    if path.is_relative_to(root):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            logger.warning("Could not remove multimodal asset %s", asset_id, exc_info=True)


@router.get("/runs")
def list_runs(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, alias="pageSize", ge=1, le=100),
    user=Depends(current_user),
) -> dict[str, Any]:
    values = [run.to_dict() for run in job_service.science_store.list_multimodal_runs(user_id=user.id)]
    rows, pagination = paginate(values, page, page_size)
    return {"data": rows, "pagination": pagination.model_dump(by_alias=True)}


@router.get("/runs/{run_id}")
def get_run(run_id: str, user=Depends(current_user)) -> dict[str, Any]:
    run = job_service.science_store.get_multimodal_run(user_id=user.id, run_id=run_id)
    if not run:
        raise ApiError("NOT_FOUND", "Multimodal run not found.", status.HTTP_404_NOT_FOUND)
    return run.to_dict(include_original=True)


@router.patch("/runs/{run_id}")
def patch_run(run_id: str, payload: MultimodalRunPatch, user=Depends(current_user)) -> dict[str, Any]:
    try:
        run = job_service.revise_multimodal_run(
            user_id=user.id,
            run_id=run_id,
            expected_revision=payload.expected_revision,
            corrections=payload.changes.model_dump(by_alias=True, exclude_none=True),
        )
    except ValueError as exc:
        if str(exc) == "REVISION_CONFLICT":
            raise ApiError("REVISION_CONFLICT", "The multimodal run changed in another session.", status.HTTP_409_CONFLICT) from exc
        if str(exc) == "INVALID_CORRECTION":
            raise ApiError("INVALID_CORRECTION", "The correction does not match a run item.", status.HTTP_422_UNPROCESSABLE_CONTENT) from exc
        raise ApiError("NOT_FOUND", "Multimodal run not found.", status.HTTP_404_NOT_FOUND) from exc
    return run.to_dict(include_original=True)
