import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evidence_ledger import gate_references


def test_reference_gate_filters_method_only_match():
    hypothesis = {
        "problem_statement": "Fe-N4单原子催化剂中自旋态会影响ORR氧还原反应的H2O2法拉第效率。",
        "rationale": "我们假设Fe-N4在低自旋状态下可以抑制O-O键断裂并提升2e ORR选择性。",
        "technical_details": "采用DFT、COHP、RRDE验证O-O键级和H2O2法拉第效率。",
    }
    refs = [
        {
            "title": "Spin-dependent oxygen reduction reaction on Fe-N4 single atom catalysts",
            "abstract": "Fe-N4 single atom catalysts for ORR and hydrogen peroxide selectivity using DFT.",
            "doi": "10.1000/orr",
            "source_platform": "semantic_scholar",
        },
        {
            "title": "New considerations on conversion of chloromethane to olefins from a DFT perspective",
            "abstract": "Chloromethane conversion to olefins on zeolite catalysts.",
            "doi": "10.1000/unrelated",
            "source_platform": "crossref",
        },
        {
            "title": "user-imported-source.pdf",
            "journal": "pdf",
            "source_platform": "user_imported",
            "is_user_document": True,
        },
    ]

    accepted, ledger = gate_references(hypothesis, refs)
    titles = [r["title"] for r in accepted]

    assert "Spin-dependent oxygen reduction reaction on Fe-N4 single atom catalysts" in titles
    assert "user-imported-source.pdf" in titles
    assert "New considerations on conversion of chloromethane to olefins from a DFT perspective" not in titles
    assert ledger["summary"]["rejected_reference_count"] == 1


if __name__ == "__main__":
    test_reference_gate_filters_method_only_match()
    print("evidence gate tests passed")
