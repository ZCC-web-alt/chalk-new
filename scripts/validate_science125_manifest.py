from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any


MANIFEST_VERSION = "science125-v1"
SOURCE_PDF_SHA256 = (
    "4bda50e8e3c90f8968f1bfd72ded4d9587ae80cd40ba66656a12c93abcf8e576"
)
SOURCE_CATEGORY_COUNTS = {
    "Mathematical Sciences": 3,
    "Chemistry": 9,
    "Medicine & Health": 11,
    "Biology": 22,
    "Astronomy": 23,
    "Physics": 18,
    "Engineering & Materials Science": 4,
    "Information Science": 4,
    "Neuroscience": 12,
    "Ecology": 8,
    "Energy Science": 3,
    "Artificial Intelligence": 8,
}
BENCHMARK_CATEGORY_COUNTS = {
    **SOURCE_CATEGORY_COUNTS,
    "Medicine & Health": 12,
    "Biology": 21,
}
OVERRIDE_QUESTION = "Can we stop ourselves from aging?"


def canonical_text(value: str) -> str:
    return unicodedata.normalize("NFC", " ".join(value.split()))


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def item_content_hash(question: str) -> str:
    return sha256_text(canonical_text(question))


def manifest_content_hash(manifest: dict[str, Any]) -> str:
    payload = copy.deepcopy(manifest)
    payload.pop("manifestContentSha256", None)
    canonical_json = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return sha256_text(unicodedata.normalize("NFC", canonical_json))


def refresh_hashes(manifest: dict[str, Any]) -> None:
    for item in manifest.get("questions", []):
        item["contentSha256"] = item_content_hash(item["question"])
    manifest["manifestContentSha256"] = manifest_content_hash(manifest)


def validate_manifest(manifest: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    questions = manifest.get("questions")
    if not isinstance(questions, list):
        return ["questions must be an array"]

    if manifest.get("manifestVersion") != MANIFEST_VERSION:
        errors.append(f"manifestVersion must be {MANIFEST_VERSION!r}")
    if manifest.get("source", {}).get("sourcePdfSha256") != SOURCE_PDF_SHA256:
        errors.append("source.sourcePdfSha256 does not match the authoritative PDF")
    if len(questions) != 125:
        errors.append(f"questions must contain 125 items, found {len(questions)}")

    expected_ids = [f"S125-{number:03d}" for number in range(1, 126)]
    actual_ids = [item.get("id") for item in questions]
    if actual_ids != expected_ids:
        errors.append("question IDs must be continuous S125-001 through S125-125")

    canonical_questions: list[str] = []
    source_counts: Counter[str] = Counter()
    benchmark_counts: Counter[str] = Counter()
    overrides: list[dict[str, Any]] = []
    source_version = manifest.get("source", {}).get("sourceVersion")

    for index, item in enumerate(questions, start=1):
        item_id = item.get("id", f"item {index}")
        question = item.get("question")
        if not isinstance(question, str) or not canonical_text(question):
            errors.append(f"{item_id}: question must be non-empty text")
            continue

        canonical_question = canonical_text(question)
        canonical_questions.append(canonical_question)
        source_domain = item.get("sourceDomain")
        benchmark_domain = item.get("benchmarkDomain")
        source_counts[source_domain] += 1
        benchmark_counts[benchmark_domain] += 1

        if source_domain != benchmark_domain:
            overrides.append(item)
        elif "classificationOverrideReason" in item:
            errors.append(
                f"{item_id}: classificationOverrideReason is only valid for an override"
            )

        if item.get("sourceVersion") != source_version:
            errors.append(f"{item_id}: sourceVersion must match the manifest source")
        pdf_page = item.get("pdfPage")
        booklet_page = item.get("bookletPage")
        if not isinstance(pdf_page, int) or not isinstance(booklet_page, int):
            errors.append(f"{item_id}: pdfPage and bookletPage must be integers")
        elif pdf_page - booklet_page != 2 or not 7 <= pdf_page <= 42:
            errors.append(f"{item_id}: invalid PDF/booklet page mapping")

        expected_hash = item_content_hash(question)
        if item.get("contentSha256") != expected_hash:
            errors.append(f"{item_id}: contentSha256 mismatch")

    if len(canonical_questions) != len(set(canonical_questions)):
        errors.append("questions must be unique after canonicalization")
    if dict(source_counts) != SOURCE_CATEGORY_COUNTS:
        errors.append(f"source domain counts mismatch: {dict(source_counts)!r}")
    if dict(benchmark_counts) != BENCHMARK_CATEGORY_COUNTS:
        errors.append(f"benchmark domain counts mismatch: {dict(benchmark_counts)!r}")
    if manifest.get("sourceCategoryCounts") != SOURCE_CATEGORY_COUNTS:
        errors.append("sourceCategoryCounts does not match the locked source taxonomy")
    if manifest.get("benchmarkCategoryCounts") != BENCHMARK_CATEGORY_COUNTS:
        errors.append(
            "benchmarkCategoryCounts does not match the locked competition taxonomy"
        )

    if len(overrides) != 1:
        errors.append("exactly one source-to-benchmark classification override is required")
    else:
        override = overrides[0]
        if (
            override.get("question") != OVERRIDE_QUESTION
            or override.get("sourceDomain") != "Biology"
            or override.get("benchmarkDomain") != "Medicine & Health"
            or not override.get("classificationOverrideReason")
        ):
            errors.append("the aging question classification override is invalid")

    expected_manifest_hash = manifest_content_hash(manifest)
    if manifest.get("manifestContentSha256") != expected_manifest_hash:
        errors.append("manifestContentSha256 mismatch")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate the versioned Science 125 benchmark manifest."
    )
    parser.add_argument("manifest", type=Path)
    parser.add_argument(
        "--refresh-hashes",
        action="store_true",
        help="Deterministically refresh item and manifest hashes before validation.",
    )
    args = parser.parse_args()

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    if args.refresh_hashes:
        refresh_hashes(manifest)
        args.manifest.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    errors = validate_manifest(manifest)
    if errors:
        for error in errors:
            print(f"error: {error}", file=sys.stderr)
        return 1

    print(f"{MANIFEST_VERSION}: valid ({len(manifest['questions'])} questions)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
