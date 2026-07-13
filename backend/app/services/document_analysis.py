from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import quote

import requests
from pydantic import BaseModel, Field, ValidationError


class ModelOutputError(ValueError):
    pass


class SopChemical(BaseModel):
    name: str = ""
    amount: str = ""
    role: str = ""
    safety: str = ""


class SopStep(BaseModel):
    step: int = 0
    action: str = ""
    params: str = ""
    safety_note: str = Field(default="", alias="safetyNote")

    model_config = {"populate_by_name": True}


class SopResult(BaseModel):
    title: str = ""
    chemicals: list[SopChemical] = Field(default_factory=list)
    steps: list[SopStep] = Field(default_factory=list)
    post_processing: str = Field(default="", alias="postProcessing")
    characterization: str = ""

    model_config = {"populate_by_name": True}


class ReactionItem(BaseModel):
    name: str = ""
    reactants: list[str] = Field(default_factory=list)
    products: list[str] = Field(default_factory=list)
    catalyst: str = ""
    solvent: str = ""
    temperature: str = ""
    time: str = ""
    pressure: str = ""
    ph: str = ""
    yield_value: str = Field(default="", alias="yield")
    workup: str = ""
    notes: str = ""

    model_config = {"populate_by_name": True}


class ReactionResult(BaseModel):
    reactions: list[ReactionItem] = Field(default_factory=list)
    summary: str = ""


class GlossaryItem(BaseModel):
    en: str = ""
    zh: str = ""
    note: str = ""


class TranslationResult(BaseModel):
    translation: str
    glossary: list[GlossaryItem] = Field(default_factory=list)


def segment_texts(texts: list[str], max_chars: int) -> list[str]:
    if max_chars < 1:
        raise ValueError("max_chars must be positive")
    combined = "\n\n".join(str(text) for text in texts)
    if not combined:
        return []
    segments: list[str] = []
    remaining = combined
    while len(remaining) > max_chars:
        cut = remaining.rfind("\n\n", 0, max_chars + 1)
        if cut < max_chars // 2:
            cut = max_chars
        else:
            cut += 2
        segments.append(remaining[:cut])
        remaining = remaining[cut:]
    if remaining:
        segments.append(remaining)
    return segments


def parse_json_object(raw: str) -> dict[str, Any]:
    text = str(raw or "").strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        value = json.loads(text)
    except (TypeError, ValueError) as exc:
        raise ModelOutputError("The model returned invalid JSON.") from exc
    if not isinstance(value, dict):
        raise ModelOutputError("The model returned a non-object JSON value.")
    return value


def parse_sop_result(raw: str) -> dict[str, Any]:
    try:
        result = SopResult.model_validate(parse_json_object(raw))
    except ValidationError as exc:
        raise ModelOutputError("The model returned an invalid SOP structure.") from exc
    return result.model_dump(by_alias=True)


def parse_reaction_result(raw: str) -> dict[str, Any]:
    try:
        result = ReactionResult.model_validate(parse_json_object(raw))
    except ValidationError as exc:
        raise ModelOutputError("The model returned an invalid reaction structure.") from exc
    return result.model_dump(by_alias=True)


def parse_translation_result(raw: str) -> dict[str, Any]:
    try:
        result = TranslationResult.model_validate(parse_json_object(raw))
    except ValidationError as exc:
        raise ModelOutputError("The model returned an invalid translation structure.") from exc
    if not result.translation.strip():
        raise ModelOutputError("The model returned an empty translation.")
    return result.model_dump()


def _normalized_key(*values: Any) -> str:
    return "|".join(re.sub(r"\s+", " ", str(value or "").strip().lower()) for value in values)


def _join_unique(values: list[str]) -> str:
    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        clean = str(value or "").strip()
        key = _normalized_key(clean)
        if clean and key not in seen:
            seen.add(key)
            output.append(clean)
    return "\n\n".join(output)


def merge_sop_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    title = next((str(item.get("title") or "").strip() for item in results if item.get("title")), "")
    chemicals: list[dict[str, Any]] = []
    chemical_keys: set[str] = set()
    steps: list[dict[str, Any]] = []
    step_keys: set[str] = set()
    for result in results:
        for chemical in result.get("chemicals") or []:
            key = _normalized_key(
                chemical.get("name"), chemical.get("amount"), chemical.get("role"), chemical.get("safety")
            )
            if key and key not in chemical_keys:
                chemical_keys.add(key)
                chemicals.append({
                    "name": str(chemical.get("name") or ""),
                    "amount": str(chemical.get("amount") or ""),
                    "role": str(chemical.get("role") or ""),
                    "safety": str(chemical.get("safety") or ""),
                })
        for step in result.get("steps") or []:
            safety_note = step.get("safetyNote", step.get("safety_note", ""))
            key = _normalized_key(step.get("action"), step.get("params"), safety_note)
            if key and key not in step_keys:
                step_keys.add(key)
                steps.append({
                    "step": len(steps) + 1,
                    "action": str(step.get("action") or ""),
                    "params": str(step.get("params") or ""),
                    "safetyNote": str(safety_note or ""),
                })
    return {
        "title": title,
        "chemicals": chemicals,
        "steps": steps,
        "postProcessing": _join_unique([
            str(item.get("postProcessing", item.get("post_processing", ""))) for item in results
        ]),
        "characterization": _join_unique([str(item.get("characterization") or "") for item in results]),
    }


def merge_reaction_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    reactions: list[dict[str, Any]] = []
    seen: set[str] = set()
    for result in results:
        for reaction in result.get("reactions") or []:
            key = _normalized_key(
                reaction.get("name"),
                ",".join(reaction.get("reactants") or []),
                ",".join(reaction.get("products") or []),
                reaction.get("catalyst"),
                reaction.get("temperature"),
                reaction.get("time"),
            )
            if key and key not in seen:
                seen.add(key)
                reactions.append({
                    "name": str(reaction.get("name") or ""),
                    "reactants": [str(value) for value in reaction.get("reactants") or []],
                    "products": [str(value) for value in reaction.get("products") or []],
                    "catalyst": str(reaction.get("catalyst") or ""),
                    "solvent": str(reaction.get("solvent") or ""),
                    "temperature": str(reaction.get("temperature") or ""),
                    "time": str(reaction.get("time") or ""),
                    "pressure": str(reaction.get("pressure") or ""),
                    "ph": str(reaction.get("ph") or ""),
                    "yield": str(reaction.get("yield") or ""),
                    "workup": str(reaction.get("workup") or ""),
                    "notes": str(reaction.get("notes") or ""),
                })
    return {
        "summary": _join_unique([str(item.get("summary") or "") for item in results]),
        "reactions": reactions,
    }


def merge_glossary(items: list[dict[str, Any]]) -> list[dict[str, str]]:
    merged: dict[str, dict[str, str]] = {}
    order: list[str] = []
    for item in items:
        en = str(item.get("en") or "").strip()
        if not en:
            continue
        key = _normalized_key(en)
        if key not in merged:
            merged[key] = {"en": en, "zh": "", "note": ""}
            order.append(key)
        target = merged[key]
        if not target["zh"]:
            target["zh"] = str(item.get("zh") or "").strip()
        if not target["note"]:
            target["note"] = str(item.get("note") or "").strip()
    return [merged[key] for key in order]


HIGH_RISK_KEYWORDS = (
    "toxic",
    "fatal",
    "explosive",
    "flammable",
    "corrosive",
    "oxidizing",
    "carcinogen",
    "mutagen",
    "reproductive",
    "danger",
)


def normalize_local_hazard(name: str, entry: dict[str, Any]) -> dict[str, Any]:
    hazards = [str(value) for value in entry.get("hazards") or [] if str(value).strip()]
    return {
        "name": name,
        "source": "local",
        "status": "ok",
        "signalWord": str(entry.get("signal_word") or ""),
        "ghsCodes": [str(value) for value in entry.get("ghs_codes") or []],
        "pictograms": [str(value) for value in entry.get("pictograms") or []],
        "hazards": hazards,
        "highRisk": bool(entry.get("high_risk")),
        "warning": "",
    }


def _pubchem_information_values(value: Any) -> list[str]:
    output: list[str] = []
    if isinstance(value, dict):
        information = value.get("Information")
        if isinstance(information, dict):
            name = str(information.get("Name") or "").strip()
            raw = information.get("StringValue", information.get("NumericValue", ""))
            if isinstance(raw, list):
                rendered = "; ".join(str(item) for item in raw if str(item).strip())
            else:
                rendered = str(raw or "").strip()
            if name and rendered:
                output.append(f"{name}: {rendered}")
        for nested in value.values():
            output.extend(_pubchem_information_values(nested))
    elif isinstance(value, list):
        for item in value:
            output.extend(_pubchem_information_values(item))
    return output


def lookup_pubchem_hazard(name: str, timeout: int = 8) -> dict[str, Any]:
    url = (
        "https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/"
        f"{quote(name, safe='')}/classification/JSON?classification_type=GHS"
    )
    try:
        response = requests.get(url, timeout=timeout)
        if response.status_code != 200:
            return {
                "name": name,
                "source": "pubchem",
                "status": "unavailable",
                "signalWord": "",
                "ghsCodes": [],
                "pictograms": [],
                "hazards": [],
                "highRisk": False,
                "warning": f"PubChem returned HTTP {response.status_code}.",
            }
        hazards = []
        seen: set[str] = set()
        for item in _pubchem_information_values(response.json()):
            key = _normalized_key(item)
            if key not in seen:
                seen.add(key)
                hazards.append(item)
            if len(hazards) >= 30:
                break
        combined = " ".join(hazards).lower()
        return {
            "name": name,
            "source": "pubchem",
            "status": "ok" if hazards else "no_data",
            "signalWord": "",
            "ghsCodes": [],
            "pictograms": [],
            "hazards": hazards,
            "highRisk": any(keyword in combined for keyword in HIGH_RISK_KEYWORDS),
            "warning": "" if hazards else "PubChem did not return GHS classification data.",
        }
    except (requests.RequestException, ValueError, TypeError):
        return {
            "name": name,
            "source": "pubchem",
            "status": "unavailable",
            "signalWord": "",
            "ghsCodes": [],
            "pictograms": [],
            "hazards": [],
            "highRisk": False,
            "warning": "PubChem safety data could not be retrieved.",
        }
