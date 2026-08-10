from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_DIR = BACKEND_DIR.parent
sys.path.insert(0, str(BACKEND_DIR))


class StandaloneLayoutTestCase(unittest.TestCase):
    def test_chalk_app_resolves_from_the_standalone_project(self) -> None:
        from app.core import config
        from app.core.legacy import bootstrap_legacy

        expected_source = PROJECT_DIR / "src" / "chalk_app"

        self.assertEqual(config.PROJECT_ROOT.resolve(), PROJECT_DIR.resolve())
        self.assertTrue(expected_source.is_dir(), f"Missing standalone package: {expected_source}")

        with patch.dict(os.environ):
            os.environ.pop("CHALK_LEGACY_ROOT", None)
            config.get_settings.cache_clear()
            self.addCleanup(config.get_settings.cache_clear)
            resolved_root = bootstrap_legacy()

        self.assertEqual(resolved_root.resolve(), PROJECT_DIR.resolve())
        bootstrap_module = sys.modules["chalk_app.bootstrap"]
        module_path = Path(bootstrap_module.__file__).resolve()
        self.assertTrue(
            module_path.is_relative_to(expected_source.resolve()),
            f"chalk_app was imported from outside the standalone project: {module_path}",
        )

    def test_relocated_copy_imports_backend_and_owns_its_databases(self) -> None:
        original_checkout = PROJECT_DIR.parent.resolve()
        with tempfile.TemporaryDirectory(prefix="chalk-web-standalone-") as temp_dir:
            relocated = Path(temp_dir) / "renamed-web-project"
            shutil.copytree(PROJECT_DIR / "backend" / "app", relocated / "backend" / "app")
            shutil.copytree(PROJECT_DIR / "src", relocated / "src")
            shutil.copytree(
                PROJECT_DIR / "benchmarks" / "science125",
                relocated / "benchmarks" / "science125",
            )

            probe = r'''
import importlib
import os
import sys
from pathlib import Path

project = Path.cwd().parent.resolve()
original = Path(os.environ.pop("ORIGINAL_CHALK_CHECKOUT")).resolve()

def outside_original(entry):
    try:
        return not Path(entry or ".").resolve().is_relative_to(original)
    except (OSError, ValueError):
        return True

sys.path[:] = [entry for entry in sys.path if outside_original(entry)]
sys.path[:0] = [str(project / "backend"), str(project / "src")]
os.environ.pop("CHALK_LEGACY_ROOT", None)
os.environ.pop("CHALK_WEB_DATA_DIR", None)

from app.core.config import PROJECT_ROOT, get_settings
from app.core.legacy import bootstrap_legacy, db

assert PROJECT_ROOT.resolve() == project
assert get_settings().data_dir.resolve() == (project / "data").resolve()
assert bootstrap_legacy().resolve() == project

legacy_db = db()
assert Path(legacy_db.DB_PATH).resolve() == (project / "data" / "app.db").resolve()
assert Path(importlib.import_module("chalk_app.bootstrap").__file__).resolve().is_relative_to(project / "src")
assert Path(importlib.import_module("scientific_toolkit").__file__).resolve().is_relative_to(project / "src")
main = importlib.import_module("app.main")
assert Path(main.__file__).resolve().is_relative_to(project / "backend")

from fastapi.testclient import TestClient
with TestClient(main.app) as client:
    response = client.get("/api/health")
    assert response.status_code == 200, response.text
'''
            env = os.environ.copy()
            env["ORIGINAL_CHALK_CHECKOUT"] = str(original_checkout)
            env.pop("CHALK_LEGACY_ROOT", None)
            env.pop("CHALK_WEB_DATA_DIR", None)
            result = subprocess.run(
                [sys.executable, "-c", probe],
                cwd=relocated / "backend",
                env=env,
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertTrue((relocated / "data" / "app.db").is_file())
            self.assertTrue((relocated / "data" / "web.db").is_file())
            self.assertTrue((relocated / "benchmarks" / "science125" / "science125-prompts-v2.json").is_file())

            override_dir = relocated / "external-runtime-data"
            override_probe = r'''
import os
import sys
from pathlib import Path

project = Path.cwd().parent.resolve()
original = Path(os.environ.pop("ORIGINAL_CHALK_CHECKOUT")).resolve()
sys.path[:] = [
    entry for entry in sys.path
    if not Path(entry or ".").resolve().is_relative_to(original)
]
sys.path[:0] = [str(project / "backend"), str(project / "src")]
os.environ.pop("CHALK_LEGACY_ROOT", None)

from app.core.config import get_settings
from app.core.legacy import db
from app.main import app
from fastapi.testclient import TestClient

data_dir = Path(os.environ["CHALK_WEB_DATA_DIR"]).resolve()
assert get_settings().data_dir.resolve() == data_dir
assert Path(db().DB_PATH).resolve() == data_dir / "app.db"
with TestClient(app) as client:
    assert client.get("/api/health").status_code == 200
'''
            override_env = env.copy()
            override_env["CHALK_WEB_DATA_DIR"] = str(override_dir)
            override_result = subprocess.run(
                [sys.executable, "-c", override_probe],
                cwd=relocated / "backend",
                env=override_env,
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )

            self.assertEqual(
                override_result.returncode,
                0,
                override_result.stdout + override_result.stderr,
            )
            self.assertTrue((override_dir / "app.db").is_file())
            self.assertTrue((override_dir / "web.db").is_file())


if __name__ == "__main__":
    unittest.main()
