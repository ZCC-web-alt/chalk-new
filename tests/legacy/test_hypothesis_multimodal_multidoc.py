from __future__ import annotations

from chalk_app.agents.agent_framework import format_structured_multimodal_evidence_for_query


def test_multimodal_evidence_query_uses_structured_points_without_raw_summary() -> None:
    evidence = {
        "figures": [
            {
                "source_label": "Fig. 2",
                "image_type": "volcano plot",
                "summary": "RAW_SUMMARY_SHOULD_NOT_APPEAR",
                "points": [
                    {
                        "parameter_cn": "OOH adsorption energy",
                        "value": "0.82",
                        "unit": "eV",
                        "source": "Fig. 2",
                        "is_suspicious": False,
                    }
                ],
            }
        ],
        "associations": [
            {
                "description": "OER descriptor relation",
                "param_a": "OOH",
                "param_b": "overpotential",
                "confidence": 0.8,
            }
        ],
    }

    text = format_structured_multimodal_evidence_for_query(evidence)

    assert "OOH adsorption energy" in text
    assert "0.82 eV" in text
    assert "OER descriptor relation" in text
    assert "RAW_SUMMARY_SHOULD_NOT_APPEAR" not in text
