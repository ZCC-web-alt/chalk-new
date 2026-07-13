# -*- coding: utf-8 -*-
"""
Workflow package exporter for Chalk hypothesis results.

The module normalizes ``_scientific_toolkit`` into a reusable intermediate
package for reports, the compute-modeling page, export buttons, and future
tree-search nodes. It deliberately records dry-run metadata only; it never
runs or submits VASP/atomate2 jobs.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


SCHEMA_VERSION = "1.0"
DEFAULT_EXPORT_ROOT = Path("exports")
_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9_.-]+")


@dataclass
class WorkflowStructureCandidate:
    index: int
    name: str = ""
    formula: str = ""
    format: str = ""
    path: str = ""
    confidence: Optional[float] = None
    source_evidence: str = ""
    parse_status: str = "unknown"
    analysis: Dict[str, Any] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)


@dataclass
class VaspInputRecommendation:
    target: str = ""
    calculation: str = ""
    incar_relax: Dict[str, Any] = field(default_factory=dict)
    incar_scf: Dict[str, Any] = field(default_factory=dict)
    kpoints_hint: str = ""
    potcar_hints: Dict[str, Any] = field(default_factory=dict)
    notes: str = ""


@dataclass
class Atomate2DryRunSummary:
    status: str = "skipped"
    workflow_kind: str = ""
    dry_run_only: bool = True
    execution_policy: str = "not_submitted"
    summary: Dict[str, Any] = field(default_factory=dict)
    workflows: List[Dict[str, Any]] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


@dataclass
class WorkflowProvenance:
    generated_at: str
    source_report_path: str = ""
    source_scientific_toolkit_status: str = ""
    source_tool_versions: Dict[str, Any] = field(default_factory=dict)


@dataclass
class HypothesisWorkflowPackage:
    schema_version: str
    package_kind: str
    hypothesis: Dict[str, Any]
    summary: Dict[str, Any]
    structures: List[WorkflowStructureCandidate]
    vasp_recommendations: List[VaspInputRecommendation]
    atomate2_dryrun: Atomate2DryRunSummary
    provenance: WorkflowProvenance
    risk_notices: List[str]
    scientific_toolkit: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def build_hypothesis_workflow_package(
    hypothesis_data: Dict[str, Any],
    report_path: str = "",
) -> Dict[str, Any]:
    """Build a standard workflow package from hypothesis JSON.

    The returned object is a plain dictionary to keep downstream JSON storage,
    Qt views, and report rendering simple.
    """
    data = hypothesis_data if isinstance(hypothesis_data, dict) else {}
    toolkit = data.get("_scientific_toolkit", {})
    if not isinstance(toolkit, dict):
        toolkit = {}

    structures = _normalize_structures(toolkit)
    vasp_recommendations = [
        VaspInputRecommendation(
            target=str(plan.get("target", "") or ""),
            calculation=str(plan.get("calculation", "") or ""),
            incar_relax=_dict_or_empty(plan.get("incar_relax")),
            incar_scf=_dict_or_empty(plan.get("incar_scf")),
            kpoints_hint=str(plan.get("kpoints_hint", "") or ""),
            potcar_hints=_dict_or_empty(plan.get("potcar_hints")),
            notes=str(plan.get("notes", "") or ""),
        )
        for plan in _list_of_dicts(toolkit.get("reproducibility_plan"))
    ]

    atomate2 = toolkit.get("atomate2_dryrun", {})
    if not isinstance(atomate2, dict):
        atomate2 = {}
    atomate2_summary = Atomate2DryRunSummary(
        status=str(atomate2.get("status", "skipped") or "skipped"),
        workflow_kind=str(atomate2.get("workflow_kind", "") or ""),
        dry_run_only=True,
        execution_policy="not_submitted",
        summary=_dict_or_empty(atomate2.get("summary")),
        workflows=_list_of_dicts(atomate2.get("workflows")),
        warnings=[str(w) for w in atomate2.get("warnings", [])] if isinstance(atomate2.get("warnings"), list) else [],
    )

    summary = toolkit.get("summary", {}) if isinstance(toolkit.get("summary"), dict) else {}
    package_summary = {
        "structure_candidates": len(structures),
        "parsed_structures": sum(1 for item in structures if item.parse_status == "ok"),
        "vasp_recommendations": len(vasp_recommendations),
        "atomate2_workflows": atomate2_summary.summary.get("workflows_planned", 0),
        "atomate2_jobs": atomate2_summary.summary.get("jobs_planned", 0),
        "source_structures_generated": summary.get("structures_generated", len(structures)),
        "source_structures_parsed": summary.get("structures_parsed", 0),
    }

    risk_notices = _build_risk_notices(toolkit, structures, atomate2_summary, vasp_recommendations)
    provenance = WorkflowProvenance(
        generated_at=datetime.now().isoformat(timespec="seconds"),
        source_report_path=str(report_path or ""),
        source_scientific_toolkit_status=str(toolkit.get("status", "") or ""),
        source_tool_versions=_dict_or_empty(toolkit.get("tool_versions")),
    )
    package = HypothesisWorkflowPackage(
        schema_version=SCHEMA_VERSION,
        package_kind="hypothesis_workflow",
        hypothesis=_hypothesis_summary(data),
        summary=package_summary,
        structures=structures,
        vasp_recommendations=vasp_recommendations,
        atomate2_dryrun=atomate2_summary,
        provenance=provenance,
        risk_notices=risk_notices,
        scientific_toolkit=toolkit,
    )
    return package.to_dict()


def export_hypothesis_workflow_package(
    package: Dict[str, Any],
    output_dir: Optional[os.PathLike[str] | str] = None,
) -> Path:
    """Export a workflow package folder and return the created directory."""
    if not isinstance(package, dict):
        raise TypeError("package must be a dictionary")

    export_dir = Path(output_dir) if output_dir else _default_export_dir()
    export_dir.mkdir(parents=True, exist_ok=True)
    for subdir in ("structures", "vasp", "atomate2", "provenance"):
        (export_dir / subdir).mkdir(parents=True, exist_ok=True)

    _write_json(export_dir / "hypothesis_summary.json", _public_package_summary(package))
    _write_text_file(export_dir / "README.md", _render_readme(package))
    _export_structures(package, export_dir / "structures")
    _export_vasp(package, export_dir / "vasp")
    _export_atomate2(package, export_dir / "atomate2")
    _export_provenance(package, export_dir / "provenance")

    return export_dir


def _normalize_structures(toolkit: Dict[str, Any]) -> List[WorkflowStructureCandidate]:
    generated = toolkit.get("generated_structures", {})
    generated_items = []
    if isinstance(generated, dict):
        generated_items = _list_of_dicts(generated.get("structures"))
    analyses = _list_of_dicts(toolkit.get("structure_analyses"))

    by_path = {str(item.get("path", "")): item for item in analyses if item.get("path")}
    structures: List[WorkflowStructureCandidate] = []
    seen_paths = set()
    source_items = generated_items or analyses

    for index, item in enumerate(source_items, 1):
        path = str(item.get("path", "") or "")
        analysis = by_path.get(path, {})
        if not analysis and index - 1 < len(analyses):
            analysis = analyses[index - 1]
        seen_paths.add(path)
        structures.append(
            WorkflowStructureCandidate(
                index=index,
                name=str(item.get("name", "") or analysis.get("name", "") or f"structure_{index}"),
                formula=str(item.get("formula", "") or analysis.get("formula_pretty", "") or analysis.get("formula", "")),
                format=str(item.get("format", "") or analysis.get("format", "") or _format_from_path(path)),
                path=path,
                confidence=item.get("confidence") if isinstance(item.get("confidence"), (int, float)) else None,
                source_evidence=str(item.get("source_evidence", "") or ""),
                parse_status=str(analysis.get("status", item.get("status", "unknown")) or "unknown"),
                analysis=analysis,
                warnings=[str(w) for w in analysis.get("warnings", [])] if isinstance(analysis.get("warnings"), list) else [],
            )
        )

    for analysis in analyses:
        path = str(analysis.get("path", "") or "")
        if path in seen_paths:
            continue
        index = len(structures) + 1
        structures.append(
            WorkflowStructureCandidate(
                index=index,
                name=str(analysis.get("name", "") or f"structure_{index}"),
                formula=str(analysis.get("formula_pretty", "") or analysis.get("formula", "")),
                format=str(analysis.get("format", "") or _format_from_path(path)),
                path=path,
                parse_status=str(analysis.get("status", "unknown") or "unknown"),
                analysis=analysis,
                warnings=[str(w) for w in analysis.get("warnings", [])] if isinstance(analysis.get("warnings"), list) else [],
            )
        )
    return structures


def _build_risk_notices(
    toolkit: Dict[str, Any],
    structures: List[WorkflowStructureCandidate],
    atomate2: Atomate2DryRunSummary,
    recommendations: List[VaspInputRecommendation],
) -> List[str]:
    notices = [
        "atomate2 is dry-run only; no workflow was executed or submitted.",
        "VASP inputs are recommendations only and must receive human review before production calculations.",
        "Structures may be Qwen-generated candidates; verify lattice, sites, composition, and charge state manually.",
    ]
    if not structures:
        notices.append("No parsed structure is available; manual review and structure preparation are required.")
    elif not any(item.parse_status == "ok" for item in structures):
        notices.append("No structure was successfully parsed by pymatgen; manual review is required before modeling.")
    if not recommendations:
        notices.append("No VASP relax/scf recommendation was generated; prepare INCAR/KPOINTS/POTCAR manually.")
    if not atomate2.summary.get("workflows_planned", 0):
        notices.append("No atomate2 dry-run workflow was planned.")
    warnings = toolkit.get("warnings", [])
    if isinstance(warnings, list):
        notices.extend(str(w) for w in warnings if str(w).strip())
    return _dedupe(notices)


def _hypothesis_summary(data: Dict[str, Any]) -> Dict[str, Any]:
    keys = [
        "paper_title",
        "title",
        "hypothesis",
        "problem_statement",
        "rationale",
        "technical_details",
        "methods",
        "expected_results",
        "paper_abstract",
        "confidence",
        "feasibility",
    ]
    return {key: data.get(key) for key in keys if key in data and data.get(key) not in (None, "")}


def _public_package_summary(package: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "schema_version": package.get("schema_version", SCHEMA_VERSION),
        "package_kind": package.get("package_kind", "hypothesis_workflow"),
        "hypothesis": package.get("hypothesis", {}),
        "summary": package.get("summary", {}),
        "structures": package.get("structures", []),
        "vasp_recommendations": package.get("vasp_recommendations", []),
        "atomate2_dryrun": package.get("atomate2_dryrun", {}),
        "provenance": package.get("provenance", {}),
        "risk_notices": package.get("risk_notices", []),
    }


def _export_structures(package: Dict[str, Any], structures_dir: Path) -> None:
    manifest = []
    for item in _list_of_dicts(package.get("structures")):
        index = int(item.get("index", len(manifest) + 1) or len(manifest) + 1)
        source = Path(str(item.get("path", "") or ""))
        fmt = str(item.get("format", "") or _format_from_path(str(source)) or "structure").upper()
        suffix = _structure_suffix(fmt, source)
        base_name = source.stem if source.name else item.get("name") or item.get("formula") or "structure"
        filename = f"{index:02d}_{_safe_name(base_name)}{suffix}"
        target = structures_dir / filename
        copied = False
        if source.exists() and source.is_file():
            _copy_structure_text_file(source, target)
            copied = True
        else:
            _write_text_file(
                target,
                "# Structure source file was not available at export time.\n"
                f"# Original path: {source}\n",
            )
        manifest.append({**item, "exported_file": str(target), "copied_from_source": copied})

        if copied and suffix.lower() != ".cif":
            _try_write_cif_copy(source, structures_dir / f"{index:02d}_{_safe_name(base_name)}.cif")
    _write_json(structures_dir / "manifest.json", manifest)


def _try_write_cif_copy(source: Path, target: Path) -> None:
    try:
        from pymatgen.core import Structure  # type: ignore

        structure = Structure.from_file(str(source))
        structure.to(filename=str(target))
    except Exception:
        return


def _export_vasp(package: Dict[str, Any], vasp_dir: Path) -> None:
    recommendations = _list_of_dicts(package.get("vasp_recommendations"))
    _write_json(vasp_dir / "vasp_recommendations.json", recommendations)
    if not recommendations:
        _write_text_file(
            vasp_dir / "README.md",
            "No VASP recommendation was generated. Prepare INCAR, KPOINTS, and POTCAR hints manually.\n",
        )
        return

    first = recommendations[0]
    _write_text_file(vasp_dir / "INCAR.relax", _format_incar(_dict_or_empty(first.get("incar_relax"))))
    _write_text_file(vasp_dir / "INCAR.scf", _format_incar(_dict_or_empty(first.get("incar_scf"))))
    _write_text_file(vasp_dir / "KPOINTS", _format_kpoints_hint(str(first.get("kpoints_hint", "") or "")))
    _write_text_file(vasp_dir / "POTCAR_HINTS.txt", _format_potcar_hints(_dict_or_empty(first.get("potcar_hints"))))


def _export_atomate2(package: Dict[str, Any], atomate2_dir: Path) -> None:
    dryrun = package.get("atomate2_dryrun", {})
    if not isinstance(dryrun, dict):
        dryrun = {}
    _write_json(atomate2_dir / "atomate2_dryrun.json", dryrun)
    _write_text_file(atomate2_dir / "jobflow_summary.md", _format_atomate2_summary(dryrun))


def _export_provenance(package: Dict[str, Any], provenance_dir: Path) -> None:
    provenance = package.get("provenance", {}) if isinstance(package.get("provenance"), dict) else {}
    toolkit = package.get("scientific_toolkit", {}) if isinstance(package.get("scientific_toolkit"), dict) else {}
    _write_json(provenance_dir / "provenance.json", provenance)
    _write_json(provenance_dir / "scientific_toolkit.json", toolkit)
    _write_text_file(provenance_dir / "source_report_path.txt", str(provenance.get("source_report_path", "") or ""))


def _render_readme(package: Dict[str, Any]) -> str:
    hypothesis = package.get("hypothesis", {}) if isinstance(package.get("hypothesis"), dict) else {}
    summary = package.get("summary", {}) if isinstance(package.get("summary"), dict) else {}
    provenance = package.get("provenance", {}) if isinstance(package.get("provenance"), dict) else {}
    risks = package.get("risk_notices", []) if isinstance(package.get("risk_notices"), list) else []
    title = hypothesis.get("paper_title") or hypothesis.get("title") or "Chalk hypothesis workflow package"
    lines = [
        f"# {title}",
        "",
        "This folder is an intermediate Chalk workflow package for compute-modeling review.",
        "",
        "## Safety status",
        "",
        "- atomate2 is dry-run only.",
        "- Calculations were not submitted and VASP was not run.",
        "- Every structure and input hint requires human review before formal computation.",
        "- Structures may be Qwen-generated candidate inputs; verify against literature, databases, or experiment.",
        "",
        "## Summary",
        "",
        f"- Structure candidates: {summary.get('structure_candidates', 0)}",
        f"- pymatgen parsed structures: {summary.get('parsed_structures', 0)}",
        f"- VASP recommendations: {summary.get('vasp_recommendations', 0)}",
        f"- atomate2 dry-run workflows: {summary.get('atomate2_workflows', 0)}",
        f"- atomate2 dry-run jobs: {summary.get('atomate2_jobs', 0)}",
        "",
        "## Contents",
        "",
        "- `hypothesis_summary.json`: normalized package metadata.",
        "- `structures/`: candidate POSCAR/CIF or source structure files plus a manifest.",
        "- `vasp/`: relax/scf INCAR hints, KPOINTS hint, and POTCAR hints.",
        "- `atomate2/`: dry-run JSON and jobflow summary.",
        "- `provenance/`: source scientific toolkit report and report path.",
        "",
        "## Linux text format for VASP",
        "",
        "- Chalk exports text files as UTF-8 without BOM and Unix LF line endings.",
        "- If you edit INCAR/POSCAR/KPOINTS/POTCAR in Windows Notepad before moving to Linux, convert them before running VASP.",
        "- On Linux, run: `dos2unix INCAR POSCAR KPOINTS POTCAR`.",
        "- If dos2unix is unavailable, run: `sed -i 's/\\r$//' INCAR POSCAR KPOINTS POTCAR`.",
        "- Check for Windows CRLF with: `file INCAR POSCAR KPOINTS POTCAR`.",
        "- Build POTCAR on the Linux/HPC side from approved PAW datasets; do not rely on this POTCAR_HINTS.txt as a runnable POTCAR.",
        "",
    ]
    if risks:
        lines.extend(["## Risk notices", ""])
        lines.extend(f"- {risk}" for risk in risks)
        lines.append("")
    source_report = provenance.get("source_report_path", "")
    if source_report:
        lines.extend(["## Source report", "", f"`{source_report}`", ""])
    return "\n".join(lines)


def _format_incar(values: Dict[str, Any]) -> str:
    if not values:
        return "# No INCAR parameters were provided.\n"
    lines = ["# Generated by Chalk workflow package exporter", "# Review before running VASP.", ""]
    for key in sorted(values.keys()):
        value = values[key]
        if isinstance(value, bool):
            value = ".TRUE." if value else ".FALSE."
        lines.append(f"{key} = {value}")
    return "\n".join(lines) + "\n"


def _format_kpoints_hint(hint: str) -> str:
    return "\n".join(
        [
            "KPOINTS generated from Chalk hint",
            "0",
            "Gamma",
            "# Review and replace with a convergence-tested mesh.",
            f"# Hint: {hint or 'No KPOINTS hint was provided.'}",
            "",
        ]
    )


def _format_potcar_hints(hints: Dict[str, Any]) -> str:
    lines = [
        "POTCAR hints generated by Chalk",
        "Review element order and pseudopotentials before any VASP run.",
        "",
    ]
    if not hints:
        lines.append("No POTCAR hints were provided.")
    else:
        for element in sorted(hints.keys()):
            lines.append(f"{element}: {hints[element]}")
    return "\n".join(lines) + "\n"


def _format_atomate2_summary(dryrun: Dict[str, Any]) -> str:
    summary = dryrun.get("summary", {}) if isinstance(dryrun.get("summary"), dict) else {}
    workflows = _list_of_dicts(dryrun.get("workflows"))
    lines = [
        "# atomate2 dry-run summary",
        "",
        "dry-run only; not submitted; no VASP calculation was executed.",
        "",
        f"- Status: {dryrun.get('status', 'skipped')}",
        f"- Workflow kind: {dryrun.get('workflow_kind', '')}",
        f"- Workflows planned: {summary.get('workflows_planned', 0)}",
        f"- Jobs planned: {summary.get('jobs_planned', 0)}",
        "",
    ]
    for workflow in workflows:
        job_names = workflow.get("job_names", [])
        if isinstance(job_names, list):
            job_names_text = ", ".join(str(name) for name in job_names)
        else:
            job_names_text = str(job_names)
        lines.extend(
            [
                f"## {workflow.get('name') or workflow.get('workflow_type') or 'workflow'}",
                "",
                f"- Type: {workflow.get('workflow_type', '')}",
                f"- Jobs: {workflow.get('job_count', 0)}",
                f"- Job names: {job_names_text}",
                f"- Execution policy: {workflow.get('execution_policy', 'not_submitted')}",
                "",
            ]
        )
    warnings = dryrun.get("warnings", [])
    if isinstance(warnings, list) and warnings:
        lines.extend(["## Warnings", ""])
        lines.extend(f"- {warning}" for warning in warnings)
        lines.append("")
    return "\n".join(lines)


def _default_export_dir() -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return DEFAULT_EXPORT_ROOT / f"chalk_workflow_{stamp}"


def _write_json(path: Path, payload: Any) -> None:
    _write_text_file(path, json.dumps(payload, ensure_ascii=False, indent=2))


def _write_text_file(path: Path, text: str) -> None:
    normalized = _normalize_unix_text(text)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(normalized)


def _normalize_unix_text(text: str) -> str:
    normalized = str(text).lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    return normalized if normalized.endswith("\n") else normalized + "\n"


def _copy_structure_text_file(source: Path, target: Path) -> None:
    raw = source.read_bytes()
    text = raw.decode("utf-8-sig")
    _write_text_file(target, text)


def _list_of_dicts(value: Any) -> List[Dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _dict_or_empty(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _dedupe(values: List[str]) -> List[str]:
    seen = set()
    out = []
    for value in values:
        text = str(value).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def _safe_name(value: Any) -> str:
    name = _SAFE_NAME_RE.sub("_", str(value or "workflow").strip()).strip("._")
    return name[:64] or "workflow"


def _format_from_path(path: str) -> str:
    suffix = Path(path).suffix.lower()
    if suffix in {".vasp", ".poscar", ".contcar"}:
        return "POSCAR"
    if suffix == ".cif":
        return "CIF"
    return suffix.lstrip(".").upper()


def _structure_suffix(fmt: str, source: Path) -> str:
    normalized = (fmt or "").upper()
    if normalized in {"POSCAR", "CONTCAR", "VASP"}:
        return ".POSCAR"
    if normalized == "CIF":
        return ".cif"
    if source.suffix:
        return source.suffix
    return ".structure"
