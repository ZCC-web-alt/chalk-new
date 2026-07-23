from __future__ import annotations

import copy
import hashlib
import json
import sys
import tempfile
import unicodedata
import unittest
from pathlib import Path
from unittest.mock import patch


BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_DIR.parent
sys.path.insert(0, str(BACKEND_DIR))

ROUTING_PATH = PROJECT_ROOT / "benchmarks" / "science125" / "science125-routing-v1.json"
MANIFEST_PATH = PROJECT_ROOT / "benchmarks" / "science125" / "science125-v1.json"


def _canonical_json(payload: dict[str, object]) -> str:
    return unicodedata.normalize(
        "NFC",
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
    )


def _content_hash(payload: dict[str, object], field: str) -> str:
    value = copy.deepcopy(payload)
    value.pop(field, None)
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


class Science125RoutingManifestTestCase(unittest.TestCase):
    def test_routing_manifest_covers_authoritative_questions_without_reclassifying_them(self) -> None:
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        routing = json.loads(ROUTING_PATH.read_text(encoding="utf-8"))

        self.assertEqual(routing["routingVersion"], "science125-routing-v1")
        self.assertEqual(routing["baseManifestVersion"], "science125-v1")
        self.assertEqual(
            routing["baseManifestContentSha256"], manifest["manifestContentSha256"]
        )
        self.assertEqual(routing["routingContentSha256"], _content_hash(routing, "routingContentSha256"))

        manifest_by_id = {item["id"]: item for item in manifest["questions"]}
        questions = routing["questions"]
        self.assertEqual(len(questions), 125)
        self.assertEqual(
            [item["questionId"] for item in questions],
            [f"S125-{number:03d}" for number in range(1, 126)],
        )
        self.assertEqual(len({item["questionId"] for item in questions}), 125)

        allowed_methods = {
            "proof",
            "experimental",
            "observational",
            "clinical",
            "engineering",
            "computational",
            "systems_policy",
        }
        for item in questions:
            source = manifest_by_id[item["questionId"]]
            self.assertEqual(item["benchmarkDomain"], source["benchmarkDomain"])
            self.assertEqual(item["methodProfile"]["primary"] in allowed_methods, True)
            secondary = item["methodProfile"]["secondary"]
            self.assertLessEqual(len(secondary), 2)
            self.assertEqual(len(secondary), len(set(secondary)))
            self.assertNotIn(item["methodProfile"]["primary"], secondary)
            self.assertEqual(item["classificationReviewStatus"], "reviewed")
            self.assertRegex(item["promptProfile"], r"^s125\.[a-z_]+\.v1$")
            self.assertRegex(item["retrievalProfile"], r"^retrieval\.[a-z0-9_.]+\.v1$")

        by_id = {item["questionId"]: item for item in questions}
        self.assertEqual(by_id["S125-006"]["primarySubdomain"], "chem.interface")
        self.assertEqual(by_id["S125-006"]["retrievalProfile"], "retrieval.chem.interface.v1")
        self.assertEqual(by_id["S125-043"]["primarySubdomain"], "bio.genome_editing")
        self.assertEqual(by_id["S125-043"]["retrievalProfile"], "retrieval.bio.genome_editing.v1")
        self.assertEqual(by_id["S125-054"]["primarySubdomain"], "astro.cosmic_rays")
        self.assertEqual(by_id["S125-054"]["retrievalProfile"], "retrieval.astro.high_energy.v1")

        self.assertEqual(by_id["S125-025"]["classificationReviewStatus"], "reviewed")
        self.assertIn("biology", by_id["S125-025"]["crossDomainTags"])
        self.assertIn("physics", by_id["S125-117"]["crossDomainTags"])

    def test_catalog_exposes_route_lookup_and_public_provider_metadata(self) -> None:
        from app.services.science125_catalog import (
            get_science125_question_profile,
            get_science125_route,
            load_science125_routing,
        )

        routing = load_science125_routing()
        self.assertEqual(routing.routing_version, "science125-routing-v1")
        route = get_science125_route("S125-006")
        self.assertEqual(route.primary_subdomain, "chem.interface")
        self.assertEqual(route.method_profile.primary, "experimental")
        self.assertEqual(route.method_profile.secondary, ("computational",))

        with patch.dict(
            "os.environ",
            {
                "SCIENCE125_CROSSREF_MAILTO": "",
                "SCIENCE125_OPENALEX_MAILTO": "",
                "SCIENCE125_SEMANTIC_SCHOLAR_API_KEY": "",
                "SCIENCE125_NCBI_API_KEY": "",
                "SCIENCE125_NCBI_TOOL_EMAIL": "",
                "SCIENCE125_NASA_ADS_API_TOKEN": "",
            },
        ):
            profile = get_science125_question_profile("S125-006")
        self.assertEqual(profile.question_id, "S125-006")
        self.assertEqual(profile.routing_version, "science125-routing-v1")
        self.assertEqual(profile.question_zh, "我们如何在微观尺度上测量界面现象？")
        self.assertIn("界面", profile.search_intent_zh)
        self.assertIn("interfacial", profile.recommended_query.lower())
        self.assertEqual(profile.localization_version, "science125-zh-CN-v1")
        self.assertFalse(profile.ready)
        self.assertIn("SEMANTIC_SCHOLAR_CREDENTIAL_REQUIRED", profile.missing_configuration_codes)
        self.assertNotIn("SCIENCE125_", profile.model_dump_json())
        self.assertTrue(profile.providers)
        self.assertTrue(profile.provider_readiness)
        for provider in profile.providers:
            self.assertNotIn("Key", provider.model_dump_json())
            self.assertNotIn("Authorization", provider.model_dump_json())
            self.assertTrue(provider.base_url.startswith("https://"))
        for readiness in profile.provider_readiness:
            self.assertNotIn("Key", readiness.model_dump_json())
            self.assertNotIn("Authorization", readiness.model_dump_json())

        with patch.dict("os.environ", {"SCIENCE125_NCBI_API_KEY": "", "SCIENCE125_NCBI_TOOL_EMAIL": ""}):
            genome_profile = get_science125_question_profile("S125-043")
        ncbi = next(item for item in genome_profile.provider_readiness if item.provider_id == "ncbi")
        self.assertFalse(ncbi.ready)
        self.assertEqual(ncbi.status, "blocked")
        self.assertIn("NCBI_CREDENTIAL_REQUIRED", ncbi.missing_configuration_codes)

        for item in routing.questions:
            with self.subTest(question_id=item.question_id):
                resolved = get_science125_question_profile(item.question_id)
                self.assertEqual(resolved.retrieval_profile, item.retrieval_profile)
                self.assertGreaterEqual(len(resolved.providers), 3)

    def test_catalog_rejects_unknown_route_id(self) -> None:
        from app.services.science125_catalog import Science125RoutingError, get_science125_route

        with self.assertRaises(Science125RoutingError):
            get_science125_route("S125-999")

    def test_loader_rejects_reclassification_even_with_a_refreshed_hash(self) -> None:
        from app.services.science125_catalog import (
            Science125RoutingError,
            load_science125_routing,
        )

        routing = json.loads(ROUTING_PATH.read_text(encoding="utf-8"))
        routing["questions"][0]["benchmarkDomain"] = "Chemistry"
        routing["routingContentSha256"] = _content_hash(routing, "routingContentSha256")
        with tempfile.TemporaryDirectory() as temp_dir:
            private_path = Path(temp_dir) / "private-routing.json"
            private_path.write_text(json.dumps(routing), encoding="utf-8")

            with self.assertRaises(Science125RoutingError) as raised:
                load_science125_routing(private_path)

            self.assertNotIn(str(private_path), str(raised.exception))


if __name__ == "__main__":
    unittest.main()
