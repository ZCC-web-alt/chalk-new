import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scientific_toolkit import (
    ScientificStructureGenerator,
    ScientificToolkitEngine,
    build_scientific_toolkit_context,
    extract_scientific_entities,
)


def test_extract_scientific_entities_from_plain_literature():
    text = (
        "The Co-N4-C single atom catalyst shows ORR activity. "
        "LiFePO4 and Fe2O3 were used as materials references. "
        "Ethanol and C6H6 were used during synthesis."
    )

    entities = extract_scientific_entities(text)

    assert "LiFePO4" in entities["materials"]
    assert "Fe2O3" in entities["materials"]
    assert "C6H6" in entities["molecules"]
    assert "ethanol" in entities["molecules"]


def test_extract_scientific_entities_ignores_score_fragments_as_smiles():
    text = (
        "Candidate scores: =0.384 =0.361 === ### =0.663 =0.548. "
        "Valid structures include CC(=O)C, c1ccccc1 and C#N."
    )

    entities = extract_scientific_entities(text)

    bad_fragments = {"=0.384", "=0.361", "===", "###", "=0.663", "=0.548"}
    assert bad_fragments.isdisjoint(set(entities["smiles"]))
    assert "CC(=O)C" in entities["smiles"]
    assert "c1ccccc1" in entities["smiles"]
    assert "C#N" in entities["smiles"]


def test_scientific_toolkit_does_not_analyze_score_fragments_as_molecules(tmp_path):
    engine = ScientificToolkitEngine(output_dir=tmp_path)
    report = engine.run(
        literature_text="Scores in the model output were =0.384, =0.361, === and ###.",
        hypothesis_data={},
        generate_structures=False,
    )

    bad_fragments = {"=0.384", "=0.361", "===", "###"}
    analyzed_inputs = {item["input"] for item in report["molecules"]}
    assert bad_fragments.isdisjoint(analyzed_inputs)


def test_scientific_toolkit_ignores_ocp_dataset_names_as_entities(tmp_path):
    text = (
        "Database evidence mentions OCP, OC20, OC20-Dense, OC22, OC25, ODAC23 "
        "and FAIR-Chem. Valid materials include LiFePO4 and NiFe oxide."
    )

    entities = extract_scientific_entities(text)

    blocked = {"OCP", "OC20", "OC20-Dense", "OC22", "OC25", "ODAC23", "FAIR-Chem"}
    assert blocked.isdisjoint(set(entities["molecules"]))
    assert blocked.isdisjoint(set(entities["materials"]))
    assert blocked.isdisjoint(set(entities["smiles"]))
    assert "LiFePO4" in entities["materials"]

    engine = ScientificToolkitEngine(output_dir=tmp_path)
    report = engine.run(text, hypothesis_data={}, generate_structures=False)
    analyzed_molecules = {item["input"] for item in report["molecules"]}
    analyzed_materials = {item["formula"] for item in report["materials"]}
    assert blocked.isdisjoint(analyzed_molecules)
    assert blocked.isdisjoint(analyzed_materials)


def test_scientific_toolkit_validates_only_hypothesis_targets_when_declared(tmp_path):
    text = (
        "Background references mention LiFePO4, Fe2O3, NiFe oxide and ethanol. "
        "The actual hypothesis is pore-confined Co-N4@COF with 4-hydroxypyridine."
    )
    hypothesis = {
        "paper_title": "Pore confined ORR hypothesis",
        "reaction_type": "ORR",
        "target_materials": ["Co-N4@COF-4HP", "Co-N4@COF-Py"],
        "active_sites": ["Co-N4 pore A"],
        "ligands": ["4-hydroxypyridine", "pyridine"],
        "adsorbates": ["*OOH", "*O", "*OH"],
        "properties_to_validate": ["adsorption_energy", "ORR 4e water pathway"],
        "controls": ["Co-N4@COF-Py"],
    }

    entities = extract_scientific_entities(text, hypothesis)

    assert "4-hydroxypyridine" in entities["molecules"]
    assert "pyridine" in entities["molecules"]
    assert "Co-N4@COF-4HP" in entities["materials"]
    assert "Co-N4@COF-Py" in entities["materials"]
    assert "LiFePO4" not in entities["materials"]
    assert "Fe2O3" not in entities["materials"]

    engine = ScientificToolkitEngine(output_dir=tmp_path)
    report = engine.run(text, hypothesis_data=hypothesis, generate_structures=False)
    analyzed_molecules = {item["input"] for item in report["molecules"]}
    analyzed_materials = {item["formula"] for item in report["materials"]}

    assert {"4-hydroxypyridine", "pyridine"}.issubset(analyzed_molecules)
    assert "LiFePO4" not in analyzed_materials
    assert "Fe2O3" not in analyzed_materials
    assert report["target_validation"]["mode"] == "hypothesis_targets"
    assert report["target_validation"]["adsorbates"] == ["*OOH", "*O", "*OH"]
    assert "LiFePO4" in report["target_validation"]["background_entities"]["materials"]


def test_structure_generator_parses_tagged_llm_files_safely(tmp_path):
    class FakeGenerator(ScientificStructureGenerator):
        def _call_llm(self, prompt, timeout=None):
            return """
            {"reasoning": "literature names LiFePO4 explicitly", "structures": [
              {
                "name": "LiFePO4 candidate",
                "formula": "LiFePO4",
                "format": "POSCAR",
                "confidence": 0.62,
                "source_evidence": "LiFePO4 appears in the source text",
                "content": "LiFePO4\\n1.0\\n10 0 0\\n0 6 0\\n0 0 4\\nLi Fe P O\\n1 1 1 4\\nDirect\\n0 0 0\\n0.5 0.5 0.5\\n0.25 0.25 0.25\\n0.1 0.1 0.1\\n0.2 0.2 0.2\\n0.3 0.3 0.3\\n0.4 0.4 0.4"
              }
            ]}
            """

    generator = FakeGenerator(output_dir=tmp_path)
    report = generator.generate(
        literature_text="LiFePO4 is the target material.",
        hypothesis_data={"paper_title": "LiFePO4 test"},
    )

    assert report["summary"]["generated_files"] == 1
    saved_path = Path(report["structures"][0]["path"])
    assert saved_path.exists()
    assert saved_path.parent == tmp_path.resolve()
    assert report["structures"][0]["format"] == "POSCAR"


def test_scientific_toolkit_runs_without_optional_dependencies(tmp_path):
    engine = ScientificToolkitEngine(output_dir=tmp_path)
    report = engine.run(
        literature_text="LiFePO4 and ethanol are discussed.",
        hypothesis_data={
            "paper_title": "Optional dependency smoke",
            "technical_details": "DFT using VASP and molecule descriptor checks.",
        },
        generate_structures=False,
    )

    assert report["summary"]["molecules_checked"] >= 1
    assert report["summary"]["materials_checked"] >= 1
    assert "tool_versions" in report
    assert report["status"] in {"ok", "partial"}

    context = build_scientific_toolkit_context(report)
    assert "RDKit" in context
    assert "pymatgen" in context


if __name__ == "__main__":
    test_extract_scientific_entities_from_plain_literature()
    test_extract_scientific_entities_ignores_score_fragments_as_smiles()
    from tempfile import TemporaryDirectory

    with TemporaryDirectory() as td:
        test_scientific_toolkit_does_not_analyze_score_fragments_as_molecules(Path(td))
    with TemporaryDirectory() as td:
        test_scientific_toolkit_validates_only_hypothesis_targets_when_declared(Path(td))
    with TemporaryDirectory() as td:
        test_structure_generator_parses_tagged_llm_files_safely(Path(td))
    with TemporaryDirectory() as td:
        test_scientific_toolkit_runs_without_optional_dependencies(Path(td))
    print("scientific toolkit tests passed")
