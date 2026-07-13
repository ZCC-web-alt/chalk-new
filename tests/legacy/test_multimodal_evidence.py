import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from data_cleaner import CleanedDataPoint
from data_miner import Association, MiningReport, QuantitativeReport, ScalingRelation
from multimodal_pipeline import MultimodalResult, build_multimodal_evidence


def test_multimodal_evidence_serializes_figures_points_and_associations():
    point = CleanedDataPoint(
        parameter="d-band center",
        parameter_cn="d带中心",
        value=-1.23,
        unit="eV",
        original_value="-1.23 eV",
        original_unit="eV",
        source="Fig. 2",
        precision=2,
    )
    result = MultimodalResult(
        image_path="data/fig2.png",
        image_type="DFT plot",
        source_label="Fig. 2",
        summary="d-band descriptor plot",
        cleaned_points=[point],
    )
    mining_report = MiningReport(
        total_associations=1,
        rule_based=1,
        associations=[
            Association(
                source_a="Fig. 2",
                source_b="Fig. 3",
                param_a="d-band center",
                param_b="adsorption energy",
                relation_type="descriptor",
                description="d带中心调控吸附能",
                scientific_basis="d-band theory",
                confidence=0.82,
                value_a=-1.23,
                value_b=0.58,
                unit_a="eV",
                unit_b="eV",
            )
        ],
    )

    evidence = build_multimodal_evidence([result], mining_report)

    assert evidence["summary"]["figures_analyzed"] == 1
    assert evidence["summary"]["cleaned_points"] == 1
    assert evidence["figures"][0]["points"][0]["parameter"] == "d-band center"
    assert evidence["associations"][0]["relation_type"] == "descriptor"


def test_quantitative_report_to_dict_keeps_scaling_relations():
    report = QuantitativeReport(
        scaling_relations=[
            ScalingRelation(
                param_x="d-band center",
                param_y="adsorption energy",
                slope=0.5,
                intercept=1.2,
                r_squared=0.91,
                p_value=0.02,
                n_points=5,
                equation="adsorption energy = 0.5000 * d-band center + 1.2000",
            )
        ]
    )

    data = report.to_dict()
    assert data["summary"]["scaling_relation_count"] == 1
    assert data["scaling_relations"][0]["param_y"] == "adsorption energy"


if __name__ == "__main__":
    test_multimodal_evidence_serializes_figures_points_and_associations()
    test_quantitative_report_to_dict_keeps_scaling_relations()
    print("multimodal evidence tests passed")
