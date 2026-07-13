from __future__ import annotations

import json
import math
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any


def camelize(value: Any) -> Any:
    if isinstance(value, list):
        return [camelize(item) for item in value]
    if isinstance(value, dict):
        return {_camel_key(str(key)): camelize(item) for key, item in value.items()}
    return value


def _camel_key(value: str) -> str:
    parts = value.split("_")
    return parts[0] + "".join(part[:1].upper() + part[1:] for part in parts[1:])


def cleaned_point_to_dict(point) -> dict[str, Any]:
    return {
        "parameter": point.parameter,
        "parameterCn": point.parameter_cn,
        "value": point.value,
        "unit": point.unit,
        "originalValue": point.original_value,
        "originalUnit": point.original_unit,
        "source": point.source,
        "isSuspicious": point.is_suspicious,
        "suspiciousReason": point.suspicious_reason,
        "precision": point.precision,
    }


def workbook_coverage(path: Path, sheet_names: list[str]) -> list[dict[str, Any]]:
    import pandas as pd

    suffix = path.suffix.lower()
    if suffix == ".csv":
        frame = pd.read_csv(path)
        return [{
            "sheetName": "CSV",
            "rowsRead": int(frame.shape[0]),
            "columnsRead": int(frame.shape[1]),
            "rowsAnalyzed": int(frame.shape[0]),
            "rowsSentToModel": min(int(frame.shape[0]), 100),
            "strategy": "full-statistics-first-100-rows-for-model" if frame.shape[0] > 100 else "full",
        }]
    excel = pd.ExcelFile(path)
    selected = sheet_names or list(excel.sheet_names[:10])
    coverage = []
    for sheet in selected:
        if sheet not in excel.sheet_names:
            raise ValueError(f"Unknown workbook sheet: {sheet}")
        frame = pd.read_excel(excel, sheet_name=sheet)
        coverage.append({
            "sheetName": sheet,
            "rowsRead": int(frame.shape[0]),
            "columnsRead": int(frame.shape[1]),
            "rowsAnalyzed": int(frame.shape[0]),
            "rowsSentToModel": min(int(frame.shape[0]), 100),
            "strategy": "full-statistics-first-100-rows-for-model" if frame.shape[0] > 100 else "full",
        })
    return coverage


def dataframe_model_context(frame, *, max_rows: int = 100, max_chars: int = 15000) -> str:
    numeric = frame.select_dtypes(include="number")
    statistics = numeric.describe().to_string() if not numeric.empty else "No numeric columns."
    missing = frame.isna().sum().to_string()
    dtypes = frame.dtypes.astype(str).to_string()
    sample = frame.head(max_rows).to_csv(index=False)
    context = (
        f"Shape: {frame.shape[0]} rows x {frame.shape[1]} columns\n"
        f"Columns and data types:\n{dtypes}\n\n"
        f"Missing values over the full dataset:\n{missing}\n\n"
        f"Descriptive statistics over the full dataset:\n{statistics}\n\n"
        f"First {min(len(frame), max_rows)} rows sent to the model:\n{sample}"
    )
    return context[:max_chars]


def select_multimodal_literature_context(
    text: str,
    query: str = "",
    *,
    segment_chars: int = 3000,
    max_chars: int = 12000,
) -> tuple[str, dict[str, Any]]:
    source = str(text or "").strip()
    chunks = [source[index:index + segment_chars] for index in range(0, len(source), segment_chars)]
    if not chunks:
        return "", {
            "sourceChars": 0,
            "includedChars": 0,
            "sourceSegmentCount": 0,
            "selectedSegmentIndices": [],
            "truncated": False,
            "strategy": "query-ranked-full-document-segments",
        }
    query_terms = {
        token.lower()
        for token in re.findall(r"[A-Za-z0-9][A-Za-z0-9_.+-]{1,}|[\u4e00-\u9fff]{2,}", str(query or ""))
    }
    domain_terms = {
        "method", "methods", "experimental", "characterization", "measurement", "calibration",
        "figure", "spectrum", "spectra", "microscopy", "diffraction", "raman", "xrd", "xps",
        "方法", "实验", "表征", "测量", "校准", "图", "光谱", "显微", "衍射",
    }
    ranked = sorted(
        enumerate(chunks),
        key=lambda item: (
            -sum(item[1].lower().count(term) * (3 if term in query_terms else 1) for term in query_terms | domain_terms),
            item[0],
        ),
    )
    selected_indices: list[int] = []
    included_chars = 0
    for index, chunk in ranked:
        if selected_indices and included_chars + len(chunk) > max_chars:
            continue
        selected_indices.append(index)
        included_chars += len(chunk)
        if included_chars >= max_chars:
            break
    selected_indices.sort()
    context = "\n\n".join(chunks[index] for index in selected_indices)
    return context, {
        "sourceChars": len(source),
        "includedChars": included_chars,
        "sourceSegmentCount": len(chunks),
        "selectedSegmentIndices": selected_indices,
        "truncated": included_chars < len(source),
        "strategy": "query-ranked-full-document-segments",
    }


def build_multimodal_context(items: list[dict[str, Any]], associations: list[dict[str, Any]]) -> str:
    lines = ["=== Multimodal evidence ==="]
    for item in items:
        if item.get("status") == "failed":
            continue
        lines.append(f"\n### {item.get('source', {}).get('fileName', 'source')} [{item.get('imageType', '')}]")
        for point in item.get("dataPoints", []):
            marker = " [suspicious]" if point.get("isSuspicious") else ""
            lines.append(f"- {point.get('parameterCn') or point.get('parameter')}: {point.get('value')} {point.get('unit', '')}{marker}")
    if associations:
        lines.append("\n### Cross-source associations")
        lines.extend(f"- {item.get('description', '')}" for item in associations)
    return "\n".join(lines)


def rebuild_multimodal_derived_result(
    result: dict[str, Any],
    *,
    miner_module=None,
    recompute_relationships: bool,
) -> dict[str, Any]:
    rebuilt = safe_result_json(result)
    items = [item for item in rebuilt.get("items", []) if isinstance(item, dict)]
    succeeded = [item for item in items if item.get("status") != "failed"]
    warnings = [str(value) for value in rebuilt.get("warnings", [])]
    associations = list(rebuilt.get("associations") or [])
    quantitative = rebuilt.get("quantitative") if isinstance(rebuilt.get("quantitative"), dict) else {}
    points = _corrected_miner_points(succeeded)
    if recompute_relationships:
        associations = []
        quantitative = {"summary": {}, "scalingRelations": [], "correlations": []}
        if miner_module is not None and len(points) >= 2:
            try:
                report = miner_module.DataMiner().mine(points, use_llm=False)
                associations = [camelize(item.to_dict()) for item in report.associations]
            except Exception:
                warnings.append("Associations could not be recalculated after the data correction.")
        if miner_module is not None and len(points) >= 3:
            try:
                quantitative = camelize(miner_module.QuantitativeMiner().mine(points).to_dict())
            except Exception:
                warnings.append("Quantitative relations could not be recalculated after the data correction.")
    rebuilt["associations"] = associations
    rebuilt["quantitative"] = quantitative
    rebuilt["context"] = build_multimodal_context(items, associations)
    rebuilt["evidence"] = {
        "figures": [{
            "source": item.get("source"),
            "imageType": item.get("imageType"),
            "summary": item.get("summary"),
            "points": item.get("dataPoints", []),
            "annotations": item.get("annotations", []),
        } for item in succeeded],
        "associations": associations,
    }
    summary = rebuilt.get("summary") if isinstance(rebuilt.get("summary"), dict) else {}
    summary.update({
        "total": len(items),
        "succeeded": len(succeeded),
        "failed": len(items) - len(succeeded),
        "dataPoints": sum(len(item.get("dataPoints") or []) for item in succeeded),
        "associations": len(associations),
    })
    rebuilt["summary"] = summary
    rebuilt["warnings"] = list(dict.fromkeys(warnings))
    return safe_result_json(rebuilt)


def _corrected_miner_points(items: list[dict[str, Any]]) -> list[SimpleNamespace]:
    values: list[SimpleNamespace] = []
    for item in items:
        for point in item.get("dataPoints", []) if isinstance(item.get("dataPoints"), list) else []:
            if not isinstance(point, dict) or isinstance(point.get("value"), bool):
                continue
            try:
                numeric_value = float(point.get("value"))
            except (TypeError, ValueError):
                continue
            if not math.isfinite(numeric_value):
                continue
            values.append(SimpleNamespace(
                parameter=str(point.get("parameter") or ""),
                parameter_cn=str(point.get("parameterCn") or ""),
                value=numeric_value,
                unit=str(point.get("unit") or ""),
                original_value=str(point.get("originalValue") or point.get("value") or ""),
                original_unit=str(point.get("originalUnit") or point.get("unit") or ""),
                source=str(point.get("source") or item.get("source", {}).get("fileName") or ""),
                is_suspicious=bool(point.get("isSuspicious", False)),
                suspicious_reason=str(point.get("suspiciousReason") or point.get("note") or ""),
                precision=int(point.get("precision") or 3),
            ))
    return values


def safe_result_json(value: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))
