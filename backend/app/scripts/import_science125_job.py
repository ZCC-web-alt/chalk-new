from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.core.config import get_settings
from app.services.job_store import WebJobStore
from app.services.science125_report_service import Science125ReportError, Science125ReportService
from app.services.science125_report_store import Science125ReportStore


def main() -> int:
    parser = argparse.ArgumentParser(description="Import a completed interactive Science 125 job into the report library.")
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--web-db", type=Path, default=None)
    args = parser.parse_args()
    path = (args.web_db or get_settings().web_db_path).resolve()
    jobs = WebJobStore(path)
    store = Science125ReportStore(path)
    service = Science125ReportService(store=store)
    try:
        job = jobs.get(args.job_id)
        if job is None:
            raise Science125ReportError("JOB_NOT_FOUND", "The requested job was not found.")
        search_id = str(job.payload.get("literatureSearchJobId") or "").strip()
        literature_job = jobs.get(search_id) if search_id else None
        report = service.import_interactive_job(job=job, literature_job=literature_job)
        print(json.dumps({
            "reportId": report.id,
            "batchId": report.batch_id,
            "questionId": report.question_id,
            "sourceType": report.source_type,
            "sourceJobId": report.source_job_id,
            "selectedHypothesisId": report.selected_hypothesis_id,
            "exports": [item.to_dict() for item in store.list_exports_for_report(user_id=report.user_id, report_id=report.id)],
        }, ensure_ascii=False, indent=2))
        return 0
    except Science125ReportError as exc:
        print(json.dumps({"code": exc.code, "message": exc.message}, ensure_ascii=False), flush=True)
        return 2
    finally:
        service.dispose()
        jobs.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
