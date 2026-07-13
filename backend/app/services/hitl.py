from __future__ import annotations

from dataclasses import dataclass, field
from threading import Event, RLock
from typing import Any


@dataclass(slots=True)
class _FeedbackSlot:
    event: Event = field(default_factory=Event)
    payload: dict[str, Any] | None = None


class HitlFeedbackBroker:
    def __init__(self) -> None:
        self._lock = RLock()
        self._slots: dict[str, _FeedbackSlot] = {}

    def open(self, job_id: str) -> None:
        with self._lock:
            if job_id in self._slots:
                raise ValueError(f"A feedback waiter already exists for job {job_id}.")
            self._slots[job_id] = _FeedbackSlot()

    def has_waiter(self, job_id: str) -> bool:
        with self._lock:
            return job_id in self._slots

    def submit(self, job_id: str, payload: dict[str, Any]) -> bool:
        with self._lock:
            slot = self._slots.get(job_id)
            if slot is None or slot.event.is_set():
                return False
            slot.payload = dict(payload)
            slot.event.set()
            return True

    def cancel(self, job_id: str) -> bool:
        return self.submit(job_id, {"action": "cancel"})

    def wait(self, job_id: str, timeout: float | None = None) -> dict[str, Any]:
        with self._lock:
            slot = self._slots.get(job_id)
        if slot is None:
            raise KeyError(f"No feedback waiter exists for job {job_id}.")
        try:
            if not slot.event.wait(timeout):
                raise TimeoutError(f"Timed out waiting for feedback for job {job_id}.")
            return dict(slot.payload or {"action": "cancel"})
        finally:
            with self._lock:
                self._slots.pop(job_id, None)

    def close(self, job_id: str) -> None:
        with self._lock:
            self._slots.pop(job_id, None)

    def cancel_all(self) -> int:
        with self._lock:
            slots = list(self._slots.values())
            for slot in slots:
                if not slot.event.is_set():
                    slot.payload = {"action": "cancel"}
                    slot.event.set()
            return len(slots)


hitl_feedback_broker = HitlFeedbackBroker()
