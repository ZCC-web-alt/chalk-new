from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sys
import unicodedata
import unittest
from collections import Counter
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = (
    PROJECT_ROOT / "benchmarks" / "science125" / "science125-v1.json"
)
VALIDATOR_PATH = PROJECT_ROOT / "scripts" / "validate_science125_manifest.py"

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


def _canonical_text(value: str) -> str:
    return unicodedata.normalize("NFC", " ".join(value.split()))


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _manifest_hash(manifest: dict[str, object]) -> str:
    payload = copy.deepcopy(manifest)
    payload.pop("manifestContentSha256", None)
    canonical_json = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return _sha256(unicodedata.normalize("NFC", canonical_json).encode("utf-8"))


class Science125ManifestTestCase(unittest.TestCase):
    def test_science125_v1_is_complete_unique_and_hash_stable(self) -> None:
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        questions = manifest["questions"]

        self.assertEqual(manifest["manifestVersion"], "science125-v1")
        self.assertEqual(
            manifest["source"]["sourcePdfSha256"],
            "4bda50e8e3c90f8968f1bfd72ded4d9587ae80cd40ba66656a12c93abcf8e576",
        )
        self.assertEqual(len(questions), 125)
        self.assertEqual(
            [item["id"] for item in questions],
            [f"S125-{number:03d}" for number in range(1, 126)],
        )
        self.assertEqual(
            len({_canonical_text(item["question"]) for item in questions}),
            125,
        )

        source_counts = Counter(item["sourceDomain"] for item in questions)
        benchmark_counts = Counter(item["benchmarkDomain"] for item in questions)
        self.assertEqual(dict(source_counts), SOURCE_CATEGORY_COUNTS)
        self.assertEqual(dict(benchmark_counts), BENCHMARK_CATEGORY_COUNTS)
        self.assertEqual(manifest["sourceCategoryCounts"], SOURCE_CATEGORY_COUNTS)
        self.assertEqual(manifest["benchmarkCategoryCounts"], BENCHMARK_CATEGORY_COUNTS)

        overrides = [
            item for item in questions if item["sourceDomain"] != item["benchmarkDomain"]
        ]
        self.assertEqual(len(overrides), 1)
        self.assertEqual(overrides[0]["question"], "Can we stop ourselves from aging?")
        self.assertEqual(overrides[0]["sourceDomain"], "Biology")
        self.assertEqual(overrides[0]["benchmarkDomain"], "Medicine & Health")
        self.assertTrue(overrides[0]["classificationOverrideReason"])
        self.assertTrue(
            all(
                "classificationOverrideReason" not in item
                for item in questions
                if item is not overrides[0]
            )
        )

        for item in questions:
            self.assertEqual(item["sourceVersion"], manifest["source"]["sourceVersion"])
            self.assertEqual(item["pdfPage"] - item["bookletPage"], 2)
            self.assertGreaterEqual(item["pdfPage"], 7)
            self.assertLessEqual(item["pdfPage"], 42)
            self.assertEqual(
                item["contentSha256"],
                _sha256(_canonical_text(item["question"]).encode("utf-8")),
            )

        self.assertEqual(manifest["manifestContentSha256"], _manifest_hash(manifest))

    def test_science125_validator_accepts_the_authoritative_manifest(self) -> None:
        result = subprocess.run(
            [sys.executable, str(VALIDATOR_PATH), str(MANIFEST_PATH)],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("science125-v1: valid (125 questions)", result.stdout)


if __name__ == "__main__":
    unittest.main()
