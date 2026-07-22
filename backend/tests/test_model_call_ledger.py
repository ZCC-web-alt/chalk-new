from __future__ import annotations

import dataclasses
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any


PROJECT_DIR = Path(__file__).resolve().parents[2]
BACKEND_DIR = PROJECT_DIR / "backend"
sys.path.insert(0, str(BACKEND_DIR))


FORBIDDEN_VALUES = {
    "private prompt text",
    "private response text",
    "dashscope-secret-api-key",
}


def as_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if dataclasses.is_dataclass(value):
        return dataclasses.asdict(value)
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if hasattr(value, "__dict__"):
        return vars(value)
    return {
        name: getattr(value, name)
        for name in dir(value)
        if not name.startswith("_") and not callable(getattr(value, name))
    }


def attempt_record(
    attempt: int,
    *,
    status_code: int,
    status: str,
    request_id: str,
    total_tokens: int,
    latency_ms: int,
) -> dict[str, Any]:
    return {
        "provider": "DashScope",
        "model": "qwen3.7-plus",
        "request_id": request_id,
        "resource_type": "science125_item",
        "resource_id": "S125-001",
        "status_code": status_code,
        "attempt": attempt,
        "prompt_tokens": 13 if total_tokens else 0,
        "completion_tokens": 8 if total_tokens else 0,
        "total_tokens": total_tokens,
        "latency_ms": latency_ms,
        "retry_reason": "http_429" if status == "retrying" else None,
        "estimated_cost": 0.00021 if total_tokens else 0.0,
        "prompt_hash": "a" * 64,
        "response_hash": "b" * 64 if total_tokens else None,
        "status": status,
    }


class ModelCallLedgerStoreTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "web.db"
        self.stores = []

    def tearDown(self) -> None:
        for store in self.stores:
            store.dispose()
        self.tmp.cleanup()

    def make_store(self):
        from app.services.model_call_ledger import ModelCallLedgerStore

        store = ModelCallLedgerStore(self.db_path)
        self.stores.append(store)
        return store

    def test_persists_every_attempt_with_context_tokens_latency_and_status(self) -> None:
        store = self.make_store()
        store.record(
            attempt_record(
                1,
                status_code=429,
                status="retrying",
                request_id="request-1",
                total_tokens=0,
                latency_ms=17,
            )
        )
        store.record(
            attempt_record(
                2,
                status_code=200,
                status="succeeded",
                request_id="request-2",
                total_tokens=21,
                latency_ms=143,
            )
        )

        rows = store.list_for_context("science125_item", "S125-001")

        self.assertEqual([row.attempt for row in rows], [1, 2])
        self.assertEqual([row.request_id for row in rows], ["request-1", "request-2"])
        self.assertEqual([row.status_code for row in rows], [429, 200])
        self.assertEqual([row.status for row in rows], ["retrying", "succeeded"])
        self.assertEqual([row.total_tokens for row in rows], [0, 21])
        self.assertEqual([row.latency_ms for row in rows], [17, 143])
        self.assertTrue(all(row.resource_type == "science125_item" for row in rows))
        self.assertTrue(all(row.resource_id == "S125-001" for row in rows))

        store.dispose()
        self.stores.remove(store)
        reopened = self.make_store()
        persisted = reopened.list_for_context("science125_item", "S125-001")
        self.assertEqual([row.attempt for row in persisted], [1, 2])

    def test_schema_and_returned_objects_never_store_raw_prompt_response_or_api_key(self) -> None:
        store = self.make_store()
        store.record(
            {
                **attempt_record(
                    1,
                    status_code=200,
                    status="succeeded",
                    request_id="request-safe",
                    total_tokens=21,
                    latency_ms=71,
                ),
                "prompt": "private prompt text",
                "response": "private response text",
                "api_key": "dashscope-secret-api-key",
            }
        )

        rows = store.list_for_context("science125_item", "S125-001")
        self.assertEqual(len(rows), 1)
        returned = as_mapping(rows[0])
        for forbidden_field in ("prompt", "response", "api_key", "authorization"):
            self.assertNotIn(forbidden_field, returned)
        self.assertTrue(FORBIDDEN_VALUES.isdisjoint({str(value) for value in returned.values()}))

        store.dispose()
        self.stores.remove(store)
        connection = sqlite3.connect(self.db_path)
        try:
            columns = {
                row[1]
                for row in connection.execute("PRAGMA table_info(model_call_ledger)").fetchall()
            }
            raw_rows = connection.execute("SELECT * FROM model_call_ledger").fetchall()
        finally:
            connection.close()

        for forbidden_column in ("prompt", "response", "api_key", "authorization"):
            self.assertNotIn(forbidden_column, columns)
        flattened_values = {str(value) for row in raw_rows for value in row}
        self.assertTrue(FORBIDDEN_VALUES.isdisjoint(flattened_values))

    def test_science125_policy_and_evidence_snapshot_hashes_are_auditable(self) -> None:
        store = self.make_store()
        store.record({
            **attempt_record(
                1,
                status_code=200,
                status="succeeded",
                request_id="request-auditable",
                total_tokens=21,
                latency_ms=71,
            ),
            "policy_hash": "c" * 64,
            "evidence_snapshot_hash": "d" * 64,
        })

        row = store.list_for_context("science125_item", "S125-001")[0]
        self.assertEqual(row.policy_hash, "c" * 64)
        self.assertEqual(row.evidence_snapshot_hash, "d" * 64)


if __name__ == "__main__":
    unittest.main()
