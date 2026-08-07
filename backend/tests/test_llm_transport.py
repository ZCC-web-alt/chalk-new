from __future__ import annotations

import dataclasses
import hashlib
import json
import re
import sys
import unittest
from datetime import UTC, datetime
from email.utils import format_datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch

import requests


PROJECT_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_DIR / "src"))

from chalk_app.core import llm_client


API_KEY = "dashscope-test-key-must-never-be-recorded"
PROMPT = "Private research prompt that must only be represented by a hash."
CONTENT = "Structured answer content that must only be represented by a hash."


class FakeResponse:
    def __init__(
        self,
        status_code: int,
        payload: Any,
        *,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status_code = status_code
        self._payload = payload
        self.headers = requests.structures.CaseInsensitiveDict(headers or {})
        self.text = json.dumps(payload) if payload is not None else ""

    def json(self) -> Any:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(
                f"HTTP {self.status_code}",
                response=self,  # type: ignore[arg-type]
            )


class FakeStreamingResponse(FakeResponse):
    def __init__(self, events: list[dict[str, Any]], *, headers: dict[str, str] | None = None) -> None:
        super().__init__(200, {}, headers=headers)
        self._events = events

    def iter_lines(self, decode_unicode: bool = False):
        for event in self._events:
            line = f"data: {json.dumps(event, ensure_ascii=False)}"
            yield line if decode_unicode else line.encode("utf-8")
        yield "data: [DONE]" if decode_unicode else b"data: [DONE]"


def success_response(
    *,
    request_id: str = "body-request-id",
    header_request_id: str | None = None,
) -> FakeResponse:
    headers = {"X-DashScope-Request-Id": header_request_id} if header_request_id else {}
    return FakeResponse(
        200,
        {
            "id": request_id,
            "choices": [{"message": {"content": CONTENT}}],
            "usage": {
                "prompt_tokens": 13,
                "completion_tokens": 8,
                "total_tokens": 21,
            },
        },
        headers=headers,
    )


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


def usage_value(usage: Any, key: str) -> int:
    if isinstance(usage, dict):
        return int(usage[key])
    return int(getattr(usage, key))


class LLMTransportTestCase(unittest.TestCase):
    def config(self):
        return llm_client.LLMConfig(api_key=API_KEY, model="qwen-test")

    def context(self):
        return llm_client.LLMCallContext(
            resource_type="science125_item",
            resource_id="S125-001",
        )

    def call_result(self, **overrides: Any):
        options: dict[str, Any] = {
            "task": "qa",
            "context": self.context(),
            "max_attempts": 4,
        }
        options.update(overrides)
        return llm_client._chat_result(PROMPT, self.config(), **options)

    def test_success_parses_content_header_request_id_usage_hashes_and_safe_telemetry(self) -> None:
        events: list[Any] = []
        response = success_response(
            request_id="body-request-id",
            header_request_id="header-request-id",
        )

        with patch.object(llm_client.requests, "post", return_value=response) as post:
            result = self.call_result(telemetry_sink=events.append)

        self.assertIsInstance(result, llm_client.LLMCallResult)
        self.assertEqual(result.content, CONTENT)
        self.assertEqual(result.request_id, "header-request-id")
        self.assertEqual(result.provider, "DashScope")
        self.assertEqual(result.model, "qwen-test")
        self.assertEqual(usage_value(result.usage, "prompt_tokens"), 13)
        self.assertEqual(usage_value(result.usage, "completion_tokens"), 8)
        self.assertEqual(usage_value(result.usage, "total_tokens"), 21)
        self.assertRegex(result.prompt_hash, re.compile(r"^[0-9a-f]{64}$"))
        self.assertRegex(result.response_hash, re.compile(r"^[0-9a-f]{64}$"))
        self.assertEqual(result.prompt_hash, hashlib.sha256(PROMPT.encode("utf-8")).hexdigest())
        self.assertEqual(result.response_hash, hashlib.sha256(CONTENT.encode("utf-8")).hexdigest())
        self.assertEqual(post.call_count, 1)

        self.assertEqual(len(events), 1)
        event = as_mapping(events[0])
        self.assertEqual(event["resource_type"], "science125_item")
        self.assertEqual(event["resource_id"], "S125-001")
        self.assertEqual(event["request_id"], "header-request-id")
        self.assertEqual(event["attempt"], 1)
        self.assertEqual(event["status_code"], 200)
        self.assertEqual(event["total_tokens"], 21)
        self.assertGreaterEqual(event["latency_ms"], 0)
        for forbidden_field in ("prompt", "response", "api_key", "authorization"):
            self.assertNotIn(forbidden_field, event)
        serialized = json.dumps(event, ensure_ascii=False, default=str)
        self.assertNotIn(PROMPT, serialized)
        self.assertNotIn(CONTENT, serialized)
        self.assertNotIn(API_KEY, serialized)

    def test_429_honors_retry_after_before_succeeding(self) -> None:
        rate_limited = FakeResponse(
            429,
            {"error": {"message": "rate limited"}},
            headers={"Retry-After": "2.5", "X-DashScope-Request-Id": "rate-limit-request"},
        )

        with (
            patch.object(llm_client.requests, "post", side_effect=[rate_limited, success_response()]) as post,
            patch("time.sleep") as sleep,
            patch("random.uniform", return_value=0.0),
        ):
            result = self.call_result()

        self.assertEqual(result.content, CONTENT)
        self.assertEqual(post.call_count, 2)
        self.assertEqual(sleep.call_count, 1)
        self.assertGreaterEqual(float(sleep.call_args.args[0]), 2.5)

    def test_retry_after_http_date_is_honored_and_capped(self) -> None:
        retry_at = format_datetime(datetime.fromtimestamp(1_100, UTC), usegmt=True)
        response = FakeResponse(429, {}, headers={"Retry-After": retry_at})

        with patch.object(llm_client.time, "time", return_value=1_000):
            delay = llm_client._retry_delay(response, 1)

        self.assertEqual(delay, 60.0)

    def test_retryable_server_statuses_retry_then_succeed(self) -> None:
        for status_code in (500, 502, 503, 504):
            with self.subTest(status_code=status_code):
                response = FakeResponse(status_code, {"error": {"message": "temporary"}})
                with (
                    patch.object(llm_client.requests, "post", side_effect=[response, success_response()]) as post,
                    patch("time.sleep"),
                    patch("random.uniform", return_value=0.0),
                ):
                    result = self.call_result()

                self.assertEqual(result.content, CONTENT)
                self.assertEqual(post.call_count, 2)

    def test_timeout_and_connection_error_retry_then_succeed(self) -> None:
        for failure in (
            requests.exceptions.Timeout("read timed out"),
            requests.exceptions.ConnectionError("connection refused"),
        ):
            with self.subTest(failure=type(failure).__name__):
                with (
                    patch.object(
                        llm_client.requests,
                        "post",
                        side_effect=[failure, success_response()],
                    ) as post,
                    patch("time.sleep"),
                    patch("random.uniform", return_value=0.0),
                ):
                    result = self.call_result()

                self.assertEqual(result.content, CONTENT)
                self.assertEqual(post.call_count, 2)

    def test_reasoning_model_uses_streaming_response_to_avoid_waiting_for_complete_json(self) -> None:
        streamed = FakeStreamingResponse(
            [
                {"id": "stream-request", "choices": [{"delta": {"content": "Structured "}}]},
                {"id": "stream-request", "choices": [{"delta": {"content": "answer"}}]},
                {
                    "id": "stream-request",
                    "choices": [{"delta": {}}],
                    "usage": {"prompt_tokens": 13, "completion_tokens": 8, "total_tokens": 21},
                },
            ],
            headers={"X-DashScope-Request-Id": "stream-header-request"},
        )
        config = llm_client.LLMConfig(api_key=API_KEY, model=llm_client.REASONING_MODEL)

        with patch.object(llm_client.requests, "post", return_value=streamed) as post:
            result = llm_client._chat_result(PROMPT, config, task="hypothesis")

        self.assertEqual(result.status, "succeeded")
        self.assertEqual(result.content, "Structured answer")
        self.assertEqual(result.request_id, "stream-header-request")
        self.assertEqual(usage_value(result.usage, "total_tokens"), 21)
        self.assertTrue(post.call_args.kwargs["stream"])
        self.assertTrue(post.call_args.kwargs["json"]["stream"])

    def test_four_read_timeouts_return_safe_terminal_metadata(self) -> None:
        events: list[Any] = []
        failures = [requests.exceptions.ReadTimeout("read timed out") for _ in range(4)]

        with (
            patch.object(llm_client.requests, "post", side_effect=failures) as post,
            patch("time.sleep"),
            patch("random.uniform", return_value=0.0),
        ):
            result = self.call_result(telemetry_sink=events.append)

        self.assertEqual(post.call_count, 4)
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.error_type, "timeout")
        self.assertEqual(result.status_code, None)
        self.assertEqual(result.request_id, None)
        self.assertEqual(result.attempts, 4)
        self.assertEqual(len(events), 4)
        self.assertEqual(as_mapping(events[-1])["status"], "failed")
        self.assertNotIn(PROMPT, json.dumps([as_mapping(event) for event in events], ensure_ascii=False))

    def test_retryable_failure_never_exceeds_four_total_attempts(self) -> None:
        always_unavailable = [FakeResponse(503, {"error": {"message": "temporary"}}) for _ in range(5)]

        with (
            patch.object(llm_client.requests, "post", side_effect=always_unavailable) as post,
            patch("time.sleep"),
            patch("random.uniform", return_value=0.0),
        ):
            try:
                self.call_result(max_attempts=12)
            except Exception:
                pass

        self.assertEqual(post.call_count, 4)

    def test_ordinary_400_does_not_retry(self) -> None:
        bad_request = FakeResponse(400, {"error": {"message": "invalid request"}})

        with patch.object(llm_client.requests, "post", return_value=bad_request) as post:
            try:
                self.call_result()
            except Exception:
                pass

        self.assertEqual(post.call_count, 1)

    def test_malformed_success_payload_does_not_retry(self) -> None:
        malformed = FakeResponse(200, {"choices": [], "usage": {}})

        with patch.object(llm_client.requests, "post", return_value=malformed) as post:
            try:
                self.call_result()
            except Exception:
                pass

        self.assertEqual(post.call_count, 1)

    def test_pre_exhausted_budget_does_not_send_a_request(self) -> None:
        budget = llm_client.LLMBudget(max_total_tokens=100, consumed_tokens=100)

        with patch.object(llm_client.requests, "post") as post:
            try:
                self.call_result(budget=budget)
            except Exception:
                pass

        post.assert_not_called()

    def test_pre_exhausted_cost_budget_does_not_send_a_request(self) -> None:
        budget = llm_client.LLMBudget(
            max_estimated_cost_cny=1.0,
            consumed_estimated_cost_cny=1.0,
        )

        with patch.object(llm_client.requests, "post") as post:
            result = self.call_result(budget=budget)

        self.assertEqual(result.status, "budget_exceeded")
        post.assert_not_called()

    def test_remaining_token_budget_caps_provider_completion_tokens(self) -> None:
        system_prompt = "You are a careful scientific research assistant."
        budget = llm_client.LLMBudget(max_total_tokens=1_000, consumed_tokens=21)

        with patch.object(llm_client.requests, "post", return_value=success_response()) as post:
            result = self.call_result(budget=budget)

        self.assertEqual(result.status, "succeeded")
        expected = 1_000 - 21 - llm_client._prompt_token_reserve(PROMPT, system_prompt)
        self.assertEqual(post.call_args.kwargs["json"]["max_tokens"], expected)

    def test_budget_that_cannot_cover_prompt_does_not_send_a_request(self) -> None:
        system_prompt = "You are a careful scientific research assistant."
        reserve = llm_client._prompt_token_reserve(PROMPT, system_prompt)
        budget = llm_client.LLMBudget(max_total_tokens=reserve)

        with patch.object(llm_client.requests, "post") as post:
            result = self.call_result(budget=budget)

        self.assertEqual(result.status, "budget_exceeded")
        post.assert_not_called()

    def test_mixed_language_science_prompt_keeps_completion_budget(self) -> None:
        # Regression: the old one-byte-per-token reserve rejected this valid
        # 30 KB prompt before making a DashScope request.
        prompt = "界面现象与可重复测量。" * 1_000
        budget = llm_client.LLMBudget(max_total_tokens=20_000)

        with patch.object(llm_client.requests, "post", return_value=success_response()) as post:
            result = llm_client._chat_result(prompt, self.config(), budget=budget)

        self.assertEqual(result.status, "succeeded")
        self.assertGreater(post.call_args.kwargs["json"]["max_tokens"], 0)

    def test_cost_budget_reserves_prompt_cost_and_caps_completion(self) -> None:
        config = llm_client.LLMConfig(
            api_key=API_KEY,
            model="qwen-test",
            input_cost_per_million_cny=10.0,
            output_cost_per_million_cny=100.0,
        )
        budget = llm_client.LLMBudget(max_estimated_cost_cny=0.05)

        with patch.object(llm_client.requests, "post", return_value=success_response()) as post:
            result = llm_client._chat_result(PROMPT, config, budget=budget)

        reserve = llm_client._prompt_token_reserve(
            PROMPT,
            "You are a careful scientific research assistant.",
        )
        expected = int((0.05 - reserve * 10.0 / 1_000_000) * 1_000_000 / 100.0)
        self.assertEqual(result.status, "succeeded")
        self.assertEqual(post.call_args.kwargs["json"]["max_tokens"], expected)

    def test_large_token_budget_uses_safe_per_call_completion_cap(self) -> None:
        budget = llm_client.LLMBudget(max_total_tokens=60_000)

        with patch.object(llm_client.requests, "post", return_value=success_response()) as post:
            self.call_result(budget=budget)

        self.assertEqual(
            post.call_args.kwargs["json"]["max_tokens"],
            llm_client.MAX_BUDGETED_COMPLETION_TOKENS,
        )

    def test_chat_compatibility_wrapper_returns_content_string(self) -> None:
        with patch.object(llm_client.requests, "post", return_value=success_response()):
            content = llm_client._chat(PROMPT, self.config(), task="qa", retries=0)

        self.assertIsInstance(content, str)
        self.assertEqual(content, CONTENT)

    def test_required_telemetry_propagates_sink_failures(self) -> None:
        def failing_sink(_: Any) -> None:
            raise RuntimeError("ledger unavailable")

        with (
            patch.object(llm_client.requests, "post", return_value=success_response()),
            self.assertRaisesRegex(RuntimeError, "ledger unavailable"),
        ):
            self.call_result(
                telemetry_sink=failing_sink,
                telemetry_required=True,
            )


if __name__ == "__main__":
    unittest.main()
