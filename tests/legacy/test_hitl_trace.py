from __future__ import annotations

from chalk_app.agents.agent_framework import HypothesisResult
from chalk_app.agents.hitl_trace import (
    build_field_diff,
    make_agent_trace,
    structured_feedback_items,
    summarize_diff,
)
from chalk_app.reports.report_renderer import HTMLReportRenderer


def test_field_diff_reports_nested_hypothesis_changes() -> None:
    before = {
        "paper_title": "Old hypothesis",
        "experiments": {"baselines": ["Pt/C"], "metrics": ["FE"]},
        "references": [{"title": "Old Ref", "doi": "10.1/old"}],
    }
    after = {
        "paper_title": "New hypothesis",
        "experiments": {"baselines": ["Pt/C", "blank electrode"], "metrics": ["FE"]},
        "references": [{"title": "New Ref", "doi": "10.1/new"}],
    }

    diff = build_field_diff(before, after)
    fields = {item["field"] for item in diff}

    assert "paper_title" in fields
    assert "experiments.baselines[1]" in fields
    assert "references[0].doi" in fields
    assert "references[0].title" in fields
    assert "paper_title" in summarize_diff(diff)


def test_structured_feedback_omits_empty_choices() -> None:
    items = structured_feedback_items(
        {
            "citation authenticity": "traceable",
            "over extension": None,
            "experiment feasibility": "",
        }
    )

    assert items == [{"label": "citation authenticity", "value": "traceable"}]


def test_agent_trace_contains_human_collaboration_context() -> None:
    result = HypothesisResult(
        raw_json={"paper_title": "Trace Test"},
        iterations=[{"round": 1}],
        critique_history=[{"round": 1, "score": 7}],
        debate_history=[{"round": 1, "balance": "balanced"}],
    )
    history = {
        "session_id": "abc",
        "mode": "hitl",
        "interactions": [
            {
                "round": 1,
                "user_action": "revise",
                "structured_feedback": {"citation authenticity": "needs review"},
            }
        ],
    }

    trace = make_agent_trace(
        session_id="abc",
        mode="hitl",
        research_question="How can the hypothesis be tested?",
        result=result,
        interaction_history=history,
    )

    assert trace["schema_version"] == "hitl-agent-trace-v1"
    assert trace["mode"] == "hitl"
    assert trace["title"] == "Trace Test"
    assert trace["interaction_history"]["interactions"][0]["user_action"] == "revise"


def test_report_renders_human_collaboration_details() -> None:
    result = HypothesisResult(
        raw_json={
            "paper_title": "HITL Report Test",
            "confidence": 7,
            "feasibility": "medium",
            "problem_statement": "A falsifiable question",
            "references": [],
        },
        interaction_history={
            "mode": "hitl",
            "interactions": [
                {
                    "round": 1,
                    "user_action": "revise",
                    "debate_stance": "devil",
                    "structured_feedback": {"baseline need": "required"},
                    "ai_revision": {
                        "summary": "baseline_added",
                        "diff": [
                            {
                                "field": "experiments.baselines[1]",
                                "change": "added",
                                "before": "",
                                "after": "blank electrode",
                            }
                        ],
                    },
                    "final_adopted": True,
                }
            ],
        },
    )

    html = HTMLReportRenderer().render(result, enhance=False)

    assert 'id="sec-human-collab"' in html
    assert "baseline_added" in html
    assert "experiments.baselines[1]" in html
