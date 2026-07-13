# -*- coding: utf-8 -*-
"""
Scientific toolkit integration for Chalk.

This module adds an optional executable science layer around RDKit and
pymatgen. It is intentionally dependency-tolerant: Chalk can generate reports
and audit traces even when the heavy scientific packages are not installed,
then upgrades to real descriptor/structure analysis as soon as they are
available.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from llm_client import LLMConfig, _chat, _ensure_config
from atomate2_dryrun import build_atomate2_context, run_atomate2_dryrun
from vasp_defaults import get_incar_template, get_potcar_suffix


MAX_STRUCTURE_TEXT_CHARS = 20000
MAX_STRUCTURE_FILES = 3
MAX_ENTITY_COUNT = 8
DEFAULT_OUTPUT_DIR = Path("data") / "generated_structures"

_FORMULA_RE = re.compile(r"\b(?:[A-Z][a-z]?\d*){2,}\b")
_SMILES_RE = re.compile(r"(?<![A-Za-z])(?:[BCNOFPSIclbrcnops0-9@+\-\[\]\(\)=#$\\/\.]{2,})(?![A-Za-z])")
_SMILES_ATOM_RE = re.compile(r"Br|Cl|[BCNOFPSIbcnoops]")
_NUMBER_FRAGMENT_RE = re.compile(r"^[<>=+\-~]*\d+(?:\.\d+)?(?:e[+\-]?\d+)?$", re.IGNORECASE)
_SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9_.-]+")

_VALID_ELEMENT_SYMBOLS = {
    "H", "He", "Li", "Be", "B", "C", "N", "O", "F", "Ne",
    "Na", "Mg", "Al", "Si", "P", "S", "Cl", "Ar", "K", "Ca",
    "Sc", "Ti", "V", "Cr", "Mn", "Fe", "Co", "Ni", "Cu", "Zn",
    "Ga", "Ge", "As", "Se", "Br", "Kr", "Rb", "Sr", "Y", "Zr",
    "Nb", "Mo", "Tc", "Ru", "Rh", "Pd", "Ag", "Cd", "In", "Sn",
    "Sb", "Te", "I", "Xe", "Cs", "Ba", "La", "Ce", "Pr", "Nd",
    "Pm", "Sm", "Eu", "Gd", "Tb", "Dy", "Ho", "Er", "Tm", "Yb",
    "Lu", "Hf", "Ta", "W", "Re", "Os", "Ir", "Pt", "Au", "Hg",
    "Tl", "Pb", "Bi", "Po", "At", "Rn", "Fr", "Ra", "Ac", "Th",
    "Pa", "U", "Np", "Pu", "Am", "Cm", "Bk", "Cf", "Es", "Fm",
    "Md", "No", "Lr", "Rf", "Db", "Sg", "Bh", "Hs", "Mt", "Ds",
    "Rg", "Cn", "Nh", "Fl", "Mc", "Lv", "Ts", "Og",
}

COMMON_MOLECULE_NAMES = {
    "ethanol",
    "methanol",
    "acetone",
    "benzene",
    "toluene",
    "phenol",
    "aniline",
    "water",
    "ammonia",
    "ethylene glycol",
    "glycerol",
    "dimethylformamide",
    "dmf",
    "dimethyl sulfoxide",
    "dmso",
    "tetrahydrofuran",
    "thf",
    "pyridine",
    "4-hydroxypyridine",
    "4-hydroxy pyridine",
}

COMMON_NAME_TO_SMILES = {
    "water": "O",
    "ethanol": "CCO",
    "methanol": "CO",
    "acetone": "CC(=O)C",
    "benzene": "c1ccccc1",
    "toluene": "Cc1ccccc1",
    "phenol": "Oc1ccccc1",
    "aniline": "Nc1ccccc1",
    "ammonia": "N",
    "ethylene glycol": "OCCO",
    "glycerol": "OCC(O)CO",
    "dimethylformamide": "CN(C)C=O",
    "dmf": "CN(C)C=O",
    "dimethyl sulfoxide": "CS(C)=O",
    "dmso": "CS(C)=O",
    "tetrahydrofuran": "C1CCOC1",
    "thf": "C1CCOC1",
    "pyridine": "c1ccncc1",
    "4-hydroxypyridine": "Oc1ccncc1",
    "4-hydroxy pyridine": "Oc1ccncc1",
}

NON_MATERIAL_FORMULAS = {
    "H2O",
    "CO2",
    "CO",
    "NH3",
    "CH4",
    "O2",
    "N2",
    "H2",
}

DATA_SOURCE_ENTITY_STOPLIST = {
    "OCP",
    "OC20",
    "OC20-DENSE",
    "OC22",
    "OC25",
    "ODAC23",
    "FAIR-CHEM",
    "ADSORBML",
    "OPEN CATALYST PROJECT",
}


def _dedupe_keep_order(values: List[str], limit: int = MAX_ENTITY_COUNT) -> List[str]:
    seen = set()
    out = []
    for value in values:
        value = str(value).strip()
        if not value:
            continue
        key = value.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(value)
        if len(out) >= limit:
            break
    return out


def _is_data_source_entity(value: str) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    normalized = re.sub(r"\s+", " ", text).strip().upper().replace("_", "-")
    return normalized in DATA_SOURCE_ENTITY_STOPLIST


def _dependency_version(module_name: str, dist_name: Optional[str] = None) -> str:
    try:
        return importlib.metadata.version(dist_name or module_name)
    except Exception:
        try:
            module = __import__(module_name)
            return str(getattr(module, "__version__", "installed"))
        except Exception:
            return "not_installed"


def _try_import_rdkit():
    try:
        from rdkit import Chem  # type: ignore
        from rdkit.Chem import Crippen, Descriptors, Lipinski, rdMolDescriptors  # type: ignore
        try:
            from rdkit import RDLogger  # type: ignore

            RDLogger.DisableLog("rdApp.error")
        except Exception:
            pass

        return Chem, Descriptors, Crippen, Lipinski, rdMolDescriptors, ""
    except Exception as exc:
        return None, None, None, None, None, str(exc)


def _try_import_pymatgen():
    try:
        from pymatgen.core import Composition, Structure  # type: ignore

        return Composition, Structure, ""
    except Exception as exc:
        return None, None, str(exc)


def _formula_elements(formula: str) -> List[str]:
    return re.findall(r"[A-Z][a-z]?", formula or "")


def _looks_like_material_formula(formula: str) -> bool:
    if _is_data_source_entity(formula):
        return False
    if formula in NON_MATERIAL_FORMULAS:
        return False
    elements = _formula_elements(formula)
    if any(el not in _VALID_ELEMENT_SYMBOLS for el in elements):
        return False
    if formula.isupper() and not any(ch.isdigit() for ch in formula):
        return False
    if "H" in elements and _looks_like_molecule_formula(formula):
        return False
    if len(set(elements)) < 2:
        return False
    if len(formula) <= 2:
        return False
    return True


def _looks_like_molecule_formula(formula: str) -> bool:
    if _is_data_source_entity(formula):
        return False
    elements = set(_formula_elements(formula))
    return (
        bool(elements)
        and any(ch.isdigit() for ch in str(formula or ""))
        and elements.issubset({"C", "H", "N", "O", "S", "P", "F", "Cl", "Br", "I"})
        and "C" in elements
    )


def _is_plausible_smiles_candidate(candidate: str) -> bool:
    candidate = str(candidate or "").strip().strip(".,;:()[]{}<>")
    if _is_data_source_entity(candidate):
        return False
    if len(candidate) < 2:
        return False
    if _NUMBER_FRAGMENT_RE.fullmatch(candidate):
        return False
    if not any(ch.isalpha() for ch in candidate):
        return False
    if not _SMILES_ATOM_RE.search(candidate):
        return False
    if re.fullmatch(r"[-=+#./\\]+", candidate):
        return False
    if candidate.count("=") == len(candidate) or candidate.count("#") == len(candidate):
        return False

    Chem, _Descriptors, _Crippen, _Lipinski, _rdMolDescriptors, _import_error = _try_import_rdkit()
    if Chem is not None:
        try:
            if Chem.MolFromSmiles(candidate) is None:
                return False
        except Exception:
            return False
    return True


def _flatten_values(value: Any) -> List[str]:
    items: List[str] = []
    if value is None:
        return items
    if isinstance(value, str):
        text = value.strip()
        if text:
            items.append(text)
        return items
    if isinstance(value, dict):
        for nested in value.values():
            items.extend(_flatten_values(nested))
        return items
    if isinstance(value, (list, tuple, set)):
        for nested in value:
            items.extend(_flatten_values(nested))
        return items
    text = str(value).strip()
    if text:
        items.append(text)
    return items


def _normalize_adsorbate_label(value: Any) -> str:
    text = str(value or "").strip().replace(" ", "")
    if not text:
        return ""
    aliases = {
        "O*": "*O",
        "*O": "*O",
        "OH*": "*OH",
        "*OH": "*OH",
        "OOH*": "*OOH",
        "*OOH": "*OOH",
        "H*": "*H",
        "*H": "*H",
        "CO*": "*CO",
        "*CO": "*CO",
        "CO2*": "*CO2",
        "*CO2": "*CO2",
        "COOH*": "*COOH",
        "*COOH": "*COOH",
        "OCHO*": "*OCHO",
        "*OCHO": "*OCHO",
    }
    upper = text.upper()
    if upper in aliases:
        return aliases[upper]
    if upper.startswith("*"):
        return "*" + upper[1:]
    if upper.endswith("*"):
        return "*" + upper[:-1]
    if upper in {"O", "OH", "OOH", "H", "CO", "CO2", "COOH", "OCHO"}:
        return "*" + upper
    return text


def _target_entities_from_hypothesis(hypothesis_data: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not isinstance(hypothesis_data, dict):
        return {"has_targets": False}

    materials = []
    for key in ("target_materials", "target_material", "materials", "candidate_materials", "catalyst", "catalyst_system"):
        materials.extend(_flatten_values(hypothesis_data.get(key)))
    controls = _flatten_values(hypothesis_data.get("controls") or hypothesis_data.get("control_group"))
    materials.extend(controls)

    record = hypothesis_data.get("experiment_record_card", {})
    if isinstance(record, dict):
        for key in ("material_system", "catalyst_system", "experimental_group", "control_group"):
            materials.extend(_flatten_values(record.get(key)))

    ligands = []
    for key in ("ligands", "target_ligands", "molecules", "target_molecules", "organic_modifiers"):
        ligands.extend(_flatten_values(hypothesis_data.get(key)))

    active_sites = _flatten_values(hypothesis_data.get("active_sites") or hypothesis_data.get("active_site"))
    adsorbates = [
        _normalize_adsorbate_label(value)
        for value in _flatten_values(hypothesis_data.get("adsorbates") or hypothesis_data.get("target_adsorbates"))
    ]
    properties = _flatten_values(hypothesis_data.get("properties_to_validate") or hypothesis_data.get("target_properties"))

    materials = [value for value in materials if value and not _is_data_source_entity(value)]
    ligands = [value for value in ligands if value and not _is_data_source_entity(value)]
    controls = [value for value in controls if value and not _is_data_source_entity(value)]
    adsorbates = [value for value in adsorbates if value and not _is_data_source_entity(value)]

    return {
        "has_targets": bool(materials or ligands or active_sites or adsorbates or properties),
        "materials": _dedupe_keep_order(materials, limit=MAX_ENTITY_COUNT),
        "molecules": _dedupe_keep_order(ligands, limit=MAX_ENTITY_COUNT),
        "ligands": _dedupe_keep_order(ligands, limit=MAX_ENTITY_COUNT),
        "controls": _dedupe_keep_order(controls, limit=MAX_ENTITY_COUNT),
        "active_sites": _dedupe_keep_order(active_sites, limit=MAX_ENTITY_COUNT),
        "adsorbates": _dedupe_keep_order(adsorbates, limit=MAX_ENTITY_COUNT),
        "properties_to_validate": _dedupe_keep_order(properties, limit=MAX_ENTITY_COUNT),
        "reaction_type": str(hypothesis_data.get("reaction_type", "") or ""),
    }


def _extract_open_scientific_entities(source: str) -> Dict[str, List[str]]:
    formulas = _dedupe_keep_order(
        [value for value in _FORMULA_RE.findall(source) if not _is_data_source_entity(value)],
        limit=MAX_ENTITY_COUNT * 2,
    )
    materials = [f for f in formulas if _looks_like_material_formula(f)]
    molecules = [f for f in formulas if _looks_like_molecule_formula(f)]

    lower_source = source.lower()
    for name in COMMON_MOLECULE_NAMES:
        if re.search(rf"\b{re.escape(name)}\b", lower_source):
            molecules.append(name)

    smiles = []
    for match in _SMILES_RE.findall(source):
        candidate = match.strip(".,;:()[]")
        if _is_data_source_entity(candidate):
            continue
        has_smiles_syntax = (
            any(ch in candidate for ch in ("=", "#", "(", ")", "[", "]", "@"))
            or any(ch.isdigit() for ch in candidate)
            or any(ch in candidate for ch in ("c", "n", "o", "p", "s", "b"))
        )
        if has_smiles_syntax and _is_plausible_smiles_candidate(candidate):
            smiles.append(candidate)

    return {
        "molecules": _dedupe_keep_order(molecules, limit=MAX_ENTITY_COUNT),
        "materials": _dedupe_keep_order(materials, limit=MAX_ENTITY_COUNT),
        "smiles": _dedupe_keep_order(smiles, limit=MAX_ENTITY_COUNT),
    }


def extract_scientific_entities(text: str, hypothesis_data: Optional[Dict[str, Any]] = None) -> Dict[str, List[str]]:
    """Extract molecule/material candidates from plain literature and hypothesis JSON."""
    if not isinstance(text, str):
        text = str(text or "")
    extra_text = ""
    if isinstance(hypothesis_data, dict):
        for key in ("problem_statement", "rationale", "technical_details", "methods", "paper_abstract"):
            value = hypothesis_data.get(key)
            if value:
                extra_text += "\n" + str(value)
        datasets = hypothesis_data.get("datasets", {})
        if isinstance(datasets, dict):
            extra_text += "\n" + " ".join(str(v) for v in datasets.values() if v)
    source = f"{text}\n{extra_text}"

    targets = _target_entities_from_hypothesis(hypothesis_data)
    if targets.get("has_targets"):
        background = _extract_open_scientific_entities(text)
        return {
            "molecules": targets.get("molecules", []),
            "materials": targets.get("materials", []),
            "smiles": [],
            "adsorbates": targets.get("adsorbates", []),
            "active_sites": targets.get("active_sites", []),
            "properties_to_validate": targets.get("properties_to_validate", []),
            "controls": targets.get("controls", []),
            "background_entities": background,
            "extraction_mode": "hypothesis_targets",
        }

    result = _extract_open_scientific_entities(source)
    result["background_entities"] = {"molecules": [], "materials": [], "smiles": []}
    result["extraction_mode"] = "open_extraction"
    return result


def _safe_structure_filename(name: str, fmt: str, idx: int) -> str:
    ext = "cif" if fmt.lower() == "cif" else fmt.upper()
    base = _SAFE_FILENAME_RE.sub("_", (name or f"structure_{idx}").strip())[:48].strip("._")
    if not base:
        base = f"structure_{idx}"
    return f"{idx:02d}_{base}.{ext}"


def _resolve_output_dir(output_dir: Optional[Path | str]) -> Path:
    path = Path(output_dir or DEFAULT_OUTPUT_DIR).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    path = path.resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _safe_write_structure(output_dir: Path, filename: str, content: str) -> Path:
    target = (output_dir / filename).resolve()
    if output_dir not in target.parents and target != output_dir:
        raise ValueError("structure path escapes output directory")
    text = (content or "").lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n").strip() + "\n"
    if len(text) > MAX_STRUCTURE_TEXT_CHARS:
        raise ValueError("structure file is too large")
    with open(target, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    return target


def _extract_json_object(text: str) -> Dict[str, Any]:
    if not text:
        return {}
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?", "", cleaned, flags=re.I).strip()
        cleaned = re.sub(r"```$", "", cleaned).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start >= 0 and end > start:
        return json.loads(cleaned[start : end + 1])
    return {}


def _extract_hypothesis_text(hypothesis_data: Dict[str, Any]) -> str:
    if not isinstance(hypothesis_data, dict):
        return ""
    parts = []
    for key in ("problem_statement", "rationale", "technical_details", "methods", "paper_abstract"):
        if hypothesis_data.get(key):
            parts.append(f"{key}: {hypothesis_data[key]}")
    datasets = hypothesis_data.get("datasets", {})
    if isinstance(datasets, dict):
        parts.append("datasets: " + json.dumps(datasets, ensure_ascii=False))
    return "\n".join(parts)


@dataclass
class ScientificStructureGenerator:
    """Generate candidate CIF/POSCAR/CONTCAR files from literature via Qwen."""

    config: Optional[LLMConfig] = None
    output_dir: Optional[Path | str] = None
    max_files: int = MAX_STRUCTURE_FILES

    def __post_init__(self):
        self.config = _ensure_config(self.config)
        self.output_dir = _resolve_output_dir(self.output_dir)

    def _call_llm(self, prompt: str, timeout: Optional[int] = None) -> str:
        return _chat(
            prompt,
            self.config,
            task="vasp_extract",
            system_prompt=(
                "你是一名材料计算建模专家。你只能根据用户给出的文献事实生成候选结构文件，"
                "必须明确置信度和证据来源。若文献缺少晶格或坐标信息，应生成最小可复核的"
                "候选模型并标记为低置信度，不要伪装成实验真实结构。"
            ),
            timeout=timeout or 180,
        )

    def generate(
        self,
        literature_text: str,
        hypothesis_data: Optional[Dict[str, Any]] = None,
        entities: Optional[Dict[str, List[str]]] = None,
    ) -> Dict[str, Any]:
        entities = entities or extract_scientific_entities(literature_text, hypothesis_data)
        materials = entities.get("materials", [])[: self.max_files]
        if not materials:
            return {
                "status": "skipped",
                "summary": {"generated_files": 0},
                "structures": [],
                "warnings": ["未从文献或假设中识别到可建模材料化学式。"],
            }

        prompt = self._build_prompt(literature_text, hypothesis_data or {}, materials)
        raw = self._call_llm(prompt, timeout=180)
        parsed = _extract_json_object(raw)
        structures = parsed.get("structures", []) if isinstance(parsed, dict) else []
        if not isinstance(structures, list):
            structures = []

        saved = []
        warnings = []
        for idx, item in enumerate(structures[: self.max_files], 1):
            if not isinstance(item, dict):
                warnings.append(f"结构候选 {idx} 不是 JSON 对象，已跳过。")
                continue
            fmt = str(item.get("format", "")).strip().upper()
            if fmt not in {"CIF", "POSCAR", "CONTCAR"}:
                warnings.append(f"结构候选 {idx} 格式 {fmt or '空'} 不受支持，已跳过。")
                continue
            content = str(item.get("content", "") or "")
            if len(content.strip()) < 20:
                warnings.append(f"结构候选 {idx} 内容过短，已跳过。")
                continue
            name = str(item.get("name") or item.get("formula") or f"structure_{idx}")
            try:
                filename = _safe_structure_filename(name, fmt, idx)
                path = _safe_write_structure(self.output_dir, filename, content)
            except Exception as exc:
                warnings.append(f"结构候选 {idx} 保存失败: {exc}")
                continue

            saved.append(
                {
                    "name": name,
                    "formula": str(item.get("formula", "")),
                    "format": fmt,
                    "path": str(path),
                    "confidence": item.get("confidence", 0),
                    "source_evidence": str(item.get("source_evidence", "")),
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                }
            )

        return {
            "status": "ok" if saved else "partial",
            "summary": {"generated_files": len(saved)},
            "structures": saved,
            "warnings": warnings,
            "raw_reasoning": str(parsed.get("reasoning", ""))[:1200] if isinstance(parsed, dict) else "",
        }

    def _build_prompt(self, literature_text: str, hypothesis_data: Dict[str, Any], materials: List[str]) -> str:
        text = (literature_text or "")[:12000]
        hypo = _extract_hypothesis_text(hypothesis_data)[:4000]
        materials_text = ", ".join(materials)
        return (
            "请根据以下纯文献内容和当前科学假设，为候选材料生成可供 pymatgen 解析的结构文件。\n"
            "输出必须是严格 JSON，不要 markdown，不要解释性前后缀。\n\n"
            "安全与真实性规则：\n"
            "1. 只能生成 CIF、POSCAR 或 CONTCAR 三种格式。\n"
            "2. 如果文献没有明确晶格参数/坐标，允许生成低置信度候选模型，但必须在 source_evidence 中说明缺口。\n"
            "3. 不要声称低置信度候选是实验真实结构。\n"
            "4. 每个 content 字段必须是完整文件文本，用 \\n 表示换行。\n"
            "5. content 必须适合 Linux/VASP 文本输入：UTF-8 无 BOM，只使用 Unix LF 换行，不要使用 Windows CRLF。\n"
            "6. 如果用户后来用 Windows 记事本手工编辑 INCAR/POSCAR/KPOINTS/POTCAR，必须提示其传到 Linux 后先执行 dos2unix，或用 sed -i 's/\\r$//' 转换行尾。\n"
            "7. 最多输出 3 个结构。\n\n"
            "JSON 格式：\n"
            "{\n"
            '  "reasoning": "为什么选择这些材料与结构格式",\n'
            '  "structures": [\n'
            '    {"name": "LiFePO4 candidate", "formula": "LiFePO4", "format": "POSCAR", '
            '"confidence": 0.55, "source_evidence": "文献提及材料但未给出坐标", "content": "..."}\n'
            "  ]\n"
            "}\n\n"
            f"候选材料: {materials_text}\n\n"
            f"【当前假设】\n{hypo}\n\n"
            f"【文献内容】\n{text}\n"
        )


@dataclass
class ScientificToolkitEngine:
    """Run RDKit/pymatgen checks and optional Qwen-generated structures."""

    config: Optional[LLMConfig] = None
    output_dir: Optional[Path | str] = None
    structure_generator_cls: Any = ScientificStructureGenerator

    def __post_init__(self):
        self.config = _ensure_config(self.config)
        self.output_dir = _resolve_output_dir(self.output_dir)

    def run(
        self,
        literature_text: str,
        hypothesis_data: Optional[Dict[str, Any]] = None,
        *,
        generate_structures: bool = True,
    ) -> Dict[str, Any]:
        hypothesis_data = hypothesis_data or {}
        entities = extract_scientific_entities(literature_text, hypothesis_data)
        target_validation = self._build_target_validation(entities, hypothesis_data)
        warnings: List[str] = []

        molecules = self._analyze_molecules(entities, warnings)
        materials = self._analyze_material_formulas(entities, warnings)

        generated_structures = {
            "status": "skipped",
            "summary": {"generated_files": 0},
            "structures": [],
            "warnings": [],
        }
        if generate_structures:
            try:
                generator = self.structure_generator_cls(
                    config=self.config,
                    output_dir=self.output_dir,
                )
                generated_structures = generator.generate(
                    literature_text=literature_text,
                    hypothesis_data=hypothesis_data,
                    entities=entities,
                )
            except Exception as exc:
                generated_structures = {
                    "status": "failed",
                    "summary": {"generated_files": 0},
                    "structures": [],
                    "warnings": [str(exc)],
                }
        warnings.extend(generated_structures.get("warnings", []) or [])

        structure_analyses = self._analyze_structure_files(
            generated_structures.get("structures", []),
            warnings,
        )
        reproducibility_plan = self._build_reproducibility_plan(materials, structure_analyses)
        atomate2_dryrun = run_atomate2_dryrun(structure_analyses)
        atomate2_summary = atomate2_dryrun.get("summary", {}) if isinstance(atomate2_dryrun, dict) else {}

        summary = {
            "molecules_checked": len(molecules),
            "materials_checked": len(materials),
            "structures_generated": generated_structures.get("summary", {}).get("generated_files", 0),
            "structures_parsed": sum(1 for s in structure_analyses if s.get("status") == "ok"),
            "atomate2_workflows_planned": atomate2_summary.get("workflows_planned", 0),
            "atomate2_jobs_planned": atomate2_summary.get("jobs_planned", 0),
            "warnings": len(warnings),
        }
        status = "ok" if not warnings else "partial"

        return {
            "status": status,
            "summary": summary,
            "entities": entities,
            "target_validation": target_validation,
            "molecules": molecules,
            "materials": materials,
            "generated_structures": generated_structures,
            "structure_analyses": structure_analyses,
            "reproducibility_plan": reproducibility_plan,
            "atomate2_dryrun": atomate2_dryrun,
            "warnings": warnings,
            "tool_versions": {
                "rdkit": _dependency_version("rdkit"),
                "pymatgen": _dependency_version("pymatgen"),
                "atomate2": atomate2_dryrun.get("tool_versions", {}).get("atomate2", _dependency_version("atomate2")),
                "jobflow": atomate2_dryrun.get("tool_versions", {}).get("jobflow", _dependency_version("jobflow")),
                "ase": atomate2_dryrun.get("tool_versions", {}).get("ase", _dependency_version("ase")),
            },
            "execution_log": {
                "validator": "ScientificToolkitEngine",
                "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "output_dir": str(self.output_dir),
                "checks": self._build_checks(summary, warnings),
            },
        }

    @staticmethod
    def _build_target_validation(entities: Dict[str, Any], hypothesis_data: Dict[str, Any]) -> Dict[str, Any]:
        targets = _target_entities_from_hypothesis(hypothesis_data)
        mode = "hypothesis_targets" if entities.get("extraction_mode") == "hypothesis_targets" else "open_extraction"
        background = entities.get("background_entities", {})
        if not isinstance(background, dict):
            background = {"molecules": [], "materials": [], "smiles": []}
        return {
            "mode": mode,
            "source": "hypothesis target fields" if mode == "hypothesis_targets" else "open literature extraction",
            "materials": list(entities.get("materials", [])),
            "molecules": list(entities.get("molecules", [])),
            "ligands": targets.get("ligands", []),
            "controls": targets.get("controls", []),
            "active_sites": list(entities.get("active_sites", [])),
            "adsorbates": list(entities.get("adsorbates", [])),
            "reaction_type": targets.get("reaction_type", ""),
            "properties_to_validate": list(entities.get("properties_to_validate", [])),
            "background_entities": {
                "molecules": list(background.get("molecules", [])),
                "materials": list(background.get("materials", [])),
                "smiles": list(background.get("smiles", [])),
            },
        }

    def _analyze_molecules(self, entities: Dict[str, List[str]], warnings: List[str]) -> List[Dict[str, Any]]:
        Chem, Descriptors, Crippen, Lipinski, rdMolDescriptors, import_error = _try_import_rdkit()
        candidates = _dedupe_keep_order(entities.get("smiles", []) + entities.get("molecules", []))
        results = []
        for candidate in candidates:
            item = {"input": candidate, "tool": "RDKit", "status": "dependency_missing", "warnings": []}
            if Chem is None:
                item["warnings"].append("RDKit 未安装，无法计算分子描述符。")
                if import_error:
                    item["error"] = import_error
                results.append(item)
                continue
            try:
                smiles_input = COMMON_NAME_TO_SMILES.get(candidate.lower(), candidate)
                mol = Chem.MolFromSmiles(smiles_input)
                if mol is None and _looks_like_molecule_formula(candidate):
                    item["status"] = "formula_only"
                    item["formula"] = candidate
                    item["warnings"].append("候选是分子式而非 SMILES，RDKit 无法唯一恢复结构。")
                    results.append(item)
                    continue
                if mol is None:
                    item["status"] = "invalid"
                    item["warnings"].append("RDKit 无法解析该分子候选。")
                    results.append(item)
                    continue
                item.update(
                    {
                        "status": "ok",
                        "canonical_smiles": Chem.MolToSmiles(mol),
                        "resolved_smiles": smiles_input,
                        "formula": rdMolDescriptors.CalcMolFormula(mol),
                        "descriptors": {
                            "exact_mol_wt": round(float(Descriptors.ExactMolWt(mol)), 4),
                            "logp": round(float(Crippen.MolLogP(mol)), 4),
                            "tpsa": round(float(rdMolDescriptors.CalcTPSA(mol)), 4),
                            "hbd": int(Lipinski.NumHDonors(mol)),
                            "hba": int(Lipinski.NumHAcceptors(mol)),
                            "rotatable_bonds": int(Lipinski.NumRotatableBonds(mol)),
                            "rings": int(rdMolDescriptors.CalcNumRings(mol)),
                            "aromatic_rings": int(rdMolDescriptors.CalcNumAromaticRings(mol)),
                        },
                    }
                )
            except Exception as exc:
                item["status"] = "error"
                item["error"] = str(exc)
            results.append(item)
        return results

    def _analyze_material_formulas(self, entities: Dict[str, List[str]], warnings: List[str]) -> List[Dict[str, Any]]:
        Composition, _Structure, import_error = _try_import_pymatgen()
        results = []
        target_mode = entities.get("extraction_mode") == "hypothesis_targets"
        for formula in entities.get("materials", []):
            item = {"formula": formula, "tool": "pymatgen", "status": "dependency_missing", "warnings": []}
            if target_mode and not re.fullmatch(r"(?:[A-Z][a-z]?\d*)+", str(formula or "")):
                item.update(
                    {
                        "tool": "hypothesis_target_registry",
                        "status": "target_descriptor",
                        "warnings": ["Target material is a catalyst/site descriptor, not a plain chemical formula."],
                    }
                )
                results.append(item)
                continue
            if Composition is None:
                item["warnings"].append("pymatgen 未安装，无法解析材料组成。")
                if import_error:
                    item["error"] = import_error
                results.append(item)
                continue
            try:
                comp = Composition(formula)
                oxi_guesses = []
                try:
                    oxi_guesses = [str(g) for g in comp.oxi_state_guesses()[:3]]
                except Exception:
                    pass
                item.update(
                    {
                        "status": "ok",
                        "reduced_formula": comp.reduced_formula,
                        "chemical_system": "-".join(sorted(el.symbol for el in comp.elements)),
                        "num_atoms": float(comp.num_atoms),
                        "element_fractions": {
                            el.symbol: round(float(comp.get_atomic_fraction(el)), 4)
                            for el in comp.elements
                        },
                        "oxidation_state_guesses": oxi_guesses,
                    }
                )
            except Exception as exc:
                item["status"] = "invalid"
                item["error"] = str(exc)
            results.append(item)
        return results

    def _analyze_structure_files(self, structures: List[Dict[str, Any]], warnings: List[str]) -> List[Dict[str, Any]]:
        _Composition, Structure, import_error = _try_import_pymatgen()
        analyses = []
        for item in structures:
            path = Path(str(item.get("path", "")))
            analysis = {
                "name": item.get("name", ""),
                "formula": item.get("formula", ""),
                "format": item.get("format", ""),
                "path": str(path),
                "status": "dependency_missing",
                "warnings": [],
            }
            if Structure is None:
                analysis["warnings"].append("pymatgen 未安装，结构文件已保存但未解析。")
                if import_error:
                    analysis["error"] = import_error
                analyses.append(analysis)
                continue
            try:
                structure = Structure.from_file(str(path))
                lattice = structure.lattice
                elements = sorted({site.specie.symbol for site in structure})
                analysis.update(
                    {
                        "status": "ok",
                        "formula_pretty": structure.composition.reduced_formula,
                        "num_sites": len(structure),
                        "density": round(float(structure.density), 4),
                        "elements": elements,
                        "lattice": {
                            "a": round(float(lattice.a), 4),
                            "b": round(float(lattice.b), 4),
                            "c": round(float(lattice.c), 4),
                            "alpha": round(float(lattice.alpha), 4),
                            "beta": round(float(lattice.beta), 4),
                            "gamma": round(float(lattice.gamma), 4),
                            "volume": round(float(lattice.volume), 4),
                        },
                    }
                )
            except Exception as exc:
                analysis["status"] = "invalid"
                analysis["error"] = str(exc)
                warnings.append(f"pymatgen 无法解析结构文件 {path.name}: {exc}")
            analyses.append(analysis)
        return analyses

    def _build_reproducibility_plan(self, materials: List[Dict[str, Any]], structures: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        plans = []
        formulas = [
            s.get("formula_pretty") or s.get("formula")
            for s in structures
            if s.get("status") == "ok" and (s.get("formula_pretty") or s.get("formula"))
        ]
        if not formulas:
            formulas = [
                m.get("reduced_formula") or m.get("formula")
                for m in materials
                if m.get("reduced_formula") or m.get("formula")
            ]
        for formula in _dedupe_keep_order([str(f) for f in formulas if f], limit=5):
            elements = _formula_elements(formula)
            plans.append(
                {
                    "target": formula,
                    "calculation": "VASP geometry optimization + SCF",
                    "incar_relax": get_incar_template("relax"),
                    "incar_scf": get_incar_template("scf"),
                    "potcar_hints": {el: get_potcar_suffix(el) or el for el in sorted(set(elements))},
                    "kpoints_hint": "Start from 6x6x6 Gamma-centered mesh; refine by convergence test.",
                    "notes": "Use Qwen-generated structures as candidate inputs only; verify against literature or database structures before final DFT.",
                }
            )
        return plans

    @staticmethod
    def _build_checks(summary: Dict[str, Any], warnings: List[str]) -> List[Dict[str, Any]]:
        return [
            {
                "name": "molecule_descriptor_gate",
                "type": "rdkit_optional",
                "status": "ready" if summary.get("molecules_checked", 0) else "recorded",
                "count": summary.get("molecules_checked", 0),
            },
            {
                "name": "material_structure_gate",
                "type": "pymatgen_optional",
                "status": "ready" if summary.get("structures_parsed", 0) else "review",
                "generated": summary.get("structures_generated", 0),
                "parsed": summary.get("structures_parsed", 0),
            },
            {
                "name": "scientific_toolkit_warnings",
                "type": "audit",
                "status": "ready" if not warnings else "review",
                "count": len(warnings),
            },
            {
                "name": "atomate2_dryrun_gate",
                "type": "atomate2_optional",
                "status": "ready" if summary.get("atomate2_workflows_planned", 0) else "review",
                "workflows": summary.get("atomate2_workflows_planned", 0),
                "jobs": summary.get("atomate2_jobs_planned", 0),
            },
        ]


def merge_scientific_toolkit_report(hypothesis_data: Dict[str, Any], toolkit_report: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(hypothesis_data, dict) or not isinstance(toolkit_report, dict) or not toolkit_report:
        return hypothesis_data
    data = json.loads(json.dumps(hypothesis_data, ensure_ascii=False))
    data["_scientific_toolkit"] = toolkit_report

    results = data.get("results", {})
    if not isinstance(results, dict):
        results = {}
    method = str(results.get("verification_method", "") or "")
    if "RDKit/pymatgen" not in method:
        results["verification_method"] = (
            f"{method} + RDKit/pymatgen/atomate2 dry-run 可执行科学验证"
            if method
            else "RDKit/pymatgen/atomate2 dry-run 可执行科学验证"
        )
    conclusion = str(results.get("feasibility_conclusion", "") or "")
    summary = toolkit_report.get("summary", {})
    addition = (
        f"科学工具层检查：分子候选 {summary.get('molecules_checked', 0)} 个，"
        f"材料候选 {summary.get('materials_checked', 0)} 个，"
        f"生成结构文件 {summary.get('structures_generated', 0)} 个，"
        f"pymatgen 成功解析 {summary.get('structures_parsed', 0)} 个，"
        f"atomate2 dry-run 规划工作流 {summary.get('atomate2_workflows_planned', 0)} 个。"
    )
    if addition not in conclusion:
        results["feasibility_conclusion"] = f"{conclusion}\n{addition}".strip()

    execution_log = results.get("execution_log", {})
    if not isinstance(execution_log, dict):
        execution_log = {}
    toolkit_log = toolkit_report.get("execution_log", {})
    if toolkit_log:
        execution_log["scientific_toolkit"] = toolkit_log
        checks = execution_log.get("checks", [])
        if isinstance(checks, list):
            checks.extend(toolkit_log.get("checks", []))
        else:
            execution_log["checks"] = toolkit_log.get("checks", [])
    results["execution_log"] = execution_log
    data["results"] = results
    return data


def build_scientific_toolkit_context(toolkit_report: Dict[str, Any], max_chars: int = 6000) -> str:
    if not toolkit_report:
        return ""
    lines = ["【RDKit/pymatgen/atomate2 可执行科学验证层】"]
    versions = toolkit_report.get("tool_versions", {})
    lines.append(
        f"RDKit: {versions.get('rdkit', 'unknown')} | "
        f"pymatgen: {versions.get('pymatgen', 'unknown')} | "
        f"atomate2: {versions.get('atomate2', 'unknown')}"
    )
    summary = toolkit_report.get("summary", {})
    lines.append(
        "摘要: "
        f"分子 {summary.get('molecules_checked', 0)} 个, "
        f"材料 {summary.get('materials_checked', 0)} 个, "
        f"生成结构 {summary.get('structures_generated', 0)} 个, "
        f"解析结构 {summary.get('structures_parsed', 0)} 个, "
        f"atomate2 工作流 {summary.get('atomate2_workflows_planned', 0)} 个。"
    )
    for mol in toolkit_report.get("molecules", [])[:5]:
        status = mol.get("status")
        desc = mol.get("descriptors", {})
        lines.append(
            f"- RDKit 分子 {mol.get('input')}: {status}; "
            f"formula={mol.get('formula', '')}; descriptors={json.dumps(desc, ensure_ascii=False)[:240]}"
        )
    for mat in toolkit_report.get("materials", [])[:5]:
        lines.append(
            f"- pymatgen 材料 {mat.get('formula')}: {mat.get('status')}; "
            f"reduced={mat.get('reduced_formula', '')}; system={mat.get('chemical_system', '')}"
        )
    for struct in toolkit_report.get("structure_analyses", [])[:5]:
        lines.append(
            f"- 结构文件 {Path(str(struct.get('path', ''))).name}: {struct.get('status')}; "
            f"formula={struct.get('formula_pretty') or struct.get('formula', '')}; path={struct.get('path', '')}"
        )
    atomate_context = build_atomate2_context(toolkit_report.get("atomate2_dryrun", {}), max_chars=1800)
    if atomate_context:
        lines.append(atomate_context)
    warnings = toolkit_report.get("warnings", [])
    if warnings:
        lines.append("警告: " + "; ".join(str(w) for w in warnings[:5]))
    text = "\n".join(lines)
    return text[:max_chars]
