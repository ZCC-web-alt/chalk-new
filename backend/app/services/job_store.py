from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import Boolean, DateTime, Integer, String, Text, create_engine, inspect, select, update
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


class WebBase(DeclarativeBase):
    pass


class WebJobRow(WebBase):
    __tablename__ = "web_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    type: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    stage: Mapped[str | None] = mapped_column(String(120), nullable=True)
    progress: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    message: Mapped[str] = mapped_column(Text, nullable=False, default="")
    result_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    feedback_prompt_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


@dataclass(slots=True)
class StoredJob:
    id: str
    user_id: int
    type: str
    payload: dict[str, Any]
    status: str
    stage: str | None
    progress: int
    message: str
    result: Any | None
    error: dict[str, Any] | None
    feedback_prompt: dict[str, Any] | None
    created_at: datetime
    updated_at: datetime
    cancel_requested: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "status": self.status,
            "stage": self.stage,
            "progress": self.progress,
            "message": self.message,
            "result": self.result,
            "error": self.error,
            "feedbackPrompt": self.feedback_prompt,
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
        }


class WebJobStore:
    def __init__(self, path: Path):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(
            f"sqlite:///{self.path.as_posix()}",
            connect_args={"check_same_thread": False},
            future=True,
        )
        self.session_factory = sessionmaker(bind=self.engine, expire_on_commit=False, future=True)
        WebBase.metadata.create_all(self.engine)
        columns = {column["name"] for column in inspect(self.engine).get_columns("web_jobs")}
        if "stage" not in columns:
            with self.engine.begin() as connection:
                connection.exec_driver_sql("ALTER TABLE web_jobs ADD COLUMN stage VARCHAR(120)")

    @staticmethod
    def _to_job(row: WebJobRow) -> StoredJob:
        return StoredJob(
            id=row.id,
            user_id=row.user_id,
            type=row.type,
            payload=_json_load(row.payload_json, {}),
            status=row.status,
            stage=row.stage,
            progress=row.progress,
            message=row.message,
            result=_json_load(row.result_json, None),
            error=_json_load(row.error_json, None),
            feedback_prompt=_json_load(row.feedback_prompt_json, None),
            created_at=row.created_at,
            updated_at=row.updated_at,
            cancel_requested=row.cancel_requested,
        )

    def create(self, user_id: int, job_type: str, payload: dict[str, Any]) -> StoredJob:
        now = utc_now()
        row = WebJobRow(
            id=str(uuid.uuid4()),
            user_id=user_id,
            type=job_type,
            payload_json=_json_dump(payload),
            status="QUEUED",
            progress=0,
            message="",
            created_at=now,
            updated_at=now,
        )
        with self.session_factory() as session:
            session.add(row)
            session.commit()
            return self._to_job(row)

    def get(self, job_id: str) -> StoredJob | None:
        with self.session_factory() as session:
            row = session.get(WebJobRow, job_id)
            return self._to_job(row) if row else None

    def get_for_user(self, user_id: int, job_id: str) -> StoredJob | None:
        with self.session_factory() as session:
            row = session.scalar(
                select(WebJobRow).where(WebJobRow.id == job_id, WebJobRow.user_id == user_id)
            )
            return self._to_job(row) if row else None

    def list_for_user(
        self,
        user_id: int,
        *,
        job_type: str | None = None,
        status: str | None = None,
    ) -> list[StoredJob]:
        statement = select(WebJobRow).where(WebJobRow.user_id == user_id)
        if job_type:
            statement = statement.where(WebJobRow.type == job_type)
        if status:
            statement = statement.where(WebJobRow.status == status)
        statement = statement.order_by(WebJobRow.created_at.desc())
        with self.session_factory() as session:
            return [self._to_job(row) for row in session.scalars(statement).all()]

    def update(self, job_id: str, **changes: Any) -> StoredJob | None:
        json_fields = {
            "payload": "payload_json",
            "result": "result_json",
            "error": "error_json",
            "feedback_prompt": "feedback_prompt_json",
        }
        values: dict[str, Any] = {"updated_at": utc_now()}
        for key, value in changes.items():
            target = json_fields.get(key, key)
            values[target] = _json_dump(value) if key in json_fields and value is not None else value
        with self.session_factory() as session:
            row = session.get(WebJobRow, job_id)
            if not row:
                return None
            for key, value in values.items():
                setattr(row, key, value)
            session.commit()
            return self._to_job(row)

    def transition(
        self,
        job_id: str,
        *,
        from_statuses: tuple[str, ...],
        require_not_cancelled: bool = True,
        **changes: Any,
    ) -> StoredJob | None:
        json_fields = {
            "payload": "payload_json",
            "result": "result_json",
            "error": "error_json",
            "feedback_prompt": "feedback_prompt_json",
        }
        values: dict[str, Any] = {"updated_at": utc_now()}
        for key, value in changes.items():
            target = json_fields.get(key, key)
            values[target] = _json_dump(value) if key in json_fields and value is not None else value
        statement = update(WebJobRow).where(
            WebJobRow.id == job_id,
            WebJobRow.status.in_(from_statuses),
        )
        if require_not_cancelled:
            statement = statement.where(WebJobRow.cancel_requested.is_(False))
        with self.session_factory() as session:
            session.execute(statement.values(**values))
            session.commit()
            row = session.get(WebJobRow, job_id)
            return self._to_job(row) if row else None

    def fail_interrupted_jobs(self) -> int:
        now = utc_now()
        error = _json_dump({"code": "SERVER_RESTARTED", "message": "The server restarted before the job completed."})
        with self.session_factory() as session:
            result = session.execute(
                update(WebJobRow)
                .where(WebJobRow.status.in_(("QUEUED", "RUNNING", "WAITING_FOR_FEEDBACK")))
                .values(
                    status="FAILED",
                    message="Interrupted by server restart.",
                    error_json=error,
                    feedback_prompt_json=None,
                    updated_at=now,
                )
            )
            session.commit()
            return int(result.rowcount or 0)

    def dispose(self) -> None:
        self.engine.dispose()
