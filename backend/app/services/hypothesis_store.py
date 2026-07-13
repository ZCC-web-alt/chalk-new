from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import DateTime, Integer, String, Text, UniqueConstraint, create_engine, delete, select
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker


def utc_now() -> datetime:
    return datetime.now(UTC)


class HypothesisArtifactBase(DeclarativeBase):
    pass


class HypothesisArtifactRow(HypothesisArtifactBase):
    __tablename__ = "hypothesis_artifacts"
    __table_args__ = (
        UniqueConstraint("user_id", "hypothesis_id", "kind", name="uq_hypothesis_artifact_kind"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    hypothesis_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    managed_path: Mapped[str] = mapped_column(Text, nullable=False)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(100), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


@dataclass(slots=True)
class StoredHypothesisArtifact:
    id: str
    user_id: int
    hypothesis_id: int
    kind: str
    path: Path
    file_name: str
    mime_type: str
    size_bytes: int
    created_at: datetime
    updated_at: datetime

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "fileName": self.file_name,
            "mimeType": self.mime_type,
            "sizeBytes": self.size_bytes,
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
        }


class HypothesisArtifactStore:
    def __init__(self, path: Path):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(
            f"sqlite:///{self.path.as_posix()}",
            connect_args={"check_same_thread": False, "timeout": 30},
            future=True,
        )
        self.session_factory = sessionmaker(bind=self.engine, expire_on_commit=False, future=True)
        HypothesisArtifactBase.metadata.create_all(self.engine)

    @staticmethod
    def _to_artifact(row: HypothesisArtifactRow) -> StoredHypothesisArtifact:
        return StoredHypothesisArtifact(
            id=row.id,
            user_id=row.user_id,
            hypothesis_id=row.hypothesis_id,
            kind=row.kind,
            path=Path(row.managed_path).resolve(),
            file_name=row.file_name,
            mime_type=row.mime_type,
            size_bytes=row.size_bytes,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    def upsert(
        self,
        user_id: int,
        hypothesis_id: int,
        kind: str,
        path: Path,
        file_name: str,
        mime_type: str,
    ) -> tuple[StoredHypothesisArtifact, list[Path]]:
        resolved = Path(path).resolve()
        now = utc_now()
        replaced: list[Path] = []
        with self.session_factory() as session:
            row = session.scalar(
                select(HypothesisArtifactRow).where(
                    HypothesisArtifactRow.user_id == user_id,
                    HypothesisArtifactRow.hypothesis_id == hypothesis_id,
                    HypothesisArtifactRow.kind == kind,
                )
            )
            if row is None:
                row = HypothesisArtifactRow(
                    id=str(uuid.uuid4()),
                    user_id=user_id,
                    hypothesis_id=hypothesis_id,
                    kind=kind,
                    managed_path=str(resolved),
                    file_name=Path(file_name).name[:255],
                    mime_type=mime_type[:100],
                    size_bytes=resolved.stat().st_size if resolved.is_file() else 0,
                    created_at=now,
                    updated_at=now,
                )
                session.add(row)
            else:
                previous = Path(row.managed_path).resolve()
                if previous != resolved:
                    replaced.append(previous)
                row.managed_path = str(resolved)
                row.file_name = Path(file_name).name[:255]
                row.mime_type = mime_type[:100]
                row.size_bytes = resolved.stat().st_size if resolved.is_file() else 0
                row.updated_at = now
            session.commit()
            return self._to_artifact(row), replaced

    def list_for_hypothesis(self, user_id: int, hypothesis_id: int) -> list[StoredHypothesisArtifact]:
        with self.session_factory() as session:
            rows = session.scalars(
                select(HypothesisArtifactRow)
                .where(
                    HypothesisArtifactRow.user_id == user_id,
                    HypothesisArtifactRow.hypothesis_id == hypothesis_id,
                )
                .order_by(HypothesisArtifactRow.created_at.asc())
            ).all()
            return [self._to_artifact(row) for row in rows]

    def get_for_user(
        self,
        user_id: int,
        hypothesis_id: int,
        artifact_id: str,
    ) -> StoredHypothesisArtifact | None:
        with self.session_factory() as session:
            row = session.scalar(
                select(HypothesisArtifactRow).where(
                    HypothesisArtifactRow.id == artifact_id,
                    HypothesisArtifactRow.user_id == user_id,
                    HypothesisArtifactRow.hypothesis_id == hypothesis_id,
                )
            )
            return self._to_artifact(row) if row else None

    def delete_for_hypothesis(self, user_id: int, hypothesis_id: int) -> list[Path]:
        with self.session_factory() as session:
            rows = session.scalars(
                select(HypothesisArtifactRow).where(
                    HypothesisArtifactRow.user_id == user_id,
                    HypothesisArtifactRow.hypothesis_id == hypothesis_id,
                )
            ).all()
            paths = [Path(row.managed_path).resolve() for row in rows]
            session.execute(
                delete(HypothesisArtifactRow).where(
                    HypothesisArtifactRow.user_id == user_id,
                    HypothesisArtifactRow.hypothesis_id == hypothesis_id,
                )
            )
            session.commit()
            return paths

    def dispose(self) -> None:
        self.engine.dispose()
