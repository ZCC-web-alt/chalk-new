# -*- coding: utf-8 -*-
"""Progress model for the hypothesis-generation visual timeline.

The orchestrator emits coarse textual stages, while the UI needs a stable
timeline that reflects the selected iteration count. This module keeps that
mapping independent from Qt and guarantees that a running task never moves
backward because of delayed progress or HITL pauses.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List


_ROUND_RE = re.compile(r"第\s*(\d+)\s*轮")


def build_hypothesis_progress_steps(
    max_iterations: int = 3,
    hitl_enabled: bool = True,
) -> List[Dict[str, Any]]:
    """Return the visual steps for the selected hypothesis iteration count."""
    rounds = max(1, min(int(max_iterations or 1), 8))
    steps: List[Dict[str, Any]] = [
        {
            "key": "pre_search",
            "short": "预搜",
            "title": "开放文献预搜索",
            "hint": "补充可引用的开放文献证据",
        },
        {
            "key": "literature",
            "short": "事实",
            "title": "文献理解与事实提取",
            "hint": "抽取问题、变量、结论与边界条件",
        },
        {
            "key": "reasoning",
            "short": "推理",
            "title": "推理链构建",
            "hint": "构建因果链与机制假说路径",
        },
        {
            "key": "initial",
            "short": "初稿",
            "title": "初始假设生成",
            "hint": "生成第一版结构化假设 JSON",
        },
        {
            "key": "cross_domain",
            "short": "迁移",
            "title": "跨学科迁移分析",
            "hint": "寻找可迁移的机制或技术类比",
        },
    ]

    if hitl_enabled:
        steps.append(
            {
                "key": "initial_hitl",
                "short": "初审",
                "title": "初始假设人工审核",
                "hint": "人工检查初始假设与证据基础",
            }
        )

    for round_num in range(1, rounds + 1):
        steps.extend(
            [
                {
                    "key": f"critique_{round_num}",
                    "short": f"思{round_num}",
                    "title": f"第 {round_num} 轮思辨评审",
                    "hint": "发现漏洞、约束和可证伪点",
                },
                {
                    "key": f"debate_{round_num}",
                    "short": f"辩{round_num}",
                    "title": f"第 {round_num} 轮多角色辩论",
                    "hint": "反方、正方与平衡结论",
                },
            ]
        )
        if hitl_enabled:
            steps.append(
                {
                    "key": f"hitl_{round_num}",
                    "short": f"审{round_num}",
                    "title": f"第 {round_num} 轮人工审核",
                    "hint": "人工选择立场、修订 JSON 或批准",
                }
            )
        steps.append(
            {
                "key": f"revision_{round_num}",
                "short": f"修{round_num}",
                "title": f"第 {round_num} 轮假设修订",
                "hint": "融合评审、辩论与人工反馈",
            }
        )

    steps.extend(
        [
            {
                "key": "validation",
                "short": "可验",
                "title": "可验证性评估",
                "hint": "评估实验设计与证伪路径",
            },
            {
                "key": "results_verify",
                "short": "推导",
                "title": "公式推导与结果验证",
                "hint": "检查定量推导和实验结果逻辑",
            },
            {
                "key": "scientific_tools",
                "short": "工具",
                "title": "科学工具验证",
                "hint": "RDKit/pymatgen/atomate2 dry-run",
            },
            {
                "key": "closed_loop",
                "short": "回验",
                "title": "智能闭环回验",
                "hint": "检查假设、结果与证据是否自洽",
            },
            {
                "key": "output",
                "short": "输出",
                "title": "标准化输出",
                "hint": "生成结构化最终假设 JSON",
            },
            {
                "key": "references",
                "short": "引用",
                "title": "开放文献支撑合并",
                "hint": "合并引用并进行证据准入",
            },
            {
                "key": "final_report",
                "short": "报告",
                "title": "标准报告生成完成",
                "hint": "渲染最终报告与可视化",
            },
        ]
    )
    return steps


@dataclass
class HypothesisProgressTracker:
    max_iterations: int = 3
    hitl_enabled: bool = True
    active_index: int = -1
    percent: int = 0
    last_stage: str = ""
    state: str = "idle"
    steps: List[Dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.reset(self.max_iterations, self.hitl_enabled)

    def reset(self, max_iterations: int = 3, hitl_enabled: bool = True) -> None:
        self.max_iterations = max(1, min(int(max_iterations or 1), 8))
        self.hitl_enabled = bool(hitl_enabled)
        self.steps = build_hypothesis_progress_steps(self.max_iterations, self.hitl_enabled)
        self.active_index = -1
        self.percent = 0
        self.last_stage = ""
        self.state = "idle"

    def update(
        self,
        stage: str = "",
        current: int = 0,
        total: int = 0,
        state: str = "running",
    ) -> Dict[str, Any]:
        mapped_index = self.index_for_stage(stage)
        if state == "done":
            mapped_index = len(self.steps) - 1
        elif state == "idle" and not stage:
            mapped_index = -1

        if state == "running":
            if mapped_index < self.active_index:
                mapped_index = self.active_index
        elif state == "error":
            mapped_index = max(mapped_index, self.active_index, 0)

        pct = self._percent_for(mapped_index, current, total, state)
        if state == "running":
            pct = max(self.percent, pct)
        elif state == "done":
            pct = 100

        self.active_index = mapped_index
        self.percent = pct
        self.last_stage = str(stage or self.last_stage or "")
        self.state = state
        return self.snapshot(stage=stage or self.last_stage, current=current, total=total)

    def snapshot(self, stage: str = "", current: int = 0, total: int = 0) -> Dict[str, Any]:
        return {
            "steps": self.steps,
            "active_index": self.active_index,
            "percent": self.percent,
            "stage": str(stage or self.last_stage or ""),
            "current": current,
            "total": total,
            "state": self.state,
        }

    def index_for_stage(self, stage: str) -> int:
        text = str(stage or "")
        if not text:
            return -1
        round_num = self._round_num(text)
        if "等待多模态" in text or "启动" in text or "Qwen-Agent" in text:
            return 0
        if "预搜索" in text:
            return self._index("pre_search")
        if "文献理解" in text or "事实" in text:
            return self._index("literature")
        if "推理链" in text:
            return self._index("reasoning")
        if "初始假设审核" in text or ("初始" in text and ("审核" in text or "人工" in text or "HITL" in text)):
            return self._index("initial_hitl") if self.hitl_enabled else self._index("initial")
        if "初始假设" in text:
            return self._index("initial")
        if "跨学科" in text:
            return self._index("cross_domain")
        if round_num:
            if "思辨与辩论审核" in text or "人工" in text or "HITL" in text or "审核" in text:
                return self._index(f"hitl_{round_num}") if self.hitl_enabled else self._index(f"debate_{round_num}")
            if "多角色辩论" in text or "辩论" in text:
                return self._index(f"debate_{round_num}")
            if "修订" in text:
                return self._index(f"revision_{round_num}")
            if "思辨" in text:
                return self._index(f"critique_{round_num}")
        if "可验证性" in text:
            return self._index("validation")
        if "公式推导" in text or "结果验证" in text:
            return self._index("results_verify")
        if "RDKit" in text or "pymatgen" in text or "科学工具" in text:
            return self._index("scientific_tools")
        if "闭环" in text:
            return self._index("closed_loop")
        if "标准化输出" in text or "生成标准化" in text:
            return self._index("output")
        if "开放文献支撑" in text or "参考文献" in text or "引用" in text:
            return self._index("references")
        if "报告" in text or "完成" in text:
            return self._index("final_report")
        return max(self.active_index, 0)

    def _index(self, key: str) -> int:
        for idx, step in enumerate(self.steps):
            if step["key"] == key:
                return idx
        return max(self.active_index, 0)

    @staticmethod
    def _round_num(stage: str) -> int:
        match = _ROUND_RE.search(stage)
        if not match:
            return 0
        try:
            return int(match.group(1))
        except ValueError:
            return 0

    def _percent_for(self, index: int, current: int, total: int, state: str) -> int:
        if state == "idle":
            return 0
        if state == "done":
            return 100
        if self.steps and index >= 0:
            step_pct = int(round(((index + 1) / len(self.steps)) * 100))
        else:
            step_pct = 0
        if total:
            raw_pct = int(round((max(0, current) / max(total, 1)) * 100))
            return max(0, min(99, max(step_pct, raw_pct)))
        return max(0, min(99, step_pct))
