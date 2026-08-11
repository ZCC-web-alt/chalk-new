from __future__ import annotations

import json
import uuid
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    event,
    inspect,
    select,
    update,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from app.services.science125_catalog import get_science125_catalog


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


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


class Science125ReportBase(DeclarativeBase):
    pass


class Science125BatchRow(Science125ReportBase):
    __tablename__ = "science125_batches"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    manifest_version: Mapped[str] = mapped_column(String(80), nullable=False)
    manifest_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    routing_version: Mapped[str] = mapped_column(String(80), nullable=False)
    routing_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(80), nullable=False)
    prompt_registry_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    total_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    succeeded_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    blocked_evidence_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    estimated_cost_cny: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    question_ids_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Science125BatchItemRow(Science125ReportBase):
    __tablename__ = "science125_batch_items"
    __table_args__ = (
        UniqueConstraint("batch_id", "question_id", name="uq_science125_batch_item_question"),
        UniqueConstraint("batch_id", "sequence", name="uq_science125_batch_item_sequence"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    batch_id: Mapped[str] = mapped_column(ForeignKey("science125_batches.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    question_id: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    report_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    last_error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Science125ReportRow(Science125ReportBase):
    __tablename__ = "science125_reports"
    __table_args__ = (
        UniqueConstraint("batch_id", "question_id", name="uq_science125_report_batch_question"),
        UniqueConstraint("user_id", "source_job_id", name="uq_science125_report_user_source_job"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    batch_id: Mapped[str] = mapped_column(ForeignKey("science125_batches.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    question_id: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    question_zh: Mapped[str | None] = mapped_column(Text, nullable=True)
    benchmark_domain: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    primary_subdomain: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    selected_hypothesis_id: Mapped[str | None] = mapped_column(String(8), nullable=True)
    selected_hypothesis_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    selected_hypothesis_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence_status: Mapped[str] = mapped_column(String(24), nullable=False, default="insufficient")
    selected_evidence_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    provider_families_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    total_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    estimated_cost_cny: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    retrieval_query: Mapped[str] = mapped_column(Text, nullable=False, default="")
    retrieval_query_zh: Mapped[str | None] = mapped_column(Text, nullable=True)
    refinement_queries_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    retrieval_snapshot_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    evidence_snapshot_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    evidence_snapshot_sha256: Mapped[str] = mapped_column(String(64), nullable=False, default="0" * 64)
    research_output_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    provenance_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False, default="batch", server_default="batch", index=True)
    source_job_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Science125ExportRow(Science125ReportBase):
    __tablename__ = "science125_exports"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    batch_id: Mapped[str] = mapped_column(ForeignKey("science125_batches.id", ondelete="CASCADE"), nullable=False, index=True)
    report_id: Mapped[str | None] = mapped_column(ForeignKey("science125_reports.id", ondelete="CASCADE"), nullable=True, index=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    format: Mapped[str] = mapped_column(String(16), nullable=False)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(120), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    managed_path: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


@dataclass(slots=True, frozen=True)
class StoredScience125Batch:
    id: str
    user_id: int
    manifest_version: str
    manifest_sha256: str
    routing_version: str
    routing_sha256: str
    prompt_version: str
    prompt_registry_sha256: str
    model: str
    status: str
    total_count: int
    succeeded_count: int
    failed_count: int
    blocked_evidence_count: int
    total_tokens: int
    estimated_cost_cny: float
    question_ids: tuple[str, ...]
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime

    def to_dict(self, *, reports: tuple[dict[str, Any], ...] = ()) -> dict[str, Any]:
        return {
            "batchId": self.id,
            "manifestVersion": self.manifest_version,
            "manifestSha256": self.manifest_sha256,
            "routingVersion": self.routing_version,
            "routingSha256": self.routing_sha256,
            "promptVersion": self.prompt_version,
            "promptRegistrySha256": self.prompt_registry_sha256,
            "model": self.model,
            "status": self.status,
            "totalCount": self.total_count,
            "succeededCount": self.succeeded_count,
            "failedCount": self.failed_count,
            "blockedEvidenceCount": self.blocked_evidence_count,
            "totalTokens": self.total_tokens,
            "estimatedCostCny": self.estimated_cost_cny,
            "questionIds": list(self.question_ids),
            "reports": list(reports),
            "startedAt": _iso(self.started_at),
            "completedAt": _iso(self.completed_at),
            "createdAt": _iso(self.created_at),
            "updatedAt": _iso(self.updated_at),
        }


@dataclass(slots=True, frozen=True)
class StoredScience125BatchItem:
    id: str
    batch_id: str
    user_id: int
    question_id: str
    sequence: int
    status: str
    attempt_number: int
    report_id: str | None
    last_error_code: str | None
    last_error_message: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(slots=True, frozen=True)
class StoredScience125Report:
    id: str
    batch_id: str
    user_id: int
    question_id: str
    question: str
    question_zh: str | None
    benchmark_domain: str
    primary_subdomain: str
    status: str
    attempt_number: int
    selected_hypothesis_id: str | None
    selected_hypothesis_confidence: float | None
    selected_hypothesis_reason: str | None
    evidence_status: str
    selected_evidence_count: int
    provider_families: tuple[str, ...]
    model: str | None
    request_id: str | None
    total_tokens: int
    latency_ms: int
    estimated_cost_cny: float
    retrieval_query: str
    retrieval_query_zh: str | None
    refinement_queries: tuple[str, ...]
    retrieval_snapshot: dict[str, Any]
    evidence_snapshot: dict[str, Any]
    evidence_snapshot_sha256: str
    research_output: dict[str, Any] | None
    provenance: dict[str, Any] | None
    source_type: str
    source_job_id: str | None
    created_at: datetime
    updated_at: datetime

    def to_summary_dict(self) -> dict[str, Any]:
        return {
            "reportId": self.id,
            "batchId": self.batch_id,
            "questionId": self.question_id,
            "question": self.question,
            "questionZh": self.question_zh,
            "benchmarkDomain": self.benchmark_domain,
            "primarySubdomain": self.primary_subdomain,
            "status": self.status,
            "attemptNumber": self.attempt_number,
            "selectedHypothesisId": self.selected_hypothesis_id,
            "selectedHypothesisConfidence": self.selected_hypothesis_confidence,
            "selectedHypothesisReason": self.selected_hypothesis_reason,
            "evidenceStatus": self.evidence_status,
            "selectedEvidenceCount": self.selected_evidence_count,
            "providerFamilies": list(self.provider_families),
            "model": self.model,
            "requestId": self.request_id,
            "totalTokens": self.total_tokens,
            "latencyMs": self.latency_ms,
            "estimatedCostCny": self.estimated_cost_cny,
            "createdAt": _iso(self.created_at),
            "updatedAt": _iso(self.updated_at),
            "sourceType": self.source_type,
            "sourceJobId": self.source_job_id,
        }

    def to_dict(self, *, exports: tuple[dict[str, Any], ...] = ()) -> dict[str, Any]:
        return {
            **self.to_summary_dict(),
            "retrievalQuery": self.retrieval_query,
            "retrievalQueryZh": self.retrieval_query_zh,
            "refinementQueries": list(self.refinement_queries),
            "retrievalSnapshot": self.retrieval_snapshot,
            "evidenceSnapshot": self.evidence_snapshot,
            "evidenceSnapshotSha256": self.evidence_snapshot_sha256,
            "researchOutput": self.research_output,
            "provenance": self.provenance,
            "sourceType": self.source_type,
            "sourceJobId": self.source_job_id,
            "exportArtifacts": list(exports),
        }


@dataclass(slots=True, frozen=True)
class StoredScience125Export:
    id: str
    batch_id: str
    report_id: str | None
    user_id: int
    format: str
    file_name: str
    mime_type: str
    size_bytes: int
    path: Path
    created_at: datetime

    def to_dict(self) -> dict[str, Any]:
        return {
            "exportId": self.id,
            "batchId": self.batch_id,
            "reportId": self.report_id,
            "format": self.format,
            "fileName": self.file_name,
            "mimeType": self.mime_type,
            "sizeBytes": self.size_bytes,
            "createdAt": _iso(self.created_at),
        }


class Science125ReportStore:
    def __init__(self, path: Path):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(
            f"sqlite:///{self.path.as_posix()}",
            connect_args={"check_same_thread": False, "timeout": 30},
            future=True,
        )
        event.listen(self.engine, "connect", self._enable_sqlite_foreign_keys)
        self.session_factory = sessionmaker(bind=self.engine, expire_on_commit=False, future=True)
        Science125ReportBase.metadata.create_all(self.engine)
        self._ensure_batch_prompt_registry_column()
        self._ensure_report_source_columns()

    def _ensure_batch_prompt_registry_column(self) -> None:
        with self.engine.connect() as connection:
            columns = {item["name"] for item in inspect(connection).get_columns("science125_batches")}
        if "prompt_registry_sha256" in columns:
            return
        legacy_hash = "0" * 64
        with self.engine.begin() as connection:
            connection.exec_driver_sql(
                "ALTER TABLE science125_batches ADD COLUMN prompt_registry_sha256 "
                f"VARCHAR(64) NOT NULL DEFAULT '{legacy_hash}'"
            )

    def _ensure_report_source_columns(self) -> None:
        with self.engine.connect() as connection:
            columns = {item["name"] for item in inspect(connection).get_columns("science125_reports")}
        with self.engine.begin() as connection:
            if "source_type" not in columns:
                connection.exec_driver_sql(
                    "ALTER TABLE science125_reports ADD COLUMN source_type VARCHAR(32) NOT NULL DEFAULT 'batch'"
                )
            if "source_job_id" not in columns:
                connection.exec_driver_sql("ALTER TABLE science125_reports ADD COLUMN source_job_id VARCHAR(36)")
            connection.exec_driver_sql(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_science125_report_user_source_job "
                "ON science125_reports (user_id, source_job_id) WHERE source_job_id IS NOT NULL"
            )

    @staticmethod
    def _enable_sqlite_foreign_keys(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    @staticmethod
    def _batch(row: Science125BatchRow) -> StoredScience125Batch:
        return StoredScience125Batch(
            id=row.id,
            user_id=row.user_id,
            manifest_version=row.manifest_version,
            manifest_sha256=row.manifest_sha256,
            routing_version=row.routing_version,
            routing_sha256=row.routing_sha256,
            prompt_version=row.prompt_version,
            prompt_registry_sha256=row.prompt_registry_sha256,
            model=row.model,
            status=row.status,
            total_count=row.total_count,
            succeeded_count=row.succeeded_count,
            failed_count=row.failed_count,
            blocked_evidence_count=row.blocked_evidence_count,
            total_tokens=row.total_tokens,
            estimated_cost_cny=row.estimated_cost_cny,
            question_ids=tuple(str(value) for value in _load(row.question_ids_json, [])),
            started_at=row.started_at,
            completed_at=row.completed_at,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    @staticmethod
    def _item(row: Science125BatchItemRow) -> StoredScience125BatchItem:
        return StoredScience125BatchItem(
            id=row.id,
            batch_id=row.batch_id,
            user_id=row.user_id,
            question_id=row.question_id,
            sequence=row.sequence,
            status=row.status,
            attempt_number=row.attempt_number,
            report_id=row.report_id,
            last_error_code=row.last_error_code,
            last_error_message=row.last_error_message,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    @staticmethod
    def _report(row: Science125ReportRow) -> StoredScience125Report:
        return StoredScience125Report(
            id=row.id,
            batch_id=row.batch_id,
            user_id=row.user_id,
            question_id=row.question_id,
            question=row.question,
            question_zh=row.question_zh,
            benchmark_domain=row.benchmark_domain,
            primary_subdomain=row.primary_subdomain,
            status=row.status,
            attempt_number=row.attempt_number,
            selected_hypothesis_id=row.selected_hypothesis_id,
            selected_hypothesis_confidence=row.selected_hypothesis_confidence,
            selected_hypothesis_reason=row.selected_hypothesis_reason,
            evidence_status=row.evidence_status,
            selected_evidence_count=row.selected_evidence_count,
            provider_families=tuple(str(value) for value in _load(row.provider_families_json, [])),
            model=row.model,
            request_id=row.request_id,
            total_tokens=row.total_tokens,
            latency_ms=row.latency_ms,
            estimated_cost_cny=row.estimated_cost_cny,
            retrieval_query=row.retrieval_query,
            retrieval_query_zh=row.retrieval_query_zh,
            refinement_queries=tuple(str(value) for value in _load(row.refinement_queries_json, [])),
            retrieval_snapshot=_load(row.retrieval_snapshot_json, {}),
            evidence_snapshot=_load(row.evidence_snapshot_json, {}),
            evidence_snapshot_sha256=row.evidence_snapshot_sha256,
            research_output=_load(row.research_output_json, None),
            provenance=_load(row.provenance_json, None),
            source_type=row.source_type or "batch",
            source_job_id=row.source_job_id,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    @staticmethod
    def _export(row: Science125ExportRow) -> StoredScience125Export:
        return StoredScience125Export(
            id=row.id,
            batch_id=row.batch_id,
            report_id=row.report_id,
            user_id=row.user_id,
            format=row.format,
            file_name=row.file_name,
            mime_type=row.mime_type,
            size_bytes=row.size_bytes,
            path=Path(row.managed_path).resolve(),
            created_at=row.created_at,
        )

    def _default_question_ids(self) -> tuple[str, ...]:
        catalog = get_science125_catalog()
        return tuple(item.id for item in catalog.data)

    def create_batch(
        self,
        *,
        user_id: int,
        question_ids: tuple[str, ...] | None,
        manifest_version: str,
        manifest_sha256: str,
        routing_version: str,
        routing_sha256: str,
        prompt_version: str,
        prompt_registry_sha256: str,
        model: str,
    ) -> StoredScience125Batch:
        catalog_ids = self._default_question_ids()
        wanted = tuple(question_ids or catalog_ids)
        unknown = sorted(set(wanted) - set(catalog_ids))
        if unknown:
            raise ValueError(f"UNKNOWN_SCIENCE125_QUESTION:{','.join(unknown)}")
        if len(set(wanted)) != len(wanted):
            raise ValueError("DUPLICATE_SCIENCE125_QUESTION")
        ordered = tuple(item_id for item_id in catalog_ids if item_id in set(wanted))
        now = utc_now()
        batch = Science125BatchRow(
            id=str(uuid.uuid4()),
            user_id=user_id,
            manifest_version=manifest_version,
            manifest_sha256=manifest_sha256,
            routing_version=routing_version,
            routing_sha256=routing_sha256,
            prompt_version=prompt_version,
            prompt_registry_sha256=prompt_registry_sha256,
            model=model,
            status="DRAFT",
            total_count=len(ordered),
            question_ids_json=_dump(list(ordered)),
            created_at=now,
            updated_at=now,
        )
        items = [
            Science125BatchItemRow(
                id=str(uuid.uuid4()),
                batch_id=batch.id,
                user_id=user_id,
                question_id=question_id,
                sequence=index,
                status="PENDING",
                attempt_number=1,
                created_at=now,
                updated_at=now,
            )
            for index, question_id in enumerate(ordered, start=1)
        ]
        with self.session_factory() as session:
            session.add(batch)
            session.flush()
            session.add_all(items)
            session.commit()
            return self._batch(batch)

    def list_batches(self, *, user_id: int) -> list[StoredScience125Batch]:
        with self.session_factory() as session:
            rows = session.scalars(
                select(Science125BatchRow)
                .where(Science125BatchRow.user_id == user_id)
                .order_by(Science125BatchRow.created_at.desc())
            ).all()
            return [self._batch(row) for row in rows]

    def get_batch_for_user(self, *, user_id: int, batch_id: str) -> StoredScience125Batch | None:
        with self.session_factory() as session:
            row = session.scalar(
                select(Science125BatchRow).where(
                    Science125BatchRow.id == batch_id,
                    Science125BatchRow.user_id == user_id,
                )
            )
            return self._batch(row) if row else None

    def get_batch(self, *, batch_id: str) -> StoredScience125Batch | None:
        with self.session_factory() as session:
            row = session.get(Science125BatchRow, batch_id)
            return self._batch(row) if row else None

    def delete_batch(self, *, user_id: int, batch_id: str) -> StoredScience125Batch | None:
        with self.session_factory() as session:
            row = session.scalar(
                select(Science125BatchRow).where(
                    Science125BatchRow.id == batch_id,
                    Science125BatchRow.user_id == user_id,
                )
            )
            if row is None:
                return None
            batch = self._batch(row)
            session.delete(row)
            session.commit()
            return batch

    def list_batch_items(self, *, user_id: int, batch_id: str) -> list[StoredScience125BatchItem]:
        with self.session_factory() as session:
            rows = session.scalars(
                select(Science125BatchItemRow)
                .where(
                    Science125BatchItemRow.user_id == user_id,
                    Science125BatchItemRow.batch_id == batch_id,
                )
                .order_by(Science125BatchItemRow.sequence)
            ).all()
            return [self._item(row) for row in rows]

    def list_batch_items_for_runner(self, *, batch_id: str, question_ids: tuple[str, ...] | None = None) -> list[StoredScience125BatchItem]:
        with self.session_factory() as session:
            statement = select(Science125BatchItemRow).where(Science125BatchItemRow.batch_id == batch_id)
            if question_ids is not None:
                statement = statement.where(Science125BatchItemRow.question_id.in_(question_ids))
            rows = session.scalars(statement.order_by(Science125BatchItemRow.sequence)).all()
            return [self._item(row) for row in rows]

    def mark_batch_running(self, *, user_id: int, batch_id: str) -> StoredScience125Batch | None:
        now = utc_now()
        with self.session_factory() as session:
            row = session.scalar(
                select(Science125BatchRow).where(
                    Science125BatchRow.id == batch_id,
                    Science125BatchRow.user_id == user_id,
                )
            )
            if not row:
                return None
            row.status = "RUNNING"
            row.started_at = row.started_at or now
            row.completed_at = None
            row.updated_at = now
            session.commit()
            return self._batch(row)

    def set_batch_status(
        self,
        *,
        user_id: int,
        batch_id: str,
        status: str,
        completed: bool = False,
    ) -> StoredScience125Batch | None:
        now = utc_now()
        with self.session_factory() as session:
            row = session.scalar(select(Science125BatchRow).where(Science125BatchRow.id == batch_id, Science125BatchRow.user_id == user_id))
            if not row:
                return None
            row.status = status
            row.updated_at = now
            if completed:
                row.completed_at = now
            session.commit()
            return self._batch(row)

    def update_batch_counters(self, *, user_id: int, batch_id: str) -> StoredScience125Batch | None:
        with self.session_factory() as session:
            batch = session.scalar(select(Science125BatchRow).where(Science125BatchRow.id == batch_id, Science125BatchRow.user_id == user_id))
            if not batch:
                return None
            reports = session.scalars(select(Science125ReportRow).where(Science125ReportRow.batch_id == batch_id, Science125ReportRow.user_id == user_id)).all()
            items = session.scalars(select(Science125BatchItemRow).where(Science125BatchItemRow.batch_id == batch_id, Science125BatchItemRow.user_id == user_id)).all()
            batch.succeeded_count = sum(row.status == "SUCCEEDED" for row in items)
            batch.failed_count = sum(row.status == "FAILED" for row in items)
            batch.blocked_evidence_count = sum(row.status == "BLOCKED_EVIDENCE" for row in items)
            batch.total_tokens = sum(int(row.total_tokens or 0) for row in reports)
            batch.estimated_cost_cny = float(sum(float(row.estimated_cost_cny or 0.0) for row in reports))
            preserve_control_state = batch.status in {"PAUSED", "CANCELLED"}
            if not preserve_control_state and batch.succeeded_count == batch.total_count and batch.total_count > 0:
                batch.status = "SUCCEEDED"
                batch.completed_at = batch.completed_at or utc_now()
            elif not preserve_control_state and (batch.failed_count + batch.blocked_evidence_count + batch.succeeded_count) == batch.total_count:
                batch.status = "FAILED"
                batch.completed_at = batch.completed_at or utc_now()
            batch.updated_at = utc_now()
            session.commit()
            return self._batch(batch)

    def prepare_items_for_retry(
        self,
        *,
        user_id: int,
        batch_id: str,
        question_ids: tuple[str, ...] | None = None,
    ) -> tuple[str, ...]:
        retryable = {"FAILED", "BLOCKED_EVIDENCE"}
        now = utc_now()
        with self.session_factory() as session:
            statement = select(Science125BatchItemRow).where(
                Science125BatchItemRow.user_id == user_id,
                Science125BatchItemRow.batch_id == batch_id,
                Science125BatchItemRow.status.in_(retryable),
            )
            if question_ids is not None:
                statement = statement.where(Science125BatchItemRow.question_id.in_(question_ids))
            rows = session.scalars(statement.order_by(Science125BatchItemRow.sequence)).all()
            for row in rows:
                row.status = "RETRYING"
                row.attempt_number += 1
                row.last_error_code = None
                row.last_error_message = None
                row.updated_at = now
                report = session.scalar(select(Science125ReportRow).where(
                    Science125ReportRow.batch_id == batch_id,
                    Science125ReportRow.question_id == row.question_id,
                ))
                if report is not None:
                    report.status = "RETRYING"
                    report.attempt_number = row.attempt_number
                    report.updated_at = now
            session.commit()
            return tuple(row.question_id for row in rows)

    def upsert_report(
        self,
        *,
        user_id: int,
        batch_id: str,
        question_id: str,
        question: str,
        question_zh: str | None = None,
        benchmark_domain: str,
        primary_subdomain: str,
        attempt_number: int,
        status: str,
        evidence_status: str,
        selected_evidence_count: int,
        provider_families: tuple[str, ...] = (),
        selected_hypothesis_id: str | None = None,
        selected_hypothesis_confidence: float | None = None,
        selected_hypothesis_reason: str | None = None,
        model: str | None = None,
        request_id: str | None = None,
        total_tokens: int = 0,
        latency_ms: int = 0,
        estimated_cost_cny: float = 0.0,
        retrieval_query: str = "",
        retrieval_query_zh: str | None = None,
        refinement_queries: tuple[str, ...] = (),
        retrieval_snapshot: dict[str, Any] | None = None,
        evidence_snapshot: dict[str, Any] | None = None,
        evidence_snapshot_sha256: str = "0" * 64,
        research_output: dict[str, Any] | None = None,
        provenance: dict[str, Any] | None = None,
        source_type: str = "batch",
        source_job_id: str | None = None,
    ) -> StoredScience125Report:
        now = utc_now()
        with self.session_factory() as session:
            batch = session.scalar(select(Science125BatchRow).where(Science125BatchRow.id == batch_id, Science125BatchRow.user_id == user_id))
            if batch is None:
                raise ValueError("BATCH_NOT_FOUND")
            row = session.scalar(
                select(Science125ReportRow).where(
                    Science125ReportRow.batch_id == batch_id,
                    Science125ReportRow.user_id == user_id,
                    Science125ReportRow.question_id == question_id,
                )
            )
            if row is None:
                row = Science125ReportRow(
                    id=str(uuid.uuid4()),
                    batch_id=batch_id,
                    user_id=user_id,
                    question_id=question_id,
                    created_at=now,
                    updated_at=now,
                    question=question,
                    benchmark_domain=benchmark_domain,
                    primary_subdomain=primary_subdomain,
                    status=status,
                    attempt_number=attempt_number,
                    evidence_status=evidence_status,
                    selected_evidence_count=selected_evidence_count,
                )
                session.add(row)
            row.question = question
            row.question_zh = question_zh
            row.benchmark_domain = benchmark_domain
            row.primary_subdomain = primary_subdomain
            row.status = status
            row.attempt_number = attempt_number
            row.selected_hypothesis_id = selected_hypothesis_id
            row.selected_hypothesis_confidence = selected_hypothesis_confidence
            row.selected_hypothesis_reason = selected_hypothesis_reason
            row.evidence_status = evidence_status
            row.selected_evidence_count = selected_evidence_count
            row.provider_families_json = _dump(list(provider_families))
            row.model = model
            row.request_id = request_id
            row.total_tokens = int(total_tokens or 0)
            row.latency_ms = int(latency_ms or 0)
            row.estimated_cost_cny = float(estimated_cost_cny or 0.0)
            row.retrieval_query = retrieval_query
            row.retrieval_query_zh = retrieval_query_zh
            row.refinement_queries_json = _dump(list(refinement_queries))
            row.retrieval_snapshot_json = _dump(retrieval_snapshot or {})
            row.evidence_snapshot_json = _dump(evidence_snapshot or {})
            row.evidence_snapshot_sha256 = evidence_snapshot_sha256
            row.research_output_json = _dump(research_output) if research_output is not None else None
            row.provenance_json = _dump(provenance) if provenance is not None else None
            row.source_type = source_type
            row.source_job_id = source_job_id
            row.updated_at = now
            item = session.scalar(
                select(Science125BatchItemRow).where(
                    Science125BatchItemRow.batch_id == batch_id,
                    Science125BatchItemRow.user_id == user_id,
                    Science125BatchItemRow.question_id == question_id,
                )
            )
            if item is not None:
                item.status = status
                item.attempt_number = attempt_number
                item.report_id = row.id
                item.updated_at = now
            session.commit()
            return self._report(row)

    def set_item_error(
        self,
        *,
        user_id: int,
        batch_id: str,
        question_id: str,
        status: str,
        code: str,
        message: str,
    ) -> None:
        now = utc_now()
        with self.session_factory() as session:
            session.execute(
                update(Science125BatchItemRow)
                .where(
                    Science125BatchItemRow.user_id == user_id,
                    Science125BatchItemRow.batch_id == batch_id,
                    Science125BatchItemRow.question_id == question_id,
                )
                .values(
                    status=status,
                    last_error_code=code[:80],
                    last_error_message=message,
                    updated_at=now,
                )
            )
            session.commit()

    def get_report_for_user(self, *, user_id: int, report_id: str) -> StoredScience125Report | None:
        with self.session_factory() as session:
            row = session.scalar(select(Science125ReportRow).where(Science125ReportRow.id == report_id, Science125ReportRow.user_id == user_id))
            return self._report(row) if row else None

    def get_report_by_source_job(self, *, user_id: int, source_job_id: str) -> StoredScience125Report | None:
        with self.session_factory() as session:
            row = session.scalar(
                select(Science125ReportRow).where(
                    Science125ReportRow.user_id == user_id,
                    Science125ReportRow.source_job_id == source_job_id,
                )
            )
            return self._report(row) if row else None

    def list_exports_for_report(self, *, user_id: int, report_id: str) -> list[StoredScience125Export]:
        with self.session_factory() as session:
            rows = session.scalars(
                select(Science125ExportRow).where(
                    Science125ExportRow.user_id == user_id,
                    Science125ExportRow.report_id == report_id,
                ).order_by(Science125ExportRow.created_at)
            ).all()
            return [self._export(row) for row in rows]

    def list_reports(
        self,
        *,
        user_id: int,
        batch_id: str | None = None,
        question_id: str | None = None,
        status: str | None = None,
        benchmark_domain: str | None = None,
        sort_by: str = "updatedAt",
        sort_order: str = "desc",
    ) -> list[StoredScience125Report]:
        with self.session_factory() as session:
            statement = select(Science125ReportRow).where(Science125ReportRow.user_id == user_id)
            if batch_id:
                statement = statement.where(Science125ReportRow.batch_id == batch_id)
            if question_id:
                statement = statement.where(Science125ReportRow.question_id == question_id)
            if status:
                statement = statement.where(Science125ReportRow.status == status)
            if benchmark_domain:
                statement = statement.where(Science125ReportRow.benchmark_domain == benchmark_domain)
            column = (
                Science125ReportRow.selected_hypothesis_confidence
                if sort_by == "selectedHypothesisConfidence"
                else Science125ReportRow.question_id
                if sort_by == "questionId"
                else Science125ReportRow.updated_at
            )
            statement = statement.order_by(column.asc() if sort_order == "asc" else column.desc())
            return [self._report(row) for row in session.scalars(statement).all()]

    def list_reports_by_question(self, *, user_id: int, question_id: str) -> list[StoredScience125Report]:
        return self.list_reports(user_id=user_id, question_id=question_id)

    def create_export(
        self,
        *,
        user_id: int,
        batch_id: str,
        report_id: str | None,
        format: str,
        file_name: str,
        mime_type: str,
        size_bytes: int,
        managed_path: Path,
    ) -> StoredScience125Export:
        now = utc_now()
        row = Science125ExportRow(
            id=str(uuid.uuid4()),
            batch_id=batch_id,
            report_id=report_id,
            user_id=user_id,
            format=format,
            file_name=Path(file_name).name[:255],
            mime_type=mime_type[:120],
            size_bytes=int(size_bytes),
            managed_path=str(Path(managed_path).resolve()),
            created_at=now,
        )
        with self.session_factory() as session:
            session.add(row)
            session.commit()
            return self._export(row)

    def get_export_for_user(self, *, user_id: int, export_id: str) -> StoredScience125Export | None:
        with self.session_factory() as session:
            row = session.scalar(select(Science125ExportRow).where(Science125ExportRow.id == export_id, Science125ExportRow.user_id == user_id))
            return self._export(row) if row else None

    def list_exports_for_report(self, *, user_id: int, report_id: str) -> list[StoredScience125Export]:
        with self.session_factory() as session:
            rows = session.scalars(
                select(Science125ExportRow)
                .where(Science125ExportRow.user_id == user_id, Science125ExportRow.report_id == report_id)
                .order_by(Science125ExportRow.created_at.desc())
            ).all()
            return [self._export(row) for row in rows]

    def list_exports_for_batch(self, *, user_id: int, batch_id: str) -> list[StoredScience125Export]:
        with self.session_factory() as session:
            rows = session.scalars(
                select(Science125ExportRow)
                .where(Science125ExportRow.user_id == user_id, Science125ExportRow.batch_id == batch_id)
                .order_by(Science125ExportRow.created_at.desc())
            ).all()
            return [self._export(row) for row in rows]

    def dispose(self) -> None:
        self.engine.dispose()


__all__ = [
    "Science125ReportStore",
    "StoredScience125Batch",
    "StoredScience125BatchItem",
    "StoredScience125Export",
    "StoredScience125Report",
]
