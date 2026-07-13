"""结构化信息提取模块——从文本中调用大模型生成 JSON 结果。

参考 LitMiner 中的 extractor.py，保留简化的逻辑并适配学习平台的 LLM 接口。
"""

import json
import re
from typing import List

from prompts.chemistry_extract import EXTRACTION_PROMPT, EXTRACTION_FIELDS
from llm_client import LLMConfig, _chat_with_provider


def build_extraction_messages(text: str, custom_fields: List[str] | None = None) -> List[dict]:
    """构造对话消息，用于让模型提取结构化信息。"""
    fields_desc = EXTRACTION_FIELDS
    if custom_fields:
        fields_desc += "\n自定义字段:\n" + "\n".join(f"- {f}" for f in custom_fields)

    system_msg = EXTRACTION_PROMPT.format(fields=fields_desc)
    max_chars = 60000
    if len(text) > max_chars:
        text = text[:max_chars] + "\n\n[文本已截断...]"
    return [
        {"role": "system", "content": system_msg},
        {"role": "user", "content": f"请从以下内容中提取结构化信息：\n\n{text}"},
    ]


def parse_llm_response(response_text: str) -> dict:
    """尝试从模型输出中提取 JSON。"""
    json_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", response_text, re.DOTALL)
    if json_match:
        json_str = json_match.group(1).strip()
    else:
        json_str = response_text.strip()
    try:
        return json.loads(json_str)
    except json.JSONDecodeError:
        return {"raw_response": response_text, "parse_error": "无法解析为 JSON"}


def extract_from_text(
    text: str,
    config: LLMConfig | None = None,
    custom_fields: List[str] | None = None,
    temperature: float = 0.1,
) -> dict:
    messages = build_extraction_messages(text, custom_fields)
    answer = _chat_with_provider("", config or LLMConfig())
    # NOTE: here we reuse _chat_with_provider which expects a prompt, so we bypass
    # by assigning messages in a different way.
    # For simplicity, call call_llm from llm_client? Actually call_llm only for QA.
    # We'll implement a simple wrapper: create prompt from messages.
    # But it's easier to use _chat_with_provider directly with messages assembled.
    from llm_client import call_llm
    # call_llm expects question and chunks -> not suitable
    # We'll reimplement simple chat using _chat_with_provider by constructing prompt.
    prompt = "\n".join([m["content"] for m in messages if m["role"] != "system"])  # simplistic
    client = config or LLMConfig()
    response = _chat_with_provider(prompt, client)
    return parse_llm_response(response)
