from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = PROJECT_ROOT / "backend"
sys.path.insert(0, str(BACKEND_DIR))

from app.services.model_call_ledger import ModelCallLedgerStore  # noqa: E402
from app.services.science_canary import require_dashscope_key, run_canary_item  # noqa: E402
from app.core.legacy import llm_client as load_llm_client  # noqa: E402


CANARY_IDS = ("S125-006", "S125-043", "S125-054")


def _load_canary_items(path: Path) -> list[dict[str, object]]:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("manifestVersion") != "science125-v1":
        raise ValueError("The canary runner requires the science125-v1 manifest.")
    questions = manifest.get("questions")
    if not isinstance(questions, list):
        raise ValueError("The Science 125 manifest has no questions array.")
    by_id = {
        str(item.get("id")): item
        for item in questions
        if isinstance(item, dict)
    }
    missing = [item_id for item_id in CANARY_IDS if item_id not in by_id]
    if missing:
        raise ValueError(f"Science 125 canary items are missing: {', '.join(missing)}")
    return [by_id[item_id] for item_id in CANARY_IDS]


def _write_summary(path: Path, payload: dict[str, object]) -> None:
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Run three real, auditable Science 125 Qwen canaries.")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--summary-path", type=Path, required=True)
    parser.add_argument("--ledger-path", type=Path, default=PROJECT_ROOT / "data" / "web.db")
    parser.add_argument("--model", default="qwen3.7-max")
    parser.add_argument("--max-total-tokens", type=int, default=120_000)
    parser.add_argument(
        "--max-estimated-cost-cny",
        type=float,
        default=os.getenv("QWEN_MAX_ESTIMATED_COST_CNY"),
    )
    parser.add_argument(
        "--input-cost-per-million-cny",
        type=float,
        default=os.getenv("QWEN_INPUT_COST_PER_MILLION_CNY", "0"),
    )
    parser.add_argument(
        "--output-cost-per-million-cny",
        type=float,
        default=os.getenv("QWEN_OUTPUT_COST_PER_MILLION_CNY", "0"),
    )
    parser.add_argument(
        "--require-cost-budget",
        action="store_true",
        help="Fail before calling DashScope unless prices and a cost ceiling are configured.",
    )
    args = parser.parse_args()

    try:
        api_key = require_dashscope_key()
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    if args.max_total_tokens <= 0:
        print("--max-total-tokens must be positive.", file=sys.stderr)
        return 2
    if args.input_cost_per_million_cny < 0 or args.output_cost_per_million_cny < 0:
        print("Model token prices cannot be negative.", file=sys.stderr)
        return 2
    if args.require_cost_budget and args.max_estimated_cost_cny is None:
        print("A required cost budget has no configured maximum.", file=sys.stderr)
        return 2
    if args.max_estimated_cost_cny is not None:
        if args.max_estimated_cost_cny <= 0:
            print("--max-estimated-cost-cny must be positive.", file=sys.stderr)
            return 2
        if args.input_cost_per_million_cny <= 0 or args.output_cost_per_million_cny <= 0:
            print(
                "A cost budget requires positive input and output token prices.",
                file=sys.stderr,
            )
            return 2

    try:
        items = _load_canary_items(args.manifest.resolve())
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Cannot load Science 125 manifest: {exc}", file=sys.stderr)
        return 2

    llm = load_llm_client()
    config = llm.LLMConfig(
        api_key=api_key,
        model=args.model,
        input_cost_per_million_cny=args.input_cost_per_million_cny,
        output_cost_per_million_cny=args.output_cost_per_million_cny,
    )
    budget = llm.LLMBudget(
        max_total_tokens=args.max_total_tokens,
        max_estimated_cost_cny=args.max_estimated_cost_cny,
    )
    ledger = ModelCallLedgerStore(args.ledger_path)
    summaries: list[dict[str, object]] = []
    try:
        for item in items:
            item_id = str(item["id"])
            try:
                summaries.append(
                    run_canary_item(
                        item,
                        config=config,
                        budget=budget,
                        telemetry_sink=ledger.record,
                        output_dir=args.output_dir,
                    )
                )
            except Exception as exc:
                summaries.append(
                    {
                        "itemId": item_id,
                        "status": "failed",
                        "errorType": type(exc).__name__,
                    }
                )
    finally:
        ledger.dispose()

    succeeded = sum(item.get("status") == "succeeded" for item in summaries)
    summary: dict[str, object] = {
        "manifestVersion": "science125-v1",
        "contractVersion": "research-v1",
        "provider": "DashScope",
        "model": args.model,
        "generatedAt": datetime.now(UTC).isoformat(),
        "succeeded": succeeded,
        "total": len(summaries),
        "allSucceeded": succeeded == len(CANARY_IDS),
        "consumedTokens": budget.consumed_tokens,
        "consumedEstimatedCostCny": budget.consumed_estimated_cost_cny,
        "items": summaries,
    }
    _write_summary(args.summary_path, summary)
    print(f"Qwen canary: {succeeded}/{len(CANARY_IDS)} succeeded")
    return 0 if summary["allSucceeded"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
