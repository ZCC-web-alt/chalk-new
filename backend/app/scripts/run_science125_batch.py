from __future__ import annotations

import argparse
import sys
from pathlib import Path

from app.core.config import get_settings
from app.services.science125_report_service import Science125ReportService
from app.services.science125_report_store import Science125ReportStore


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a durable Science 125 hypothesis batch sequentially.")
    parser.add_argument("--batch-id", required=True, help="Science 125 batch UUID.")
    parser.add_argument(
        "--question-id",
        action="append",
        dest="question_ids",
        help="Optional question ID to run/retry. May be repeated.",
    )
    parser.add_argument(
        "--web-db",
        type=Path,
        default=get_settings().web_db_path,
        help="Path to web.db. Defaults to CHALK_WEB_DATA_DIR/web.db.",
    )
    args = parser.parse_args(argv)
    store = Science125ReportStore(args.web_db)
    service = Science125ReportService(store=store)
    try:
        batch = service.run_batch(
            batch_id=args.batch_id,
            question_ids=tuple(args.question_ids) if args.question_ids else None,
        )
        if batch is None:
            print("Science 125 batch not found.", file=sys.stderr)
            return 2
        print(
            f"Science 125 batch {batch.id}: "
            f"status={batch.status}, succeeded={batch.succeeded_count}/{batch.total_count}, "
            f"failed={batch.failed_count}, blockedEvidence={batch.blocked_evidence_count}, "
            f"tokens={batch.total_tokens}, costCny={batch.estimated_cost_cny:.4f}"
        )
        return 0 if batch.status == "SUCCEEDED" else 1
    finally:
        service.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
