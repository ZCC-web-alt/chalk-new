"""
Multi-Agent 科学假设生成框架

基于阿里云百炼平台千问系列模型，实现多智能体协作的科学假设自动生成。
Agent 架构：
  LiteratureAgent          → 文献理解与事实提取
  ReasoningChainAgent     → 推理链构建（观察→原理→归纳→演绎→假设）
  HypothesisAgent          → 假设生成
  CritiqueAgent            → 假设思辨与质疑
  RevisionAgent            → 假设修订与完善
  ValidationAgent          → 可验证性评估
  ResultsVerificationAgent → 公式推导与实验结果验证
  OutputAgent              → 标准化格式输出

编排模式：Pipeline + Debate Loop
  文献 → 推理链 → [假设生成 → 思辨 → 修订] × N轮 → 可验证性评估 → 公式推导验证 → 标准化输出
"""

import json
import os
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Callable

from llm_client import (
    LLM_ERROR_PREFIX,
    _chat,
    LLMConfig,
    _ensure_config,
    _lang_instruction,
)
from hitl_trace import build_field_diff, summarize_diff
from prompts.domain_hypothesis import (
    apply_domain_output_defaults,
    domain_guidance,
    domain_label,
    domain_output_contract,
)


# ─────────────────────────────────────────────────────────────
# 标准化假设输出 Schema（对应比赛要求的字段）
# ─────────────────────────────────────────────────────────────

HYPOTHESIS_OUTPUT_SCHEMA = {
    "problem_statement": "当前领域存在的具体局限性与待研究问题",
    "rationale": "基于逻辑推理的创新点阐述与推导链条",
    "technical_details": "验证假设所需的技术栈（方法/工具/参数等）",
    "datasets": {
        "source": "假设推演依据的历史/文献数据",
        "target": "验证实验所需的拟采集数据特征",
    },
    "paper_title": "符合学术规范的论文标题",
    "paper_abstract": "包含背景、方法、预期结果的完整摘要",
    "methods": "具体实施步骤（模型架构或实验流程）",
    "experiments": {
        "baselines": "基线方法列表",
        "metrics": "评估指标列表",
        "design": "实验设计描述",
    },
    "expected_results": "通过公式推导或理论分析预期的实验结果",
    "results": {
        "verification_method": "公式推导 | 文献对比 | 半经验估算",
        "derivation": "完整的公式推导过程（逐步展示推导链）",
        "calculated_values": [
            {"parameter": "参数名", "value": "计算值", "derivation": "推导依据"}
        ],
        "comparison_with_literature": [
            {"metric": "指标名", "predicted": "预测值", "literature": "文献值", "deviation": "偏差"}
        ],
        "feasibility_conclusion": "基于推导验证的可行性结论",
    },
    "references": "引用的真实文献列表（严禁虚构）",
    "structured_extraction_table": "多文献结构化提取表",
    "database_schema": "自建数据库字段建议",
    "literature_network": "文献关系网络文字版",
    "paper_titles": "3个英文学术标题候选",
    "experiment_record_card": "可直接进入实验记录的假设卡片",
    "reference_status": "引用真实性与缺失全文核验状态",
    "confidence": "假设置信度评分（1-10）",
    "feasibility": "可行性评估（高/中/低）",
}


# Hypothesis generation is intentionally slow because it may need to read
# multiple papers, run critique loops, and produce a fully formatted report.
# Keep long Qwen calls open for 25 minutes before surfacing a timeout.
HYPOTHESIS_LLM_TIMEOUT_SECONDS = 25 * 60


def _database_evidence_ids_from_snapshot(snapshot: Dict) -> List[str]:
    """Return compact LE/DE/CE ids for report and lab-record traceability."""
    if not isinstance(snapshot, dict):
        return []
    specs = [
        ("literature_evidence", "LE"),
        ("domain_evidence", "DE"),
        ("computational_catalysis_evidence", "CE"),
    ]
    ids = []
    seen = set()
    for key, prefix in specs:
        items = snapshot.get(key, [])
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict) or item.get("id") in (None, ""):
                continue
            evidence_id = f"{prefix}-{item.get('id')}"
            if evidence_id not in seen:
                seen.add(evidence_id)
                ids.append(evidence_id)
    return ids[:18]


def _attach_database_evidence_ids(data: Dict, snapshot: Dict) -> None:
    ids = _database_evidence_ids_from_snapshot(snapshot)
    if not ids:
        return
    record = data.get("experiment_record_card")
    if not isinstance(record, dict):
        record = {}
    if not record.get("database_evidence_ids"):
        record["database_evidence_ids"] = ids
    data["experiment_record_card"] = record


CONFIDENCE_SCORE_RUBRIC = [
    ("evidence_relevance", "证据相关性", 25),
    ("computational_evidence_coverage", "计算证据覆盖", 20),
    ("citation_auditability", "引用可审计性", 15),
    ("mechanistic_consistency", "机理一致性", 15),
    ("data_extraction_quality", "数据抽取质量", 10),
    ("tool_cross_validation", "工具交叉验证", 10),
    ("uncertainty_transparency", "不确定性透明度", 5),
]

FEASIBILITY_SCORE_RUBRIC = [
    ("synthesis_feasibility", "合成可行性", 20),
    ("experimental_protocol_maturity", "实验协议成熟度", 20),
    ("characterization_feasibility", "表征可行性", 15),
    ("computational_feasibility", "计算可行性", 15),
    ("stability_risk", "稳定性风险", 10),
    ("resource_time_cost", "资源周期成本", 10),
    ("validation_discriminability", "验证区分度", 10),
]


def _score_level(score: float, max_score: float) -> str:
    pct = score / max_score if max_score else 0
    if pct >= 0.8:
        return "high"
    if pct >= 0.55:
        return "medium"
    return "low"


def _collect_evidence_ids_from_snapshot(snapshot: Any) -> Dict[str, List[str]]:
    ids = {"LE": [], "DE": [], "CE": []}
    if not isinstance(snapshot, dict):
        return ids
    for key, prefix in (
        ("literature_evidence", "LE"),
        ("domain_evidence", "DE"),
        ("computational_catalysis_evidence", "CE"),
    ):
        items = snapshot.get(key, [])
        if not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, dict) and item.get("id") not in (None, ""):
                evidence_id = f"{prefix}-{item.get('id')}"
                if evidence_id not in ids[prefix]:
                    ids[prefix].append(evidence_id)
    sets = snapshot.get("adsorbate_energy_sets", [])
    if isinstance(sets, list):
        for item in sets:
            if not isinstance(item, dict):
                continue
            for evidence_id in item.get("evidence_ids", []) if isinstance(item.get("evidence_ids"), list) else []:
                if str(evidence_id).startswith("CE-") and evidence_id not in ids["CE"]:
                    ids["CE"].append(evidence_id)
    return ids


def _extract_audit_evidence_ids(audit: Any) -> List[str]:
    out: List[str] = []
    if not isinstance(audit, dict):
        return out
    claims = audit.get("claims", [])
    if not isinstance(claims, list):
        return out
    for claim in claims:
        if not isinstance(claim, dict):
            continue
        evidence_ids = claim.get("evidence_ids", [])
        if not isinstance(evidence_ids, list):
            evidence_ids = [evidence_ids]
        for evidence_id in evidence_ids:
            value = str(evidence_id or "").strip()
            if value and value not in out:
                out.append(value)
    return out


def _has_complete_adsorbate_set(snapshot: Any) -> bool:
    if not isinstance(snapshot, dict):
        return False
    sets = snapshot.get("adsorbate_energy_sets", [])
    if not isinstance(sets, list):
        return False
    return any(isinstance(item, dict) and item.get("coverage", {}).get("complete") for item in sets)


def _missing_adsorbate_messages(snapshot: Any) -> List[str]:
    messages: List[str] = []
    if not isinstance(snapshot, dict):
        return messages
    for item in snapshot.get("adsorbate_energy_sets", []) if isinstance(snapshot.get("adsorbate_energy_sets"), list) else []:
        if not isinstance(item, dict):
            continue
        coverage = item.get("coverage", {}) if isinstance(item.get("coverage"), dict) else {}
        missing = coverage.get("missing_adsorbates", [])
        if missing:
            messages.append("缺少同一位点吸附能: " + ", ".join(str(x) for x in missing))
    for warning in snapshot.get("warnings", []) if isinstance(snapshot.get("warnings"), list) else []:
        if warning:
            messages.append(str(warning))
    return list(dict.fromkeys(messages))[:4]


def build_score_breakdown(
    hypothesis_data: Dict,
    *,
    database_evidence: Optional[Dict] = None,
    scientific_toolkit: Optional[Dict] = None,
    qwen_agent_context: Optional[Dict] = None,
    verifiability_result: Optional[Dict] = None,
) -> Dict[str, Any]:
    """Build auditable confidence/feasibility scoring details for reports."""
    if not isinstance(hypothesis_data, dict):
        hypothesis_data = {}
    existing = hypothesis_data.get("score_breakdown")
    if isinstance(existing, dict) and existing.get("confidence") and existing.get("feasibility"):
        return existing

    evidence_ids = _collect_evidence_ids_from_snapshot(database_evidence or hypothesis_data.get("_database_evidence_context", {}))
    all_ids = evidence_ids["LE"] + evidence_ids["DE"] + evidence_ids["CE"]
    refs = hypothesis_data.get("references", [])
    refs = refs if isinstance(refs, list) else []
    results = hypothesis_data.get("results", {})
    results = results if isinstance(results, dict) else {}
    comparison = results.get("comparison_with_literature", [])
    comparison = comparison if isinstance(comparison, list) else []
    methods_text = str(hypothesis_data.get("methods", "")) + "\n" + str(hypothesis_data.get("technical_details", ""))
    rationale_text = str(hypothesis_data.get("rationale", "")) + "\n" + str(hypothesis_data.get("problem_statement", ""))
    toolkit = scientific_toolkit or hypothesis_data.get("_scientific_toolkit", {})
    toolkit_summary = toolkit.get("summary", {}) if isinstance(toolkit, dict) and isinstance(toolkit.get("summary"), dict) else {}
    qwen_context = qwen_agent_context or hypothesis_data.get("_qwen_agent_tool_context", {})
    audit_ids = _extract_audit_evidence_ids(qwen_context.get("qwen_agent_audit", {}) if isinstance(qwen_context, dict) else {})
    complete_ads = _has_complete_adsorbate_set(database_evidence or hypothesis_data.get("_database_evidence_context", {}))
    missing_ads = _missing_adsorbate_messages(database_evidence or hypothesis_data.get("_database_evidence_context", {}))

    confidence_dims = []
    confidence_specs = {
        "evidence_relevance": (
            min(25, 8 + 3 * len(evidence_ids["LE"]) + 2 * len(evidence_ids["DE"]) + 2 * len(evidence_ids["CE"])),
            all_ids[:8],
            [] if all_ids else ["缺少 LE/DE/CE 证据 ID"],
            "Evidence relevance is estimated from matched local evidence IDs.",
        ),
        "computational_evidence_coverage": (
            20 if complete_ads else (12 if evidence_ids["CE"] else 4),
            evidence_ids["CE"][:8],
            missing_ads or ([] if evidence_ids["CE"] else ["缺少计算催化吸附能证据"]),
            "Coverage favors same-site adsorbate energy sets for target intermediates.",
        ),
        "citation_auditability": (
            min(15, 5 + 2 * len(refs) + len(evidence_ids["LE"])),
            evidence_ids["LE"][:8],
            [] if refs or evidence_ids["LE"] else ["缺少可追踪参考文献或 DOI"],
            "Citation auditability uses accepted references and literature evidence.",
        ),
        "mechanistic_consistency": (
            13 if any(token in rationale_text for token in ["*OOH", "*O", "*OH", "ORR", "OER", "HER", "CO2RR", "NRR", "机理"]) else 8,
            evidence_ids["CE"][:5] + evidence_ids["DE"][:3],
            [] if rationale_text else ["缺少机理说明文本"],
            "Mechanistic consistency checks whether the hypothesis names reaction/intermediate logic.",
        ),
        "data_extraction_quality": (
            10 if hypothesis_data.get("structured_extraction_table") or hypothesis_data.get("_multimodal_evidence") else 5,
            all_ids[:5],
            [] if hypothesis_data.get("structured_extraction_table") else ["缺少结构化文献抽取表"],
            "Data extraction quality rewards structured literature/multimodal evidence.",
        ),
        "tool_cross_validation": (
            min(10, 3 + int(toolkit_summary.get("molecules_checked", 0)) + int(toolkit_summary.get("materials_checked", 0)) + len(audit_ids)),
            audit_ids[:8],
            [] if toolkit or audit_ids else ["缺少科学工具或 Qwen-Agent 审计记录"],
            "Tool cross-validation uses RDKit/pymatgen/atomate2 and Qwen-Agent evidence audit traces.",
        ),
        "uncertainty_transparency": (
            5 if hypothesis_data.get("reference_status") or missing_ads or "不确定" in rationale_text or "风险" in methods_text else 3,
            all_ids[:4],
            [] if missing_ads or hypothesis_data.get("reference_status") else ["建议显式列出证据缺口和不确定性"],
            "Uncertainty transparency rewards explicit evidence gaps and boundary notes.",
        ),
    }
    for dim_id, label, max_score in CONFIDENCE_SCORE_RUBRIC:
        score, ids, penalties, rationale = confidence_specs[dim_id]
        score = max(0, min(max_score, float(score)))
        confidence_dims.append(
            {
                "id": dim_id,
                "label": label,
                "score": round(score, 1),
                "max_score": max_score,
                "level": _score_level(score, max_score),
                "rationale": rationale,
                "evidence_ids": ids,
                "penalties": penalties,
                "improvement_suggestions": [] if score >= max_score * 0.8 else ["补充目标材料、目标吸附物和同位点证据链。"],
            }
        )

    common_protocols = ["RRDE", "LSV", "Tafel", "XRD", "XPS", "SEM", "TEM", "Raman", "DFT", "chrono", "stability"]
    protocol_hits = sum(1 for token in common_protocols if token.lower() in methods_text.lower())
    feasibility_raw = str(hypothesis_data.get("feasibility", ""))
    base_feas = 16 if feasibility_raw in {"高", "楂?", "high"} else (11 if feasibility_raw in {"中", "涓?", "medium"} else 7)
    verifiability_score = 0
    if isinstance(verifiability_result, dict):
        try:
            verifiability_score = float(verifiability_result.get("verifiability_score", 0))
        except Exception:
            verifiability_score = 0
    feasibility_specs = {
        "synthesis_feasibility": (
            min(20, base_feas + (2 if "合成" in methods_text or "synthesis" in methods_text.lower() else 0)),
            evidence_ids["DE"][:5],
            [] if methods_text else ["缺少合成路线说明"],
            "Synthesis feasibility uses stated routes, controls and feasibility field.",
        ),
        "experimental_protocol_maturity": (
            min(20, 6 + protocol_hits * 2),
            evidence_ids["DE"][:6],
            [] if protocol_hits >= 3 else ["实验协议指标不足，如 RRDE/LSV/Tafel/稳定性窗口"],
            "Protocol maturity counts mature electrochemical and characterization protocols.",
        ),
        "characterization_feasibility": (
            min(15, 5 + sum(1 for token in ["XRD", "XPS", "SEM", "TEM", "Raman", "FTIR", "BET"] if token.lower() in methods_text.lower()) * 2),
            evidence_ids["DE"][:5],
            [] if any(token.lower() in methods_text.lower() for token in ["xrd", "xps", "sem", "tem", "raman"]) else ["缺少表征方案"],
            "Characterization feasibility rewards standard structural/surface tools.",
        ),
        "computational_feasibility": (
            min(15, 7 + (4 if "DFT" in methods_text else 0) + (4 if evidence_ids["CE"] else 0)),
            evidence_ids["CE"][:6],
            [] if evidence_ids["CE"] or "DFT" in methods_text else ["缺少 DFT/OCP 计算支撑"],
            "Computational feasibility uses DFT plan and local computational evidence availability.",
        ),
        "stability_risk": (
            8 if "stability" in methods_text.lower() or "稳定" in methods_text else 5,
            evidence_ids["DE"][:5],
            [] if "stability" in methods_text.lower() or "稳定" in methods_text else ["缺少长期稳定性或降解风险窗口"],
            "Stability risk improves when explicit stability tests are planned.",
        ),
        "resource_time_cost": (
            7 if "原位" in methods_text or "同步辐射" in methods_text else 9,
            [],
            ["可能存在高成本表征/计算资源"] if "原位" in methods_text or "同步辐射" in methods_text else [],
            "Resource score penalizes high-cost or long-cycle methods.",
        ),
        "validation_discriminability": (
            10 if comparison or hypothesis_data.get("experiments") else 6,
            all_ids[:6],
            [] if comparison or hypothesis_data.get("experiments") else ["缺少可区分假设与对照的验证指标"],
            "Validation discriminability rewards explicit baselines, metrics and literature comparisons.",
        ),
    }
    feasibility_dims = []
    for dim_id, label, max_score in FEASIBILITY_SCORE_RUBRIC:
        score, ids, penalties, rationale = feasibility_specs[dim_id]
        if verifiability_score:
            score = min(max_score, score + max(0, verifiability_score - 70) / 30)
        score = max(0, min(max_score, float(score)))
        feasibility_dims.append(
            {
                "id": dim_id,
                "label": label,
                "score": round(score, 1),
                "max_score": max_score,
                "level": _score_level(score, max_score),
                "rationale": rationale,
                "evidence_ids": ids,
                "penalties": penalties,
                "improvement_suggestions": [] if score >= max_score * 0.8 else ["细化实验步骤、对照组、稳定性和资源周期。"],
            }
        )

    return {
        "confidence": {
            "total_score": round(sum(item["score"] for item in confidence_dims), 1),
            "max_score": 100,
            "dimensions": confidence_dims,
        },
        "feasibility": {
            "total_score": round(sum(item["score"] for item in feasibility_dims), 1),
            "max_score": 100,
            "dimensions": feasibility_dims,
        },
    }


def format_structured_multimodal_evidence_for_query(evidence: Optional[Dict]) -> str:
    """Return compact structured multimodal evidence for database facet extraction."""
    if not isinstance(evidence, dict):
        return ""
    lines: List[str] = []
    for fig in evidence.get("figures", [])[:8]:
        if not isinstance(fig, dict):
            continue
        header = " ".join(
            str(part)
            for part in [fig.get("source_label", ""), fig.get("image_type", "")]
            if part
        ).strip()
        if header:
            lines.append(f"figure: {header}")
        points = fig.get("points", []) if isinstance(fig.get("points", []), list) else []
        if not points:
            lines.append("未提取到可用定量数据")
            continue
        for point in points[:8]:
            if not isinstance(point, dict):
                continue
            parameter = point.get("parameter_cn") or point.get("parameter") or point.get("target") or ""
            value = point.get("value") or point.get("normalized_value") or ""
            unit = point.get("unit") or point.get("normalized_unit") or ""
            source = point.get("source") or fig.get("source_label") or ""
            suspicious = " 可疑" if point.get("is_suspicious") else ""
            parts = [str(parameter).strip(), str(value).strip(), str(unit).strip(), str(source).strip()]
            line = " ".join(part for part in parts if part).strip()
            if line:
                lines.append(f"point: {line}{suspicious}")
    for assoc in evidence.get("associations", [])[:6]:
        if not isinstance(assoc, dict):
            continue
        parts = [
            assoc.get("description", ""),
            assoc.get("param_a", ""),
            assoc.get("param_b", ""),
            assoc.get("scientific_basis", ""),
        ]
        line = " ".join(str(part).strip() for part in parts if part).strip()
        if line:
            lines.append(f"association: {line}")
    return "\n".join(lines)


class LLMServiceError(RuntimeError):
    """Raised when the LLM provider returned a machine-readable service error."""

    def __init__(self, info: Dict):
        self.info = info if isinstance(info, dict) else {}
        message = self.info.get("message") or "通义千问接口调用失败。"
        model = self.info.get("model") or ""
        detail = self.info.get("detail") or ""
        parts = [message]
        if model:
            parts.append(f"模型: {model}")
        if detail:
            parts.append(f"详情: {detail}")
        super().__init__("；".join(parts))


def _llm_error_from_text(text: object) -> Optional[Dict]:
    if not isinstance(text, str) or not text.startswith(LLM_ERROR_PREFIX):
        return None
    payload = text[len(LLM_ERROR_PREFIX):].strip()
    try:
        info = json.loads(payload)
    except Exception:
        info = {"error_type": "unknown", "message": "通义千问接口调用失败。", "detail": payload}
    if not isinstance(info, dict):
        info = {"error_type": "unknown", "message": "通义千问接口调用失败。", "detail": str(info)}
    return info


def _raise_if_llm_error(text: object, *, stage: str = "") -> None:
    info = _llm_error_from_text(text)
    if not info:
        return
    if stage:
        info = dict(info)
        info["stage"] = stage
        info["message"] = f"{stage}失败：{info.get('message', '通义千问接口调用失败。')}"
    raise LLMServiceError(info)


# ─────────────────────────────────────────────────────────────
# Agent 消息协议
# ─────────────────────────────────────────────────────────────

class AgentRole(str, Enum):
    USER = "user"
    LITERATURE = "literature"
    REASONING_CHAIN = "reasoning_chain"
    HYPOTHESIS = "hypothesis"
    CROSS_DOMAIN = "cross_domain"
    CRITIQUE = "critique"
    DEVIL_ADVOCATE = "devil_advocate"
    OPTIMIST = "optimist"
    REVISION = "revision"
    VALIDATION = "validation"
    RESULTS_VERIFICATION = "results_verification"
    OUTPUT = "output"


@dataclass
class AgentMessage:
    """Agent 间传递的消息"""
    role: AgentRole
    content: str
    metadata: Dict = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)


@dataclass
class HypothesisResult:
    """最终输出的结构化假设结果"""
    raw_json: Dict = field(default_factory=dict)
    iterations: List[Dict] = field(default_factory=list)
    critique_history: List[Dict] = field(default_factory=list)
    final_output: str = ""
    confidence: int = 5
    feasibility: str = "中"
    verification_report: Optional[Dict] = None  # 验证报告 JSON（按需生成）
    results_verification: Optional[Dict] = None  # 公式推导验证结果 JSON
    reasoning_chain: Optional[Dict] = None  # 推理链 JSON（ReasoningChainAgent 输出）
    cross_domain_analogies: Optional[Dict] = None  # 跨学科技术迁移 JSON（CrossDomainAnalogyAgent 输出）
    debate_history: List[Dict] = field(default_factory=list)
    closed_loop_validation: Optional[Dict] = None
    nature_figures: List[Dict] = field(default_factory=list)  # Nature 级 matplotlib 图表
    executable_validation: Optional[Dict] = None  # 代码执行验证结果
    scientific_toolkit: Optional[Dict] = None  # RDKit/pymatgen 科学工具验证结果
    interaction_history: Dict = field(default_factory=lambda: {"interactions": []})


# ─────────────────────────────────────────────────────────────
# Base Agent
# ─────────────────────────────────────────────────────────────

class BaseAgent:
    """所有 Agent 的基类"""

    role: AgentRole = AgentRole.LITERATURE
    model_task: str = "qa"  # MODEL_MAP 中的 task key
    system_prompt: str = ""
    timeout: int = 120

    def __init__(self, config: Optional[LLMConfig] = None):
        self.config = _ensure_config(config)

    def _call_llm(self, prompt: str, timeout: Optional[int] = None) -> str:
        return _chat(
            prompt,
            self.config,
            task=self.model_task,
            system_prompt=self.system_prompt,
            timeout=timeout or self.timeout,
        )

    def process(self, input_msg: AgentMessage) -> AgentMessage:
        raise NotImplementedError


# ─────────────────────────────────────────────────────────────
# 文献理解 Agent
# ─────────────────────────────────────────────────────────────

class LiteratureAgent(BaseAgent):
    """
    从文献文本中提取关键科学事实，避免断章取义。
    输出结构化的事实摘要，供后续假设生成使用。

    【深度语义筛选】
    - 严禁仅依据论文标题判断相关性
    - 必须深入阅读摘要、方法论、实验结果及讨论部分进行细粒度语义匹配
    - 剔除标题看似相关但实际研究变量/实验条件/核心结论无关的文献
    - 仅保留真正能为假设分析提供理论支撑、数据对照或机制解释的文献内容
    """

    role = AgentRole.LITERATURE
    model_task = "summarize"
    system_prompt = (
        "你是一名资深的化学与材料科学研究助理。"
        "你的任务是从科学文献中精准提取关键事实信息，"
        "包括：研究背景、核心问题、已有方法、关键发现、"
        "数据支撑、结论与不足。"
        "你必须严格忠实于原文，不得编造或推断原文未明确表述的信息。\n\n"
        "【断章取义防护规则】：\n"
        "1. 如果片段标注了[有前文续接]或[有后文延续]，说明该片段可能不完整，"
        "   提取事实时必须标注'[上下文不完整]'，不得将片段信息当作完整结论\n"
        "2. 如果片段标注了[此片段可能被截断]，提取时必须降低该信息的置信度\n"
        "3. 对于跨片段的论点，必须标注'[论点可能跨段，需验证]'，不要拼接推断\n"
        "4. 在 key_findings 中，每条发现必须标注来源片段编号和完整性状态\n\n"
        "【深度语义筛选规则（极其重要）】：\n"
        "1. 严禁仅依据论文标题判断相关性——必须深入阅读摘要、方法论、实验结果及讨论部分\n"
        "2. 剔除标题看似相关但实际研究变量（如不同催化体系、不同反应条件）、"
        "   实验条件或核心结论与当前研究问题无关的文献\n"
        "3. 每条 key_findings 和 data_support 必须附带 relevance_score（0-10）评分：\n"
        "   9-10: 直接相关（同一体系/相同变量/可直接引用的数据）\n"
        "   7-8: 高度相关（相似体系/可迁移的方法论或数据）\n"
        "   5-6: 一般参考（提供背景知识但不直接支撑假设）\n"
        "   0-4: 低相关或不相关（仅保留用于排除干扰项）\n"
        "4. 在 limitations 中明确区分'本研究自身的不足'与'该领域普遍存在的瓶颈'\n"
        "5. 输出 screened_fragments 字段：列出每个片段的编号、relevance_score、"
        "相关理由（一句话说明为何保留或剔除）\n"
    )

    def process(self, input_msg: AgentMessage) -> AgentMessage:
        text = input_msg.content[:15000]
        research_question = input_msg.metadata.get("research_question", "")
        lang_inst = _lang_instruction(text)

        # 注入研究问题作为筛选基准
        question_context = ""
        if research_question:
            question_context = (
                f"\n\n【当前研究问题 — 用于判断文献相关性】:\n{research_question}\n\n"
                "请以上述研究问题为基准，对以下文献节选进行深度语义筛选。"
                "仅提取与研究问题真正相关的科学事实。"
            )

        prompt = (
            "请从下面的科学文献节选中提取关键科学事实，"
            "严格以 JSON 格式输出，不要加 markdown 代码块，直接返回 JSON 对象：\n"
            "{\n"
            '  "research_background": "研究背景概述",\n'
            '  "core_problem": "文献试图解决的核心科学问题",\n'
            '  "existing_methods": ["已有方法1", "已有方法2"],\n'
            '  "key_findings": [\n'
            '    {"finding": "关键发现文本", "source_fragment": 1, '
            '"relevance_score": 8, "context_status": "完整"}\n'
            '  ],\n'
            '  "data_support": [\n'
            '    {"data_desc": "数据描述", "value_range": "数值范围", '
            '"experimental_condition": "实验条件", "relevance_score": 9, "directly_usable": true}\n'
            '  ],\n'
            '  "conclusions": ["主要结论1", "主要结论2"],\n'
            '  "limitations": ["当前研究的不足/知识缺口1", "不足2"],\n'
            '  "future_directions": ["未来研究方向1", "方向2"],\n'
            '  "key_parameters": {"参数名": "参数值"},\n'
            '  "material_system": "材料/化学体系描述",\n'
            '  "screened_fragments": [\n'
            '    {"fragment_id": 1, "relevance_score": 8, '
            '"keep_reason": "提供了XX参数的直接测量值"},\n'
            '    {"fragment_id": 2, "relevance_score": 3, '
            '"discard_reason": "研究的是XX体系而非目标YY体系"}\n'
            '  ]\n'
            "}\n\n"
            f"{lang_inst}\n"
            f"{question_context}\n"
            "规则：\n"
            "1. 每个字段必须严格基于原文内容，原文没有的信息留空字符串或空数组\n"
            "2. limitations 和 future_directions 是假设生成最重要的输入，务必仔细提取\n"
            "3. key_parameters 用于记录具体的实验/计算参数\n"
            "4. relevance_score 必须 >= 6 才能纳入 key_findings/data_support 的主要结果\n"
            "5. screened_fragments 中 relevance_score < 4 的片段应标记 discard_reason\n\n"
            f"【文献节选】:\n{text}"
        )

        result = self._call_llm(prompt, timeout=HYPOTHESIS_LLM_TIMEOUT_SECONDS)

        return AgentMessage(
            role=self.role,
            content=result,
            metadata={"source_length": len(text)},
        )


# ─────────────────────────────────────────────────────────────
# 假设生成 Agent
# ─────────────────────────────────────────────────────────────

class HypothesisAgent(BaseAgent):
    """
    基于文献事实，通过归纳与演绎推理生成科学假设。
    支持多轮迭代——接收上一轮思辨反馈后修订假设。
    """

    role = AgentRole.HYPOTHESIS
    model_task = "compare"  # 用 qwen3.7-max
    system_prompt = (
        "你是一名具有创新思维的材料科学与化学研究专家。"
        "你擅长从已有科学事实中发现知识缺口，"
        "通过逻辑推理生成新颖、可验证的科学假设。"
        "你生成的假设必须：\n"
        "1. 基于真实文献事实，有据可循\n"
        "2. 具有科学创新性，不是简单的重复\n"
        "3. 可通过实验或计算验证\n"
        "4. 引用的参考文献必须是真实的（严禁虚构）\n"
        "\n"
        "【极其重要 - 关于参考文献的真实性】\n"
        "你引用的每一篇参考文献都必须是真实发表的学术论文。"
        "绝对不允许编造、伪造或臆造任何参考文献信息，包括但不限于：作者名、论文标题、期刊名、年份、DOI。"
        "如果你对某篇文献的真实性有任何不确定，宁可删除该引用也不要编造。"
        "宁可 references 列表为空，也不要包含任何虚假文献。"
        "编造参考文献是严重的学术不端行为。\n\n"
        "【强制引用标注规则（极其重要）】：\n"
        "在生成假设时，每一句涉及客观事实、理论依据或数据对比的陈述，"
        "必须严格溯源至文献，并在句末使用方括号数字索引标注出处。\n"
        "规则：\n"
        "1. 单篇引用：句末标注 [1]，如 'Co-N4催化剂的ORR半波电位为0.81 V vs RHE [1]'\n"
        "2. 多篇引用：句末标注 [1][3]，如 'd-band中心与吸附能呈线性关系 [1][3]'\n"
        "3. 数字索引与 references 列表中的顺序对应\n"
        "4. problem_statement、rationale、technical_details、methods、experiments、"
        "expected_results 等所有涉及事实陈述的字段都必须标注引用\n"
        "5. 纯推理/假设性陈述无需引用，但需明确标注'基于以上事实，我们假设...'\n"
        "6. 没有任何引用标注的假设将被视为缺乏文献支撑而不合格\n"
    )

    def process(self, input_msg: AgentMessage) -> AgentMessage:
        facts_json = input_msg.content
        critique = input_msg.metadata.get("critique", "")
        iteration = input_msg.metadata.get("iteration", 0)
        reasoning_chain = input_msg.metadata.get("reasoning_chain", "")
        user_edited_hypothesis = input_msg.metadata.get("user_edited_hypothesis", "")
        domain = input_msg.metadata.get("domain", "")
        database_evidence_context = input_msg.metadata.get("database_evidence_context", "")
        domain_prompt = domain_guidance(domain)
        output_contract = domain_output_contract(domain)
        lang_inst = "请务必全部使用中文输出。"

        prompt = (
            "基于以下文献事实，生成一个科学假设和研究计划。\n\n"
        )

        if domain_prompt:
            prompt += (
                f"【当前研究方向】：{domain_label(domain)}\n"
                f"{domain_prompt}\n\n"
                f"{output_contract}\n\n"
            )

        if iteration > 0 and critique:
            prompt += (
                f"【第 {iteration} 轮思辨反馈】（请根据反馈修订假设）:\n{critique}\n\n"
            )

        # 注入推理链上下文（如有）
        if reasoning_chain:
            prompt += (
                f"【推理链分析】（请基于此推理链的逻辑步骤生成假设，"
                f"在 rationale 中标注引用了哪些推理步骤）:\n{reasoning_chain}\n\n"
            )

        if database_evidence_context:
            prompt += (
                f"{database_evidence_context}\n\n"
                "请在 datasets.source、Source、References 或 Database Evidence 中明确标注上述证据来源。"
                "其中 computational_catalysis_evidence / OCP / FAIR-Chem / OC20 / OC22 / OC25 / ODAC23 "
                "只能作为计算证据或结构-能量-吸附描述符证据，不得写成已完成的实验性能结果。"
                "若数据库未提供足够证据，请明确写“证据不足/需核验”，严禁补造数据。\n\n"
            )

        if user_edited_hypothesis:
            prompt += (
                "【用户人工修订稿】:\n"
                f"{user_edited_hypothesis}\n\n"
                "请以人工修订稿为优先约束，保留用户明确修改的研究对象、baseline、"
                "实验路径与参考文献要求；只有在与真实文献或可验证性冲突时才调整，并说明理由。\n\n"
            )

        prompt += (
            f"【文献事实摘要】:\n{facts_json}\n\n"
            f"{lang_inst}\n"
            "请严格以 JSON 格式输出，不要加 markdown 代码块，直接返回 JSON 对象：\n"
            "{\n"
            '  "problem_statement": "当前领域存在的具体局限性与待研究问题",\n'
            '  "rationale": "基于逻辑推理的创新点阐述（展示完整的推导链条）",\n'
            '  "technical_details": "验证假设所需的技术栈（具体方法/工具/参数）",\n'
            '  "datasets": {\n'
            '    "source": "假设推演依据的历史/文献数据描述",\n'
            '    "target": "验证实验所需的拟采集数据特征"\n'
            '  },\n'
            '  "paper_title": "符合学术规范的论文标题",\n'
            '  "paper_abstract": "包含背景、方法、预期结果的完整摘要",\n'
            '  "methods": "具体实施步骤（模型架构或实验流程）",\n'
            '  "experiments": {\n'
            '    "baselines": ["基线方法1", "基线方法2"],\n'
            '    "metrics": ["评估指标1", "评估指标2"],\n'
            '    "design": "实验设计描述"\n'
            '  },\n'
            '  "expected_results": "通过公式推导或理论分析预期的实验结果",\n'
            '  "structured_extraction_table": [\n'
            '    {"paper_id": "P1", "title": "文献标题", "materials_or_reaction": "材料/反应", "key_data": "性能数据", "mechanism": "机理", "limitation": "局限", "evidence_status": "已验证/需核验"}\n'
            '  ],\n'
            '  "database_schema": {"table_name": "方向数据库表名", "fields": [{"name": "字段名", "description": "字段含义", "required": true}]},\n'
            '  "literature_network": {"node_groups": {"核心/机制/实验/数据方法文献": []}, "evidence_edges": [{"from": "P1", "to": "P2", "relation": "支持/对比/指出局限"}], "network_text": "文字版文献关系网络"},\n'
            '  "paper_titles": ["English title 1", "English title 2", "English title 3"],\n'
            '  "experiment_record_card": {"hypothesis_id": "H-001", "hypothesis_statement": "可进入实验记录的假设", "variables_to_test": [], "control_group": "", "experimental_group": "", "testing_protocol": "", "expected_metrics": [], "risk_points": ""},\n'
            '  "reference_status": "引用来源、需核验项、缺失全文的合规补充方式",\n'
            '  "references": [\n'
            '    {"authors": "Author et al.", "title": "Paper Title", "journal": "Journal Name", "year": 2024, "doi": "10.xxxx/xxxxx"}\n'
            '  ],\n'
            '  "confidence": 7,\n'
            '  "feasibility": "高"\n'
            "}\n\n"
            "规则：\n"
            "1. references 中的文献必须是真实存在的，严禁编造！如果不确定，宁可留空也不要编造。\n"
            "2. confidence 为 1-10 的整数评分\n"
            "3. feasibility 为 高/中/低 之一\n"
            "4. expected_results 必须包含具体的数值预测（如能量范围、性能提升百分比等），而不是模糊描述\n"
            "5. 必须支持多文献共同分析，不能逐篇孤立总结；文献关系网络必须体现证据链、对照关系和局限性来源\n"
            "6. 不得使用非授权来源获取全文；全文不足时写明需用户上传授权PDF、摘要、图表或DOI元数据补全\n"
        )

        result = self._call_llm(prompt, timeout=HYPOTHESIS_LLM_TIMEOUT_SECONDS)
        return AgentMessage(
            role=self.role,
            content=result,
            metadata={"iteration": iteration},
        )


# ─────────────────────────────────────────────────────────────
# 思辨 Agent
# ─────────────────────────────────────────────────────────────

class CritiqueAgent(BaseAgent):
    """
    对假设进行质疑、找漏洞，提出改进建议。
    模拟科学同行评审的过程。
    """

    role = AgentRole.CRITIQUE
    model_task = "compare"
    system_prompt = (
        "你是一名严谨的学术评审专家，擅长发现研究假设中的漏洞。"
        "你的职责是对科学假设进行批判性分析，"
        "从逻辑自洽性、可验证性、创新性、数据支撑等维度提出质疑和改进建议。"
        "你的批评必须建设性——既要指出问题，也要给出具体的改进方向。"
    )

    def process(self, input_msg: AgentMessage) -> AgentMessage:
        hypothesis_json = input_msg.content
        lang_inst = "请务必全部使用中文输出。"

        prompt = (
            "请对下面的科学假设进行严格的批判性分析。\n\n"
            f"【科学假设】:\n{hypothesis_json}\n\n"
            f"{lang_inst}\n"
            "请严格以 JSON 格式输出，不要加 markdown 代码块，直接返回 JSON 对象：\n"
            "{\n"
            '  "overall_score": 7,\n'
            '  "logical_consistency": {"score": 7, "issues": ["逻辑问题1"]},\n'
            '  "verifiability": {"score": 6, "issues": ["可验证性问题1"]},\n'
            '  "novelty": {"score": 8, "issues": ["创新性评价"]},\n'
            '  "data_support": {"score": 7, "issues": ["数据支撑问题1"]},\n'
            '  "critical_flaws": ["严重缺陷1", "严重缺陷2"],\n'
            '  "improvement_suggestions": ["改进建议1", "改进建议2"],\n'
            '  "missing_references": ["假设可能引用但未引用的重要文献方向"],\n'
            '  "summary": "总体评价与改进方向总结"\n'
            "}\n\n"
            "规则：\n"
            "1. 各维度 score 为 1-10 的整数\n"
            "2. critical_flaws 列出必须解决的严重问题（如果没有则留空数组）\n"
            "3. improvement_suggestions 必须具体可操作\n"
            "4. 如果假设已经很好（overall_score >= 8），可以减少批评力度"
        )

        result = self._call_llm(prompt, timeout=120)
        return AgentMessage(
            role=self.role,
            content=result,
            metadata={},
        )


# ─────────────────────────────────────────────────────────────
# 跨学科技术迁移 Agent
# ─────────────────────────────────────────────────────────────

class CrossDomainAnalogyAgent(BaseAgent):
    """
    挖掘跨学科技术迁移潜力。

    基于知识库中的 CrossDomainMapping 预置映射，
    为当前假设寻找跨领域的技术迁移机会，
    生成结构化的跨学科类比与迁移方案。

    管线位置：Step 2.5（Hypothesis 后、Critique 前）
    """

    role = AgentRole.CROSS_DOMAIN
    model_task = "compare"
    system_prompt = (
        "你是一名擅长跨学科思维的科学顾问。"
        "你的专长是发现不同研究领域之间的深层类比，"
        "将一个领域的成功经验迁移到另一个领域。"
        "你必须：\n"
        "1. 基于科学原理建立类比，而非表面相似\n"
        "2. 明确指出迁移的适用条件和局限性\n"
        "3. 给出具体的可操作方案\n"
    )

    def process(self, input_msg: AgentMessage) -> AgentMessage:
        hypothesis_json = input_msg.content
        cross_domain_hints = input_msg.metadata.get("cross_domain_hints", "")
        domain = input_msg.metadata.get("domain", "")
        lang_inst = "请务必全部使用中文输出。"

        prompt = (
            "请为以下科学假设挖掘跨学科技术迁移潜力——"
            "从其他研究领域的成功经验中，发现可以迁移到本假设的方法、"
            "思路或技术，为假设提供新的视角和增强方案。\n\n"
            f"【科学假设】:\n{hypothesis_json}\n\n"
        )

        if domain:
            prompt += f"【当前研究领域】: {domain}\n\n"

        if cross_domain_hints:
            prompt += (
                f"【知识库预置的跨领域映射参考】:\n{cross_domain_hints}\n\n"
                "请在分析中参考上述预置映射，但不必局限于这些映射。"
                "你也可以发现新的、知识库未覆盖的跨领域类比。\n\n"
            )

        prompt += (
            f"{lang_inst}\n"
            "请严格以 JSON 格式输出，不要加 markdown 代码块，直接返回 JSON 对象：\n"
            "{\n"
            '  "analogies": [\n'
            '    {\n'
            '      "source_domain": "源领域名称（如 光催化）",\n'
            '      "target_domain": "目标领域名称（如 电催化）",\n'
            '      "source_descriptor": "源领域关键描述符",\n'
            '      "target_descriptor": "目标领域关键描述符",\n'
            '      "mapping_rationale": "类比映射的科学原理",\n'
            '      "transferable_method": "可迁移的具体方法/技术",\n'
            '      "application_plan": "如何在当前假设中应用此迁移",\n'
            '      "confidence": 0.8,\n'
            '      "limitations": ["适用局限1"]\n'
            '    }\n'
            '  ],\n'
            '  "innovation_boost": "跨学科迁移如何提升假设的创新性（1-2句总结）",\n'
            '  "recommended_experiments": ["受跨领域启发的建议实验1", "建议实验2"]\n'
            "}\n\n"
            "规则：\n"
            "1. analogies 至少包含 1 条跨领域类比\n"
            "2. 每条类比的 mapping_rationale 必须基于具体的科学原理，不能是表面类比\n"
            "3. application_plan 必须具体可操作\n"
            "4. confidence 为 0-1 的浮点数\n"
            "5. 必须指出 limitations，任何跨领域迁移都有局限\n"
            "6. 【动态发现要求】不要仅依赖预置映射，请主动基于假设内容发现新的跨学科迁移机会。"
            "    思考：当前假设中的核心问题，在其他领域（如物理学、生物学、材料科学、工程学）"
            "    是否有类似的问题和解决方案？例如：\n"
            "    - 催化剂设计 → 酶催化（生物化学）\n"
            "    - 界面工程 → 细胞膜调控（生物学）\n"
            "    - 能量存储 → 神经信号传递（生物物理学）\n"
            "    - 材料合成 → 矿物结晶（地质学）\n"
            "    请至少发现 1 条知识库未覆盖的跨领域类比，并标注为 'discovered': true\n"
            "7. 输出格式中新增 discovered_analogies 字段，记录 LLM 新发现的迁移机会"
        )

        result = self._call_llm(prompt, timeout=120)
        return AgentMessage(
            role=self.role,
            content=result,
            metadata={"domain": domain},
        )


# ─────────────────────────────────────────────────────────────
# 魔鬼代言人 Agent（Devil's Advocate）
# ─────────────────────────────────────────────────────────────

class DevilAdvocateAgent(BaseAgent):
    """
    以反方立场攻击假设，选择最致命的 1-2 个弱点进行猛攻。

    与 CritiqueAgent 不同：Critique 做全面评审，DevilAdvocate 专攻致命弱点。
    角色：反方 / "实验派"，质疑理论的可靠性。

    管线位置：Step 3.5（Critique 后、Revision 前，与 Optimist 配对）
    """

    role = AgentRole.DEVIL_ADVOCATE
    model_task = "compare"
    system_prompt = (
        "你是一名'魔鬼代言人'——你的职责是从反方立场攻击科学假设。"
        "你不是在做平衡评审，而是在找致命弱点。"
        "你要像一个持怀疑态度的资深审稿人，"
        "集中火力攻击假设中最脆弱的 1-2 个环节。"
        "你的攻击必须基于科学证据和逻辑推理，而非无理取闹。"
    )

    def process(self, input_msg: AgentMessage) -> AgentMessage:
        hypothesis_json = input_msg.content
        critique_json = input_msg.metadata.get("critique", "")
        cross_domain_json = input_msg.metadata.get("cross_domain_analogies", "")
        lang_inst = "请务必全部使用中文输出。"

        prompt = (
            "请以'魔鬼代言人'的立场，攻击以下科学假设的最致命弱点。\n\n"
            f"【科学假设】:\n{hypothesis_json}\n\n"
        )

        if critique_json:
            prompt += f"【已有评审意见（可参考但不重复）】:\n{critique_json}\n\n"

        if cross_domain_json:
            prompt += f"【跨学科迁移方案（需质疑其可靠性）】:\n{cross_domain_json}\n\n"

        prompt += (
            f"{lang_inst}\n"
            "请严格以 JSON 格式输出，不要加 markdown 代码块，直接返回 JSON 对象：\n"
            "{\n"
            '  "stance": "反方（魔鬼代言人）",\n'
            '  "attack_points": [\n'
            '    {\n'
            '      "target": "攻击的具体假设环节",\n'
            '      "evidence": "支持攻击的科学证据或逻辑推理",\n'
            '      "if_wrong": "如果该环节确实有问题，最坏后果是什么",\n'
            '      "severity": "致命/严重/中等",\n'
            '      "counter_evidence": "假设方可能用于反驳的证据（你也需预判）"\n'
            '    }\n'
            '  ],\n'
            '  "overall_verdict": "假设应被拒绝/需要重大修改/可以继续但有风险",\n'
            '  "confidence_in_rejection": 0.7\n'
            "}\n\n"
            "规则：\n"
            "1. attack_points 最多 2 条，选择最致命的弱点集中攻击\n"
            "2. 每条攻击必须有 evidence 支撑，不能只说'这可能不对'\n"
            "3. severity 只能是 致命/严重/中等 之一\n"
            "4. if_wrong 必须描述具体的最坏后果\n"
            "5. counter_evidence 体现你的客观性——你也要预判对方如何反驳你\n"
            "6. confidence_in_rejection 为 0-1 浮点数，你对拒绝假设的信心"
        )

        result = self._call_llm(prompt, timeout=120)
        return AgentMessage(
            role=self.role,
            content=result,
            metadata={},
        )


# ─────────────────────────────────────────────────────────────
# 乐观辩护 Agent（Optimist）
# ─────────────────────────────────────────────────────────────

class OptimistAgent(BaseAgent):
    """
    以正方立场为假设辩护，回应魔鬼代言人的攻击。

    角色：正方 / "理论派"，捍卫假设的合理性。
    必须提供辩护证据，不能只是空口辩护。

    管线位置：Step 3.5（紧跟 DevilAdvocateAgent 之后）
    """

    role = AgentRole.OPTIMIST
    model_task = "compare"
    system_prompt = (
        "你是一名科学假设的'乐观辩护人'——你的职责是从正方立场捍卫假设。"
        "你要像一个坚定的假设提出者，回应质疑，为假设的合理性辩护。"
        "但你的辩护必须基于科学证据，不能无理强辩。"
        "如果攻击确实有道理，你应该承认并提出改进方案而非回避。"
    )

    def process(self, input_msg: AgentMessage) -> AgentMessage:
        hypothesis_json = input_msg.content
        devil_attack_json = input_msg.metadata.get("devil_attack", "")
        cross_domain_json = input_msg.metadata.get("cross_domain_analogies", "")
        lang_inst = "请务必全部使用中文输出。"

        prompt = (
            "请以'乐观辩护人'的立场，为以下科学假设辩护，"
            "回应魔鬼代言人的攻击。\n\n"
            f"【科学假设】:\n{hypothesis_json}\n\n"
        )

        if devil_attack_json:
            prompt += f"【魔鬼代言人的攻击】:\n{devil_attack_json}\n\n"

        if cross_domain_json:
            prompt += f"【跨学科迁移方案（可用于辩护）】:\n{cross_domain_json}\n\n"

        prompt += (
            f"{lang_inst}\n"
            "请严格以 JSON 格式输出，不要加 markdown 代码块，直接返回 JSON 对象：\n"
            "{\n"
            '  "stance": "正方（乐观辩护人）",\n'
            '  "defense_points": [\n'
            '    {\n'
            '      "counters_attack": "回应的攻击点",\n'
            '      "evidence": "辩护的科学证据",\n'
            '      "validity_range": "辩护成立的适用范围/条件",\n'
            '      "concession": "部分承认的合理质疑（如有）"\n'
            '    }\n'
            '  ],\n'
            '  "key_experiment": "最能验证假设的关键实验（如果辩护成功）",\n'
            '  "overall_verdict": "假设基本成立/需要小修/需要大修但值得继续",\n'
            '  "confidence_in_hypothesis": 0.7\n'
            "}\n\n"
            "规则：\n"
            "1. defense_points 必须逐条回应魔鬼代言人的 attack_points\n"
            "2. evidence 必须是具体的科学依据，不能只说'有可能'\n"
            "3. validity_range 必须说明辩护在什么条件下成立\n"
            "4. concession 体现你的客观性——部分承认攻击的合理性\n"
            "5. key_experiment 给出最关键的判决性实验\n"
            "6. confidence_in_hypothesis 为 0-1 浮点数"
        )

        result = self._call_llm(prompt, timeout=120)
        return AgentMessage(
            role=self.role,
            content=result,
            metadata={},
        )


# ─────────────────────────────────────────────────────────────
# 辩论摘要（DebateSummary）工具函数
# ─────────────────────────────────────────────────────────────

def summarize_debate(
    devil_json: Dict,
    optimist_json: Dict,
) -> Dict:
    """
    综合魔鬼代言人和乐观辩护人的论点，生成辩论摘要。

    Returns:
        包含 balance / verdict / key_insights 的字典
    """
    devil_conf = devil_json.get("confidence_in_rejection", 0.5)
    optimist_conf = optimist_json.get("confidence_in_hypothesis", 0.5)

    # 平衡判定
    if optimist_conf > devil_conf + 0.15:
        balance = "favor_hypothesis"
    elif devil_conf > optimist_conf + 0.15:
        balance = "favor_rejection"
    else:
        balance = "balanced"

    # 提取攻击与防御要点
    attacks = devil_json.get("attack_points", [])
    defenses = optimist_json.get("defense_points", [])

    key_insights = []
    for i, attack in enumerate(attacks):
        insight = {
            "issue": attack.get("target", ""),
            "severity": attack.get("severity", "中等"),
            "devil_evidence": attack.get("evidence", ""),
            "optimist_response": defenses[i].get("evidence", "") if i < len(defenses) else "",
            "optimist_concession": defenses[i].get("concession", "") if i < len(defenses) else "",
        }
        key_insights.append(insight)

    return {
        "balance": balance,
        "devil_verdict": devil_json.get("overall_verdict", ""),
        "optimist_verdict": optimist_json.get("overall_verdict", ""),
        "devil_confidence": devil_conf,
        "optimist_confidence": optimist_conf,
        "key_insights": key_insights,
        "key_experiment": optimist_json.get("key_experiment", ""),
        "recommendation": (
            "建议继续假设并按计划验证" if balance == "favor_hypothesis"
            else "建议重大修改假设后再验证" if balance == "favor_rejection"
            else "假设有争议，建议补充数据后判断"
        ),
    }
# ─────────────────────────────────────────────────────────────

class ValidationAgent(BaseAgent):
    """
    评估假设的可行性，检查实验设计是否合理，
    确定假设是否可以被当前技术手段验证。
    """

    role = AgentRole.VALIDATION
    model_task = "compare"
    system_prompt = (
        "你是一名实验设计与计算验证专家。"
        "你的职责是评估科学假设的可验证性，"
        "检查实验/计算设计是否合理可行。"
    )

    def process(self, input_msg: AgentMessage) -> AgentMessage:
        hypothesis_json = input_msg.content
        lang_inst = "请务必全部使用中文输出。"

        prompt = (
            "请评估以下科学假设的可验证性。\n\n"
            f"【科学假设与研究计划】:\n{hypothesis_json}\n\n"
            f"{lang_inst}\n"
            "请严格以 JSON 格式输出，不要加 markdown 代码块，直接返回 JSON 对象：\n"
            "{\n"
            '  "overall_feasibility": "高",\n'
            '  "experimental_design_valid": true,\n'
            '  "computationally_feasible": true,\n'
            '  "data_availability": {"source_data_available": true, "target_data_acquirable": true},\n'
            '  "time_estimate": "预估所需时间（如 2-4 weeks）",\n'
            '  "resource_requirements": ["所需资源1", "所需资源2"],\n'
            '  "potential_risks": ["风险1", "风险2"],\n'
            '  "alternative_approaches": ["备选验证方案1"],\n'
            '  "recommendations": ["建议1", "建议2"],\n'
            '  "final_verdict": "该假设具备可验证性，建议按计划执行 / 需要修订后执行 / 不建议执行"\n'
            "}\n\n"
            "规则：\n"
            "1. overall_feasibility 为 高/中/低 之一\n"
            "2. 认真检查 methods 和 experiments 字段的合理性\n"
            "3. 如果发现致命可行性问题，在 final_verdict 中明确说明"
        )

        result = self._call_llm(prompt, timeout=120)
        return AgentMessage(
            role=self.role,
            content=result,
            metadata={},
        )


# ─────────────────────────────────────────────────────────────
# 公式推导与实验结果验证 Agent
# ─────────────────────────────────────────────────────────────

class ResultsVerificationAgent(BaseAgent):
    """
    对假设进行公式推导和文献对比验证，生成结构化的 results 字段。

    比赛要求对应：
      "实验结果（Results）：通过公式推导或实际执行，在一定范围内验证该实验可行性"

    该 Agent 会：
      1. 从假设中提取关键预测值和理论依据
      2. 基于已知标度关系和理论公式进行逐步推导
      3. 将推导计算结果与文献已知值做对比
      4. 给出量化偏差和可行性结论
    """

    role = AgentRole.RESULTS_VERIFICATION
    model_task = "compare"  # 用 qwen3.7-max
    system_prompt = (
        "你是一名擅长公式推导和定量验证的理论化学/材料科学研究专家。"
        "你的职责是对科学假设进行严谨的公式推导验证——从基本原理出发，"
        "逐步推导出可计算的预测值，并与文献实验数据对比，评估假设的可行性。"
        "你必须做到：\n"
        "1. 推导过程完整，每一步都标注使用的公式和假设条件\n"
        "2. 计算结果精确，给出具体数值和单位\n"
        "3. 文献对比客观，标注数据来源\n"
        "4. 不得编造文献数据，不确定的值标注[估算]\n"
    )

    def process(self, input_msg: AgentMessage) -> AgentMessage:
        hypothesis_json = input_msg.content
        scaling_context = input_msg.metadata.get("scaling_context", "")
        lang_inst = "请务必全部使用中文输出。"

        # 构建推导引导上下文
        scaling_section = ""
        if scaling_context:
            scaling_section = (
                "\n\n【已知定量标度关系（来自多模态数据拟合）】:\n"
                f"{scaling_context}\n"
                "请在推导中优先使用上述已验证的标度关系。\n"
            )

        prompt = (
            "请对下面的科学假设进行公式推导验证，逐步推导出具体预测值，"
            "并与文献已知值做对比，评估假设的可行性。\n\n"
            f"【科学假设与研究计划】:\n{hypothesis_json}\n"
            f"{scaling_section}\n"
            f"{lang_inst}\n"
            "请严格以 JSON 格式输出，不要加 markdown 代码块，直接返回 JSON 对象：\n"
            "{\n"
            '  "verification_method": "公式推导",\n'
            '  "derivation": "完整的公式推导过程（逐步展示推导链，每步标注公式来源和假设条件）",\n'
            '  "calculated_values": [\n'
            '    {\n'
            '      "parameter": "参数名称",\n'
            '      "value": "计算得到的数值（含单位）",\n'
            '      "derivation": "该值的推导依据和公式"\n'
            '    }\n'
            '  ],\n'
            '  "comparison_with_literature": [\n'
            '    {\n'
            '      "metric": "对比指标名称",\n'
            '      "predicted": "本假设推导的预测值",\n'
            '      "literature": "文献已报道的实验/计算值（注明来源）",\n'
            '      "deviation": "偏差百分比或绝对差"\n'
            '    }\n'
            '  ],\n'
            '  "feasibility_conclusion": "基于公式推导和文献对比的综合可行性结论（量化说明假设在多大范围内可行）"\n'
            "}\n\n"
            "规则：\n"
            "1. derivation 必须是完整的推导链，例如：\n"
            '   "1) 由 Nørskov d 带理论：ΔE_OH = α·ε_d + β\\n'
            '   2) 代入 ε_d = -1.5 eV，α = 0.52，β = 1.57\\n'
            '   3) 计算得 ΔE_OH = 0.52 × (-1.5) + 1.57 = 0.79 eV\\n'
            '   4) 根据 ORR 热力学：η = |ΔE_OH - 0.45| = 0.34 V"\n'
            "2. calculated_values 必须至少包含 2 个具体计算值\n"
            "3. comparison_with_literature 必须至少与 1 个文献值对比\n"
            "4. 如果文献值不确定，标注[估算]，不要编造具体数值\n"
            "5. feasibility_conclusion 必须包含量化结论（如'偏差<15%，假设可行'）\n"
        )

        result = self._call_llm(prompt, timeout=HYPOTHESIS_LLM_TIMEOUT_SECONDS)
        return AgentMessage(
            role=self.role,
            content=result,
            metadata={},
        )


# ─────────────────────────────────────────────────────────────
# 标准化输出 Agent
# ─────────────────────────────────────────────────────────────

class OutputAgent(BaseAgent):
    """
    将假设结果格式化为比赛要求的标准输出格式。
    包含所有必填字段，以及参考文献真实性检查。
    """

    role = AgentRole.OUTPUT
    model_task = "compare"
    system_prompt = (
        "你是一名学术写作专家，擅长将研究结果格式化为标准的学术论文格式。"
        "你负责最终输出的质量把关，确保格式规范、内容完整。\n\n"
        "【强制引用标注规则】：\n"
        "在整理输出时，每一句涉及客观事实、理论依据或数据对比的陈述，"
        "必须保留原始假设中的方括号数字索引（如 [1]、[2]），不得删除。"
        "若原始假设中缺少引用标注，你必须在对应事实陈述后补充标注。"
        "数字索引与 references 列表中的顺序对应。"
        "多篇文献同时支撑一个论点时标注 [1][3] 格式。"
    )

    def process(self, input_msg: AgentMessage) -> AgentMessage:
        hypothesis_json = input_msg.content
        critique_json = input_msg.metadata.get("critique", "")
        validation_json = input_msg.metadata.get("validation", "")
        results_verification_json = input_msg.metadata.get("results_verification", "")
        cross_domain_json = input_msg.metadata.get("cross_domain_analogies", "")
        debate_json = input_msg.metadata.get("debate_summary", "")
        citation_whitelist = input_msg.metadata.get("citation_whitelist", [])
        quantitative_ctx = input_msg.metadata.get("quantitative_context", "")
        executable_validation = input_msg.metadata.get("executable_validation", {})
        scientific_toolkit = input_msg.metadata.get("scientific_toolkit", {})
        scientific_toolkit_context = input_msg.metadata.get("scientific_toolkit_context", "")
        dataset_source_ctx = input_msg.metadata.get("dataset_source_context", "")
        verifiability_score = input_msg.metadata.get("verifiability_score", {})
        experiment_template_ctx = input_msg.metadata.get("experiment_template_context", "")
        database_evidence_context = input_msg.metadata.get("database_evidence_context", "")
        domain = input_msg.metadata.get("domain", "")
        domain_prompt = domain_guidance(domain)
        output_contract = domain_output_contract(domain)

        prompt = (
            "请将下面的科学假设与研究计划整理为标准的学术输出格式。"
            "请务必全部使用中文输出。\n"
            "输出必须包含以下所有标准化字段，格式为 JSON。\n\n"
            f"【科学假设】:\n{hypothesis_json}\n"
        )

        if domain_prompt:
            prompt += (
                f"\n【当前研究方向】：{domain_label(domain)}\n"
                f"{domain_prompt}\n\n"
                f"{output_contract}\n"
                "请把上述领域约束写入最终 JSON，而不是只作为背景说明。\n"
            )

        if critique_json:
            prompt += f"\n【思辨评审结果】:\n{critique_json}\n"

        if validation_json:
            prompt += f"\n【可验证性评估】:\n{validation_json}\n"

        if results_verification_json:
            prompt += f"\n【公式推导与结果验证】:\n{results_verification_json}\n"

        if executable_validation:
            try:
                exec_validation_text = json.dumps(executable_validation, ensure_ascii=False, indent=2)
            except (TypeError, ValueError):
                exec_validation_text = str(executable_validation)
            prompt += (
                "\n【代码执行验证结果（必须保留，不得被改写为主观判断）】:\n"
                f"{exec_validation_text}\n"
                "请将 execution_log 原样保留到 results.execution_log，"
                "将代码计算出的 calculated_values 与 comparison_with_literature 合并到 results 中，"
                "并在 results.derivation 中追加“代码执行验证”小节。\n"
            )

        if scientific_toolkit_context or scientific_toolkit:
            prompt += "\n【RDKit/pymatgen 可执行科学验证结果】:\n"
            if scientific_toolkit_context:
                prompt += f"{scientific_toolkit_context}\n"
            elif scientific_toolkit:
                try:
                    prompt += json.dumps(scientific_toolkit, ensure_ascii=False, indent=2)[:6000] + "\n"
                except (TypeError, ValueError):
                    prompt += str(scientific_toolkit)[:6000] + "\n"
            prompt += (
                "请在 technical_details、methods 与 results.feasibility_conclusion 中体现："
                "RDKit 用于分子实体/描述符校验，pymatgen 用于 Qwen 生成的 CIF/POSCAR/CONTCAR "
                "候选结构解析、晶格/组成检查和 VASP 可复现计算准备。"
                "不要把低置信度候选结构表述为实验真实结构。\n"
            )

        if cross_domain_json:
            prompt += f"\n【跨学科技术迁移】:\n{cross_domain_json}\n"

        if debate_json:
            prompt += f"\n【多角色辩论摘要】:\n{debate_json}\n"

        if quantitative_ctx:
            prompt += (
                f"\n【已验证的定量标度关系（来自多模态数据拟合）】:\n{quantitative_ctx}\n"
                "请在 results.derivation 中引用上述标度关系方程，"
                "将拟合公式作为推导链的关键步骤，"
                "并在 results.calculated_values 中使用拟合方程计算预测值。\n"
            )

        if dataset_source_ctx:
            prompt += (
                f"\n{dataset_source_ctx}\n"
                "请在 datasets.source 中优先使用上述真实数据来源，"
                "不要编造不存在的数据集名称。\n"
            )

        if verifiability_score:
            vs = verifiability_score
            prompt += (
                f"\n【可验证性量化评分】: {vs.get('verifiability_score', 'N/A')}/100 "
                f"({vs.get('level', '')})\n"
                "请在输出中包含 verifiability_score 字段，"
                "并在 feasibility 中体现此评分。\n"
            )
            if vs.get("improvement_suggestions"):
                prompt += "改进建议: " + "; ".join(vs["improvement_suggestions"]) + "\n"

        if experiment_template_ctx:
            prompt += f"\n{experiment_template_ctx}\n"

        if database_evidence_context:
            prompt += (
                f"\n{database_evidence_context}\n"
                "请在最终 JSON 中保留 database_evidence 或在 datasets.source / reference_status 中明确说明数据库证据来源。"
                "其中 OCP、FAIR-Chem、OC20、OC22、OC25、ODAC23 必须标注为计算证据或数据源线索，"
                "不得改写成实验过电位、Tafel、法拉第效率或循环寿命等已测实验结果。\n"
            )

        if citation_whitelist:
            whitelist_items = []
            for w in citation_whitelist:
                if w.get("source") == "doi":
                    whitelist_items.append(f"  - DOI: {w['doi']}")
                elif w.get("source") == "author_year":
                    whitelist_items.append(f"  - {w['authors']} ({w['year']})")
                elif w.get("source") == "bracket_ref":
                    whitelist_items.append(f"  - [{w['ref_id']}] {w['context'][:80]}")
                elif w.get("source") == "literature_fact":
                    whitelist_items.append(f"  - {w['reference'][:80]}")
            if whitelist_items:
                prompt += (
                    "\n\n【引用白名单 — 你只能从以下文献中选取 references】:\n"
                    + "\n".join(whitelist_items)
                    + "\n\n严格规则：\n"
                    "  - references 中的每一条文献必须能在上述白名单中找到对应项\n"
                    "  - 禁止编造白名单中不存在的作者、标题、DOI\n"
                )
            else:
                prompt += (
                    "\n\n【引用白名单为空 — 仍需输出 references】:\n"
                    "白名单中暂无可匹配的文献条目，但 references 是比赛必填字段。\n"
                    "references 字段必须存在，但不得为了凑数量编造论文。"
                    "若没有可信来源，请输出空列表 []，并在 reference_status 中写明需要补充的 DOI、摘要、用户授权全文或数据库记录。\n"
                )

        prompt += (
            "\n请严格以 JSON 格式输出，不要加 markdown 代码块，直接返回 JSON 对象：\n"
            "{\n"
            '  "problem_statement": "现有M-N-C催化剂在酸性介质中ORR稳定性差，金属中心易脱溶",\n'
            '  "rationale": "基于d-band理论，调节配位环境可优化*OH吸附...",\n'
            '  "technical_details": "DFT计算(VASP)、XPS表征、LSV电化学测试...",\n'
            '  "datasets": {\n'
            '    "source": "文献报道的Co-N4/石墨烯d-band中心(-1.95 eV)、*OH吸附能(1.23 eV)等DFT数据",\n'
            '    "target": "不同配位数Co-Nx催化剂的ORR极化曲线、XPS Co 2p谱、稳定性测试数据"\n'
            '  },\n'
            '  "paper_title": "配位环境调控Co单原子催化剂ORR性能的机制研究",\n'
            '  "paper_titles": ["Coordination Engineering of Co Single-Atom Catalysts for Oxygen Reduction", "Mechanistic Study of Co-Nx Active Sites in Oxygen Reduction", "Data-Guided Design of Co-N-C Catalysts for ORR"],\n'
            '  "paper_abstract": "Background: ... Methods: ... Expected Results: ...",\n'
            '  "methods": "1.构建不同配位数Co-Nx模型 2.DFT计算吸附能 3.合成并电化学表征",\n'
            '  "experiments": {\n'
            '    "baselines": ["Pt/C (20 wt%)", "Co-N4/石墨烯"],\n'
            '    "metrics": ["半波电位(E1/2)", "Tafel斜率", "稳定性(1000圈后E1/2偏移)"],\n'
            '    "design": "在0.1M KOH中用RRDE测试ORR性能，对比不同配位数催化剂"\n'
            '  },\n'
            '  "expected_results": "Co-N3的E1/2预计达到0.85 V vs RHE，Tafel斜率~65 mV/dec",\n'
            '  "results": {\n'
            '    "verification_method": "公式推导+文献对比",\n'
            '    "derivation": "根据d-band理论: E_*OH = α×ε_d + β，代入ε_d = -1.68 eV，α=0.52，β=-0.31，得E_*OH = 1.18 eV",\n'
            '    "calculated_values": [\n'
            '      {"parameter": "*OH吸附能", "value": "1.18 eV", "derivation": "E_*OH = 0.52×(-1.68)+(-0.31)"},\n'
            '      {"parameter": "过电势η", "value": "0.42 V", "derivation": "η = |E_*OH - 0.86| (理想值)"}\n'
            '    ],\n'
            '    "comparison_with_literature": [\n'
            '      {"metric": "E1/2", "predicted": "0.85 V", "literature": "0.82 V (Co-N4)", "deviation": "+3.7%"}\n'
            '    ],\n'
            '    "feasibility_conclusion": "基于d-band标度关系推导，Co-N3催化剂的ORR过电势预测为0.42V，处于火山图峰值附近，可行性高"\n'
            '  },\n'
            '  "structured_extraction_table": [\n'
            '    {"paper_id": "P1", "title": "Evidence Paper", "materials_or_reaction": "Co-N-C / ORR", "key_data": "E1/2, Tafel slope", "mechanism": "*OH adsorption", "limitation": "acidic stability", "evidence_status": "需核验"}\n'
            '  ],\n'
            '  "database_schema": {"table_name": "electrocatalysis_literature_evidence", "fields": [{"name": "reaction_type", "description": "反应类型", "required": true}]},\n'
            '  "literature_network": {"node_groups": {"反应机制核心文献": ["P1"], "材料设计文献": []}, "evidence_edges": [{"from": "P1", "to": "P2", "relation": "P1提供机制，P2提供实验基线"}], "network_text": "P1支持P2的机理解释"},\n'
            '  "experiment_record_card": {"hypothesis_id": "H-001", "research_direction": "电催化", "hypothesis_statement": "可验证假设", "variables_to_test": ["配位数"], "control_group": "Co-N4", "experimental_group": "Co-N3", "testing_protocol": "LSV/Tafel/EIS", "expected_metrics": ["过电位", "Tafel斜率"], "risk_points": "结构重构导致活性位变化"},\n'
            '  "reference_status": "仅保留白名单或用户导入来源；未核验条目标注需核验",\n'
            '  "references": [{"authors": "...", "title": "...", "journal": "...", "year": 2024, "doi": "..."}],\n'
            '  "confidence": 7,\n'
            '  "feasibility": "高",\n'
            '  "review_notes": "综合评审意见"\n'
            "}\n\n"
            "严格规则：\n"
            "1. 所有字段都必须填写，不能留空。以上示例仅为格式参考，请根据实际假设内容填写\n"
            "2. datasets.source 必须描述具体的文献数据（如参数名+数值+来源），不能只写\"文献数据\"\n"
            "3. datasets.target 必须描述需要采集的数据特征（表征手段+量纲），不能只写\"实验数据\"\n"
            "4. experiments.baselines 必须列出具体的基线方法名称（至少2个），不能为空数组\n"
            "5. experiments.metrics 必须列出具体的评估指标名（至少2个），不能为空数组\n"
            "6. experiments.design 必须包含电解质/测试条件等关键信息，不能只写\"电化学测试\"\n"
            "7. results.derivation 必须是完整的公式推导链，每一步标注使用的公式和假设条件，"
            "    不能只写结论不写推导过程；推导链长度不少于 100 字\n"
            "8. results.calculated_values 必须至少包含 2 个具体计算值，"
            "    每个值必须标注参数名、数值、单位和推导公式\n"
            "9. results.comparison_with_literature 必须至少与 1 个文献值对比，"
            "    标注预测值、文献值、偏差百分比\n"
            "10. results.feasibility_conclusion 必须包含量化结论，"
            "    如'偏差<15%，假设可行'，不能只写'可行'或'不可行'\n"
            "11. references 字段必须存在。每条文献必须包含 authors、title、journal、year 字段（doi 可选），且必须来自白名单、用户导入、公开元数据或合规开放来源。"
            "    如果没有可信来源，允许 references = []，但必须在 reference_status 写明缺口，严禁编造。\n"
            "12. expected_results 字段已废弃，如有内容请合并到 results.feasibility_conclusion 中\n"
            "13. structured_extraction_table、database_schema、literature_network、paper_titles、experiment_record_card、reference_status 必须输出。\n"
        )

        result = self._call_llm(prompt, timeout=HYPOTHESIS_LLM_TIMEOUT_SECONDS)
        return AgentMessage(
            role=self.role,
            content=result,
            metadata={},
        )


# ─────────────────────────────────────────────────────────────
# SchemaValidator — 输出完整性后置校验
# ─────────────────────────────────────────────────────────────

class SchemaValidator:
    """
    对 OutputAgent 输出的 JSON 进行后置校验：
      1. 检查 HYPOTHESIS_OUTPUT_SCHEMA 中所有必填字段是否齐全
      2. 缺失字段用默认值填充
      3. 关键嵌套字段（datasets/experiments/results）缺失时触发 LLM 补填
      4. 返回校验报告 + 修复后的数据
    """

    # 必填的顶层字段及其默认值
    REQUIRED_TOP_FIELDS = {
        "problem_statement": "",
        "rationale": "",
        "technical_details": "",
        "paper_title": "未命名假设",
        "paper_titles": [],
        "paper_abstract": "",
        "methods": "",
        "expected_results": "",
        "structured_extraction_table": [],
        "database_schema": {},
        "literature_network": {},
        "experiment_record_card": {},
        "reference_status": "",
        "confidence": 5,
        "feasibility": "中",
    }

    # 必填的嵌套字段及其默认结构
    REQUIRED_NESTED_FIELDS = {
        "datasets": {"source": "", "target": ""},
        "experiments": {"baselines": [], "metrics": [], "design": ""},
        "results": {
            "verification_method": "",
            "derivation": "",
            "calculated_values": [],
            "comparison_with_literature": [],
            "feasibility_conclusion": "",
        },
    }

    def __init__(self, config: Optional[LLMConfig] = None):
        self.config = _ensure_config(config)

    def validate(self, data: dict, citation_whitelist: list = None) -> dict:
        """
        校验并修复输出数据。

        Args:
            data: OutputAgent 输出的原始 JSON 字典
            citation_whitelist: 引用白名单列表（可选）

        Returns:
            {"data": 修复后的数据, "report": {"missing_filled": [...], "nested_refilled": [...]}}
        """
        report = {"missing_filled": [], "nested_refilled": []}

        if not isinstance(data, dict):
            return {"data": data, "report": {"error": "输出不是有效的JSON对象"}}

        for field_name, default_val in self.REQUIRED_TOP_FIELDS.items():
            if field_name not in data or not data[field_name]:
                data[field_name] = default_val
                report["missing_filled"].append(field_name)

        fields_need_llm_refill = []
        for field_name, default_struct in self.REQUIRED_NESTED_FIELDS.items():
            if field_name not in data or not data[field_name]:
                data[field_name] = default_struct
                fields_need_llm_refill.append(field_name)
                report["nested_refilled"].append(field_name)
            elif isinstance(data[field_name], dict) and isinstance(default_struct, dict):
                for sub_field, sub_default in default_struct.items():
                    if sub_field not in data[field_name] or not data[field_name][sub_field]:
                        data[field_name][sub_field] = sub_default
                        sub_key = f"{field_name}.{sub_field}"
                        if sub_field in ("calculated_values", "comparison_with_literature", "baselines", "metrics"):
                            if field_name not in fields_need_llm_refill:
                                fields_need_llm_refill.append(field_name)
                                report["nested_refilled"].append(sub_key)
                        else:
                            report["missing_filled"].append(sub_key)

        if "expected_results" in data and data["expected_results"]:
            if "results" in data and isinstance(data["results"], dict):
                existing = data["results"].get("feasibility_conclusion", "")
                data["results"]["feasibility_conclusion"] = (
                    f"{existing}\n\n预期结果: {data['expected_results']}".strip()
                )
            data["expected_results"] = ""

        if "results" in data and isinstance(data["results"], dict):
            results = data["results"]
            critical_text_fields = ["derivation", "feasibility_conclusion"]
            for sub_field in critical_text_fields:
                if sub_field in results:
                    value = str(results[sub_field]).strip()
                    if len(value) < 10:
                        results[sub_field] = ""
                        if "results" not in fields_need_llm_refill:
                            fields_need_llm_refill.append("results")
                            report["nested_refilled"].append(f"results.{sub_field}")

            critical_list_fields = ["calculated_values", "comparison_with_literature"]
            for sub_field in critical_list_fields:
                if sub_field in results:
                    value = results[sub_field]
                    if not isinstance(value, list) or len(value) < 1:
                        results[sub_field] = []
                        if "results" not in fields_need_llm_refill:
                            fields_need_llm_refill.append("results")
                            report["nested_refilled"].append(f"results.{sub_field}")

        if "references" not in data or not data["references"]:
            # 尝试从 citation_whitelist 填充 references
            if citation_whitelist:
                auto_refs = []
                for w in citation_whitelist:
                    ref_entry = {}
                    if w.get("source") == "doi":
                        ref_entry = {
                            "authors": w.get("authors", ""),
                            "title": w.get("title", ""),
                            "journal": "",
                            "year": w.get("year", ""),
                            "doi": w.get("doi", ""),
                        }
                    elif w.get("source") == "author_year":
                        ref_entry = {
                            "authors": w.get("authors", ""),
                            "title": "",
                            "journal": "",
                            "year": w.get("year", ""),
                        }
                    elif w.get("source") == "literature_fact":
                        ref_entry = {
                            "authors": "",
                            "title": w.get("reference", "")[:100],
                            "journal": "",
                            "year": "",
                        }
                    if ref_entry and any(ref_entry.values()):
                        auto_refs.append(ref_entry)
                data["references"] = auto_refs if auto_refs else []
            else:
                data["references"] = []
            report["missing_filled"].append("references")

        if citation_whitelist is not None:
            ref_report = self._validate_references_whitelist(data, citation_whitelist)
            report["reference_whitelist"] = ref_report
            if ref_report["removed_count"] > 0:
                report.setdefault("warnings", []).append(
                    f"已移除 {ref_report['removed_count']} 条不在白名单内的引用"
                )

        if fields_need_llm_refill:
            data = self._llm_refill_fields(data, fields_need_llm_refill)

        return {"data": data, "report": report}

    def _validate_references_whitelist(self, data: dict, whitelist: list) -> dict:
        removed = []
        valid_refs = []

        for ref in data.get("references", []):
            if not isinstance(ref, dict):
                removed.append({"ref": str(ref)[:80], "reason": "非标准格式"})
                continue

            matched = False
            ref_doi = ref.get("doi", "").strip()
            ref_authors = ref.get("authors", "").strip()
            ref_year = ref.get("year", 0)
            ref_title = ref.get("title", "").strip()

            for w in whitelist:
                if ref_doi and w.get("doi") and ref_doi == w["doi"]:
                    matched = True
                    break
                if (ref_authors and ref_year
                        and w.get("authors") and w.get("year")
                        and ref_year == w["year"]):
                    w_authors_lower = w["authors"].lower()
                    if any(part in ref_authors.lower() for part in w_authors_lower.split() if len(part) > 2):
                        matched = True
                        break
                if ref_title and w.get("context"):
                    w_words = set(w["context"].lower().split())
                    r_words = set(ref_title.lower().split())
                    overlap = len(w_words & r_words)
                    if overlap > 0 and overlap / max(len(r_words), 1) > 0.5:
                        matched = True
                        break
                if ref_title and w.get("reference"):
                    w_words = set(w["reference"].lower().split())
                    r_words = set(ref_title.lower().split())
                    overlap = len(w_words & r_words)
                    if overlap > 0 and overlap / max(len(r_words), 1) > 0.5:
                        matched = True
                        break

            if matched:
                valid_refs.append(ref)
            else:
                removed.append({
                    "ref": f"{ref_authors} ({ref_year}) - {ref_title[:50]}",
                    "reason": "不在引用白名单内",
                })

        data["references"] = valid_refs
        return {"removed_count": len(removed), "removed_details": removed}

    def _llm_refill_fields(self, data: dict, missing_fields: list) -> dict:
        """
        用 LLM 根据已有上下文补填缺失的关键嵌套字段。

        只对 datasets/experiments/results 缺失时触发。
        """
        # 构建已有上下文摘要
        context_parts = []
        if data.get("problem_statement"):
            context_parts.append(f"问题: {data['problem_statement'][:300]}")
        if data.get("rationale"):
            context_parts.append(f"推理依据: {data['rationale'][:300]}")
        if data.get("methods"):
            context_parts.append(f"方法: {data['methods'][:300]}")
        if data.get("expected_results"):
            context_parts.append(f"预期结果: {data['expected_results'][:300]}")
        if data.get("technical_details"):
            context_parts.append(f"技术细节: {data['technical_details'][:300]}")

        context = "\n".join(context_parts)
        if not context:
            return data  # 没有足够上下文，无法补填

        field_instructions = []
        if "datasets" in missing_fields:
            field_instructions.append(
                '  "datasets": {"source": "文献数据描述", "target": "拟采集数据特征"}'
            )
        if "experiments" in missing_fields:
            field_instructions.append(
                '  "experiments": {"baselines": ["基线方法"], "metrics": ["评估指标"], "design": "实验设计"}'
            )
        if "results" in missing_fields:
            field_instructions.append(
                '  "results": {"verification_method": "方法", "derivation": "推导过程", '
                '"calculated_values": [{"parameter": "名", "value": "值", "derivation": "依据"}], '
                '"comparison_with_literature": [{"metric": "名", "predicted": "预测值", "literature": "文献值", "deviation": "偏差"}], '
                '"feasibility_conclusion": "结论"}'
            )

        prompt = (
            "你是一名科学研究助理。根据以下研究假设的上下文，补填缺失的字段。\n"
            "请务必全部使用中文输出。\n\n"
            f"【已有上下文】:\n{context}\n\n"
            f"需要补填的字段:\n" + "\n".join(field_instructions) + "\n\n"
            "请以 JSON 格式输出（不加 markdown 代码块），只输出需要补填的字段：\n"
            "{\n"
            + ",\n".join(field_instructions) + "\n"
            "}\n"
        )

        try:
            result = _chat(prompt, self.config, task="compare", timeout=120)
            _raise_if_llm_error(result, stage="SchemaValidator 字段补填")
            result = result.strip()
            if result.startswith("```"):
                result = result.split("\n", 1)[1].rsplit("```", 1)[0].strip()

            parsed = json.loads(result)
            if isinstance(parsed, dict):
                # 合并补填结果（不覆盖已有值）
                for key, value in parsed.items():
                    if key in missing_fields and (key not in data or not data[key]):
                        data[key] = value
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"SchemaValidator LLM 补填失败: {e}")

        return data

    @staticmethod
    def validate_and_fix(data: dict, config: Optional[LLMConfig] = None, citation_whitelist: list = None) -> dict:
        """
        便捷函数：校验并修复输出数据。

        Returns:
            修复后的数据字典
        """
        validator = SchemaValidator(config=config)
        result = validator.validate(data, citation_whitelist=citation_whitelist)
        return result["data"]


class DatasetSourceEnricher:

    def enrich(self, hypothesis_json: str) -> str:
        from knowledge_base import ExternalKnowledgeAdapter

        hypo_data = self._safe_parse(hypothesis_json)
        if not hypo_data:
            return ""

        material_names = self._extract_material_names(hypo_data)
        results = []
        for name in material_names[:5]:
            mp_data = ExternalKnowledgeAdapter.query_material(name)
            if mp_data:
                results.append({
                    "name": name,
                    "source": "Materials Project",
                    "url": f"https://materialsproject.org/materials/{name}",
                    "properties": {
                        "band_gap": mp_data.get("band_gap"),
                        "formation_energy": mp_data.get("formation_energy_per_atom"),
                    },
                })
                continue

            pc_data = ExternalKnowledgeAdapter.query_compound_properties(name)
            if pc_data:
                results.append({
                    "name": name,
                    "source": "PubChem",
                    "url": f"https://pubchem.ncbi.nlm.nih.gov/compound/{name}",
                    "properties": {
                        "molecular_formula": pc_data.get("MolecularFormula"),
                        "molecular_weight": pc_data.get("MolecularWeight"),
                    },
                })

        if not results:
            return ""

        lines = ["【真实数据集来源（来自外部知识库验证）】："]
        for r in results:
            props_str = ", ".join(
                f"{k}={v}" for k, v in r["properties"].items() if v is not None
            )
            lines.append(
                f"- {r['name']}: 来源={r['source']}, "
                f"URL={r['url']}, 性质({props_str})"
            )
        return "\n".join(lines)

    @staticmethod
    def _extract_material_names(hypo_data: dict) -> list:
        import re
        names = []
        for field in ["technical_details", "methods", "problem_statement"]:
            text = str(hypo_data.get(field, ""))
            formulas = re.findall(
                r'\b([A-Z][a-z]?(?:\d+)?(?:[A-Z][a-z]?(?:\d+)?)+(?:/\w+)?)\b',
                text,
            )
            names.extend(formulas)
        datasets = hypo_data.get("datasets", {})
        if isinstance(datasets, dict):
            source = datasets.get("source", "")
            if source:
                names.append(source)
        return list(dict.fromkeys(names))[:10]

    @staticmethod
    def _safe_parse(text):
        try:
            return json.loads(text) if isinstance(text, str) else text
        except (json.JSONDecodeError, TypeError):
            return None


class VerifiabilityScorer:

    DIMENSION_WEIGHTS = {
        "experimental_complexity": 0.35,
        "data_availability": 0.35,
        "technology_readiness": 0.30,
    }

    def score(self, hypothesis_json: str, validation_json: str = "") -> dict:
        hypo_data = self._safe_parse(hypothesis_json)
        val_data = self._safe_parse(validation_json) or {}

        exp_score = self._score_experimental_complexity(hypo_data, val_data)
        data_score = self._score_data_availability(hypo_data, val_data)
        tech_score = self._score_technology_readiness(hypo_data, val_data)

        total = (
            exp_score["score"] * self.DIMENSION_WEIGHTS["experimental_complexity"]
            + data_score["score"] * self.DIMENSION_WEIGHTS["data_availability"]
            + tech_score["score"] * self.DIMENSION_WEIGHTS["technology_readiness"]
        )

        level = (
            "高可验证" if total >= 75
            else "中等可验证" if total >= 50
            else "低可验证"
        )

        suggestions = []
        if exp_score["score"] < 60:
            suggestions.append("建议简化实验设计，使用更常规的表征手段")
        if data_score["score"] < 60:
            suggestions.append("建议使用公开数据集（Materials Project/PubChem）作为验证基准")
        if tech_score["score"] < 60:
            suggestions.append("建议引用已有成熟技术路线，降低技术风险")

        return {
            "verifiability_score": round(total, 1),
            "dimensions": {
                "experimental_complexity": exp_score,
                "data_availability": data_score,
                "technology_readiness": tech_score,
            },
            "level": level,
            "improvement_suggestions": suggestions,
        }

    @staticmethod
    def _score_experimental_complexity(hypo_data, val_data) -> dict:
        score = 70
        methods = str(hypo_data.get("methods", ""))
        conventional = ["XRD", "XPS", "SEM", "TEM", "Raman", "FTIR", "UV-Vis", "BET"]
        for c in conventional:
            if c.lower() in methods.lower():
                score += 3
        advanced = ["同步辐射", "中子衍射", "原位TEM", "自由电子激光"]
        for a in advanced:
            if a in methods:
                score -= 10
        if val_data.get("computationally_feasible"):
            score += 5
        if val_data.get("experimental_design_valid"):
            score += 5
        score = max(0, min(100, score))
        return {
            "score": score,
            "detail": f"常规表征覆盖: {sum(1 for c in conventional if c.lower() in methods.lower())}/{len(conventional)}",
        }

    @staticmethod
    def _score_data_availability(hypo_data, val_data) -> dict:
        score = 50
        datasets = hypo_data.get("datasets", {})
        source = str(datasets.get("source", "") if isinstance(datasets, dict) else "")
        public_sources = ["Materials Project", "PubChem", "ICSD", "COD", "NIST"]
        for ps in public_sources:
            if ps.lower() in source.lower():
                score += 15
        data_avail = val_data.get("data_availability", {})
        if isinstance(data_avail, dict):
            if data_avail.get("source_data_available"):
                score += 10
            if data_avail.get("target_data_acquirable"):
                score += 10
        score = max(0, min(100, score))
        return {"score": score, "detail": f"数据来源: {source[:50] if source else '未指定'}"}

    @staticmethod
    def _score_technology_readiness(hypo_data, val_data) -> dict:
        score = 60
        tech = str(hypo_data.get("technical_details", ""))
        mature_tech = ["DFT", "分子动力学", "有限元", "机器学习", "深度学习"]
        for mt in mature_tech:
            if mt in tech:
                score += 5
        novel_tech = ["量子计算", "全新合成路线", "未报道的"]
        for nt in novel_tech:
            if nt in tech:
                score -= 8
        feasibility = val_data.get("overall_feasibility", "中")
        if feasibility == "高":
            score += 15
        elif feasibility == "低":
            score -= 15
        score = max(0, min(100, score))
        return {"score": score, "detail": f"可行性: {feasibility}"}

    @staticmethod
    def _safe_parse(text):
        try:
            return json.loads(text) if isinstance(text, str) else text
        except (json.JSONDecodeError, TypeError):
            return None


class ExperimentTemplateRecommender:

    def recommend(self, hypothesis_json: str, domain: str = "") -> str:
        from knowledge_base import get_domain_profile

        if not domain:
            return ""

        profile = get_domain_profile(domain)
        if not profile:
            return ""

        templates = profile.experiment_templates
        if not templates:
            return ""

        hypo_data = self._safe_parse(hypothesis_json)
        if not hypo_data:
            return ""

        hypo_text = json.dumps(hypo_data, ensure_ascii=False).lower()
        matched = []

        for tmpl_name, tmpl_content in templates.items():
            if isinstance(tmpl_content, dict):
                keywords = tmpl_content.get("keywords", [])
                if isinstance(keywords, list):
                    overlap = sum(1 for k in keywords if k.lower() in hypo_text)
                    if overlap > 0:
                        matched.append((tmpl_name, tmpl_content, overlap))
                else:
                    matched.append((tmpl_name, tmpl_content, 1))
            else:
                matched.append((tmpl_name, tmpl_content, 1))

        matched.sort(key=lambda x: x[2], reverse=True)
        top_matched = matched[:3]

        if not top_matched:
            default_templates = list(templates.items())[:2]
            top_matched = [(n, c, 0) for n, c in default_templates]

        lines = ["【推荐实验设计模板（来自领域知识库）】："]
        for tmpl_name, tmpl_content, _ in top_matched:
            lines.append(f"\n模板: {tmpl_name}")
            if isinstance(tmpl_content, dict):
                for k, v in tmpl_content.items():
                    if k != "keywords":
                        lines.append(f"  {k}: {v}")
            else:
                lines.append(f"  {tmpl_content}")

        lines.append(
            "\n请在 methods 和 experiments 中参考上述模板结构，"
            "确保实验设计符合领域规范。"
        )
        return "\n".join(lines)

    @staticmethod
    def _safe_parse(text):
        try:
            return json.loads(text) if isinstance(text, str) else text
        except (json.JSONDecodeError, TypeError):
            return None


class ClosedLoopVerifier:
    """
    智能闭环回验：将关键结论与原始文献数据交叉校验。
    偏差超过阈值时触发假设修订，形成验证-修订闭环。
    """

    MAX_REVISION_LOOPS = 2
    DEVIATION_THRESHOLD = 0.30

    def verify(
        self,
        hypothesis_json: str,
        results_verification_json: str,
        literature_facts: str,
        scaling_context: str = "",
    ) -> dict:
        hypo_data = self._safe_parse(hypothesis_json)
        rv_data = self._safe_parse(results_verification_json)
        lit_data = self._safe_parse(literature_facts)

        if not hypo_data or not rv_data:
            return {"needs_revision": False, "max_deviation": 0, "deviation_details": []}

        calculated_values = rv_data.get("calculated_values", [])
        comparisons = rv_data.get("comparison_with_literature", [])

        deviations = []
        for comp in comparisons:
            calculated = comp.get("calculated_value")
            literature = comp.get("literature_value")
            if calculated is not None and literature is not None:
                try:
                    calc_num = self._extract_number(str(calculated))
                    lit_num = self._extract_number(str(literature))
                    if calc_num is not None and lit_num is not None and lit_num != 0:
                        deviation = abs(calc_num - lit_num) / abs(lit_num)
                        deviations.append({
                            "parameter": comp.get("parameter", "未知"),
                            "calculated": calc_num,
                            "literature": lit_num,
                            "deviation": deviation,
                            "deviation_pct": f"{deviation*100:.1f}%",
                            "status": "严重偏差" if deviation > self.DEVIATION_THRESHOLD
                                      else "可接受" if deviation > 0.15
                                      else "良好",
                        })
                except (ValueError, TypeError):
                    pass

        known_ranges = self._extract_known_ranges(lit_data)
        for cv in calculated_values:
            param_name = cv.get("parameter", cv.get("name", ""))
            value = self._extract_number(str(cv.get("value", "")))
            if value is not None and known_ranges:
                for kr in known_ranges:
                    if kr["parameter"].lower() in param_name.lower():
                        if not (kr["min"] <= value <= kr["max"]):
                            deviations.append({
                                "parameter": param_name,
                                "calculated": value,
                                "literature_range": f"{kr['min']}-{kr['max']} {kr.get('unit', '')}",
                                "deviation": 1.0,
                                "deviation_pct": "超出已知范围",
                                "status": "严重偏差",
                            })

        max_deviation = max((d["deviation"] for d in deviations), default=0)
        needs_revision = max_deviation > self.DEVIATION_THRESHOLD

        revision_hints = ""
        if needs_revision:
            severe = [d for d in deviations if d["status"] == "严重偏差"]
            hint_lines = []
            for s in severe:
                hint_lines.append(
                    f"- 参数 {s['parameter']}: 计算值 {s['calculated']} "
                    f"与文献值 {s.get('literature', s.get('literature_range', '?'))} "
                    f"偏差 {s['deviation_pct']}，需要修正"
                )
            revision_hints = (
                "【闭环回验发现以下严重偏差，请修订假设中的对应参数】：\n"
                + "\n".join(hint_lines)
            )

        return {
            "needs_revision": needs_revision,
            "deviation_details": deviations,
            "max_deviation": max_deviation,
            "cross_validation_report": self._format_report(deviations),
            "revision_hints": revision_hints,
        }

    @staticmethod
    def _extract_number(text: str):
        import re
        match = re.search(r'[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?', text)
        return float(match.group()) if match else None

    @staticmethod
    def _extract_known_ranges(lit_data):
        ranges = []
        if not isinstance(lit_data, dict):
            return ranges
        for field in ["key_parameters", "data_support"]:
            items = lit_data.get(field, [])
            if isinstance(items, list):
                for item in items:
                    if isinstance(item, dict) and "range" in item:
                        try:
                            r = item["range"]
                            if isinstance(r, (list, tuple)) and len(r) == 2:
                                ranges.append({
                                    "parameter": item.get("name", item.get("parameter", "")),
                                    "min": float(r[0]),
                                    "max": float(r[1]),
                                    "unit": item.get("unit", ""),
                                })
                        except (ValueError, TypeError):
                            pass
        return ranges

    @staticmethod
    def _format_report(deviations):
        if not deviations:
            return "闭环回验通过：所有计算值与文献数据偏差在可接受范围内。"
        lines = ["闭环回验报告："]
        for d in deviations:
            lines.append(
                f"  {d['parameter']}: 计算={d['calculated']}, "
                f"文献={d.get('literature', d.get('literature_range', '?'))}, "
                f"偏差={d['deviation_pct']} [{d['status']}]"
            )
        return "\n".join(lines)

    @staticmethod
    def _safe_parse(text):
        if not text:
            return None
        try:
            return json.loads(text) if isinstance(text, str) else text
        except (json.JSONDecodeError, TypeError):
            return None


class HypothesisOrchestrator:
    """
    多智能体编排器：管理假设生成的完整流水线。

    流程：
    1. LiteratureAgent          → 提取文献事实
    2. ReasoningChainAgent      → 推理链构建
    3. HypothesisAgent          → 生成初始假设
    4. CrossDomainAnalogyAgent  → 跨学科技术迁移（Step 2.5）
    5. [CritiqueAgent → DebateRound(DevilAdvocate+Optimist) → RevisionAgent] × N轮
    6. ValidationAgent           → 可验证性评估
    7. ResultsVerificationAgent  → 公式推导与实验结果验证
    8. OutputAgent               → 标准化输出

    支持回调函数报告进度。
    """

    def __init__(
        self,
        config: Optional[LLMConfig] = None,
        max_iterations: int = 3,
        on_progress: Optional[Callable[[str, int, int], None]] = None,
        user_feedback_callback: Optional[Callable] = None,
        interaction_history: Optional[dict] = None,
        auto_verify: bool = True,
        multimodal_context: str = "",
        quantitative_context: str = "",
        multimodal_evidence: Optional[Dict] = None,
        quantitative_report: Optional[Dict] = None,
        rag_context: str = "",
        evidence_context: Optional[Dict] = None,
        domain: str = "",
        user_id: int = 0,
        source_doc_id: int = 0,
        source_doc_ids: Optional[List[int]] = None,
        enable_qwen_agent_tools: bool = True,
    ):
        self.config = _ensure_config(config)
        self.max_iterations = max_iterations
        self.on_progress = on_progress
        self.user_feedback_callback = user_feedback_callback
        self.interaction_history = interaction_history or {"interactions": []}  # callback(stage_name, current, total)
        self.auto_verify = auto_verify
        self.multimodal_context = multimodal_context  # 多模态数据注入上下文
        self.quantitative_context = quantitative_context  # 定量标度关系上下文
        self.multimodal_evidence = multimodal_evidence or {}
        self.quantitative_report = quantitative_report or {}
        self.rag_context = rag_context
        self.evidence_context = evidence_context or {}
        self.database_evidence_context = ""
        self.database_evidence_snapshot: Dict = {}
        self._last_debate_data = {}
        self._last_hitl_context = {}
        self.domain = domain  # 研究领域标识
        self.user_id = user_id  # 用户 ID（用于获取用户导入的文档）
        self.source_doc_id = int(source_doc_id or 0)  # 假设页面当前加载的文档 ID
        self.source_doc_ids: List[int] = []
        for doc_id in source_doc_ids or []:
            if not doc_id:
                continue
            normalized = int(doc_id)
            if normalized not in self.source_doc_ids:
                self.source_doc_ids.append(normalized)
        if self.source_doc_id and self.source_doc_id not in self.source_doc_ids:
            self.source_doc_ids.insert(0, self.source_doc_id)
        if not self.source_doc_id and self.source_doc_ids:
            self.source_doc_id = self.source_doc_ids[0]
        self.enable_qwen_agent_tools = enable_qwen_agent_tools
        self.qwen_agent_tool_context: Dict = {}

        # 初始化所有 Agent
        self.literature_agent = LiteratureAgent(config)
        self.hypothesis_agent = HypothesisAgent(config)
        self.cross_domain_agent = CrossDomainAnalogyAgent(config)
        self.critique_agent = CritiqueAgent(config)
        self.devil_advocate_agent = DevilAdvocateAgent(config)
        self.optimist_agent = OptimistAgent(config)
        self.validation_agent = ValidationAgent(config)
        self.results_verification_agent = ResultsVerificationAgent(config)
        self.output_agent = OutputAgent(config)

        # 推理链 Agent（独立于 BaseAgent 继承体系，因其接口不同）
        from reasoning_chain import ReasoningChainAgent
        self.reasoning_chain_agent = ReasoningChainAgent(config)

    def _report(self, stage: str, current: int, total: int):
        if self.on_progress:
            self.on_progress(stage, current, total)

    def _record_interaction(
        self,
        round_num: int,
        hypothesis_json: dict,
        critique_json: Optional[dict],
        user_action: str,
        user_feedback_text: str = "",
        user_edited_hypothesis: Optional[dict] = None,
        debate_stance: str = "neutral",
        selected_attack_indices: Optional[list] = None,
        structured_feedback: Optional[dict] = None,
        manual_revision_diff: Optional[list] = None,
    ) -> dict:
        """记录一次 HITL 交互到 interaction_history"""
        from datetime import datetime
        entry = {
            "round": round_num,
            "stage": "post_critique" if critique_json else "post_initial",
            "hypothesis_snapshot": hypothesis_json,
            "critique_snapshot": critique_json,
            "user_action": user_action,
            "user_feedback_text": user_feedback_text,
            "user_edited_hypothesis": user_edited_hypothesis,
            "debate_stance": debate_stance,
            "selected_attack_indices": selected_attack_indices or [],
            "structured_feedback": structured_feedback or {},
            "manual_revision_diff": manual_revision_diff or [],
            "manual_revision_summary": summarize_diff(manual_revision_diff or []),
            "final_adopted": user_action == "approve",
            "timestamp": datetime.utcnow().isoformat(),
        }
        self.interaction_history["interactions"].append(entry)
        self.interaction_history["total_human_interactions"] = len(
            self.interaction_history["interactions"]
        )
        return entry

    def _update_hitl_context(
        self,
        *,
        research_question: str = "",
        source_mode: str = "",
        pre_search_keywords: Optional[list] = None,
        pre_search_refs: Optional[list] = None,
        pre_search_context: str = "",
        literature_search_diagnostics: Optional[dict] = None,
        literature_facts: Optional[dict] = None,
        literature_facts_raw: str = "",
        reasoning_chain: Optional[dict] = None,
        cross_domain_analogies: Optional[dict] = None,
        debate_data: Optional[dict] = None,
        current_stage: str = "",
    ) -> dict:
        """Build the compact context shown in HITL review dialogs."""
        evidence_source = self.evidence_context.get("source") if isinstance(self.evidence_context, dict) else ""
        default_source_mode = "PaperQA/科学证据 RAG 上下文" if evidence_source == "paperqa" else (
            "RAG 语义检索上下文" if self.rag_context else "原始文献全文/摘要"
        )
        resolved_source_mode = source_mode or default_source_mode
        if evidence_source == "paperqa" and resolved_source_mode == "RAG 语义检索上下文":
            resolved_source_mode = "PaperQA/科学证据 RAG 上下文"
        self._last_hitl_context = {
            "current_stage": current_stage,
            "research_question": research_question or "",
            "source_mode": resolved_source_mode,
            "domain": self.domain or "自动检测",
            "pre_search_keywords": pre_search_keywords or [],
            "pre_search_refs": pre_search_refs or [],
            "pre_search_context": pre_search_context or "",
            "literature_search_diagnostics": (
                literature_search_diagnostics
                or getattr(self, "literature_search_diagnostics", {})
                or {}
            ),
            "literature_facts": literature_facts or {},
            "literature_facts_raw": literature_facts_raw or "",
            "reasoning_chain": reasoning_chain or {},
            "cross_domain_analogies": cross_domain_analogies or {},
            "debate_data": debate_data or self._last_debate_data or {},
            "scientific_evidence_context": self.evidence_context,
            "database_evidence_context": getattr(self, "database_evidence_snapshot", {}) or {},
            "rag_context_excerpt": (self.rag_context or "")[:4000],
            "multimodal_context_excerpt": (self.multimodal_context or "")[:3000],
            "quantitative_context_excerpt": (self.quantitative_context or "")[:3000],
            "qwen_agent_tool_context": getattr(self, "qwen_agent_tool_context", {}) or {},
        }
        return self._last_hitl_context

    @staticmethod
    def _feedback_trace_kwargs(feedback) -> dict:
        return {
            "debate_stance": getattr(feedback, "debate_stance", "neutral"),
            "selected_attack_indices": getattr(feedback, "selected_attack_indices", []),
            "structured_feedback": getattr(feedback, "structured_feedback", {}),
            "manual_revision_diff": getattr(feedback, "manual_revision_diff", []),
        }

    @staticmethod
    def _attach_ai_revision_trace(
        entry: Optional[dict],
        before_hypothesis: dict,
        after_hypothesis: dict,
        source: str,
    ):
        if not entry:
            return
        diff = build_field_diff(before_hypothesis, after_hypothesis)
        entry["ai_revision"] = {
            "source": source,
            "diff": diff,
            "summary": summarize_diff(diff),
            "final_adopted": True,
        }
        entry["ai_revised_hypothesis"] = after_hypothesis
        entry["final_adopted"] = True

    @staticmethod
    def _parse_json_raw(text: str) -> dict:
        """容错 JSON 解析"""
        import re
        llm_error = _llm_error_from_text(text)
        if llm_error:
            return {"llm_error": True, **llm_error}
        text = text.strip()
        # 移除 qwen3 的 thinking 标签
        text = re.sub(r'<think[^>]*>.*?</think\s*>', '', text, flags=re.DOTALL)
        if text.startswith("```"):
            lines = text.split("\n")
            lines = [l for l in lines if not l.strip().startswith("```")]
            text = "\n".join(lines)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            for start_char, end_char in [("{", "}"), ("[", "]")]:
                start = text.find(start_char)
                end = text.rfind(end_char)
                if start != -1 and end != -1 and end > start:
                    try:
                        return json.loads(text[start:end + 1])
                    except json.JSONDecodeError:
                        continue
            return {"raw_text": text, "parse_error": True}

    @staticmethod
    def _extract_citation_whitelist(literature_facts: str, raw_text: str) -> list:
        import re
        whitelist = []
        seen = set()

        bracket_refs = re.findall(r'\[(\d+)\]\s*([^\[]*?)(?=\[\d+\]|$)', raw_text)
        for num, context in bracket_refs:
            key = f"bracket_{num}"
            if key not in seen:
                seen.add(key)
                whitelist.append({
                    "source": "bracket_ref",
                    "ref_id": num,
                    "context": context.strip()[:200],
                })

        author_year_refs = re.findall(
            r'\(([A-Z][a-z]+(?:\s+et\s+al\.)?(?:\s+&\s+[A-Z][a-z]+)?),\s*(\d{4})\)',
            raw_text,
        )
        for author, year in author_year_refs:
            key = f"{author}_{year}"
            if key not in seen:
                seen.add(key)
                whitelist.append({
                    "source": "author_year",
                    "authors": author,
                    "year": int(year),
                })

        doi_refs = re.findall(r'10\.\d{4,9}/[^\s\]"]+', raw_text)
        for doi in doi_refs:
            doi = doi.rstrip('.,;)')
            if doi not in seen:
                seen.add(doi)
                whitelist.append({
                    "source": "doi",
                    "doi": doi,
                })

        lit_data = HypothesisOrchestrator._parse_json_raw(literature_facts)
        if isinstance(lit_data, dict):
            for field in ["key_findings", "data_support", "existing_methods"]:
                items = lit_data.get(field, [])
                if isinstance(items, list):
                    for item in items:
                        if isinstance(item, dict) and item.get("reference"):
                            ref_str = str(item["reference"])
                            if ref_str not in seen:
                                seen.add(ref_str)
                                whitelist.append({
                                    "source": "literature_fact",
                                    "reference": ref_str,
                                })

        return whitelist

    def run(
        self,
        literature_text: str,
        research_question: str = "",
    ) -> HypothesisResult:
        """
        执行完整的假设生成流水线。

        Args:
            literature_text: 文献全文或摘要
            research_question: 用户自定义的研究问题（可选）

        Returns:
            HypothesisResult 包含最终假设和迭代历史
        """
        result = HypothesisResult()
        self.interaction_history.setdefault("mode", "hitl" if self.user_feedback_callback else "auto")
        self.interaction_history.setdefault("interactions", [])
        self.interaction_history.setdefault("schema_version", "hitl-interaction-v1")

        # 构建上下文：RAG 语义检索优先，否则用全文
        # 优先级：rag_context（精准检索） > literature_text（全文截断）
        if self.rag_context:
            full_text = self.rag_context
        else:
            full_text = literature_text

        # 注入多模态上下文（如果有的话）
        if self.multimodal_context:
            mm_section = (
                "【以下内容为多模态数据分析结果（来自图表/图像/表格的自动识别），"
                "请将其与文本文献内容交叉验证，标注来源为[多模态]】:\n"
                f"{self.multimodal_context}\n"
                "【多模态内容结束】\n\n"
            )
            full_text = mm_section + full_text

        database_evidence_context = ""
        database_evidence_snapshot = {}
        try:
            if self.user_id:
                from db import get_session
                from evidence_database import (
                    evidence_search_bundle_to_dict,
                    extract_query_facets,
                    format_database_evidence_context,
                    query_evidence,
                )

                structured_multimodal_query = format_structured_multimodal_evidence_for_query(
                    self.multimodal_evidence
                )
                query_basis = "\n".join(
                    part
                    for part in [
                        research_question,
                        literature_text[:2000],
                        self.multimodal_context[:1000],
                        structured_multimodal_query[:2000],
                    ]
                    if part
                )
                facets = extract_query_facets(query_basis, self.domain)
                with get_session() as db_session:
                    bundle = query_evidence(
                        db_session,
                        user_id=self.user_id,
                        domain=self.domain,
                        query_text=query_basis,
                        reaction_type=facets.get("reaction_type", ""),
                        battery_type=facets.get("battery_type", ""),
                        ion_type=facets.get("ion_type", ""),
                        material=facets.get("material", ""),
                        adsorbates=facets.get("adsorbates", []),
                    )
                    database_evidence_context = format_database_evidence_context(
                        bundle, domain=self.domain
                    )
                    database_evidence_snapshot = evidence_search_bundle_to_dict(bundle)
                    database_evidence_snapshot["facets"] = facets
                if database_evidence_context:
                    full_text = database_evidence_context + "\n\n" + full_text
                self.database_evidence_context = database_evidence_context
                self.database_evidence_snapshot = database_evidence_snapshot
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(
                f"自建数据库证据检索失败（不影响主流程）: {e}"
            )
            database_evidence_context = (
                "【自建数据库证据上下文】\n"
                "数据库证据检索失败或不可用；请输出“证据不足/需核验”，不得编造数据库证据。"
            )
            database_evidence_snapshot = {"error": str(e), "warnings": ["database evidence lookup failed"]}
            self.database_evidence_context = database_evidence_context
            self.database_evidence_snapshot = database_evidence_snapshot

        # ── Step 0-A: Qwen-Agent 官方工具增强（Function Calling / RAG / MCP 入口） ──
        if self.enable_qwen_agent_tools:
            try:
                from qwen_agent_bridge import (
                    ChalkToolContext,
                    build_chalk_qwen_tool_context,
                    format_tool_context,
                    qwen_agent_available,
                )

                if qwen_agent_available():
                    self._report("Qwen-Agent 工具增强", 0, 10)
                    agent_tool_ctx = build_chalk_qwen_tool_context(
                        research_question or literature_text[:300],
                        config=self.config,
                        context=ChalkToolContext(
                            user_id=self.user_id,
                            doc_id=self.source_doc_id or (
                                self.source_doc_ids[0] if self.source_doc_ids else None
                            ),
                            api_key=self.config.api_key or "",
                            domain=self.domain,
                            base_dir=os.getcwd(),
                            max_rag_chunks=8,
                            min_rag_score=0.25,
                        ),
                        use_official_agent=True,
                    )
                    self.qwen_agent_tool_context = agent_tool_ctx
                    tool_context_text = agent_tool_ctx.get("summary", "")
                    if not tool_context_text and agent_tool_ctx.get("tool_calls"):
                        tool_context_text = format_tool_context(agent_tool_ctx["tool_calls"])
                    if tool_context_text:
                        full_text = (
                            "【Qwen-Agent 工具增强上下文】\n"
                            "以下内容由官方 Qwen-Agent 工具调用层生成或整理，"
                            "包括 Chalk RAG、Crossref/PubChem/报告工具可追溯结果。"
                            "请优先把它作为证据候选，并继续执行引用真实性校验。\n"
                            f"{tool_context_text}\n"
                            "【Qwen-Agent 工具增强上下文结束】\n\n"
                            + full_text
                        )
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning(
                    f"Qwen-Agent 工具增强失败，继续使用原管线: {e}"
                )
                self.qwen_agent_tool_context = {"ok": False, "error": str(e)}

        # ── Step 0: 预搜索开放文献（在假设生成之前） ──
        # 基于用户文档 + multimodal 数据提取关键词，搜索 OA 文献作为补充上下文
        pre_search_context = ""
        pre_search_refs = []
        pre_keywords = []
        pre_search_diagnostics = {}
        self.literature_search_diagnostics = {
            "pre_search": {},
            "post_search": {},
            "warnings": [],
        }
        try:
            from literature_search import (
                LiteratureSearchEngine,
                SearchQuery,
                _extract_search_keywords,
                get_user_document_references,
            )

            # 从用户文档标题 + 全文前段 + multimodal 数据中提取关键词
            pre_search_data = {
                "problem_statement": full_text[:2000],  # 文献内容前段
                "rationale": research_question or "",
                "technical_details": self.multimodal_context[:1000] if self.multimodal_context else "",
            }
            pre_keywords = _extract_search_keywords(pre_search_data)

            if pre_keywords:
                self._report("预搜索开放文献", 0, 10)
                engine = LiteratureSearchEngine(
                    platforms={
                        "arxiv": True,
                        "crossref": True,
                        "semantic_scholar": bool(os.getenv("SEMANTIC_SCHOLAR_API_KEY", "").strip()),
                        "doaj": False,  # 预搜索阶段跳过较慢的平台
                        "pmc": False,
                    },
                    timeout=12,
                )
                query = SearchQuery(
                    keywords=pre_keywords,
                    domain=self.domain,
                    max_results=3,
                    year_from="2018",
                )
                pre_diagnostic_result = engine.search_with_diagnostics(query)
                pre_results = pre_diagnostic_result.results
                pre_search_diagnostics = pre_diagnostic_result.to_dict()
                self.literature_search_diagnostics["pre_search"] = pre_search_diagnostics
                self.literature_search_diagnostics["warnings"].extend(
                    pre_search_diagnostics.get("warnings", []) or []
                )
                pre_search_refs = [r.to_reference_dict() for r in pre_results]

                if pre_search_refs:
                    # 构建预搜索文献上下文，注入到 full_text 前面
                    ref_lines = []
                    for ref in pre_search_refs:
                        authors = ref.get("authors", "")
                        title = ref.get("title", "")
                        journal = ref.get("journal", "")
                        year = ref.get("year", "")
                        doi = ref.get("doi", "")
                        abstract = ref.get("abstract", ref.get("url", ""))
                        line = f"- {authors}, \"{title}\""
                        if journal:
                            line += f", {journal}"
                        if year:
                            line += f", {year}"
                        if doi:
                            line += f" (DOI: {doi})"
                        if abstract:
                            line += f"\n  摘要: {abstract[:200]}"
                        ref_lines.append(line)

                    pre_search_context = (
                        "【以下为系统自动检索到的相关开放文献，请在假设生成时参考并引用】:\n"
                        + "\n".join(ref_lines)
                        + "\n【预搜索文献结束】\n\n"
                    )
                    if pre_search_diagnostics.get("warnings"):
                        pre_search_context += (
                            "【文献检索诊断提示】\n"
                            + "\n".join(f"- {w}" for w in pre_search_diagnostics.get("warnings", [])[:4])
                            + "\n\n"
                        )
                    full_text = pre_search_context + full_text

                import logging
                logging.getLogger(__name__).info(
                    f"预搜索完成: {len(pre_search_refs)} 条文献, "
                    f"关键词: {pre_keywords}"
                )
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"预搜索开放文献失败（不影响主流程）: {e}")

        self._update_hitl_context(
            research_question=research_question,
            source_mode="RAG 语义检索上下文" if self.rag_context else "原始文献全文/摘要",
            pre_search_keywords=pre_keywords,
            pre_search_refs=pre_search_refs,
            pre_search_context=pre_search_context,
            literature_search_diagnostics=self.literature_search_diagnostics,
            current_stage="预搜索开放文献",
        )
        self._last_hitl_context["database_evidence_context"] = database_evidence_snapshot

        # 统计总步数用于进度报告
        # lit + reasoning_chain + hypo + cross_domain + (critique+debate+revise)*N + valid + results_verify + output + auto_verify
        total_steps = 4 + self.max_iterations * 3 + 3 + (1 if self.auto_verify else 0)
        step = 0

        # ── Step 1: 文献理解 ──
        self._report("文献理解与事实提取", step, total_steps)
        input_text = full_text
        if research_question:
            input_text = f"【用户关注的研究问题】: {research_question}\n\n{full_text}"

        lit_msg = self.literature_agent.process(
            AgentMessage(
                role=AgentRole.USER,
                content=input_text,
                metadata={"research_question": research_question},
            )
        )
        _raise_if_llm_error(lit_msg.content, stage="文献理解与事实提取")
        lit_facts = self._parse_json_raw(lit_msg.content)
        step += 1

        citation_whitelist = self._extract_citation_whitelist(lit_msg.content, full_text)
        self._update_hitl_context(
            research_question=research_question,
            source_mode="RAG 语义检索上下文" if self.rag_context else "原始文献全文/摘要",
            pre_search_keywords=pre_keywords,
            pre_search_refs=pre_search_refs,
            pre_search_context=pre_search_context,
            literature_facts=lit_facts,
            literature_facts_raw=lit_msg.content,
            current_stage="文献理解与事实提取",
        )

        # ── Step 1.5: 推理链构建 ──
        self._report("构建推理链", step, total_steps)
        chain = None
        chain_json_str = ""
        try:
            chain = self.reasoning_chain_agent.process(
                literature_facts=lit_msg.content,
                domain=self.domain,  # 使用 Orchestrator 的 domain 参数
            )
            chain_json_str = json.dumps(chain.to_dict(), ensure_ascii=False)
            result.reasoning_chain = chain.to_dict()
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"推理链构建失败（不影响主流程）: {e}")
            chain = None
            chain_json_str = ""
        step += 1
        self._update_hitl_context(
            research_question=research_question,
            source_mode="RAG 语义检索上下文" if self.rag_context else "原始文献全文/摘要",
            pre_search_keywords=pre_keywords,
            pre_search_refs=pre_search_refs,
            pre_search_context=pre_search_context,
            literature_facts=lit_facts,
            literature_facts_raw=lit_msg.content,
            reasoning_chain=result.reasoning_chain,
            current_stage="推理链构建",
        )

        # ── Step 2: 初始假设生成 ──
        self._report("生成初始假设", step, total_steps)
        hypo_msg = self.hypothesis_agent.process(
            AgentMessage(
                role=AgentRole.LITERATURE,
                content=lit_msg.content,
                metadata={
                    "iteration": 0,
                    "reasoning_chain": chain_json_str,
                    "domain": self.domain,
                    "database_evidence_context": database_evidence_context,
                },
            )
        )
        _raise_if_llm_error(hypo_msg.content, stage="生成初始假设")
        current_hypothesis = hypo_msg.content
        step += 1

        # ── Step 2.5: 跨学科技术迁移 ──
        self._report("跨学科技术迁移分析", step, total_steps)
        cross_domain_hints = ""
        try:
            from knowledge_base import find_cross_domain_analogies
            analogies = find_cross_domain_analogies(
                domain=self.domain,
                hypothesis_text=current_hypothesis,
                top_k=3,
            )
            if analogies:
                hint_lines = []
                for a in analogies:
                    hint_lines.append(
                        f"- {a.source_domain}({a.source_descriptor}) ↔ "
                        f"{a.target_domain}({a.target_descriptor}): {a.mapping_rationale}"
                    )
                cross_domain_hints = "\n".join(hint_lines)
        except Exception:
            pass

        cd_msg = self.cross_domain_agent.process(
            AgentMessage(
                role=AgentRole.HYPOTHESIS,
                content=current_hypothesis,
                metadata={
                    "domain": self.domain,
                    "cross_domain_hints": cross_domain_hints,
                },
            )
        )
        _raise_if_llm_error(cd_msg.content, stage="跨学科技术迁移分析")
        cross_domain_data = self._parse_json_raw(cd_msg.content)
        result.cross_domain_analogies = cross_domain_data
        step += 1
        self._update_hitl_context(
            research_question=research_question,
            source_mode="RAG 语义检索上下文" if self.rag_context else "原始文献全文/摘要",
            pre_search_keywords=pre_keywords,
            pre_search_refs=pre_search_refs,
            pre_search_context=pre_search_context,
            literature_facts=lit_facts,
            literature_facts_raw=lit_msg.content,
            reasoning_chain=result.reasoning_chain,
            cross_domain_analogies=cross_domain_data,
            current_stage="初始假设审核",
        )

        # ── HITL 检查点 A: 初始假设 ──
        feedback = None  # 保存最近的用户反馈供后续使用
        pending_initial_feedback = None
        pending_initial_entry = None
        if self.user_feedback_callback:
            self._report("初始假设人工审核", step, total_steps)
            parsed_hypo = self._parse_json_raw(current_hypothesis)
            try:
                feedback = self.user_feedback_callback(
                    parsed_hypo, None, 0, self.interaction_history
                )
            except Exception:
                feedback = None

            if feedback and hasattr(feedback, "action"):
                if (
                    feedback.action == "skip"
                    and getattr(feedback, "user_edited_hypothesis", None)
                ):
                    feedback.action = "revise"
                if feedback.action == "cancel":
                    result.raw_json = self._parse_json_raw(current_hypothesis)
                    result.final_output = json.dumps(result.raw_json, ensure_ascii=False, indent=2)
                    result.interaction_history = self.interaction_history
                    return result
                elif feedback.action == "revise":
                    entry = self._record_interaction(
                        0, parsed_hypo, None, "revise",
                        getattr(feedback, "user_feedback_text", ""),
                        getattr(feedback, "user_edited_hypothesis", None),
                        **self._feedback_trace_kwargs(feedback),
                    )
                    pending_initial_feedback = feedback
                    pending_initial_entry = entry
                    if hasattr(feedback, "user_edited_hypothesis") and feedback.user_edited_hypothesis:
                        current_hypothesis = json.dumps(feedback.user_edited_hypothesis, ensure_ascii=False)
                        entry["manual_revision"] = {
                            "diff": getattr(feedback, "manual_revision_diff", []),
                            "summary": summarize_diff(getattr(feedback, "manual_revision_diff", [])),
                            "final_adopted": True,
                        }
                        entry["final_adopted"] = True
                elif feedback.action == "approve":
                    entry = self._record_interaction(
                        0, parsed_hypo, None, "approve",
                        getattr(feedback, "user_feedback_text", ""),
                        getattr(feedback, "user_edited_hypothesis", None),
                        **self._feedback_trace_kwargs(feedback),
                    )
                    if hasattr(feedback, "user_edited_hypothesis") and feedback.user_edited_hypothesis:
                        current_hypothesis = json.dumps(feedback.user_edited_hypothesis, ensure_ascii=False)
                        entry["manual_revision"] = {
                            "diff": getattr(feedback, "manual_revision_diff", []),
                            "summary": summarize_diff(getattr(feedback, "manual_revision_diff", [])),
                            "final_adopted": True,
                        }
                        entry["final_adopted"] = True
                else:
                    self._record_interaction(
                        0, parsed_hypo, None, "skip",
                        getattr(feedback, "user_feedback_text", ""),
                        getattr(feedback, "user_edited_hypothesis", None),
                        **self._feedback_trace_kwargs(feedback),
                    )

        # ── Step 3-N: 思辨迭代循环 ──
        for i in range(self.max_iterations):
            # 思辨
            self._report(f"第 {i+1} 轮思辨", step, total_steps)
            critique_msg = self.critique_agent.process(
                AgentMessage(
                    role=AgentRole.HYPOTHESIS,
                    content=current_hypothesis,
                )
            )
            _raise_if_llm_error(critique_msg.content, stage=f"第 {i+1} 轮思辨")
            critique_data = self._parse_json_raw(critique_msg.content)
            result.critique_history.append({
                "round": i + 1,
                "score": critique_data.get("overall_score", "N/A"),
                "summary": critique_data.get("summary", ""),
                "critical_flaws": critique_data.get("critical_flaws", []),
                "improvements": critique_data.get("improvement_suggestions", []),
            })
            step += 1

            # ── Step 3.5: 多角色辩论（Devil's Advocate + Optimist） ──
            self._report(f"第 {i+1} 轮多角色辩论", step, total_steps)
            try:
                devil_msg = self.devil_advocate_agent.process(
                    AgentMessage(
                        role=AgentRole.HYPOTHESIS,
                        content=current_hypothesis,
                        metadata={
                            "critique": critique_msg.content,
                            "cross_domain_analogies": cd_msg.content if cd_msg else "",
                        },
                    )
                )
                _raise_if_llm_error(devil_msg.content, stage=f"第 {i+1} 轮反方辩论")
                devil_data = self._parse_json_raw(devil_msg.content)
                if devil_data.get("parse_error"):
                    raise ValueError(f"DevilAdvocate 输出解析失败: {devil_data.get('raw_text', '')[:200]}")

                optimist_msg = self.optimist_agent.process(
                    AgentMessage(
                        role=AgentRole.HYPOTHESIS,
                        content=current_hypothesis,
                        metadata={
                            "devil_attack": devil_msg.content,
                            "cross_domain_analogies": cd_msg.content if cd_msg else "",
                        },
                    )
                )
                _raise_if_llm_error(optimist_msg.content, stage=f"第 {i+1} 轮正方辩护")
                optimist_data = self._parse_json_raw(optimist_msg.content)
                if optimist_data.get("parse_error"):
                    raise ValueError(f"Optimist 输出解析失败: {optimist_data.get('raw_text', '')[:200]}")

                debate_summary = summarize_debate(devil_data, optimist_data)
                result.debate_history.append({
                    "round": i + 1,
                    "devil_verdict": devil_data.get("overall_verdict", ""),
                    "optimist_verdict": optimist_data.get("overall_verdict", ""),
                    "balance": debate_summary.get("balance", "balanced"),
                    "recommendation": debate_summary.get("recommendation", ""),
                    "devil_attack_points": devil_data.get("attack_points", []),
                    "optimist_defense_points": optimist_data.get("defense_points", []),
                    "key_insights": debate_summary.get("key_insights", []),
                })
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning(f"辩论轮次失败（不影响主流程）: {e}")
                result.debate_history.append({
                    "round": i + 1,
                    "error": True,
                    "error_message": str(e),
                })
            step += 1

            if result.debate_history:
                self._last_debate_data = result.debate_history[-1]
            else:
                self._last_debate_data = {}
            self._update_hitl_context(
                research_question=research_question,
                source_mode="RAG 语义检索上下文" if self.rag_context else "原始文献全文/摘要",
                pre_search_keywords=pre_keywords,
                pre_search_refs=pre_search_refs,
                pre_search_context=pre_search_context,
                literature_facts=lit_facts,
                literature_facts_raw=lit_msg.content,
                reasoning_chain=result.reasoning_chain,
                cross_domain_analogies=cross_domain_data,
                debate_data=self._last_debate_data,
                current_stage=f"第 {i+1} 轮思辨与辩论审核",
            )

            overall_score = critique_data.get("overall_score", 0)
            has_critical = bool(critique_data.get("critical_flaws", []))
            human_revision_entry = None
            human_before_revision = None

            # ── HITL 检查点 B: 思辨后 ──
            if self.user_feedback_callback:
                self._report(f"第 {i+1} 轮人工审核", step, total_steps)
                parsed_hypo = self._parse_json_raw(current_hypothesis)
                try:
                    feedback = self.user_feedback_callback(
                        parsed_hypo, critique_data, i + 1, self.interaction_history
                    )
                except Exception:
                    feedback = None

                if feedback and hasattr(feedback, "action"):
                    if (
                        feedback.action == "skip"
                        and getattr(feedback, "user_edited_hypothesis", None)
                    ):
                        feedback.action = "revise"
                    if feedback.action == "cancel":
                        result.raw_json = self._parse_json_raw(current_hypothesis)
                        result.final_output = json.dumps(result.raw_json, ensure_ascii=False, indent=2)
                        result.interaction_history = self.interaction_history
                        return result
                    elif feedback.action == "approve":
                        entry = self._record_interaction(
                            i + 1, parsed_hypo, critique_data, "approve",
                            getattr(feedback, "user_feedback_text", ""),
                            getattr(feedback, "user_edited_hypothesis", None),
                            **self._feedback_trace_kwargs(feedback),
                        )
                        if hasattr(feedback, "user_edited_hypothesis") and feedback.user_edited_hypothesis:
                            current_hypothesis = json.dumps(
                                feedback.user_edited_hypothesis, ensure_ascii=False
                            )
                            entry["manual_revision"] = {
                                "diff": getattr(feedback, "manual_revision_diff", []),
                                "summary": summarize_diff(getattr(feedback, "manual_revision_diff", [])),
                                "final_adopted": True,
                            }
                            entry["final_adopted"] = True
                        break  # 用户批准，跳过剩余迭代
                    elif feedback.action == "revise":
                        human_revision_entry = self._record_interaction(
                            i + 1, parsed_hypo, critique_data, "revise",
                            getattr(feedback, "user_feedback_text", ""),
                            getattr(feedback, "user_edited_hypothesis", None),
                            **self._feedback_trace_kwargs(feedback),
                        )
                        human_before_revision = parsed_hypo
                        if hasattr(feedback, "user_edited_hypothesis") and feedback.user_edited_hypothesis:
                            current_hypothesis = json.dumps(
                                feedback.user_edited_hypothesis, ensure_ascii=False
                            )
                            human_revision_entry["manual_revision"] = {
                                "diff": getattr(feedback, "manual_revision_diff", []),
                                "summary": summarize_diff(getattr(feedback, "manual_revision_diff", [])),
                                "final_adopted": True,
                            }
                    else:
                        self._record_interaction(
                            i + 1, parsed_hypo, critique_data, "skip",
                            getattr(feedback, "user_feedback_text", ""),
                            getattr(feedback, "user_edited_hypothesis", None),
                            **self._feedback_trace_kwargs(feedback),
                        )

            revision_feedback = None
            if feedback and hasattr(feedback, "action") and feedback.action == "revise":
                revision_feedback = feedback
            elif i == 0 and pending_initial_feedback is not None:
                revision_feedback = pending_initial_feedback
                if human_revision_entry is None:
                    human_revision_entry = pending_initial_entry
                    human_before_revision = (
                        pending_initial_entry.get("hypothesis_snapshot", {})
                        if pending_initial_entry
                        else {}
                    )

            human_requested_revision = revision_feedback is not None
            if overall_score >= 9 and not has_critical and not human_requested_revision:
                # 假设已经足够好，提前终止
                break

            # 修订假设
            self._report(f"第 {i+1} 轮修订", step, total_steps)
            revision_metadata = {
                "critique": critique_msg.content,
                "iteration": i + 1,
                "reasoning_chain": chain_json_str,  # 推理链上下文持续注入
                "domain": self.domain,
                "database_evidence_context": database_evidence_context,
            }
            # 注入辩论摘要到修订上下文
            if result.debate_history:
                latest_debate = result.debate_history[-1]
                debate_section = (
                    f"\n\n【多角色辩论摘要】:\n"
                    f"反方结论: {latest_debate.get('devil_verdict', '')}\n"
                    f"正方结论: {latest_debate.get('optimist_verdict', '')}\n"
                    f"辩论平衡: {latest_debate.get('balance', '')}\n"
                    f"建议: {latest_debate.get('recommendation', '')}\n"
                    "请在修订中充分考虑辩论双方的观点。"
                )
                revision_metadata["critique"] += debate_section
            # 注入用户反馈到修订上下文
            if revision_feedback:
                fb_text = getattr(revision_feedback, "user_feedback_text", "")
                debate_stance = getattr(revision_feedback, "debate_stance", "neutral")
                selected_attacks = getattr(revision_feedback, "selected_attack_indices", [])
                structured_fb = getattr(revision_feedback, "structured_feedback", {})

                stance_section = ""
                if debate_stance == "devil":
                    stance_section = "\n用户选择支持反方立场，请重点加强假设中被攻击环节的论证。"
                    if selected_attacks and result.debate_history:
                        attacks = result.debate_history[-1].get("devil_attack_points", [])
                        targeted = [attacks[idx] for idx in selected_attacks if idx < len(attacks)]
                        if targeted:
                            stance_section += "\n用户指定攻击的环节：\n"
                            for t in targeted:
                                stance_section += f"  - {t.get('target', '')}: {t.get('evidence', '')}\n"
                elif debate_stance == "optimist":
                    stance_section = "\n用户选择支持正方立场，请在修订中强化辩护证据。"
                elif debate_stance == "custom":
                    stance_section = "\n用户指定了自定义攻击点，请针对性地修订。"

                struct_section = ""
                if structured_fb:
                    parts = []
                    for k, v in structured_fb.items():
                        if v and v != "—":
                            parts.append(f"  - {k}: {v}")
                    if parts:
                        struct_section = "\n【结构化反馈】:\n" + "\n".join(parts) + "\n"

                if fb_text or stance_section or struct_section:
                    user_fb_section = (
                        f"\n\n【用户在第 {i+1} 轮提供的指导】:\n"
                        f"{fb_text}\n{stance_section}{struct_section}"
                        "请在修订中充分考虑用户的方向性指导。"
                    )
                    revision_metadata["critique"] += user_fb_section
                edited = getattr(revision_feedback, "user_edited_hypothesis", None)
                if edited:
                    revision_metadata["user_edited_hypothesis"] = json.dumps(
                        edited, ensure_ascii=False
                    )

            revision_msg = self.hypothesis_agent.process(
                AgentMessage(
                    role=AgentRole.CRITIQUE,
                    content=lit_msg.content,
                    metadata=revision_metadata,
                )
            )
            _raise_if_llm_error(revision_msg.content, stage=f"第 {i+1} 轮假设修订")
            current_hypothesis = revision_msg.content
            revised_hypothesis = self._parse_json_raw(current_hypothesis)
            if human_revision_entry is not None:
                self._attach_ai_revision_trace(
                    human_revision_entry,
                    human_before_revision or {},
                    revised_hypothesis,
                    "human_feedback+agent_critique",
                )
            result.iterations.append({
                "round": i + 1,
                "hypothesis": revised_hypothesis,
                "critique_score": overall_score,
            })
            step += 1

        # ── 可验证性评估 ──
        self._report("可验证性评估", step, total_steps)
        validation_msg = self.validation_agent.process(
            AgentMessage(
                role=AgentRole.HYPOTHESIS,
                content=current_hypothesis,
            )
        )
        _raise_if_llm_error(validation_msg.content, stage="可验证性评估")
        step += 1

        # ── 公式推导与实验结果验证 ──
        self._report("公式推导与结果验证", step, total_steps)
        results_verification_msg = self.results_verification_agent.process(
            AgentMessage(
                role=AgentRole.HYPOTHESIS,
                content=current_hypothesis,
                metadata={
                    "scaling_context": self.quantitative_context,
                },
            )
        )
        _raise_if_llm_error(results_verification_msg.content, stage="公式推导与结果验证")
        step += 1

        executable_validation = {}
        try:
            from scientific_validation import ScientificValidationEngine
            executable_validation = ScientificValidationEngine().validate(
                hypothesis_data=self._parse_json_raw(current_hypothesis),
                results_verification=self._parse_json_raw(results_verification_msg.content),
                scaling_context=self.quantitative_context,
                quantitative_report=getattr(self, "quantitative_report", {}),
                multimodal_evidence=getattr(self, "multimodal_evidence", {}),
            )
            result.executable_validation = executable_validation
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"代码执行验证失败（不影响主流程）: {e}")

        scientific_toolkit = {}
        try:
            self._report("RDKit/pymatgen 科学工具验证", step, total_steps)
            from scientific_toolkit import ScientificToolkitEngine
            scientific_toolkit = ScientificToolkitEngine(config=self.config).run(
                literature_text=full_text,
                hypothesis_data=self._parse_json_raw(current_hypothesis),
                generate_structures=True,
            )
            result.scientific_toolkit = scientific_toolkit
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"RDKit/pymatgen 科学工具验证失败（不影响主流程）: {e}")

        # ── 智能闭环回验 ──
        self._report("智能闭环回验", step, total_steps)
        closed_loop_verifier = ClosedLoopVerifier()
        revision_loops = 0

        while revision_loops < ClosedLoopVerifier.MAX_REVISION_LOOPS:
            cl_result = closed_loop_verifier.verify(
                hypothesis_json=current_hypothesis,
                results_verification_json=results_verification_msg.content,
                literature_facts=lit_msg.content,
                scaling_context=self.quantitative_context,
            )
            result.closed_loop_validation = cl_result

            if not cl_result["needs_revision"]:
                break

            self._report(f"闭环回验修订 (第{revision_loops+1}轮)", step, total_steps)
            revision_metadata = {
                "critique": cl_result["revision_hints"],
                "iteration": self.max_iterations + revision_loops + 1,
                "reasoning_chain": chain_json_str,
                "closed_loop_revision": True,
                "domain": self.domain,
                "database_evidence_context": database_evidence_context,
            }

            revision_msg = self.hypothesis_agent.process(
                AgentMessage(
                    role=AgentRole.CRITIQUE,
                    content=lit_msg.content,
                    metadata=revision_metadata,
                )
            )
            _raise_if_llm_error(revision_msg.content, stage=f"闭环回验修订第 {revision_loops+1} 轮")
            current_hypothesis = revision_msg.content

            results_verification_msg = self.results_verification_agent.process(
                AgentMessage(
                    role=AgentRole.HYPOTHESIS,
                    content=current_hypothesis,
                    metadata={"scaling_context": self.quantitative_context},
                )
            )
            _raise_if_llm_error(
                results_verification_msg.content,
                stage=f"闭环回验结果验证第 {revision_loops+1} 轮",
            )
            revision_loops += 1

        step += 1

        try:
            from scientific_validation import ScientificValidationEngine
            executable_validation = ScientificValidationEngine().validate(
                hypothesis_data=self._parse_json_raw(current_hypothesis),
                results_verification=self._parse_json_raw(results_verification_msg.content),
                scaling_context=self.quantitative_context,
                quantitative_report=getattr(self, "quantitative_report", {}),
                multimodal_evidence=getattr(self, "multimodal_evidence", {}),
            )
            result.executable_validation = executable_validation
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"代码执行验证失败（不影响主流程）: {e}")

        try:
            self._report("RDKit/pymatgen 科学工具复核", step, total_steps)
            from scientific_toolkit import ScientificToolkitEngine
            scientific_toolkit = ScientificToolkitEngine(config=self.config).run(
                literature_text=full_text,
                hypothesis_data=self._parse_json_raw(current_hypothesis),
                generate_structures=True,
            )
            result.scientific_toolkit = scientific_toolkit
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"RDKit/pymatgen 科学工具复核失败（不影响主流程）: {e}")

        # ── 标准化输出 ──
        self._report("生成标准化输出", step, total_steps)

        dataset_source_ctx = ""
        try:
            dataset_enricher = DatasetSourceEnricher()
            dataset_source_ctx = dataset_enricher.enrich(current_hypothesis)
        except Exception:
            pass

        verifiability_result = {}
        try:
            verifiability_scorer = VerifiabilityScorer()
            verifiability_result = verifiability_scorer.score(
                current_hypothesis, validation_msg.content
            )
        except Exception:
            pass

        experiment_template_ctx = ""
        try:
            template_recommender = ExperimentTemplateRecommender()
            experiment_template_ctx = template_recommender.recommend(
                current_hypothesis, self.domain
            )
        except Exception:
            pass

        scientific_toolkit_ctx = ""
        if scientific_toolkit:
            try:
                from scientific_toolkit import build_scientific_toolkit_context
                scientific_toolkit_ctx = build_scientific_toolkit_context(scientific_toolkit)
            except Exception:
                scientific_toolkit_ctx = ""

        debate_summary_text = ""
        if result.debate_history:
            last_debate = result.debate_history[-1]
            debate_summary_text = json.dumps(last_debate, ensure_ascii=False, indent=2)

        output_msg = self.output_agent.process(
            AgentMessage(
                role=AgentRole.HYPOTHESIS,
                content=current_hypothesis,
                metadata={
                    "critique": critique_msg.content,
                    "validation": validation_msg.content,
                    "results_verification": results_verification_msg.content,
                    "executable_validation": executable_validation,
                    "scientific_toolkit": scientific_toolkit,
                    "scientific_toolkit_context": scientific_toolkit_ctx,
                    "cross_domain_analogies": cd_msg.content if cd_msg else "",
                    "debate_summary": debate_summary_text,
                    "citation_whitelist": citation_whitelist,
                    "quantitative_context": self.quantitative_context,
                    "dataset_source_context": dataset_source_ctx,
                    "verifiability_score": verifiability_result,
                    "experiment_template_context": experiment_template_ctx,
                    "database_evidence_context": database_evidence_context,
                    "domain": self.domain,
                },
            )
        )
        _raise_if_llm_error(output_msg.content, stage="生成标准化输出")
        step += 1

        # ── 组装最终结果 ──
        final_data = self._parse_json_raw(output_msg.content)

        schema_validator = SchemaValidator(config=self.config)
        schema_result = schema_validator.validate(final_data, citation_whitelist=citation_whitelist)
        final_data = schema_result["data"]
        try:
            from scientific_validation import merge_executable_validation
            final_data = merge_executable_validation(final_data, executable_validation)
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"代码执行验证结果合并失败（不影响主流程）: {e}")
        try:
            from scientific_toolkit import merge_scientific_toolkit_report
            final_data = merge_scientific_toolkit_report(final_data, scientific_toolkit)
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"RDKit/pymatgen 科学工具结果合并失败（不影响主流程）: {e}")
        final_data = apply_domain_output_defaults(final_data, self.domain)
        try:
            from hypothesis_workflow_exporter import build_hypothesis_workflow_package
            final_data["_hypothesis_workflow_package"] = build_hypothesis_workflow_package(final_data)
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"计算建模工作流包生成失败（不影响主流程）: {e}")

        if getattr(self, "multimodal_evidence", None):
            final_data["_multimodal_evidence"] = self.multimodal_evidence
        if getattr(self, "quantitative_report", None):
            final_data["_quantitative_validation_source"] = self.quantitative_report
        if getattr(self, "qwen_agent_tool_context", None):
            final_data["_qwen_agent_tool_context"] = self.qwen_agent_tool_context
        if getattr(self, "evidence_context", None):
            final_data["_scientific_evidence_context"] = self.evidence_context
        if getattr(self, "database_evidence_snapshot", None):
            final_data["_database_evidence_context"] = self.database_evidence_snapshot
            _attach_database_evidence_ids(final_data, self.database_evidence_snapshot)
        if self.source_doc_ids:
            final_data["_source_document_ids"] = self.source_doc_ids
        final_data["score_breakdown"] = build_score_breakdown(
            final_data,
            database_evidence=getattr(self, "database_evidence_snapshot", {}),
            scientific_toolkit=scientific_toolkit,
            qwen_agent_context=getattr(self, "qwen_agent_tool_context", {}),
            verifiability_result=verifiability_result,
        )

        if schema_result.get("report", {}).get("reference_whitelist", {}).get("removed_count", 0) > 0:
            import logging
            logging.getLogger(__name__).info(
                f"引用白名单校验: 移除 {schema_result['report']['reference_whitelist']['removed_count']} 条不在白名单内的引用"
            )

        # ── OA 文献搜索 + 用户文档合并 ──
        self._report("搜索开放文献支撑", step, total_steps)
        try:
            from literature_search import (
                search_literature_for_hypothesis,
                get_user_document_references,
                merge_references,
            )

            # 1. 获取用户导入的文档（多文献输入时合并全部选中文献）
            user_refs = []
            if self.user_id:
                doc_ids = self.source_doc_ids or ([self.source_doc_id] if self.source_doc_id else [])
                for doc_id in doc_ids:
                    user_refs.extend(get_user_document_references(self.user_id, doc_id))

            # 2. 从假设中提取关键词，第二轮 OA 文献搜索（基于生成的假设细节）
            post_search_refs = []
            post_search_diagnostics = {}
            try:
                post_search_results, post_search_diagnostics = search_literature_for_hypothesis(
                    hypothesis_data=final_data,
                    domain=self.domain,
                    max_results_per_platform=2,  # 第二轮搜索，少取一些避免重复
                    return_diagnostics=True,
                    platforms={
                        "arxiv": True,
                        "crossref": True,
                        "semantic_scholar": bool(os.getenv("SEMANTIC_SCHOLAR_API_KEY", "").strip()),
                        "doaj": True,
                        "pmc": True,
                    },
                )
                self.literature_search_diagnostics["post_search"] = post_search_diagnostics
                self.literature_search_diagnostics["warnings"].extend(
                    post_search_diagnostics.get("warnings", []) or []
                )
                post_search_refs = [r.to_reference_dict() for r in post_search_results]
            except Exception as e2:
                import logging
                logging.getLogger(__name__).warning(f"第二轮 OA 搜索失败: {e2}")
                post_search_diagnostics = {"warnings": [str(e2)], "platform_status": {}}
                self.literature_search_diagnostics["post_search"] = post_search_diagnostics
                self.literature_search_diagnostics["warnings"].append(str(e2))

            # 3. LLM OutputAgent 已生成的引用
            llm_refs = final_data.get("references", [])

            # 4. 四类合并（用户导入 > 预搜索文献 > LLM 生成 > 第二轮搜索）
            # 先将预搜索结果和第二轮搜索结果合并
            all_search_refs = pre_search_refs + post_search_refs

            merged_refs = merge_references(
                user_refs=user_refs,
                search_refs=all_search_refs,
                llm_refs=llm_refs,
                max_total=15,
            )
            try:
                from evidence_ledger import gate_references
                gated_refs, evidence_ledger = gate_references(
                    final_data,
                    merged_refs,
                    max_total=15,
                )
                final_data["references"] = gated_refs
                final_data["_evidence_ledger"] = evidence_ledger
            except Exception as gate_err:
                import logging
                logging.getLogger(__name__).warning(
                    f"引用证据准入失败，保留原始合并引用: {gate_err}"
                )
                final_data["references"] = merged_refs
                final_data["_evidence_ledger"] = {
                    "summary": {
                        "initial_reference_count": len(merged_refs),
                        "accepted_reference_count": len(merged_refs),
                        "rejected_reference_count": 0,
                        "claim_count": 0,
                        "supported_claim_count": 0,
                        "unsupported_claim_count": 0,
                    },
                    "claims": [],
                    "references": [],
                    "rejected_references": [],
                    "error": str(gate_err),
                }

            # 5. 搜索统计信息
            final_refs = final_data.get("references", [])
            ledger_summary = final_data.get("_evidence_ledger", {}).get("summary", {})
            ref_stats = {
                "user_documents": len(user_refs),
                "pre_search_results": len(pre_search_refs),
                "post_search_results": len(post_search_refs),
                "llm_generated": len(llm_refs),
                "total_merged": len(merged_refs),
                "accepted_after_evidence_gate": len(final_refs),
                "rejected_by_evidence_gate": ledger_summary.get("rejected_reference_count", 0),
                "warnings": list(dict.fromkeys(self.literature_search_diagnostics.get("warnings", []))),
            }
            final_data["_reference_sources"] = ref_stats
            final_data["_literature_search_diagnostics"] = self.literature_search_diagnostics

            import logging
            logging.getLogger(__name__).info(
                f"参考文献合并: 用户文档={len(user_refs)}, "
                f"预搜索={len(pre_search_refs)}, "
                f"后搜索={len(post_search_refs)}, "
                f"LLM生成={len(llm_refs)}, "
                f"合并去重后={len(merged_refs)}, "
                f"证据准入后={len(final_refs)}"
            )
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"OA 文献搜索失败（不影响主流程）: {e}")

        final_data = apply_domain_output_defaults(final_data, self.domain)
        if getattr(self, "database_evidence_snapshot", None):
            final_data["_database_evidence_context"] = self.database_evidence_snapshot
            _attach_database_evidence_ids(final_data, self.database_evidence_snapshot)
        if self.source_doc_ids:
            final_data["_source_document_ids"] = self.source_doc_ids
        final_data["score_breakdown"] = build_score_breakdown(
            final_data,
            database_evidence=getattr(self, "database_evidence_snapshot", {}),
            scientific_toolkit=scientific_toolkit,
            qwen_agent_context=getattr(self, "qwen_agent_tool_context", {}),
            verifiability_result=verifiability_result,
        )
        final_data["_human_collaboration"] = self.interaction_history
        try:
            from hypothesis_tree_search import build_trace_first_hypothesis_tree
            final_data["_hypothesis_tree_search"] = build_trace_first_hypothesis_tree(
                final_data,
                iterations=result.iterations,
                critique_history=result.critique_history,
                debate_history=result.debate_history,
                interaction_history=self.interaction_history,
                scientific_toolkit=scientific_toolkit,
                workflow_package=final_data.get("_hypothesis_workflow_package", {}),
                results_verification=self._parse_json_raw(results_verification_msg.content),
            )
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"HypothesisTreeSearch 轨迹树生成失败（不影响主流程）: {e}")
        result.raw_json = final_data
        result.confidence = final_data.get("confidence", 5)
        result.feasibility = final_data.get("feasibility", "中")
        result.final_output = json.dumps(final_data, ensure_ascii=False, indent=2)
        result.interaction_history = self.interaction_history

        # 保存公式推导验证结果（独立于 final_output 中的 results 字段）
        result.results_verification = self._parse_json_raw(results_verification_msg.content)
        result.executable_validation = executable_validation

        # ── 自动结果验证（可选） ──
        if self.auto_verify:
            try:
                from hypothesis_verifier import ResultVerifier
                verifier = ResultVerifier(config=self.config)

                def _verify_progress(stage, cur, tot):
                    self._report(f"验证: {stage}", step + cur, total_steps)

                verify_report = verifier.verify(final_data, on_progress=_verify_progress)
                result.verification_report = verify_report.to_json()
                step += 1
            except Exception as e:
                # 验证失败不影响主流程
                result.verification_report = {"error": str(e), "partial": True}

        self._report("完成", total_steps, total_steps)

        return result


# ─────────────────────────────────────────────────────────────
# 便捷函数
# ─────────────────────────────────────────────────────────────

def generate_hypothesis(
    literature_text: str,
    research_question: str = "",
    config: Optional[LLMConfig] = None,
    max_iterations: int = 3,
    on_progress: Optional[Callable[[str, int, int], None]] = None,
    user_feedback_callback: Optional[Callable] = None,
    interaction_history: Optional[dict] = None,
    auto_verify: bool = True,
    multimodal_context: str = "",
    quantitative_context: str = "",
    multimodal_evidence: Optional[Dict] = None,
    quantitative_report: Optional[Dict] = None,
    rag_context: str = "",
    evidence_context: Optional[Dict] = None,
    domain: str = "",
    user_id: int = 0,
    source_doc_id: int = 0,
    source_doc_ids: Optional[List[int]] = None,
) -> HypothesisResult:
    """
    一键生成科学假设的便捷函数。

    Args:
        literature_text: 文献文本（全文截断，作为 fallback）
        research_question: 用户自定义研究问题
        config: LLM 配置
        max_iterations: 最大思辨迭代轮数
        on_progress: 进度回调 (stage_name, current_step, total_steps)
        user_feedback_callback: 人在回路回调
        interaction_history: 可选的交互历史字典，会被原地修改
        auto_verify: 是否自动验证结果（默认 True）
        multimodal_context: 多模态数据注入上下文
        quantitative_context: 定量标度关系上下文
        multimodal_evidence: 结构化多模态证据包
        quantitative_report: 结构化定量建模报告
        rag_context: RAG 语义检索上下文（替代全文截断，优先使用）
        evidence_context: PaperQA/科学证据 RAG 的结构化证据上下文
        domain: 研究领域标识
        user_id: 用户 ID（用于获取用户导入的文档作为参考文献）
        source_doc_id: 假设页面加载的第一篇文档 ID
        source_doc_ids: 假设页面加载的多篇文档 ID

    Returns:
        HypothesisResult
    """
    orchestrator = HypothesisOrchestrator(
        config=config,
        max_iterations=max_iterations,
        on_progress=on_progress,
        user_feedback_callback=user_feedback_callback,
        interaction_history=interaction_history,
        auto_verify=auto_verify,
        multimodal_context=multimodal_context,
        quantitative_context=quantitative_context,
        multimodal_evidence=multimodal_evidence,
        quantitative_report=quantitative_report,
        rag_context=rag_context,
        evidence_context=evidence_context,
        domain=domain,
        user_id=user_id,
        source_doc_id=source_doc_id,
        source_doc_ids=source_doc_ids,
    )
    return orchestrator.run(literature_text, research_question)
