from __future__ import annotations

import copy
import hashlib
import json
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

from app.core.config import PROJECT_ROOT


LOCALIZATION_PATH = PROJECT_ROOT / "benchmarks" / "science125" / "science125-zh-CN-v1.json"
LOCALIZATION_VERSION = "science125-zh-CN-v1"
BASE_MANIFEST_CONTENT_SHA256 = "de0a173974a4ac1863186ff11c68d6946975228d53a552442aec8f8d951226d3"
EXPECTED_LOCALIZED_IDS = frozenset(f"S125-{index:03d}" for index in range(1, 126))
DETAILED_PILOT_IDS = frozenset({"S125-006", "S125-043", "S125-054"})
TRANSLATION_REVIEW_STATUSES = frozenset({"reviewed", "translated_pending_review"})


class Science125LocalizationError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("The Science 125 Chinese localization is unavailable.")


@dataclass(frozen=True, slots=True)
class RelevanceConcept:
    label_zh: str
    terms: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Science125Localization:
    question_id: str
    question_zh: str
    search_intent_zh: str
    recommended_query: str
    relevance_concepts: tuple[RelevanceConcept, ...]
    translation_review_status: str
    localization_version: str = LOCALIZATION_VERSION


def _default_search_intent(question_zh: str) -> str:
    return (
        f"围绕“{question_zh}”检索相关研究，重点关注现有证据、关键机制、"
        "可检验假设、研究方法、适用边界、局限性与反例。"
    )


def _canonical_json(payload: Mapping[str, Any]) -> str:
    return unicodedata.normalize(
        "NFC",
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
    )


def _content_hash(payload: Mapping[str, Any]) -> str:
    value = copy.deepcopy(dict(payload))
    value.pop("localizationContentSha256", None)
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def load_science125_localizations(
    path: Path = LOCALIZATION_PATH,
) -> dict[str, Science125Localization]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("localization must be an object")
        if raw.get("localizationVersion") != LOCALIZATION_VERSION:
            raise ValueError("unexpected localization version")
        if raw.get("locale") != "zh-CN":
            raise ValueError("unexpected localization locale")
        if raw.get("baseManifestVersion") != "science125-v1":
            raise ValueError("unexpected base manifest version")
        if raw.get("baseManifestContentSha256") != BASE_MANIFEST_CONTENT_SHA256:
            raise ValueError("localization is not bound to the authoritative manifest")
        if raw.get("localizationContentSha256") != _content_hash(raw):
            raise ValueError("localization content hash mismatch")
        items = raw.get("items")
        if not isinstance(items, list):
            raise ValueError("localization items must be a list")
        output: dict[str, Science125Localization] = {}
        for item in items:
            if not isinstance(item, dict):
                raise ValueError("localization item must be an object")
            question_id = str(item.get("questionId") or "").strip()
            if not question_id.startswith("S125-") or question_id in output:
                raise ValueError("localization question IDs are invalid")
            raw_concepts = item.get("relevanceConcepts", [])
            if not isinstance(raw_concepts, list):
                raise ValueError("localization relevance concepts must be a list")
            concepts: list[RelevanceConcept] = []
            for concept in raw_concepts:
                if not isinstance(concept, dict):
                    raise ValueError("relevance concept must be an object")
                label = str(concept.get("labelZh") or "").strip()
                terms = concept.get("terms")
                if not label or not isinstance(terms, list):
                    raise ValueError("relevance concept is invalid")
                normalized_terms = tuple(
                    dict.fromkeys(str(term).strip().casefold() for term in terms if str(term).strip())
                )
                if not normalized_terms:
                    raise ValueError("relevance concept terms are required")
                concepts.append(RelevanceConcept(label_zh=label, terms=normalized_terms))
            question_zh = str(item.get("questionZh") or "").strip()
            search_intent_zh = str(item.get("searchIntentZh") or "").strip()
            review_status = str(item.get("translationReviewStatus") or "").strip()
            translation = Science125Localization(
                question_id=question_id,
                question_zh=question_zh,
                search_intent_zh=search_intent_zh or _default_search_intent(question_zh),
                recommended_query=str(item.get("recommendedQuery") or "").strip(),
                relevance_concepts=tuple(concepts),
                translation_review_status=review_status,
            )
            if (
                not translation.question_zh
                or not translation.search_intent_zh
                or translation.translation_review_status not in TRANSLATION_REVIEW_STATUSES
            ):
                raise ValueError("localization text is incomplete")
            if question_id in DETAILED_PILOT_IDS and (
                not translation.recommended_query
                or not translation.relevance_concepts
                or translation.translation_review_status != "reviewed"
            ):
                raise ValueError("pilot localization requires reviewed retrieval metadata")
            output[question_id] = translation
        if set(output) != EXPECTED_LOCALIZED_IDS:
            raise ValueError("localization must cover exactly the enabled pilot questions")
        return output
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
        raise Science125LocalizationError() from None


@lru_cache(maxsize=1)
def get_science125_localizations() -> dict[str, Science125Localization]:
    return load_science125_localizations()


def get_science125_localization(question_id: str) -> Science125Localization | None:
    return get_science125_localizations().get(str(question_id).strip())


__all__ = [
    "LOCALIZATION_PATH",
    "LOCALIZATION_VERSION",
    "DETAILED_PILOT_IDS",
    "RelevanceConcept",
    "Science125Localization",
    "Science125LocalizationError",
    "get_science125_localization",
    "get_science125_localizations",
    "load_science125_localizations",
]
