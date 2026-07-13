from __future__ import annotations

import sys
import threading
import unittest
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))


class HitlFeedbackBrokerTestCase(unittest.TestCase):
    def test_feedback_submission_wakes_the_waiting_job(self) -> None:
        from app.services.hitl import HitlFeedbackBroker

        broker = HitlFeedbackBroker()
        broker.open("job-1")
        received: list[dict] = []

        thread = threading.Thread(target=lambda: received.append(broker.wait("job-1", timeout=1)))
        thread.start()
        self.assertTrue(broker.submit("job-1", {"action": "approve"}))
        thread.join(timeout=2)

        self.assertFalse(thread.is_alive())
        self.assertEqual(received, [{"action": "approve"}])
        self.assertFalse(broker.has_waiter("job-1"))

    def test_cancellation_wakes_the_waiting_job(self) -> None:
        from app.services.hitl import HitlFeedbackBroker

        broker = HitlFeedbackBroker()
        broker.open("job-2")

        self.assertTrue(broker.cancel("job-2"))
        self.assertEqual(broker.wait("job-2", timeout=1), {"action": "cancel"})
        self.assertFalse(broker.submit("job-2", {"action": "skip"}))

    def test_duplicate_waiter_is_rejected(self) -> None:
        from app.services.hitl import HitlFeedbackBroker

        broker = HitlFeedbackBroker()
        broker.open("job-3")

        with self.assertRaises(ValueError):
            broker.open("job-3")

    def test_cancel_all_wakes_every_waiter(self) -> None:
        from app.services.hitl import HitlFeedbackBroker

        broker = HitlFeedbackBroker()
        broker.open("job-1")
        broker.open("job-2")

        affected = broker.cancel_all()

        self.assertEqual(affected, 2)
        self.assertEqual(broker.wait("job-1"), {"action": "cancel"})
        self.assertEqual(broker.wait("job-2"), {"action": "cancel"})


if __name__ == "__main__":
    unittest.main()
