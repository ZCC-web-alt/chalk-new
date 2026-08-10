from __future__ import annotations

import copy
import hashlib
import json
import unicodedata
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BENCHMARK_DIR = PROJECT_ROOT / "benchmarks" / "science125"
MANIFEST_PATH = BENCHMARK_DIR / "science125-v1.json"
ROUTING_PATH = BENCHMARK_DIR / "science125-routing-v1.json"
OUTPUT_PATH = BENCHMARK_DIR / "science125-prompts-v2.json"


DOMAIN_MODULES = {
    "Mathematical Sciences": (
        "先固定定义域、公理与假设，列出定理依赖、必要引理、boundary cases、counterexamples 和逐项证明义务。"
        "形式化验证（formal verification）应说明证明助手、形式系统与可复核证书；数值探索只能形成猜想或经验证数值界，"
        "不得替代 theorems、definitions 或 proof。"
    ),
    "Chemistry": (
        "明确组成、化学物种及其状态、reaction conditions、质量与电荷守恒、thermodynamics、kinetics、"
        "structural characterization、选择性、降解与副反应。记录校准、单位、重复性和 safety；"
        "严格区分 measured observations、computational predictions 与机制解释，不默认 electrocatalysis。"
    ),
    "Medicine & Health": (
        "按 guidelines、systematic reviews、RCT 和 cohort 的证据等级组织证据，以 PICO/PECO 明确人群、干预或暴露、"
        "比较和结局。说明纳排标准、效应量、样本量、偏倚、混杂、随访、临床终点、ethics 和 safety endpoints。"
    ),
    "Biology": (
        "建立分子、细胞、个体、种群的 multi-level 证据链，要求 functional perturbation、genetic/evolution 对照、"
        "生物学重复、表型操作性定义和检测限。明确 cross-species 外推边界，不把相关性写成因果。"
    ),
    "Astronomy": (
        "要求 calibrated observations、selection effects/selection function、巡天范围、sensitivity limits、时间采样、"
        "multi-wavelength/multi-messenger 联合约束和竞争模型。非探测只能给出对应参数域内的 upper limit。"
    ),
    "Physics": (
        "逐项检查 conserved quantities、symmetries、units 和量纲一致性，固定参数范围、calibration、"
        "系统与统计 uncertainty budgets。零结果应表述为排除区间，并声明理论 applicability domain。"
    ),
    "Engineering & Materials Science": (
        "使用 common baselines 和统一测试协议，量化需求指标、lifetime、reliability、failure modes、"
        "manufacturability、制造一致性、规模化、cost、维护及 safety acceptance thresholds。"
    ),
    "Information Science": (
        "固定任务；Version data and software 并记录数据来源；设置 strong baselines，防止 leakage，执行 ablations、"
        "error analysis 和 external reproducibility。报告 compute、latency、storage、security 与资源约束。"
    ),
    "Neuroscience": (
        "分别操作化 neural and behavioral endpoints，优先 preregister，控制运动、任务与测量混杂并校正多重比较。"
        "因果结论需要 causal perturbation 或独立复现；禁止 reverse inference。"
    ),
    "Ecology": (
        "明确 spatial/temporal scale、重复站点与季节、detectability 和 environmental covariates，采用准实验对照与"
        "外部地点验证。局部结果只能支持 local boundary 内的结论，局部零结果不得外推为全球零效应。"
    ),
    "Energy Science": (
        "固定系统边界并执行 energy and mass conservation，在标准条件下报告 efficiency、lifetime、衰减、资源约束、"
        "lifecycle 影响、并网约束和 safety stopping rules。"
    ),
    "Artificial Intelligence": (
        "Define the task operationally，给出 operational definition、数据版本和 held-out 测试，检验分布偏移、robustness、calibration、fairness、"
        "external replication、算力与 energy use。不得从 language performance 或拟人化表达推断 consciousness。"
    ),
}


DOMAIN_EVIDENCE = {
    "Mathematical Sciences": "同行评议证明、可追溯引理、形式化证书与针对边界情形的反例搜索",
    "Chemistry": "原始谱学/显微/散射数据、定量反应条件、重复实验及经验证的计算化学结果",
    "Medicine & Health": "指南、系统综述、注册试验、RCT、队列与安全性监测数据",
    "Biology": "功能扰动、遗传或进化对照、多层级表型和独立生物学重复",
    "Astronomy": "带仪器响应和选择函数的巡天目录、标定观测、多波段或多信使数据",
    "Physics": "可校准实验、单位完整的数据、误差预算、独立装置复现及受约束理论推导",
    "Engineering & Materials Science": "统一基准测试、寿命与失效数据、制造批次差异、规模化和安全验证",
    "Information Science": "版本化数据集与代码、强基线、外部测试集、消融和资源测量",
    "Neuroscience": "预注册神经与行为数据、因果扰动、混杂控制和独立样本复现",
    "Ecology": "跨站点跨季节重复观测、可检测性校正、环境协变量和准实验数据",
    "Energy Science": "统一系统边界下的能量/物质流、效率、寿命、生命周期和安全数据",
    "Artificial Intelligence": "held-out 与分布外评测、校准/公平/鲁棒性结果、外部复现及能耗测量",
}


METHOD_REQUIREMENTS = {
    "proof": "形式化陈述、证明义务、边界构造与反例搜索",
    "experimental": "可操纵自变量、匹配对照、盲法或随机化、校准测量和重复",
    "observational": "采样框、选择函数、混杂变量、不确定度和独立观测",
    "clinical": "PICO/PECO、纳排标准、主要终点、效应量、功效、随访与安全监测",
    "engineering": "需求指标、统一基线、设计矩阵、失效分析、成本与验收阈值",
    "computational": "数据/代码/模型版本、强基线、消融、防泄漏和外部复现",
    "systems_policy": "系统边界、情景、价值假设、利益相关方、敏感性和分配效应",
}


METHOD_TESTS = {
    "proof": "构造能区分命题成立、仅在附加假设下成立或存在反例的证明义务与机器可检验证书",
    "experimental": "预先规定能让竞争机制产生不同方向或不同量级预测的干预、对照和停止阈值",
    "observational": "设计能使竞争模型在空间、时间、频段或亚群上产生可区分预测的观测与留出检验",
    "clinical": "预先规定主要终点、最小临床重要差异、亚组和安全停止界限以区分获益、无效与伤害",
    "engineering": "在统一工况下设置基线、应力矩阵和验收阈值，区分性能增益、权衡和失效",
    "computational": "使用 held-out 数据、强基线、消融和外部复现区分真实增益、泄漏与过拟合",
    "systems_policy": "用多个可审计情景和敏感性分析区分技术约束、行为响应与价值选择",
}


METHOD_OBSERVABLES = {
    "proof": "列出形式对象、量词、参数域、假设强度、关键引理、证明义务和可构造反例的搜索边界；不得套用实验单位或检测限",
    "experimental": "明确可操纵自变量、响应变量、单位、校准链、采样窗口、检测限、重复数和系统/统计不确定度",
    "observational": "明确目标总体、采样框、时间/空间/频段窗口、直接观测量、代理量、选择函数、灵敏度和不确定度",
    "clinical": "明确人群、干预或暴露、比较、主要/次要终点、效应量、最小临床重要差异、随访窗口和安全终点",
    "engineering": "明确输入工况、性能指标、资源消耗、寿命、可靠性、成本、安全裕度和可量化验收阈值",
    "computational": "明确任务输入输出、数据切分、模型/代码版本、基线指标、校准误差、算力、延迟、存储和能耗",
    "systems_policy": "明确系统边界、情景参数、行为响应、资源流、分配效应、时间尺度和可观测政策结果",
}


METHOD_CONTROLS = {
    "proof": "与已知定理、弱化假设下的命题、极端参数和候选反例族比较，并逐项核验依赖引理",
    "experimental": "设置阴性/阳性、空白、假处理、剂量或条件梯度及独立重复；需要时采用随机化和盲法",
    "observational": "设置时空或人群匹配基线、负对照、竞争模型、留出样本和独立仪器/数据集",
    "clinical": "使用适当标准治疗、安慰或替代暴露比较，控制基线风险、混杂、失访和共同干预",
    "engineering": "使用现行最佳方案和统一工况基线，设置应力、寿命、失效与规模化测试矩阵",
    "computational": "比较强基线、简单模型、消融、随机/多数类基线、时间外或机构外留出集",
    "systems_policy": "比较现状、无政策、替代政策和多组参数情景，并显式分离事实预测与价值权重",
}


METHOD_FAILURES = {
    "proof": "未证明引理、循环论证、量词交换、隐藏正则性假设、遗漏边界情形、形式化器与手工证明不一致",
    "experimental": "校准漂移、批次效应、污染、仪器饱和、低功效、不可重复和干预未真正改变目标变量",
    "observational": "选择偏差、不可检测区间、代理量失真、未测混杂、时间覆盖不足和模型不可识别",
    "clinical": "选择偏差、混杂、失访、终点替换、多重比较、功效不足、依从性差和安全信号",
    "engineering": "实验室到规模化失真、短期指标替代寿命、制造波动、共因失效、成本遗漏和安全裕度不足",
    "computational": "数据泄漏、分布偏移、过拟合、基线过弱、指标投机、随机种子敏感和外部复现失败",
    "systems_policy": "系统边界遗漏、反弹效应、参数不可识别、行为适应、分配伤害和价值假设伪装成事实",
}


METHOD_SCOPE = {
    "proof": "形式系统、假设集合、参数域和已验证证明义务",
    "experimental": "实验对象、材料或生物体系、反应/环境条件、尺度和测量窗口",
    "observational": "实际采样总体、巡天或地点、时间范围、选择函数和灵敏度覆盖区间",
    "clinical": "纳入人群、医疗场景、干预剂量、比较方案、随访期和终点定义",
    "engineering": "测试工况、制造路线、规模、寿命区间、成本口径和安全标准",
    "computational": "数据分布、任务定义、版本化实现、算力预算和评测协议",
    "systems_policy": "声明的系统边界、利益相关方、情景、地区、时期和价值假设",
}


DOMAIN_CHECKS = {
    "Mathematical Sciences": ("operational_definition", "applicability_boundary", "negative_evidence"),
    "Chemistry": ("measurement_plan", "source_traceability", "replication", "safety_boundary"),
    "Medicine & Health": ("source_traceability", "selection_effects", "safety_boundary", "replication"),
    "Biology": ("measurement_plan", "replication", "applicability_boundary", "negative_evidence"),
    "Astronomy": ("selection_effects", "uncertainty_budget", "measurement_plan", "negative_evidence"),
    "Physics": ("uncertainty_budget", "measurement_plan", "applicability_boundary", "negative_evidence"),
    "Engineering & Materials Science": ("measurement_plan", "replication", "safety_boundary", "applicability_boundary"),
    "Information Science": ("data_leakage", "replication", "measurement_plan", "source_traceability"),
    "Neuroscience": ("operational_definition", "selection_effects", "replication", "negative_evidence"),
    "Ecology": ("selection_effects", "uncertainty_budget", "replication", "applicability_boundary"),
    "Energy Science": ("measurement_plan", "uncertainty_budget", "safety_boundary", "applicability_boundary"),
    "Artificial Intelligence": ("operational_definition", "data_leakage", "replication", "safety_boundary"),
}


METHOD_CHECKS = {
    "proof": ("operational_definition", "applicability_boundary", "negative_evidence"),
    "experimental": ("measurement_plan", "replication", "safety_boundary", "negative_evidence"),
    "observational": ("selection_effects", "uncertainty_budget", "negative_evidence"),
    "clinical": ("source_traceability", "selection_effects", "replication", "safety_boundary", "uncertainty_budget"),
    "engineering": ("measurement_plan", "replication", "safety_boundary", "applicability_boundary"),
    "computational": ("data_leakage", "replication", "measurement_plan", "applicability_boundary"),
    "systems_policy": ("operational_definition", "uncertainty_budget", "safety_boundary", "applicability_boundary"),
}


def _canonical_json(payload: dict[str, Any]) -> str:
    return unicodedata.normalize(
        "NFC",
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
    )


def _content_hash(payload: dict[str, Any], field: str) -> str:
    value = copy.deepcopy(payload)
    value.pop(field, None)
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _module_for(question: dict[str, Any], route: dict[str, Any]) -> dict[str, Any]:
    question_id = str(question["id"])
    question_text = str(question["question"])
    domain = str(route["benchmarkDomain"])
    subdomain = str(route["primarySubdomain"])
    topic = subdomain.split(".", maxsplit=1)[-1].replace("_", " ")
    primary_method = str(route["methodProfile"]["primary"])
    secondary_methods = [str(item) for item in route["methodProfile"].get("secondary", [])]
    method_names = [primary_method, *secondary_methods]
    method_text = "；".join(METHOD_REQUIREMENTS[name] for name in method_names)
    required_checks = list(DOMAIN_CHECKS[domain])
    for method_name in method_names:
        for check in METHOD_CHECKS[method_name]:
            if check not in required_checks:
                required_checks.append(check)
    tags = ", ".join(str(item) for item in route.get("crossDomainTags", [])) or "无额外跨域标签"
    module = {
        "questionId": question_id,
        "primarySubdomain": subdomain,
        "researchObjective": (
            f"围绕原题“{question_text}”，在 {subdomain} 范围内把宽泛疑问拆成可证伪的竞争假设；"
            "不得把研究目标改写成预设结论。"
        ),
        "requiredConcepts": (
            f"定义原题中的核心名词和 {topic} 的操作性含义，明确研究对象、空间/时间/人群尺度及适用域；主方法为 {primary_method}，"
            f"跨域标签为 {tags}，跨域概念必须说明映射关系。"
        ),
        "evidenceRequirements": (
            f"优先使用与“{question_text}”直接相关的{DOMAIN_EVIDENCE[domain]}；"
            "每项关键主张必须绑定已审核 sourceRef，并区分全文证据、元数据线索和缺失证据。"
        ),
        "variablesAndObservables": (
            f"为 {subdomain} {METHOD_OBSERVABLES[primary_method]}；"
            f"这些观测量必须能够回答“{question_text}”而不是仅作主题相似描述。"
        ),
        "comparatorsAndControls": (
            f"针对“{question_text}”至少设置一个零效应基线和一个竞争解释；{METHOD_CONTROLS[primary_method]}。"
            f"次方法补充约束为 {method_text}；"
            "不得用同源重复记录冒充独立验证。"
        ),
        "discriminatingTests": (
            f"{METHOD_TESTS[primary_method]}；给出支持、反驳和证据不足三种判定规则，且阈值必须在观察结果前确定。"
        ),
        "negativeEvidenceAndFailureModes": (
            f"主动检索与 {subdomain} 主流解释冲突的结果，并检查{METHOD_FAILURES[primary_method]}；"
            "说明每种失败是否反驳假设，还是仅降低可识别性。"
        ),
        "scopeBoundaries": (
            f"结论仅限于证据覆盖的 {subdomain}：{METHOD_SCOPE[primary_method]}；"
            "不得从局部样本、单一模型或短期结果外推到原题的普遍答案。"
        ),
        "forbiddenInferences": (
            "禁止伪造引用、把相关性当因果、把无证据当反证、把计算输出当实测、把代理指标当目标本身，"
            "以及超出审核证据和方法识别能力的确定性陈述。"
        ),
        "requiredDomainChecks": required_checks,
        "reviewStatus": "draft_pending_review",
    }
    module["moduleSha256"] = _content_hash(module, "moduleSha256")
    return module


def build_registry() -> dict[str, Any]:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    routing = json.loads(ROUTING_PATH.read_text(encoding="utf-8"))
    questions = manifest["questions"]
    routes = routing["questions"]
    if [item["id"] for item in questions] != [item["questionId"] for item in routes]:
        raise RuntimeError("Science 125 manifest and routing order differ.")
    registry = {
        "promptVersion": "science125-prompts-v2",
        "baseManifestVersion": manifest["manifestVersion"],
        "baseManifestContentSha256": manifest["manifestContentSha256"],
        "routingVersion": routing["routingVersion"],
        "routingContentSha256": routing["routingContentSha256"],
        "domainModules": DOMAIN_MODULES,
        "questionModules": [
            _module_for(question, route)
            for question, route in zip(questions, routes, strict=True)
        ],
    }
    registry["registryContentSha256"] = _content_hash(registry, "registryContentSha256")
    return registry


def main() -> None:
    registry = build_registry()
    OUTPUT_PATH.write_text(
        json.dumps(registry, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {OUTPUT_PATH} ({registry['registryContentSha256']})")


if __name__ == "__main__":
    main()
