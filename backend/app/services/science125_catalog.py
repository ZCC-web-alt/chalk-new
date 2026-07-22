from __future__ import annotations

import copy
import hashlib
import json
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Any, get_args

from pydantic import ValidationError

from app.core.config import PROJECT_ROOT
from app.schemas.science125 import (
    Science125ProviderOut,
    Science125ProviderReadinessOut,
    Science125QuestionListOut,
    Science125QuestionOut,
    Science125QuestionProfileOut,
    Science125RoutingEntry,
    Science125RoutingManifest,
)
from app.schemas.research import Science125CrossDomainTag
from app.services.science125_retrieval import (
    get_provider,
    get_science125_retrieval_profile,
    profile_readiness,
)


MANIFEST_PATH = PROJECT_ROOT / "benchmarks" / "science125" / "science125-v1.json"
ROUTING_PATH = PROJECT_ROOT / "benchmarks" / "science125" / "science125-routing-v1.json"
MANIFEST_VERSION = "science125-v1"
ROUTING_VERSION = "science125-routing-v1"
MANIFEST_CONTENT_SHA256 = "de0a173974a4ac1863186ff11c68d6946975228d53a552442aec8f8d951226d3"
SCIENCE125_PILOT_IDS = frozenset({"S125-006", "S125-043", "S125-054"})


class Science125CatalogError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("The Science 125 question catalog is unavailable.")


class Science125RoutingError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("The Science 125 routing profile is unavailable.")


def _canonical_text(value: str) -> str:
    return unicodedata.normalize("NFC", " ".join(value.split()))


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical_json(payload: dict[str, Any]) -> str:
    return unicodedata.normalize(
        "NFC",
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
    )


def _content_hash(payload: dict[str, Any], field: str) -> str:
    value = copy.deepcopy(payload)
    value.pop(field, None)
    return _sha256(_canonical_json(value).encode("utf-8"))


def _read_json(path: Path) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("manifest must be an object")
    return raw


def _load_authoritative_manifest(path: Path = MANIFEST_PATH) -> dict[str, Any]:
    raw = _read_json(path)
    if raw.get("manifestVersion") != MANIFEST_VERSION:
        raise ValueError("unexpected manifest version")
    if raw.get("manifestContentSha256") != MANIFEST_CONTENT_SHA256:
        raise ValueError("unexpected authoritative manifest hash")
    if _content_hash(raw, "manifestContentSha256") != MANIFEST_CONTENT_SHA256:
        raise ValueError("manifest content hash mismatch")
    questions = raw.get("questions")
    if not isinstance(questions, list) or len(questions) != 125:
        raise ValueError("manifest must contain exactly 125 questions")
    return raw


def _parse_manifest_questions(raw: dict[str, Any]) -> list[dict[str, Any]]:
    questions = raw["questions"]
    parsed: list[dict[str, Any]] = []
    canonical_questions: set[str] = set()
    for number, item in enumerate(questions, start=1):
        if not isinstance(item, dict):
            raise ValueError("question item must be an object")
        question_id = item.get("id")
        if question_id != f"S125-{number:03d}":
            raise ValueError("question IDs are not continuous")
        public_values = {
            "id": question_id,
            "question": item.get("question"),
            "sourceDomain": item.get("sourceDomain"),
            "benchmarkDomain": item.get("benchmarkDomain"),
            "pdfPage": item.get("pdfPage"),
            "bookletPage": item.get("bookletPage"),
        }
        # Routing fields are merged after the independent routing document has
        # been validated against this authoritative manifest.
        question = str(item.get("question") or "")
        canonical_question = _canonical_text(question)
        if canonical_question in canonical_questions:
            raise ValueError("question text is not unique")
        canonical_questions.add(canonical_question)
        if item.get("contentSha256") != _sha256(canonical_question.encode("utf-8")):
            raise ValueError("question content hash mismatch")
        if int(item.get("pdfPage") or 0) - int(item.get("bookletPage") or 0) != 2:
            raise ValueError("question page mapping is invalid")
        # Validate the non-routing portion with the public model's primitive
        # fields once the route is available; retaining the raw values here
        # keeps the two manifests independently hash-verifiable.
        parsed.append(public_values)
    return parsed


def _parse_manifest(raw: dict[str, Any], routing: Science125RoutingManifest) -> Science125QuestionListOut:
    manifest_questions = _parse_manifest_questions(raw)
    routes = {item.question_id: item for item in routing.questions}
    if set(routes) != {item["id"] for item in manifest_questions}:
        raise ValueError("routing question IDs do not match the authoritative manifest")

    parsed: list[Science125QuestionOut] = []
    for item in manifest_questions:
        route = routes[item["id"]]
        if route.benchmark_domain != item["benchmarkDomain"]:
            raise ValueError("routing benchmark domain differs from authoritative manifest")
        route_values = route.model_dump(by_alias=True)
        route_values.pop("questionId", None)
        values = {
            **item,
            **route_values,
        }
        parsed.append(Science125QuestionOut.model_validate(values))

    return Science125QuestionListOut(
        manifestVersion=MANIFEST_VERSION,
        routingVersion=ROUTING_VERSION,
        data=tuple(parsed),
    )


def _parse_routing(raw: dict[str, Any], manifest: dict[str, Any]) -> Science125RoutingManifest:
    if raw.get("routingVersion") != ROUTING_VERSION:
        raise ValueError("unexpected routing version")
    if raw.get("baseManifestVersion") != MANIFEST_VERSION:
        raise ValueError("unexpected routing base manifest version")
    if raw.get("baseManifestContentSha256") != manifest.get("manifestContentSha256"):
        raise ValueError("routing is not bound to the authoritative manifest")
    if raw.get("routingContentSha256") != _content_hash(raw, "routingContentSha256"):
        raise ValueError("routing content hash mismatch")

    manifest_by_id = {item["id"]: item for item in manifest["questions"]}
    parsed = Science125RoutingManifest.model_validate(raw)
    if len(parsed.questions) != 125:
        raise ValueError("routing must contain exactly 125 questions")
    if [item.question_id for item in parsed.questions] != [f"S125-{i:03d}" for i in range(1, 126)]:
        raise ValueError("routing question IDs are not continuous")

    prompt_by_domain = {
        "Mathematical Sciences": "s125.mathematics.v1",
        "Chemistry": "s125.chemistry.v1",
        "Medicine & Health": "s125.medicine.v1",
        "Biology": "s125.biology.v1",
        "Astronomy": "s125.astronomy.v1",
        "Physics": "s125.physics.v1",
        "Engineering & Materials Science": "s125.engineering_materials.v1",
        "Information Science": "s125.information_science.v1",
        "Neuroscience": "s125.neuroscience.v1",
        "Ecology": "s125.ecology.v1",
        "Energy Science": "s125.energy.v1",
        "Artificial Intelligence": "s125.ai.v1",
    }
    for route in parsed.questions:
        source = manifest_by_id.get(route.question_id)
        if source is None or route.benchmark_domain != source["benchmarkDomain"]:
            raise ValueError("routing benchmark domain differs from authoritative manifest")
        if route.prompt_profile != prompt_by_domain[route.benchmark_domain]:
            raise ValueError("routing prompt profile does not match benchmark domain")
        try:
            get_science125_retrieval_profile(route.retrieval_profile)
        except KeyError as exc:
            raise ValueError("routing retrieval profile is not registered") from exc
        if len(set(route.cross_domain_tags)) != len(route.cross_domain_tags):
            raise ValueError("routing cross-domain tags must be unique")
        allowed_cross_domain_tags = set(get_args(Science125CrossDomainTag))
        if not set(route.cross_domain_tags).issubset(allowed_cross_domain_tags):
            raise ValueError("routing cross-domain tags are not supported by research-v1")
    return parsed


def load_science125_routing(
    path: Path = ROUTING_PATH,
    manifest_path: Path = MANIFEST_PATH,
) -> Science125RoutingManifest:
    try:
        manifest = _load_authoritative_manifest(manifest_path)
        return _parse_routing(_read_json(path), manifest)
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError, KeyError, ValidationError):
        raise Science125RoutingError() from None


@lru_cache(maxsize=1)
def get_science125_routing() -> Science125RoutingManifest:
    return load_science125_routing()


def load_science125_catalog(
    path: Path = MANIFEST_PATH,
    routing_path: Path = ROUTING_PATH,
) -> Science125QuestionListOut:
    try:
        manifest = _load_authoritative_manifest(path)
        routing = load_science125_routing(routing_path, path)
        return _parse_manifest(manifest, routing)
    except (
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        TypeError,
        ValueError,
        KeyError,
        ValidationError,
        Science125RoutingError,
    ):
        raise Science125CatalogError() from None


@lru_cache(maxsize=1)
def get_science125_catalog() -> Science125QuestionListOut:
    return load_science125_catalog()


def get_science125_route(question_id: str) -> Science125RoutingEntry:
    try:
        for route in get_science125_routing().questions:
            if route.question_id == question_id:
                return route
    except Science125RoutingError:
        raise
    raise Science125RoutingError()


def is_science125_pilot_enabled(question_id: str) -> bool:
    return str(question_id).strip() in SCIENCE125_PILOT_IDS


_ENV_PROVIDER_IDS: dict[str, str] = {
    "SCIENCE125_CROSSREF_MAILTO": "crossref",
    "SCIENCE125_OPENALEX_MAILTO": "openalex",
    "SCIENCE125_NCBI_API_KEY": "ncbi",
    "SCIENCE125_NCBI_TOOL_EMAIL": "ncbi",
    "SCIENCE125_SEMANTIC_SCHOLAR_API_KEY": "semantic_scholar",
    "SCIENCE125_NASA_ADS_API_TOKEN": "nasa_ads",
}


def _public_configuration_code(code: str) -> str:
    """Convert internal environment names into stable, non-secret status codes."""
    normalized = str(code).strip()
    if normalized.startswith("MISSING_"):
        normalized = normalized.removeprefix("MISSING_")
    if normalized.endswith("_API_KEY") or normalized.endswith("_API_TOKEN"):
        if "SEMANTIC_SCHOLAR" in normalized:
            return "SEMANTIC_SCHOLAR_CREDENTIAL_REQUIRED"
        if "NASA_ADS" in normalized:
            return "NASA_ADS_CREDENTIAL_REQUIRED"
        if "NCBI" in normalized:
            return "NCBI_CREDENTIAL_REQUIRED"
    if normalized.endswith("_MAILTO"):
        if "CROSSREF" in normalized:
            return "CROSSREF_CONTACT_REQUIRED"
        if "OPENALEX" in normalized:
            return "OPENALEX_CONTACT_REQUIRED"
    if normalized.endswith("_TOOL_EMAIL"):
        return "NCBI_TOOL_EMAIL_REQUIRED"
    if normalized.endswith("_CREDENTIAL_REQUIRED") or normalized.endswith("_TOKEN_REQUIRED"):
        return normalized
    return "PROVIDER_CONFIGURATION_REQUIRED"


def get_science125_question_profile(question_id: str) -> Science125QuestionProfileOut:
    route = get_science125_route(question_id)
    try:
        retrieval_profile = get_science125_retrieval_profile(route.retrieval_profile)
        readiness = profile_readiness(route.retrieval_profile)
    except KeyError:
        raise Science125RoutingError()
    required_ids = set(retrieval_profile.required_providers)
    for env_var in retrieval_profile.required_env_vars:
        provider_id = _ENV_PROVIDER_IDS.get(env_var)
        if provider_id in retrieval_profile.provider_ids:
            required_ids.add(provider_id)
    providers: list[Science125ProviderOut] = []
    provider_readiness: list[Science125ProviderReadinessOut] = []
    readiness_by_id = {item.provider_id: item for item in readiness.providers}
    raw_profile_missing_codes = set(readiness.missing_configuration_codes)
    profile_missing_codes = tuple(
        dict.fromkeys(_public_configuration_code(code) for code in readiness.missing_configuration_codes)
    )
    for provider_id in retrieval_profile.provider_ids:
        try:
            definition = get_provider(provider_id)
        except KeyError:
            raise Science125RoutingError()
        required = provider_id in required_ids
        # ProviderDefinition is deliberately the only source for URL and
        # official policy metadata. Display names remain presentation-only.
        display_name = {
            "arxiv": "arXiv",
            "ncbi": "NCBI E-utilities",
            "semantic_scholar": "Semantic Scholar",
            "crossref": "Crossref",
            "openalex": "OpenAlex",
            "europe_pmc": "Europe PMC",
            "inspire": "INSPIRE HEP",
            "nasa_ads": "NASA ADS",
            "dblp": "DBLP",
            "gbif": "GBIF Literature",
            "osti": "OSTI",
            "doaj": "DOAJ",
            "clinical_trials": "ClinicalTrials.gov",
        }.get(provider_id, provider_id)
        providers.append(
            Science125ProviderOut(
                providerId=definition.provider_id,
                displayName=display_name,
                baseUrl=definition.base_url,
                policySourceUrl=definition.policy.policy_source_url,
                authMode=(
                    "required"
                    if required
                    else (
                        "optional"
                        if definition.required_env_vars or definition.optional_env_vars
                        else "none"
                    )
                ),
                isRequired=required,
            )
        )
        item = readiness_by_id.get(provider_id)
        if item is None:
            raise Science125RoutingError()
        status = "ready" if item.ready else ("blocked" if required else "degraded")
        provider_missing_codes = tuple(
            dict.fromkeys(
                _public_configuration_code(code)
                for code in item.missing_configuration_codes
            )
        )
        for env_var in retrieval_profile.required_env_vars:
            if (
                _ENV_PROVIDER_IDS.get(env_var) == provider_id
                and f"MISSING_{env_var}" in raw_profile_missing_codes
            ):
                code = _public_configuration_code(f"MISSING_{env_var}")
                if code not in provider_missing_codes:
                    provider_missing_codes += (code,)
        if provider_id in required_ids and provider_missing_codes:
            status = "blocked"
        is_provider_ready = item.ready and not (
            provider_id in required_ids and provider_missing_codes
        )
        provider_readiness.append(
            Science125ProviderReadinessOut(
                providerId=item.provider_id,
                ready=is_provider_ready,
                status=status,
                missingConfigurationCodes=provider_missing_codes if required else (),
            )
        )
    return Science125QuestionProfileOut(
        questionId=route.question_id,
        routingVersion=ROUTING_VERSION,
        ready=readiness.ready and not profile_missing_codes,
        missingConfigurationCodes=profile_missing_codes,
        benchmarkDomain=route.benchmark_domain,
        primarySubdomain=route.primary_subdomain,
        crossDomainTags=route.cross_domain_tags,
        methodProfile=route.method_profile,
        promptProfile=route.prompt_profile,
        retrievalProfile=route.retrieval_profile,
        classificationReviewStatus=route.classification_review_status,
        pilotEnabled=is_science125_pilot_enabled(route.question_id),
        providers=tuple(providers),
        providerReadiness=tuple(provider_readiness),
    )


__all__ = [
    "MANIFEST_CONTENT_SHA256",
    "SCIENCE125_PILOT_IDS",
    "MANIFEST_PATH",
    "ROUTING_PATH",
    "ROUTING_VERSION",
    "Science125CatalogError",
    "Science125RoutingError",
    "get_science125_catalog",
    "get_science125_question_profile",
    "is_science125_pilot_enabled",
    "get_science125_route",
    "get_science125_routing",
    "load_science125_catalog",
    "load_science125_routing",
]
