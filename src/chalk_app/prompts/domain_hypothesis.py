"""Domain-specific hypothesis prompts and output defaults.

The prompt text is intentionally long. The competition use case rewards
specific, auditable, experiment-ready hypotheses rather than short generic
summaries.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List


BATTERY_DATABASE_FIELDS = [
    "paper_id",
    "title",
    "DOI",
    "year",
    "journal",
    "battery_type",
    "ion_type",
    "cathode_material",
    "anode_material",
    "electrolyte",
    "separator",
    "additive",
    "synthesis_method",
    "electrode_loading",
    "voltage_window",
    "current_density",
    "specific_capacity",
    "capacity_retention",
    "cycle_number",
    "coulombic_efficiency",
    "rate_capability",
    "EIS_Rct",
    "safety_indicator",
    "key_mechanism",
    "limitation",
    "source_text",
    "reliability_level",
]

ELECTROCATALYSIS_DATABASE_FIELDS = [
    "paper_id",
    "title",
    "DOI",
    "year",
    "journal",
    "reaction_type",
    "catalyst_type",
    "catalyst_composition",
    "support_material",
    "synthesis_method",
    "active_site",
    "electrolyte",
    "pH",
    "temperature",
    "loading_amount",
    "overpotential",
    "current_density",
    "Tafel_slope",
    "ECSA",
    "TOF",
    "Faradaic_efficiency",
    "selectivity",
    "stability_time",
    "product_distribution",
    "DFT_descriptor",
    "key_intermediate",
    "rate_determining_step",
    "degradation_mechanism",
    "limitation",
    "source_text",
    "reliability_level",
]

BATTERY_EXPERIMENT_RECORD_FIELDS = [
    "hypothesis_id",
    "research_direction",
    "material_system",
    "hypothesis_statement",
    "source_references",
    "variables_to_test",
    "control_group",
    "experimental_group",
    "synthesis_route",
    "cell_assembly",
    "testing_protocol",
    "characterization_methods",
    "expected_metrics",
    "risk_points",
    "result_record",
    "conclusion",
    "next_optimization_step",
]

ELECTROCATALYSIS_EXPERIMENT_RECORD_FIELDS = [
    "hypothesis_id",
    "research_direction",
    "reaction_type",
    "catalyst_system",
    "hypothesis_statement",
    "source_references",
    "proposed_active_site",
    "variables_to_test",
    "control_group",
    "experimental_group",
    "synthesis_route",
    "electrode_preparation",
    "electrolyte_condition",
    "testing_protocol",
    "product_analysis",
    "characterization_methods",
    "expected_metrics",
    "risk_points",
    "result_record",
    "conclusion",
    "next_optimization_step",
]

BATTERY_REQUIRED_METRICS = [
    "specific capacity (mAh g^-1)",
    "cycle life",
    "capacity retention",
    "coulombic efficiency",
    "rate capability",
    "energy density",
    "safety",
    "charge-transfer resistance (Rct)",
    "thermal stability",
]

ELECTROCATALYSIS_REQUIRED_METRICS = [
    "overpotential",
    "Tafel slope",
    "current density",
    "stability",
    "Faradaic efficiency",
    "selectivity",
    "ECSA",
    "TOF",
    "mass activity",
    "product distribution",
    "performance at industrial current density",
]


BATTERY_GUIDANCE = """
【电池领域专精提示词】

你是一名化工、材料、电化学与数据科学交叉领域的科研助手。你的任务是基于用户输入的多篇真实文献、摘要、DOI、实验数据、数据库信息或实验记录，围绕“电池领域”生成可验证、可溯源、可进入实验记录的科研假设。你必须支持多文献共同分析，而不是逐篇孤立总结。

一、研究范围限定
本次研究只聚焦电池领域，不要泛泛讨论新能源材料。请优先从两个入口展开。

入口 1：从电极材料出发。
- 正极材料：高镍层状氧化物、磷酸铁锂、富锂锰基、钠电正极、硫正极等。
- 负极材料：石墨、硅碳负极、锂金属负极、硬碳、钠离子负极等。

入口 2：从传导离子出发。
- 锂离子电池、钠离子电池、钾离子电池、锌离子电池、固态电池、多价离子电池。

重点关注改进方向。
- 提高比容量、循环寿命、倍率性能、能量密度和安全性。
- 改善电极-电解液界面稳定性。
- 抑制枝晶、副反应、气体释放、过渡金属溶解、氧释放、SEI/CEI不稳定或结构坍塌。
- 优化电解液、添加剂、隔膜、包覆层、界面层或粘结剂体系。

二、输入内容处理
用户可能输入文献标题、DOI、摘要、实验方法、材料体系、性能数据、图表描述、数据库字段、实验记录或研究目标。你必须把多篇文献放在同一个证据池中进行关系分析。
如果某篇文献信息不完整，请标注“信息不足，需进一步核验”，不能补写不存在的结论。
如果无法获取全文，只能基于题名、摘要、公开元数据或用户提供片段分析，不能假装读过全文。
文献来源必须合规：优先用户导入PDF/片段、DOI和出版社公开元数据、Crossref、Semantic Scholar、OpenAlex、PubMed/PMC、DOAJ、arXiv、机构授权全文和实验室自建数据库。不得使用非授权来源获取全文；全文不足时应提示用户上传授权文件或补充摘要/图表。

补充要求：数据库证据作为假设种子。
如果输入中包含“自建数据库证据上下文”、literature_evidence、domain_evidence、computational_catalysis_evidence 或类似数据库命中记录，不要只把它们列为参考资料，而要把可溯源字段转化为具体假设起点。
- 优先引用证据ID、材料体系、数据集/来源、关键指标、测试条件和可靠性等级。
- 在 Problem Statement、Rationale、Datasets.source、Source、Methods、experiment_record_card.source_references 中明确写出数据库证据如何支撑假设。
- 对电池证据，优先使用 specific_capacity、capacity_retention、cycle_number、coulombic_efficiency、rate_capability、EIS_Rct、voltage_window、current_density、key_mechanism 等字段构造推导链。
- 示例表达：基于自建电池数据库中 High-Ni cathode 在 1C、300 cycles 后 capacity_retention=82% 且 EIS_Rct 从 45 Ω 增至 120 Ω，分析 CEI 稳定化添加剂对循环寿命或阻抗变化的假设价值。
- 如果数据库只给出目录项或证据不足，必须写“证据不足/需核验”，不能把目录项改写成已完成实验结果。

三、输出目标
你需要完成以下任务：
1. 从多篇文献中抽取结构化信息。
2. 构建适合自建数据库的字段。
3. 分析文献之间的关系网络。
4. 识别当前电池研究中的具体局限性。
5. 生成有逻辑推导链条的研究假设。
6. 给出可执行的实验方法。
7. 给出可量化的评价指标。
8. 将假设转化为实验记录模板。
9. 输出符合学术论文雏形的标题和摘要。
10. 列出真实参考文献，禁止虚构。

四、必须输出的十个字段
每个字段都必须有内容；证据不足时写“证据不足/需核验”，不能编造。

1. Problem Statement：待研究问题。
必须指出具体对象、具体机制和具体后果。不能写“性能有待提高”。示例：高镍层状正极在高电压循环中因表面氧释放、过渡金属溶解和CEI膜不稳定，导致阻抗升高、容量衰减和热安全风险增加。

2. Rationale：解决思路。
必须展示推导链条：材料结构变化 -> 界面副反应 -> 电荷转移阻抗变化 -> 容量衰减 -> 可调控变量 -> 假设方案。

3. Technical Details：必要技术手段。
必须给出电化学测试、材料表征、数据方法和可选深度学习方法。包括恒流充放电、CV、EIS、倍率性能、GITT、循环寿命、XRD、SEM、TEM、XPS、Raman、FTIR、ICP、DSC/TGA、PCA、聚类、随机森林、XGBoost、SHAP、贝叶斯优化、LSTM、Transformer、CNN或图神经网络。

4. Datasets：数据集。
可用真实合规来源包括 Battery Archive、NASA battery dataset、CALCE battery dataset、Materials Project、OQMD、NOMAD、文献手工提取数据表、实验室自建循环性能数据库。每个数据集说明用途、字段和限制。

5. Source：历史依据。
说明输入文献关键发现、数据库历史性能趋势、已报道材料体系、已知失效机制和已验证实验现象。每条依据尽量关联具体文献或数据来源。

6. Target：拟采集数据。
列出初始比容量、循环后容量保持率、库伦效率、倍率性能、EIS阻抗参数、Li+/Na+扩散系数、电极厚度、载量、孔隙率、SEM/TEM形貌特征、XPS界面组分、热稳定性数据等。

7. Paper Title：论文标题。
生成 3 个符合学术出版规范的英文标题，避免营销化表达。主标题写入 paper_title，三个候选写入 paper_titles。

8. Paper Abstract：论文摘要。
英文摘要必须包含背景、具体问题、方法、数据来源、假设、预期结果和学术意义。

9. Methods：方法论。
给出具体实施步骤：文献筛选、数据抽取、数据库字段构建、文献关系网络构建、假设生成、材料制备、电池组装、电化学测试、表征验证、机器学习建模、结果解释。

10. Experiments / Results / References：实验设计、结果与参考论文。
必须包括 Baselines、Metrics、Expected Results、Feasibility Check、References。结果部分必须把计算输出或公式推导与原文献值进行对比；没有足够数据时标注“证据不足，需补充文献值或实验值”。

五、文献关系网络
构建文字版文献关系网络：
- 核心综述文献：定义研究问题。
- 关键实验文献：提供材料体系和性能数据。
- 机制解释文献：解释失效或改性机理。
- 数据方法文献：支持建模方法。
- 产业或专利资料：说明实用价值。
说明关系：A文献支持B文献的机制解释；C文献与D文献使用相似材料但评价指标不同；E文献指出现有方法局限性；F文献为本假设提供实验基线。

六、自建数据库字段
必须输出 database_schema，字段至少包含：
paper_id, title, DOI, year, journal, battery_type, ion_type, cathode_material, anode_material, electrolyte, separator, additive, synthesis_method, electrode_loading, voltage_window, current_density, specific_capacity, capacity_retention, cycle_number, coulombic_efficiency, rate_capability, EIS_Rct, safety_indicator, key_mechanism, limitation, source_text, reliability_level。

七、实验记录输出
必须输出 experiment_record_card，字段至少包含：
hypothesis_id, research_direction, material_system, hypothesis_statement, source_references, variables_to_test, control_group, experimental_group, synthesis_route, cell_assembly, testing_protocol, characterization_methods, expected_metrics, risk_points, result_record, conclusion, next_optimization_step。

八、评价指标
电池方向必须至少包含：比容量 mAh g^-1、循环寿命、容量保持率、库伦效率、倍率性能、能量密度、安全性、电荷转移阻抗、热稳定性。

九、真实性约束
严禁虚构论文、DOI、数据集和实验结果。无法确认的论文标注“需核验”。没有足够证据支持假设标注“证据不足”。References 字段必须存在，但只能列真实、用户导入或白名单来源；如果没有可信引用，references 可为空，并在 reference_status 中说明原因和需要补充的文献类型。
"""


ELECTROCATALYSIS_GUIDANCE = """
【电催化领域专精提示词】

你是一名化工、电化学、电催化、材料科学与数据科学交叉领域的科研助手。你的任务是基于用户输入的多篇真实文献、摘要、DOI、实验数据、数据库信息或实验记录，围绕“电催化领域”生成可验证、可溯源、可进入实验记录的科研假设。你必须支持多文献共同分析，而不是单篇文献总结。

一、研究范围限定
本次研究只聚焦电催化领域，不要泛泛讨论新能源材料。请优先从“反应类型”出发，再分析材料、机理和选择性。

反应入口：
- HER：析氢反应。
- OER：析氧反应。
- ORR：氧还原反应。
- CO2RR：二氧化碳电还原。
- NRR：氮还原反应。
- 电催化有机氧化/还原反应。

材料入口：
- 合金材料、氧化物、氢氧化物、硫化物、磷化物、氮化物、碳基材料、单原子催化剂、MOF/COF衍生催化剂、缺陷工程材料、异质结构材料。

重点关注：
- 活性位点、中间体吸附能、电子结构调控、电荷转移、反应路径、结构重构、选择性来源、稳定性衰减机制和工业电流密度下性能表现。

二、输入内容处理
用户可能输入文献标题、DOI、摘要、催化剂组成、合成方法、反应类型、电解液、过电位、Tafel斜率、法拉第效率、选择性、稳定性、DFT计算结果、图表描述或实验目标。你必须把多篇文献放在同一个证据池中，构建证据链。
如果某篇文献信息不完整，请标注“信息不足，需进一步核验”，不能补写不存在的结论。
如果无法获取全文，只能基于题名、摘要、公开元数据或用户提供片段分析，不能假装读过全文。
文献来源必须合规：优先用户导入PDF/片段、DOI和出版社公开元数据、Crossref、Semantic Scholar、OpenAlex、PubMed/PMC、DOAJ、arXiv、机构授权全文和实验室自建数据库。不得使用非授权来源获取全文；全文不足时应提示用户上传授权文件或补充摘要/图表。

补充要求：数据库证据作为假设种子。
如果输入中包含“自建数据库证据上下文”、literature_evidence、domain_evidence、computational_catalysis_evidence 或类似数据库命中记录，不要只把它们列为参考资料，而要把可溯源字段转化为具体假设起点。
- 优先引用证据ID、材料体系、数据集/来源、反应类型、关键中间体、吸附能/DFT能量、测试条件和可靠性等级。
- 在 Problem Statement、Rationale、Datasets.source、Source、Methods、experiment_record_card.source_references 中明确写出数据库证据如何支撑假设。
- 对 OCP、FAIR-Chem、OC20、OC20-Dense、OC22、OC25、ODAC23 等计算证据，只能作为结构-能量-吸附描述符、模型预测或候选筛选线索，不得直接等同于实验性能，不得改写成实验过电位、Tafel 斜率或法拉第效率。
- 示例表达：基于 OCP OC20-Dense 中 *O 吸附能，分析 mp-1219797 在 OER/ORR 吸附能描述符上的假设价值，并在后续实验中用 LSV、Tafel、EIS 和稳定性测试验证。
- 如果数据库只给出 catalog/source_catalog 目录项，要明确标注“数据源线索，尚非具体计算条目”；如果证据不足，必须写“证据不足/需核验”。

三、输出目标
你需要完成以下任务：
1. 从多篇文献中提取催化反应、材料体系、性能指标和机理信息。
2. 构建适合自建数据库的电催化字段。
3. 构建文献关系网络，体现不同文献之间的证据链。
4. 明确当前电催化研究中的具体局限性。
5. 从反应机理出发生成科研假设。
6. 给出可执行实验方案。
7. 给出评价指标和对照组。
8. 将假设转化为实验记录模板。
9. 输出符合学术论文规范的标题和摘要。
10. 列出真实参考文献，禁止虚构。

四、必须输出的十个字段
每个字段都必须有内容；证据不足时写“证据不足/需核验”，不能编造。

1. Problem Statement：待研究问题。
必须具体到某一反应、某一类材料、某一关键中间体、某一性能瓶颈和某一工程化限制。示例：碱性HER中非贵金属催化剂常因水解离动力学缓慢和H*吸附能不匹配，导致低电流密度下活性尚可，但在工业电流密度下稳定性和能效不足。

2. Rationale：解决思路。
必须展示推导链条：反应路径 -> 速率决定步骤 -> 中间体吸附 -> 电子结构调控 -> 材料设计 -> 性能预测 -> 实验验证。

3. Technical Details：必要技术手段。
必须覆盖电化学测试、催化评价、材料表征、机理分析、理论计算和数据方法。包括 LSV、CV、Tafel、EIS、chronoamperometry、chronopotentiometry、过电位、电流密度、法拉第效率、选择性、TOF、ECSA、质量活性、XRD、SEM、TEM、XPS、Raman、FTIR、BET、ICP、原位/准原位XPS、原位Raman、原位FTIR、DEMS、DFT、吸附能计算、d-band center、自由能图、随机森林、XGBoost、SHAP、贝叶斯优化、图神经网络和主动学习。

4. Datasets：数据集。
可用真实合规来源包括 Materials Project、OQMD、NOMAD、Catalysis-Hub、Open Catalyst Project、文献手工提取电催化数据库、实验室自建催化性能数据库。每个数据集需说明用途、字段和限制。

5. Source：历史依据。
说明输入文献关键实验结果、已报道反应路径、已知活性位点、DFT计算结论、已报道性能指标和已知失效机制。每条依据尽量关联具体文献或数据来源。

6. Target：拟采集数据。
列出催化剂组成、晶相结构、粒径、比表面积、缺陷浓度、电解液类型、pH、过电位、Tafel斜率、ECSA、TOF、稳定性、法拉第效率、产物分布、选择性、反应前后结构变化。

7. Paper Title：论文标题。
生成 3 个符合学术出版规范的英文标题，避免夸张表达。主标题写入 paper_title，三个候选写入 paper_titles。

8. Paper Abstract：论文摘要。
英文摘要必须包含背景、具体反应、研究问题、材料体系、方法、数据来源、预期结果和学术意义。

9. Methods：方法论。
给出具体实施步骤：文献筛选、数据抽取、数据库构建、文献关系网络构建、反应机理分析、假设生成、催化剂合成、电极制备、电化学测试、产物分析、原位表征、DFT或机器学习建模、结果解释。

10. Experiments / Results / References：实验设计、结果与参考论文。
必须包括 Baselines、Metrics、Expected Results、Feasibility Check、References。结果部分必须把计算输出或公式推导与原文献值进行对比；没有足够数据时标注“证据不足，需补充文献值或实验值”。

五、文献关系网络
构建文字版文献关系网络：
- 反应机制核心文献：定义反应路径和速率决定步骤。
- 材料设计文献：提供催化剂组成和结构调控策略。
- 性能评价文献：提供过电位、Tafel斜率、稳定性等数据。
- 理论计算文献：提供吸附能、自由能路径、电子结构解释。
- 数据方法文献：支持机器学习或高通量筛选。
- 工业化相关文献：说明大电流密度、耐久性和放大应用问题。
说明关系：A文献提出机理；B文献验证类似材料体系；C文献指出稳定性不足；D文献提供DFT证据；E文献可作为实验基线；F文献支持评价指标选择。

六、自建数据库字段
必须输出 database_schema，字段至少包含：
paper_id, title, DOI, year, journal, reaction_type, catalyst_type, catalyst_composition, support_material, synthesis_method, active_site, electrolyte, pH, temperature, loading_amount, overpotential, current_density, Tafel_slope, ECSA, TOF, Faradaic_efficiency, selectivity, stability_time, product_distribution, DFT_descriptor, key_intermediate, rate_determining_step, degradation_mechanism, limitation, source_text, reliability_level。

七、实验记录输出
必须输出 experiment_record_card，字段至少包含：
hypothesis_id, research_direction, reaction_type, catalyst_system, hypothesis_statement, source_references, proposed_active_site, variables_to_test, control_group, experimental_group, synthesis_route, electrode_preparation, electrolyte_condition, testing_protocol, product_analysis, characterization_methods, expected_metrics, risk_points, result_record, conclusion, next_optimization_step。

八、评价指标
电催化方向必须至少包含：过电位、Tafel斜率、电流密度、稳定性、法拉第效率、选择性、ECSA、TOF、质量活性、产物分布、工业电流密度下性能。

九、真实性约束
严禁虚构论文、DOI、数据集和实验结果。无法确认的论文标注“需核验”。没有足够证据支持假设标注“证据不足”。References 字段必须存在，但只能列真实、用户导入或白名单来源；如果没有可信引用，references 可为空，并在 reference_status 中说明原因和需要补充的文献类型。
"""


_DOMAIN_ALIASES = {
    "battery": "battery",
    "battery_materials": "battery",
    "batteries": "battery",
    "电池": "battery",
    "电池材料": "battery",
    "electrocatalysis": "electrocatalysis",
    "electrocatalyst": "electrocatalysis",
    "电催化": "electrocatalysis",
}


def normalize_domain(domain: str) -> str:
    text = str(domain or "").strip().lower()
    return _DOMAIN_ALIASES.get(text, "")


def domain_label(domain: str) -> str:
    key = normalize_domain(domain)
    if key == "battery":
        return "电池"
    if key == "electrocatalysis":
        return "电催化"
    return str(domain or "科学研究")


def domain_guidance(domain: str) -> str:
    key = normalize_domain(domain)
    if key == "battery":
        return BATTERY_GUIDANCE
    if key == "electrocatalysis":
        return ELECTROCATALYSIS_GUIDANCE
    return ""


def domain_database_fields(domain: str) -> List[str]:
    key = normalize_domain(domain)
    if key == "battery":
        return list(BATTERY_DATABASE_FIELDS)
    if key == "electrocatalysis":
        return list(ELECTROCATALYSIS_DATABASE_FIELDS)
    return []


def domain_metrics(domain: str) -> List[str]:
    key = normalize_domain(domain)
    if key == "battery":
        return list(BATTERY_REQUIRED_METRICS)
    if key == "electrocatalysis":
        return list(ELECTROCATALYSIS_REQUIRED_METRICS)
    return []


def domain_output_contract(domain: str) -> str:
    key = normalize_domain(domain)
    if not key:
        return ""
    fields = domain_database_fields(key)
    metrics = domain_metrics(key)
    record_fields = (
        BATTERY_EXPERIMENT_RECORD_FIELDS
        if key == "battery"
        else ELECTROCATALYSIS_EXPERIMENT_RECORD_FIELDS
    )
    return (
        "\n【领域输出契约】\n"
        f"当前专精方向：{domain_label(key)}。\n"
        "最终 JSON 除十个比赛字段外，还必须包含：\n"
        "1. structured_extraction_table: 多文献结构化提取表，按文献列出材料/反应/数据/机制/局限/证据状态。\n"
        "2. database_schema: 自建数据库表设计，必须覆盖以下字段："
        + ", ".join(fields)
        + "。\n"
        "3. literature_network: 文字版文献关系网络，包含节点分组和 evidence_edges。\n"
        "4. paper_titles: 3 个英文学术标题候选，paper_title 使用其中最推荐的一个。\n"
        "5. experiment_record_card: 可直接进入实验记录的假设卡片，必须覆盖以下字段："
        + ", ".join(record_fields)
        + "。\n"
        "6. experiments.metrics 必须至少覆盖这些评价指标："
        + ", ".join(metrics)
        + "。\n"
        "7. reference_status: 说明引用来源、白名单匹配、需核验项和缺失全文的合规补充方式。\n"
    )


def _blank_record(fields: List[str]) -> Dict[str, str]:
    return {field: "" for field in fields}


def _ensure_list(value: Any) -> List[Any]:
    if isinstance(value, list):
        return value
    if value in (None, ""):
        return []
    return [value]


def apply_domain_output_defaults(data: Dict[str, Any], domain: str) -> Dict[str, Any]:
    """Ensure domain-specific report sections exist without inventing evidence."""
    if not isinstance(data, dict):
        return data

    key = normalize_domain(domain or data.get("_domain", ""))
    if not key:
        return data

    data["_domain"] = domain_label(key)
    data.setdefault("structured_extraction_table", [])

    if key == "battery":
        table_name = "battery_literature_evidence"
        database_fields = BATTERY_DATABASE_FIELDS
        record_fields = BATTERY_EXPERIMENT_RECORD_FIELDS
        metrics = BATTERY_REQUIRED_METRICS
        network_categories = [
            "核心综述文献",
            "关键实验文献",
            "机制解释文献",
            "数据方法文献",
            "产业或专利资料",
        ]
    else:
        table_name = "electrocatalysis_literature_evidence"
        database_fields = ELECTROCATALYSIS_DATABASE_FIELDS
        record_fields = ELECTROCATALYSIS_EXPERIMENT_RECORD_FIELDS
        metrics = ELECTROCATALYSIS_REQUIRED_METRICS
        network_categories = [
            "反应机制核心文献",
            "材料设计文献",
            "性能评价文献",
            "理论计算文献",
            "数据方法文献",
            "工业化相关文献",
        ]

    existing_schema = data.get("database_schema")
    if not isinstance(existing_schema, dict):
        existing_schema = {}
    existing_schema.setdefault("table_name", table_name)
    existing_schema.setdefault(
        "purpose",
        f"存储{domain_label(key)}方向多文献证据、性能指标、机制解释和可靠性等级。",
    )
    existing_fields = existing_schema.get("fields", [])
    if not isinstance(existing_fields, list) or not existing_fields:
        existing_schema["fields"] = [
            {"name": field, "description": "", "required": True}
            for field in database_fields
        ]
    else:
        names = {
            item.get("name") if isinstance(item, dict) else str(item)
            for item in existing_fields
        }
        for field in database_fields:
            if field not in names:
                existing_fields.append({"name": field, "description": "", "required": True})
        existing_schema["fields"] = existing_fields
    existing_schema.setdefault(
        "compliance_note",
        "source_text 只能来自用户导入内容、公开元数据、开放全文或机构授权全文；不可使用非授权全文来源。",
    )
    data["database_schema"] = existing_schema

    network = data.get("literature_network")
    if not isinstance(network, dict):
        network = {}
    network.setdefault("node_groups", {name: [] for name in network_categories})
    network.setdefault("evidence_edges", [])
    network.setdefault(
        "network_text",
        "请在导入更多文献后，将综述、实验、机制、数据方法和产业资料按证据链连接；信息不足的边标注“需核验”。",
    )
    data["literature_network"] = network

    record = data.get("experiment_record_card")
    if not isinstance(record, dict):
        record = _blank_record(record_fields)
    else:
        record = deepcopy(record)
        for field in record_fields:
            record.setdefault(field, "")
    record.setdefault("hypothesis_statement", data.get("rationale", ""))
    if not record.get("hypothesis_statement"):
        record["hypothesis_statement"] = data.get("problem_statement", "")
    refs = data.get("references", [])
    if refs and not record.get("source_references"):
        record["source_references"] = [
            ref.get("doi") or ref.get("title") or str(ref)
            for ref in refs
            if isinstance(ref, dict)
        ][:8]
    if not record.get("expected_metrics"):
        record["expected_metrics"] = metrics
    data["experiment_record_card"] = record

    experiments = data.get("experiments")
    if not isinstance(experiments, dict):
        experiments = {}
    existing_metrics = [str(item) for item in _ensure_list(experiments.get("metrics"))]
    lower_existing = " | ".join(existing_metrics).lower()
    for metric in metrics:
        if metric.lower() not in lower_existing:
            existing_metrics.append(metric)
    experiments["metrics"] = existing_metrics
    data["experiments"] = experiments

    titles = data.get("paper_titles")
    if not isinstance(titles, list):
        titles = []
    main_title = data.get("paper_title")
    if main_title and main_title not in titles:
        titles.insert(0, main_title)
    data["paper_titles"] = titles[:3]

    if "reference_status" not in data:
        if data.get("references"):
            data["reference_status"] = "References were assembled from user-provided sources, metadata search, or citation whitelist; any incomplete entry should be verified before submission."
        else:
            data["reference_status"] = "No verified reference is available yet; add user-uploaded papers, DOI metadata, abstracts, or authorized full text before final submission."

    return data
