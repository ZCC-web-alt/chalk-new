import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scientific_validation import ScientificValidationEngine, merge_executable_validation


def test_validation_engine_builds_auditable_checks_from_scaling_context():
    hypothesis = {
        "experiments": {
            "baselines": ["Pt/C", "Fe-N4/C"],
            "metrics": ["half-wave potential", "Tafel slope"],
            "design": "RRDE test in 0.1 M KOH",
        },
        "results": {
            "verification_method": "公式推导",
            "derivation": "LLM preliminary derivation",
            "calculated_values": [],
            "comparison_with_literature": [],
            "feasibility_conclusion": "待验证",
        },
    }
    scaling_context = """
=== 定量标度关系 ===

标度关系 1: adsorption energy = 0.5000 * d-band center + 1.2000
  R² = 0.9100, p = 0.0200, n = 5
  依据: Norskov d-band theory
    - Fe-N4: d-band center=-1.2 eV, adsorption energy=0.6 eV
"""

    validation = ScientificValidationEngine().validate(
        hypothesis_data=hypothesis,
        results_verification={"comparison_with_literature": []},
        scaling_context=scaling_context,
    )

    assert validation["execution_log"]["validator"] == "ScientificValidationEngine"
    assert validation["execution_log"]["checks"]
    assert validation["calculated_values"][0]["parameter"] == "adsorption energy"
    assert "0.6000" in validation["calculated_values"][0]["value"]
    assert validation["comparison_with_literature"][0]["status"] == "ready"

    merged = merge_executable_validation(hypothesis, validation)
    assert "代码执行验证" in merged["results"]["verification_method"]
    assert merged["_executable_validation"]["execution_log"]["checks"]


if __name__ == "__main__":
    test_validation_engine_builds_auditable_checks_from_scaling_context()
    print("scientific validation tests passed")
