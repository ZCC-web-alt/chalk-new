import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hypothesis_workflow_exporter import (
    build_hypothesis_workflow_package,
    export_hypothesis_workflow_package,
)


def _write_poscar(path: Path) -> Path:
    path.write_text(
        "\n".join(
            [
                "LiFePO4 candidate",
                "1.0",
                "10.0 0.0 0.0",
                "0.0 6.0 0.0",
                "0.0 0.0 4.0",
                "Li Fe P O",
                "1 1 1 4",
                "Direct",
                "0.0 0.0 0.0",
                "0.5 0.5 0.5",
                "0.25 0.25 0.25",
                "0.1 0.1 0.1",
                "0.2 0.2 0.2",
                "0.3 0.3 0.3",
                "0.4 0.4 0.4",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def _sample_hypothesis(poscar: Path) -> dict:
    return {
        "title": "LiFePO4 coating hypothesis",
        "hypothesis": "Carbon coating improves LiFePO4 rate capability.",
        "confidence": 8,
        "_scientific_toolkit": {
            "status": "ok",
            "summary": {
                "structures_generated": 1,
                "structures_parsed": 1,
                "atomate2_workflows_planned": 1,
                "atomate2_jobs_planned": 2,
            },
            "generated_structures": {
                "structures": [
                    {
                        "name": "LiFePO4 candidate",
                        "formula": "LiFePO4",
                        "format": "POSCAR",
                        "path": str(poscar),
                        "confidence": 0.72,
                        "source_evidence": "LiFePO4 appears in the hypothesis source text.",
                    }
                ]
            },
            "structure_analyses": [
                {
                    "name": "LiFePO4 candidate",
                    "formula": "LiFePO4",
                    "format": "POSCAR",
                    "path": str(poscar),
                    "status": "ok",
                    "formula_pretty": "LiFePO4",
                    "num_sites": 7,
                    "density": 3.21,
                    "elements": ["Fe", "Li", "O", "P"],
                    "lattice": {
                        "a": 10.0,
                        "b": 6.0,
                        "c": 4.0,
                        "alpha": 90.0,
                        "beta": 90.0,
                        "gamma": 90.0,
                        "volume": 240.0,
                    },
                }
            ],
            "reproducibility_plan": [
                {
                    "target": "LiFePO4",
                    "calculation": "VASP geometry optimization + SCF",
                    "incar_relax": {"SYSTEM": "Relax", "ENCUT": 520, "NSW": 100},
                    "incar_scf": {"SYSTEM": "SCF", "ENCUT": 520, "NSW": 0},
                    "kpoints_hint": "Start from 6x6x6 Gamma-centered mesh.",
                    "potcar_hints": {"Li": "Li_sv", "Fe": "Fe_pv", "P": "P", "O": "O"},
                    "notes": "Use Qwen-generated structures as candidate inputs only.",
                }
            ],
            "atomate2_dryrun": {
                "status": "ok",
                "workflow_kind": "double_relax",
                "summary": {
                    "structures_seen": 1,
                    "workflows_planned": 1,
                    "jobs_planned": 2,
                    "warnings": 0,
                },
                "workflows": [
                    {
                        "name": "DoubleRelaxMaker",
                        "workflow_type": "DoubleRelaxMaker",
                        "job_count": 2,
                        "job_names": ["relax 1", "relax 2"],
                        "dry_run_only": True,
                        "execution_policy": "not_submitted",
                    }
                ],
                "warnings": [],
            },
            "warnings": [],
        },
    }


def test_build_package_normalizes_scientific_toolkit(tmp_path):
    poscar = _write_poscar(tmp_path / "candidate.POSCAR")
    package = build_hypothesis_workflow_package(
        _sample_hypothesis(poscar),
        report_path=str(tmp_path / "report.html"),
    )

    assert package["schema_version"] == "1.0"
    assert package["summary"]["structure_candidates"] == 1
    assert package["summary"]["parsed_structures"] == 1
    assert package["summary"]["atomate2_workflows"] == 1
    assert package["structures"][0]["format"] == "POSCAR"
    assert package["structures"][0]["analysis"]["formula_pretty"] == "LiFePO4"
    assert package["vasp_recommendations"][0]["target"] == "LiFePO4"
    assert package["atomate2_dryrun"]["dry_run_only"] is True
    assert package["provenance"]["source_report_path"].endswith("report.html")


def test_export_package_writes_reproducible_workflow_folder(tmp_path):
    poscar = _write_poscar(tmp_path / "candidate.POSCAR")
    package = build_hypothesis_workflow_package(_sample_hypothesis(poscar))

    export_dir = export_hypothesis_workflow_package(package, tmp_path / "workflow_out")

    assert (export_dir / "README.md").exists()
    assert (export_dir / "hypothesis_summary.json").exists()
    assert (export_dir / "structures" / "01_candidate.POSCAR").exists()
    assert (export_dir / "vasp" / "INCAR.relax").exists()
    assert (export_dir / "vasp" / "INCAR.scf").exists()
    assert (export_dir / "vasp" / "KPOINTS").exists()
    assert (export_dir / "vasp" / "POTCAR_HINTS.txt").exists()
    assert (export_dir / "atomate2" / "atomate2_dryrun.json").exists()
    assert (export_dir / "atomate2" / "jobflow_summary.md").exists()
    assert (export_dir / "provenance" / "scientific_toolkit.json").exists()
    assert (export_dir / "provenance" / "source_report_path.txt").exists()

    readme = (export_dir / "README.md").read_text(encoding="utf-8")
    assert "dry-run" in readme
    assert "not submitted" in readme
    assert "human review" in readme
    assert "Qwen-generated candidate" in readme

    dryrun = json.loads((export_dir / "atomate2" / "atomate2_dryrun.json").read_text(encoding="utf-8"))
    assert dryrun["summary"]["workflows_planned"] == 1


def test_export_package_writes_linux_text_format(tmp_path):
    poscar = _write_poscar(tmp_path / "candidate.POSCAR")
    poscar.write_bytes(b"\xef\xbb\xbfLiFePO4 candidate\r\n1.0\r\n")
    package = build_hypothesis_workflow_package(_sample_hypothesis(poscar))

    export_dir = export_hypothesis_workflow_package(package, tmp_path / "linux_text")

    text_files = [
        export_dir / "README.md",
        export_dir / "hypothesis_summary.json",
        export_dir / "structures" / "01_candidate.POSCAR",
        export_dir / "structures" / "manifest.json",
        export_dir / "vasp" / "INCAR.relax",
        export_dir / "vasp" / "INCAR.scf",
        export_dir / "vasp" / "KPOINTS",
        export_dir / "vasp" / "POTCAR_HINTS.txt",
        export_dir / "atomate2" / "atomate2_dryrun.json",
        export_dir / "atomate2" / "jobflow_summary.md",
        export_dir / "provenance" / "scientific_toolkit.json",
        export_dir / "provenance" / "source_report_path.txt",
    ]

    for path in text_files:
        raw = path.read_bytes()
        assert b"\r\n" not in raw, path
        assert b"\r" not in raw, path
        assert not raw.startswith(b"\xef\xbb\xbf"), path
        assert raw.endswith(b"\n"), path

    readme = (export_dir / "README.md").read_text(encoding="utf-8")
    assert "Linux text format" in readme
    assert "dos2unix INCAR POSCAR KPOINTS POTCAR" in readme
    assert "sed -i 's/\\r$//' INCAR POSCAR KPOINTS POTCAR" in readme


def test_export_package_without_structures_keeps_review_warning(tmp_path):
    hypothesis = {
        "title": "No structure hypothesis",
        "_scientific_toolkit": {
            "status": "partial",
            "summary": {"structures_generated": 0, "structures_parsed": 0},
            "generated_structures": {"structures": []},
            "structure_analyses": [],
            "reproducibility_plan": [],
            "atomate2_dryrun": {"status": "skipped", "summary": {"workflows_planned": 0, "jobs_planned": 0}},
            "warnings": ["No structure was generated."],
        },
    }

    package = build_hypothesis_workflow_package(hypothesis)
    export_dir = export_hypothesis_workflow_package(package, tmp_path / "no_structures")

    assert package["summary"]["structure_candidates"] == 0
    assert package["risk_notices"]
    readme = (export_dir / "README.md").read_text(encoding="utf-8")
    assert "No parsed structure is available" in readme
    assert "manual review" in readme


if __name__ == "__main__":
    with TemporaryDirectory() as td:
        test_build_package_normalizes_scientific_toolkit(Path(td))
    with TemporaryDirectory() as td:
        test_export_package_writes_reproducible_workflow_folder(Path(td))
    with TemporaryDirectory() as td:
        test_export_package_writes_linux_text_format(Path(td))
    with TemporaryDirectory() as td:
        test_export_package_without_structures_keeps_review_warning(Path(td))
    print("hypothesis workflow export tests passed")
