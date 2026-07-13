# -*- coding: utf-8 -*-
"""Trace-first hypothesis tree search for Chalk.

This module does not expand new hypotheses yet. It turns the real multi-agent
pipeline trace into a structured search tree that can be shown in HITL review,
HTML reports, and future AI Scientist style search workflows.
"""

from __future__ import annotations

import copy
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional


SCHEMA_VERSION = "1.0"


@dataclass
class HypothesisScore:
    evidence_score: float = 0.0
    feasibility_score: float = 0.0
    tool_score: float = 0.0
    novelty_score: float = 0.0
    reproducibility_score: float = 0.0
    overall_score: float = 0.0
    rationale: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class HypothesisNode:
    node_id: str
    parent_id: Optional[str]
    stage: str
    agent_role: str
    hypothesis_json: Dict[str, Any]
    source_round: Optional[int] = None
    support_evidence: List[Any] = field(default_factory=list)
    objections: List[Any] = field(default_factory=list)
    human_feedback: Dict[str, Any] = field(default_factory=dict)
    scientific_toolkit_summary: Dict[str, Any] = field(default_factory=dict)
    workflow_package_summary: Dict[str, Any] = field(default_factory=dict)
    score: Dict[str, Any] = field(default_factory=dict)
    decision: str = "kept"
    decision_reason: str = ""
    created_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class HypothesisSearchTree:
    """Mutable builder for a trace-first hypothesis tree."""

    def __init__(self, *, base_context: Optional[Dict[str, Any]] = None):
        self.base_context = base_context or {}
        self.nodes: List[HypothesisNode] = []
        self.root_id: str = ""
        self.recommended_node_id: str = ""

    def add_node(
        self,
        *,
        stage: str,
        agent_role: str,
        hypothesis_json: Optional[Dict[str, Any]] = None,
        parent_id: Optional[str] = None,
        source_round: Optional[int] = None,
        support_evidence: Optional[List[Any]] = None,
        objections: Optional[List[Any]] = None,
        human_feedback: Optional[Dict[str, Any]] = None,
        decision: str = "kept",
        decision_reason: str = "",
        score_overrides: Optional[Dict[str, Any]] = None,
    ) -> HypothesisNode:
        node_id = f"n_{len(self.nodes) + 1:03d}_{stage}"
        score = score_hypothesis_node(
            hypothesis_json or {},
            self.base_context,
            stage=stage,
            score_overrides=score_overrides,
        ).to_dict()
        node = HypothesisNode(
            node_id=node_id,
            parent_id=parent_id,
            stage=stage,
            agent_role=agent_role,
            hypothesis_json=_json_clone(hypothesis_json or {}),
            source_round=source_round,
            support_evidence=support_evidence or [],
            objections=objections or [],
            human_feedback=human_feedback or {},
            scientific_toolkit_summary=_scientific_summary(self.base_context.get("_scientific_toolkit", {})),
            workflow_package_summary=_workflow_summary(self.base_context.get("_hypothesis_workflow_package", {})),
            score=score,
            decision=decision,
            decision_reason=decision_reason,
            created_at=datetime.now().isoformat(timespec="seconds"),
        )
        self.nodes.append(node)
        if not self.root_id:
            self.root_id = node.node_id
        self.recommended_node_id = self.recommend_node_id()
        return node

    def recommend_node_id(self) -> str:
        candidates = [
            node for node in self.nodes
            if node.decision in {"kept", "final", "revised"} and isinstance(node.score, dict)
        ]
        if not candidates:
            return self.nodes[-1].node_id if self.nodes else ""
        return max(candidates, key=lambda node: float(node.score.get("overall_score", 0.0))).node_id

    def to_dict(self) -> Dict[str, Any]:
        self.recommended_node_id = self.recommend_node_id()
        return {
            "schema_version": SCHEMA_VERSION,
            "root_id": self.root_id,
            "recommended_node_id": self.recommended_node_id,
            "nodes": [node.to_dict() for node in self.nodes],
            "summary": self.summary(),
        }

    def summary(self) -> Dict[str, Any]:
        stage_counts: Dict[str, int] = {}
        for node in self.nodes:
            stage_counts[node.stage] = stage_counts.get(node.stage, 0) + 1
        recommended = next(
            (node for node in self.nodes if node.node_id == self.recommended_node_id),
            None,
        )
        return {
            "node_count": len(self.nodes),
            "stage_counts": stage_counts,
            "recommended_stage": recommended.stage if recommended else "",
            "recommended_overall_score": (
                recommended.score.get("overall_score", 0.0)
                if recommended and isinstance(recommended.score, dict)
                else 0.0
            ),
            "tool_ready": bool(_scientific_summary(self.base_context.get("_scientific_toolkit", {})).get("tool_ready")),
            "workflow_ready": bool(_workflow_summary(self.base_context.get("_hypothesis_workflow_package", {})).get("workflow_ready")),
        }


def build_trace_first_hypothesis_tree(
    final_data: Dict[str, Any],
    *,
    iterations: Optional[List[Dict[str, Any]]] = None,
    critique_history: Optional[List[Dict[str, Any]]] = None,
    debate_history: Optional[List[Dict[str, Any]]] = None,
    interaction_history: Optional[Dict[str, Any]] = None,
    scientific_toolkit: Optional[Dict[str, Any]] = None,
    workflow_package: Optional[Dict[str, Any]] = None,
    validation_report: Optional[Dict[str, Any]] = None,
    results_verification: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build a tree from the real multi-agent trajectory."""
    data = final_data if isinstance(final_data, dict) else {}
    context = _json_clone(data)
    if scientific_toolkit is not None:
        context["_scientific_toolkit"] = scientific_toolkit
    if workflow_package is not None:
        context["_hypothesis_workflow_package"] = workflow_package
    if validation_report is not None:
        context["_validation_report"] = validation_report
    if results_verification is not None:
        context["_results_verification"] = results_verification

    iterations = _list_of_dicts(iterations if iterations is not None else data.get("iterations"))
    critique_history = _list_of_dicts(critique_history if critique_history is not None else data.get("critique_history"))
    debate_history = _list_of_dicts(debate_history if debate_history is not None else data.get("debate_history"))
    interaction_history = interaction_history if isinstance(interaction_history, dict) else data.get("_human_collaboration", {})
    interactions = _list_of_dicts(interaction_history.get("interactions") if isinstance(interaction_history, dict) else [])

    tree = HypothesisSearchTree(base_context=context)
    initial_hypothesis = _initial_hypothesis(data, iterations, interactions)
    root = tree.add_node(
        stage="initial_hypothesis",
        agent_role="HypothesisAgent",
        hypothesis_json=initial_hypothesis,
        support_evidence=_support_from_data(context),
        decision="kept",
        decision_reason="Initial structured hypothesis from the generation pipeline.",
    )
    current_parent = root.node_id

    for entry in [item for item in interactions if _round(item) == 0]:
        node = tree.add_node(
            stage="human_revision",
            agent_role="HumanInTheLoop",
            hypothesis_json=_hypothesis_from_interaction(entry, initial_hypothesis),
            parent_id=current_parent,
            source_round=0,
            human_feedback=_human_feedback_summary(entry),
            support_evidence=_support_from_human(entry),
            decision="revised" if entry.get("user_action") == "revise" else "kept",
            decision_reason="Initial HITL checkpoint captured before iterative critique.",
        )
        current_parent = node.node_id

    rounds = sorted({
        _round(item) for item in critique_history + debate_history + iterations + interactions
        if _round(item) > 0
    })
    for round_num in rounds:
        critique = _first_round(critique_history, round_num)
        if critique:
            node = tree.add_node(
                stage="critic_review",
                agent_role="CritiqueAgent",
                hypothesis_json=_round_hypothesis(round_num, iterations, interactions, data),
                parent_id=current_parent,
                source_round=round_num,
                objections=_critique_objections(critique),
                support_evidence=_support_from_data(context),
                decision="revised",
                decision_reason=str(critique.get("summary", "Critique identified revision pressure.") or ""),
                score_overrides=_score_from_critique(critique),
            )
            current_parent = node.node_id

        debate = _first_round(debate_history, round_num)
        if debate:
            devil = tree.add_node(
                stage="devil_advocate",
                agent_role="DevilAdvocateAgent",
                hypothesis_json=_round_hypothesis(round_num, iterations, interactions, data),
                parent_id=current_parent,
                source_round=round_num,
                objections=_debate_attacks(debate),
                decision="kept",
                decision_reason=str(debate.get("devil_verdict", "Devil advocate branch recorded.") or ""),
            )
            optimist = tree.add_node(
                stage="optimist_review",
                agent_role="OptimistAgent",
                hypothesis_json=_round_hypothesis(round_num, iterations, interactions, data),
                parent_id=devil.node_id,
                source_round=round_num,
                support_evidence=_debate_defenses(debate),
                decision="kept",
                decision_reason=str(debate.get("optimist_verdict", "Optimist branch recorded.") or ""),
            )
            current_parent = optimist.node_id

        for entry in [item for item in interactions if _round(item) == round_num]:
            node = tree.add_node(
                stage="human_revision",
                agent_role="HumanInTheLoop",
                hypothesis_json=_hypothesis_from_interaction(entry, _round_hypothesis(round_num, iterations, interactions, data)),
                parent_id=current_parent,
                source_round=round_num,
                human_feedback=_human_feedback_summary(entry),
                support_evidence=_support_from_human(entry),
                objections=_human_objections(entry),
                decision="revised" if entry.get("user_action") == "revise" else "kept",
                decision_reason="Human checkpoint captured with structured feedback.",
            )
            current_parent = node.node_id

        iteration = _first_round(iterations, round_num)
        if iteration and isinstance(iteration.get("hypothesis"), dict):
            node = tree.add_node(
                stage="human_revision",
                agent_role="RevisionAgent",
                hypothesis_json=iteration.get("hypothesis", {}),
                parent_id=current_parent,
                source_round=round_num,
                support_evidence=[{"revision_source": "agent_revision", "critique_score": iteration.get("critique_score")}],
                decision="revised",
                decision_reason="Agent revision after critique, debate, and optional HITL feedback.",
            )
            current_parent = node.node_id

    if context.get("_scientific_toolkit") or context.get("_hypothesis_workflow_package"):
        tool = tree.add_node(
            stage="scientific_toolkit_review",
            agent_role="ScientificToolkitEngine",
            hypothesis_json=data,
            parent_id=current_parent,
            support_evidence=[_scientific_summary(context.get("_scientific_toolkit", {})), _workflow_summary(context.get("_hypothesis_workflow_package", {}))],
            objections=_tool_objections(context.get("_scientific_toolkit", {}), context.get("_hypothesis_workflow_package", {})),
            decision="kept",
            decision_reason="RDKit/pymatgen/atomate2 dry-run review attached to the trajectory.",
        )
        current_parent = tool.node_id

    tree.add_node(
        stage="final_hypothesis",
        agent_role="OutputAgent",
        hypothesis_json=data,
        parent_id=current_parent,
        support_evidence=_support_from_data(context),
        decision="final",
        decision_reason="Final schema-normalized hypothesis selected for report output.",
    )
    return tree.to_dict()


def score_hypothesis_node(
    hypothesis_json: Dict[str, Any],
    context: Dict[str, Any],
    *,
    stage: str = "",
    score_overrides: Optional[Dict[str, Any]] = None,
) -> HypothesisScore:
    score_overrides = score_overrides or {}
    evidence_score, evidence_rationale = _score_evidence(context)
    feasibility_score, feasibility_rationale = _score_feasibility(hypothesis_json, context)
    tool_score, tool_rationale = _score_tools(context.get("_scientific_toolkit", {}))
    reproducibility_score, reproducibility_rationale = _score_reproducibility(
        context.get("_hypothesis_workflow_package", {}),
        context.get("_scientific_toolkit", {}),
    )
    novelty_score, novelty_rationale = _score_novelty(hypothesis_json, context)

    if "feasibility_score" in score_overrides:
        feasibility_score = _clamp01(score_overrides["feasibility_score"])
        feasibility_rationale = "Derived from critique/validation override."
    if stage == "critic_review":
        feasibility_score = min(feasibility_score, 0.7)
    if stage in {"scientific_toolkit_review", "final_hypothesis"}:
        reproducibility_score = max(reproducibility_score, tool_score)

    overall = (
        0.30 * evidence_score
        + 0.20 * feasibility_score
        + 0.20 * reproducibility_score
        + 0.15 * tool_score
        + 0.15 * novelty_score
    )
    return HypothesisScore(
        evidence_score=round(evidence_score, 3),
        feasibility_score=round(feasibility_score, 3),
        tool_score=round(tool_score, 3),
        novelty_score=round(novelty_score, 3),
        reproducibility_score=round(reproducibility_score, 3),
        overall_score=round(_clamp01(overall), 3),
        rationale={
            "evidence": evidence_rationale,
            "feasibility": feasibility_rationale,
            "tool": tool_rationale,
            "novelty": novelty_rationale,
            "reproducibility": reproducibility_rationale,
        },
    )


def _score_evidence(context: Dict[str, Any]) -> tuple[float, str]:
    refs = context.get("references", [])
    ref_count = len(refs) if isinstance(refs, list) else 0
    ledger = context.get("_evidence_ledger", {})
    ledger_summary = ledger.get("summary", {}) if isinstance(ledger, dict) and isinstance(ledger.get("summary"), dict) else {}
    accepted = int(ledger_summary.get("accepted_reference_count", ref_count) or 0)
    claims = int(ledger_summary.get("claim_count", 0) or 0)
    supported = int(ledger_summary.get("supported_claim_count", 0) or 0)
    ref_component = min(1.0, accepted / 5.0)
    coverage = (supported / claims) if claims else (0.6 if accepted else 0.2)
    score = 0.45 * ref_component + 0.55 * coverage
    return _clamp01(score), f"accepted_refs={accepted}, supported_claims={supported}/{claims or 'n/a'}"


def _score_feasibility(hypothesis_json: Dict[str, Any], context: Dict[str, Any]) -> tuple[float, str]:
    confidence = hypothesis_json.get("confidence", context.get("confidence", 5))
    try:
        confidence_score = float(confidence) / 10.0
    except Exception:
        confidence_score = 0.5
    feasibility = str(hypothesis_json.get("feasibility", context.get("feasibility", "")) or "").lower()
    feasibility_map = {
        "高": 0.85,
        "high": 0.85,
        "中": 0.6,
        "medium": 0.6,
        "低": 0.35,
        "low": 0.35,
    }
    feasibility_score = next((value for key, value in feasibility_map.items() if key in feasibility), 0.55)
    verification = context.get("_results_verification") or context.get("results", {})
    verification_bonus = 0.05 if verification else 0.0
    return _clamp01(0.55 * confidence_score + 0.45 * feasibility_score + verification_bonus), (
        f"confidence={confidence}, feasibility={hypothesis_json.get('feasibility', context.get('feasibility', 'unknown'))}"
    )


def _score_tools(toolkit: Dict[str, Any]) -> tuple[float, str]:
    summary = toolkit.get("summary", {}) if isinstance(toolkit, dict) and isinstance(toolkit.get("summary"), dict) else {}
    parsed = int(summary.get("structures_parsed", 0) or 0)
    generated = int(summary.get("structures_generated", 0) or 0)
    workflows = int(summary.get("atomate2_workflows_planned", 0) or 0)
    jobs = int(summary.get("atomate2_jobs_planned", 0) or 0)
    molecules = int(summary.get("molecules_checked", 0) or 0)
    materials = int(summary.get("materials_checked", 0) or 0)
    score = 0.0
    if molecules or materials:
        score += 0.2
    if generated:
        score += 0.2
    if parsed:
        score += 0.25
    if workflows:
        score += 0.25
    if jobs:
        score += 0.1
    if not summary:
        return 0.25, "No scientific toolkit summary; fallback low score."
    return _clamp01(score), f"structures_generated={generated}, parsed={parsed}, workflows={workflows}, jobs={jobs}"


def _score_reproducibility(workflow: Dict[str, Any], toolkit: Dict[str, Any]) -> tuple[float, str]:
    workflow_summary = workflow.get("summary", {}) if isinstance(workflow, dict) and isinstance(workflow.get("summary"), dict) else {}
    toolkit_summary = toolkit.get("summary", {}) if isinstance(toolkit, dict) and isinstance(toolkit.get("summary"), dict) else {}
    candidates = int(workflow_summary.get("structure_candidates", toolkit_summary.get("structures_generated", 0)) or 0)
    parsed = int(workflow_summary.get("parsed_structures", toolkit_summary.get("structures_parsed", 0)) or 0)
    vasp = int(workflow_summary.get("vasp_recommendations", 0) or 0)
    workflows = int(workflow_summary.get("atomate2_workflows", toolkit_summary.get("atomate2_workflows_planned", 0)) or 0)
    score = 0.15
    if candidates:
        score += 0.2
    if parsed:
        score += 0.25
    if vasp:
        score += 0.2
    if workflows:
        score += 0.2
    return _clamp01(score), f"structures={candidates}, parsed={parsed}, vasp={vasp}, atomate2_workflows={workflows}"


def _score_novelty(hypothesis_json: Dict[str, Any], context: Dict[str, Any]) -> tuple[float, str]:
    novelty = hypothesis_json.get("novelty_score") or context.get("novelty_score")
    if isinstance(novelty, (int, float)):
        return _clamp01(float(novelty) if novelty <= 1 else float(novelty) / 10.0), "Provided novelty score."
    text = " ".join(
        str(value)
        for value in (
            hypothesis_json.get("hypothesis"),
            hypothesis_json.get("rationale"),
            hypothesis_json.get("technical_details"),
            context.get("hypothesis"),
        )
        if value
    )
    bonus = 0.08 if any(token in text.lower() for token in ("novel", "new", "unexpected", "mechanism", "cross")) else 0.0
    return _clamp01(0.6 + bonus), "heuristic fallback until LLM novelty evaluator is enabled."


def _initial_hypothesis(data: Dict[str, Any], iterations: List[Dict[str, Any]], interactions: List[Dict[str, Any]]) -> Dict[str, Any]:
    for entry in interactions:
        snapshot = entry.get("hypothesis_snapshot")
        if isinstance(snapshot, dict) and snapshot:
            return snapshot
    if iterations and isinstance(iterations[0].get("hypothesis"), dict):
        return iterations[0]["hypothesis"]
    return data


def _round_hypothesis(round_num: int, iterations: List[Dict[str, Any]], interactions: List[Dict[str, Any]], data: Dict[str, Any]) -> Dict[str, Any]:
    for entry in interactions:
        if _round(entry) == round_num and isinstance(entry.get("hypothesis_snapshot"), dict):
            return entry["hypothesis_snapshot"]
    for item in iterations:
        if _round(item) == round_num and isinstance(item.get("hypothesis"), dict):
            return item["hypothesis"]
    return data


def _hypothesis_from_interaction(entry: Dict[str, Any], fallback: Dict[str, Any]) -> Dict[str, Any]:
    edited = entry.get("user_edited_hypothesis")
    if isinstance(edited, dict) and edited:
        return edited
    snapshot = entry.get("hypothesis_snapshot")
    if isinstance(snapshot, dict) and snapshot:
        return snapshot
    return fallback


def _support_from_data(context: Dict[str, Any]) -> List[Any]:
    refs = context.get("references", [])
    support: List[Any] = []
    if isinstance(refs, list):
        support.extend(refs[:5])
    evidence = context.get("_evidence_ledger", {})
    if isinstance(evidence, dict) and evidence.get("summary"):
        support.append({"evidence_ledger": evidence.get("summary")})
    return support


def _support_from_human(entry: Dict[str, Any]) -> List[Any]:
    support = []
    if entry.get("user_feedback_text"):
        support.append({"human_feedback_text": entry.get("user_feedback_text")})
    structured = entry.get("structured_feedback")
    if isinstance(structured, dict) and structured:
        support.append({"structured_feedback": structured})
    return support


def _human_feedback_summary(entry: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "round": _round(entry),
        "action": entry.get("user_action", entry.get("action", "")),
        "feedback_text": entry.get("user_feedback_text", entry.get("feedback_text", "")),
        "debate_stance": entry.get("debate_stance", ""),
        "structured_feedback": entry.get("structured_feedback", {}),
    }


def _human_objections(entry: Dict[str, Any]) -> List[Any]:
    structured = entry.get("structured_feedback", {})
    if not isinstance(structured, dict):
        return []
    return [
        {"criterion": key, "value": value}
        for key, value in structured.items()
        if value and str(value) not in {"—", "通过", "无"}
    ]


def _critique_objections(critique: Dict[str, Any]) -> List[Any]:
    objections: List[Any] = []
    for key in ("critical_flaws", "improvements", "improvement_suggestions"):
        value = critique.get(key)
        if isinstance(value, list):
            objections.extend(value)
        elif value:
            objections.append(value)
    if critique.get("summary"):
        objections.append({"summary": critique.get("summary")})
    return objections


def _debate_attacks(debate: Dict[str, Any]) -> List[Any]:
    attacks = debate.get("devil_attack_points", [])
    out = attacks if isinstance(attacks, list) else []
    if debate.get("devil_verdict"):
        out = out + [{"verdict": debate.get("devil_verdict")}]
    return out


def _debate_defenses(debate: Dict[str, Any]) -> List[Any]:
    defenses = debate.get("optimist_defense_points", [])
    out = defenses if isinstance(defenses, list) else []
    if debate.get("optimist_verdict"):
        out = out + [{"verdict": debate.get("optimist_verdict")}]
    return out


def _tool_objections(toolkit: Dict[str, Any], workflow: Dict[str, Any]) -> List[Any]:
    objections: List[Any] = []
    warnings = toolkit.get("warnings", []) if isinstance(toolkit, dict) else []
    if isinstance(warnings, list):
        objections.extend(warnings)
    risks = workflow.get("risk_notices", []) if isinstance(workflow, dict) else []
    if isinstance(risks, list):
        objections.extend(risks)
    return objections


def _scientific_summary(toolkit: Dict[str, Any]) -> Dict[str, Any]:
    summary = toolkit.get("summary", {}) if isinstance(toolkit, dict) and isinstance(toolkit.get("summary"), dict) else {}
    return {
        "status": toolkit.get("status", "") if isinstance(toolkit, dict) else "",
        "summary": summary,
        "tool_ready": bool(summary.get("structures_parsed") or summary.get("atomate2_workflows_planned") or summary.get("molecules_checked") or summary.get("materials_checked")),
    }


def _workflow_summary(workflow: Dict[str, Any]) -> Dict[str, Any]:
    summary = workflow.get("summary", {}) if isinstance(workflow, dict) and isinstance(workflow.get("summary"), dict) else {}
    return {
        "summary": summary,
        "workflow_ready": bool(summary.get("structure_candidates") or summary.get("vasp_recommendations") or summary.get("atomate2_workflows")),
    }


def _score_from_critique(critique: Dict[str, Any]) -> Dict[str, Any]:
    score = critique.get("score", critique.get("overall_score"))
    try:
        value = float(score)
        return {"feasibility_score": max(0.1, min(1.0, value / 10.0))}
    except Exception:
        return {}


def _first_round(items: Iterable[Dict[str, Any]], round_num: int) -> Dict[str, Any]:
    for item in items:
        if _round(item) == round_num:
            return item
    return {}


def _round(item: Dict[str, Any]) -> int:
    try:
        return int(item.get("round", 0) or 0)
    except Exception:
        return 0


def _list_of_dicts(value: Any) -> List[Dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _json_clone(value: Any) -> Any:
    try:
        return json.loads(json.dumps(value, ensure_ascii=False))
    except Exception:
        return copy.deepcopy(value)


def _clamp01(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except Exception:
        return 0.0
