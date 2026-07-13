"""
scientific_validation.py — 可执行科学验证层

在 LLM 公式推导之外增加一层确定性的代码执行验证：
  1. 解析多模态定量挖掘得到的标度关系
  2. 对可计算数据点执行数值代入和偏差检查
  3. 检查实验设计是否具备 baseline/metric 可复现要素
  4. 生成可审计 execution_log，并可合并回最终 results 字段
"""

from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


_NUMBER_RE = re.compile(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?")


def _to_float(value: Any) -> Optional[float]:
    if isinstance(value, (int, float)):
        return float(value)
    if value is None:
        return None
    match = _NUMBER_RE.search(str(value))
    return float(match.group()) if match else None


def _fmt_num(value: float) -> str:
    return f"{value:.4f}"


def _normalize_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, str):
        return [v.strip() for v in re.split(r"[;；,\n]", value) if v.strip()]
    return [value]


@dataclass
class ExecutableScalingRelation:
    param_x: str
    param_y: str
    slope: float
    intercept: float
    r_squared: float = 0.0
    p_value: float = 1.0
    n_points: int = 0
    unit_x: str = ""
    unit_y: str = ""
    equation: str = ""
    scientific_basis: str = ""
    data_points_used: List[Dict[str, Any]] = field(default_factory=list)

    def predict(self, x_value: float) -> float:
        return self.slope * x_value + self.intercept


class ScientificValidationEngine:
    """确定性验证引擎：输出可审计的公式代入与实验设计检查。"""

    RANDOM_SEED = 42
    DEVIATION_READY_THRESHOLD = 0.15

    def validate(
        self,
        hypothesis_data: Dict[str, Any],
        results_verification: Optional[Dict[str, Any]] = None,
        scaling_context: str = "",
        quantitative_report: Optional[Dict[str, Any]] = None,
        multimodal_evidence: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        if not isinstance(hypothesis_data, dict):
            hypothesis_data = {}
        if not isinstance(results_verification, dict):
            results_verification = {}
        if not isinstance(quantitative_report, dict):
            quantitative_report = {}
        if not isinstance(multimodal_evidence, dict):
            multimodal_evidence = {}

        checks: List[Dict[str, Any]] = []
        calculated_values: List[Dict[str, Any]] = []
        comparisons: List[Dict[str, Any]] = []
        derivation_lines: List[str] = []

        experiment_check = self._check_experiment_readiness(hypothesis_data)
        checks.append(experiment_check)
        comparisons.append({
            "metric": "实验设计可复现性",
            "predicted": (
                f"baselines={experiment_check['baseline_count']}, "
                f"metrics={experiment_check['metric_count']}"
            ),
            "literature": "比赛要求：需包含基线对比与评估指标",
            "deviation": "0%" if experiment_check["status"] == "ready" else "要素不足",
            "status": experiment_check["status"],
        })

        relations = self._collect_scaling_relations(quantitative_report, scaling_context)
        if relations:
            derivation_lines.append("【标度关系代码执行验证】")
        for idx, relation in enumerate(relations, 1):
            check = {
                "name": f"scaling_relation_{idx}",
                "type": "numeric_substitution",
                "equation": relation.equation or (
                    f"{relation.param_y} = {relation.slope:.4f} * "
                    f"{relation.param_x} + {relation.intercept:.4f}"
                ),
                "r_squared": round(relation.r_squared, 4),
                "n_points": relation.n_points,
                "status": self._relation_status(relation),
            }
            checks.append(check)
            derivation_lines.append(
                f"{idx}) 使用标度关系 {check['equation']}；"
                f"拟合质量 R²={relation.r_squared:.4f}, n={relation.n_points}。"
            )

            samples = self._sample_points_for_relation(relation, multimodal_evidence)
            if not samples:
                derivation_lines.append(
                    f"   未发现可代入的 {relation.param_x} 数据点，因此只记录拟合方程，"
                    "不生成预测值。"
                )
                continue

            for sample_idx, sample in enumerate(samples[:3], 1):
                x_value = sample.get("x")
                if x_value is None:
                    continue
                predicted = relation.predict(float(x_value))
                observed = sample.get("y")
                unit_y = relation.unit_y or sample.get("unit_y", "")
                source = sample.get("source", "")
                derivation = (
                    f"{relation.param_y} = {relation.slope:.4f} × "
                    f"{relation.param_x}({float(x_value):.4f}) + "
                    f"{relation.intercept:.4f} = {_fmt_num(predicted)}"
                )
                if unit_y:
                    derivation += f" {unit_y}"
                if source:
                    derivation += f"；输入来源: {source}"
                calculated_values.append({
                    "parameter": relation.param_y,
                    "value": f"{_fmt_num(predicted)} {unit_y}".strip(),
                    "derivation": derivation,
                    "input": {
                        "parameter": relation.param_x,
                        "value": f"{float(x_value):.4f} {relation.unit_x}".strip(),
                        "source": source,
                    },
                    "source": "ScientificValidationEngine",
                })
                derivation_lines.append(f"   {idx}.{sample_idx} {derivation}")

                if observed is not None:
                    deviation = self._relative_deviation(predicted, float(observed))
                    comparisons.append({
                        "metric": relation.param_y,
                        "predicted": f"{_fmt_num(predicted)} {unit_y}".strip(),
                        "literature": f"{_fmt_num(float(observed))} {unit_y}".strip(),
                        "deviation": f"{deviation * 100:.1f}%",
                        "status": (
                            "ready"
                            if deviation <= self.DEVIATION_READY_THRESHOLD
                            else "review"
                        ),
                        "source": source,
                    })

        llm_comparisons = self._normalize_llm_comparisons(results_verification)
        comparisons.extend(llm_comparisons)

        if not derivation_lines:
            derivation_lines.append(
                "未发现可执行的定量标度关系；本轮执行了实验设计可复现性检查，"
                "并保留 LLM 推导结果供人工审阅。"
            )

        ready_checks = sum(1 for c in checks if c.get("status") == "ready")
        conclusion = (
            f"代码执行验证完成：共执行 {len(checks)} 项检查，"
            f"{ready_checks} 项达到 ready 状态；"
            f"生成 {len(calculated_values)} 个可追溯计算值。"
        )
        if relations:
            conclusion += " 标度关系来源于多模态定量挖掘，可作为后续实验验证的数值先验。"
        else:
            conclusion += " 当前缺少足够结构化数值关系，建议补充图表数据或历史实验数据以增强量化验证。"

        input_sources = []
        if scaling_context:
            input_sources.append("quantitative_context")
        if quantitative_report:
            input_sources.append("quantitative_report")
        if multimodal_evidence:
            input_sources.append("multimodal_evidence")
        if results_verification:
            input_sources.append("llm_results_verification")

        return {
            "verification_method": "代码执行验证 + 公式推导",
            "derivation": "\n".join(derivation_lines),
            "calculated_values": calculated_values,
            "comparison_with_literature": comparisons,
            "feasibility_conclusion": conclusion,
            "execution_log": {
                "validator": "ScientificValidationEngine",
                "random_seed": self.RANDOM_SEED,
                "input_source": input_sources or ["hypothesis_data"],
                "checks": checks,
            },
        }

    def _check_experiment_readiness(self, hypothesis_data: Dict[str, Any]) -> Dict[str, Any]:
        experiments = hypothesis_data.get("experiments", {})
        if not isinstance(experiments, dict):
            experiments = {}
        baselines = _normalize_list(experiments.get("baselines"))
        metrics = _normalize_list(experiments.get("metrics"))
        design = str(experiments.get("design", "") or "")
        status = "ready" if len(baselines) >= 2 and len(metrics) >= 2 and len(design) >= 12 else "review"
        return {
            "name": "experiment_readiness",
            "type": "reproducibility_gate",
            "baseline_count": len(baselines),
            "metric_count": len(metrics),
            "has_design": bool(design.strip()),
            "status": status,
        }

    def _collect_scaling_relations(
        self,
        quantitative_report: Dict[str, Any],
        scaling_context: str,
    ) -> List[ExecutableScalingRelation]:
        relations: List[ExecutableScalingRelation] = []
        for item in quantitative_report.get("scaling_relations", []) or []:
            if not isinstance(item, dict):
                continue
            relation = self._relation_from_dict(item)
            if relation:
                relations.append(relation)

        existing = {(r.param_x, r.param_y, round(r.slope, 8), round(r.intercept, 8)) for r in relations}
        for relation in self._parse_relations_from_context(scaling_context):
            key = (relation.param_x, relation.param_y, round(relation.slope, 8), round(relation.intercept, 8))
            if key not in existing:
                relations.append(relation)
                existing.add(key)
        return relations

    def _relation_from_dict(self, item: Dict[str, Any]) -> Optional[ExecutableScalingRelation]:
        slope = _to_float(item.get("slope"))
        intercept = _to_float(item.get("intercept"))
        if slope is None or intercept is None:
            return None
        return ExecutableScalingRelation(
            param_x=str(item.get("param_x", "")).strip(),
            param_y=str(item.get("param_y", "")).strip(),
            slope=slope,
            intercept=intercept,
            r_squared=_to_float(item.get("r_squared")) or 0.0,
            p_value=_to_float(item.get("p_value")) or 1.0,
            n_points=int(_to_float(item.get("n_points")) or 0),
            unit_x=str(item.get("unit_x", "") or ""),
            unit_y=str(item.get("unit_y", "") or ""),
            equation=str(item.get("equation", "") or ""),
            scientific_basis=str(item.get("scientific_basis", "") or ""),
            data_points_used=list(item.get("data_points_used", []) or []),
        )

    def _parse_relations_from_context(self, text: str) -> List[ExecutableScalingRelation]:
        if not text:
            return []

        lines = text.splitlines()
        relations: List[ExecutableScalingRelation] = []
        current: Optional[ExecutableScalingRelation] = None

        equation_pattern = re.compile(
            r"标度关系\s*\d+\s*:\s*(?P<y>.+?)\s*=\s*"
            r"(?P<slope>[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)\s*\*\s*"
            r"(?P<x>.+?)\s*\+\s*(?P<intercept>[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)"
        )
        stats_pattern = re.compile(
            r"R[²2]\s*=\s*(?P<r2>[-+]?\d*\.?\d+).*?"
            r"p\s*=\s*(?P<p>[-+]?\d*\.?\d+).*?"
            r"n\s*=\s*(?P<n>\d+)"
        )

        for raw_line in lines:
            line = raw_line.strip()
            match = equation_pattern.search(line)
            if match:
                current = ExecutableScalingRelation(
                    param_x=match.group("x").strip(),
                    param_y=match.group("y").strip(),
                    slope=float(match.group("slope")),
                    intercept=float(match.group("intercept")),
                    equation=line.split(":", 1)[-1].strip(),
                )
                relations.append(current)
                continue

            if current is None:
                continue

            stats = stats_pattern.search(line)
            if stats:
                current.r_squared = float(stats.group("r2"))
                current.p_value = float(stats.group("p"))
                current.n_points = int(stats.group("n"))
                continue

            if line.startswith("依据:"):
                current.scientific_basis = line.split(":", 1)[-1].strip()
                continue

            point = self._parse_point_line(line, current.param_x, current.param_y)
            if point:
                current.data_points_used.append(point)

        return relations

    @staticmethod
    def _parse_point_line(line: str, param_x: str, param_y: str) -> Optional[Dict[str, Any]]:
        if not line.startswith("-"):
            return None
        material = ""
        if ":" in line:
            material = line.split(":", 1)[0].lstrip("- ").strip()

        x_match = re.search(re.escape(param_x) + r"\s*=\s*(" + _NUMBER_RE.pattern + r")\s*([^\s,，]*)", line)
        y_match = re.search(re.escape(param_y) + r"\s*=\s*(" + _NUMBER_RE.pattern + r")\s*([^\s,，]*)", line)
        if not x_match:
            return None
        return {
            "material": material,
            "x": float(x_match.group(1)),
            "y": float(y_match.group(1)) if y_match else None,
            "unit_x": x_match.group(2) or "",
            "unit_y": y_match.group(2) if y_match else "",
            "source": material or "quantitative_context",
        }

    def _sample_points_for_relation(
        self,
        relation: ExecutableScalingRelation,
        multimodal_evidence: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        samples: List[Dict[str, Any]] = []
        for point in relation.data_points_used:
            if not isinstance(point, dict):
                continue
            x_value = _to_float(point.get("x"))
            if x_value is None:
                x_value = _to_float(point.get(relation.param_x))
            if x_value is None:
                continue
            y_value = _to_float(point.get("y"))
            if y_value is None:
                y_value = _to_float(point.get(relation.param_y))
            samples.append({
                "x": x_value,
                "y": y_value,
                "unit_x": point.get("unit_x", relation.unit_x),
                "unit_y": point.get("unit_y", relation.unit_y),
                "source": point.get("source") or point.get("material") or "quantitative_report",
            })

        if samples:
            return samples

        for fig in multimodal_evidence.get("figures", []) or []:
            if not isinstance(fig, dict):
                continue
            for point in fig.get("points", []) or []:
                if not isinstance(point, dict):
                    continue
                if str(point.get("parameter", "")).lower() != relation.param_x.lower():
                    continue
                x_value = _to_float(point.get("value"))
                if x_value is None:
                    continue
                samples.append({
                    "x": x_value,
                    "y": None,
                    "unit_x": point.get("unit", relation.unit_x),
                    "unit_y": relation.unit_y,
                    "source": point.get("source") or fig.get("source_label", ""),
                })
        return samples

    @staticmethod
    def _relation_status(relation: ExecutableScalingRelation) -> str:
        if relation.n_points >= 3 and relation.r_squared >= 0.6:
            return "ready"
        if relation.n_points and relation.r_squared:
            return "review"
        return "recorded"

    @staticmethod
    def _relative_deviation(predicted: float, observed: float) -> float:
        denom = abs(observed) if observed else 1.0
        return abs(predicted - observed) / denom

    def _normalize_llm_comparisons(self, results_verification: Dict[str, Any]) -> List[Dict[str, Any]]:
        normalized = []
        for comp in results_verification.get("comparison_with_literature", []) or []:
            if not isinstance(comp, dict):
                continue
            predicted = comp.get("predicted", comp.get("calculated_value", ""))
            literature = comp.get("literature", comp.get("literature_value", ""))
            pred_num = _to_float(predicted)
            lit_num = _to_float(literature)
            deviation_text = comp.get("deviation", comp.get("deviation_pct", ""))
            status = "recorded"
            if pred_num is not None and lit_num is not None:
                deviation = self._relative_deviation(pred_num, lit_num)
                deviation_text = f"{deviation * 100:.1f}%"
                status = "ready" if deviation <= self.DEVIATION_READY_THRESHOLD else "review"
            normalized.append({
                "metric": comp.get("metric", comp.get("parameter", "")),
                "predicted": predicted,
                "literature": literature,
                "deviation": deviation_text,
                "status": status,
                "source": "LLM results verification",
            })
        return normalized


def merge_executable_validation(
    hypothesis_data: Dict[str, Any],
    executable_validation: Dict[str, Any],
) -> Dict[str, Any]:
    """把确定性验证结果合并回最终比赛规范 JSON。"""
    if not isinstance(hypothesis_data, dict):
        return hypothesis_data
    if not isinstance(executable_validation, dict) or not executable_validation:
        return hypothesis_data

    data = copy.deepcopy(hypothesis_data)
    results = data.get("results", {})
    if not isinstance(results, dict):
        results = {}

    method = str(results.get("verification_method", "") or "").strip()
    executable_method = executable_validation.get("verification_method", "代码执行验证")
    if "代码执行验证" not in method:
        results["verification_method"] = (
            f"{method} + {executable_method}" if method else executable_method
        )

    existing_derivation = str(results.get("derivation", "") or "").strip()
    executable_derivation = str(executable_validation.get("derivation", "") or "").strip()
    if executable_derivation and executable_derivation not in existing_derivation:
        results["derivation"] = (
            f"{existing_derivation}\n\n【代码执行验证】\n{executable_derivation}"
            if existing_derivation
            else executable_derivation
        )

    existing_values = results.get("calculated_values", [])
    if not isinstance(existing_values, list):
        existing_values = [existing_values] if existing_values else []
    results["calculated_values"] = existing_values + list(
        executable_validation.get("calculated_values", []) or []
    )

    existing_comparisons = results.get("comparison_with_literature", [])
    if not isinstance(existing_comparisons, list):
        existing_comparisons = [existing_comparisons] if existing_comparisons else []
    results["comparison_with_literature"] = existing_comparisons + list(
        executable_validation.get("comparison_with_literature", []) or []
    )

    conclusion = str(results.get("feasibility_conclusion", "") or "").strip()
    executable_conclusion = str(executable_validation.get("feasibility_conclusion", "") or "").strip()
    if executable_conclusion and executable_conclusion not in conclusion:
        results["feasibility_conclusion"] = (
            f"{conclusion}\n{executable_conclusion}" if conclusion else executable_conclusion
        )

    results["execution_log"] = executable_validation.get("execution_log", {})
    data["results"] = results
    data["_executable_validation"] = executable_validation
    return data


def validation_to_prompt_text(executable_validation: Dict[str, Any]) -> str:
    """供 OutputAgent 注入 prompt 的紧凑文本。"""
    if not executable_validation:
        return ""
    return json.dumps(executable_validation, ensure_ascii=False, indent=2)
