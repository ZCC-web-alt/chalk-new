from __future__ import annotations

import json
import re
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


Scalar = str | int | float | bool
MAX_STRUCTURE_ATOMS = 5000
MAX_BOND_ATOMS = 300
MAX_PREVIEW_BONDS = 50_000


def is_valid_element_symbol(value: str) -> bool:
    try:
        from pymatgen.core import Element

        return Element.is_valid_symbol(value)
    except (ImportError, TypeError, ValueError):
        return False


def is_valid_potcar_potential_name(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z][A-Za-z0-9_.+\-]{0,79}", str(value or "")))


class KpointsExtraction(BaseModel):
    mode: Literal["Gamma", "Monkhorst-Pack", "Line"] = "Gamma"
    mesh: str = "6 6 6"

    model_config = {"extra": "forbid"}

    @field_validator("mesh")
    @classmethod
    def validate_mesh(cls, value: str) -> str:
        values = value.replace("x", " ").split()
        if len(values) != 3 or any(not item.isdigit() or not 1 <= int(item) <= 99 for item in values):
            raise ValueError("KPOINTS mesh must contain three integers between 1 and 99.")
        return " ".join(values)


class PoscarSuggestion(BaseModel):
    lattice_type: str = "unknown"
    lattice_constants: dict[str, float | int | None] = Field(default_factory=dict)
    space_group: str = "unknown"
    formula_units: int | float | None = None
    basis_atoms: list[dict[str, Any]] = Field(default_factory=list, max_length=10000)

    model_config = {"extra": "forbid"}


class VaspExtraction(BaseModel):
    calc_type: Literal["scf", "relax", "dos", "band", "phonon", "optics", "unknown"] = "unknown"
    system_name: str = Field(default="", max_length=240)
    functional: str = Field(default="unknown", max_length=80)
    is_metal: bool = False
    incar: dict[str, Scalar | None] = Field(default_factory=dict, max_length=200)
    kpoints: KpointsExtraction = Field(default_factory=KpointsExtraction)
    poscar: PoscarSuggestion = Field(default_factory=PoscarSuggestion)
    potcar_elements: list[str] = Field(default_factory=list, max_length=100)
    missing_params: list[str] = Field(default_factory=list, max_length=200)
    notes: str = Field(default="", max_length=12000)

    model_config = {"extra": "forbid"}

    @field_validator("incar")
    @classmethod
    def validate_incar(cls, value: dict[str, Scalar | None]) -> dict[str, Scalar | None]:
        for key in value:
            if not re.fullmatch(r"[A-Z][A-Z0-9_]{0,29}", key):
                raise ValueError("Invalid INCAR parameter name.")
        return value

    @field_validator("potcar_elements")
    @classmethod
    def validate_elements(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            element = value.strip()
            if not re.fullmatch(r"[A-Z][a-z]?", element) or not is_valid_element_symbol(element):
                raise ValueError("Invalid chemical element.")
            if element not in normalized:
                normalized.append(element)
        return normalized


class MsGuidePhase(BaseModel):
    title: str = Field(default="", max_length=240)
    module_path: str = Field(default="", max_length=500)
    params: dict[str, Scalar] = Field(default_factory=dict, max_length=100)
    steps: list[dict[str, Any]] = Field(default_factory=list, max_length=100)

    model_config = {"extra": "forbid"}


class MsGuideExtraction(BaseModel):
    modeling_target: str = Field(default="unknown", max_length=120)
    method: str = Field(default="unknown", max_length=120)
    guide: dict[str, MsGuidePhase] = Field(default_factory=dict, max_length=20)
    tips: list[str] = Field(default_factory=list, max_length=100)
    warnings: list[str] = Field(default_factory=list, max_length=100)

    model_config = {"extra": "forbid"}


def parse_json_object(raw: str) -> dict[str, Any]:
    text = str(raw or "").strip()
    text = re.sub(r"<think[^>]*>.*?</think\s*>", "", text, flags=re.DOTALL).strip()
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(lines[1:-1] if lines and lines[-1].strip().startswith("```") else lines[1:])
    text = text.strip()
    if text.lower().startswith("json"):
        text = text[4:].strip()
    try:
        value = json.loads(text)
    except (TypeError, ValueError) as exc:
        raise ValueError("The model did not return valid JSON.") from exc
    if not isinstance(value, dict) or not value:
        raise ValueError("The model returned an empty result.")
    return value


def segment_modeling_text(text: str, *, segment_chars: int = 10000, max_chars: int = 60000) -> tuple[list[str], dict[str, Any]]:
    source = text.strip()
    source_chars = len(source)
    if source_chars > max_chars:
        raise ValueError(f"Modeling input exceeds {max_chars:,} characters.")
    segments = [source[index:index + segment_chars] for index in range(0, source_chars, segment_chars)] or [source]
    return segments, {
        "sourceChars": source_chars,
        "includedChars": source_chars,
        "segmentCount": len(segments),
        "truncated": False,
    }


def select_document_modeling_text(text: str, *, max_chars: int = 60000) -> tuple[list[str], dict[str, Any]]:
    source = text.strip()
    source_chars = len(source)
    chunks = [source[index:index + 10000] for index in range(0, source_chars, 10000)]
    keywords = (
        "vasp", "dft", "density functional", "computational", "calculation", "incar",
        "k-point", "kpoint", "pbe", "hse", "castep", "materials studio", "simulation",
        "计算", "第一性原理", "密度泛函", "建模",
    )
    ranked = sorted(
        enumerate(chunks),
        key=lambda item: (-sum(item[1].lower().count(keyword) for keyword in keywords), item[0]),
    )
    selected_indices: list[int] = []
    selected_chars = 0
    for index, chunk in ranked:
        if selected_chars + len(chunk) > max_chars and selected_indices:
            continue
        selected_indices.append(index)
        selected_chars += len(chunk)
        if selected_chars >= max_chars:
            break
    selected_indices.sort()
    selected = [chunks[index] for index in selected_indices]
    return selected, {
        "sourceChars": source_chars,
        "includedChars": sum(len(chunk) for chunk in selected),
        "segmentCount": len(selected),
        "sourceSegmentCount": len(chunks),
        "selectedSegmentIndices": selected_indices,
        "truncated": sum(len(chunk) for chunk in selected) < source_chars,
        "strategy": "keyword-ranked-full-document-segments",
    }


def merge_vasp_extractions(values: list[VaspExtraction]) -> VaspExtraction:
    if not values:
        raise ValueError("No VASP extraction was returned.")
    merged = values[0].model_dump()
    conflicts: list[str] = []
    for value in values[1:]:
        current = value.model_dump()
        for key in ("calc_type", "system_name", "functional"):
            candidate = current.get(key)
            if candidate and candidate != "unknown":
                if merged.get(key) not in (None, "", "unknown") and merged.get(key) != candidate:
                    conflicts.append(f"Conflicting {key}: {merged.get(key)} / {candidate}")
                elif merged.get(key) in (None, "", "unknown"):
                    merged[key] = candidate
        merged["is_metal"] = bool(merged.get("is_metal") or current.get("is_metal"))
        for key, candidate in current.get("incar", {}).items():
            if candidate is None:
                continue
            if key not in merged["incar"] or merged["incar"][key] is None:
                merged["incar"][key] = candidate
            elif merged["incar"][key] != candidate:
                conflicts.append(f"Conflicting INCAR {key}: {merged['incar'][key]} / {candidate}")
        if merged.get("kpoints", {}).get("mesh") == "6 6 6" and current.get("kpoints"):
            merged["kpoints"] = current["kpoints"]
        for element in current.get("potcar_elements", []):
            if element not in merged["potcar_elements"]:
                merged["potcar_elements"].append(element)
        for missing in current.get("missing_params", []):
            if missing not in merged["missing_params"]:
                merged["missing_params"].append(missing)
        if current.get("notes"):
            merged["notes"] = "\n".join(filter(None, [merged.get("notes", ""), current["notes"]]))
    if conflicts:
        merged["notes"] = "\n".join(filter(None, [merged.get("notes", ""), *conflicts]))
    return VaspExtraction.model_validate(merged)


def build_vasp_workspace_result(
    extraction: VaspExtraction,
    *,
    requested_calc_type: str,
    source_kind: str,
    defaults_module,
) -> dict[str, Any]:
    calc_type = extraction.calc_type if extraction.calc_type != "unknown" else requested_calc_type
    if calc_type == "auto" or calc_type == "unknown":
        calc_type = "scf"
    defaults = defaults_module.get_incar_template(calc_type, extraction.is_metal)
    incar = {key: {"value": value, "source": "default", "warnings": []} for key, value in defaults.items()}
    for key, value in extraction.incar.items():
        if value is not None:
            incar[key] = {"value": value, "source": source_kind, "warnings": []}
    raw_incar = {key: item["value"] for key, item in incar.items()}
    mesh = [int(value) for value in extraction.kpoints.mesh.split()]
    kpoints_source = source_kind if extraction.kpoints.mesh != "6 6 6" else "default"
    if calc_type == "band" or extraction.kpoints.mode == "Line":
        kpoints_text = defaults_module.format_kpoints_line()
    else:
        kpoints_text = defaults_module.format_kpoints(
            " ".join(str(value) for value in mesh),
            "Gamma" if extraction.kpoints.mode == "Gamma" else "Monkhorst-Pack",
        )
    potcar_elements = [{
        "element": element,
        "potential": defaults_module.get_potcar_suffix(element) or element,
        "source": source_kind,
        "warnings": [],
    } for element in extraction.potcar_elements]
    potcar_lines = ["POTCAR is not distributed by Chalk.", "", "Element order:"]
    potcar_lines.extend(f"{item['element']} -> {item['potential']}" for item in potcar_elements)
    risks = [
        "Generated parameters are recommendations and require human review.",
        "No validated structure is available until a CIF or POSCAR passes pymatgen validation.",
        "POTCAR binaries are never included in exports.",
    ]
    return {
        "calcType": calc_type,
        "systemName": extraction.system_name,
        "functional": extraction.functional,
        "isMetal": extraction.is_metal,
        "incar": incar,
        "kpoints": {"mode": extraction.kpoints.mode, "mesh": mesh, "source": kpoints_source, "warnings": []},
        "potcarElements": potcar_elements,
        "structureSuggestion": extraction.poscar.model_dump(by_alias=True),
        "missingParams": extraction.missing_params,
        "notes": extraction.notes,
        "files": {
            "incar": defaults_module.format_incar(raw_incar),
            "kpoints": kpoints_text,
            "potcarGuide": "\n".join(potcar_lines) + "\n",
        },
        "riskNotices": risks,
    }


def parse_structure_content(content: str, format_name: str) -> tuple[dict[str, Any], str]:
    try:
        from pymatgen.core import Structure

        structure = Structure.from_str(content, fmt="cif" if format_name == "cif" else "poscar")
    except Exception as exc:
        raise ValueError("The structure could not be parsed by pymatgen.") from exc
    if len(structure) > MAX_STRUCTURE_ATOMS:
        raise ValueError(f"The structure contains too many atoms for Web preview (max {MAX_STRUCTURE_ATOMS}).")
    lattice = structure.lattice
    atoms = [{
        "index": index,
        "element": site.specie.symbol,
        "fractional": [round(float(value), 8) for value in site.frac_coords],
        "cartesian": [round(float(value), 8) for value in site.coords],
    } for index, site in enumerate(structure)]
    bonds: list[dict[str, Any]] = []
    seen: set[tuple[int, int]] = set()
    bonds_truncated = False
    bonds_omitted = len(structure) > MAX_BOND_ATOMS
    if not bonds_omitted:
        for index, neighbors in enumerate(structure.get_all_neighbors(3.2)):
            for neighbor in neighbors:
                other = int(neighbor.index)
                pair = tuple(sorted((index, other)))
                if index == other or pair in seen:
                    continue
                seen.add(pair)
                bonds.append({"from": pair[0], "to": pair[1], "distance": round(float(neighbor.nn_distance), 5)})
                if len(bonds) >= MAX_PREVIEW_BONDS:
                    bonds_truncated = True
                    break
            if bonds_truncated:
                break
    geometry = {
        "formula": structure.composition.reduced_formula,
        "density": round(float(structure.density), 6),
        "cell": [[round(float(value), 8) for value in vector] for vector in lattice.matrix],
        "lattice": {
            "a": round(float(lattice.a), 6),
            "b": round(float(lattice.b), 6),
            "c": round(float(lattice.c), 6),
            "alpha": round(float(lattice.alpha), 6),
            "beta": round(float(lattice.beta), 6),
            "gamma": round(float(lattice.gamma), 6),
            "volume": round(float(lattice.volume), 6),
        },
        "atoms": atoms,
        "bonds": bonds,
        "bondsOmitted": bonds_omitted,
        "bondsTruncated": bonds_truncated,
    }
    return geometry, structure.to(fmt="poscar")


def prepare_modeling_changes(
    current: dict[str, Any],
    changes: dict[str, Any],
    defaults_module,
) -> dict[str, Any]:
    merged = json.loads(json.dumps(current, ensure_ascii=False, default=str))
    for key, value in changes.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key].update(value)
        else:
            merged[key] = value
    incar = merged.get("incar") if isinstance(merged.get("incar"), dict) else {}
    raw_incar = {
        key: entry.get("value")
        for key, entry in incar.items()
        if isinstance(entry, dict) and "value" in entry
    }
    kpoints = merged.get("kpoints") if isinstance(merged.get("kpoints"), dict) else {}
    existing_files = merged.get("files") if isinstance(merged.get("files"), dict) else {}
    mesh = kpoints.get("mesh") if isinstance(kpoints.get("mesh"), list) else [6, 6, 6]
    mode = str(kpoints.get("mode") or "Gamma")
    calc_type = str(merged.get("calcType") or "scf")
    if calc_type == "band" or mode == "Line":
        kpoints_text = defaults_module.format_kpoints_line()
    else:
        kpoints_text = defaults_module.format_kpoints(
            " ".join(str(int(value)) for value in mesh),
            "Gamma" if mode == "Gamma" else "Monkhorst-Pack",
        )
    potcar_lines = ["POTCAR is not distributed by Chalk.", "", "Element order:"]
    for item in merged.get("potcarElements") or []:
        if isinstance(item, dict):
            potcar_lines.append(f"{item.get('element', '')} -> {item.get('potential', '')}")
    generated_files = {
        "incar": defaults_module.format_incar(raw_incar) if raw_incar else str(existing_files.get("incar") or ""),
        "kpoints": kpoints_text if kpoints else str(existing_files.get("kpoints") or ""),
        "potcarGuide": ("\n".join(potcar_lines) + "\n") if merged.get("potcarElements") else str(existing_files.get("potcarGuide") or ""),
    }
    return {**changes, "files": generated_files}
