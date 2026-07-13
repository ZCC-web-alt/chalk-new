from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import DateTime, Integer, String, Text, UniqueConstraint, create_engine, delete, select
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker


def utc_now() -> datetime:
    return datetime.now(UTC)


def _json_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _json_load(value: str | None, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return fallback


def _normalize_document_ids(document_ids: list[int]) -> list[int]:
    return sorted({int(document_id) for document_id in document_ids})


def _scope_key(document_ids: list[int]) -> str:
    normalized = _normalize_document_ids(document_ids)
    return ",".join(str(document_id) for document_id in normalized)


class AnalysisBase(DeclarativeBase):
    pass


class DocumentAnalysisRow(AnalysisBase):
    __tablename__ = "document_analyses"
    __table_args__ = (
        UniqueConstraint("user_id", "analysis_type", "scope_key", name="uq_document_analysis_scope"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    analysis_type: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    scope_key: Mapped[str] = mapped_column(String(500), nullable=False)
    document_ids_json: Mapped[str] = mapped_column(Text, nullable=False)
    result_json: Mapped[str] = mapped_column(Text, nullable=False)
    job_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AnalysisAssetRow(AnalysisBase):
    __tablename__ = "analysis_assets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    document_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    analysis_type: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    managed_path: Mapped[str] = mapped_column(Text, nullable=False)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


@dataclass(slots=True)
class StoredAnalysis:
    id: str
    user_id: int
    analysis_type: str
    document_ids: list[int]
    result: dict[str, Any]
    job_id: str | None
    created_at: datetime
    updated_at: datetime

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.analysis_type,
            "documentIds": self.document_ids,
            "result": self.result,
            "jobId": self.job_id,
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
        }


@dataclass(slots=True)
class StoredAsset:
    id: str
    user_id: int
    document_id: int
    analysis_type: str
    path: Path
    file_name: str
    mime_type: str
    created_at: datetime


class DocumentAnalysisStore:
    def __init__(self, path: Path):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(
            f"sqlite:///{self.path.as_posix()}",
            connect_args={"check_same_thread": False, "timeout": 30},
            future=True,
        )
        self.session_factory = sessionmaker(bind=self.engine, expire_on_commit=False, future=True)
        AnalysisBase.metadata.create_all(self.engine)

    @staticmethod
    def _to_analysis(row: DocumentAnalysisRow) -> StoredAnalysis:
        return StoredAnalysis(
            id=row.id,
            user_id=row.user_id,
            analysis_type=row.analysis_type,
            document_ids=[int(value) for value in _json_load(row.document_ids_json, [])],
            result=_json_load(row.result_json, {}),
            job_id=row.job_id,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    @staticmethod
    def _to_asset(row: AnalysisAssetRow) -> StoredAsset:
        return StoredAsset(
            id=row.id,
            user_id=row.user_id,
            document_id=row.document_id,
            analysis_type=row.analysis_type,
            path=Path(row.managed_path).resolve(),
            file_name=row.file_name,
            mime_type=row.mime_type,
            created_at=row.created_at,
        )

    def upsert(
        self,
        user_id: int,
        analysis_type: str,
        document_ids: list[int],
        result: dict[str, Any],
        job_id: str | None = None,
    ) -> StoredAnalysis:
        normalized_ids = _normalize_document_ids(document_ids)
        if not normalized_ids:
            raise ValueError("At least one document ID is required.")
        scope = _scope_key(normalized_ids)
        now = utc_now()
        with self.session_factory() as session:
            row = session.scalar(
                select(DocumentAnalysisRow).where(
                    DocumentAnalysisRow.user_id == user_id,
                    DocumentAnalysisRow.analysis_type == analysis_type,
                    DocumentAnalysisRow.scope_key == scope,
                )
            )
            if row is None:
                row = DocumentAnalysisRow(
                    id=str(uuid.uuid4()),
                    user_id=user_id,
                    analysis_type=analysis_type,
                    scope_key=scope,
                    document_ids_json=_json_dump(normalized_ids),
                    result_json=_json_dump(result),
                    job_id=job_id,
                    created_at=now,
                    updated_at=now,
                )
                session.add(row)
            else:
                row.document_ids_json = _json_dump(normalized_ids)
                row.result_json = _json_dump(result)
                row.job_id = job_id
                row.updated_at = now
            session.commit()
            return self._to_analysis(row)

    def get_for_scope(
        self,
        user_id: int,
        analysis_type: str,
        document_ids: list[int],
    ) -> StoredAnalysis | None:
        scope = _scope_key(document_ids)
        with self.session_factory() as session:
            row = session.scalar(
                select(DocumentAnalysisRow).where(
                    DocumentAnalysisRow.user_id == user_id,
                    DocumentAnalysisRow.analysis_type == analysis_type,
                    DocumentAnalysisRow.scope_key == scope,
                )
            )
            return self._to_analysis(row) if row else None

    def list_for_document(self, user_id: int, document_id: int) -> list[StoredAnalysis]:
        with self.session_factory() as session:
            rows = session.scalars(
                select(DocumentAnalysisRow)
                .where(
                    DocumentAnalysisRow.user_id == user_id,
                    DocumentAnalysisRow.analysis_type != "comparison",
                    DocumentAnalysisRow.scope_key == str(int(document_id)),
                )
                .order_by(DocumentAnalysisRow.updated_at.desc())
            ).all()
        return [self._to_analysis(row) for row in rows]

    def get_comparison(self, user_id: int, document_ids: list[int]) -> StoredAnalysis | None:
        return self.get_for_scope(user_id, "comparison", document_ids)

    def register_asset(
        self,
        user_id: int,
        document_id: int,
        analysis_type: str,
        path: Path,
        file_name: str,
        mime_type: str,
        asset_id: str | None = None,
    ) -> StoredAsset:
        resolved = Path(path).resolve()
        row = AnalysisAssetRow(
            id=asset_id or str(uuid.uuid4()),
            user_id=user_id,
            document_id=document_id,
            analysis_type=analysis_type,
            managed_path=str(resolved),
            file_name=Path(file_name).name[:255],
            mime_type=mime_type[:100],
            created_at=utc_now(),
        )
        with self.session_factory() as session:
            session.add(row)
            session.commit()
            return self._to_asset(row)

    def get_asset_for_user(self, user_id: int, document_id: int, asset_id: str) -> StoredAsset | None:
        with self.session_factory() as session:
            row = session.scalar(
                select(AnalysisAssetRow).where(
                    AnalysisAssetRow.id == asset_id,
                    AnalysisAssetRow.user_id == user_id,
                    AnalysisAssetRow.document_id == document_id,
                )
            )
            return self._to_asset(row) if row else None

    def list_assets_for_document(self, user_id: int, document_id: int) -> list[StoredAsset]:
        with self.session_factory() as session:
            rows = session.scalars(
                select(AnalysisAssetRow)
                .where(
                    AnalysisAssetRow.user_id == user_id,
                    AnalysisAssetRow.document_id == document_id,
                )
                .order_by(AnalysisAssetRow.created_at, AnalysisAssetRow.id)
            ).all()
        return [self._to_asset(row) for row in rows]

    def remove_assets(self, user_id: int, asset_ids: list[str]) -> list[Path]:
        if not asset_ids:
            return []
        with self.session_factory() as session:
            rows = session.scalars(
                select(AnalysisAssetRow).where(
                    AnalysisAssetRow.user_id == user_id,
                    AnalysisAssetRow.id.in_(asset_ids),
                )
            ).all()
            paths = [Path(row.managed_path).resolve() for row in rows]
            session.execute(
                delete(AnalysisAssetRow).where(
                    AnalysisAssetRow.user_id == user_id,
                    AnalysisAssetRow.id.in_([row.id for row in rows]),
                )
            )
            session.commit()
            return paths

    def delete_for_document(self, user_id: int, document_id: int) -> list[Path]:
        with self.session_factory() as session:
            analysis_rows = session.scalars(
                select(DocumentAnalysisRow).where(DocumentAnalysisRow.user_id == user_id)
            ).all()
            analysis_ids = [
                row.id
                for row in analysis_rows
                if int(document_id) in _json_load(row.document_ids_json, [])
            ]
            asset_rows = session.scalars(
                select(AnalysisAssetRow).where(
                    AnalysisAssetRow.user_id == user_id,
                    AnalysisAssetRow.document_id == document_id,
                )
            ).all()
            paths = [Path(row.managed_path).resolve() for row in asset_rows]
            if analysis_ids:
                session.execute(delete(DocumentAnalysisRow).where(DocumentAnalysisRow.id.in_(analysis_ids)))
            session.execute(
                delete(AnalysisAssetRow).where(
                    AnalysisAssetRow.user_id == user_id,
                    AnalysisAssetRow.document_id == document_id,
                )
            )
            session.commit()
            return paths

    def dispose(self) -> None:
        self.engine.dispose()
