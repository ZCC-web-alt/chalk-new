from __future__ import annotations

import json
import uuid
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, create_engine, delete, select
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker


def utc_now() -> datetime:
    return datetime.now(UTC)


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _load(value: str | None, fallback: Any) -> Any:
    if not value:
        return deepcopy(fallback)
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return deepcopy(fallback)


def _deep_merge(original: Any, changes: Any) -> Any:
    if not isinstance(original, dict) or not isinstance(changes, dict):
        return deepcopy(changes)
    merged = deepcopy(original)
    for key, value in changes.items():
        merged[key] = _deep_merge(merged.get(key), value) if key in merged else deepcopy(value)
    return merged


def _diff(before: Any, after: Any, path: str = "") -> list[dict[str, Any]]:
    if isinstance(before, dict) and isinstance(after, dict):
        changes: list[dict[str, Any]] = []
        keys = list(before) + [key for key in after if key not in before]
        for key in keys:
            child = f"{path}.{key}" if path else str(key)
            changes.extend(_diff(before.get(key), after.get(key), child))
        return changes
    if before == after:
        return []
    return [{"path": path or "$", "before": before, "after": after}]


class ScienceBase(DeclarativeBase):
    pass


class MultimodalAssetRow(ScienceBase):
    __tablename__ = "multimodal_assets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    managed_path: Mapped[str] = mapped_column(Text, nullable=False)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(100), nullable=False)
    asset_type: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    metadata_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class MultimodalRunRow(ScienceBase):
    __tablename__ = "multimodal_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    question: Mapped[str] = mapped_column(Text, nullable=False, default="")
    options_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    source_refs_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    original_result_json: Mapped[str] = mapped_column(Text, nullable=False)
    corrected_result_json: Mapped[str] = mapped_column(Text, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    job_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class MultimodalRevisionRow(ScienceBase):
    __tablename__ = "multimodal_revisions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("multimodal_runs.id"), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    changes_json: Mapped[str] = mapped_column(Text, nullable=False)
    diff_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ModelingWorkspaceRow(ScienceBase):
    __tablename__ = "modeling_workspaces"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    source_json: Mapped[str] = mapped_column(Text, nullable=False)
    mode: Mapped[str] = mapped_column(String(20), nullable=False)
    original_result_json: Mapped[str] = mapped_column(Text, nullable=False)
    current_result_json: Mapped[str] = mapped_column(Text, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    active_structure_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    job_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ModelingRevisionRow(ScienceBase):
    __tablename__ = "modeling_revisions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("modeling_workspaces.id"), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    changes_json: Mapped[str] = mapped_column(Text, nullable=False)
    diff_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ModelingStructureRow(ScienceBase):
    __tablename__ = "modeling_structures"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("modeling_workspaces.id"), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    managed_path: Mapped[str] = mapped_column(Text, nullable=False)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    format: Mapped[str] = mapped_column(String(30), nullable=False)
    geometry_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    warnings_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ModelingArtifactRow(ScienceBase):
    __tablename__ = "modeling_artifacts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("modeling_workspaces.id"), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    managed_path: Mapped[str] = mapped_column(Text, nullable=False)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


@dataclass(slots=True)
class StoredMultimodalAsset:
    id: str
    user_id: int
    path: Path
    file_name: str
    mime_type: str
    asset_type: str
    metadata: dict[str, Any]
    sha256: str
    created_at: datetime

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "fileName": self.file_name,
            "mimeType": self.mime_type,
            "assetType": self.asset_type,
            "metadata": self.metadata,
            "sha256": self.sha256,
            "createdAt": self.created_at,
        }


@dataclass(slots=True)
class StoredMultimodalRun:
    id: str
    user_id: int
    question: str
    options: dict[str, Any]
    source_refs: list[dict[str, Any]]
    original_result: dict[str, Any]
    corrected_result: dict[str, Any]
    revision: int
    job_id: str | None
    created_at: datetime
    updated_at: datetime

    def to_dict(self, *, include_original: bool = False) -> dict[str, Any]:
        value = {
            "id": self.id,
            "question": self.question,
            "options": self.options,
            "sources": self.source_refs,
            "result": self.corrected_result,
            "revision": self.revision,
            "jobId": self.job_id,
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
        }
        if include_original:
            value["originalResult"] = self.original_result
        return value


@dataclass(slots=True)
class StoredModelingWorkspace:
    id: str
    user_id: int
    source: dict[str, Any]
    mode: str
    original_result: dict[str, Any]
    current_result: dict[str, Any]
    revision: int
    active_structure_id: str | None
    job_id: str | None
    created_at: datetime
    updated_at: datetime

    def to_dict(self, *, include_original: bool = True) -> dict[str, Any]:
        value = {
            "id": self.id,
            "source": self.source,
            "mode": self.mode,
            "result": self.current_result,
            "revision": self.revision,
            "activeStructureId": self.active_structure_id,
            "jobId": self.job_id,
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
        }
        if include_original:
            value["originalResult"] = self.original_result
        return value


@dataclass(slots=True)
class StoredModelingStructure:
    id: str
    workspace_id: str
    user_id: int
    path: Path
    file_name: str
    format: str
    geometry: dict[str, Any]
    warnings: list[str]
    created_at: datetime

    def to_dict(self, *, include_geometry: bool = False) -> dict[str, Any]:
        value = {
            "id": self.id,
            "workspaceId": self.workspace_id,
            "fileName": self.file_name,
            "format": self.format,
            "warnings": self.warnings,
            "createdAt": self.created_at,
        }
        if include_geometry:
            value["geometry"] = self.geometry
        return value


@dataclass(slots=True)
class StoredModelingArtifact:
    id: str
    workspace_id: str
    user_id: int
    revision: int
    kind: str
    path: Path
    file_name: str
    mime_type: str
    created_at: datetime

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "workspaceId": self.workspace_id,
            "revision": self.revision,
            "kind": self.kind,
            "fileName": self.file_name,
            "mimeType": self.mime_type,
            "createdAt": self.created_at,
        }


class ScienceWorkspaceStore:
    def __init__(self, path: Path):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(
            f"sqlite:///{self.path.as_posix()}",
            connect_args={"check_same_thread": False, "timeout": 30},
            future=True,
        )
        self.session_factory = sessionmaker(bind=self.engine, expire_on_commit=False, future=True)
        ScienceBase.metadata.create_all(self.engine)

    @staticmethod
    def _asset(row: MultimodalAssetRow) -> StoredMultimodalAsset:
        return StoredMultimodalAsset(
            id=row.id,
            user_id=row.user_id,
            path=Path(row.managed_path).resolve(),
            file_name=row.file_name,
            mime_type=row.mime_type,
            asset_type=row.asset_type,
            metadata=_load(row.metadata_json, {}),
            sha256=row.sha256,
            created_at=row.created_at,
        )

    @staticmethod
    def _run(row: MultimodalRunRow) -> StoredMultimodalRun:
        return StoredMultimodalRun(
            id=row.id,
            user_id=row.user_id,
            question=row.question,
            options=_load(row.options_json, {}),
            source_refs=_load(row.source_refs_json, []),
            original_result=_load(row.original_result_json, {}),
            corrected_result=_load(row.corrected_result_json, {}),
            revision=row.revision,
            job_id=row.job_id,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    @staticmethod
    def _workspace(row: ModelingWorkspaceRow) -> StoredModelingWorkspace:
        return StoredModelingWorkspace(
            id=row.id,
            user_id=row.user_id,
            source=_load(row.source_json, {}),
            mode=row.mode,
            original_result=_load(row.original_result_json, {}),
            current_result=_load(row.current_result_json, {}),
            revision=row.revision,
            active_structure_id=row.active_structure_id,
            job_id=row.job_id,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    @staticmethod
    def _structure(row: ModelingStructureRow) -> StoredModelingStructure:
        return StoredModelingStructure(
            id=row.id,
            workspace_id=row.workspace_id,
            user_id=row.user_id,
            path=Path(row.managed_path).resolve(),
            file_name=row.file_name,
            format=row.format,
            geometry=_load(row.geometry_json, {}),
            warnings=_load(row.warnings_json, []),
            created_at=row.created_at,
        )

    @staticmethod
    def _artifact(row: ModelingArtifactRow) -> StoredModelingArtifact:
        return StoredModelingArtifact(
            id=row.id,
            workspace_id=row.workspace_id,
            user_id=row.user_id,
            revision=row.revision,
            kind=row.kind,
            path=Path(row.managed_path).resolve(),
            file_name=row.file_name,
            mime_type=row.mime_type,
            created_at=row.created_at,
        )

    def create_multimodal_asset(
        self,
        *,
        user_id: int,
        managed_path: Path,
        file_name: str,
        mime_type: str,
        asset_type: str,
        metadata: dict[str, Any],
        sha256: str = "",
    ) -> StoredMultimodalAsset:
        row = MultimodalAssetRow(
            id=str(uuid.uuid4()),
            user_id=user_id,
            managed_path=str(Path(managed_path).resolve()),
            file_name=Path(file_name).name[:255],
            mime_type=mime_type[:100],
            asset_type=asset_type[:30],
            metadata_json=_dump(metadata),
            sha256=sha256[:64],
            created_at=utc_now(),
        )
        with self.session_factory() as session:
            session.add(row)
            session.commit()
            return self._asset(row)

    def get_multimodal_asset(self, *, user_id: int, asset_id: str) -> StoredMultimodalAsset | None:
        with self.session_factory() as session:
            row = session.scalar(select(MultimodalAssetRow).where(
                MultimodalAssetRow.id == asset_id,
                MultimodalAssetRow.user_id == user_id,
            ))
            return self._asset(row) if row else None

    def list_multimodal_assets(self, *, user_id: int) -> list[StoredMultimodalAsset]:
        with self.session_factory() as session:
            rows = session.scalars(select(MultimodalAssetRow).where(
                MultimodalAssetRow.user_id == user_id,
            ).order_by(MultimodalAssetRow.created_at.desc())).all()
        return [self._asset(row) for row in rows]

    def delete_multimodal_asset(self, *, user_id: int, asset_id: str) -> Path | None:
        with self.session_factory() as session:
            row = session.scalar(select(MultimodalAssetRow).where(
                MultimodalAssetRow.id == asset_id,
                MultimodalAssetRow.user_id == user_id,
            ))
            if row is None:
                return None
            path = Path(row.managed_path).resolve()
            session.delete(row)
            session.commit()
            return path

    def create_multimodal_run(
        self,
        *,
        user_id: int,
        question: str,
        options: dict[str, Any],
        source_refs: list[dict[str, Any]],
        result: dict[str, Any],
        job_id: str | None,
    ) -> StoredMultimodalRun:
        now = utc_now()
        row = MultimodalRunRow(
            id=str(uuid.uuid4()),
            user_id=user_id,
            question=question,
            options_json=_dump(options),
            source_refs_json=_dump(source_refs),
            original_result_json=_dump(result),
            corrected_result_json=_dump(result),
            revision=1,
            job_id=job_id,
            created_at=now,
            updated_at=now,
        )
        with self.session_factory() as session:
            session.add(row)
            session.commit()
            return self._run(row)

    def get_multimodal_run(self, *, user_id: int, run_id: str) -> StoredMultimodalRun | None:
        with self.session_factory() as session:
            row = session.scalar(select(MultimodalRunRow).where(
                MultimodalRunRow.id == run_id,
                MultimodalRunRow.user_id == user_id,
            ))
            return self._run(row) if row else None

    def list_multimodal_runs(self, *, user_id: int) -> list[StoredMultimodalRun]:
        with self.session_factory() as session:
            rows = session.scalars(select(MultimodalRunRow).where(
                MultimodalRunRow.user_id == user_id,
            ).order_by(MultimodalRunRow.updated_at.desc())).all()
        return [self._run(row) for row in rows]

    def update_multimodal_run(
        self,
        *,
        user_id: int,
        run_id: str,
        expected_revision: int,
        corrections: dict[str, Any],
        result_transform: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    ) -> StoredMultimodalRun:
        with self.session_factory() as session:
            row = session.scalar(select(MultimodalRunRow).where(
                MultimodalRunRow.id == run_id,
                MultimodalRunRow.user_id == user_id,
            ))
            if row is None:
                raise ValueError("NOT_FOUND")
            if row.revision != expected_revision:
                raise ValueError("REVISION_CONFLICT")
            before = _load(row.corrected_result_json, {})
            if "itemCorrections" in corrections:
                items = deepcopy(before.get("items") or [])
                for correction in corrections.get("itemCorrections") or []:
                    index = int(correction.get("itemIndex", -1))
                    if index < 0 or index >= len(items) or not isinstance(items[index], dict):
                        raise ValueError("INVALID_CORRECTION")
                    if "dataPoints" in correction:
                        items[index]["dataPoints"] = deepcopy(correction["dataPoints"])
                    if "annotations" in correction:
                        items[index]["annotations"] = deepcopy(correction["annotations"])
                corrections = {"items": items}
            after = _deep_merge(before, corrections)
            if result_transform is not None:
                after = result_transform(after)
            revision = row.revision + 1
            now = utc_now()
            session.add(MultimodalRevisionRow(
                id=str(uuid.uuid4()),
                run_id=row.id,
                user_id=user_id,
                revision=revision,
                changes_json=_dump(corrections),
                diff_json=_dump(_diff(before, after)),
                created_at=now,
            ))
            row.corrected_result_json = _dump(after)
            row.revision = revision
            row.updated_at = now
            session.commit()
            return self._run(row)

    def create_modeling_workspace(
        self,
        *,
        user_id: int,
        source: dict[str, Any],
        mode: str,
        original_result: dict[str, Any],
        job_id: str | None,
    ) -> StoredModelingWorkspace:
        now = utc_now()
        row = ModelingWorkspaceRow(
            id=str(uuid.uuid4()),
            user_id=user_id,
            source_json=_dump(source),
            mode=mode,
            original_result_json=_dump(original_result),
            current_result_json=_dump(original_result),
            revision=1,
            job_id=job_id,
            created_at=now,
            updated_at=now,
        )
        with self.session_factory() as session:
            session.add(row)
            session.commit()
            return self._workspace(row)

    def get_modeling_workspace(self, *, user_id: int, workspace_id: str) -> StoredModelingWorkspace | None:
        with self.session_factory() as session:
            row = session.scalar(select(ModelingWorkspaceRow).where(
                ModelingWorkspaceRow.id == workspace_id,
                ModelingWorkspaceRow.user_id == user_id,
            ))
            return self._workspace(row) if row else None

    def list_modeling_workspaces(self, *, user_id: int) -> list[StoredModelingWorkspace]:
        with self.session_factory() as session:
            rows = session.scalars(select(ModelingWorkspaceRow).where(
                ModelingWorkspaceRow.user_id == user_id,
            ).order_by(ModelingWorkspaceRow.updated_at.desc())).all()
        return [self._workspace(row) for row in rows]

    def update_modeling_workspace(
        self,
        *,
        user_id: int,
        workspace_id: str,
        expected_revision: int,
        changes: dict[str, Any],
        active_structure_id: str | None = None,
    ) -> StoredModelingWorkspace:
        with self.session_factory() as session:
            row = session.scalar(select(ModelingWorkspaceRow).where(
                ModelingWorkspaceRow.id == workspace_id,
                ModelingWorkspaceRow.user_id == user_id,
            ))
            if row is None:
                raise ValueError("NOT_FOUND")
            if row.revision != expected_revision:
                raise ValueError("REVISION_CONFLICT")
            before = _load(row.current_result_json, {})
            after = _deep_merge(before, changes)
            revision = row.revision + 1
            now = utc_now()
            session.add(ModelingRevisionRow(
                id=str(uuid.uuid4()),
                workspace_id=row.id,
                user_id=user_id,
                revision=revision,
                changes_json=_dump(changes),
                diff_json=_dump(_diff(before, after)),
                created_at=now,
            ))
            row.current_result_json = _dump(after)
            row.revision = revision
            if active_structure_id is not None:
                row.active_structure_id = active_structure_id or None
            row.updated_at = now
            session.commit()
            return self._workspace(row)

    def list_modeling_revisions(self, *, user_id: int, workspace_id: str) -> list[dict[str, Any]]:
        with self.session_factory() as session:
            rows = session.scalars(select(ModelingRevisionRow).where(
                ModelingRevisionRow.workspace_id == workspace_id,
                ModelingRevisionRow.user_id == user_id,
            ).order_by(ModelingRevisionRow.revision.desc())).all()
        return [{
            "id": row.id,
            "revision": row.revision,
            "changes": _load(row.changes_json, {}),
            "diff": _load(row.diff_json, []),
            "createdAt": row.created_at,
        } for row in rows]

    def register_modeling_structure(
        self,
        *,
        user_id: int,
        workspace_id: str,
        managed_path: Path,
        file_name: str,
        format: str,
        geometry: dict[str, Any],
        warnings: list[str],
    ) -> StoredModelingStructure:
        if self.get_modeling_workspace(user_id=user_id, workspace_id=workspace_id) is None:
            raise ValueError("NOT_FOUND")
        row = ModelingStructureRow(
            id=str(uuid.uuid4()),
            workspace_id=workspace_id,
            user_id=user_id,
            managed_path=str(Path(managed_path).resolve()),
            file_name=Path(file_name).name[:255],
            format=format[:30],
            geometry_json=_dump(geometry),
            warnings_json=_dump(warnings),
            created_at=utc_now(),
        )
        with self.session_factory() as session:
            session.add(row)
            session.commit()
            return self._structure(row)

    def list_modeling_structures(self, *, user_id: int, workspace_id: str) -> list[StoredModelingStructure]:
        with self.session_factory() as session:
            rows = session.scalars(select(ModelingStructureRow).where(
                ModelingStructureRow.workspace_id == workspace_id,
                ModelingStructureRow.user_id == user_id,
            ).order_by(ModelingStructureRow.created_at)).all()
        return [self._structure(row) for row in rows]

    def get_modeling_structure(
        self,
        *,
        user_id: int,
        workspace_id: str,
        structure_id: str,
    ) -> StoredModelingStructure | None:
        with self.session_factory() as session:
            row = session.scalar(select(ModelingStructureRow).where(
                ModelingStructureRow.id == structure_id,
                ModelingStructureRow.workspace_id == workspace_id,
                ModelingStructureRow.user_id == user_id,
            ))
            return self._structure(row) if row else None

    def register_modeling_artifact(
        self,
        *,
        user_id: int,
        workspace_id: str,
        revision: int,
        kind: str,
        managed_path: Path,
        file_name: str,
        mime_type: str,
    ) -> StoredModelingArtifact:
        row = ModelingArtifactRow(
            id=str(uuid.uuid4()),
            workspace_id=workspace_id,
            user_id=user_id,
            revision=revision,
            kind=kind[:40],
            managed_path=str(Path(managed_path).resolve()),
            file_name=Path(file_name).name[:255],
            mime_type=mime_type[:100],
            created_at=utc_now(),
        )
        with self.session_factory() as session:
            session.add(row)
            session.commit()
            return self._artifact(row)

    def get_modeling_artifact(
        self,
        *,
        user_id: int,
        workspace_id: str,
        artifact_id: str,
    ) -> StoredModelingArtifact | None:
        with self.session_factory() as session:
            row = session.scalar(select(ModelingArtifactRow).where(
                ModelingArtifactRow.id == artifact_id,
                ModelingArtifactRow.workspace_id == workspace_id,
                ModelingArtifactRow.user_id == user_id,
            ))
            return self._artifact(row) if row else None

    def list_modeling_artifacts(self, *, user_id: int, workspace_id: str) -> list[StoredModelingArtifact]:
        with self.session_factory() as session:
            rows = session.scalars(select(ModelingArtifactRow).where(
                ModelingArtifactRow.workspace_id == workspace_id,
                ModelingArtifactRow.user_id == user_id,
            ).order_by(ModelingArtifactRow.created_at.desc())).all()
        return [self._artifact(row) for row in rows]

    def delete_modeling_workspace(self, *, user_id: int, workspace_id: str) -> list[Path] | None:
        with self.session_factory() as session:
            workspace = session.scalar(select(ModelingWorkspaceRow).where(
                ModelingWorkspaceRow.id == workspace_id,
                ModelingWorkspaceRow.user_id == user_id,
            ))
            if workspace is None:
                return None
            structures = session.scalars(select(ModelingStructureRow).where(
                ModelingStructureRow.workspace_id == workspace_id,
                ModelingStructureRow.user_id == user_id,
            )).all()
            artifacts = session.scalars(select(ModelingArtifactRow).where(
                ModelingArtifactRow.workspace_id == workspace_id,
                ModelingArtifactRow.user_id == user_id,
            )).all()
            paths = [Path(row.managed_path).resolve() for row in [*structures, *artifacts]]
            session.execute(delete(ModelingRevisionRow).where(ModelingRevisionRow.workspace_id == workspace_id))
            session.execute(delete(ModelingStructureRow).where(ModelingStructureRow.workspace_id == workspace_id))
            session.execute(delete(ModelingArtifactRow).where(ModelingArtifactRow.workspace_id == workspace_id))
            session.delete(workspace)
            session.commit()
            return paths

    def dispose(self) -> None:
        self.engine.dispose()
