from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))


class WebJobStoreTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "web.db"
        self.stores = []

    def tearDown(self) -> None:
        for store in self.stores:
            store.dispose()
        self.tmp.cleanup()

    def make_store(self):
        from app.services.job_store import WebJobStore

        store = WebJobStore(self.db_path)
        self.stores.append(store)
        return store

    def test_jobs_persist_across_store_instances(self) -> None:
        first = self.make_store()
        created = first.create(user_id=7, job_type="rag_qa", payload={"question": "test"})

        second = self.make_store()
        loaded = second.get_for_user(user_id=7, job_id=created.id)

        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.type, "rag_qa")
        self.assertEqual(loaded.payload, {"question": "test"})
        self.assertIsNone(second.get_for_user(user_id=8, job_id=created.id))

    def test_job_stage_is_persisted_for_refresh_recovery(self) -> None:
        store = self.make_store()
        created = store.create(user_id=7, job_type="hypothesis_generate", payload={"researchQuestion": "test"})
        store.update(created.id, status="RUNNING", stage="reasoning_chain")

        loaded = store.get(created.id)

        self.assertEqual(loaded.stage, "reasoning_chain")
        self.assertEqual(loaded.to_dict()["stage"], "reasoning_chain")

    def test_startup_marks_interrupted_jobs_failed_without_exposing_tracebacks(self) -> None:
        store = self.make_store()
        queued = store.create(user_id=1, job_type="literature_search", payload={"queryText": "catalysis"})
        store.update(queued.id, status="RUNNING", message="Searching")

        affected = store.fail_interrupted_jobs()
        loaded = store.get_for_user(user_id=1, job_id=queued.id)

        self.assertEqual(affected, 1)
        self.assertEqual(loaded.status, "FAILED")
        self.assertEqual(loaded.error["code"], "SERVER_RESTARTED")
        self.assertNotIn("trace", loaded.error)

    def test_startup_marks_waiting_hitl_jobs_failed(self) -> None:
        store = self.make_store()
        job = store.create(user_id=1, job_type="hypothesis_generate", payload={"researchQuestion": "q"})
        store.update(
            job.id,
            status="WAITING_FOR_FEEDBACK",
            feedback_prompt={"kind": "hypothesis_review", "round": 0},
        )

        affected = store.fail_interrupted_jobs()
        loaded = store.get_for_user(user_id=1, job_id=job.id)

        self.assertEqual(affected, 1)
        self.assertEqual(loaded.status, "FAILED")
        self.assertEqual(loaded.error["code"], "SERVER_RESTARTED")

    def test_terminal_job_state_cannot_be_overwritten_by_stale_worker_transition(self) -> None:
        store = self.make_store()
        queued = store.create(user_id=1, job_type="rag_qa", payload={"question": "q"})
        running = store.transition(queued.id, from_statuses=("QUEUED",), status="RUNNING")
        self.assertEqual(running.status, "RUNNING")

        cancelled = store.transition(
            queued.id,
            from_statuses=("QUEUED", "RUNNING", "WAITING_FOR_FEEDBACK"),
            require_not_cancelled=False,
            status="CANCELLED",
            cancel_requested=True,
        )
        self.assertEqual(cancelled.status, "CANCELLED")

        stale_completion = store.transition(
            queued.id,
            from_statuses=("RUNNING",),
            status="SUCCEEDED",
            progress=100,
        )
        self.assertEqual(stale_completion.status, "CANCELLED")


if __name__ == "__main__":
    unittest.main()
