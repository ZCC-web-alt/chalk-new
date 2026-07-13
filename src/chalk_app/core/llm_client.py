"""
统一的大模型调用封装 — Qwen (DashScope) 专版。

仅通过阿里云 DashScope API 调用通义千问系列模型。
不同功能自动匹配合适的 Qwen 模型，以实现效率最大化。

API Key 通过 LLMConfig 传入（用户在界面设置），或环境变量 DASHSCOPE_API_KEY。
"""

import hashlib
import os
import random
import time
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from typing import Any, Callable, Dict, List, Mapping, Optional

import requests


# ─────────────────────────────────────────────────────────────
# 模型路由表：每个功能对应最适合的 Qwen 模型
# ─────────────────────────────────────────────────────────────

MODEL_MAP = {
    # 均衡型 (3.7-plus) — 轻量/日常通用任务
    "summarize":          "qwen3.7-plus",    # 文献摘要
    "extract_names":      "qwen3.7-plus",    # 化学品名称提取
    "find_image_pages":   "qwen3.7-plus",    # 含图页检测

    # 日常通用 + RAG
    "qa":                 "qwen3.7-plus",    # 智能问答 (RAG)
    "lab_suggest":        "qwen3.7-plus",    # 实验记录 AI 建议

    # 推理型 (3.7-max) — 复杂结构化输出 + 多步推理 + Agent
    "sop":                "qwen3.7-max",     # SOP 结构化提取
    "reactions":          "qwen3.7-max",     # 反应信息提取
    "compare":            "qwen3.7-max",     # 多文献对比分析
    "vasp_extract":       "qwen3.7-max",     # VASP 计算参数提取
    "ms_guide":           "qwen3.7-max",     # Materials Studio 建模指南
    "hypothesis":         "qwen3.7-max",     # 科学假设生成
    "critique":           "qwen3.7-max",     # 假设思辨评审
    "validation":         "qwen3.7-max",     # 可验证性评估
    "excel_interpret":    "qwen3.7-max",     # Excel 数据解读（纯文本推理）

    # 多模态 (VL) — 图片理解 + 表格 OCR
    "image_understand":   "qwen-vl-max",     # 学术图表理解
    "table_ocr":          "qwen-vl-ocr",     # 表格 OCR（专用模型）
    "chart_analysis":     "qwen-vl-max",     # 数据图表分析

    # 专用型 — 翻译
    "translate":          "qwen3.7-max",     # 文献翻译 + 术语提取
}

# 默认模型（兜底）
DEFAULT_MODEL = "qwen3.7-plus"

# DashScope API 兼容 OpenAI 格式的 base URL
DASHSCOPE_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
LLM_ERROR_PREFIX = "__CHALK_LLM_ERROR__:"
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
MAX_TOTAL_ATTEMPTS = 4
MAX_BUDGETED_COMPLETION_TOKENS = 8_192


@dataclass(slots=True)
class LLMCallContext:
    resource_type: str = "unscoped"
    resource_id: str = ""


@dataclass(slots=True)
class LLMUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


@dataclass(slots=True)
class LLMBudget:
    max_total_tokens: int | None = None
    consumed_tokens: int = 0
    max_estimated_cost_cny: float | None = None
    consumed_estimated_cost_cny: float = 0.0

    def is_exhausted(self) -> bool:
        tokens_exhausted = (
            self.max_total_tokens is not None
            and self.consumed_tokens >= self.max_total_tokens
        )
        cost_exhausted = (
            self.max_estimated_cost_cny is not None
            and self.consumed_estimated_cost_cny >= self.max_estimated_cost_cny
        )
        return tokens_exhausted or cost_exhausted

    def consume(self, usage: LLMUsage, estimated_cost_cny: float) -> None:
        self.consumed_tokens += max(0, usage.total_tokens)
        self.consumed_estimated_cost_cny += max(0.0, estimated_cost_cny)


@dataclass(slots=True)
class LLMCallResult:
    content: str
    provider: str
    model: str
    request_id: str | None
    status_code: int | None
    attempts: int
    prompt_hash: str
    response_hash: str | None
    usage: LLMUsage
    latency_ms: int
    estimated_cost_cny: float
    status: str
    error_type: str | None = None


TelemetrySink = Callable[[Mapping[str, Any]], None]


class LLMConfig:
    """大模型配置，仅保留 Qwen (DashScope) 相关字段。"""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        base_url: Optional[str] = None,
        input_cost_per_million_cny: float = 0.0,
        output_cost_per_million_cny: float = 0.0,
    ):
        self.api_key = api_key
        self.model = model       # 如果指定则覆盖自动匹配
        self.base_url = base_url or DASHSCOPE_BASE_URL
        self.input_cost_per_million_cny = max(0.0, float(input_cost_per_million_cny))
        self.output_cost_per_million_cny = max(0.0, float(output_cost_per_million_cny))


def _ensure_config(config: Optional[LLMConfig]) -> LLMConfig:
    if config is None:
        return LLMConfig()
    return config


def _get_api_key(config: LLMConfig) -> str:
    """获取 API Key，优先使用用户在界面中输入的，其次使用环境变量。"""
    key = config.api_key or os.getenv("DASHSCOPE_API_KEY") or os.getenv("QWEN_API_KEY")
    if not key:
        raise ValueError(
            "未配置 API Key。请在「API 设置」页面输入您的通义千问 API Key，"
            "或设置环境变量 DASHSCOPE_API_KEY。"
        )
    return key


def _get_model(config: LLMConfig, task: str) -> str:
    """根据任务类型返回对应的 Qwen 模型名。如果用户指定了模型则优先使用。"""
    if config.model:
        return config.model
    return MODEL_MAP.get(task, DEFAULT_MODEL)


def _format_llm_error(error_type: str, model: str, detail: object) -> str:
    """Return a machine-readable error marker plus a human-readable message."""
    messages = {
        "timeout": (
            "通义千问接口请求超时。模型可能仍在排队或生成，请稍后重试；"
            "也可以减少输入文献/图表数量，或把最大迭代轮数调低后再生成。"
        ),
        "network": "通义千问接口网络连接失败，请检查网络、代理或稍后重试。",
        "api": "通义千问接口返回错误，请检查 API Key、模型名、额度或服务状态。",
        "budget": "本次运行已达到模型调用预算上限，未继续发送请求。",
        "unknown": "通义千问接口调用失败。",
    }
    payload = {
        "error_type": error_type,
        "model": model,
        "message": messages.get(error_type, messages["unknown"]),
        "detail": str(detail),
    }
    return LLM_ERROR_PREFIX + json_dumps(payload)


def json_dumps(value: object) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


# ─────────────────────────────────────────────────────────────
# 底层调用
# ─────────────────────────────────────────────────────────────

def _safe_nonnegative_int(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _parse_usage(data: Mapping[str, Any]) -> LLMUsage:
    raw = data.get("usage")
    usage = raw if isinstance(raw, Mapping) else {}
    prompt_tokens = _safe_nonnegative_int(usage.get("prompt_tokens", usage.get("input_tokens", 0)))
    completion_tokens = _safe_nonnegative_int(
        usage.get("completion_tokens", usage.get("output_tokens", 0))
    )
    total_tokens = _safe_nonnegative_int(usage.get("total_tokens", 0))
    if total_tokens == 0:
        total_tokens = prompt_tokens + completion_tokens
    return LLMUsage(prompt_tokens, completion_tokens, total_tokens)


def _response_request_id(response: Any, data: Mapping[str, Any] | None = None) -> str | None:
    headers = getattr(response, "headers", {}) or {}
    for name in ("X-DashScope-Request-Id", "X-Request-Id", "Request-Id"):
        value = headers.get(name) if hasattr(headers, "get") else None
        if value:
            return str(value)
    if data:
        value = data.get("id") or data.get("request_id") or data.get("requestId")
        if value:
            return str(value)
    return None


def _retry_delay(response: Any, attempt: int) -> float:
    headers = getattr(response, "headers", {}) or {}
    retry_after = headers.get("Retry-After") if hasattr(headers, "get") else None
    try:
        if retry_after is not None:
            return min(60.0, max(0.0, float(retry_after)))
    except (TypeError, ValueError):
        try:
            retry_at = parsedate_to_datetime(str(retry_after)).timestamp()
            return min(60.0, max(0.0, retry_at - time.time()))
        except (TypeError, ValueError, OverflowError):
            pass
    ceiling = min(60.0, float(2 ** max(0, attempt - 1)))
    return random.uniform(0.0, ceiling)


def _estimate_cost(config: LLMConfig, usage: LLMUsage) -> float:
    return (
        usage.prompt_tokens * config.input_cost_per_million_cny
        + usage.completion_tokens * config.output_cost_per_million_cny
    ) / 1_000_000


def _emit_telemetry(
    sink: TelemetrySink | None,
    event: Mapping[str, Any],
    *,
    required: bool = False,
) -> None:
    if sink is None:
        if required:
            raise RuntimeError("A required model-call telemetry sink was not configured.")
        return
    try:
        sink(dict(event))
    except Exception:
        if required:
            raise
        # Compatibility calls remain available when optional telemetry is unavailable.
        return


def _attempt_event(
    *,
    context: LLMCallContext,
    model: str,
    request_id: str | None,
    status_code: int | None,
    attempt: int,
    usage: LLMUsage,
    latency_ms: int,
    retry_reason: str | None,
    estimated_cost_cny: float,
    prompt_hash: str,
    response_hash: str | None,
    status: str,
) -> dict[str, Any]:
    return {
        "provider": "DashScope",
        "model": model,
        "request_id": request_id,
        "resource_type": context.resource_type,
        "resource_id": context.resource_id,
        "status_code": status_code,
        "attempt": attempt,
        "prompt_tokens": usage.prompt_tokens,
        "completion_tokens": usage.completion_tokens,
        "total_tokens": usage.total_tokens,
        "latency_ms": latency_ms,
        "retry_reason": retry_reason,
        "estimated_cost": estimated_cost_cny,
        "prompt_hash": prompt_hash,
        "response_hash": response_hash,
        "status": status,
    }


def _failed_result(
    *,
    model: str,
    prompt_hash: str,
    attempts: int,
    latency_ms: int,
    error_type: str,
    detail: object,
    request_id: str | None = None,
    status_code: int | None = None,
) -> LLMCallResult:
    return LLMCallResult(
        content=_format_llm_error(error_type, model, detail),
        provider="DashScope",
        model=model,
        request_id=request_id,
        status_code=status_code,
        attempts=attempts,
        prompt_hash=prompt_hash,
        response_hash=None,
        usage=LLMUsage(),
        latency_ms=latency_ms,
        estimated_cost_cny=0.0,
        status="budget_exceeded" if error_type == "budget" else "failed",
        error_type=error_type,
    )


def _prompt_token_reserve(prompt: str, system_prompt: str) -> int:
    # A UTF-8 byte is a conservative upper bound for one model token. The
    # framing reserve covers message roles and provider-side chat wrappers.
    return len(prompt.encode("utf-8")) + len(system_prompt.encode("utf-8")) + 128


def _chat_result(
    prompt: str,
    config: LLMConfig,
    task: str = "qa",
    system_prompt: Optional[str] = None,
    timeout: int = 60,
    *,
    context: LLMCallContext | None = None,
    budget: LLMBudget | None = None,
    telemetry_sink: TelemetrySink | None = None,
    telemetry_required: bool = False,
    max_attempts: int = MAX_TOTAL_ATTEMPTS,
) -> LLMCallResult:
    model = _get_model(config, task)
    prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    call_context = context or LLMCallContext()
    attempts_allowed = min(MAX_TOTAL_ATTEMPTS, max(1, int(max_attempts)))
    sys_msg = system_prompt or "You are a careful scientific research assistant."
    completion_cap: int | None = None
    budget_error: str | None = None
    if budget is not None:
        if budget.is_exhausted():
            budget_error = "The configured model-call budget is exhausted."
        else:
            prompt_reserve = _prompt_token_reserve(prompt, sys_msg)
            if budget.max_total_tokens is not None:
                remaining_tokens = budget.max_total_tokens - budget.consumed_tokens
                completion_cap = min(
                    MAX_BUDGETED_COMPLETION_TOKENS,
                    remaining_tokens - prompt_reserve,
                )
            if budget.max_estimated_cost_cny is not None:
                if (
                    config.input_cost_per_million_cny <= 0
                    or config.output_cost_per_million_cny <= 0
                ):
                    budget_error = "A cost budget requires positive model token prices."
                else:
                    remaining_cost = (
                        budget.max_estimated_cost_cny
                        - budget.consumed_estimated_cost_cny
                        - prompt_reserve * config.input_cost_per_million_cny / 1_000_000
                    )
                    cost_completion_cap = int(
                        remaining_cost
                        * 1_000_000
                        / config.output_cost_per_million_cny
                    )
                    completion_cap = min(
                        MAX_BUDGETED_COMPLETION_TOKENS
                        if completion_cap is None
                        else completion_cap,
                        cost_completion_cap,
                    )
            if completion_cap is not None and completion_cap < 1:
                budget_error = "The remaining budget cannot cover this prompt and one output token."

    if budget_error is not None:
        result = _failed_result(
            model=model,
            prompt_hash=prompt_hash,
            attempts=0,
            latency_ms=0,
            error_type="budget",
            detail=budget_error,
        )
        _emit_telemetry(
            telemetry_sink,
            _attempt_event(
                context=call_context,
                model=model,
                request_id=None,
                status_code=None,
                attempt=0,
                usage=LLMUsage(),
                latency_ms=0,
                retry_reason=None,
                estimated_cost_cny=0.0,
                prompt_hash=prompt_hash,
                response_hash=None,
                status="budget_exceeded",
            ),
            required=telemetry_required,
        )
        return result

    api_key = _get_api_key(config)
    base_url = config.base_url or DASHSCOPE_BASE_URL
    url = f"{base_url.rstrip('/')}/chat/completions"
    request_timeout = (15, timeout)
    total_latency_ms = 0
    request_payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": sys_msg},
            {"role": "user", "content": prompt},
        ],
    }
    if completion_cap is not None:
        request_payload["max_tokens"] = completion_cap

    for attempt in range(1, attempts_allowed + 1):
        started = time.perf_counter()
        response = None
        latency_ms = 0
        latency_recorded = False
        try:
            response = requests.post(
                url,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=request_payload,
                timeout=request_timeout,
            )
            latency_ms = max(0, int((time.perf_counter() - started) * 1000))
            total_latency_ms += latency_ms
            latency_recorded = True
            status_code = _safe_nonnegative_int(getattr(response, "status_code", 0)) or None
            request_id = _response_request_id(response)

            if status_code in RETRYABLE_STATUS_CODES and attempt < attempts_allowed:
                reason = f"http_{status_code}"
                _emit_telemetry(
                    telemetry_sink,
                    _attempt_event(
                        context=call_context,
                        model=model,
                        request_id=request_id,
                        status_code=status_code,
                        attempt=attempt,
                        usage=LLMUsage(),
                        latency_ms=latency_ms,
                        retry_reason=reason,
                        estimated_cost_cny=0.0,
                        prompt_hash=prompt_hash,
                        response_hash=None,
                        status="retrying",
                    ),
                    required=telemetry_required,
                )
                time.sleep(_retry_delay(response, attempt))
                continue

            if status_code is not None and status_code >= 400:
                _emit_telemetry(
                    telemetry_sink,
                    _attempt_event(
                        context=call_context,
                        model=model,
                        request_id=request_id,
                        status_code=status_code,
                        attempt=attempt,
                        usage=LLMUsage(),
                        latency_ms=latency_ms,
                        retry_reason=None,
                        estimated_cost_cny=0.0,
                        prompt_hash=prompt_hash,
                        response_hash=None,
                        status="failed",
                    ),
                    required=telemetry_required,
                )
                return _failed_result(
                    model=model,
                    prompt_hash=prompt_hash,
                    attempts=attempt,
                    latency_ms=total_latency_ms,
                    error_type="api",
                    detail=f"HTTP {status_code}",
                    request_id=request_id,
                    status_code=status_code,
                )

            raw_data = response.json()
            if not isinstance(raw_data, Mapping):
                raise TypeError("DashScope returned a non-object JSON response.")
            choices = raw_data.get("choices")
            if not isinstance(choices, list) or not choices:
                raise ValueError("DashScope response did not contain a choice.")
            first_choice = choices[0]
            message = first_choice.get("message") if isinstance(first_choice, Mapping) else None
            content = message.get("content") if isinstance(message, Mapping) else None
            if not isinstance(content, str):
                raise ValueError("DashScope response did not contain text content.")

            usage = _parse_usage(raw_data)
            request_id = _response_request_id(response, raw_data)
            response_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
            estimated_cost_cny = _estimate_cost(config, usage)
            if budget is not None:
                budget.consume(usage, estimated_cost_cny)
            _emit_telemetry(
                telemetry_sink,
                _attempt_event(
                    context=call_context,
                    model=model,
                    request_id=request_id,
                    status_code=status_code,
                    attempt=attempt,
                    usage=usage,
                    latency_ms=latency_ms,
                    retry_reason=None,
                    estimated_cost_cny=estimated_cost_cny,
                    prompt_hash=prompt_hash,
                    response_hash=response_hash,
                    status="succeeded",
                ),
                required=telemetry_required,
            )
            return LLMCallResult(
                content=content,
                provider="DashScope",
                model=model,
                request_id=request_id,
                status_code=status_code,
                attempts=attempt,
                prompt_hash=prompt_hash,
                response_hash=response_hash,
                usage=usage,
                latency_ms=total_latency_ms,
                estimated_cost_cny=estimated_cost_cny,
                status="succeeded",
            )
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as exc:
            if not latency_recorded:
                latency_ms = max(0, int((time.perf_counter() - started) * 1000))
                total_latency_ms += latency_ms
            reason = "timeout" if isinstance(exc, requests.exceptions.Timeout) else "connection_error"
            should_retry = attempt < attempts_allowed
            _emit_telemetry(
                telemetry_sink,
                _attempt_event(
                    context=call_context,
                    model=model,
                    request_id=None,
                    status_code=None,
                    attempt=attempt,
                    usage=LLMUsage(),
                    latency_ms=latency_ms,
                    retry_reason=reason if should_retry else None,
                    estimated_cost_cny=0.0,
                    prompt_hash=prompt_hash,
                    response_hash=None,
                    status="retrying" if should_retry else "failed",
                ),
                required=telemetry_required,
            )
            if should_retry:
                time.sleep(_retry_delay(response, attempt))
                continue
            return _failed_result(
                model=model,
                prompt_hash=prompt_hash,
                attempts=attempt,
                latency_ms=total_latency_ms,
                error_type="timeout" if reason == "timeout" else "network",
                detail=exc,
            )
        except requests.exceptions.RequestException as exc:
            if not latency_recorded:
                latency_ms = max(0, int((time.perf_counter() - started) * 1000))
                total_latency_ms += latency_ms
            _emit_telemetry(
                telemetry_sink,
                _attempt_event(
                    context=call_context,
                    model=model,
                    request_id=None,
                    status_code=None,
                    attempt=attempt,
                    usage=LLMUsage(),
                    latency_ms=latency_ms,
                    retry_reason=None,
                    estimated_cost_cny=0.0,
                    prompt_hash=prompt_hash,
                    response_hash=None,
                    status="failed",
                ),
                required=telemetry_required,
            )
            return _failed_result(
                model=model,
                prompt_hash=prompt_hash,
                attempts=attempt,
                latency_ms=total_latency_ms,
                error_type="network",
                detail=exc,
            )
        except Exception as exc:
            if not latency_recorded:
                latency_ms = max(0, int((time.perf_counter() - started) * 1000))
                total_latency_ms += latency_ms
            response_request_id = _response_request_id(response) if response is not None else None
            response_status = _safe_nonnegative_int(getattr(response, "status_code", 0)) or None
            _emit_telemetry(
                telemetry_sink,
                _attempt_event(
                    context=call_context,
                    model=model,
                    request_id=response_request_id,
                    status_code=response_status,
                    attempt=attempt,
                    usage=LLMUsage(),
                    latency_ms=latency_ms,
                    retry_reason=None,
                    estimated_cost_cny=0.0,
                    prompt_hash=prompt_hash,
                    response_hash=None,
                    status="failed",
                ),
                required=telemetry_required,
            )
            return _failed_result(
                model=model,
                prompt_hash=prompt_hash,
                attempts=attempt,
                latency_ms=total_latency_ms,
                error_type="api",
                detail=exc,
                request_id=response_request_id,
                status_code=response_status,
            )

    return _failed_result(
        model=model,
        prompt_hash=prompt_hash,
        attempts=attempts_allowed,
        latency_ms=total_latency_ms,
        error_type="unknown",
        detail="DashScope call ended without a result.",
    )


# ─────────────────────────────────────────────────────────────
# 公共辅助函数
# ─────────────────────────────────────────────────────────────

def _chat(
    prompt: str,
    config: LLMConfig,
    task: str = "qa",
    system_prompt: Optional[str] = None,
    timeout: int = 60,
    retries: int = 1,
) -> str:
    result = _chat_result(
        prompt,
        config,
        task=task,
        system_prompt=system_prompt or "你是一名擅长化学文献与实验的学术助理。",
        timeout=timeout,
        max_attempts=min(MAX_TOTAL_ATTEMPTS, max(1, int(retries) + 1)),
    )
    return result.content


def format_context(chunks: List[Dict]) -> str:
    """
    将检索到的片段构造成提示词文本。
    chunks: [{text, source_title, ...}, ...]
    """
    parts = []
    for i, c in enumerate(chunks, 1):
        parts.append(f"[片段{i} | {c['source_title']}]\n{c['text']}")
    return "\n\n".join(parts)


# ─────────────────────────────────────────────────────────────
# 各功能的调用函数
# ─────────────────────────────────────────────────────────────

def call_llm(
    question: str,
    chunks: List[Dict],
    config: Optional[LLMConfig] = None,
) -> str:
    """
    智能问答（RAG）。
    自动使用 qwen3.7-plus。
    """
    if not chunks:
        return "当前还没有可用的文献内容，请先导入 PDF 或网页，再进行提问。"

    cfg = _ensure_config(config)
    ctx = format_context(chunks)
    lang_inst = _lang_instruction(question + ctx)
    prompt = (
        "你是一名精通化学与实验设计的学术助理。"
        "下面给出的是用户已上传的文献/实验资料片段，请仅基于这些内容回答问题，"
        "不要编造原文中没有的信息。回答时要：\n"
        "1. 先用通俗准确的语言回答用户的问题；\n"
        "2. 在回答结尾给出你主要参考的片段编号列表（例如：主要依据片段 1, 3, 4）；\n"
        "3. 如果原文中没有关键信息，请明确说明。\n\n"
        f"【语言要求】: {lang_inst}\n\n"
        f"【文献片段】:\n{ctx}\n\n"
        f"【用户问题】:{question}\n\n"
    )
    return _chat(prompt, cfg, task="qa")


def summarize_chemistry_document(
    full_text: str,
    config: Optional[LLMConfig] = None,
) -> str:
    """
    文献自动摘要。
    自动使用 qwen3.7-plus。
    """
    if not full_text.strip():
        return "文档内容为空，无法生成摘要。"

    cfg = _ensure_config(config)
    snippet = full_text[:12000]
    prompt = (
        '下面是一份与化学实验相关的文档内容，请你作为"化学文献分析助手"提取并列出以下信息：\n'
        "请务必全部使用中文输出，包括标题、作者、期刊、结论等所有字段均翻译为中文。\n"
        "每一项前加\"**字段名**：\"格式。\n"
        "- **标题**：\n"
        "- **作者**：\n"
        "- **期刊**：\n"
        "- **年份**：\n"
        "- **DOI**：\n"
        "- **材料名称**：\n"
        "- **分子式**：\n"
        "- **实验安全注意事项**：\n"
        "- **研究目的**：\n"
        "- **主要结论（分条说明）**：\n"
        "- **关键化学物质及其性能**：\n\n"
        "请只根据文档内容回答，不要凭空编造。\n\n"
        f"【文档内容节选（如篇幅较长仅保留前 12000 字符）】:\n{snippet}"
    )
    return _chat(prompt, cfg, task="summarize")


def extract_sop(
    full_text: str,
    config: Optional[LLMConfig] = None,
) -> str:
    """
    提取标准操作程序（SOP），返回 JSON。
    自动使用 qwen3.7-max（需要严格 JSON 结构化输出）。
    """
    if not full_text.strip():
        return "{}"
    cfg = _ensure_config(config)
    snippet = full_text[:10000]
    lang_inst = _lang_instruction(snippet)
    prompt = (
        "你是一名化学实验 SOP 专家。请阅读下面的实验文献节选，"
        "提取出标准操作程序（Standard Operating Procedure），"
        "严格以 JSON 格式输出，不要加 markdown 代码块，直接返回 JSON 对象，结构如下：\n"
        "{\n"
        '  "title": "Experiment Title / 实验名称",\n'
        '  "chemicals": [\n'
        '    {"name": "Chemical Name", "amount": "Amount / 用量", "role": "Role / 作用", "safety": "Safety Note / 安全提示"}\n'
        "  ],\n"
        '  "steps": [\n'
        '    {"step": 1, "action": "Action / 操作描述", "params": "Parameters / 温度/时间/压力等参数", "safety_note": "Safety Note / 安全注意"}\n'
        "  ],\n"
        '  "post_processing": "Post-processing / 后处理说明",\n'
        '  "characterization": "Characterization / 表征/分析方法"\n'
        "}\n\n"
        f"【语言要求】: {lang_inst}（JSON 字段名保持英文，字段值按要求语言输出）\n"
        "如果原文中没有某项信息，对应字段留空字符串或空数组。\n"
        "不要凭空编造原文中没有的信息。\n\n"
        f"【文献节选】:\n{snippet}"
    )
    result = _chat(prompt, cfg, task="sop")
    return result.strip()


def multi_doc_compare(
    docs: List[Dict],
    config: Optional[LLMConfig] = None,
) -> str:
    """
    多文献横向对比分析。
    自动使用 qwen3.7-max（多文档综合推理）。
    """
    if not docs:
        return "没有提供文献内容。"
    cfg = _ensure_config(config)
    parts = []
    for i, d in enumerate(docs, 1):
        snippet = d.get("text", "")[:4000]
        parts.append(f"【文献 {i}：{d.get('title', '未知')}】\n{snippet}")
    combined = "\n\n".join(parts)
    lang_inst = _lang_instruction(combined)
    prompt = (
        "你是一名化学研究员，擅长系统比较不同实验方法。"
        "请对下面给出的多篇化学文献进行横向对比，"
        "用 Markdown 表格呈现，对比维度包括：\n"
        "Method / 实验方法 | Conditions (T/P/Solvent) / 反应条件 | Catalyst/Reagent / 催化剂或关键试剂 | Yield/Purity / 产率/纯度 | Characterization / 表征结果 | Safety Risk / 安全风险等级\n\n"
        "表格之后再用 2-3 段文字总结各方案的优缺点，给出选材/选路线建议。\n"
        f"【语言要求】: {lang_inst}\n\n"
        f"{combined}"
    )
    return _chat(prompt, cfg, task="compare", timeout=120)


def translate_with_glossary(
    text: str,
    glossary: Optional[List[Dict]] = None,
    config: Optional[LLMConfig] = None,
) -> str:
    """
    专业术语翻译 + 术语库注入。
    自动使用 qwen3.7-max。
    """
    if not text.strip():
        return '{"translation": "", "glossary": []}'
    cfg = _ensure_config(config)
    snippet = text[:8000]
    gloss_hint = ""
    if glossary:
        pairs = "; ".join([f"{g['en']}={g['zh']}" for g in glossary[:50]])
        gloss_hint = f"\n\n【用户已有术语库（翻译时请优先采用）】：{pairs}"
    lang_inst = _lang_instruction(snippet)
    prompt = (
        "你是一名化工与材料科学领域的专业翻译。"
        f"请将下面的文献内容翻译为另一种语言。{lang_inst}\n"
        "如果是英文请翻译为中文，如果是中文请翻译为英文。\n"
        "并在翻译结束后，从全文中提取所有重要的化工/材料专业术语，"
        "整理为术语对照表（同时包含中英文）。\n"
        "严格以 JSON 格式输出（不要加 markdown 代码块）：\n"
        "{\n"
        '  "translation": "完整译文",\n'
        '  "glossary": [\n'
        '    {"en": "English term", "zh": "中文译名", "note": "Brief note / 简短说明（可选）"}\n'
        "  ]\n"
        "}\n"
        + gloss_hint
        + f"\n\n【原文节选】:\n{snippet}"
    )
    # 翻译使用更长的超时时间（180秒）
    result = _chat(prompt, cfg, task="translate", timeout=180)
    return result.strip()


def lab_record_suggest(
    record_text: str,
    related_doc_summaries: List[str],
    config: Optional[LLMConfig] = None,
) -> str:
    """
    实验记录智能建议。
    自动使用 qwen3.7-plus。
    """
    cfg = _ensure_config(config)
    doc_context = ""
    if related_doc_summaries:
        doc_context = "\n\n【相关文献摘要】:\n" + "\n---\n".join(related_doc_summaries[:3])
    lang_inst = _lang_instruction(record_text)
    prompt = (
        "你是一名经验丰富的化学实验安全顾问。"
        "实验者正在进行实验并记录了以下内容，请根据记录内容和相关文献，"
        "给出专业建议，包括：\n"
        "1. 记录中出现的异常现象（颜色变化/沉淀/放热等）的可能原因；\n"
        "2. 针对实验现象的安全处置建议；\n"
        "3. 下一步实验操作建议；\n"
        "4. 与相关文献对比，指出当前实验与文献方法的差异。\n"
        f"【语言要求】: {lang_inst}\n\n"
        f"【实验记录】:\n{record_text}"
        + doc_context
    )
    return _chat(prompt, cfg, task="lab_suggest")


def extract_reactions(
    full_text: str,
    config: Optional[LLMConfig] = None,
) -> str:
    """
    从化学文献中提取反应信息，返回 JSON。
    自动使用 qwen3.7-max（复杂结构化提取）。
    """
    if not full_text.strip():
        return '{"reactions": [], "summary": ""}'
    cfg = _ensure_config(config)
    snippet = full_text[:12000]
    lang_inst = _lang_instruction(snippet)
    prompt = (
        "你是一名化学合成专家。请阅读下面的化学文献节选，"
        "提取其中描述的所有化学反应信息，"
        "严格以 JSON 格式输出，不要加 markdown 代码块，直接返回 JSON 对象，结构如下：\n"
        "{\n"
        '  "reactions": [\n'
        "    {\n"
        '      "name": "Reaction Name / 反应名称或编号",\n'
        '      "reactants": ["Reactant 1 / 反应物1", "Reactant 2 / 反应物2"],\n'
        '      "products": ["Product 1 / 产物1", "Product 2 / 产物2"],\n'
        '      "catalyst": "Catalyst / 催化剂或关键试剂",\n'
        '      "solvent": "Solvent System / 溶剂体系",\n'
        '      "temperature": "Temperature / 反应温度",\n'
        '      "time": "Reaction Time / 反应时间",\n'
        '      "pressure": "Pressure / 反应压力",\n'
        '      "ph": "pH Value",\n'
        '      "yield": "Yield / 产率/收率",\n'
        '      "workup": "Workup / 后处理方法",\n'
        '      "notes": "Notes / 其他重要信息"\n'
        "    }\n"
        "  ],\n"
        '  "summary": "Brief summary in 2-3 sentences / 用2-3句话概括"\n'
        "}\n\n"
        f"【语言要求】: {lang_inst}（JSON 字段名保持英文，字段值按要求语言输出）\n"
        "要求：\n"
        "1. 反应物 → 产物 的方向必须正确\n"
        "2. 温度、时间、产率等数值务必准确提取，不要编造\n"
        "3. 如果某字段在文献中没有明确提及，使用空字符串 \"\" 或空数组 []\n"
        "4. 如果文献中没有任何反应信息，返回 reactions 为空数组\n\n"
        f"【文献内容】：\n{snippet}"
    )
    return _chat(prompt, cfg, task="reactions")


def find_image_pages(
    per_page_text: str,
    config: Optional[LLMConfig] = None,
) -> List[int]:
    """
    分析每页文本，返回可能包含图片的页码列表。
    自动使用 qwen3.7-plus。
    """
    cfg = _ensure_config(config)
    prompt = (
        "下面给出一份 PDF 文件的逐页文本内容，用一行类似 '----PAGE----' 的标记分隔各页。"
        "请分析哪些页可能包含图片或图表（例如图 1、Figure、图片等），"
        "只返回页码数字，用逗号或空格分隔，格式如 '1,3,5' 或 '2 4'. 不需要其他说明。"
        f"\n\n{per_page_text}"
    )
    resp = _chat(prompt, cfg, task="find_image_pages")
    import re
    nums = re.findall(r"\d+", resp)
    try:
        return [int(n) for n in nums]
    except Exception:
        return []


# ─────────────────────────────────────────────────────────────
# 通用文本提取（化学品名称）
# ─────────────────────────────────────────────────────────────

def extract_chemical_names_llm(
    text: str,
    config: Optional[LLMConfig] = None,
) -> List[str]:
    """
    使用 LLM 从文本中提取化学品名称。
    自动使用 qwen3.7-plus。
    """
    cfg = _ensure_config(config)
    prompt = (
        "请从下面的化学文献中提取所有出现的化学品名称，"
        "只返回化学品英文名称列表，用换行符分隔，不要编号，不要其他说明。\n\n"
        f"{text[:6000]}"
    )
    result = _chat(prompt, cfg, task="extract_names")
    chemicals = [c.strip() for c in result.strip().splitlines() if c.strip()]
    return chemicals


def crawl_summarize(
    text: str,
    config: Optional[LLMConfig] = None,
) -> str:
    """
    网页爬取入库时的快速摘要。
    自动使用 qwen3.7-plus。
    """
    return summarize_chemistry_document(text, config=config)


# ─────────────────────────────────────────────────────────────
# 中英文互译（通用）
# ─────────────────────────────────────────────────────────────

def translate_text(
    text: str,
    config: Optional[LLMConfig] = None,
    target_lang: Optional[str] = None,
) -> str:
    """
    中英文互译。

    Args:
        text: 待翻译文本
        config: LLMConfig
        target_lang: 目标语言 "zh" 或 "en"。None 时自动检测。
    """
    if not text.strip():
        return ""
    cfg = _ensure_config(config)

    if target_lang == "zh":
        lang_hint = "请将以下英文翻译为中文。直接输出翻译结果。\n"
    elif target_lang == "en":
        lang_hint = "请将以下中文翻译为英文。直接输出翻译结果。\n"
    else:
        lang_hint = (
            "你是一名专业的中英文翻译。请判断以下文本是中文还是英文，然后翻译为另一种语言。\n"
            "如果是中文，翻译为英文；如果是英文，翻译为中文。\n"
            "请直接输出翻译结果，不要加任何解释或说明。\n"
        )

    prompt = f"{lang_hint}\n【原文】:\n{text[:8000]}"
    return _chat(prompt, cfg, task="translate", timeout=180)


# ─────────────────────────────────────────────────────────────
# 语言检测辅助
# ─────────────────────────────────────────────────────────────

def _detect_lang(text: str) -> str:
    """简单检测文本主要语言。返回 'zh' 或 'en'。"""
    if not text:
        return "zh"
    zh_chars = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
    if zh_chars / max(len(text), 1) > 0.1:
        return "zh"
    return "en"


def _lang_instruction(text: str) -> str:
    """
    返回语言互译指令。
    如果文本主要是中文，指示输出为英文；反之亦然。
    """
    lang = _detect_lang(text)
    if lang == "zh":
        return "请用英文回答/输出（English output required）。"
    else:
        return "请用中文回答/输出。"


# ─────────────────────────────────────────────────────────────
# VASP 计算参数提取
# ─────────────────────────────────────────────────────────────

def extract_vasp_parameters(
    text: str,
    calc_type: str = "auto",
    config: Optional["LLMConfig"] = None,
) -> str:
    """
    从文献文本中提取 VASP 计算参数。

    返回 JSON 字符串，包含：
      calc_type, system_name, functional, is_metal,
      incar (dict), kpoints (dict), poscar (dict),
      potcar_elements (list), missing_params (list), notes
    """
    lang_inst = _lang_instruction(text)
    snippet = text[:12000]

    calc_type_hint = ""
    if calc_type and calc_type != "auto":
        calc_type_hint = f"\n用户指定的计算类型: {calc_type}（若文献描述与之相符，优先采用）\n"

    prompt = (
        "你是一名计算化学与材料科学专家，精通 VASP (Vienna Ab initio Simulation Package) 第一性原理计算。\n"
        "请阅读下面的文献节选，提取所有与 VASP 计算相关的参数信息。\n\n"
        "严格以 JSON 格式输出，不要加 markdown 代码块标记（不要 ```json），直接返回 JSON 对象：\n"
        "{\n"
        '  "calc_type": "scf 或 relax 或 dos 或 band 或 phonon 或 optics 或 unknown",\n'
        '  "system_name": "材料名称或化学式",\n'
        '  "functional": "PBE 或 PBEsol 或 HSE06 或 SCAN 或 r2SCAN 或 unknown",\n'
        '  "is_metal": true 或 false,\n'
        '  "incar": {\n'
        '    "ENCUT": 520,\n'
        '    "EDIFF": 1e-6,\n'
        '    "IBRION": 2,\n'
        '    "...": "..." \n'
        "  },\n"
        '  "kpoints": {\n'
        '    "mode": "Gamma 或 Monkhorst-Pack 或 Line",\n'
        '    "mesh": "6 6 6"\n'
        "  },\n"
        '  "poscar": {\n'
        '    "lattice_type": "FCC/BCC/HCP/Hexagonal/Simple Cubic/unknown",\n'
        '    "lattice_constants": {"a": 0, "b": 0, "c": 0, "alpha": 90, "beta": 90, "gamma": 90},\n'
        '    "space_group": "如 Fd-3m 或 unknown",\n'
        '    "formula_units": 0,\n'
        '    "basis_atoms": [{"element": "Co", "fractional_coords": [0, 0, 0]}, ...]\n'
        "  },\n"
        '  "potcar_elements": ["Co", "N", "C", ...],\n'
        '  "missing_params": ["文献中提到但未给出具体值的参数列表"],\n'
        '  "notes": "补充说明"\n'
        "}\n\n"
        "规则：\n"
        "1. 文献明确给出的数值必须原样提取，不要修改。\n"
        "2. 文献未提及的参数设为 null，不要猜测或编造。\n"
        "3. 若文献提到 'standard DFT settings' 等模糊描述，根据上下文合理推断具体值并在 notes 中说明。\n"
        "4. 对于金属体系 ISMEAR 应为 1 (Methfessel-Paxton)，绝缘体/半导体 ISMEAR=0 (Gaussian)，"
        "DOS 计算应 ISMEAR=-5 (Tetrahedron)。\n"
        "5. POSCAR 中如果能提取出晶格参数和原子坐标，请尽量提取。\n"
        "6. potcar_elements 按文献提及的元素顺序排列。\n\n"
        f"{lang_inst}\n"
        f"{calc_type_hint}\n"
        f"【文献节选】:\n{snippet}"
    )

    cfg = _ensure_config(config)
    return _chat(prompt, cfg, task="vasp_extract", timeout=120)


# ─────────────────────────────────────────────────────────────
# Materials Studio 2024 建模指南生成
# ─────────────────────────────────────────────────────────────

def generate_ms_guide(
    text: str,
    model_target: str = "auto",
    config: Optional["LLMConfig"] = None,
) -> str:
    """
    根据文献描述生成 Materials Studio 2024 详细操作指南。

    返回 JSON 字符串，包含：
      modeling_target, method, guide (三阶段步骤), tips, warnings
    """
    lang_inst = _lang_instruction(text)
    snippet = text[:10000]

    prompt = (
        "你是一名 Materials Studio 2024 (Biovia/Dassault Systemes) 建模与计算专家。\n"
        "请根据下面的文献描述，生成一份详细的 Materials Studio 2024 操作指南。\n\n"
        "严格以 JSON 格式输出，不要加 markdown 代码块标记（不要 ```json），直接返回 JSON 对象：\n"
        "{\n"
        '  "modeling_target": "crystal 或 surface 或 interface 或 adsorption 或 molecule 或 nanoparticle 或 defect 或 amorphous 或 unknown",\n'
        '  "method": "CASTEP 或 DFTB+ 或 DMol3 或 Forcite 或 GULP 或 Amorphous Cell 或 Adsorption Locator",\n'
        '  "guide": {\n'
        '    "phase_1_structure": {\n'
        '      "title": "结构搭建",\n'
        '      "steps": [\n'
        '        {"step": 1, "action": "File > Import", "detail": "导入 .cif 或 .xsd 结构文件"},\n'
        '        {"step": 2, "action": "Build > ...", "detail": "..."},\n'
        "        ...\n"
        "      ]\n"
        "    },\n"
        '    "phase_2_calculation": {\n'
        '      "title": "计算设置",\n'
        '      "module_path": "Modules > CASTEP > Calculation",\n'
        '      "params": {"functional": "GGA-PBE", "quality": "Fine", "...": "..."},\n'
        '      "steps": [\n'
        "        ...\n"
        "      ]\n"
        "    },\n"
        '    "phase_3_analysis": {\n'
        '      "title": "结果分析",\n'
        '      "steps": [\n'
        "        ...\n"
        "      ]\n"
        "    }\n"
        "  },\n"
        '  "tips": ["实用提示 1", "实用提示 2"],\n'
        '  "warnings": ["注意事项 1"]\n'
        "}\n\n"
        "Materials Studio 2024 模块参考（路径必须准确）：\n"
        "- CASTEP: DFT 平面波，适用于周期性体系。路径: Modules > CASTEP > Calculation\n"
        "  - 泛函: GGA-PBE, LDA, HSE06 等\n"
        "  - 精度: Coarse / Medium / Fine / Ultra-Fine\n"
        "  - k-point 网格: Calculation > Setup > k-point grid\n"
        "- DMol3: DFT 数值原子轨道，适用于分子和团簇。路径: Modules > DMol3 > Calculation\n"
        "  - 泛函: GGA-PBE, BLYP 等; 基组: DNP, TNP\n"
        "- Forcite: 经典分子力学/动力学。路径: Modules > Forcite > Calculation\n"
        "  - 力场: COMPASS III, Universal, DREIDING\n"
        "- DFTB+: 半经验 DFT。路径: Modules > DFTB+ > Calculation\n"
        "- Amorphous Cell: 构建非晶体系。路径: Modules > Amorphous Cell > Construction\n"
        "- 表面切割: Build > Surfaces > Cleave Surface\n"
        "- 超胞扩展: Build > Symmetry > Supercell\n"
        "- 层状结构: Build > Build Layers\n"
        "- 吸附位点搜索: Modules > Adsorption Locator > Calculation\n"
        "- 添加原子: Build > Add Atoms\n"
        "- 手动成键: Edit > Bond\n\n"
        "规则：\n"
        "1. 根据文献描述识别建模目标类型（晶体/表面/界面/吸附/分子等）。\n"
        "2. 选择最合适的方法/模块（DFT 用 CASTEP 或 DMol3；力场用 Forcite）。\n"
        "3. 菜单路径必须符合 Materials Studio 2024 界面（不要引用旧版路径）。\n"
        "4. 步骤必须具体、可操作，包含参数值和选项名称。\n"
        "5. 表面/吸附模型需注明真空层厚度（建议 >= 15 Å）。\n"
        "6. 对常见陷阱给出 warnings。\n\n"
        f"{lang_inst}\n"
        f"【文献节选】:\n{snippet}"
    )

    cfg = _ensure_config(config)
    return _chat(prompt, cfg, task="ms_guide", timeout=120)
