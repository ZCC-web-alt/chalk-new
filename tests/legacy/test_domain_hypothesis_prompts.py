# -*- coding: utf-8 -*-
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent_framework import HypothesisResult, build_score_breakdown
from prompts.domain_hypothesis import (
    apply_domain_output_defaults,
    domain_guidance,
    domain_output_contract,
)
from report_renderer import HTMLReportRenderer


def test_battery_guidance_and_defaults_are_experiment_ready():
    guidance = domain_guidance("battery_materials")
    contract = domain_output_contract("battery_materials")

    assert "电池领域" in guidance
    assert "Battery Archive" in guidance
    assert "比容量" in guidance
    assert "不得使用非授权来源" in guidance
    assert "structured_extraction_table" in contract
    assert "experiment_record_card" in contract

    data = apply_domain_output_defaults({}, "battery_materials")

    assert data["_domain"] == "电池"
    field_names = {field["name"] for field in data["database_schema"]["fields"]}
    assert "specific_capacity" in field_names
    assert "capacity_retention" in field_names
    assert "EIS_Rct" in field_names
    assert "source_text" in field_names
    assert "specific capacity (mAh g^-1)" in data["experiments"]["metrics"]
    assert "thermal stability" in data["experiments"]["metrics"]
    assert "cell_assembly" in data["experiment_record_card"]
    assert "No verified reference" in data["reference_status"]


def test_electrocatalysis_guidance_and_defaults_are_reaction_first():
    guidance = domain_guidance("electrocatalysis")
    contract = domain_output_contract("电催化")

    assert "电催化领域" in guidance
    assert "HER" in guidance
    assert "CO2RR" in guidance
    assert "工业电流密度" in guidance
    assert "reaction_type" in contract
    assert "Faradaic_efficiency" in contract

    data = apply_domain_output_defaults({}, "electrocatalysis")

    assert data["_domain"] == "电催化"
    field_names = {field["name"] for field in data["database_schema"]["fields"]}
    assert "reaction_type" in field_names
    assert "catalyst_composition" in field_names
    assert "overpotential" in field_names
    assert "rate_determining_step" in field_names
    assert "overpotential" in data["experiments"]["metrics"]
    assert "performance at industrial current density" in data["experiments"]["metrics"]
    assert "electrode_preparation" in data["experiment_record_card"]
    assert "product_analysis" in data["experiment_record_card"]


def test_domain_guidance_turns_database_records_into_hypothesis_seeds():
    battery = domain_guidance("battery_materials")
    electrocatalysis = domain_guidance("electrocatalysis")

    assert "数据库证据作为假设种子" in battery
    assert "capacity_retention" in battery
    assert "EIS_Rct" in battery
    assert "循环寿命或阻抗变化的假设价值" in battery
    assert "数据库证据作为假设种子" in electrocatalysis
    assert "OCP OC20-Dense" in electrocatalysis
    assert "*O 吸附能" in electrocatalysis
    assert "mp-1219797" in electrocatalysis
    assert "OER/ORR 吸附能描述符" in electrocatalysis
    assert "不得直接等同于实验性能" in electrocatalysis


def test_report_renderer_uses_three_major_blocks_and_domain_sections():
    data = apply_domain_output_defaults(
        {
            "paper_title": "Domain-Guided Battery Hypothesis",
            "paper_titles": [
                "Interface Stabilization for High-Nickel Cathodes",
                "Data-Guided Electrolyte Additive Design",
                "Mechanistic Evaluation of CEI Stability",
            ],
            "paper_abstract": "Background: test. Methods: test. Expected results: test.",
            "problem_statement": "高镍正极界面副反应导致阻抗升高和容量衰减。",
            "rationale": "结构变化 -> 界面副反应 -> Rct升高 -> 容量衰减 -> 添加剂调控。",
            "technical_details": "恒流充放电、EIS、XPS、XGBoost、SHAP。",
            "methods": "文献筛选、数据抽取、数据库构建、实验验证。",
            "datasets": {
                "source": "用户导入文献和Battery Archive。",
                "target": "比容量、容量保持率、EIS_Rct、XPS界面组分。",
            },
            "structured_extraction_table": [
                {
                    "paper_id": "P1",
                    "title": "Verified paper",
                    "materials_or_reaction": "High-Ni cathode",
                    "key_data": "capacity retention",
                    "mechanism": "CEI instability",
                    "limitation": "needs verification",
                    "evidence_status": "需核验",
                }
            ],
            "literature_network": {
                "node_groups": {"核心综述文献": ["P1"], "关键实验文献": ["P2"]},
                "evidence_edges": [{"from": "P1", "to": "P2", "relation": "P1定义问题，P2提供实验基线"}],
                "network_text": "P1支持P2的机制解释。",
            },
            "references": [],
            "_source_documents": [
                {
                    "source_id": "D1",
                    "document_id": 101,
                    "title": "User supplied high-nickel cathode paper",
                    "source_type": "pdf",
                    "included_chars": 1200,
                    "truncated": False,
                },
                {
                    "source_id": "D2",
                    "document_id": 102,
                    "title": "User supplied electrolyte additive paper",
                    "source_type": "doi",
                    "included_chars": 900,
                    "truncated": True,
                },
            ],
            "_qwen_agent_tool_context": {
                "mode": "local_tools",
                "tools": ["chalk_crossref_search"],
                "tool_calls": [
                    {"tool": "chalk_crossref_search", "result": {"ok": True, "data": []}}
                ],
            },
            "_scientific_evidence_context": {
                "source": "paperqa",
                "status": "ok",
                "answer": "Scientific evidence placeholder.",
                "citations": [],
            },
            "_database_evidence_context": {
                "summary": {
                    "literature_evidence_count": 1,
                    "domain_evidence_count": 1,
                    "computational_catalysis_evidence_count": 1,
                    "has_evidence": True,
                },
                "literature_evidence": [
                    {
                        "id": 1,
                        "title": "Verified paper",
                        "doi": "10.0000/example",
                        "material_system": "High-Ni cathode",
                        "key_data": "capacity retention",
                        "reliability_level": "verified",
                    }
                ],
                "domain_evidence": [
                    {
                        "id": 2,
                        "material_system": "High-Ni cathode",
                        "metric_name": "capacity_retention",
                        "metric_value": "82",
                        "metric_unit": "%",
                        "key_mechanism": "CEI instability",
                        "reliability_level": "verified",
                    }
                ],
                "computational_catalysis_evidence": [
                    {
                        "id": 3,
                        "dataset_name": "OC22",
                        "task_type": "source_catalog",
                        "reaction_context": "OER/ORR",
                        "material_system": "oxide electrocatalysts",
                        "adsorbate": "O*",
                        "adsorption_energy": "-1.2 eV",
                        "notes": "计算证据",
                    }
                ],
                "source_catalog": {
                    "computational": {
                        "OC22": {
                            "scope": "OER/ORR oxide electrocatalysis",
                            "url": "https://arxiv.org/abs/2206.08917",
                            "note": "计算证据，不得直接等同于实验性能",
                        }
                    }
                },
                "warnings": [],
            },
            "_hypothesis_tree_search": {
                "nodes": [],
                "summary": {"node_count": 0},
            },
        },
        "battery_materials",
    )

    html = HTMLReportRenderer().render(HypothesisResult(raw_json=data), enhance=False)

    assert "sec-foundation" in html
    assert "sec-hypothesis-plan" in html
    assert "sec-validation-trace" in html
    assert "nav-group-toggle" in html
    assert "nav-subitem" in html
    assert "toggleNavGroup" in html
    assert "1. 研究依据" in html
    assert "2. 假设方案" in html
    assert "3. 验证溯源" in html
    assert "A. 多文献提取表" in html
    assert "待研究问题（Problem Statement）" in html
    assert "解决思路（Rationale）" in html
    assert "必要的技术手段（Technical Details）" in html
    assert "数据集（Datasets）： Source与Target" in html
    assert "标题（Paper Title）" in html
    assert "摘要（Paper Abstract）" in html
    assert "方法论（Methods）" in html
    assert "实验设计（Experiments）" in html
    assert "实验结果（Results）" in html
    assert "参考论文（References）" in html
    assert "用户主动输入文献（全部显示）" in html
    assert "User supplied high-nickel cathode paper" in html
    assert "User supplied electrolyte additive paper" in html
    assert "document_id=101" in html
    assert "document_id=102" in html
    assert "A. 多文献结构化提取表" in html
    assert "B. 自建数据库字段建议" in html
    assert "C. 文献关系网络图文字版" in html
    assert "D. 数据库证据" in html
    assert "自建数据库匹配证据" in html
    assert "计算催化证据" in html
    assert "OC22" in html
    assert "不得直接等同于实验性能" in html
    assert "database_evidence_ids" in html
    assert "LE-1；DE-2；CE-3" in html
    assert "N. 可直接进入实验记录的假设卡片" in html
    assert "Qwen-Agent 工具调用" in html
    assert "AI Scientist 假设演化轨迹" in html
    assert "科学证据 RAG" in html


def test_report_renderer_hides_verbose_multimodal_summary_and_nature_figures():
    data = apply_domain_output_defaults(
        {
            "paper_title": "Report cleanup",
            "problem_statement": "Clean report output.",
            "rationale": "Test report rendering.",
            "technical_details": "No extra figure prose.",
            "methods": "Render HTML.",
            "results": {
                "comparison_with_literature": [
                    {
                        "metric": "qualitative validation",
                        "predicted": "needs verification",
                        "literature": "not available",
                        "deviation": "n/a",
                    }
                ]
            },
            "_multimodal_evidence": {
                "summary": {"figures_analyzed": 1, "cleaned_points": 0, "suspicious_points": 0, "associations": 0},
                "figures": [
                    {
                        "source_label": "Fig. 1",
                        "image_type": "schematic",
                        "summary": "### 图片类型\n类型: 流程图/示意图\n---\n### 图注与标注\nverbose text\n### 关键数据点\n无法识别\n### 数据趋势分析\n无法识别",
                        "points": [],
                    }
                ],
                "associations": [],
            },
        },
        "electrocatalysis",
    )

    html = HTMLReportRenderer().render(HypothesisResult(raw_json=data), enhance=False)

    assert "### 图片类型" not in html
    assert "### 图注与标注" not in html
    assert "关键数据点" not in html
    assert "数据趋势分析" not in html
    assert "未提取到可用定量数据" in html
    assert "nature-figure" not in html.lower()
    assert "barChart" not in html


def test_score_breakdown_is_rendered_with_evidence_links():
    data = apply_domain_output_defaults(
        {
            "paper_title": "ORR score details",
            "confidence": 8,
            "feasibility": "高",
            "problem_statement": "Co-N4@COF ORR 4e water pathway.",
            "rationale": "Use pore confinement to tune *OOH, *O and *OH adsorption.",
            "technical_details": "DFT and RRDE validation.",
            "methods": "XRD, XPS, Raman, RRDE and DFT.",
            "target_materials": ["Co-N4@COF-4HP"],
            "adsorbates": ["*OOH", "*O", "*OH"],
            "score_breakdown": {
                "confidence": {
                    "total_score": 82,
                    "max_score": 100,
                    "dimensions": [
                        {
                            "id": "computational_evidence_coverage",
                            "label": "计算证据覆盖",
                            "score": 18,
                            "max_score": 20,
                            "level": "high",
                            "rationale": "同位点覆盖 *OOH/*O/*OH。",
                            "evidence_ids": ["CE-1", "CE-2", "CE-3"],
                            "penalties": [],
                            "improvement_suggestions": [],
                        }
                    ],
                },
                "feasibility": {
                    "total_score": 76,
                    "max_score": 100,
                    "dimensions": [
                        {
                            "id": "experimental_protocol_maturity",
                            "label": "实验协议成熟度",
                            "score": 18,
                            "max_score": 20,
                            "level": "high",
                            "rationale": "RRDE/LSV/Tafel 协议明确。",
                            "evidence_ids": ["DE-4"],
                            "penalties": ["缺少长期稳定性窗口"],
                            "improvement_suggestions": ["补充 10 h chronoamperometry"],
                        }
                    ],
                },
            },
        },
        "electrocatalysis",
    )

    html = HTMLReportRenderer().render(HypothesisResult(raw_json=data), enhance=False)

    assert "sec-score-breakdown" in html
    assert "scoreConfidenceChart" in html
    assert "scoreFeasibilityChart" in html
    assert "computational_evidence_coverage" in html
    assert "CE-1" in html
    assert "DE-4" in html
    assert "缺少长期稳定性窗口" in html


def test_score_breakdown_penalizes_missing_same_site_adsorbate_coverage():
    data = apply_domain_output_defaults(
        {
            "paper_title": "ORR same-site coverage audit",
            "confidence": 8,
            "feasibility": "high",
            "problem_statement": "Co-N4@COF ORR 4e water pathway.",
            "rationale": "Tune *OOH, *O and *OH adsorption at the same Co-N4 pore site.",
            "technical_details": "DFT, RRDE, LSV, Tafel and XPS validation.",
            "methods": "DFT, RRDE, LSV, Tafel and XPS validation.",
        },
        "electrocatalysis",
    )
    database_evidence = {
        "literature_evidence": [{"id": 1, "title": "ORR benchmark"}],
        "domain_evidence": [{"id": 2, "metric_name": "mass_activity"}],
        "computational_catalysis_evidence": [{"id": 3, "adsorbate": "*O"}],
        "adsorbate_energy_sets": [
            {
                "material_system": "Co-N4@COF",
                "surface_facet": "pore-A",
                "active_site": "Co-N4 pore A",
                "coverage": {"complete": False, "missing_adsorbates": ["*OOH", "*OH"]},
                "evidence_ids": ["CE-3"],
            }
        ],
    }

    breakdown = build_score_breakdown(data, database_evidence=database_evidence)
    confidence_dims = {item["id"]: item for item in breakdown["confidence"]["dimensions"]}
    coverage = confidence_dims["computational_evidence_coverage"]

    assert coverage["score"] < coverage["max_score"]
    assert "CE-3" in coverage["evidence_ids"]
    assert any("*OOH" in item and "*OH" in item for item in coverage["penalties"])
    assert confidence_dims["uncertainty_transparency"]["score"] == 5


def test_report_renderer_builds_score_breakdown_for_legacy_scores():
    data = apply_domain_output_defaults(
        {
            "paper_title": "Legacy score report",
            "confidence": 6,
            "feasibility": "中",
            "problem_statement": "Legacy report without score_breakdown.",
            "rationale": "Backwards compatibility.",
        },
        "electrocatalysis",
    )

    html = HTMLReportRenderer().render(HypothesisResult(raw_json=data), enhance=False)

    assert "sec-score-breakdown" in html
    assert "legacy_confidence" in html
    assert "legacy_feasibility" in html


def test_report_renderer_coerces_chart_numbers_without_zero_fallback():
    renderer = HTMLReportRenderer()

    assert renderer._coerce_chart_number("-1.2 eV") == -1.2
    assert renderer._coerce_chart_number("35 ± 2 mV/dec") == 35.0
    assert abs(renderer._coerce_chart_number("0.2~0.4 eV") - 0.3) < 1e-9
    assert renderer._coerce_chart_number("<10 mA cm-2") == 10.0
    assert renderer._coerce_chart_number("无法识别") is None
    assert renderer._coerce_chart_number("not available") is None

    data = apply_domain_output_defaults(
        {
            "paper_title": "Numeric chart",
            "problem_statement": "Check chart data.",
            "rationale": "Numeric comparisons only.",
            "technical_details": "Render chart.",
            "methods": "Render HTML.",
            "results": {
                "comparison_with_literature": [
                    {"metric": "Delta G", "predicted": "-1.2 eV", "literature": "-1.0 eV", "deviation": "20%"},
                    {"metric": "qualitative", "predicted": "needs verification", "literature": "not available", "deviation": "n/a"},
                ]
            },
        },
        "electrocatalysis",
    )
    html = renderer.render(HypothesisResult(raw_json=data), enhance=False)

    assert "barChart" in html
    assert "Delta G" in html
    assert '"qualitative"' not in html


if __name__ == "__main__":
    test_battery_guidance_and_defaults_are_experiment_ready()
    test_electrocatalysis_guidance_and_defaults_are_reaction_first()
    test_domain_guidance_turns_database_records_into_hypothesis_seeds()
    test_report_renderer_uses_three_major_blocks_and_domain_sections()
    test_report_renderer_hides_verbose_multimodal_summary_and_nature_figures()
    test_score_breakdown_is_rendered_with_evidence_links()
    test_score_breakdown_penalizes_missing_same_site_adsorbate_coverage()
    test_report_renderer_builds_score_breakdown_for_legacy_scores()
    test_report_renderer_coerces_chart_numbers_without_zero_fallback()
    print("domain hypothesis prompt tests passed")
