# -*- coding: utf-8 -*-
"""
atomate2 dry-run workflow planning for Chalk.

This module deliberately creates auditable jobflow/atomate2 plans without
submitting or executing any calculation. The JSON contract is kept small so the
hypothesis pipeline, report renderer, and future HITL views do not depend on
jobflow internals.
"""

from __future__ import annotations

import importlib.metadata
import time
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


DEFAULT_WORKFLOW_KIND = "vasp_double_relax"


def _dependency_version(module_name: str, dist_name: Optional[str] = None) -> str:
    try:
        return importlib.metadata.version(dist_name or module_name)
    except Exception:
        try:
            module = __import__(module_name)
            return str(getattr(module, "__version__", "installed"))
        except Exception:
            return "not_installed"


def _warning_messages(caught: Iterable[warnings.WarningMessage]) -> List[str]:
    messages: List[str] = []
    seen = set()
    for item in caught:
        message = str(item.message).strip()
        if not message:
            continue
        text = f"{item.category.__name__}: {message}"
        if text in seen:
            continue
        seen.add(text)
        messages.append(text)
    return messages


def _try_import_dependencies() -> Tuple[Dict[str, Any], List[str], str]:
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        try:
            from pymatgen.core import Structure  # type: ignore
            from atomate2.vasp.flows.core import DoubleRelaxMaker  # type: ignore
            from atomate2.vasp.jobs.core import RelaxMaker, StaticMaker  # type: ignore

            deps = {
                "Structure": Structure,
                "DoubleRelaxMaker": DoubleRelaxMaker,
                "RelaxMaker": RelaxMaker,
                "StaticMaker": StaticMaker,
            }
            return deps, _warning_messages(caught), ""
        except Exception as exc:
            return {}, _warning_messages(caught), str(exc)


def _make_workflow(makers: Dict[str, Any], workflow_kind: str, structure: Any) -> Tuple[Any, str]:
    kind = (workflow_kind or DEFAULT_WORKFLOW_KIND).strip().lower()
    if kind in {"vasp_double_relax", "double_relax", "double-relax"}:
        maker = makers["DoubleRelaxMaker"]()
        return maker.make(structure), "DoubleRelaxMaker"
    if kind in {"vasp_relax", "relax"}:
        maker = makers["RelaxMaker"]()
        return maker.make(structure), "RelaxMaker"
    if kind in {"vasp_static", "static", "scf"}:
        maker = makers["StaticMaker"]()
        return maker.make(structure), "StaticMaker"
    raise ValueError(f"unsupported atomate2 workflow_kind: {workflow_kind}")


def _flow_jobs(flow_or_job: Any) -> List[Any]:
    jobs = getattr(flow_or_job, "jobs", None)
    if isinstance(jobs, list):
        return jobs
    return [flow_or_job]


def _source_summary(item: Dict[str, Any], path: Path) -> Dict[str, Any]:
    return {
        "path": str(path),
        "name": item.get("name", ""),
        "format": item.get("format", ""),
        "formula": item.get("formula_pretty") or item.get("formula") or "",
        "num_sites": item.get("num_sites", ""),
    }


@dataclass
class Atomate2DryRunPlanner:
    """Create atomate2/jobflow workflow metadata without running calculations."""

    workflow_kind: str = DEFAULT_WORKFLOW_KIND
    max_structures: int = 3

    def plan(
        self,
        structure_analyses: List[Dict[str, Any]],
        *,
        workflow_kind: Optional[str] = None,
    ) -> Dict[str, Any]:
        kind = workflow_kind or self.workflow_kind
        warnings_out: List[str] = []
        workflows: List[Dict[str, Any]] = []
        structures_seen = len(structure_analyses or [])

        candidates = [
            item for item in structure_analyses or []
            if isinstance(item, dict) and item.get("status") == "ok" and item.get("path")
        ][: self.max_structures]
        if not candidates:
            warnings_out.append("没有可用于 atomate2 dry-run 的 pymatgen 结构解析结果。")
            return self._build_report("skipped", structures_seen, workflows, warnings_out, kind)

        makers, import_warnings, import_error = _try_import_dependencies()
        warnings_out.extend(f"atomate2/jobflow 诊断: {message}" for message in import_warnings)
        if import_error:
            warnings_out.append(f"atomate2 dry-run 依赖不可用: {import_error}")
            return self._build_report("skipped", structures_seen, workflows, warnings_out, kind)

        Structure = makers["Structure"]
        for item in candidates:
            path = Path(str(item.get("path", ""))).expanduser()
            if not path.exists():
                warnings_out.append(f"atomate2 dry-run 跳过不存在的结构文件: {path.name}")
                continue
            try:
                with warnings.catch_warnings(record=True) as caught:
                    warnings.simplefilter("always")
                    structure = Structure.from_file(str(path))
                    flow_or_job, workflow_type = _make_workflow(makers, kind, structure)
                    flow_dict = flow_or_job.as_dict() if hasattr(flow_or_job, "as_dict") else {}
                warnings_out.extend(f"atomate2/jobflow 诊断: {message}" for message in _warning_messages(caught))

                jobs = _flow_jobs(flow_or_job)
                job_names = [
                    str(getattr(job, "name", "") or getattr(job, "uuid", "") or f"job_{idx}")
                    for idx, job in enumerate(jobs, 1)
                ]
                workflows.append(
                    {
                        "name": str(getattr(flow_or_job, "name", "") or flow_dict.get("name") or workflow_type),
                        "source_structure": _source_summary(item, path),
                        "status": "ok",
                        "engine": "atomate2",
                        "workflow_kind": kind,
                        "workflow_type": workflow_type,
                        "flow_class": type(flow_or_job).__name__,
                        "job_count": len(jobs),
                        "job_names": job_names,
                        "uuid": str(getattr(flow_or_job, "uuid", "") or flow_dict.get("uuid") or ""),
                        "as_dict_keys": sorted(str(key) for key in flow_dict.keys()) if isinstance(flow_dict, dict) else [],
                        "dry_run_only": True,
                        "execution_policy": "not_submitted",
                        "notes": [
                            "仅生成 atomate2/jobflow 工作流计划；未调用 run_locally，也未提交 VASP/DFT 作业。",
                            "输入结构来自 Qwen 生成并经 pymatgen 解析的候选文件，正式计算前需要人工复核晶格、坐标和赝势。",
                        ],
                    }
                )
            except Exception as exc:
                warnings_out.append(f"atomate2 dry-run 规划失败 {path.name}: {exc}")

        if workflows and warnings_out:
            status = "partial"
        elif workflows:
            status = "ok"
        else:
            status = "skipped" if not warnings_out else "partial"
        return self._build_report(status, structures_seen, workflows, warnings_out, kind)

    @staticmethod
    def _build_report(
        status: str,
        structures_seen: int,
        workflows: List[Dict[str, Any]],
        warnings_out: List[str],
        workflow_kind: str,
    ) -> Dict[str, Any]:
        jobs_planned = sum(int(item.get("job_count", 0) or 0) for item in workflows)
        return {
            "status": status,
            "workflow_kind": workflow_kind,
            "summary": {
                "structures_seen": structures_seen,
                "workflows_planned": len(workflows),
                "jobs_planned": jobs_planned,
                "warnings": len(warnings_out),
            },
            "workflows": workflows,
            "warnings": warnings_out,
            "tool_versions": {
                "atomate2": _dependency_version("atomate2"),
                "jobflow": _dependency_version("jobflow"),
                "ase": _dependency_version("ase"),
            },
            "execution_log": {
                "validator": "Atomate2DryRunPlanner",
                "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "dry_run_only": True,
                "checks": [
                    {
                        "name": "atomate2_workflow_plan",
                        "type": "atomate2_dry_run",
                        "status": "ready" if workflows else "review",
                        "workflows": len(workflows),
                        "jobs": jobs_planned,
                    }
                ],
            },
        }


def run_atomate2_dryrun(
    structure_analyses: List[Dict[str, Any]],
    workflow_kind: str = DEFAULT_WORKFLOW_KIND,
    *,
    max_structures: int = 3,
) -> Dict[str, Any]:
    planner = Atomate2DryRunPlanner(workflow_kind=workflow_kind, max_structures=max_structures)
    return planner.plan(structure_analyses)


def build_atomate2_context(dryrun_report: Dict[str, Any], max_chars: int = 2400) -> str:
    if not isinstance(dryrun_report, dict) or not dryrun_report:
        return ""
    versions = dryrun_report.get("tool_versions", {}) if isinstance(dryrun_report.get("tool_versions"), dict) else {}
    summary = dryrun_report.get("summary", {}) if isinstance(dryrun_report.get("summary"), dict) else {}
    lines = ["【atomate2 dry-run 工作流规划】"]
    lines.append(
        "版本: "
        f"atomate2={versions.get('atomate2', 'unknown')} | "
        f"jobflow={versions.get('jobflow', 'unknown')} | "
        f"ase={versions.get('ase', 'unknown')}"
    )
    lines.append(
        "摘要: "
        f"结构输入 {summary.get('structures_seen', 0)} 个, "
        f"规划工作流 {summary.get('workflows_planned', 0)} 个, "
        f"规划 Job {summary.get('jobs_planned', 0)} 个, "
        "执行策略=not_submitted。"
    )
    for workflow in dryrun_report.get("workflows", [])[:5]:
        if not isinstance(workflow, dict):
            continue
        source = workflow.get("source_structure", {}) if isinstance(workflow.get("source_structure"), dict) else {}
        job_names = workflow.get("job_names", [])
        jobs = ", ".join(str(name) for name in job_names[:4]) if isinstance(job_names, list) else str(job_names)
        lines.append(
            f"- {workflow.get('workflow_type', '')} / {workflow.get('name', '')}: "
            f"{workflow.get('job_count', 0)} jobs ({jobs}); "
            f"structure={source.get('formula', '')}; path={source.get('path', '')}"
        )
    dryrun_warnings = dryrun_report.get("warnings", [])
    if dryrun_warnings:
        lines.append("警告: " + "; ".join(str(w) for w in dryrun_warnings[:4]))
    return "\n".join(lines)[:max_chars]
