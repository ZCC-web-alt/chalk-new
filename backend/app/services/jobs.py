from __future__ import annotations

import hashlib
import json
import logging
import mimetypes
import os
import shutil
import tempfile
import uuid
import zipfile
from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from threading import RLock
from typing import Any, Callable, Mapping

from app.core.config import get_settings
from app.core.legacy import (
    agent_framework,
    chem_structure,
    db,
    document_image_extractor,
    evidence_database,
    hazard_db,
    hitl_trace,
    hypothesis_workflow_exporter,
    literature_search,
    llm_client,
    pdf_utils,
    rag,
    report_renderer,
    scientific_evidence_rag,
    multimodal,
    multimodal_pipeline,
    data_cleaner,
    data_miner,
    vasp_defaults,
)
from app.services import api_keys
from app.services.analysis_store import DocumentAnalysisStore
from app.services.document_analysis import (
    ModelOutputError,
    lookup_pubchem_hazard,
    merge_glossary,
    merge_reaction_results,
    merge_sop_results,
    normalize_local_hazard,
    parse_reaction_result,
    parse_sop_result,
    parse_translation_result,
    segment_texts,
)
from app.services.document_excerpts import DocumentPageExcerptError, extract_document_page_excerpt
from app.services.hitl import hitl_feedback_broker
from app.services.hypothesis_store import HypothesisArtifactStore
from app.services.hypotheses import (
    HypothesisSourceDocument,
    build_hypothesis_input,
    parse_json_object,
    sanitize_public_data,
    secure_report_html,
)
from app.services.job_store import StoredJob, WebJobStore
from app.services.model_call_ledger import ModelCallLedgerStore
from app.services.research_generation import (
    ResearchGenerationRequest,
    ResearchGenerationCallError,
    ResearchGenerationService,
    ResearchGenerationValidationError,
)
from app.services.science125_catalog import (
    Science125RoutingError,
    get_science125_route,
    is_science125_pilot_enabled,
)
from app.services.science_workspace_store import ScienceWorkspaceStore
from app.services.science125_context import Science125ContextError, load_science125_context_index
from app.services.science125_retrieval import (
    EvidenceRecord,
    ProviderRateStateStore,
    default_provider_adapters,
    get_provider,
    get_science125_retrieval_profile,
    profile_readiness,
    search_science125,
)
from app.services.science125_queries import build_science125_query_plan
from app.services.science125_relevance import SCORING_VERSION, qualify_science125_evidence
from app.services.modeling_generation import (
    MsGuideExtraction,
    VaspExtraction,
    build_vasp_workspace_result,
    is_valid_potcar_potential_name,
    merge_vasp_extractions,
    parse_json_object as parse_model_json,
    parse_structure_content,
    segment_modeling_text,
    select_document_modeling_text,
    prepare_modeling_changes,
)
from app.services.multimodal_analysis import (
    build_multimodal_context,
    camelize,
    cleaned_point_to_dict,
    dataframe_model_context,
    rebuild_multimodal_derived_result,
    safe_result_json,
    select_multimodal_literature_context,
    workbook_coverage,
)


logger = logging.getLogger(__name__)


class SafeJobError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class JobCancelled(RuntimeError):
    pass


class ActiveHypothesisJobError(RuntimeError):
    def __init__(self, existing_job_id: str):
        super().__init__("A hypothesis generation job is already active for this user.")
        self.existing_job_id = existing_job_id


@dataclass(slots=True)
class WebHypothesisFeedback:
    action: str
    feedback_text: str = ""
    edited_hypothesis: dict[str, Any] | None = None
    debate_stance: str = "neutral"
    selected_attack_indices: list[int] = field(default_factory=list)
    structured_feedback: dict[str, Any] = field(default_factory=dict)
    manual_revision_diff: list[dict[str, Any]] = field(default_factory=list)

    @property
    def user_feedback_text(self) -> str:
        return self.feedback_text

    @property
    def user_edited_hypothesis(self) -> dict[str, Any] | None:
        return self.edited_hypothesis


class JobService:
    def __init__(self, store: WebJobStore | None = None):
        self._store_lock = RLock()
        self._store = store or WebJobStore(get_settings().web_db_path)
        self._analysis_store = DocumentAnalysisStore(self._store.path)
        self._hypothesis_store = HypothesisArtifactStore(self._store.path)
        self._model_call_ledger = ModelCallLedgerStore(self._store.path)
        self._science125_rate_store = ProviderRateStateStore(self._store.path)
        self._science_store: ScienceWorkspaceStore | None = None
        self._uploads_dir = self._store.path.parent / "uploads"
        self._uploads_dir.mkdir(parents=True, exist_ok=True)
        self._assets_dir = self._store.path.parent / "analysis-assets"
        self._assets_dir.mkdir(parents=True, exist_ok=True)
        self._hypothesis_assets_dir = self._store.path.parent / "hypothesis-assets"
        self._hypothesis_assets_dir.mkdir(parents=True, exist_ok=True)
        self._multimodal_assets_dir = self._store.path.parent / "multimodal-assets"
        self._multimodal_assets_dir.mkdir(parents=True, exist_ok=True)
        self._modeling_assets_dir = self._store.path.parent / "modeling-assets"
        self._modeling_assets_dir.mkdir(parents=True, exist_ok=True)
        self._evidence_imports_dir = self._store.path.parent / "evidence-imports"
        self._evidence_imports_dir.mkdir(parents=True, exist_ok=True)
        self._max_analysis_chars = get_settings().max_analysis_chars
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="chalk-job")
        self._hypothesis_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="chalk-hypothesis")
        self._handlers: dict[str, Callable[[StoredJob], Any]] = {
            "pdf_import": self._run_pdf_import,
            "pdf_reimport": self._run_pdf_reimport,
            "rag_qa": self._run_rag_qa,
            "literature_search": self._run_literature_search,
            "evidence_manifest_import": self._run_evidence_manifest_import,
            "lab_suggest": self._run_lab_suggest,
            "prompt_polish": self._run_prompt_polish,
            "hypothesis_generate": self._run_hypothesis_generate,
            "hypothesis_report": self._run_hypothesis_report,
            "hypothesis_workflow_export": self._run_hypothesis_workflow_export,
            "evidence_query": self._run_evidence_query,
            "document_analysis": self._run_document_analysis,
            "document_compare": self._run_document_compare,
            "multimodal_analyze": self._run_multimodal_analyze,
            "modeling_generate": self._run_modeling_generate,
            "modeling_export": self._run_modeling_export,
        }

    @property
    def store(self) -> WebJobStore:
        with self._store_lock:
            return self._store

    @property
    def analysis_store(self) -> DocumentAnalysisStore:
        with self._store_lock:
            return self._analysis_store

    @property
    def hypothesis_store(self) -> HypothesisArtifactStore:
        with self._store_lock:
            return self._hypothesis_store

    @property
    def model_call_ledger(self) -> ModelCallLedgerStore:
        with self._store_lock:
            return self._model_call_ledger

    @property
    def science125_rate_store(self) -> ProviderRateStateStore:
        with self._store_lock:
            return self._science125_rate_store

    @property
    def science_store(self) -> ScienceWorkspaceStore:
        with self._store_lock:
            if self._science_store is None:
                self._science_store = ScienceWorkspaceStore(self._store.path)
            return self._science_store

    @property
    def assets_dir(self) -> Path:
        with self._store_lock:
            return self._assets_dir

    @property
    def uploads_dir(self) -> Path:
        with self._store_lock:
            return self._uploads_dir

    @property
    def hypothesis_assets_dir(self) -> Path:
        with self._store_lock:
            return self._hypothesis_assets_dir

    @property
    def multimodal_assets_dir(self) -> Path:
        with self._store_lock:
            return self._multimodal_assets_dir

    @property
    def modeling_assets_dir(self) -> Path:
        with self._store_lock:
            return self._modeling_assets_dir

    @property
    def evidence_imports_dir(self) -> Path:
        with self._store_lock:
            return self._evidence_imports_dir

    def configure_store(self, path: Path) -> None:
        resolved = Path(path).resolve()
        with self._store_lock:
            if self._store.path == resolved:
                return
            previous = self._store
            previous_analysis = self._analysis_store
            previous_hypothesis = self._hypothesis_store
            previous_ledger = self._model_call_ledger
            previous_rate_store = self._science125_rate_store
            previous_science = self._science_store
            self._store = WebJobStore(resolved)
            self._analysis_store = DocumentAnalysisStore(resolved)
            self._hypothesis_store = HypothesisArtifactStore(resolved)
            self._model_call_ledger = ModelCallLedgerStore(resolved)
            self._science125_rate_store = ProviderRateStateStore(resolved)
            self._science_store = None
            self._uploads_dir = resolved.parent / "uploads"
            self._uploads_dir.mkdir(parents=True, exist_ok=True)
            self._assets_dir = resolved.parent / "analysis-assets"
            self._assets_dir.mkdir(parents=True, exist_ok=True)
            self._hypothesis_assets_dir = resolved.parent / "hypothesis-assets"
            self._hypothesis_assets_dir.mkdir(parents=True, exist_ok=True)
            self._multimodal_assets_dir = resolved.parent / "multimodal-assets"
            self._multimodal_assets_dir.mkdir(parents=True, exist_ok=True)
            self._modeling_assets_dir = resolved.parent / "modeling-assets"
            self._modeling_assets_dir.mkdir(parents=True, exist_ok=True)
            self._evidence_imports_dir = resolved.parent / "evidence-imports"
            self._evidence_imports_dir.mkdir(parents=True, exist_ok=True)
            previous.dispose()
            previous_analysis.dispose()
            previous_hypothesis.dispose()
            previous_ledger.dispose()
            previous_rate_store.dispose()
            if previous_science is not None:
                previous_science.dispose()

    def dispose_stores(self) -> None:
        """Release SQLite handles owned by the Web job service."""
        with self._store_lock:
            self._analysis_store.dispose()
            self._hypothesis_store.dispose()
            self._model_call_ledger.dispose()
            self._science125_rate_store.dispose()
            if self._science_store is not None:
                self._science_store.dispose()
                self._science_store = None
            self._store.dispose()

    @staticmethod
    def _resource_for(job_type: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        if job_type == "document_analysis":
            return {
                "analysisType": payload.get("analysisType"),
                "documentId": payload.get("documentId"),
            }
        if job_type == "pdf_reimport":
            return {"documentId": payload.get("documentId")}
        if job_type == "lab_suggest":
            return {"recordId": payload.get("recordId")}
        if job_type == "evidence_manifest_import":
            return {
                "datasetName": payload.get("datasetName"),
                "fileName": payload.get("originalFilename"),
            }
        if job_type == "document_compare":
            return {"documentIds": sorted(payload.get("documentIds") or [])}
        if job_type == "literature_search":
            science125_id = str(payload.get("science125Id") or "").strip()
            return {"science125Id": science125_id} if science125_id else None
        if job_type == "hypothesis_generate":
            resource = {
                "sourceDocumentIds": list(payload.get("sourceDocIds") or []),
                "domain": str(payload.get("domain") or ""),
            }
            science125_id = str(payload.get("science125Id") or "").strip()
            if science125_id:
                resource["science125Id"] = science125_id
            return resource
        if job_type == "hypothesis_report":
            return {
                "hypothesisId": payload.get("hypothesisId"),
                "artifactKind": "interactive_report_html",
            }
        if job_type == "hypothesis_workflow_export":
            return {
                "hypothesisId": payload.get("hypothesisId"),
                "artifactKind": "workflow_package_zip",
            }
        if job_type == "multimodal_analyze":
            return {
                "sourceIds": [
                    source.get("assetId") for source in payload.get("sources") or [] if source.get("assetId")
                ],
            }
        if job_type == "modeling_generate":
            source = payload.get("source") or {}
            return {
                "sourceType": source.get("type"),
                "documentId": source.get("documentId"),
                "hypothesisId": source.get("hypothesisId"),
                "requestHash": hashlib.sha256(
                    json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
                ).hexdigest()[:16],
            }
        if job_type == "modeling_export":
            return {"workspaceId": payload.get("workspaceId")}
        return None

    @staticmethod
    def _multimodal_request_fingerprint(payload: dict[str, Any]) -> str:
        normalized_sources = []
        for source in payload.get("sources") or []:
            if not isinstance(source, dict):
                continue
            normalized_sources.append({
                "sourceType": source.get("sourceType"),
                "assetId": source.get("assetId"),
                "documentId": source.get("documentId"),
                "sheetNames": sorted(str(value) for value in source.get("sheetNames") or []),
            })
        normalized_sources.sort(key=lambda value: (
            str(value.get("sourceType") or ""),
            int(value.get("documentId") or 0),
            str(value.get("assetId") or ""),
        ))
        normalized = {
            "sources": normalized_sources,
            "question": str(payload.get("question") or "").strip(),
            "useLiteratureContext": bool(payload.get("useLiteratureContext", True)),
        }
        return hashlib.sha256(
            json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()

    @staticmethod
    def _result_resource_for(job_type: str, result: Any) -> dict[str, Any]:
        if not isinstance(result, dict):
            return {}
        if job_type == "multimodal_analyze":
            run_id = str(result.get("runId") or "").strip()
            return {"runId": run_id} if run_id else {}
        if job_type == "modeling_generate":
            workspace_id = str(result.get("workspaceId") or "").strip()
            return {"workspaceId": workspace_id} if workspace_id else {}
        if job_type == "modeling_export":
            artifact = result.get("artifact")
            artifact_id = str(artifact.get("id") or "").strip() if isinstance(artifact, dict) else ""
            return {"artifactId": artifact_id} if artifact_id else {}
        return {}

    @classmethod
    def public_resource(cls, job: StoredJob) -> dict[str, Any] | None:
        payload_resource = cls._resource_for(job.type, job.payload) or {}
        result_resource = cls._result_resource_for(job.type, job.result)
        resource = {**payload_resource, **result_resource}
        return resource or None

    def to_public_dict(self, job: StoredJob) -> dict[str, Any]:
        return {**job.to_dict(), "resource": self.public_resource(job)}

    def fail_interrupted_jobs(self) -> int:
        return self.store.fail_interrupted_jobs()

    def interrupt_for_shutdown(self) -> int:
        affected = self.fail_interrupted_jobs()
        hitl_feedback_broker.cancel_all()
        return affected

    def create(self, user_id: int, job_type: str, payload: dict[str, Any]) -> StoredJob:
        with self._store_lock:
            if job_type not in self._handlers:
                raise ValueError(f"Unsupported job type: {job_type}")
            if job_type == "hypothesis_generate":
                requested = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                active = [
                    job
                    for job in self._store.list_for_user(user_id, job_type=job_type)
                    if job.status in {"QUEUED", "RUNNING", "WAITING_FOR_FEEDBACK"}
                ]
                for existing in active:
                    existing_payload = json.dumps(
                        existing.payload,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                    if existing_payload == requested:
                        return existing
                if active:
                    raise ActiveHypothesisJobError(active[0].id)
            if job_type in {
                "document_analysis",
                "document_compare",
                "hypothesis_report",
                "hypothesis_workflow_export",
                "multimodal_analyze",
                "modeling_generate",
                "modeling_export",
            }:
                requested_resource = self._resource_for(job_type, payload)
                for existing in self._store.list_for_user(user_id, job_type=job_type):
                    if existing.status not in {"QUEUED", "RUNNING"}:
                        continue
                    if job_type == "multimodal_analyze":
                        if self._multimodal_request_fingerprint(existing.payload) == self._multimodal_request_fingerprint(payload):
                            return existing
                        continue
                    if self.public_resource(existing) == requested_resource:
                        return existing
            job = self._store.create(user_id=user_id, job_type=job_type, payload=payload)
            executor = self._hypothesis_executor if job_type == "hypothesis_generate" else self._executor
            executor.submit(self._execute, job.id)
            return job

    def list_for_user(
        self,
        user_id: int,
        *,
        job_type: str | None = None,
        status: str | None = None,
    ) -> list[StoredJob]:
        return self.store.list_for_user(user_id, job_type=job_type, status=status)

    def get_for_user(self, user_id: int, job_id: str) -> StoredJob | None:
        return self.store.get_for_user(user_id, job_id)

    def revise_multimodal_run(
        self,
        *,
        user_id: int,
        run_id: str,
        expected_revision: int,
        corrections: dict[str, Any],
    ):
        item_corrections = corrections.get("itemCorrections") or []
        data_points_changed = any(
            isinstance(item, dict) and "dataPoints" in item
            for item in item_corrections
        )
        return self.science_store.update_multimodal_run(
            user_id=user_id,
            run_id=run_id,
            expected_revision=expected_revision,
            corrections=corrections,
            result_transform=lambda result: rebuild_multimodal_derived_result(
                result,
                miner_module=data_miner() if data_points_changed else None,
                recompute_relationships=data_points_changed,
            ),
        )

    def cancel(self, user_id: int, job_id: str) -> StoredJob | None:
        job = self.get_for_user(user_id, job_id)
        if not job:
            return None
        if job.status in {"QUEUED", "RUNNING", "WAITING_FOR_FEEDBACK"}:
            cancelled = self.store.transition(
                job.id,
                from_statuses=("QUEUED", "RUNNING", "WAITING_FOR_FEEDBACK"),
                require_not_cancelled=False,
                cancel_requested=True,
                status="CANCELLED",
                message="Cancellation requested.",
            )
            if job.type == "hypothesis_generate":
                hitl_feedback_broker.cancel(job.id)
            return cancelled
        return job

    def submit_hypothesis_feedback(
        self,
        user_id: int,
        job_id: str,
        feedback: dict[str, Any],
    ) -> StoredJob | None:
        job = self.get_for_user(user_id, job_id)
        if not job or job.status != "WAITING_FOR_FEEDBACK":
            return job
        if not hitl_feedback_broker.has_waiter(job.id):
            return job
        resumed = self.store.transition(
            job.id,
            from_statuses=("WAITING_FOR_FEEDBACK",),
            status="RUNNING",
            stage="HITL_FEEDBACK_RECEIVED",
            feedback_prompt=None,
            message="Feedback received. Continuing hypothesis generation.",
        )
        if not resumed or resumed.status != "RUNNING":
            return resumed
        if not hitl_feedback_broker.submit(job.id, feedback):
            self._fail(resumed, "FEEDBACK_CHANNEL_CLOSED", "The feedback channel is no longer available.")
            return self.store.get(job.id)
        return resumed

    def resume_with_feedback(self, user_id: int, job_id: str, feedback: dict[str, Any]) -> StoredJob | None:
        job = self.get_for_user(user_id, job_id)
        if not job or job.status != "WAITING_FOR_FEEDBACK":
            return job
        payload = dict(job.payload)
        payload.setdefault("feedback", []).append(feedback)
        resumed = self.store.transition(
            job.id,
            from_statuses=("WAITING_FOR_FEEDBACK",),
            payload=payload,
            status="QUEUED",
            feedback_prompt=None,
            cancel_requested=False,
        )
        if resumed and resumed.status == "QUEUED":
            self._executor.submit(self._execute, job.id)
        return resumed

    def _execute(self, job_id: str) -> None:
        job = self.store.get(job_id)
        if not job or job.status == "CANCELLED":
            return
        job = self.store.transition(
            job.id,
            from_statuses=("QUEUED",),
            status="RUNNING",
            progress=max(job.progress, 1),
            message="Started.",
        )
        if not job or job.status != "RUNNING":
            return
        handler = self._handlers.get(job.type)
        if handler is None:
            self._fail(job, "UNSUPPORTED_JOB", "Unsupported job type.")
            return
        try:
            result = handler(job)
            current = self.store.get(job.id)
            if not current:
                return
            if current.cancel_requested or current.status == "CANCELLED":
                return
            if current.status == "WAITING_FOR_FEEDBACK":
                return
            public_result = dict(result) if isinstance(result, dict) else result
            if isinstance(public_result, dict):
                public_result.pop("_reportSnapshot", None)
            completed = self.store.transition(
                current.id,
                from_statuses=("RUNNING",),
                status="SUCCEEDED",
                progress=100,
                message="Completed.",
                result=public_result,
            )
            if completed and job.type == "hypothesis_generate" and job.payload.get("science125Id"):
                # Report adoption is a separate, idempotent persistence step. A report failure
                # must never rewrite an already successful Qwen job as failed.
                try:
                    from app.services.science125_report_service import get_science125_report_service

                    search_job_id = str(job.payload.get("literatureSearchJobId") or "").strip()
                    literature_job = self.store.get_for_user(job.user_id, search_job_id) if search_job_id else None
                    import_job = replace(completed, result=result)
                    report = get_science125_report_service().import_interactive_job(
                        job=import_job,
                        literature_job=literature_job,
                    )
                    enriched_result = dict(public_result) if isinstance(public_result, dict) else {"value": public_result}
                    enriched_result["science125Report"] = {
                        "reportId": report.id,
                        "batchId": report.batch_id,
                        "sourceType": report.source_type,
                        "sourceJobId": report.source_job_id,
                    }
                    self.store.update(job.id, result=enriched_result)
                except Exception:
                    logger.exception("Science 125 job %s succeeded but report adoption failed", job.id)
        except JobCancelled:
            current = self.store.get(job.id) or job
            self.store.transition(
                current.id,
                from_statuses=("QUEUED", "RUNNING", "WAITING_FOR_FEEDBACK"),
                require_not_cancelled=False,
                status="CANCELLED",
                cancel_requested=True,
                message="Cancelled.",
            )
        except SafeJobError as exc:
            self._fail(job, exc.code, exc.message)
        except ValueError:
            logger.info("Job %s (%s) rejected invalid input", job.id, job.type, exc_info=True)
            self._fail(job, "VALIDATION_ERROR", "The job input was invalid.")
        except Exception:
            logger.exception("Job %s (%s) failed", job.id, job.type)
            self._fail(job, "JOB_FAILED", "The job could not be completed.")

    def _update_progress(self, job: StoredJob, **changes: Any) -> StoredJob:
        updated = self.store.transition(job.id, from_statuses=("RUNNING",), **changes)
        return updated or job

    def _fail(self, job: StoredJob, code: str, message: str) -> None:
        self.store.transition(
            job.id,
            from_statuses=("QUEUED", "RUNNING"),
            status="FAILED",
            error={"code": code, "message": message},
            message=message,
        )

    def _raise_if_cancelled(self, job: StoredJob) -> None:
        current = self.store.get(job.id)
        if current and (current.cancel_requested or current.status in {"CANCELLED", "FAILED"}):
            raise JobCancelled()

    def _llm_config_for_user(self, user_id: int):
        key = api_keys.api_key_store.get_key(user_id, "dashscope")
        return llm_client().LLMConfig(api_key=key) if key else llm_client().LLMConfig()

    @staticmethod
    def _raise_for_llm_error(value: str) -> None:
        prefix = getattr(llm_client(), "LLM_ERROR_PREFIX", "__CHALK_LLM_ERROR__:")
        if not value.startswith(prefix):
            return
        try:
            payload = json.loads(value[len(prefix) :])
        except ValueError:
            payload = {}
        message = str(payload.get("message") or "The model service returned an error.")
        raise SafeJobError("LLM_SERVICE_ERROR", message)

    def _run_pdf_import(self, job: StoredJob) -> dict[str, Any]:
        stored_path = Path(str(job.payload.get("storedPath") or "")).resolve()
        original_filename = str(job.payload.get("originalFilename") or stored_path.name)
        title = str(job.payload.get("title") or "").strip()
        upload_root = (self.uploads_dir / str(job.user_id)).resolve()
        if stored_path.is_symlink() or not stored_path.is_relative_to(upload_root) or not stored_path.is_file():
            raise SafeJobError("UPLOAD_NOT_FOUND", "The uploaded PDF is no longer available.")

        succeeded = False
        session = None
        try:
            self._update_progress(job, progress=10, message="Extracting PDF text.")
            text = pdf_utils().extract_text_from_pdf(str(stored_path))
            if not text.strip():
                raise SafeJobError("EMPTY_DOCUMENT", "No readable text was found in the PDF.")
            self._raise_if_cancelled(job)

            chunks = pdf_utils().split_text_into_chunks(text)
            if not chunks:
                raise SafeJobError("EMPTY_DOCUMENT", "No searchable text chunks were produced.")
            user_key = api_keys.api_key_store.get_key(job.user_id, "dashscope")
            vectors = rag().embed_texts([chunk[:8192] for chunk in chunks], api_key=user_key).astype("float32")
            self._raise_if_cancelled(job)
            self._update_progress(job, progress=70, message="Saving document and embeddings.")

            database = db()
            session = database.get_session()
            document = database.Document(
                user_id=job.user_id,
                title=title or original_filename,
                source_type="pdf",
                source_path=str(stored_path),
                summary=None,
            )
            session.add(document)
            session.flush()
            for index, chunk in enumerate(chunks):
                session.add(
                    database.DocumentChunk(
                        document_id=document.id,
                        user_id=job.user_id,
                        order=index,
                        text=chunk,
                        embedding=vectors[index].tobytes(),
                        chunk_type="text",
                    )
                )
            self._raise_if_cancelled(job)
            session.commit()
            session.refresh(document)
            succeeded = True
            return {"documentId": document.id, "title": document.title, "chunkCount": len(chunks)}
        except Exception:
            if session is not None:
                session.rollback()
            raise
        finally:
            if session is not None:
                session.close()
            if not succeeded:
                stored_path.unlink(missing_ok=True)

    def _run_pdf_reimport(self, job: StoredJob) -> dict[str, Any]:
        document_id = int(job.payload.get("documentId") or 0)
        stored_path = Path(str(job.payload.get("storedPath") or "")).resolve()
        upload_root = (self.uploads_dir / str(job.user_id)).resolve()
        if stored_path.is_symlink() or not stored_path.is_relative_to(upload_root) or not stored_path.is_file():
            raise SafeJobError("UPLOAD_NOT_FOUND", "The replacement PDF is no longer available.")

        session = None
        succeeded = False
        previous_managed_path: Path | None = None
        try:
            self._update_progress(job, progress=10, message="Extracting replacement PDF text.")
            text = pdf_utils().extract_text_from_pdf(str(stored_path))
            if not text.strip():
                raise SafeJobError("EMPTY_DOCUMENT", "No readable text was found in the replacement PDF.")
            chunks = pdf_utils().split_text_into_chunks(text)
            if not chunks:
                raise SafeJobError("EMPTY_DOCUMENT", "No searchable text chunks were produced.")
            user_key = api_keys.api_key_store.get_key(job.user_id, "dashscope")
            vectors = rag().embed_texts([chunk[:8192] for chunk in chunks], api_key=user_key).astype("float32")
            self._raise_if_cancelled(job)
            self._update_progress(job, progress=70, message="Replacing document index.")

            database = db()
            session = database.get_session()
            document = session.query(database.Document).filter(
                database.Document.id == document_id,
                database.Document.user_id == job.user_id,
            ).first()
            if not document:
                raise SafeJobError("DOCUMENT_NOT_FOUND", "The document was not found.")
            if document.source_path:
                candidate = Path(document.source_path).resolve()
                if candidate.is_relative_to(upload_root) and not candidate.is_symlink():
                    previous_managed_path = candidate
            session.query(database.DocumentChunk).filter(
                database.DocumentChunk.document_id == document_id,
                database.DocumentChunk.user_id == job.user_id,
            ).delete(synchronize_session=False)
            for index, chunk in enumerate(chunks):
                session.add(database.DocumentChunk(
                    document_id=document_id,
                    user_id=job.user_id,
                    order=index,
                    text=chunk,
                    embedding=vectors[index].tobytes(),
                    chunk_type="text",
                ))
            document.source_type = "pdf"
            document.source_path = str(stored_path)
            self._raise_if_cancelled(job)
            session.commit()
            succeeded = True
            if previous_managed_path and previous_managed_path != stored_path:
                previous_managed_path.unlink(missing_ok=True)
            return {"documentId": document_id, "title": document.title, "chunkCount": len(chunks)}
        except Exception:
            if session is not None:
                session.rollback()
            raise
        finally:
            if session is not None:
                session.close()
            if not succeeded:
                stored_path.unlink(missing_ok=True)

    def _run_rag_qa(self, job: StoredJob) -> dict[str, Any]:
        question = str(job.payload.get("question") or "").strip()
        if not question:
            raise SafeJobError("QUESTION_REQUIRED", "A question is required.")
        top_k = int(job.payload.get("topK") or 5)
        document_id = job.payload.get("documentId")
        document_id = int(document_id) if document_id not in (None, "") else None
        api_key = api_keys.api_key_store.get_key(job.user_id, "dashscope")
        session = db().get_session()
        try:
            if document_id is not None:
                owned = session.query(db().Document.id).filter(
                    db().Document.id == document_id,
                    db().Document.user_id == job.user_id,
                ).first()
                if not owned:
                    raise SafeJobError("DOCUMENT_NOT_FOUND", "The selected document was not found.")
            self._update_progress(job, progress=20, message="Searching document chunks.")
            chunks = rag().search_with_context(
                session,
                job.user_id,
                question,
                top_k=top_k,
                doc_id=document_id,
                api_key=api_key,
            )
            if not chunks:
                raise SafeJobError("NO_RELEVANT_CONTEXT", "No relevant document content was found.")
            self._raise_if_cancelled(job)
            self._update_progress(job, progress=65, message="Generating answer.")
            try:
                answer = llm_client().call_llm(question, chunks, config=self._llm_config_for_user(job.user_id))
            except ValueError as exc:
                raise SafeJobError("API_KEY_REQUIRED", str(exc)) from exc
            self._raise_for_llm_error(answer)
            citations = [
                {
                    "index": index,
                    "documentId": chunk.get("document_id"),
                    "chunkId": chunk.get("chunk_id"),
                    "sourceTitle": str(chunk.get("source_title") or ""),
                    "excerpt": str(chunk.get("text") or "")[:600],
                    "score": float(chunk.get("score") or 0.0),
                    "contextMarkers": str(chunk.get("context_markers") or ""),
                }
                for index, chunk in enumerate(chunks, 1)
            ]
            return {"answer": answer, "chunks": chunks, "citations": citations}
        finally:
            session.close()

    def _required_llm_config(self, user_id: int):
        key = api_keys.api_key_store.get_key(user_id, "dashscope")
        if not key and not (os.getenv("DASHSCOPE_API_KEY") or os.getenv("QWEN_API_KEY")):
            raise SafeJobError(
                "API_KEY_REQUIRED",
                "Configure a DashScope API Key before running this analysis.",
            )
        return llm_client().LLMConfig(api_key=key) if key else llm_client().LLMConfig()

    def _load_owned_document(self, user_id: int, document_id: int) -> dict[str, Any]:
        database = db()
        session = database.get_session()
        try:
            document = session.query(database.Document).filter(
                database.Document.id == document_id,
                database.Document.user_id == user_id,
            ).first()
            if not document:
                raise SafeJobError("DOCUMENT_NOT_FOUND", "The selected document was not found.")
            chunks = session.query(database.DocumentChunk).filter(
                database.DocumentChunk.document_id == document_id,
                database.DocumentChunk.user_id == user_id,
            ).order_by(database.DocumentChunk.order.asc()).all()
            texts = [str(chunk.text or "") for chunk in chunks]
            full_text = "\n\n".join(texts)
            return {
                "id": document.id,
                "title": document.title,
                "sourceType": document.source_type,
                "sourcePath": document.source_path,
                "texts": texts,
                "fullText": full_text,
                "sourceCharCount": len(full_text),
            }
        finally:
            session.close()

    def _require_document_text(self, document: dict[str, Any]) -> None:
        if not str(document.get("fullText") or "").strip():
            raise SafeJobError("EMPTY_DOCUMENT", "The document has no readable text.")
        if int(document.get("sourceCharCount") or 0) > self._max_analysis_chars:
            raise SafeJobError(
                "DOCUMENT_TOO_LARGE",
                f"The document exceeds the {self._max_analysis_chars:,}-character analysis limit.",
            )

    def _science125_authoritative_input(self, job: StoredJob) -> tuple[str, str, dict[str, Any]] | None:
        science125_id = str(job.payload.get("science125Id") or "").strip()
        if not science125_id:
            return None
        try:
            index = load_science125_context_index()
            item = index.items[science125_id]
        except (Science125ContextError, KeyError) as exc:
            raise SafeJobError(
                "SCIENCE125_CONTEXT_UNAVAILABLE",
                "The authoritative Science 125 source context is unavailable.",
            ) from exc
        expected_hash = str(job.payload.get("_science125ContextSha256") or "").strip()
        if expected_hash and expected_hash != item.context_sha256:
            raise SafeJobError(
                "SCIENCE125_CONTEXT_CHANGED",
                "The authoritative Science 125 source context changed before generation.",
            )
        expected_extraction = str(job.payload.get("_science125ExtractionVersion") or "").strip()
        if expected_extraction and expected_extraction != index.extraction_version:
            raise SafeJobError(
                "SCIENCE125_CONTEXT_CHANGED",
                "The Science 125 extraction version changed before generation.",
            )
        context = (
            f"【Science 125 题册原文上下文 | item={item.id} | sha256={item.context_sha256}】\n"
            f"{item.source_context}"
        )
        snapshot = {
            "id": item.id,
            "contextSha256": item.context_sha256,
            "extractionVersion": index.extraction_version,
            "pdfPage": item.pdf_page,
            "bookletPage": item.booklet_page,
        }
        return item.headline, context, snapshot

    def _source_page_input(
        self,
        job: StoredJob,
        *,
        include_excerpt_text: bool = False,
    ) -> tuple[str, list[dict[str, Any]]]:
        raw_selections = job.payload.get("sourcePageSelections") or []
        if not raw_selections:
            return "", []
        if len(raw_selections) > 10:
            raise SafeJobError("VALIDATION_ERROR", "Select at most 10 PDF page excerpts.")
        allowed_root = (self.uploads_dir / str(job.user_id)).resolve()
        snapshots: list[dict[str, Any]] = []
        lines = ["【人工审核的自备 PDF 页摘录】"]
        seen_documents: set[int] = set()
        for index, selection in enumerate(raw_selections, start=1):
            try:
                document_id = int(selection["documentId"])
                pages = [int(page) for page in selection["pages"]]
                max_chars = int(selection["maxChars"])
                expected_pdf_hash = str(selection["pdfSha256"])
                expected_text_hash = str(selection["textSha256"])
            except (KeyError, TypeError, ValueError) as exc:
                raise SafeJobError("VALIDATION_ERROR", "A PDF page selection is invalid.") from exc
            if document_id in seen_documents:
                raise SafeJobError("VALIDATION_ERROR", "Each source document may have only one page selection.")
            seen_documents.add(document_id)
            document = self._load_owned_document(job.user_id, document_id)
            if document.get("sourceType") != "pdf":
                raise SafeJobError("EXCERPT_SOURCE_UNAVAILABLE", "A selected document has no available PDF source.")
            try:
                excerpt = extract_document_page_excerpt(
                    str(document.get("sourcePath") or ""),
                    pages=pages,
                    max_chars=max_chars,
                    allowed_root=allowed_root,
                )
            except DocumentPageExcerptError as exc:
                raise SafeJobError(exc.code, exc.message) from exc
            if excerpt.pdf_sha256 != expected_pdf_hash or excerpt.text_sha256 != expected_text_hash:
                raise SafeJobError(
                    "SOURCE_PAGE_SNAPSHOT_CHANGED",
                    "A selected PDF page changed; review the pages again before generating.",
                )
            page_label = ", ".join(str(page) for page in excerpt.pages)
            lines.extend([
                (
                    f"[P{index}] {document['title']} | documentId={document_id} | PDF pages={page_label} | "
                    f"pdfSha256={excerpt.pdf_sha256} | textSha256={excerpt.text_sha256}"
                ),
                excerpt.text,
            ])
            snapshot = {
                "documentId": document_id,
                "title": document["title"],
                "pages": excerpt.pages,
                "pdfSha256": excerpt.pdf_sha256,
                "textSha256": excerpt.text_sha256,
                "maxChars": excerpt.max_chars,
                "originalCharCount": excerpt.original_char_count,
                "returnedCharCount": len(excerpt.text),
                "truncated": excerpt.truncated,
            }
            if include_excerpt_text:
                snapshot["excerptText"] = excerpt.text
            snapshots.append(snapshot)
        return "\n".join(lines), snapshots

    def _prepare_science125_generation_input(self, job: StoredJob) -> dict[str, Any]:
        authoritative_input = self._science125_authoritative_input(job)
        if authoritative_input is None:
            raise SafeJobError("SCIENCE125_ID_REQUIRED", "A Science 125 item ID is required.")
        research_question, authoritative_context, source_context_snapshot = authoritative_input
        reviewed_literature = str(
            job.payload.get("supplementalContext") or job.payload.get("literatureText") or ""
        ).strip()
        page_context, source_page_snapshots = self._source_page_input(job)
        sections = [authoritative_context]
        if reviewed_literature:
            sections.append(
                "【人工审核的检索材料与补充证据；不属于权威题册原文】\n"
                + reviewed_literature
            )
        if page_context:
            sections.append(page_context)
        return {
            "researchQuestion": research_question,
            "literatureText": "\n\n".join(sections),
            "science125SourceContext": source_context_snapshot,
            "sourcePageSelections": source_page_snapshots,
        }

    def _summarize_texts(
        self,
        job: StoredJob,
        texts: list[str],
        config,
        *,
        progress_start: int = 5,
        progress_end: int = 85,
    ) -> tuple[str, int, int]:
        source_text = "\n\n".join(texts)
        segments = segment_texts(texts, max_chars=12_000)
        if not segments:
            raise SafeJobError("EMPTY_DOCUMENT", "The document has no readable text.")
        summaries: list[str] = []
        total_initial = len(segments)
        for index, segment in enumerate(segments, 1):
            progress = progress_start + int((index - 1) / max(total_initial, 1) * (progress_end - progress_start) * 0.75)
            self._update_progress(
                job,
                progress=min(progress, progress_end),
                message=f"Summarizing document segment {index}/{total_initial}.",
            )
            self._raise_if_cancelled(job)
            summary = llm_client().summarize_chemistry_document(segment, config=config)
            self._raise_for_llm_error(summary)
            if not str(summary or "").strip():
                raise SafeJobError("EMPTY_MODEL_OUTPUT", "The model returned an empty summary.")
            summaries.append(str(summary).strip())

        reduction_round = 0
        while len(summaries) > 1:
            reduction_round += 1
            if reduction_round > 8:
                raise SafeJobError("SUMMARY_REDUCTION_FAILED", "The document summaries could not be combined.")
            labeled = [f"[Section {index}]\n{value}" for index, value in enumerate(summaries, 1)]
            batches = segment_texts(labeled, max_chars=12_000)
            reduced: list[str] = []
            for index, batch in enumerate(batches, 1):
                self._raise_if_cancelled(job)
                value = llm_client().summarize_chemistry_document(batch, config=config)
                self._raise_for_llm_error(value)
                if not str(value or "").strip():
                    raise SafeJobError("EMPTY_MODEL_OUTPUT", "The model returned an empty summary.")
                reduced.append(str(value).strip())
            summaries = reduced

        self._update_progress(job, progress=progress_end, message="Document summary completed.")
        return summaries[0], len(segments), len(source_text)

    @staticmethod
    def _asset_ids(value: Any) -> set[str]:
        found: set[str] = set()
        if isinstance(value, dict):
            asset_id = value.get("assetId")
            if isinstance(asset_id, str) and asset_id:
                found.add(asset_id)
            for nested in value.values():
                found.update(JobService._asset_ids(nested))
        elif isinstance(value, list):
            for nested in value:
                found.update(JobService._asset_ids(nested))
        return found

    def _remove_managed_assets(self, user_id: int, asset_ids: set[str]) -> None:
        paths = self.analysis_store.remove_assets(user_id, sorted(asset_ids))
        root = self.assets_dir.resolve()
        for path in paths:
            resolved = Path(path).resolve()
            if resolved.is_relative_to(root):
                try:
                    resolved.unlink(missing_ok=True)
                except OSError:
                    logger.warning("Could not delete superseded analysis asset %s", resolved, exc_info=True)

    def _persist_analysis(
        self,
        job: StoredJob,
        analysis_type: str,
        document_ids: list[int],
        result: dict[str, Any],
        previous_result: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        old_asset_ids = self._asset_ids(previous_result or {})
        new_asset_ids = self._asset_ids(result)
        try:
            stored = self.analysis_store.upsert(
                job.user_id,
                analysis_type,
                document_ids,
                result,
                job_id=job.id,
            )
        except Exception:
            self._remove_managed_assets(job.user_id, new_asset_ids - old_asset_ids)
            raise
        self._remove_managed_assets(job.user_id, old_asset_ids - new_asset_ids)
        return stored.to_dict()

    def _update_document_summary(self, user_id: int, document_id: int, summary: str) -> None:
        database = db()
        session = database.get_session()
        try:
            document = session.query(database.Document).filter(
                database.Document.id == document_id,
                database.Document.user_id == user_id,
            ).first()
            if not document:
                raise SafeJobError("DOCUMENT_NOT_FOUND", "The selected document was not found.")
            document.summary = summary
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def _run_document_analysis(self, job: StoredJob) -> dict[str, Any]:
        analysis_type = str(job.payload.get("analysisType") or "")
        document_id = int(job.payload.get("documentId") or 0)
        document = self._load_owned_document(job.user_id, document_id)
        previous = self.analysis_store.get_for_scope(job.user_id, analysis_type, [document_id])
        previous_result = previous.result if previous else None
        try:
            if analysis_type == "summary":
                self._require_document_text(document)
                config = self._required_llm_config(job.user_id)
                summary, segment_count, source_char_count = self._summarize_texts(job, document["texts"], config)
                result = {
                    "summary": summary,
                    "segmentCount": segment_count,
                    "sourceCharCount": source_char_count,
                }
                self._raise_if_cancelled(job)
                self._update_document_summary(job.user_id, document_id, summary)
            elif analysis_type == "images":
                result = self._extract_document_images(job, document)
            elif analysis_type == "structures":
                result = self._analyze_document_structures(job, document, previous_result)
            elif analysis_type == "safety":
                result = self._analyze_document_safety(job, document)
            elif analysis_type == "sop":
                result = self._extract_document_sop(job, document)
            elif analysis_type == "reactions":
                result = self._extract_document_reactions(job, document)
            elif analysis_type == "translation":
                result = self._translate_document(job, document)
            else:
                raise SafeJobError("UNSUPPORTED_ANALYSIS", "Unsupported document analysis type.")
        except ModelOutputError as exc:
            raise SafeJobError("INVALID_MODEL_OUTPUT", str(exc)) from exc

        self._raise_if_cancelled(job)
        return self._persist_analysis(
            job,
            analysis_type,
            [document_id],
            result,
            previous_result=previous_result,
        )

    def _extract_document_images(self, job: StoredJob, document: dict[str, Any]) -> dict[str, Any]:
        source_path = Path(str(document.get("sourcePath") or "")).resolve()
        if document.get("sourceType") != "pdf" or not source_path.is_file():
            raise SafeJobError("PDF_NOT_AVAILABLE", "The source PDF is not available for image extraction.")
        use_hints = bool(job.payload.get("useLlmPageHints"))
        config = self._required_llm_config(job.user_id) if use_hints else None
        self._update_progress(job, progress=10, message="Extracting images from the PDF.")
        temp_root = (self.store.path.parent / "tmp").resolve()
        temp_root.mkdir(parents=True, exist_ok=True)
        created_asset_ids: set[str] = set()
        images: list[dict[str, Any]] = []
        try:
            with tempfile.TemporaryDirectory(prefix=f"analysis-{job.id}-", dir=temp_root) as temporary:
                temporary_root = Path(temporary).resolve()
                paths = document_image_extractor().extract_document_pdf_images(
                    str(source_path),
                    temporary,
                    job.user_id,
                    document["id"],
                    full_text=document.get("fullText") or "",
                    config=config,
                    use_llm_page_hints=use_hints,
                    clear_existing=True,
                )
                target_dir = self.assets_dir / str(job.user_id) / str(document["id"]) / "images" / job.id
                target_dir.mkdir(parents=True, exist_ok=True)
                allowed_suffixes = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}
                for index, raw_path in enumerate(paths, 1):
                    self._raise_if_cancelled(job)
                    source = Path(raw_path).resolve()
                    suffix = source.suffix.lower()
                    if not source.is_relative_to(temporary_root) or not source.is_file() or suffix not in allowed_suffixes:
                        continue
                    target = target_dir / f"figure-{index}-{uuid.uuid4().hex[:8]}{suffix}"
                    shutil.copyfile(source, target)
                    mime_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
                    asset = self.analysis_store.register_asset(
                        job.user_id,
                        document["id"],
                        "images",
                        target,
                        source.name,
                        mime_type,
                    )
                    created_asset_ids.add(asset.id)
                    images.append({
                        "assetId": asset.id,
                        "fileName": asset.file_name,
                        "mimeType": asset.mime_type,
                    })
        except Exception:
            self._remove_managed_assets(job.user_id, created_asset_ids)
            raise
        self._update_progress(job, progress=90, message=f"Extracted {len(images)} PDF images.")
        return {"images": images, "count": len(images), "usedLlmPageHints": use_hints}

    def _extract_document_chemicals(self, job: StoredJob, document: dict[str, Any]) -> list[str]:
        self._require_document_text(document)
        config = self._required_llm_config(job.user_id)
        segments = segment_texts(document["texts"], max_chars=6_000)
        chemicals: list[str] = []
        seen: set[str] = set()
        for index, segment in enumerate(segments, 1):
            self._update_progress(
                job,
                progress=5 + int(index / max(len(segments), 1) * 40),
                message=f"Identifying chemicals in segment {index}/{len(segments)}.",
            )
            self._raise_if_cancelled(job)
            values = llm_client().extract_chemical_names_llm(segment, config=config)
            rendered = "\n".join(str(value) for value in values)
            self._raise_for_llm_error(rendered)
            for value in values:
                clean = str(value or "").strip().lstrip("-• ")
                key = clean.casefold()
                if clean and len(clean) <= 200 and key not in seen:
                    seen.add(key)
                    chemicals.append(clean)
                    if len(chemicals) >= 20:
                        return chemicals
        return chemicals

    def _register_structure_image(
        self,
        job: StoredJob,
        document_id: int,
        image_bytes: bytes | None,
    ) -> str | None:
        if not image_bytes or not image_bytes.startswith(b"\x89PNG\r\n\x1a\n") or len(image_bytes) > 5 * 1024 * 1024:
            return None
        target_dir = self.assets_dir / str(job.user_id) / str(document_id) / "structures" / job.id
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"structure-{uuid.uuid4().hex}.png"
        target.write_bytes(image_bytes)
        asset = self.analysis_store.register_asset(
            job.user_id,
            document_id,
            "structures",
            target,
            "structure.png",
            "image/png",
        )
        return asset.id

    @staticmethod
    def _chemical_info(name: str, info, asset_id: str | None) -> dict[str, Any]:
        result = {
            "name": name,
            "cid": getattr(info, "cid", None) if info else None,
            "iupacName": str(getattr(info, "iupac_name", "") or "") if info else "",
            "molecularFormula": str(getattr(info, "molecular_formula", "") or "") if info else "",
            "molecularWeight": str(getattr(info, "molecular_weight", "") or "") if info else "",
            "cas": str(getattr(info, "cas", "") or "") if info else "",
            "canonicalSmiles": str(getattr(info, "canonical_smiles", "") or "") if info else "",
            "inchi": str(getattr(info, "inchi", "") or "") if info else "",
            "inchiKey": str(getattr(info, "inchikey", "") or "") if info else "",
            "meltingPoint": str(getattr(info, "melting_point", "") or "") if info else "",
            "boilingPoint": str(getattr(info, "boiling_point", "") or "") if info else "",
            "density": str(getattr(info, "density", "") or "") if info else "",
            "solubility": str(getattr(info, "solubility", "") or "") if info else "",
            "appearance": str(getattr(info, "appearance", "") or "") if info else "",
            "pubchemUrl": str(getattr(info, "pubchem_url", "") or "") if info else "",
        }
        if asset_id:
            result["assetId"] = asset_id
        return result

    def _analyze_document_structures(
        self,
        job: StoredJob,
        document: dict[str, Any],
        previous_result: dict[str, Any] | None,
    ) -> dict[str, Any]:
        requested_name = str(job.payload.get("chemicalName") or "").strip()
        if requested_name:
            chemicals = [str(value) for value in (previous_result or {}).get("chemicals") or []]
            if requested_name.casefold() not in {value.casefold() for value in chemicals}:
                chemicals.append(requested_name)
        else:
            chemicals = self._extract_document_chemicals(job, document)
        compounds = [dict(value) for value in (previous_result or {}).get("compounds") or []]
        warnings = [str(value) for value in (previous_result or {}).get("warnings") or []]
        query_name = requested_name or (chemicals[0] if chemicals else "")
        if not query_name:
            return {"chemicals": [], "compounds": [], "warnings": ["No chemical names were identified."]}

        self._update_progress(job, progress=60, message=f"Querying PubChem for {query_name}.")
        info = chem_structure().query_chem_info(query_name)
        image_bytes = chem_structure().fetch_structure_image(query_name)
        if info is None and not image_bytes:
            if requested_name:
                raise SafeJobError("CHEMICAL_NOT_FOUND", "PubChem did not return the requested chemical.")
            warning = f"PubChem did not return structure data for {query_name}."
            if warning not in warnings:
                warnings.append(warning)
            return {"chemicals": chemicals, "compounds": compounds, "warnings": warnings}
        asset_id = self._register_structure_image(job, document["id"], image_bytes)
        compound = self._chemical_info(query_name, info, asset_id)
        key = query_name.casefold()
        compounds = [item for item in compounds if str(item.get("name") or "").casefold() != key]
        compounds.append(compound)
        return {"chemicals": chemicals, "compounds": compounds, "warnings": warnings}

    def _analyze_document_safety(self, job: StoredJob, document: dict[str, Any]) -> dict[str, Any]:
        chemicals = self._extract_document_chemicals(job, document)[:15]
        results: list[dict[str, Any]] = []
        warnings: list[str] = []
        for index, name in enumerate(chemicals, 1):
            self._raise_if_cancelled(job)
            self._update_progress(
                job,
                progress=45 + int(index / max(len(chemicals), 1) * 45),
                message=f"Checking safety data for {name}.",
            )
            local_entry = hazard_db().lookup(name)
            item = normalize_local_hazard(name, local_entry) if local_entry else lookup_pubchem_hazard(name)
            results.append(item)
            if item.get("warning"):
                warnings.append(f"{name}: {item['warning']}")
        return {
            "chemicals": results,
            "highRiskNames": [item["name"] for item in results if item.get("highRisk")],
            "warnings": warnings,
        }

    def _extract_document_sop(self, job: StoredJob, document: dict[str, Any]) -> dict[str, Any]:
        self._require_document_text(document)
        config = self._required_llm_config(job.user_id)
        segments = segment_texts(document["texts"], max_chars=10_000)
        results: list[dict[str, Any]] = []
        for index, segment in enumerate(segments, 1):
            self._update_progress(job, progress=5 + int(index / len(segments) * 80), message=f"Extracting SOP segment {index}/{len(segments)}.")
            self._raise_if_cancelled(job)
            raw = llm_client().extract_sop(segment, config=config)
            self._raise_for_llm_error(raw)
            results.append(parse_sop_result(raw))
        merged = merge_sop_results(results)
        return {**merged, "segmentCount": len(segments), "sourceCharCount": document["sourceCharCount"]}

    def _extract_document_reactions(self, job: StoredJob, document: dict[str, Any]) -> dict[str, Any]:
        self._require_document_text(document)
        config = self._required_llm_config(job.user_id)
        segments = segment_texts(document["texts"], max_chars=12_000)
        results: list[dict[str, Any]] = []
        for index, segment in enumerate(segments, 1):
            self._update_progress(job, progress=5 + int(index / len(segments) * 80), message=f"Extracting reactions from segment {index}/{len(segments)}.")
            self._raise_if_cancelled(job)
            raw = llm_client().extract_reactions(segment, config=config)
            self._raise_for_llm_error(raw)
            results.append(parse_reaction_result(raw))
        merged = merge_reaction_results(results)
        return {**merged, "segmentCount": len(segments), "sourceCharCount": document["sourceCharCount"]}

    def _translate_document(self, job: StoredJob, document: dict[str, Any]) -> dict[str, Any]:
        self._require_document_text(document)
        config = self._required_llm_config(job.user_id)
        session = db().get_session()
        try:
            glossary = [
                {"en": item.en_term, "zh": item.zh_term, "note": item.note or ""}
                for item in db().get_user_glossary(session, job.user_id)
            ]
        finally:
            session.close()
        source_segments = segment_texts(document["texts"], max_chars=8_000)
        translated_segments: list[dict[str, Any]] = []
        glossary_items: list[dict[str, Any]] = []
        for index, segment in enumerate(source_segments, 1):
            self._update_progress(job, progress=5 + int(index / len(source_segments) * 85), message=f"Translating segment {index}/{len(source_segments)}.")
            self._raise_if_cancelled(job)
            raw = llm_client().translate_with_glossary(segment, glossary=glossary, config=config)
            self._raise_for_llm_error(raw)
            parsed = parse_translation_result(raw)
            translated_segments.append({
                "index": index,
                "sourceText": segment,
                "translation": parsed["translation"],
            })
            glossary_items.extend(parsed.get("glossary") or [])
        return {
            "segments": translated_segments,
            "glossary": merge_glossary(glossary_items),
            "segmentCount": len(source_segments),
            "sourceCharCount": document["sourceCharCount"],
        }

    def _run_document_compare(self, job: StoredJob) -> dict[str, Any]:
        document_ids = sorted({int(value) for value in job.payload.get("documentIds") or []})
        if not 2 <= len(document_ids) <= 5:
            raise SafeJobError("VALIDATION_ERROR", "Select between two and five unique documents.")
        config = self._required_llm_config(job.user_id)
        documents = [self._load_owned_document(job.user_id, document_id) for document_id in document_ids]
        comparison_input: list[dict[str, str]] = []
        source_counts: dict[str, int] = {}
        for index, document in enumerate(documents, 1):
            self._require_document_text(document)
            self._update_progress(job, progress=5 + int((index - 1) / len(documents) * 65), message=f"Summarizing document {index}/{len(documents)} for comparison.")
            digest, _, source_count = self._summarize_texts(
                job,
                document["texts"],
                config,
                progress_start=5 + int((index - 1) / len(documents) * 65),
                progress_end=5 + int(index / len(documents) * 65),
            )
            comparison_input.append({"title": document["title"], "text": digest})
            source_counts[str(document["id"])] = source_count
        self._raise_if_cancelled(job)
        self._update_progress(job, progress=75, message="Comparing full-document summaries.")
        markdown = llm_client().multi_doc_compare(comparison_input, config=config)
        self._raise_for_llm_error(markdown)
        if not str(markdown or "").strip():
            raise SafeJobError("EMPTY_MODEL_OUTPUT", "The model returned an empty comparison.")
        result = {
            "markdown": str(markdown).strip(),
            "documents": [{"id": item["id"], "title": item["title"]} for item in documents],
            "sourceCharCounts": source_counts,
        }
        previous = self.analysis_store.get_comparison(job.user_id, document_ids)
        return self._persist_analysis(
            job,
            "comparison",
            document_ids,
            result,
            previous_result=previous.result if previous else None,
        )

    def _run_literature_search(self, job: StoredJob) -> dict[str, Any]:
        query_text = str(job.payload.get("queryText") or "").strip()
        if not query_text:
            raise SafeJobError("QUERY_REQUIRED", "A search query is required.")
        if job.payload.get("science125Id"):
            return self._run_science125_literature_search(job, query_text)
        requested = set(job.payload.get("platforms") or [])
        platform_keys = {
            "Crossref": "crossref",
            "arXiv": "arxiv",
            "Semantic Scholar": "semantic_scholar",
            "DOAJ": "doaj",
            "PMC": "pmc",
        }
        enabled = {key: label in requested for label, key in platform_keys.items()}
        query = literature_search().SearchQuery(
            keywords=[query_text],
            domain=str(job.payload.get("domain") or ""),
            max_results=int(job.payload.get("maxResults") or 20),
            year_from=str(job.payload.get("yearFrom") or ""),
            year_to=str(job.payload.get("yearTo") or ""),
        )
        self._update_progress(job, progress=10, message="Searching literature providers.")
        engine = literature_search().LiteratureSearchEngine(platforms=enabled)
        engine.semantic_scholar_api_key = api_keys.api_key_store.get_key(job.user_id, "semantic_scholar") or ""
        engine.ncbi_api_key = api_keys.api_key_store.get_key(job.user_id, "ncbi") or ""
        engine.crossref_mailto = api_keys.api_key_store.get_key(job.user_id, "crossref_mailto") or "chalk@example.com"
        engine.session.headers["User-Agent"] = (
            f"ChalkWeb/1.0 (Academic Research; mailto:{engine.crossref_mailto})"
        )
        diagnostics = engine.search_with_diagnostics(query)
        self._raise_if_cancelled(job)
        results = []
        for item in diagnostics.results:
            raw = item.to_reference_dict()
            identity = "|".join(
                [str(raw.get("source_platform") or ""), str(raw.get("doi") or ""), str(raw.get("url") or ""), str(raw.get("title") or "")]
            )
            results.append(
                {
                    "id": hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16],
                    "title": str(raw.get("title") or ""),
                    "authors": str(raw.get("authors") or ""),
                    "journal": str(raw.get("journal") or ""),
                    "year": str(raw.get("year") or ""),
                    "doi": str(raw.get("doi") or ""),
                    "abstract": str(raw.get("abstract") or ""),
                    "sourcePlatform": str(raw.get("source_platform") or ""),
                    "url": str(raw.get("url") or ""),
                    "isOpenAccess": bool(raw.get("is_open_access")),
                    "relevanceScore": float(raw.get("search_relevance_score") or 0.0),
                    "accessStatus": str(raw.get("access_status") or "needs_verification"),
                    "needsFulltext": bool(raw.get("needs_fulltext")),
                    "warning": str(raw.get("warning") or ""),
                }
            )
        return {
            "results": results,
            "platformStatus": diagnostics.platform_status,
            "warnings": list(diagnostics.warnings),
            "query": {
                "queryText": query_text,
                "domain": query.domain,
                "platforms": list(requested),
                "maxResults": query.max_results,
                "yearFrom": query.year_from,
                "yearTo": query.year_to,
            },
        }

    @staticmethod
    def _science125_evidence_is_full_text(record: Mapping[str, Any]) -> bool:
        return str(record.get("accessStatus") or record.get("access_status") or "").strip().lower() not in {
            "", "metadata", "metadata_only", "needs_verification",
        }

    @staticmethod
    def _science125_provider_family(provider_id: str) -> str:
        if provider_id == "user_pdf":
            return "user_pdf"
        try:
            return get_provider(provider_id).family
        except KeyError:
            return provider_id

    def _run_science125_literature_search(self, job: StoredJob, query_text: str) -> dict[str, Any]:
        science125_id = str(job.payload.get("science125Id") or "").strip()
        credential_environment = api_keys.api_key_store.science125_environment(job.user_id)
        try:
            route = get_science125_route(science125_id)
            retrieval_profile = get_science125_retrieval_profile(route.retrieval_profile)
            readiness = profile_readiness(
                route.retrieval_profile,
                environ=credential_environment,
            )
        except (KeyError, Science125RoutingError) as exc:
            raise SafeJobError(
                "SCIENCE125_ROUTING_UNAVAILABLE",
                "The Science 125 domain routing profile is unavailable.",
            ) from exc
        if not readiness.ready:
            codes = ", ".join(readiness.missing_configuration_codes) or "provider configuration"
            raise SafeJobError(
                "SCIENCE125_PROVIDER_NOT_READY",
                f"The required Science 125 literature providers are not ready: {codes}.",
            )

        self._update_progress(job, progress=10, message="Searching Science 125 official literature APIs.")
        adapters = default_provider_adapters(environ=credential_environment)
        search_window = f"daily:{datetime.now(UTC).date().isoformat()}"
        searches: list[tuple[str, Any]] = []
        query_plan = build_science125_query_plan(
            retrieval_profile.query_adapter,
            query_text,
            primary_subdomain=route.primary_subdomain,
            question_id=science125_id,
        )

        def run_search(search_query: str, *, query_index: int) -> None:
            parameters: dict[str, Any] = {
                "science125Id": science125_id,
                "queryIndex": query_index,
                "queryPlanVersion": "science125-query-plan-v1",
            }
            searches.append((
                search_query,
                search_science125(
                    route.retrieval_profile,
                    search_query,
                    adapters=adapters,
                    store=self.science125_rate_store,
                    environ=credential_environment,
                    parameters=parameters,
                    window=search_window,
                    user_scope=str(job.user_id),
                ),
            ))

        def record_identity(record: EvidenceRecord) -> str:
            return str(
                record.doi
                or record.pmid
                or record.arxiv_id
                or record.ads_id
                or record.stable_id
                or record.title
            ).strip().casefold()

        def qualification(record: EvidenceRecord):
            scoring_query = " ".join((query_plan.topic_summary, *query_plan.keywords))
            return qualify_science125_evidence(science125_id, scoring_query, record)

        def merged_records() -> list[EvidenceRecord]:
            merged: dict[str, EvidenceRecord] = {}
            for _search_query, search_result in searches:
                for record in search_result.evidence:
                    identity = record_identity(record)
                    previous = merged.get(identity)
                    if previous is None:
                        merged[identity] = record
                        continue
                    previous_quality = qualification(previous)
                    current_quality = qualification(record)
                    if (
                        current_quality.eligible_for_generation,
                        current_quality.relevance.score,
                    ) > (
                        previous_quality.eligible_for_generation,
                        previous_quality.relevance.score,
                    ):
                        merged[identity] = record
            return list(merged.values())

        def readiness_for(records: list[EvidenceRecord]) -> dict[str, Any]:
            eligible = [record for record in records if qualification(record).eligible_for_generation]
            families = sorted({
                self._science125_provider_family(record.provider)
                for record in eligible
            })
            return {
                "eligibleFullTextCount": len(eligible),
                "minimumAcceptedEvidence": retrieval_profile.min_accepted_evidence,
                "providerFamilyCount": len(families),
                "minimumProviderFamilies": retrieval_profile.min_provider_families,
                "providerFamilies": families,
                "minimumRelevanceLabel": "medium",
                "ready": (
                    len(eligible) >= retrieval_profile.min_accepted_evidence
                    and len(families) >= retrieval_profile.min_provider_families
                ),
            }

        for index, planned_query in enumerate(query_plan.queries, 1):
            self._update_progress(
                job,
                progress=10 + int(index / max(1, len(query_plan.queries)) * 50),
                message=f"Searching extracted topic keywords ({index}/{len(query_plan.queries)}).",
            )
            run_search(planned_query, query_index=index)
            self._raise_if_cancelled(job)
            if readiness_for(merged_records())["ready"]:
                break

        executed_queries = [search_query for search_query, _result in searches]
        refinement_queries = executed_queries[1:]

        records = merged_records()
        evidence_readiness = readiness_for(records)
        rows: list[dict[str, Any]] = []
        for record in records:
            access_status = record.access_status or "metadata"
            evidence_qualification = qualification(record)
            relevance = evidence_qualification.relevance
            rows.append({
                "id": record.stable_id,
                "title": record.title,
                "authors": ", ".join(record.authors),
                "journal": "",
                "year": "",
                "doi": record.doi or "",
                "abstract": record.abstract,
                "sourcePlatform": record.provider,
                "providerFamily": self._science125_provider_family(record.provider),
                "url": record.full_text_url or (f"https://doi.org/{record.doi}" if record.doi else ""),
                "isOpenAccess": access_status in {"open_full_text", "open_access", "study_registry"},
                "relevanceScore": relevance.score,
                "relevanceLabel": relevance.label,
                "relevanceBreakdown": relevance.to_dict(),
                "evidenceEligibility": evidence_qualification.to_dict(),
                "accessStatus": access_status,
                "needsFulltext": not self._science125_evidence_is_full_text(record.to_dict()),
                "warning": record.warning or "",
            })
        rows.sort(
            key=lambda row: (
                not bool((row.get("evidenceEligibility") or {}).get("eligibleForGeneration")),
                -float(row["relevanceScore"]),
                str(row["sourcePlatform"]),
                str(row["title"]).casefold(),
            )
        )
        diagnostics: dict[str, Any] = {}
        diagnostic_rows: list[dict[str, Any]] = []
        for search_index, (search_query, search_result) in enumerate(searches):
            for item in search_result.diagnostics:
                key = item.provider if search_index == 0 else f"{item.provider}#refinement{search_index}"
                payload = {**item.to_dict(), "query": search_query}
                diagnostics[key] = payload
                diagnostic_rows.append(payload)
        evidence_status = "ready_for_review" if evidence_readiness["ready"] else "evidence_insufficient"
        warnings = [
            str(item["message"])
            for item in diagnostic_rows
            if item.get("message") and item.get("status") != "succeeded"
        ]
        return {
            "results": rows,
            "relevanceScoringVersion": SCORING_VERSION,
            "platformStatus": diagnostics,
            "providerDiagnostics": diagnostic_rows,
            "warnings": warnings,
            "evidenceStatus": evidence_status,
            "evidenceReadiness": evidence_readiness,
            "refinementQueries": refinement_queries,
            "query": {
                "queryText": query_text,
                "originalQueryText": query_text,
                "topicSummary": query_plan.topic_summary,
                "keywords": list(query_plan.keywords),
                "queries": executed_queries,
                "queryRuns": [
                    {
                        "query": search_query,
                        "queryHash": search_result.query_hash,
                        "cacheKey": search_result.cache_key,
                        "resultCount": len(search_result.evidence),
                    }
                    for search_query, search_result in searches
                ],
                "science125Id": science125_id,
                "retrievalProfile": route.retrieval_profile,
                "queryHash": searches[0][1].query_hash,
                "cacheKey": searches[0][1].cache_key,
                "maxProviders": retrieval_profile.max_providers,
            },
            "policyHashes": {
                provider_id: get_provider(provider_id).policy.policy_hash
                for provider_id in retrieval_profile.provider_ids
            },
        }

    def _run_lab_suggest(self, job: StoredJob) -> dict[str, Any]:
        record_id = int(job.payload.get("recordId") or 0)
        config = self._required_llm_config(job.user_id)
        database = db()
        session = database.get_session()
        try:
            record = session.query(database.LabRecord).filter(
                database.LabRecord.id == record_id,
                database.LabRecord.user_id == job.user_id,
            ).first()
            if not record:
                raise SafeJobError("LAB_RECORD_NOT_FOUND", "The lab record was not found.")
            content = str(record.content or "").strip()
            if not content:
                raise SafeJobError("LAB_RECORD_EMPTY", "Add experiment notes before requesting an AI suggestion.")
            try:
                related_ids = json.loads(record.related_doc_ids or "[]")
            except (TypeError, ValueError):
                related_ids = []
            related_ids = [int(value) for value in related_ids if str(value).isdigit()][:3]
            summaries: list[str] = []
            if related_ids:
                documents = session.query(database.Document).filter(
                    database.Document.user_id == job.user_id,
                    database.Document.id.in_(related_ids),
                ).all()
                summaries = [f"{document.title}: {document.summary}" for document in documents if document.summary]
            self._update_progress(job, progress=35, message="Analyzing the experiment record.")
            suggestion = llm_client().lab_record_suggest(content, summaries, config=config)
            self._raise_for_llm_error(str(suggestion or ""))
            suggestion = str(suggestion or "").strip()
            if not suggestion:
                raise SafeJobError("EMPTY_MODEL_OUTPUT", "The model returned an empty suggestion.")
            self._raise_if_cancelled(job)
            record.ai_suggestion = suggestion
            session.commit()
            return {"recordId": record.id, "suggestion": suggestion}
        finally:
            session.close()

    def _run_prompt_polish(self, job: StoredJob) -> dict[str, Any]:
        text = str(job.payload.get("text") or "").strip()
        config = self._required_llm_config(job.user_id)
        prompt = (
            "你是科研假设生成提示词编辑器。请把用户的假设生成引导词润色成更清楚、"
            "更容易被模型执行的中文提示词。只改写表达，不新增没有依据的数值、论文、材料结论；"
            "保留材料、反应、吸附物、中间体、实验条件和验证约束；只输出可直接使用的提示词，不要解释。\n\n"
            f"用户原始引导词：\n{text}"
        )
        polished = llm_client()._chat(prompt, config, task="qa", timeout=120, retries=1)
        self._raise_for_llm_error(str(polished or ""))
        polished = str(polished or "").strip()
        if not polished:
            raise SafeJobError("EMPTY_MODEL_OUTPUT", "The model returned an empty prompt.")
        return {"text": polished}

    @staticmethod
    def _camel_to_snake_data(value: Any) -> Any:
        if isinstance(value, dict):
            converted: dict[str, Any] = {}
            for key, nested in value.items():
                key_text = str(key)
                snake = ""
                for character in key_text:
                    if character.isupper():
                        snake += "_" + character.lower()
                    else:
                        snake += character
                converted[snake] = JobService._camel_to_snake_data(nested)
            return converted
        if isinstance(value, list):
            return [JobService._camel_to_snake_data(item) for item in value]
        return value

    @staticmethod
    def _compact_evidence_summary(evidence_context: dict[str, Any]) -> dict[str, Any]:
        compact = dict(evidence_context or {})
        compact.pop("context", None)
        documents = []
        for item in compact.get("documents") or []:
            if not isinstance(item, dict):
                continue
            documents.append({key: value for key, value in item.items() if key != "context"})
        if documents:
            compact["documents"] = documents
        return compact

    def _build_hypothesis_evidence(
        self,
        job: StoredJob,
        documents: list[HypothesisSourceDocument],
        research_question: str,
        api_key: str | None,
    ) -> tuple[str, dict[str, Any], list[str]]:
        if not documents:
            return "", {"status": "not_requested", "documents": []}, []
        module = scientific_evidence_rag()
        session = db().get_session()
        contexts: list[str] = []
        records: list[dict[str, Any]] = []
        warnings: list[str] = []
        try:
            for index, document in enumerate(documents, 1):
                self._raise_if_cancelled(job)
                self._update_progress(
                    job,
                    progress=3 + int(index / max(len(documents), 1) * 7),
                    message=f"Building evidence context {index}/{len(documents)}.",
                )
                query = research_question.strip() or f"What evidence and limitations are reported in {document.title}?"
                try:
                    evidence = module.build_scientific_evidence_context(
                        session=session,
                        user_id=job.user_id,
                        research_question=query,
                        doc_id=document.id,
                        max_chunks=12,
                        min_score=0.2,
                        api_key=api_key,
                    )
                except Exception:
                    logger.warning(
                        "Could not build hypothesis evidence for document %s",
                        document.id,
                        exc_info=True,
                    )
                    warnings.append(f"Evidence retrieval failed for document {document.id} ({document.title}).")
                    records.append({
                        "documentId": document.id,
                        "title": document.title,
                        "status": "failed",
                        "citations": [],
                    })
                    continue
                formatted = str(module.format_evidence_context(evidence) or "").strip()
                if formatted:
                    contexts.append(f"[Evidence for D{index}: {document.title}]\n{formatted}")
                raw = module.evidence_to_dict(evidence)
                records.append({
                    "documentId": document.id,
                    "title": document.title,
                    "status": str(raw.get("status") or ""),
                    "source": str(raw.get("source") or ""),
                    "answer": str(raw.get("answer") or "")[:2000],
                    "citations": raw.get("citations") or [],
                    "warnings": [str(value) for value in raw.get("warnings") or []],
                })
                warnings.extend(str(value) for value in raw.get("warnings") or [] if str(value).strip())
        finally:
            session.close()
        status = "ok" if contexts else "unavailable"
        return "\n\n".join(contexts), {"status": status, "documents": records}, warnings

    def _wait_for_hypothesis_feedback(
        self,
        job: StoredJob,
        hypothesis_json: dict[str, Any],
        critique_json: dict[str, Any] | None,
        round_number: int,
        interaction_history: dict[str, Any],
        orchestrator: Any,
        evidence_context: dict[str, Any],
    ) -> WebHypothesisFeedback:
        hitl_feedback_broker.open(job.id)
        review_context = getattr(orchestrator, "_last_hitl_context", {}) or {}
        allowed_review_keys = {
            "current_stage",
            "source_mode",
            "domain",
            "pre_search_keywords",
            "pre_search_refs",
            "literature_search_diagnostics",
            "literature_facts",
            "reasoning_chain",
            "cross_domain_analogies",
            "debate_data",
            "database_evidence_context",
            "qwen_agent_tool_context",
        }
        compact_review = {
            key: value
            for key, value in review_context.items()
            if key in allowed_review_keys
        } if isinstance(review_context, dict) else {}
        prompt = sanitize_public_data(
            {
                "kind": "hypothesis_review",
                "round": round_number,
                "stage": "initial" if round_number == 0 else "iteration",
                "hypothesis": hypothesis_json,
                "critique": critique_json or {},
                "debate": getattr(orchestrator, "_last_debate_data", {}) or {},
                "evidenceSummary": self._compact_evidence_summary(evidence_context),
                "reviewContext": compact_review,
                "history": interaction_history or {"interactions": []},
            },
            private_roots=[get_settings().legacy_root, self.store.path.parent],
        )
        waiting = self.store.transition(
            job.id,
            from_statuses=("RUNNING",),
            status="WAITING_FOR_FEEDBACK",
            stage="HITL_REVIEW",
            feedback_prompt=prompt,
            message="Waiting for hypothesis review.",
        )
        if not waiting or waiting.status != "WAITING_FOR_FEEDBACK":
            hitl_feedback_broker.close(job.id)
            return WebHypothesisFeedback(action="cancel")

        submitted = hitl_feedback_broker.wait(job.id)
        action = str(submitted.get("action") or "skip")
        if action == "cancel":
            return WebHypothesisFeedback(action="cancel")
        edited = submitted.get("editedHypothesis")
        edited_hypothesis = self._camel_to_snake_data(edited) if isinstance(edited, dict) else None
        manual_diff = (
            hitl_trace().build_field_diff(hypothesis_json, edited_hypothesis)
            if edited_hypothesis is not None
            else []
        )
        structured = submitted.get("structuredFeedback") or {}
        return WebHypothesisFeedback(
            action=action,
            feedback_text=str(submitted.get("feedbackText") or ""),
            edited_hypothesis=edited_hypothesis,
            debate_stance=str(submitted.get("debateStance") or "neutral"),
            selected_attack_indices=[int(value) for value in submitted.get("selectedAttackIndices") or []],
            structured_feedback=self._camel_to_snake_data(structured),
            manual_revision_diff=manual_diff,
        )

    def _create_hypothesis_artifacts(
        self,
        job: StoredJob,
        hypothesis_id: int,
        result: Any,
        interaction_history: dict[str, Any],
    ) -> tuple[list[dict[str, Any]], list[str]]:
        artifacts: list[dict[str, Any]] = []
        warnings: list[str] = []
        try:
            report, report_warnings = self._create_report_artifact(
                job,
                hypothesis_id,
                result,
                enhance=True,
            )
            artifacts.append(report)
            warnings.extend(report_warnings)
        except Exception:
            logger.warning("Could not create hypothesis HTML report", exc_info=True)
            warnings.append("The interactive HTML report could not be generated.")
        try:
            artifacts.append(self._create_trace_artifact(
                job,
                hypothesis_id,
                result,
                interaction_history,
            ))
        except Exception:
            logger.warning("Could not create hypothesis Agent Trace", exc_info=True)
            warnings.append("The Agent Trace could not be generated.")
        try:
            artifacts.append(self._create_workflow_artifact(job, hypothesis_id, result))
        except Exception:
            logger.warning("Could not create hypothesis workflow package", exc_info=True)
            warnings.append("The workflow package could not be generated.")
        return artifacts, warnings

    def _hypothesis_directory(self, user_id: int, hypothesis_id: int) -> Path:
        directory = self.hypothesis_assets_dir / str(user_id) / str(hypothesis_id)
        directory.mkdir(parents=True, exist_ok=True)
        resolved = directory.resolve()
        if not resolved.is_relative_to(self.hypothesis_assets_dir.resolve()):
            raise SafeJobError("UNSAFE_ARTIFACT_PATH", "The managed artifact path is invalid.")
        return resolved

    @staticmethod
    def _atomic_write_text(path: Path, value: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_text(value, encoding="utf-8")
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)

    def _remove_replaced_hypothesis_artifacts(self, paths: list[Path]) -> None:
        root = self.hypothesis_assets_dir.resolve()
        for path in paths:
            resolved = Path(path).resolve()
            if not resolved.is_relative_to(root):
                logger.warning("Refusing to remove unmanaged hypothesis artifact %s", resolved)
                continue
            try:
                if resolved.is_dir():
                    shutil.rmtree(resolved)
                else:
                    resolved.unlink(missing_ok=True)
            except OSError:
                logger.warning("Could not remove replaced hypothesis artifact %s", resolved, exc_info=True)

    def _register_hypothesis_artifact(
        self,
        job: StoredJob,
        hypothesis_id: int,
        kind: str,
        path: Path,
        file_name: str,
        mime_type: str,
    ) -> dict[str, Any]:
        resolved = path.resolve()
        if not resolved.is_file() or not resolved.is_relative_to(self.hypothesis_assets_dir.resolve()):
            raise SafeJobError("UNSAFE_ARTIFACT_PATH", "The generated artifact is outside managed storage.")
        artifact, replaced = self.hypothesis_store.upsert(
            job.user_id,
            hypothesis_id,
            kind,
            resolved,
            file_name,
            mime_type,
        )
        self._remove_replaced_hypothesis_artifacts(replaced)
        return artifact.to_dict()

    def _create_report_artifact(
        self,
        job: StoredJob,
        hypothesis_id: int,
        result: Any,
        *,
        enhance: bool,
    ) -> tuple[dict[str, Any], list[str]]:
        warnings: list[str] = []
        renderer = report_renderer().HTMLReportRenderer(self._llm_config_for_user(job.user_id))
        try:
            html = renderer.render(result, enhance=enhance)
            if enhance and not getattr(renderer, "_enhanced", None):
                warnings.append("LLM report enhancement was unavailable; the standard report was used.")
        except Exception:
            if not enhance:
                raise
            logger.warning("Enhanced report rendering failed; falling back to the standard report", exc_info=True)
            renderer = report_renderer().HTMLReportRenderer(self._llm_config_for_user(job.user_id))
            html = renderer.render(result, enhance=False)
            warnings.append("LLM report enhancement failed; the standard report was used.")
        if not str(html or "").strip():
            raise SafeJobError("EMPTY_REPORT", "The report renderer returned no HTML.")
        secured = secure_report_html(
            str(html),
            private_roots=[get_settings().legacy_root, self.store.path.parent, self.hypothesis_assets_dir],
        )
        target = self._hypothesis_directory(job.user_id, hypothesis_id) / "interactive-report.html"
        self._atomic_write_text(target, secured)
        artifact = self._register_hypothesis_artifact(
            job,
            hypothesis_id,
            "interactive_report_html",
            target,
            "interactive-report.html",
            "text/html; charset=utf-8",
        )
        return artifact, warnings

    def _create_trace_artifact(
        self,
        job: StoredJob,
        hypothesis_id: int,
        result: Any,
        interaction_history: dict[str, Any],
    ) -> dict[str, Any]:
        history = interaction_history or {"interactions": []}
        trace = hitl_trace().make_agent_trace(
            session_id=str(history.get("session_id") or ""),
            mode=str(history.get("mode") or "auto"),
            research_question=str(job.payload.get("researchQuestion") or ""),
            result=result,
            interaction_history=history,
        )
        public_trace = sanitize_public_data(
            trace,
            private_roots=[get_settings().legacy_root, self.store.path.parent, self.hypothesis_assets_dir],
        )
        target = self._hypothesis_directory(job.user_id, hypothesis_id) / "agent-trace.json"
        self._atomic_write_text(
            target,
            json.dumps(public_trace, ensure_ascii=False, indent=2, default=str),
        )
        return self._register_hypothesis_artifact(
            job,
            hypothesis_id,
            "agent_trace_json",
            target,
            "agent-trace.json",
            "application/json",
        )

    @staticmethod
    def _validate_zip_member_name(name: str) -> str:
        normalized = str(name or "").replace("\\", "/")
        member = PurePosixPath(normalized)
        if (
            not normalized
            or member.is_absolute()
            or ".." in member.parts
            or any(part in {"", "."} for part in member.parts)
            or (member.parts and ":" in member.parts[0])
        ):
            raise SafeJobError("UNSAFE_WORKFLOW_PACKAGE", "The workflow package contains an unsafe path.")
        return member.as_posix()

    def _sanitize_workflow_tree(self, root: Path) -> None:
        private_roots = [get_settings().legacy_root, self.store.path.parent, self.hypothesis_assets_dir]
        text_suffixes = {".json", ".md", ".txt", ".yaml", ".yml", ".toml", ".ini", ".sh", ".ps1"}
        resolved_root = root.resolve()
        for path in sorted(root.rglob("*")):
            if path.is_symlink():
                raise SafeJobError("UNSAFE_WORKFLOW_PACKAGE", "Symbolic links are not allowed in workflow packages.")
            resolved = path.resolve()
            if not resolved.is_relative_to(resolved_root):
                raise SafeJobError("UNSAFE_WORKFLOW_PACKAGE", "The workflow package escaped managed storage.")
            if not path.is_file() or path.suffix.lower() not in text_suffixes:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if path.suffix.lower() == ".json":
                try:
                    parsed = json.loads(text)
                except ValueError:
                    parsed = None
                if parsed is not None:
                    cleaned = sanitize_public_data(parsed, private_roots=private_roots)
                    self._atomic_write_text(path, json.dumps(cleaned, ensure_ascii=False, indent=2, default=str))
                    continue
            cleaned_text = sanitize_public_data(text, private_roots=private_roots)
            self._atomic_write_text(path, str(cleaned_text))

    def _zip_workflow_tree(
        self,
        source: Path,
        target: Path,
        *,
        max_files: int = 200,
        max_uncompressed: int = 100 * 1024 * 1024,
    ) -> None:
        root = source.resolve()
        files: list[tuple[Path, str, int]] = []
        total_size = 0
        for path in sorted(source.rglob("*")):
            if path.is_symlink():
                raise SafeJobError("UNSAFE_WORKFLOW_PACKAGE", "Symbolic links are not allowed in workflow packages.")
            resolved = path.resolve()
            if not resolved.is_relative_to(root):
                raise SafeJobError("UNSAFE_WORKFLOW_PACKAGE", "The workflow package escaped managed storage.")
            if not path.is_file():
                continue
            arcname = self._validate_zip_member_name(path.relative_to(source).as_posix())
            size = path.stat().st_size
            total_size += size
            files.append((path, arcname, size))
            if len(files) > max_files or total_size > max_uncompressed:
                raise SafeJobError(
                    "WORKFLOW_PACKAGE_TOO_LARGE",
                    "The workflow package exceeds the file count or uncompressed size limit.",
                )
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
        try:
            with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
                for path, arcname, _ in files:
                    archive.write(path, arcname)
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)

    def _create_workflow_artifact(
        self,
        job: StoredJob,
        hypothesis_id: int,
        result: Any,
    ) -> dict[str, Any]:
        raw_json = getattr(result, "raw_json", {}) or {}
        if not isinstance(raw_json, dict):
            raise SafeJobError("INVALID_HYPOTHESIS_OUTPUT", "The hypothesis result is invalid.")
        module = hypothesis_workflow_exporter()
        package = raw_json.get("_hypothesis_workflow_package")
        if not isinstance(package, dict) or not package:
            package = module.build_hypothesis_workflow_package(raw_json)
        package = self._validated_workflow_package_sources(package)
        directory = self._hypothesis_directory(job.user_id, hypothesis_id)
        export_root = directory / f".workflow-{uuid.uuid4().hex}"
        target = directory / "workflow-package.zip"
        try:
            exported = Path(module.export_hypothesis_workflow_package(package, export_root)).resolve()
            if exported != export_root.resolve():
                raise SafeJobError("UNSAFE_WORKFLOW_PACKAGE", "The workflow exporter returned an unsafe directory.")
            self._sanitize_workflow_tree(exported)
            self._zip_workflow_tree(exported, target)
        finally:
            resolved_export = export_root.resolve()
            if resolved_export.is_relative_to(self.hypothesis_assets_dir.resolve()):
                shutil.rmtree(resolved_export, ignore_errors=True)
        return self._register_hypothesis_artifact(
            job,
            hypothesis_id,
            "workflow_package_zip",
            target,
            "workflow-package.zip",
            "application/zip",
        )

    def _validated_workflow_package_sources(self, package: dict[str, Any]) -> dict[str, Any]:
        cloned = json.loads(json.dumps(package, ensure_ascii=False, default=str))
        structures = cloned.get("structures")
        if not isinstance(structures, list):
            return cloned
        backend_root = Path(__file__).resolve().parents[2]
        trusted_roots = [
            (get_settings().legacy_root / "data" / "generated_structures").resolve(),
            (get_settings().legacy_root / "src" / "chalk_app" / "app" / "data" / "generated_structures").resolve(),
            (get_settings().data_dir / "generated_structures").resolve(),
            (backend_root / "data" / "generated_structures").resolve(),
            self.hypothesis_assets_dir.resolve(),
        ]
        for item in structures:
            if not isinstance(item, dict):
                continue
            raw_path = str(item.get("path") or "").strip()
            if not raw_path:
                continue
            candidate = Path(raw_path).resolve()
            if candidate.is_symlink():
                raise SafeJobError(
                    "UNSAFE_WORKFLOW_SOURCE",
                    "Symbolic-link structure sources are not allowed in workflow exports.",
                )
            if not candidate.is_file():
                item["path"] = ""
                continue
            if not any(candidate.is_relative_to(root) for root in trusted_roots):
                raise SafeJobError(
                    "UNSAFE_WORKFLOW_SOURCE",
                    "The workflow package referenced an unmanaged structure source.",
                )
            item["path"] = str(candidate)
        return cloned

    @staticmethod
    def _science125_snapshot_hash(payload: Mapping[str, Any]) -> str:
        return hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()

    def _required_science125_llm_config(self, user_id: int):
        credential_environment = api_keys.api_key_store.science125_environment(user_id)
        settings = get_settings()
        api_key = str(credential_environment.get("DASHSCOPE_API_KEY") or "").strip()
        if not api_key:
            raise SafeJobError(
                "DASHSCOPE_API_KEY_REQUIRED",
                "Science 125 generation requires a configured DashScope API Key.",
            )
        try:
            input_cost = float(str(
                credential_environment.get("QWEN_INPUT_COST_PER_MILLION_CNY")
                or settings.qwen_input_cost_per_million_cny
                or ""
            ))
            output_cost = float(str(
                credential_environment.get("QWEN_OUTPUT_COST_PER_MILLION_CNY")
                or settings.qwen_output_cost_per_million_cny
                or ""
            ))
        except ValueError as exc:
            raise SafeJobError(
                "SCIENCE125_MODEL_PRICING_REQUIRED",
                "Science 125 generation requires valid Qwen input and output token prices.",
            ) from exc
        if input_cost <= 0 or output_cost <= 0:
            raise SafeJobError(
                "SCIENCE125_MODEL_PRICING_REQUIRED",
                "Science 125 generation requires positive Qwen input and output token prices.",
            )
        return llm_client().LLMConfig(
            api_key=api_key,
            model=llm_client().REASONING_MODEL,
            input_cost_per_million_cny=input_cost,
            output_cost_per_million_cny=output_cost,
        )

    def _science125_reviewed_evidence(self, job: StoredJob) -> tuple[list[dict[str, Any]], dict[str, Any], str, str]:
        science125_id = str(job.payload.get("science125Id") or "").strip()
        search_job_id = str(job.payload.get("literatureSearchJobId") or "").strip()
        search_job = self.store.get_for_user(job.user_id, search_job_id)
        if not search_job or search_job.type != "literature_search":
            raise SafeJobError("SCIENCE125_SEARCH_NOT_FOUND", "The reviewed literature search was not found.")
        if search_job.status != "SUCCEEDED":
            raise SafeJobError("SCIENCE125_SEARCH_NOT_READY", "The reviewed literature search has not completed.")
        if str(search_job.payload.get("science125Id") or "") != science125_id:
            raise SafeJobError("SCIENCE125_SEARCH_MISMATCH", "The reviewed literature search belongs to another item.")
        search_result = search_job.result if isinstance(search_job.result, dict) else {}
        result_rows = search_result.get("results") if isinstance(search_result.get("results"), list) else []
        by_id = {
            str(row.get("id") or "").strip(): row
            for row in result_rows
            if isinstance(row, dict) and str(row.get("id") or "").strip()
        }
        reviewed_ids = [str(value).strip() for value in job.payload.get("reviewedEvidenceIds") or []]
        if not reviewed_ids or any(value not in by_id for value in reviewed_ids):
            raise SafeJobError(
                "SCIENCE125_EVIDENCE_SNAPSHOT_INVALID",
                "One or more reviewed evidence records are unavailable in the completed search snapshot.",
            )
        query_context = search_result.get("query") if isinstance(search_result.get("query"), dict) else {}
        query_text = str(query_context.get("queryText") or search_job.payload.get("queryText") or "").strip()
        records: list[dict[str, Any]] = []
        for evidence_id in reviewed_ids:
            row = by_id[evidence_id]
            evidence_record = EvidenceRecord(
                provider=str(row.get("sourcePlatform") or "").strip(),
                stable_id=evidence_id,
                title=str(row.get("title") or "").strip(),
                abstract=str(row.get("abstract") or "").strip(),
                doi=str(row.get("doi") or "").strip() or None,
                full_text_url=str(row.get("url") or "").strip() or None,
                access_status=str(row.get("accessStatus") or "metadata").strip() or "metadata",
                warning=str(row.get("warning") or "").strip(),
            )
            evidence_qualification = qualify_science125_evidence(science125_id, query_text, evidence_record)
            if not evidence_qualification.eligible_for_generation:
                continue
            records.append({
                "provider": evidence_record.provider,
                "providerFamily": str(row.get("providerFamily") or "").strip()
                or self._science125_provider_family(evidence_record.provider),
                "stableId": evidence_id,
                "title": evidence_record.title,
                "abstract": evidence_record.abstract,
                "doi": evidence_record.doi,
                "fullTextUrl": evidence_record.full_text_url,
                "accessStatus": evidence_record.access_status,
                "warning": evidence_record.warning or None,
            })

        _page_context, page_snapshots = self._source_page_input(job, include_excerpt_text=True)
        for snapshot in page_snapshots:
            excerpt_text = str(snapshot.pop("excerptText", "")).strip()
            text_hash = str(snapshot.get("textSha256") or "")
            document_id = int(snapshot.get("documentId") or 0)
            if not excerpt_text or not text_hash or document_id < 1:
                raise SafeJobError("SOURCE_PAGE_SNAPSHOT_CHANGED", "A reviewed PDF page excerpt is unavailable.")
            records.append({
                "provider": "user_pdf",
                "providerFamily": "user_pdf",
                "stableId": f"user_pdf:{document_id}:{text_hash}",
                "title": str(snapshot.get("title") or "Reviewed PDF page excerpt"),
                "abstract": excerpt_text,
                "accessStatus": "reviewed_full_text_excerpt",
                "warning": "User-reviewed PDF page excerpt; validate its publication provenance before relying on it.",
            })

        full_text_records = [record for record in records if self._science125_evidence_is_full_text(record)]
        families = {
            str(record.get("providerFamily") or "").strip()
            or self._science125_provider_family(str(record.get("provider") or ""))
            for record in full_text_records
        }
        if len(full_text_records) < 3 or len(families) < 2:
            raise SafeJobError(
                "EVIDENCE_INSUFFICIENT",
                "Science 125 generation requires at least three reviewed full-text evidence records with medium or higher relevance from two provider families.",
            )
        search_query = search_result.get("query") if isinstance(search_result.get("query"), dict) else {}
        snapshot = {
            "searchJobId": search_job.id,
            "queryHash": str(search_query.get("queryHash") or ""),
            "reviewedEvidence": [
                {
                    "stableId": record["stableId"],
                    "provider": record["provider"],
                    "accessStatus": record["accessStatus"],
                    "contentHash": hashlib.sha256(str(record.get("abstract") or "").encode("utf-8")).hexdigest(),
                }
                for record in records
            ],
            "pageSelections": page_snapshots,
        }
        policy_hashes = search_result.get("policyHashes") if isinstance(search_result.get("policyHashes"), dict) else {}
        return records, snapshot, self._science125_snapshot_hash(snapshot), self._science125_snapshot_hash(policy_hashes)

    def _run_science125_hypothesis_generate(self, job: StoredJob) -> dict[str, Any]:
        if not is_science125_pilot_enabled(str(job.payload.get("science125Id") or "")):
            raise SafeJobError(
                "SCIENCE125_PILOT_NOT_ENABLED",
                "Science 125 generation is currently limited to the three audited pilot items.",
            )
        authoritative_input = self._science125_authoritative_input(job)
        if authoritative_input is None:
            raise SafeJobError("SCIENCE125_ID_REQUIRED", "A Science 125 item ID is required.")
        question, source_context, source_context_snapshot = authoritative_input
        try:
            route = get_science125_route(str(job.payload.get("science125Id") or ""))
        except Science125RoutingError as exc:
            raise SafeJobError("SCIENCE125_ROUTING_UNAVAILABLE", "The Science 125 routing profile is unavailable.") from exc
        records, evidence_snapshot, evidence_snapshot_hash, policy_hash = self._science125_reviewed_evidence(job)
        self._raise_if_cancelled(job)
        config = self._required_science125_llm_config(job.user_id)
        budget = llm_client().LLMBudget(max_total_tokens=20_000, max_estimated_cost_cny=3.0)
        request = ResearchGenerationRequest(
            question=question,
            profile="general_science",
            chemistry_subdomain=None,
            candidate_count=3,
            science125_id=str(job.payload["science125Id"]),
            science125_source_context=source_context,
            science125_routing={
                **route.model_dump(by_alias=True),
                "routingVersion": "science125-routing-v1",
            },
            evidence_records=tuple(records),
        )

        def telemetry_sink(event: Mapping[str, Any]) -> None:
            enriched = dict(event)
            enriched["policy_hash"] = policy_hash
            enriched["evidence_snapshot_hash"] = evidence_snapshot_hash
            self.model_call_ledger.record(enriched)

        self._update_progress(job, progress=45, message="Generating a dedicated Science 125 research-v1 result with Qwen.")
        try:
            generated = ResearchGenerationService.from_environment().generate(
                request,
                config=config,
                budget=budget,
                context=llm_client().LLMCallContext(resource_type="science125_item", resource_id=job.id),
                telemetry_sink=telemetry_sink,
            )
        except ResearchGenerationValidationError as exc:
            raise SafeJobError(
                "SCIENCE125_SCHEMA_INVALID",
                "Qwen returned a Science 125 result that could not satisfy the research-v1 contract.",
            ) from exc
        except ValueError as exc:
            raise SafeJobError("SCIENCE125_GENERATION_INVALID", "Science 125 generation input is invalid.") from exc
        except ResearchGenerationCallError as exc:
            result = exc.result
            status = result.status_code
            error_type = result.error_type or ("budget" if result.status == "budget_exceeded" else "unknown")
            if error_type == "budget":
                advice = "Increase the Science 125 per-run budget or reduce the reviewed evidence context."
            elif status in {401, 403}:
                advice = "Re-save a valid server DashScope API key in API Settings and retry."
            elif status in {400, 404}:
                advice = "Verify the DashScope model name and request configuration."
            elif status == 429:
                advice = "DashScope rate limit persisted after retries; wait for cooldown and retry."
            elif status is not None and status >= 500:
                advice = "DashScope returned a server error after retries; retry later."
            elif error_type in {"timeout", "network"}:
                advice = "DashScope connection timed out after retries; check network and retry."
            else:
                advice = "Review the Qwen API configuration and retry."
            request_hint = f" requestId={result.request_id}." if result.request_id else ""
            raise SafeJobError(
                "SCIENCE125_QWEN_FAILED",
                f"DashScope Qwen generation failed: status={status or 'none'}, "
                f"error_type={error_type}, attempts={result.attempts}.{request_hint} {advice}",
            ) from exc
        except RuntimeError as exc:
            raise SafeJobError("SCIENCE125_QWEN_FAILED", "DashScope Qwen could not generate the Science 125 result.") from exc
        self._raise_if_cancelled(job)
        output = generated.output.model_dump(by_alias=True, mode="json")
        return {
            "researchOutput": output,
            "_reportSnapshot": {
                "reviewedEvidence": records,
                "evidenceSnapshot": evidence_snapshot,
                "evidenceSnapshotHash": evidence_snapshot_hash,
                "policyHash": policy_hash,
            },
            "audit": {
                "contractVersion": output["contractVersion"],
                "provider": generated.call.provider,
                "model": generated.call.model,
                "requestId": generated.call.request_id,
                "totalTokens": generated.total_tokens,
                "latencyMs": generated.latency_ms,
                "retryCount": generated.retry_count,
                "estimatedCostCny": generated.estimated_cost_cny,
                "schemaRepaired": generated.schema_repaired,
                "policyHash": policy_hash,
                "evidenceSnapshotHash": evidence_snapshot_hash,
                "sourceContext": source_context_snapshot,
                "evidenceCount": len(records),
                "providerFamilies": sorted({
                    str(record.get("providerFamily") or "").strip()
                    or self._science125_provider_family(str(record["provider"]))
                    for record in records
                }),
            },
        }

    def _run_hypothesis_generate(self, job: StoredJob) -> dict[str, Any]:
        if job.payload.get("science125Id"):
            return self._run_science125_hypothesis_generate(job)
        research_question = str(job.payload.get("researchQuestion") or "").strip()
        supplemental_context = str(
            job.payload.get("supplementalContext") or job.payload.get("literatureText") or ""
        ).strip()
        source_doc_ids = [int(value) for value in job.payload.get("sourceDocIds") or []]
        multimodal_run_ids = [str(value) for value in job.payload.get("multimodalRunIds") or []]
        if len(source_doc_ids) > 20 or len(set(source_doc_ids)) != len(source_doc_ids):
            raise SafeJobError("VALIDATION_ERROR", "Select at most 20 unique source documents.")
        if not research_question and not supplemental_context and not source_doc_ids:
            raise SafeJobError(
                "INPUT_REQUIRED",
                "Provide a research question, source document, or supplemental context.",
            )
        if len(multimodal_run_ids) > 20 or len(set(multimodal_run_ids)) != len(multimodal_run_ids):
            raise SafeJobError("VALIDATION_ERROR", "Select at most 20 unique multimodal runs.")

        multimodal_context_parts: list[str] = []
        multimodal_evidence: dict[str, Any] = {"figures": [], "associations": []}
        quantitative_report: dict[str, Any] = {"scalingRelations": [], "correlations": []}
        multimodal_snapshots: list[dict[str, Any]] = []
        for run_id in multimodal_run_ids:
            run = self.science_store.get_multimodal_run(user_id=job.user_id, run_id=run_id)
            if not run:
                raise SafeJobError("MULTIMODAL_RUN_NOT_FOUND", "A selected multimodal run was not found.")
            result_snapshot = safe_result_json(run.corrected_result)
            context = str(result_snapshot.get("context") or "").strip()
            if context:
                multimodal_context_parts.append(context)
            evidence = result_snapshot.get("evidence") if isinstance(result_snapshot.get("evidence"), dict) else {}
            multimodal_evidence["figures"].extend(evidence.get("figures") or [])
            multimodal_evidence["associations"].extend(evidence.get("associations") or [])
            quantitative = result_snapshot.get("quantitative") if isinstance(result_snapshot.get("quantitative"), dict) else {}
            quantitative_report["scalingRelations"].extend(
                quantitative.get("scalingRelations") or quantitative.get("scaling_relations") or []
            )
            quantitative_report["correlations"].extend(quantitative.get("correlations") or [])
            multimodal_snapshots.append({
                "runId": run.id,
                "revision": run.revision,
                "sources": run.source_refs,
                "result": result_snapshot,
            })
        multimodal_context = "\n\n".join(multimodal_context_parts)
        quantitative_context = "\n".join(
            str(item.get("equation") or item.get("bestEquation") or "")
            for item in quantitative_report["scalingRelations"]
            if isinstance(item, dict)
        )

        config = self._required_llm_config(job.user_id)
        source_documents: list[HypothesisSourceDocument] = []
        for document_id in source_doc_ids:
            loaded = self._load_owned_document(job.user_id, document_id)
            if not str(loaded.get("fullText") or "").strip():
                raise SafeJobError(
                    "EMPTY_DOCUMENT",
                    f"Document {document_id} has no readable text for hypothesis generation.",
                )
            source_documents.append(HypothesisSourceDocument(
                id=loaded["id"],
                title=loaded["title"],
                source_type=loaded["sourceType"],
                text=loaded["fullText"],
            ))
        try:
            literature_text, source_metadata = build_hypothesis_input(
                source_documents,
                supplemental_context,
            )
        except ValueError as exc:
            raise SafeJobError("HYPOTHESIS_CONTEXT_TOO_LARGE", str(exc)) from exc

        api_key = api_keys.api_key_store.get_key(job.user_id, "dashscope")
        rag_context, evidence_context, evidence_warnings = self._build_hypothesis_evidence(
            job,
            source_documents,
            research_question,
            api_key,
        )
        warnings = list(dict.fromkeys(str(value) for value in evidence_warnings if str(value).strip()))
        for item in source_metadata:
            if item.get("truncated"):
                warnings.append(
                    f"Document {item['documentId']} ({item['title']}) was explicitly truncated to "
                    f"{item['includedChars']:,} of {item['sourceChars']:,} characters in the balanced context."
                )
        evidence_context = {
            **(evidence_context or {}),
            "sourceDocuments": source_metadata,
        }
        interaction_history = {
            "session_id": str(uuid.uuid4()),
            "mode": "hitl" if bool(job.payload.get("hitlEnabled", True)) else "auto",
            "interactions": [],
        }

        def on_progress(stage: str, current: int, total: int) -> None:
            self._raise_if_cancelled(job)
            progress = 10 + int((current / max(total, 1)) * 80)
            self.store.transition(
                job.id,
                from_statuses=("RUNNING",),
                progress=min(progress, 95),
                stage=stage,
                message=stage,
            )

        orchestrator_holder: dict[str, Any] = {}

        def feedback_callback(hypothesis_json, critique_json, round_number, history):
            orchestrator = orchestrator_holder.get("value")
            if orchestrator is None:
                return WebHypothesisFeedback(action="cancel")
            return self._wait_for_hypothesis_feedback(
                job,
                hypothesis_json if isinstance(hypothesis_json, dict) else {},
                critique_json if isinstance(critique_json, dict) else None,
                int(round_number or 0),
                history if isinstance(history, dict) else interaction_history,
                orchestrator,
                evidence_context,
            )

        orchestrator = agent_framework().HypothesisOrchestrator(
            config=config,
            max_iterations=int(job.payload.get("maxIterations") or 2),
            on_progress=on_progress,
            user_feedback_callback=(
                feedback_callback if bool(job.payload.get("hitlEnabled", True)) else None
            ),
            interaction_history=interaction_history,
            auto_verify=bool(job.payload.get("autoVerify", True)),
            rag_context=rag_context,
            evidence_context=evidence_context,
            domain=str(job.payload.get("domain") or ""),
            user_id=job.user_id,
            source_doc_id=source_doc_ids[0] if source_doc_ids else 0,
            source_doc_ids=source_doc_ids,
            multimodal_context=multimodal_context,
            quantitative_context=quantitative_context,
            multimodal_evidence=multimodal_evidence,
            quantitative_report=quantitative_report,
            external_data_api_keys={
                "materials_project": (
                    api_keys.api_key_store.get_key(job.user_id, "materials_project")
                    or os.getenv("MATERIALS_PROJECT_API_KEY", "").strip()
                    or os.getenv("MP_API_KEY", "").strip()
                ),
            },
        )
        orchestrator_holder["value"] = orchestrator
        result = orchestrator.run(
            literature_text=literature_text,
            research_question=research_question,
        )
        self._raise_if_cancelled(job)

        raw_json = getattr(result, "raw_json", None)
        if not isinstance(raw_json, dict) or not raw_json:
            raise SafeJobError("INVALID_HYPOTHESIS_OUTPUT", "The hypothesis pipeline returned no valid result.")
        if raw_json.get("llm_error") or raw_json.get("parse_error"):
            raise SafeJobError("INVALID_HYPOTHESIS_OUTPUT", "The hypothesis pipeline returned an invalid result.")
        raw_json = dict(raw_json)
        raw_json["_source_document_ids"] = source_doc_ids
        raw_json["_source_documents"] = [
            {"id": item.id, "title": item.title, "source_type": item.source_type}
            for item in source_documents
        ]
        if job.payload.get("domain"):
            raw_json["_domain"] = str(job.payload.get("domain"))

        result.raw_json = raw_json
        result.final_output = json.dumps(raw_json, ensure_ascii=False, indent=2, default=str)
        final_history = getattr(result, "interaction_history", None)
        if not isinstance(final_history, dict):
            final_history = getattr(orchestrator, "interaction_history", interaction_history)
        if not isinstance(final_history, dict):
            final_history = interaction_history
        final_history["session_id"] = str(final_history.get("session_id") or interaction_history["session_id"])
        final_history["mode"] = interaction_history["mode"]
        final_history.setdefault("interactions", [])

        extra = {
            "iterations": getattr(result, "iterations", []) or [],
            "critique_history": getattr(result, "critique_history", []) or [],
            "reasoning_chain": getattr(result, "reasoning_chain", None),
            "verification_report": getattr(result, "verification_report", None),
            "results_verification": getattr(result, "results_verification", None),
            "research_question": research_question or None,
            "cross_domain_analogies": getattr(result, "cross_domain_analogies", None),
            "debate_history": getattr(result, "debate_history", []) or [],
            "closed_loop_validation": getattr(result, "closed_loop_validation", None),
            "executable_validation": getattr(result, "executable_validation", None),
            "scientific_toolkit": getattr(result, "scientific_toolkit", None),
            "source_document_ids": source_doc_ids,
            "source_documents": source_metadata,
            "scientific_evidence_context": evidence_context,
            "interaction_history": final_history,
            "domain": str(job.payload.get("domain") or ""),
            "multimodal_run_snapshots": multimodal_snapshots,
            "multimodal_context": multimodal_context,
            "multimodal_evidence": multimodal_evidence,
            "quantitative_context": quantitative_context,
            "quantitative_report": quantitative_report,
        }
        title = str(raw_json.get("paper_title") or raw_json.get("title") or "Untitled hypothesis")[:500]
        try:
            confidence = int(getattr(result, "confidence", raw_json.get("confidence", 5)) or 5)
        except (TypeError, ValueError):
            confidence = 5
        confidence = max(1, min(confidence, 10))
        feasibility = str(getattr(result, "feasibility", raw_json.get("feasibility", "unknown")) or "unknown")[:20]

        database = db()
        session = database.get_session()
        try:
            hypothesis = database.Hypothesis(
                user_id=job.user_id,
                title=title,
                research_question=research_question or None,
                result_json=result.final_output,
                confidence=confidence,
                feasibility=feasibility,
                iteration_count=len(extra["iterations"]),
                related_doc_ids=(json.dumps(source_doc_ids, ensure_ascii=False) if source_doc_ids else None),
                extra_json=json.dumps(extra, ensure_ascii=False, default=str),
                status="draft",
            )
            session.add(hypothesis)
            session.flush()
            if final_history.get("interactions"):
                session.add(database.HypothesisFeedback(
                    hypothesis_id=hypothesis.id,
                    user_id=job.user_id,
                    session_id=final_history["session_id"],
                    interaction_json=json.dumps(final_history, ensure_ascii=False, default=str),
                    mode=final_history["mode"],
                ))
            self._raise_if_cancelled(job)
            session.commit()
            session.refresh(hypothesis)
            hypothesis_id = int(hypothesis.id)
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

        artifacts: list[dict[str, Any]] = []
        try:
            artifacts, artifact_warnings = self._create_hypothesis_artifacts(
                job,
                hypothesis_id,
                result,
                final_history,
            )
            warnings.extend(str(value) for value in artifact_warnings if str(value).strip())
        except Exception:
            logger.warning("Could not create artifacts for hypothesis %s", hypothesis_id, exc_info=True)
            warnings.append("The hypothesis was saved, but one or more artifacts could not be generated.")
        return {
            "hypothesisId": hypothesis_id,
            "summary": {
                "title": title,
                "confidence": confidence,
                "feasibility": feasibility,
                "status": "draft",
            },
            "warnings": list(dict.fromkeys(warnings)),
            "artifacts": artifacts,
        }

    def _load_owned_hypothesis_result(
        self,
        user_id: int,
        hypothesis_id: int,
    ) -> tuple[Any, dict[str, Any]]:
        database = db()
        session = database.get_session()
        try:
            hypothesis = session.query(database.Hypothesis).filter(
                database.Hypothesis.id == hypothesis_id,
                database.Hypothesis.user_id == user_id,
            ).first()
            if not hypothesis:
                raise SafeJobError("HYPOTHESIS_NOT_FOUND", "The hypothesis was not found.")
            raw = parse_json_object(hypothesis.result_json)
            if not raw:
                raise SafeJobError("INVALID_HYPOTHESIS_OUTPUT", "The saved hypothesis has no structured result.")
            extra = parse_json_object(hypothesis.extra_json)
            interaction_history = extra.get("interaction_history")
            if not isinstance(interaction_history, dict):
                feedback = session.query(database.HypothesisFeedback).filter(
                    database.HypothesisFeedback.hypothesis_id == hypothesis_id,
                    database.HypothesisFeedback.user_id == user_id,
                ).order_by(database.HypothesisFeedback.created_at.desc()).first()
                interaction_history = parse_json_object(feedback.interaction_json) if feedback else {"interactions": []}
            result = agent_framework().HypothesisResult(
                raw_json=raw,
                iterations=extra.get("iterations") or [],
                critique_history=extra.get("critique_history") or [],
                final_output=json.dumps(raw, ensure_ascii=False, indent=2, default=str),
                confidence=int(hypothesis.confidence or 5),
                feasibility=str(hypothesis.feasibility or ""),
                verification_report=extra.get("verification_report"),
                results_verification=extra.get("results_verification"),
                reasoning_chain=extra.get("reasoning_chain"),
                cross_domain_analogies=extra.get("cross_domain_analogies"),
                debate_history=extra.get("debate_history") or [],
                closed_loop_validation=extra.get("closed_loop_validation"),
                executable_validation=extra.get("executable_validation"),
                scientific_toolkit=extra.get("scientific_toolkit"),
                interaction_history=interaction_history,
            )
            return result, interaction_history
        finally:
            session.close()

    def _run_hypothesis_report(self, job: StoredJob) -> dict[str, Any]:
        hypothesis_id = int(job.payload.get("hypothesisId") or 0)
        if hypothesis_id < 1:
            raise SafeJobError("VALIDATION_ERROR", "A hypothesis ID is required.")
        self._update_progress(job, progress=15, message="Loading hypothesis report data.")
        result, _ = self._load_owned_hypothesis_result(job.user_id, hypothesis_id)
        self._raise_if_cancelled(job)
        self._update_progress(job, progress=45, message="Rendering interactive hypothesis report.")
        artifact, warnings = self._create_report_artifact(
            job,
            hypothesis_id,
            result,
            enhance=bool(job.payload.get("enhance", True)),
        )
        return {"hypothesisId": hypothesis_id, "artifacts": [artifact], "warnings": warnings}

    def _run_hypothesis_workflow_export(self, job: StoredJob) -> dict[str, Any]:
        hypothesis_id = int(job.payload.get("hypothesisId") or 0)
        if hypothesis_id < 1:
            raise SafeJobError("VALIDATION_ERROR", "A hypothesis ID is required.")
        self._update_progress(job, progress=20, message="Loading hypothesis workflow data.")
        result, _ = self._load_owned_hypothesis_result(job.user_id, hypothesis_id)
        self._raise_if_cancelled(job)
        self._update_progress(job, progress=55, message="Building dry-run workflow package.")
        artifact = self._create_workflow_artifact(job, hypothesis_id, result)
        return {"hypothesisId": hypothesis_id, "artifacts": [artifact], "warnings": []}

    def _resolve_multimodal_source(self, user_id: int, source: dict[str, Any]) -> dict[str, Any]:
        source_type = str(source.get("sourceType") or "")
        asset_id = str(source.get("assetId") or "")
        if source_type == "upload":
            asset = self.science_store.get_multimodal_asset(user_id=user_id, asset_id=asset_id)
            if not asset or asset.path.is_symlink() or not asset.path.is_file():
                raise SafeJobError("MULTIMODAL_ASSET_NOT_FOUND", "A multimodal asset was not found.")
            root = (self.multimodal_assets_dir / str(user_id)).resolve()
            if not asset.path.is_relative_to(root):
                raise SafeJobError("UNSAFE_MULTIMODAL_ASSET", "A multimodal asset was outside managed storage.")
            return {
                "path": asset.path,
                "assetType": asset.asset_type,
                "source": {
                    "sourceType": "upload",
                    "assetId": asset.id,
                    "fileName": asset.file_name,
                    "mimeType": asset.mime_type,
                },
                "metadata": asset.metadata,
                "sheetNames": list(source.get("sheetNames") or []),
                "document": None,
            }
        if source_type == "documentAsset":
            document_id = int(source.get("documentId") or 0)
            document = self._load_owned_document(user_id, document_id)
            asset = self.analysis_store.get_asset_for_user(user_id, document_id, asset_id)
            if not asset or asset.path.is_symlink() or not asset.path.is_file():
                raise SafeJobError("MULTIMODAL_ASSET_NOT_FOUND", "A document image was not found.")
            root = self.assets_dir.resolve()
            if not asset.path.is_relative_to(root):
                raise SafeJobError("UNSAFE_MULTIMODAL_ASSET", "A document image was outside managed storage.")
            return {
                "path": asset.path,
                "assetType": "image",
                "source": {
                    "sourceType": "documentAsset",
                    "documentId": document_id,
                    "assetId": asset.id,
                    "fileName": asset.file_name,
                    "mimeType": asset.mime_type,
                    "documentTitle": document["title"],
                },
                "metadata": {},
                "sheetNames": [],
                "document": document,
            }
        raise SafeJobError("VALIDATION_ERROR", "Unsupported multimodal source.")

    def _analyze_multimodal_source(
        self,
        job: StoredJob,
        source: dict[str, Any],
        *,
        question: str,
        use_literature_context: bool,
    ) -> dict[str, Any]:
        resolved = self._resolve_multimodal_source(job.user_id, source)
        config = self._required_llm_config(job.user_id)
        path = Path(resolved["path"])
        warnings = list(resolved.get("metadata", {}).get("warnings") or [])
        coverage: dict[str, Any] | list[dict[str, Any]] = {}
        if resolved["assetType"] == "workbook":
            coverage = workbook_coverage(path, resolved["sheetNames"])
            analyses = []
            selected_sheets = [item["sheetName"] for item in coverage]
            module = multimodal()
            if path.suffix.lower() == ".csv":
                import pandas as pd

                frame = pd.read_csv(path)
                data_context = dataframe_model_context(frame)
                prompt = (
                    "Analyze the following experimental CSV data. Report data overview, statistics, trends, "
                    "scientific interpretation, uncertainty, and limitations. Do not invent values.\n\n"
                    f"User question: {question or 'Analyze the experimental data.'}\n\n{data_context}"
                )
                analysis = llm_client()._chat(prompt, config, task="qa", timeout=120)
                analyses.append(f"## CSV\n\n{analysis}")
            else:
                for sheet_name in selected_sheets:
                    self._raise_if_cancelled(job)
                    analysis = module.analyze_excel_data(
                        str(path),
                        sheet_name=sheet_name,
                        user_question=question or None,
                        config=config,
                    )
                    analyses.append(f"## Sheet: {sheet_name}\n\n{analysis}")
            analysis_text = "\n\n---\n\n".join(analyses)
            image_type = "table"
        else:
            literature_context = None
            document = resolved.get("document")
            if use_literature_context and document:
                full_text = str(document.get("fullText") or "")
                selected_context, coverage = select_multimodal_literature_context(full_text, question)
                literature_context = selected_context or None
            analysis_text = multimodal().analyze_scientific_image(
                str(path),
                user_question=question or None,
                config=config,
                literature_context=literature_context,
            )
            image_type = multimodal_pipeline()._infer_image_type(analysis_text, str(path))

        if not str(analysis_text or "").strip() or multimodal_pipeline()._is_error_response(analysis_text):
            raise SafeJobError("MULTIMODAL_MODEL_FAILED", "The multimodal model did not return a valid analysis.")
        raw_points = multimodal_pipeline()._extract_data_points_from_analysis(analysis_text, config=config)
        cleaner = data_cleaner().DataCleaner()
        cleaned = cleaner.clean_data_points(raw_points, source=resolved["source"]["fileName"]) if raw_points else []
        item = {
            "status": "succeeded",
            "source": resolved["source"],
            "imageType": image_type,
            "analysisMarkdown": analysis_text,
            "summary": analysis_text[:600],
            "dataPoints": [cleaned_point_to_dict(point) for point in cleaned],
            "coverage": coverage,
            "warnings": warnings,
            "annotations": [],
            "_cleanedObjects": cleaned,
        }
        return item

    def _run_multimodal_analyze(self, job: StoredJob) -> dict[str, Any]:
        sources = list(job.payload.get("sources") or [])
        if not sources or len(sources) > 50:
            raise SafeJobError("VALIDATION_ERROR", "Select between 1 and 50 multimodal sources.")
        question = str(job.payload.get("question") or "").strip()
        use_literature_context = bool(job.payload.get("useLiteratureContext", True))
        items: list[dict[str, Any]] = []
        all_cleaned = []
        warnings: list[str] = []
        for index, source in enumerate(sources):
            self._raise_if_cancelled(job)
            self._update_progress(
                job,
                progress=5 + int(index / max(len(sources), 1) * 75),
                stage="MULTIMODAL_ANALYSIS",
                message=f"Analyzing source {index + 1} of {len(sources)}.",
            )
            try:
                item = self._analyze_multimodal_source(
                    job,
                    source,
                    question=question,
                    use_literature_context=use_literature_context,
                )
                item.setdefault("status", "succeeded")
                all_cleaned.extend(item.pop("_cleanedObjects", []))
                items.append(item)
            except JobCancelled:
                raise
            except Exception:
                logger.exception("Multimodal source analysis failed for job %s", job.id)
                items.append({
                    "status": "failed",
                    "source": {
                        "sourceType": source.get("sourceType"),
                        "assetId": source.get("assetId"),
                        "documentId": source.get("documentId"),
                    },
                    "error": {"code": "ANALYSIS_FAILED", "message": "This source could not be analyzed."},
                    "dataPoints": [],
                    "warnings": [],
                    "annotations": [],
                })
        succeeded = [item for item in items if item.get("status") == "succeeded"]
        if not succeeded:
            raise SafeJobError("MULTIMODAL_ANALYSIS_FAILED", "None of the selected sources could be analyzed.")

        self._update_progress(job, progress=84, stage="ASSOCIATION_MINING", message="Mining cross-source associations.")
        associations: list[dict[str, Any]] = []
        quantitative: dict[str, Any] = {"summary": {}, "scalingRelations": [], "correlations": []}
        if len(all_cleaned) >= 2:
            try:
                config = self._required_llm_config(job.user_id)
                report = data_miner().DataMiner().mine(all_cleaned, use_llm=True, config=config)
                associations = [camelize(item.to_dict()) for item in report.associations]
            except Exception:
                logger.warning("Association mining failed for job %s", job.id, exc_info=True)
                warnings.append("Cross-source association mining could not be completed.")
        if len(all_cleaned) >= 3:
            try:
                quantitative = camelize(data_miner().QuantitativeMiner().mine(all_cleaned).to_dict())
            except Exception:
                logger.warning("Quantitative mining failed for job %s", job.id, exc_info=True)
                warnings.append("Quantitative relation mining could not be completed.")
        result = safe_result_json({
            "summary": {
                "total": len(items),
                "succeeded": len(succeeded),
                "failed": len(items) - len(succeeded),
                "dataPoints": sum(len(item.get("dataPoints") or []) for item in succeeded),
                "associations": len(associations),
            },
            "items": items,
            "associations": associations,
            "quantitative": quantitative,
            "context": build_multimodal_context(items, associations),
            "evidence": {
                "figures": [{
                    "source": item.get("source"),
                    "imageType": item.get("imageType"),
                    "summary": item.get("summary"),
                    "points": item.get("dataPoints", []),
                } for item in succeeded],
                "associations": associations,
            },
            "warnings": warnings,
        })
        run = self.science_store.create_multimodal_run(
            user_id=job.user_id,
            question=question,
            options={"useLiteratureContext": use_literature_context},
            source_refs=sources,
            result=result,
            job_id=job.id,
        )
        return {"runId": run.id, **result}

    def _load_modeling_source(self, job: StoredJob) -> tuple[list[str], dict[str, Any], dict[str, Any], str]:
        source = dict(job.payload.get("source") or {})
        source_type = str(source.get("type") or "")
        if source_type == "manual":
            segments, coverage = segment_modeling_text(str(source.get("text") or ""))
            public_source = {
                "type": "manual",
                "chars": coverage["sourceChars"],
                "sha256": hashlib.sha256(str(source.get("text") or "").encode("utf-8")).hexdigest(),
            }
            return segments, coverage, public_source, "manual"
        if source_type == "document":
            document_id = int(source.get("documentId") or 0)
            document = self._load_owned_document(job.user_id, document_id)
            segments, coverage = select_document_modeling_text(str(document.get("fullText") or ""))
            return segments, coverage, {
                "type": "document",
                "documentId": document_id,
                "title": document["title"],
            }, "literature"
        if source_type == "hypothesis":
            hypothesis_id = int(source.get("hypothesisId") or 0)
            result, _ = self._load_owned_hypothesis_result(job.user_id, hypothesis_id)
            raw = dict(getattr(result, "raw_json", {}) or {})
            package = raw.get("_hypothesis_workflow_package")
            if not isinstance(package, dict):
                package = hypothesis_workflow_exporter().build_hypothesis_workflow_package(raw)
            package = self._validated_workflow_package_sources(package)
            return [], {"sourceChars": 0, "includedChars": 0, "segmentCount": 0, "truncated": False}, {
                "type": "hypothesis",
                "hypothesisId": hypothesis_id,
                "workflow": sanitize_public_data(package),
                "_internalWorkflow": package,
            }, "hypothesis"
        raise SafeJobError("VALIDATION_ERROR", "Unsupported modeling source.")

    def _modeling_result_from_workflow(self, source: dict[str, Any]) -> dict[str, Any]:
        package = source.get("workflow") if isinstance(source.get("workflow"), dict) else {}
        recommendations = package.get("vaspRecommendations") or package.get("vasp_recommendations") or []
        recommendation = recommendations[0] if isinstance(recommendations, list) and recommendations else {}
        defaults = vasp_defaults()
        incar_values = recommendation.get("incarRelax") or recommendation.get("incar_relax") or {}
        potcar_hints = recommendation.get("potcarHints") or recommendation.get("potcar_hints") or {}
        try:
            extraction = VaspExtraction.model_validate({
                "calc_type": "relax",
                "system_name": str(recommendation.get("target") or ""),
                "functional": "unknown",
                "is_metal": False,
                "incar": incar_values,
                "kpoints": {"mode": "Gamma", "mesh": "6 6 6"},
                "poscar": {"lattice_type": "unknown", "lattice_constants": {}, "basis_atoms": []},
                "potcar_elements": list(potcar_hints),
                "missing_params": [],
                "notes": str(recommendation.get("notes") or ""),
            })
        except ValueError as exc:
            raise SafeJobError(
                "INVALID_MODELING_OUTPUT",
                "The hypothesis workflow contains invalid structured data.",
            ) from exc
        result = build_vasp_workspace_result(
            extraction,
            requested_calc_type="relax",
            source_kind="hypothesis",
            defaults_module=defaults,
        )
        warnings = list(result.get("warnings") or [])
        for item in result.get("potcarElements") or []:
            suggested = str(potcar_hints.get(item["element"]) or item["potential"])
            if is_valid_potcar_potential_name(suggested):
                item["potential"] = suggested
            else:
                warnings.append(f"Ignored an invalid POTCAR suggestion for {item['element']}.")
            item["warnings"] = []
        potcar_lines = ["POTCAR is not distributed by Chalk.", "", "Element order:"]
        potcar_lines.extend(f"{item['element']} -> {item['potential']}" for item in result.get("potcarElements") or [])
        result["files"]["potcarGuide"] = "\n".join(potcar_lines) + "\n"
        result["workflow"] = package
        result["atomate2DryRun"] = package.get("atomate2Dryrun") or package.get("atomate2_dryrun") or {}
        result["riskNotices"] = list(package.get("riskNotices") or package.get("risk_notices") or []) + [
            *result.get("riskNotices", []),
            "Dry-run only. No calculation has been submitted.",
        ]
        result["warnings"] = list(dict.fromkeys(warnings))
        return result

    def _run_modeling_generate(self, job: StoredJob) -> dict[str, Any]:
        segments, coverage, public_source, provenance = self._load_modeling_source(job)
        internal_workflow = public_source.pop("_internalWorkflow", None)
        mode = str(job.payload.get("mode") or "vasp")
        requested_calc_type = str(job.payload.get("calcType") or "auto")
        vaspkit_task = str(job.payload.get("vaspkitTask") or "")
        self._update_progress(job, progress=10, stage="MODELING_SOURCE", message="Preparing modeling source data.")
        if public_source.get("type") == "hypothesis":
            result = self._modeling_result_from_workflow(public_source)
        else:
            config = self._required_llm_config(job.user_id)
            result: dict[str, Any] = {"riskNotices": [], "warnings": []}
            if mode in {"vasp", "both"}:
                extractions: list[VaspExtraction] = []
                for index, segment in enumerate(segments):
                    self._raise_if_cancelled(job)
                    self._update_progress(
                        job,
                        progress=15 + int(index / max(len(segments), 1) * 45),
                        stage="VASP_EXTRACTION",
                        message=f"Extracting VASP parameters from segment {index + 1} of {len(segments)}.",
                    )
                    raw = llm_client().extract_vasp_parameters(segment, requested_calc_type, config=config)
                    if str(raw).startswith(getattr(llm_client(), "LLM_ERROR_PREFIX", "__CHALK_LLM_ERROR__:")):
                        raise SafeJobError("MODELING_MODEL_FAILED", "The VASP parameter model call failed.")
                    try:
                        extractions.append(VaspExtraction.model_validate(parse_model_json(raw)))
                    except ValueError as exc:
                        raise SafeJobError("INVALID_MODELING_OUTPUT", "The VASP model returned invalid structured data.") from exc
                extraction = merge_vasp_extractions(extractions)
                result.update(build_vasp_workspace_result(
                    extraction,
                    requested_calc_type=requested_calc_type,
                    source_kind=provenance,
                    defaults_module=vasp_defaults(),
                ))
                guide = vasp_defaults().get_vaspkit_guide(vaspkit_task)
                result["vaspkit"] = {
                    "task": vaspkit_task,
                    "name": guide.get("name", "") if guide else "",
                    "steps": list(guide.get("steps") or []) if guide else [],
                }
            if mode in {"ms", "both"}:
                raw = llm_client().generate_ms_guide("\n\n".join(segments), "auto", config=config)
                try:
                    result["msGuide"] = MsGuideExtraction.model_validate(parse_model_json(raw)).model_dump(by_alias=True)
                except ValueError as exc:
                    raise SafeJobError("INVALID_MODELING_OUTPUT", "The Materials Studio model returned invalid structured data.") from exc
        result["coverage"] = coverage
        result["mode"] = mode
        workspace = self.science_store.create_modeling_workspace(
            user_id=job.user_id,
            source=public_source,
            mode=mode,
            original_result=safe_result_json(result),
            job_id=job.id,
        )
        if isinstance(internal_workflow, dict):
            workspace = self._import_hypothesis_workflow_structures(job, workspace, internal_workflow)
        return {
            "workspaceId": workspace.id,
            "revision": workspace.revision,
            "coverage": coverage,
            "warnings": workspace.current_result.get("warnings", []),
        }

    def _import_hypothesis_workflow_structures(
        self,
        job: StoredJob,
        workspace,
        package: dict[str, Any],
    ):
        candidates = package.get("structures") if isinstance(package.get("structures"), list) else []
        if not candidates:
            return workspace
        root = (self.modeling_assets_dir / str(job.user_id) / workspace.id / "structures").resolve()
        root.mkdir(parents=True, exist_ok=True)
        imported = []
        warnings: list[str] = []
        for candidate in candidates[:20]:
            self._raise_if_cancelled(job)
            if not isinstance(candidate, dict):
                continue
            raw_path = str(candidate.get("path") or "").strip()
            if not raw_path:
                warnings.append(f"Structure candidate {candidate.get('index', '?')} has no managed source file.")
                continue
            source_path = Path(raw_path).resolve()
            try:
                if source_path.is_symlink() or not source_path.is_file():
                    raise ValueError("source is unavailable")
                if source_path.stat().st_size > 10 * 1024 * 1024:
                    raise ValueError("source exceeds 10 MiB")
                content = source_path.read_text(encoding="utf-8")
                format_hint = str(candidate.get("format") or "").lower()
                format_name = "cif" if source_path.suffix.lower() == ".cif" or format_hint == "cif" else "poscar"
                geometry, canonical_poscar = parse_structure_content(content, format_name)
                destination = root / f"{uuid.uuid4().hex}.vasp"
                destination.write_text(canonical_poscar, encoding="utf-8")
                imported.append(self.science_store.register_modeling_structure(
                    user_id=job.user_id,
                    workspace_id=workspace.id,
                    managed_path=destination,
                    file_name=Path(str(candidate.get("name") or source_path.name)).name,
                    format="poscar",
                    geometry=geometry,
                    warnings=["Imported from a hypothesis workflow; scientific suitability still requires review."],
                ))
            except (OSError, UnicodeError, ValueError):
                logger.warning("Hypothesis structure import failed for job %s", job.id, exc_info=True)
                warnings.append(f"Structure candidate {candidate.get('index', '?')} could not be validated.")
        if not imported and not warnings:
            return workspace
        current_warnings = [str(value) for value in workspace.current_result.get("warnings", [])]
        return self.science_store.update_modeling_workspace(
            user_id=job.user_id,
            workspace_id=workspace.id,
            expected_revision=workspace.revision,
            changes={
                "structureImport": {
                    "candidates": min(len(candidates), 20),
                    "imported": len(imported),
                    "warnings": warnings,
                },
                "warnings": list(dict.fromkeys([*current_warnings, *warnings])),
            },
            active_structure_id=imported[0].id if imported else None,
        )

    def _run_modeling_export(self, job: StoredJob) -> dict[str, Any]:
        workspace_id = str(job.payload.get("workspaceId") or "")
        expected_revision = int(job.payload.get("expectedRevision") or 0)
        workspace = self.science_store.get_modeling_workspace(user_id=job.user_id, workspace_id=workspace_id)
        if not workspace:
            raise SafeJobError("MODELING_WORKSPACE_NOT_FOUND", "The modeling workspace was not found.")
        if workspace.revision != expected_revision:
            raise SafeJobError("REVISION_CONFLICT", "The modeling workspace changed before export.")
        self._update_progress(job, progress=25, stage="MODELING_EXPORT", message="Building modeling export package.")
        root = (self.modeling_assets_dir / str(job.user_id) / workspace.id).resolve()
        root.mkdir(parents=True, exist_ok=True)
        target = root / f"modeling-revision-{workspace.revision}.zip"
        temporary = target.with_suffix(".zip.tmp")
        result = workspace.current_result
        files = prepare_modeling_changes(result, {}, vasp_defaults()).get("files") or {}
        active_structure = None
        if workspace.active_structure_id:
            active_structure = self.science_store.get_modeling_structure(
                user_id=job.user_id,
                workspace_id=workspace.id,
                structure_id=workspace.active_structure_id,
            )
        includes_poscar = bool(active_structure and active_structure.path.is_file())
        manifest = {
            "workspaceId": workspace.id,
            "revision": workspace.revision,
            "mode": workspace.mode,
            "includesValidatedPoscar": includes_poscar,
            "includesPotcarBinary": False,
            "executionPolicy": "dry-run-only-not-submitted",
        }
        provenance = {
            "source": workspace.source,
            "revision": workspace.revision,
            "parameterSources": {
                key: value.get("source")
                for key, value in (result.get("incar") or {}).items()
                if isinstance(value, dict)
            },
        }
        try:
            with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                if files.get("incar"):
                    archive.writestr("INCAR", str(files["incar"]))
                if files.get("kpoints"):
                    archive.writestr("KPOINTS", str(files["kpoints"]))
                if files.get("potcarGuide"):
                    archive.writestr("POTCAR_GUIDE.txt", str(files["potcarGuide"]))
                if includes_poscar:
                    structure_root = (self.modeling_assets_dir / str(job.user_id) / workspace.id).resolve()
                    structure_path = active_structure.path.resolve()
                    if not structure_path.is_relative_to(structure_root) or structure_path.is_symlink():
                        raise SafeJobError("UNSAFE_MODELING_STRUCTURE", "The selected structure is outside managed storage.")
                    archive.writestr("POSCAR", structure_path.read_bytes())
                if result.get("vaspkit"):
                    archive.writestr("VASPKIT_GUIDE.json", json.dumps(result["vaspkit"], ensure_ascii=False, indent=2))
                if result.get("msGuide"):
                    archive.writestr("MATERIALS_STUDIO_GUIDE.json", json.dumps(result["msGuide"], ensure_ascii=False, indent=2))
                if result.get("workflow"):
                    archive.writestr("WORKFLOW.json", json.dumps(result["workflow"], ensure_ascii=False, indent=2))
                if result.get("atomate2DryRun"):
                    archive.writestr("ATOMATE2_DRY_RUN.json", json.dumps(result["atomate2DryRun"], ensure_ascii=False, indent=2))
                archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
                archive.writestr("provenance.json", json.dumps(provenance, ensure_ascii=False, indent=2, default=str))
                archive.writestr("RISK_NOTICES.md", "\n".join(f"- {item}" for item in result.get("riskNotices", [])))
                archive.writestr(
                    "README.md",
                    "# Chalk modeling package\n\nDry-run only. Review every structure and parameter before calculation.\n",
                )
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
        artifact = self.science_store.register_modeling_artifact(
            user_id=job.user_id,
            workspace_id=workspace.id,
            revision=workspace.revision,
            kind="modeling_package_zip",
            managed_path=target,
            file_name=target.name,
            mime_type="application/zip",
        )
        return {"workspaceId": workspace.id, "artifact": artifact.to_dict(), "warnings": []}

    def _run_evidence_query(self, job: StoredJob) -> dict[str, Any]:
        payload = job.payload
        session = db().get_session()
        try:
            bundle = evidence_database().query_evidence(
                session,
                user_id=job.user_id,
                domain=str(payload.get("domain") or ""),
                query_text=str(payload.get("queryText") or ""),
                reaction_type=str(payload.get("reactionType") or ""),
                battery_type=str(payload.get("batteryType") or ""),
                ion_type=str(payload.get("ionType") or ""),
                material=str(payload.get("material") or ""),
                metric_names=payload.get("metricNames") or None,
                adsorbates=payload.get("adsorbates") or None,
                max_each=int(payload.get("maxEach") or 6),
            )
            return evidence_database().evidence_search_bundle_to_dict(bundle)
        finally:
            session.close()

    def _run_evidence_manifest_import(self, job: StoredJob) -> dict[str, Any]:
        stored_path = Path(str(job.payload.get("storedPath") or "")).resolve()
        user_root = (self.evidence_imports_dir / str(job.user_id)).resolve()
        if stored_path.is_symlink() or not stored_path.is_relative_to(user_root) or not stored_path.is_file():
            raise SafeJobError("MANIFEST_NOT_FOUND", "The uploaded evidence manifest is no longer available.")
        session = db().get_session()
        try:
            self._update_progress(job, progress=15, message="Validating evidence manifest.")
            result = evidence_database().index_computational_manifest(
                session,
                user_id=job.user_id,
                dataset_name=str(job.payload.get("datasetName") or "OC dataset"),
                path=str(stored_path),
                max_rows=5000,
            )
            self._raise_if_cancelled(job)
            errors = sanitize_public_data(list(result.errors)[:20], private_roots=(user_root,))
            if result.imported == 0 and errors:
                raise SafeJobError("MANIFEST_IMPORT_FAILED", str(errors[0]))
            return {
                "imported": int(result.imported),
                "skipped": int(result.skipped),
                "errors": errors,
                "fileName": str(job.payload.get("originalFilename") or stored_path.name),
            }
        finally:
            session.close()
            stored_path.unlink(missing_ok=True)


job_service = JobService()
