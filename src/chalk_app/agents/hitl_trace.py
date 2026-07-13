# -*- coding: utf-8 -*-
"""
Shared helpers for HITL review records, field diffs, and Agent Trace output.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, Iterable, List


REVIEW_CRITERIA = [
    {
        "key": "citation_authenticity",
        "label": "引用是否真实",
        "options": ["—", "真实且可追溯", "部分需要核验", "疑似虚构或缺失"],
    },
    {
        "key": "over_extension",
        "label": "是否存在过度外推",
        "options": ["—", "未发现", "轻微外推", "存在明显过度外推"],
    },
    {
        "key": "falsifiability",
        "label": "假设是否可证伪",
        "options": ["—", "可证伪", "需要补充判据", "目前不可证伪"],
    },
    {
        "key": "experiment_feasibility",
        "label": "实验路径是否可行",
        "options": ["—", "可行", "需调整条件", "不可行"],
    },
    {
        "key": "baseline_need",
        "label": "是否需要补充 baseline",
        "options": ["—", "不需要", "需要补充", "必须补充"],
    },
    {
        "key": "reference_replacement",
        "label": "是否需要替换参考文献",
        "options": ["—", "不需要", "建议替换", "必须替换"],
    },
]


ACTION_LABELS = {
    "approve": "批准",
    "revise": "反馈修订",
    "skip": "跳过",
    "cancel": "取消",
}


STANCE_LABELS = {
    "neutral": "中立观察",
    "devil": "支持反方",
    "optimist": "支持正方",
    "custom": "自定义攻击点",
}


CHANGE_LABELS = {
    "added": "新增",
    "removed": "删除",
    "changed": "修改",
}


def compact_value(value: Any, max_len: int = 160) -> str:
    """Render JSON-ish values into a compact, stable one-line string."""
    if value is None:
        text = "（空）"
    elif isinstance(value, (dict, list)):
        text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    else:
        text = str(value)
    text = " ".join(text.split())
    if len(text) > max_len:
        return text[: max_len - 1] + "…"
    return text


def _flatten(value: Any, prefix: str = "", depth: int = 0, max_depth: int = 5) -> Dict[str, Any]:
    if depth >= max_depth:
        return {prefix or "$": value}

    if isinstance(value, dict):
        if not value:
            return {prefix or "$": value}
        flat: Dict[str, Any] = {}
        for key in sorted(value.keys(), key=lambda item: str(item)):
            path = f"{prefix}.{key}" if prefix else str(key)
            flat.update(_flatten(value[key], path, depth + 1, max_depth))
        return flat

    if isinstance(value, list):
        if not value:
            return {prefix or "$": value}
        flat = {}
        for idx, item in enumerate(value[:30]):
            path = f"{prefix}[{idx}]" if prefix else f"[{idx}]"
            flat.update(_flatten(item, path, depth + 1, max_depth))
        if len(value) > 30:
            flat[f"{prefix}.__truncated__"] = f"{len(value) - 30} more items"
        return flat

    return {prefix or "$": value}


def _stable(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def build_field_diff(before: Any, after: Any, max_items: int = 80) -> List[Dict[str, str]]:
    """
    Produce a field-level diff between two JSON-compatible values.

    The output is intentionally small and UI-friendly: path, change type,
    before value, and after value.
    """
    before_flat = _flatten(before)
    after_flat = _flatten(after)
    all_paths = sorted(set(before_flat.keys()) | set(after_flat.keys()))
    diff: List[Dict[str, str]] = []

    for path in all_paths:
        before_missing = path not in before_flat
        after_missing = path not in after_flat
        if before_missing:
            change = "added"
        elif after_missing:
            change = "removed"
        elif _stable(before_flat[path]) != _stable(after_flat[path]):
            change = "changed"
        else:
            continue

        diff.append(
            {
                "field": path,
                "change": change,
                "before": "" if before_missing else compact_value(before_flat[path]),
                "after": "" if after_missing else compact_value(after_flat[path]),
            }
        )
        if len(diff) >= max_items:
            diff.append(
                {
                    "field": "__truncated__",
                    "change": "changed",
                    "before": "",
                    "after": "字段变化过多，已截断显示",
                }
            )
            break

    return diff


def summarize_diff(diff: Iterable[Dict[str, str]], max_items: int = 6) -> str:
    items = list(diff)
    if not items:
        return "未发现字段变化"
    visible = items[:max_items]
    parts = [
        f"{item.get('field', '?')}（{CHANGE_LABELS.get(item.get('change', ''), item.get('change', '变化'))}）"
        for item in visible
    ]
    suffix = f" 等 {len(items)} 项" if len(items) > max_items else ""
    return "；".join(parts) + suffix


def structured_feedback_items(feedback: Dict[str, Any]) -> List[Dict[str, str]]:
    if not isinstance(feedback, dict):
        return []
    items = []
    for key, value in feedback.items():
        if not value or value == "—":
            continue
        if isinstance(value, dict):
            label = str(value.get("label") or key)
            text = str(value.get("value") or "")
        else:
            label = str(key)
            text = str(value)
        if text and text != "—":
            items.append({"label": label, "value": text})
    return items


def make_agent_trace(
    *,
    session_id: str,
    mode: str,
    research_question: str,
    result: Any,
    interaction_history: Dict[str, Any],
) -> Dict[str, Any]:
    data = getattr(result, "raw_json", {}) or {}
    return {
        "schema_version": "hitl-agent-trace-v1",
        "created_at": datetime.utcnow().isoformat(),
        "session_id": session_id,
        "mode": mode,
        "research_question": research_question,
        "title": data.get("paper_title", "未命名假设") if isinstance(data, dict) else "未命名假设",
        "interaction_history": interaction_history or {"interactions": []},
        "iterations": getattr(result, "iterations", []),
        "critique_history": getattr(result, "critique_history", []),
        "debate_history": getattr(result, "debate_history", []),
        "reasoning_chain": getattr(result, "reasoning_chain", None),
        "final_hypothesis": data,
    }
