"""
data_miner.py — 多模态数据关联挖掘引擎

从清洗后的多模态数据中自动发现跨数据源的科学关联规律：
  1. 规则驱动 — 基于催化领域先验知识的 descriptor-property 映射
  2. LLM 辅助 — 让 qwen3.8-max 补充规则未覆盖的关联
  3. 关系类型 — correlation / causation / descriptor / mechanism

比赛要求对应：
  "实现数据的智能清洗、分析与关联挖掘，精准识别关键信息"
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from data_cleaner import CleanedDataPoint

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
# 数据类
# ─────────────────────────────────────────────────────────────

@dataclass
class Association:
    """发现的跨数据源关联"""
    source_a: str          # 来源1（如 "Fig.3 XPS"）
    source_b: str          # 来源2（如 "Fig.5 LSV"）
    param_a: str           # 参数1（如 "Co 2p3/2 binding energy"）
    param_b: str           # 参数2（如 "half-wave potential"）
    relation_type: str     # correlation / causation / descriptor / mechanism
    description: str       # 自然语言描述
    scientific_basis: str  # 科学依据（理论/文献）
    confidence: float = 0.8  # 置信度 0-1
    value_a: float = 0.0
    value_b: float = 0.0
    unit_a: str = ""
    unit_b: str = ""

    def to_dict(self) -> dict:
        return {
            "source_a": self.source_a, "source_b": self.source_b,
            "param_a": self.param_a, "param_b": self.param_b,
            "relation_type": self.relation_type,
            "description": self.description,
            "scientific_basis": self.scientific_basis,
            "confidence": self.confidence,
            "value_a": self.value_a, "value_b": self.value_b,
            "unit_a": self.unit_a, "unit_b": self.unit_b,
        }

    def to_html(self) -> str:
        type_labels = {
            "correlation": "相关性",
            "causation": "因果关系",
            "descriptor": "描述符关系",
            "mechanism": "机理推断",
        }
        type_colors = {
            "correlation": "#378ADD",
            "causation": "#D85A30",
            "descriptor": "#534AB7",
            "mechanism": "#0F6E56",
        }
        label = type_labels.get(self.relation_type, self.relation_type)
        color = type_colors.get(self.relation_type, "#5F5E5A")
        conf_pct = f"{self.confidence * 100:.0f}%"
        return (
            f"<div style='border:1px solid {color};border-radius:8px;padding:10px;margin:6px 0;background:#fafbfc;'>"
            f"<div style='display:flex;justify-content:space-between;align-items:center;'>"
            f"<span style='color:{color};font-weight:500;font-size:13px;'>[{label}] {self.description}</span>"
            f"<span style='color:#888;font-size:11px;'>置信度 {conf_pct}</span></div>"
            f"<div style='font-size:12px;color:#555;margin-top:4px;'>"
            f"{self.param_a} ({self.value_a:.3f} {self.unit_a}) → "
            f"{self.param_b} ({self.value_b:.3f} {self.unit_b})<br>"
            f"<b>科学依据:</b> {self.scientific_basis}</div></div>"
        )


@dataclass
class MiningReport:
    """关联挖掘报告"""
    total_associations: int = 0
    rule_based: int = 0
    llm_supplemented: int = 0
    associations: List[Association] = field(default_factory=list)

    def to_html(self) -> str:
        parts = []
        parts.append(
            f"<h4 style='color:#0F6E56;'>关联挖掘报告 "
            f"(共 {self.total_associations} 条关联)</h4>"
        )
        parts.append(f"<p style='font-size:12px;color:#666;'>"
                     f"规则驱动: {self.rule_based} 条 | "
                     f"LLM补充: {self.llm_supplemented} 条</p>")
        for assoc in self.associations:
            parts.append(assoc.to_html())
        return "\n".join(parts)

    def to_context_text(self) -> str:
        """生成可注入假设生成上下文的文本段落。"""
        if not self.associations:
            return ""
        lines = ["=== 多模态数据关联分析 ===", ""]
        for i, a in enumerate(self.associations, 1):
            lines.append(f"关联 {i}: {a.description}")
            lines.append(f"  参数: {a.param_a} ({a.value_a:.3f} {a.unit_a}) → "
                         f"{a.param_b} ({a.value_b:.3f} {a.unit_b})")
            lines.append(f"  类型: {a.relation_type} | 依据: {a.scientific_basis}")
            lines.append("")
        lines.append("以上关联规律来自文献中图表/数据的跨源分析，可作为假设生成的数据支撑。")
        return "\n".join(lines)


# ─────────────────────────────────────────────────────────────
# 内置关联规则（催化/电化学领域先验知识）
# ─────────────────────────────────────────────────────────────

# 规则格式：(param_pattern_a, param_pattern_b, relation_type, description_template, scientific_basis)
# param_pattern 使用小写关键词匹配
ASSOCIATION_RULES: List[Dict[str, Any]] = [
    # ── d 带理论 → 吸附能 → 催化活性 ──
    {
        "param_a": "d-band center",
        "param_b": "adsorption energy",
        "relation_type": "descriptor",
        "description": "d带中心调控吸附能",
        "scientific_basis": "Norskov d-band theory: d-band center closer to Fermi level → stronger adsorbate binding",
    },
    {
        "param_a": "d-band center",
        "param_b": "overpotential",
        "relation_type": "descriptor",
        "description": "d带中心预测ORR过电势",
        "scientific_basis": "Sabatier principle: optimal d-band center balances *OH/*OOH binding for minimal overpotential",
    },
    {
        "param_a": "d-band center",
        "param_b": "half-wave potential",
        "relation_type": "correlation",
        "description": "d带中心与半波电位的相关性",
        "scientific_basis": "d-band center determines oxygen intermediate binding, directly affecting ORR half-wave potential",
    },
    # ── 吸附能 → 催化性能 ──
    {
        "param_a": "adsorption energy",
        "param_b": "overpotential",
        "relation_type": "descriptor",
        "description": "吸附能决定催化过电势",
        "scientific_basis": "Volcano plot relationship: ΔG*OH ~ 0.1-0.5 eV gives optimal ORR activity",
    },
    {
        "param_a": "binding energy",
        "param_b": "adsorption energy",
        "relation_type": "correlation",
        "description": "结合能与吸附能的关联",
        "scientific_basis": "Both reflect surface-adsorbate interaction strength; binding energy from XPS, adsorption from DFT",
    },
    # ── XRD → 结构 → 性能 ──
    {
        "param_a": "interlayer distance",
        "param_b": "d-band center",
        "relation_type": "causation",
        "description": "层间距调控电子结构",
        "scientific_basis": "Increased interlayer spacing modulates pi-pi coupling and d-band structure of embedded metal sites",
    },
    {
        "param_a": "lattice constant",
        "param_b": "adsorption energy",
        "relation_type": "causation",
        "description": "晶格应变影响吸附行为",
        "scientific_basis": "Strain effect: tensile strain upshifts d-band center → stronger binding (Norskov strain theory)",
    },
    # ── XPS → 电子结构 → 活性 ──
    {
        "param_a": "binding energy xps",
        "param_b": "d-band center",
        "relation_type": "correlation",
        "description": "XPS结合能位移反映电子结构变化",
        "scientific_basis": "XPS core-level shift correlates with valence d-band position changes",
    },
    {
        "param_a": "binding energy xps",
        "param_b": "overpotential",
        "relation_type": "correlation",
        "description": "XPS信号与催化性能的关联",
        "scientific_basis": "Metal oxidation state (XPS) affects d-band electron filling → oxygen binding strength",
    },
    # ── 电化学动力学 ──
    {
        "param_a": "tafel slope",
        "param_b": "mechanism",
        "relation_type": "mechanism",
        "description": "Tafel斜率推断反应机理",
        "scientific_basis": "~60 mV/dec: first electron transfer RDS; ~120 mV/dec: second electron transfer RDS",
    },
    {
        "param_a": "electron transfer number",
        "param_b": "mechanism",
        "relation_type": "mechanism",
        "description": "电子转移数指示反应路径",
        "scientific_basis": "n ≈ 4: direct 4e- ORR pathway; n ≈ 2: 2e- peroxide pathway",
    },
    # ── 表面积 → 比活性 ──
    {
        "param_a": "limiting current density",
        "param_b": "half-wave potential",
        "relation_type": "correlation",
        "description": "极限电流与半波电位的一致性",
        "scientific_basis": "Higher limiting current and more positive half-wave potential both indicate better ORR activity",
    },
    # ── 通用跨图比较 ──
    {
        "param_a": "faradaic efficiency",
        "param_b": "conversion efficiency",
        "relation_type": "correlation",
        "description": "法拉第效率与转化效率的关联",
        "scientific_basis": "Higher Faradaic efficiency indicates fewer side reactions, typically correlating with conversion",
    },
]


# 参数关键词 → 标准名映射（用于规则匹配）
_PARAM_PATTERNS: Dict[str, List[str]] = {
    "d-band center": ["d-band center", "d带中心", "d-band", "d band center", "epsilon_d"],
    "adsorption energy": ["adsorption energy", "吸附能", "delta_g", "δg", "adsorption"],
    "binding energy": ["binding energy", "结合能", "bind energy"],
    "overpotential": ["overpotential", "过电势", "过电位", "eta"],
    "half-wave potential": ["half-wave potential", "半波电位", "e1/2", "e_half"],
    "onset potential": ["onset potential", "起始电位", "onset"],
    "tafel slope": ["tafel slope", "塔菲尔斜率", "tafel"],
    "electron transfer number": ["electron transfer number", "电子转移数", "n_e", "transfer number"],
    "limiting current density": ["limiting current", "极限电流", "jl", "j_limit"],
    "binding energy xps": ["binding energy xps", "2p", "2p3/2", "2p1/2", "xps binding", "xps结合能"],
    "interlayer distance": ["interlayer distance", "层间距", "d-spacing", "d002", "d_002"],
    "lattice constant": ["lattice constant", "晶格常数", "lattice parameter", "a_0"],
    "faradaic efficiency": ["faradaic efficiency", "法拉第效率", "fe"],
    "conversion efficiency": ["conversion efficiency", "转化效率"],
    "formation energy": ["formation energy", "形成能"],
    "band gap": ["band gap", "带隙", "eg", "e_g"],
    "work function": ["work function", "功函数", "phi"],
}


def _match_param(dp: CleanedDataPoint) -> List[str]:
    """返回数据点参数匹配到的标准名列表。"""
    matches = []
    name_lower = dp.parameter.lower()
    cn_lower = dp.parameter_cn.lower() if dp.parameter_cn else ""
    combined = name_lower + " " + cn_lower + " " + dp.source.lower()

    for std_name, patterns in _PARAM_PATTERNS.items():
        for pat in patterns:
            if pat.lower() in combined:
                matches.append(std_name)
                break
    return matches


# ─────────────────────────────────────────────────────────────
# 核心挖掘类
# ─────────────────────────────────────────────────────────────

class DataMiner:
    """关联挖掘引擎"""

    def __init__(self):
        self.report = MiningReport()
        self._rules = ASSOCIATION_RULES  # 默认用硬编码规则，可通过 load_domain_rules() 覆盖
        self._param_patterns = dict(_PARAM_PATTERNS)  # 默认别名映射

    def load_domain_rules(self, domain: str):
        """
        从知识库加载指定领域的关联规则和参数别名映射。

        如果知识库中没有该领域，保持原有硬编码规则不变。

        Args:
            domain: 领域标识（如 "electrocatalysis"）
        """
        try:
            from knowledge_base import get_domain_profile, get_rules_for_domain, get_aliases_for_domain
        except ImportError:
            logger.warning("knowledge_base 模块不可用，保持默认规则")
            return

        profile = get_domain_profile(domain)
        if not profile:
            logger.info(f"知识库中无 {domain} 领域，保持默认规则")
            return

        # 加载领域规则
        domain_rules = get_rules_for_domain(domain)
        if domain_rules:
            self._rules = domain_rules
            logger.info(f"已从知识库加载 {domain} 领域的 {len(domain_rules)} 条关联规则")

        # 加载领域别名映射
        domain_aliases = get_aliases_for_domain(domain)
        if domain_aliases:
            self._param_patterns = domain_aliases
            logger.info(f"已从知识库加载 {domain} 领域的 {len(domain_aliases)} 个参数别名映射")

    def mine(
        self,
        data_points: List[CleanedDataPoint],
        use_llm: bool = True,
        config=None,
        domain: str = "",
    ) -> MiningReport:
        """
        从清洗后的数据点中挖掘关联。

        Args:
            data_points: 清洗后的数据点列表
            use_llm: 是否用 LLM 补充关联（默认 True）
            config: LLMConfig（use_llm=True 时需要）
            domain: 领域标识（可选，用于加载领域特定规则）

        Returns:
            MiningReport
        """
        self.report = MiningReport()

        # 如果指定了领域，加载对应规则
        if domain:
            self.load_domain_rules(domain)

        # 第一步：规则驱动
        rule_results = self._rule_based_mining(data_points)
        self.report.associations.extend(rule_results)
        self.report.rule_based = len(rule_results)

        # 第二步：LLM 补充（如果有足够数据且未找到足够关联）
        if use_llm and config and len(data_points) >= 3:
            llm_results = self._llm_supplement(data_points, config)
            if llm_results:
                # 去重：排除规则已发现的相同关联
                existing = {(a.param_a, a.param_b) for a in self.report.associations}
                for a in llm_results:
                    key = (a.param_a, a.param_b)
                    if key not in existing and (a.param_b, a.param_a) not in existing:
                        self.report.associations.append(a)
                        self.report.llm_supplemented += 1

        self.report.total_associations = len(self.report.associations)
        return self.report

    def _match_param(self, dp: CleanedDataPoint) -> List[str]:
        """返回数据点参数匹配到的标准名列表。使用 self._param_patterns。"""
        matches = []
        name_lower = dp.parameter.lower()
        cn_lower = dp.parameter_cn.lower() if dp.parameter_cn else ""
        combined = name_lower + " " + cn_lower + " " + dp.source.lower()

        for std_name, patterns in self._param_patterns.items():
            for pat in patterns:
                if pat.lower() in combined:
                    matches.append(std_name)
                    break
        return matches

    def _rule_based_mining(
        self, data_points: List[CleanedDataPoint]
    ) -> List[Association]:
        """基于预定义规则挖掘关联。使用 self._rules（可能来自知识库）。"""
        # 构建参数索引：标准名 → [CleanedDataPoint]
        param_index: Dict[str, List[CleanedDataPoint]] = {}
        for dp in data_points:
            for std_name in self._match_param(dp):
                param_index.setdefault(std_name, []).append(dp)

        associations = []
        seen = set()

        for rule in self._rules:
            key_a = rule["param_a"]
            key_b = rule["param_b"]
            pts_a = param_index.get(key_a, [])
            pts_b = param_index.get(key_b, [])

            if not pts_a or not pts_b:
                continue

            # 为每对匹配的数据点生成关联
            for pa in pts_a:
                for pb in pts_b:
                    if pa.source == pb.source:
                        continue  # 跳过同一来源
                    pair_key = (pa.parameter, pb.parameter, pa.source, pb.source)
                    if pair_key in seen:
                        continue
                    seen.add(pair_key)

                    assoc = Association(
                        source_a=pa.source,
                        source_b=pb.source,
                        param_a=f"{pa.parameter_cn or pa.parameter} ({pa.parameter})",
                        param_b=f"{pb.parameter_cn or pb.parameter} ({pb.parameter})",
                        relation_type=rule["relation_type"],
                        description=f"{rule['description']}: "
                                    f"{pa.parameter_cn or pa.parameter} "
                                    f"({pa.value:.3f} {pa.unit}) "
                                    f"与 {pb.parameter_cn or pb.parameter} "
                                    f"({pb.value:.3f} {pb.unit}) 存在关联",
                        scientific_basis=rule["scientific_basis"],
                        confidence=0.85,  # 规则驱动的默认置信度
                        value_a=pa.value,
                        value_b=pb.value,
                        unit_a=pa.unit,
                        unit_b=pb.unit,
                    )
                    associations.append(assoc)

        return associations

    def _llm_supplement(
        self,
        data_points: List[CleanedDataPoint],
        config,
    ) -> List[Association]:
        """用 LLM 发现规则未覆盖的关联。"""
        try:
            from llm_client import _chat, _ensure_config, _detect_lang
            cfg = _ensure_config(config)
        except ImportError:
            return []

        # 构建数据摘要
        data_summary = "\n".join([
            f"- {dp.parameter_cn or dp.parameter} ({dp.parameter}): "
            f"{dp.value:.3f} {dp.unit} [来源: {dp.source}]"
            for dp in data_points
        ])

        # 列出规则已覆盖的关联类型
        covered_types = {r["relation_type"] for r in ASSOCIATION_RULES}

        prompt = (
            "你是催化与材料科学领域的数据分析专家。以下是从科学文献图表中提取的数据点：\n\n"
            f"{data_summary}\n\n"
            "请分析这些数据点之间是否存在科学关联（排除已明确列出的常见关联如 d-band-adsorption 关系），"
            "找出不明显的跨数据源规律。\n\n"
            "严格以 JSON 数组格式输出（不加 markdown 代码块）：\n"
            "[\n"
            "  {\n"
            '    "param_a": "参数A名称",\n'
            '    "value_a": 数值A,\n'
            '    "unit_a": "单位A",\n'
            '    "source_a": "来源A",\n'
            '    "param_b": "参数B名称",\n'
            '    "value_b": 数值B,\n'
            '    "unit_b": "单位B",\n'
            '    "source_b": "来源B",\n'
            '    "relation_type": "correlation|causation|descriptor|mechanism",\n'
            '    "description": "关联的自然语言描述",\n'
            '    "scientific_basis": "科学依据",\n'
            '    "confidence": 0.7\n'
            "  }\n"
            "]\n\n"
            "规则：\n"
            "1. 只输出确实有科学依据的关联，不要臆造\n"
            "2. 没有发现额外关联则输出空数组 []\n"
            "3. confidence 范围 0.5-0.9\n"
        )

        try:
            result = _chat(prompt, cfg, task="compare", timeout=120)
            result = result.strip()
            if result.startswith("```"):
                result = result.split("\n", 1)[1].rsplit("```", 1)[0].strip()

            parsed = json.loads(result)
            if not isinstance(parsed, list):
                return []

            associations = []
            for item in parsed:
                if not isinstance(item, dict):
                    continue
                assoc = Association(
                    source_a=item.get("source_a", ""),
                    source_b=item.get("source_b", ""),
                    param_a=item.get("param_a", ""),
                    param_b=item.get("param_b", ""),
                    relation_type=item.get("relation_type", "correlation"),
                    description=item.get("description", ""),
                    scientific_basis=item.get("scientific_basis", ""),
                    confidence=float(item.get("confidence", 0.7)),
                    value_a=float(item.get("value_a", 0)),
                    value_b=float(item.get("value_b", 0)),
                    unit_a=item.get("unit_a", ""),
                    unit_b=item.get("unit_b", ""),
                )
                associations.append(assoc)
            return associations

        except (json.JSONDecodeError, ValueError, KeyError, TypeError) as e:
            logger.warning(f"LLM 关联挖掘解析失败: {e}")
            return []
        except Exception as e:
            logger.error(f"LLM 关联挖掘出错: {e}")
            return []


# ─────────────────────────────────────────────────────────────
# 便捷函数
# ─────────────────────────────────────────────────────────────

def mine_associations(
    data_points: List[CleanedDataPoint],
    use_llm: bool = True,
    config=None,
    domain: str = "",
) -> MiningReport:
    """
    一键关联挖掘。

    Args:
        data_points: 清洗后的数据点
        use_llm: 是否启用 LLM 补充
        config: LLMConfig
        domain: 领域标识（可选，如 "electrocatalysis"）

    Returns:
        MiningReport
    """
    miner = DataMiner()
    return miner.mine(data_points, use_llm=use_llm, config=config, domain=domain)


# ─────────────────────────────────────────────────────────────
# 定量关联建模引擎
# ─────────────────────────────────────────────────────────────

@dataclass
class ScalingRelation:
    """标度关系（线性回归拟合结果）"""
    param_x: str              # 自变量标准名（如 "d-band center"）
    param_y: str              # 因变量标准名（如 "adsorption energy"）
    slope: float = 0.0        # 斜率
    intercept: float = 0.0    # 截距
    r_squared: float = 0.0    # R² 决定系数
    p_value: float = 1.0      # p 值（近似）
    n_points: int = 0         # 数据点数
    unit_x: str = ""          # x 单位
    unit_y: str = ""          # y 单位
    equation: str = ""        # 方程字符串
    scientific_basis: str = ""  # 科学依据
    data_points_used: List[Dict] = field(default_factory=list)  # 使用的数据点

    def to_dict(self) -> dict:
        return {
            "param_x": self.param_x, "param_y": self.param_y,
            "slope": round(self.slope, 4), "intercept": round(self.intercept, 4),
            "r_squared": round(self.r_squared, 4), "p_value": round(self.p_value, 6),
            "n_points": self.n_points,
            "unit_x": self.unit_x, "unit_y": self.unit_y,
            "equation": self.equation,
            "scientific_basis": self.scientific_basis,
            "data_points_used": self.data_points_used,
        }

    def predict(self, x_value: float) -> float:
        """用拟合模型预测 y 值"""
        return self.slope * x_value + self.intercept

    def to_html(self) -> str:
        conf_label = "强" if self.r_squared >= 0.85 else ("中" if self.r_squared >= 0.6 else "弱")
        conf_color = "#27ae60" if self.r_squared >= 0.85 else ("#f39c12" if self.r_squared >= 0.6 else "#e74c3c")
        return (
            f"<div style='border:1px solid {conf_color};border-radius:8px;padding:10px;margin:6px 0;background:#fafbfc;'>"
            f"<div style='display:flex;justify-content:space-between;align-items:center;'>"
            f"<span style='color:{conf_color};font-weight:500;font-size:13px;'>"
            f"📏 {self.param_x} → {self.param_y} 线性标度关系</span>"
            f"<span style='color:#888;font-size:11px;'>R²={self.r_squared:.3f} ({conf_label}相关)</span></div>"
            f"<div style='font-size:12px;color:#555;margin-top:4px;'>"
            f"<b>方程:</b> {self.equation}<br>"
            f"<b>斜率:</b> {self.slope:.4f} &nbsp; <b>截距:</b> {self.intercept:.4f} &nbsp; "
            f"<b>p值:</b> {self.p_value:.4f} &nbsp; <b>数据点:</b> {self.n_points}<br>"
            f"<b>科学依据:</b> {self.scientific_basis}</div></div>"
        )


@dataclass
class QuantitativeReport:
    """定量建模报告"""
    scaling_relations: List[ScalingRelation] = field(default_factory=list)
    correlations: List[Dict] = field(default_factory=list)

    def to_html(self) -> str:
        parts = []
        parts.append(
            f"<h4 style='color:#534AB7;'>定量关联建模报告 "
            f"({len(self.scaling_relations)} 条标度关系)</h4>"
        )
        for sr in self.scaling_relations:
            parts.append(sr.to_html())
        if self.correlations:
            parts.append("<h5 style='color:#555;margin-top:12px;'>相关系数矩阵</h5>")
            for c in self.correlations:
                parts.append(
                    f"<p style='font-size:12px;'>{c.get('param_a','')} ↔ "
                    f"{c.get('param_b','')}: "
                    f"r={c.get('pearson_r', 0):.3f}, "
                    f"ρ={c.get('spearman_rho', 0):.3f}</p>"
                )
        return "\n".join(parts)

    def to_context_text(self) -> str:
        """生成可注入假设生成上下文的文本"""
        if not self.scaling_relations:
            return ""
        lines = ["=== 定量标度关系 ===", ""]
        for i, sr in enumerate(self.scaling_relations, 1):
            lines.append(f"标度关系 {i}: {sr.equation}")
            lines.append(f"  R² = {sr.r_squared:.4f}, p = {sr.p_value:.4f}, n = {sr.n_points}")
            lines.append(f"  依据: {sr.scientific_basis}")
            # 附上具体数据点
            for dp in sr.data_points_used:
                lines.append(f"    - {dp.get('material','')}: "
                             f"{sr.param_x}={dp.get('x','?')} {sr.unit_x}, "
                             f"{sr.param_y}={dp.get('y','?')} {sr.unit_y}")
            lines.append("")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        """生成结构化定量建模报告，供可执行验证与报告渲染使用。"""
        return {
            "summary": {
                "scaling_relation_count": len(self.scaling_relations),
                "correlation_count": len(self.correlations),
                "strong_scaling_relation_count": sum(
                    1 for sr in self.scaling_relations if sr.r_squared >= 0.85
                ),
            },
            "scaling_relations": [sr.to_dict() for sr in self.scaling_relations],
            "correlations": list(self.correlations),
        }


class QuantitativeMiner:
    """
    定量关联建模引擎。

    从清洗后的多模态数据中拟合定量关系：
      1. 线性标度关系（如 d-band center → adsorption energy）
      2. Pearson / Spearman 相关系数
      3. 为 ResultsVerificationAgent 提供可计算公式
    """

    # 已知科学标度关系（用于引导拟合方向）
    KNOWN_SCALING_PAIRS = [
        {
            "x": "d-band center", "y": "adsorption energy",
            "basis": "Norskov d-band theory: ΔE_ads = α·ε_d + β",
            "min_points": 3,
        },
        {
            "x": "d-band center", "y": "overpotential",
            "basis": "Sabatier principle: optimal d-band center minimizes η",
            "min_points": 3,
        },
        {
            "x": "d-band center", "y": "half-wave potential",
            "basis": "d-band center determines ORR intermediate binding → E₁/₂",
            "min_points": 3,
        },
        {
            "x": "binding energy xps", "y": "d-band center",
            "basis": "XPS core-level shift correlates with valence d-band position",
            "min_points": 3,
        },
        {
            "x": "lattice constant", "y": "adsorption energy",
            "basis": "Strain effect: tensile strain upshifts d-band → stronger binding",
            "min_points": 3,
        },
        {
            "x": "interlayer distance", "y": "d-band center",
            "basis": "Layer spacing modulates π-π coupling and d-band structure",
            "min_points": 3,
        },
        {
            "x": "binding energy xps", "y": "overpotential",
            "basis": "Oxidation state (XPS) → d-electron filling → oxygen binding → η",
            "min_points": 3,
        },
        {
            "x": "adsorption energy", "y": "overpotential",
            "basis": "Volcano plot: ΔG*OH ~ 0.1-0.5 eV gives optimal ORR activity",
            "min_points": 3,
        },
    ]

    def mine(
        self,
        data_points: List[CleanedDataPoint],
        domain: str = "",
    ) -> QuantitativeReport:
        """
        从清洗后的数据点中拟合定量关系。

        Args:
            data_points: 清洗后的数据点列表
            domain: 领域标识（可选，用于加载领域参数别名）

        Returns:
            QuantitativeReport
        """
        report = QuantitativeReport()

        # 构建参数索引：标准名 → [CleanedDataPoint]
        # 如果指定了领域，使用知识库的别名映射
        param_patterns = dict(_PARAM_PATTERNS)  # 默认
        if domain:
            try:
                from knowledge_base import get_aliases_for_domain
                domain_aliases = get_aliases_for_domain(domain)
                if domain_aliases:
                    param_patterns = domain_aliases
            except ImportError:
                pass

        param_index: Dict[str, List[CleanedDataPoint]] = {}
        for dp in data_points:
            for std_name in self._match_param_with(dp, param_patterns):
                param_index.setdefault(std_name, []).append(dp)

        # 尝试拟合已知的标度关系对
        for pair in self.KNOWN_SCALING_PAIRS:
            x_name = pair["x"]
            y_name = pair["y"]
            pts_x = param_index.get(x_name, [])
            pts_y = param_index.get(y_name, [])

            if len(pts_x) < pair["min_points"] or len(pts_y) < pair["min_points"]:
                continue

            # 尝试按来源配对数据点
            paired = self._pair_by_source(pts_x, pts_y)
            if len(paired) < pair["min_points"]:
                continue

            # 拟合线性回归
            x_vals = [p[0] for p in paired]
            y_vals = [p[1] for p in paired]
            sr = self._fit_linear(
                x_vals, y_vals,
                param_x=x_name, param_y=y_name,
                unit_x=pts_x[0].unit, unit_y=pts_y[0].unit,
                basis=pair["basis"],
                paired_points=paired,
                sources_x=[p[2] for p in paired],
                sources_y=[p[3] for p in paired],
                params_x_cn=[pts_x[0].parameter_cn or x_name],
                params_y_cn=[pts_y[0].parameter_cn or y_name],
            )
            if sr and sr.r_squared > 0.3:  # 至少弱相关才保留
                report.scaling_relations.append(sr)

        # 计算相关系数
        param_names = list(param_index.keys())
        for i in range(len(param_names)):
            for j in range(i + 1, len(param_names)):
                pts_a = param_index[param_names[i]]
                pts_b = param_index[param_names[j]]
                paired = self._pair_by_source(pts_a, pts_b)
                if len(paired) < 3:
                    continue
                a_vals = [p[0] for p in paired]
                b_vals = [p[1] for p in paired]
                pearson_r = self._pearson_r(a_vals, b_vals)
                spearman_rho = self._spearman_rho(a_vals, b_vals)
                if abs(pearson_r) > 0.5 or abs(spearman_rho) > 0.5:
                    report.correlations.append({
                        "param_a": param_names[i],
                        "param_b": param_names[j],
                        "pearson_r": pearson_r,
                        "spearman_rho": spearman_rho,
                        "n_points": len(paired),
                    })

        return report

    def _pair_by_source(
        self,
        pts_x: List[CleanedDataPoint],
        pts_y: List[CleanedDataPoint],
    ) -> List[Tuple[float, float, str, str]]:
        """
        按来源配对数据点，返回 [(x_val, y_val, source_x, source_y), ...]
        对于同一来源的多数据点，做笛卡尔积。
        """
        from collections import defaultdict
        x_by_source = defaultdict(list)
        for p in pts_x:
            x_by_source[p.source].append(p)
        y_by_source = defaultdict(list)
        for p in pts_y:
            y_by_source[p.source].append(p)

        paired = []
        # 同来源配对
        common_sources = set(x_by_source.keys()) & set(y_by_source.keys())
        for src in common_sources:
            for px in x_by_source[src]:
                for py in y_by_source[src]:
                    paired.append((px.value, py.value, px.source, py.source))

        # 如果同来源配对不够，尝试跨来源配对（取最近的值）
        if len(paired) < 3:
            paired = []
            for px in pts_x:
                for py in pts_y:
                    paired.append((px.value, py.value, px.source, py.source))

        return paired

    def _fit_linear(
        self,
        x_vals: List[float],
        y_vals: List[float],
        param_x: str,
        param_y: str,
        unit_x: str,
        unit_y: str,
        basis: str,
        paired_points: List[Tuple[float, float, str, str]],
        sources_x: List[str] = None,
        sources_y: List[str] = None,
        params_x_cn: List[str] = None,
        params_y_cn: List[str] = None,
    ) -> Optional[ScalingRelation]:
        """最小二乘法线性回归拟合"""
        n = len(x_vals)
        if n < 3:
            return None

        # 最小二乘法
        sum_x = sum(x_vals)
        sum_y = sum(y_vals)
        sum_xx = sum(x * x for x in x_vals)
        sum_xy = sum(x * y for x, y in zip(x_vals, y_vals))

        denom = n * sum_xx - sum_x * sum_x
        if abs(denom) < 1e-12:
            return None

        slope = (n * sum_xy - sum_x * sum_y) / denom
        intercept = (sum_y - slope * sum_x) / n

        # 计算 R²
        y_mean = sum_y / n
        ss_tot = sum((y - y_mean) ** 2 for y in y_vals)
        ss_res = sum((y - (slope * x + intercept)) ** 2 for x, y in zip(x_vals, y_vals))
        r_squared = 1 - (ss_res / ss_tot) if ss_tot > 0 else 0.0

        # 近似 p 值（t 检验）
        if n > 2:
            se = (ss_res / (n - 2)) ** 0.5 if ss_res >= 0 else 0
            ss_x = sum((x - sum_x / n) ** 2 for x in x_vals)
            if ss_x > 0 and se > 0:
                t_stat = slope / (se / ss_x ** 0.5)
                # 近似双侧 p 值（使用简化的 t 分布近似）
                p_value = self._approx_p_value(t_stat, n - 2)
            else:
                p_value = 1.0
        else:
            p_value = 1.0

        # 构建方程字符串
        sign = "+" if intercept >= 0 else "-"
        eq = f"{param_y} = {slope:.4f} × {param_x} {sign} {abs(intercept):.4f} ({unit_y})"

        # 构建数据点记录
        data_pts_used = []
        for i, (xv, yv, sx, sy) in enumerate(paired_points):
            data_pts_used.append({
                "material": sx if sx == sy else f"{sx}/{sy}",
                "x": round(xv, 4),
                "y": round(yv, 4),
            })

        return ScalingRelation(
            param_x=param_x,
            param_y=param_y,
            slope=slope,
            intercept=intercept,
            r_squared=r_squared,
            p_value=p_value,
            n_points=n,
            unit_x=unit_x,
            unit_y=unit_y,
            equation=eq,
            scientific_basis=basis,
            data_points_used=data_pts_used,
        )

    @staticmethod
    def _pearson_r(x: List[float], y: List[float]) -> float:
        """Pearson 相关系数"""
        n = len(x)
        if n < 3:
            return 0.0
        mx = sum(x) / n
        my = sum(y) / n
        num = sum((xi - mx) * (yi - my) for xi, yi in zip(x, y))
        den_x = (sum((xi - mx) ** 2 for xi in x)) ** 0.5
        den_y = (sum((yi - my) ** 2 for yi in y)) ** 0.5
        if den_x * den_y == 0:
            return 0.0
        return num / (den_x * den_y)

    @staticmethod
    def _spearman_rho(x: List[float], y: List[float]) -> float:
        """Spearman 秩相关系数"""
        n = len(x)
        if n < 3:
            return 0.0

        def rank(vals):
            sorted_vals = sorted(enumerate(vals), key=lambda v: v[1])
            ranks = [0] * len(vals)
            for rank_i, (orig_i, _) in enumerate(sorted_vals):
                ranks[orig_i] = rank_i + 1
            return ranks

        rx = rank(x)
        ry = rank(y)
        n_pts = len(rx)
        mr = (n_pts + 1) / 2
        num = sum((rxi - mr) * (ryi - mr) for rxi, ryi in zip(rx, ry))
        den = (sum((rxi - mr) ** 2 for rxi in rx) * sum((ryi - mr) ** 2 for ryi in ry)) ** 0.5
        if den == 0:
            return 0.0
        return num / den

    @staticmethod
    def _approx_p_value(t_stat: float, df: int) -> float:
        """近似双侧 p 值（基于正态分布近似 t 分布，df>5 时精度可接受）"""
        import math
        z = abs(t_stat)
        # 使用 Abramowitz & Stegun 近似计算标准正态 CDF
        # P(|Z| > z) = 2 * (1 - Φ(z))
        b0 = 0.2316419
        b1 = 0.319381530
        b2 = -0.356563782
        b3 = 1.781477937
        b4 = -1.821255978
        b5 = 1.330274429
        t = 1.0 / (1.0 + b0 * z)
        pdf = math.exp(-0.5 * z * z) / math.sqrt(2 * math.pi)
        cdf = 1.0 - pdf * (b1 * t + b2 * t**2 + b3 * t**3 + b4 * t**4 + b5 * t**5)
        p = 2.0 * (1.0 - cdf)
        return max(0.0, min(1.0, p))

    @staticmethod
    def _match_param_with(dp: CleanedDataPoint, patterns: Dict[str, List[str]]) -> List[str]:
        """使用指定的别名映射返回数据点参数匹配到的标准名列表。"""
        matches = []
        name_lower = dp.parameter.lower()
        cn_lower = dp.parameter_cn.lower() if dp.parameter_cn else ""
        combined = name_lower + " " + cn_lower + " " + dp.source.lower()

        for std_name, pats in patterns.items():
            for pat in pats:
                if pat.lower() in combined:
                    matches.append(std_name)
                    break
        return matches


def mine_quantitative(
    data_points: List[CleanedDataPoint],
    domain: str = "",
) -> QuantitativeReport:
    """
    一键定量关联建模。

    Args:
        data_points: 清洗后的数据点
        domain: 领域标识（可选）

    Returns:
        QuantitativeReport
    """
    miner = QuantitativeMiner()
    return miner.mine(data_points, domain=domain)


# ─────────────────────────────────────────────────────────────
# 高级多模型定量建模引擎 (P1: AdvancedMiner)
# ─────────────────────────────────────────────────────────────

@dataclass
class FittedModel:
    """单个拟合模型的结果"""
    model_type: str           # "linear" / "polynomial" / "symbolic" / "random_forest"
    equation: str             # 方程字符串
    r_squared: float = 0.0    # R² 决定系数
    adjusted_r_squared: float = 0.0  # 调整后 R²
    aic: float = float("inf")  # AIC 信息准则（越小越好）
    n_params: int = 2         # 模型参数个数（用于 AIC 惩罚）
    predictions: List[float] = field(default_factory=list)  # 模型预测值
    residuals: List[float] = field(default_factory=list)     # 残差

    # 符号回归特有字段
    basis_functions: List[str] = field(default_factory=list)  # 使用的基函数
    coefficients: List[float] = field(default_factory=list)   # 对应系数

    # 随机森林特有字段
    feature_importance: Dict[str, float] = field(default_factory=dict)
    oob_score: float = -1.0  # OOB R²

    def to_dict(self) -> dict:
        d = {
            "model_type": self.model_type,
            "equation": self.equation,
            "r_squared": round(self.r_squared, 4),
            "adjusted_r_squared": round(self.adjusted_r_squared, 4),
            "aic": round(self.aic, 2),
            "n_params": self.n_params,
        }
        if self.basis_functions:
            d["basis_functions"] = self.basis_functions
            d["coefficients"] = [round(c, 4) for c in self.coefficients]
        if self.feature_importance:
            d["feature_importance"] = self.feature_importance
            d["oob_score"] = round(self.oob_score, 4)
        return d


@dataclass
class ModelResult:
    """参数对的多模型比较结果"""
    param_x: str
    param_y: str
    unit_x: str = ""
    unit_y: str = ""
    models: List[FittedModel] = field(default_factory=list)
    best_model: Optional[FittedModel] = None   # AIC 最优模型
    data_points_used: List[Dict] = field(default_factory=list)
    scientific_basis: str = ""

    @property
    def best_equation(self) -> str:
        return self.best_model.equation if self.best_model else "N/A"

    @property
    def best_r_squared(self) -> float:
        return self.best_model.r_squared if self.best_model else 0.0

    def to_dict(self) -> dict:
        return {
            "param_x": self.param_x,
            "param_y": self.param_y,
            "unit_x": self.unit_x,
            "unit_y": self.unit_y,
            "best_equation": self.best_equation,
            "best_r_squared": round(self.best_r_squared, 4),
            "best_model_type": self.best_model.model_type if self.best_model else "",
            "best_aic": round(self.best_model.aic, 2) if self.best_model else None,
            "models": [m.to_dict() for m in self.models],
            "scientific_basis": self.scientific_basis,
            "data_points_used": self.data_points_used,
        }

    def to_html(self) -> str:
        if not self.best_model:
            return ""
        bm = self.best_model
        # 颜色基于 R²
        color = "#27ae60" if bm.r_squared >= 0.85 else ("#f39c12" if bm.r_squared >= 0.6 else "#e74c3c")
        conf = "强" if bm.r_squared >= 0.85 else ("中" if bm.r_squared >= 0.6 else "弱")

        parts = [
            f"<div style='border:1px solid {color};border-radius:8px;padding:10px;margin:6px 0;background:#fafbfc;'>",
            f"<div style='display:flex;justify-content:space-between;align-items:center;'>",
            f"<span style='color:{color};font-weight:500;font-size:13px;'>"
            f"🔬 {self.param_x} → {self.param_y} 多模型拟合</span>",
            f"<span style='color:#888;font-size:11px;'>最优: {bm.model_type} | R²={bm.r_squared:.3f} ({conf})</span></div>",
            f"<div style='font-size:12px;color:#555;margin-top:4px;'>",
            f"<b>最优方程:</b> {bm.equation}<br>",
            f"<b>AIC:</b> {bm.aic:.2f} &nbsp; <b>调整R²:</b> {bm.adjusted_r_squared:.3f} &nbsp; "
            f"<b>参数数:</b> {bm.n_params}<br>",
        ]

        # 各模型比较表
        if len(self.models) > 1:
            parts.append("<table style='font-size:11px;margin-top:6px;border-collapse:collapse;'>")
            parts.append(
                "<tr style='background:#eee;'>"
                "<th style='padding:2px 6px;'>模型</th>"
                "<th style='padding:2px 6px;'>R²</th>"
                "<th style='padding:2px 6px;'>调整R²</th>"
                "<th style='padding:2px 6px;'>AIC</th>"
                "</tr>"
            )
            for m in sorted(self.models, key=lambda m: m.aic):
                highlight = "font-weight:bold;" if m is bm else ""
                parts.append(
                    f"<tr style='{highlight}'>"
                    f"<td style='padding:2px 6px;'>{m.model_type}</td>"
                    f"<td style='padding:2px 6px;'>{m.r_squared:.4f}</td>"
                    f"<td style='padding:2px 6px;'>{m.adjusted_r_squared:.4f}</td>"
                    f"<td style='padding:2px 6px;'>{m.aic:.2f}</td>"
                    f"</tr>"
                )
            parts.append("</table>")

        if self.scientific_basis:
            parts.append(f"<b>科学依据:</b> {self.scientific_basis}")
        parts.append("</div></div>")
        return "".join(parts)

    def to_context_text(self) -> str:
        """生成可注入假设生成上下文的文本"""
        if not self.best_model:
            return ""
        lines = [f"=== 多模型定量关系: {self.param_x} → {self.param_y} ===", ""]
        lines.append(f"最优模型: {self.best_model.model_type}")
        lines.append(f"方程: {self.best_model.equation}")
        lines.append(f"R² = {self.best_model.r_squared:.4f}, "
                     f"调整R² = {self.best_model.adjusted_r_squared:.4f}, "
                     f"AIC = {self.best_model.aic:.2f}")
        if len(self.models) > 1:
            lines.append("")
            lines.append("模型比较:")
            for m in sorted(self.models, key=lambda m: m.aic):
                marker = " ← 最优" if m is self.best_model else ""
                lines.append(f"  {m.model_type}: R²={m.r_squared:.4f}, AIC={m.aic:.2f}{marker}")
        if self.scientific_basis:
            lines.append(f"依据: {self.scientific_basis}")
        lines.append("")
        return "\n".join(lines)


@dataclass
class AdvancedQuantitativeReport:
    """多模型定量建模报告"""
    model_results: List[ModelResult] = field(default_factory=list)
    correlations: List[Dict] = field(default_factory=list)

    def to_html(self) -> str:
        parts = [
            f"<h4 style='color:#534AB7;'>多模型定量建模报告 "
            f"({len(self.model_results)} 条拟合关系)</h4>"
        ]
        for mr in self.model_results:
            parts.append(mr.to_html())
        if self.correlations:
            parts.append("<h5 style='color:#555;margin-top:12px;'>相关系数矩阵</h5>")
            for c in self.correlations:
                parts.append(
                    f"<p style='font-size:12px;'>{c.get('param_a', '')} ↔ "
                    f"{c.get('param_b', '')}: "
                    f"r={c.get('pearson_r', 0):.3f}, "
                    f"ρ={c.get('spearman_rho', 0):.3f}</p>"
                )
        return "\n".join(parts)

    def to_context_text(self) -> str:
        """生成可注入假设生成上下文的文本"""
        if not self.model_results:
            return ""
        lines = []
        for mr in self.model_results:
            ctx = mr.to_context_text()
            if ctx:
                lines.append(ctx)
        return "\n".join(lines) if lines else ""


class AdvancedMiner:
    """
    高级多模型定量建模引擎。

    从清洗后的多模态数据中用多种模型拟合定量关系：
      1. 线性回归（纯 Python，继承自 QuantitativeMiner）
      2. 多项式回归（二次，纯 Python）
      3. 符号回归（纯 Python，基函数组合搜索）
      4. 随机森林（可选依赖 sklearn）
    用 AIC 信息准则进行模型选择，自动推荐最优模型。
    """

    # 已知科学标度关系（复用 QuantitativeMiner.KNOWN_SCALING_PAIRS）
    KNOWN_SCALING_PAIRS = QuantitativeMiner.KNOWN_SCALING_PAIRS

    def mine(
        self,
        data_points: List[CleanedDataPoint],
        domain: str = "",
        models: Optional[List[str]] = None,
    ) -> AdvancedQuantitativeReport:
        """
        从清洗后的数据点中用多模型拟合定量关系。

        Args:
            data_points: 清洗后的数据点列表
            domain: 领域标识（可选）
            models: 要使用的模型列表，默认 ["linear", "polynomial", "symbolic"]
                    可选 "random_forest"（需 sklearn）

        Returns:
            AdvancedQuantitativeReport
        """
        if models is None:
            models = ["linear", "polynomial", "symbolic"]

        report = AdvancedQuantitativeReport()

        # 构建参数索引
        param_patterns = dict(_PARAM_PATTERNS)
        if domain:
            try:
                from knowledge_base import get_aliases_for_domain
                domain_aliases = get_aliases_for_domain(domain)
                if domain_aliases:
                    param_patterns = domain_aliases
            except ImportError:
                pass

        param_index: Dict[str, List[CleanedDataPoint]] = {}
        for dp in data_points:
            for std_name in QuantitativeMiner._match_param_with(dp, param_patterns):
                param_index.setdefault(std_name, []).append(dp)

        # 复用 QuantitativeMiner 的配对逻辑
        qm = QuantitativeMiner()

        # 尝试拟合已知的标度关系对
        for pair in self.KNOWN_SCALING_PAIRS:
            x_name = pair["x"]
            y_name = pair["y"]
            pts_x = param_index.get(x_name, [])
            pts_y = param_index.get(y_name, [])

            if len(pts_x) < pair["min_points"] or len(pts_y) < pair["min_points"]:
                continue

            paired = qm._pair_by_source(pts_x, pts_y)
            if len(paired) < pair["min_points"]:
                continue

            x_vals = [p[0] for p in paired]
            y_vals = [p[1] for p in paired]

            # 多模型拟合
            mr = self._fit_all_models(
                x_vals, y_vals,
                param_x=x_name, param_y=y_name,
                unit_x=pts_x[0].unit, unit_y=pts_y[0].unit,
                basis=pair["basis"],
                paired_points=paired,
                models_to_fit=models,
            )
            if mr and mr.best_model and mr.best_model.r_squared > 0.2:
                report.model_results.append(mr)

        # 相关系数（复用 QuantitativeMiner 逻辑）
        param_names = list(param_index.keys())
        for i in range(len(param_names)):
            for j in range(i + 1, len(param_names)):
                pts_a = param_index[param_names[i]]
                pts_b = param_index[param_names[j]]
                paired = qm._pair_by_source(pts_a, pts_b)
                if len(paired) < 3:
                    continue
                a_vals = [p[0] for p in paired]
                b_vals = [p[1] for p in paired]
                pearson_r = QuantitativeMiner._pearson_r(a_vals, b_vals)
                spearman_rho = QuantitativeMiner._spearman_rho(a_vals, b_vals)
                if abs(pearson_r) > 0.5 or abs(spearman_rho) > 0.5:
                    report.correlations.append({
                        "param_a": param_names[i],
                        "param_b": param_names[j],
                        "pearson_r": pearson_r,
                        "spearman_rho": spearman_rho,
                        "n_points": len(paired),
                    })

        return report

    # ── 模型拟合核心 ──

    def _fit_all_models(
        self,
        x_vals: List[float],
        y_vals: List[float],
        param_x: str,
        param_y: str,
        unit_x: str,
        unit_y: str,
        basis: str,
        paired_points: List[Tuple[float, float, str, str]],
        models_to_fit: List[str],
    ) -> Optional[ModelResult]:
        """用多种模型拟合，返回 ModelResult"""
        n = len(x_vals)
        if n < 3:
            return None

        fitted_models: List[FittedModel] = []

        for model_type in models_to_fit:
            fm = None
            if model_type == "linear":
                fm = self._fit_linear(x_vals, y_vals)
            elif model_type == "polynomial":
                fm = self._fit_polynomial(x_vals, y_vals)
            elif model_type == "symbolic":
                fm = self._fit_symbolic(x_vals, y_vals)
            elif model_type == "random_forest":
                fm = self._fit_random_forest(x_vals, y_vals)
            else:
                logger.warning(f"未知模型类型: {model_type}")
                continue

            if fm is not None:
                fitted_models.append(fm)

        if not fitted_models:
            return None

        # 选择 AIC 最小的模型作为最优
        best = min(fitted_models, key=lambda m: m.aic)

        # 构建数据点记录
        data_pts_used = []
        for xv, yv, sx, sy in paired_points:
            data_pts_used.append({
                "material": sx if sx == sy else f"{sx}/{sy}",
                "x": round(xv, 4),
                "y": round(yv, 4),
            })

        return ModelResult(
            param_x=param_x,
            param_y=param_y,
            unit_x=unit_x,
            unit_y=unit_y,
            models=fitted_models,
            best_model=best,
            data_points_used=data_pts_used,
            scientific_basis=basis,
        )

    # ── 线性回归 ──

    def _fit_linear(self, x: List[float], y: List[float]) -> Optional[FittedModel]:
        """纯 Python 最小二乘线性回归"""
        n = len(x)
        if n < 3:
            return None

        sum_x = sum(x)
        sum_y = sum(y)
        sum_xx = sum(xi * xi for xi in x)
        sum_xy = sum(xi * yi for xi, yi in zip(x, y))

        denom = n * sum_xx - sum_x * sum_x
        if abs(denom) < 1e-12:
            return None

        slope = (n * sum_xy - sum_x * sum_y) / denom
        intercept = (sum_y - slope * sum_x) / n

        predictions = [slope * xi + intercept for xi in x]
        residuals = [yi - pi for yi, pi in zip(y, predictions)]

        r_sq = self._r_squared(y, predictions)
        adj_r_sq = self._adjusted_r_squared(r_sq, n, 2)
        aic = self._compute_aic(residuals, n, 2)

        sign = "+" if intercept >= 0 else "-"
        eq = f"y = {slope:.4f}·x {sign} {abs(intercept):.4f}"

        return FittedModel(
            model_type="linear",
            equation=eq,
            r_squared=r_sq,
            adjusted_r_squared=adj_r_sq,
            aic=aic,
            n_params=2,
            predictions=predictions,
            residuals=residuals,
            coefficients=[slope, intercept],
        )

    # ── 多项式回归（二次） ──

    def _fit_polynomial(self, x: List[float], y: List[float], degree: int = 2) -> Optional[FittedModel]:
        """
        纯 Python 多项式回归（默认二次）。
        用正规方程 (X^T X)^{-1} X^T y 求解。
        """
        n = len(x)
        if n < degree + 1:
            return None

        # 构建范德蒙矩阵
        # X[i][j] = x_i^j, j=0..degree
        X = [[xi ** j for j in range(degree + 1)] for xi in x]

        # X^T X
        p = degree + 1
        XtX = [[0.0] * p for _ in range(p)]
        for i in range(p):
            for j in range(p):
                XtX[i][j] = sum(X[k][i] * X[k][j] for k in range(n))

        # X^T y
        Xty = [sum(X[k][i] * y[k] for k in range(n)) for i in range(p)]

        # 高斯消元求解
        coeffs = self._solve_linear_system(XtX, Xty)
        if coeffs is None:
            return None

        predictions = [sum(coeffs[j] * (xi ** j) for j in range(degree + 1)) for xi in x]
        residuals = [yi - pi for yi, pi in zip(y, predictions)]

        r_sq = self._r_squared(y, predictions)
        n_params = degree + 1
        adj_r_sq = self._adjusted_r_squared(r_sq, n, n_params)
        aic = self._compute_aic(residuals, n, n_params)

        # 方程字符串
        terms = []
        for j in range(degree, -1, -1):
            c = coeffs[j]
            if abs(c) < 1e-8:
                continue
            if j == 0:
                terms.append(f"{c:+.4f}")
            elif j == 1:
                terms.append(f"{c:+.4f}·x")
            else:
                terms.append(f"{c:+.4f}·x²" if j == 2 else f"{c:+.4f}·x^{j}")

        eq = "y = " + " ".join(terms).replace("+ -", "- ").lstrip("+ ").strip()
        if eq.startswith("y = "):
            pass  # fine
        else:
            eq = f"y = {eq}"

        return FittedModel(
            model_type="polynomial",
            equation=eq,
            r_squared=r_sq,
            adjusted_r_squared=adj_r_sq,
            aic=aic,
            n_params=n_params,
            predictions=predictions,
            residuals=residuals,
            coefficients=list(coeffs),
        )

    # ── 符号回归（纯 Python 基函数组合搜索） ──

    def _fit_symbolic(self, x: List[float], y: List[float]) -> Optional[FittedModel]:
        """
        纯 Python 符号回归。

        策略：在基函数空间 {1, x, 1/x, log|x|, x²} 中搜索
        1~3 个基函数的最优线性组合，用 AIC 选模型。
        """
        n = len(x)
        if n < 4:
            return None

        # 安全检查：排除不安全的值
        safe_x = []
        safe_y = []
        for xi, yi in zip(x, y):
            if xi == 0 or xi != xi:  # 排除 0 和 NaN
                continue
            if abs(xi) < 1e-10:
                continue
            safe_x.append(xi)
            safe_y.append(yi)

        if len(safe_x) < 4:
            return None

        # 构建基函数矩阵
        # 基函数: x, 1/x, log|x|, x²
        basis_names = ["x", "1/x", "log|x|", "x²"]
        basis_cols = []
        for xi in safe_x:
            row = [
                xi,
                1.0 / xi,
                math.log(abs(xi)) if abs(xi) > 1e-15 else 0.0,
                xi * xi,
            ]
            basis_cols.append(row)

        best_fm: Optional[FittedModel] = None
        best_aic = float("inf")

        # 搜索 1~3 个基函数的组合
        from itertools import combinations

        for n_basis in range(1, min(4, len(basis_names) + 1)):
            for combo in combinations(range(len(basis_names)), n_basis):
                # 构建设计矩阵: 截距 + 选中的基函数
                X = [[1.0] + [basis_cols[k][j] for j in combo] for k in range(len(safe_x))]
                p = 1 + n_basis  # 截距 + 基函数

                if len(safe_x) <= p:
                    continue

                # 正规方程
                XtX = [[0.0] * p for _ in range(p)]
                for i in range(p):
                    for j in range(p):
                        XtX[i][j] = sum(X[k][i] * X[k][j] for k in range(len(safe_x)))

                Xty = [sum(X[k][i] * safe_y[k] for k in range(len(safe_x))) for i in range(p)]

                coeffs = self._solve_linear_system(XtX, Xty)
                if coeffs is None:
                    continue

                # 预测
                predictions = [sum(coeffs[j] * X[k][j] for j in range(p)) for k in range(len(safe_x))]
                residuals = [safe_y[k] - predictions[k] for k in range(len(safe_x))]

                r_sq = self._r_squared(safe_y, predictions)
                adj_r_sq = self._adjusted_r_squared(r_sq, len(safe_x), p)
                aic = self._compute_aic(residuals, len(safe_x), p)

                if aic < best_aic:
                    best_aic = aic
                    # 方程字符串
                    used_names = [basis_names[j] for j in combo]
                    terms = [f"{coeffs[0]:.4f}"]
                    for idx, j in enumerate(combo):
                        c = coeffs[idx + 1]
                        sign = "+" if c >= 0 else "-"
                        terms.append(f"{sign} {abs(c):.4f}·{basis_names[j]}")
                    eq = "y = " + " ".join(terms)

                    best_fm = FittedModel(
                        model_type="symbolic",
                        equation=eq,
                        r_squared=r_sq,
                        adjusted_r_squared=adj_r_sq,
                        aic=aic,
                        n_params=p,
                        predictions=predictions,
                        residuals=residuals,
                        basis_functions=used_names,
                        coefficients=list(coeffs),
                    )

        return best_fm

    # ── 随机森林（可选依赖 sklearn） ──

    def _fit_random_forest(self, x: List[float], y: List[float]) -> Optional[FittedModel]:
        """
        随机森林回归（可选依赖 sklearn）。
        sklearn 不可用时跳过，不报错。
        """
        try:
            from sklearn.ensemble import RandomForestRegressor
            import numpy as np
        except ImportError:
            logger.info("sklearn 未安装，跳过随机森林模型")
            return None

        n = len(x)
        if n < 5:
            return None

        try:
            import numpy as np
            X_arr = np.array(x).reshape(-1, 1)
            y_arr = np.array(y)

            rf = RandomForestRegressor(
                n_estimators=100,
                max_depth=5,
                min_samples_split=2,
                oob_score=True,
                random_state=42,
            )
            rf.fit(X_arr, y_arr)

            predictions = rf.predict(X_arr).tolist()
            residuals = (y_arr - rf.predict(X_arr)).tolist()

            r_sq = self._r_squared(y, predictions)
            adj_r_sq = self._adjusted_r_squared(r_sq, n, rf.n_estimators)
            aic = self._compute_aic(residuals, n, min(10, n // 2))  # 近似参数数

            # 特征重要性（1D 时只有一个特征）
            feat_imp = {"x": round(rf.feature_importances_[0], 4)} if hasattr(rf, 'feature_importances_') else {}

            return FittedModel(
                model_type="random_forest",
                equation=f"RF(x) [n_estimators={rf.n_estimators}, OOB R²={rf.oob_score_:.3f}]",
                r_squared=r_sq,
                adjusted_r_squared=adj_r_sq,
                aic=aic,
                n_params=min(10, n // 2),  # 近似
                predictions=predictions,
                residuals=residuals,
                feature_importance=feat_imp,
                oob_score=rf.oob_score_,
            )
        except Exception as e:
            logger.warning(f"随机森林拟合失败: {e}")
            return None

    # ── 工具函数 ──

    @staticmethod
    def _r_squared(y_true: List[float], y_pred: List[float]) -> float:
        """计算 R² 决定系数"""
        n = len(y_true)
        if n == 0:
            return 0.0
        y_mean = sum(y_true) / n
        ss_tot = sum((yi - y_mean) ** 2 for yi in y_true)
        ss_res = sum((yi - pi) ** 2 for yi, pi in zip(y_true, y_pred))
        if ss_tot < 1e-12:
            return 1.0 if ss_res < 1e-12 else 0.0
        return max(0.0, 1.0 - ss_res / ss_tot)

    @staticmethod
    def _adjusted_r_squared(r_squared: float, n: int, p: int) -> float:
        """计算调整后 R²"""
        if n <= p:
            return 0.0
        return 1.0 - (1.0 - r_squared) * (n - 1) / (n - p)

    @staticmethod
    def _compute_aic(residuals: List[float], n: int, k: int) -> float:
        """
        计算 AIC（Akaike Information Criterion）。

        AIC = n·ln(RSS/n) + 2k
        其中 RSS = 残差平方和，k = 参数数（含截距）
        """
        if n == 0 or k == 0:
            return float("inf")
        rss = sum(r * r for r in residuals)
        if rss <= 0:
            rss = 1e-10  # 避免 log(0)
        return n * math.log(rss / n) + 2 * k

    @staticmethod
    def _solve_linear_system(A: List[List[float]], b: List[float]) -> Optional[List[float]]:
        """
        高斯消元法求解线性方程组 Ax = b。
        带部分主元选取。
        """
        n = len(b)
        if n == 0:
            return None

        # 增广矩阵
        aug = [A[i][:] + [b[i]] for i in range(n)]

        for col in range(n):
            # 部分主元选取
            max_row = col
            max_val = abs(aug[col][col])
            for row in range(col + 1, n):
                if abs(aug[row][col]) > max_val:
                    max_val = abs(aug[row][col])
                    max_row = row
            if max_val < 1e-12:
                return None  # 奇异矩阵

            # 交换行
            aug[col], aug[max_row] = aug[max_row], aug[col]

            # 消元
            for row in range(col + 1, n):
                factor = aug[row][col] / aug[col][col]
                for j in range(col, n + 1):
                    aug[row][j] -= factor * aug[col][j]

        # 回代
        x = [0.0] * n
        for i in range(n - 1, -1, -1):
            s = aug[i][n]
            for j in range(i + 1, n):
                s -= aug[i][j] * x[j]
            if abs(aug[i][i]) < 1e-12:
                return None
            x[i] = s / aug[i][i]

        return x


def mine_quantitative_advanced(
    data_points: List[CleanedDataPoint],
    domain: str = "",
    models: Optional[List[str]] = None,
) -> AdvancedQuantitativeReport:
    """
    一键高级多模型定量关联建模。

    Args:
        data_points: 清洗后的数据点
        domain: 领域标识（可选）
        models: 模型列表，默认 ["linear", "polynomial", "symbolic"]
                可选加 "random_forest"（需 sklearn）

    Returns:
        AdvancedQuantitativeReport
    """
    miner = AdvancedMiner()
    return miner.mine(data_points, domain=domain, models=models)
