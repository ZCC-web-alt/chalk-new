from __future__ import annotations

import re
from dataclasses import asdict, dataclass, is_dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

from sqlalchemy import DateTime, Float, Integer, String, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker


HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")
ALLOWED_STATUSES = {"retrying", "succeeded", "failed", "budget_exceeded"}


def utc_now() -> datetime:
    return datetime.now(UTC)


def _text(value: Any, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _optional_text(value: Any, limit: int) -> str | None:
    text = _text(value, limit)
    return text or None


def _nonnegative_int(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _nonnegative_float(value: Any) -> float:
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return 0.0


def _hash(value: Any) -> str | None:
    text = _text(value, 64).lower()
    return text if HASH_PATTERN.fullmatch(text) else None


class ModelLedgerBase(DeclarativeBase):
    pass


class ModelCallLedgerRow(ModelLedgerBase):
    __tablename__ = "model_call_ledger"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    model: Mapped[str] = mapped_column(String(120), nullable=False)
    request_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    resource_type: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    resource_id: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    retry_reason: Mapped[str | None] = mapped_column(String(80), nullable=True)
    estimated_cost: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    prompt_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    response_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


@dataclass(slots=True)
class StoredModelCall:
    id: int
    provider: str
    model: str
    request_id: str | None
    resource_type: str
    resource_id: str
    status_code: int | None
    attempt: int
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    latency_ms: int
    retry_reason: str | None
    estimated_cost: float
    prompt_hash: str | None
    response_hash: str | None
    status: str
    created_at: datetime


class ModelCallLedgerStore:
    def __init__(self, path: Path):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(
            f"sqlite:///{self.path.as_posix()}",
            connect_args={"check_same_thread": False, "timeout": 30},
            future=True,
        )
        self.session_factory = sessionmaker(bind=self.engine, expire_on_commit=False, future=True)
        ModelLedgerBase.metadata.create_all(self.engine)

    @staticmethod
    def _stored(row: ModelCallLedgerRow) -> StoredModelCall:
        return StoredModelCall(
            id=row.id,
            provider=row.provider,
            model=row.model,
            request_id=row.request_id,
            resource_type=row.resource_type,
            resource_id=row.resource_id,
            status_code=row.status_code,
            attempt=row.attempt,
            prompt_tokens=row.prompt_tokens,
            completion_tokens=row.completion_tokens,
            total_tokens=row.total_tokens,
            latency_ms=row.latency_ms,
            retry_reason=row.retry_reason,
            estimated_cost=row.estimated_cost,
            prompt_hash=row.prompt_hash,
            response_hash=row.response_hash,
            status=row.status,
            created_at=row.created_at,
        )

    def record(self, event: Mapping[str, Any] | Any) -> StoredModelCall:
        if is_dataclass(event):
            event = asdict(event)
        if not isinstance(event, Mapping):
            raise TypeError("Model-call ledger events must be mappings or dataclasses.")
        status = _text(event.get("status"), 32)
        if status not in ALLOWED_STATUSES:
            status = "failed"
        status_value = event.get("status_code")
        status_code = _nonnegative_int(status_value) if status_value is not None else None
        row = ModelCallLedgerRow(
            provider=_text(event.get("provider"), 40) or "DashScope",
            model=_text(event.get("model"), 120) or "unknown",
            request_id=_optional_text(event.get("request_id"), 200),
            resource_type=_text(event.get("resource_type"), 80) or "unscoped",
            resource_id=_text(event.get("resource_id"), 200),
            status_code=status_code,
            attempt=_nonnegative_int(event.get("attempt")),
            prompt_tokens=_nonnegative_int(event.get("prompt_tokens")),
            completion_tokens=_nonnegative_int(event.get("completion_tokens")),
            total_tokens=_nonnegative_int(event.get("total_tokens")),
            latency_ms=_nonnegative_int(event.get("latency_ms")),
            retry_reason=_optional_text(event.get("retry_reason"), 80),
            estimated_cost=_nonnegative_float(event.get("estimated_cost")),
            prompt_hash=_hash(event.get("prompt_hash")),
            response_hash=_hash(event.get("response_hash")),
            status=status,
            created_at=utc_now(),
        )
        with self.session_factory() as session:
            session.add(row)
            session.commit()
            return self._stored(row)

    def list_for_context(self, resource_type: str, resource_id: str) -> list[StoredModelCall]:
        with self.session_factory() as session:
            rows = session.scalars(
                select(ModelCallLedgerRow)
                .where(
                    ModelCallLedgerRow.resource_type == _text(resource_type, 80),
                    ModelCallLedgerRow.resource_id == _text(resource_id, 200),
                )
                .order_by(ModelCallLedgerRow.id)
            ).all()
        return [self._stored(row) for row in rows]

    def dispose(self) -> None:
        self.engine.dispose()


__all__ = ["ModelCallLedgerStore", "StoredModelCall"]
