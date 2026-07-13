from __future__ import annotations

import logging
import math
from pathlib import Path
from typing import Any

import uuid

from fastapi import APIRouter, Depends, File, Query, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator

from app.core.config import get_settings
from app.core.dependencies import current_user
from app.core.errors import ApiError
from app.core.legacy import vasp_defaults
from app.schemas.common import paginate
from app.services.jobs import job_service
from app.services.modeling_generation import (
    is_valid_element_symbol,
    is_valid_potcar_potential_name,
    parse_structure_content,
    prepare_modeling_changes,
)


router = APIRouter(prefix="/modeling", tags=["modeling"])
logger = logging.getLogger(__name__)


class ModelingWorkspacePatch(BaseModel):
    expected_revision: int = Field(alias="expectedRevision", ge=1)
    changes: dict[str, Any]
    active_structure_id: str | None = Field(default=None, alias="activeStructureId", max_length=64)

    model_config = {"populate_by_name": True, "extra": "forbid"}

    @field_validator("changes")
    @classmethod
    def validate_changes(cls, value: dict[str, Any]) -> dict[str, Any]:
        allowed = {"incar", "kpoints", "potcarElements", "notes"}
        if set(value) - allowed:
            raise ValueError("Unsupported modeling field.")
        incar = value.get("incar")
        if incar is not None:
            if not isinstance(incar, dict) or len(incar) > 200:
                raise ValueError("INCAR changes are invalid.")
            for key, entry in incar.items():
                if not key or len(key) > 30 or not key.replace("_", "").isalnum() or key.upper() != key:
                    raise ValueError("INCAR parameter names are invalid.")
                if not isinstance(entry, dict) or "value" not in entry:
                    raise ValueError("INCAR entries must contain a value.")
                if not isinstance(entry["value"], (str, int, float, bool)):
                    raise ValueError("INCAR values must be scalar.")
                if isinstance(entry["value"], float) and not math.isfinite(entry["value"]):
                    raise ValueError("INCAR numeric values must be finite.")
                if isinstance(entry["value"], str) and len(entry["value"]) > 500:
                    raise ValueError("INCAR values are too long.")
                entry["source"] = "user"
        kpoints = value.get("kpoints")
        if kpoints is not None:
            if not isinstance(kpoints, dict) or set(kpoints) - {"mode", "mesh", "source"}:
                raise ValueError("KPOINTS changes are invalid.")
            if "mode" in kpoints and kpoints["mode"] not in {"Gamma", "Monkhorst-Pack", "Line"}:
                raise ValueError("KPOINTS mode is invalid.")
            if "mesh" in kpoints:
                mesh = kpoints["mesh"]
                if (
                    not isinstance(mesh, list)
                    or len(mesh) != 3
                    or any(type(item) is not int or not 1 <= item <= 99 for item in mesh)
                ):
                    raise ValueError("KPOINTS mesh must contain three integers between 1 and 99.")
            kpoints["source"] = "user"
        potcar_elements = value.get("potcarElements")
        if potcar_elements is not None:
            if not isinstance(potcar_elements, list) or len(potcar_elements) > 100:
                raise ValueError("POTCAR element suggestions are invalid.")
            seen: set[str] = set()
            for entry in potcar_elements:
                if not isinstance(entry, dict) or set(entry) - {"element", "potential", "source"}:
                    raise ValueError("POTCAR element suggestions are invalid.")
                element = str(entry.get("element") or "").strip()
                potential = str(entry.get("potential") or "").strip()
                if not is_valid_element_symbol(element) or element in seen:
                    raise ValueError("POTCAR element symbols are invalid or duplicated.")
                if not is_valid_potcar_potential_name(potential):
                    raise ValueError("POTCAR potential names are invalid.")
                seen.add(element)
                entry["element"] = element
                entry["potential"] = potential
                entry["source"] = "user"
        notes = value.get("notes")
        if notes is not None and (not isinstance(notes, str) or len(notes) > 12_000):
            raise ValueError("Modeling notes are invalid.")
        return value


def _workspace_out(workspace) -> dict[str, Any]:
    value = workspace.to_dict()
    value["revisions"] = job_service.science_store.list_modeling_revisions(
        user_id=workspace.user_id,
        workspace_id=workspace.id,
    )
    value["structures"] = [item.to_dict() for item in job_service.science_store.list_modeling_structures(
        user_id=workspace.user_id,
        workspace_id=workspace.id,
    )]
    value["artifacts"] = [item.to_dict() for item in job_service.science_store.list_modeling_artifacts(
        user_id=workspace.user_id,
        workspace_id=workspace.id,
    )]
    return value


@router.get("/options")
def get_options(user=Depends(current_user)) -> dict[str, Any]:
    module = vasp_defaults()
    return {
        "calcTypes": [
            {"id": key, "name": module.VASP_CALC_TYPES[key]["name"]}
            for key in module.get_calc_types()
        ],
        "vaspkitTasks": [
            {"id": key, "name": module.VASPKIT_GUIDE[key]["name"]}
            for key in module.get_vaspkit_types()
        ],
        "limits": {"manualTextChars": 60000, "structureBytes": 10 * 1024 * 1024},
    }


@router.get("/workspaces")
def list_workspaces(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, alias="pageSize", ge=1, le=100),
    user=Depends(current_user),
) -> dict[str, Any]:
    values = [_workspace_out(item) for item in job_service.science_store.list_modeling_workspaces(user_id=user.id)]
    rows, pagination = paginate(values, page, page_size)
    return {"data": rows, "pagination": pagination.model_dump(by_alias=True)}


@router.get("/workspaces/{workspace_id}")
def get_workspace(workspace_id: str, user=Depends(current_user)) -> dict[str, Any]:
    workspace = job_service.science_store.get_modeling_workspace(user_id=user.id, workspace_id=workspace_id)
    if not workspace:
        raise ApiError("NOT_FOUND", "Modeling workspace not found.", status.HTTP_404_NOT_FOUND)
    return _workspace_out(workspace)


@router.patch("/workspaces/{workspace_id}")
def patch_workspace(
    workspace_id: str,
    payload: ModelingWorkspacePatch,
    user=Depends(current_user),
) -> dict[str, Any]:
    if payload.active_structure_id:
        structure = job_service.science_store.get_modeling_structure(
            user_id=user.id,
            workspace_id=workspace_id,
            structure_id=payload.active_structure_id,
        )
        if not structure:
            raise ApiError("NOT_FOUND", "Modeling structure not found.", status.HTTP_404_NOT_FOUND)
    try:
        current = job_service.science_store.get_modeling_workspace(user_id=user.id, workspace_id=workspace_id)
        if not current:
            raise ApiError("NOT_FOUND", "Modeling workspace not found.", status.HTTP_404_NOT_FOUND)
        prepared_changes = prepare_modeling_changes(current.current_result, payload.changes, vasp_defaults())
        workspace = job_service.science_store.update_modeling_workspace(
            user_id=user.id,
            workspace_id=workspace_id,
            expected_revision=payload.expected_revision,
            changes=prepared_changes,
            active_structure_id=payload.active_structure_id,
        )
    except ValueError as exc:
        if str(exc) == "REVISION_CONFLICT":
            raise ApiError("REVISION_CONFLICT", "The workspace changed in another session.", status.HTTP_409_CONFLICT) from exc
        raise ApiError("NOT_FOUND", "Modeling workspace not found.", status.HTTP_404_NOT_FOUND) from exc
    return _workspace_out(workspace)


@router.delete("/workspaces/{workspace_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_workspace(workspace_id: str, user=Depends(current_user)) -> None:
    paths = job_service.science_store.delete_modeling_workspace(user_id=user.id, workspace_id=workspace_id)
    if paths is None:
        raise ApiError("NOT_FOUND", "Modeling workspace not found.", status.HTTP_404_NOT_FOUND)
    root = (get_settings().modeling_assets_dir / str(user.id)).resolve()
    for path in paths:
        if path.is_relative_to(root):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                logger.warning("Could not remove modeling resource %s", path.name, exc_info=True)


@router.post("/workspaces/{workspace_id}/structures", status_code=status.HTTP_201_CREATED)
async def upload_structure(
    workspace_id: str,
    file: UploadFile = File(...),
    user=Depends(current_user),
) -> dict[str, Any]:
    workspace = job_service.science_store.get_modeling_workspace(user_id=user.id, workspace_id=workspace_id)
    if not workspace:
        raise ApiError("NOT_FOUND", "Modeling workspace not found.", status.HTTP_404_NOT_FOUND)
    original_name = Path(file.filename or "").name
    suffix = Path(original_name).suffix.lower()
    if original_name.upper() == "POSCAR" or suffix == ".vasp":
        format_name = "poscar"
        stored_suffix = ".vasp"
    elif suffix == ".cif":
        format_name = "cif"
        stored_suffix = ".cif"
    else:
        raise ApiError("INVALID_STRUCTURE", "Upload a CIF, VASP, or POSCAR file.", status.HTTP_422_UNPROCESSABLE_CONTENT)
    root = (get_settings().modeling_assets_dir / str(user.id) / workspace_id / "structures").resolve()
    root.mkdir(parents=True, exist_ok=True)
    destination = root / f"{uuid.uuid4().hex}{stored_suffix}"
    temporary = destination.with_suffix(destination.suffix + ".upload")
    total = 0
    try:
        with temporary.open("wb") as handle:
            while chunk := await file.read(1024 * 1024):
                total += len(chunk)
                if total > 10 * 1024 * 1024:
                    raise ApiError("FILE_TOO_LARGE", "The structure exceeds 10 MiB.", status.HTTP_413_CONTENT_TOO_LARGE)
                handle.write(chunk)
        raw = temporary.read_bytes()
        try:
            content = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ApiError("INVALID_STRUCTURE", "The structure must be UTF-8 text.", status.HTTP_422_UNPROCESSABLE_CONTENT) from exc
        try:
            geometry, canonical_poscar = parse_structure_content(content, format_name)
        except ValueError as exc:
            raise ApiError("INVALID_STRUCTURE", str(exc), status.HTTP_422_UNPROCESSABLE_CONTENT) from exc
        temporary.replace(destination)
        if format_name == "cif":
            canonical_path = root / f"{destination.stem}.vasp"
            canonical_path.write_text(canonical_poscar, encoding="utf-8")
            destination.unlink(missing_ok=True)
            destination = canonical_path
        else:
            destination.write_text(canonical_poscar, encoding="utf-8")
        structure = job_service.science_store.register_modeling_structure(
            user_id=user.id,
            workspace_id=workspace_id,
            managed_path=destination,
            file_name=original_name,
            format=format_name,
            geometry=geometry,
            warnings=["Structure parsed successfully; scientific suitability still requires human review."],
        )
        return structure.to_dict(include_geometry=True)
    finally:
        temporary.unlink(missing_ok=True)
        await file.close()


@router.get("/workspaces/{workspace_id}/structures/{structure_id}")
def get_structure(workspace_id: str, structure_id: str, user=Depends(current_user)) -> dict[str, Any]:
    structure = job_service.science_store.get_modeling_structure(
        user_id=user.id,
        workspace_id=workspace_id,
        structure_id=structure_id,
    )
    if not structure:
        raise ApiError("NOT_FOUND", "Modeling structure not found.", status.HTTP_404_NOT_FOUND)
    return structure.to_dict(include_geometry=True)


@router.get("/workspaces/{workspace_id}/artifacts/{artifact_id}")
def get_artifact(workspace_id: str, artifact_id: str, user=Depends(current_user)):
    artifact = job_service.science_store.get_modeling_artifact(
        user_id=user.id,
        workspace_id=workspace_id,
        artifact_id=artifact_id,
    )
    if not artifact or not artifact.path.is_file():
        raise ApiError("NOT_FOUND", "Modeling artifact not found.", status.HTTP_404_NOT_FOUND)
    root = (get_settings().modeling_assets_dir / str(user.id) / workspace_id).resolve()
    if not artifact.path.is_relative_to(root) or artifact.path.is_symlink():
        raise ApiError("NOT_FOUND", "Modeling artifact not found.", status.HTTP_404_NOT_FOUND)
    return FileResponse(artifact.path, media_type=artifact.mime_type, filename=artifact.file_name)
