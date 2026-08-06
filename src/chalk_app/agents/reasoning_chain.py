"""
reasoning_chain.py — 结构化推理链模块

将科学推理过程拆解为显式的推理步骤（DAG 有向无环图），
使假设生成"有据可循"，用户可逐步审计推理逻辑。

核心组件：
  - ReasoningStep:  单步推理节点
  - ReasoningChain:  完整推理链（DAG）
  - ReasoningChainAgent: 从文献事实构建推理链的 Agent

管线位置：
  LiteratureAgent → ReasoningChainAgent → HypothesisAgent → ...
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from llm_client import _chat, LLMConfig, _ensure_config, _lang_instruction


# ─────────────────────────────────────────────────────────────
# 数据类
# ─────────────────────────────────────────────────────────────

# 推理步骤类型常量
STEP_OBSERVATION = "observation"   # 观察：从文献/数据中获取的事实
STEP_PRINCIPLE   = "principle"     # 原理：已知的科学理论/定律
STEP_INDUCTION   = "induction"     # 归纳：从多个观察总结规律
STEP_DEDUCTION   = "deduction"     # 演绎：从一般原理推导具体预测
STEP_ABDUCTION   = "abduction"     # 溯因：已知果推测因

STEP_TYPES = [STEP_OBSERVATION, STEP_PRINCIPLE, STEP_INDUCTION, STEP_DEDUCTION, STEP_ABDUCTION]

STEP_TYPE_LABELS = {
    STEP_OBSERVATION: "观察",
    STEP_PRINCIPLE:   "原理",
    STEP_INDUCTION:   "归纳",
    STEP_DEDUCTION:   "演绎",
    STEP_ABDUCTION:   "溯因",
}

STEP_TYPE_COLORS = {
    STEP_OBSERVATION: "#378ADD",   # 蓝
    STEP_PRINCIPLE:   "#722ed1",   # 紫
    STEP_INDUCTION:   "#f39c12",   # 橙
    STEP_DEDUCTION:   "#27ae60",   # 绿
    STEP_ABDUCTION:   "#e74c3c",   # 红
}

LOGIC_TYPES = ["inductive", "deductive", "abductive", "mixed"]

LOGIC_TYPE_LABELS = {
    "inductive":  "归纳推理",
    "deductive":  "演绎推理",
    "abductive":  "溯因推理",
    "mixed":      "混合推理",
}


@dataclass
class ReasoningStep:
    """单步推理节点"""
    step_id: int                        # 步骤编号（1, 2, 3...）
    step_type: str                      # observation | principle | induction | deduction | abduction
    content: str                        # 该步骤的自然语言描述
    evidence: List[str] = field(default_factory=list)   # 支撑该步骤的文献引用/数据
    confidence: float = 0.8             # 置信度 0.0-1.0
    leads_to: List[int] = field(default_factory=list)   # 该步骤推导出哪些后续步骤（step_id 列表）
    reasoning_path: str = ""            # 从上一步到这一步的推理逻辑说明（如"基于Step1的d-band值，代入Sabatier原理"）

    def to_dict(self) -> dict:
        return {
            "step_id": self.step_id,
            "step_type": self.step_type,
            "content": self.content,
            "evidence": self.evidence,
            "confidence": round(self.confidence, 3),
            "leads_to": self.leads_to,
            "reasoning_path": self.reasoning_path,
        }

    @staticmethod
    def from_dict(d: dict) -> "ReasoningStep":
        return ReasoningStep(
            step_id=d.get("step_id", 0),
            step_type=d.get("step_type", "observation"),
            content=d.get("content", ""),
            evidence=d.get("evidence", []),
            confidence=float(d.get("confidence", 0.8)),
            leads_to=d.get("leads_to", []),
            reasoning_path=d.get("reasoning_path", ""),
        )

    def type_label(self) -> str:
        return STEP_TYPE_LABELS.get(self.step_type, self.step_type)

    def type_color(self) -> str:
        return STEP_TYPE_COLORS.get(self.step_type, "#5F5E5A")


@dataclass
class ReasoningChain:
    """完整推理链（DAG 结构）"""
    question: str                       # 待研究问题
    steps: List[ReasoningStep] = field(default_factory=list)
    conclusion: str = ""                # 推理链最终指向的假设方向
    logic_type: str = "mixed"           # inductive | deductive | abductive | mixed
    domain: str = ""                    # 所属领域
    overall_confidence: float = 0.7     # 整条链的综合置信度

    def to_dict(self) -> dict:
        return {
            "question": self.question,
            "steps": [s.to_dict() for s in self.steps],
            "conclusion": self.conclusion,
            "logic_type": self.logic_type,
            "domain": self.domain,
            "overall_confidence": round(self.overall_confidence, 3),
        }

    @staticmethod
    def from_dict(d: dict) -> "ReasoningChain":
        steps = [ReasoningStep.from_dict(s) for s in d.get("steps", [])]
        return ReasoningChain(
            question=d.get("question", ""),
            steps=steps,
            conclusion=d.get("conclusion", ""),
            logic_type=d.get("logic_type", "mixed"),
            domain=d.get("domain", ""),
            overall_confidence=float(d.get("overall_confidence", 0.7)),
        )

    def logic_type_label(self) -> str:
        return LOGIC_TYPE_LABELS.get(self.logic_type, self.logic_type)

    def validate(self) -> List[str]:
        """校验推理链的合理性，返回问题列表（空列表=无问题）"""
        issues = []
        if not self.steps:
            issues.append("推理链为空，至少需要 3 步")
            return issues

        step_ids = {s.step_id for s in self.steps}

        # 检查 step_id 唯一性
        if len(step_ids) != len(self.steps):
            issues.append("存在重复的 step_id")

        # 检查 leads_to 引用的 step_id 是否存在
        for s in self.steps:
            for target in s.leads_to:
                if target not in step_ids:
                    issues.append(f"步骤 {s.step_id} 引用了不存在的步骤 {target}")

        # 检查自环
        for s in self.steps:
            if s.step_id in s.leads_to:
                issues.append(f"步骤 {s.step_id} 存在自环引用")

        # 检查至少包含 3 种步骤类型
        types_used = {s.step_type for s in self.steps}
        if len(types_used) < 2:
            issues.append(f"推理步骤类型过于单一（仅 {types_used}），至少需要 2 种")

        # 检查 confidence 范围
        for s in self.steps:
            if not (0.0 <= s.confidence <= 1.0):
                issues.append(f"步骤 {s.step_id} 的 confidence={s.confidence} 超出 [0,1] 范围")

        return issues

    def to_html(self, teaching_mode: bool = False, interactive: bool = True) -> str:
        """
        生成 HTML 格式的推理链可视化。

        Args:
            teaching_mode: 教学模式下隐藏详细证据，显示推理路径说明，
                          步骤卡片使用简化布局，适合教学演示。
            interactive: 交互模式下每个步骤卡片支持点击展开详细解释、
                         查看证据、追问"为什么"。
        """
        parts = []

        # 标题
        logic_label = self.logic_type_label()
        mode_tag = " 🎓 教学模式" if teaching_mode else ""
        parts.append(
            f"<h3 style='color:#333;'>🧠 推理链分析{mode_tag} "
            f"<span style='font-size:13px;color:#888;font-weight:normal;'>"
            f"[{logic_label}] 置信度 {self.overall_confidence:.0%}</span></h3>"
        )
        parts.append(
            f"<p style='color:#555;font-size:13px;'>"
            f"<b>研究问题:</b> {self.question}</p>"
        )

        # 构建 step_id → step 映射
        step_map = {s.step_id: s for s in self.steps}

        # 找到根节点（没有被其他步骤 leads_to 引用的步骤）
        referenced = set()
        for s in self.steps:
            referenced.update(s.leads_to)
        roots = [s for s in self.steps if s.step_id not in referenced]
        if not roots:
            roots = self.steps[:1]  # fallback

        # 按层级渲染（BFS 遍历 DAG，去重防止多前驱导致重复）
        visited = set()
        layers = []
        current_layer = roots

        while current_layer:
            layers.append(current_layer)
            next_layer_ids = set()
            next_layer_list = []
            for s in current_layer:
                if s.step_id in visited:
                    continue
                visited.add(s.step_id)
                for target_id in s.leads_to:
                    if target_id in step_map and target_id not in visited and target_id not in next_layer_ids:
                        next_layer_ids.add(target_id)
                        next_layer_list.append(step_map[target_id])
            current_layer = next_layer_list

        # 处理未被访问到的孤立步骤
        orphans = [s for s in self.steps if s.step_id not in visited]
        if orphans:
            layers.append(orphans)

        # 渲染每一层
        for layer_idx, layer in enumerate(layers):
            if layer_idx > 0:
                # 层间箭头
                parts.append(
                    "<div style='text-align:center;color:#ccc;font-size:20px;margin:4px 0;'>↓</div>"
                )

            for step in layer:
                color = step.type_color()
                label = step.type_label()
                conf_pct = f"{step.confidence:.0%}"
                conf_color = "#27ae60" if step.confidence >= 0.8 else (
                    "#f39c12" if step.confidence >= 0.6 else "#e74c3c"
                )

                if teaching_mode:
                    # 教学模式：简化卡片 + <details> 折叠（QTextBrowser 兼容）
                    card = (
                        f"<div style='border-left:4px solid {color};"
                        f"background:#fafbfc;border-radius:0 8px 8px 0;"
                        f"padding:8px 12px;margin:4px 0;'>"
                    )
                    card += (
                        f"<div style='display:flex;justify-content:space-between;align-items:center;'>"
                        f"<span style='color:{color};font-weight:600;font-size:12px;'>"
                        f"[{label}]</span>"
                        f"<span style='color:{conf_color};font-size:11px;'>"
                        f"置信度 {conf_pct}</span></div>"
                    )
                    card += (
                        f"<div style='font-size:13px;color:#333;margin-top:3px;"
                        f"line-height:1.5;'>{step.content}</div>"
                    )
                    # 教学模式：直接展示推理路径 + 证据 + 教学提示
                    card += (
                        f"<div style='margin-top:6px;padding:8px;"
                        f"background:#f0f5ff;border-radius:6px;font-size:12px;'>"
                    )
                    if step.reasoning_path:
                        card += (
                            f"<div style='color:#722ed1;margin-bottom:4px;"
                            f"font-weight:500;'>💡 推理路径:</div>"
                            f"<div style='color:#555;padding-left:8px;"
                            f"border-left:2px solid #d3adf7;'>"
                            f"{step.reasoning_path}</div>"
                        )
                    # 教学提示：根据推理类型给出教学引导
                    teaching_hint = self._get_teaching_hint(step)
                    if teaching_hint:
                        card += (
                            f"<div style='margin-top:6px;padding:6px 8px;"
                            f"background:#fff7e6;border-radius:4px;color:#d46b08;'>"
                            f"🎓 {teaching_hint}</div>"
                        )
                    if step.evidence:
                        ev_items = "<br>".join(f"• {ev}" for ev in step.evidence[:3])
                        card += (
                            f"<div style='color:#378ADD;margin-top:4px;'>"
                            f"📄 支撑证据: {ev_items}</div>"
                        )
                    # 教学模式"为什么这样推理"也用 <details> 折叠
                    card += (
                        f"<details style='margin-top:6px;'>"
                        f"<summary style='font-size:11px;color:#722ed1;cursor:pointer;'>"
                        f"🤔 为什么这样推理？</summary>"
                        f"<div style='margin-top:4px;padding:8px;"
                        f"background:#fff7e6;border-radius:6px;color:#d46b08;font-size:12px;'>"
                        f"<b>🎓 教学引导：</b><br>"
                        f"每个推理步骤都遵循从已知到未知的逻辑链条。<br>"
                        f"• <b>观察</b>：从实验数据中直接读取的事实<br>"
                        f"• <b>原理</b>：已被验证的理论或规律<br>"
                        f"• <b>归纳</b>：从多个特例总结一般规律<br>"
                        f"• <b>演绎</b>：从一般原理推导特定结论<br>"
                        f"• <b>溯因</b>：从结果反推最可能的原因<br>"
                        f"请检查此步骤的推理类型和路径，思考其逻辑是否自洽。</div>"
                        f"</details>"
                    )
                    card += "</div></div>"
                else:
                    if interactive:
                        card = (
                            f"<div style='border-left:4px solid {color};"
                            f"background:#fafbfc;border-radius:0 8px 8px 0;"
                            f"padding:10px 14px;margin:6px 0;'>"
                        )
                        card += (
                            f"<div style='display:flex;justify-content:space-between;align-items:center;'>"
                            f"<span style='color:{color};font-weight:600;font-size:13px;'>"
                            f"[Step {step.step_id}] {label}</span>"
                            f"<span style='color:{conf_color};font-size:11px;'>"
                            f"置信度 {conf_pct}</span>"
                            f"</div>"
                        )
                        card += (
                            f"<div style='font-size:13px;color:#333;margin-top:4px;"
                            f"line-height:1.6;'>{step.content}</div>"
                        )
                        # 直接展示详情（无需折叠）
                        card += (
                            f"<div style='margin-top:8px;padding:8px;"
                            f"background:#f0f5ff;border-radius:6px;font-size:12px;'>"
                        )
                        if step.reasoning_path:
                            card += (
                                f"<div style='color:#722ed1;margin-bottom:4px;'>"
                                f"💡 推理路径: {step.reasoning_path}</div>"
                            )
                        card += (
                            f"<div style='color:#666;margin-bottom:4px;'>"
                            f"📊 置信度: {conf_pct}</div>"
                        )
                        if step.evidence:
                            ev_items = "<br>".join(f"• {ev}" for ev in step.evidence)
                            card += (
                                f"<div style='color:#378ADD;'>📄 支撑证据: {ev_items}</div>"
                            )
                        if step.leads_to:
                            targets_str = ", ".join(f"Step {t}" for t in step.leads_to)
                            card += (
                                f"<div style='color:#27ae60;'>→ 指向: {targets_str}</div>"
                            )
                        # "为什么这样推理" 也用 <details>
                        card += (
                            f"<details style='margin-top:6px;'>"
                            f"<summary style='font-size:11px;color:#722ed1;cursor:pointer;'>"
                            f"🤔 为什么这样推理？</summary>"
                            f"<div style='margin-top:4px;padding:8px;"
                            f"background:#fff7e6;border-radius:6px;color:#d46b08;font-size:12px;'>"
                            f"<b>🤔 追问解释：</b><br>"
                            f"此步骤的推理依据来自上游观察/原理，"
                            f"具体逻辑请查看「推理路径」说明。<br>"
                            f"如需更详细解释，请在反馈中指定此步骤编号。</div>"
                            f"</details>"
                        )
                        card += "</div></div>"
                    else:
                        card = (
                            f"<div style='border-left:4px solid {color};"
                            f"background:#fafbfc;border-radius:0 8px 8px 0;"
                            f"padding:10px 14px;margin:6px 0;'>"
                        )
                        card += (
                            f"<div style='display:flex;justify-content:space-between;align-items:center;'>"
                            f"<span style='color:{color};font-weight:600;font-size:13px;'>"
                            f"[Step {step.step_id}] {label}</span>"
                            f"<span style='color:{conf_color};font-size:11px;'>置信度 {conf_pct}</span>"
                            f"</div>"
                        )
                        card += (
                            f"<div style='font-size:13px;color:#333;margin-top:4px;"
                            f"line-height:1.6;'>{step.content}</div>"
                        )
                        if step.reasoning_path:
                            card += (
                                f"<details style='margin-top:4px;' open>"
                                f"<summary style='font-size:11px;color:#722ed1;cursor:pointer;'>"
                                f"💡 推理路径</summary>"
                                f"<div style='font-size:11px;color:#555;padding:3px 0 3px 12px;"
                                f"border-left:2px solid #d3adf7;'>"
                                f"{step.reasoning_path}</div>"
                                f"</details>"
                            )
                        if step.evidence:
                            card += (
                                f"<details style='margin-top:6px;'>"
                                f"<summary style='font-size:11px;color:#888;cursor:pointer;'>"
                                f"支撑证据 ({len(step.evidence)} 条)</summary>"
                            )
                            for ev in step.evidence:
                                card += (
                                    f"<div style='font-size:11px;color:#666;"
                                    f"padding:2px 0 2px 12px;border-left:2px solid #ddd;'>"
                                    f"{ev}</div>"
                                )
                            card += "</details>"
                        if step.leads_to:
                            targets_str = ", ".join(f"Step {t}" for t in step.leads_to)
                            card += (
                                f"<div style='font-size:11px;color:#aaa;margin-top:4px;'>"
                                f"→ 指向: {targets_str}</div>"
                            )
                        card += "</div>"

                parts.append(card)

        # 结论
        if self.conclusion:
            parts.append(
                f"<div style='margin-top:12px;padding:10px 14px;"
                f"background:#e8f5e9;border-left:4px solid #27ae60;"
                f"border-radius:0 8px 8px 0;'>"
                f"<b style='color:#27ae60;'>📝 推理结论:</b> "
                f"<span style='font-size:13px;'>{self.conclusion}</span></div>"
            )

        return "\n".join(parts)

    @staticmethod
    def _get_teaching_hint(step: 'ReasoningStep') -> str:
        """根据推理步骤类型生成教学引导提示"""
        hints = {
            STEP_OBSERVATION: (
                "这是一个实验观察——注意检查数据来源和测量条件，"
                "思考是否有其他可能的解读方式"
            ),
            STEP_PRINCIPLE: (
                "这是一个理论原理——它是经过大量验证的规律，"
                "注意它的适用范围和边界条件"
            ),
            STEP_INDUCTION: (
                "这是归纳推理——从特例总结一般规律，"
                "注意样本是否足够、是否有反例"
            ),
            STEP_DEDUCTION: (
                "这是演绎推理——从一般原理推导特定结论，"
                "检查前提条件是否在当前场景下成立"
            ),
            STEP_ABDUCTION: (
                "这是溯因推理——从结果反推最可能的原因，"
                "注意是否还有其他同样合理的解释"
            ),
        }
        return hints.get(step.step_type, "")

    def to_context_text(self) -> str:
        """生成可注入假设生成上下文的文本"""
        if not self.steps:
            return ""
        lines = ["=== 推理链分析 ===", ""]
        lines.append(f"研究问题: {self.question}")
        lines.append(f"推理类型: {self.logic_type_label()}")
        lines.append(f"综合置信度: {self.overall_confidence:.0%}")
        lines.append("")
        for s in self.steps:
            label = s.type_label()
            lines.append(f"  Step {s.step_id} [{label}]: {s.content}")
            if s.reasoning_path:
                lines.append(f"    推理路径: {s.reasoning_path}")
            if s.evidence:
                lines.append(f"    证据: {'; '.join(s.evidence[:3])}")
            if s.leads_to:
                lines.append(f"    → 指向 Step {', '.join(str(t) for t in s.leads_to)}")
            lines.append("")
        lines.append(f"推理结论: {self.conclusion}")
        lines.append("")
        lines.append("以上推理链基于文献事实分析，假设生成请基于此推理链展开。")
        return "\n".join(lines)


# ─────────────────────────────────────────────────────────────
# 推理链 Agent
# ─────────────────────────────────────────────────────────────

class ReasoningChainAgent:
    """
    从文献事实中构建显式推理链。

    管线位置：LiteratureAgent → ReasoningChainAgent → HypothesisAgent

    输入：文献事实摘要（LiteratureAgent 输出的 JSON 字符串）
    输出：ReasoningChain JSON（供 HypothesisAgent 消费）
    """

    # 使用 qwen3.8-max 保证推理质量
    model_task: str = "compare"
    timeout: int = 150

    def __init__(self, config: Optional[LLMConfig] = None):
        self.config = _ensure_config(config)

    def _call_llm(self, prompt: str, timeout: Optional[int] = None) -> str:
        return _chat(
            prompt,
            self.config,
            task=self.model_task,
            timeout=timeout or self.timeout,
        )

    def process(self, literature_facts: str, domain: str = "") -> ReasoningChain:
        """
        从文献事实构建推理链。

        Args:
            literature_facts: LiteratureAgent 输出的 JSON 字符串
            domain: 领域标识（可选，辅助 prompt 引导）

        Returns:
            ReasoningChain
        """
        domain_hint = ""
        if domain:
            domain_hint = f"\n已知该文献属于【{domain}】领域，请在推理中结合该领域的先验知识。"

        lang_inst = "请务必全部使用中文输出。"

        # 动态内容部分（含 f-string）
        dynamic_part = (
            f"【文献事实摘要】:\n{literature_facts}\n"
            f"{domain_hint}\n"
            f"{lang_inst}\n\n"
        )

        # 静态模板部分（纯字符串，避免大括号问题）
        static_part = (
            "你是一名逻辑分析专家，擅长将科学推理过程拆解为显式的推理链。\n"
            "你的任务是从文献事实中构建一条结构化推理链，从观察到原理，从原理到假设方向。\n\n"
            + dynamic_part +
            "请严格以 JSON 格式输出，不要加 markdown 代码块，直接返回 JSON 对象：\n"
            "{\n"
            '  "question": "从文献事实中提炼的核心研究问题",\n'
            '  "logic_type": "inductive|deductive|abductive|mixed",\n'
            '  "domain": "electrocatalysis|photocatalysis|battery_materials|other",\n'
            '  "steps": [\n'
            '    {\n'
            '      "step_id": 1,\n'
            '      "step_type": "observation",\n'
            '      "content": "观察到的事实描述",\n'
            '      "evidence": ["支撑的文献片段或数据1", "片段2"],\n'
            '      "confidence": 0.9,\n'
            '      "leads_to": [2, 3],\n'
            '      "reasoning_path": ""\n'
            '    },\n'
            '    {\n'
            '      "step_id": 2,\n'
            '      "step_type": "principle",\n'
            '      "content": "从观察中归纳出的科学原理",\n'
            '      "evidence": ["理论依据或文献引用"],\n'
            '      "confidence": 0.85,\n'
            '      "leads_to": [4],\n'
            '      "reasoning_path": "基于Step1的观察，应用d-band理论得出..."\n'
            '    },\n'
            '    {\n'
            '      "step_id": 3,\n'
            '      "step_type": "induction",\n'
            '      "content": "从多个观察中总结的规律",\n'
            '      "evidence": ["归纳依据"],\n'
            '      "confidence": 0.75,\n'
            '      "leads_to": [4],\n'
            '      "reasoning_path": "从Step1的多个数据点归纳出..."\n'
            '    },\n'
            '    {\n'
            '      "step_id": 4,\n'
            '      "step_type": "deduction",\n'
            '      "content": "从原理/归纳推导出的具体预测",\n'
            '      "evidence": ["推导前提"],\n'
            '      "confidence": 0.7,\n'
            '      "leads_to": [],\n'
            '      "reasoning_path": "将Step2的原理与Step3的规律结合，推导出..."\n'
            '    }\n'
            '  ],\n'
            '  "conclusion": "推理链最终指向的假设方向（必须是可验证的具体假设，不是需要进一步研究）",\n'
            '  "overall_confidence": 0.75\n'
            "}\n\n"
            "严格规则：\n"
            "1. steps 必须至少包含 3 步，且至少覆盖 observation + principle + (induction 或 deduction) 三种类型\n"
            "2. step_type 只能是: observation, principle, induction, deduction, abduction\n"
            "3. leads_to 构成有向无环图（DAG）：step_id 不能出现在自己的 leads_to 中\n"
            "4. confidence 基于证据充分程度：文献直接陈述 > 0.85，间接推理 0.7-0.85，推测 < 0.7\n"
            "5. evidence 必须引用原文内容或已知科学理论，不能编造\n"
            "6. conclusion 必须是可验证的假设方向，不是模糊的描述\n"
            "7. overall_confidence 是所有步骤置信度的加权平均\n"
            "8. 如果推理过程中使用了溯因推理（从结果反推原因），标记为 abduction 类型\n"
            "9. logic_type 反映推理链的主要逻辑形式：\n"
            "   - inductive: 从多个观察总结规律\n"
            "   - deductive: 从一般原理推导具体预测\n"
            "   - abductive: 从已知结果推测原因\n"
            "   - mixed: 包含多种逻辑形式\n"
            "10. reasoning_path 描述从上一步到当前步骤的推理逻辑链路，格式如\"基于Step X的观察，应用Y理论得出...\"。\n"
            "    第一个步骤（根节点）的 reasoning_path 为空字符串，后续步骤必须填写。\n"
        )

        prompt = static_part

        result = self._call_llm(prompt, timeout=150)
        chain = self._parse_chain(result)

        # 校验
        issues = chain.validate()
        if issues:
            import logging
            logger = logging.getLogger(__name__)
            logger.warning(f"推理链校验问题: {issues}")

        return chain

    @staticmethod
    def _parse_chain(raw: str) -> ReasoningChain:
        """容错解析 LLM 输出为 ReasoningChain"""
        text = raw.strip()

        # 去掉 markdown 代码块
        if text.startswith("```"):
            lines = text.split("\n")
            lines = [l for l in lines if not l.strip().startswith("```")]
            text = "\n".join(lines)

        # 尝试直接 JSON 解析
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            # 尝试提取 { ... }
            start = text.find("{")
            end = text.rfind("}")
            if start != -1 and end != -1 and end > start:
                try:
                    data = json.loads(text[start:end + 1])
                except json.JSONDecodeError:
                    data = {"raw_text": text, "parse_error": True}
            else:
                data = {"raw_text": text, "parse_error": True}

        # 如果解析失败，返回空链
        if data.get("parse_error"):
            return ReasoningChain(
                question="解析失败",
                steps=[],
                conclusion="LLM 输出无法解析为推理链",
                logic_type="mixed",
                domain="",
                overall_confidence=0.0,
            )

        return ReasoningChain.from_dict(data)


# ─────────────────────────────────────────────────────────────
# 便捷函数
# ─────────────────────────────────────────────────────────────

def build_reasoning_chain(
    literature_facts: str,
    domain: str = "",
    config: Optional[LLMConfig] = None,
) -> ReasoningChain:
    """
    一键构建推理链。

    Args:
        literature_facts: LiteratureAgent 输出的 JSON 字符串
        domain: 领域标识（可选）
        config: LLM 配置

    Returns:
        ReasoningChain
    """
    agent = ReasoningChainAgent(config=config)
    return agent.process(literature_facts, domain=domain)


# ─────────────────────────────────────────────────────────────
# 辩论可视化渲染
# ─────────────────────────────────────────────────────────────

def debate_to_html(debate_history: list) -> str:
    """
    将多角色辩论历史渲染为 HTML 对比卡片。

    辩论格式（来自 summarize_debate()）:
      [
        {
          "round": 1,
          "devil_advocate": {"attack_points": [...]},
          "optimist": {"defense_points": [...]},
          "summary": {
            "verdict": "balanced" | "favor_hypothesis" | "favor_rejection",
            "key_insights": [...],
            "recommendation": "..."
          }
        },
        ...
      ]

    Args:
        debate_history: 辩论历史列表

    Returns:
        HTML 格式的辩论可视化
    """
    if not debate_history:
        return "<p style='color:#888;font-size:13px;'>暂无辩论记录</p>"

    parts = []
    parts.append(
        "<h3 style='color:#333;'>⚖️ 多角色辩论记录</h3>"
    )

    for round_data in debate_history:
        round_num = round_data.get("round", "?")

        parts.append(
            f"<div style='margin:12px 0;padding:8px 12px;"
            f"background:#f8f9fa;border-radius:8px;'>"
        )
        parts.append(
            f"<h4 style='color:#555;margin:0 0 8px 0;'>🔄 第 {round_num} 轮辩论</h4>"
        )

        # ── 反方攻击 ──
        # 兼容两种结构：嵌套 devil_advocate.attack_points 或扁平 devil_attack_points
        devil = round_data.get("devil_advocate", {})
        attacks = devil.get("attack_points", []) or round_data.get("devil_attack_points", [])
        # 错误状态展示
        if round_data.get("error"):
            parts.append(
                f"<div style='margin:8px 0;padding:8px 12px;"
                f"background:#fff1f0;border-left:3px solid #f5222d;border-radius:4px;'>"
                f"<span style='color:#f5222d;font-weight:600;'>⚠️ 辩论生成失败</span>"
                f"<div style='font-size:11px;color:#666;margin-top:4px;'>"
                f"{round_data.get('error_message', '未知错误')}</div></div>"
            )
        if attacks:
            parts.append(
                "<div style='margin-bottom:8px;'>"
                "<div style='color:#e74c3c;font-weight:600;font-size:12px;margin-bottom:4px;'>"
                "😈 魔鬼代言人 — 攻击</div>"
            )
            for atk in attacks:
                severity = atk.get("severity", "medium")
                sev_color = "#e74c3c" if severity == "high" else (
                    "#f39c12" if severity == "medium" else "#95a5a6"
                )
                target = atk.get("target", "")
                evidence = atk.get("evidence", "")
                if_wrong = atk.get("if_wrong", "")

                parts.append(
                    f"<div style='border-left:3px solid {sev_color};"
                    f"padding:4px 8px;margin:4px 0 4px 8px;background:#fff5f5;"
                    f"border-radius:0 4px 4px 0;'>"
                )
                parts.append(
                    f"<div style='font-size:12px;color:#333;'>"
                    f"<span style='color:{sev_color};font-weight:600;'>"
                    f"[{severity.upper()}]</span> {target}</div>"
                )
                if evidence:
                    parts.append(
                        f"<div style='font-size:11px;color:#666;margin-top:2px;'>"
                        f"证据: {evidence[:200]}{'...' if len(evidence) > 200 else ''}</div>"
                    )
                if if_wrong:
                    parts.append(
                        f"<div style='font-size:11px;color:#888;margin-top:2px;'>"
                        f"如果假设错误: {if_wrong[:150]}{'...' if len(if_wrong) > 150 else ''}</div>"
                    )
                parts.append("</div>")
            parts.append("</div>")

        # ── 正方辩护 ──
        # 兼容两种结构：嵌套 optimist.defense_points 或扁平 optimist_defense_points
        optimist_data = round_data.get("optimist", {})
        defenses = optimist_data.get("defense_points", []) or round_data.get("optimist_defense_points", [])
        if defenses:
            parts.append(
                "<div style='margin-bottom:8px;'>"
                "<div style='color:#27ae60;font-weight:600;font-size:12px;margin-bottom:4px;'>"
                "😇 乐观派 — 辩护</div>"
            )
            for dfn in defenses:
                counters = dfn.get("counters_attack", "")
                evidence = dfn.get("evidence", "")
                validity = dfn.get("validity_range", "")
                concession = dfn.get("concession", "")

                parts.append(
                    "<div style='border-left:3px solid #27ae60;"
                    "padding:4px 8px;margin:4px 0 4px 8px;background:#f0fff0;"
                    "border-radius:0 4px 4px 0;'>"
                )
                if counters:
                    parts.append(
                        f"<div style='font-size:12px;color:#333;'>反击: {counters[:200]}"
                        f"{'...' if len(counters) > 200 else ''}</div>"
                    )
                if evidence:
                    parts.append(
                        f"<div style='font-size:11px;color:#666;margin-top:2px;'>"
                        f"证据: {evidence[:150]}{'...' if len(evidence) > 150 else ''}</div>"
                    )
                if validity:
                    parts.append(
                        f"<div style='font-size:11px;color:#888;margin-top:2px;'>"
                        f"有效范围: {validity[:120]}</div>"
                    )
                if concession:
                    parts.append(
                        f"<div style='font-size:11px;color:#f39c12;margin-top:2px;'>"
                        f"让步: {concession[:120]}</div>"
                    )
                parts.append("</div>")

            # 关键实验建议
            key_exp = optimist_data.get("key_experiment", "") or round_data.get("key_experiment", "")
            if key_exp:
                parts.append(
                    f"<div style='margin:4px 0 4px 8px;padding:4px 8px;"
                    f"background:#e3f2fd;border-radius:4px;font-size:11px;color:#1565c0;'>"
                    f"🧪 建议实验: {key_exp[:200]}{'...' if len(key_exp) > 200 else ''}</div>"
                )
            parts.append("</div>")

        # ── 平衡判定 ──
        # 兼容两种结构：嵌套 summary.verdict 或扁平 balance/recommendation/key_insights
        summary = round_data.get("summary", {})
        if not summary or not isinstance(summary, dict) or not summary.get("verdict"):
            # 从扁平结构构建 summary
            summary = {
                "verdict": round_data.get("balance", round_data.get("devil_verdict", "balanced")),
                "key_insights": round_data.get("key_insights", []),
                "recommendation": round_data.get("recommendation", ""),
            }
        if summary:
            verdict = summary.get("verdict", "balanced")
            verdict_labels = {
                "favor_hypothesis": ("支持假设", "#27ae60"),
                "favor_rejection": ("倾向否定", "#e74c3c"),
                "balanced": ("暂无定论", "#f39c12"),
            }
            v_label, v_color = verdict_labels.get(verdict, ("暂无定论", "#95a5a6"))

            parts.append(
                f"<div style='margin-top:6px;padding:6px 10px;"
                f"background:#fff;border:1px solid #ddd;border-radius:4px;'>"
            )
            parts.append(
                f"<span style='color:{v_color};font-weight:600;font-size:12px;'>"
                f"⚖️ 判定: {v_label}</span>"
            )

            insights = summary.get("key_insights", [])
            if insights:
                for ins in insights:
                    # key_insights 可能是 dict 或 str
                    if isinstance(ins, dict):
                        ins_text = ins.get("issue", str(ins))
                    else:
                        ins_text = str(ins)
                    parts.append(
                        f"<div style='font-size:11px;color:#555;margin-top:2px;'>"
                        f"• {ins_text[:200]}{'...' if len(ins_text) > 200 else ''}</div>"
                    )

            recommendation = summary.get("recommendation", "")
            if recommendation:
                parts.append(
                    f"<div style='font-size:11px;color:#722ed1;margin-top:4px;'>"
                    f"💡 建议: {recommendation[:200]}{'...' if len(recommendation) > 200 else ''}</div>"
                )
            parts.append("</div>")

        parts.append("</div>")  # round box

    return "\n".join(parts)
