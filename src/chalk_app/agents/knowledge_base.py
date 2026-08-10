"""
knowledge_base.py — 跨学科领域知识库

提供:
  - DomainDescriptor:  领域描述符/性质定义
  - DomainRelation:    descriptor-property 关系映射
  - TransferableTechnique: 跨领域可迁移技术
  - DomainProfile:     完整领域画像
  - KNOWLEDGE_BASE:    预置 15 大领域知识
  - detect_domain():   从文献文本自动检测研究领域
  - ExternalKnowledgeAdapter: 外部知识图谱 API 适配器

覆盖领域（对应化学学科14大研究方向）:
  1.  electrocatalysis          — 电催化（催化与表界面化学子领域）
  2.  photocatalysis            — 光催化（催化与表界面化学子领域）
  3.  battery_materials          — 电池材料（能源化学子领域）
  4.  synthetic_chemistry        — 合成化学
  5.  surface_interface_chemistry — 催化与表界面化学
  6.  physical_chemistry         — 物理化学
  7.  quantum_chemistry          — 量子化学
  8.  analytical_chemistry        — 分析化学
  9.  organic_chemistry           — 有机化学
  10. inorganic_chemistry         — 无机化学
  11. polymer_chemistry           — 高分子化学与物理
  12. chemical_biology            — 化学生物学
  13. materials_chemistry         — 材料化学
  14. energy_chemistry            — 能源化学
  15. environmental_chemistry     — 环境化学
  16. nano_chemistry              — 聚集体与纳米化学
  17. cluster_chemistry           — 团簇与仿生化学
  18. green_chemical_engineering  — 绿色化工与过程强化

用途：
  - DataMiner 动态加载领域关联规则
  - ReasoningChainAgent 注入领域先验知识
  - 假设生成管线自动适配领域上下文
  - CrossDomainAnalogyAgent 跨领域技术迁移
"""

from __future__ import annotations

import os
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
# 数据类
# ─────────────────────────────────────────────────────────────

@dataclass
class DomainDescriptor:
    """领域描述符/性质定义"""
    name: str                          # 标准名（如 "d-band center"）
    name_cn: str                       # 中文名
    aliases: List[str] = field(default_factory=list)  # 别名列表（用于匹配）
    unit: str = ""                     # 典型单位
    typical_range: Tuple[float, float] = (0.0, 1.0)    # 典型值范围
    description: str = ""              # 物理含义

    def matches(self, text: str) -> bool:
        """检查文本是否匹配该描述符（含别名）"""
        text_lower = text.lower()
        if self.name.lower() in text_lower or self.name_cn in text:
            return True
        for alias in self.aliases:
            if alias.lower() in text_lower:
                return True
        return False


@dataclass
class DomainRelation:
    """领域内 descriptor-property 关系"""
    descriptor: str           # 描述符标准名
    property: str             # 性质标准名
    formula: str              # 经验公式（如 "E_ads = α × ε_d + β"）
    domain: str               # 适用领域
    applicable_to: List[str] = field(default_factory=list)  # 适用材料类别
    reference: str = ""       # 文献依据
    confidence: float = 0.8   # 置信度

    def to_rule_dict(self) -> dict:
        """转换为 DataMiner 的 ASSOCIATION_RULES 格式"""
        return {
            "param_a": self.descriptor,
            "param_b": self.property,
            "relation_type": "descriptor",
            "description": f"{self.descriptor} → {self.property}",
            "scientific_basis": self.reference or self.formula,
        }


@dataclass
class TransferableTechnique:
    """跨领域可迁移技术"""
    from_domain: str          # 源领域
    to_domain: str            # 目标领域
    technique: str            # 技术名
    applicable_condition: str  # 适用条件
    example: str              # 示例


@dataclass
class DomainProfile:
    """完整领域画像"""
    name: str                          # 领域标识
    name_cn: str                       # 中文名
    descriptors: List[DomainDescriptor] = field(default_factory=list)
    properties: List[DomainDescriptor] = field(default_factory=list)  # 复用同一结构
    relations: List[DomainRelation] = field(default_factory=list)
    transferable_techniques: List[TransferableTechnique] = field(default_factory=list)
    baselines: List[str] = field(default_factory=list)       # 基线方法
    metrics: List[str] = field(default_factory=list)         # 核心评价指标
    characterization: List[str] = field(default_factory=list) # 标准表征手段
    experiment_templates: Dict = field(default_factory=dict)  # 实验设计模板

    def get_all_aliases(self) -> Dict[str, List[str]]:
        """获取所有描述符/性质的别名映射：标准名 → [别名列表]"""
        mapping = {}
        for d in self.descriptors + self.properties:
            mapping[d.name] = d.aliases + [d.name_cn]
        return mapping

    def get_all_rules(self) -> List[dict]:
        """获取所有关联规则（DataMiner 兼容格式）"""
        return [r.to_rule_dict() for r in self.relations]

    def find_descriptor(self, text: str) -> Optional[str]:
        """从文本中匹配描述符标准名"""
        for d in self.descriptors + self.properties:
            if d.matches(text):
                return d.name
        return None


# ─────────────────────────────────────────────────────────────
# 预置领域知识库
# ─────────────────────────────────────────────────────────────

# ═══════════════════════════════════════════════════════════
# 1. 电催化（原有）
# ═══════════════════════════════════════════════════════════

ELECTROCATALYSIS = DomainProfile(
    name="electrocatalysis",
    name_cn="电催化",
    descriptors=[
        DomainDescriptor("d-band center", "d带中心",
            ["d-band", "epsilon_d", "d band center", "d band"], "eV", (-3.0, 0.5),
            "金属 d 带中心相对 Fermi 能级的位置，决定吸附强度"),
        DomainDescriptor("adsorption energy", "吸附能",
            ["delta_g", "ΔG", "adsorption", "ΔE_ads"], "eV", (0.1, 8.0),
            "吸附质在表面的结合强度"),
        DomainDescriptor("binding energy", "结合能",
            ["bind energy", "E_bind"], "eV", (0.1, 10.0),
            "表面-吸附质相互作用的能量"),
        DomainDescriptor("binding energy xps", "XPS结合能",
            ["2p", "2p3/2", "2p1/2", "xps binding", "xps结合能"], "eV", (770, 890),
            "XPS 测得的芯层结合能位移"),
        DomainDescriptor("interlayer distance", "层间距",
            ["d-spacing", "d002", "d_002"], "Å", (3.0, 10.0),
            "层状材料的层间距"),
        DomainDescriptor("lattice constant", "晶格常数",
            ["lattice parameter", "a_0", "晶格参数"], "Å", (2.0, 15.0),
            "晶体晶格常数"),
        DomainDescriptor("coordination number", "配位数",
            ["CN", "coordination", "配位数"], "", (2, 12),
            "中心金属原子的配位数"),
        DomainDescriptor("work function", "功函数",
            ["phi", "功函数", "φ"], "eV", (2.0, 6.0),
            "金属表面的功函数"),
        DomainDescriptor("formation energy", "形成能",
            ["ΔE_f", "E_formation"], "eV/atom", (-5.0, 5.0),
            "化合物形成能"),
        DomainDescriptor("band gap", "带隙",
            ["E_g", "eg", "bandgap"], "eV", (0.0, 6.0),
            "半导体带隙"),
    ],
    properties=[
        DomainDescriptor("overpotential", "过电势",
            ["eta", "η", "overpotential"], "V", (0.1, 1.5),
            "催化反应的热力学过电势"),
        DomainDescriptor("half-wave potential", "半波电位",
            ["E_1/2", "e1/2", "E_half"], "V vs RHE", (0.5, 0.95),
            "ORR 半波电位"),
        DomainDescriptor("onset potential", "起始电位",
            ["E_onset", "onset"], "V vs RHE", (0.3, 1.2),
            "ORR 起始电位"),
        DomainDescriptor("tafel slope", "Tafel斜率",
            ["tafel", "塔菲尔斜率"], "mV/dec", (30, 120),
            "Tafel 斜率，反映反应动力学机理"),
        DomainDescriptor("electron transfer number", "电子转移数",
            ["n_e", "transfer number"], "", (2.0, 4.0),
            "ORR 电子转移数"),
        DomainDescriptor("limiting current density", "极限电流密度",
            ["J_L", "j_limit", "Jl"], "mA/cm²", (2.0, 8.0),
            "ORR 极限电流密度"),
        DomainDescriptor("faradaic efficiency", "法拉第效率",
            ["FE", "法拉第效率"], "%", (50, 100),
            "法拉第效率"),
        DomainDescriptor("coulombic efficiency", "库仑效率",
            ["CE", "库仑效率"], "%", (80, 100),
            "库仑效率"),
    ],
    relations=[
        DomainRelation("d-band center", "adsorption energy",
            "E_ads = α × ε_d + β", "electrocatalysis",
            ["transition metals"], "Norskov d-band theory", 0.85),
        DomainRelation("d-band center", "overpotential",
            "optimal ε_d minimizes η (volcano plot)", "electrocatalysis",
            ["transition metals"], "Sabatier principle", 0.80),
        DomainRelation("d-band center", "half-wave potential",
            "ε_d determines ORR intermediate binding → E_1/2", "electrocatalysis",
            ["transition metals"], "d-band theory for ORR", 0.75),
        DomainRelation("adsorption energy", "overpotential",
            "ΔG*OH ~ 0.1-0.5 eV gives optimal ORR activity (volcano peak)", "electrocatalysis",
            ["M-N-C catalysts"], "Volcano plot relationship", 0.85),
        DomainRelation("binding energy xps", "d-band center",
            "XPS core-level shift correlates with valence d-band position", "electrocatalysis",
            ["transition metals"], "Core-level shift theory", 0.75),
        DomainRelation("binding energy xps", "overpotential",
            "Metal oxidation state (XPS) → d-electron filling → oxygen binding → η", "electrocatalysis",
            ["M-N-C catalysts"], "Oxidation state-activity correlation", 0.70),
        DomainRelation("interlayer distance", "d-band center",
            "Increased spacing modulates π-π coupling and d-band structure", "electrocatalysis",
            ["layered materials"], "Interlayer coupling effect", 0.70),
        DomainRelation("lattice constant", "adsorption energy",
            "Tensile strain upshifts d-band → stronger binding", "electrocatalysis",
            ["strained metals"], "Norskov strain theory", 0.80),
        DomainRelation("tafel slope", "mechanism",
            "~60 mV/dec: first e⁻ transfer RDS; ~120 mV/dec: second e⁻ transfer RDS", "electrocatalysis",
            [], "Tafel analysis", 0.90),
        DomainRelation("electron transfer number", "mechanism",
            "n ≈ 4: 4e⁻ ORR; n ≈ 2: 2e⁻ peroxide pathway", "electrocatalysis",
            [], "RRDE analysis", 0.90),
        DomainRelation("coordination number", "d-band center",
            "Lower CN → narrower d-band → ε_d closer to E_F", "electrocatalysis",
            ["single-atom catalysts"], "Coordination-activity relationship", 0.75),
        DomainRelation("faradaic efficiency", "coulombic efficiency",
            "Higher FE indicates fewer side reactions, typically correlating with CE", "electrocatalysis",
            [], "Electrochemical efficiency correlation", 0.65),
    ],
    transferable_techniques=[
        TransferableTechnique("battery_materials", "electrocatalysis",
            "in-situ XRD", "晶格参数变化监测",
            "电池充放电中监测晶格变化 → 催化剂稳定性原位监测"),
        TransferableTechnique("photocatalysis", "electrocatalysis",
            "EPR 自旋捕获", "自由基中间体检测",
            "光催化中用 EPR 检测 ·OH → 电催化中检测 ORR 中间体"),
    ],
    baselines=["Pt/C (20 wt%)", "IrO₂", "RuO₂"],
    metrics=["half-wave potential", "Tafel slope", "onset potential",
             "stability (@ 1000 cycles)", "electron transfer number"],
    characterization=["XRD", "XPS", "SEM/TEM", "Raman", "BET", "XAS", "HAADF-STEM"],
    experiment_templates={
        "electrochemical_tests": ["LSV", "CV", "EIS", "chronoamperometry", "RRDE"],
        "standard_conditions": {
            "electrolyte": "0.1 M KOH",
            "rotation_speed": "1600 rpm",
            "scan_rate": "10 mV/s",
            "reference_electrode": "Ag/AgCl",
        },
    },
)

# ═══════════════════════════════════════════════════════════
# 2. 光催化（原有）
# ═══════════════════════════════════════════════════════════

PHOTOCATALYSIS = DomainProfile(
    name="photocatalysis",
    name_cn="光催化",
    descriptors=[
        DomainDescriptor("band gap", "带隙",
            ["E_g", "eg", "bandgap"], "eV", (0.5, 4.0),
            "半导体的带隙宽度，决定光吸收范围"),
        DomainDescriptor("conduction band", "导带",
            ["CB", "CBM", "导带底"], "eV", (-5.0, -2.0),
            "导带最低点位置，决定还原能力"),
        DomainDescriptor("valence band", "价带",
            ["VB", "VBM", "价带顶"], "eV", (-8.0, -4.0),
            "价带最高点位置，决定氧化能力"),
        DomainDescriptor("absorption edge", "吸收边",
            ["λ_edge", "吸收边"], "nm", (200, 800),
            "光吸收截止波长"),
        DomainDescriptor("specific surface area", "比表面积",
            ["BET", "S_BET", "SSA"], "m²/g", (5, 500),
            "BET 比表面积"),
        DomainDescriptor("particle size", "颗粒尺寸",
            ["crystallite size", "粒径"], "nm", (2, 200),
            "纳米颗粒尺寸"),
        DomainDescriptor("defect density", "缺陷密度",
            ["oxygen vacancy", "Vo", "缺陷"], "cm⁻²", (1e10, 1e16),
            "表面缺陷密度"),
    ],
    properties=[
        DomainDescriptor("H₂ evolution rate", "产氢速率",
            ["H₂ production", "H₂ rate"], "μmol/g/h", (10, 50000),
            "光催化产氢速率"),
        DomainDescriptor("CO₂ reduction rate", "CO₂还原速率",
            ["CO₂ conversion", "CO yield"], "μmol/g/h", (1, 5000),
            "光催化 CO₂ 还原速率"),
        DomainDescriptor("quantum efficiency", "量子效率",
            ["AQE", "QE", "表观量子效率"], "%", (0.1, 50),
            "表观量子效率"),
        DomainDescriptor("photocurrent density", "光电流密度",
            ["photocurrent"], "μA/cm²", (1, 5000),
            "瞬态/稳态光电流密度"),
    ],
    relations=[
        DomainRelation("band gap", "H₂ evolution rate",
            "E_g > 1.23 eV required for water splitting; optimal 2.0-2.5 eV", "photocatalysis",
            ["oxide semiconductors"], "Thermodynamic requirement for water splitting", 0.85),
        DomainRelation("conduction band", "H₂ evolution rate",
            "CB more negative than H⁺/H₂ (-4.44 eV vs vacuum) → H₂ evolution feasible", "photocatalysis",
            ["semiconductors"], "Band alignment for H₂ evolution", 0.90),
        DomainRelation("specific surface area", "H₂ evolution rate",
            "Higher S_BET → more active sites → higher rate (linear at low S_BET)", "photocatalysis",
            ["porous materials"], "Surface area-activity correlation", 0.70),
        DomainRelation("defect density", "H₂ evolution rate",
            "Moderate Vo improves charge separation; excessive Vo acts as recombination center", "photocatalysis",
            ["TiO₂", "BiVO₄"], "Defect engineering", 0.65),
        DomainRelation("particle size", "band gap",
            "Quantum confinement: smaller particles → wider band gap", "photocatalysis",
            ["nanoparticles < 10 nm"], "Quantum size effect", 0.80),
    ],
    transferable_techniques=[
        TransferableTechnique("electrocatalysis", "photocatalysis",
            "Tafel analysis", "反应动力学机理判断",
            "电催化 Tafel 斜率分析 → 光催化瞬态光电流动力学分析"),
        TransferableTechnique("battery_materials", "photocatalysis",
            "EIS 奈奎斯特图", "电荷转移阻抗分析",
            "电池 EIS → 光催化界面电荷转移阻抗"),
    ],
    baselines=["TiO₂ (P25)", "g-C₃N₄", "CdS"],
    metrics=["H₂ evolution rate", "quantum efficiency", "photocurrent density", "stability"],
    characterization=["UV-Vis DRS", "PL", "TRPL", "XRD", "XPS", "SEM/TEM", "BET"],
    experiment_templates={
        "photocatalytic_tests": ["H₂ evolution (GC)", "CO₂ reduction (GC-MS)", "degradation (UV-Vis)"],
        "standard_conditions": {
            "light_source": "300W Xe lamp",
            "sacrificial_agent": "0.1M Na₂S/Na₂SO₃ (for H₂)",
            "catalyst_loading": "50 mg",
            "reactor_volume": "100 mL",
        },
    },
)

# ═══════════════════════════════════════════════════════════
# 3. 电池材料（原有）
# ═══════════════════════════════════════════════════════════

BATTERY_MATERIALS = DomainProfile(
    name="battery_materials",
    name_cn="电池材料",
    descriptors=[
        DomainDescriptor("specific capacity", "比容量",
            ["capacity", "Q", "放电容量"], "mAh/g", (50, 3000),
            "电池活性物质的比容量"),
        DomainDescriptor("coulombic efficiency", "库仑效率",
            ["CE", "库仑效率"], "%", (80, 100),
            "充放电库仑效率"),
        DomainDescriptor("cycling stability", "循环稳定性",
            ["capacity retention", "循环保持率"], "%", (50, 100),
            "N 圈循环后容量保持率"),
        DomainDescriptor("rate capability", "倍率性能",
            ["rate performance", "倍率"], "mAh/g", (10, 2000),
            "不同倍率下的放电容量"),
        DomainDescriptor("voltage plateau", "电压平台",
            ["plateau", "工作电压"], "V", (0.5, 4.5),
            "充放电电压平台"),
        DomainDescriptor("diffusion coefficient", "扩散系数",
            ["D_Li", "D_Na", "离子扩散"], "cm²/s", (1e-16, 1e-6),
            "固相离子扩散系数"),
        DomainDescriptor("ionic conductivity", "离子电导率",
            ["σ_ion", "离子电导"], "S/cm", (1e-8, 1e-2),
            "固态电解质离子电导率"),
        DomainDescriptor("electronic conductivity", "电子电导率",
            ["σ_e", "电子电导"], "S/cm", (1e-10, 1e6),
            "电极材料电子电导率"),
    ],
    properties=[
        DomainDescriptor("energy density", "能量密度",
            ["Wh/kg", "gravimetric energy"], "Wh/kg", (50, 500),
            "质量能量密度"),
        DomainDescriptor("power density", "功率密度",
            ["W/kg", "gravimetric power"], "W/kg", (10, 10000),
            "质量功率密度"),
    ],
    relations=[
        DomainRelation("specific capacity", "energy density",
            "E = Q × V_avg", "battery_materials",
            ["Li-ion", "Na-ion"], "Energy = Capacity × Average Voltage", 0.95),
        DomainRelation("diffusion coefficient", "rate capability",
            "Higher D_ion → better rate performance (Cottrell equation)", "battery_materials",
            ["intercalation materials"], "Diffusion-limited rate", 0.80),
        DomainRelation("ionic conductivity", "cycling stability",
            "Higher σ_ion reduces polarization → better cycling", "battery_materials",
            ["solid electrolytes"], "Overpotential-cycling correlation", 0.70),
        DomainRelation("electronic conductivity", "rate capability",
            "Higher σ_e → less polarization at high rate", "battery_materials",
            ["cathode materials"], "Electronic conductivity-rate correlation", 0.75),
        DomainRelation("specific capacity", "cycling stability",
            "Large volume change materials → fast capacity fade", "battery_materials",
            ["Si anode", "conversion cathodes"], "Volume change-degradation", 0.70),
    ],
    transferable_techniques=[
        TransferableTechnique("electrocatalysis", "battery_materials",
            "XPS 价态分析", "电极材料氧化态追踪",
            "电催化 XPS 价态分析 → 电池充放电后电极价态变化"),
        TransferableTechnique("photocatalysis", "battery_materials",
            "UV-Vis 光谱", "光学带隙估算",
            "光催化 UV-Vis → 电池材料带隙和光学性质"),
    ],
    baselines=["LiCoO₂/graphite", "LiFePO₄/graphite", "NCM811/graphite"],
    metrics=["specific capacity", "cycling stability", "rate capability", "coulombic efficiency"],
    characterization=["XRD", "XPS", "SEM/TEM", "EIS", "GITT", "CV", "Raman", "BET"],
    experiment_templates={
        "electrochemical_tests": ["GCD", "CV", "EIS", "GITT", "rate test"],
        "standard_conditions": {
            "voltage_window": "2.5-4.2 V (for NCM)",
            "current_rate": "0.1C for formation, 0.5C for cycling",
            "electrolyte": "1M LiPF₆ in EC/DMC",
        },
    },
)

# ═══════════════════════════════════════════════════════════
# 4. 合成化学
# ═══════════════════════════════════════════════════════════

SYNTHETIC_CHEMISTRY = DomainProfile(
    name="synthetic_chemistry",
    name_cn="合成化学",
    descriptors=[
        DomainDescriptor("reaction yield", "反应收率",
            ["yield", "收率", "产率", "conversion"], "%", (0, 100),
            "目标产物的收率"),
        DomainDescriptor("turnover number", "转化数",
            ["TON", "turnover number"], "", (1, 1000000),
            "催化剂转化数"),
        DomainDescriptor("turnover frequency", "转化频率",
            ["TOF", "turnover frequency"], "h⁻¹", (1, 100000),
            "单位时间单位催化位点的转化数"),
        DomainDescriptor("enantioselectivity", "对映选择性",
            ["ee", "enantio", "对映选择性", "ee值"], "%", (0, 99.9),
            "对映体过量百分比"),
        DomainDescriptor("atom economy", "原子经济性",
            ["atom economy", "原子经济", "原子利用率"], "%", (10, 100),
            "反应物原子转化为产物的比例"),
        DomainDescriptor("E-factor", "环境因子",
            ["E factor", "E-factor", "环境因子"], "kg/kg", (0.01, 100),
            "单位质量产物产生的废物质量"),
        DomainDescriptor("catalyst loading", "催化剂用量",
            ["catalyst loading", "催化剂负载量", "mol%"], "mol%", (0.001, 20),
            "催化剂相对底物的摩尔百分比"),
        DomainDescriptor("reaction temperature", "反应温度",
            ["temperature", "反应温度", "T"], "°C", (-78, 300),
            "反应进行温度"),
        DomainDescriptor("reaction time", "反应时间",
            ["time", "反应时间", "t"], "h", (0.01, 72),
            "反应所需时间"),
    ],
    properties=[
        DomainDescriptor("selectivity", "选择性",
            ["chemoselectivity", "regioselectivity", "化学选择性", "区域选择性"], "%", (50, 100),
            "目标产物占总产物的比例"),
        DomainDescriptor("scalability", "可扩展性",
            ["scale-up", "放大", "scalable"], "", (0, 1),
            "反应从实验室到工业放大的可行性"),
        DomainDescriptor("green chemistry score", "绿色化学评分",
            ["green score", "绿色评分"], "", (0, 1),
            "基于12条绿色化学原则的综合评分"),
    ],
    relations=[
        DomainRelation("catalyst loading", "reaction yield",
            "Higher loading → higher yield (diminishing returns above threshold)", "synthetic_chemistry",
            ["homogeneous catalysis"], "Catalyst loading-yield relationship", 0.75),
        DomainRelation("atom economy", "E-factor",
            "Higher atom economy → lower E-factor (less waste)", "synthetic_chemistry",
            [], "Green chemistry metrics correlation", 0.85),
        DomainRelation("reaction temperature", "enantioselectivity",
            "Lower T → higher ee for most asymmetric reactions (ΔΔG‡ = -RT ln[(1+ee)/(1-ee)])", "synthetic_chemistry",
            ["asymmetric catalysis"], "Temperature-selectivity relationship", 0.80),
        DomainRelation("catalyst loading", "turnover number",
            "TON = product/catalyst; lower loading → higher TON at same yield", "synthetic_chemistry",
            [], "Catalyst efficiency metric", 0.90),
    ],
    transferable_techniques=[
        TransferableTechnique("electrocatalysis", "synthetic_chemistry",
            "电催化偶联", "温和条件C-C键构建",
            "电催化氧化还原 → 有机电合成中的绿色电子转移试剂"),
        TransferableTechnique("nano_chemistry", "synthetic_chemistry",
            "纳米催化剂", "高活性高选择性催化",
            "纳米团簇催化剂 → 合成化学中的高TON/TOF催化剂"),
    ],
    baselines=["Pd/C (Suzuki)", "Rh/Al₂O₃ (hydrogenation)", "TiCl₄/DIAD (Mitsunobu)"],
    metrics=["reaction yield", "enantioselectivity (ee)", "TON/TOF", "atom economy", "E-factor"],
    characterization=["NMR", "HPLC/GC", "MS", "IR", "X-ray crystallography", "chiral HPLC"],
    experiment_templates={
        "synthetic_tests": ["small-scale optimization", "gram-scale synthesis", "substrate scope"],
        "standard_conditions": {
            "solvent": "THF or DCM",
            "concentration": "0.1-0.5 M",
            "atmosphere": "N₂ or Ar",
            "monitoring": "TLC / HPLC",
        },
    },
)

# ═══════════════════════════════════════════════════════════
# 5. 催化与表界面化学（广义，含均相/多相/酶催化）
# ═══════════════════════════════════════════════════════════

SURFACE_INTERFACE_CHEMISTRY = DomainProfile(
    name="surface_interface_chemistry",
    name_cn="催化与表界面化学",
    descriptors=[
        DomainDescriptor("surface energy", "表面能",
            ["γ", "surface energy", "表面自由能"], "J/m²", (0.01, 5.0),
            "固体表面的单位面积自由能"),
        DomainDescriptor("contact angle", "接触角",
            ["θ", "contact angle", "润湿角"], "°", (0, 180),
            "液滴在表面的接触角，表征润湿性"),
        DomainDescriptor("zeta potential", "Zeta电位",
            ["ζ", "zeta", "表面电荷"], "mV", (-100, 100),
            "颗粒表面电动电位"),
        DomainDescriptor("BET surface area", "BET比表面积",
            ["S_BET", "比表面积", "SSA"], "m²/g", (1, 3000),
            "Brunauer-Emmett-Teller 法测定的比表面积"),
        DomainDescriptor("pore size", "孔径",
            ["pore diameter", "孔径分布", "D_pore"], "nm", (0.3, 100),
            "多孔材料的平均孔径"),
        DomainDescriptor("acid site density", "酸位点密度",
            ["acid sites", "Brønsted酸", "Lewis酸"], "μmol/g", (10, 5000),
            "固体酸催化剂的活性位点密度"),
        DomainDescriptor("surface roughness", "表面粗糙度",
            ["Ra", "Rq", "粗糙度"], "nm", (0.1, 1000),
            "固体表面微观粗糙度"),
    ],
    properties=[
        DomainDescriptor("catalytic activity", "催化活性",
            ["activity", "转化率", "催化活性"], "% or mol/g/h", (0, 100),
            "催化剂的转化能力"),
        DomainDescriptor("selectivity", "选择性",
            ["selectivity", "选择性"], "%", (0, 100),
            "目标产物选择性"),
        DomainDescriptor("wettability", "润湿性",
            ["wettability", "亲水性", "疏水性"], "", (0, 1),
            "表面的亲/疏水特性"),
        DomainDescriptor("interfacial tension", "界面张力",
            ["interfacial tension", "界面张力"], "mN/m", (0.1, 72),
            "两相界面的张力"),
    ],
    relations=[
        DomainRelation("surface energy", "contact angle",
            "cos(θ) = (γ_S - γ_SL)/γ_L (Young's equation)", "surface_interface_chemistry",
            [], "Young's equation", 0.90),
        DomainRelation("zeta potential", "catalytic activity",
            "|ζ| > 30 mV → good dispersion → more accessible active sites", "surface_interface_chemistry",
            ["colloidal catalysts"], "Dispersion-activity correlation", 0.65),
        DomainRelation("BET surface area", "catalytic activity",
            "Higher S_BET → more accessible sites → higher activity (volcano for ultrahigh S_BET)", "surface_interface_chemistry",
            ["porous catalysts"], "Surface area-activity", 0.70),
        DomainRelation("acid site density", "selectivity",
            "Moderate acid strength → high selectivity; too strong → side reactions", "surface_interface_chemistry",
            ["zeolites", "solid acids"], "Acidity-selectivity relationship", 0.75),
    ],
    transferable_techniques=[
        TransferableTechnique("electrocatalysis", "surface_interface_chemistry",
            "原位XAS", "工况活性位点结构",
            "电催化原位XAS → 多相催化工况表征"),
        TransferableTechnique("nano_chemistry", "surface_interface_chemistry",
            "STEM-EELS mapping", "纳米尺度元素分布",
            "纳米材料STEM-EELS → 催化剂活性位点空间分布"),
    ],
    baselines=["Zeolite H-ZSM-5 (acid catalysis)", "Pd/Al₂O₃ (hydrogenation)", "TiO₂ (photocatalytic surface)"],
    metrics=["catalytic activity (conversion)", "selectivity", "TOF", "stability (TOS)", "acid site density"],
    characterization=["XRD", "XPS", "BET", "NH₃-TPD", "CO chemisorption", "TEM", "AFM", "in-situ FTIR"],
    experiment_templates={
        "catalytic_tests": ["batch reactor", "flow reactor", "TPD/TPR"],
        "standard_conditions": {
            "temperature": "100-400 °C",
            "pressure": "1-50 bar",
            "WHSV": "0.5-5 h⁻¹",
        },
    },
)

# ═══════════════════════════════════════════════════════════
# 6. 物理化学（含化学动力学/胶体/电化学）
# ═══════════════════════════════════════════════════════════

PHYSICAL_CHEMISTRY = DomainProfile(
    name="physical_chemistry",
    name_cn="物理化学",
    descriptors=[
        DomainDescriptor("activation energy", "活化能",
            ["Ea", "E_a", "活化能", "activation energy"], "kJ/mol", (1, 400),
            "反应的活化能垒"),
        DomainDescriptor("enthalpy change", "焓变",
            ["ΔH", "enthalpy", "反应焓"], "kJ/mol", (-500, 500),
            "反应的焓变"),
        DomainDescriptor("entropy change", "熵变",
            ["ΔS", "entropy", "反应熵"], "J/mol/K", (-500, 500),
            "反应的熵变"),
        DomainDescriptor("Gibbs free energy", "吉布斯自由能",
            ["ΔG", "Gibbs", "自由能变"], "kJ/mol", (-500, 500),
            "反应的吉布斯自由能变化"),
        DomainDescriptor("rate constant", "速率常数",
            ["k", "rate constant", "反应速率常数"], "varies", (1e-20, 1e10),
            "反应速率常数"),
        DomainDescriptor("reaction order", "反应级数",
            ["order", "n", "反应级数"], "", (0, 4),
            "对某反应物的反应级数"),
        DomainDescriptor("heat capacity", "热容",
            ["Cp", "Cv", "热容"], "J/mol/K", (1, 500),
            "恒压/恒容热容"),
        DomainDescriptor("diffusion coefficient liquid", "液相扩散系数",
            ["D", "扩散系数", "Fick"], "cm²/s", (1e-10, 1e-4),
            "溶液中的分子扩散系数"),
    ],
    properties=[
        DomainDescriptor("reaction rate", "反应速率",
            ["rate", "速率", "r"], "mol/L/s", (1e-10, 1e3),
            "化学反应速率"),
        DomainDescriptor("equilibrium constant", "平衡常数",
            ["K_eq", "K", "平衡常数"], "", (1e-20, 1e20),
            "化学平衡常数"),
        DomainDescriptor("phase transition temperature", "相变温度",
            ["T_m", "T_c", "相变温度", "熔点"], "K", (100, 4000),
            "相变（熔融/沸腾/玻璃化等）温度"),
    ],
    relations=[
        DomainRelation("activation energy", "rate constant",
            "k = A·exp(-Ea/RT) (Arrhenius equation)", "physical_chemistry",
            [], "Arrhenius equation", 0.95),
        DomainRelation("enthalpy change", "Gibbs free energy",
            "ΔG = ΔH - TΔS (Gibbs-Helmholtz)", "physical_chemistry",
            [], "Gibbs-Helmholtz equation", 0.95),
        DomainRelation("Gibbs free energy", "equilibrium constant",
            "ΔG° = -RT ln(K_eq)", "physical_chemistry",
            [], "van't Hoff / Gibbs relation", 0.90),
        DomainRelation("activation energy", "reaction rate",
            "Higher Ea → lower rate at given T (Arrhenius)", "physical_chemistry",
            [], "Activation barrier-rate correlation", 0.90),
    ],
    transferable_techniques=[
        TransferableTechnique("quantum_chemistry", "physical_chemistry",
            "势能面计算", "反应路径与过渡态",
            "量子化学PES扫描 → 物理化学动力学参数预测"),
        TransferableTechnique("electrochemical", "physical_chemistry",
            "Butler-Volmer方程", "电化学动力学",
            "电化学BV方程 → 化学动力学中电子转移理论"),
    ],
    baselines=["Arrhenius plot baseline", "NIST thermochemical tables"],
    metrics=["activation energy", "rate constant", "equilibrium constant", "ΔG", "ΔH"],
    characterization=["DSC", "TGA", "calorimetry", "kinetic studies", "viscometry", "conductivity"],
    experiment_templates={
        "kinetic_experiments": ["variable-temperature kinetics", "isotope effects", "Eyring plot"],
        "standard_conditions": {
            "temperature_range": "25-200 °C",
            "pressure": "1 atm (unless gas-phase)",
        },
    },
)

# ═══════════════════════════════════════════════════════════
# 7. 量子化学
# ═══════════════════════════════════════════════════════════

QUANTUM_CHEMISTRY = DomainProfile(
    name="quantum_chemistry",
    name_cn="量子化学",
    descriptors=[
        DomainDescriptor("HOMO energy", "HOMO能量",
            ["E_HOMO", "HOMO", "最高占据轨道"], "eV", (-12, -2),
            "最高占据分子轨道能量"),
        DomainDescriptor("LUMO energy", "LUMO能量",
            ["E_LUMO", "LUMO", "最低未占轨道"], "eV", (-5, 2),
            "最低未占分子轨道能量"),
        DomainDescriptor("HOMO-LUMO gap", "HOMO-LUMO能隙",
            ["gap", "能隙", "HOMO-LUMO gap", "E_gap"], "eV", (0.5, 10),
            "前线轨道能隙"),
        DomainDescriptor("ionization potential", "电离能",
            ["IP", "IE", "电离势", "vertical IP"], "eV", (3, 25),
            "垂直/绝热电离能"),
        DomainDescriptor("electron affinity", "电子亲和能",
            ["EA", "电子亲和势"], "eV", (-1, 5),
            "垂直/绝热电子亲和能"),
        DomainDescriptor("Mulliken charge", "Mulliken电荷",
            ["q_Mulliken", "Mulliken", "原子电荷"], "e", (-1.5, 1.5),
            "Mulliken 布居分析的原子电荷"),
        DomainDescriptor("bond order", "键级",
            ["Wiberg", "Mayer", "键级"], "", (0, 3),
            "化学键的键级"),
        DomainDescriptor("dipole moment", "偶极矩",
            ["μ", "dipole", "偶极矩"], "Debye", (0, 15),
            "分子偶极矩"),
        DomainDescriptor("polarizability", "极化率",
            ["α", "polarizability", "极化率"], "a.u.", (1, 500),
            "分子极化率"),
        DomainDescriptor("reaction barrier", "反应势垒",
            ["ΔG‡", "TS energy", "过渡态能量", "barrier"], "kJ/mol", (1, 300),
            "反应过渡态相对能量"),
    ],
    properties=[
        DomainDescriptor("molecular stability", "分子稳定性",
            ["stability", "稳定性"], "", (0, 1),
            "分子的热力学/动力学稳定性"),
        DomainDescriptor("chemical reactivity", "化学反应性",
            ["reactivity", "反应性", "反应活性"], "", (0, 1),
            "分子的化学反应活性指数"),
        DomainDescriptor("spectroscopic property", "光谱性质",
            ["UV-Vis", "IR frequency", "NMR shift", "光谱"], "varies", (0, 1),
            "分子的光谱预测值"),
    ],
    relations=[
        DomainRelation("HOMO-LUMO gap", "molecular stability",
            "Larger gap → kinetically more stable (harder to excite)", "quantum_chemistry",
            [], "Frontier orbital theory", 0.80),
        DomainRelation("HOMO energy", "chemical reactivity",
            "Higher E_HOMO → better electron donor → more nucleophilic", "quantum_chemistry",
            [], "Koopmans' theorem", 0.85),
        DomainRelation("ionization potential", "HOMO energy",
            "IP ≈ -E_HOMO (Koopmans' theorem)", "quantum_chemistry",
            [], "Koopmans' approximation", 0.75),
        DomainRelation("reaction barrier", "chemical reactivity",
            "Lower ΔG‡ → higher reaction rate (kinetic control)", "quantum_chemistry",
            [], "Transition state theory", 0.90),
        DomainRelation("bond order", "molecular stability",
            "Higher bond order → shorter, stronger bond", "quantum_chemistry",
            [], "Bond order-length correlation", 0.70),
    ],
    transferable_techniques=[
        TransferableTechnique("electrocatalysis", "quantum_chemistry",
            "DFT + d-band theory", "催化活性预测",
            "电催化d-band → 量子化学DFT计算验证电子结构"),
        TransferableTechnique("organic_chemistry", "quantum_chemistry",
            "Fukui函数", "反应位点预测",
            "有机化学亲电/亲核反应 → 量子化学Fukui函数定位活性位点"),
    ],
    baselines=["B3LYP/6-31G*", "PBE/DZP", "CCSD(T)/CBS"],
    metrics=["energy convergence", "HOMO-LUMO gap", "reaction barrier", "spectroscopic match", "binding energy accuracy"],
    characterization=["computational only", "UV-Vis comparison", "IR frequency comparison", "NMR shift comparison"],
    experiment_templates={
        "computational_protocols": ["geometry optimization", "frequency analysis", "TS search (NEB/QST2)", "solvent model (PCM)"],
        "standard_conditions": {
            "functional": "B3LYP or PBE0",
            "basis_set": "6-311+G** or def2-TZVP",
            "solvent": "PCM (water/THF)",
            "convergence": "10⁻⁶ Hartree",
        },
    },
)

# ═══════════════════════════════════════════════════════════
# 8. 分析化学
# ═══════════════════════════════════════════════════════════

ANALYTICAL_CHEMISTRY = DomainProfile(
    name="analytical_chemistry",
    name_cn="分析化学",
    descriptors=[
        DomainDescriptor("detection limit", "检出限",
            ["LOD", "检出限", "detection limit"], "varies", (1e-15, 1e-3),
            "方法的最低检出限"),
        DomainDescriptor("quantification limit", "定量限",
            ["LOQ", "定量限", "quantification limit"], "varies", (1e-14, 1e-2),
            "方法的最低定量限"),
        DomainDescriptor("sensitivity", "灵敏度",
            ["sensitivity", "灵敏度", "slope"], "varies", (0.1, 10000),
            "校准曲线斜率（灵敏度）"),
        DomainDescriptor("precision", "精密度",
            ["RSD", "repeatability", "精密度", "reproducibility"], "%", (0.01, 20),
            "重复性/再现性（RSD）"),
        DomainDescriptor("accuracy", "准确度",
            ["recovery", "准确度", "trueness"], "%", (80, 120),
            "加标回收率或与参考值的偏差"),
        DomainDescriptor("linear range", "线性范围",
            ["linearity", "线性范围", "R²"], "varies", (0, 1),
            "校准曲线的线性范围或相关系数"),
        DomainDescriptor("resolution", "分辨率",
            ["resolution", "Rs", "分辨率"], "", (0.5, 10),
            "色谱/光谱的分离分辨率"),
        DomainDescriptor("signal to noise ratio", "信噪比",
            ["S/N", "SNR", "信噪比"], "", (1, 10000),
            "信号与噪声的比值"),
    ],
    properties=[
        DomainDescriptor("method reliability", "方法可靠性",
            ["reliability", "可靠性"], "", (0, 1),
            "分析方法的综合可靠性"),
        DomainDescriptor("sample throughput", "样品通量",
            ["throughput", "通量", "samples/h"], "samples/h", (1, 10000),
            "单位时间处理样品数"),
        DomainDescriptor("real-time capability", "实时性",
            ["real-time", "实时", "response time"], "s", (0.001, 3600),
            "从采样到结果的时间"),
    ],
    relations=[
        DomainRelation("signal to noise ratio", "detection limit",
            "LOD = 3σ/S (3σ criterion), S = sensitivity slope", "analytical_chemistry",
            [], "LOD-S/N relationship", 0.90),
        DomainRelation("resolution", "method reliability",
            "Rs > 1.5 → baseline separation → reliable quantification", "analytical_chemistry",
            ["chromatography"], "Resolution-reliability", 0.80),
        DomainRelation("precision", "accuracy",
            "High precision (low RSD) is prerequisite for high accuracy", "analytical_chemistry",
            [], "Precision-accuracy hierarchy", 0.75),
    ],
    transferable_techniques=[
        TransferableTechnique("environmental_chemistry", "analytical_chemistry",
            "LC-MS/MS多残留分析", "痕量污染物检测",
            "环境化学污染物分析 → 分析化学方法开发"),
        TransferableTechnique("chemical_biology", "analytical_chemistry",
            "荧光探针设计", "活体实时监测",
            "化学生物学荧光探针 → 分析化学活体原位检测"),
    ],
    baselines=["HPLC-UV (pharmaceutical)", "ICP-MS (trace metals)", "GC-MS (VOCs)"],
    metrics=["LOD", "LOQ", "precision (RSD)", "accuracy (recovery)", "linear range (R²)"],
    characterization=["HPLC", "GC-MS", "LC-MS/MS", "ICP-MS", "NMR", "UV-Vis", "fluorescence", "electrochemical sensor"],
    experiment_templates={
        "method_validation": ["linearity", "LOD/LOQ", "precision", "accuracy", "robustness"],
        "standard_conditions": {
            "calibration_points": "≥ 5",
            "replicates": "≥ 3",
            "spike_levels": "80%/100%/120%",
        },
    },
)

# ═══════════════════════════════════════════════════════════
# 9. 有机化学
# ═══════════════════════════════════════════════════════════

ORGANIC_CHEMISTRY = DomainProfile(
    name="organic_chemistry",
    name_cn="有机化学",
    descriptors=[
        DomainDescriptor("logP", "辛醇-水分配系数",
            ["cLogP", "logP", "partition coefficient", "脂水分配"], "", (-2, 8),
            "辛醇/水分配系数的对数"),
        DomainDescriptor("pKa", "酸解离常数",
            ["pKa", "酸度常数", "解离常数"], "", (-5, 15),
            "酸解离常数的负对数"),
        DomainDescriptor("polar surface area", "极性表面积",
            ["PSA", "TPSA", "极性表面积"], "Å²", (0, 250),
            "拓扑极性表面积"),
        DomainDescriptor("rotatable bonds", "可旋转键数",
            ["RotB", "rotatable bonds", "可旋转键"], "", (0, 20),
            "分子中可旋转键的数量"),
        DomainDescriptor("H-bond donors acceptors", "氢键供/受体数",
            ["HBD", "HBA", "氢键供体", "氢键受体"], "", (0, 15),
            "氢键供体/受体数量"),
        DomainDescriptor("melting point", "熔点",
            ["Tm", "mp", "熔点"], "°C", (-200, 500),
            "有机化合物熔点"),
        DomainDescriptor("molecular weight", "分子量",
            ["MW", "Mw", "分子量"], "Da", (50, 2000),
            "分子量"),
    ],
    properties=[
        DomainDescriptor("solubility", "溶解度",
            ["S", "solubility", "溶解度", "LogS"], "mg/mL", (1e-6, 500),
            "在水/有机溶剂中的溶解度"),
        DomainDescriptor("permeability", "渗透性",
            ["Papp", "permeability", "渗透性"], "cm/s", (1e-8, 1e-3),
            "膜渗透性"),
        DomainDescriptor("metabolic stability", "代谢稳定性",
            ["t₁/₂ metabolic", "代谢半衰期", "CLint"], "min", (1, 600),
            "体外代谢半衰期"),
        DomainDescriptor("bioactivity", "生物活性",
            ["IC50", "EC50", "Ki", "生物活性"], "nM", (0.01, 100000),
            "对生物靶点的抑制/激活浓度"),
    ],
    relations=[
        DomainRelation("logP", "permeability",
            "Optimal logP 1-3 for good membrane permeability (Lipinski)", "organic_chemistry",
            ["drug-like molecules"], "Lipinski's Rule of Five", 0.75),
        DomainRelation("polar surface area", "permeability",
            "PSA < 140 Å² for good oral bioavailability (Veber rule)", "organic_chemistry",
            ["oral drugs"], "Veber's rule", 0.70),
        DomainRelation("molecular weight", "solubility",
            "Higher MW → typically lower solubility", "organic_chemistry",
            [], "MW-solubility trend", 0.60),
        DomainRelation("logP", "metabolic stability",
            "Very high logP → CYP metabolism → lower stability", "organic_chemistry",
            [], "Lipophilicity-metabolism correlation", 0.65),
    ],
    transferable_techniques=[
        TransferableTechnique("synthetic_chemistry", "organic_chemistry",
            "光氧化还原催化", "温和条件下自由基反应",
            "合成化学光催化方法 → 有机化学C-C键构建新策略"),
        TransferableTechnique("quantum_chemistry", "organic_chemistry",
            "NMR化学位移计算", "结构确证辅助",
            "量子化学NMR预测 → 有机化学结构鉴定验证"),
    ],
    baselines=["Lipinski Rule of Five", "Veber rules", "PAINS filter"],
    metrics=["yield", "selectivity", "logP", "IC50/EC50", "stability"],
    characterization=["NMR (¹H/¹³C)", "MS", "IR", "UV-Vis", "X-ray", "HPLC", "chiral HPLC", "HRMS"],
    experiment_templates={
        "organic_synthesis": ["reaction optimization", "substrate scope", "scale-up"],
        "standard_conditions": {
            "solvent": "THF/DCM/MeCN",
            "temperature": "rt to reflux",
            "atmosphere": "N₂/Ar",
        },
    },
)

# ═══════════════════════════════════════════════════════════
# 10. 无机化学
# ═══════════════════════════════════════════════════════════

INORGANIC_CHEMISTRY = DomainProfile(
    name="inorganic_chemistry",
    name_cn="无机化学",
    descriptors=[
        DomainDescriptor("ionic radius", "离子半径",
            ["r_ion", "离子半径", "Shannon radius"], "Å", (0.3, 2.5),
            "离子的有效半径（Shannon）"),
        DomainDescriptor("electronegativity", "电负性",
            ["χ", "Pauling EN", "电负性"], "", (0.7, 4.0),
            "Pauling 电负性"),
        DomainDescriptor("oxidation state", "氧化态",
            ["OS", "oxidation state", "价态", "氧化数"], "", (-4, 8),
            "金属的氧化态"),
        DomainDescriptor("crystal field splitting", "晶体场分裂能",
            ["Δ_oct", "10Dq", "晶体场分裂", "CFSE"], "cm⁻¹", (5000, 40000),
            "八面体晶体场分裂能"),
        DomainDescriptor("lattice energy", "晶格能",
            ["U_lattice", "晶格能"], "kJ/mol", (500, 15000),
            "离子晶体的晶格能"),
        DomainDescriptor("magnetic moment", "磁矩",
            ["μ_eff", "magnetic moment", "有效磁矩"], "μB", (0, 10),
            "有效磁矩"),
        DomainDescriptor("BET surface area", "比表面积",
            ["S_BET", "SSA", "比表面积"], "m²/g", (10, 5000),
            "MOF/COF 等多孔材料的比表面积"),
        DomainDescriptor("pore volume", "孔体积",
            ["V_pore", "孔容", "孔体积"], "cm³/g", (0.1, 3.0),
            "多孔材料的总孔体积"),
    ],
    properties=[
        DomainDescriptor("conductivity", "电导率",
            ["σ", "conductivity", "电导率"], "S/cm", (1e-10, 1e3),
            "材料的电导率"),
        DomainDescriptor("luminescence quantum yield", "发光量子产率",
            ["Φ_PL", "PLQY", "量子产率", "发光效率"], "%", (0, 100),
            "光致发光量子产率"),
        DomainDescriptor("gas adsorption capacity", "气体吸附容量",
            ["uptake", "adsorption", "气体吸附"], "mmol/g or cm³/g", (0.1, 50),
            "MOF/COF 的气体吸附容量"),
        DomainDescriptor("catalytic activity", "催化活性",
            ["TOF", "TON", "催化活性"], "h⁻¹", (1, 100000),
            "无机催化剂的催化活性"),
    ],
    relations=[
        DomainRelation("ionic radius", "lattice energy",
            "Smaller r_ion → larger lattice energy (Born-Landé equation)", "inorganic_chemistry",
            ["ionic crystals"], "Born-Landé equation", 0.85),
        DomainRelation("crystal field splitting", "luminescence quantum yield",
            "Δ_oct determines d-d transition energy → emission wavelength and efficiency", "inorganic_chemistry",
            ["transition metal complexes"], "Crystal field theory for luminescence", 0.70),
        DomainRelation("BET surface area", "gas adsorption capacity",
            "Higher S_BET → higher gas uptake (especially at low P)", "inorganic_chemistry",
            ["MOFs", "COFs", "zeolites"], "Surface area-adsorption correlation", 0.80),
        DomainRelation("electronegativity", "bond dissociation energy",
            "Greater Δχ → more ionic → higher lattice energy", "inorganic_chemistry",
            [], "Electronegativity-bond polarity", 0.75),
    ],
    transferable_techniques=[
        TransferableTechnique("surface_interface_chemistry", "inorganic_chemistry",
            "MOF限域催化", "孔道择形催化",
            "多相催化择形原理 → MOF孔道择形催化"),
        TransferableTechnique("electrocatalysis", "inorganic_chemistry",
            "单原子催化剂设计", "配位环境调控",
            "电催化SAC配位调控 → 无机配位化学活性位点设计"),
    ],
    baselines=["Pt/O₂ (catalysis)", "ZIF-8 (MOF)", "UiO-66 (MOF)", "Ir(ppy)₃ (luminescence)"],
    metrics=["BET surface area", "gas uptake", "PLQY", "TOF/TON", "stability"],
    characterization=["PXRD", "single-crystal XRD", "XPS", "BET", "TGA", "ICP-OES", "UV-Vis", "PL", "SQUID"],
    experiment_templates={
        "inorganic_synthesis": ["solvothermal", "solid-state", "hydrothermal"],
        "standard_conditions": {
            "temperature": "RT - 200 °C (solvothermal)",
            "time": "12-72 h",
            "solvent": "DMF / DEF / water",
        },
    },
)

# ═══════════════════════════════════════════════════════════
# 11. 高分子化学与物理
# ═══════════════════════════════════════════════════════════

POLYMER_CHEMISTRY = DomainProfile(
    name="polymer_chemistry",
    name_cn="高分子化学与物理",
    descriptors=[
        DomainDescriptor("molecular weight Mn", "数均分子量",
            ["Mn", "M_n", "数均分子量"], "kDa", (1, 10000),
            "数均分子量"),
        DomainDescriptor("molecular weight Mw", "重均分子量",
            ["Mw", "M_w", "重均分子量"], "kDa", (1, 50000),
            "重均分子量"),
        DomainDescriptor("PDI", "多分散指数",
            ["PDI", "Đ", "多分散性"], "", (1.0, 3.0),
            "Mw/Mn，分子量分布宽度"),
        DomainDescriptor("glass transition temperature", "玻璃化转变温度",
            ["Tg", "glass transition", "玻璃化温度"], "°C", (-150, 350),
            "玻璃化转变温度"),
        DomainDescriptor("melting temperature polymer", "熔融温度",
            ["Tm", "melting point", "熔点"], "°C", (-50, 400),
            "结晶性高分子的熔融温度"),
        DomainDescriptor("crystallinity", "结晶度",
            ["Xc", "crystallinity", "结晶度"], "%", (0, 100),
            "高分子的结晶度"),
        DomainDescriptor("crosslink density", "交联密度",
            ["ν_e", "crosslink", "交联密度"], "mol/cm³", (1e-5, 1e-1),
            "交联网络的密度"),
        DomainDescriptor("tensile strength", "拉伸强度",
            ["σ_b", "tensile", "拉伸强度"], "MPa", (0.1, 5000),
            "高分子材料的拉伸强度"),
        DomainDescriptor("elongation at break", "断裂伸长率",
            ["ε_b", "elongation", "断裂伸长率"], "%", (0.1, 2000),
            "断裂时的伸长率"),
    ],
    properties=[
        DomainDescriptor("mechanical modulus", "力学模量",
            ["E", "modulus", "弹性模量", "杨氏模量"], "GPa", (0.001, 300),
            "杨氏模量/储能模量"),
        DomainDescriptor("thermal stability", "热稳定性",
            ["Td", "degradation temperature", "热分解温度"], "°C", (100, 600),
            "热分解温度（TGA 5%失重温度）"),
        DomainDescriptor("gas permeability", "气体渗透性",
            ["P", "permeability", "渗透系数"], "Barrer", (0.01, 10000),
            "气体在聚合物膜中的渗透系数"),
    ],
    relations=[
        DomainRelation("molecular weight Mn", "glass transition temperature",
            "Tg = Tg∞ - K/Mn (Fox-Flory equation)", "polymer_chemistry",
            ["amorphous polymers"], "Fox-Flory equation", 0.80),
        DomainRelation("crystallinity", "tensile strength",
            "Higher Xc → higher modulus and strength (up to embrittlement point)", "polymer_chemistry",
            ["semi-crystalline polymers"], "Crystallinity-strength correlation", 0.75),
        DomainRelation("crosslink density", "mechanical modulus",
            "E = 3ν_eRT (rubber elasticity theory)", "polymer_chemistry",
            ["elastomers", "thermosets"], "Rubber elasticity theory", 0.85),
        DomainRelation("PDI", "mechanical modulus",
            "Broader PDI → more heterogeneous chain length → affects processing", "polymer_chemistry",
            [], "PDI-processing correlation", 0.55),
    ],
    transferable_techniques=[
        TransferableTechnique("nano_chemistry", "polymer_chemistry",
            "纳米复合", "高分子纳米复合材料",
            "纳米颗粒分散 → 高分子/纳米复合增强"),
        TransferableTechnique("green_chemical_engineering", "polymer_chemistry",
            "微反应器聚合", "精准链结构控制",
            "过程强化微反应器 → 高分子可控/活性聚合"),
    ],
    baselines=["PE (HDPE/LDPE)", "PP", "PET", "PMMA", "PDMS"],
    metrics=["Tg", "tensile strength", "elongation at break", "PDI", "thermal stability (Td)"],
    characterization=["GPC/SEC", "DSC", "TGA", "DMA", "tensile testing", "XRD (WAXS/SAXS)", "NMR", "FTIR"],
    experiment_templates={
        "polymer_tests": ["tensile test", "DMA", "DSC", "GPC", "permeability test"],
        "standard_conditions": {
            "strain_rate": "10 mm/min (tensile)",
            "DSC_rate": "10 °C/min",
            "GPC_solvent": "THF",
        },
    },
)

# ═══════════════════════════════════════════════════════════
# 12. 化学生物学
# ═══════════════════════════════════════════════════════════

CHEMICAL_BIOLOGY = DomainProfile(
    name="chemical_biology",
    name_cn="化学生物学",
    descriptors=[
        DomainDescriptor("binding affinity", "结合亲和力",
            ["Kd", "KD", "结合常数", "亲和力"], "nM", (0.01, 100000),
            "配体-靶点结合解离常数"),
        DomainDescriptor("IC50", "半抑制浓度",
            ["IC50", "IC₅₀", "半数抑制浓度"], "nM", (0.1, 100000),
            "50% 抑制浓度"),
        DomainDescriptor("EC50", "半有效浓度",
            ["EC50", "EC₅₀", "半数有效浓度"], "nM", (0.1, 100000),
            "50% 有效浓度"),
        DomainDescriptor("selectivity index", "选择性指数",
            ["SI", "selectivity index", "选择性指数"], "", (1, 10000),
            "靶点 vs 非靶点的活性比值"),
        DomainDescriptor("cell permeability", "细胞渗透性",
            ["Caco-2", "Papp", "细胞渗透性"], "cm/s", (1e-8, 1e-3),
            "Caco-2 细胞渗透性"),
        DomainDescriptor("metabolic half-life", "代谢半衰期",
            ["t₁/₂", "metabolic half-life", "代谢半衰期"], "min", (1, 600),
            "体外肝微粒体代谢半衰期"),
        DomainDescriptor("oral bioavailability", "口服生物利用度",
            ["F%", "bioavailability", "生物利用度"], "%", (0, 100),
            "口服生物利用度"),
    ],
    properties=[
        DomainDescriptor("potency", "效价强度",
            ["potency", "效价"], "nM", (0.01, 100000),
            "药物的效价（IC50/EC50越低越强）"),
        DomainDescriptor("efficacy", "效能",
            ["efficacy", "Emax", "效能"], "%", (0, 100),
            "最大效应百分比"),
        DomainDescriptor("toxicity", "毒性",
            ["LD50", "CC50", "毒性", "细胞毒性"], "μM", (0.01, 10000),
            "化合物对细胞的毒性"),
        DomainDescriptor("target engagement", "靶点占有率",
            ["TE", "target engagement", "靶点结合率"], "%", (0, 100),
            "体内靶点结合百分比"),
    ],
    relations=[
        DomainRelation("binding affinity", "potency",
            "Lower Kd → higher potency (generally correlates with IC50)", "chemical_biology",
            [], "Affinity-potency correlation", 0.80),
        DomainRelation("selectivity index", "toxicity",
            "Higher SI → wider therapeutic window → lower toxicity risk", "chemical_biology",
            [], "Selectivity-toxicity window", 0.75),
        DomainRelation("cell permeability", "oral bioavailability",
            "Higher Caco-2 Papp → generally better oral F%", "chemical_biology",
            ["oral drugs"], "Permeability-bioavailability", 0.70),
        DomainRelation("metabolic half-life", "oral bioavailability",
            "Longer t₁/₂ → higher exposure → better F%", "chemical_biology",
            [], "Metabolic stability-bioavailability", 0.65),
    ],
    transferable_techniques=[
        TransferableTechnique("organic_chemistry", "chemical_biology",
            "PROTAC设计", "靶向蛋白降解",
            "有机化学 linker 设计 → PROTAC 双功能分子"),
        TransferableTechnique("analytical_chemistry", "chemical_biology",
            "活细胞荧光成像", "亚细胞定位",
            "分析化学荧光探针 → 化学生物学活细胞成像"),
    ],
    baselines=["Staurosporine (kinase)", "Imatinib (BCR-ABL)", "Rapamycin (mTOR)"],
    metrics=["IC50/EC50", "selectivity index", "cell permeability", "metabolic stability", "oral bioavailability"],
    characterization=["SPR/BLI", "ITC", "fluorescence microscopy", "flow cytometry", "Western blot", "CETSA"],
    experiment_templates={
        "bioassays": ["enzyme assay", "cell viability", "binding assay", "ADME panel"],
        "standard_conditions": {
            "cell_line": "HEK293 or HeLa",
            "assay_type": "fluorescence/luminescence",
            "incubation": "24-72 h",
            "replicates": "≥ 3",
        },
    },
)

# ═══════════════════════════════════════════════════════════
# 13. 材料化学
# ═══════════════════════════════════════════════════════════

MATERIALS_CHEMISTRY = DomainProfile(
    name="materials_chemistry",
    name_cn="材料化学",
    descriptors=[
        DomainDescriptor("band gap material", "带隙",
            ["E_g", "bandgap", "带隙", "band gap"], "eV", (0, 8),
            "半导体/绝缘体的带隙"),
        DomainDescriptor("carrier concentration", "载流子浓度",
            ["n_c", "carrier density", "载流子浓度"], "cm⁻³", (1e10, 1e22),
            "半导体载流子浓度"),
        DomainDescriptor("carrier mobility", "载流子迁移率",
            ["μ", "mobility", "迁移率"], "cm²/V/s", (0.1, 100000),
            "载流子迁移率"),
        DomainDescriptor("dielectric constant", "介电常数",
            ["ε_r", "dielectric", "介电常数"], "", (1, 10000),
            "相对介电常数"),
        DomainDescriptor("thermal conductivity", "热导率",
            ["κ", "thermal conductivity", "热导率"], "W/m/K", (0.01, 2000),
            "材料的热导率"),
        DomainDescriptor("Young's modulus", "杨氏模量",
            ["E", "Young's modulus", "杨氏模量", "弹性模量"], "GPa", (0.001, 1000),
            "杨氏模量"),
        DomainDescriptor("hardness", "硬度",
            ["Hv", "hardness", "维氏硬度"], "GPa", (0.1, 100),
            "维氏硬度"),
        DomainDescriptor("refractive index", "折射率",
            ["n", "refractive index", "折射率"], "", (1, 4),
            "光学折射率"),
        DomainDescriptor("magnetization", "磁化强度",
            ["M_s", "saturation magnetization", "饱和磁化强度"], "emu/g", (0, 300),
            "饱和磁化强度"),
    ],
    properties=[
        DomainDescriptor("electrical conductivity", "电导率",
            ["σ", "conductivity", "电导率"], "S/cm", (1e-15, 1e6),
            "电导率"),
        DomainDescriptor("thermoelectric figure of merit", "热电优值",
            ["ZT", "热电优值", "figure of merit"], "", (0.01, 3),
            "热电材料品质因数 ZT = S²σT/κ"),
        DomainDescriptor("piezoelectric coefficient", "压电系数",
            ["d33", "d31", "压电系数"], "pC/N", (1, 2000),
            "压电系数"),
        DomainDescriptor("luminescence QY material", "发光量子产率",
            ["PLQY", "Φ_PL", "量子产率"], "%", (0, 100),
            "发光量子产率"),
    ],
    relations=[
        DomainRelation("carrier concentration", "carrier mobility",
            "Higher n_c → increased scattering → lower μ (trade-off)", "materials_chemistry",
            ["semiconductors"], "Mobility-concentration trade-off", 0.70),
        DomainRelation("band gap material", "electrical conductivity",
            "Larger E_g → intrinsic σ lower (semiconductor); doping can compensate", "materials_chemistry",
            [], "Band gap-conductivity", 0.75),
        DomainRelation("thermal conductivity", "thermoelectric figure of merit",
            "Lower κ → higher ZT (at given S and σ)", "materials_chemistry",
            ["thermoelectrics"], "κ-ZT inverse correlation", 0.85),
        DomainRelation("carrier concentration", "electrical conductivity",
            "σ = n_c × e × μ", "materials_chemistry",
            [], "Drude model", 0.90),
    ],
    transferable_techniques=[
        TransferableTechnique("inorganic_chemistry", "materials_chemistry",
            "溶胶-凝胶法", "纳米材料合成",
            "无机化学 sol-gel → 材料化学功能薄膜/粉体制备"),
        TransferableTechnique("polymer_chemistry", "materials_chemistry",
            "自修复高分子", "智能材料设计",
            "高分子化学自修复机制 → 材料化学智能响应材料"),
    ],
    baselines=["Si (semiconductor)", "BaTiO₃ (piezoelectric)", "Bi₂Te₃ (thermoelectric)", "YAG:Ce (phosphor)"],
    metrics=["electrical conductivity", "ZT", "PLQY", "piezoelectric coefficient", "hardness"],
    characterization=["XRD", "SEM/TEM", "Hall effect", "UV-Vis-NIR", "PPMS/SQUID", "DSC", "nanoindentation", "laser flash"],
    experiment_templates={
        "materials_tests": ["Hall measurement", "4-point probe", "laser flash", "nanoindentation"],
        "standard_conditions": {
            "temperature": "RT (unless specified)",
            "atmosphere": "ambient or N₂",
        },
    },
)

# ═══════════════════════════════════════════════════════════
# 14. 能源化学（广义，含氢能/生物质/CO₂转化/燃料电池）
# ═══════════════════════════════════════════════════════════

ENERGY_CHEMISTRY = DomainProfile(
    name="energy_chemistry",
    name_cn="能源化学",
    descriptors=[
        DomainDescriptor("energy conversion efficiency", "能量转换效率",
            ["η_energy", "效率", "conversion efficiency"], "%", (5, 95),
            "系统的能量转换效率"),
        DomainDescriptor("hydrogen storage capacity", "储氢容量",
            ["wt% H₂", "储氢量", "hydrogen storage"], "wt%", (0.1, 18),
            "质量储氢容量"),
        DomainDescriptor("CO₂ conversion rate", "CO₂转化速率",
            ["CO₂ conversion", "CO₂转化率"], "μmol/g/h or %", (0.1, 100),
            "CO₂ 还原/转化的速率或转化率"),
        DomainDescriptor("biomass conversion rate", "生物质转化率",
            ["biomass conversion", "生物质转化", "转化率"], "%", (10, 100),
            "生物质高值化转化的转化率"),
        DomainDescriptor("fuel cell power density", "燃料电池功率密度",
            ["P_density", "燃料电池功率"], "mW/cm²", (10, 2000),
            "燃料电池的功率密度"),
        DomainDescriptor("thermoelectric efficiency", "热电效率",
            ["η_TE", "热电转换效率"], "%", (1, 20),
            "热电转换效率"),
        DomainDescriptor("solar-to-fuel efficiency", "太阳能-燃料效率",
            ["STF", "solar-to-fuel", "太阳能燃料效率"], "%", (0.01, 20),
            "太阳能到化学燃料的转换效率"),
    ],
    properties=[
        DomainDescriptor("overall system efficiency", "系统总效率",
            ["η_total", "系统效率"], "%", (5, 80),
            "含所有损耗的系统总效率"),
        DomainDescriptor("cost-effectiveness", "经济性",
            ["LCOE", "cost", "度电成本", "经济性"], "$/kWh or ¥/kWh", (0.01, 1.0),
            "平准化能源成本"),
        DomainDescriptor("sustainability index", "可持续性指数",
            ["sustainability", "可持续", "碳排放"], "", (0, 1),
            "能源系统的可持续性评估"),
    ],
    relations=[
        DomainRelation("energy conversion efficiency", "overall system efficiency",
            "System η = product of component η's (cascade losses)", "energy_chemistry",
            [], "Cascaded efficiency model", 0.85),
        DomainRelation("hydrogen storage capacity", "cost-effectiveness",
            "Higher wt% H₂ → lower storage cost → better LCOE", "energy_chemistry",
            ["solid-state H₂ storage"], "Storage capacity-cost correlation", 0.70),
        DomainRelation("CO₂ conversion rate", "sustainability index",
            "Higher CO₂ conversion → lower net emission → better sustainability", "energy_chemistry",
            [], "Carbon neutrality metric", 0.75),
    ],
    transferable_techniques=[
        TransferableTechnique("electrocatalysis", "energy_chemistry",
            "电解水制氢", "绿氢生产",
            "电催化HER/OER → 能源化学电解水制氢系统"),
        TransferableTechnique("photocatalysis", "energy_chemistry",
            "光催化CO₂还原", "太阳能燃料合成",
            "光催化CO₂还原 → 能源化学太阳能-燃料系统"),
        TransferableTechnique("battery_materials", "energy_chemistry",
            "固态电池技术", "高安全储能",
            "电池材料固态电解质 → 能源化学安全储能系统"),
    ],
    baselines=["PEMFC (H₂)", "Li-ion NCM", "Si solar cell (benchmark)"],
    metrics=["energy conversion efficiency", "power density", "STF efficiency", "hydrogen storage wt%", "cost"],
    characterization=["electrochemical workstation", "GC", "mass spectrometry", "solar simulator", "calorimetry"],
    experiment_templates={
        "energy_tests": ["polarization curve (fuel cell)", "I-V curve (solar)", "GCD (battery)", "CO₂ reduction product analysis"],
        "standard_conditions": {
            "temperature": "25-80 °C",
            "pressure": "1-30 bar",
            "irradiance": "AM 1.5G (100 mW/cm²)",
        },
    },
)

# ═══════════════════════════════════════════════════════════
# 15. 环境化学
# ═══════════════════════════════════════════════════════════

ENVIRONMENTAL_CHEMISTRY = DomainProfile(
    name="environmental_chemistry",
    name_cn="环境化学",
    descriptors=[
        DomainDescriptor("pollutant concentration", "污染物浓度",
            ["C", "concentration", "污染物浓度"], "μg/L or μg/m³", (1e-6, 1000),
            "环境中污染物浓度"),
        DomainDescriptor("half-life environmental", "环境半衰期",
            ["t₁/₂ env", "half-life", "环境半衰期"], "d or yr", (0.01, 100),
            "污染物在环境中的降解半衰期"),
        DomainDescriptor("bioaccumulation factor", "生物富集因子",
            ["BAF", "BCF", "生物富集", "bioaccumulation"], "L/kg", (1, 100000),
            "生物富集因子"),
        DomainDescriptor("Koc", "有机碳吸附系数",
            ["Koc", "K_oc", "有机碳分配系数"], "L/kg", (1, 1000000),
            "有机碳-水分配系数"),
        DomainDescriptor("LD50", "半致死剂量",
            ["LD50", "LD₅₀", "半致死量"], "mg/kg", (0.1, 10000),
            "半数致死剂量"),
        DomainDescriptor("pH", "酸碱度",
            ["pH", "酸度"], "", (0, 14),
            "水/土壤的pH值"),
        DomainDescriptor("dissolved oxygen", "溶解氧",
            ["DO", "dissolved oxygen", "溶解氧"], "mg/L", (0, 20),
            "水体溶解氧浓度"),
        DomainDescriptor("chemical oxygen demand", "化学需氧量",
            ["COD", "化学需氧量"], "mg/L", (0, 10000),
            "化学需氧量"),
    ],
    properties=[
        DomainDescriptor("toxicity", "毒性",
            ["toxicity", "毒性", "ecotoxicity"], "", (0, 1),
            "污染物的生态/健康毒性"),
        DomainDescriptor("persistence", "持久性",
            ["persistence", "持久性", "POPs"], "", (0, 1),
            "污染物的环境持久性"),
        DomainDescriptor("mobility", "迁移性",
            ["mobility", "迁移", "迁移性"], "", (0, 1),
            "污染物在环境介质中的迁移能力"),
        DomainDescriptor("remediation efficiency", "修复效率",
            ["removal", "remediation", "修复效率", "去除率"], "%", (0, 100),
            "污染修复/去除效率"),
    ],
    relations=[
        DomainRelation("Koc", "mobility",
            "Higher Koc → lower mobility (stronger soil sorption)", "environmental_chemistry",
            [], "Soil sorption-mobility", 0.85),
        DomainRelation("bioaccumulation factor", "toxicity",
            "Higher BAF → bioconcentration → higher chronic toxicity risk", "environmental_chemistry",
            [], "Bioaccumulation-toxicity", 0.75),
        DomainRelation("half-life environmental", "persistence",
            "Longer t₁/₂ → more persistent → higher risk (POPs criterion: t₁/₂ > 60 d in water)", "environmental_chemistry",
            [], "Half-life-persistence", 0.90),
        DomainRelation("pollutant concentration", "toxicity",
            "Dose-response: higher C → higher adverse effect probability", "environmental_chemistry",
            [], "Dose-response relationship", 0.80),
    ],
    transferable_techniques=[
        TransferableTechnique("analytical_chemistry", "environmental_chemistry",
            "LC-MS/MS多残留筛查", "新污染物检测",
            "分析化学LC-MS/MS → 环境化学PFAS/抗生素痕量检测"),
        TransferableTechnique("photocatalysis", "environmental_chemistry",
            "光催化降解", "有机污染物矿化",
            "光催化降解有机物 → 环境化学水处理技术"),
        TransferableTechnique("green_chemical_engineering", "environmental_chemistry",
            "源头减排工艺", "污染预防",
            "绿色化工原子经济 → 环境化学源头减污"),
    ],
    baselines=["WHO drinking water standards", "EU Water Framework Directive limits", "GB 3838-2002 (China)"],
    metrics=["remediation efficiency", "pollutant concentration (post-treatment)", "half-life", "BAF", "COD removal"],
    characterization=["GC-MS", "LC-MS/MS", "ICP-MS", "IC", "UV-Vis", "TOC analyzer", "pH meter", "DO meter"],
    experiment_templates={
        "environmental_tests": ["batch adsorption", "degradation kinetics", "toxicity assay", "field sampling"],
        "standard_conditions": {
            "temperature": "25 ± 2 °C",
            "pH": "7.0 ± 0.5",
            "reaction_time": "0-72 h",
        },
    },
)

# ═══════════════════════════════════════════════════════════
# 16. 聚集体与纳米化学
# ═══════════════════════════════════════════════════════════

NANO_CHEMISTRY = DomainProfile(
    name="nano_chemistry",
    name_cn="聚集体与纳米化学",
    descriptors=[
        DomainDescriptor("particle size nano", "纳米粒径",
            ["size", "DLS", "粒径", "hydrodynamic diameter"], "nm", (1, 500),
            "纳米颗粒的粒径"),
        DomainDescriptor("size distribution", "粒径分布",
            ["PDI nano", "PDI", "粒径分布", "polydispersity"], "", (0.01, 0.5),
            "粒径多分散指数"),
        DomainDescriptor("surface plasmon resonance", "表面等离激元共振",
            ["SPR", "LSPR", "等离激元", "plasmon"], "nm", (400, 1200),
            "局域表面等离激元共振波长"),
        DomainDescriptor("zeta potential nano", "Zeta电位",
            ["ζ nano", "zeta", "表面电位"], "mV", (-100, 100),
            "纳米颗粒的Zeta电位"),
        DomainDescriptor("aspect ratio", "长径比",
            ["AR", "aspect ratio", "长径比"], "", (1, 50),
            "纳米棒/线的长径比"),
        DomainDescriptor("surface functionalization density", "表面修饰密度",
            ["ligand density", "功能化密度", "修饰密度"], "nm⁻²", (0.1, 10),
            "表面配体/功能基团的密度"),
        DomainDescriptor("aggregation number", "聚集数",
            ["N_agg", "aggregation number", "聚集数"], "", (2, 1000),
            "自组装聚集体的分子数"),
        DomainDescriptor("quantum yield nano", "量子产率",
            ["QY", "量子产率", "Φ"], "%", (0, 100),
            "荧光/发光量子产率"),
    ],
    properties=[
        DomainDescriptor("catalytic activity nano", "催化活性",
            ["TOF nano", "nano-catalysis", "纳米催化"], "h⁻¹", (1, 100000),
            "纳米催化剂的催化活性"),
        DomainDescriptor("photothermal conversion", "光热转换效率",
            ["η_PTT", "photothermal", "光热转换"], "%", (10, 80),
            "纳米材料的光热转换效率"),
        DomainDescriptor("drug loading", "药物装载量",
            ["DLC", "DL%", "drug loading", "载药量"], "%", (1, 50),
            "纳米载体的药物装载百分比"),
        DomainDescriptor("colloidal stability", "胶体稳定性",
            ["stability", "胶体稳定", "聚集稳定性"], "", (0, 1),
            "纳米分散液的胶体稳定性"),
    ],
    relations=[
        DomainRelation("particle size nano", "surface plasmon resonance",
            "Larger Au NPs → red-shifted LSPR (Mie theory)", "nano_chemistry",
            ["Au", "Ag nanoparticles"], "Mie theory / LSPR size dependence", 0.85),
        DomainRelation("zeta potential nano", "colloidal stability",
            "|ζ| > 30 mV → electrostatically stable dispersion", "nano_chemistry",
            [], "DLVO theory", 0.80),
        DomainRelation("particle size nano", "catalytic activity nano",
            "Smaller NPs → higher surface/volume → higher TOF (size effect)", "nano_chemistry",
            ["metal NPs < 5 nm"], "Nanocatalysis size effect", 0.75),
        DomainRelation("aspect ratio", "surface plasmon resonance",
            "Higher AR → longitudinal LSPR red-shifts", "nano_chemistry",
            ["Au nanorods"], "Gans theory for anisotropic NPs", 0.80),
    ],
    transferable_techniques=[
        TransferableTechnique("surface_interface_chemistry", "nano_chemistry",
            "自组装单分子层", "纳米颗粒表面功能化",
            "表界面化学SAMs → 纳米化学配体修饰"),
        TransferableTechnique("chemical_biology", "nano_chemistry",
            "靶向纳米载体", "药物递送系统",
            "化学生物学靶向设计 → 纳米化学靶向载药"),
    ],
    baselines=["Au NPs (citrate-capped)", "CdSe/ZnS QDs", "Fe₃O₄ NPs (superparamagnetic)"],
    metrics=["particle size + PDI", "quantum yield", "catalytic TOF", "photothermal η", "drug loading %"],
    characterization=["TEM/HRTEM", "DLS", "UV-Vis", "XPS", "FTIR", "zeta potential", "TGA", "ICP-OES"],
    experiment_templates={
        "nano_synthesis": ["hot injection", "seeded growth", "solvothermal", "microemulsion"],
        "standard_conditions": {
            "temperature": "100-300 °C (hot injection)",
            "solvent": "ODE/OAm (non-polar) or water (polar)",
            "reaction_time": "5 min - 24 h",
        },
    },
)

# ═══════════════════════════════════════════════════════════
# 17. 团簇与仿生化学
# ═══════════════════════════════════════════════════════════

CLUSTER_CHEMISTRY = DomainProfile(
    name="cluster_chemistry",
    name_cn="团簇与仿生化学",
    descriptors=[
        DomainDescriptor("cluster nuclearity", "团簇核数",
            ["M_n", "nuclearity", "核数", "Au₂₅", "Ag₄₄"], "", (2, 500),
            "金属团簇的金属原子数"),
        DomainDescriptor("metal-metal bond length", "金属-金属键长",
            ["M-M", "MM distance", "金属间距"], "Å", (2.0, 4.0),
            "团簇中金属-金属键长"),
        DomainDescriptor("HOMO-LUMO gap cluster", "HOMO-LUMO能隙",
            ["gap", "E_gap", "能隙"], "eV", (0.3, 4.0),
            "团簇的前线轨道能隙"),
        DomainDescriptor("magnetic moment cluster", "磁矩",
            ["μ_eff cluster", "magnetic moment", "磁矩"], "μB", (0, 15),
            "团簇的有效磁矩"),
        DomainDescriptor("redox potential cluster", "氧化还原电位",
            ["E₁/₂", "redox", "氧化还原电位"], "V", (-2.5, 2.5),
            "团簇的氧化还原电位"),
        DomainDescriptor("LMCT energy", "配体-金属电荷转移能",
            ["LMCT", "MLCT", "电荷转移能"], "eV", (1, 5),
            "配体到金属的电荷转移能量"),
        DomainDescriptor("stability index cluster", "稳定性指数",
            ["stability", "magic number", "超原子电子数"], "", (0, 1),
            "团簇的稳定性（超原子规则、电子壳层闭合）"),
    ],
    properties=[
        DomainDescriptor("catalytic activity cluster", "催化活性",
            ["TOF cluster", "TON cluster", "催化活性"], "h⁻¹", (1, 100000),
            "团簇催化剂的催化活性"),
        DomainDescriptor("luminescence cluster", "发光性能",
            ["PL", "emission", "发光", "荧光"], "nm", (400, 1200),
            "团簇的发光波长和量子产率"),
        DomainDescriptor("enzyme-mimicking activity", "仿酶活性",
            ["Km", "Vmax", "类酶活性", "仿酶"], "μM or μM/min", (0.01, 10000),
            "团簇模拟酶的Michaelis-Menten参数"),
    ],
    relations=[
        DomainRelation("cluster nuclearity", "HOMO-LUMO gap cluster",
            "Larger nuclearity → smaller gap (approaching bulk metal limit)", "cluster_chemistry",
            ["Au clusters"], "Size-dependent electronic structure (Jellium model)", 0.80),
        DomainRelation("cluster nuclearity", "stability index cluster",
            "Magic numbers (e.g., Au₂₅, Au₁₀₂) → enhanced stability (superatom shell closure)", "cluster_chemistry",
            [], "Superatom electron count rule (N* = n - z)", 0.85),
        DomainRelation("HOMO-LUMO gap cluster", "luminescence cluster",
            "Smaller gap → longer emission wavelength; gap affects PLQY", "cluster_chemistry",
            [], "Gap-luminescence relationship", 0.70),
        DomainRelation("redox potential cluster", "catalytic activity cluster",
            "Appropriate E₁/₂ (neither too positive nor negative) → optimal catalytic activity", "cluster_chemistry",
            ["electrocatalytic clusters"], "Sabatier principle for clusters", 0.70),
    ],
    transferable_techniques=[
        TransferableTechnique("electrocatalysis", "cluster_chemistry",
            "单原子催化活性位点", "团簇→SAC演化",
            "电催化SAC → 团簇化学最小催化单元"),
        TransferableTechnique("inorganic_chemistry", "cluster_chemistry",
            "金属蛋白活性中心模拟", "仿生团簇设计",
            "无机化学金属蛋白模拟 → 团簇化学仿酶设计"),
    ],
    baselines=["Au₂₅(SR)₁₈", "Ag₄₄(SR)₃₀", "Fe₄S₄ cluster (FeMoco model)"],
    metrics=["cluster nuclearity", "HOMO-LUMO gap", "PLQY", "enzyme-mimicking Km/Vmax", "TOF"],
    characterization=["ESI-MS", "single-crystal XRD", "UV-Vis", "PL", "XPS", "TEM/HAADF-STEM", "EPR", "Mössbauer"],
    experiment_templates={
        "cluster_synthesis": ["NaBH₄ reduction", "size-focusing", "ligand exchange"],
        "standard_conditions": {
            "temperature": "0 °C - RT",
            "solvent": "MeOH/DCM/THF",
            "time": "2-48 h",
            "thiolate:metal ratio": "2-6:1",
        },
    },
)

# ═══════════════════════════════════════════════════════════
# 18. 绿色化工与过程强化
# ═══════════════════════════════════════════════════════════

GREEN_CHEMICAL_ENGINEERING = DomainProfile(
    name="green_chemical_engineering",
    name_cn="绿色化工与过程强化",
    descriptors=[
        DomainDescriptor("atom economy process", "过程原子经济性",
            ["atom economy", "原子经济", "原子利用率"], "%", (10, 100),
            "过程的原子经济性"),
        DomainDescriptor("E-factor process", "过程环境因子",
            ["E factor", "E-factor", "环境因子"], "kg/kg", (0.01, 100),
            "过程的废弃物产生因子"),
        DomainDescriptor("process mass intensity", "过程质量强度",
            ["PMI", "process mass intensity", "质量强度"], "kg/kg", (1, 1000),
            "过程质量强度 = 总输入/产品"),
        DomainDescriptor("space-time yield", "时空收率",
            ["STY", "space-time yield", "时空产率"], "kg/m³/h", (0.1, 10000),
            "单位反应器体积单位时间的产物质量"),
        DomainDescriptor("energy intensity", "能量强度",
            ["E_intensity", "能耗", "能量强度"], "MJ/kg", (0.1, 1000),
            "单位产物的能量消耗"),
        DomainDescriptor("solvent intensity", "溶剂强度",
            ["SI", "solvent intensity", "溶剂用量"], "kg/kg", (0, 100),
            "单位产物消耗的溶剂质量"),
        DomainDescriptor("recycle ratio", "循环比",
            ["recycle", "循环比", "回用率"], "%", (0, 100),
            "溶剂/催化剂/未反应物的回收比例"),
        DomainDescriptor("residence time", "停留时间",
            ["τ", "residence time", "停留时间"], "s or min", (0.001, 3600),
            "反应器内的平均停留时间"),
    ],
    properties=[
        DomainDescriptor("process efficiency", "过程效率",
            ["η_process", "过程效率", "转化率"], "%", (10, 100),
            "过程的综合效率"),
        DomainDescriptor("product yield process", "产品收率",
            ["yield", "收率", "产物收率"], "%", (10, 100),
            "过程产品收率"),
        DomainDescriptor("waste generation", "废弃物产生",
            ["waste", "废弃物", "三废"], "kg/kg", (0, 50),
            "单位产品的废弃物产生量"),
        DomainDescriptor("safety index", "安全指数",
            ["safety", "安全指数", "风险评估"], "", (0, 1),
            "过程安全评估指数"),
    ],
    relations=[
        DomainRelation("atom economy process", "E-factor process",
            "Higher atom economy → lower E-factor", "green_chemical_engineering",
            [], "Green metrics inverse correlation", 0.90),
        DomainRelation("process mass intensity", "waste generation",
            "Higher PMI → more waste (PMI = 1 + E-factor)", "green_chemical_engineering",
            [], "PMI-waste correlation", 0.85),
        DomainRelation("residence time", "space-time yield",
            "Shorter τ → higher STY (at same conversion) = process intensification", "green_chemical_engineering",
            ["microreactors"], "Process intensification metric", 0.80),
        DomainRelation("recycle ratio", "waste generation",
            "Higher recycle → lower waste generation", "green_chemical_engineering",
            [], "Recycle-waste inverse", 0.85),
    ],
    transferable_techniques=[
        TransferableTechnique("synthetic_chemistry", "green_chemical_engineering",
            "一锅法串联反应", "减少分离步骤",
            "合成化学一锅法 → 绿色化工步骤集成"),
        TransferableTechnique("nano_chemistry", "green_chemical_engineering",
            "膜催化反应器", "反应-分离耦合",
            "纳米化学膜材料 → 绿色化工反应-分离耦合强化"),
    ],
    baselines=["TTraditional batch (E-factor ~25-100)", "Pfizer MPI target (< 40)"],
    metrics=["E-factor", "PMI", "space-time yield", "atom economy", "energy intensity", "safety index"],
    characterization=["GC", "HPLC", "calorimetry", "flow meter", "pressure sensor", "online IR/Raman"],
    experiment_templates={
        "process_tests": ["continuous flow", "batch comparison", "heat/mass transfer analysis"],
        "standard_conditions": {
            "reactor": "microreactor or CSTR",
            "temperature": "optimized",
            "pressure": "ambient to 50 bar",
            "flow_rate": "0.1-10 mL/min",
        },
    },
)


# ─────────────────────────────────────────────────────────────
# 知识库注册表
# ─────────────────────────────────────────────────────────────

KNOWLEDGE_BASE: Dict[str, DomainProfile] = {
    # 原有3大领域
    "electrocatalysis": ELECTROCATALYSIS,
    "photocatalysis": PHOTOCATALYSIS,
    "battery_materials": BATTERY_MATERIALS,
    # 新增15大领域
    "synthetic_chemistry": SYNTHETIC_CHEMISTRY,
    "surface_interface_chemistry": SURFACE_INTERFACE_CHEMISTRY,
    "physical_chemistry": PHYSICAL_CHEMISTRY,
    "quantum_chemistry": QUANTUM_CHEMISTRY,
    "analytical_chemistry": ANALYTICAL_CHEMISTRY,
    "organic_chemistry": ORGANIC_CHEMISTRY,
    "inorganic_chemistry": INORGANIC_CHEMISTRY,
    "polymer_chemistry": POLYMER_CHEMISTRY,
    "chemical_biology": CHEMICAL_BIOLOGY,
    "materials_chemistry": MATERIALS_CHEMISTRY,
    "energy_chemistry": ENERGY_CHEMISTRY,
    "environmental_chemistry": ENVIRONMENTAL_CHEMISTRY,
    "nano_chemistry": NANO_CHEMISTRY,
    "cluster_chemistry": CLUSTER_CHEMISTRY,
    "green_chemical_engineering": GREEN_CHEMICAL_ENGINEERING,
}


# ─────────────────────────────────────────────────────────────
# 跨学科技术迁移映射（CrossDomainAnalogyAgent 使用）
# ─────────────────────────────────────────────────────────────

@dataclass
class CrossDomainMapping:
    """跨领域描述符映射——用于发现跨学科技术迁移潜力"""
    source_domain: str              # 源领域标识
    target_domain: str              # 目标领域标识
    source_descriptor: str          # 源领域描述符标准名
    target_descriptor: str          # 目标领域描述符标准名
    mapping_rationale: str          # 映射的物理/化学原理
    confidence: float = 0.7        # 映射置信度
    example: str = ""              # 具体迁移示例


CROSS_DOMAIN_MAPPINGS: List[CrossDomainMapping] = [
    # ── 电催化 ↔ 光催化 ──
    CrossDomainMapping(
        "electrocatalysis", "photocatalysis",
        "d-band center", "band gap",
        "d 带中心调控吸附强度 ↔ 带隙调控光吸收范围，"
        "二者均通过电子结构工程优化活性",
        0.80,
        "d-band 上移 → ORR 活性增强 ↔ 带隙缩小 → 可见光吸收增强",
    ),
    CrossDomainMapping(
        "electrocatalysis", "photocatalysis",
        "adsorption energy", "conduction band",
        "*OH 吸附能决定 ORR 过电势 ↔ CB 位置决定光催化还原能力，"
        "二者均与表面反应热力学耦合",
        0.75,
        "优化 *OH 吸附 → 低过电势 ↔ 优化 CB 位置 → 高 H₂ 析出速率",
    ),
    CrossDomainMapping(
        "electrocatalysis", "photocatalysis",
        "tafel slope", "quantum efficiency",
        "Tafel 斜率反映动力学机理 ↔ AQE 反映光子利用效率，"
        "均为活性-机理桥接指标",
        0.65,
        "Tafel ~60 mV/dec ↔ AQE > 10%，均指示理想机理路径",
    ),
    # ── 电催化 ↔ 电池材料 ──
    CrossDomainMapping(
        "electrocatalysis", "battery_materials",
        "d-band center", "voltage plateau",
        "d 带中心影响氧化还原电位 ↔ 电压平台由电极氧化还原对决定",
        0.70,
        "ε_d 调控 ORR 电位 ↔ 过渡金属 d 电子填充调控 Li 插层电位",
    ),
    CrossDomainMapping(
        "electrocatalysis", "battery_materials",
        "coordination number", "diffusion coefficient",
        "低配位数→窄d带→高活性 ↔ 大扩散通道→高离子迁移，"
        "均为结构-功能关系",
        0.65,
        "Co-N₄ SAC 高活性 ↔ Li⁺ 在大孔道中快扩散",
    ),
    CrossDomainMapping(
        "electrocatalysis", "battery_materials",
        "interlayer distance", "specific capacity",
        "层间距调控 d 带耦合 ↔ 层间距决定可嵌入离子数量",
        0.70,
        "大层间距 → d 带上移 → 高 ORR 活性 ↔ 大层间距 → 更多 Li⁻ 存储 → 高比容量",
    ),
    # ── 光催化 ↔ 电池材料 ──
    CrossDomainMapping(
        "photocatalysis", "battery_materials",
        "band gap", "voltage plateau",
        "半导体带隙 ↔ 电池工作电压，"
        "二者均由电子结构中能级间距决定",
        0.72,
        "E_g = 2.0 eV → 可见光驱动 ↔ V_plateau = 3.7 V → 高能量密度",
    ),
    CrossDomainMapping(
        "photocatalysis", "battery_materials",
        "defect density", "cycling stability",
        "适量缺陷促进电荷分离 ↔ 适量缺陷缓冲体积变化，"
        "过量则有害（复合中心/结构崩塌）",
        0.68,
        "适中氧空位 → 高光催化活性 ↔ 适中缺陷 → 缓冲应变 → 长循环寿命",
    ),
    # ── 量子化学 → 多领域 ──
    CrossDomainMapping(
        "quantum_chemistry", "electrocatalysis",
        "HOMO-LUMO gap", "d-band center",
        "前线轨道能隙 ↔ d 带中心位置，均为电子结构核心描述符，"
        "可从第一性原理计算互推",
        0.75,
        "DFT 计算 HOMO-LUMO → 预测 d 带中心 → 指导催化剂筛选",
    ),
    CrossDomainMapping(
        "quantum_chemistry", "organic_chemistry",
        "reaction barrier", "logP",
        "反应势垒决定动力学 ↔ logP 影响反应环境偏好，"
        "量子化学可同时预测二者",
        0.60,
        "TS 计算 ΔG‡ → 预测反应速率 ↔ 量子化学 logP 计算 → 溶解性优化",
    ),
    # ── 合成化学 → 多领域 ──
    CrossDomainMapping(
        "synthetic_chemistry", "polymer_chemistry",
        "catalyst loading", "PDI",
        "催化剂用量影响反应控制度 ↔ 引发剂/链转移剂比例影响分子量分布，"
        "均为'控制试剂用量→产物精度'逻辑",
        0.65,
        "低催化剂量 → 高 TON ↔ 精确引发剂比例 → 低 PDI",
    ),
    # ── 纳米化学 → 多领域 ──
    CrossDomainMapping(
        "nano_chemistry", "chemical_biology",
        "surface functionalization density", "cell permeability",
        "表面配体密度调控生物相容性 ↔ 细胞渗透性决定递送效率",
        0.60,
        "PEG 化密度 → 长循环 ↔ 适中功能化 → 高细胞摄取",
    ),
    # ── 环境化学 ↔ 光催化 ──
    CrossDomainMapping(
        "environmental_chemistry", "photocatalysis",
        "remediation efficiency", "H₂ evolution rate",
        "污染物降解效率 ↔ 光催化活性，均为光驱动氧化还原过程",
        0.70,
        "TiO₂ 光催化降解有机物 ↔ 同一体系优化后可耦合产氢",
    ),
]


def find_cross_domain_analogies(
    domain: str,
    hypothesis_text: str = "",
    top_k: int = 3,
) -> List[CrossDomainMapping]:
    """
    为指定领域查找跨学科技术迁移映射。

    Args:
        domain: 当前领域标识（如 "electrocatalysis"）
        hypothesis_text: 假设文本（用于语义匹配，可选）
        top_k: 返回最多 top_k 条映射

    Returns:
        按置信度排序的 CrossDomainMapping 列表
    """
    # 1. 精确匹配：source_domain 或 target_domain == domain
    direct = [m for m in CROSS_DOMAIN_MAPPINGS
              if m.source_domain == domain or m.target_domain == domain]

    # 2. 语义辅助：如果提供了 hypothesis_text，用关键词匹配做粗筛
    semantic_hits: List[CrossDomainMapping] = []
    if hypothesis_text:
        text_lower = hypothesis_text.lower()
        for m in CROSS_DOMAIN_MAPPINGS:
            if m in direct:
                continue
            # 如果映射原理/示例中的关键词出现在假设中
            keywords = (m.mapping_rationale + m.example).lower()
            overlap = sum(1 for w in keywords.split() if len(w) > 4 and w in text_lower)
            if overlap > 0:
                semantic_hits.append(m)

    # 合并并按置信度排序
    all_mappings = direct + semantic_hits
    all_mappings.sort(key=lambda m: m.confidence, reverse=True)
    return all_mappings[:top_k]


# ─────────────────────────────────────────────────────────────
# 领域自动检测
# ─────────────────────────────────────────────────────────────

DOMAIN_KEYWORDS: Dict[str, Dict] = {
    # ── 原有3大领域 ──
    "electrocatalysis": {
        "en": [
            "ORR", "OER", "HER", "overpotential", "Tafel", "half-wave potential",
            "LSV", "CV", "rotating disk", "RRDE", "oxygen reduction",
            "oxygen evolution", "hydrogen evolution", "electrocatalyst",
            "onset potential", "electron transfer number",
        ],
        "cn": [
            "电催化", "氧还原", "析氧", "析氢", "过电势", "过电位",
            "半波电位", "Tafel斜率", "塔菲尔", "旋转圆盘",
        ],
    },
    "photocatalysis": {
        "en": [
            "photocatalysis", "photocatalytic", "H₂ evolution", "CO₂ reduction",
            "band gap", "light absorption", "quantum efficiency", "photocurrent",
            "photoelectrochemical", "visible light", "UV-Vis DRS", "charge separation",
        ],
        "cn": [
            "光催化", "光解水", "光还原", "产氢", "量子效率",
            "光电流", "光电化学", "可见光", "带隙",
        ],
    },
    "battery_materials": {
        "en": [
            "cathode", "anode", "specific capacity", "coulombic efficiency",
            "cycling stability", "lithium-ion", "sodium-ion", "solid electrolyte",
            "rate capability", "energy density", "power density", "intercalation",
            "GITT", "charge-discharge",
        ],
        "cn": [
            "正极", "负极", "比容量", "循环稳定性", "库仑效率",
            "锂离子", "钠离子", "固态电解质", "倍率性能",
            "能量密度", "充放电", "嵌入",
        ],
    },
    # ── 新增15大领域 ──
    "synthetic_chemistry": {
        "en": [
            "synthesis", "synthetic", "cross-coupling", "C-H activation",
            "catalytic reaction", "organocatalysis", "total synthesis",
            "enantioselective", "stereoselective", "cascade reaction",
            "multicomponent reaction", "atom economy", "green synthesis",
            "turnover number", "turnover frequency", "asymmetric synthesis",
        ],
        "cn": [
            "合成", "有机合成", "交叉偶联", "C-H活化", "催化反应",
            "全合成", "对映选择性", "不对称合成", "串联反应",
            "原子经济性", "绿色合成", "转化数", "收率",
        ],
    },
    "surface_interface_chemistry": {
        "en": [
            "surface chemistry", "interface", "adsorption isotherm",
            "contact angle", "wettability", "heterogeneous catalysis",
            "homogeneous catalysis", "enzyme catalysis", "active site",
            "BET", "TPD", "acid site", "in-situ characterization",
            "operando", "surface energy", "zeolite",
        ],
        "cn": [
            "表面化学", "界面", "吸附等温线", "接触角", "润湿性",
            "多相催化", "均相催化", "酶催化", "活性位点",
            "酸位点", "原位表征", "工况", "沸石",
        ],
    },
    "physical_chemistry": {
        "en": [
            "thermodynamics", "kinetics", "activation energy", "Arrhenius",
            "Gibbs free energy", "enthalpy", "entropy", "equilibrium constant",
            "reaction rate", "transition state", "Eyring equation",
            "colloidal", "soft matter", "rheology", "phase transition",
            "electrochemistry", "double layer",
        ],
        "cn": [
            "热力学", "动力学", "活化能", "阿伦尼乌斯", "吉布斯自由能",
            "焓变", "熵变", "平衡常数", "反应速率", "过渡态",
            "胶体", "软物质", "流变", "相变", "电化学", "双电层",
        ],
    },
    "quantum_chemistry": {
        "en": [
            "quantum chemistry", "DFT", "ab initio", "density functional",
            "HOMO", "LUMO", "molecular orbital", "computational chemistry",
            "basis set", "coupled cluster", "MP2", "Hartree-Fock",
            "potential energy surface", "transition state search",
            "NEB", "VQE", "neural network potential",
        ],
        "cn": [
            "量子化学", "密度泛函", "从头算", "分子轨道", "计算化学",
            "基组", "耦合簇", "势能面", "过渡态搜索",
            "前线轨道", "HOMO-LUMO",
        ],
    },
    "analytical_chemistry": {
        "en": [
            "analytical chemistry", "HPLC", "GC-MS", "LC-MS", "ICP-MS",
            "detection limit", "sensitivity", "selectivity",
            "fluorescence probe", "electrochemical sensor", "biosensor",
            "mass spectrometry", "NMR spectroscopy", "chromatography",
            "single molecule", "super-resolution", "wearable sensor",
        ],
        "cn": [
            "分析化学", "液相色谱", "气相色谱", "质谱", "检出限",
            "灵敏度", "荧光探针", "电化学传感器", "生物传感器",
            "单分子检测", "超分辨", "可穿戴", "便携式",
        ],
    },
    "organic_chemistry": {
        "en": [
            "organic chemistry", "organic reaction", "functional group",
            "natural product", "medicinal chemistry", "drug discovery",
            "chirality", "stereochemistry", "retrosynthesis",
            "photo redox", "electrochemical synthesis",
            "TADF", "covalent organic framework", "molecular machine",
        ],
        "cn": [
            "有机化学", "有机反应", "官能团", "天然产物", "药物化学",
            "手性", "立体化学", "逆合成", "光氧化还原", "电化学合成",
            "热激活延迟荧光", "共价有机框架", "分子机器",
        ],
    },
    "inorganic_chemistry": {
        "en": [
            "inorganic chemistry", "coordination chemistry", "metal-organic framework",
            "MOF", "COF", "perovskite", "rare earth", "lanthanide",
            "crystal field", "ligand field", "ionic conductor",
            "topological material", "bioinorganic", "platinum drug",
        ],
        "cn": [
            "无机化学", "配位化学", "金属有机框架", "MOF", "COF",
            "钙钛矿", "稀土", "镧系", "晶体场", "配体场",
            "快离子导体", "拓扑材料", "生物无机", "铂类药物",
        ],
    },
    "polymer_chemistry": {
        "en": [
            "polymer", "polymerization", "ATRP", "RAFT", "macromolecule",
            "glass transition", "crystallinity", "crosslinking",
            "elastomer", "thermoset", "OLED polymer", "self-healing",
            "shape memory", "drug carrier", "hydrogel", "block copolymer",
        ],
        "cn": [
            "高分子", "聚合", "可控聚合", "玻璃化转变", "结晶度",
            "交联", "弹性体", "热固性", "自修复", "形状记忆",
            "水凝胶", "嵌段共聚物", "药物载体",
        ],
    },
    "chemical_biology": {
        "en": [
            "chemical biology", "bioorthogonal", "PROTAC", "kinase inhibitor",
            "small molecule probe", "protein labeling", "post-translational modification",
            "chemical genetics", "drug target", "binding affinity",
            "cell permeability", "bioavailability", "enzyme inhibitor",
        ],
        "cn": [
            "化学生物学", "生物正交", "蛋白降解", "激酶抑制剂",
            "小分子探针", "蛋白质标记", "翻译后修饰", "化学遗传学",
            "药物靶点", "结合亲和力", "细胞渗透性", "生物利用度",
        ],
    },
    "materials_chemistry": {
        "en": [
            "materials chemistry", "2D material", "quantum dot",
            "nanocomposite", "biomimetic material", "energy material",
            "functional material", "information material", "flexible electronics",
            "thermoelectric", "piezoelectric", "ferroelectric",
            "photonic crystal", "metamaterial",
        ],
        "cn": [
            "材料化学", "二维材料", "量子点", "纳米复合", "仿生材料",
            "能源材料", "功能材料", "信息材料", "柔性电子",
            "热电", "压电", "铁电", "光子晶体",
        ],
    },
    "energy_chemistry": {
        "en": [
            "energy chemistry", "hydrogen storage", "fuel cell", "PEMFC",
            "solar fuel", "CO₂ reduction", "biomass conversion",
            "biofuel", "solar-to-fuel", "electrolysis",
            "photovoltaic", "thermoelectric generator",
            "ammonia cracking", "liquid organic hydrogen carrier",
        ],
        "cn": [
            "能源化学", "储氢", "燃料电池", "太阳能燃料", "CO₂还原",
            "生物质转化", "生物燃料", "电解水", "光伏",
            "热电发电", "氨裂解", "液态有机储氢", "双碳",
        ],
    },
    "environmental_chemistry": {
        "en": [
            "environmental chemistry", "pollutant", "PFAS", "microplastic",
            "antibiotic resistance", "heavy metal", "remediation",
            "PM2.5", "ozone", "POPs", "bioaccumulation",
            "persistence", "ecotoxicity", "water treatment", "soil remediation",
        ],
        "cn": [
            "环境化学", "污染物", "微塑料", "抗生素抗性", "重金属",
            "修复", "PM2.5", "臭氧", "持久性有机污染物", "生物富集",
            "持久性", "生态毒性", "水处理", "土壤修复",
        ],
    },
    "nano_chemistry": {
        "en": [
            "nanochemistry", "nanoparticle", "self-assembly", "supramolecular",
            "surface plasmon resonance", "AIE", "aggregation-induced emission",
            "colloidal synthesis", "quantum confinement",
            "mesoporous", "nanostructure", "LSPR", "DLS",
        ],
        "cn": [
            "纳米化学", "纳米颗粒", "自组装", "超分子", "表面等离激元",
            "聚集诱导发光", "AIE", "胶体合成", "量子限域",
            "介孔", "纳米结构", "胶体稳定性",
        ],
    },
    "cluster_chemistry": {
        "en": [
            "cluster chemistry", "metal cluster", "nanocluster", "superatom",
            "magic number", "atomically precise", "Au₂₅", "Ag₄₄",
            "enzyme mimic", "biomimetic", "nanozyme",
            "Mössbauer", "FeMoco",
        ],
        "cn": [
            "团簇化学", "金属团簇", "纳米团簇", "超原子", "幻数",
            "原子级精确", "仿酶", "纳米酶", "团簇催化",
        ],
    },
    "green_chemical_engineering": {
        "en": [
            "green chemistry", "process intensification", "microreactor",
            "continuous flow", "membrane separation", "E-factor",
            "atom economy", "solvent-free", "carbon footprint",
            "process mass intensity", "space-time yield",
            "industrial sustainability", "mesoscale",
        ],
        "cn": [
            "绿色化学", "过程强化", "微反应器", "连续流", "膜分离",
            "环境因子", "原子经济性", "无溶剂", "碳足迹",
            "过程质量强度", "时空收率", "工业可持续", "介尺度",
        ],
    },
}


def detect_domain(text: str) -> Tuple[str, float]:
    """
    从文献文本自动检测研究领域。

    Args:
        text: 文献文本（可以是全文或摘要）

    Returns:
        (domain_name, confidence) 元组
        - domain_name: 18个领域之一或 "unknown"
        - confidence: 0.0-1.0 的置信度
    """
    text_lower = text.lower()
    scores: Dict[str, float] = {}

    for domain, kw_dict in DOMAIN_KEYWORDS.items():
        all_keywords = kw_dict.get("en", []) + kw_dict.get("cn", [])
        if not all_keywords:
            continue
        hits = sum(1 for kw in all_keywords if kw.lower() in text_lower)
        # 归一化：命中数 / 总关键词数，但上限为 1.0
        scores[domain] = min(1.0, hits / max(len(all_keywords) * 0.15, 1))

    if not scores or max(scores.values()) < 0.05:
        return "unknown", 0.0

    best_domain = max(scores, key=scores.get)
    best_score = scores[best_domain]

    # 如果第二名和第一名差距很小，降低置信度
    sorted_scores = sorted(scores.values(), reverse=True)
    if len(sorted_scores) >= 2 and sorted_scores[0] > 0:
        gap = (sorted_scores[0] - sorted_scores[1]) / sorted_scores[0]
        confidence = best_score * (0.5 + 0.5 * gap)  # gap 越大越自信
    else:
        confidence = best_score

    return best_domain, min(1.0, confidence)


def get_domain_profile(domain: str) -> Optional[DomainProfile]:
    """获取领域画像"""
    return KNOWLEDGE_BASE.get(domain)


def get_rules_for_domain(domain: str) -> list:
    """获取指定领域的关联规则（DataMiner 兼容格式）"""
    profile = KNOWLEDGE_BASE.get(domain)
    if not profile:
        return []
    return profile.get_all_rules()


def get_aliases_for_domain(domain: str) -> Dict[str, List[str]]:
    """获取指定领域的别名映射"""
    profile = KNOWLEDGE_BASE.get(domain)
    if not profile:
        return {}
    return profile.get_all_aliases()


# ─────────────────────────────────────────────────────────────
# 外部知识图谱 API 适配器
# ─────────────────────────────────────────────────────────────

class ExternalKnowledgeAdapter:
    """
    外部知识图谱适配器。

    支持的外部数据源：
    - Materials Project API: 无机材料结构/性质数据库
    - Semantic Scholar API: 文献摘要/引用网络
    - PubChem PUG REST: 化学物质性质

    所有查询均做优雅降级：API Key 缺失或网络错误时返回 None。
    """

    # ── Materials Project API ──

    @staticmethod
    def query_material(
        formula: str,
        api_key: str | None = None,
        session=None,
    ) -> Optional[dict]:
        """
        查询 Materials Project 获取材料结构/性质。

        API Key 可由调用方显式传入；legacy 桌面模式回退到环境变量。
        返回: {"formula": ..., "band_gap": ..., "formation_energy": ..., ...}
        """
        resolved_key = str(
            api_key
            or os.getenv("MATERIALS_PROJECT_API_KEY", "")
            or os.getenv("MP_API_KEY", "")
        ).strip()
        normalized_formula = str(formula or "").strip()
        if not resolved_key or not normalized_formula:
            return None
        try:
            import requests

            client = session or requests
            response = client.get(
                "https://api.materialsproject.org/materials/summary/",
                params={
                    "formula": normalized_formula,
                    "_fields": (
                        "material_id,formula_pretty,band_gap,formation_energy_per_atom,"
                        "energy_above_hull,total_magnetization,volume,density"
                    ),
                    "_limit": 1,
                },
                headers={"Accept": "application/json", "X-API-KEY": resolved_key},
                timeout=(10, 30),
            )
            if response.status_code != 200:
                return None
            payload = response.json()
            docs = payload.get("data", []) if isinstance(payload, dict) else []
            if docs and isinstance(docs[0], dict):
                return dict(docs[0])
        except Exception as e:
            logger.debug(f"Materials Project 查询失败: {e}")
        return None

    # ── Semantic Scholar API ──

    @staticmethod
    def query_paper(doi: str) -> Optional[dict]:
        """
        查询 Semantic Scholar 获取论文摘要/引用。

        用于参考文献深度验证（P1 预埋接口）。
        免费 API，无需 Key。
        返回: {"title": ..., "abstract": ..., "citationCount": ..., ...}
        """
        import requests
        try:
            url = f"https://api.semanticscholar.org/graph/v1/paper/DOI:{doi}"
            resp = requests.get(url, params={
                "fields": "title,abstract,citationCount,year,authors"
            }, timeout=10)
            if resp.status_code == 200:
                return resp.json()
        except Exception as e:
            logger.debug(f"Semantic Scholar 查询失败: {e}")
        return None

    @staticmethod
    def query_paper_by_title(title: str, year: int = 0) -> Optional[dict]:
        """
        通过标题关键词在 Semantic Scholar 搜索论文（无 DOI 回退方案）。

        当参考文献缺少 DOI 时，用标题进行模糊搜索，返回匹配度最高的论文信息。
        可选 year 参数提高搜索精度。

        返回: {"title": ..., "abstract": ..., "citationCount": ..., "year": ..., "paperId": ...}
              未找到时返回 None。
        """
        import requests
        try:
            url = "https://api.semanticscholar.org/graph/v1/paper/search"
            params = {
                "query": title,
                "fields": "title,abstract,citationCount,year,authors,externalIds",
                "limit": 5,
            }
            if year:
                params["year"] = f"{year}-{year}"

            resp = requests.get(url, params=params, timeout=15)
            if resp.status_code != 200:
                logger.debug(f"Semantic Scholar 标题搜索 HTTP {resp.status_code}")
                return None

            data = resp.json()
            papers = data.get("data", [])
            if not papers:
                return None

            # 选择匹配度最高的结果（优先选标题相似度高的）
            best = None
            best_score = 0.0
            title_lower = title.lower().strip()

            for paper in papers:
                paper_title = (paper.get("title") or "").lower().strip()
                if not paper_title:
                    continue

                # 简单的标题相似度：公共词占比
                title_words = set(title_lower.split())
                paper_words = set(paper_title.split())
                if not title_words:
                    continue
                common = title_words & paper_words
                score = len(common) / len(title_words)

                # 如果年份精确匹配，加分
                if year and paper.get("year") == year:
                    score += 0.1

                if score > best_score:
                    best_score = score
                    best = paper

            # 最低相似度阈值：标题至少有 40% 的词匹配
            if best and best_score >= 0.4:
                logger.info(f"标题搜索命中: '{best.get('title', '')}' (相似度={best_score:.2f})")
                return best

            return None
        except Exception as e:
            logger.debug(f"Semantic Scholar 标题搜索失败: {e}")
        return None

    # ── PubChem PUG REST ──

    @staticmethod
    def query_compound_properties(name: str) -> Optional[dict]:
        """
        查询 PubChem 获取化合物物理化学性质。

        返回: {"molecular_formula": ..., "molecular_weight": ..., ...}
        """
        import requests
        try:
            # 先获取 CID
            search_url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/{name}/cids/JSON"
            resp = requests.get(search_url, timeout=10)
            if resp.status_code != 200:
                return None
            cid = resp.json().get("IdentifierList", {}).get("CID", [None])[0]
            if not cid:
                return None

            # 获取性质
            prop_url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{cid}/property/MolecularFormula,MolecularWeight,InChI/JSON"
            resp = requests.get(prop_url, timeout=10)
            if resp.status_code == 200:
                props = resp.json().get("PropertyTable", {}).get("Properties", [{}])
                return props[0] if props else None
        except Exception as e:
            logger.debug(f"PubChem 查询失败: {e}")
        return None
