import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent_framework import HypothesisResult
from hypothesis_tree_search import build_trace_first_hypothesis_tree
from report_renderer import HTMLReportRenderer


def _sample_final_data():
    return {
        "paper_title": "Trace-first LiFePO4 hypothesis",
        "hypothesis": "Carbon coating improves LiFePO4 rate capability.",
        "confidence": 8,
        "feasibility": "高",
        "references": [{"title": "Ref A"}, {"title": "Ref B"}],
        "_evidence_ledger": {
            "summary": {
                "accepted_reference_count": 2,
                "claim_count": 3,
                "supported_claim_count": 2,
            }
        },
        "_scientific_toolkit": {
            "status": "ok",
            "summary": {
                "structures_generated": 1,
                "structures_parsed": 1,
                "atomate2_workflows_planned": 1,
                "atomate2_jobs_planned": 2,
            },
        },
        "_hypothesis_workflow_package": {
            "summary": {
                "structure_candidates": 1,
                "parsed_structures": 1,
                "vasp_recommendations": 1,
                "atomate2_workflows": 1,
                "atomate2_jobs": 2,
            },
            "risk_notices": ["dry-run only"],
        },
    }


def test_trace_first_tree_builds_real_pipeline_nodes():
    final_data = _sample_final_data()
    tree = build_trace_first_hypothesis_tree(
        final_data,
        iterations=[
            {
                "round": 1,
                "hypothesis": {"hypothesis": "Revised LiFePO4 coating hypothesis"},
                "critique_score": 7,
            }
        ],
        critique_history=[
            {
                "round": 1,
                "score": 6,
                "summary": "Mechanism needs tighter evidence.",
                "critical_flaws": ["Missing coating thickness control."],
                "improvements": ["Add impedance validation."],
            }
        ],
        debate_history=[
            {
                "round": 1,
                "devil_verdict": "Alternative particle-size explanation remains plausible.",
                "optimist_verdict": "Coating mechanism is still testable.",
                "devil_attack_points": [{"target": "mechanism", "evidence": "No impedance data"}],
                "optimist_defense_points": [{"target": "testability", "evidence": "Can run EIS"}],
            }
        ],
        interaction_history={
            "interactions": [
                {
                    "round": 1,
                    "user_action": "revise",
                    "user_feedback_text": "Preserve the coating branch but add controls.",
                    "hypothesis_snapshot": {"hypothesis": "Initial LiFePO4 coating hypothesis"},
                    "structured_feedback": {"证据是否充足": "需要补充"},
                }
            ]
        },
    )

    assert tree["schema_version"] == "1.0"
    assert tree["root_id"]
    assert tree["recommended_node_id"]
    assert len(tree["nodes"]) >= 7
    stages = [node["stage"] for node in tree["nodes"]]
    assert "initial_hypothesis" in stages
    assert "critic_review" in stages
    assert "devil_advocate" in stages
    assert "optimist_review" in stages
    assert "human_revision" in stages
    assert "scientific_toolkit_review" in stages
    assert "final_hypothesis" in stages

    for node in tree["nodes"]:
        score = node["score"]
        for key in (
            "evidence_score",
            "feasibility_score",
            "tool_score",
            "novelty_score",
            "reproducibility_score",
            "overall_score",
            "rationale",
        ):
            assert key in score

    json.dumps(tree, ensure_ascii=False)


def test_trace_first_tree_degrades_without_scientific_tools():
    final_data = {
        "paper_title": "No tool hypothesis",
        "hypothesis": "A testable hypothesis.",
        "confidence": 5,
        "references": [],
    }

    tree = build_trace_first_hypothesis_tree(final_data)

    assert tree["nodes"]
    assert tree["summary"]["tool_ready"] is False
    assert tree["summary"]["node_count"] == len(tree["nodes"])
    assert tree["nodes"][0]["score"]["tool_score"] <= 0.4
    assert "fallback" in tree["nodes"][0]["score"]["rationale"]["novelty"]


def test_report_renderer_shows_hypothesis_tree():
    final_data = _sample_final_data()
    final_data["_hypothesis_tree_search"] = build_trace_first_hypothesis_tree(
        final_data,
        critique_history=[{"round": 1, "summary": "Need stronger mechanism."}],
        debate_history=[{"round": 1, "devil_verdict": "Alternative explanation.", "optimist_verdict": "Still testable."}],
    )
    html = HTMLReportRenderer().render(HypothesisResult(raw_json=final_data), enhance=False)

    assert "AI Scientist 假设演化轨迹" in html
    assert "initial_hypothesis" in html
    assert "overall_score" in html


if __name__ == "__main__":
    test_trace_first_tree_builds_real_pipeline_nodes()
    test_trace_first_tree_degrades_without_scientific_tools()
    test_report_renderer_shows_hypothesis_tree()
    print("hypothesis tree search tests passed")
