from __future__ import annotations

import base64
import gzip
import json
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Science125CanaryInputsTestCase(unittest.TestCase):
    def test_materializes_split_gzip_context_and_reviewed_evidence_without_partial_outputs(self) -> None:
        from scripts.materialize_science125_canary_inputs import materialize_from_environment

        context = gzip.compress(b'{"indexVersion":"science125-context-v1"}')
        encoded_context = base64.b64encode(context).decode("ascii")
        evidence = base64.b64encode(
            json.dumps({"S125-006": [{"stableId": "doi:10.1/example"}]}).encode("utf-8")
        ).decode("ascii")
        environment = {
            "SCIENCE125_CANARY_CONTEXT_INDEX_GZIP_B64_PART1": encoded_context[:20],
            "SCIENCE125_CANARY_CONTEXT_INDEX_GZIP_B64_PART2": encoded_context[20:],
            "SCIENCE125_CANARY_REVIEWED_EVIDENCE_B64": evidence,
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            context_path = root / "science125-context-v1.json"
            evidence_path = root / "reviewed-evidence.json"

            materialize_from_environment(
                environment,
                context_path=context_path,
                evidence_path=evidence_path,
                validate_context=lambda value: self.assertEqual(
                    json.loads(value.read_text(encoding="utf-8"))["indexVersion"],
                    "science125-context-v1",
                ),
            )

            self.assertEqual(
                context_path.read_text(encoding="utf-8"),
                '{"indexVersion":"science125-context-v1"}',
            )
            self.assertEqual(
                json.loads(evidence_path.read_text(encoding="utf-8")),
                {"S125-006": [{"stableId": "doi:10.1/example"}]},
            )

    def test_rejects_missing_or_malformed_secret_data_before_creating_files(self) -> None:
        from scripts.materialize_science125_canary_inputs import CanaryInputError, materialize_from_environment

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            context_path = root / "science125-context-v1.json"
            evidence_path = root / "reviewed-evidence.json"
            with self.assertRaisesRegex(CanaryInputError, "missing"):
                materialize_from_environment({}, context_path=context_path, evidence_path=evidence_path)
            self.assertFalse(context_path.exists())
            self.assertFalse(evidence_path.exists())

            malformed = {
                "SCIENCE125_CANARY_CONTEXT_INDEX_GZIP_B64_PART1": "not-base64",
                "SCIENCE125_CANARY_CONTEXT_INDEX_GZIP_B64_PART2": "",
                "SCIENCE125_CANARY_REVIEWED_EVIDENCE_B64": "e30=",
            }
            with self.assertRaises(CanaryInputError):
                materialize_from_environment(malformed, context_path=context_path, evidence_path=evidence_path)
            self.assertFalse(context_path.exists())
            self.assertFalse(evidence_path.exists())


if __name__ == "__main__":
    unittest.main()
