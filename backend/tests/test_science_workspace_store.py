from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
import sys


BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))


class ScienceWorkspaceStoreTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        from app.services.science_workspace_store import ScienceWorkspaceStore

        self.store = ScienceWorkspaceStore(Path(self.tmp.name) / "web.db")
        self.addCleanup(self.store.dispose)

    def test_multimodal_run_revision_is_user_scoped_and_optimistic(self) -> None:
        asset = self.store.create_multimodal_asset(
            user_id=7,
            managed_path=Path(self.tmp.name) / "image.png",
            file_name="image.png",
            mime_type="image/png",
            asset_type="image",
            metadata={"width": 20, "height": 10},
        )
        run = self.store.create_multimodal_run(
            user_id=7,
            question="What is the main peak?",
            options={"useLiteratureContext": False},
            source_refs=[{"sourceType": "upload", "assetId": asset.id}],
            result={"items": [{"dataPoints": []}]},
            job_id="job-1",
        )

        updated = self.store.update_multimodal_run(
            user_id=7,
            run_id=run.id,
            expected_revision=1,
            corrections={"items": [{"dataPoints": [{"parameter": "peak", "value": 1.2, "unit": "eV"}]}]},
        )

        self.assertEqual(updated.revision, 2)
        self.assertEqual(updated.corrected_result["items"][0]["dataPoints"][0]["value"], 1.2)
        with self.assertRaisesRegex(ValueError, "REVISION_CONFLICT"):
            self.store.update_multimodal_run(
                user_id=7,
                run_id=run.id,
                expected_revision=1,
                corrections={"items": []},
            )
        self.assertIsNone(self.store.get_multimodal_run(user_id=8, run_id=run.id))

    def test_modeling_workspace_records_server_side_diff(self) -> None:
        workspace = self.store.create_modeling_workspace(
            user_id=3,
            source={"type": "manual"},
            mode="vasp",
            original_result={
                "incar": {"ENCUT": {"value": 520, "source": "default"}},
                "kpoints": {"mode": "Gamma", "mesh": [6, 6, 6]},
            },
            job_id="job-2",
        )

        updated = self.store.update_modeling_workspace(
            user_id=3,
            workspace_id=workspace.id,
            expected_revision=1,
            changes={"incar": {"ENCUT": {"value": 600, "source": "user"}}},
        )

        self.assertEqual(updated.revision, 2)
        revisions = self.store.list_modeling_revisions(user_id=3, workspace_id=workspace.id)
        self.assertEqual(len(revisions), 1)
        self.assertEqual(revisions[0]["diff"][0]["path"], "incar.ENCUT.value")
        self.assertEqual(revisions[0]["diff"][0]["before"], 520)
        self.assertEqual(revisions[0]["diff"][0]["after"], 600)


if __name__ == "__main__":
    unittest.main()
