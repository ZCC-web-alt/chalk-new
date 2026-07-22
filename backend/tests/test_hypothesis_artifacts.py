from __future__ import annotations

import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


BACKEND_DIR = Path(__file__).resolve().parents[1]
ROOT_DIR = BACKEND_DIR.parents[1]
sys.path.insert(0, str(BACKEND_DIR))
sys.path.insert(0, str(ROOT_DIR / "src"))
sys.path.insert(0, str(ROOT_DIR))


class HypothesisArtifactTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp.name)
        from app.services.job_store import WebJobStore
        from app.services.jobs import JobService

        self.store = WebJobStore(self.tmp_path / "web.db")
        self.service = JobService(self.store)
        self.job = self.store.create(1, "hypothesis_generate", {"researchQuestion": "test"})
        self.result = SimpleNamespace(
            raw_json={"paper_title": "Managed hypothesis", "confidence": 7},
            final_output="",
            confidence=7,
            feasibility="high",
            iterations=[],
            critique_history=[],
            reasoning_chain={},
            debate_history=[],
            interaction_history={"session_id": "trace-session", "mode": "hitl", "interactions": []},
        )

    def tearDown(self) -> None:
        self.service._executor.shutdown(wait=True, cancel_futures=True)
        self.service._hypothesis_executor.shutdown(wait=True, cancel_futures=True)
        self.service.model_call_ledger.dispose()
        self.service.science125_rate_store.dispose()
        self.service.analysis_store.dispose()
        self.service.hypothesis_store.dispose()
        self.store.dispose()
        self.tmp.cleanup()

    def test_generation_creates_secured_report_trace_and_sanitized_workflow_zip(self) -> None:
        private_path = self.tmp_path / "private" / "secret.cif"

        class Renderer:
            def __init__(self, config=None):
                self._enhanced = None

            def render(self, result, enhance=True):
                self._enhanced = {"insight": "enhanced"} if enhance else None
                return f"<html><head></head><body>{private_path}</body></html>"

        class TraceModule:
            @staticmethod
            def make_agent_trace(**kwargs):
                return {"session_id": kwargs["session_id"], "trace_path": str(private_path)}

        class WorkflowModule:
            @staticmethod
            def build_hypothesis_workflow_package(data):
                return {"package_kind": "hypothesis_workflow", "source_path": str(private_path)}

            @staticmethod
            def export_hypothesis_workflow_package(package, output_dir):
                output = Path(output_dir)
                output.mkdir(parents=True, exist_ok=True)
                (output / "package.json").write_text(json.dumps(package), encoding="utf-8")
                (output / "README.md").write_text(f"source={private_path}", encoding="utf-8")
                return output

        with (
            patch("app.services.jobs.report_renderer", return_value=SimpleNamespace(HTMLReportRenderer=Renderer)),
            patch("app.services.jobs.hitl_trace", return_value=TraceModule),
            patch("app.services.jobs.hypothesis_workflow_exporter", return_value=WorkflowModule),
            patch("app.services.jobs.api_keys.api_key_store.get_key", return_value="test-key"),
        ):
            artifacts, warnings = self.service._create_hypothesis_artifacts(
                self.job,
                12,
                self.result,
                self.result.interaction_history,
            )

        self.assertEqual(warnings, [])
        self.assertEqual({item["kind"] for item in artifacts}, {
            "interactive_report_html",
            "agent_trace_json",
            "workflow_package_zip",
        })
        stored = self.service.hypothesis_store.list_for_hypothesis(1, 12)
        self.assertEqual(len(stored), 3)
        report = next(item for item in stored if item.kind == "interactive_report_html")
        html = report.path.read_text(encoding="utf-8")
        self.assertIn("Content-Security-Policy", html)
        self.assertNotIn(str(self.tmp_path), html)
        workflow = next(item for item in stored if item.kind == "workflow_package_zip")
        with zipfile.ZipFile(workflow.path) as archive:
            self.assertEqual(sorted(archive.namelist()), ["README.md", "package.json"])
            combined = "\n".join(archive.read(name).decode("utf-8") for name in archive.namelist())
        self.assertNotIn(str(self.tmp_path), combined)

    def test_report_enhancement_failure_falls_back_without_failing(self) -> None:
        class Renderer:
            def __init__(self, config=None):
                self._enhanced = None

            def render(self, result, enhance=True):
                if enhance:
                    raise RuntimeError("enhancement failed")
                return "<html><head></head><body>standard</body></html>"

        with (
            patch("app.services.jobs.report_renderer", return_value=SimpleNamespace(HTMLReportRenderer=Renderer)),
            patch("app.services.jobs.api_keys.api_key_store.get_key", return_value="test-key"),
        ):
            artifact, warnings = self.service._create_report_artifact(self.job, 8, self.result, enhance=True)

        self.assertEqual(artifact["kind"], "interactive_report_html")
        self.assertTrue(any("standard report" in warning.lower() for warning in warnings))

    def test_workflow_zip_rejects_member_limits_and_traversal_names(self) -> None:
        from app.services.jobs import SafeJobError

        source = self.tmp_path / "workflow"
        source.mkdir()
        (source / "one.txt").write_text("one", encoding="utf-8")
        (source / "two.txt").write_text("two", encoding="utf-8")

        with self.assertRaises(SafeJobError) as count_error:
            self.service._zip_workflow_tree(source, self.tmp_path / "workflow.zip", max_files=1)
        self.assertEqual(count_error.exception.code, "WORKFLOW_PACKAGE_TOO_LARGE")

        with self.assertRaises(SafeJobError) as traversal_error:
            self.service._validate_zip_member_name("../escape.txt")
        self.assertEqual(traversal_error.exception.code, "UNSAFE_WORKFLOW_PACKAGE")

    def test_workflow_export_rejects_unmanaged_structure_source_files(self) -> None:
        from app.services.jobs import SafeJobError

        secret = self.tmp_path / "outside-managed-roots.txt"
        secret.write_text("private", encoding="utf-8")

        with self.assertRaises(SafeJobError) as context:
            self.service._validated_workflow_package_sources({
                "structures": [{"index": 1, "path": str(secret)}],
            })

        self.assertEqual(context.exception.code, "UNSAFE_WORKFLOW_SOURCE")


if __name__ == "__main__":
    unittest.main()
